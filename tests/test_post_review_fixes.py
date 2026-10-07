"""Post manual-review fix pack: ViewModel / service level regressions (no database, calls run inline)."""
import os
import unittest
from unittest.mock import patch

from PySide6.QtGui import QGuiApplication

from app.session import AppSession
from app.viewmodels.correction_viewmodel import CorrectionViewModel
from database.connection import DatabaseConnection
from services.authentication_service import AuthenticationResult, _VERIFIED

PERMISSIONS = frozenset({"APP_LOGIN", "TRANSACTION_CORRECT"})


def make_session(permissions=PERMISSIONS):
    session = AppSession()
    session._establish(AuthenticationResult(17, 7, "مدير", permissions, _VERIFIED), int(session.generation))
    return session


class InlineMixin:
    """Runs each background call inline through the real completion path."""

    def _start(self, operation, call):
        actor = self._session.current_actor()
        if actor is None:
            return
        self._request += 1
        self._latest[operation] = self._request
        self._busy.add(operation) if hasattr(self, "_busy") else self._busy_ops.add(operation)
        result, error, code, kind = None, "", "", ""
        try:
            result = call(_expected_actor=actor)
        except (ValueError, PermissionError) as exc:
            error, code, kind = str(exc), getattr(exc, "code", ""), type(exc).__name__
        self._completed(self._request, str(actor.generation), operation, result, error, code, kind)


class InlineCorrection(InlineMixin, CorrectionViewModel):
    pass


class FakeCorrectionService:
    """Two settlement corrections in a row: every correction is a NEW record and the current
    settlement the screen loads is always the latest effective one."""

    def __init__(self):
        self.corrections, self.calls = [], []

    def _targets(self):
        done = len(self.corrections)
        return {"invoice_id": "901", "invoice_number": "26100601", "customer_id": "12", "customer_name": "عميل",
                "bank_accounts": [{"id": 7, "name": "المحفظة", "account_type": "MOBILE_WALLET"}],
                "targets": [{"reference": "26100601", "source_type": "INVOICE", "source_id": "901",
                             "source_text": "فاتورة", "direction": "SALE", "reversed_by": None,
                             "settlement_corrections": done, "settlement_allowed": True}]}

    def targets(self, invoice_id, *, _expected_actor=None):
        self.calls.append("targets")
        return self._targets()

    def current_settlement(self, source_type, source_id, *, _expected_actor=None):
        self.calls.append("current_settlement")
        cash = "20000.00" if not self.corrections else self.corrections[-1]["cash"]
        legs = [{"method_code": "PHYSICAL_CASH", "bank_account_id": "", "amount": cash,
                 "method_text": "نقدي", "account_name": ""}]
        if self.corrections:
            legs.append({"method_code": "MOBILE_WALLET", "bank_account_id": "7",
                         "amount": self.corrections[-1]["wallet"], "method_text": "محفظة", "account_name": "المحفظة"})
        return {"legs": legs, "account": "0.00", "total": "20000.00", "direction_text": "المستلم من العميل",
                "has_customer": True, "next_sequence": len(self.corrections) + 1}

    def preview_settlement(self, source_type, source_id, legs, account, *, _expected_actor=None):
        self.calls.append("preview_settlement")
        return {"problems": [], "money_deltas": [{"account": "الخزنة", "delta": "-1000.00"}], "account_delta": "0"}

    def correct_settlement(self, source_type, source_id, legs, account, reason, *, expected_sequence,
                           _expected_actor=None):
        if expected_sequence != len(self.corrections) + 1:
            raise ValueError("stale settlement")
        by = {l.method_code if hasattr(l, "method_code") else l.method: l.amount for l in legs}
        self.corrections.append({"cash": str(by.get("PHYSICAL_CASH", "0")), "wallet": str(by.get("MOBILE_WALLET", "0")),
                                 "reason": reason})
        return {"code": f"FC{len(self.corrections):06d}", "source_reference": "26100601"}


class CorrectionRefreshTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        cls.app = QGuiApplication.instance() or QGuiApplication([])

    def setUp(self):
        guard = patch.object(DatabaseConnection, "connect", side_effect=AssertionError("no database"))
        guard.start()
        self.addCleanup(guard.stop)
        self.service = FakeCorrectionService()
        self.vm = InlineCorrection(make_session(), service=self.service, debounce_ms=0)

    def correct(self, cash, wallet):
        self.vm.setLegAmount(self.vm.legs[0]["key"], cash)
        if len(self.vm.legs) < 2:
            self.vm.addLeg()
        wallet_leg = self.vm.legs[1]
        self.vm.setLegMethod(wallet_leg["key"], "MOBILE_WALLET")
        self.vm.setLegAccount(wallet_leg["key"], "7")
        self.vm.setLegAmount(wallet_leg["key"], wallet)
        self.vm._timer.stop()
        self.vm._run_settlement_preview()
        self.vm.setReason("تصحيح طريقة الدفع")
        self.assertTrue(self.vm.canExecute)
        self.vm.requestConfirm()
        self.vm.execute()

    def test_second_correction_needs_no_page_reentry(self):
        self.vm.open("901")
        self.vm.setMode("SETTLEMENT")
        self.assertEqual([l["amount"] for l in self.vm.legs], ["20000.00"])
        self.correct("19000", "1000")
        # Correction A: saved as FC000001; the screen already shows the NEW current settlement.
        self.assertEqual(len(self.service.corrections), 1)
        self.assertFalse(self.vm.done)                       # not locked: ready for the next correction
        self.assertEqual(self.vm.mode, "SETTLEMENT")
        self.assertEqual([(l["method"], l["amount"]) for l in self.vm.legs],
                         [("PHYSICAL_CASH", "19000"), ("MOBILE_WALLET", "1000")])
        self.assertEqual((self.vm.reason, self.vm.accountAmount), ("", ""))     # the old draft is gone
        self.assertEqual(self.vm.settlement["deltas"], [])                       # no stale delta preview
        self.assertIn("FC000001", self.vm.notice)
        self.assertIn("صُححت تسويتها 1 مرة", [t["state"] for t in self.vm.targets][0])
        self.assertFalse(self.vm.canExecute)                 # a new reason is required
        # Correction B on the same open screen: a separate, second record.
        self.correct("18000", "2000")
        self.assertEqual([c["reason"] for c in self.service.corrections], ["تصحيح طريقة الدفع"] * 2)
        self.assertEqual([(c["cash"], c["wallet"]) for c in self.service.corrections],
                         [("19000", "1000"), ("18000", "2000")])        # never merged / overwritten
        self.assertIn("FC000002", self.vm.notice)
        self.assertEqual([l["amount"] for l in self.vm.legs], ["18000", "2000"])

    def test_full_reversal_still_ends_the_workflow(self):
        service = self.service
        service.preview_reversal = lambda *a, **k: {
            "can_reverse": True, "source_text": "فاتورة", "reference": "26100601", "customer_name": "عميل",
            "customer_id": "12", "original_date": "2026-10-06 10:00:00", "original_total": "20000.00", "money": [],
            "account": [], "inventory": [], "old_gold": [], "reviews": 0, "cases": 0, "blockers": []}
        service.reverse = lambda *a, **k: {"code": "RV000001", "source_reference": "26100601"}
        self.vm.open("901")
        self.vm.setMode("REVERSAL")
        self.vm.setReason("خطأ")
        self.vm.requestConfirm()
        self.vm.execute()
        self.assertTrue(self.vm.done)


# ---------------------------------------------------------------------------------------------------------
# 21. Settlement correction: cash direction (audit)
# ---------------------------------------------------------------------------------------------------------
from decimal import Decimal as D

from app.viewmodels.financial_accounts_viewmodel import movement_line, reference_text
from services.correction_service import SettlementLeg
from tests.test_cash_screen import cash_row
from tests.test_correction_service import CorrectionTestCase
from tests.test_financial_accounts import FinancialTestCase

REASON = "تصحيح طريقة الدفع"


