"""Supplier gold settlement source selection on the real Main.qml, with real clicks at the target sizes."""
from decimal import Decimal as D
from unittest.mock import patch
import unittest

from PySide6.QtCore import QMetaObject, QObject, QPointF, Qt
from PySide6.QtTest import QTest

from tests import test_invoice_page_qml as harness
from tests.market_trip_fakes import OWNER, build
from tests.supplier_fakes import ALL as SUPPLIER_RIGHTS

TODAY = "2026-10-10"
SUPPLIER = "11"                      # the fake inactive 18K supplier: an inactive supplier still receives a settlement
# What an OWNER really has: the supplier rights (view / manage / edit / post / intake) AND the settlement rights.
RIGHTS = tuple(SUPPLIER_RIGHTS) + ("SUPPLIER_GOLD_SETTLE", "SUPPLIER_GOLD_SETTLE_REVERSE", "TRANSACTION_CORRECT",
                                   "MARKET_TRIP_MANAGE", "INTERMEDIARY_HOLDING_MANAGE")


class LiveDetailStub:
    def list_suppliers(self, search="", active=False, _expected_actor=None, **kw):
        detail = self.supplier_detail(SUPPLIER)
        return [{**detail["supplier"], "gold_status": "RESOLVED", "gold_direction": "SETTLED",
                 "gold_balance": D("0.000"), "making_direction": "SETTLED", "making": D("0.00"), "posted_invoice_count": 0}]

    def supplier_detail(self, supplier_id, _expected_actor=None):
        return {"supplier": {"id": SUPPLIER, "code": "S000011", "name": "مورد متوقف", "phone": "", "notes": "", "is_active": False, "karat_id": 2,
                             "karat_value": 18, "karat_locked": False, "is_supplier": True, "is_intermediary": False, "role_text": "مورد"},
                "balances": {"gold_status": "RESOLVED", "gold_karat_value": 18, "gold_by_karat": [], "gold_direction": "SETTLED",
                             "gold_balance": D("0.000"), "making_direction": "SETTLED", "making": D("0.00")},
                "gold_ledger": [], "making_ledger": []}


class TripSettlementStub:
    def __init__(self, sources=None):
        self.sources = sources if sources is not None else [{
            "key": "custody-gold:1:1:LOGO", "source_type": "CUSTODY_GOLD", "trip_id": "1", "trip_code": "MT000001",
            "karat_id": "1", "designation": "LOGO", "available": "25.000", "karat_value": 21,
            "text": "MT000001 • عيار 21 (لوجو) • المتاح 25.000 جرام"}]
        self.posts = []
        self.adjustments = []
        self.availability = D("25.000")

    def supplier_settlement_sources(self, _expected_actor=None):
        return self.sources

    def trip_detail(self, trip_id, _expected_actor=None):
        return {"trip": {"status": "OPEN"}, "mode": "LEGACY",
                "custody": {"gold": [{"karat_id": 1, "designation": self.sources[0]["designation"], "outstanding": self.availability,
                                      "karat_value": 21}], "bar": D("0"), "lots": []}}

    def settle_supplier_gold(self, *args, **kwargs):
        self.posts.append((args, kwargs))
        return {"code": "SG000001", "settlement_code": "SG000001", "delivered_weight": "10.000",
                "credited_weight": "11.667", "target_karat": 18}

    def correct_issue_weight(self, *args, **kwargs):
        self.adjustments.append((args, kwargs))
        before = self.availability
        self.availability = D(kwargs["target_weight"])
        return {"code": "MT000001-9", "weight": str(self.availability - before),
                "source_kind": kwargs["source_kind"]}


class HoldingSettlementStub:
    def settlement_sources(self, _expected_actor=None):
        return []


