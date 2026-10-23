"""Purchasing & Suppliers V1 (Phase 1): rules, supplier master, purchase invoices, the two supplier ledgers,
idempotent posting, piece-by-piece intake through the canonical inventory path, informational weight variance,
immutability and permissions. Real services over in-memory doubles; the live database is blocked."""
import ast
from datetime import date
from decimal import Decimal as D
from pathlib import Path
import unittest

from services import supplier_rules as rules
from services.supplier_rules import SupplierRuleError
from tests.supplier_fakes import ALL, build

ROOT = Path(__file__).resolve().parents[1]
KEY = "11111111-1111-4111-8111-%012d"


def key(n):
    return KEY % n


GROUPS = [{"weight": "40.000", "making_per_gram": "120", "description": "خواتم"},
          {"weight": "35.000", "making_per_gram": "180", "description": "سلاسل"},
          {"weight": "25.000", "making_per_gram": "150", "description": ""}]


def groups(*rows):
    return [{"weight": w, "rate": r, "description": ""} for w, r in rows]


class RulesTests(unittest.TestCase):
    def test_making_is_per_gram_per_group_and_the_example_is_14850(self):
        result = rules.normalize_groups([{"weight": g["weight"], "making_per_gram": g["making_per_gram"],
                                          "description": g["description"]} for g in GROUPS])
        self.assertEqual(result["total_weight"], D("100.000"))
        self.assertEqual(result["gross_making"], D("14850.00"))
        self.assertEqual([g["making_total"] for g in result["groups"]], [D("4800.00"), D("6300.00"), D("3750.00")])

    def test_everything_is_decimal_never_float(self):
        result = rules.normalize_groups([{"weight": "0.333", "making_per_gram": "10.10"}])
        for value in (result["total_weight"], result["gross_making"], result["groups"][0]["making_total"],
                      rules.group_making_total("0.333", "10.10"), rules.equivalent_21("3.3", 18)):
            self.assertIsInstance(value, D)
        self.assertEqual(result["gross_making"], D("3.36"))                       # 0.333 x 10.10 = 3.3633 → 3.36

    def test_eq21_keeps_the_karat_basis_without_premature_rounding(self):
        self.assertEqual(rules.equivalent_21("100.000", 21), D("100.000000"))
        self.assertEqual(rules.equivalent_21("100.000", 18), D("85.714286"))
        self.assertEqual(rules.equivalent_21("100.000", 24), D("114.285714"))

    def test_direction_and_the_one_sign_convention(self):
        self.assertEqual((rules.direction(D("5")), rules.direction(D("-5")), rules.direction(0)),
                         (rules.SHOP_OWES, rules.SUPPLIER_OWES, rules.SETTLED))

    def test_weight_variance_exact_negative_positive(self):
        self.assertEqual(rules.variance("100.000", "100.000"), D("0.000"))
        self.assertEqual(rules.variance("99.920", "100.000"), D("-0.080"))
        self.assertEqual(rules.variance("100.150", "100.000"), D("0.150"))
        progress = rules.progress("99.920", "100.000")
        self.assertEqual((progress["variance"], progress["remaining_reference"], progress["percent"],
                          progress["over_supplier_weight"]), (D("-0.080"), D("0.080"), D("99.9"), False))
        self.assertTrue(rules.progress("100.150", "100.000")["over_supplier_weight"])

    def test_invalid_input_is_refused_with_a_code(self):
        for bad, code in ((dict(weight="0", making_per_gram="1"), "WEIGHT_INVALID"),
                          (dict(weight="-1", making_per_gram="1"), "WEIGHT_INVALID"),
                          (dict(weight="abc", making_per_gram="1"), "WEIGHT_INVALID"),
                          (dict(weight="", making_per_gram="1"), "WEIGHT_INVALID"),
                          (dict(weight="1", making_per_gram="-5"), "RATE_INVALID"),
                          (dict(weight="1", making_per_gram="x"), "RATE_INVALID")):
            with self.subTest(bad=bad), self.assertRaises(SupplierRuleError) as refused:
                rules.normalize_groups([bad])
            self.assertEqual(refused.exception.code, code)
        with self.assertRaises(SupplierRuleError):
            rules.normalize_groups([])
        with self.assertRaises(SupplierRuleError):
            rules.normalize_groups([{"weight": "1", "making_per_gram": "1"}] * (rules.MAX_GROUPS + 1))

    def test_arabic_digits_are_the_same_digits(self):
        result = rules.normalize_groups([{"weight": "٤٠٫٥٠٠", "making_per_gram": "١٢٠"}])
        self.assertEqual((result["total_weight"], result["gross_making"]), (D("40.500"), D("4860.00")))


