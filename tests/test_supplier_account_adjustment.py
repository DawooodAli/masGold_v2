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


class AdjustmentReversalTests(AccountAdjustCase):
    """Owner 2026-11-08 (migration 102): an account adjustment is reversed by the exact opposite row that names it."""

    def reverse(self, adjustment, reason="تسوية بالغلط", key=None, service=None):
        self.k += 1
        return (service or self.owner).reverse_account_adjustment(adjustment, reason, key or base.key(self.k))

    def test_a_gold_adjustment_is_reversed_by_its_exact_opposite_and_the_original_stays(self):
        gold = self.gold()
        done = self.post("GOLD", "DEDUCT", "1.5")
        self.assertEqual(self.gold(), gold - D("1.500"))
        undone = self.reverse(done["id"])
        self.assertEqual((self.gold(), undone["delta"], undone["reverses"]), (gold, D("1.500"), str(done["id"])))
        self.assertEqual(len(self.db.account_adjustments), 2)
        original = next(a for a in self.db.account_adjustments if a["id"] == int(done["id"]))
        self.assertEqual((original["delta"], original.get("reverses_id")), (D("-1.500"), None))
        self.assertEqual(self.db.gold[-1]["source_id"], int(undone["id"]))

    def test_a_making_adjustment_is_reversed_too_and_nothing_else_moves(self):
        making, cash = self.making(), len(self.db.cash_moves)
        done = self.post("MAKING", "ADD", "250.50")
        self.reverse(done["id"])
        self.assertEqual((self.making(), len(self.db.cash_moves)), (making, cash))

    def test_it_cannot_be_reversed_twice_and_a_reversal_is_never_reversed(self):
        done = self.post("GOLD", "ADD", "2")
        undone = self.reverse(done["id"])
        with self.assertRaises(SupplierRuleError) as twice:
            self.reverse(done["id"])
        self.assertEqual(twice.exception.code, "ALREADY_REVERSED")
        with self.assertRaises(SupplierRuleError) as again:
            self.reverse(undone["id"])
        self.assertEqual(again.exception.code, "ADJUSTMENT_IS_REVERSAL")

    def test_a_reason_and_the_owner_right_are_required_and_a_retry_replays(self):
        done = self.post("GOLD", "ADD", "2")
        with self.assertRaises(SupplierRuleError):
            self.reverse(done["id"], reason="  ")
        manager = self.with_rights(list(OWNER_RIGHTS))
        with self.assertRaises(PermissionError):
            self.reverse(done["id"], service=manager)
        key = base.key(777)
        first = self.reverse(done["id"], key=key)
        again = self.reverse(done["id"], key=key)
        self.assertEqual((again["duplicate"], again["id"]), (True, first["id"]))
        self.assertEqual(len(self.db.account_adjustments), 2)

    def test_the_ledger_rows_know_their_state(self):
        done = self.post("GOLD", "ADD", "2")
        self.reverse(done["id"])
        detail = self.suppliers.supplier_detail(self.supplier)
        rows = {r["source_id"]: r for r in detail["gold_ledger"] if r["source_type"] == "SUPPLIER_ACCOUNT_ADJUSTMENT"}
        original, undo = rows[str(done["id"])], [r for k, r in rows.items() if k != str(done["id"])][0]
        self.assertEqual((original["adjustment_reversed"], original["adjustment_reverses"]), (True, False))
        self.assertEqual((undo["adjustment_reversed"], undo["adjustment_reverses"]), (False, True))
