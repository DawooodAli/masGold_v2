"""The inventory category of supplier-intake pieces is AUTOMATIC: resolved from the making group's making rate and the
EXISTING category rules (item_categories purchase-making ranges). Nothing is chosen on invoice creation, nothing is
stored on the group, no name is matched and no threshold is invented. Zero or several matching categories are reported
exactly, never guessed. The ranges below are the real DEV values (A 50-150, B 200-250, C 250-300, D 300-350, L 350-400,
T unconfigured), which overlap at 250 / 300 / 350 and leave gaps."""
from decimal import Decimal as D
import inspect
import unittest

from app.viewmodels.supplier_purchase_viewmodel import SupplierPurchaseViewModel, category_fields
from services import supplier_rules as rules
from services.supplier_purchase_service import SupplierPurchaseService
from services.supplier_rules import SupplierRuleError
from tests import test_suppliers as base
from tests.supplier_fakes import dev_categories


def row(code, low, high, active=1, category_id=1, name=None):
    return {"id": category_id, "code": code, "name": name or code, "is_active": active,
            "purchase_making_min_per_gram": None if low is None else D(low),
            "purchase_making_max_per_gram": None if high is None else D(high)}


DEV = [row("A", "50", "150", category_id=1), row("B", "200", "250", category_id=2), row("C", "250", "300", category_id=3),
       row("D", "300", "350", category_id=4), row("L", "350", "400", category_id=5), row("T", None, None, category_id=6)]


class RuleTests(unittest.TestCase):
    def resolve(self, rate, categories=DEV):
        return rules.resolve_category(rate, categories)

    def test_a_rate_inside_exactly_one_range_resolves_to_that_category(self):
        for rate, code in (("50", "A"), ("100", "A"), ("150", "A"), ("200", "B"), ("225", "B"), ("275", "C"),
                           ("325", "D"), ("375", "L"), ("400", "L")):
            with self.subTest(rate=rate):
                result = self.resolve(rate)
                self.assertEqual((result["status"], result["category"]["code"]), (rules.CATEGORY_RESOLVED, code))

    def test_the_boundaries_follow_the_applications_inclusive_convention_and_expose_the_overlaps(self):
        for rate, codes in (("250", ["B", "C"]), ("300", ["C", "D"]), ("350", ["D", "L"])):
            with self.subTest(rate=rate):
                result = self.resolve(rate)
                self.assertEqual((result["status"], result["category"], result["matches"]),
                                 (rules.CATEGORY_AMBIGUOUS, None, codes))                # reported, never guessed

    def test_a_rate_in_a_gap_or_outside_every_range_matches_nothing(self):
        for rate in ("0", "49.99", "150.01", "175", "199.99", "400.01", "1000"):
            with self.subTest(rate=rate):
                result = self.resolve(rate)
                self.assertEqual((result["status"], result["category"], result["matches"]),
                                 (rules.CATEGORY_NO_MATCH, None, []))

    def test_an_unconfigured_category_never_matches_and_an_inactive_one_is_ignored(self):
        self.assertNotIn("T", self.resolve("120")["matches"])                           # no bounds: not configured
        inactive = [row("A", "50", "150", active=0)] + DEV[1:]
        self.assertEqual(self.resolve("100", inactive)["status"], rules.CATEGORY_NO_MATCH)

    def test_a_missing_bound_is_open_like_everywhere_else_in_the_application(self):
        only_min, only_max = [row("X", "100", None)], [row("Y", None, "100")]
        self.assertEqual(self.resolve("99.99", only_min)["status"], rules.CATEGORY_NO_MATCH)
        self.assertEqual(self.resolve("100", only_min)["category"]["code"], "X")
        self.assertEqual(self.resolve("100000", only_min)["category"]["code"], "X")
        self.assertEqual(self.resolve("0", only_max)["category"]["code"], "Y")
        self.assertEqual(self.resolve("100.01", only_max)["status"], rules.CATEGORY_NO_MATCH)

    def test_no_name_or_code_is_ever_used_only_the_ranges(self):
        renamed = [row("ZZZ", "50", "150", name="خاتم ذهب")] + DEV[1:]
        self.assertEqual(self.resolve("100", renamed)["category"]["code"], "ZZZ")

    def test_the_arabic_explanations_name_the_exact_problem(self):
        gap = rules.category_problem("175", self.resolve("175"))
        self.assertIn("لا توجد فئة نشطة تطابق مصنعية المجموعة (175.00 ج/جرام)", gap)
        overlap = rules.category_problem("250", self.resolve("250"))
        self.assertIn("(250.00 ج/جرام) تطابق أكثر من فئة (B، C)", overlap)
        self.assertEqual(rules.category_problem("100", self.resolve("100")), "")


