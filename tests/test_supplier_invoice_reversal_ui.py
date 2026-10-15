"""Full reversal of a posted supplier purchase invoice (052) on the real Main.qml: the invoice details dialog, the confirmation dialog with the exact balance
effect, the reversed state and the refusal paths, with real clicks at 1080x680. The services are the real ones over the in-memory database of
tests/supplier_fakes.py (NOT real-MySQL tests)."""
from decimal import Decimal as D
import os
from pathlib import Path
from unittest.mock import patch
import unittest

from PySide6.QtCore import Q_ARG, QMetaObject
from PySide6.QtTest import QTest

from services.correction_service import CorrectionService
from tests import test_invoice_page_qml as harness
from tests import test_supplier_payments as payments
from tests.supplier_fakes import AccountsRepo, OWNER_RIGHTS, PaymentRepo, build_payments
from tests.test_supplier_invoice_reversal import InvoiceCorrections, ReversalRepo

REASON = "وزن مدخل بالخطأ"
GOLD_ZERO = "حساب المورد: لا توجد مستحقات ذهب"


class InvoiceReversalPageTests(harness.InvoicePageTests):
    def setUp(self):
        super().setUp()
        vms = self.controller.operational_viewmodels
        self.svm, self.pvm = vms["supplierVM"], vms["supplierPurchaseVM"]
        self.db, _, self.authorization, suppliers, purchases, _ = build_payments(session=self.controller.session)
        for target, value in (("repositories.supplier_purchase_repository.SupplierPurchaseRepository", ReversalRepo),
                              ("repositories.supplier_gold_settlement_repository.SupplierGoldSettlementRepository", ReversalRepo),
                              ("repositories.supplier_payment_repository.SupplierPaymentRepository", PaymentRepo),
                              ("repositories.financial_account_repository.FinancialAccountRepository", AccountsRepo)):
            started = patch(target, value)
            started.start()
            self.addCleanup(started.stop)
        self.corrections = CorrectionService(self.controller.session, authorization=self.authorization, transaction_factory=self.db.transaction,
                                             invoice_repository=payments.Invoices, correction_repository=InvoiceCorrections,
                                             customer_repository=payments.Customers)
        purchases._corrections = self.corrections
        self.svm._service, self.pvm._service = suppliers, purchases
        self.suppliers, self.purchases = suppliers, purchases
        self.supplier = self.invoice = None

    def prepare(self, revoke=()):
        """One 21K supplier with ONE posted invoice (50 g, 100 EGP/g), created with the owner's rights; `revoke` then removes rights."""
        self.rights(*OWNER_RIGHTS)
        self.supplier = self.suppliers.create_supplier("مورد العكس", None, None, 1)
        draft = self.purchases.create_draft(self.supplier, 1, "INV-REV", "2026-10-09", [{"weight": "50", "making_per_gram": "100"}], "", payments.key(900))
        self.invoice = self.purchases.post_invoice(draft["id"], payments.key(901))["id"]
        if revoke:
            self.grant(revoke=tuple(revoke))
            self.svm.changed.emit()
            self.pvm.changed.emit()
            QTest.qWait(60)

    # ------------------------------------------------------------ helpers
    def rights(self, *codes):
        self.grant(*codes)
        self.svm.changed.emit()
        self.pvm.changed.emit()
        QTest.qWait(60)

    def wait_idle(self):
        self.wait_until(lambda: not self.svm.busy and not self.pvm.busy, "workers idle")
        QTest.qWait(80)

    def open_details(self):
        self.root.setProperty("currentPage", 14)
        QTest.qWait(200)
        self.svm.selectSupplier(self.supplier)
        self.wait_until(lambda: self.svm.detail.get("id") == self.supplier, "supplier selected")
        self.wait_idle()
        dialog = self.item("supplierIntakeDialog")
        QMetaObject.invokeMethod(dialog, "openFor", Q_ARG("QVariant", str(self.invoice)))
        self.wait_until(lambda: dialog.property("opened") and self.pvm.detail.get("id") == str(self.invoice), "details dialog")
        QTest.qWait(150)

    def open_confirmation(self):
        self.click("reverseInvoiceButton")
        self.wait_until(lambda: self.item("reverseInvoiceDialog").property("opened"), "confirmation dialog")
        self.wait_until(lambda: bool(self.pvm.reversal), "the exact effect is loaded")
        QTest.qWait(150)

    def type_in(self, name, text):
        self.click(name)
        self.type_text(text)

    def capture(self, name):
        """A review screenshot of the whole window (only when MASGOLD_UI_REVIEW_DIR is set)."""
        review = os.getenv("MASGOLD_UI_REVIEW_DIR")
        if not review:
            return
        Path(review).mkdir(parents=True, exist_ok=True)
        QTest.qWait(200)
        target = Path(review) / f"{name}-{self.root.width()}x{self.root.height()}.png"
        self.assertTrue(self.root.grabWindow().save(str(target)), str(target))

    def move(self, ledger, supplier):
        return sum((m["weight_delta_grams" if ledger == "gold" else "amount_delta"] for m in getattr(self.db, ledger) if m["supplier_id"] == int(supplier)), D("0"))

    # ------------------------------------------------------------ tests
    def test_the_owner_reverses_an_invoice_with_real_clicks_at_1080_and_sees_the_exact_effect_first(self):
        self.root.setWidth(1080)
        self.root.setHeight(680)
        self.prepare()
        self.open_details()
        self.assertTrue(self.item("reverseInvoiceButton").property("visible"))
        self.assert_inside_window(self.item("reverseInvoiceButton"))
        before = (self.move("gold", self.supplier), self.move("making", self.supplier))
        self.open_confirmation()
        for name in ("reverseInvoiceReason", "confirmReverseInvoiceButton", "cancelReverseInvoiceButton"):
            self.assert_inside_window(self.item(name))
        self.assertIn("−50.000", self.text("reverseInvoiceGoldEffect"))                      # the exact opposite entry, before anything is posted
        self.assertIn("جرام عيار 21", self.text("reverseInvoiceGoldEffect"))
        self.assertIn(GOLD_ZERO, self.text("reverseInvoiceGoldAfter"))                         # the balance after
        self.assertIn("−5,000.00", self.text("reverseInvoiceMakingEffect"))
        self.assertIn("لا توجد مستحقات مصنعية", self.text("reverseInvoiceMakingAfter"))
        self.assertFalse(self.item("confirmReverseInvoiceButton").property("enabled"))         # a reason is mandatory
        self.capture("supplier-invoice-reversal-confirmation")
        self.assertEqual((self.move("gold", self.supplier), self.move("making", self.supplier)), before)    # the preview wrote nothing
        self.type_in("reverseInvoiceReason", REASON)
        self.assertTrue(self.item("confirmReverseInvoiceButton").property("enabled"))
        self.click("confirmReverseInvoiceButton")
        self.wait_until(lambda: len(self.db.corrections) == 1, "reversed")
        self.wait_until(lambda: not self.item("reverseInvoiceDialog").property("visible"), "confirmation closed")
        self.wait_idle()
        self.assertEqual((self.move("gold", self.supplier), self.move("making", self.supplier)), (D("0.000"), D("0.00")))
        self.wait_until(lambda: self.pvm.detail.get("reversed") is True, "reversed state loaded")
        QTest.qWait(150)
        self.assertTrue(self.item("invoiceReversedBanner").property("visible"))
        self.assertIn("RV000001", self.text("invoiceReversedTitle"))
        self.assertFalse(self.item("reverseInvoiceButton").property("visible"))                # a second reversal is not offered
        self.assertEqual(self.pvm.detail["statusText"], "معكوسة")
        self.capture("supplier-invoice-reversed-details")
        self.assertEqual(self.warnings, [])

    def test_the_action_is_hidden_without_the_owner_right_and_the_service_still_refuses(self):
        self.prepare(revoke=("SUPPLIER_INVOICE_REVERSE",))
        self.open_details()
        self.assertFalse(self.item("reverseInvoiceButton").property("visible"))
        before = (len(self.db.corrections), self.move("gold", self.supplier))
        self.pvm.reverseInvoice(str(self.invoice), REASON)                                     # the slot is reachable; the service is the authority
        self.wait_idle()
        self.assertEqual((len(self.db.corrections), self.move("gold", self.supplier)), before)
        self.assertNotEqual(self.pvm.errorMessage, "")
        self.assertEqual(self.warnings, [])

    def test_an_invoice_with_an_intaken_piece_shows_the_exact_reason_and_cannot_be_confirmed(self):
        self.prepare()
        group = self.purchases._repository(self.db).groups(self.invoice)[0]
        self.purchases.add_piece(self.invoice, str(group["id"]), 1, "10.000", payments.key(990))
        self.open_details()
        self.open_confirmation()
        blockers = [self.pvm.reversal["blockers"]]
        self.assertTrue(blockers[0])
        self.assertIn("1 قطعة", " ".join(blockers[0]))
        self.type_in("reverseInvoiceReason", REASON)
        self.assertFalse(self.item("confirmReverseInvoiceButton").property("enabled"))
        self.assertEqual(len(self.db.corrections), 0)
        self.assertEqual(self.warnings, [])


def load_tests(loader, tests, pattern):
    """Only this module's tests: the inherited harness tests run in their own modules."""
    suite = unittest.TestSuite()
    suite.addTests(InvoiceReversalPageTests(name) for name in loader.getTestCaseNames(InvoiceReversalPageTests)
                   if name in InvoiceReversalPageTests.__dict__)
    return suite


if __name__ == "__main__":
    unittest.main()
