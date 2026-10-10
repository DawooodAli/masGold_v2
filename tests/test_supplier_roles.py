"""Market-party roles on the Supplier Master (044): is_supplier / is_intermediary, at least one role. Real services over the in-memory
doubles of tests/supplier_fakes.py (the 044 CHECK constraints are mirrored as asserts); the SQL was exercised on masgold_rehearsal.
Settlement, holdings, sales, bar allocation and reports are NOT part of this slice."""
from decimal import Decimal as D
import unittest

from app.viewmodels.supplier_viewmodel import SupplierViewModel
from services import supplier_rules as rules
from services.supplier_rules import SupplierRuleError
from tests.supplier_fakes import build_payments

TODAY = "2026-10-10"
K21, K18 = 1, 2
ROWS = [{"weight": "50", "making_per_gram": "100"}]


def key(n):
    return "44444444-4444-4444-8444-%012d" % n


class RoleCase(unittest.TestCase):
    def setUp(self):
        self.db, self.session, self.authorization, self.suppliers, self.purchases, self.payments = build_payments()
        self.n = 0

    def refused(self, call, code):
        with self.assertRaises(SupplierRuleError) as refusal:
            call()
        self.assertEqual(refusal.exception.code, code)
        return str(refusal.exception)

    def next_key(self):
        self.n += 1
        return key(self.n)

    def supplier(self, name="مورد", karat=K21, **roles):
        return self.suppliers.create_supplier(name, None, None, karat, **roles)

    def intermediary(self, name="وسيط"):
        return self.suppliers.create_supplier(name, None, None, None, is_supplier=False, is_intermediary=True)

    def invoice(self, supplier):
        draft = self.purchases.create_draft(supplier, None, "INV", TODAY, ROWS, "", self.next_key())
        return draft["id"]

    def detail(self, supplier):
        return self.suppliers.supplier_detail(supplier)

    def row(self, supplier):
        return next(r for r in self.suppliers.list_suppliers() if r["id"] == str(supplier))


