"""Full reversal of a POSTED supplier purchase invoice (052): the real SupplierPurchaseService, the real CorrectionService and the real SupplierService over the
in-memory transactional database of tests/supplier_fakes.py (a failed transaction restores every table, so atomicity is testable). The SQL, the constraint
replacement and the locks are the subject of the opt-in rehearsal tests (tests/test_supplier_invoice_reversal_mysql.py); nothing here proves them."""
import copy
from decimal import Decimal as D
from unittest.mock import patch
import unittest

from services.correction_service import CorrectionError, CorrectionService
from services.supplier_rules import SupplierRuleError
from tests import test_supplier_payments as base
from tests.supplier_fakes import MANAGER_RIGHTS, OWNER_RIGHTS, PaymentRepo, PurchaseRepo

SOURCE = "SUPPLIER_PURCHASE_INVOICE"
REASON = "وزن مدخل بالخطأ"


class InvoiceCorrections(base.Corrections):
    """The correction repository double of the payment tests, accepting the 052 source (invoice-less, full reversal only)."""

    def corrections_for_source(self, source_type, source_id, current=False):             # the 052 decision is a CURRENT read (a plain double has one view)
        return super().corrections_for_source(source_type, source_id)

    def insert_correction(self, **kw):
        self._fail("correction")
        assert kw["reason"].strip()                                                    # chk_correction_reason
        assert kw["source_type"] == SOURCE and kw["invoice_id"] is None                # chk_correction_source (052)
        assert kw["correction_type"] == "FULL_REVERSAL"
        if any((c["source_type"], c["source_id"], c["correction_type"]) == (SOURCE, str(kw["source_id"]), "FULL_REVERSAL") for c in self.db.corrections):
            raise RuntimeError("Duplicate entry for key uq_correction_one_reversal")
        correction_id = str(self.db.next("correction"))
        self.db.corrections.append({"correction_id": correction_id, "correction_type": kw["correction_type"], "source_type": kw["source_type"],
                                    "source_id": str(kw["source_id"]), "source_reference": kw["source_reference"], "reason": kw["reason"],
                                    "occurred_at": kw["occurred_at"], "invoice_id": kw["invoice_id"], "user_id": kw["performed_by_user_id"]})
        return correction_id

    def add_money(self, **kw):                                                         # a purchase invoice reversal never posts money
        raise AssertionError("a supplier invoice reversal posted money")


class ReversalRepo(PaymentRepo):
    """What CorrectionService reaches through SupplierPurchaseRepository / SupplierGoldSettlementRepository: the invoice read (the payment double
    overrides `_joined` for its own rows, so the invoice join is restated here), the supplier lock and the two ledger inserts with their failure hooks."""

    def get_invoice(self, invoice_id, lock=False):
        found = self.db.invoices.get(int(invoice_id))
        return PurchaseRepo._joined(self, found) if found else None


class InvoiceReversalCase(base.PaymentCase):
    """One 21K supplier with ONE posted invoice (50 g, 100 EGP/g): gold owed 50.000 g, making owed 5,000.00."""

    def setUp(self):
        super().setUp()
        for target in ("repositories.supplier_purchase_repository.SupplierPurchaseRepository",
                       "repositories.supplier_gold_settlement_repository.SupplierGoldSettlementRepository"):
            started = patch(target, ReversalRepo)
            started.start()
            self.addCleanup(started.stop)
        self.corrections = self.correction_service(self.session, self.authorization)
        self.payments._corrections = self.corrections
        self.purchases._corrections = self.corrections
        self.invoice = 1

    def correction_service(self, session, authorization):
        return CorrectionService(session, authorization=authorization, transaction_factory=self.db.transaction,
                                 invoice_repository=base.Invoices, correction_repository=InvoiceCorrections,
                                 customer_repository=base.Customers)

    # ------------------------------------------------------------ helpers
    def reverse(self, invoice=None, reason=REASON, service=None):
        return (service or self.purchases).reverse_invoice(self.invoice if invoice is None else invoice, reason)

    def purchases_for(self, permissions, user_id=8):
        session = base.session_for(permissions, user_id=user_id)
        authorization = base.FakeAuthorization(session)
        service = self.purchases.__class__(session, transaction_factory=self.db.transaction, repository=PaymentRepo,
                                           barcodes=self.purchases._barcodes, inventory=self.purchases._inventory,
                                           authorization=authorization, corrections=self.correction_service(session, authorization))
        return service, session, authorization

    def ledgers(self):
        """Everything a reversal may NOT touch."""
        d = self.db
        return copy.deepcopy((d.cash_moves, d.bank_moves, d.items, d.barcodes, d.movements, d.links, d.payments, d.legs, d.suppliers, d.invoices, d.groups))

    def second_supplier_invoice(self, supplier_name="مورد آخر"):
        other = self.suppliers.create_supplier(supplier_name, None, None, 2)
        draft = self.purchases.create_draft(other, 2, "INV-OTHER", base.TODAY, [{"weight": "10", "making_per_gram": "50"}], "", base.key(950))
        self.purchases.post_invoice(draft["id"], base.key(951))
        return other, draft["id"]


