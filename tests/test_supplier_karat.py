"""Each supplier is dedicated to ONE karat (18 / 21 / 22 / 24). Invoices inherit it; the supplier gold account is in PHYSICAL grams
of that karat (no Eq21); making stays a separate EGP account. The karat is locked once any invoice exists; a legacy supplier
without one is resolved only when its whole history agrees. Plus static checks of migration 035 (the real-MySQL rehearsal is
a separate run)."""
from decimal import Decimal as D
from pathlib import Path
import re
import unittest

from app.viewmodels.supplier_viewmodel import (SupplierViewModel, gold_position_text, karat_note, position_parts)
from services import supplier_rules as rules
from services.supplier_rules import SupplierRuleError
from tests import test_suppliers as base

ROOT = Path(__file__).resolve().parents[1]
K21, K18, K24 = 1, 2, 3                                   # fake karat master ids (21 / 18 / 24)
ROWS = [{"weight": "7", "making_per_gram": "100"}]


class SupplierKaratCase(base.SupplierCase):
    def supplier(self, name, karat_id):
        return self.suppliers.create_supplier(name, karat_id=karat_id)

    def refused(self, call, code):
        with self.assertRaises(SupplierRuleError) as refusal:
            call()
        self.assertEqual(refusal.exception.code, code)
        return str(refusal.exception)

    def balance(self, supplier_id):
        return self.suppliers.supplier_detail(supplier_id)["balances"]


class MasterTests(SupplierKaratCase):
    def test_a_new_supplier_requires_a_supported_active_karat(self):
        for missing in (None, 0, "0", "", "abc"):
            with self.subTest(karat=missing):
                self.refused(lambda m=missing: self.suppliers.create_supplier("بلا عيار", karat_id=m), "KARAT_REQUIRED")
        self.refused(lambda: self.suppliers.create_supplier("بلا عيار", karat_id=99), "KARAT_NOT_SUPPORTED")   # not in the master
        self.db.karats[K24]["is_active"] = 0
        self.refused(lambda: self.suppliers.create_supplier("بلا عيار", karat_id=K24), "KARAT_NOT_SUPPORTED")   # inactive
        self.db.karats[9] = {"id": 9, "karat_value": 23, "name": "23K", "is_active": 1}
        self.refused(lambda: self.suppliers.create_supplier("بلا عيار", karat_id=9), "KARAT_NOT_SUPPORTED")     # 23 is not offered
        self.assertEqual(len(self.db.suppliers), 1)                                       # only the fixture supplier exists
        self.db.karats[10] = {"id": 10, "karat_value": 22, "name": "22K", "is_active": 1}
        for karat in (K21, K18, 10):                                                      # 10 is the 22K master row
            sid = self.suppliers.create_supplier(f"مورد {karat}", karat_id=karat)
            self.assertEqual(self.db.suppliers[int(sid)]["karat_id"], karat)

    def test_the_supplier_detail_and_list_carry_the_karat(self):
        sid = self.supplier("مورد 18", K18)
        detail = self.suppliers.supplier_detail(sid)["supplier"]
        self.assertEqual((detail["karat_id"], detail["karat_value"], detail["karat_locked"]), (K18, 18, False))
        row = next(r for r in self.suppliers.list_suppliers() if r["id"] == sid)
        self.assertEqual((row["karat_value"], row["gold_status"], row["gold_balance"]), (18, "RESOLVED", D("0.000")))

    def test_the_karat_can_change_while_the_supplier_has_no_invoice_or_movement(self):
        sid = self.supplier("مورد حر", K21)
        self.suppliers.update_supplier(sid, "مورد حر", karat_id=K24)
        self.assertEqual(self.db.suppliers[int(sid)]["karat_id"], K24)

    def test_once_any_invoice_exists_even_a_draft_or_cancelled_one_the_karat_is_locked(self):
        for state in ("draft", "cancelled", "posted"):
            with self.subTest(state=state):
                sid = self.supplier(f"مورد {state}", K21)
                draft = self.draft(supplier_id=sid, karat_id=K21, rows=ROWS, reference=state)
                if state == "cancelled":
                    self.purchases.cancel_draft(draft["id"])
                if state == "posted":
                    self.purchases.post_invoice(draft["id"], self.key())
                self.refused(lambda s=sid: self.suppliers.update_supplier(s, "x", karat_id=K18), "KARAT_LOCKED")
                self.assertEqual(self.db.suppliers[int(sid)]["karat_id"], K21)
                self.suppliers.update_supplier(sid, f"مورد {state} معدل", karat_id=K21)       # the same karat is not a change
                self.assertEqual(self.suppliers.supplier_detail(sid)["supplier"]["karat_locked"], True)


