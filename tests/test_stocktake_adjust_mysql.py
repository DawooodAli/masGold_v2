"""Explicit opt-in rehearsal check (migration 073): the stocktake write-off on REAL MySQL (masgold_rehearsal, level >= 73).

Every write runs inside ONE transaction that is ALWAYS rolled back, so no piece, movement or approval persists."""
from contextlib import contextmanager
import os
import unittest

from services.stocktake_adjust_service import StocktakeAdjustService, StocktakeError
from tests.test_market_trip_book_mysql import BookCoreMySqlTests


@unittest.skipUnless(os.environ.get("MASGOLD_BOOK_REHEARSAL") == "1", "explicit rehearsal-only opt-in")
class StocktakeAdjustMySqlTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        BookCoreMySqlTests.setUpClass()
        cls.h = BookCoreMySqlTests("test_the_migration_is_registered_and_the_objects_exist")
        with cls.h.tx() as c:
            q = c.cursor()
            q.execute("SELECT MAX(version) FROM schema_migrations")
            assert int(q.fetchone()[0]) >= 73
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

    def rows(self, sql, params=()):
        q = self.connection.cursor(dictionary=True)
        try:
            q.execute(sql, params)
            return q.fetchall()
        finally:
            q.close()

    def service(self):
        from tests.auth_fakes import FakeAuthorization
        session = self.h.session
        return StocktakeAdjustService(session, transaction_factory=self.factory, authorization=FakeAuthorization(session))

    def test_single_pieces_leave_the_stock_with_a_movement_and_a_stored_record_and_nothing_persists(self):
        pieces = self.rows("SELECT id, weight_grams FROM inventory_items WHERE inventory_status = 'IN_STOCK' ORDER BY id LIMIT 2")
        if len(pieces) < 2:
            self.skipTest("the rehearsal database has fewer than two pieces in stock")
        ids = [str(p["id"]) for p in pieces]
        result = self.service().write_off_pieces(ids, "MISSING", "", True, "mysql-stocktake-0001")
        self.assertEqual(result["written_off_count"], 2)
        marks = ", ".join(["%s"] * 2)
        self.assertEqual({r["inventory_status"] for r in self.rows(f"SELECT inventory_status FROM inventory_items WHERE id IN ({marks})", tuple(ids))}, {"REMOVED"})
        self.assertEqual(len(self.rows("SELECT m.id FROM inventory_movements m JOIN inventory_movement_types t ON t.id = m.movement_type_id WHERE t.code = 'STOCKTAKE_WRITE_OFF' "
                                       f"AND m.inventory_item_id IN ({marks})", tuple(ids))), 2)
        record = self.rows("SELECT expected_count, found_count, written_off_count, reason_code FROM stocktake_adjustments WHERE code = %s", (result["code"],))[0]
        self.assertEqual((record["expected_count"], record["found_count"], record["written_off_count"], record["reason_code"]), (2, 0, 2, "MISSING"))
        self.assertEqual(len(self.rows("SELECT id FROM stocktake_adjustment_items WHERE adjustment_id = %s", (result["id"],))), 2)
        again = self.service().write_off_pieces(ids, "MISSING", "", True, "mysql-stocktake-0001")      # an exact replay returns the original
        self.assertTrue(again["replayed"])
        with self.assertRaises(StocktakeError) as raised:                                              # the pieces are no longer in stock
            self.service().write_off_pieces(ids, "MISSING", "", True, "mysql-stocktake-0002")
        self.assertEqual(raised.exception.code, "STOCK_CHANGED")


if __name__ == "__main__":
    unittest.main()