class SupplierCase(unittest.TestCase):
    def setUp(self):
        self.db, self.session, self.authorization, self.suppliers, self.purchases = build()
        self.supplier_id = self.suppliers.create_supplier("مورد الذهب", "0100", "ملاحظة", karat_id=1)
        self.n = 0

    def key(self):
        self.n += 1
        return key(self.n)

    def draft(self, rows=None, karat_id=1, reference="INV-77", supplier_id=None, request_key=None):
        return self.purchases.create_draft(
            supplier_id or self.supplier_id, karat_id, reference, "2026-10-08",
            rows if rows is not None else [{"weight": g["weight"], "making_per_gram": g["making_per_gram"],
                                            "description": g["description"]} for g in GROUPS],
            "", request_key or self.key())

    def posted(self, **kwargs):
        draft = self.draft(**kwargs)
        return self.purchases.post_invoice(draft["id"], self.key()), draft

    def piece(self, invoice_id, group_id, weight, **kwargs):
        kwargs.setdefault("barcode_mode", "AUTO")
        return self.purchases.add_piece(invoice_id, group_id, 1, weight, self.key(), **kwargs)

    def group_ids(self, invoice_id):
        return [str(g["id"]) for g in self.purchases._repository(self.db).groups(invoice_id)]

    def balances(self):
        detail = self.suppliers.supplier_detail(self.supplier_id)
        return detail["balances"]


class SupplierMasterTests(SupplierCase):
    def test_create_edit_deactivate_and_unique_name(self):
        self.suppliers.update_supplier(self.supplier_id, "مورد الذهب الجديد", "0111", "")
        self.assertEqual(self.db.suppliers[int(self.supplier_id)]["name"], "مورد الذهب الجديد")
        with self.assertRaises(SupplierRuleError) as refused:
            self.suppliers.create_supplier("مورد الذهب الجديد", karat_id=1)
        self.assertEqual(refused.exception.code, "SUPPLIER_EXISTS")
        self.suppliers.set_active(self.supplier_id, False)
        self.assertFalse(self.suppliers.list_suppliers(active_only=True))
        self.assertEqual(len(self.suppliers.list_suppliers()), 1)
        with self.assertRaises(SupplierRuleError) as inactive:
            self.draft()
        self.assertEqual(inactive.exception.code, "SUPPLIER_INACTIVE")

    def test_a_used_supplier_is_never_deleted_an_unused_one_may_be(self):
        self.posted()
        with self.assertRaises(SupplierRuleError) as refused:
            self.suppliers.delete_unused_supplier(self.supplier_id)
        self.assertEqual(refused.exception.code, "SUPPLIER_IN_USE")
        self.assertIn(int(self.supplier_id), self.db.suppliers)
        spare = self.suppliers.create_supplier("مورد بلا حركة", karat_id=1)
        self.suppliers.delete_unused_supplier(spare)
        self.assertNotIn(int(spare), self.db.suppliers)

    def test_a_blank_name_is_refused(self):
        with self.assertRaises(SupplierRuleError):
            self.suppliers.create_supplier("   ", karat_id=1)


