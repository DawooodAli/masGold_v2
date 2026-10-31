"""Supplier MAKING receipt (migration 093): money RECEIVED from a supplier against making, with its canonical reversal.

Same SMxxxxxx document as a payment with the opposite direction: one money IN movement (cash into a cash account, or a bank / wallet account)
and one POSITIVE supplier-making movement (type RECEIPT). The supplier GOLD account never moves. Real services over the in-memory database of
tests/supplier_fakes.py; the SQL itself is proven by the schema test and (opt-in) by a rehearsal run of migration 093."""
import unittest
from decimal import Decimal as D

from repositories.supplier_payment_repository import SOURCE
from services import supplier_rules as rules
from tests.test_supplier_payments import PaymentCase, TODAY, key


class ReceiptCase(PaymentCase):
    def receive(self, amount="1000", method="BANK", account="5", request=None, when=TODAY, reference="", notes="", service=None):
        self.n += 1
        return (service or self.payments).receive(self.supplier, method, account, amount, when, reference, notes, request or key(self.n))


class ReceiptPostingTests(ReceiptCase):
    def test_a_bank_receipt_posts_one_money_in_and_one_positive_making_movement(self):
        gold_before = self.gold()
        result = self.receive("1000.00", reference="تحويل 5", notes="رد جزء")
        self.assertEqual((result["code"], result["direction"], result["amount"], result["balance_before"], result["balance_after"],
                          result["duplicate"]), ("SM000001", "RECEIVE", "1000.00", "5000.00", "6000.00", False))
        (money,) = self.db.bank_moves
        self.assertEqual((money["account_id"], money["amount_delta"], money["reference_type"], money["reference_code"], money["type_code"]),
                         (5, D("1000.00"), SOURCE, "SM000001", "SUPPLIER_MAKING_RECEIPT"))
        made = [m for m in self.db.making if m["source_type"] == SOURCE]
        self.assertEqual([(m["amount_delta"], m["movement_type_id"]) for m in made], [(D("1000.00"), 5)])
        self.assertEqual(self.making(), D("6000.00"))
        self.assertEqual(self.gold(), gold_before)                                      # the supplier GOLD account never moves
        (payment,) = self.db.payments.values()
        self.assertEqual((payment["direction"], payment["payment_method"], payment["bank_account_id"]), ("RECEIVE", "BANK", 5))
        self.assertEqual(self.db.audit_rows[-1]["event_type"], "SUPPLIER_MAKING_RECEIPT")

    def test_a_cash_receipt_goes_into_the_selected_cash_account(self):
        self.receive("750", method="CASH", account="1")
        self.assertEqual((self.cash(1), self.bank(5), self.making()), (D("750.00"), D("0.00"), D("5750.00")))
        (money,) = self.db.cash_moves
        self.assertEqual(money["type_code"], "SUPPLIER_MAKING_RECEIPT")

    def test_a_receipt_may_cross_zero_into_the_shop_owing_the_supplier(self):
        self.pay("5000")                                                                 # the position is now 0
        self.receive("300")
        self.assertEqual(self.making(), D("300.00"))

    def test_exact_replay_returns_the_original_and_posts_nothing(self):
        first = self.receive("400", request=key(500))
        before = self.state()
        again = self.receive("400", request=key(500))
        self.assertEqual((again["id"], again["duplicate"]), (first["id"], True))
        self.assertEqual(self.state(), before)

    def test_the_same_key_with_another_payload_is_a_conflict(self):
        self.receive("400", request=key(500))
        self.refused("REQUEST_CONFLICT", lambda: self.receive("401", request=key(500)))

    def test_bad_input_is_refused_without_a_trace(self):
        self.refused("METHOD_INVALID", lambda: self.receive(method="CUSTODY"))
        self.refused("ACCOUNT_REQUIRED", lambda: self.receive(method="BANK", account=""))
        self.refused("ACCOUNT_NOT_FOUND", lambda: self.receive(method="BANK", account="999"))

    def test_the_payment_right_is_required(self):
        from tests.supplier_fakes import CASHIER_RIGHTS
        service, _, _ = self.service_for(CASHIER_RIGHTS)
        with self.assertRaises(Exception):
            self.receive(service=service)
        self.assertEqual(self.db.payments, {})

    def test_a_failed_posting_is_atomic(self):
        before = self.state()
        self.db.fail_at = "making"
        with self.assertRaises(RuntimeError):
            self.receive("100")
        self.db.fail_at = None
        self.assertEqual(self.state(), before)


class ReceiptReversalTests(ReceiptCase):
    REASON = "استلام مسجل بالخطأ"

    def reverse_receipt(self, payment_id="1"):
        return self.payments.reverse(payment_id, self.REASON)

    def test_owner_reverses_a_receipt_and_the_position_returns(self):
        self.receive("1000")
        gold = self.gold()
        result = self.reverse_receipt()
        self.assertEqual((result["code"], result["source_reference"]), ("RV000001", "SM000001"))
        self.assertEqual((self.bank(5), self.making(), self.gold()), (D("0.00"), D("5000.00"), gold))
        reversal = [m for m in self.db.making if m["source_type"] == "TRANSACTION_CORRECTION"]
        self.assertEqual([(m["amount_delta"], m["movement_type_id"]) for m in reversal], [(D("-1000.00"), 6)])   # RECEIPT_REVERSAL
        self.assertEqual(self.db.bank_moves[1]["amount_delta"], D("-1000.00"))
        self.assertEqual(self.db.bank_moves[0]["amount_delta"], D("1000.00"))                                  # the original stays
        self.refused("ALREADY_REVERSED", lambda: self.reverse_receipt(), error=Exception)


class ReceiptWiringTests(ReceiptCase):
    def test_the_rule_adds_and_the_context_offers_the_main_safe(self):
        self.assertEqual(rules.making_after_receipt("5000.00", "250.50"), D("5250.50"))
        context = self.payments.payment_context(self.supplier)
        self.assertEqual(context["main_safe"]["name"] is not None, True)


if __name__ == "__main__":
    unittest.main()
