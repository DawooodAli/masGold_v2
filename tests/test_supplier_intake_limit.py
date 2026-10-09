"""Making-group intake limit: the cumulative ACTUAL weight of a group's pieces may not exceed its supplier weight
+ exactly 0.333 g (OWNER rule; fixed, not a percentage, not per piece, not a setting). Service, rules and view-model
behaviour over the in-memory fakes. Concurrency is NOT claimed here: it is proven on real MySQL in
tests/test_supplier_intake_concurrency_mysql.py (opt-in, disposable rehearsal database)."""
from decimal import Decimal as D
import unittest

from app.viewmodels.supplier_purchase_viewmodel import SupplierPurchaseViewModel, allowance_fields
from services import supplier_rules as rules
from services.supplier_rules import SupplierRuleError
from tests import test_suppliers as base

TOLERANCE_TEXT = "والسماحية المسموحة: 0.333 جرام."


class RulesTests(unittest.TestCase):
    def test_the_tolerance_is_fixed_and_the_limit_is_weight_plus_it(self):
        self.assertEqual(rules.INTAKE_TOLERANCE, D("0.333"))
        self.assertEqual(rules.intake_limit("10.000"), D("10.333"))
        self.assertEqual(rules.intake_limit(D("40")), D("40.333"))

    def test_the_boundary_is_inclusive_and_one_unit_more_is_refused(self):
        rules.check_intake("10.000", "0", "10.333")                                  # equality: allowed
        rules.check_intake("10.000", "10.000", "0.333")                              # cumulative equality: allowed
        with self.assertRaises(SupplierRuleError) as refused:
            rules.check_intake("10.000", "0", "10.334")                              # the smallest unit more
        self.assertEqual(refused.exception.code, "GROUP_WEIGHT_LIMIT")
        self.assertEqual(str(refused.exception), "وزن القطعة يتجاوز المتاح للمجموعة. المتاح: 10.333 جرام، " + TOLERANCE_TEXT)

    def test_it_is_decimal_arithmetic_not_float(self):
        rules.check_intake("0.1", "0.2", "0.233")            # 0.1 + 0.333 = 0.433 = 0.2 + 0.233 exactly (floats would drift)
        with self.assertRaises(SupplierRuleError):
            rules.check_intake("0.1", "0.2", "0.234")

    def test_allowance_keeps_a_negative_value_and_an_over_limit_group_gets_its_own_explanation(self):
        self.assertEqual(rules.intake_allowance("10.000", "10.100"), D("0.233"))
        self.assertEqual(rules.intake_allowance("10.000", "12.000"), D("-1.667"))     # never floored by the rule
        with self.assertRaises(SupplierRuleError) as refused:
            rules.check_intake("10.000", "12.000", "0.001")
        self.assertEqual(refused.exception.code, "GROUP_OVER_LIMIT")