class DraftTests(SupplierCase):
    def test_valid_draft_has_one_karat_reconciled_groups_and_no_ledger_effect(self):
        draft = self.draft()
        self.assertEqual((draft["status"], draft["karat_id"], draft["supplier_weight"], draft["gross_making"]),
                         ("DRAFT", 1, D("100.000"), D("14850.00")))
        self.assertEqual(len(self.purchases._repository(self.db).groups(draft["id"])), 3)
        self.assertEqual((self.db.gold, self.db.making), ([], []))
        self.assertNotIn("karat_id", self.purchases._repository(self.db).groups(draft["id"])[0])   # karat is per invoice

    def test_the_invoice_weight_is_the_sum_of_its_groups(self):
        draft = self.draft(rows=[{"weight": "10", "making_per_gram": "100"}, {"weight": "2.5", "making_per_gram": "0"}])
        self.assertEqual((draft["supplier_weight"], draft["gross_making"]), (D("12.500"), D("1000.00")))

    def test_inconsistent_or_empty_groups_never_become_a_draft(self):
        for rows in ([], [{"weight": "0", "making_per_gram": "1"}], [{"weight": "5", "making_per_gram": "-1"}]):
            with self.subTest(rows=rows), self.assertRaises(SupplierRuleError):
                self.draft(rows=rows)
        self.assertEqual(self.db.invoices, {})

    def test_unknown_supplier_or_karat_is_refused(self):
        with self.assertRaises(SupplierRuleError):
            self.draft(supplier_id="999")
        with self.assertRaises(SupplierRuleError):
            self.draft(karat_id=99)

    def test_the_same_request_key_returns_the_same_draft(self):
        first = self.draft(request_key=key(900))
        second = self.draft(request_key=key(900))
        self.assertEqual((first["id"], second["duplicate"]), (second["id"], True))
        self.assertEqual(len(self.db.invoices), 1)

    def test_an_edited_draft_keeps_its_invoice_and_reference_and_touches_no_ledger_or_balance(self):
        draft = self.draft()
        before = (self.balances(), list(self.db.gold), list(self.db.making), len(self.db.invoices))
        rows = [{"weight": "45.000", "making_per_gram": "130", "description": "خواتم"},
                {"weight": "55.000", "making_per_gram": "100", "description": "سلاسل"}]
        edited = self.purchases.update_draft(draft["id"], self.supplier_id, 1, "INV-EDITED", "2026-10-09", rows, "ملاحظة",
                                             _expected_actor=None)
        self.assertEqual((edited["id"], edited["code"], edited["status"]), (draft["id"], draft["code"], "DRAFT"))
        detail = self.purchases.invoice_detail(draft["id"])                              # reopen: the changes persisted
        self.assertEqual((detail["code"], detail["supplier_reference"], detail["supplier_weight"], detail["gross_making"]),
                         (draft["code"], "INV-EDITED", D("100.000"), D("11350.00")))
        self.assertEqual([(g["supplier_weight"], g["making_per_gram"], g["description"]) for g in detail["groups"]],
                         [(D("45.000"), D("130.00"), "خواتم"), (D("55.000"), D("100.00"), "سلاسل")])
        self.assertEqual((self.balances(), list(self.db.gold), list(self.db.making), len(self.db.invoices)), before)
        self.assertEqual(self.db.audit_rows[-1]["event_type"], "SUPPLIER_INVOICE_DRAFT_UPDATED")

    def test_saving_a_draft_without_any_change_is_fine_even_though_mysql_reports_zero_changed_rows(self):
        draft = self.draft()
        detail = self.purchases.invoice_detail(draft["id"])
        same_rows = [{"weight": str(g["supplier_weight"]), "making_per_gram": str(g["making_per_gram"]),
                      "description": g["description"]} for g in detail["groups"]]
        again = self.purchases.update_draft(draft["id"], self.supplier_id, detail["karat_id"], detail["supplier_reference"],
                                            str(detail["invoice_date"]), same_rows, detail["notes"])
        self.assertEqual((again["id"], again["status"]), (draft["id"], "DRAFT"))         # not "already posted"

    def test_a_posted_invoice_can_never_be_edited_through_the_draft_path(self):
        posted, draft = self.posted()
        before = (dict(self.db.invoices[int(posted["id"])]), [dict(g) for g in self.db.groups.values()],
                  list(self.db.gold), list(self.db.making))
        with self.assertRaises(SupplierRuleError) as refused:
            self.purchases.update_draft(posted["id"], self.supplier_id, 1, "HACK", "2026-10-09",
                                        [{"weight": "1", "making_per_gram": "1", "description": ""}], "")
        self.assertEqual(refused.exception.code, "INVOICE_NOT_DRAFT")
        self.assertEqual((self.db.invoices[int(posted["id"])], list(self.db.groups.values()), list(self.db.gold),
                          list(self.db.making)), (before[0], before[1], before[2], before[3]))

    def test_draft_can_be_edited_and_cancelled_until_posted(self):
        draft = self.draft()
        edited = self.purchases.update_draft(draft["id"], self.supplier_id, 1, "R2", "2026-10-09",
                                             [{"weight": "50", "making_per_gram": "10"}], "")
        self.assertEqual((edited["karat_id"], edited["supplier_weight"], edited["gross_making"]),
                         (1, D("50.000"), D("500.00")))              # the invoice karat is the supplier's (21K)
        self.assertEqual(len(self.purchases._repository(self.db).groups(draft["id"])), 1)
        self.purchases.cancel_draft(draft["id"])
        self.assertEqual(self.db.invoices[int(draft["id"])]["status"], "CANCELLED")
        with self.assertRaises(SupplierRuleError):
            self.purchases.post_invoice(draft["id"], self.key())
        self.assertEqual((self.db.gold, self.db.making), ([], []))