class InvoiceTests(SupplierKaratCase):
    def test_the_invoice_inherits_the_suppliers_karat_with_or_without_a_supplied_one(self):
        sid = self.supplier("مورد 18", K18)
        inherited = self.draft(supplier_id=sid, karat_id=None, rows=ROWS, reference="A")
        explicit = self.draft(supplier_id=sid, karat_id=K18, rows=ROWS, reference="B")
        self.assertEqual((inherited["karat_id"], explicit["karat_id"]), (K18, K18))

    def test_a_supplied_karat_that_differs_is_refused_at_every_step(self):
        sid = self.supplier("مورد 18", K18)
        message = self.refused(lambda: self.draft(supplier_id=sid, karat_id=K21, rows=ROWS), "KARAT_MISMATCH")
        self.assertIn("عيار 18", message)
        draft = self.draft(supplier_id=sid, karat_id=K18, rows=ROWS, reference="OK")
        self.refused(lambda: self.purchases.update_draft(draft["id"], sid, K21, "x", "2026-10-09", ROWS, ""), "KARAT_MISMATCH")
        self.assertEqual(self.db.invoices[int(draft["id"])]["karat_id"], K18)               # nothing was changed
        self.assertEqual(len(self.db.invoices), 1)

    def test_a_supplier_without_a_karat_cannot_create_or_post_an_invoice(self):
        legacy = self.supplier("قديم", K21)
        self.db.suppliers[int(legacy)]["karat_id"] = None                                   # a legacy, unresolved supplier
        self.refused(lambda: self.draft(supplier_id=legacy, karat_id=K21, rows=ROWS), "SUPPLIER_KARAT_MISSING")
        self.assertEqual(len(self.db.invoices), 0)

    def test_an_existing_incompatible_draft_is_never_silently_converted_nor_posted(self):
        sid = self.supplier("مورد", K21)
        draft = self.draft(supplier_id=sid, karat_id=K21, rows=ROWS, reference="OLD")
        self.db.suppliers[int(sid)]["karat_id"] = K18                    # the supplier is later dedicated to 18K (data repair)
        self.refused(lambda: self.purchases.post_invoice(draft["id"], self.key()), "KARAT_MISMATCH")
        self.refused(lambda: self.purchases.update_draft(draft["id"], sid, K21, "x", "2026-10-09", ROWS, ""), "KARAT_MISMATCH")
        self.assertEqual((self.db.invoices[int(draft["id"])]["karat_id"], self.db.invoices[int(draft["id"])]["status"]),
                         (K21, "DRAFT"))                                  # untouched: nothing converted, nothing posted
        self.assertEqual(self.db.gold, [])
        self.db.suppliers[int(sid)]["karat_id"] = None
        self.refused(lambda: self.purchases.post_invoice(draft["id"], self.key()), "SUPPLIER_KARAT_MISSING")

    def test_draft_editing_and_cancellation_still_work(self):
        sid = self.supplier("مورد", K24)
        draft = self.draft(supplier_id=sid, karat_id=K24, rows=ROWS, reference="E")
        edited = self.purchases.update_draft(draft["id"], sid, K24, "E2", "2026-10-09",
                                             [{"weight": "9", "making_per_gram": "50"}], "")
        self.assertEqual((edited["id"], edited["karat_id"], edited["supplier_weight"]), (draft["id"], K24, D("9.000")))
        self.purchases.cancel_draft(draft["id"])
        self.assertEqual(self.db.invoices[int(draft["id"])]["status"], "CANCELLED")


