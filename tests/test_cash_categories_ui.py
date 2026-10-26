"""Settings → تصنيفات المصروفات والإيرادات on the real Main.qml with real clicks: the list, adding a category, renaming, disabling / enabling and the two-click delete (offered only for an
unused category). The service is a recorder (no database); a user without SETTINGS_MANAGE is not offered the card."""
from unittest import mock
import unittest

from PySide6.QtTest import QTest

from app.viewmodels.cash_category_viewmodel import CashCategoryViewModel
from services.cash_category_service import SEED, CategoryError, view
from tests import test_invoice_page_qml as harness

SIZES = ((1440, 900), (1280, 800), (1080, 680))


class Recorder:
    def __init__(self, allowed=True):
        self.allowed = allowed
        self.rows = [{"id": i + 1, "code": c, "name": n, "direction": d, "is_active": 1, "is_system": 1} for i, (c, n, d) in enumerate(SEED)]
        self.used = {"RENT"}
        self.calls = []

    def can_manage(self):
        return self.allowed

    def categories(self, *, _expected_actor=None):
        return [view(r, r["code"] in self.used) for r in self.rows]

    def _row(self, code):
        return next(r for r in self.rows if r["code"] == code)

    def add(self, name, direction, *, _expected_actor=None):
        if any(r["name"] == name for r in self.rows):
            raise CategoryError("NAME_TAKEN", "يوجد تصنيف بنفس الاسم.")
        code = f"CNEW{len(self.rows):08d}"
        self.rows.append({"id": len(self.rows) + 1, "code": code, "name": name, "direction": direction, "is_active": 1, "is_system": 0})
        self.calls.append(("add", name, direction))
        return view(self._row(code), False)

    def rename(self, code, name, *, _expected_actor=None):
        self._row(code)["name"] = name
        self.calls.append(("rename", code, name))
        return view(self._row(code), False)

    def set_active(self, code, active, *, _expected_actor=None):
        self._row(code)["is_active"] = 1 if active else 0
        self.calls.append(("active", code, bool(active)))
        return view(self._row(code), False)

    def delete(self, code, *, _expected_actor=None):
        if code in self.used:
            raise CategoryError("CATEGORY_USED", "التصنيف مستخدم في عمليات سابقة: عطّله بدل الحذف.")
        self.rows = [r for r in self.rows if r["code"] != code]
        self.calls.append(("delete", code))
        return {"code": code, "deleted": True}


class _Base(harness.InvoicePageTests):
    allowed = True

    def setUp(self):
        service = Recorder(self.allowed)
        type(self).service = service
        patcher = mock.patch("app.viewmodels.cash_category_viewmodel.create_cash_category_viewmodel", lambda session: CashCategoryViewModel(session, service))
        patcher.start()
        self.addCleanup(patcher.stop)
        super().setUp()

    def open_settings(self):
        # The card lives on the Settings page itself (moved out of the cash screen's dialog).
        self.root.setProperty("currentPage", 6)
        self.wait_until(lambda: self.visual_item("settingsCashCategories") is not None, "the card exists")
        vm = self.controller.operational_viewmodels["cashCategoryVM"]
        self.wait_until(lambda: vm.loaded, "the categories loaded")
        self.wait_until(lambda: self.item("settingsCashCategories").property("visible"), "the card is shown")
        return vm


class CashCategoryCardTests(_Base):
    def test_add_rename_disable_and_delete_with_real_clicks(self):
        vm = self.open_settings()
        self.scroll_settings_to("settingsCashCategories")
        service = type(self).service
        self.assertEqual(len(vm.categories), 10)
        self.assertIsNone(self.visual_item("categoryDelete_RENT") if self.item("categoryDelete_RENT").property("visible") else None)      # a used category has no delete button
        self.assertFalse(self.item("categoryDelete_RENT").property("visible"))
        # add
        self.type_into("cashCategoryNewName", "تأمينات")
        self.click("cashCategoryAdd")
        self.wait_until(lambda: len(vm.categories) == 11, "the new category is listed")
        self.assertEqual(service.calls[-1], ("add", "تأمينات", "OUT"))
        self.assertTrue(any(c["name"] == "تأمينات" for c in vm.categories))
        # rename: edit, type, save
        self.scroll_settings_to("categoryEdit_EXPENSE")
        self.click("categoryEdit_EXPENSE")
        self.type_into("categoryRenameField_EXPENSE", "مصروفات عامة")
        self.click("categorySave_EXPENSE")
        self.wait_until(lambda: ("rename", "EXPENSE", "مصروفات عامة") in service.calls, "renamed")
        self.wait_until(lambda: any(c["name"] == "مصروفات عامة" for c in vm.categories), "the list shows the new name")
        # disable then enable
        self.scroll_settings_to("categoryToggle_SALARY")
        self.click("categoryToggle_SALARY")
        self.wait_until(lambda: ("active", "SALARY", False) in service.calls, "disabled")
        self.wait_until(lambda: next(c for c in vm.categories if c["code"] == "SALARY")["active"] is False, "listed as disabled")
        QTest.qWait(400)                                                                         # the rebuilt row replaces the old delegate (destroyed asynchronously)
        self.wait_until(lambda: self.item("categoryToggle_SALARY").property("text") == "تفعيل", "the row shows the enable button")
        self.click("categoryToggle_SALARY")
        self.wait_until(lambda: ("active", "SALARY", True) in service.calls, "enabled again")
        # delete needs two clicks
        QTest.qWait(400)
        self.scroll_settings_to("categoryDelete_TRANSPORT")
        self.click("categoryDelete_TRANSPORT")
        self.assertNotIn(("delete", "TRANSPORT"), service.calls)                               # the first click only asks
        self.assertEqual(self.item("categoryDelete_TRANSPORT").property("text"), "تأكيد الحذف")
        self.click("categoryDelete_TRANSPORT")
        self.wait_until(lambda: ("delete", "TRANSPORT") in service.calls, "deleted after the confirmation")
        self.wait_until(lambda: all(c["code"] != "TRANSPORT" for c in vm.categories), "no longer listed")
        for width, height in SIZES:
            self.root.setWidth(width)
            self.root.setHeight(height)
            QTest.qWait(150)
            self.scroll_settings_to("cashCategoryAdd")
            self.assert_inside_window(self.item("cashCategoryAdd"))


class CashCategoryHiddenTests(_Base):
    allowed = False

    def test_a_user_without_the_settings_right_is_not_offered_the_card(self):
        self.root.setProperty("currentPage", 6)
        QTest.qWait(400)
        QTest.qWait(200)
        card = self.visual_item("settingsCashCategories")
        self.assertTrue(card is None or not card.property("visible"))


for _cls in (_Base, CashCategoryCardTests, CashCategoryHiddenTests):
    for _name in dir(harness.InvoicePageTests):                                                    # the inherited billing tests belong to their own module
        if _name.startswith("test_") and _name not in _cls.__dict__:
            setattr(_cls, _name, None)


if __name__ == "__main__":
    unittest.main()