class MasterRoleTests(RoleCase):
    def test_a_party_created_the_old_way_is_supplier_only_and_its_fields_are_unchanged(self):
        sid = self.suppliers.create_supplier("مورد قديم", "010", "ملاحظة", K21)
        row = self.row(sid)
        self.assertEqual((row["is_supplier"], row["is_intermediary"], row["role_text"], row["karat_id"], row["phone"], row["notes"], row["code"]),
                         (True, False, "مورد", K21, "010", "ملاحظة", f"S{int(sid):06d}"))
        self.assertEqual(self.detail(sid)["supplier"]["role_text"], "مورد")

    def test_an_intermediary_only_party_needs_no_karat_and_has_no_supplier_account_or_warning(self):
        sid = self.intermediary()
        stored = self.db.suppliers[int(sid)]
        self.assertEqual((stored["is_supplier"], stored["is_intermediary"], stored["karat_id"]), (False, True, None))
        row = self.row(sid)
        self.assertEqual((row["gold_status"], row["gold_balance"], row["role_text"]), ("NOT_APPLICABLE", None, "وسيط سوق"))
        detail = self.detail(sid)
        self.assertEqual(detail["balances"]["gold_status"], "NOT_APPLICABLE")                        # never the "unresolved karat" state
        self.assertEqual((detail["gold_ledger"], detail["making_ledger"]), ([], []))
        self.refused(lambda: self.suppliers.create_supplier("وسيط بعيار", None, None, K21, is_supplier=False, is_intermediary=True), "KARAT_NOT_ALLOWED")

    def test_both_roles_need_a_supported_karat_and_neither_role_is_refused(self):
        self.refused(lambda: self.suppliers.create_supplier("كلاهما", None, None, None, is_supplier=True, is_intermediary=True), "KARAT_REQUIRED")
        sid = self.suppliers.create_supplier("كلاهما", None, None, K18, is_supplier=True, is_intermediary=True)
        row = self.row(sid)
        self.assertEqual((row["is_supplier"], row["is_intermediary"], row["role_text"], row["karat_id"]), (True, True, "مورد • وسيط سوق", K18))
        self.refused(lambda: self.suppliers.create_supplier("بلا دور", None, None, K21, is_supplier=False, is_intermediary=False), "ROLE_REQUIRED")
        self.refused(lambda: self.suppliers.update_supplier(sid, "كلاهما", is_supplier=False, is_intermediary=False), "ROLE_REQUIRED")
        self.assertEqual(len(self.db.suppliers), 1)

    def test_the_role_filters_include_a_party_with_both_roles(self):
        only_supplier, only_intermediary = self.supplier("أ"), self.intermediary("ب")
        both = self.suppliers.create_supplier("ج", None, None, K21, is_supplier=True, is_intermediary=True)
        ids = lambda role: sorted(r["id"] for r in self.suppliers.list_suppliers(role=role))
        self.assertEqual(ids(None), sorted([only_supplier, only_intermediary, both]))
        self.assertEqual(ids("SUPPLIER"), sorted([only_supplier, both]))
        self.assertEqual(ids("INTERMEDIARY"), sorted([only_intermediary, both]))
        self.refused(lambda: self.suppliers.list_suppliers(role="BOGUS"), "ROLE_FILTER_INVALID")

    def test_supplier_operations_are_refused_for_an_intermediary_only_party_by_the_services(self):
        sid = self.intermediary()
        before = (dict(self.db.invoices), list(self.db.making), list(self.db.gold), dict(self.db.payments))
        self.refused(lambda: self.purchases.create_draft(sid, K21, "INV", TODAY, ROWS, "", self.next_key()), "SUPPLIER_ROLE_REQUIRED")
        self.refused(lambda: self.payments.pay(sid, "CASH", None, "100", TODAY, "", "", self.next_key()), "SUPPLIER_ROLE_REQUIRED")
        self.assertEqual((dict(self.db.invoices), list(self.db.making), list(self.db.gold), dict(self.db.payments)), before)

    def test_adding_the_supplier_role_to_an_intermediary_needs_a_karat_and_adding_the_intermediary_role_never_touches_it(self):
        sid = self.intermediary()
        self.refused(lambda: self.suppliers.update_supplier(sid, "وسيط", is_supplier=True), "KARAT_REQUIRED")
        self.suppliers.update_supplier(sid, "وسيط", karat_id=K18, is_supplier=True)
        self.assertEqual((self.db.suppliers[int(sid)]["karat_id"], self.db.suppliers[int(sid)]["is_supplier"]), (K18, True))
        self.invoice(sid)                                                                   # now a supplier with history: its karat is locked
        self.suppliers.update_supplier(sid, "وسيط", is_intermediary=True)                    # adding the other role: karat unchanged
        stored = self.db.suppliers[int(sid)]
        self.assertEqual((stored["karat_id"], stored["is_supplier"], stored["is_intermediary"]), (K18, True, True))
        self.refused(lambda: self.suppliers.update_supplier(sid, "وسيط", karat_id=K21), "KARAT_LOCKED")   # the existing karat-lock rule

    def test_an_unresolved_legacy_karat_is_retained_not_guessed(self):
        sid = self.supplier()
        self.db.suppliers[int(sid)]["karat_id"] = None                                      # a legacy supplier whose karat was never resolved
        self.suppliers.update_supplier(sid, "اسم جديد")
        self.suppliers.update_supplier(sid, "اسم جديد", is_intermediary=True)
        stored = self.db.suppliers[int(sid)]
        self.assertEqual((stored["name"], stored["karat_id"], stored["is_supplier"], stored["is_intermediary"]), ("اسم جديد", None, True, True))
        self.assertEqual(self.detail(sid)["balances"]["gold_status"], "UNRESOLVED")           # the warning still belongs to a supplier

    def test_the_supplier_role_cannot_be_removed_while_any_history_exists(self):
        with_draft = self.suppliers.create_supplier("مسودة", None, None, K21, is_supplier=True, is_intermediary=True)
        draft = self.invoice(with_draft)
        self.purchases.cancel_draft(draft)                                                  # a CANCELLED invoice is still history
        self.refused(lambda: self.suppliers.update_supplier(with_draft, "مسودة", is_supplier=False), "ROLE_HISTORY_EXISTS")
        with_payment = self.suppliers.create_supplier("دفعة", None, None, K21, is_supplier=True, is_intermediary=True)
        self.payments.pay(with_payment, "CASH", None, "10", TODAY, "", "", self.next_key())
        self.refused(lambda: self.suppliers.update_supplier(with_payment, "دفعة", is_supplier=False), "ROLE_HISTORY_EXISTS")
        self.assertTrue(self.db.suppliers[int(with_draft)]["is_supplier"])
        self.assertTrue(self.db.suppliers[int(with_payment)]["is_supplier"])
        self.assertEqual(self.db.suppliers[int(with_payment)]["karat_id"], K21)             # nothing was changed by the refusals

    def test_a_party_without_history_may_drop_the_supplier_role_and_loses_its_karat(self):
        sid = self.suppliers.create_supplier("جديد", None, None, K21, is_supplier=True, is_intermediary=True)
        self.suppliers.update_supplier(sid, "جديد", is_supplier=False)
        stored = self.db.suppliers[int(sid)]
        self.assertEqual((stored["is_supplier"], stored["is_intermediary"], stored["karat_id"]), (False, True, None))
        self.refused(lambda: self.suppliers.update_supplier(sid, "جديد", karat_id=K21), "KARAT_NOT_ALLOWED")

    def test_an_inactive_supplier_with_both_roles_stays_payable(self):
        sid = self.suppliers.create_supplier("متوقف", None, None, K21, is_supplier=True, is_intermediary=True)
        self.suppliers.set_active(sid, False)
        paid = self.payments.pay(sid, "CASH", None, "25", TODAY, "", "", self.next_key())
        self.assertEqual(paid["amount"], "25.00")

    def test_the_roles_are_audited_and_creation_permissions_are_unchanged(self):
        sid = self.suppliers.create_supplier("مدقق", None, None, K21, is_supplier=True, is_intermediary=True)
        event = next(r for r in self.db.audit_rows if r["event_type"] == "SUPPLIER_CREATED" and r["entity_id"] == int(sid))
        self.assertEqual((event["metadata"]["is_supplier"], event["metadata"]["is_intermediary"]), (True, True))


