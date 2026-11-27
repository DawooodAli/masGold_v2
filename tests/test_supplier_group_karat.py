"""Migration 112 (owner 2026-11-27): a supplier invoice group may carry its OWN karat (e.g. 24K for a supplier whose account karat is 21).
The group's weight stays the ACTUAL grams (making and intake use it); its grams converted into the account karat are what the shop owes. Real services over in-memory doubles."""
from decimal import Decimal as D
import unittest

from services import supplier_rules as rules
from services.supplier_rules import SupplierRuleError
from tests import test_supplier_payments as base
from tests.test_supplier_group_adjustment import AdjustCase
from tests.test_suppliers import SupplierCase

K21, K18, K24 = 1, 2, 3                 # ids in the fake database; the supplier of SupplierCase has the account karat 21


def row(weight, rate, karat_id=None, description=""):
    return {"weight": weight, "making_per_gram": rate, "description": description, **({} if karat_id is None else {"karat_id": karat_id})}


class ConversionRuleTests(unittest.TestCase):
    def test_a_24k_group_is_converted_into_the_21k_account(self):
        self.assertEqual(rules.credited_weight("100.000", 24, 21), D("114.286"))                # 100 x 24 / 21 = 114.2857...
        self.assertEqual(rules.credited_weight("100.000", 18, 21), D("85.714"))
        self.assertEqual(rules.credited_weight("100.000", 21, 21), D("100.000"))               # the account karat: the weight itself
        self.assertIsInstance(rules.credited_weight("1.5", 24, 21), D)

    def test_it_matches_the_settlement_credit_formula(self):
        for weight in ("0.001", "3.333", "12.345", "100.000"):
            self.assertEqual(rules.credited_weight(weight, 24, 21), rules.settlement_credit(D(weight), 24, 21)[0])

    def test_rounding_is_half_up_and_symmetric_for_adjustments(self):
        self.assertEqual(rules.credited_weight("0.500", 24, 22), D("0.545"))                    # 0.54545... -> 0.545
        add, deduct = rules.credited_weight("7.777", 24, 21), rules.credited_weight("-7.777", 24, 21)
        self.assertEqual(add, -deduct)                                                          # ADD x then DEDUCT x leaves exactly zero

    def test_the_smallest_weight_never_converts_to_zero(self):
        for karat in (18, 21, 22, 24):
            self.assertGreaterEqual(rules.credited_weight("0.001", karat, 24), D("0.001"))      # 0.001 x 18 / 24 = 0.00075 rounds half up to 0.001
            self.assertGreaterEqual(rules.credited_weight("0.001", 18, karat), D("0.001"))

    def test_groups_without_a_karat_credit_their_own_grams(self):
        result = rules.normalize_groups([{"weight": "10", "making_per_gram": "1"}])
        self.assertEqual((result["total_weight"], result["credited_total"], result["groups"][0]["credited_weight_grams"]), (D("10.000"), D("10.000"), D("10.000")))

    def test_mixed_groups_sum_each_groups_own_rounded_credit(self):
        result = rules.normalize_groups([{"weight": "100", "making_per_gram": "10", "karat_id": K24, "karat_value": 24},
                                         {"weight": "50", "making_per_gram": "20", "karat_id": K21, "karat_value": 21}], 21)
        self.assertEqual((result["total_weight"], result["credited_total"]), (D("150.000"), D("164.286")))   # 114.286 + 50.000
        self.assertEqual(result["gross_making"], D("2000.00"))                                  # making on the ACTUAL grams: 100 x 10 + 50 x 20


