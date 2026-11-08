"""Supplier account differences («تسوية فروق الذهب / المصنعيات») on the real Main.qml with real clicks at the three target sizes."""
from decimal import Decimal as D
import unittest

from PySide6.QtTest import QTest

from tests import test_supplier_payment_ui as base


class AccountAdjustPageTests(base.SupplierPaymentPageTests):
    def gold(self):
        return sum((m["weight_delta_grams"] for m in self.db.gold), D("0.000"))

    def open_dialog(self, button):
        self.click(button)
        self.wait_until(lambda: self.item("accountAdjustDialog").property("opened"), "adjust dialog")
        QTest.qWait(80)

    def test_buttons_are_hidden_without_the_owner_right(self):
        self.open_supplier()
        self.assertFalse(self.item("adjustGoldDifferenceButton").property("visible"))
        self.assertFalse(self.item("adjustMakingDifferenceButton").property("visible"))

    def test_gold_and_making_differences_post_with_real_clicks_at_every_size(self):
        self.rights("SUPPLIER_ACCOUNT_ADJUST")
        for width, height in ((1440, 900), (1280, 800), (1080, 680)):
            self.root.setWidth(width)
            self.root.setHeight(height)
            self.open_supplier()
            for name in ("adjustGoldDifferenceButton", "adjustMakingDifferenceButton"):
                self.assert_inside_window(self.item(name))
            self.open_dialog("adjustGoldDifferenceButton")
            self.assertFalse(self.item("confirmAccountAdjustButton").property("enabled"))          # nothing typed yet
            for name in ("confirmAccountAdjustButton", "cancelAccountAdjustButton", "accountAdjustAmountField", "accountAdjustReasonField"):
                self.assert_inside_window(self.item(name))
            self.click("cancelAccountAdjustButton")
            self.closed("accountAdjustDialog")
        self.open_supplier()
        gold, making = self.gold(), self.making()
        self.open_dialog("adjustGoldDifferenceButton")
        self.type_in("accountAdjustAmountField", "1.5")
        self.type_in("accountAdjustReasonField", "فرق جرد")
        self.click("confirmAccountAdjustButton")
        self.wait_until(lambda: len(self.db.account_adjustments) == 1, "gold difference posted")
        self.closed("accountAdjustDialog")
        self.wait_idle()
        self.assertEqual(self.gold(), gold + D("1.500"))
        self.open_dialog("adjustMakingDifferenceButton")
        self.assertEqual((self.item("accountAdjustAddButton").property("text"), self.item("accountAdjustDeductButton").property("text")), ("علينا", "لنا"))
        self.assertIn("علينا:", self.item("accountAdjustHint").property("text"))
        self.click("accountAdjustDeductButton")
        self.assertIn("لنا:", self.item("accountAdjustHint").property("text"))
        self.type_in("accountAdjustAmountField", "75")
        self.type_in("accountAdjustReasonField", "فرق مصنعيات")
        self.click("confirmAccountAdjustButton")
        self.wait_until(lambda: len(self.db.account_adjustments) == 2, "making difference posted")
        self.closed("accountAdjustDialog")
        self.wait_idle()
        self.assertEqual(self.making(), making - D("75.00"))
        self.assertEqual(self.db.cash_moves, [])


for _name in dir(base.SupplierPaymentPageTests):                                              # the inherited tests belong to their own modules
    if _name.startswith("test_") and _name not in AccountAdjustPageTests.__dict__:
        setattr(AccountAdjustPageTests, _name, None)

if __name__ == "__main__":
    unittest.main()
