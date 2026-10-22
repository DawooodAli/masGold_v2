"""Settings → تكلفة المصنعية للحروف on the real Main.qml with real clicks: the list of letters, typing a cost, saving and clearing it. The service is a recorder (no database); a user without
SETTINGS_MANAGE is not offered the card."""
from decimal import Decimal as D
from unittest import mock
import unittest

from PySide6.QtTest import QTest

from app.viewmodels.category_cost_viewmodel import CategoryCostViewModel
from services.category_cost_service import CostError, parse_cost
from tests import test_invoice_page_qml as harness

SIZES = ((1440, 900), (1280, 800), (1080, 680))


class Recorder:
    def __init__(self, allowed=True):
        self.allowed = allowed
        self.rows = {i: {"id": str(i), "code": c, "name": c, "cost": D(cost) if cost else None, "range_min": D("50"), "range_max": D("200")}
                     for i, (c, cost) in enumerate((("A", "150.00"), ("B", None), ("C", None)), start=1)}
        self.calls = []

    def can_manage(self):
        return self.allowed

    def costs(self, *, _expected_actor=None):
        return [dict(r) for r in self.rows.values()]

    def set_cost(self, category_id, amount, *, _expected_actor=None):
        value = parse_cost(amount)
        self.rows[int(category_id)]["cost"] = value
        self.calls.append(("set", int(category_id), value))
        return {"id": str(category_id), "cost": value}

    def clear_cost(self, category_id, *, _expected_actor=None):
        self.rows[int(category_id)]["cost"] = None
        self.calls.append(("clear", int(category_id)))
        return {"id": str(category_id), "cost": None}


class _Base(harness.InvoicePageTests):
    allowed = True

    def setUp(self):
        service = Recorder(self.allowed)
        type(self).service = service
        patcher = mock.patch("app.viewmodels.category_cost_viewmodel.create_category_cost_viewmodel", lambda session: CategoryCostViewModel(session, service))
        patcher.start()
        self.addCleanup(patcher.stop)
        super().setUp()


class CategoryCostCardTests(_Base):
    def test_save_a_cost_refuse_a_bad_one_and_clear_with_real_clicks(self):
        self.open_settings_dialog(2, "openCategoryCostsButton", "categoryCostsDialog")
        self.wait_until(lambda: self.visual_item("settingsCategoryCosts") is not None, "the card exists")
        vm = self.controller.operational_viewmodels["categoryCostVM"]
        self.wait_until(lambda: vm.loaded, "loaded")
        self.wait_until(lambda: self.item("settingsCategoryCosts").property("visible"), "the card is shown")
        service = type(self).service
        self.scroll_dialog_to("categoryCostsDialog", "costRow_A")
        self.assertEqual(self.item("costField_A").property("text"), "150.00")                    # the stored cost is shown
        self.assertEqual(self.item("costField_B").property("text"), "")                          # not set
        self.assertFalse(self.item("costClear_B").property("visible"))                           # nothing to clear
        self.type_into("costField_B", "220")
        self.click("costSave_B")
        self.wait_until(lambda: ("set", 2, D("220.00")) in service.calls, "saved")
        self.wait_until(lambda: next(c for c in vm.categories if c["code"] == "B")["hasCost"], "listed with its cost")
        QTest.qWait(400)                                                                         # the rebuilt row replaces the old delegate
        self.type_into("costField_C", "12.345")                                                  # three decimals: refused with the service's message
        self.click("costSave_C")
        self.wait_until(lambda: vm.messageIsError, "refused")
        self.assertNotIn(("set", 3, D("12.35")), service.calls)
        self.scroll_dialog_to("categoryCostsDialog", "costClear_A")
        self.click("costClear_A")
        self.wait_until(lambda: ("clear", 1) in service.calls, "cleared")
        for width, height in SIZES:
            self.root.setWidth(width)
            self.root.setHeight(height)
            QTest.qWait(150)
            self.scroll_dialog_to("categoryCostsDialog", "costRow_B")
            self.assert_inside_window(self.item("costSave_B"))


class CategoryCostHiddenTests(_Base):
    allowed = False

    def test_a_user_without_the_settings_right_is_not_offered_the_card(self):
        self.root.setProperty("currentPage", 2)
        QTest.qWait(400)
        QTest.qWait(200)
        self.assertFalse(self.item("openCategoryCostsButton").property("visible"))


for _cls in (_Base, CategoryCostCardTests, CategoryCostHiddenTests):
    for _name in dir(harness.InvoicePageTests):                                                    # the inherited billing tests belong to their own module
        if _name.startswith("test_") and _name not in _cls.__dict__:
            setattr(_cls, _name, None)


if __name__ == "__main__":
    unittest.main()
