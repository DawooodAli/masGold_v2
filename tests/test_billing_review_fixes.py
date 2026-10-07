"""Owner manual-review fixes at the SERVICE level (no QML): the category manager-approval threshold,
EGP-5 rounding of the final customer money, and inactive payment methods at finalize."""
import copy
from decimal import Decimal as D
import unittest

from services.invoice_service import InvoiceDraft, InvoiceError, InvoiceService, PaymentInput, round_to_egp5
from tests.test_invoice_service import InvoiceServiceTestCase, cash, disc, old, sale

# Item 100: 21K 10.500 g × SHOP_SELL 6232 = 65,436.00 gold value; making = total − 65,436.


class Line:
    def __init__(self, making, weight):
        self.making_total, self.weight_grams = D(making), D(weight)


class ThresholdBoundaryTests(unittest.TestCase):
    """Category A: sale 200–400, manager approval strictly BELOW 250 (per gram)."""
    RANGES = {1: {"code": "A", "sale_making_min_per_gram": D("200"), "sale_making_max_per_gram": D("400"),
                  "manager_approval_below_per_gram": D("250")}}

    def group(self, average):
        return InvoiceService._making_groups([Line(D(average) * 10, "10.000")], [1], self.RANGES)[0]

    def test_boundaries(self):
        for average, needs in (("300", False), ("250", False), ("249.99", True), ("220", True)):
            with self.subTest(average):
                group = self.group(average)
                self.assertEqual(group["needs_approval"], needs)
                self.assertEqual(group["status"], "WITHIN_RANGE")             # 220 is inside 200–400
        self.assertEqual(self.group("190")["status"], "OUT_OF_RANGE")         # hard minimum: its own rule

    def test_no_threshold_configured_never_needs_approval(self):
        ranges = {1: dict(self.RANGES[1], manager_approval_below_per_gram=None)}
        self.assertFalse(InvoiceService._making_groups([Line("2200", "10")], [1], ranges)[0]["needs_approval"])


class ManagerApprovalTests(InvoiceServiceTestCase):
    def setUp(self):
        super().setUp()
        self.store.categories[1].update(sale_making_min_per_gram=D("200"), sale_making_max_per_gram=D("400"),
                                        manager_approval_below_per_gram=D("250"))
        for item in self.store.items.values():
            item.setdefault("category_id", 1)

    def draft(self, total):
        return InvoiceDraft(5, "12", (sale(),), (), (cash(total),), invoice_total=total)

    def test_below_threshold_is_refused_without_a_manager_even_bypassing_the_screen(self):
        # 67,745 − 65,436 = 2,309 making / 10.5 g = 219.90 / g  (below 250)
        preview = self.service.preview(self.draft("67745"))
        self.assertTrue(preview["making_needs_approval"])
        self.assertIn("MAKING_APPROVAL_REQUIRED", [p["code"] for p in preview["blocking_problems"]])
        self.assertRefused("MAKING_APPROVAL_REQUIRED", self.draft("67745"))
        self.assertEqual(self.store.reviews, [])

    def test_just_below_250_is_refused_and_above_is_normal(self):
        self.assertRefused("MAKING_APPROVAL_REQUIRED", self.draft("68060"))      # 2,624 / 10.5 = 249.90
        result = self.finalize(self.draft("68065"))                               # 2,629 / 10.5 = 250.38
        self.assertFalse(result["making_review_required"])
        self.assertEqual(self.store.reviews, [])

    def as_manager(self):
        permissions = self.permissions | {"INVOICE_SETTLEMENT_APPROVE"}
        self.authorization.permissions = permissions
        self.session._actor = self.session.current_actor().__class__(
            17, 7, "مدير", permissions, self.session.current_actor().generation)

    def test_the_right_to_approve_is_not_an_approval(self):
        self.as_manager()
        preview = self.service.preview(self.draft("67745"))
        self.assertTrue(preview["making_needs_approval"])                    # still needs the explicit act
        self.assertTrue(preview["making_can_confirm"])
        self.assertEqual(preview["making_approval_category"], "A")
        self.assertRefused("MAKING_APPROVAL_REQUIRED", self.draft("67745"))
        self.assertEqual(self.store.reviews, [])

    def test_explicit_confirmation_records_the_actual_approver(self):
        self.as_manager()
        draft = InvoiceDraft(5, "12", (sale(),), (), (cash("67745"),), invoice_total="67745", making_confirmed=True)
        self.assertFalse(self.service.preview(draft)["making_needs_approval"])
        result = self.finalize(draft)
        self.assertTrue(result["making_review_required"])
        review = next(r for r in self.store.reviews if r["review_type"] == "MAKING_PRICE")
        self.assertEqual((review["details"]["making_approved_by_user_id"], review["details"]["making_approval_kind"]),
                         (17, "CONFIRMED_BY_ACTOR"))
        self.assertIn("أقل من حد موافقة المدير", review["summary"])

    def test_a_confirmation_without_the_right_approves_nothing(self):
        draft = InvoiceDraft(5, "12", (sale(),), (), (cash("67745"),), invoice_total="67745", making_confirmed=True)
        self.assertFalse(self.service.preview(draft)["making_can_confirm"])
        self.assertRefused("MAKING_APPROVAL_REQUIRED", draft)


