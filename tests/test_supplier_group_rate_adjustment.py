"""Making-rate adjustment of a group of a POSTED supplier invoice (migration 106): the real SupplierPurchaseService and CorrectionService over the in-memory database of
tests/supplier_fakes.py. The SQL (table, CHECKs, locks) is covered by the static schema contract at the bottom, not here."""
from decimal import Decimal as D
import hashlib
from pathlib import Path
import unittest

from services import supplier_rules as rules
from services.supplier_rules import SupplierRuleError
from tests import test_supplier_payments as base
from tests.supplier_fakes import OWNER_RIGHTS
from tests.test_supplier_group_adjustment import ADJUST, AdjustCase


class RateAdjustCase(AdjustCase):
    """One 21K supplier with ONE posted invoice: a single group of 50 g at 100 EGP/g, making owed 5,000.00."""

    def rate(self, new_rate, reason="مصنعية المورد غلط", key=None, service=None):
        self.n += 1
        return (service or self.service).adjust_group_rate(self.invoice, self.group, new_rate, reason, key or base.key(self.n))


class RateAdjustmentTests(RateAdjustCase):
    def test_a_higher_rate_increases_what_we_owe_by_the_difference_times_the_group_weight(self):
        before = self.making()
        done = self.rate("110")
        self.assertEqual((done["delta"], done["old_rate"], done["new_rate"]), (D("500.00"), D("100.00"), D("110.00")))
        self.assertEqual(self.making(), before + D("500.00"))
        movement = self.db.making[-1]
        self.assertEqual((movement["amount_delta"], movement["source_type"], movement["movement_type_id"]), (D("500.00"), "SUPPLIER_INVOICE_RATE_ADJUSTMENT", 9))

    def test_a_lower_rate_decreases_it_and_gold_is_untouched(self):
        gold = self.gold()
        done = self.rate("90.50")
        self.assertEqual(done["delta"], D("-475.00"))
        self.assertEqual((self.making(), self.gold()), (D("4525.00"), gold))

    def test_the_detail_shows_the_current_rate_and_total_and_keeps_the_original(self):
        self.rate("110")
        self.rate("105")                                                # a second adjustment is relative to the CURRENT rate
        group = self.purchases.invoice_detail(self.invoice)["groups"][0]
        self.assertEqual((group["making_per_gram"], group["original_making_per_gram"]), (D("105.00"), D("100.00")))
        self.assertEqual((group["making_total"], group["original_making_total"], group["rate_adjustment"], group["rate_adjusted"]),
                         (D("5250.00"), D("5000.00"), D("250.00"), True))
        self.assertEqual(self.making(), D("5250.00"))                    # the ledger agrees with the detail

    def test_the_stored_group_and_header_are_never_rewritten(self):
        self.rate("120")
        stored = self.db.groups[self.group]
        self.assertEqual((stored["making_per_gram"], stored["making_total"]), (D("100.00"), D("5000.00")))
        self.assertEqual(self.db.invoices[self.invoice]["gross_making_total"], D("5000.00"))

    def test_gold_adjustment_does_not_change_the_making_basis(self):
        self.adjust("ADD", "5")                                          # +5 g of gold must not enlarge the making difference
        self.assertEqual(self.rate("110")["delta"], D("500.00"))

    def test_an_unchanged_rate_a_negative_rate_and_a_rounded_to_nothing_difference_are_refused(self):
        for value, code in (("100", "RATE_UNCHANGED"), ("-1", "RATE_INVALID")):
            with self.subTest(value=value):
                with self.assertRaises(SupplierRuleError) as caught:
                    self.rate(value)
                self.assertEqual(caught.exception.code, code)
        self.assertEqual(len(self.db.rate_adjustments), 0)

    def test_a_reason_is_required_and_so_is_the_right(self):
        with self.assertRaises(SupplierRuleError):
            self.rate("110", reason="  ")
        weak = self.with_rights(list(OWNER_RIGHTS))                      # without SUPPLIER_INVOICE_ADJUST
        with self.assertRaises(Exception):
            self.rate("110", service=weak)
        self.assertEqual(len(self.db.rate_adjustments), 0)

    def test_an_exact_replay_posts_once_and_a_different_request_with_the_same_key_is_refused(self):
        key = base.key(999)
        first = self.rate("110", key=key)
        again = self.rate("110", key=key)
        self.assertTrue(again["duplicate"])
        self.assertEqual((first["id"], len(self.db.rate_adjustments)), (again["id"], 1))
        with self.assertRaises(SupplierRuleError) as caught:
            self.rate("120", key=key)
        self.assertEqual(caught.exception.code, "REQUEST_CONFLICT")

    def test_a_group_with_entered_pieces_is_refused(self):
        self.purchases.add_piece(self.invoice, str(self.group), 1, "10.000", base.key(980))
        with self.assertRaises(SupplierRuleError) as caught:
            self.rate("110")
        self.assertEqual(caught.exception.code, "GROUP_HAS_PIECES")
        self.assertEqual(len(self.db.rate_adjustments), 0)

    def test_a_reversed_invoice_cannot_be_adjusted(self):
        self.reverse()
        with self.assertRaises(SupplierRuleError) as caught:
            self.rate("110")
        self.assertEqual(caught.exception.code, "INVOICE_REVERSED")

    def test_a_failure_after_the_row_leaves_nothing_behind(self):
        original = self.db.making
        self.db.fail_at = None
        service = self.service
        repo_class = type(service._repository(self.db))
        real = repo_class.insert_making_movement

        def boom(self_, **kwargs):
            raise RuntimeError("ledger down")
        repo_class.insert_making_movement = boom
        try:
            with self.assertRaises(RuntimeError):
                self.rate("110")
        finally:
            repo_class.insert_making_movement = real
        self.assertEqual((len(self.db.rate_adjustments), self.db.making), (0, original))             # the transaction rolled the adjustment row back


