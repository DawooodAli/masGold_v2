"""Task 15: the reason-only dialogs share the interaction of reverseSettlementDialog (the approved reference, tested in
tests/test_supplier_gold_settlement_ui.py): the reason field takes focus on open, Escape closes at once, Return / keypad Enter submit
only a non-blank reason while nothing is running, and a repeated key or button + key reaches the view model once.

The real Main.qml pages run offscreen with the database blocked. Each dialog is opened through its own openFor(); the view-model slot the
dialog calls is replaced on the instance by a recorder that mimics the real `_start()`: it notes the arguments and marks the operation
busy (`_busy` marker + `changed`), exactly what the real slots do before their worker starts. No service, no business rule is exercised
here: the tests prove what the dialog passes and when it refuses to pass it."""
import unittest

from PySide6.QtCore import Q_ARG, QMetaObject, QObject, Qt
from PySide6.QtTest import QTest

from tests import test_invoice_page_qml as harness

SUPPLIERS_PAGE, TRIPS_PAGE = 14, 15
REASON = "خطأ في التسجيل"

# name, dialog objectName, view model (context name or "supplierVM.marketer"), slot, busy marker, reason field, confirm button,
# open() arguments, expected slot arguments (the reason is appended as the second-to-last or last item, see ARGS)
# The raw preview the service gives reverseInvoiceDialog (SupplierPurchaseViewModel.reversal formats it); the dialog's confirm button also needs it loaded.
INVOICE_PREVIEW = {"id": "9", "code": "SI000009", "supplier_name": "مورد الاختبار", "karat_value": 21, "reversed": False, "reversal_code": "", "can_reverse": True,
                   "blockers": [], "gold_delta": "-100", "making_delta": "-10800", "gold_before": "100", "gold_after": "0", "making_before": "10800", "making_after": "0"}

SPECS = (
    {"name": "payment", "dialog": "reversePaymentDialog", "vm": "supplierVM", "slot": "reversePayment", "marker": "reverse", "page": SUPPLIERS_PAGE,
     "field": "reverseReasonField", "confirm": "confirmReversePaymentButton",
     "open": ({"paymentId": "31", "reference": "PM000031", "amount": "1,000 ج", "detail": "دفعة"},), "args": ("31", REASON)},
    {"name": "invoice", "dialog": "reverseInvoiceDialog", "vm": "supplierPurchaseVM", "slot": "reverseInvoice", "marker": "write", "page": SUPPLIERS_PAGE,
     "field": "reverseInvoiceReason", "confirm": "confirmReverseInvoiceButton", "preview": INVOICE_PREVIEW,
     "open": ({"id": "9", "code": "SI000009"},), "args": ("9", REASON)},
    {"name": "adjustment", "dialog": "reverseAdjustmentDialog", "vm": "supplierPurchaseVM", "slot": "reverseAccountAdjustment", "marker": "write", "page": SUPPLIERS_PAGE,
     "field": "reverseAdjustmentReason", "confirm": "confirmReverseAdjustmentButton",
     "open": ({"adjustmentId": "41", "movement": "تسوية فروق", "side": "لنا", "weight": "1.000"},), "args": ("41", REASON)},
    {"name": "bar fee", "dialog": "reverseBarFeeDialog", "vm": "supplierVM", "slot": "reverseBarFee", "marker": "barfeerev", "page": SUPPLIERS_PAGE,
     "field": "reverseBarFeeReason", "confirm": "confirmReverseBarFeeButton",
     "open": ({"barFeeEntryId": "51", "movement": "أجر سبيكة", "side": "علينا", "amount": "500", "reference": "BF51"},), "args": ("51", REASON)},
    {"name": "receipt", "dialog": "reverseReceiptDialog", "vm": "supplierVM", "slot": "reverseReceipt", "marker": "hrrev", "page": SUPPLIERS_PAGE,
     "field": "reverseReceiptReason", "confirm": "confirmReverseReceiptButton",
     "open": ({"id": "61", "code": "HR000061", "text": "استلام", "effectText": "1.000 جرام"},), "args": ("61", REASON)},
    {"name": "transfer", "dialog": "reverseTransferDialog", "vm": "supplierVM", "slot": "reverseSupplierGoldTransfer", "marker": "transferrev", "page": SUPPLIERS_PAGE,
     "field": "reverseTransferReason", "confirm": "confirmReverseTransferButton",
     "open": ({"transferId": "71", "movement": "تسوية ذهب", "weight": "2.000", "reference": "TR71"},), "args": ("71", REASON)},
    {"name": "marketer document", "dialog": "marketerReverseDialog", "vm": "supplierVM.marketer", "slot": "reverseDocument", "marker": "write", "page": SUPPLIERS_PAGE,
     "field": "marketerReverseReason", "confirm": "confirmMarketerReverseButton",
     "open": ({"id": "81", "kindText": "تسليم", "code": "MD000081", "text": "مستند"}, False), "args": ("81", REASON)},
    {"name": "marketer settlement", "dialog": "marketerReverseDialog", "vm": "supplierVM.marketer", "slot": "reverseSettlement", "marker": "write", "page": SUPPLIERS_PAGE,
     "field": "marketerReverseReason", "confirm": "confirmMarketerReverseButton",
     "open": ({"id": "82", "kindText": "", "code": "MS000082", "text": "تسوية"}, True), "args": ("82", REASON)},
    {"name": "trip reverse", "dialog": "tripReasonDialog", "vm": "marketTripVM", "slot": "reverseEntry", "marker": "write", "page": TRIPS_PAGE,
     "field": "tripReasonField", "confirm": "confirmTripReasonButton", "open": ("REVERSE", "91", "TX000091", ""), "args": ("91", REASON)},
    {"name": "trip reopen", "dialog": "tripReasonDialog", "vm": "marketTripVM", "slot": "reopenTrip", "marker": "write", "page": TRIPS_PAGE,
     "field": "tripReasonField", "confirm": "confirmTripReasonButton", "open": ("REOPEN", "92", "TRIP92", ""), "args": ("92", REASON)},
)