class ServiceTests(base.SupplierCase):
    """Groups: 100 EGP/g (A), 220 (B), 275 (C); weights 40 / 35 / 25."""
    ROWS = [{"weight": "40.000", "making_per_gram": "100", "description": "خواتم"},
            {"weight": "35.000", "making_per_gram": "220", "description": "سلاسل"},
            {"weight": "25.000", "making_per_gram": "275", "description": ""}]

    def setUp(self):
        super().setUp()
        dev_categories(self.db)
        self.invoice, _ = self.posted(rows=self.ROWS)
        self.invoice_id = self.invoice["id"]
        self.g1, self.g2, self.g3 = self.group_ids(self.invoice_id)

    def footprint(self):
        db = self.db
        return (len(db.items), len(db.links), len(db.barcodes), len(db.movements), len(db.audit_rows),
                db.sequences["barcode"], len(db.gold), len(db.making))

    def test_an_invoice_needs_no_category_to_be_created_posted_or_loaded(self):
        self.db.categories = {}                                                          # not even a category master
        invoice, _ = self.posted(rows=self.ROWS, reference="NOCAT")
        detail = self.purchases.invoice_detail(invoice["id"])
        self.assertEqual((detail["status"], detail["supplier_weight"], detail["gross_making"]),
                         ("POSTED", D("100.000"), D("18575.00")))
        self.assertEqual([g["category_status"] for g in detail["groups"]], [rules.CATEGORY_NO_MATCH] * 3)   # shown, not stored
        self.assertTrue(all("category_id" not in g or g["category_id"] is None for g in detail["groups"]))
        self.assertNotIn("category_id", next(iter(self.db.groups.values())))             # nothing is stored on the group

    def test_the_group_detail_shows_the_resolved_category_and_the_exact_problem(self):
        groups = self.purchases.invoice_detail(self.invoice_id)["groups"]
        self.assertEqual([(g["category_code"], g["category_status"], g["category_problem"]) for g in groups],
                         [("A", rules.CATEGORY_RESOLVED, ""), ("B", rules.CATEGORY_RESOLVED, ""),
                          ("C", rules.CATEGORY_RESOLVED, "")])
        self.db.groups[int(self.g2)]["making_per_gram"] = D("250.00")                    # an overlap
        self.db.groups[int(self.g3)]["making_per_gram"] = D("175.00")                    # a gap
        groups = self.purchases.invoice_detail(self.invoice_id)["groups"]
        self.assertEqual(groups[1]["category_status"], rules.CATEGORY_AMBIGUOUS)
        self.assertIn("(B، C)", groups[1]["category_problem"])
        self.assertEqual(groups[2]["category_status"], rules.CATEGORY_NO_MATCH)

    def test_each_piece_gets_the_category_of_its_groups_making_rate_whatever_the_item_type(self):
        first = self.piece(self.invoice_id, self.g1, "5.000")                            # item type 1
        second = self.purchases.add_piece(self.invoice_id, self.g2, 2, "6.000", self.key())   # item type 2
        third = self.purchases.add_piece(self.invoice_id, self.g3, 1, "7.000", self.key())
        items = [self.db.items[int(p["inventory_item_id"])] for p in (first, second, third)]
        self.assertEqual([i["category_id"] for i in items], [1, 2, 3])
        self.assertEqual([p["category_code"] for p in (first, second, third)], ["A", "B", "C"])
        self.assertEqual([i["acquisition_making_per_gram"] for i in items], [D("100.00"), D("220.00"), D("275.00")])
        audit = self.db.audit_rows[-1]["metadata"]
        self.assertEqual((audit["category_id"], audit["category_code"], audit["category_basis"]),
                         (3, "C", "PURCHASE_MAKING_RANGE"))

    def test_the_item_type_default_category_never_replaces_the_resolved_one(self):
        self.db.item_type_defaults = {1: 3}                                              # type 1 "defaults" to C
        piece = self.piece(self.invoice_id, self.g1, "5.000")
        self.assertEqual(self.db.items[int(piece["inventory_item_id"])]["category_id"], 1)

    def test_a_client_supplied_category_is_checked_never_trusted(self):
        before = self.footprint()
        for conflicting in (2, "3", 9.0):
            with self.subTest(supplied=conflicting), self.assertRaises(SupplierRuleError) as refused:
                self.purchases.add_piece(self.invoice_id, self.g1, 1, "1.000", self.key(), category_id=conflicting)
            self.assertEqual(refused.exception.code, "CATEGORY_CONFLICT")
        self.assertEqual(self.footprint(), before)                                       # a refusal persists nothing
        same = self.purchases.add_piece(self.invoice_id, self.g1, 1, "1.000", self.key(), category_id=1)
        self.assertEqual(self.db.items[int(same["inventory_item_id"])]["category_id"], 1)
        blank = self.purchases.add_piece(self.invoice_id, self.g1, 1, "1.000", self.key(), category_id=0)
        self.assertEqual(self.db.items[int(blank["inventory_item_id"])]["category_id"], 1)

    def test_a_rate_with_no_matching_category_refuses_the_piece_and_persists_nothing(self):
        self.db.groups[int(self.g2)]["making_per_gram"] = D("175.00")
        before = self.footprint()
        with self.assertRaises(SupplierRuleError) as refused:
            self.piece(self.invoice_id, self.g2, "1.000")
        self.assertEqual(refused.exception.code, "CATEGORY_NO_MATCH")
        self.assertIn("لا توجد فئة نشطة تطابق مصنعية المجموعة (175.00 ج/جرام)", str(refused.exception))
        self.assertEqual(self.footprint(), before)                                       # barcode sequence untouched too

    def test_an_ambiguous_rate_names_the_categories_and_is_never_guessed(self):
        self.db.groups[int(self.g2)]["making_per_gram"] = D("250.00")
        before = self.footprint()
        with self.assertRaises(SupplierRuleError) as refused:
            self.piece(self.invoice_id, self.g2, "1.000")
        self.assertEqual(refused.exception.code, "CATEGORY_AMBIGUOUS")
        self.assertIn("(B، C)", str(refused.exception))
        self.assertEqual(self.footprint(), before)

    def test_the_resolution_uses_the_current_ranges_and_never_rewrites_existing_pieces(self):
        old = self.piece(self.invoice_id, self.g1, "5.000")                              # received as A
        snapshot = dict(self.db.items[int(old["inventory_item_id"])])
        self.db.categories[1]["is_active"] = 0                                           # A is deactivated in Master Data
        with self.assertRaises(SupplierRuleError) as refused:
            self.piece(self.invoice_id, self.g1, "1.000")
        self.assertEqual(refused.exception.code, "CATEGORY_NO_MATCH")
        self.assertEqual(self.db.items[int(old["inventory_item_id"])], snapshot)         # history untouched
        self.assertEqual(self.db.links[0]["making_per_gram"], D("100.00"))               # and its making snapshot

    def test_the_group_weight_allowance_still_applies_with_a_resolved_category(self):
        self.piece(self.invoice_id, self.g1, "40.333")
        with self.assertRaises(SupplierRuleError) as refused:
            self.piece(self.invoice_id, self.g1, "0.001")
        self.assertEqual(refused.exception.code, "GROUP_WEIGHT_LIMIT")

    def test_resolving_a_category_never_touches_the_supplier_accounts(self):
        before = (self.balances(), list(self.db.gold), list(self.db.making))
        self.piece(self.invoice_id, self.g1, "5.000")
        self.piece(self.invoice_id, self.g2, "5.000")
        self.assertEqual(before, (self.balances(), list(self.db.gold), list(self.db.making)))

    def test_the_detail_reads_everything_inside_its_transaction_like_the_real_connection_requires(self):
        """SP000022 regression: a repository read placed after the `with` block ran on a CLOSED MySQL connection
        ("MySQL Connection not available") and the intake dialog showed the generic load error. The fake connection is
        closed when a transaction ends, so the same mistake fails here."""
        from mysql.connector import OperationalError
        captured, make = [], self.purchases._repository
        self.purchases._repository = lambda connection: (captured.append(connection), make(connection))[1]
        detail = self.purchases.invoice_detail(self.invoice_id)                          # must not touch a closed connection
        self.assertEqual([g["category_code"] for g in detail["groups"]], ["A", "B", "C"])
        with self.assertRaises(OperationalError):                                        # the connection IS closed afterwards
            captured[-1].items

    def test_there_is_no_manual_assignment_path_left(self):
        self.assertFalse(hasattr(SupplierPurchaseService, "assign_group_category"))
        self.assertEqual(inspect.signature(SupplierPurchaseService.add_piece).parameters["category_id"].default, None)


