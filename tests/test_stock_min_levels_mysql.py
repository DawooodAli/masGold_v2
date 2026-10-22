"""Explicit opt-in rehearsal check (migration 072): the minimum stock rules and the alert listing on REAL MySQL (masgold_rehearsal, level >= 72).

Every write runs inside ONE transaction that is ALWAYS rolled back, so no rule or audit row persists."""
from contextlib import contextmanager
from datetime import date
from decimal import Decimal as D
import os
import unittest

from mysql.connector import Error as MySqlError

from services.report_service import ReportService
from services.stock_min_level_service import LevelError, StockMinLevelService
from tests.test_market_trip_book_mysql import BookCoreMySqlTests


@unittest.skipUnless(os.environ.get("MASGOLD_BOOK_REHEARSAL") == "1", "explicit rehearsal-only opt-in")
class StockMinLevelsMySqlTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        BookCoreMySqlTests.setUpClass()
        cls.h = BookCoreMySqlTests("test_the_migration_is_registered_and_the_objects_exist")
        with cls.h.tx() as c:
            q = c.cursor()
            q.execute("SELECT MAX(version) FROM schema_migrations")
            assert int(q.fetchone()[0]) >= 72
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

    def ids(self):
        return self.scalar("SELECT id FROM item_types WHERE is_active = TRUE ORDER BY id LIMIT 1"), self.scalar("SELECT id FROM karats WHERE is_active = TRUE ORDER BY id LIMIT 1")

    def test_a_rule_is_saved_replaced_listed_alerted_and_deleted_and_nothing_persists(self):
        service = StockMinLevelService(self.h.session, transaction_factory=self.factory)
        type_id, karat_id = self.ids()
        first = service.save(type_id, karat_id, "1000000", "")["id"]                                       # an absurd minimum: the type is certainly below it
        again = service.save(type_id, karat_id, "", "2500.5")["id"]
        self.assertEqual(first, again)                                                                       # one rule per (type, karat): replaced
        row = [r for r in service.overview()["levels"] if r["id"] == str(first)][0]
        self.assertEqual((row["min_pieces"], row["min_grams"]), (None, D("2500.500")))
        report = ReportService(self.h.session, transaction_factory=self.factory, today=date.today).run("alerts", {})
        below = next(t for t in report["tables"] if t["key"] == "below_min")["rows"]
        self.assertTrue(any("جرام" in r["short"] for r in below))                                            # the rule shows up in the inventory alerts
        self.assertEqual(service.remove(first), {"id": str(first), "deleted": True})
        with self.assertRaises(LevelError):
            service.remove(first)
        q = self.connection.cursor()
        try:
            with self.assertRaises(MySqlError):
                q.execute("INSERT INTO stock_min_levels (item_type_id, karat_id, min_pieces, min_grams) VALUES (%s, %s, NULL, NULL)", (type_id, karat_id))        # chk_stock_min_amounts
            with self.assertRaises(MySqlError):
                q.execute("INSERT INTO stock_min_levels (item_type_id, karat_id, min_pieces) VALUES (%s, %s, 0)", (type_id, karat_id))
        finally:
            q.close()
        self.assertGreaterEqual(self.scalar("SELECT COUNT(*) FROM audit_log WHERE entity_type = 'STOCK_MIN_LEVEL' AND entity_id = %s", (first,)), 3)


if __name__ == "__main__":
    unittest.main()
