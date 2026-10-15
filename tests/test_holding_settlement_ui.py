"""Supplier gold settlement from a holding (050, «تسوية ذهب لمورد من رصيد المسوقجي») on the real Main.qml with real mouse clicks at the three target sizes.
The services are the real ones over the in-memory database of tests/market_trip_fakes.py; the supplier READ service is the small stub of
tests/test_intermediary_holdings_ui.py (the real SQL was exercised on the masgold_rehearsal sandbox). NOT real-MySQL tests."""
from decimal import Decimal as D
import unittest

from PySide6.QtCore import QObject
from PySide6.QtTest import QTest

from services.holding_settlement_service import HoldingSettlementService
from services.supplier_gold_settlement_service import SupplierGoldSettlementService
from tests import test_intermediary_holdings_ui as ui
from tests.market_trip_fakes import HoldingRepo, OWNER, SettlementRepo, TripRepo
from tests.test_market_trip_ui import MarketTripPageTests

TODAY = "2026-10-10"
RADIO = "holdingSettleDeclaration_"


class SettlementPageTests(ui.HoldingPartyPageTests):
    """Party page: the settlement dialog (lot and LOGO sources), its declarations, refusals, reversal and the three target sizes."""

    def setUp(self):
        super().setUp()
        session = self.controller.session
        self.settle_service = HoldingSettlementService(session, transaction_factory=self.db.transaction, repository=TripRepo, holdings=HoldingRepo,
                                                       parties=SettlementRepo, authorization=self.authorization, corrections=self.corrections)
        self.svm._holding_settlements = self.settle_service
        self.svm._settlements = SupplierGoldSettlementService(session, transaction_factory=self.db.transaction, repository=SettlementRepo,
                                                              authorization=self.authorization, corrections=self.corrections)
        self.db.suppliers[21] = {"id": 21, "name": "مورد ومسوقجي", "is_active": 1, "is_supplier": True, "is_intermediary": True, "karat_id": 1}

    # ------------------------------------------------------------ helpers
    def pick(self, name, index):
        MarketTripPageTests.pick(self, name, index)

    def untouched(self):
        return (len(self.db.scrap), self.db.cash(), len(self.db.shop_moves), len(self.db.sm_making), len(self.db.lot_moves), len(self.db.entries))

    def exists(self, name):
        QTest.qWait(60)
        return self.root.findChild(QObject, name) is not None or self.visual_item(name) is not None

    def open_settle(self, button="holdingSettleButton"):
        self.click(button)
        self.wait_until(lambda: self.item("holdingSettleDialog").property("opened"), "settlement dialog")
        self.wait_until(lambda: self.svm.holdingSettlementContext.get("poolId"), "settlement context")
        QTest.qWait(150)

    def recipient_index(self, supplier_id):
        return [r["id"] for r in self.svm.holdingSettlementContext["recipients"]].index(str(supplier_id)) + 1

    def fill(self, supplier, grams):
        self.pick("holdingSettleSupplier", self.recipient_index(supplier))
        self.type_replace("holdingSettleQty", grams)
        QTest.qWait(120)

    def lot_balance(self):
        return HoldingRepo(self.db).pool_balance(max(self.db.holding_pools))

    # ------------------------------------------------------------ tests
    def test_lot_settlement_with_real_clicks_at_1080_posts_one_sg_and_refreshes_the_party(self):
        self.root.setWidth(1080)
        self.root.setHeight(680)
        trip, lot, done = self.hand_over()
        self.tvm.selectTrip(trip)
        self.open_party()
        before, reloads = self.untouched(), self.stub.reloads
        self.assert_inside_window(self.item("holdingSettleButton"))
        self.open_settle()
        for name in ("holdingSettleSupplier", "holdingSettleQty", "holdingSettleDate", "confirmHoldingSettleButton", "cancelHoldingSettleButton"):
            self.assert_inside_window(self.item(name))
        self.assertIn("875", self.text("holdingSettleSource"))
        self.assertIn("10.000", self.text("holdingSettleSource"))                             # the ORIGINAL full-lot weight
        self.assertIn("10.000", self.text("holdingSettleAvailable"))
        self.assertFalse(self.item("confirmHoldingSettleButton").property("enabled"))
        self.fill(11, "4")                                                                    # an inactive 18K supplier
        self.assertIn("4.667", self.text("holdingSettleCredited"))
        self.assertIn("4.666667", self.text("holdingSettleAnalytical"))
        self.assertIn("6.000", self.text("holdingSettleRemaining"))
        self.assertTrue(self.exists(RADIO + "GRAM_ENTITLEMENT") and self.exists(RADIO + "PHYSICALLY_DIVIDED"))
        self.assertFalse(self.exists(RADIO + "WHOLE_ORIGINAL_BAR"))                           # a partial quantity is never offered as the whole bar
        self.assertFalse(self.item("confirmHoldingSettleButton").property("enabled"))        # no choice is preselected
        self.click(RADIO + "GRAM_ENTITLEMENT")
        self.assertTrue(self.item("confirmHoldingSettleButton").property("enabled"))
        self.click("confirmHoldingSettleButton")
        self.wait_until(lambda: len(self.db.settlements) == 1, "settlement posted")
        self.closed("holdingSettleDialog")
        self.wait_idle()
        self.assertEqual(self.untouched(), before)                                            # no custody, Scrap, cash, making or shop-lot movement
        header = next(iter(self.db.settlements.values()))
        self.assertEqual((header["source_kind"], header["delivered_weight_grams"], header["physical_declaration"], header["supplier_id"]),
                         ("HOLDING_LOT", D("4.000"), "GRAM_ENTITLEMENT", 11))
        self.assertEqual(self.lot_balance(), D("6.000"))
        self.wait_until(lambda: self.stub.reloads > reloads, "party reloaded after the commit")
        self.wait_until(lambda: "6.000" in self.text("holdingFigures"), "remaining grams shown")
        self.assertIn("SG000001", " ".join(self.visible_texts("holdingSettlementRow")))
        self.assertFalse(self.item("holdingReturnButton").property("enabled"))               # allocation history blocks the whole-bar return
        self.assertIn("تسوية ذهب لمورد لم تُعكس", self.text("holdingReason"))
        self.assertNotIn("SG000001", self.text("holdingReason"))
        self.open_details()
        self.assertIn("SG000001", self.text("holdingRefs"))                                     # ... its reference is under Details
        self.assertIn("لا يُعكس تسليمها حتى بعد عكس التسوية", self.text("holdingRefs"))
        self.assertEqual(self.warnings, [])

    def test_the_full_original_quantity_offers_the_three_declarations_and_none_is_preselected(self):
        trip, lot, done = self.hand_over()
        self.open_party()
        self.open_settle()
        self.fill(10, "10")
        for code in ("WHOLE_ORIGINAL_BAR", "GRAM_ENTITLEMENT", "PHYSICALLY_DIVIDED"):
            self.assertTrue(self.exists(RADIO + code), code)
        options = self.svm.previewHoldingSettlement("10", "10")["options"]
        self.assertEqual(options[0], {"code": "WHOLE_ORIGINAL_BAR", "text": "تم تسليم نفس السبكة كاملة دون تجزئة"})      # the settlement wording, not the return's
        self.assertEqual(self.svm.holdingSettleConfirmationText, "تم تسليم نفس السبكة كاملة دون تجزئة")
        self.assertNotEqual(self.svm.holdingConfirmationText, self.svm.holdingSettleConfirmationText)                      # «تم استلام …» stays the normal return's
        self.assertFalse(self.item("confirmHoldingSettleButton").property("enabled"))
        self.click(RADIO + "WHOLE_ORIGINAL_BAR")
        self.click("confirmHoldingSettleButton")
        self.wait_until(lambda: len(self.db.settlements) == 1, "whole delivery posted")
        self.closed("holdingSettleDialog")
        self.assertEqual(next(iter(self.db.settlements.values()))["physical_declaration"], "WHOLE_ORIGINAL_BAR")
        self.assertEqual(self.lot_balance(), D("0.000"))

    def test_after_a_partial_allocation_the_remaining_balance_is_not_offered_as_a_whole_bar(self):
        trip, lot, done = self.hand_over()
        self.settle_service.settle("LOT", str(max(self.db.holding_pools)), "10", "4", "GRAM_ENTITLEMENT", False, TODAY, "", "", self.key())
        self.open_party()
        self.open_settle()
        self.fill(10, "6")                                                                    # the full REMAINING balance
        self.assertTrue(self.exists(RADIO + "GRAM_ENTITLEMENT") and self.exists(RADIO + "PHYSICALLY_DIVIDED"))
        self.assertFalse(self.exists(RADIO + "WHOLE_ORIGINAL_BAR"))

    def test_a_self_settlement_needs_the_explicit_confirmation(self):
        self.db.suppliers[21]["is_intermediary"] = True
        trip, lot, done = self.hand_over(party="21")
        self.open_party("21")
        self.open_settle()
        self.fill(21, "3")
        self.click(RADIO + "GRAM_ENTITLEMENT")
        self.assertTrue(self.item("holdingSettleSelfConfirm").property("visible"))
        self.assertFalse(self.item("confirmHoldingSettleButton").property("enabled"))
        self.click("holdingSettleSelfConfirm")
        self.assertTrue(self.item("confirmHoldingSettleButton").property("enabled"))
        self.click("confirmHoldingSettleButton")
        self.wait_until(lambda: len(self.db.settlements) == 1, "self settlement posted")
        self.assertEqual(next(iter(self.db.settlements.values()))["self_settlement_confirmed"], 1)

    def test_a_refusal_after_opening_keeps_the_dialog_and_every_field_then_succeeds(self):
        trip, lot, done = self.hand_over()
        pool = str(max(self.db.holding_pools))
        self.open_party()
        self.open_settle()
        self.fill(10, "4")
        self.click(RADIO + "GRAM_ENTITLEMENT")
        self.type_in("holdingSettleReference", "مرجع")
        self.settle_service.settle("LOT", pool, "10", "8", "GRAM_ENTITLEMENT", False, TODAY, "", "", self.key())      # another employee consumes 8 g meanwhile
        self.click("confirmHoldingSettleButton")
        self.wait_idle()
        self.assertTrue(self.item("holdingSettleDialog").property("opened"))
        self.assertIn("2.000", self.text("holdingSettleError"))
        self.assertEqual((self.text("holdingSettleQty"), self.text("holdingSettleReference")), ("4", "مرجع"))
        self.assertTrue(self.item(RADIO + "GRAM_ENTITLEMENT").property("checked"))
        self.assertEqual(len(self.db.settlements), 1)
        self.type_replace("holdingSettleQty", "2")
        self.click("confirmHoldingSettleButton")
        self.wait_until(lambda: len(self.db.settlements) == 2, "retry posted")
        self.closed("holdingSettleDialog")

    def test_a_second_submission_while_the_first_is_running_is_ignored(self):
        trip, lot, done = self.hand_over()
        self.open_party()
        args = ("LOT", str(max(self.db.holding_pools)), "10", "4", "GRAM_ENTITLEMENT", False, TODAY, "", "", "77777777-7777-4777-8777-777777777777")
        self.svm.postHoldingSettlement(*args)
        self.svm.postHoldingSettlement(*args)
        self.wait_idle()
        self.assertEqual(len(self.db.settlements), 1)

    def test_logo_settlement_has_no_declaration_and_updates_the_remaining_grams(self):
        self.root.setWidth(1080)
        self.root.setHeight(680)
        trip, done = self.logo_hand_over("8")
        self.open_party()
        self.assert_inside_window(self.item("logoSettleButton"))
        self.open_settle("logoSettleButton")
        self.fill(11, "1")
        self.assertIn("1.167", self.text("holdingSettleCredited"))
        self.assertFalse(self.exists(RADIO + "GRAM_ENTITLEMENT"))
        self.assertTrue(self.item("confirmHoldingSettleButton").property("enabled"))
        self.click("confirmHoldingSettleButton")
        self.wait_until(lambda: len(self.db.settlements) == 1, "logo settlement posted")
        self.closed("holdingSettleDialog")
        self.wait_idle()
        self.assertEqual(self.holding.party_holdings(ui.PARTY)["logo_pools"][0]["balance"], D("7.000"))
        self.wait_until(lambda: "7.000" in self.text("logoFigures"), "remaining LOGO grams shown")
        self.assertEqual(self.warnings, [])

    def test_the_settlement_dialog_is_fully_visible_at_the_three_target_sizes(self):
        self.hand_over()
        self.open_party()
        for width, height in ((1440, 900), (1280, 800), (1080, 680)):
            with self.subTest(size=f"{width}x{height}"):
                self.root.setWidth(width)
                self.root.setHeight(height)
                QTest.qWait(200)
                self.assert_inside_window(self.item("holdingSettleButton"))
                self.open_settle()
                self.fill(10, "10")
                for name in ("holdingSettleSupplier", "holdingSettleQty", "holdingSettleDate", "confirmHoldingSettleButton", "cancelHoldingSettleButton"):
                    self.assert_inside_window(self.item(name))
                self.click("cancelHoldingSettleButton")
                self.closed("holdingSettleDialog")

    def test_the_holder_page_reverses_through_the_one_canonical_correction_and_says_where_the_grams_go(self):
        trip, lot, done = self.hand_over()
        pool = str(max(self.db.holding_pools))
        sg = self.settle_service.settle("LOT", pool, "10", "4", "GRAM_ENTITLEMENT", False, TODAY, "", "", self.key())
        self.open_party()
        self.assertEqual(len(self.visible_texts("holdingSettlementRow")), 1)
        self.click("holdingSettlementReverseButton")
        self.wait_until(lambda: self.item("reverseSettlementDialog").property("opened"), "reverse dialog")
        self.assertIn("رصيد المسوقجي", self.text("reverseSettlementNote"))
        self.assertNotIn("كسر المحل", self.text("reverseSettlementNote"))
        self.type_in("reverseSettlementReason", "خطأ في المورد")
        self.click("confirmReverseSettlementButton")
        self.wait_until(lambda: any(c["source_type"] == "SUPPLIER_GOLD_SETTLEMENT" for c in self.db.corrections), "reversed")
        self.closed("reverseSettlementDialog")
        self.wait_idle()
        self.assertEqual(self.lot_balance(), D("10.000"))
        self.assertIn("رصيد المسوقجي", self.svm.message)
        self.assertNotIn("كسر المحل", self.svm.message)
        self.wait_until(lambda: any("معكوسة" in t for t in self.visible_texts("holdingSettlementRow")), "reversed state listed")
        self.assertTrue(self.item("holdingReturnButton").property("enabled"))                # restored entitlement permits the distinct intact-return path
        self.click("holdingReturnButton")
        self.wait_until(lambda: self.item("holdingReturnDialog").property("opened"), "whole-return review dialog")
        self.assertIn("SG000001", self.text("holdingReturnEventReview"))
        self.assertTrue(self.item("holdingIntactConfirm").property("visible"))               # a single confirmation covers the original bar
        self.assertFalse(self.exists("holdingPhysicalEventsConfirm"))
        self.click("cancelHoldingReturnButton")
        self.closed("holdingReturnDialog")
        self.assertEqual(self.warnings, [])

    def test_the_buttons_follow_the_rights_and_the_service_still_refuses_without_them(self):
        trip, lot, done = self.hand_over()
        self.open_party()
        self.assertTrue(self.svm.canSettleFromHolding)
        self.grant(revoke=("SUPPLIER_GOLD_SETTLE",))
        self.svm.changed.emit()
        QTest.qWait(80)
        self.assertFalse(self.svm.canSettleFromHolding)
        self.assertFalse(self.item("holdingSettleButton").property("visible"))
        self.svm.postHoldingSettlement("LOT", str(max(self.db.holding_pools)), "10", "4", "GRAM_ENTITLEMENT", False, TODAY, "", "", self.key())
        self.wait_idle()
        self.assertEqual(self.db.settlements, {})


def load_tests(loader, tests, pattern):
    """Only the tests defined here: the page class inherits the whole harness (and the 048 / 049 party tests), which other modules already run."""
    suite = unittest.TestSuite()
    suite.addTests(SettlementPageTests(name) for name in loader.getTestCaseNames(SettlementPageTests) if name in SettlementPageTests.__dict__)
    return suite


if __name__ == "__main__":
    unittest.main()