class ViewModelTests(base.SupplierCase):
    def setUp(self):
        super().setUp()
        dev_categories(self.db)
        self.invoice, _ = self.posted(rows=ServiceTests.ROWS)
        self.vm = SupplierPurchaseViewModel(self.session, self.purchases)

    def test_the_view_model_exposes_the_resolved_category_read_only(self):
        self.vm._detail = self.purchases.invoice_detail(self.invoice["id"])
        groups = self.vm.detail["groups"]
        self.assertEqual([(g["categoryResolved"], g["categoryText"], g["categoryProblem"]) for g in groups],
                         [(True, "A", ""), (True, "B", ""), (True, "C", "")])
        self.assertFalse(hasattr(SupplierPurchaseViewModel, "assignGroupCategory"))
        self.assertFalse(hasattr(SupplierPurchaseViewModel, "canAssignCategory"))

    def test_an_unresolved_group_carries_the_reason_not_a_category(self):
        row = {"category_status": rules.CATEGORY_AMBIGUOUS, "category_code": "", "category_name": "",
               "category_problem": "تطابق أكثر من فئة"}
        self.assertEqual(category_fields(row), {"categoryStatus": "AMBIGUOUS", "categoryResolved": False,
                                                "categoryText": "", "categoryProblem": "تطابق أكثر من فئة"})
        named = {"category_status": rules.CATEGORY_RESOLVED, "category_code": "B", "category_name": "جيدة", "category_problem": ""}
        self.assertEqual(category_fields(named)["categoryText"], "B — جيدة")


if __name__ == "__main__":
    unittest.main()
