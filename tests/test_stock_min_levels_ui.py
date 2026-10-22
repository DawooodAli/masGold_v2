"""Settings → الحد الأدنى للمخزون on the real Main.qml with real clicks: pick a type and a karat, type a minimum, save, replace and delete. The service is a recorder (no database); a user without
SETTINGS_MANAGE is not offered the card."""
from decimal import Decimal as D
from unittest import mock
import unittest

from PySide6.QtTest import QTest

from app.viewmodels.stock_min_level_viewmodel import StockMinLevelViewModel
from services.stock_min_level_service import parse_grams, parse_pieces
from tests import test_invoice_page_qml as harness

SIZES = ((1440, 900), (1280, 800), (1080, 680))


class Recorder:
    def __init__(self, allowed=True):
        self.allowed = allowed
        self.rules = {1: {"type": "دبلة", "karat": 21, "min_pieces": 5, "min_grams": None}}
        self.calls = []

    def can_manage(self):
        return self.allowed

    def overview(self, *, _expected_actor=None):
        return {"types": [{"id": "1", "name": "دبلة"}, {"id": "2", "name": "خاتم"}], "karats": [{"id": "2", "value": 21}, {"id": "1", "value": 18}],
                "levels": [{"id": str(i), "type": r["type"], "karat": r["karat"], "min_pieces": r["min_pieces"], "min_grams": r["min_grams"], "pieces": 4, "grams": D("21.500")}
                           for i, r in self.rules.items()]}

    def save(self, item_type_id, karat_id, pieces, grams, *, _expected_actor=None):
        p, g = parse_pieces(pieces), parse_grams(grams)
        self.calls.append(("save", item_type_id, karat_id, p, g))
        names = {"1": "دبلة", "2": "خاتم"}
        self.rules[len(self.rules) + 1] = {"type": names[item_type_id], "karat": 21 if karat_id == "2" else 18, "min_pieces": p, "min_grams": g}
        return {"id": "9"}

    def remove(self, level_id, *, _expected_actor=None):
        self.calls.append(("remove", level_id))
        self.rules.pop(int(level_id), None)
        return {"id": level_id, "deleted": True}


class _Base(harness.InvoicePageTests):
    allowed = True

    def setUp(self):
        service = Recorder(self.allowed)
        type(self).service = service
        patcher = mock.patch("app.viewmodels.stock_min_level_viewmodel.create_stock_min_level_viewmodel", lambda session: StockMinLevelViewModel(session, service))
        patcher.start()
        self.addCleanup(patcher.stop)
        super().setUp()


class StockMinLevelCardTests(_Base):
    def test_save_a_rule_refuse_a_bad_number_and_delete_with_real_clicks(self):
        self.open_settings_dialog(2, "openStockMinLevelsButton", "stockMinLevelsDialog")
        self.wait_until(lambda: self.visual_item("settingsStockMinLevels") is not None, "the card exists")
        vm = self.controller.operational_viewmodels["stockMinLevelVM"]
        self.wait_until(lambda: vm.loaded, "loaded")
        self.wait_until(lambda: self.item("settingsStockMinLevels").property("visible"), "the card is shown")
        service = type(self).service
        self.scroll_dialog_to("stockMinLevelsDialog", "levelRow_1")
        self.assertFalse(self.item("levelSave").property("enabled"))                              # nothing typed yet
        self.choose("levelType", 1)                                                               # خاتم
        self.choose("levelKarat", 1)                                                              # عيار 18
        self.type_into("levelPieces", "3")
        self.click("levelSave")
        self.wait_until(lambda: ("save", "2", "1", 3, None) in service.calls, "saved")
        self.wait_until(lambda: len(vm.levels) == 2, "the new rule is listed")
        QTest.qWait(400)
        self.scroll_dialog_to("stockMinLevelsDialog", "levelGrams")                                                     # the form moved down by one row
        self.type_into("levelGrams", "1.2345")
        self.click("levelSave")
        self.wait_until(lambda: vm.messageIsError, "refused")
        self.assertEqual(len([c for c in service.calls if c[0] == "save"]), 1)
        self.scroll_dialog_to("stockMinLevelsDialog", "levelDelete_1")
        self.click("levelDelete_1")
        self.wait_until(lambda: ("remove", "1") in service.calls, "deleted")
        self.wait_until(lambda: all(l["id"] != "1" for l in vm.levels), "no longer listed")
        for width, height in SIZES:
            self.root.setWidth(width)
            self.root.setHeight(height)
            QTest.qWait(150)
            self.scroll_dialog_to("stockMinLevelsDialog", "levelSave")
            self.assert_inside_window(self.item("levelSave"))


class StockMinLevelHiddenTests(_Base):
    allowed = False

    def test_a_user_without_the_settings_right_is_not_offered_the_card(self):
        self.root.setProperty("currentPage", 2)
        QTest.qWait(400)
        QTest.qWait(200)
        self.assertFalse(self.item("openStockMinLevelsButton").property("visible"))


for _cls in (_Base, StockMinLevelCardTests, StockMinLevelHiddenTests):
    for _name in dir(harness.InvoicePageTests):                                                    # the inherited billing tests belong to their own module
        if _name.startswith("test_") and _name not in _cls.__dict__:
            setattr(_cls, _name, None)


if __name__ == "__main__":
    unittest.main()