class ViewModelRoleTests(RoleCase):
    def setUp(self):
        super().setUp()
        self.vm = SupplierViewModel(self.session, self.suppliers, payments=self.payments)

    def load(self, sid):
        self.vm._suppliers = self.suppliers.list_suppliers()
        self.vm._detail = self.suppliers.supplier_detail(sid)

    def test_a_supplier_shows_its_role_and_account_and_an_intermediary_only_party_shows_no_balances(self):
        supplier, intermediary = self.supplier("مورد"), self.intermediary()
        self.load(intermediary)
        rows = {r["id"]: r for r in self.vm.suppliers}
        self.assertEqual((rows[supplier]["roleText"], rows[supplier]["isSupplier"]), ("مورد", True))
        self.assertEqual((rows[intermediary]["roleText"], rows[intermediary]["isSupplier"], rows[intermediary]["goldText"],
                          rows[intermediary]["makingText"]), ("وسيط سوق", False, "", ""))
        detail = self.vm.detail
        self.assertEqual((detail["roleText"], detail["isSupplier"], detail["isIntermediary"], detail["hasAccount"], detail["goldNote"]),
                         ("وسيط سوق", False, True, False, ""))
        self.load(supplier)
        self.assertEqual((self.vm.detail["hasAccount"], self.vm.detail["isSupplier"]), (True, True))

    def test_the_role_filter_reaches_the_service_only_when_chosen(self):
        calls = []

        class Spy:
            def list_suppliers(self, search, active_only, *, _expected_actor=None, **extra):
                calls.append(extra)
                return []

        vm = SupplierViewModel(self.session, Spy())
        actor = self.session.current_actor()
        vm._start = lambda operation, call: calls.append(("start", call(_expected_actor=actor)))
        vm.refresh()
        vm.setRoleFilter("INTERMEDIARY")
        vm.setRoleFilter("nonsense")
        self.assertEqual([c for c in calls if isinstance(c, dict)], [{}, {"role": "INTERMEDIARY"}, {}])


class RulesTests(unittest.TestCase):
    def test_role_text_and_validation(self):
        self.assertEqual((rules.role_text(True, False), rules.role_text(False, True), rules.role_text(True, True)), ("مورد", "وسيط سوق", "مورد • وسيط سوق"))
        self.assertEqual(rules.validate_roles(1, 0), (True, False))
        with self.assertRaises(SupplierRuleError):
            rules.validate_roles(False, False)
        self.assertTrue(rules.has_supplier_role({}))                                         # a row from before 044 is a supplier


if __name__ == "__main__":
    unittest.main()