class PostingTests(SupplierCase):
    def test_posting_writes_the_gold_liability_and_the_making_liability(self):
        posted, draft = self.posted()
        self.assertEqual((posted["status"], posted["duplicate"]), ("POSTED", False))
        self.assertNotIn("eq21", posted)                                          # no Eq21 on a supplier-facing result
        (gold,) = self.db.gold
        self.assertEqual((gold["weight_delta_grams"], gold["equivalent_21_delta_grams"], gold["karat_id"],
                          gold["source_type"], gold["source_id"], gold["created_by_user_id"]),
                         (D("100.000"), D("100.000000"), 1, "SUPPLIER_PURCHASE_INVOICE", int(draft["id"]), 7))
        (making,) = self.db.making
        self.assertEqual((making["amount_delta"], making["source_id"]), (D("14850.00"), int(draft["id"])))
        self.assertEqual(self.db.audit_rows[-1]["event_type"], "SUPPLIER_INVOICE_POSTED")

    def test_original_physical_weight_and_karat_are_preserved_and_the_supplier_account_is_in_those_grams(self):
        supplier = self.suppliers.create_supplier("مورد عيار 18", karat_id=2)
        self.posted(supplier_id=supplier, karat_id=2, rows=[{"weight": "100", "making_per_gram": "10"}])
        (gold,) = self.db.gold
        self.assertEqual((gold["karat_id"], gold["weight_delta_grams"]), (2, D("100.000")))
        self.assertEqual(gold["equivalent_21_delta_grams"], D("85.714286"))      # stored unchanged, an internal compatibility field
        balances = self.suppliers.supplier_detail(supplier)["balances"]
        self.assertEqual((balances["gold_status"], balances["gold_karat_value"], balances["gold_balance"],
                          balances["gold_direction"]), ("RESOLVED", 18, D("100.000"), rules.SHOP_OWES))   # 100 g of 18K, not 85.714

    def test_posting_twice_never_duplicates_a_ledger_movement(self):
        draft = self.draft()
        first = self.purchases.post_invoice(draft["id"], key(500))
        again = self.purchases.post_invoice(draft["id"], key(500))              # a retry of the same request
        self.assertEqual((first["duplicate"], again["duplicate"]), (False, True))
        with self.assertRaises(SupplierRuleError) as refused:                   # any other second post
            self.purchases.post_invoice(draft["id"], key(501))
        self.assertEqual(refused.exception.code, "ALREADY_POSTED")
        self.assertEqual((len(self.db.gold), len(self.db.making)), (1, 1))

    def test_a_failed_posting_leaves_nothing_behind(self):
        draft = self.draft()
        self.db.groups[min(self.db.groups)]["supplier_weight_grams"] = D("41.000")      # groups no longer reconcile
        with self.assertRaises(SupplierRuleError) as refused:
            self.purchases.post_invoice(draft["id"], self.key())
        self.assertEqual(refused.exception.code, "INVOICE_INCONSISTENT")
        self.assertEqual((self.db.gold, self.db.making, self.db.invoices[int(draft["id"])]["status"]),
                         ([], [], "DRAFT"))

    def test_a_posted_invoice_cannot_be_edited_cancelled_or_reposted(self):
        posted, draft = self.posted()
        with self.assertRaises(SupplierRuleError):
            self.purchases.update_draft(draft["id"], self.supplier_id, 1, "x", "2026-10-09",
                                        [{"weight": "1", "making_per_gram": "1"}], "")
        with self.assertRaises(SupplierRuleError):
            self.purchases.cancel_draft(draft["id"])
        invoice = self.db.invoices[int(draft["id"])]
        self.assertEqual((invoice["supplier_weight_grams"], invoice["gross_making_total"]), (D("100.000"), D("14850.00")))
        self.assertEqual(len(self.purchases._repository(self.db).groups(draft["id"])), 3)

    def test_zero_making_posts_the_gold_only(self):
        self.posted(rows=[{"weight": "10", "making_per_gram": "0"}])
        self.assertEqual((len(self.db.gold), len(self.db.making)), (1, 0))

    def test_net_balances_are_independent_per_dimension_and_never_floored(self):
        self.posted()                                                          # 100.000 g 21K, making 14,850
        self.posted(rows=[{"weight": "50", "making_per_gram": "100"}], reference="B")   # the same supplier, the same 21K
        balances = self.balances()
        self.assertEqual((balances["gold_direction"], balances["gold_balance"], balances["making"]),
                         (rules.SHOP_OWES, D("150.000"), D("19850.00")))
        self.assertEqual([(k["karat_value"], k["weight_balance"]) for k in balances["gold_by_karat"]], [(21, D("150.000"))])
        # A later credit (a future phase's movement) can take the supplier past zero: no floor, explicit direction.
        self.db.gold.append({**self.db.gold[0], "id": 99, "weight_delta_grams": D("-159.950"),
                             "equivalent_21_delta_grams": D("-159.950"), "source_id": 99, "karat_id": 1})
        self.db.making.append({**self.db.making[0], "id": 99, "amount_delta": D("-21350.00"), "source_id": 99})
        after = self.balances()
        self.assertEqual((after["gold_direction"], after["gold_balance"]), (rules.SUPPLIER_OWES, D("-9.950")))
        self.assertEqual((after["making_direction"], after["making"]), (rules.SUPPLIER_OWES, D("-1500.00")))

    def test_the_actor_is_always_the_session_user(self):
        self.posted()
        self.assertEqual({m["created_by_user_id"] for m in self.db.gold + self.db.making}, {7})
        self.assertEqual({a["user_id"] for a in self.db.audit_rows}, {7})