class CorrectionCashDirectionTests(CorrectionTestCase):
    """The economic sign of every posted correction row. Cash decreasing is a cash OUT (negative delta); cash
    increasing is a cash IN (positive delta); the non-cash account receives the opposite delta."""

    def correct(self, source_type, source_id, legs, account=None):
        return self.service.correct_settlement(source_type, source_id, [SettlementLeg(*l) for l in legs], account, REASON)

    def corrections(self):
        return sorted(m[:3] for m in self.store.money if m[3] == "TRANSACTION_CORRECTION")

    def test_sale_cash_to_wallet_is_cash_out_and_wallet_in(self):
        # Invoice 500 (customer pays IN): cash 20,000 + bank 7 10,000 + account 5,000. Move 1,000 cash → wallet 8.
        cash_before = self.store.balance("CASH", 1)
        self.correct("INVOICE", "500", [("PHYSICAL_CASH", "19000.00"), ("BANK_TRANSFER", "10000.00", "7"),
                                        ("MOBILE_WALLET", "1000.00", "8")], "5000.00")
        self.assertEqual(self.corrections(), [("BANK", 8, D("1000.00")), ("CASH", 1, D("-1000.00"))])
        self.assertEqual(self.store.balance("CASH", 1), cash_before - D("1000.00"))      # the safe lost 1,000

    def test_sale_wallet_to_cash_is_cash_in_and_wallet_out(self):
        self.correct("INVOICE", "500", [("PHYSICAL_CASH", "19000.00"), ("BANK_TRANSFER", "10000.00", "7"),
                                        ("MOBILE_WALLET", "1000.00", "8")], "5000.00")
        cash_before = self.store.balance("CASH", 1)
        self.correct("INVOICE", "500", [("PHYSICAL_CASH", "20000.00"), ("BANK_TRANSFER", "10000.00", "7")], "5000.00")
        second = [m[:3] for m in self.store.money[-2:]]
        self.assertEqual(sorted(second), [("BANK", 8, D("-1000.00")), ("CASH", 1, D("1000.00"))])
        self.assertEqual(self.store.balance("CASH", 1), cash_before + D("1000.00"))
        self.assertEqual(self.store.balance("BANK", 8), D("0.00"))             # the wallet nets back to zero

    def test_payout_less_cash_is_a_cash_in_and_more_cash_a_cash_out(self):
        # Return 800 (money OUT to the customer): cash −12,000. Paying 1,000 of it through the wallet instead
        # means LESS cash leaves the safe: a cash IN of 1,000 (never a cash out) and the wallet goes out.
        self.correct("RETURN", "800", [("PHYSICAL_CASH", "11000.00"), ("MOBILE_WALLET", "1000.00", "8")], "500.00")
        self.assertEqual(self.corrections(), [("BANK", 8, D("-1000.00")), ("CASH", 1, D("1000.00"))])
        self.correct("RETURN", "800", [("PHYSICAL_CASH", "12000.00")], "500.00")
        self.assertEqual(sorted(m[:3] for m in self.store.money[-2:]),
                         [("BANK", 8, D("1000.00")), ("CASH", 1, D("-1000.00"))])    # back: more cash leaves again

    def test_every_correction_is_a_new_record_and_never_edits_the_original_rows(self):
        original = [m for m in self.store.money if m[3] == "INVOICE"]
        self.correct("INVOICE", "500", [("PHYSICAL_CASH", "19000.00"), ("BANK_TRANSFER", "10000.00", "7"),
                                        ("MOBILE_WALLET", "1000.00", "8")], "5000.00")
        self.correct("INVOICE", "500", [("PHYSICAL_CASH", "18000.00"), ("BANK_TRANSFER", "10000.00", "7"),
                                        ("MOBILE_WALLET", "2000.00", "8")], "5000.00")
        self.assertEqual([c["chain_sequence"] for c in self.store.corrections], [1, 2])
        self.assertEqual([m for m in self.store.money if m[3] == "INVOICE"], original)
        self.assertEqual(self.store.balance("BANK", 8), D("2000.00"))


