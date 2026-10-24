"""A supplier making payment / reversal moves REAL money, so the money views reload at once instead of waiting for the periodic
timer: the top-bar balances (balancesVM), the wallet / bank balances (reviewsVM) and الحسابات المالية (financialAccountsVM).

The real ApplicationController wiring is used; the balance services are counting stand-ins that read the same in-memory
ledgers the payment writes (authoritative re-reads, never local arithmetic). The balance strip's own timer is never started."""
import time
from decimal import Decimal as D
from unittest.mock import patch
from uuid import uuid4
import unittest

from PySide6.QtTest import QTest

from app.formatting import money
from services.correction_service import CorrectionService
from tests import test_invoice_page_qml as harness
from tests.supplier_fakes import AccountsRepo, MANAGER_RIGHTS, OWNER_RIGHTS, PaymentRepo, build_payments
from tests.test_supplier_payments import Corrections, Customers, Invoices

TODAY = "2026-10-09"


class Ledgers:
    """OperationalBalanceService / FinancialAccountService stand-in reading the payment tests' in-memory money ledgers."""

    def __init__(self, db):
        self.db = db
        self.strip_calls = self.account_calls = self.view_calls = 0
        self.slow_first_strip = 0.0
        self.fail = False

    def can_view(self):
        return True

    def cash(self):
        return sum((m["amount_delta"] for m in self.db.cash_moves), D("0.00"))

    def strip(self, **kwargs):
        self.strip_calls += 1
        value, first = self.cash(), self.strip_calls == 1                      # the value is read BEFORE any delay (a stale read)
        if first and self.slow_first_strip:
            time.sleep(self.slow_first_strip)
        if self.fail:
            raise RuntimeError("balance read failed")
        return {"cash_balance": str(value), "old_gold": []}

    def financial_accounts(self, **kwargs):
        self.account_calls += 1
        if self.fail:
            raise RuntimeError("accounts read failed")
        return []

    def cash_view(self, start="", end="", label="", **kwargs):
        self.view_calls += 1
        return {"account": {"name": "Main Safe", "balance": str(self.cash())}}