class GroupKaratTests(SupplierCase):
    def test_a_24k_group_posts_the_converted_grams_in_the_account_karat_and_the_making_on_actual_grams(self):
        posted, draft = self.posted(rows=[row("100", "50", K24), row("40", "120")])
        self.assertEqual((posted["supplier_weight"], posted["gross_making"]), (D("140.000"), D("9800.00")))   # actual grams, making 100 x 50 + 40 x 120
        (gold,) = self.db.gold
        self.assertEqual((gold["karat_id"], gold["weight_delta_grams"]), (K21, D("154.286")))                  # 114.286 (24K group) + 40.000
        self.assertEqual(gold["equivalent_21_delta_grams"], D("154.286000"))
        (making,) = self.db.making
        self.assertEqual(making["amount_delta"], D("9800.00"))
        balances = self.suppliers.supplier_detail(self.supplier_id)["balances"]
        self.assertEqual((balances["gold_karat_value"], balances["gold_balance"], balances["gold_direction"]), (21, D("154.286"), rules.SHOP_OWES))
        self.assertEqual(self.db.audit_rows[-1]["metadata"]["actual_weight"], "140.000")

    def test_the_groups_store_their_karat_and_the_credited_grams(self):
        draft = self.draft(rows=[row("100", "50", K24), row("40", "120"), row("10", "5", K18)])
        stored = self.purchases._repository(self.db).groups(draft["id"])
        self.assertEqual([(g["karat_id"], g["supplier_weight_grams"], g["credited_weight_grams"]) for g in stored],
                         [(K24, D("100.000"), D("114.286")), (K21, D("40.000"), D("40.000")), (K18, D("10.000"), D("8.571"))])

    def test_a_group_without_a_karat_defaults_to_the_account_karat_and_nothing_changes_for_old_invoices(self):
        posted, _ = self.posted()                                                              # the three plain groups of SupplierCase
        (gold,) = self.db.gold
        self.assertEqual((gold["karat_id"], gold["weight_delta_grams"]), (K21, D("100.000")))

    def test_an_unknown_or_inactive_group_karat_is_refused_and_nothing_is_saved(self):
        self.db.karats[K18]["is_active"] = 0
        for bad in (K18, 99, "abc"):
            with self.subTest(bad=bad), self.assertRaises(SupplierRuleError) as refused:
                self.draft(rows=[row("10", "1", bad)])
            self.assertEqual(refused.exception.code, "KARAT_NOT_FOUND")
        self.assertEqual(self.db.invoices, {})

    def test_the_invoice_karat_must_still_be_the_suppliers(self):
        with self.assertRaises(SupplierRuleError) as refused:
            self.draft(rows=[row("10", "1", K24)], karat_id=K24)                                # the account karat cannot be changed through a group
        self.assertEqual(refused.exception.code, "KARAT_MISMATCH")

    def test_posting_refuses_a_group_whose_stored_credit_no_longer_matches(self):
        draft = self.draft(rows=[row("100", "50", K24)])
        next(iter(self.db.groups.values()))["credited_weight_grams"] = D("100.000")           # a tampered / stale credit
        with self.assertRaises(SupplierRuleError) as refused:
            self.purchases.post_invoice(draft["id"], self.key())
        self.assertEqual(refused.exception.code, "INVOICE_INCONSISTENT")
        self.assertEqual(self.db.gold, [])

    def test_a_piece_of_a_24k_group_is_entered_as_24k_stock(self):
        posted, _ = self.posted(rows=[row("100", "50", K24), row("40", "120")])
        g24, g21 = self.group_ids(posted["id"])
        piece24 = self.piece(posted["id"], g24, "10.000")
        piece21 = self.piece(posted["id"], g21, "5.000")
        self.assertEqual(self.db.items[int(piece24["inventory_item_id"])]["karat_id"], K24)
        self.assertEqual(self.db.items[int(piece21["inventory_item_id"])]["karat_id"], K21)

    def test_the_intake_limit_compares_actual_grams_with_the_actual_group_weight(self):
        posted, _ = self.posted(rows=[row("100", "50", K24)])
        (g24,) = self.group_ids(posted["id"])
        self.piece(posted["id"], g24, "100.000")                                               # exactly the group's actual grams: accepted
        with self.assertRaises(SupplierRuleError):
            self.piece(posted["id"], g24, "0.500")                                             # beyond actual + 0.333 g tolerance

    def test_the_open_group_list_shows_the_groups_own_karat(self):
        self.posted(rows=[row("100", "50", K24), row("40", "120")])
        karats = sorted(g["karat_value"] for g in self.purchases.open_groups())
        self.assertEqual(karats, [21, 24])

    def test_the_detail_shows_each_groups_karat_and_credited_grams(self):
        posted, _ = self.posted(rows=[row("100", "50", K24), row("40", "120")])
        detail = self.purchases.invoice_detail(posted["id"])
        self.assertEqual([(g["karat_value"], g["supplier_weight"], g["credited_weight"]) for g in detail["groups"]],
                         [(24, D("100.000"), D("114.286")), (21, D("40.000"), D("40.000"))])
        self.assertEqual(detail["karat_value"], 21)                                            # the invoice stays in the account karat


class GroupKaratAdjustmentAndReversalTests(AdjustCase):
    """The 21K supplier of the payment case receives a second invoice with a 24K group (100 g, 50 EGP/g)."""

    def setUp(self):
        super().setUp()
        draft = self.purchases.create_draft(self.supplier, 1, "INV-24", base.TODAY, [{"weight": "100", "making_per_gram": "50", "karat_id": K24}], "", base.key(8800))
        self.purchases.post_invoice(draft["id"], base.key(8801))
        self.invoice = draft["id"]
        self.group = [g["id"] for g in self.db.groups.values() if int(g["invoice_id"]) == int(draft["id"])][0]
        self.before = self.gold()

    def test_adding_or_deducting_gold_on_a_24k_group_posts_the_converted_grams(self):
        self.adjust("ADD", "10")
        self.adjust("DEDUCT", "10")
        weights = [m["weight_delta_grams"] for m in self.db.gold][-3:]
        self.assertEqual(weights, [D("114.286"), D("11.429"), D("-11.429")])                   # 10 x 24 / 21 = 11.4286: add and deduct are exact opposites
        self.assertEqual(self.gold(), self.before)
        self.assertTrue(all(m["karat_id"] == K21 for m in self.db.gold))

    def test_a_full_reversal_negates_the_stored_converted_movement_not_the_actual_grams(self):
        self.assertEqual(self.before, D("50.000") + D("114.286"))
        self.reverse()
        self.assertEqual(self.gold(), D("50.000"))                                             # exactly the 114.286 credited grams are taken back


if __name__ == "__main__":
    unittest.main()