class ReasonDialogTests(harness.InvoicePageTests):
    def setUp(self):
        super().setUp()
        self.recorded = {}
        self.markers = []
        self.addCleanup(self.release_busy)

    def release_busy(self):
        for vm, marker in self.markers:
            vm._busy.discard(marker)

    # ------------------------------------------------------------ helpers
    def view_model(self, spec):
        vm = self.controller.operational_viewmodels["supplierVM"]
        return vm.marketer if spec["vm"] == "supplierVM.marketer" else self.controller.operational_viewmodels[spec["vm"]]

    def prepare(self, spec):
        """Show the page, replace the view-model slot by the recorder, return the dialog."""
        self.root.setWidth(1280)
        self.root.setHeight(800)
        self.root.setProperty("currentPage", spec["page"])
        QTest.qWait(250)
        vm = self.view_model(spec)
        calls = []

        def recorder(*arguments):
            calls.append(arguments)
            vm._busy.add(spec["marker"])                    # what the real slot's _start() does before its worker runs
            vm.changed.emit()

        setattr(vm, spec["slot"], recorder)
        if "preview" in spec:
            vm.loadReversal = lambda *unused: None              # the preview is handed over below, not read from a service
            vm._reversal = dict(spec["preview"])
            vm.changed.emit()
        self.markers.append((vm, spec["marker"]))
        self.recorded[spec["slot"]] = calls
        QTest.qWait(60)
        return self.item(spec["dialog"]), vm, calls

    def open_dialog(self, spec, dialog):
        arguments = [Q_ARG("QVariant", value) for value in spec["open"]]
        QMetaObject.invokeMethod(dialog, "openFor", Qt.DirectConnection, *arguments)

    def settled_calls(self, calls):
        QTest.qWait(300)                                   # long enough for a second (duplicate) call to arrive
        return list(calls)

    def focus_name(self):
        item = self.root.activeFocusItem()
        return "none" if item is None else (item.objectName() or item.metaObject().className())

    def close_dialog(self, dialog, vm, spec):
        vm._busy.discard(spec["marker"])
        vm.changed.emit()
        if dialog.property("visible"):
            QMetaObject.invokeMethod(dialog, "close")
            self.wait_until(lambda: not dialog.property("visible"), "dialog closed")

    def type_reason(self, spec, text=REASON):
        self.click(spec["field"])
        QTest.keyClick(self.root, Qt.Key_A, Qt.ControlModifier)
        self.type_text(text)
        QTest.qWait(60)

    def check(self, spec, calls):
        """The view model got exactly the dialog's own arguments (an adjustment also carries the supplier id and a request key)."""
        self.assertEqual(len(calls), 1, (spec["name"], calls))
        arguments = calls[0]
        if spec["slot"] == "reverseAccountAdjustment":
            self.assertEqual((arguments[1], arguments[2]), spec["args"])
            self.assertTrue(arguments[3])
        else:
            self.assertEqual(tuple(arguments), spec["args"])

    def each(self):
        for spec in SPECS:
            with self.subTest(dialog=spec["name"]):
                dialog, vm, calls = self.prepare(spec)
                yield spec, dialog, vm, calls
                self.close_dialog(dialog, vm, spec)

    # ------------------------------------------------------------ tests
    def test_every_dialog_takes_focus_into_the_reason_field_before_its_open_transition_ends(self):
        for spec, dialog, vm, calls in self.each():
            self.open_dialog(spec, dialog)                          # no wait: focus and Escape work at once
            self.assertEqual(self.focus_name(), spec["field"])
            self.assertTrue(self.item(spec["field"]).property("activeFocus"))
            self.assertFalse(self.item(spec["confirm"]).property("enabled"), "an empty reason must keep the confirm button disabled")

    def test_every_dialog_closes_on_an_immediate_escape_without_submitting(self):
        for spec, dialog, vm, calls in self.each():
            self.open_dialog(spec, dialog)
            QTest.keyClick(self.root, Qt.Key_Escape)
            self.wait_until(lambda: not dialog.property("visible"), f"{spec['name']} did not close on Escape")
            self.assertEqual(self.settled_calls(calls), [])

    def test_every_dialog_submits_once_on_return_and_on_keypad_enter(self):
        for key, modifier in ((Qt.Key_Return, Qt.NoModifier), (Qt.Key_Enter, Qt.KeypadModifier)):
            for spec, dialog, vm, calls in self.each():
                self.open_dialog(spec, dialog)
                self.wait_until(lambda: dialog.property("opened"), "dialog opened")
                self.type_reason(spec)
                self.assertEqual(self.item(spec["field"]).property("text"), REASON)
                QTest.keyClick(self.root, key, modifier)
                self.wait_until(lambda: calls, f"{spec['name']}: Enter did not submit")
                self.check(spec, self.settled_calls(calls))

    def test_every_dialog_does_not_submit_an_empty_or_blank_reason_on_enter(self):
        for spec, dialog, vm, calls in self.each():
            for reason in ("", "   "):
                self.open_dialog(spec, dialog)
                self.wait_until(lambda: dialog.property("opened"), "dialog opened")
                self.click(spec["field"])
                if reason:
                    self.type_text(reason)
                self.assertEqual(self.item(spec["field"]).property("text"), reason)
                QTest.keyClick(self.root, Qt.Key_Return)
                self.assertEqual(self.settled_calls(calls), [], (spec["name"], repr(reason)))
                self.assertTrue(dialog.property("visible"))
                QMetaObject.invokeMethod(dialog, "close")
                self.wait_until(lambda: not dialog.property("visible"), "dialog closed")

    def test_every_dialog_does_not_submit_on_enter_while_its_operation_is_running(self):
        for spec, dialog, vm, calls in self.each():
            self.open_dialog(spec, dialog)
            self.wait_until(lambda: dialog.property("opened"), "dialog opened")
            self.type_reason(spec)
            vm._busy.add(spec["marker"])
            vm.changed.emit()
            QTest.qWait(60)
            self.assertFalse(self.item(spec["confirm"]).property("enabled"))
            QTest.keyClick(self.root, Qt.Key_Return)
            self.assertEqual(self.settled_calls(calls), [], spec["name"])
            self.assertTrue(dialog.property("visible"))

    def test_every_dialog_reaches_the_view_model_once_from_repeated_keys_or_button_plus_key(self):
        for how in ("two Returns", "button then Return", "Return then button"):
            for spec, dialog, vm, calls in self.each():
                self.open_dialog(spec, dialog)
                self.wait_until(lambda: dialog.property("opened"), "dialog opened")
                self.type_reason(spec)
                if how == "two Returns":
                    QTest.keyClick(self.root, Qt.Key_Return)
                    QTest.keyClick(self.root, Qt.Key_Return)
                elif how == "button then Return":
                    self.click(spec["confirm"])
                    QTest.keyClick(self.root, Qt.Key_Return)
                else:
                    QTest.keyClick(self.root, Qt.Key_Return)
                    self.click(spec["confirm"]) if self.item(spec["confirm"]).property("enabled") else None
                self.wait_until(lambda: calls, f"{spec['name']} ({how}): nothing submitted")
                self.check(spec, self.settled_calls(calls))

    def test_every_dialog_button_click_still_submits_the_same_arguments_once(self):
        for spec, dialog, vm, calls in self.each():
            self.open_dialog(spec, dialog)
            self.wait_until(lambda: dialog.property("opened"), "dialog opened")
            self.type_reason(spec)
            self.assertTrue(self.item(spec["confirm"]).property("enabled"))
            self.click(spec["confirm"])
            self.wait_until(lambda: calls, f"{spec['name']}: the button did not submit")
            self.check(spec, self.settled_calls(calls))

    def test_invoice_reversal_never_submits_on_enter_without_a_confirmed_preview(self):
        """reverseInvoiceDialog: Enter is gated by the same enabled state as the button, which needs the loaded preview and canReverse."""
        spec = next(s for s in SPECS if s["name"] == "invoice")
        for label, preview in (("preview not loaded", {}), ("service says it cannot be reversed", {**INVOICE_PREVIEW, "can_reverse": False}),
                               ("preview of another invoice", {**INVOICE_PREVIEW, "id": "10"})):
            with self.subTest(case=label):
                dialog, vm, calls = self.prepare(spec)
                vm._reversal = dict(preview)
                vm.changed.emit()
                self.open_dialog(spec, dialog)
                self.wait_until(lambda: dialog.property("opened"), "dialog opened")
                self.type_reason(spec)
                self.assertFalse(self.item(spec["confirm"]).property("enabled"))
                QTest.keyClick(self.root, Qt.Key_Return)
                self.assertEqual(self.settled_calls(calls), [])
                self.assertTrue(dialog.property("visible"))
                self.close_dialog(dialog, vm, spec)

    def test_a_dialog_opened_for_a_second_row_starts_with_an_empty_reason_and_focus(self):
        for spec, dialog, vm, calls in self.each():
            self.open_dialog(spec, dialog)
            self.wait_until(lambda: dialog.property("opened"), "dialog opened")
            self.type_reason(spec)
            QMetaObject.invokeMethod(dialog, "close")
            self.wait_until(lambda: not dialog.property("visible"), "dialog closed")
            self.open_dialog(spec, dialog)
            self.assertEqual(self.item(spec["field"]).property("text"), "")
            self.assertEqual(self.focus_name(), spec["field"])
            self.assertFalse(self.item(spec["confirm"]).property("enabled"))
            self.assertEqual(calls, [])


# The base class carries the invoice-page tests: only this module's own tests are collected here.
for _inherited in [n for n in dir(harness.InvoicePageTests) if n.startswith("test_")]:
    setattr(ReasonDialogTests, _inherited, None)


if __name__ == "__main__":
    unittest.main()