class ReversalPostingTests(InvoiceReversalCase):
    def test_the_exact_opposite_entries_are_posted_and_everything_original_stays_untouched(self):
        original = copy.deepcopy((self.db.gold, self.db.making, self.db.invoices, self.db.groups))
        before_other = self.ledgers()
        result = self.reverse()
        self.assertEqual((result["code"], result["source_reference"]), ("RV000001", "SP000001"))
        self.assertEqual((self.gold(), self.making()), (D("0.000"), D("0.00")))                  # the pre-posting balances
        self.assertEqual(self.db.gold[0], original[0][0])                                          # the original movements are unchanged
        self.assertEqual(self.db.making[0], original[1][0])
        self.assertEqual(self.db.invoices, original[2])                                            # the invoice stays POSTED, immutable
        self.assertEqual(self.db.groups, original[3])
        reversal_gold = [m for m in self.db.gold if m["source_type"] == "TRANSACTION_CORRECTION"]
        reversal_making = [m for m in self.db.making if m["source_type"] == "TRANSACTION_CORRECTION"]
        self.assertEqual([(m["weight_delta_grams"], m["equivalent_21_delta_grams"], m["movement_type_id"], m["karat_id"], m["source_id"])
                          for m in reversal_gold],
                         [(-self.db.gold[0]["weight_delta_grams"], -self.db.gold[0]["equivalent_21_delta_grams"], 2, self.db.gold[0]["karat_id"], 1)])
        self.assertEqual([(m["amount_delta"], m["movement_type_id"], m["source_id"]) for m in reversal_making], [(D("-5000.00"), 4, 1)])
        (correction,) = self.db.corrections
        self.assertEqual((correction["source_type"], correction["source_id"], correction["invoice_id"], correction["reason"]),
                         (SOURCE, "1", None, REASON))
        self.assertEqual(self.db.audit_rows[-1]["event_type"], "TRANSACTION_REVERSED")
        after = self.ledgers()
        self.assertEqual(after, before_other)                                                      # no inventory, cash, bank, payment or other-supplier effect

    def test_the_reversed_state_is_derived_and_visible_while_the_stored_status_stays_posted(self):
        self.reverse()
        detail = self.purchases.invoice_detail(self.invoice)
        self.assertEqual((detail["status"], detail["reversed"], detail["reversal_code"], detail["reversal_reason"]), ("POSTED", True, "RV000001", REASON))
        (row,) = [r for r in self.purchases.list_invoices(self.supplier) if r["id"] == "1"]
        self.assertEqual((row["status"], row["reversed"], row["reversal_code"]), ("POSTED", True, "RV000001"))

    def test_the_supplier_ledgers_label_the_rows_by_code_and_link_both_ways(self):
        self.reverse()
        detail = self.suppliers.supplier_detail(self.supplier)
        gold = {r["movement"]: r for r in detail["gold_ledger"]}
        self.assertEqual(set(gold), {"فاتورة شراء", "عكس فاتورة شراء"})
        self.assertEqual(gold["فاتورة شراء"]["invoice_reversed_by"], "RV000001")
        self.assertEqual(gold["عكس فاتورة شراء"]["reverses_invoice"], "SP000001")
        making = {r["movement_code"]: r for r in detail["making_ledger"]}
        self.assertEqual(making["PURCHASE_INVOICE"]["invoice_reversed_by"], "RV000001")
        self.assertEqual(making["PURCHASE_INVOICE_REVERSAL"]["reverses_invoice"], "SP000001")
        self.assertEqual(detail["balances"]["gold_balance"], D("0.000"))
        self.assertEqual(detail["balances"]["making"], D("0.00"))

    def test_a_payment_made_since_posting_is_not_touched_and_the_balance_may_cross_zero(self):
        self.pay("1000")                                                                           # making owed 4,000.00
        payments = copy.deepcopy((self.db.payments, self.db.cash_moves, self.db.legs))
        self.reverse()
        self.assertEqual(self.making(), D("-1000.00"))                                             # supplier credit: allowed, never floored
        self.assertEqual(copy.deepcopy((self.db.payments, self.db.cash_moves, self.db.legs)), payments)

    def test_an_invoice_without_making_posts_only_the_gold_reversal(self):
        draft = self.purchases.create_draft(self.supplier, 1, "NO-MAKING", base.TODAY, [{"weight": "10", "making_per_gram": "0"}], "", base.key(960))
        posted = self.purchases.post_invoice(draft["id"], base.key(961))
        making = len(self.db.making)
        self.reverse(posted["id"])
        self.assertEqual(len(self.db.making), making)                                              # no making movement existed, none is posted
        self.assertEqual(self.gold(), D("50.000"))

    def test_the_eq21_compatibility_value_is_the_exact_negation_of_the_stored_one(self):
        other, invoice = self.second_supplier_invoice()                                            # an 18K supplier
        stored = next(m for m in self.db.gold if m["supplier_id"] == int(other))
        self.reverse(invoice)
        undone = next(m for m in self.db.gold if m["supplier_id"] == int(other) and m["movement_type_id"] == 2)
        self.assertEqual((undone["weight_delta_grams"], undone["equivalent_21_delta_grams"], undone["karat_id"]),
                         (-stored["weight_delta_grams"], -stored["equivalent_21_delta_grams"], stored["karat_id"]))
        self.assertEqual(self.db.gold[0]["supplier_id"], int(self.supplier))                       # the first supplier is untouched
        self.assertEqual(self.gold_of(self.supplier), D("50.000"))

    def gold_of(self, supplier):
        return sum((m["weight_delta_grams"] for m in self.db.gold if m["supplier_id"] == int(supplier)), D("0.000"))


