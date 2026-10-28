"""Explicit opt-in rehearsal check (migration 087): customer merge / delete and the manual warning flags on REAL MySQL
(masgold_rehearsal, level >= 87). Every write runs inside ONE transaction that is ALWAYS rolled back."""
from contextlib import contextmanager
from decimal import Decimal
import os
import unittest
from uuid import uuid4

from repositories.customer_repository import CustomerRepository
from services.customer_merge_service import CustomerMergeError, CustomerMergeService
from tests import test_market_trip_book_mysql as _book
BookCoreMySqlTests = _book.BookCoreMySqlTests
del _book


@unittest.skipUnless(os.environ.get("MASGOLD_BOOK_REHEARSAL") == "1", "explicit rehearsal-only opt-in")
class CustomerMergeMySqlTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        BookCoreMySqlTests.setUpClass()
        cls.h = BookCoreMySqlTests("test_the_migration_is_registered_and_the_objects_exist")
        with cls.h.tx() as c:
            q = c.cursor()
            q.execute("SELECT MAX(version) FROM schema_migrations")
            assert int(q.fetchone()[0]) >= 87
            q.close()

    def setUp(self):
        from database.connection import DatabaseConnection
        self.connection = DatabaseConnection(self.h.config).connect()
        self.connection.rollback()
        self.connection.start_transaction(isolation_level="REPEATABLE READ")
        self.addCleanup(self._close)
        self.a, self.b, self.c = (self.customer(f"عميل دمج {n} {uuid4().hex[:6]}") for n in "أبج")

    def _close(self):
        self.connection.rollback()
        self.connection.close()

    def customer(self, name):
        q = self.connection.cursor()
        q.execute("INSERT INTO customers (name) VALUES (%s)", (name,))
        cid = str(q.lastrowid)
        q.close()
        return cid

    def factory(self):
        @contextmanager
        def tx():
            yield self.connection
        return tx

    def scalar(self, sql, params=()):
        q = self.connection.cursor()
        try:
            q.execute(sql, params)
            row = q.fetchone()
            return None if row is None else row[0]
        finally:
            q.close()

    def service(self):
        return CustomerMergeService(self.h.session, transaction_factory=self.factory())

    def ledger(self, cid, delta):
        CustomerRepository(self.connection).add_transaction(customer_id=int(cid), type_code="SALE_BALANCE_DUE" if Decimal(delta) > 0 else "CUSTOMER_CASH_IN", amount_delta=Decimal(delta),
                                                            created_by_user_id=None, reference_type="TEST", description="اختبار")

    def balance(self, cid):
        return Decimal(str(self.scalar("SELECT COALESCE(SUM(amount_delta), 0) FROM customer_transactions WHERE customer_id = %s", (cid,))))

    def test_merge_moves_the_ledger_retires_the_source_and_keeps_one_active_flag(self):
        self.ledger(self.a, "500")
        self.ledger(self.b, "-200")
        repo = CustomerRepository(self.connection)
        repo.add_flag(int(self.a), "NOT_COMMITTED", "غير ملتزم", None)
        repo.add_flag(int(self.b), "NOT_COMMITTED", "غير ملتزم", None)
        usage = self.service().usage(self.a)
        self.assertFalse(usage["deletable"])
        preview = self.service().preview(self.a, self.b)
        self.assertEqual(preview["combined_balance"], "300.00")
        result = self.service().merge(self.a, self.b, "نفس الشخص")
        self.assertEqual((result["balance_after"], result["duplicate"]), ("300.00", False))
        self.assertEqual((self.balance(self.a), self.balance(self.b)), (Decimal("0"), Decimal("300")))
        row = self.scalar("SELECT merged_into_customer_id FROM customers WHERE id = %s", (self.a,))
        self.assertEqual(str(row), self.b)
        self.assertEqual(self.scalar("SELECT is_active FROM customers WHERE id = %s", (self.a,)), 0)
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM customer_flags WHERE customer_id = %s AND is_active = TRUE", (self.b,)), 1)
        self.assertTrue(self.service().merge(self.a, self.b, "نفس الشخص")["duplicate"])
        listed = {str(r["customer_id"]) for r in CustomerRepository(self.connection).search_customers("عميل دمج")}
        self.assertIn(self.b, listed)
        self.assertNotIn(self.a, listed)                                            # the merged customer disappears from the list by itself
        with self.assertRaises(CustomerMergeError) as ctx:
            self.service().delete(self.b)                                           # a survivor keeps its trail
        self.assertEqual(ctx.exception.code, "CUSTOMER_IN_USE")

    def test_delete_only_a_customer_nothing_points_at(self):
        self.ledger(self.b, "10")
        with self.assertRaises(CustomerMergeError):
            self.service().delete(self.b)
        self.assertTrue(self.service().usage(self.c)["deletable"])
        self.service().delete(self.c)
        self.assertIsNone(self.scalar("SELECT id FROM customers WHERE id = %s", (self.c,)))

    def test_the_manual_flags_travel_with_the_customer_listing(self):
        repo = CustomerRepository(self.connection)
        repo.add_flag(int(self.a), "NOT_COMMITTED", "غير ملتزم", None)
        repo.add_flag(int(self.a), "NEEDS_FOLLOWUP", "يحتاج متابعة", None)
        row = repo.get_customer_by_id(int(self.a))
        self.assertEqual(set(row["active_manual_flags"].split(",")), {"NOT_COMMITTED", "NEEDS_FOLLOWUP"})
        found = repo.active_flag_types(int(self.a))
        repo.deactivate_flag(found["NOT_COMMITTED"], None, "إلغاء")
        self.assertEqual(repo.get_customer_by_id(int(self.a))["active_manual_flags"], "NEEDS_FOLLOWUP")


if __name__ == "__main__":
    unittest.main()
