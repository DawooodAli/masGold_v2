"""The colour theme: the local setting (fake store, never the registry), the view model and the real Settings > المظهر card with real clicks."""
from unittest import mock
import unittest

from PySide6.QtCore import QObject, QPointF, Qt
from PySide6.QtTest import QTest

from app.viewmodels.theme_viewmodel import ThemeViewModel
from config.theme_settings import DEFAULT_THEME, THEME_CODES, ThemeSettings
from tests import test_invoice_page_qml as harness


class Store(dict):
    def value(self, key, default=None):
        return self.get(key, default)

    def setValue(self, key, value):
        self[key] = value

    def sync(self):
        pass


class ThemeSettingsTests(unittest.TestCase):
    def test_default_then_saved_and_an_unknown_value_falls_back(self):
        store = Store()
        settings = ThemeSettings(store)
        self.assertEqual(settings.theme(), DEFAULT_THEME)
        settings.set_theme("blue")
        self.assertEqual(ThemeSettings(store).theme(), "blue")
        store["ui/theme"] = "hot-pink"
        self.assertEqual(settings.theme(), DEFAULT_THEME)
        with self.assertRaises(ValueError):
            settings.set_theme("hot-pink")

    def test_the_view_model_publishes_and_refuses_unknown_codes(self):
        vm = ThemeViewModel(settings=ThemeSettings(Store()))
        seen = []
        vm.changed.connect(lambda: seen.append(vm.name))
        self.assertTrue(vm.setTheme("green"))
        self.assertFalse(vm.setTheme("nope"))
        self.assertEqual((vm.name, seen), ("green", ["green"]))
        self.assertEqual([t["code"] for t in vm.themes], list(THEME_CODES))


class ThemeCardTests(harness.InvoicePageTests):
    def test_choosing_a_theme_with_a_real_click_recolours_the_application(self):
        vm = self.controller.operational_viewmodels["themeVM"]
        vm._settings = ThemeSettings(Store())                                      # never the registry of this computer
        self.root.setProperty("currentPage", 6)
        page = self.root.findChild(QObject, "settingsPage")
        QTest.qWait(200)
        card = self.visual_item("settingsThemeCard")
        self.assertIsNotNone(card)
        self.click("settingsThemeCard")
        self.wait_until(lambda: page.property("openSection") == "theme")
        before = self.root.findChild(QObject, "settingsThemeSection")
        self.assertTrue(before.property("visible"))
        for width, height in ((1440, 900), (1280, 800), (1080, 680)):
            self.root.setWidth(width); self.root.setHeight(height)
            QTest.qWait(80)
            choice = self.visual_item("themeChoice_blue")
            self.assertIsNotNone(choice)
            point = choice.mapToScene(QPointF(choice.width() / 2, choice.height() / 2)).toPoint()
            self.assertLess(point.y(), self.root.height())                              # reachable without scrolling
        self.click("themeChoice_blue")
        self.wait_until(lambda: vm.name == "blue")
        self.assertEqual(vm._settings.theme(), "blue")
        self.assertTrue(self.visual_item("themeChoice_blue").property("selected"))


for _name in dir(harness.InvoicePageTests):                                                      # the inherited billing tests belong to their own module
    if _name.startswith("test_") and _name not in ThemeCardTests.__dict__:
        setattr(ThemeCardTests, _name, None)


if __name__ == "__main__":
    unittest.main()
