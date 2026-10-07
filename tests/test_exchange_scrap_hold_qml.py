"""Exchange pieces held in Scrap on the real مراجعات المدير page (Main.qml shell: right sidebar and header unchanged),
real clicks at 1440×900 / 1280×800 / 1080×680: barcode, EX reference, where the piece is now, the two decisions with
their confirmations, the details and the movement history. The database is blocked; the review service is an
in-memory double returning the service's own read model (with_scrap_story)."""
from datetime import datetime
from decimal import Decimal as D
from unittest.mock import Mock, patch

from PySide6.QtCore import QPointF
from PySide6.QtTest import QTest

from services.management_review_service import with_scrap_story
from tests import test_invoice_page_qml as harness

REVIEW_PAGE = 10


def held_row():
    return with_scrap_story({
        "review_id": "901", "review_status": "OPEN", "invoice_id": "500", "invoice_number": "26092701",
        "lifecycle_line_id": "1", "lifecycle_id": "1", "exchange_code": "EX000001", "source_label": "استبدال EX000001",
        "exchanged_at": datetime(2026, 10, 3, 12, 30), "created_at": datetime(2026, 10, 3, 12, 30),
        "source_invoice_line_id": "9", "inventory_item_id": "205", "item_type_name": "دبلة", "karat_value": 21,
        "weight_grams": D("5.000"), "item_weight_grams": D("5.000"), "inventory_status": "CONVERTED_TO_OLD_GOLD",
        "customer_name_snapshot": "أحمد علي", "stored_barcode": "01260925", "hold_id": "1", "hold_status": "HELD",
        "hold_weight_grams": D("5.000"), "scrap_entered_at": datetime(2026, 10, 3, 12, 30), "in_movement_id": "1",
        "out_movement_id": None, "exchanged_by_name": "موظف", "karat_physical_grams": D("10.000"),
        "karat_available_grams": D("5.000"), "karat_held_grams": D("5.000")})


class ScrapReviews:
    def __init__(self):
        self.calls = []

    def counts(self, **kwargs):
        return {"RETURNED_ITEM_INSPECTION": 1}

    def open_reviews(self, **kwargs):
        return {"INVENTORY_MATCH": [], "MAKING_PRICE": [], "CASH_SETTLEMENT": [], "OLD_GOLD_DISCOUNT": [],
                "RETURNED_ITEM_INSPECTION": [held_row()], "RETURN_APPROVAL": []}

    def _decide(self, outcome, review_id):
        self.calls.append((outcome, review_id))
        return {"review_id": review_id, "resolution": outcome, "stored_barcode": "01260925",
                "hold_status": "RETURNED_TO_STOCK" if outcome == "RETURN_TO_STOCK" else "COMMITTED",
                "inventory_status": "IN_STOCK" if outcome == "RETURN_TO_STOCK" else "CONVERTED_TO_OLD_GOLD"}

    def return_to_stock(self, review_id, **kwargs):
        return self._decide("RETURN_TO_STOCK", review_id)

    def convert_to_old_gold(self, review_id, **kwargs):
        return self._decide("CONVERT_TO_OLD_GOLD", review_id)