class AccountTests(SupplierKaratCase):
    def test_seven_grams_from_an_18k_supplier_adds_seven_grams_of_18k(self):
        sid = self.supplier("مورد 18", K18)
        posted, _ = self.posted(supplier_id=sid, karat_id=K18, rows=ROWS)
        balance = self.balance(sid)
        self.assertEqual((balance["gold_balance"], balance["gold_karat_value"], balance["gold_direction"]),
                         (D("7.000"), 18, rules.SHOP_OWES))                                # 7 g of 18K, NOT 6 g Eq21
        self.assertEqual(gold_position_text(balance["gold_direction"], balance["gold_balance"], 18), "علينا للمورد 7.000 جرام عيار 18")

    def test_seven_grams_from_a_24k_supplier_adds_seven_grams_of_24k(self):
        sid = self.supplier("مورد 24", K24)
        self.posted(supplier_id=sid, karat_id=K24, rows=ROWS)
        balance = self.balance(sid)
        self.assertEqual((balance["gold_balance"], balance["gold_karat_value"]), (D("7.000"), 24))   # not 8.000 Eq21

    def test_the_stored_eq21_is_kept_as_an_internal_field_and_drives_nothing(self):
        sid = self.supplier("مورد 18", K18)
        self.posted(supplier_id=sid, karat_id=K18, rows=ROWS)
        (movement,) = self.db.gold
        self.assertEqual((movement["weight_delta_grams"], movement["equivalent_21_delta_grams"]), (D("7.000"), D("6.000000")))
        movement["equivalent_21_delta_grams"] = D("999.000000")                              # even a different stored value ...
        self.assertEqual(self.balance(sid)["gold_balance"], D("7.000"))                      # ... never moves the account

    def test_payable_receivable_and_settled_wording_and_running_balances_are_physical_grams(self):
        sid = self.supplier("مورد 18", K18)
        self.posted(supplier_id=sid, karat_id=K18, rows=ROWS)
        self.assertEqual(gold_position_text(rules.SUPPLIER_OWES, D("-7"), 18), "لنا عند المورد 7.000 جرام عيار 18")
        self.assertEqual(gold_position_text(rules.SETTLED, 0, 18), "الحساب متوازن")
        self.db.gold.append({**self.db.gold[0], "id": 50, "weight_delta_grams": D("-10.000"), "equivalent_21_delta_grams": D("-8.571429"),
                             "source_id": 50, "occurred_at": self.db.now.replace(hour=11)})
        detail = self.suppliers.supplier_detail(sid)
        self.assertEqual((detail["balances"]["gold_direction"], detail["balances"]["gold_balance"]), (rules.SUPPLIER_OWES, D("-3.000")))
        self.assertEqual([r["weight_balance_after"] for r in detail["gold_ledger"]], [D("-3.000"), D("7.000")])   # newest first
        vm = SupplierViewModel(self.session, self.suppliers)
        vm._selected, vm._detail = sid, detail
        self.assertEqual([r["balance"] for r in vm.detail["goldLedger"]], ["لنا عند المورد 3.000 جرام عيار 18", "علينا للمورد 7.000 جرام عيار 18"])
        self.assertEqual(vm.detail["goldText"], "لنا عند المورد 3.000 جرام عيار 18")
        self.assertEqual(vm.detail["goldParts"], {"lead": "لنا عند المورد", "number": "3.000", "tail": "جرام عيار 18"})
        self.assertEqual(vm.detail["goldBreakdown"], [])                                      # no Eq21 headline, no redundant lines

    def test_making_stays_a_separate_egp_account(self):
        sid = self.supplier("مورد 18", K18)
        self.posted(supplier_id=sid, karat_id=K18, rows=[{"weight": "7", "making_per_gram": "100"}])
        balance = self.balance(sid)
        self.assertEqual((balance["making"], balance["making_direction"], balance["gold_balance"]), (D("700.00"), rules.SHOP_OWES, D("7.000")))
        self.assertEqual(position_parts(rules.SHOP_OWES, D("700"), "making"), {"lead": "علينا للمورد", "number": "700.00 ج", "tail": "مصنعيات"})

    def test_the_supplier_list_shows_grams_of_the_suppliers_karat(self):
        sid = self.supplier("مورد 18", K18)
        self.posted(supplier_id=sid, karat_id=K18, rows=ROWS)
        vm = SupplierViewModel(self.session, self.suppliers)
        vm._suppliers = self.suppliers.list_suppliers()
        row = next(r for r in vm.suppliers if r["id"] == sid)
        self.assertEqual((row["goldText"], row["karatText"]), ("علينا للمورد 7.000 جرام عيار 18", "عيار 18"))


