"""Sale of custody gold (053, «بيع ذهب العهدة») on the real Main.qml with real mouse clicks at the three target sizes. The services are the real
ones over the in-memory database (tests/market_trip_fakes.py). NOT real-MySQL tests."""
from decimal import Decimal as D
import unittest

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest

from tests import test_invoice_page_qml as harness
from tests import test_market_trip_ui as base

PRICES = {21: D("6000.0000"), 24: D("7000.0000")}


class GoldSaleUiTests(base.MarketTripHeatPageTests):
    def setUp(self):
        super().setUp()
        self.tvm._service._price_reference = lambda: dict(PRICES)

    # ------------------------------------------------------------ helpers
    def plain(self, text):
        return text.replace("⁦", "").replace("⁩", "")

    def open_sale(self):
        self.reveal(self.item("custodySaleButton"))
        self.click("custodySaleButton")
        self.wait_until(lambda: self.item("tripGoldSaleDialog").property("opened"), "sale dialog")
        self.wait_until(lambda: "buyers" in self.tvm._context and self.tvm._reference_prices, "context and price reference loaded")
        QTest.qWait(120)

    def fill_sale(self, weight="4", price="24000", name="تاجر الصاغة", buyer=0):
        self.pick("custodyGoldSaleSourceCombo", 1)
        self.type_in("custodyGoldSaleWeightField", weight)
        self.type_in("custodyGoldSalePriceField", price)
        if buyer:
            self.pick("custodyGoldSaleBuyerCombo", buyer)
        else:
            self.type_in("custodyGoldSaleNameField", name)

    def outstanding_logo(self):
        custody = self.tvm._service.trip_detail(self.tvm.selectedTripId)["custody"]
        return next(g for g in custody["gold"] if g["designation"] == "LOGO")["outstanding"]

    def custody_cash(self):
        return self.tvm._service.trip_detail(self.tvm.selectedTripId)["custody"]["cash"]["outstanding"]

    # ------------------------------------------------------------ tests
    def test_selling_with_real_clicks_at_1080_moves_gold_out_and_cash_into_the_custody_only(self):
        self.root.setWidth(1080)
        self.root.setHeight(680)
        self.logo_trip()                                                                          # 10 g of 21K LOGO in a new custody, no cash
        scrap, cash_moves, strips = len(self.db.scrap), len(self.db.cash_moves), self.counts()
        self.assertTrue(self.item("custodySaleButton").property("enabled"))
        self.open_sale()
        for name in ("custodyGoldSaleSourceCombo", "custodyGoldSalePriceField", "custodyGoldSaleBuyerCombo", "custodyGoldSaleDateField",
                     "confirmCustodyGoldSaleButton", "cancelCustodyGoldSaleButton"):
            self.assert_inside_window(self.item(name))
        self.assertFalse(self.item("confirmCustodyGoldSaleButton").property("enabled"))          # nothing chosen yet
        self.fill_sale("4", "24000")
        self.assertIn("المتاح 10.000 جرام عيار 21", self.plain(self.text("custodyGoldSaleAvailable")))
        self.assertIn("24,000.00", self.plain(self.text("custodyGoldSaleTotal")))
        self.assertIn("المرجع", self.plain(self.text("custodyGoldSaleReference")))
        self.assertIsNone(self.visible_item("custodyGoldSaleWarning"))                            # exactly the reference: no warning
        self.assertTrue(self.item("confirmCustodyGoldSaleButton").property("enabled"))
        self.click("confirmCustodyGoldSaleButton")
        self.wait_until(lambda: len(self.db.gold_sales) == 1, "sale posted")
        self.closed("tripGoldSaleDialog")
        self.wait_idle()
        self.assertEqual((len(self.db.scrap), len(self.db.cash_moves)), (scrap, cash_moves))   # no Scrap, no Main Safe / bank movement
        self.assertEqual(self.counts(), strips)                                                   # no shop balance moved, so none reloaded
        self.assertEqual((self.outstanding_logo(), self.custody_cash()), (D("6.000"), D("24000.00")))
        sale = next(iter(self.db.gold_sales.values()))
        self.assertEqual((sale["buyer_kind"], sale["buyer_name"], sale["total_price"], sale["price_warning_acknowledged"]),
                         ("THIRD_PARTY", "تاجر الصاغة", D("24000.00"), 0))
        what = self.plain(" ".join(self.visible_texts("entryWhat")))
        self.assertIn("تاجر الصاغة", what)
        self.assertIn("4.000 جرام", what)
        self.assertIn("بيع", self.plain(" ".join(self.visible_texts("figureOutstanding") + [self.text("tripMessage")])))
        self.assertEqual(self.db.sup_gold, [])                                                    # no supplier ledger
        self.assertEqual(self.warnings, [])

    def test_a_price_far_from_the_reference_is_warned_then_posts_once_after_the_owner_confirms(self):
        self.logo_trip()
        self.open_sale()
        self.fill_sale("4", "10000")                                                              # reference 24,000 -> -58.33 %
        self.assertIsNotNone(self.visible_item("custodyGoldSaleWarning"))
        self.assertIn("58.33", self.plain(self.text("custodyGoldSaleWarning")))
        self.click("confirmCustodyGoldSaleButton")
        self.wait_until(lambda: self.tvm.errorMessage != "", "price refusal")
        QTest.qWait(100)
        self.assertEqual(len(self.db.gold_sales), 0)
        self.assertEqual(self.tvm.errorCode, "PRICE_DEVIATION")
        self.assertIsNone(self.visible_item("confirmCustodyGoldSaleButton"))                      # replaced by the explicit confirmation
        self.assertTrue(self.item("tripGoldSaleDialog").property("opened"))
        self.assertEqual(self.text("custodyGoldSalePriceField"), "10000")                         # every field kept
        self.click("acknowledgeCustodyGoldSalePriceButton")
        self.wait_until(lambda: len(self.db.gold_sales) == 1, "sale posted after the acknowledgement")
        self.closed("tripGoldSaleDialog")
        self.wait_idle()
        sale = next(iter(self.db.gold_sales.values()))
        self.assertEqual((sale["price_warning_acknowledged"], sale["deviation_percent"]), (1, D("-58.33")))
        self.assertEqual(len(self.db.gold_sales), 1)

    def test_buyers_inactive_parties_are_not_offered_and_a_third_party_needs_a_name(self):
        self.logo_trip()
        self.open_sale()
        self.assertEqual([b["id"] for b in self.tvm.detail["saleBuyers"]], ["10", "12"])          # the inactive supplier (11) is not offered
        self.pick("custodyGoldSaleSourceCombo", 1)
        self.type_in("custodyGoldSaleWeightField", "1")
        self.type_in("custodyGoldSalePriceField", "6000")
        self.assertFalse(self.item("confirmCustodyGoldSaleButton").property("enabled"))          # a third party needs a name
        self.assertIsNotNone(self.visible_item("custodyGoldSaleNameField"))
        self.type_in("custodyGoldSaleNameField", "مشتري")
        self.assertTrue(self.item("confirmCustodyGoldSaleButton").property("enabled"))
        self.pick("custodyGoldSaleBuyerCombo", 1)                                                 # a registered supplier: the name field disappears
        self.assertIsNone(self.visible_item("custodyGoldSaleNameField"))
        self.click("confirmCustodyGoldSaleButton")
        self.wait_until(lambda: len(self.db.gold_sales) == 1, "sale posted")
        sale = next(iter(self.db.gold_sales.values()))
        self.assertEqual((sale["buyer_kind"], sale["buyer_party_id"], sale["buyer_name"]), ("REGISTERED_PARTY", 10, "مورد نشط"))
        self.assertEqual((self.db.sup_gold, self.db.sm_making), ([], []))                         # the supplier's payables are untouched

    def test_a_weight_above_the_custody_or_an_invalid_price_is_explained_and_cannot_be_submitted(self):
        self.logo_trip()
        self.open_sale()
        self.pick("custodyGoldSaleSourceCombo", 1)
        self.type_in("custodyGoldSaleNameField", "مشتري")
        self.type_in("custodyGoldSalePriceField", "100")
        self.type_in("custodyGoldSaleWeightField", "10.001")
        self.assertIsNotNone(self.visible_item("custodyGoldSalePreviewError"))
        self.assertFalse(self.item("confirmCustodyGoldSaleButton").property("enabled"))
        for bad in ("abc", "0", "1.2345"):
            self.click("custodyGoldSaleWeightField")
            QTest.keyClick(self.root, Qt.Key_A, Qt.ControlModifier)
            self.type_text(bad)
            QTest.qWait(80)
            self.assertFalse(self.item("confirmCustodyGoldSaleButton").property("enabled"), bad)
        self.assertEqual(self.db.gold_sales, {})

    def test_the_dialog_fits_every_target_size(self):
        self.logo_trip()
        for width, height in harness.SIZES:
            with self.subTest(size=(width, height)):
                self.root.setWidth(width)
                self.root.setHeight(height)
                QTest.qWait(150)
                self.open_sale()
                self.fill_sale("4", "10000")
                for name in ("custodyGoldSaleSourceCombo", "custodyGoldSalePriceField", "custodyGoldSaleBuyerCombo", "custodyGoldSaleDateField",
                             "custodyGoldSaleTotal", "custodyGoldSaleWarning", "confirmCustodyGoldSaleButton", "cancelCustodyGoldSaleButton"):
                    self.assert_inside_window(self.item(name))
                dialog = self.item("tripGoldSaleDialog")
                self.assertLessEqual(dialog.property("height"), height)
                self.assertLessEqual(dialog.property("width"), width)
                dialog.close()
                self.closed("tripGoldSaleDialog")
        self.assertEqual(self.warnings, [])

    def test_the_reversal_from_the_archive_restores_the_gold_and_removes_the_proceeds(self):
        self.logo_trip()
        self.open_sale()
        self.fill_sale("4", "24000")
        self.click("confirmCustodyGoldSaleButton")
        self.wait_until(lambda: len(self.db.gold_sales) == 1, "sale posted")
        self.closed("tripGoldSaleDialog")
        self.wait_idle()
        self.open_archive()
        buttons = []

        def collect(node):
            if node.objectName() == "reverseEntryButton" and node.property("visible"):
                buttons.append(node)
            for child in self.child_items(node):
                collect(child)
        collect(self.root.contentItem())
        self.assertEqual(len(buttons), 2)                                                         # the issue and the sale
        self.reveal(buttons[-1])
        self.assert_inside_window(buttons[-1])
        self.click(buttons[-1])
        self.wait_until(lambda: self.item("tripReasonDialog").property("opened"), "reversal dialog")
        self.assertFalse(self.item("confirmTripReasonButton").property("enabled"))                # a reason is mandatory
        self.type_in("tripReasonField", "خطأ في الصفقة")
        self.click("confirmTripReasonButton")
        self.wait_until(lambda: len(self.db.corrections) == 1, "reversed")
        self.closed("tripReasonDialog")
        self.wait_idle()
        self.assertEqual((self.outstanding_logo(), self.custody_cash()), (D("10.000"), D("0.00")))
        self.assertIn("معكوسة بالعملية RV000001", " ".join(self.visible_texts("entryState")))
        self.assertEqual(self.warnings, [])


for _name in [n for n in dir(base.MarketTripHeatPageTests) if n.startswith("test_") and n not in vars(GoldSaleUiTests)]:
    setattr(GoldSaleUiTests, _name, None)                    # the inherited UI tests run in their own modules
if __name__ == "__main__":
    unittest.main()
