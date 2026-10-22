"""Explicit opt-in rehearsal check (migration 069): the manageable income / expense categories on REAL MySQL (masgold_rehearsal, level >= 69).

Every write runs inside ONE transaction that is ALWAYS rolled back, so the seeded list and the financial operations of the rehearsal database are never changed."""
from contextlib import contextmanager
import os
import unittest
from uuid import uuid4

from mysql.connector import Error as MySqlError

from services.cash_category_service import SEED, CashCategoryService, CategoryError
from tests.test_market_trip_book_mysql import BookCoreMySqlTests


@unittest.skipUnless(os.environ.get("MASGOLD_BOOK_REHEARSAL") == "1", "explicit rehearsal-only opt-in")
class CashCategoriesMySqlTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        BookCoreMySqlTests.setUpClass()
        cls.h = BookCoreMySqlTests("test_the_migration_is_registered_and_the_objects_exist")
        with cls.h.tx() as c:
            q = c.cursor()
            q.execute("SELECT MAX(version) FROM schema_migrations")
            assert int(q.fetchone()[0]) >= 69
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

    def service(self):
        @contextmanager
        def factory():
            yield self.connection
        return CashCategoryService(self.h.session, transaction_factory=factory)

    def scalar(self, sql, params=()):
        q = self.connection.cursor()
        try:
            q.execute(sql, params)
            return q.fetchone()[0]
        finally:
            q.close()

    def execute(self, sql, params=()):
        q = self.connection.cursor()
        try:
            q.execute(sql, params)
        finally:
            q.close()

    def operation(self, category, kind="MANUAL_WITHDRAWAL"):
        cash = self.scalar("SELECT id FROM cash_accounts ORDER BY id LIMIT 1")
        user = self.scalar("SELECT id FROM users ORDER BY id LIMIT 1")
        column = "to_cash_account_id" if kind == "MANUAL_DEPOSIT" else "from_cash_account_id"
        self.execute(f"INSERT INTO financial_operations (operation_type, category, request_key, request_hash, amount, {column}, performed_by_user_id, occurred_at) "
                     "VALUES (%s, %s, %s, %s, 10.00, %s, %s, NOW())", (kind, category, str(uuid4()), "0" * 64, cash, user))

    def test_the_seed_is_the_ten_categories_of_the_code_and_the_old_rows_still_resolve(self):
        rows = self.service().categories()
        self.assertEqual([(r["code"], r["name"], r["direction"]) for r in rows if r["system"]][:10], [(c, n, d) for c, n, d in SEED])
        orphans = self.scalar("SELECT COUNT(*) FROM financial_operations fo LEFT JOIN cash_categories c ON c.code = fo.category WHERE fo.category IS NOT NULL AND c.code IS NULL")
        self.assertEqual(orphans, 0)

    def test_add_use_and_the_foreign_key_protect_a_used_category_and_nothing_persists(self):
        service = self.service()
        added = service.add("تأمينات تجريبية", "OUT")
        self.operation(added["code"])                                                                         # accepted: it exists
        with self.assertRaises(MySqlError):
            self.operation("NOT_A_CATEGORY")                                                                  # fk_fo_category
        with self.assertRaises(CategoryError) as raised:
            service.delete(added["code"])                                                                     # the service explains it ...
        self.assertEqual(raised.exception.code, "CATEGORY_USED")
        with self.assertRaises(MySqlError):
            self.execute("DELETE FROM cash_categories WHERE code = %s", (added["code"],))                    # ... and the database refuses it too
        self.assertFalse(service.set_active(added["code"], False)["active"])
        renamed = service.rename(added["code"], "تأمينات مغيّرة")
        self.assertEqual(renamed["name"], "تأمينات مغيّرة")
        with self.assertRaises(CategoryError):
            service.add("تأمينات مغيّرة", "OUT")                                                              # unique name

    def test_an_unused_category_is_deleted_and_the_audit_row_is_written(self):
        service = self.service()
        added = service.add("مؤقت للحذف", "BOTH")
        self.assertEqual(service.delete(added["code"]), {"code": added["code"], "deleted": True})
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM cash_categories WHERE code = %s", (added["code"],)), 0)
        self.assertGreaterEqual(self.scalar("SELECT COUNT(*) FROM audit_log WHERE entity_type = 'CASH_CATEGORY' AND entity_reference = %s", (added["code"],)), 2)


if __name__ == "__main__":
    unittest.main()
