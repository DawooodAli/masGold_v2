"""موضع الذهب inside مراجعات المدير (real Main.qml, real clicks, the database blocked): the consolidated 21K view, its details and refresh,
no supplier figure anywhere, a user without the full right set sees nothing, and the simplified review page (empty sections hidden)."""
import os
from decimal import Decimal as D

from PySide6.QtTest import QTest

from services.gold_position_service import build_position
from tests import test_invoice_page_qml as harness
from tests.test_gold_position_service import custody, pool, scrap_row

RIGHTS = {"MANAGEMENT_REVIEW", "MARKET_TRIP_MANAGE", "INTERMEDIARY_HOLDING_VIEW"}


def sample_position():
    return build_position(
        scrap=[scrap_row(21, "10.000", held="2.000"), scrap_row(18, "6.300")], shop_lots=[], receipt_stock=[
            {"piece_id": 1, "receipt_id": 1, "party_name": "حسن", "stock_grams": D("3.000")}],
        inventory=[{"karat_value": 21, "weight_grams": "30.000"}], returned_pending=[],
        trips=[{"id": 2, "code": "MT000002", "status": "OPEN", "custody": custody(gold=[(21, "LOGO", "5.000")])}],
        pools=[pool(3, 11, "حسن", "4.000", "900.0")], logo_pools=[])


class Service:
    def __init__(self):
        self.calls = 0

    def position(self, *, _expected_actor=None):
        self.calls += 1
        return sample_position()


class GoldPositionPageTests(harness.InvoicePageTests):
    def open_reviews(self, rights=RIGHTS):
        actor = self.controller.session.current_actor()
        object.__setattr__(actor, "permissions", frozenset(actor.permissions | set(rights)))
        vm = self.controller.operational_viewmodels["reviewsVM"]
        self.vm, self.service = vm, Service()
        vm._gold_service = self.service
        vm._counts = {code: 0 for code in vm._counts}
        vm._on_reviews({}, "")
        vm._on_accounts([], "")
        vm.openReviews = lambda: None
        vm.changed.emit()
        self.root.setProperty("currentPage", 10)
        QTest.qWait(300)

    def visible_texts(self, name, parent=None):
        parent = parent or self.root.contentItem()
        found = [str(parent.property("text"))] if parent.objectName() == name and parent.property("visible") else []
        for child in self.child_items(parent):
            found += self.visible_texts(name, child)
        return found

    def load(self):
        self.vm.loadGoldPosition()
        self.wait_until(lambda: self.vm.goldPosition.get("summary"), "gold position loaded")
        QTest.qWait(150)

    def test_the_summary_in_21k_and_the_details_at_every_size(self):
        self.open_reviews()
        self.load()
        for width, height in harness.SIZES:
            self.root.setWidth(width)
            self.root.setHeight(height)
            QTest.qWait(120)
            for name in ("goldDetailsToggle", "goldRefreshButton"):
                self.assert_inside_window(self.visible_item(name))
        shop = D("10") + D("6.3") * 18 / 21 + D("30")
        custody_eq, inter = D("5"), D("4") * D("0.9") * 24 / 21
        total = (shop + custody_eq + inter).quantize(D("0.001"))
        self.assertIsNone(self.visible_item("goldTotalValue"))                  # the four summary cards were removed at the owner's request
        self.assertIsNotNone(self.visible_item("goldUnknownNote"))               # the legacy receipt piece (3 g, assay unknown)
        self.assertIn("3.000", self.text("goldUnknownNote"))
        self.assertIsNone(self.visible_item("goldDetailsPanel"))                 # details closed by default
        self.click("goldDetailsToggle")
        self.assertIsNotNone(self.visible_item("goldDetailsPanel"))
        rows = self.visible_texts("goldDetailRow")
        self.assertTrue(any("MT000002" in r and "لوجو" in r for r in rows), rows)
        self.assertTrue(any("حسن" in r for r in rows), rows)
        self.assertTrue(all("مورد" not in r for r in rows))                       # no supplier figure in the view
        target = os.environ.get("MASGOLD_UI_REVIEW_DIR")
        if target:
            self.root.setWidth(1080)
            self.root.setHeight(680)
            QTest.qWait(250)
            self.root.grabWindow().save(os.path.join(target, "gold-position-1080x680.png"))
        calls = self.service.calls
        self.click("goldRefreshButton")
        self.wait_until(lambda: self.service.calls == calls + 1, "refresh")

    def test_the_card_filters_to_the_gold_position(self):
        self.open_reviews()
        self.load()
        self.assertIsNotNone(self.visible_item("reviewCard_GOLD_POSITION"))
        self.click("reviewCard_GOLD_POSITION")
        self.assertEqual(self.vm.selectedCategory, "GOLD_POSITION")
        self.assertIsNotNone(self.visible_item("goldPositionSection"))
        self.assertIsNone(self.visible_item("financialAccountsSection"))
        self.click("reviewCard_GOLD_POSITION")
        self.assertEqual(self.vm.selectedCategory, "")

    def test_a_user_without_all_three_rights_sees_nothing_and_triggers_no_read(self):
        self.open_reviews(rights={"MANAGEMENT_REVIEW"})
        self.vm.loadGoldPosition()
        QTest.qWait(150)
        self.assertEqual(self.service.calls, 0)
        self.assertFalse(self.vm.canViewGoldPosition)
        self.assertIsNone(self.visible_item("reviewCard_GOLD_POSITION"))
        self.assertIsNone(self.visible_item("goldPositionSection"))

    def test_the_simplified_page_hides_empty_review_sections(self):
        self.open_reviews()
        self.load()
        for name in ("reviewMakingSection", "reviewInspectionSection", "reviewInventorySection", "reviewSettlementSection",
                     "reviewDiscountSection", "reviewReturnApprovalSection"):
            self.assertIsNone(self.visible_item(name), name)
        self.assertIsNotNone(self.visible_item("noPendingReviews"))
        self.assertIsNone(self.visible_item("newAccountForm"))                     # behind its toggle


if __name__ == "__main__":
    import unittest
    unittest.main()