class IntakeTests(SupplierCase):
    def setUp(self):
        super().setUp()
        self.posted_invoice, _ = self.posted()
        self.invoice_id = self.posted_invoice["id"]
        self.g1, self.g2, self.g3 = self.group_ids(self.invoice_id)

    def test_a_piece_is_a_real_inventory_item_with_everything_inherited(self):
        result = self.piece(self.invoice_id, self.g2, "12.340", size="18")
        item = self.db.items[int(result["inventory_item_id"])]
        self.assertEqual((item["karat_id"], item["weight_grams"], item["stock_classification"],
                          item["acquisition_source"], item["inventory_status"], item["size"]),
                         (1, D("12.340"), "NEW_STOCK", "SUPPLIER_PURCHASE", "IN_STOCK", "18"))
        self.assertEqual(item["acquisition_making_per_gram"], D("180.00"))     # group 2's ORIGINAL rate
        self.assertEqual(item["created_by_user_id"], 7)
        self.assertEqual(len(self.db.movements), 1)                            # the canonical ITEM_CREATED movement
        self.assertEqual(self.db.barcodes[result["barcode"]], item["id"])      # barcode via the canonical sequence
        self.assertRegex(result["barcode"], r"^01261008$")
        (link,) = self.db.links
        self.assertEqual((link["invoice_id"], link["group_id"], link["making_per_gram"]),
                         (int(self.invoice_id), int(self.g2), D("180.00")))

    def test_every_piece_is_traceable_back_to_invoice_group_and_supplier(self):
        result = self.piece(self.invoice_id, self.g3, "5.000")
        origin = self.purchases.piece_origin(result["inventory_item_id"])
        self.assertEqual((origin["invoice_id"], origin["group_no"], origin["supplier_name"],
                          origin["supplier_reference"], origin["making_per_gram"]),
                         (self.invoice_id, 3, "مورد الذهب", "INV-77", D("150.00")))
        self.assertIsNone(self.purchases.piece_origin("999"))                  # stock from elsewhere

    def test_the_making_rate_snapshot_survives_a_later_change_of_the_group_row(self):
        result = self.piece(self.invoice_id, self.g1, "5.000")
        self.db.groups[int(self.g1)]["making_per_gram"] = D("999.00")          # tampering with the live group row
        self.assertEqual(self.db.links[0]["making_per_gram"], D("120.00"))
        self.assertEqual(self.db.items[int(result["inventory_item_id"])]["acquisition_making_per_gram"], D("120.00"))

    def test_partial_progress_and_group_and_invoice_variance(self):
        self.piece(self.invoice_id, self.g1, "39.900")
        self.piece(self.invoice_id, self.g1, "0.050")
        self.piece(self.invoice_id, self.g2, "34.970")
        detail = self.purchases.invoice_detail(self.invoice_id)
        first, second, third = detail["groups"]
        self.assertEqual((first["piece_count"], first["actual_weight"], first["variance"], first["remaining_reference"]),
                         (2, D("39.950"), D("-0.050"), D("0.050")))
        self.assertEqual((second["piece_count"], second["variance"]), (1, D("-0.030")))
        self.assertEqual((third["piece_count"], third["actual_weight"], third["variance"]), (0, D("0.000"), D("-25.000")))
        self.assertEqual((detail["piece_count"], detail["intake"]["actual_weight"], detail["intake"]["variance"]),
                         (3, D("74.920"), D("-25.080")))

    def test_the_100_vs_99_920_example(self):
        for group, weight in ((self.g1, "40.000"), (self.g2, "34.950"), (self.g3, "24.970")):
            self.piece(self.invoice_id, group, weight)
        intake = self.purchases.invoice_detail(self.invoice_id)["intake"]
        self.assertEqual((intake["actual_weight"], intake["variance"], intake["over_supplier_weight"]),
                         (D("99.920"), D("-0.080"), False))

    def test_exact_match_and_positive_variance(self):
        self.piece(self.invoice_id, self.g1, "40.000")
        self.piece(self.invoice_id, self.g2, "35.000")
        self.piece(self.invoice_id, self.g3, "25.000")
        self.assertEqual(self.purchases.invoice_detail(self.invoice_id)["intake"]["variance"], D("0.000"))
        self.piece(self.invoice_id, self.g3, "0.150")
        intake = self.purchases.invoice_detail(self.invoice_id)["intake"]
        self.assertEqual((intake["variance"], intake["over_supplier_weight"]), (D("0.150"), True))

    def test_variance_changes_neither_supplier_balance_nor_making_nor_inventory_weights(self):
        before = (self.balances(), len(self.db.gold), len(self.db.making), list(self.db.gold), list(self.db.making))
        self.piece(self.invoice_id, self.g1, "39.000")
        self.piece(self.invoice_id, self.g1, "1.200")                          # group 1 now OVER its supplier weight (within 0.333)
        after = (self.balances(), len(self.db.gold), len(self.db.making), list(self.db.gold), list(self.db.making))
        self.assertEqual(before, after)
        self.assertEqual(sorted(i["weight_grams"] for i in self.db.items.values()), [D("1.200"), D("39.000")])
        self.assertEqual(self.db.movements[0]["weight_grams"], D("39.000"))    # inventory keeps the shop-scale weight

    def test_intake_never_mutates_the_posted_contract(self):
        before = (dict(self.db.invoices[int(self.invoice_id)]), [dict(g) for g in self.db.groups.values()])
        self.piece(self.invoice_id, self.g1, "20.000")
        self.piece(self.invoice_id, self.g2, "20.000")
        after = (self.db.invoices[int(self.invoice_id)], list(self.db.groups.values()))
        self.assertEqual(before[0], after[0])
        self.assertEqual(before[1], after[1])
        detail = self.purchases.invoice_detail(self.invoice_id)
        self.assertEqual((detail["supplier_weight"], detail["gross_making"], detail["status"]),
                         (D("100.000"), D("14850.00"), "POSTED"))

    def test_the_same_piece_request_is_idempotent(self):
        first = self.purchases.add_piece(self.invoice_id, self.g1, 1, "5.000", key(800))
        again = self.purchases.add_piece(self.invoice_id, self.g1, 1, "5.000", key(800))
        self.assertEqual((first["duplicate"], again["duplicate"], again["inventory_item_id"]),
                         (False, True, first["inventory_item_id"]))
        self.assertEqual((len(self.db.items), len(self.db.links), len(self.db.barcodes)), (1, 1, 1))
        with self.assertRaises(SupplierRuleError):                              # the key cannot be reused elsewhere
            self.purchases.add_piece(self.invoice_id, self.g2, 1, "5.000", key(800))

    def test_barcode_modes(self):
        auto = self.piece(self.invoice_id, self.g1, "1.000")
        manual = self.piece(self.invoice_id, self.g1, "1.000", barcode_mode="MANUAL", barcode="SUP-0001")
        none = self.piece(self.invoice_id, self.g1, "1.000", barcode_mode="NONE")
        self.assertEqual((auto["barcode"], manual["barcode"], none["barcode"]), ("01261008", "SUP-0001", "02261008"))
        self.assertEqual(self.db.items[int(none["inventory_item_id"])]["barcode"], "02261008")       # «no barcode» is also automatic now: every piece can be printed
        with self.assertRaises(SupplierRuleError):
            self.piece(self.invoice_id, self.g1, "1.000", barcode_mode="MANUAL", barcode="  ")
        with self.assertRaises(SupplierRuleError):
            self.piece(self.invoice_id, self.g1, "1.000", barcode_mode="BOGUS")

    def test_a_duplicate_manual_barcode_leaves_no_orphan_piece_or_link(self):
        self.piece(self.invoice_id, self.g1, "1.000", barcode_mode="MANUAL", barcode="DUP-1")
        with self.assertRaises(SupplierRuleError) as refused:
            self.piece(self.invoice_id, self.g1, "2.000", barcode_mode="MANUAL", barcode="DUP-1")
        self.assertIn("الباركود", str(refused.exception))
        self.assertEqual((len(self.db.items), len(self.db.links), len(self.db.movements)), (1, 1, 1))

    def test_a_failure_after_the_inventory_row_rolls_everything_back(self):
        self.db.fail_after_item = True
        with self.assertRaises(RuntimeError):
            self.piece(self.invoice_id, self.g1, "1.000")
        self.assertEqual((self.db.items, self.db.links, self.db.barcodes, self.db.movements), ({}, [], {}, []))
        self.db.fail_after_item = False
        self.assertEqual(self.piece(self.invoice_id, self.g1, "1.000")["barcode"], "01261008")   # sequence rolled back too

    def test_refusals_write_nothing(self):
        draft = self.draft(reference="D")
        cases = [(draft["id"], self.g1, "5", "INVOICE_NOT_POSTED"),
                 (self.invoice_id, "99999", "5", "GROUP_NOT_FOUND"),
                 (self.invoice_id, self.g1, "0", "WEIGHT_INVALID"),
                 (self.invoice_id, self.g1, "100.001", "PIECE_TOO_HEAVY")]
        for invoice, group, weight, code in cases:
            with self.subTest(code=code), self.assertRaises(SupplierRuleError) as refused:
                self.purchases.add_piece(invoice, group, 1, weight, self.key())
            self.assertEqual(refused.exception.code, code)
        other_group = self.group_ids(draft["id"])[0]
        with self.assertRaises(SupplierRuleError):                              # a group of ANOTHER invoice
            self.purchases.add_piece(self.invoice_id, other_group, 1, "5", self.key())
        with self.assertRaises(SupplierRuleError) as bad_type:
            self.purchases.add_piece(self.invoice_id, self.g1, 999, "5", self.key())
        self.assertEqual(bad_type.exception.code, "INVENTORY_REFUSED")
        self.assertEqual((self.db.items, self.db.links, self.db.barcodes), ({}, [], {}))

    def test_supplier_pieces_have_their_own_source_and_it_changes_no_accounting(self):
        import inspect
        from services import supplier_purchase_service
        from services.inventory_service import InventoryService
        from tests.supplier_fakes import InventoryRepo
        self.assertEqual(supplier_purchase_service.INTAKE_SOURCE, "SUPPLIER_PURCHASE")
        self.assertNotEqual(supplier_purchase_service.INTAKE_SOURCE, "MARKET_PURCHASE")
        before = (self.balances(), list(self.db.gold), list(self.db.making))
        first = self.piece(self.invoice_id, self.g1, "5.000")
        second = self.piece(self.invoice_id, self.g2, "6.000", barcode_mode="NONE")
        sources = {self.db.items[int(p["inventory_item_id"])]["acquisition_source"] for p in (first, second)}
        self.assertEqual(sources, {"SUPPLIER_PURCHASE"})
        self.assertEqual(before, (self.balances(), list(self.db.gold), list(self.db.making)))
        # Traceability is untouched by the source label.
        origin = self.purchases.piece_origin(first["inventory_item_id"])
        self.assertEqual((origin["invoice_id"], origin["group_no"], origin["making_per_gram"]),
                         (self.invoice_id, 1, D("120.00")))
        # The shared canonical path still defaults to manual entry and passes MARKET_PURCHASE through untouched.
        self.assertEqual(inspect.signature(InventoryService._create_item).parameters["acquisition_source"].default,
                         "MANUAL_ENTRY")
        automatic = type("B", (), {"__init__": lambda s, c: None, "allocate_barcode_value": lambda s, d: "99261008"})
        market = InventoryService(repository=InventoryRepo, barcode_repository=automatic).create_item_on_connection(
            self.db, actor_user_id=7, item_type_id=1, karat_id=1, weight_grams="2",
            acquisition_source="MARKET_PURCHASE")
        self.assertEqual(self.db.items[market]["acquisition_source"], "MARKET_PURCHASE")
        self.assertIsNone(self.purchases.piece_origin(market))                       # not a supplier piece

    def test_intake_does_not_need_or_use_the_manual_stock_right(self):
        self.assertNotIn("INVENTORY_MANAGE", ALL)                                # supplier intake has its own right
        self.piece(self.invoice_id, self.g1, "1.000")
        self.assertIn(("WRITE", "SUPPLIER_INTAKE"), self.authorization.checked)
        self.assertNotIn(("WRITE", "INVENTORY_MANAGE"), self.authorization.checked)


