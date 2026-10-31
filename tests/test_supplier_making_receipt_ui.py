"""Supplier making RECEIPT dialog («استلام مصنعيات من المورد») on the real Main.qml with real clicks at the three target sizes."""
from decimal import Decimal as D
import unittest

from PySide6.QtTest import QTest

from tests import test_supplier_payment_ui as base


class SupplierReceiptPageTests(base.SupplierPaymentPageTests):
    def open_receive(self):
        self.click("receiveMakingButton")
        self.wait_until(lambda: self.item("receiveMakingDialog").property("opened"), "receipt dialog")
        self.wait_until(lambda: self.svm.payContext.get("supplierId"), "context")
        QTest.qWait(80)

    def test_cash_receipt_goes_to_the_main_safe_with_real_clicks_at_every_size(self):
        for width, height in ((1440, 900), (1280, 800), (1080, 680)):
            self.root.setWidth(width)
            self.root.setHeight(height)
            self.open_supplier()
            self.assert_inside_window(self.item("receiveMakingButton"))
            self.open_receive()
            self.assertEqual(self.text("recvCurrentPosition"), "علينا للمورد 5,000 ج مصنعيات")
            self.assertFalse(self.item("confirmReceiveMakingButton").property("enabled"))      # no amount yet
            self.type_in("recvAmountField", "1200")
            self.assertEqual(self.text("recvResultPosition"), "علينا للمورد 6,200 ج مصنعيات")
            self.assertTrue(self.item("confirmReceiveMakingButton").property("enabled"))        # cash needs no account choice
            for name in ("confirmReceiveMakingButton", "cancelReceiveMakingButton", "recvAmountField", "recvNotesField"):
                self.assert_inside_window(self.item(name))
            self.click("cancelReceiveMakingButton")
            self.closed("receiveMakingDialog")
        self.open_receive()
        self.type_in("recvAmountField", "1200")
        self.click("confirmReceiveMakingButton")
        self.wait_until(lambda: len(self.db.payments) == 1, "receipt posted")
        self.closed("receiveMakingDialog")
        self.wait_idle()
        (payment,) = self.db.payments.values()
        self.assertEqual((payment["direction"], payment["payment_method"], payment["amount"]), ("RECEIVE", "CASH", D("1200.00")))
        self.assertEqual([m["amount_delta"] for m in self.db.cash_moves], [D("1200.00")])
        self.assertEqual(self.making(), D("6200.00"))

    def test_bank_receipt_needs_the_account_to_be_chosen(self):
        self.open_supplier()
        self.open_receive()
        self.type_in("recvAmountField", "300")
        self.click("recvMethodBank")
        QTest.qWait(80)
        self.assertFalse(self.item("confirmReceiveMakingButton").property("enabled"))
        self.pick("recvBankCombo", 1)
        self.assertTrue(self.item("confirmReceiveMakingButton").property("enabled"))
        self.click("confirmReceiveMakingButton")
        self.wait_until(lambda: len(self.db.payments) == 1, "receipt posted")
        self.assertEqual([(m["account_id"], m["amount_delta"]) for m in self.db.bank_moves], [(5, D("300.00"))])


for _name in dir(base.SupplierPaymentPageTests):                                              # the inherited tests belong to their own modules
    if _name.startswith("test_") and _name not in SupplierReceiptPageTests.__dict__:
        setattr(SupplierReceiptPageTests, _name, None)

if __name__ == "__main__":
    unittest.main()