class SupplierPaymentRefreshTests(harness.InvoicePageTests):
    def setUp(self):
        super().setUp()
        vms = self.controller.operational_viewmodels
        self.svm, self.balances, self.reviews = vms["supplierVM"], vms["balancesVM"], vms["reviewsVM"]
        self.accounts = vms["financialAccountsVM"]
        session = self.controller.session
        self.db, _, self.authorization, suppliers, purchases, self.payments = build_payments(session=session)
        self.svm._service, self.svm._payments = suppliers, self.payments
        self.payments._corrections = CorrectionService(
            session, authorization=self.authorization, transaction_factory=self.db.transaction,
            invoice_repository=Invoices, correction_repository=Corrections, customer_repository=Customers)
        for target, value in (("repositories.supplier_payment_repository.SupplierPaymentRepository", PaymentRepo),
                              ("repositories.financial_account_repository.FinancialAccountRepository", AccountsRepo)):
            started = patch(target, value)
            started.start()
            self.addCleanup(started.stop)
        vms["supplierPurchaseVM"]._service = purchases
        self.balances._timer.stop()                                              # the periodic refresh must play no part
        self.ledgers = Ledgers(self.db)
        self.balances._service = self.ledgers
        self.reviews._balance_service = self.ledgers
        self.accounts._service = self.ledgers
        self.grant(*OWNER_RIGHTS)
        self.supplier = suppliers.create_supplier("مورد التحديث", karat_id=1)
        draft = purchases.create_draft(self.supplier, 1, "INV-1", TODAY, [{"weight": "50", "making_per_gram": "100"}], "", str(uuid4()))
        purchases.post_invoice(draft["id"], str(uuid4()))
        self.svm.selectSupplier(self.supplier)
        self.wait_until(lambda: not self.svm.busy, "supplier loaded")
        self.pay_calls = []
        original = self.payments.pay
        self.payments.pay = lambda *a, **k: self.pay_calls.append(a) or original(*a, **k)

    # ------------------------------------------------------------ helpers
    def settle(self):
        self.wait_until(lambda: not self.svm.busy, "supplier view model idle")
        self.wait_until(lambda: not self.balances._workers and not self.reviews._workers and not self.accounts._workers, "refreshes done")
        QTest.qWait(60)

    def pay(self, amount="1000", method="BANK", bank="5", key=None):
        self.svm.payMaking(self.supplier, method, bank, False, amount, TODAY, "", "", key or str(uuid4()))
        self.settle()

    def counts(self):
        return (self.ledgers.strip_calls, self.ledgers.account_calls, self.ledgers.view_calls)

    # ------------------------------------------------------------ tests
    def test_a_successful_payment_reloads_every_money_view_at_once_without_the_timer(self):
        self.accounts.activate()
        self.settle()
        self.assertFalse(self.balances._timer.isActive())                       # the periodic refresh plays no part
        before = self.counts()
        self.pay("1000")
        self.assertEqual(self.counts(), (before[0] + 1, before[1] + 1, before[2] + 1))
        self.assertEqual(self.balances.cashText, money(D("0.00")))               # Main Safe is not a making-payment source
        self.assertEqual(self.accounts._view["account"]["balance"], "0.00")
        self.assertEqual(len(self.db.bank_moves), 1)
        self.assertEqual(self.svm.errorMessage, "")

    def test_a_bank_payment_also_reloads_the_wallet_and_bank_balances(self):
        before = self.counts()
        self.pay("250", method="BANK", bank="5")
        self.assertEqual((self.ledgers.strip_calls, self.ledgers.account_calls), (before[0] + 1, before[1] + 1))
        self.assertEqual(len(self.db.bank_moves), 1)

    def test_a_reversal_reloads_them_again_and_restores_the_displayed_balance(self):
        self.accounts.activate()
        self.pay("1000")
        before = self.counts()
        self.svm.reversePayment("1", "غلط")
        self.settle()
        self.assertEqual(self.counts(), (before[0] + 1, before[1] + 1, before[2] + 1))
        self.assertEqual(self.balances.cashText, money(D("0.00")))
        self.assertEqual(self.accounts._view["account"]["balance"], "0.00")
        self.assertEqual(len(self.db.corrections), 1)

    def test_off_page_the_financial_view_reads_nothing_and_the_next_entry_loads_current_data(self):
        self.pay("700")
        self.assertEqual(self.ledgers.view_calls, 0)                            # not on the page: marked stale, not read
        self.accounts.activate()
        self.settle()
        self.assertEqual(self.ledgers.view_calls, 1)
        self.assertEqual(self.accounts._view["account"]["balance"], "0.00")
        self.assertEqual(len(self.db.bank_moves), 1)

    def test_a_refused_payment_or_reversal_notifies_nothing(self):
        self.accounts.activate()
        self.settle()
        self.pay("10", method="BANK", bank="7")                                  # inactive account: refused
        self.assertIn("غير نشط", self.svm.errorMessage)
        self.pay("0")                                                            # invalid amount: refused
        self.pay("1000")
        before = self.counts()
        self.svm._payments = build_payments_manager(self)
        self.svm.reversePayment("1", "غلط")                                      # a manager may not reverse
        self.settle()
        self.assertTrue(self.svm.errorMessage)
        self.assertEqual(self.counts(), before)
        self.assertEqual(self.db.corrections, [])

    def test_a_refresh_requested_during_a_running_load_still_ends_with_a_fresh_read(self):
        self.ledgers.slow_first_strip = 0.7
        self.balances.refresh()                                                  # a load that reads the OLD value and is slow
        QTest.qWait(80)
        self.pay("1000")                                                         # the payment commits while that load is running
        self.wait_until(lambda: self.ledgers.strip_calls >= 2, "second read")
        self.settle()
        QTest.qWait(900)                                                         # the slow stale read finally returns
        self.assertEqual(self.ledgers.strip_calls, 2)
        self.assertEqual(self.balances.cashText, money(D("0.00")))                # the stale result did not win

    def test_a_failing_refresh_is_not_presented_as_a_failed_payment_and_nothing_is_resubmitted(self):
        self.ledgers.fail = True
        self.pay("1000")
        self.assertEqual((len(self.db.payments), len(self.pay_calls)), (1, 1))
        self.assertEqual(self.svm.errorMessage, "")
        self.assertIn("SM000001", self.svm.message)
        self.assertNotEqual(self.balances.errorMessage, "")                      # the failure belongs to the balance view only
        QTest.qWait(200)
        self.assertEqual((len(self.db.payments), len(self.pay_calls)), (1, 1))   # no automatic resubmission


def build_payments_manager(case):
    """A payment service for a manager (may pay, may not reverse) over the same database."""
    from services.supplier_payment_service import SupplierPaymentService
    from tests.auth_fakes import FakeAuthorization
    session = case.controller.session
    authorization = FakeAuthorization(session, MANAGER_RIGHTS)
    return SupplierPaymentService(
        session, transaction_factory=case.db.transaction, repository=PaymentRepo, accounts=AccountsRepo,
        authorization=authorization,
        corrections=CorrectionService(session, authorization=authorization, transaction_factory=case.db.transaction,
                                      invoice_repository=Invoices, correction_repository=Corrections, customer_repository=Customers))


# The base class carries the invoice-page tests: only this module's own tests are collected here.
for _inherited in [n for n in dir(harness.InvoicePageTests) if n.startswith("test_")]:
    setattr(SupplierPaymentRefreshTests, _inherited, None)


if __name__ == "__main__":
    unittest.main()