class ScrapReviewPageTests(harness.InvoicePageTests):
    def setUp(self):
        manager = harness.PERMISSIONS | {"MANAGEMENT_REVIEW"}
        with patch.object(harness, "PERMISSIONS", manager):
            super().setUp()
        self.reviews_vm = self.controller.operational_viewmodels["reviewsVM"]
        self.fake = ScrapReviews()
        self.reviews_vm._service = self.fake
        # A manager decision refreshes the Customers area (by design); keep it on an in-memory double.
        self.controller.operational_viewmodels["customerVM"]._service = Mock(
            get_customer_types=Mock(return_value=[]), get_customer_transaction_types=Mock(return_value=[]),
            search_customers=Mock(return_value={"items": [], "page": 1, "has_more": False}))

    def open_page(self, width, height):
        self.root.setWidth(width)
        self.root.setHeight(height)
        self.root.setProperty("currentPage", REVIEW_PAGE)
        self.reviews_vm.openReviews()
        self.wait_until(lambda: len(self.reviews_vm.scrapPieces) == 1, "scrap piece")
        QTest.qWait(150)

    def scroll_to(self, name):
        flick = self.item("reviewScroll").property("contentItem")
        target = self.visible_item(name)
        self.assertIsNotNone(target, name)
        top = target.mapToItem(flick.property("contentItem"), QPointF(0, 0)).y()
        limit = max(0, flick.property("contentHeight") - flick.height())
        flick.setProperty("contentY", max(0, min(limit, top - 60)))
        QTest.qWait(150)
        return target

    def inside(self, name):
        item = self.visible_item(name)
        self.assertIsNotNone(item, name)
        self.assert_inside_window(item)
        corner = item.mapToScene(QPointF(item.width(), item.height()))
        self.assertLessEqual(corner.x(), self.root.width() + 1, name)
        return item

    def test_scrap_piece_card_decisions_and_history_at_all_sizes(self):
        for width, height in harness.SIZES:
            with self.subTest(size=(width, height)):
                self.open_page(width, height)
                self.assertEqual(self.item("reviewScrapSection").property("visible"), True)
                self.scroll_to("scrapPiece_0")
                self.assertEqual(self.visible_item("scrapBarcode_0").property("text"), "01260925")
                self.assertEqual(self.visible_item("scrapExchange_0").property("text"), "EX000001")
                self.assertEqual(self.visible_item("scrapItemId_0").property("text"), "رقم القطعة #205")
                self.assertEqual(self.visible_item("scrapDestination_0").property("text"),
                                 "الموقع الحالي: رصيد الكسر — عيار 21")
                self.assertEqual(self.visible_item("scrapState_0").property("text"),
                                 "محوّلة إلى الكسر — معلّقة لقرار المدير")
                for name in ("scrapRestock_0", "scrapCommit_0", "scrapToggle_0", "scrapBarcode_0"):
                    self.inside(name)
                self.assertIsNone(self.visible_item("scrapDetails_0"))                 # collapsed by default
                self.click(self.visible_item("scrapToggle_0"))
                self.scroll_to("scrapTimeline_0")
                self.assertIsNotNone(self.visible_item("scrapDetails_0"))
                self.assertEqual([self.visible_item(f"scrapEvent_0_{i}").property("text") for i in (0, 1)],
                                 ["عملية استبدال", "تم تحويل القطعة إلى الكسر"])
                self.inside("scrapDetails_0")
                self.scroll_to("scrapToggle_0")        # the summary cards above take height: scroll to the toggle
                self.click(self.visible_item("scrapToggle_0"))                           # collapse again
                # إرجاع من الكسر إلى المخزون: confirmation, then cancel (nothing decided).
                self.scroll_to("scrapPiece_0")
                self.click(self.visible_item("scrapRestock_0"))
                self.wait_until(lambda: self.item("inspectionConfirm").property("opened"), "confirm")
                self.assertEqual(self.text("inspectionConfirmText"),
                                 "سيتم إخراج وزن القطعة من الكسر وإعادة نفس القطعة إلى المخزون بنفس الباركود."
                                 " هل تريد المتابعة؟")
                facts = self.text("inspectionConfirmFacts")
                for part in ("01260925", "دبلة", "عيار 21", "5.000", "EX000001"):
                    self.assertIn(part, facts)
                self.click("inspectionConfirmNo")
                self.wait_until(lambda: not self.item("inspectionConfirm").property("visible"), "closed")
                self.assertEqual(self.fake.calls, [])
                # تثبيت في الكسر: its own consequential confirmation.
                self.click(self.visible_item("scrapCommit_0"))
                self.wait_until(lambda: self.item("inspectionConfirm").property("opened"), "confirm")
                self.assertIn("سيتم تثبيت القطعة داخل رصيد الكسر وإتاحتها ككسر عادي", self.text("inspectionConfirmText"))
                self.click("inspectionConfirmNo")
                self.wait_until(lambda: not self.item("inspectionConfirm").property("visible"), "closed")
        self.assertEqual(self.warnings, [])

    def test_confirmed_decisions_reach_the_service(self):
        self.open_page(1080, 680)
        self.scroll_to("scrapPiece_0")
        self.click(self.visible_item("scrapRestock_0"))
        self.wait_until(lambda: self.item("inspectionConfirm").property("opened"), "confirm")
        self.click("inspectionConfirmYes")
        self.wait_until(lambda: self.fake.calls == [("RETURN_TO_STOCK", "901")], "return to stock")
        self.wait_until(lambda: "تمت إعادة القطعة من الكسر إلى المخزون" in self.reviews_vm.inspectionMessage, "message")
        self.scroll_to("scrapPiece_0")
        self.click(self.visible_item("scrapCommit_0"))
        self.wait_until(lambda: self.item("inspectionConfirm").property("opened"), "confirm")
        self.click("inspectionConfirmYes")
        self.wait_until(lambda: self.fake.calls[-1] == ("CONVERT_TO_OLD_GOLD", "901"), "commit")
        self.wait_until(lambda: "تم تثبيت القطعة داخل الكسر" in self.reviews_vm.inspectionMessage, "message")
        self.assertEqual(self.warnings, [])


# unittest otherwise inherits every Billing harness test, inflating the suite count.
for _name in dir(harness.InvoicePageTests):
    if _name.startswith("test_"):
        setattr(ScrapReviewPageTests, _name, None)