class ReversalGuardTests(InvoiceReversalCase):
    def test_a_second_reversal_is_refused_and_writes_nothing(self):
        self.reverse()
        self.refused("ALREADY_REVERSED", lambda: self.reverse(), CorrectionError)
        self.assertEqual(len(self.db.corrections), 1)

    def test_only_a_posted_invoice_can_be_reversed(self):
        draft = self.purchases.create_draft(self.supplier, 1, "DRAFT-1", base.TODAY, [{"weight": "5", "making_per_gram": "10"}], "", base.key(970))
        self.refused("INVOICE_NOT_POSTED", lambda: self.reverse(draft["id"]), CorrectionError)
        cancelled = self.purchases.create_draft(self.supplier, 1, "DRAFT-2", base.TODAY, [{"weight": "5", "making_per_gram": "10"}], "", base.key(971))
        self.purchases.cancel_draft(cancelled["id"])
        self.refused("INVOICE_NOT_POSTED", lambda: self.reverse(cancelled["id"]), CorrectionError)
        self.refused("SOURCE_NOT_FOUND", lambda: self.reverse(999), CorrectionError)

    def test_an_invoice_with_any_intaken_piece_is_refused_with_the_exact_reason_and_no_cascade(self):
        group = self.purchases._repository(self.db).groups(self.invoice)[0]
        self.purchases.add_piece(self.invoice, str(group["id"]), 1, "10.000", base.key(980))
        error = self.refused("INVOICE_HAS_INTAKE", lambda: self.reverse(), CorrectionError)
        self.assertIn("1 قطعة", str(error))
        self.assertEqual((self.gold(), self.making(), len(self.db.corrections)), (D("50.000"), D("5000.00"), 0))
        preview = self.purchases.reversal_preview(self.invoice)
        self.assertEqual((preview["can_reverse"], [b["code"] for b in preview["blockers"]]), (False, ["INVOICE_HAS_INTAKE"]))

    def test_intake_after_a_reversal_is_refused(self):
        self.reverse()
        group = self.purchases._repository(self.db).groups(self.invoice)[0]
        self.refused("INVOICE_REVERSED", lambda: self.purchases.add_piece(self.invoice, str(group["id"]), 1, "5.000", base.key(981)))

    def test_the_rights_are_checked_in_the_service_and_again_against_the_database_inside_the_transaction(self):
        before = self.state()
        manager, _, _ = self.purchases_for(MANAGER_RIGHTS, 31)
        with self.assertRaises(PermissionError):
            manager.reverse_invoice(self.invoice, REASON)                                           # no SUPPLIER_INVOICE_REVERSE
        no_correct, _, _ = self.purchases_for(OWNER_RIGHTS - {"TRANSACTION_CORRECT"}, 32)
        with self.assertRaises(PermissionError):
            no_correct.reverse_invoice(self.invoice, REASON)                                        # no TRANSACTION_CORRECT
        self.assertEqual(self.state(), before)
        service, session, authorization = self.purchases_for(OWNER_RIGHTS, 33)
        authorization.permissions = OWNER_RIGHTS - {"SUPPLIER_INVOICE_REVERSE"}                    # the session still shows it, the database says no
        with self.assertRaises(PermissionError):
            service.reverse_invoice(self.invoice, REASON)
        authorization.permissions = OWNER_RIGHTS - {"TRANSACTION_CORRECT"}
        with self.assertRaises(PermissionError):
            service.reverse_invoice(self.invoice, REASON)
        self.assertEqual(self.state(), before)
        direct = self.correction_service(*self.purchases_for(MANAGER_RIGHTS, 34)[1:])
        with self.assertRaises(PermissionError):
            direct.reverse(SOURCE, "1", REASON)                                                     # CorrectionService itself refuses a manager

    def test_a_reason_is_mandatory(self):
        for reason in ("", "   "):
            self.refused("REASON_REQUIRED", lambda r=reason: self.reverse(reason=r), CorrectionError)
        self.assertEqual(self.db.corrections, [])

    def test_a_failure_at_any_stage_changes_nothing(self):
        for step in ("correction", "gold", "making", "audit"):
            with self.subTest(step=step):
                self.db.fail_at = step
                before = self.state()
                with self.assertRaises(RuntimeError):
                    self.reverse()
                self.db.fail_at = None
                self.assertEqual(self.state(), before)
                self.assertEqual((self.db.corrections, self.gold(), self.making()), ([], D("50.000"), D("5000.00")))
        self.reverse()                                                                              # the unchanged state is still reversible

    def test_an_audit_failure_rolls_the_whole_reversal_back(self):
        before = self.state()
        with patch.object(CorrectionService, "_audit", side_effect=RuntimeError("audit failure")):
            with self.assertRaisesRegex(RuntimeError, "audit failure"):
                self.reverse()
        self.assertEqual(self.state(), before)
        self.assertEqual((self.gold(), self.making(), self.db.corrections), (D("50.000"), D("5000.00"), []))

    def test_a_session_that_ended_during_the_reversal_rolls_everything_back(self):
        before = self.state()
        with patch.object(self.session, "is_current", return_value=False), self.assertRaises(PermissionError):
            self.reverse()
        self.assertEqual(self.state(), before)

    def test_the_settlement_correction_is_not_offered_for_a_supplier_invoice(self):
        with self.assertRaises(CorrectionError) as refused:
            self.corrections.correct_settlement(SOURCE, "1", [], None, REASON)
        self.assertEqual(refused.exception.code, "SETTLEMENT_NOT_SUPPORTED")