class EgpFiveRoundingTests(InvoiceServiceTestCase):
    def test_round_to_nearest_five(self):
        for exact, final in (("60272.15", "60270.00"), ("60272.50", "60275.00"), ("60272.49", "60270.00"),
                             ("18341.68", "18340.00"), ("6113.89", "6115.00"), ("25035", "25035.00")):
            with self.subTest(exact):
                self.assertEqual(round_to_egp5(D(exact)), D(final))

    def test_sale_total_must_be_a_multiple_of_five(self):
        self.assertRefused("TOTAL_NOT_MULTIPLE_OF_5",
                           InvoiceDraft(5, "12", (sale(),), (), (cash("67011"),), invoice_total="67011"))
        self.finalize(InvoiceDraft(5, "12", (sale(),), (), (cash("67010"),), invoice_total="67010"))

    def old_only(self, weight, paid):
        return InvoiceDraft(5, "12", (), (old(1, weight, **disc()),), (cash(paid),))

    def test_old_gold_final_amount_rounds_down_automatically(self):
        # 3 g × 6207 = 18,621 − 1.5% (279.32) = 18,341.68 exact → the customer receives 18,340.
        preview = self.service.preview(self.old_only("3", "18340"))
        self.assertEqual((preview["full_amount"], preview["full_amount_exact"]), ("18340.00", "18341.68"))
        self.assertTrue(preview["settlement_rounding"])
        self.assertEqual(preview["blocking_problems"], [])
        self.finalize(self.old_only("3", "18340"))
        adjustment = self.store.adjustments[0]
        self.assertEqual(abs(adjustment["amount_signed"]), D("1.68"))
        self.assertEqual(adjustment["reason"], "تقريب المبلغ النهائي لأقرب 5 جنيه")
        self.assertEqual([abs(m["amount_delta"]) for m in self.store.cash_movements], [D("18340.00")])
        self.assertEqual(self.store.ledger, [])                                     # never a balance
        self.assertFalse([r for r in self.store.reviews if r["review_type"] == "CASH_SETTLEMENT"])

    def test_old_gold_final_amount_rounds_up_automatically(self):
        # 1 g × 6207 = 6,207 − 1.5% (93.11) = 6,113.89 exact → the customer receives 6,115.
        preview = self.service.preview(self.old_only("1", "6115"))
        self.assertEqual((preview["full_amount"], preview["full_amount_exact"]), ("6115.00", "6113.89"))
        self.assertEqual(preview["blocking_problems"], [])
        self.finalize(self.old_only("1", "6115"))
        self.assertEqual(abs(self.store.adjustments[0]["amount_signed"]), D("1.11"))

    def test_money_that_is_not_a_multiple_of_five_is_refused(self):
        self.assertRefused("PAYMENT_NOT_MULTIPLE_OF_5", self.old_only("3", "18341.68"))


