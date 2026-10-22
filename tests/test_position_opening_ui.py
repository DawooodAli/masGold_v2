"""Settings → الرصيد الافتتاحي وجرد الذهب on the real Main.qml with real clicks: the card is for the owner only, the initial balance is entered ONCE and then shown read-only,
and the manual stocktaking button appears. The service is a recorder (no database)."""
from datetime import datetime
from decimal import Decimal as D
from unittest import mock
import unittest

from PySide6.QtTest import QTest

from app.viewmodels.position_snapshot_viewmodel import PositionSnapshotViewModel
from services.position_snapshot_service import SnapshotError
from tests import test_invoice_page_qml as harness

SIZES = ((1440, 900), (1280, 800), (1080, 680))


class RecorderService:
    def __init__(self, allowed=True):
        self.allowed, self.opening, self.saved, self.manual = allowed, None, [], 0

    def can_snapshot(self):
        return self.allowed

    def opening_state(self, *, _expected_actor=None):
        return self.opening

    def set_opening(self, gold, suppliers, cash, bank, *, _expected_actor=None):
        if self.opening is not None:
            raise SnapshotError("ALREADY_INITIALIZED", "الرصيد الافتتاحي مسجل بالفعل ولا يتغير.")
        self.saved.append((gold, suppliers, cash, bank))
        self.opening = {"id": "1", "taken_at": datetime(2026, 10, 22, 9, 0), "gold_shop_eq21": D(gold), "gold_custody_eq21": D(0), "gold_intermediary_eq21": D(0), "gold_book_eq21": D(0),
                        "gold_suppliers_eq21": D(suppliers or 0), "gold_total_eq21": D(gold) + D(suppliers or 0), "cash_egp": D(cash or 0), "bank_egp": D(bank or 0)}
        return {"id": "1"}

    def take(self, kind="MANUAL", *, _expected_actor=None):
        self.manual += 1
        return {"taken": True}

    def ensure_daily(self, *, _expected_actor=None):
        return {"taken": False, "reason": "ALREADY_TAKEN"}


class _CardBase(harness.InvoicePageTests):
    service = None

    def setUp(self):
        service = RecorderService(allowed=type(self).allowed)
        type(self).service = service
        patcher = mock.patch("app.viewmodels.position_snapshot_viewmodel.create_position_snapshot_viewmodel", lambda session: PositionSnapshotViewModel(session, service))
        patcher.start()
        self.addCleanup(patcher.stop)
        super().setUp()

    allowed = True

    def open_settings(self):
        self.root.setProperty("currentPage", 6)
        self.wait_until(lambda: self.visual_item("settingsPositionOpening") is not None, "the card is visible")
        vm = self.controller.operational_viewmodels["positionSnapshotVM"]
        self.wait_until(lambda: vm.loaded, "the initial balance state loaded")
        return vm



class PositionOpeningCardTests(_CardBase):
    def test_the_initial_balance_is_entered_once_then_read_only_and_the_manual_button_appears(self):
        vm = self.open_settings()
        QTest.qWait(300)
        self.scroll_settings_to("settingsPositionOpening")
        self.assertFalse(self.item("openingSave").property("enabled"))                       # nothing typed, nothing confirmed
        self.type_into("openingGold", "5000")
        self.type_into("openingSuppliers", "-120.5")
        self.type_into("openingCash", "200000")
        self.type_into("openingBank", "30000")
        self.assertFalse(self.item("openingSave").property("enabled"))                       # the confirmation is mandatory
        QTest.qWait(300)                                                                     # the other Settings cards finish loading and move the layout
        self.scroll_settings_to("openingConfirm")
        self.click("openingConfirm")
        self.wait_until(lambda: self.item("openingSave").property("enabled"), "save enabled after confirming")
        self.assert_inside_window(self.item("openingSave"))
        self.click("openingSave")
        self.wait_until(lambda: vm.hasOpening, "recorded")
        self.assertEqual(type(self).service.saved, [("5000", "-120.5", "200000", "30000")])
        self.wait_until(lambda: self.visual_item("openingRecorded") is not None, "the recorded line is shown")
        self.assertIn("5,000.000", self.item("openingRecorded").property("text"))
        self.assertFalse(self.item("openingFields").property("visible"))                       # the entry fields are gone: it cannot be entered twice
        self.click("openingTakeNow")
        self.wait_until(lambda: type(self).service.manual == 1, "manual sheet requested")
        for width, height in SIZES:
            self.root.setWidth(width)
            self.root.setHeight(height)
            QTest.qWait(150)
            self.scroll_settings_to("settingsPositionOpening")
            self.assert_inside_window(self.item("openingTakeNow"))
        self.assertEqual(self.warnings if hasattr(self, "warnings") else [], [])


class PositionOpeningHiddenTests(_CardBase):
    allowed = False

    def test_a_user_who_is_not_the_owner_is_not_offered_the_card(self):
        self.root.setProperty("currentPage", 6)
        QTest.qWait(400)
        self.assertFalse(self.item("settingsPositionOpening").property("visible"))            # not the owner: the card is not offered


for _cls in (_CardBase, PositionOpeningCardTests, PositionOpeningHiddenTests):
    for _name in dir(harness.InvoicePageTests):                                                    # the inherited billing tests belong to their own module
        if _name.startswith("test_") and _name not in _cls.__dict__:
            setattr(_cls, _name, None)


if __name__ == "__main__":
    unittest.main()
