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
        self.allowed, self.opening, self.ledgers, self.saved, self.manual = allowed, None, None, [], 0
        self.accounts = [{"id": "1", "ledger": "CASH", "name": "الخزنة الرئيسية", "type": "CASH", "free": True}, {"id": "5", "ledger": "BANK", "name": "بنك مصر", "type": "BANK", "free": True},
                         {"id": "6", "ledger": "BANK", "name": "فودافون كاش", "type": "MOBILE_WALLET", "free": True}]

    def can_snapshot(self):
        return self.allowed

    def opening_state(self, *, _expected_actor=None):
        return {"opening": self.opening, "ledgers_posted": self.ledgers is not None, "accounts": self.accounts,
                "posted": {"scrap": {21: D("100"), 18: D("50")}, "cash": D("200000"), "bank": D("31500")} if self.ledgers is not None else {"scrap": {}, "cash": D(0), "bank": D(0)}}

    def post_opening_ledgers(self, scrap, cash, banks, *, _expected_actor=None):
        if self.ledgers is not None:
            raise SnapshotError("ALREADY_INITIALIZED", "الكسر والكاش والبنوك سُجّلت بالفعل ولا تتغير.")
        self.ledgers = (dict(scrap), cash, dict(banks))
        return {"posted": []}

    def set_opening(self, new_gold, suppliers, *, _expected_actor=None):
        if self.opening is not None:
            raise SnapshotError("ALREADY_INITIALIZED", "الرصيد الافتتاحي مسجل بالفعل ولا يتغير.")
        self.saved.append((dict(new_gold), suppliers))
        gold = D("5204.000") + D("154.286")
        self.opening = {"id": "1", "taken_at": datetime(2026, 10, 22, 9, 0), "gold_shop_eq21": gold, "gold_custody_eq21": D(0), "gold_intermediary_eq21": D(0), "gold_book_eq21": D(0),
                        "gold_suppliers_eq21": D(suppliers or 0), "gold_total_eq21": gold + D(suppliers or 0), "cash_egp": D("200000"), "bank_egp": D("31500")}
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
        self.open_settings_dialog(10, "reviewsOpenOpening", "positionOpeningDialog")                 # the card lives inside مراجعات المدير
        self.wait_until(lambda: self.visual_item("settingsPositionOpening") is not None, "the card is visible")
        vm = self.controller.operational_viewmodels["positionSnapshotVM"]
        self.wait_until(lambda: vm.loaded, "the initial balance state loaded")
        return vm



class PositionOpeningCardTests(_CardBase):
    def test_stage_one_posts_the_ledgers_then_the_new_gold_is_typed_with_its_21k_equivalent_and_the_card_closes(self):
        vm = self.open_settings()
        QTest.qWait(300)
        self.assertIsNotNone(self.visual_item("openingStage1"))
        self.assertFalse(self.item("openingStage2").property("visible"))                         # the new gold waits for stage 1
        self.assertFalse(self.item("openingLedgersSave").property("enabled"))                    # the confirmation is mandatory
        self.type_into("openingScrap21", "100")
        self.type_into("openingScrap18", "50")
        self.type_into("openingScrap24", "10")
        self.type_into("openingCash", "200000")
        self.type_into("openingAccount_5", "30000")
        self.type_into("openingAccount_6", "1500")
        self.scroll_dialog_to("positionOpeningDialog", "openingLedgersConfirm")
        self.click("openingLedgersConfirm")
        self.wait_until(lambda: self.item("openingLedgersSave").property("enabled"), "stage 1 can be saved")
        self.assert_inside_window(self.item("openingLedgersSave"))
        self.click("openingLedgersSave")
        self.wait_until(lambda: vm.ledgersPosted, "stage 1 recorded")
        self.assertEqual(type(self).service.ledgers, ({"21": "100", "18": "50", "24": "10"}, "200000", {"5": "30000", "6": "1500"}))
        self.wait_until(lambda: self.item("openingStage2").property("visible"), "stage 2 appears")
        self.assertFalse(self.item("openingStage1").property("visible"))                         # stage 1 cannot be entered twice
        self.type_into("openingGold21", "5000")
        self.type_into("openingGold18", "210")
        self.type_into("openingGold24", "21")
        self.wait_until(lambda: "5,204.000" in str(self.item("openingEq21").property("text")), "the 21K equivalent is computed by itself")
        self.scroll_dialog_to("positionOpeningDialog", "openingConfirm")
        self.assertFalse(self.item("openingSave").property("enabled"))
        self.click("openingConfirm")
        self.wait_until(lambda: self.item("openingSave").property("enabled"), "stage 2 can be saved")
        self.assert_inside_window(self.item("openingSave"))
        self.click("openingSave")
        self.wait_until(lambda: vm.hasOpening, "recorded")
        self.assertEqual(type(self).service.saved, [({"21": "5000", "18": "210", "24": "21"}, "")])
        self.wait_until(lambda: self.visual_item("openingRecorded") is not None, "the recorded line is shown")
        self.assertIn("5,358.286", self.item("openingRecorded").property("text"))
        self.assertFalse(self.item("openingStage2").property("visible"))                         # the card closes: nothing left to enter
        self.assertFalse(self.item("openingStage1").property("visible"))
        self.click("openingTakeNow")
        self.wait_until(lambda: type(self).service.manual == 1, "manual sheet requested")
        for width, height in SIZES:
            self.root.setWidth(width)
            self.root.setHeight(height)
            QTest.qWait(150)
            self.scroll_dialog_to("positionOpeningDialog", "settingsPositionOpening")
            self.assert_inside_window(self.item("openingTakeNow"))
        self.assertEqual(self.warnings if hasattr(self, "warnings") else [], [])


class PositionOpeningHiddenTests(_CardBase):
    allowed = False

    def test_a_user_who_is_not_the_owner_is_not_offered_the_card(self):
        self.root.setProperty("currentPage", 10)
        QTest.qWait(500)
        button = self.visual_item("reviewsOpenOpening")
        self.assertTrue(button is None or not button.property("visible"))                    # not the owner: no button in the manager reviews
        self.root.setProperty("currentPage", 6)
        QTest.qWait(300)
        self.assertIsNone(self.visual_item("settingsPositionOpening"))                        # and the Settings page never carried it any more


for _cls in (_CardBase, PositionOpeningCardTests, PositionOpeningHiddenTests):
    for _name in dir(harness.InvoicePageTests):                                                    # the inherited billing tests belong to their own module
        if _name.startswith("test_") and _name not in _cls.__dict__:
            setattr(_cls, _name, None)


if __name__ == "__main__":
    unittest.main()