class RoundedSettlementAllocationTests(InvoiceServiceTestCase):
    """Old gold only: money + customer account add up to the FINAL EGP-5 amount, never to the exact value."""

    def with_price(self, price):
        from unittest.mock import patch as patched
        from tests import test_invoice_service as fixture
        return patched.dict(fixture.PRICES, {1: (D(price), D("9200.0000"))})

    @staticmethod
    def draft(paid, account=None):
        extra = {"account_amount": account, "account_direction": "SHOP_OWES"} if account else {}
        return InvoiceDraft(5, "12", (), (old(1, "1", **disc()),), (cash(paid),), **extra)

    def test_exact_9005_22_rounds_to_9005_and_the_credit_is_5_not_5_22(self):
        with self.with_price("9142.3600"):                       # 1 g − 1.5% = 9,005.22 exact
            preview = self.service.preview(self.draft("9000"))
            self.assertEqual((preview["old_gold_total"], preview["final_settlement_amount"]), ("9005.22", "9005.00"))
            # What is left to the customer is read against the FINAL amount: 9,005 − 9,000 = 5.00.
            self.assertEqual((preview["gap_signed"], preview["difference_signed"]), ("-5.22", "-5.00"))
            # The exact fraction must not become a customer balance.
            self.assertRefused("ACCOUNT_AMOUNT_NOT_ROUNDED", self.draft("9000", "5.22"))
            self.assertEqual(self.store.ledger, [])
            ok = self.service.preview(self.draft("9000", "5"))
            self.assertEqual(ok["blocking_problems"], [])
            self.assertTrue(ok["settlement_rounding"])
            self.assertEqual((ok["rounding_difference"], ok["difference_signed"]), ("0.22", "0.00"))
            self.finalize(self.draft("9000", "5"))
            self.assertEqual([(m["type_code"], m["amount_delta"]) for m in self.store.ledger],
                             [("BUY_BALANCE_DUE", D("-5.00"))])
            self.assertEqual([abs(m["amount_delta"]) for m in self.store.cash_movements], [D("9000.00")])
            self.assertEqual(abs(self.store.adjustments[0]["amount_signed"]), D("0.22"))   # audit keeps the exact
            # real money + account == the rounded final settlement
            self.assertEqual(D("9000.00") + abs(self.store.ledger[0]["amount_delta"]), D("9005.00"))

    def test_everything_on_the_account_is_the_final_amount_too(self):
        with self.with_price("9142.3600"):
            self.assertRefused("ACCOUNT_AMOUNT_NOT_ROUNDED", InvoiceDraft(
                5, "12", (), (old(1, "1", **disc()),), (), account_amount="9005.22", account_direction="SHOP_OWES"))
            self.finalize(InvoiceDraft(5, "12", (), (old(1, "1", **disc()),), (), account_amount="9005",
                                       account_direction="SHOP_OWES"))
            self.assertEqual(self.store.ledger[0]["amount_delta"], D("-9005.00"))

    def test_rounded_up_final_amount_with_account_part(self):
        with self.with_price("9141.9100"):                       # exact 9,004.78 → final 9,005
            self.assertRefused("ACCOUNT_AMOUNT_NOT_ROUNDED", self.draft("9000", "4.78"))
            self.finalize(self.draft("9000", "5"))
            self.assertEqual(self.store.ledger[0]["amount_delta"], D("-5.00"))
            self.assertEqual(D("9000.00") + abs(self.store.ledger[0]["amount_delta"]), D("9005.00"))

    def test_account_part_larger_than_the_rounding_allows_is_refused(self):
        with self.with_price("9142.3600"):
            self.assertRefused("ACCOUNT_AMOUNT_INVALID", self.draft("9000", "20"))


class AccountNeedsValidCustomerTests(InvoiceServiceTestCase):
    """A customer-account amount needs a real immutable customer id (the screen is never the only guard)."""

    def test_account_amount_without_a_customer_is_refused_by_the_service(self):
        self.assertRefused("CUSTOMER_NAME_REQUIRED", InvoiceDraft(
            5, None, (sale(),), (), (cash("40000"),), invoice_total="67010",
            account_amount="27010", account_direction="CUSTOMER_OWES"))
        self.assertRefused("CUSTOMER_NOT_FOUND", InvoiceDraft(
            5, "99999", (sale(),), (), (cash("40000"),), invoice_total="67010",
            account_amount="27010", account_direction="CUSTOMER_OWES"))
        self.assertEqual(self.store.ledger, [])


class MixedRoundingBoundaryTests(unittest.TestCase):
    def test_nearest_five_examples(self):
        for exact, final in (("9340.36", "9340.00"), ("9342.60", "9345.00"), ("9342.50", "9345.00"),
                             ("9337.49", "9335.00")):
            with self.subTest(exact):
                self.assertEqual(round_to_egp5(D(exact)), D(final))