class CashViewDirectionTests(FinancialTestCase):
    def test_correction_rows_are_placed_by_their_sign(self):
        self.store.cash_rows = [
            cash_row(2, "1000", "CORRECTION", ("1000", 1)),            # cash increased → incoming
            cash_row(1, "-1000", "CORRECTION", ("-1000", 1))]          # cash decreased → outgoing
        view = self.fin.cash_view()
        self.assertEqual([(r["label"], r["amount"]) for r in view["in"]["rows"]], [("تصحيح تسوية", "1000.00")])
        self.assertEqual([(r["label"], r["amount"]) for r in view["out"]["rows"]], [("تصحيح تسوية", "1000.00")])
        self.assertEqual((view["in"]["total"], view["out"]["total"]), ("1000.00", "1000.00"))


# ---------------------------------------------------------------------------------------------------------
# 20 / 22. Movement identification and the bank / wallet ledger rows
# ---------------------------------------------------------------------------------------------------------
class MovementContextTests(FinancialTestCase):
    @staticmethod
    def row(i, delta, kind, **extra):
        base = cash_row(i, delta, kind, ("1000", 1), invoice_id=extra.pop("invoice_id", None))
        base.update(extra)
        return base

    def test_cash_rows_carry_their_stored_identification(self):
        self.store.cash_rows = [
            self.row(1, "2000", "SALE", source_reference="26100601", invoice_number="26100601", customer_name="أحمد",
                     customer_id=12, invoice_id=21),
            self.row(2, "200", "CUSTOMER_PAYMENT", source_reference="CS000003", customer_name="بكر", customer_id=13),
            self.row(3, "-1000", "CORRECTION", source_reference="FC000001", invoice_number="26100601",
                     corrected_reference="26100601", customer_name="أحمد", customer_id=12, invoice_id=21)]
        rows = self.fin.cash_view()["in"]["rows"] + self.fin.cash_view()["out"]["rows"]
        sale, payment, correction = rows[0], rows[1], rows[2]
        self.assertEqual((sale["reference"], sale["invoice_number"], sale["customer"], sale["customer_id"]),
                         ("26100601", "26100601", "أحمد", "12"))
        self.assertEqual((payment["reference"], payment["customer"]), ("CS000003", "بكر"))
        self.assertEqual((correction["reference"], correction["corrected_reference"]), ("FC000001", "26100601"))

    def test_lines_read_like_the_accountant_expects(self):
        sale = {"kind": "SALE", "reference": "26100601", "invoice_number": "26100601", "customer": "أحمد"}
        self.assertEqual(reference_text(sale), "فاتورة ⁦26100601⁩")
        self.assertEqual(movement_line("بيع", sale, "2,000.00 ج"), "بيع — فاتورة ⁦26100601⁩ — أحمد — 2,000.00 ج")
        payment = {"kind": "CUSTOMER_PAYMENT", "reference": "CS000003", "customer": "بكر"}
        self.assertEqual(movement_line("سداد عميل", payment, "200.00 ج"), "سداد عميل — ⁦CS000003⁩ — بكر — 200.00 ج")
        correction = {"kind": "CORRECTION", "reference": "FC000001", "invoice_number": "26100601",
                      "corrected_reference": "26100601", "customer": ""}
        self.assertEqual(reference_text(correction), "فاتورة ⁦26100601⁩ — ⁦FC000001⁩")
        refund = {"kind": "RETURN", "reference": "RT000001", "invoice_number": "26100601"}
        self.assertEqual(reference_text(refund), "⁦RT000001⁩ — فاتورة ⁦26100601⁩")
        self.assertEqual(reference_text({"kind": "OTHER"}), "")                       # nothing stored: nothing invented

    def test_bank_ledger_rows_have_labels_and_sources(self):
        self.store.statement_rows = [{
            "id": 9, "amount_delta": D("1000.00"), "balance_after": D("19000.00"), "label_key": "TRANSFER_IN",
            "source_reference": "TR000002", "invoice_number": None, "corrected_reference": None,
            "customer_name": None, "customer_id": None, "invoice_id": None, "operation_id": 2,
            "occurred_at": None, "actor_name": "المدير"}, {
            "id": 8, "amount_delta": D("-500.00"), "balance_after": D("18000.00"), "label_key": "CORRECTION",
            "source_reference": "FC000001", "invoice_number": "26100601", "corrected_reference": "26100601",
            "customer_name": "أحمد", "customer_id": 12, "invoice_id": 21, "operation_id": None,
            "occurred_at": None, "actor_name": "المدير"}]
        statement = self.fin.statement("BANK:7")
        first, second = statement["rows"]
        self.assertEqual((first["label"], first["reference"], first["balance_after"]),
                         ("إيداع في الحساب", "TR000002", D("19000.00")))      # a transfer INTO a bank = a deposit
        self.assertEqual((second["label"], second["customer"], second["customer_id"], second["invoice_id"]),
                         ("تصحيح تسوية", "أحمد", "12", "21"))
        cash = self.fin.statement("CASH:1")["rows"][0]
        self.assertEqual(cash["label"], "سحب بنك")                            # the same movement seen from the safe


