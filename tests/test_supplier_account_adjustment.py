"""Supplier ACCOUNT differences (096, «تسوية فروق الذهب / المصنعيات»): real SupplierPurchaseService over the in-memory database of tests/supplier_fakes.py.
The SQL (table, CHECKs) is covered by the static schema test; nothing here touches Scrap, cash, bank or stock."""
from decimal import Decimal as D
import unittest

from services.supplier_rules import SupplierRuleError
from tests import test_supplier_payments as base
from tests.supplier_fakes import OWNER_RIGHTS
from tests.test_supplier_group_adjustment import AdjustCase

RIGHT = "SUPPLIER_ACCOUNT_ADJUST"


class AccountAdjustCase(AdjustCase):
    def setUp(self):
        super().setUp()
        self.owner = self.with_rights(list(OWNER_RIGHTS) + [RIGHT])
        self.k = 900

    def post(self, kind, direction, amount, reason="فرق جرد", key=None, service=None):
        self.k += 1
        return (service or self.owner).adjust_account(self.supplier, kind, direction, amount, reason, key or base.key(self.k))


class AccountAdjustTests(AccountAdjustCase):
    def test_gold_difference_moves_only_the_supplier_gold_account(self):
        gold, making = self.gold(), self.making()
        cash, banks, audits = len(self.db.cash_moves), len(self.db.bank_moves), len(self.db.audit_rows)
        self.post("GOLD", "ADD", "1.25")
        self.assertEqual(self.gold(), gold + D("1.250"))
        self.post("GOLD", "DEDUCT", "0.5")
        self.assertEqual(self.gold(), gold + D("0.750"))
        self.assertEqual(self.making(), making)
        self.assertEqual((len(self.db.cash_moves), len(self.db.bank_moves)), (cash, banks))
        self.assertEqual(len(self.db.audit_rows), audits + 2)
        row = self.db.gold[-1]
        self.assertEqual((row["source_type"], row["karat_id"]), ("SUPPLIER_ACCOUNT_ADJUSTMENT", 1))           # the supplier's own karat

    def test_making_difference_moves_only_the_making_account(self):
        gold, making = self.gold(), self.making()
        self.post("MAKING", "ADD", "250.50")
        self.post("MAKING", "DEDUCT", "50")
        self.assertEqual(self.making(), making + D("200.50"))
        self.assertEqual(self.gold(), gold)

    def test_the_balance_may_cross_zero(self):
        self.post("MAKING", "DEDUCT", "999999")
        self.assertLess(self.making(), 0)

    def test_reason_amount_kind_and_direction_are_required(self):
        for args in (("GOLD", "ADD", "1", "  "), ("GOLD", "ADD", "0", "x"), ("GOLD", "ADD", "", "x"),
                     ("OTHER", "ADD", "1", "x"), ("GOLD", "SIDEWAYS", "1", "x"), ("MAKING", "ADD", "1.234", "x")):
            with self.assertRaises(SupplierRuleError, msg=str(args)):
                self.post(*args)
        self.assertEqual(self.db.account_adjustments, [])

    def test_only_the_owner_right_may_post(self):
        for rights in (list(OWNER_RIGHTS), list(OWNER_RIGHTS) + ["SUPPLIER_INVOICE_ADJUST"]):
            with self.assertRaises(Exception):
                self.post("GOLD", "ADD", "1", service=self.with_rights(rights))
        self.assertEqual(self.db.account_adjustments, [])

    def test_an_exact_replay_posts_once_and_a_different_payload_conflicts(self):
        key = base.key(999)
        first = self.post("GOLD", "ADD", "1", key=key)
        again = self.post("GOLD", "ADD", "1", key=key)
        self.assertTrue(again["duplicate"])
        self.assertEqual(first["id"], again["id"])
        self.assertEqual(len(self.db.account_adjustments), 1)
        with self.assertRaises(SupplierRuleError) as caught:
            self.post("GOLD", "ADD", "2", key=key)
        self.assertEqual(caught.exception.code, "REQUEST_CONFLICT")

    def test_gold_needs_a_supplier_with_a_karat(self):
        self.db.suppliers[int(self.supplier)]["karat_id"] = None
        with self.assertRaises(SupplierRuleError) as caught:
            self.post("GOLD", "ADD", "1")
        self.assertEqual(caught.exception.code, "SUPPLIER_KARAT_MISSING")
        self.post("MAKING", "ADD", "10")                      # making never needs a karat

    def test_the_ledger_rows_carry_arabic_labels(self):
        self.post("GOLD", "ADD", "1")
        self.post("MAKING", "ADD", "10")
        from services import supplier_rules as rules
        self.assertEqual(rules.gold_movement_text("ACCOUNT_ADJUSTMENT", "x"), "تسوية فروق ذهب")


if __name__ == "__main__":
    unittest.main()