class PermissionTests(SupplierCase):
    def case(self, permissions=None, granted=None):
        return build(permissions or ALL, granted=granted)

    def test_each_protected_write_is_denied_without_its_own_permission(self):
        for missing, action in (
                ("SUPPLIER_MANAGE", lambda s, p: s.create_supplier("جديد", karat_id=1)),
                ("SUPPLIER_INVOICE_EDIT", lambda s, p: p.create_draft("1", 1, "", "2026-10-08",
                                                                       [{"weight": "1", "making_per_gram": "1"}], "", key(1))),
                ("SUPPLIER_INVOICE_POST", lambda s, p: p.post_invoice("1", key(2))),
                ("SUPPLIER_INTAKE", lambda s, p: p.add_piece("1", "1", 1, "1", key(3)))):
            with self.subTest(missing=missing):
                db, _, authorization, suppliers, purchases = self.case(ALL - {missing})
                with self.assertRaises(PermissionError):
                    action(suppliers, purchases)
                self.assertEqual((db.transactions, db.suppliers, db.invoices, db.gold, db.items), (0, {}, {}, [], {}))

    def test_the_authorized_path_works_on_the_strict_write_path(self):
        self.posted()
        self.assertEqual({c for c in self.authorization.checked if c[0] == "WRITE"},
                         {("WRITE", "SUPPLIER_MANAGE"), ("WRITE", "SUPPLIER_INVOICE_EDIT"),
                          ("WRITE", "SUPPLIER_INVOICE_POST")})

    def test_reads_need_supplier_view_on_the_read_path(self):
        db, _, authorization, suppliers, purchases = self.case(ALL - {"SUPPLIER_VIEW"})
        for read in (lambda: suppliers.list_suppliers(), lambda: suppliers.supplier_detail("1"),
                     lambda: purchases.list_invoices(), lambda: purchases.invoice_detail("1"),
                     lambda: purchases.piece_origin("1")):
            with self.assertRaises(PermissionError):
                read()
        self.assertEqual(db.transactions, 0)
        self.suppliers.list_suppliers()
        self.assertIn(("READ", "SUPPLIER_VIEW"), self.authorization.checked)

    def test_a_grant_revoked_in_the_database_refuses_even_with_a_stale_session(self):
        db, session, authorization, suppliers, purchases = self.case()
        authorization.permissions = {"APP_LOGIN", "SUPPLIER_VIEW"}              # revoked behind the cached session
        self.assertIn("SUPPLIER_INVOICE_POST", session.current_actor().permissions)     # the cache still lists it
        with self.assertRaises(PermissionError):
            suppliers.create_supplier("جديد", karat_id=1)
        with self.assertRaises(PermissionError):
            purchases.post_invoice("1", key(5))
        self.assertEqual((db.suppliers, db.gold), ({}, []))

    def test_no_session_actor_means_no_access(self):
        from app.session import AppSession
        from services.supplier_service import SupplierService
        service = SupplierService(AppSession(), transaction_factory=self.db.transaction)
        opened = self.db.transactions
        with self.assertRaises(Exception):
            service.create_supplier("x", karat_id=1)
        self.assertEqual(self.db.transactions, opened)                          # no transaction was opened

    def test_supplier_rights_are_explicit_grants_in_the_migration_for_owner_and_manager_only(self):
        sql = (ROOT / "database" / "schema" / "034_suppliers_purchasing.sql").read_text(encoding="utf-8")
        for code in ALL - {"APP_LOGIN"}:
            self.assertIn(f"'{code}'", sql)
        self.assertIn("r.code IN ('OWNER','MANAGER')", sql)
        self.assertNotIn("CASHIER", sql)
        self.assertNotIn("SALESPERSON", sql)

    def test_the_view_model_slots_carry_no_identity(self):
        names = {"user_id", "employee_id", "actor", "actor_id", "created_by", "performed_by", "posted_by"}
        for path in ("supplier_viewmodel.py", "supplier_purchase_viewmodel.py"):
            tree = ast.parse((ROOT / "app" / "viewmodels" / path).read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.FunctionDef):
                    self.assertFalse({a.arg for a in node.args.args} & names, (path, node.name))


