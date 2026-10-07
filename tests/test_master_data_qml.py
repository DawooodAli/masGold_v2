"""Master Data pages on the real Main.qml: page-scoped messages, automatic switch to النشطة after
reactivation, + فئة جديدة and the permanent-delete confirmation — real clicks at 1440×900 / 1280×800 /
1080×680. The database is blocked; the real MasterDataService runs over the in-memory store."""
from decimal import Decimal as D

from PySide6.QtTest import QTest

from services.master_data_service import MasterDataService
from tests import test_invoice_page_qml as harness
from tests.test_master_data import Repo, Store

FACTORIES, ITEM_TYPES, CATEGORIES = 7, 8, 9


class MasterDataPageTests(harness.InvoicePageTests):
    def open_master_data(self):
        self.md_store = Store()
        store = self.md_store

        from contextlib import contextmanager

        @contextmanager
        def transaction():
            import copy
            saved = copy.deepcopy(store.tables())
            try:
                yield type("T", (), {"connection": type("C", (), {"store": store})()})()
            except BaseException:
                for key, value in saved.items():
                    setattr(store, key, value)
                raise

        self.md = self.controller.operational_viewmodels["masterDataVM"]
        from tests.auth_fakes import FakeAuthorization
        session = self.controller.session
        self.md._service = MasterDataService(
            session, authorization=FakeAuthorization(session, {"APP_LOGIN", "MASTER_DATA_MANAGE"}),
            transaction_factory=transaction, repository=Repo)
        self.md.refresh()

    def page(self, index):
        self.root.setProperty("currentPage", index)
        QTest.qWait(120)

    @staticmethod
    def _children(item):
        from PySide6.QtQuick import QQuickItem
        return [child for child in item.childItems() if isinstance(child, QQuickItem)]

    def visible_item(self, name, parent=None):
        parent = parent or self.root.contentItem()
        if parent.objectName() == name and parent.property("visible"):
            return parent
        for child in self._children(parent):
            found = self.visible_item(name, child)
            if found is not None:
                return found
        return None

    def button(self, text, parent=None):
        """The visible Button whose label starts with `text` (the pages' buttons have no objectName)."""
        parent = parent or self.root.contentItem()
        if parent.property("visible") is False:
            return None
        if parent.inherits("QQuickAbstractButton") and str(parent.property("text")).startswith(text):
            return parent
        for child in self._children(parent):
            found = self.button(text, child)
            if found is not None:
                return found
        return None

    def popup_button(self, dialog_name, text):
        dialog = self.item(dialog_name) if isinstance(dialog_name, str) else dialog_name
        return self.button(text, dialog.property("contentItem"))

    def test_messages_do_not_leak_between_pages(self):
        self.open_master_data()
        self.page(FACTORIES)
        self.assertTrue(self.md.createFactory("مصنع جديد", "", ""))
        self.assertIsNotNone(self.visible_item("factoriesMessage"))
        self.page(ITEM_TYPES)                                       # navigation clears it
        self.assertEqual(self.md.actionMessage, "")
        self.assertIsNone(self.visible_item("itemTypesMessage"))
        self.assertTrue(self.md.disableItemType(1))
        self.assertIsNotNone(self.visible_item("itemTypesMessage"))
        self.page(CATEGORIES)
        self.assertIsNone(self.visible_item("categoriesMessage"))
        self.assertIsNone(self.visible_item("itemTypesMessage"))
        # the same message never shows on another page even without navigation
        self.md._set_action("factories", True, "رسالة مصانع")
        self.assertIsNone(self.visible_item("categoriesMessage"))
        self.page(FACTORIES)
        self.assertIsNone(self.visible_item("factoriesMessage"))
        self.assertEqual(self.warnings, [])

    def test_reactivate_switches_to_active_tab_for_each_page(self):
        self.open_master_data()
        self.md_store.item_types[1]["is_active"] = 0
        self.md_store.categories[2]["is_active"] = 0
        self.md.refresh()
        for index, page_name, dialog, name in ((FACTORIES, "factoriesPage", "reactivateFactoryDialog", "النور للتصنيع"),
                                               (ITEM_TYPES, "itemTypesPage", "reactivateItemTypeDialog", "خاتم"),
                                               (CATEGORIES, "categoriesPage", "reactivateCategoryDialog", "B")):
            with self.subTest(page_name):
                self.page(index)
                page = self.item(page_name)
                self.click(self.button("⛔  المعطلة", page))
                self.assertTrue(page.property("showDisabled"))
                self.click(self.button("↻  إعادة تفعيل", page))
                popup = self.item(dialog)
                self.wait_until(lambda: popup.property("opened"), dialog)
                self.click(self.popup_button(popup, "إعادة التفعيل"))
                self.wait_until(lambda: not page.property("showDisabled"), "switched to النشطة")
                self.wait_until(lambda: not popup.property("visible"), "reactivate dialog closed")
                self.assertIsNotNone(self.button("✓  النشطة", page))
                self.assertEqual(self.md.actionSuccess, True)
        self.assertEqual((self.md.disabledFactoryCount, self.md.disabledItemTypeCount,
                          self.md.disabledCategoryCount), (0, 0, 0))
        self.assertEqual(self.warnings, [])

    def test_delete_confirmation_used_and_unused_at_all_sizes(self):
        self.open_master_data()
        self.md_store.inventory_items.append({"id": 1, "inventory_status": "SOLD", "factory_id": 1,
                                              "item_type_id": 1, "category_id": 1})
        self.md.refresh()                                           # the lists carry ever_used from the last load
        blocked = "لا يمكن الحذف النهائي لأن السجل استُخدم سابقًا."
        for width, height in harness.SIZES:
            with self.subTest(size=(width, height)):
                self.root.setWidth(width)
                self.root.setHeight(height)
                self.page(FACTORIES)
                page = self.item("factoriesPage")
                self.click(self.button("⛔  المعطلة", page))
                # used (SOLD stock): no delete button at all, the reason is shown, reactivation stays available
                self.assertIsNone(self.visible_item("deleteDisabledFactory"))
                reason = self.visible_item("deleteBlockedFactory")
                self.assertIsNotNone(reason)
                self.assertEqual(reason.property("text"), blocked)
                self.assert_inside_window(reason)
                self.assertIsNotNone(self.button("↻  إعادة تفعيل", page))
                self.assert_inside_window(self.button("↻  إعادة تفعيل", page))
                # the service still refuses regardless of what the page shows
                self.assertFalse(self.md.deleteFactory(1))
                self.assertEqual(self.md.actionMessage,
                                 "لا يمكن حذف المصنع نهائيًا لأنه مرتبط ببيانات في المخزون. يمكنك تعطيله فقط.")
                self.assertIn(1, self.md_store.factories)
                # unused: the button shows in the danger zone and deletes after the confirmation
                self.assertTrue(self.md.createFactory("DELETE TEST FACTORY", "", ""))
                new = max(self.md_store.factories)
                self.assertTrue(self.md.disableFactory(new))
                self.click(self.button("⛔  المعطلة", page))
                rows = [r for r in self._all("deleteDisabledFactory")]
                self.assertEqual(len(rows), 1)
                self.assert_inside_window(rows[-1])
                self.click(rows[-1])
                dialog = self.item("deleteMasterDialog")
                self.wait_until(lambda: dialog.property("opened"), "delete dialog")
                self.assertEqual(self.item("deleteMasterQuestion").property("text"),
                                 "سيتم حذف المصنع نهائيًا ولا يمكن التراجع عن ذلك. هل أنت متأكد؟")
                for name in ("deleteMasterConfirm", "deleteMasterCancel", "deleteMasterQuestion"):
                    self.assert_inside_window(self.item(name))
                self.click("deleteMasterConfirm")
                self.wait_until(lambda: not dialog.property("visible"), "deleted")
                self.assertNotIn(new, self.md_store.factories)
                self.assertEqual(self.md_store.inventory_items[0]["factory_id"], 1)     # stock untouched
        self.assertEqual(self.warnings, [])

    def _all(self, name, parent=None):
        parent = parent or self.root.contentItem()
        found = [parent] if parent.objectName() == name and parent.property("visible") else []
        for child in self._children(parent):
            found += self._all(name, child)
        return found

    def test_new_category_dialog_fits_and_creates(self):
        self.open_master_data()
        for width, height in harness.SIZES:
            with self.subTest(size=(width, height)):
                self.root.setWidth(width)
                self.root.setHeight(height)
                self.page(CATEGORIES)
                self.click("newCategoryButton")
                dialog = self.item("editCategoryDialog")
                self.wait_until(lambda: dialog.property("opened"), "category dialog")
                self.assertTrue(dialog.property("creating"))
                for name in ("newCategoryCode", "newCategoryName", "saveCategoryButton"):
                    self.assert_inside_window(self.item(name))
                self.assertIsNone(self.visible_item("deleteCategoryButton"))          # nothing to delete yet
                code = f"N{width}"
                self.click("newCategoryCode")
                self.type_text(code.lower())
                self.click("saveCategoryButton")
                self.wait_until(lambda: not dialog.property("visible"), "created")
                row = next(c for c in self.md_store.categories.values() if c["code"] == code)
                self.assertEqual((row["is_active"], row["name"]), (1, code))
                self.assertIsNotNone(self.visible_item("categoriesMessage"))
                # duplicate: refused in the dialog with the Arabic business error
                self.click("newCategoryButton")
                self.wait_until(lambda: dialog.property("opened"), "category dialog again")
                self.click("newCategoryCode")
                self.type_text(code)
                self.click("saveCategoryButton")
                self.assertTrue(dialog.property("visible"))
                self.assertIn("كود الفئة مستخدم بالفعل.", self._texts_any(dialog))
                dialog.close()
                self.wait_until(lambda: not dialog.property("visible"), "category dialog closed")
        self.assertEqual(self.md.categoryCount, 9)
        self.assertEqual(self.warnings, [])

    def _texts_any(self, popup):
        out = []

        def walk(item):
            text = item.property("text")
            if isinstance(text, str):
                out.append(text)
            for child in self._children(item):
                walk(child)
        walk(popup.property("contentItem"))
        return " ".join(out)

    def test_edit_dialog_delete_for_item_type_and_category(self):
        self.open_master_data()
        self.md_store.categories[1]["sale_making_min_per_gram"] = D("200.00")
        self.page(ITEM_TYPES)
        self.md.createItemType("DELETE TEST TYPE", "", 0, "")
        new_type = max(self.md_store.item_types)
        self.md.refresh()
        dialog = self.item("deleteMasterDialog")
        edit = self.item("editItemTypeDialog")                      # as a row click opens it
        edit.setProperty("itemTypeId", new_type)
        edit.open()
        self.wait_until(lambda: edit.property("opened"), "edit type")
        self.assertNotIn("حذف نهائي", self._texts_any(edit))      # an ACTIVE record has no permanent delete
        self.assertIsNone(self.visible_item("deleteItemTypeButton"))
        edit.close()
        self.wait_until(lambda: not edit.property("visible"), "edit closed")
        self.assertTrue(self.md.disableItemType(new_type))
        self.page(ITEM_TYPES)
        self.click(self.button("⛔  المعطلة", self.item("itemTypesPage")))
        self.click(self.visible_item("deleteDisabledItemType"))
        self.wait_until(lambda: dialog.property("opened"), "delete type")
        self.click("deleteMasterConfirm")
        self.wait_until(lambda: not dialog.property("visible"), "type deleted")
        self.assertNotIn(new_type, self.md_store.item_types)
        self.page(CATEGORIES)
        category = self.item("editCategoryDialog")
        category.setProperty("creating", False)
        category.setProperty("categoryId", 6)
        category.setProperty("categoryCode", "T")
        category.open()
        self.wait_until(lambda: category.property("opened"), "edit category")
        self.click(self.visible_item("deleteCategoryButton"))
        self.wait_until(lambda: dialog.property("opened"), "delete category")
        self.click("deleteMasterConfirm")
        self.wait_until(lambda: not category.property("visible"), "category deleted")
        self.assertNotIn(6, self.md_store.categories)
        self.assertEqual(self.warnings, [])

    # ---------------------------------------------------------------- UX-MASTER-DELETE-01 / FORM-02
    def _edit_dialog(self, page_index, dialog_name, opener, row):
        self.page(page_index)
        dialog = self.item(dialog_name)
        dialog.open()                                   # as a row click would (fields stay empty)
        self.wait_until(lambda: dialog.property("opened"), dialog_name)
        return dialog

    def test_active_edit_dialogs_have_no_permanent_delete(self):
        self.open_master_data()
        self.md_store.factories[1]["is_active"] = 1
        self.md.refresh()
        cases = ((FACTORIES, "editFactoryDialog", "openFactory", self.md_store.factories[1], "⛔  تعطيل المصنع"),
                 (ITEM_TYPES, "editItemTypeDialog", "openItemType", self.md_store.item_types[1], "⛔  تعطيل الصنف"))
        for width, height in harness.SIZES:
            self.root.setWidth(width)
            self.root.setHeight(height)
            for index, name, opener, row, disable_text in cases:
                with self.subTest(dialog=name, size=(width, height)):
                    dialog = self._edit_dialog(index, name, opener, row)
                    self.assertIsNotNone(self.popup_button(dialog, disable_text))
                    self.assertIsNotNone(self.popup_button(dialog, "إلغاء"))
                    self.assertIsNotNone(self.popup_button(dialog, "💾"))
                    self.assertNotIn("حذف نهائي", self._texts_any(dialog))
                    self.assertIsNone(self.visible_item("deleteFactoryButton"))
                    self.assertIsNone(self.visible_item("deleteItemTypeButton"))
                    for button in (disable_text, "إلغاء", "💾"):          # every action stays reachable
                        self.assert_inside_window(self.popup_button(dialog, button))
                    dialog.close()
                    self.wait_until(lambda: not dialog.property("visible"), "closed")
        self.assertEqual(self.warnings, [])

    def test_disabled_item_type_used_shows_reason_and_never_used_shows_delete(self):
        self.open_master_data()
        self.md.createItemType("NEVER USED TYPE", "", 0, "")
        fresh = max(self.md_store.item_types)
        self.md_store.inventory_items.append({"id": 1, "inventory_status": "SOLD", "factory_id": None,
                                              "item_type_id": 1, "category_id": None})
        self.assertTrue(self.md.disableItemType(1))                 # historically used
        self.assertTrue(self.md.disableItemType(fresh))             # never used
        for width, height in harness.SIZES:
            with self.subTest(size=(width, height)):
                self.root.setWidth(width)
                self.root.setHeight(height)
                self.page(ITEM_TYPES)
                page = self.item("itemTypesPage")
                self.click(self.button("⛔  المعطلة", page))
                reasons = self._all("deleteBlockedItemType")
                buttons = self._all("deleteDisabledItemType")
                self.assertEqual((len(reasons), len(buttons)), (1, 1))
                self.assertEqual(reasons[0].property("text"), "لا يمكن الحذف النهائي لأن السجل استُخدم سابقًا.")
                for item in reasons + buttons:
                    self.assert_inside_window(item)
                self.assertEqual(len(self._all_buttons("↻  إعادة تفعيل", page)), 2)   # reactivation stays
                self.click(buttons[0])
                dialog = self.item("deleteMasterDialog")
                self.wait_until(lambda: dialog.property("opened"), "confirmation")
                self.assertIn("الصنف", self.item("deleteMasterQuestion").property("text"))
                self.click("deleteMasterCancel")
                self.wait_until(lambda: not dialog.property("visible"), "cancelled")
                self.assertIn(fresh, self.md_store.item_types)         # cancel deleted nothing
        self.assertFalse(self.md.deleteItemType(1))                    # service refusal is independent of the UI
        self.assertEqual(self.warnings, [])

    def _all_buttons(self, text, parent):
        found = []
        if parent.property("visible") is False:
            return found
        if parent.inherits("QQuickAbstractButton") and str(parent.property("text")).startswith(text):
            found.append(parent)
        for child in self._children(parent):
            found += self._all_buttons(text, child)
        return found

    def test_master_data_dialogs_keep_right_breathing_room(self):
        """Factory and Item Type dialogs share one shell: a clear margin on both sides, fields aligned."""
        from PySide6.QtCore import QPointF
        self.open_master_data()
        self.md_store.factories[1]["is_active"] = 1
        self.md.refresh()
        for width, height in harness.SIZES:
            self.root.setWidth(width)
            self.root.setHeight(height)
            for index, name, opener, row, field in (
                    (FACTORIES, "editFactoryDialog", "openFactory", self.md_store.factories[1], "editFactoryNameField"),
                    (ITEM_TYPES, "editItemTypeDialog", "openItemType", self.md_store.item_types[1],
                     "editItemTypeNameField")):
                with self.subTest(dialog=name, size=(width, height)):
                    dialog = self._edit_dialog(index, name, opener, row)
                    content = dialog.property("contentItem")
                    box = content.mapToScene(QPointF(0, 0))
                    name_field = self.item(field)
                    point = name_field.mapToScene(QPointF(0, 0))
                    left = point.x() - box.x()
                    right = (box.x() + content.width()) - (point.x() + name_field.width())
                    self.assertGreaterEqual(right, 28, f"{name}: right margin")
                    self.assertGreaterEqual(left, 28, f"{name}: left margin")
                    self.assertAlmostEqual(left, right, delta=1)
                    self.assertLessEqual(content.height(), height)       # the dialog fits, nothing clipped
                    self.assertGreaterEqual(box.y(), 0)
                    dialog.close()
                    self.wait_until(lambda: not dialog.property("visible"), "closed")
        self.assertEqual(self.warnings, [])


# unittest otherwise inherits every Billing harness test, inflating the suite count.
for _name in dir(harness.InvoicePageTests):
    if _name.startswith("test_"):
        setattr(MasterDataPageTests, _name, None)
