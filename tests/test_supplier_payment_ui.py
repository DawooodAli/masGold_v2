"""Supplier making payments (Phase 2A) on the real Main.qml: the «دفع مصنعيات» dialog, the history rows and the reversal dialog,
with real mouse clicks at the three target sizes. The services are the real ones over the in-memory database."""
from decimal import Decimal as D
from unittest.mock import patch
from uuid import uuid4
import unittest

from PySide6.QtCore import QMetaObject, QObject, QPointF, Qt
from PySide6.QtTest import QTest

from services.correction_service import CorrectionService
from tests import test_invoice_page_qml as harness
from tests.supplier_fakes import AccountsRepo, MANAGER_RIGHTS, OWNER_RIGHTS, PaymentRepo, build_payments
from tests.test_supplier_payments import Corrections, Customers, Invoices

NAME = "مورد الدفعات"
CASH_TEXT = "نقدًا من الخزنة الرئيسية"


class SupplierPaymentPageTests(harness.InvoicePageTests):
    def setUp(self):
        super().setUp()
        vms = self.controller.operational_viewmodels
        self.svm, self.pvm = vms["supplierVM"], vms["supplierPurchaseVM"]
        session = self.controller.session
        self.db, _, self.authorization, suppliers, purchases, payments = build_payments(session=session)
        self.svm._service, self.pvm._service, self.svm._payments = suppliers, purchases, payments
        payments._corrections = CorrectionService(
            session, authorization=self.authorization, transaction_factory=self.db.transaction,
            invoice_repository=Invoices, correction_repository=Corrections, customer_repository=Customers)
        for target, value in (("repositories.supplier_payment_repository.SupplierPaymentRepository", PaymentRepo),
                              ("repositories.financial_account_repository.FinancialAccountRepository", AccountsRepo)):
            started = patch(target, value)
            started.start()
            self.addCleanup(started.stop)
        self.grant(*OWNER_RIGHTS)
        self.supplier = suppliers.create_supplier(NAME, karat_id=1)
        draft = purchases.create_draft(self.supplier, 1, "INV-1", "2026-10-09", [{"weight": "50", "making_per_gram": "100"}],
                                       "", str(uuid4()))
        purchases.post_invoice(draft["id"], str(uuid4()))
        self.master = vms["masterDataVM"]
        self.master._karats = [{"id": 1, "karat_value": 21}, {"id": 2, "karat_value": 18}]
        self.master.dataChanged.emit()

    # ------------------------------------------------------------ helpers
    def rights(self, *codes, revoke=()):
        self.grant(*codes, revoke=revoke)
        self.svm.changed.emit()
        self.pvm.changed.emit()
        QTest.qWait(60)

    def wait_idle(self):
        self.wait_until(lambda: not self.svm.busy and not self.pvm.busy, "workers idle")
        QTest.qWait(80)

    def open_supplier(self):
        self.root.setProperty("currentPage", 14)
        QTest.qWait(200)
        self.svm.refresh()
        self.wait_idle()
        self.svm.selectSupplier(self.supplier)
        self.wait_until(lambda: "id" in self.svm.detail, "supplier detail")
        self.wait_idle()

    def open_dialog(self):
        self.click("payMakingButton")
        self.wait_until(lambda: self.item("payMakingDialog").property("opened"), "payment dialog")
        self.wait_until(lambda: self.svm.payContext.get("supplierId"), "payment context")
        QTest.qWait(80)

    def type_in(self, name, text):
        self.click(name)
        self.type_text(text)
        QTest.qWait(60)

    def pick(self, name, index):
        combo = self.item(name)
        self.click(combo)
        QTest.qWait(80)
        for child in combo.findChildren(QObject):
            if child.metaObject().indexOfMethod("close()") >= 0 and child.property("visible"):
                QMetaObject.invokeMethod(child, "close")
                QTest.qWait(150)
        combo.setProperty("currentIndex", index)
        combo.activated[int].emit(index)
        QTest.qWait(150)

    def double_click(self, name):
        item = self.item(name)
        self.assertTrue(item.property("enabled"))
        point = item.mapToScene(QPointF(item.width() / 2, item.height() / 2)).toPoint()
        QTest.mouseClick(self.root, Qt.LeftButton, Qt.NoModifier, point)
        QTest.mouseClick(self.root, Qt.LeftButton, Qt.NoModifier, point)

    def visible_texts(self, name, parent=None):
        parent = parent or self.root.contentItem()
        found = [str(parent.property("text"))] if parent.objectName() == name and parent.property("visible") else []
        for child in parent.childItems():
            found += self.visible_texts(name, child)
        return found

    def making(self):
        return sum((m["amount_delta"] for m in self.db.making), D("0.00"))

    def closed(self, name):
        self.wait_until(lambda: not self.item(name).property("visible"), f"{name} closed")

    # ------------------------------------------------------------ tests
    def test_owner_pays_from_the_main_safe_with_real_clicks_at_1080(self):
        self.root.setWidth(1080)
        self.root.setHeight(680)
        self.open_supplier()
        self.assertEqual(self.text("makingPosition"), "علينا للمورد 5,000.00 ج مصنعيات")
        self.assert_inside_window(self.item("payMakingButton"))
        self.open_dialog()
        self.assertEqual(self.text("payCurrentPosition"), "علينا للمورد 5,000.00 ج مصنعيات")
        self.assertIsNone(self.visible_item("payBankCombo"))                 # the bank selector is for bank payments only
        self.assertFalse(self.item("confirmPayMakingButton").property("enabled"))      # no amount yet
        self.type_in("payAmountField", "1000")
        self.assertEqual(self.text("payResultPosition"), "علينا للمورد 4,000.00 ج مصنعيات")
        self.assertTrue(self.item("confirmPayMakingButton").property("enabled"))
        for name in ("confirmPayMakingButton", "cancelPayMakingButton", "payAmountField", "payNotesField"):
            self.assert_inside_window(self.item(name))
        self.click("confirmPayMakingButton")
        self.wait_until(lambda: len(self.db.payments) == 1, "payment posted")
        self.closed("payMakingDialog")
        self.wait_idle()
        self.assertEqual(self.text("makingPosition"), "علينا للمورد 4,000.00 ج مصنعيات")
        self.assertEqual([(m["account_id"], m["amount_delta"]) for m in self.db.cash_moves], [(1, D("-1000.00"))])
        self.assertEqual(self.db.bank_moves, [])
        self.assertEqual(self.svm.message, "تم تسجيل دفعة المصنعيات SM000001.")
        self.assertEqual(self.warnings, [])

    def test_a_bank_payment_needs_the_account_and_the_payment_can_cross_into_credit(self):
        self.open_supplier()
        self.open_dialog()
        self.click("payMethodBank")
        QTest.qWait(80)
        self.assertIsNotNone(self.visible_item("payBankCombo"))
        self.type_in("payAmountField", "6000")
        self.assertEqual(self.text("payResultPosition"), "لنا عند المورد 1,000.00 ج مصنعيات")
        self.assertIn("رصيد دائن", self.text("payCreditNote"))
        self.assertFalse(self.item("confirmPayMakingButton").property("enabled"))         # no account chosen yet
        self.assertEqual(self.item("payBankCombo").property("currentIndex"), 0)           # the placeholder, not the first bank
        self.pick("payBankCombo", 1)
        self.assertTrue(self.item("confirmPayMakingButton").property("enabled"))
        self.click("confirmPayMakingButton")
        self.wait_until(lambda: len(self.db.payments) == 1, "payment posted")
        self.closed("payMakingDialog")
        self.wait_idle()
        self.assertEqual(([m["account_id"] for m in self.db.bank_moves], self.db.cash_moves), ([5], []))
        self.assertEqual(self.text("makingPosition"), "لنا عند المورد 1,000.00 ج مصنعيات")
        self.assertEqual(self.making(), D("-1000.00"))
        self.assertEqual(self.warnings, [])

    def test_an_invalid_amount_is_explained_and_never_submittable(self):
        self.open_supplier()
        self.open_dialog()
        for bad in ("abc", "0", "12.345"):
            self.item("payAmountField").setProperty("text", "")
            self.type_in("payAmountField", bad)
            self.assertFalse(self.item("confirmPayMakingButton").property("enabled"), bad)
        self.assertNotEqual(self.text("payResultPosition"), "علينا للمورد 4,000.00 ج مصنعيات")
        self.assertEqual(self.db.payments, {})

    def test_a_refusal_keeps_every_field_and_a_corrected_retry_posts_once(self):
        self.open_supplier()
        self.open_dialog()
        self.click("payMethodBank")
        self.type_in("payAmountField", "250")
        self.type_in("payReferenceField", "REF-9")
        self.type_in("payNotesField", "ملاحظة")
        self.pick("payBankCombo", 1)
        self.db.bank_accounts[5]["is_active"] = 0                  # deactivated after the dialog loaded
        self.click("confirmPayMakingButton")
        self.wait_until(lambda: self.svm.errorMessage != "", "refusal")
        QTest.qWait(80)
        self.assertTrue(self.item("payMakingDialog").property("visible"))               # still open
        self.assertIn("غير نشط", self.text("payError"))
        self.assertEqual((self.text("payAmountField"), self.text("payReferenceField"), self.text("payNotesField")),
                         ("250", "REF-9", "ملاحظة"))
        self.assertEqual(self.db.payments, {})
        self.db.bank_accounts[5]["is_active"] = 1
        self.click("confirmPayMakingButton")
        self.wait_until(lambda: len(self.db.payments) == 1, "payment posted")
        self.closed("payMakingDialog")
        self.assertEqual(next(iter(self.db.payments.values()))["reference"], "REF-9")

    def test_a_double_click_posts_exactly_once(self):
        self.open_supplier()
        self.open_dialog()
        self.type_in("payAmountField", "700")
        self.double_click("confirmPayMakingButton")
        self.wait_until(lambda: len(self.db.payments) >= 1, "payment posted")
        self.closed("payMakingDialog")
        self.wait_idle()
        QTest.qWait(150)
        self.assertEqual((len(self.db.payments), len(self.db.cash_moves), self.making()), (1, 1, D("4300.00")))

    def test_history_shows_reference_method_account_and_the_owner_reverses_it_with_a_reason(self):
        self.open_supplier()
        self.open_dialog()
        self.type_in("payAmountField", "1000")
        self.click("confirmPayMakingButton")
        self.wait_until(lambda: len(self.db.payments) == 1, "payment posted")
        self.closed("payMakingDialog")
        self.wait_idle()
        self.click("tabMaking")
        QTest.qWait(150)
        self.assertIn(CASH_TEXT, " ".join(self.visible_texts("makingPaymentDetail")))
        self.assertIsNotNone(self.visible_item("reversePaymentButton"))
        self.assert_inside_window(self.visible_item("reversePaymentButton"))
        self.click(self.visible_item("reversePaymentButton"))
        self.wait_until(lambda: self.item("reversePaymentDialog").property("opened"), "reversal dialog")
        self.assertFalse(self.item("confirmReversePaymentButton").property("enabled"))     # a reason is mandatory
        self.assert_inside_window(self.item("confirmReversePaymentButton"))
        self.type_in("reverseReasonField", "دفعة مكررة")
        self.assertTrue(self.item("confirmReversePaymentButton").property("enabled"))
        self.click("confirmReversePaymentButton")
        self.wait_until(lambda: len(self.db.corrections) == 1, "reversed")
        self.closed("reversePaymentDialog")
        self.wait_idle()
        QTest.qWait(150)
        self.assertEqual((self.making(), sum((m["amount_delta"] for m in self.db.cash_moves), D("0.00"))),
                         (D("5000.00"), D("0.00")))
        states = " | ".join(self.visible_texts("makingPaymentState"))
        self.assertIn("معكوسة بالعملية RV000001", states)
        self.assertIn("عكس الدفعة SM000001", states)
        self.assertIsNone(self.visible_item("reversePaymentButton"))               # a reversed payment cannot be reversed again
        self.assertEqual(self.warnings, [])

    def test_buttons_follow_the_rights_a_manager_pays_but_cannot_reverse_a_cashier_cannot_pay(self):
        self.open_supplier()
        self.open_dialog()
        self.type_in("payAmountField", "100")
        self.click("confirmPayMakingButton")
        self.wait_until(lambda: len(self.db.payments) == 1, "payment posted")
        self.closed("payMakingDialog")
        self.wait_idle()
        self.click("tabMaking")
        QTest.qWait(120)
        self.assertIsNotNone(self.visible_item("reversePaymentButton"))
        self.rights(revoke=("SUPPLIER_MAKING_PAY_REVERSE",))                               # MANAGER
        self.assertIsNotNone(self.visible_item("payMakingButton"))
        self.assertIsNone(self.visible_item("reversePaymentButton"))
        self.rights(revoke=("SUPPLIER_MAKING_PAY",))                                       # no payment right at all
        self.assertIsNone(self.visible_item("payMakingButton"))
        self.assertEqual(MANAGER_RIGHTS & {"SUPPLIER_MAKING_PAY_REVERSE"}, frozenset())

    def test_an_inactive_supplier_is_labelled_inactive_yet_can_be_paid_and_reversed(self):
        self.svm._service.set_active(self.supplier, False)
        self.open_supplier()
        self.assertFalse(self.svm.detail["active"])
        self.assertEqual(self.item("toggleSupplierButton").property("text"), "تفعيل")           # visibly inactive
        self.assertFalse(self.item("newInvoiceButton").property("enabled"))                     # purchases stay blocked
        self.assertTrue(self.item("payMakingButton").property("enabled"))
        self.open_dialog()
        self.assertIsNotNone(self.visible_item("payInactiveNote"))
        self.type_in("payAmountField", "1000")
        self.assertTrue(self.item("confirmPayMakingButton").property("enabled"))
        self.click("confirmPayMakingButton")
        self.wait_until(lambda: len(self.db.payments) == 1, "payment posted")
        self.closed("payMakingDialog")
        self.wait_idle()
        self.assertEqual(self.text("makingPosition"), "علينا للمورد 4,000.00 ج مصنعيات")
        self.assertFalse(self.db.suppliers[int(self.supplier)]["is_active"])
        self.click("tabMaking")
        QTest.qWait(150)
        self.click(self.visible_item("reversePaymentButton"))
        self.wait_until(lambda: self.item("reversePaymentDialog").property("opened"), "reversal dialog")
        self.type_in("reverseReasonField", "تصحيح")
        self.click("confirmReversePaymentButton")
        self.wait_until(lambda: len(self.db.corrections) == 1, "reversed")
        self.closed("reversePaymentDialog")
        self.assertEqual(self.making(), D("5000.00"))
        self.assertEqual(self.warnings, [])

    def test_the_server_refuses_when_the_right_is_revoked_even_though_the_button_was_visible(self):
        self.open_supplier()
        self.open_dialog()
        self.type_in("payAmountField", "100")
        self.authorization.permissions = OWNER_RIGHTS - {"SUPPLIER_MAKING_PAY"}           # revoked in the database
        self.click("confirmPayMakingButton")
        self.wait_until(lambda: self.svm.errorMessage != "", "refusal")
        self.assertEqual(self.db.payments, {})
        self.assertTrue(self.item("payMakingDialog").property("visible"))

    def test_dialogs_fit_at_every_target_size(self):
        self.open_supplier()
        for width, height in harness.SIZES:
            with self.subTest(size=(width, height)):
                self.root.setWidth(width)
                self.root.setHeight(height)
                QTest.qWait(150)
                self.assert_inside_window(self.item("payMakingButton"))
                self.open_dialog()
                self.click("payMethodBank")
                self.type_in("payAmountField", "6000")
                for name in ("payMethodCash", "payMethodBank", "payBankCombo", "payAmountField", "payDateField",
                             "payNotesField", "confirmPayMakingButton", "cancelPayMakingButton"):
                    self.assert_inside_window(self.item(name))
                self.item("payMakingDialog").close()
                self.closed("payMakingDialog")
                self.item("payAmountField").setProperty("text", "")


# The base class carries the invoice-page tests: only this module's own tests are collected here.
for _inherited in [n for n in dir(harness.InvoicePageTests) if n.startswith("test_")]:
    setattr(SupplierPaymentPageTests, _inherited, None)


if __name__ == "__main__":
    unittest.main()