class LayeringTests(unittest.TestCase):
    def test_no_sql_in_view_models_or_qml_and_no_float_money_in_services(self):
        for path in (ROOT / "app" / "viewmodels").glob("supplier*_viewmodel.py"):
            text = path.read_text(encoding="utf-8")
            for word in ("SELECT ", "INSERT INTO", "UPDATE ", "cursor("):
                self.assertNotIn(word, text, path.name)
        qml = (ROOT / "qml" / "SuppliersPage.qml").read_text(encoding="utf-8")
        for word in ("SELECT ", "INSERT INTO", "parseFloat", "Number(", "toFixed"):
            self.assertNotIn(word, qml)                                         # no arithmetic and no SQL in QML
        for path in ("supplier_rules.py", "supplier_service.py", "supplier_purchase_service.py"):
            tree = ast.parse((ROOT / "services" / path).read_text(encoding="utf-8"))
            self.assertFalse([n for n in ast.walk(tree) if isinstance(n, ast.Name) and n.id == "float"], path)

    def test_the_weight_unit_is_never_written_jm(self):
        import re
        standalone = re.compile(r"(?<![ء-ي])جم(?![ء-ي])")
        for path in list((ROOT / "app" / "viewmodels").glob("supplier*_viewmodel.py")) + [
                ROOT / "qml" / "SuppliersPage.qml", ROOT / "services" / "supplier_rules.py",
                ROOT / "services" / "supplier_service.py", ROOT / "services" / "supplier_purchase_service.py"]:
            self.assertIsNone(standalone.search(path.read_text(encoding="utf-8")), path.name)


if __name__ == "__main__":
    unittest.main()
