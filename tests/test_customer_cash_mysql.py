"""Explicit opt-in rehearsal check (migration 086): customer cash in / out on REAL MySQL (masgold_rehearsal, level >= 86).

Every write runs inside ONE transaction that is ALWAYS rolled back, so the rehearsal database keeps its rows."""
from contextlib import contextmanager
from decimal import Decimal
import os
import unittest
from uuid import uuid4

from services.correction_service import CorrectionService, FO
from services.financial_account_service import FinancialAccountService
from tests import test_market_trip_book_mysql as _book
BookCoreMySqlTests = _book.BookCoreMySqlTests
del _book


@unittest.skipUnless(os.environ.get("MASGOLD_BOOK_REHEARSAL") == "1", "explicit rehearsal-only opt-in")
class CustomerCashMySqlTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        BookCoreMySqlTests.setUpClass()
        cls.h = BookCoreMySqlTests("test_the_migration_is_registered_and_the_objects_exist")
        with cls.h.tx() as c:
            q = c.cursor()
            q.execute("SELECT MAX(version) FROM schema_migrations")
            assert int(q.fetchone()[0]) >= 86
            q.close()

    def setUp(self):
        from database.connection import DatabaseConnection
        self.connection = DatabaseConnection(self.h.config).connect()
        self.connection.rollback()
        self.connection.start_transaction(isolation_level="REPEATABLE READ")
        self.addCleanup(self._close)
        q = self.connection.cursor()
        q.execute("INSERT INTO customers (name) VALUES (%s)", (f"عميل تجريبي {uuid4().hex[:8]}",))      # rolled back with everything else
        self.customer = str(q.lastrowid)
        q.close()

    def _close(self):
        self.connection.rollback()
        self.connection.close()

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

    def balance(self):
        return Decimal(str(self.scalar("SELECT COALESCE(SUM(amount_delta), 0) FROM customer_transactions WHERE customer_id = %s", (self.customer,))))

    def test_in_and_out_move_the_account_and_the_statement_and_the_reversal_takes_them_back(self):
        service = FinancialAccountService(self.h.session, transaction_factory=self.factory())
        account = self.scalar("SELECT id FROM cash_accounts WHERE is_active = TRUE ORDER BY id LIMIT 1")
        cash_before = Decimal(str(self.scalar("SELECT COALESCE(SUM(amount_delta), 0) FROM cash_movements WHERE cash_account_id = %s", (account,))))
        before = self.balance()
        result = service.customer_cash("IN", self.customer, f"CASH:{account}", "700", "اختبار", str(uuid4()))
        self.assertEqual(self.balance(), before - Decimal("700"))                                   # the statement moved toward للعميل
        out = service.customer_cash("OUT", self.customer, f"CASH:{account}", "300", "", str(uuid4()))
        view = service.cash_view("2000-01-01", "2100-01-01")                                         # the read model names the customer on both rows
        names = {r["customer"] for r in view["in"]["rows"] + view["out"]["rows"] if r["reference"].startswith("FO")}
        self.assertTrue(any(n.startswith("عميل تجريبي") for n in names), names)
        self.assertEqual(self.balance(), before - Decimal("400"))
        cash_now = Decimal(str(self.scalar("SELECT COALESCE(SUM(amount_delta), 0) FROM cash_movements WHERE cash_account_id = %s", (account,))))
        self.assertEqual(cash_now, cash_before + Decimal("400"))
        corrector = CorrectionService(self.h.session, transaction_factory=self.factory())
        corrector.reverse(FO, out["operation_id"], "عكس اختبار")
        corrector.reverse(FO, result["operation_id"], "عكس اختبار")
        self.assertEqual(self.balance(), before)
        self.assertEqual(Decimal(str(self.scalar("SELECT COALESCE(SUM(amount_delta), 0) FROM cash_movements WHERE cash_account_id = %s", (account,)))), cash_before)

        q = self.connection.cursor()
        q.execute("INSERT INTO customers (name) VALUES (%s)", (f"عميل تجريبي آخر {uuid4().hex[:8]}",))
        other = str(q.lastrowid)
        q.close()
        service.customer_cash("OUT", other, f"CASH:{account}", "50", "", str(uuid4()))                 # the newest movement
        service.customer_cash("OUT", self.customer, f"CASH:{account}", "10", "", str(uuid4()))         # now THIS one is the newest
        lists = service.customer_balances("عميل تجريبي")
        ids = [r["customer_id"] for r in lists["owes"] + lists["owed"]]
        self.assertTrue(isinstance(lists["owes"], list) and isinstance(lists["owed"], list))
        mine = [r["customer_id"] for r in lists["owes"]]
        if self.customer in mine and other in mine:
            self.assertLess(mine.index(self.customer), mine.index(other))                              # newest first, never by name

if __name__ == "__main__":
    unittest.main()