class RateAdjustmentIntakeTests(RateAdjustCase):
    def test_a_piece_entered_after_the_adjustment_resolves_its_category_and_cost_from_the_new_rate(self):
        self.db.categories = {1: {"id": 1, "code": "A", "name": "A", "is_active": 1, "purchase_making_min_per_gram": D("50"), "purchase_making_max_per_gram": D("150")},
                              2: {"id": 2, "code": "B", "name": "B", "is_active": 1, "purchase_making_min_per_gram": D("200"), "purchase_making_max_per_gram": D("250")}}
        first = self.purchases.invoice_detail(self.invoice)["groups"][0]
        self.assertEqual(first["category_code"], "A")
        self.rate("210")
        group = self.purchases.invoice_detail(self.invoice)["groups"][0]
        self.assertEqual((group["category_code"], group["making_per_gram"]), ("B", D("210.00")))
        piece = self.purchases.add_piece(self.invoice, str(self.group), 1, "10.000", base.key(990))
        self.assertEqual(piece["making_per_gram"], D("210.00"))
        self.assertEqual(self.db.links[-1]["making_per_gram"], D("210.00"))

    def test_the_entry_list_uses_the_adjusted_rate(self):
        self.rate("120")
        groups = self.purchases.open_groups()
        self.assertEqual(groups[0]["making_per_gram"], D("120.00"))


class RateAdjustmentReversalTests(RateAdjustCase):
    def blockers(self):
        return [b["code"] for b in self.purchases.reversal_preview(self.invoice)["blockers"]]

    def test_the_invoice_cannot_be_reversed_while_a_rate_differs_from_the_original(self):
        self.rate("110")
        self.assertEqual(self.blockers(), ["INVOICE_HAS_RATE_ADJUSTMENTS"])
        self.assertFalse(self.purchases.reversal_preview(self.invoice)["can_reverse"])

    def test_returning_to_the_original_rate_unblocks_the_reversal_and_it_negates_the_original_making_only(self):
        self.rate("110")
        self.rate("100")                                                 # the opposite adjustment
        self.assertEqual(self.blockers(), [])
        self.assertEqual(self.making(), D("5000.00"))
        self.reverse()
        self.assertEqual((self.making(), self.gold()), (D("0.00"), D("0.000")))


