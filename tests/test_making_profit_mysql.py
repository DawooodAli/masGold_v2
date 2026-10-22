"""Explicit opt-in rehearsal check (migration 070): the category making costs, their freeze into a new piece and the «ربح المصنعية» report on REAL MySQL (masgold_rehearsal, level >= 70).

Every write runs inside ONE transaction that is ALWAYS rolled back, so no cost, piece or audit row persists."""
from contextlib import contextmanager
from datetime import date
from decimal import Decimal as D
import os
import unittest

from mysql.connector import Error as MySqlError

from services.category_cost_service import CategoryCostService, CostError
from services.report_service import ReportService
from tests.test_market_trip_book_mysql import BookCoreMySqlTests

WIDE = {"from": "2020-01-01", "to": "2030-12-31"}


@unittest.skipUnless(os.environ.get("MASGOLD_BOOK_REHEARSAL") == "1", "explicit rehearsal-only opt-in")
class MakingProfitMySqlTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        BookCoreMySqlTests.setUpClass()
        cls.h = BookCoreMySqlTests("test_the_migration_is_registered_and_the_objects_exist")
        with cls.h.tx() as c:
            q = c.cursor()
            q.execute("SELECT MAX(version) FROM schema_migrations")
            assert int(q.fetchone()[0]) >= 70
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

    @contextmanager
    def factory(self):
        yield self.connection

    def scalar(self, sql, params=()):
        q = self.connection.cursor()
        try:
            q.execute(sql, params)
            return q.fetchone()[0]
        finally:
            q.close()

    def test_the_cost_is_stored_audited_validated_and_nothing_persists(self):
        service = CategoryCostService(self.h.session, transaction_factory=self.factory)
        category = self.scalar("SELECT id FROM item_categories WHERE is_active = TRUE ORDER BY sort_order, code LIMIT 1")
        self.assertEqual(service.set_cost(category, "125.5")["cost"], D("125.50"))
        self.assertEqual(self.scalar("SELECT making_cost_per_gram FROM item_category_costs WHERE category_id = %s", (category,)), D("125.50"))
        self.assertEqual(service.set_cost(category, "130")["cost"], D("130.00"))                                    # replaced, still one row
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM item_category_costs WHERE category_id = %s", (category,)), 1)
        with self.assertRaises(CostError):
            service.set_cost(category, "-5")
        self.assertGreaterEqual(self.scalar("SELECT COUNT(*) FROM audit_log WHERE entity_type = 'ITEM_CATEGORY_COST' AND entity_id = %s", (category,)), 2)
        service.clear_cost(category)
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM item_category_costs WHERE category_id = %s", (category,)), 0)
        q = self.connection.cursor()
        try:
            with self.assertRaises(MySqlError):
                q.execute("INSERT INTO item_category_costs (category_id, making_cost_per_gram) VALUES (%s, -1)", (category,))      # chk_category_cost_amount
            with self.assertRaises(MySqlError):
                q.execute("INSERT INTO item_category_costs (category_id, making_cost_per_gram) VALUES (999999, 1)")                  # fk to item_categories
        finally:
            q.close()

    def test_the_margin_equals_an_independent_sql_aggregate_with_a_category_cost_in_force(self):
        service = CategoryCostService(self.h.session, transaction_factory=self.factory)
        for category in [r for r in self.h_categories()]:
            service.set_cost(category, "120")                                                                       # an estimate for every piece that has no stored cost
        report = ReportService(self.h.session, transaction_factory=self.factory, today=date.today).run("makingprofit", dict(WIDE))
        card = {t["label"]: t["value"] for t in report["totals"]}
        expected = self.scalar(
            "SELECT COALESCE(SUM(il.making_total - ROUND(COALESCE(ii.acquisition_making_per_gram, cc.making_cost_per_gram) * il.weight_grams, 2)), 0) "
            "FROM invoice_lines il JOIN invoices iv ON iv.id = il.invoice_id JOIN inventory_items ii ON ii.id = il.inventory_item_id "
            "LEFT JOIN item_category_costs cc ON cc.category_id = ii.category_id "
            "WHERE il.line_type = 'SELL_INVENTORY' AND iv.invoice_status = 'FINALIZED' AND il.making_total IS NOT NULL "
            "AND COALESCE(ii.acquisition_making_per_gram, cc.making_cost_per_gram) IS NOT NULL "
            "AND NOT EXISTS (SELECT 1 FROM transaction_corrections c WHERE c.source_type = 'INVOICE' AND c.source_id = iv.id AND c.correction_type = 'FULL_REVERSAL')")
        self.assertEqual(card["الفرق على المصنعية"], D(str(expected)).quantize(D("0.01")))

    def h_categories(self):
        q = self.connection.cursor()
        try:
            q.execute("SELECT id FROM item_categories WHERE is_active = TRUE")
            return [r[0] for r in q.fetchall()]
        finally:
            q.close()


if __name__ == "__main__":
    unittest.main()
