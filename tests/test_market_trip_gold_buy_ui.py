"""Cash gold purchase (054, «شراء ذهب نقدي») on the real Main.qml with real mouse clicks at the three target sizes. The services are the real ones
over the in-memory database (tests/market_trip_fakes.py). NOT real-MySQL tests."""
from decimal import Decimal as D
import unittest

from PySide6.QtTest import QTest

from tests import test_invoice_page_qml as harness
from tests import test_market_trip_ui as base

PRICES = {21: D("6000.0000"), 18: D("5142.8571"), 24: D("7000.0000")}


class GoldBuyUiTests(base.MarketTripHeatPageTests):
    def setUp(self):
        super().setUp()
        self.tvm._service._price_reference = lambda: dict(PRICES)

    # ------------------------------------------------------------ helpers
    def plain(self, text):
        return text.replace("⁦", "").replace("⁩", "")

    def cash_trip(self, cash="20000"):
        self.open_page()
        self.new_trip(cash)
        self.wait_until(lambda: self.tvm.detail.get("buyAvailable"), "purchase available")

    def open_buy(self):
        self.reveal(self.item("custodyGoldBuyButton"))
        self.click("custodyGoldBuyButton")
        self.wait_until(lambda: self.item("tripGoldBuyDialog").property("opened"), "purchase dialog")
        self.wait_until(lambda: "buyers" in self.tvm._context and self.tvm._reference_prices, "context and price reference loaded")
        QTest.qWait(120)

    def fill_line(self, weight="5", price="30000", karat_index=2, index=0):
        self.pick(f"custodyGoldBuyKarat_{index}", karat_index)                                  # entries: placeholder, 24K, 21K, 18K
        self.type_in(f"custodyGoldBuyWeight_{index}", weight)
        self.type_in(f"custodyGoldBuyPrice_{index}", price)

    def gold_row(self, karat=21, designation="LOGO"):
        custody = self.tvm._service.trip_detail(self.tvm.selectedTripId)["custody"]
        return next((g for g in custody["gold"] if g["karat_value"] == karat and g["designation"] == designation), None)

    def custody_cash(self):
        return self.tvm._service.trip_detail(self.tvm.selectedTripId)["custody"]["cash"]

    # ------------------------------------------------------------ tests
    def test_buying_with_real_clicks_at_1080_spends_custody_cash_and_adds_the_gold_only(self):
        self.root.setWidth(1080)
        self.root.setHeight(680)
        self.cash_trip("20000")
        scrap, cash_moves, strips, safe = len(self.db.scrap), len(self.db.cash_moves), self.counts(), self.db.cash()
        self.assertTrue(self.item("custodyGoldBuyButton").property("enabled"))
        self.open_buy()
        for name in ("custodyGoldBuyVendorCombo", "custodyGoldBuyNameField", "custodyGoldBuyDateField", "custodyGoldBuyKarat_0",
                     "custodyGoldBuyDesignation_0", "custodyGoldBuyWeight_0", "custodyGoldBuyPrice_0", "confirmCustodyGoldBuyButton",
                     "cancelCustodyGoldBuyButton"):
            self.assert_inside_window(self.item(name))
        self.assertEqual(self.item("custodyGoldBuyDesignation_0").property("currentIndex"), 1)         # «لوجو» is the default destination
        self.assertFalse(self.item("confirmCustodyGoldBuyButton").property("enabled"))                  # nothing chosen yet
        self.fill_line("5", "30000")
        self.assertFalse(self.item("confirmCustodyGoldBuyButton").property("enabled"))                  # the vendor is mandatory
        self.type_in("custodyGoldBuyNameField", "تاجر السوق")
        self.assertIn("20,000.00", self.plain(self.text("custodyGoldBuyAvailable")))
        self.assertIsNotNone(self.visible_item("custodyGoldBuyPreviewError"))                          # 30,000 is more than the 20,000 of cash
        self.assertFalse(self.item("confirmCustodyGoldBuyButton").property("enabled"))
        self.type_replace("custodyGoldBuyPrice_0", "12000")
        self.assertIsNone(self.visible_item("custodyGoldBuyPreviewError"))
        self.assertIsNotNone(self.visible_item("custodyGoldBuyWarning"))                                 # 12,000 vs the 30,000 reference
        self.type_replace("custodyGoldBuyPrice_0", "20000")
        self.assertIsNone(self.visible_item("custodyGoldBuyPreviewError"))
        self.assertTrue(self.item("confirmCustodyGoldBuyButton").property("enabled"))
        self.assertIn("20,000.00", self.plain(self.text("custodyGoldBuyTotal")))
        self.assertIsNotNone(self.visible_item("custodyGoldBuyWarning"))                                 # still -33.33 % from the 30,000 reference
        self.click("confirmCustodyGoldBuyButton")
        self.wait_until(lambda: self.tvm.errorMessage != "", "price refusal")
        QTest.qWait(100)
        self.assertEqual((self.tvm.errorCode, len(self.db.gold_buys)), ("PRICE_DEVIATION", 0))
        self.assertIsNone(self.visible_item("confirmCustodyGoldBuyButton"))
        self.click("acknowledgeCustodyGoldBuyPriceButton")
        self.wait_until(lambda: len(self.db.gold_buys) == 1, "purchase posted")
        self.closed("tripGoldBuyDialog")
        self.wait_idle()
        self.assertEqual((len(self.db.scrap), len(self.db.cash_moves), self.db.cash()), (scrap, cash_moves, safe))   # no Scrap, no Main Safe / bank movement
        self.assertEqual(self.counts(), strips)
        self.assertEqual(self.gold_row()["outstanding"], D("5.000"))
        self.assertEqual((self.custody_cash()["spent"], self.custody_cash()["outstanding"]), (D("20000.00"), D("0.00")))
        head = next(iter(self.db.gold_buys.values()))
        self.assertEqual((head["vendor_kind"], head["vendor_name"], head["price_warning_acknowledged"]), ("THIRD_PARTY", "تاجر السوق", 1))
        what = self.plain(" ".join(self.visible_texts("entryWhat")))
        self.assertIn("تاجر السوق", what)
        self.assertIn("5.000 جرام عيار 21 (لوجو)", what)
        self.assertEqual((self.db.sup_gold, self.db.sm_making, self.db.settlements), ([], [], {}))
        self.assertEqual(self.warnings, [])

    def type_replace(self, name, text):
        from PySide6.QtCore import Qt
        self.click(name)
        QTest.keyClick(self.root, Qt.Key_A, Qt.ControlModifier)
        self.type_text(text)
        QTest.qWait(80)

    def test_the_vendor_may_be_a_registered_party_and_a_typed_name_can_be_registered_as_a_new_party(self):
        self.cash_trip("20000")
        self.open_buy()
        db = self.db

        class Registry:
            def create_supplier(self, name, karat_id=None, **kwargs):
                db.suppliers[99] = {"id": 99, "name": name, "is_active": 1, "is_supplier": True, "is_intermediary": False, "karat_id": int(karat_id)}
                return 99
        self.tvm._service._vendor_registry = Registry
        self.fill_line("1", "6000")
        self.type_in("custodyGoldBuyNameField", "بائع جديد")
        self.assertIsNotNone(self.visible_item("registerGoldBuyVendorButton"))
        self.assertFalse(self.item("registerGoldBuyVendorButton").property("enabled"))                  # the account karat must be named
        self.pick("custodyGoldBuyRegisterKaratCombo", 2)
        self.assertTrue(self.item("registerGoldBuyVendorButton").property("enabled"))
        self.click("registerGoldBuyVendorButton")
        self.wait_until(lambda: 99 in db.suppliers and self.item("custodyGoldBuyVendorCombo").property("currentIndex") > 0, "the new party is selected")
        self.assertIsNone(self.visible_item("custodyGoldBuyNameField"))                                # a registered vendor needs no typed name
        self.assertTrue(self.item("confirmCustodyGoldBuyButton").property("enabled"))
        self.click("confirmCustodyGoldBuyButton")
        self.wait_until(lambda: len(self.db.gold_buys) == 1, "purchase posted")
        head = next(iter(self.db.gold_buys.values()))
        self.assertEqual((head["vendor_kind"], head["vendor_party_id"], head["vendor_name"]), ("REGISTERED_PARTY", 99, "بائع جديد"))
        self.assertEqual((self.db.sup_gold, self.db.sm_making), ([], []))                              # the supplier's payables are untouched

    def test_the_dialog_fits_every_target_size(self):
        self.cash_trip("20000")
        for width, height in harness.SIZES:
            with self.subTest(size=(width, height)):
                self.root.setWidth(width)
                self.root.setHeight(height)
                QTest.qWait(150)
                self.open_buy()
                self.fill_line("1", "6000")
                self.type_in("custodyGoldBuyNameField", "بائع")
                for name in ("custodyGoldBuyVendorCombo", "custodyGoldBuyDateField", "custodyGoldBuyKarat_0", "custodyGoldBuyWeight_0",
                             "custodyGoldBuyPrice_0", "custodyGoldBuyTotal", "confirmCustodyGoldBuyButton", "cancelCustodyGoldBuyButton"):
                    self.assert_inside_window(self.item(name))
                dialog = self.item("tripGoldBuyDialog")
                self.assertLessEqual(dialog.property("height"), height)
                self.assertLessEqual(dialog.property("width"), width)
                dialog.close()
                self.closed("tripGoldBuyDialog")
        self.assertEqual(self.warnings, [])

    def test_the_reversal_from_the_archive_restores_the_cash_and_removes_the_gold(self):
        self.cash_trip("20000")
        self.open_buy()
        self.fill_line("5", "30000")
        self.type_replace("custodyGoldBuyPrice_0", "15000")
        self.type_in("custodyGoldBuyNameField", "تاجر السوق")
        self.click("confirmCustodyGoldBuyButton")
        self.wait_until(lambda: self.tvm.errorMessage != "", "price refusal")
        self.click("acknowledgeCustodyGoldBuyPriceButton")
        self.wait_until(lambda: len(self.db.gold_buys) == 1, "purchase posted")
        self.closed("tripGoldBuyDialog")
        self.wait_idle()
        self.open_archive()
        buttons = []

        def collect(node):
            if node.objectName() == "reverseEntryButton" and node.property("visible"):
                buttons.append(node)
            for child in self.child_items(node):
                collect(child)
        collect(self.root.contentItem())
        self.assertEqual(len(buttons), 2)                                                               # the issue and the purchase
        self.reveal(buttons[0])
        self.assert_inside_window(buttons[0])
        self.click(buttons[0])
        self.wait_until(lambda: self.item("tripReasonDialog").property("opened"), "reversal dialog")
        self.assertFalse(self.item("confirmTripReasonButton").property("enabled"))
        self.type_in("tripReasonField", "خطأ في الشراء")
        self.click("confirmTripReasonButton")
        self.wait_until(lambda: len(self.db.corrections) == 1, "reversed")
        self.closed("tripReasonDialog")
        self.wait_idle()
        self.assertIsNone(self.gold_row())
        self.assertEqual((self.custody_cash()["spent"], self.custody_cash()["outstanding"]), (D("0.00"), D("20000.00")))
        self.assertIn("معكوسة بالعملية RV000001", " ".join(self.visible_texts("entryState")))
        self.assertEqual(self.warnings, [])


for _name in [n for n in dir(base.MarketTripHeatPageTests) if n.startswith("test_") and n not in vars(GoldBuyUiTests)]:
    setattr(GoldBuyUiTests, _name, None)                    # the inherited UI tests run in their own modules
if __name__ == "__main__":
    unittest.main()