class OldGoldOnlyCustomerOptionalTests(InvoiceServiceTestCase):
    """Old gold only: a customer is required only when an amount goes on the customer account. Fully settled with
    real money (account part = 0) it needs none — the EGP-5 rounding is an adjustment, never a customer balance."""

    def with_price(self):
        from unittest.mock import patch as patched
        from tests import test_invoice_service as fixture
        return patched.dict(fixture.PRICES, {1: (D("9187.5000"), D("9200.0000"))})   # 1 g − 1.5% = 9,049.69 exact

    @staticmethod
    def draft(payments, customer=None, account=None):
        extra = {"account_amount": account, "account_direction": "SHOP_OWES"} if account else {}
        return InvoiceDraft(5, customer, (), (old(1, "1", **disc()),), tuple(payments), **extra)

    def finalized_without_customer(self, payments):
        preview = self.service.preview(self.draft(payments))
        self.assertEqual(preview["blocking_problems"], [])
        self.assertEqual((preview["old_gold_total"], preview["final_settlement_amount"]), ("9049.69", "9050.00"))
        self.finalize(self.draft(payments))
        invoice = self.store.invoices[0]
        self.assertIsNone(invoice["customer_id"])
        self.assertEqual((self.store.ledger, self.store.snapshots, self.store.new_customers), ([], [], []))
        return sum((abs(m["amount_delta"]) for m in self.store.cash_movements + self.store.bank_movements), D("0"))

    def test_a_full_cash_payout_needs_no_customer(self):
        with self.with_price():
            self.assertEqual(self.finalized_without_customer([cash("9050")]), D("9050.00"))
            self.assertEqual(abs(self.store.adjustments[0]["amount_signed"]), D("0.31"))   # rounding audit only

    def test_a_full_wallet_payout_needs_no_customer(self):
        with self.with_price():
            self.assertEqual(self.finalized_without_customer(
                [PaymentInput("MOBILE_WALLET", "9050", bank_account_id="9")]), D("9050.00"))
            self.assertEqual([m["bank_account_id"] for m in self.store.bank_movements], [9])

    def test_split_real_payments_need_no_customer(self):
        with self.with_price():
            self.assertEqual(self.finalized_without_customer(
                [cash("4000"), PaymentInput("MOBILE_WALLET", "5050", bank_account_id="9")]), D("9050.00"))

    def test_an_account_part_needs_a_customer(self):
        with self.with_price():
            draft = self.draft([cash("9000")], account="50")
            self.assertEqual([p["code"] for p in self.service.preview(draft)["blocking_problems"]][:1],
                             ["CUSTOMER_NAME_REQUIRED"])
            self.assertRefused("CUSTOMER_NAME_REQUIRED", draft)
            self.assertEqual((self.store.invoices, self.store.ledger), ([], []))

    def test_the_same_invoice_with_a_valid_customer_posts_exactly_50(self):
        with self.with_price():
            self.service.preview(self.draft([cash("9000")], customer="12", account="50"))
            self.finalize(self.draft([cash("9000")], customer="12", account="50"))
            self.assertEqual([(m["type_code"], m["amount_delta"]) for m in self.store.ledger],
                             [("BUY_BALANCE_DUE", D("-50.00"))])               # never 49.69
            self.assertEqual(self.store.invoices[0]["customer_id"], "12")

    def test_the_fractional_49_69_never_reaches_the_account(self):
        with self.with_price():
            self.assertRefused("ACCOUNT_AMOUNT_NOT_ROUNDED",
                               self.draft([cash("9000")], customer="12", account="49.69"))
            self.assertEqual(self.store.ledger, [])

    def test_sale_and_mixed_still_need_a_customer(self):
        for draft in (InvoiceDraft(5, None, (sale(),), (), (cash("67010"),), invoice_total="67010"),
                      InvoiceDraft(5, None, (sale(),), (old(1, "5", **disc()),), (cash("36440"),),
                                   invoice_total="36440")):
            with self.subTest(kind=len(draft.old_gold_lines)):
                self.assertEqual(self.service.preview(draft)["blocking_problems"][0]["code"], "CUSTOMER_NAME_REQUIRED")
                self.assertRefused("CUSTOMER_NAME_REQUIRED", draft)


