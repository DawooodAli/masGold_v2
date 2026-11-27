"""Supplier gold settlement source selection on the real Main.qml, with real clicks at the target sizes."""
from decimal import Decimal as D
from unittest.mock import patch
import unittest

from PySide6.QtCore import Q_ARG, QDate, QMetaObject, QObject, QPointF, Qt
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
        return {"trip": {"status": "OPEN"},
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


class NoTransferSources:
    """108: the gold other suppliers hold for the shop is not under test here (tests/test_supplier_gold_transfer_ui.py)."""
    def sources(self, to_supplier_id, _expected_actor=None):
        return []


class RecordingReverseStub:
    """Task 03: the gold-settlement service behind SupplierViewModel.reverseSettlement. It only records what the view model passes
    (the settlement id and the reason) and answers like the real service; no database, no business rule."""
    def __init__(self):
        self.calls = []

    def reverse(self, settlement_id, reason, _expected_actor=None):
        self.calls.append((settlement_id, reason))
        return {"code": "RV000001", "source_reference": "SG000001", "effects": {}}


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
        self.svm._transfers = NoTransferSources()
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

    # ------------------------------------------------------------ Task 03: reverseSettlementDialog keyboard / focus characterization
    # The dialog is a Popup with two modes sharing one reason field: «عكس التسوية» (a supplier-ledger settlement) and «تأكيد التصحيح»
    # (a holding-sourced settlement). Each test below runs both. Tests marked "characterizes missing behaviour" fail until the
    # dialog takes focus on open, closes on an immediate Escape and submits on Enter; the rest pass today and guard existing behaviour.
    REVERSAL_MODES = (("عكس التسوية", False), ("تأكيد التصحيح", True))
    REVERSAL_ID, REVERSAL_REASON = "7", "خطأ في تسجيل المورد"

    def reversal_setup(self):
        self.root.setWidth(1280)
        self.root.setHeight(800)
        self.reversals = RecordingReverseStub()
        self.svm._settlements = self.reversals
        self.open_supplier()
        self.addCleanup(self.drop_reversal_dialog)
        QTest.qWait(100)

    def drop_reversal_dialog(self):
        self.svm._busy.discard("setrev")
        if self.root is None:                  # cleanups run after tearDown has already released the window
            return
        dialog = self.root.findChild(QObject, "reverseSettlementDialog")
        if dialog is not None and dialog.property("visible"):
            QMetaObject.invokeMethod(dialog, "close")
            QTest.qWait(150)

    def open_reversal(self, from_holding):
        """Open through the dialog's own openFor(), with the map the two real openers pass (supplier ledger row / holding settlement row)."""
        dialog = self.item("reverseSettlementDialog")
        row = {"settlementId": self.REVERSAL_ID, "reference": "SG000001", "detail": "تسوية تاريخية", "effect": "2.000 جرام إلى رصيد المسوقجي",
               "fromHolding": from_holding}
        QMetaObject.invokeMethod(dialog, "openFor", Qt.DirectConnection, Q_ARG("QVariant", row))
        self.wait_until(lambda: dialog.property("opened"), "reversal dialog opened")
        QTest.qWait(150)
        return dialog

    def close_reversal(self):
        dialog = self.item("reverseSettlementDialog")
        if dialog.property("visible"):
            QMetaObject.invokeMethod(dialog, "close")
            self.closed("reverseSettlementDialog")

    def focus_name(self):
        item = self.root.activeFocusItem()
        return "none" if item is None else (item.objectName() or item.metaObject().className())

    def settle_calls(self):
        QTest.qWait(350)                       # long enough for a second (duplicate) worker call to arrive
        return list(self.reversals.calls)

    def test_reversal_dialog_modes_are_told_apart_by_their_button_text(self):
        self.reversal_setup()
        for label, from_holding in self.REVERSAL_MODES:
            with self.subTest(mode=label):
                self.open_reversal(from_holding)
                self.assertEqual(self.item("confirmReverseSettlementButton").property("text"), label)
                self.assertEqual("رصيد المسوقجي" in self.text("reverseSettlementNote"), from_holding)
                self.close_reversal()

    def test_reversal_dialog_reason_field_stays_editable(self):
        self.reversal_setup()
        for label, from_holding in self.REVERSAL_MODES:
            with self.subTest(mode=label):
                self.open_reversal(from_holding)
                field = self.item("reverseSettlementReason")
                self.assertTrue(field.property("visible"))
                self.assertTrue(field.property("enabled"))
                self.assertFalse(field.property("readOnly"))
                self.assertEqual(field.property("text"), "")
                self.type_in("reverseSettlementReason", self.REVERSAL_REASON)           # real click, then real key events
                self.assertEqual(field.property("text"), self.REVERSAL_REASON)
                self.close_reversal()

    def test_reversal_dialog_takes_focus_into_the_reason_field_on_open(self):
        """Characterizes missing behaviour: today the focus stays on the page underneath."""
        self.reversal_setup()
        barcode = self.item("invoiceBarcodeField")
        for label, from_holding in self.REVERSAL_MODES:
            with self.subTest(mode=label):
                self.open_reversal(from_holding)
                self.assertEqual(self.focus_name(), "reverseSettlementReason")
                self.assertTrue(self.item("reverseSettlementReason").property("activeFocus"))
                self.assertFalse(barcode.property("activeFocus"), "the page's barcode field kept the keyboard focus")
                self.close_reversal()

    def test_reversal_dialog_closes_on_an_immediate_escape_without_submitting(self):
        """Characterizes missing behaviour: today Escape does nothing until the user clicks inside the dialog."""
        self.reversal_setup()
        for label, from_holding in self.REVERSAL_MODES:
            with self.subTest(mode=label):
                dialog = self.open_reversal(from_holding)
                QTest.keyClick(self.root, Qt.Key_Escape)                                  # no click inside the dialog first
                self.wait_until(lambda: not dialog.property("visible"), "the dialog did not close on Escape")
                self.assertEqual(self.settle_calls(), [])
                self.click("settleGoldButton")                                            # the page underneath is usable again
                self.wait_until(lambda: self.item("goldSettleDialog").property("opened"), "page button after Escape")
                QMetaObject.invokeMethod(self.item("goldSettleDialog"), "close")
                self.closed("goldSettleDialog")

    def test_reversal_button_click_submits_the_id_and_reason_once(self):
        """Existing behaviour, and the fixture's proof that the settlement id and the reason are observable at the view-model boundary."""
        self.reversal_setup()
        for label, from_holding in self.REVERSAL_MODES:
            with self.subTest(mode=label):
                self.reversals.calls.clear()
                self.open_reversal(from_holding)
                self.type_in("reverseSettlementReason", self.REVERSAL_REASON)
                self.click("confirmReverseSettlementButton")
                self.wait_until(lambda: self.reversals.calls, "reversal submitted")
                self.assertEqual(self.settle_calls(), [(self.REVERSAL_ID, self.REVERSAL_REASON)])
                self.closed("reverseSettlementDialog")
                self.wait_until(lambda: not self.svm.busy, "worker idle")

    def test_reversal_dialog_submits_once_on_enter_with_a_valid_reason(self):
        """Characterizes missing behaviour: the reason field has no Enter handler today."""
        self.reversal_setup()
        for label, from_holding in self.REVERSAL_MODES:
            with self.subTest(mode=label):
                self.reversals.calls.clear()
                self.open_reversal(from_holding)
                self.type_in("reverseSettlementReason", self.REVERSAL_REASON)           # the field holds focus here, whatever the open focus is
                self.assertTrue(self.item("reverseSettlementReason").property("activeFocus"))
                QTest.keyClick(self.root, Qt.Key_Return)
                self.wait_until(lambda: self.reversals.calls, "Enter did not submit the reversal")
                self.assertEqual(self.settle_calls(), [(self.REVERSAL_ID, self.REVERSAL_REASON)])
                self.closed("reverseSettlementDialog")
                self.wait_until(lambda: not self.svm.busy, "worker idle")

    def test_reversal_dialog_submits_once_on_keypad_enter(self):
        """Task 04: the keypad Enter (Key_Enter with KeypadModifier) takes the same path as Return."""
        self.reversal_setup()
        for label, from_holding in self.REVERSAL_MODES:
            with self.subTest(mode=label):
                self.reversals.calls.clear()
                self.open_reversal(from_holding)
                self.type_in("reverseSettlementReason", self.REVERSAL_REASON)
                QTest.keyClick(self.root, Qt.Key_Enter, Qt.KeypadModifier)
                self.wait_until(lambda: self.reversals.calls, "keypad Enter did not submit the reversal")
                self.assertEqual(self.settle_calls(), [(self.REVERSAL_ID, self.REVERSAL_REASON)])
                self.closed("reverseSettlementDialog")
                self.wait_until(lambda: not self.svm.busy, "worker idle")

    def test_reversal_dialog_never_submits_twice_from_repeated_keys_or_button_plus_key(self):
        """Task 04: Return pressed twice in a row, and a button click followed at once by Return, each reach the service once."""
        self.reversal_setup()
        for label, from_holding in self.REVERSAL_MODES:
            for how in ("two Returns", "button then Return"):
                with self.subTest(mode=label, how=how):
                    self.reversals.calls.clear()
                    self.open_reversal(from_holding)
                    self.type_in("reverseSettlementReason", self.REVERSAL_REASON)
                    if how == "two Returns":
                        QTest.keyClick(self.root, Qt.Key_Return)
                        QTest.keyClick(self.root, Qt.Key_Return)
                    else:
                        self.click("confirmReverseSettlementButton")
                        QTest.keyClick(self.root, Qt.Key_Return)
                    self.wait_until(lambda: self.reversals.calls, "reversal submitted")
                    self.assertEqual(self.settle_calls(), [(self.REVERSAL_ID, self.REVERSAL_REASON)])
                    self.closed("reverseSettlementDialog")
                    self.wait_until(lambda: not self.svm.busy, "worker idle")

    def test_reversal_dialog_has_focus_and_closes_on_escape_before_its_open_transition_ends(self):
        """Task 04: no wait after openFor(): the focus is already in the reason field and Escape already closes it."""
        self.reversal_setup()
        row = {"settlementId": self.REVERSAL_ID, "reference": "SG000001", "detail": "تسوية تاريخية", "effect": "", "fromHolding": False}
        for label, from_holding in self.REVERSAL_MODES:
            with self.subTest(mode=label):
                dialog = self.item("reverseSettlementDialog")
                QMetaObject.invokeMethod(dialog, "openFor", Qt.DirectConnection, Q_ARG("QVariant", {**row, "fromHolding": from_holding}))
                self.assertEqual(self.focus_name(), "reverseSettlementReason")
                QTest.keyClick(self.root, Qt.Key_Escape)
                self.wait_until(lambda: not dialog.property("visible"), "the dialog did not close on an immediate Escape")
                self.assertEqual(self.settle_calls(), [])

    def test_reversal_dialog_does_not_submit_an_empty_or_blank_reason_on_enter(self):
        """Passes today (no Enter handler); stays a guard once Enter submits."""
        self.reversal_setup()
        for label, from_holding in self.REVERSAL_MODES:
            for reason in ("", "   "):
                with self.subTest(mode=label, reason=repr(reason)):
                    dialog = self.open_reversal(from_holding)
                    self.click("reverseSettlementReason")
                    if reason:
                        self.type_text(reason)
                    self.assertEqual(self.item("reverseSettlementReason").property("text"), reason)
                    QTest.keyClick(self.root, Qt.Key_Return)
                    self.assertEqual(self.settle_calls(), [])
                    self.assertTrue(dialog.property("visible"))
                    self.close_reversal()

    def test_reversal_dialog_does_not_submit_on_enter_while_a_reversal_is_running(self):
        """Passes today (no Enter handler); stays a guard once Enter submits. The busy state is the view model's own `setrev` marker."""
        self.reversal_setup()
        for label, from_holding in self.REVERSAL_MODES:
            with self.subTest(mode=label):
                dialog = self.open_reversal(from_holding)
                self.type_in("reverseSettlementReason", self.REVERSAL_REASON)
                self.svm._busy.add("setrev")
                self.svm.changed.emit()
                self.wait_until(lambda: self.svm.settlementReversing, "busy state")
                QTest.keyClick(self.root, Qt.Key_Return)
                self.assertEqual(self.settle_calls(), [])
                self.assertTrue(dialog.property("visible"))
                self.svm._busy.discard("setrev")
                self.svm.changed.emit()
                self.close_reversal()


    # ------------------------------------------------------------ Task 17: goldSettleDialog keyboard flow (the source first; Return walks the typed fields)
    def walk_settlement_fields(self, reference="REF-17", notes="ملاحظة"):
        """With a source chosen and a weight typed: Return walks weight -> date -> reference -> notes without posting; the last Return is left to the caller."""
        self.click("goldSettleWeight")
        for field, text in (("goldSettleDate", ""), ("goldSettleReference", reference), ("goldSettleNotes", notes)):
            QTest.keyClick(self.root, Qt.Key_Return)
            self.assertEqual(self.focus_name(), field)
            self.assertEqual(self.trip_settlements.posts, [])
            if text:
                self.type_text(text)
        QTest.qWait(60)

    def prepare_settlement(self, weight="10"):
        self.open_supplier()
        self.open_dialog()
        self.pick_source()
        self.type_in("goldSettleWeight", weight)

    def test_settlement_dialog_focuses_the_source_first_and_escape_closes_it_at_once(self):
        self.open_supplier()
        dialog = self.item("goldSettleDialog")
        QMetaObject.invokeMethod(dialog, "openFor")                                  # no wait: the source list is still loading, Escape already works
        QTest.keyClick(self.root, Qt.Key_Escape)
        self.closed("goldSettleDialog")
        self.assertEqual(self.trip_settlements.posts, [])
        self.open_dialog()                                                           # the list is loaded: the SOURCE has the focus, not the weight
        self.assertEqual(self.focus_name(), "goldSettleSource")
        self.assertFalse(self.item("goldSettleWeight").property("activeFocus"))
        self.assertFalse(self.item("confirmGoldSettleButton").property("enabled"))
        QTest.keyClick(self.root, Qt.Key_Escape)
        self.closed("goldSettleDialog")

    def test_return_walks_the_typed_fields_and_only_the_last_one_posts_the_same_arguments(self):
        self.prepare_settlement()
        self.walk_settlement_fields()
        self.assertEqual(self.trip_settlements.posts, [])
        QTest.keyClick(self.root, Qt.Key_Return)                                     # Return in the notes field
        self.wait_until(lambda: len(self.trip_settlements.posts) == 1, "custody settlement")
        self.closed("goldSettleDialog")
        self.wait_idle()
        args, kwargs = self.trip_settlements.posts[0]
        self.assertEqual(args[:8], ("1", SUPPLIER, "1", "LOGO", "10", QDate.currentDate().toString("yyyy-MM-dd"), "REF-17", "ملاحظة"))
        self.assertTrue(args[8])                                                     # the request key
        self.assertEqual(kwargs["_expected_actor"].user_id, self.controller.session.current_actor().user_id)
        self.assertEqual(self.db.settlements, {})
        self.assertEqual(self.warnings, [])

    def test_keypad_enter_in_the_last_field_posts_once_too(self):
        self.prepare_settlement()
        self.walk_settlement_fields()
        QTest.keyClick(self.root, Qt.Key_Enter, Qt.KeypadModifier)
        self.wait_until(lambda: len(self.trip_settlements.posts) == 1, "custody settlement")
        self.closed("goldSettleDialog")
        self.wait_idle()
        self.assertEqual(len(self.trip_settlements.posts), 1)

    def test_enter_never_posts_what_the_button_refuses(self):
        self.open_supplier()
        self.open_dialog()
        self.type_in("goldSettleWeight", "10")                                       # no source chosen
        self.walk_settlement_fields()
        QTest.keyClick(self.root, Qt.Key_Return)
        QTest.qWait(300)
        self.assertEqual(self.trip_settlements.posts, [])
        self.assertTrue(self.item("goldSettleDialog").property("visible"))
        self.pick_source()                                                           # a source but a weight above the available gold
        self.type_in("goldSettleWeight", "25.001")
        self.assertFalse(self.item("confirmGoldSettleButton").property("enabled"))
        self.click("goldSettleNotes")
        QTest.keyClick(self.root, Qt.Key_Return)
        QTest.qWait(300)
        self.assertEqual(self.trip_settlements.posts, [])
        self.type_in("goldSettleWeight", "1.2345")                                   # an invalid weight
        self.click("goldSettleNotes")
        QTest.keyClick(self.root, Qt.Key_Return)
        QTest.qWait(300)
        self.assertEqual(self.trip_settlements.posts, [])
        self.assertTrue(self.item("goldSettleDialog").property("visible"))

    def test_a_repeated_key_or_button_plus_key_posts_one_custody_settlement(self):
        for how in ("two Returns", "button then Return"):
            with self.subTest(how=how):
                self.trip_settlements.posts.clear()
                self.prepare_settlement("4")
                self.walk_settlement_fields()
                if how == "two Returns":
                    QTest.keyClick(self.root, Qt.Key_Return)
                    QTest.keyClick(self.root, Qt.Key_Return)
                else:
                    self.click("confirmGoldSettleButton")
                    QTest.keyClick(self.root, Qt.Key_Return)
                self.wait_until(lambda: len(self.trip_settlements.posts) >= 1, "custody settlement")
                self.closed("goldSettleDialog")
                self.wait_idle()
                QTest.qWait(200)
                self.assertEqual(len(self.trip_settlements.posts), 1)

    def test_enter_is_refused_while_a_settlement_is_running(self):
        self.prepare_settlement()
        self.walk_settlement_fields()
        self.svm._busy.add("suppliergoldsettle")
        self.svm.changed.emit()
        QTest.qWait(60)
        self.assertFalse(self.item("confirmGoldSettleButton").property("enabled"))
        QTest.keyClick(self.root, Qt.Key_Return)
        QTest.qWait(300)
        self.assertEqual(self.trip_settlements.posts, [])
        self.assertTrue(self.item("goldSettleDialog").property("visible"))
        self.svm._busy.discard("suppliergoldsettle")
        self.svm.changed.emit()

    def test_enter_is_never_bound_to_the_weight_adjustment(self):
        """The shortage branch: Return in the notes or the adjustment-reason field neither posts the settlement nor records the weight difference."""
        self.trip_settlements.sources = [{
            "key": "custody-gold:1:1:UNCLASSIFIED", "source_type": "CUSTODY_GOLD", "trip_id": "1", "trip_code": "MT000001",
            "karat_id": "1", "designation": "UNCLASSIFIED", "available": "66.300", "karat_value": 21,
            "text": "MT000001 • عيار 21 (بدون وجهة) • المتاح 66.300 جرام"}]
        self.trip_settlements.availability = D("66.300")
        self.root.setWidth(1080)
        self.root.setHeight(680)
        self.open_supplier()
        self.open_dialog()
        self.pick_source("custody-gold:1:1:UNCLASSIFIED")
        self.type_in("goldSettleWeight", "66.450")
        self.pick_adjustment_source("UNSOURCED")
        self.type_in("goldSettleAdjustmentReason", "weight difference")
        self.assertTrue(self.item("recordCustodyWeightAdjustmentButton").property("enabled"))
        QTest.keyClick(self.root, Qt.Key_Return)                                     # in the adjustment reason field
        self.click("goldSettleNotes")
        QTest.keyClick(self.root, Qt.Key_Return)                                     # in the notes field (the settlement is refused: short)
        QTest.qWait(400)
        self.assertEqual((self.trip_settlements.posts, self.trip_settlements.adjustments), ([], []))
        self.assertTrue(self.item("goldSettleDialog").property("visible"))

    def test_the_settlement_dialog_keeps_its_fit_and_source_focus_at_every_target_size(self):
        self.open_supplier()
        for width, height in harness.SIZES:
            with self.subTest(size=(width, height)):
                self.root.setWidth(width)
                self.root.setHeight(height)
                QTest.qWait(150)
                self.open_dialog()
                self.assertEqual(self.focus_name(), "goldSettleSource")
                for name in ("goldSettleSource", "goldSettleWeight", "goldSettleDate", "goldSettleReference", "goldSettleNotes",
                             "confirmGoldSettleButton", "cancelGoldSettleButton"):
                    self.assert_inside_window(self.item(name))
                dialog = self.item("goldSettleDialog")                                       # a Popup is not an Item: its own rectangle
                self.assertTrue(dialog.property("x") >= 0 and dialog.property("y") >= 0
                                and dialog.property("x") + dialog.property("width") <= width
                                and dialog.property("y") + dialog.property("height") <= height, (width, height))
                QTest.keyClick(self.root, Qt.Key_Escape)
                self.closed("goldSettleDialog")


# The base class carries the invoice-page tests: only this module's own tests are collected here.
for _inherited in [n for n in dir(harness.InvoicePageTests) if n.startswith("test_")]:
    setattr(GoldSettlementPageTests, _inherited, None)


if __name__ == "__main__":
    unittest.main()