# ---------------------------------------------------------------------------------------------------------
# كسر فقط = حفظ فقط: the print service and the history never print / offer a reprint for old gold only
# ---------------------------------------------------------------------------------------------------------
from tests import test_invoice_history as history_fixture
from tests import test_invoice_print_service as print_fixture
from services.invoice_print_service import NOT_PRINTABLE_TEXT, is_old_gold_purchase_only


class SaveOnlyOldGoldServiceTests(unittest.TestCase):
    def service(self, lines, invoice_type):
        sent = []
        invoices = {"42": print_fixture.invoice_row(lines=lines, invoice_type=invoice_type)}
        service, state = print_fixture.make_service(invoices, print_document=lambda ops: (sent.append(ops) or (True, "ok", None)))
        return service, sent

    def test_classification_comes_from_the_saved_lines(self):
        sale, gold = print_fixture.sale_line(), print_fixture.old_gold_line()
        self.assertTrue(is_old_gold_purchase_only([gold]))
        self.assertFalse(is_old_gold_purchase_only([sale]))
        self.assertFalse(is_old_gold_purchase_only([sale, gold]))
        self.assertFalse(is_old_gold_purchase_only([]))
        manual = print_fixture.sale_line(line_type="SELL_UNIDENTIFIED")
        self.assertFalse(is_old_gold_purchase_only([manual, gold]))

    def test_old_gold_only_is_never_composed_or_sent(self):
        service, sent = self.service([print_fixture.old_gold_line()], "BUY")
        outcome = service.print_invoice("42")
        self.assertEqual((outcome.printed, outcome.message), (False, NOT_PRINTABLE_TEXT))
        self.assertEqual(sent, [])                                                  # no overlay, no print job

    def test_sale_and_mixed_still_print(self):
        for lines, kind in (([print_fixture.sale_line()], "SELL"),
                            ([print_fixture.sale_line(), print_fixture.old_gold_line()], "MIXED")):
            with self.subTest(kind):
                service, sent = self.service(lines, kind)
                outcome = service.print_invoice("42")
                self.assertTrue(outcome.printed)
                self.assertEqual(len(sent), 1)

    def test_history_never_offers_reprint_for_old_gold_only(self):
        service, state, printer = history_fixture.make(permissions=history_fixture.MANAGER)
        self.assertTrue(service.detail("21")["can_reprint"])                        # the fixture is a MIXED invoice
        lines = state["invoices"]["21"]["lines"]
        state["invoices"]["21"]["lines"] = [l for l in lines if l["line_type"] != "BUY_OLD_GOLD"]
        self.assertTrue(service.detail("21")["can_reprint"])                        # SALE
        state["invoices"]["21"]["lines"] = [l for l in lines if l["line_type"] == "BUY_OLD_GOLD"]
        detail = service.detail("21")
        self.assertEqual(detail["transaction_kind"], "OLD_GOLD_PURCHASE")
        self.assertFalse(detail["can_reprint"])


if __name__ == "__main__":
    unittest.main()