class Karat22SupplierTests(SupplierKaratCase):
    """A 22K supplier: the invoice inherits 22, the gold account is PHYSICAL grams of 22K (no Eq21), intake creates 22K pieces."""

    def setUp(self):
        super().setUp()
        self.db.karats[10] = {"id": 10, "karat_value": 22, "name": "22K", "is_active": 1}
        self.k22 = 10

    def test_a_22k_supplier_invoice_inherits_22_and_a_different_karat_is_refused(self):
        sid = self.supplier("مورد 22", self.k22)
        inherited = self.draft(supplier_id=sid, karat_id=None, rows=ROWS, reference="A")
        self.assertEqual(inherited["karat_id"], self.k22)
        message = self.refused(lambda: self.draft(supplier_id=sid, karat_id=K21, rows=ROWS), "KARAT_MISMATCH")
        self.assertIn("عيار 22", message)

    def test_seven_grams_from_a_22k_supplier_adds_seven_physical_grams_of_22k(self):
        sid = self.supplier("مورد 22", self.k22)
        self.posted(supplier_id=sid, karat_id=self.k22, rows=ROWS)
        balance = self.balance(sid)
        self.assertEqual((balance["gold_balance"], balance["gold_karat_value"], balance["gold_direction"]),
                         (D("7.000"), 22, rules.SHOP_OWES))
        self.assertEqual(gold_position_text(balance["gold_direction"], balance["gold_balance"], 22), "علينا للمورد 7.000 جرام عيار 22")
        (movement,) = self.db.gold
        self.assertEqual((movement["karat_id"], movement["weight_delta_grams"]), (self.k22, D("7.000")))
        movement["equivalent_21_delta_grams"] = D("999.000000")                   # the stored Eq21 never drives the account
        self.assertEqual(self.balance(sid)["gold_balance"], D("7.000"))

    def test_making_for_a_22k_supplier_stays_a_separate_egp_account(self):
        sid = self.supplier("مورد 22", self.k22)
        self.posted(supplier_id=sid, karat_id=self.k22, rows=[{"weight": "7", "making_per_gram": "100"}])
        balance = self.balance(sid)
        self.assertEqual((balance["making"], balance["gold_balance"]), (D("700.00"), D("7.000")))

    def test_barcode_intake_from_a_22k_invoice_creates_22k_inventory_pieces(self):
        sid = self.supplier("مورد 22", self.k22)
        invoice, _ = self.posted(supplier_id=sid, karat_id=self.k22, rows=[{"weight": "40", "making_per_gram": "100"}])
        (group,) = self.group_ids(invoice["id"])
        self.piece(invoice["id"], group, "10.000")
        self.assertEqual([i["karat_id"] for i in self.db.items.values()], [self.k22])
        self.assertEqual(self.balance(sid)["gold_balance"], D("40.000"))          # intake never changes the supplier account