class InactivePaymentTests(InvoiceServiceTestCase):
    def test_inactive_method_or_account_is_refused_at_finalize(self):
        original = type(self.store).__dict__.get("methods")
        draft = InvoiceDraft(5, "12", (sale(),), (), (cash("40000"),
                                                      PaymentInput("INSTAPAY", "27010", bank_account_id="8")),
                             invoice_total="67010")
        self.assertRefused("BANK_ACCOUNT_INVALID", draft)                         # account 8 is closed
        from tests import test_invoice_service as fixture
        real = fixture.InvoiceRepo.get_payment_method

        def inactive(repo, code):
            row = real(repo, code)
            return None if row is None else dict(row, is_active=0 if code == "INSTAPAY" else 1)
        fixture.InvoiceRepo.get_payment_method = inactive
        try:
            self.assertRefused("PAYMENT_METHOD_NOT_ALLOWED", InvoiceDraft(
                5, "12", (sale(),), (), (cash("40000"), PaymentInput("INSTAPAY", "27010", bank_account_id="7")),
                invoice_total="67010"))
        finally:
            fixture.InvoiceRepo.get_payment_method = real


class PostInvoiceLifecycleTests(unittest.TestCase):
    """Invoice 26100203: 73,260 = cash 70,000 + 3,260 على العميل; later CS000001 1,000 and CS000002 2,260."""

    class Ledger:
        def __init__(self, balance, movements):
            self.balance, self.movements, self.after = balance, movements, None

        def customer_movements_after(self, customer_id, after):
            self.after = after
            return self.movements

        def customer_balance(self, customer_id):
            return self.balance

    MOVES = [{"transaction_id": "41", "occurred_at": "2026-10-02 10:00:00", "amount_delta": D("-1000.00"),
              "type_code": "CUSTOMER_ACCOUNT_SETTLEMENT", "type_name": "سداد حساب", "reference_code": "CS000001",
              "actor": "admin", "settlement_legs": "PHYSICAL_CASH:1000.00"},
             {"transaction_id": "42", "occurred_at": "2026-10-02 11:00:00", "amount_delta": D("-2260.00"),
              "type_code": "CUSTOMER_ACCOUNT_SETTLEMENT", "type_name": "سداد حساب", "reference_code": "CS000002",
              "actor": "admin", "settlement_legs": "PHYSICAL_CASH:2260.00"}]

    def invoice(self):
        return {"customer_id": "5", "account_entries": [{"transaction_id": "40", "amount_delta": D("3260.00")}]}

    def test_original_snapshot_kept_and_later_settlements_listed(self):
        from services.invoice_history_service import InvoiceHistoryService
        ledger = self.Ledger(D("0.00"), self.MOVES)
        post = InvoiceHistoryService._post_invoice(ledger, self.invoice())
        self.assertEqual(ledger.after, 40)                                  # only movements after this invoice
        self.assertEqual((post["original_account"], post["current_balance"]), (D("3260.00"), D("0.00")))
        self.assertNotIn("state", post)                                     # nothing is attributed to the invoice
        self.assertEqual([m["reference"] for m in post["movements"]], ["CS000001", "CS000002"])
        self.assertEqual(post["movements"][1]["legs"], [{"method_code": "PHYSICAL_CASH", "amount": D("2260.00")}])

    def test_no_movement_no_section(self):
        from services.invoice_history_service import InvoiceHistoryService
        self.assertIsNone(InvoiceHistoryService._post_invoice(self.Ledger(D("0"), []), {"customer_id": "5",
                                                                                      "account_entries": []}))

    def test_viewmodel_text(self):
        from PySide6.QtGui import QGuiApplication
        QGuiApplication.instance() or QGuiApplication([])
        from app.viewmodels.invoice_history_viewmodel import InvoiceHistoryViewModel
        from services.invoice_history_service import InvoiceHistoryService
        vm = InvoiceHistoryViewModel.__new__(InvoiceHistoryViewModel)
        vm._detail = {"post_invoice": InvoiceHistoryService._post_invoice(self.Ledger(D("0.00"), self.MOVES),
                                                                          self.invoice())}
        post = InvoiceHistoryViewModel.postInvoice.fget(vm)
        self.assertEqual(post["originalText"], "الحالة عند الإصدار: مسددة جزئيًا — 3,260.00 ج على العميل")
        self.assertEqual(post["currentText"], "رصيد العميل الحالي: الحساب متوازن")
        self.assertNotIn("سداد", post["currentText"])                     # never "this invoice was paid later"
        vm._detail["post_invoice"]["current_balance"] = D("2260.00")
        self.assertEqual(InvoiceHistoryViewModel.postInvoice.fget(vm)["currentText"],
                         "رصيد العميل الحالي: 2,260.00 ج على العميل")
        self.assertEqual([r["reference"] for r in post["rows"]], ["CS000001", "CS000002"])
        self.assertIn("نقدي", post["rows"][0]["methods"])


if __name__ == "__main__":
    unittest.main()