class IntakeLimitTests(base.SupplierCase):
    """Groups of the posted invoice: 40.000 / 35.000 / 25.000 (limits 40.333 / 35.333 / 25.333)."""

    def setUp(self):
        super().setUp()
        self.invoice, _ = self.posted()
        self.invoice_id = self.invoice["id"]
        self.g1, self.g2, self.g3 = self.group_ids(self.invoice_id)

    def refused(self, group, weight, code="GROUP_WEIGHT_LIMIT"):
        with self.assertRaises(SupplierRuleError) as refusal:
            self.piece(self.invoice_id, group, weight)
        self.assertEqual(refusal.exception.code, code)
        return str(refusal.exception)

    def footprint(self):
        db = self.db
        return (len(db.items), len(db.links), len(db.barcodes), len(db.movements), len(db.audit_rows),
                db.sequences["barcode"], len(db.gold), len(db.making))

    # ---- allowed / refused
    def test_intake_below_the_supplier_weight_is_allowed(self):
        self.piece(self.invoice_id, self.g1, "39.999")
        self.assertEqual(len(self.db.links), 1)

    def test_intake_above_the_supplier_weight_but_within_the_tolerance_is_allowed(self):
        self.piece(self.invoice_id, self.g1, "40.200")
        group = self.purchases.invoice_detail(self.invoice_id)["groups"][0]
        self.assertEqual((group["actual_weight"], group["variance"], group["over_supplier_weight"]),
                         (D("40.200"), D("0.200"), True))

    def test_exactly_the_supplier_weight_plus_the_tolerance_is_allowed(self):
        self.piece(self.invoice_id, self.g1, "40.333")
        self.assertEqual(self.purchases.invoice_detail(self.invoice_id)["groups"][0]["intake_allowance"], D("0.000"))

    def test_one_smallest_unit_above_the_maximum_is_refused_with_the_arabic_message(self):
        message = self.refused(self.g1, "40.334")
        self.assertEqual(message, "وزن القطعة يتجاوز المتاح للمجموعة. المتاح: 40.333 جرام، " + TOLERANCE_TEXT)

    def test_pieces_consume_one_cumulative_allowance(self):
        self.piece(self.invoice_id, self.g1, "25.000")
        self.piece(self.invoice_id, self.g1, "15.200")                           # 40.200
        self.piece(self.invoice_id, self.g1, "0.133")                            # 40.333: exactly the maximum
        message = self.refused(self.g1, "0.001")
        self.assertIn("المتاح: 0.000 جرام", message)
        self.assertEqual(len(self.db.links), 3)

    def test_the_remaining_allowance_is_what_the_message_reports(self):
        self.piece(self.invoice_id, self.g1, "30.000")
        self.assertIn("المتاح: 10.333 جرام", self.refused(self.g1, "10.334"))

    def test_another_groups_unused_weight_never_increases_this_groups_allowance(self):
        self.piece(self.invoice_id, self.g1, "40.333")                           # group 1 full; groups 2 and 3 untouched
        self.refused(self.g1, "0.001")
        self.piece(self.invoice_id, self.g2, "35.333")                           # group 2 has its OWN allowance
        self.refused(self.g2, "0.001")
        self.refused(self.g3, "25.334")                                          # and group 3 its own, unaffected by 1 / 2

    def test_the_input_precision_convention_is_unchanged(self):
        self.piece(self.invoice_id, self.g1, "40.3334")                          # rounds half-up to 40.333: allowed
        self.refused(self.g2, "35.3335")                                         # rounds to 35.334: refused

    def test_the_whole_invoice_weight_guard_and_group_ownership_still_apply(self):
        with self.assertRaises(SupplierRuleError) as heavy:
            self.piece(self.invoice_id, self.g1, "100.001")
        self.assertEqual(heavy.exception.code, "PIECE_TOO_HEAVY")
        other, _ = self.posted(reference="OTHER")
        foreign_group = self.group_ids(other["id"])[0]
        with self.assertRaises(SupplierRuleError) as foreign:
            self.piece(self.invoice_id, foreign_group, "1.000")                  # a group of ANOTHER invoice
        self.assertEqual(foreign.exception.code, "GROUP_NOT_FOUND")

    # ---- a refusal persists nothing
    def test_a_refusal_creates_no_item_link_barcode_movement_audit_or_balance_change(self):
        self.piece(self.invoice_id, self.g1, "40.000")
        before, balances = self.footprint(), self.balances()
        self.refused(self.g1, "0.334")
        self.assertEqual(self.footprint(), before)                               # the barcode sequence is untouched too
        self.assertEqual(self.balances(), balances)

    # ---- the authoritative source: every linked piece counts, whatever happens to it later
    def test_entered_pieces_keep_consuming_the_allowance_after_a_sale_or_a_status_change(self):
        first = self.piece(self.invoice_id, self.g1, "40.000")
        for status in ("SOLD", "RETURNED_PENDING", "SCRAPPED", "REMOVED"):
            self.db.items[int(first["inventory_item_id"])]["inventory_status"] = status
            self.assertIn("المتاح: 0.333 جرام", self.refused(self.g1, "0.334"))
        self.piece(self.invoice_id, self.g1, "0.333")                            # exactly the remaining allowance
        self.refused(self.g1, "0.001")

    # ---- legacy over-limit data
    def test_an_existing_over_limit_group_keeps_its_data_and_refuses_further_intake_with_an_explanation(self):
        first = self.piece(self.invoice_id, self.g1, "40.000")
        self.db.items[int(first["inventory_item_id"])]["weight_grams"] = D("45.000")      # entered before the rule existed
        before = self.footprint()
        message = self.refused(self.g1, "0.001", code="GROUP_OVER_LIMIT")
        self.assertIn("تجاوزت هذه المجموعة الحد الأقصى", message)
        self.assertEqual(self.footprint(), before)
        self.assertEqual(self.db.items[int(first["inventory_item_id"])]["weight_grams"], D("45.000"))   # untouched
        group = self.purchases.invoice_detail(self.invoice_id)["groups"][0]
        self.assertEqual((group["actual_weight"], group["variance"], group["supplier_weight"]),
                         (D("45.000"), D("5.000"), D("40.000")))                  # nothing floored or rewritten
        self.assertEqual(group["intake_allowance"], D("-4.667"))
        self.piece(self.invoice_id, self.g2, "10.000")                            # other groups are unaffected

    # ---- idempotency and accounting
    def test_a_retry_of_an_accepted_piece_is_idempotent_even_when_the_group_is_now_full(self):
        key = self.key()
        first = self.purchases.add_piece(self.invoice_id, self.g1, 1, "40.333", key)
        again = self.purchases.add_piece(self.invoice_id, self.g1, 1, "40.333", key)
        self.assertEqual((first["duplicate"], again["duplicate"], again["inventory_item_id"]),
                         (False, True, first["inventory_item_id"]))
        self.assertEqual(len(self.db.links), 1)

    def test_intake_variance_inside_the_tolerance_never_touches_supplier_gold_or_making(self):
        before = (self.balances(), list(self.db.gold), list(self.db.making))
        self.piece(self.invoice_id, self.g1, "40.333")
        self.piece(self.invoice_id, self.g2, "35.250")
        after = (self.balances(), list(self.db.gold), list(self.db.making))
        self.assertEqual(before, after)                                          # no ledger adjustment, no making change
        detail = self.purchases.invoice_detail(self.invoice_id)
        self.assertEqual(detail["supplier_weight"], D("100.000"))               # the supplier weight stays authoritative
        self.assertEqual(detail["groups"][0]["making_total"], D("4800.00"))     # the group's making liability is unchanged