class UnresolvedHistoryTests(SupplierKaratCase):
    """A legacy supplier (karat NULL) whose history cannot be resolved: read-only per-karat history, nothing combined."""

    def mixed(self):
        sid = self.supplier("مختلط", K21)
        self.posted(supplier_id=sid, karat_id=K21, rows=[{"weight": "10", "making_per_gram": "10"}], reference="A")
        other = self.supplier("مؤقت", K18)
        self.posted(supplier_id=other, karat_id=K18, rows=[{"weight": "4", "making_per_gram": "10"}], reference="B")
        for movement in self.db.gold:                                       # make the history MIXED (21K and 18K) for one supplier
            movement["supplier_id"] = int(sid)
        for invoice in self.db.invoices.values():
            invoice["supplier_id"] = int(sid)
        self.db.suppliers[int(sid)]["karat_id"] = None
        return sid

    def test_mixed_history_stays_unresolved_with_per_karat_lines_and_an_explanation(self):
        sid = self.mixed()
        balances = self.balance(sid)
        self.assertEqual((balances["gold_status"], balances["gold_balance"], balances["gold_direction"]), ("UNRESOLVED", None, None))
        self.assertEqual([(k["karat_value"], k["weight_balance"]) for k in balances["gold_by_karat"]],
                         [(18, D("4.000")), (21, D("10.000"))])                             # never added into 14 g
        vm = SupplierViewModel(self.session, self.suppliers)
        vm._selected, vm._detail = sid, self.suppliers.supplier_detail(sid)
        detail = vm.detail
        self.assertEqual((detail["goldResolved"], detail["goldText"], detail["karatText"]), (False, "العيار غير محدد", "العيار غير محدد"))
        self.assertEqual([line["text"] for line in detail["goldBreakdown"]], ["علينا للمورد 4.000 جرام عيار 18", "علينا للمورد 10.000 جرام عيار 21"])
        self.assertIn("أعيرة مختلفة (18، 21)", detail["goldNote"])
        self.assertEqual({r["balance"] for r in detail["goldLedger"]}, {"علينا للمورد 4.000 جرام عيار 18", "علينا للمورد 10.000 جرام عيار 21"})

    def test_a_mixed_supplier_cannot_be_assigned_a_karat_and_takes_no_invoice(self):
        sid = self.mixed()
        message = self.refused(lambda: self.suppliers.update_supplier(sid, "مختلط", karat_id=K21), "KARAT_HISTORY_MIXED")
        self.assertIn("أعيرة مختلفة", message)
        self.assertIsNone(self.db.suppliers[int(sid)]["karat_id"])
        self.refused(lambda: self.draft(supplier_id=sid, karat_id=K21, rows=ROWS), "SUPPLIER_KARAT_MISSING")

    def test_a_legacy_supplier_with_a_single_karat_history_accepts_only_that_karat(self):
        sid = self.supplier("قديم", K21)
        self.posted(supplier_id=sid, karat_id=K21, rows=ROWS)
        self.db.suppliers[int(sid)]["karat_id"] = None
        self.refused(lambda: self.suppliers.update_supplier(sid, "قديم", karat_id=K18), "KARAT_HISTORY_MISMATCH")
        self.suppliers.update_supplier(sid, "قديم", karat_id=K21)
        self.assertEqual(self.db.suppliers[int(sid)]["karat_id"], K21)
        self.assertEqual(self.balance(sid)["gold_balance"], D("7.000"))

    def test_a_legacy_supplier_without_history_can_simply_be_given_a_karat(self):
        sid = self.supplier("جديد قديم", K21)
        self.db.suppliers[int(sid)]["karat_id"] = None
        self.suppliers.update_supplier(sid, "جديد قديم", karat_id=K24)
        self.assertEqual(self.db.suppliers[int(sid)]["karat_id"], K24)

    def test_the_unresolved_note_explains_each_case(self):
        none = {"karat_id": None, "history_karats": []}
        one = {"karat_id": None, "history_karats": [21]}
        mixed = {"karat_id": None, "history_karats": [18, 21]}
        self.assertIn("حدّده من تعديل المورد", karat_note(none))
        self.assertIn("(عيار 21)", karat_note(one))
        self.assertIn("أعيرة مختلفة (18، 21)", karat_note(mixed))
        self.assertEqual(karat_note({"karat_id": 1, "history_karats": [21]}), "")