class GoldSettlementPageTests(harness.InvoicePageTests):
    def setUp(self):
        super().setUp()
        session = self.controller.session
        self.db, _, self.authorization, _, _ = build(permissions=OWNER, session=session)
        self.svm = self.controller.operational_viewmodels["supplierVM"]
        self.svm._service = LiveDetailStub()
        self.controller.operational_viewmodels["supplierPurchaseVM"]._service = type(
            "NoInvoices", (), {"list_invoices": lambda s, supplier, status=None, **kw: []})()      # the purchase side is not under test
        self.trip_settlements = TripSettlementStub()
        self.svm._market_trip_settlements = self.trip_settlements
        self.svm._holding_settlements = HoldingSettlementStub()
        self.grant(*RIGHTS)
        self.svm.changed.emit()

    # ------------------------------------------------------------ helpers
    def rights(self, *codes, revoke=()):
        self.grant(*codes, revoke=revoke)
        self.svm.changed.emit()
        QTest.qWait(60)

    def wait_idle(self):
        self.wait_until(lambda: not self.svm.busy, "worker idle")
        QTest.qWait(80)

    def open_supplier(self):
        self.root.setProperty("currentPage", 14)
        QTest.qWait(200)
        self.svm.refresh()
        self.wait_idle()
        self.svm.selectSupplier(SUPPLIER)
        self.wait_until(lambda: "id" in self.svm.detail, "supplier detail")
        self.wait_idle()

    def open_dialog(self, require_sources=True):
        self.click("settleGoldButton")
        self.wait_until(lambda: self.item("goldSettleDialog").property("opened"), "settlement dialog")
        self.wait_until(lambda: not self.svm.supplierGoldSourcesLoading, "source list")
        if require_sources:
            self.assertTrue(self.svm.supplierGoldSources)

    def type_in(self, name, text):
        self.click(name)
        QTest.keyClick(self.root, Qt.Key_A, Qt.ControlModifier)
        self.type_text(text)
        QTest.qWait(60)

    def pick_source(self, key="custody-gold:1:1:LOGO"):
        options = [""] + [row["key"] for row in self.svm.supplierGoldSources]
        index = options.index(key)
        combo = self.item("goldSettleSource")
        self.click(combo)
        QTest.qWait(80)
        for child in combo.findChildren(QObject):
            if child.metaObject().indexOfMethod("close()") >= 0 and child.property("visible"):
                QMetaObject.invokeMethod(child, "close")
                QTest.qWait(150)
        combo.setProperty("currentIndex", index)
        combo.activated[int].emit(index)
        self.wait_until(lambda: self.svm.supplierGoldSourceContext.get("key") == key, "source details")

    def pick_adjustment_source(self, key):
        combo = self.item("goldSettleAdjustmentSource")
        self.click(combo)
        self.wait_until(lambda: combo.property("down"), "adjustment source popup")
        index = {"": 0, "SHOP_SCRAP": 1, "UNSOURCED": 2}[key]
        for _ in range(index):
            QTest.keyClick(self.root, Qt.Key_Down)
        QTest.keyClick(self.root, Qt.Key_Return)
        self.wait_until(lambda: not combo.property("down"), "adjustment source popup closed")

    def double_click(self, name):
        item = self.item(name)
        self.assertTrue(item.property("enabled"))
        point = item.mapToScene(QPointF(item.width() / 2, item.height() / 2)).toPoint()
        QTest.mouseClick(self.root, Qt.LeftButton, Qt.NoModifier, point)
        QTest.mouseClick(self.root, Qt.LeftButton, Qt.NoModifier, point)

    def visible_texts(self, name, parent=None):
        parent = parent or self.root.contentItem()
        found = [str(parent.property("text"))] if parent.objectName() == name and parent.property("visible") else []
        for child in self.child_items(parent):
            found += self.visible_texts(name, child)
        return found

    def closed(self, name):
        self.wait_until(lambda: not self.item(name).property("visible"), f"{name} closed")

    # ------------------------------------------------------------ tests
    def test_new_supplier_settlement_uses_an_open_market_custody_source(self):
        self.root.setWidth(1080)
        self.root.setHeight(680)
        self.open_supplier()
        self.assert_inside_window(self.item("settleGoldButton"))
        self.open_dialog()
        self.pick_source()
        self.type_in("goldSettleWeight", "10")
        self.assertIn("MT000001 • عيار 21 (لوجو)", self.text("goldSettleSourceText"))
        self.assertEqual(self.text("goldSettleAvailable"), "المتاح من هذا المصدر: 25.000 جرام")
        self.assertEqual(self.text("goldSettleDelivered"), "المُسلَّم: 10.000 جرام (عيار 21)")
        self.assertEqual(self.text("goldSettleCredited"), "يُحتسب للمورد: 11.667 جرام عيار 18")
        self.assertTrue(self.item("confirmGoldSettleButton").property("enabled"))
        for name in ("goldSettleSource", "goldSettleWeight", "goldSettleDate", "confirmGoldSettleButton",
                     "cancelGoldSettleButton"):
            self.assert_inside_window(self.item(name))
        self.click("confirmGoldSettleButton")
        self.wait_until(lambda: len(self.trip_settlements.posts) == 1, "custody settlement")
        self.closed("goldSettleDialog")
        self.wait_idle()
        args, kwargs = self.trip_settlements.posts[0]
        self.assertEqual(args[:5], ("1", SUPPLIER, "1", "LOGO", "10"))
        self.assertEqual(kwargs["_expected_actor"].user_id, self.controller.session.current_actor().user_id)
        self.assertEqual(self.db.settlements, {})                                      # no SHOP_SCRAP settlement or ledger write
        self.assertEqual(self.warnings, [])

    def test_over_available_and_invalid_weights_cannot_be_submitted(self):
        self.open_supplier()
        self.open_dialog()
        self.pick_source()
        self.type_in("goldSettleWeight", "25.001")
        self.assertIsNotNone(self.visible_item("goldSettleShort"))
        self.assertFalse(self.item("confirmGoldSettleButton").property("enabled"))
        self.type_in("goldSettleWeight", "1.2345")
        self.assertFalse(self.item("confirmGoldSettleButton").property("enabled"))
        self.assertIsNotNone(self.visible_item("goldSettlePreviewError"))
        self.assertEqual(self.trip_settlements.posts, [])

    def test_double_click_submits_only_one_custody_settlement(self):
        self.open_supplier()
        self.open_dialog()
        self.pick_source()
        self.type_in("goldSettleWeight", "4")
        self.double_click("confirmGoldSettleButton")
        self.wait_until(lambda: len(self.trip_settlements.posts) >= 1, "custody settlement")
        self.closed("goldSettleDialog")
        self.wait_idle()
        self.assertEqual(len(self.trip_settlements.posts), 1)
        self.assertEqual(self.db.settlements, {})

    def test_short_unclassified_custody_can_add_only_the_deficit_without_scrap(self):
        self.root.setWidth(1080)
        self.root.setHeight(680)
        self.trip_settlements.sources = [{
            "key": "custody-gold:1:1:UNCLASSIFIED", "source_type": "CUSTODY_GOLD", "trip_id": "1", "trip_code": "MT000001",
            "karat_id": "1", "designation": "UNCLASSIFIED", "available": "66.300", "karat_value": 21,
            "text": "MT000001 • عيار 21 (بدون وجهة) • المتاح 66.300 جرام"}]
        self.trip_settlements.availability = D("66.300")
        self.open_supplier()
        self.open_dialog()
        self.pick_source("custody-gold:1:1:UNCLASSIFIED")
        self.type_in("goldSettleWeight", "66.450")
        self.assertEqual(self.text("goldSettleAdjustmentAmount"), "يمكن تسجيل الفرق فقط: 0.150 جرام")
        self.assertFalse(self.item("confirmGoldSettleButton").property("enabled"))
        self.pick_adjustment_source("UNSOURCED")
        self.type_in("goldSettleAdjustmentReason", "weight difference")
        self.assertEqual(self.item("goldSettleDialog").property("adjustmentSourceKind"), "UNSOURCED")
        self.assertEqual(self.text("goldSettleAdjustmentReason"), "weight difference")
        self.assertTrue(self.svm.previewSupplierGoldSettlement(SUPPLIER, "66.450", "", False)["canIncreaseCustody"])
        self.assertFalse(self.svm.adjustingSupplierCustody)
        self.assertTrue(self.item("recordCustodyWeightAdjustmentButton").property("enabled"))
        self.assert_inside_window(self.item("recordCustodyWeightAdjustmentButton"))
        self.double_click("recordCustodyWeightAdjustmentButton")
        self.wait_until(lambda: len(self.trip_settlements.adjustments) == 1, "custody adjustment")
        self.wait_until(lambda: self.svm.supplierGoldSourceContext.get("available") == "66.450", "updated custody balance")
        self.wait_idle()
        args, kwargs = self.trip_settlements.adjustments[0]
        self.assertEqual(args[:6], ("1", "1", "INCREASE", None, "weight difference", self.text("goldSettleDate")))
        self.assertEqual((kwargs["source_kind"], kwargs["target_weight"]), ("UNSOURCED", "66.450"))
        self.assertEqual(self.db.physical(1), D("100.000"))
        self.assertEqual(self.trip_settlements.posts, [])
        self.assertIn("0.150 جرام", self.text("goldSettleMessage"))
        self.assertEqual(self.warnings, [])

    def test_unavailable_source_rights_hide_the_settlement_action(self):
        self.open_supplier()
        self.rights(revoke=("MARKET_TRIP_MANAGE", "INTERMEDIARY_HOLDING_MANAGE"))
        self.assertIsNone(self.visible_item("settleGoldButton"))
        self.assertEqual(self.warnings, [])

    def test_empty_source_list_explains_that_no_gold_is_available(self):
        self.trip_settlements.sources = []
        self.open_supplier()
        self.open_dialog(require_sources=False)
        self.wait_until(lambda: self.visible_item("goldSettleNoSources") is not None, "empty source message")
        self.assertIn("لا يوجد ذهب متاح", self.text("goldSettleNoSources"))
        self.assertFalse(self.item("confirmGoldSettleButton").property("enabled"))

    def test_the_dialogs_fit_at_every_target_size(self):
        self.open_supplier()
        for width, height in harness.SIZES:
            with self.subTest(size=(width, height)):
                self.root.setWidth(width)
                self.root.setHeight(height)
                QTest.qWait(150)
                self.assert_inside_window(self.item("settleGoldButton"))
                self.open_dialog()
                self.pick_source()
                self.type_in("goldSettleWeight", "10")
                for name in ("goldSettleSource", "goldSettleWeight", "goldSettleDate", "goldSettleCredited",
                             "confirmGoldSettleButton", "cancelGoldSettleButton"):
                    self.assert_inside_window(self.item(name))
                dialog = self.item("goldSettleDialog")
                self.assertLessEqual(dialog.property("height"), height)
                self.assertLessEqual(dialog.property("width"), width)
                dialog.close()
                self.closed("goldSettleDialog")
        self.assertEqual(self.warnings, [])

    def test_the_reversal_dialog_fits_at_every_target_size(self):
        self.open_supplier()
        dialog = self.item("reverseSettlementDialog")
        for width, height in harness.SIZES:
            with self.subTest(size=(width, height)):
                self.root.setWidth(width)
                self.root.setHeight(height)
                QTest.qWait(100)
                dialog.setProperty("settlementId", "1")
                dialog.setProperty("code", "SG000001")
                dialog.setProperty("detailText", "تسوية تاريخية")
                QMetaObject.invokeMethod(dialog, "open")
                self.wait_until(lambda: dialog.property("visible"), "reversal dialog")
                for name in ("reverseSettlementReason", "confirmReverseSettlementButton"):
                    self.assert_inside_window(self.item(name))
                dialog.close()
                self.closed("reverseSettlementDialog")
        self.assertEqual(self.warnings, [])


# The base class carries the invoice-page tests: only this module's own tests are collected here.
for _inherited in [n for n in dir(harness.InvoicePageTests) if n.startswith("test_")]:
    setattr(GoldSettlementPageTests, _inherited, None)


if __name__ == "__main__":
    unittest.main()
