"""Explicit opt-in rehearsal check (migration 067): the position snapshot table, its constraints, the service and the «ربح الذهب» report on REAL MySQL (masgold_rehearsal, level >= 67).

Everything that writes runs inside ONE transaction that is ALWAYS rolled back: no snapshot persists, so the one real initial balance is never consumed by a test."""
from contextlib import contextmanager
from datetime import date
from decimal import Decimal as D
import os
import unittest

from mysql.connector import Error as MySqlError

from services.position_snapshot_service import PositionSnapshotService, SnapshotError
from services.report_service import ReportService
from tests.test_market_trip_book_mysql import BookCoreMySqlTests


@unittest.skipUnless(os.environ.get("MASGOLD_BOOK_REHEARSAL") == "1", "explicit rehearsal-only opt-in")
class PositionSnapshotsMySqlTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        BookCoreMySqlTests.setUpClass()
        cls.h = BookCoreMySqlTests("test_the_migration_is_registered_and_the_objects_exist")
        with cls.h.tx() as c:
            q = c.cursor()
            q.execute("SELECT MAX(version) FROM schema_migrations")
            assert int(q.fetchone()[0]) >= 67
            q.close()

    def setUp(self):
        from database.connection import DatabaseConnection
        self.connection = DatabaseConnection(self.h.config).connect()
        self.connection.rollback()
        self.connection.start_transaction(isolation_level="REPEATABLE READ")
        self.addCleanup(self._close)

    def _close(self):
        self.connection.rollback()
        self.connection.close()

    def count(self):
        q = self.connection.cursor()
        q.execute("SELECT COUNT(*) FROM position_snapshots")
        try:
            return q.fetchone()[0]
        finally:
            q.close()

    def snapshot_service(self):
        @contextmanager
        def factory():
            yield self.connection
        return PositionSnapshotService(self.h.session, transaction_factory=factory)

    def insert(self, **overrides):
        values = {"snapshot_kind": "DAILY", "gold_shop_eq21": "10.000", "gold_total_eq21": "10.000"}
        values.update(overrides)
        q = self.connection.cursor()
        try:
            q.execute(f"INSERT INTO position_snapshots (taken_at, {', '.join(values)}) VALUES (NOW(), {', '.join(['%s'] * len(values))})", tuple(values.values()))
        finally:
            q.close()

    def test_the_constraints_refuse_a_second_opening_a_wrong_total_and_an_unknown_kind(self):
        before = self.count()
        self.insert(snapshot_kind="OPENING")
        with self.assertRaises(MySqlError):
            self.insert(snapshot_kind="OPENING")                                                   # one initial balance only (unique generated column)
        self.insert(snapshot_kind="DAILY")                                                         # any number of daily sheets
        for bad in ({"gold_total_eq21": "11.000"}, {"snapshot_kind": "WEEKLY"}, {"unknown_grams": "-1"}):
            with self.assertRaises(MySqlError):
                self.insert(**bad)
        self.assertEqual(self.count(), before + 2)

    def test_the_service_enters_the_opening_once_takes_a_daily_sheet_once_and_the_report_reads_them(self):
        service = self.snapshot_service()
        self.assertEqual(service.ensure_daily(), {"taken": False, "reason": "NO_OPENING"})
        service.set_opening("5000", "-120.5", "200000", "30000")
        with self.assertRaises(SnapshotError) as raised:
            service.set_opening("1", "0", "0", "0")
        self.assertEqual(raised.exception.code, "ALREADY_INITIALIZED")
        first = service.ensure_daily()
        self.assertTrue(first["taken"], first)
        self.assertEqual(service.ensure_daily(), {"taken": False, "reason": "ALREADY_TAKEN"})
        q = self.connection.cursor(dictionary=True)
        q.execute("SELECT snapshot_kind, gold_total_eq21, gold_shop_eq21, gold_suppliers_eq21 FROM position_snapshots ORDER BY id")
        rows = q.fetchall()
        q.close()
        self.assertEqual([r["snapshot_kind"] for r in rows][-2:], ["OPENING", "DAILY"])
        opening = next(r for r in rows if r["snapshot_kind"] == "OPENING")
        self.assertEqual((opening["gold_shop_eq21"], opening["gold_suppliers_eq21"], opening["gold_total_eq21"]), (D("5000.000"), D("-120.500"), D("4879.500")))

        @contextmanager
        def factory():
            yield self.connection
        report = ReportService(self.h.session, transaction_factory=factory, today=date.today).run("profit", {"from": date.today().isoformat(), "to": date.today().isoformat()})
        labels = {t["label"]: t for t in report["totals"]}
        self.assertIn("ربح الذهب (مكافئ 21)", labels)
        self.assertNotEqual(labels["ربح الذهب (مكافئ 21)"]["value"], "غير متاح", labels["ربح الذهب (مكافئ 21)"]["note"])      # two sheets: a difference can be measured
        parts = {r["part"]: r for r in report["tables"][0]["rows"]}
        self.assertEqual(parts["الإجمالي"]["opening"], D("4879.500"))
        self.assertEqual(parts["الإجمالي"]["change"], parts["الإجمالي"]["closing"] - parts["الإجمالي"]["opening"])


if __name__ == "__main__":
    unittest.main()
