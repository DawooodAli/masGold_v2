"""Explicit opt-in rehearsal check: the employee KPI report, the cash-flow report and the new expense categories (migration 068) on REAL MySQL (masgold_rehearsal, level >= 68).

The reports only read. The category constraint is exercised inside ONE transaction that is ALWAYS rolled back, so no operation persists."""
from contextlib import contextmanager
from datetime import date
from decimal import Decimal as D
import os
import unittest
from uuid import uuid4

from mysql.connector import Error as MySqlError

from services.report_service import ReportService
from tests.test_market_trip_book_mysql import BookCoreMySqlTests

WIDE = {"from": "2020-01-01", "to": "2030-12-31"}


@unittest.skipUnless(os.environ.get("MASGOLD_BOOK_REHEARSAL") == "1", "explicit rehearsal-only opt-in")
class StaffAndCashflowMySqlTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        BookCoreMySqlTests.setUpClass()
        cls.h = BookCoreMySqlTests("test_the_migration_is_registered_and_the_objects_exist")
        with cls.h.tx() as c:
            q = c.cursor()
            q.execute("SELECT MAX(version) FROM schema_migrations")
            assert int(q.fetchone()[0]) >= 68
            q.close()
        cls.service = ReportService(cls.h.session, today=date.today)

    def test_the_employee_report_equals_an_independent_sql_aggregate_per_employee_and_karat(self):
        result = self.service.run("employees", dict(WIDE))
        reported = {}
        for t in result["tables"]:
            for r in t["rows"]:
                reported[(t["title"], int(r["karat"].split()[-1]))] = r["sold"]
        rows = self.h.query(
            "SELECT COALESCE(e.name, 'غير محدد') AS employee, k.karat_value, SUM(il.weight_grams) AS grams "
            "FROM invoice_lines il JOIN invoices iv ON iv.id = il.invoice_id JOIN karats k ON k.id = il.karat_id LEFT JOIN employees e ON e.id = iv.employee_id "
            "WHERE il.line_type IN ('SELL_INVENTORY','SELL_OLD_GOLD','SELL_UNIDENTIFIED') AND iv.invoice_status = 'FINALIZED' "
            "AND NOT EXISTS (SELECT 1 FROM transaction_corrections c WHERE c.source_type = 'INVOICE' AND c.source_id = iv.id AND c.correction_type = 'FULL_REVERSAL') "
            "GROUP BY employee, k.karat_value")
        expected = {(r["employee"], int(r["karat_value"])): D(str(r["grams"])) for r in rows}
        for key, grams in expected.items():
            self.assertEqual(reported.get(key), grams, key)
        self.assertEqual({k for k, v in reported.items() if v}, set(expected))

    def test_the_cashflow_report_accounts_for_every_movement_of_the_period(self):
        result = self.service.run("cashflow", dict(WIDE))
        in_, out = D("0"), D("0")
        for key in ("operating", "internal"):
            for r in next(t for t in result["tables"] if t["key"] == key)["rows"]:
                in_ += r["in"]
                out += r["out"]
        sql = self.h.query(
            "SELECT COALESCE(SUM(IF(a > 0, a, 0)), 0) AS money_in, COALESCE(-SUM(IF(a < 0, a, 0)), 0) AS money_out FROM ("
            " SELECT m.amount_delta AS a FROM cash_movements m JOIN cash_movement_types t ON t.id = m.movement_type_id WHERE t.code <> 'OPENING_BALANCE'"
            " UNION ALL SELECT m.amount_delta FROM bank_movements m JOIN bank_movement_types t ON t.id = m.movement_type_id WHERE t.code <> 'OPENING_BALANCE') x")[0]
        self.assertEqual((in_, out), (D(str(sql["money_in"])), D(str(sql["money_out"]))))                      # nothing lost, nothing counted twice

    def test_the_new_categories_are_accepted_for_withdrawals_only_and_nothing_persists(self):
        from database.connection import DatabaseConnection
        connection = DatabaseConnection(self.h.config).connect()
        try:
            connection.rollback()
            connection.start_transaction(isolation_level="REPEATABLE READ")
            q = connection.cursor()
            q.execute("SELECT id FROM cash_accounts ORDER BY id LIMIT 1")
            cash_id = q.fetchone()[0]
            q.execute("SELECT id FROM users ORDER BY id LIMIT 1")
            user_id = q.fetchone()[0]

            def insert(kind, category):
                column = "to_cash_account_id" if kind == "MANUAL_DEPOSIT" else "from_cash_account_id"
                q.execute(f"INSERT INTO financial_operations (operation_type, category, request_key, request_hash, amount, {column}, performed_by_user_id, occurred_at) "
                          "VALUES (%s, %s, %s, %s, 10.00, %s, %s, NOW())", (kind, category, str(uuid4()), "0" * 64, cash_id, user_id))
            for category in ("UTILITIES", "TRANSPORT", "MAINTENANCE", "HOSPITALITY", "STAFF_ADVANCE"):
                insert("MANUAL_WITHDRAWAL", category)
                with self.assertRaises(MySqlError):
                    insert("MANUAL_DEPOSIT", category)                                                         # an expense category is never income
            with self.assertRaises(MySqlError):
                insert("MANUAL_WITHDRAWAL", "NOT_A_CATEGORY")
            q.close()
        finally:
            connection.rollback()
            connection.close()
        left = self.h.query("SELECT COUNT(*) AS n FROM financial_operations WHERE category IN ('UTILITIES','TRANSPORT','MAINTENANCE','HOSPITALITY','STAFF_ADVANCE')")[0]["n"]
        self.assertEqual(left, 0)


if __name__ == "__main__":
    unittest.main()