class ReversalPreviewTests(InvoiceReversalCase):
    def test_the_preview_states_the_exact_entries_and_the_balances_before_and_after(self):
        before = self.state()
        preview = self.purchases.reversal_preview(self.invoice)
        self.assertEqual((preview["can_reverse"], preview["reversed"], preview["blockers"]), (True, False, []))
        self.assertEqual((preview["gold_delta"], preview["making_delta"]), (D("-50.000"), D("-5000.00")))
        self.assertEqual((preview["gold_before"], preview["gold_after"], preview["making_before"], preview["making_after"]),
                         (D("50.000"), D("0.000"), D("5000.00"), D("0.00")))
        self.assertEqual(self.state(), before)                                                      # a read: nothing was written

    def test_the_preview_needs_the_owner_right_and_reports_a_reversed_invoice(self):
        manager, _, _ = self.purchases_for(MANAGER_RIGHTS, 41)
        with self.assertRaises(PermissionError):
            manager.reversal_preview(self.invoice)
        self.reverse()
        again = self.purchases.reversal_preview(self.invoice)
        self.assertEqual((again["reversed"], again["reversal_code"], again["can_reverse"]), (True, "RV000001", False))
        self.assertEqual([b["code"] for b in again["blockers"]], ["ALREADY_REVERSED"])


if __name__ == "__main__":
    unittest.main()