class ViewModelTests(base.SupplierCase):
    def setUp(self):
        super().setUp()
        self.invoice, _ = self.posted()
        self.invoice_id = self.invoice["id"]
        self.g1, self.g2, self.g3 = self.group_ids(self.invoice_id)
        self.vm = SupplierPurchaseViewModel(self.session, self.purchases)

    def load(self):
        self.vm._detail = self.purchases.invoice_detail(self.invoice_id)
        return self.vm.detail["groups"]

    def test_each_group_shows_its_tolerance_inclusive_allowance_separately_from_the_reference_remaining(self):
        self.piece(self.invoice_id, self.g1, "39.900")
        first = self.load()[0]
        self.assertEqual(first["allowanceLine"], "المتاح للإدخال شامل السماحية: 0.433 جرام")
        self.assertEqual(first["remainingText"], "المتبقي للإدخال (مرجعي): 0.100")      # the reference value keeps its meaning
        self.assertEqual((first["overLimit"], first["overLimitText"]), (False, ""))

    def test_a_negative_allowance_is_shown_as_zero_only_for_display(self):
        item = self.piece(self.invoice_id, self.g1, "40.000")
        self.db.items[int(item["inventory_item_id"])]["weight_grams"] = D("45.000")
        group = self.load()[0]
        self.assertEqual(group["allowanceLine"], "المتاح للإدخال شامل السماحية: 0.000 جرام")
        self.assertTrue(group["overLimit"])
        self.assertIn("لا يمكن إدخال قطع إضافية", group["overLimitText"])
        raw = self.vm._detail["groups"][0]
        self.assertEqual((raw["intake_allowance"], raw["variance"]), (D("-4.667"), D("5.000")))   # underlying values intact

    def test_the_ui_precheck_uses_the_same_rule_and_stays_silent_when_nothing_is_known(self):
        self.piece(self.invoice_id, self.g1, "40.000")
        self.load()
        refusal = self.vm.pieceWeightProblem(self.g1, "0.334")
        self.assertEqual(refusal, "وزن القطعة يتجاوز المتاح للمجموعة. المتاح: 0.333 جرام، " + TOLERANCE_TEXT)
        self.assertEqual(self.vm.pieceWeightProblem(self.g1, "0.333"), "")             # equality is allowed
        self.assertEqual(self.vm.pieceWeightProblem(self.g2, "35.333"), "")             # another group's own allowance
        for unknown in ("", "abc", "0", "-1"):
            self.assertEqual(self.vm.pieceWeightProblem(self.g1, unknown), "", unknown)  # the service reports those
        self.assertEqual(self.vm.pieceWeightProblem("999", "1"), "")

    def test_allowance_fields_are_pure_presentation(self):
        row = {"intake_allowance": D("0.333")}
        self.assertEqual(allowance_fields(row)["allowanceText"], "0.333")


if __name__ == "__main__":
    unittest.main()