class RuleTests(unittest.TestCase):
    H21, H18 = {"karat_id": 1, "karat_value": 21}, {"karat_id": 2, "karat_value": 18}

    def test_the_pure_change_rule(self):
        rules.check_supplier_karat_change(None, 2, [])                              # no history: free
        rules.check_supplier_karat_change(1, 1, [self.H21])                         # unchanged
        rules.check_supplier_karat_change(None, 1, [self.H21])                      # the history's own karat
        for current, wanted, history, code in ((1, 2, [self.H21], "KARAT_LOCKED"), (None, 2, [self.H21], "KARAT_HISTORY_MISMATCH"),
                                               (None, 1, [self.H21, self.H18], "KARAT_HISTORY_MIXED")):
            with self.subTest(code=code), self.assertRaises(SupplierRuleError) as refused:
                rules.check_supplier_karat_change(current, wanted, history)
            self.assertEqual(refused.exception.code, code)

    def test_the_supported_karats_are_18_21_22_24(self):
        self.assertEqual(rules.SUPPORTED_KARATS, (18, 21, 22, 24))


class MigrationTests(unittest.TestCase):
    """Static checks of migration 035 (the real MySQL rehearsal is a separate, documented run)."""
    SQL = (ROOT / "database" / "schema" / "035_supplier_karat.sql").read_text(encoding="utf-8")
    ROLLBACK = (ROOT / "database" / "schema" / "rollback" / "035_supplier_karat_rollback.sql").read_text(encoding="utf-8")

    def test_it_adds_a_nullable_indexed_foreign_keyed_column_and_never_defaults_it(self):
        self.assertIn("ADD COLUMN karat_id TINYINT UNSIGNED DEFAULT NULL", self.SQL)
        self.assertIn("ADD KEY idx_suppliers_karat (karat_id)", self.SQL)
        self.assertIn("FOREIGN KEY (karat_id) REFERENCES karats(id)", self.SQL)
        self.assertNotRegex(self.SQL, r"karat_id[^;]*DEFAULT\s+\d")                 # no default karat

    def test_the_backfill_is_the_only_data_statement_and_needs_exactly_one_karat_in_the_whole_history(self):
        code = re.sub(r"(?m)^--.*$", "", self.SQL)
        statements = [part.strip() for part in code.split(";") if part.strip()]
        updates = [part for part in statements if re.match(r"(?is)UPDATE\b", part)]         # statements that START with UPDATE
        self.assertEqual(len(updates), 1)
        self.assertEqual(len([part for part in statements if re.match(r"(?is)(DELETE|TRUNCATE)\b", part)]), 0)
        update = updates[0]
        self.assertRegex(update, r"(?is)UPDATE suppliers s")
        self.assertIn("supplier_purchase_invoices", update)                          # drafts and cancelled included: no status filter
        self.assertIn("supplier_gold_movements", update)
        self.assertNotRegex(update, r"(?i)status")
        self.assertIn("HAVING COUNT(DISTINCT h.karat_id) = 1", update)
        self.assertIn("k.karat_value IN (18, 21, 24)", update)
        self.assertIn("WHERE s.karat_id IS NULL", update)                            # re-runnable, never overwrites a choice
        for table in ("supplier_purchase_invoices", "supplier_invoice_groups", "supplier_invoice_pieces",
                      "supplier_gold_movements", "supplier_making_movements", "inventory_items"):
            self.assertNotRegex(self.SQL, rf"(?is)(UPDATE|DELETE FROM|ALTER TABLE)\s+{table}\b")   # history untouched

    def test_the_eq21_ledger_column_is_left_alone(self):
        self.assertNotIn("equivalent_21_delta_grams", re.sub(r"--[^\n]*", "", self.SQL))

    def test_the_rollback_refuses_once_a_karat_is_assigned(self):
        self.assertIn("EXISTS(SELECT 1 FROM suppliers WHERE karat_id IS NOT NULL)", self.ROLLBACK)
        self.assertIn("ROLLBACK_035_REFUSED", self.ROLLBACK)
        self.assertIn("DROP COLUMN karat_id", self.ROLLBACK)

    def test_the_manifest_lists_035_and_still_pins_the_frozen_034(self):
        manifest = (ROOT / "database" / "schema" / "SHA256SUMS").read_text(encoding="utf-8")
        self.assertIn("035_supplier_karat.sql", manifest)
        self.assertIn("3f35b7d149cfd51f40fe82377532e43fc93e4d6af7e9dd6551c2efcf51db0de2  034_suppliers_purchasing.sql", manifest)


if __name__ == "__main__":
    unittest.main()