class RateAdjustmentRulesTests(unittest.TestCase):
    def test_the_delta_is_rounded_once_half_up(self):
        self.assertEqual(rules.rate_adjustment_delta("0.333", "100", "100.01"), D("0.00"))
        self.assertEqual(rules.rate_adjustment_delta("0.500", "100", "100.01"), D("0.01"))
        self.assertEqual(rules.rate_adjustment_delta("12.345", "100.00", "99.00"), D("-12.35"))

    def test_the_effective_rate_is_the_newest_adjustment_of_that_group_only(self):
        group = {"id": 5, "making_per_gram": D("100")}
        rows = [{"group_id": 5, "new_rate": D("110")}, {"group_id": 6, "new_rate": D("999")}, {"group_id": 5, "new_rate": D("105")}]
        self.assertEqual(rules.effective_rate(group, rows), D("105"))
        self.assertEqual(rules.effective_rate(group, []), D("100"))


ROOT = Path(__file__).resolve().parents[1]
UP = (ROOT / "database" / "schema" / "106_supplier_group_rate_adjustment.sql").read_text(encoding="utf-8")
DOWN = (ROOT / "database" / "schema" / "rollback" / "106_supplier_group_rate_adjustment_rollback.sql").read_text(encoding="utf-8")


class RateAdjustmentSchemaTests(unittest.TestCase):
    """Static contract of migration 106: text only, nothing is executed on MySQL."""

    def test_the_migration_requires_105_and_is_guarded(self):
        self.assertIn("version=105", UP)
        self.assertIn("MIGRATION_106_REFUSED_missing_105", UP)
        self.assertIn("CREATE TABLE IF NOT EXISTS supplier_invoice_group_rate_adjustments", UP)
        self.assertIn("ON DUPLICATE KEY UPDATE", UP)
        self.assertIn("VALUES (106,", UP)

    def test_the_table_refuses_an_unchanged_rate_a_zero_delta_a_blank_reason_and_a_repeated_request(self):
        for text in ("chk_sigra_rates CHECK (old_rate >= 0 AND new_rate >= 0 AND new_rate <> old_rate)", "chk_sigra_delta CHECK (delta_amount <> 0)", "chk_sigra_reason",
                     "uq_sigra_request", "fk_sigra_invoice", "fk_sigra_group", "fk_sigra_user"):
            self.assertIn(text, UP)

    def test_the_making_movement_type_exists_and_no_new_right_is_added(self):
        self.assertIn("INSERT INTO supplier_making_movement_types", UP)
        self.assertIn("'GROUP_RATE_ADJUSTMENT'", UP)
        self.assertNotIn("INSERT INTO permissions", UP)                    # the existing SUPPLIER_INVOICE_ADJUST (owner / manager) governs it

    def test_the_rollback_refuses_when_an_adjustment_or_a_later_migration_exists(self):
        self.assertIn("ROLLBACK_106_REFUSED_later_migration_or_adjustment_exists", DOWN)
        self.assertIn("FROM supplier_invoice_group_rate_adjustments", DOWN)
        self.assertIn("version > 106", DOWN)
        self.assertIn("DELETE FROM schema_migrations WHERE version = 106", DOWN)

    def test_the_manifest_carries_the_checksum(self):
        digest = hashlib.sha256((ROOT / "database" / "schema" / "106_supplier_group_rate_adjustment.sql").read_bytes()).hexdigest()
        self.assertIn(f"{digest}  106_supplier_group_rate_adjustment.sql", (ROOT / "database" / "schema" / "SHA256SUMS").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
