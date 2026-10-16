"""The schema-level check: the required level equals the migration chain, the message is specific, and the worker shows it (instead of the generic invoice
error) only when a missing table / column coincides with a database that is really behind. No database is used."""
import unittest
from unittest.mock import patch

from mysql.connector import errors as mysql_errors
from PySide6.QtCore import QCoreApplication

from app.workers.invoice_worker import InvoiceWorker
from services import schema_level
from tools.schema_rehearsal import chain

GENERIC = "تعذر تحميل بيانات الفاتورة. أعد المحاولة."


class FakeConnection:
    def __init__(self, level=None, fail=False):
        self.level, self.fail, self.closed, self.rolled_back = level, fail, False, False

    def cursor(self):
        if self.fail:
            raise RuntimeError("connection lost")
        outer = self

        class Cursor:
            def execute(self, sql, params=()):
                self.sql = sql

            def fetchone(self):
                return (outer.level,)

            def close(self):
                pass
        return Cursor()

    def rollback(self):
        self.rolled_back = True

    def close(self):
        self.closed = True


class SchemaLevelTests(unittest.TestCase):
    def test_the_required_level_is_the_highest_migration_of_the_chain(self):
        self.assertEqual(schema_level.REQUIRED_SCHEMA_LEVEL, chain.LAST)

    def test_the_message_names_both_levels(self):
        self.assertEqual(schema_level.outdated_message(51, 54), "قاعدة البيانات على المستوى 51 والمطلوب 54: طبّق التحديثات.")
        self.assertEqual(schema_level.outdated_message(51), schema_level.outdated_message(51, schema_level.REQUIRED_SCHEMA_LEVEL))

    def test_only_a_database_behind_the_required_level_is_reported(self):
        required = schema_level.REQUIRED_SCHEMA_LEVEL
        for level, expected in ((51, schema_level.outdated_message(51)), (required - 1, schema_level.outdated_message(required - 1)),
                                (required, None), (required + 1, None), (None, None)):
            connection = FakeConnection(level)
            self.assertEqual(schema_level.schema_problem(lambda c=connection: c), expected, level)
            self.assertTrue(connection.closed and connection.rolled_back)

    def test_a_failure_of_the_check_itself_keeps_the_generic_message(self):
        self.assertIsNone(schema_level.schema_problem(lambda: FakeConnection(fail=True)))

        def broken():
            raise RuntimeError("no database")
        self.assertIsNone(schema_level.schema_problem(broken))


class WorkerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QCoreApplication.instance() or QCoreApplication([])

    class Session:
        def is_current(self, actor):
            return True

    class Actor:
        generation = 3

    def run_worker(self, error, logged=True):
        def call(_expected_actor):
            raise error
        worker = InvoiceWorker(call, self.Session(), self.Actor(), 7, "detail")
        results = []
        worker.completed.connect(lambda *args: results.append(args))
        if logged:                                           # an unexpected failure is logged; a business refusal is not
            with self.assertLogs("app.workers.invoice_worker", level="ERROR"):
                worker.run()
        else:
            worker.run()
        return results[0]

    def test_a_missing_table_on_a_database_behind_the_build_shows_the_specific_message(self):
        error = mysql_errors.ProgrammingError(errno=1146, msg="Table 'x.market_trip_gold_sales' doesn't exist", sqlstate="42S02")
        with patch("services.schema_level.schema_problem", return_value=schema_level.outdated_message(51)) as check:
            request, generation, operation, result, message, code, kind = self.run_worker(error)
        self.assertEqual((message, code, kind), (schema_level.outdated_message(51), "SCHEMA_OUTDATED", "SchemaOutdated"))
        self.assertEqual((request, generation, operation, result), (7, "3", "detail", None))
        self.assertEqual(check.call_count, 1)

    def test_a_missing_column_is_treated_the_same_way(self):
        error = mysql_errors.ProgrammingError(errno=1054, msg="Unknown column", sqlstate="42S22")
        with patch("services.schema_level.schema_problem", return_value=schema_level.outdated_message(52)):
            self.assertEqual(self.run_worker(error)[4], schema_level.outdated_message(52))

    def test_an_up_to_date_database_keeps_the_generic_message(self):
        error = mysql_errors.ProgrammingError(errno=1146, msg="missing", sqlstate="42S02")
        with patch("services.schema_level.schema_problem", return_value=None):
            message, code, kind = self.run_worker(error)[4:]
        self.assertEqual((message, code, kind), (GENERIC, "", "ProgrammingError"))

    def test_any_other_error_never_runs_the_check(self):
        for error in (mysql_errors.ProgrammingError(errno=1064, msg="syntax", sqlstate="42000"), RuntimeError("boom"), KeyError("x")):
            with patch("services.schema_level.schema_problem") as check:
                message = self.run_worker(error)[4]
            self.assertEqual((message, check.call_count), (GENERIC, 0), type(error).__name__)

    def test_business_refusals_are_unchanged(self):
        with patch("services.schema_level.schema_problem") as check:
            message, code, kind = self.run_worker(ValueError("رفض عمل: بيانات غير صحيحة"), logged=False)[4:]
        self.assertEqual((message, code, kind, check.call_count), ("رفض عمل: بيانات غير صحيحة", "", "ValueError", 0))


if __name__ == "__main__":
    unittest.main()
