"""Real Qt event-loop and price panel checks with no live DB/network access."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QUICK_BACKEND", "software")
os.environ.setdefault("QML_DISABLE_DISK_CACHE", "1")

from threading import Event, get_ident
from pathlib import Path
import unittest
from unittest.mock import patch, Mock

from PySide6.QtCore import QObject, QCoreApplication, QEvent, QMetaObject, QPointF, QTimer, Qt
from PySide6.QtGui import QFont, QFontDatabase, QGuiApplication
from PySide6.QtQuickControls2 import QQuickStyle
from PySide6.QtTest import QTest

from app.session import AppSession
from app.viewmodels.gold_price_viewmodel import GoldPriceViewModel
from app.workers.gold_price_worker import GoldPriceWorker
from main import ApplicationController, create_operational_viewmodels
from services.authentication_service import AuthenticationResult, _VERIFIED
from services.gold_price_service import GoldPriceService
from tests import test_authentication_qml as authentication_fixture
from tests.gold_price_database_fixture import PriceDatabase


class FakeGoldPrices:
    def __init__(self):
        self.gate = Event()
        self.gate.set()
        self.started = Event()
        self.threads = []
        self.calls = []
        self.actors = []
        self.mode = "AUTO"
        self.fail = False
        self.math = GoldPriceService(AppSession(), providers={})

    def snapshot(self):
        self.threads.append(get_ident())
        return {"mode": self.mode, "status": "FRESH", "source": "TEST",
                "prices": self.preview_manual("6125", "6225"), "warnings": [],
                "refresh_seconds": 600, "age_seconds": 2,
                "provider_timestamp": "2026-09-23T12:00:00+00:00",
                "fetched_at": "2026-09-23T12:00:01+00:00",
                "activated_at": "2026-09-23T12:00:02+00:00"}

    def refresh(self, *, _expected_actor=None):
        self.actors.append(_expected_actor)
        self.calls.append(("refresh",))
        self.started.set()
        self.gate.wait(3)
        if self.fail:
            raise RuntimeError("private DB or credential details")
        return self.snapshot()

    def preview_manual(self, buy21, sell21):
        return self.math.preview_manual(buy21, sell21)

    def activate_manual(self, buy21, sell21, reason, *, _expected_actor=None):
        self.actors.append(_expected_actor)
        self.calls.append(("manual", buy21, sell21, reason))
        self.mode = "MANUAL"
        return self.snapshot()

    def return_to_auto(self, reason, *, _expected_actor=None):
        self.actors.append(_expected_actor)
        self.calls.append(("auto", reason))
        self.mode = "AUTO"
        return self.snapshot()


class QtPriceTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if QGuiApplication.instance() is None:
            QQuickStyle.setStyle("Material")
        cls.app = QGuiApplication.instance() or QGuiApplication([])
        cls.app.setQuitOnLastWindowClosed(False)
        cls.app.setLayoutDirection(Qt.RightToLeft)
        # Windows offscreen Qt does not enumerate system fonts. Load an Arabic-
        # capable test fallback so geometry checks exercise actual text extents.
        # Shipped QML retains Cairo; no font setting is changed in the app.
        fonts = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts"
        user_cairo = Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft/Windows/Fonts/Cairo.ttf"
        if user_cairo.is_file(): QFontDatabase.addApplicationFont(str(user_cairo))
        for filename in ("Cairo.ttf", "seguisym.ttf"):
            path = fonts / filename
            if path.is_file(): QFontDatabase.addApplicationFont(str(path))
        fallback = fonts / "arial.ttf"
        if "Cairo" not in QFontDatabase.families() and fallback.is_file():
            QFontDatabase.addApplicationFont(str(fallback))
            QFont.insertSubstitution("Cairo", "Arial")

    def wait_until(self, predicate):
        for _ in range(150):
            if predicate():
                return
            QTest.qWait(20)
        self.fail("Timed out waiting for price worker")

    def establish(self, session, manage=True):
        permissions = {"APP_LOGIN", "SETTINGS_MANAGE"} if manage else {"APP_LOGIN"}
        result = AuthenticationResult(17, None, "موظف", frozenset(permissions), _VERIFIED)
        self.assertTrue(session._establish(result, int(session.generation)))


class GoldPriceWorkerTests(QtPriceTestCase):
    def setUp(self):
        self.block = patch("database.connection.DatabaseConnection.connect",
                           side_effect=AssertionError("Live database forbidden"))
        self.block.start()
        self.addCleanup(self.block.stop)
        self.session = AppSession()
        self.service = FakeGoldPrices()
        self.vm = GoldPriceViewModel(self.session, self.service)

    def tearDown(self):
        self.service.gate.set()
        self.session.logout()
        self.vm.shutdown()
        QTest.qWait(20)
        self.vm.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)

    def ready(self, manage=True):
        self.establish(self.session, manage)
        self.wait_until(lambda: bool(self.vm.prices) and not self.vm.busy)

    def test_worker_gui_heartbeat_overlap_and_logout_late_result(self):
        self.service.gate.clear()
        self.establish(self.session)
        self.wait_until(self.service.started.is_set)
        heartbeat = []
        QTimer.singleShot(5, lambda: heartbeat.append(True))
        for _ in range(5):
            self.vm.refresh()
        QTest.qWait(40)
        self.assertTrue(heartbeat)
        self.assertEqual(self.service.calls, [("refresh",)])
        self.assertTrue(all(thread != get_ident() for thread in self.service.threads))
        self.session.logout()
        self.assertEqual(self.vm.prices, [])
        self.assertFalse(self.vm.busy)
        self.service.gate.set()
        QTest.qWait(80)
        self.assertEqual(self.vm.prices, [])
        self.assertFalse(self.vm._timer.isActive())

    def test_relogin_during_old_worker_reloads_new_session(self):
        self.service.gate.clear()
        self.establish(self.session)
        self.wait_until(self.service.started.is_set)
        self.session.logout()
        self.establish(self.session, manage=False)
        self.assertEqual(self.vm.prices, [])
        self.service.gate.set()
        self.wait_until(lambda: bool(self.vm.prices) and not self.vm.busy)
        self.assertFalse(self.vm.canManage)
        self.assertEqual(self.service.calls, [("refresh",), ("refresh",)])

    def test_preview_requires_reason_and_confirm_submits_no_actor(self):
        self.ready()
        self.assertFalse(self.vm.previewManual("6125", "6225", " "))
        self.assertTrue(self.vm.previewManual("6125", "6225", "مراجعة المالك"))
        self.assertEqual([r["karat"] for r in self.vm.previewPrices], ["18", "21", "24"])
        self.assertEqual(self.service.calls, [("refresh",)])
        self.assertTrue(all(isinstance(row["shop_buy_price_per_gram"], str)
                            for row in self.vm.previewPrices))
        self.vm.confirmManual()
        self.wait_until(lambda: self.vm.mode == "MANUAL" and not self.vm.busy)
        self.assertEqual(self.service.calls[-1], ("manual", "6125", "6225", "مراجعة المالك"))
        self.assertIs(self.service.actors[-1], self.session.current_actor())
        count = len(self.service.calls)
        self.vm.refresh()
        self.wait_until(lambda: not self.vm.busy)
        self.assertEqual(len(self.service.calls), count)
        self.vm.returnToAuto("")
        self.wait_until(lambda: self.vm.mode == "AUTO" and not self.vm.busy)
        self.assertIn(("auto", ""), self.service.calls)

    def test_confirm_without_preview_or_during_refresh_reports_arabic_error(self):
        self.ready()
        self.assertFalse(self.vm.confirmManual())
        self.assertIn("اعرض", self.vm.errorMessage)
        self.assertTrue(self.vm.previewManual("6125", "6225", "سبب"))
        self.service.gate.clear()
        self.vm.refresh()
        self.assertFalse(self.vm.confirmManual())
        self.assertIn("جارٍ", self.vm.errorMessage)
        self.service.gate.set()
        self.wait_until(lambda: not self.vm.busy)
        self.session.logout()
        self.assertFalse(self.vm.confirmManual())
        self.assertIn("الدخول", self.vm.errorMessage)

    def test_manual_failure_is_logged_and_shown_without_losing_preview(self):
        self.ready()
        self.vm.previewManual("6125", "6225", "سبب")
        with patch.object(self.service, "activate_manual", side_effect=RuntimeError("private details")), \
             self.assertLogs("app.workers.gold_price_worker", level="ERROR") as diagnostics:
            self.vm.confirmManual()
            self.wait_until(lambda: not self.vm.busy)
        self.assertTrue(self.vm.previewReady)
        self.assertIn("اليدوي", self.vm.errorMessage)
        self.assertNotIn("private", self.vm.errorMessage)
        self.assertNotIn("private", " ".join(diagnostics.output))
        self.assertIn("RuntimeError", " ".join(diagnostics.output))
        self.assertEqual(self.vm.successMessage, "")

    def test_worker_keeps_original_actor_across_service_dispatch_race(self):
        self.ready()
        original = self.session.current_actor()
        observed = []

        class DispatchRace:
            def activate_manual(inner, buy, sell, reason, *, _expected_actor):
                # A new login arrives after the worker checked its session.
                self.session.logout()
                self.establish(self.session)
                observed.append(_expected_actor)
                if not self.session.is_current(_expected_actor):
                    raise PermissionError("انتهت جلسة الدخول.")
                self.fail("An old manual payload reached the new login")

        worker = GoldPriceWorker(DispatchRace(), self.session, original, 77,
                                 "manual", ("6125", "6225", "old reason"))
        results = []
        worker.completed.connect(lambda *args: results.append(args))
        worker.run()
        self.assertEqual(observed, [original])
        self.assertIsNot(self.session.current_actor(), original)
        self.assertIsNone(results[0][3])
        self.assertIn("انتهت", results[0][4])

    def test_read_only_user_views_prices_and_cannot_submit_manual(self):
        self.ready(manage=False)
        self.assertIn("6,125", self.vm.headline)
        self.assertFalse(self.vm.previewManual("6125", "6225", "reason"))
        self.vm.confirmManual()
        self.vm.returnToAuto("")
        self.assertEqual(self.service.calls, [("refresh",)])

    def test_failed_refresh_preserves_prices_and_sanitizes_error(self):
        self.ready()
        before = self.vm.prices
        self.service.fail = True
        self.vm.refresh()
        self.wait_until(lambda: not self.vm.busy)
        self.assertEqual(self.vm.prices, before)
        self.assertEqual(self.vm.status, "ERROR")
        self.assertNotIn("private", self.vm.errorMessage)


class GoldPricePanelTests(QtPriceTestCase):
    def visual_item(self, name, parent=None):
        parent = parent or self.root.contentItem()
        if parent.objectName() == name:
            return parent
        for child in parent.childItems():
            found = self.visual_item(name, child)
            if found is not None:
                return found
        return None

    def assert_in_viewport(self, item, viewport=None):
        self.assertTrue(item.property("visible"), item.objectName())
        self.assertGreater(item.width(), 0, item.objectName())
        self.assertGreater(item.height(), 0, item.objectName())
        point = item.mapToScene(QPointF(0, 0))
        lower = viewport.mapToScene(QPointF(0, 0)) if viewport else QPointF(0, 0)
        right = lower.x() + (viewport.width() if viewport else self.root.width())
        bottom = lower.y() + (viewport.height() if viewport else self.root.height())
        self.assertGreaterEqual(point.x(), lower.x() - 1, item.objectName())
        self.assertGreaterEqual(point.y(), lower.y() - 1, item.objectName())
        self.assertLessEqual(point.x() + item.width(), right + 1, item.objectName())
        self.assertLessEqual(point.y() + item.height(), bottom + 1, item.objectName())

    def test_settings_fields_actions_and_topbar_visible_at_all_sizes(self):
        service = Mock()
        service.load.return_value = {"base_url": "https://goldtimo.com/api/v1", "timeout": "10",
            "refresh": "60", "stale": "300", "stored": True, "configured": True}
        self.controller.gold_settings.service = service
        self.open_panel()
        QMetaObject.invokeMethod(self.dialog, "close")
        self.wait_until(lambda: not self.dialog.property("visible"))
        self.root.setProperty("currentPage", 6)
        self.wait_until(lambda: self.controller.gold_settings.data.get("stored", False))
        viewport = self.root.findChild(QObject, "settingsScroll")
        card = self.root.findChild(QObject, "goldPriceArea")
        buy = self.root.findChild(QObject, "goldTopBuy")
        sell = self.root.findChild(QObject, "goldTopSell")
        self.assertNotEqual(buy.property("color"), sell.property("color"))
        target = os.getenv("MASGOLD_UI_REVIEW_DIR")
        if target: Path(target).mkdir(parents=True, exist_ok=True)
        for width, height in ((1440, 900), (1280, 800), (1080, 680)):
            self.root.setWidth(width); self.root.setHeight(height)
            viewport.property("contentItem").setProperty("contentY", 0)
            QTest.qWait(100)
            for name in ("providerSource", "providerUrl", "providerTimeout", "providerRefresh",
                         "providerStale", "providerApiKey", "testProviderConnection",
                         "saveProviderSettings", "deleteProviderKey"):
                self.assert_in_viewport(self.root.findChild(QObject, name), viewport)
            for item in (card, buy, sell, self.root.findChild(QObject, "goldTopStatus")):
                self.assert_in_viewport(item)
            for name in ("settingsCategories", "settingsFactories", "settingsItemTypes"):
                self.assert_in_viewport(self.root.findChild(QObject, name + "Subtitle"),
                                        self.root.findChild(QObject, name))
            if target:
                shot = self.root.grabWindow()
                self.assertTrue(shot.save(str(Path(target) / f"settings-{width}x{height}.png")))
                point = card.mapToScene(QPointF(0, 0))
                self.assertTrue(shot.copy(int(point.x()), int(point.y()), int(card.width()), int(card.height())).save(
                    str(Path(target) / f"topbar-{width}x{height}.png")))
            point = card.mapToScene(QPointF(card.width()/2, card.height()/2)).toPoint()
            QTest.mouseClick(self.root, Qt.LeftButton, Qt.NoModifier, point)
            self.wait_until(lambda: self.dialog.property("opened"))
            QMetaObject.invokeMethod(self.dialog, "close")
            self.wait_until(lambda: not self.dialog.property("visible"))
        original_sell = sell.property("color")
        self.controller.gold_prices._data["status"] = "STALE_ALLOWED"
        self.controller.gold_prices.changed.emit()
        status = self.root.findChild(QObject, "goldTopStatus")
        self.assertEqual(status.property("value"), "تلقائي • قديم")              # simple: mode + freshness
        self.assertNotIn("GoldTimo", status.property("value"))                   # no provider on the card
        self.assertNotEqual(status.property("color"), original_sell)
        self.assertEqual(self.warnings, [])

    def test_settings_success_result_uses_shared_colors_and_safe_rows(self):
        service = Mock()
        service.load.return_value = {"base_url": "https://goldtimo.com/api/v1", "timeout": "10",
            "refresh": "60", "stale": "300", "stored": True, "configured": True}
        service.test.return_value = {"source": "GOLDTIMO", "fetched_at": "2026-09-24T13:15:08Z",
            "provider_timestamp": "2026-09-24T16:15:08+03:00", "timestamp_skew_seconds": 1.0,
            "credential_source": "KEYRING", "coherent_set": "PASS",
            "prices": [{"karat": karat, "SHOP_BUY_from_gramSell": buy, "SHOP_SELL_from_gramBuy": sell}
                for karat, buy, sell in ((18, "5320.0000", "5341"), (21, "6207", "6232"), (24, "7093", "7122"))]}
        self.controller.gold_settings.service = service
        self.open_panel()
        QMetaObject.invokeMethod(self.dialog, "close")
        self.wait_until(lambda: not self.dialog.property("visible"))
        self.root.setProperty("currentPage", 6)
        self.wait_until(lambda: self.controller.gold_settings.data.get("stored", False))
        # Smallest supported window, page at the top: the card must scroll into view by itself.
        self.root.setWidth(1080); self.root.setHeight(680)
        viewport = self.root.findChild(QObject, "settingsScroll")
        viewport.property("contentItem").setProperty("contentY", 0)
        QTest.qWait(60)
        QMetaObject.invokeMethod(self.root.findChild(QObject, "testProviderConnection"), "clicked")
        self.wait_until(lambda: bool(self.controller.gold_settings.result) and not self.controller.gold_settings.busy)
        QTest.qWait(200)
        for name in ("providerTestResult", "providerTestPrices", "providerTestTimestamp",
                     "providerTestSkew", "providerTestCredential"):
            self.assert_in_viewport(self.root.findChild(QObject, name), viewport)
        for width, height in ((1440, 900), (1280, 800), (1080, 680)):
            self.root.setWidth(width); self.root.setHeight(height)
            QTest.qWait(60)
            viewport = self.root.findChild(QObject, "settingsScroll")
            flick = viewport.property("contentItem")
            # Scroll to the Test Connection result card (Settings → الطباعة now follows it).
            card = self.root.findChild(QObject, "providerTestResult")
            bottom = card.mapToItem(flick.property("contentItem"), QPointF(0, card.height())).y()
            flick.setProperty("contentY", max(0, min(flick.property("contentHeight") - viewport.height(),
                                                     bottom - viewport.height() + 12)))
            QTest.qWait(60)
            for name in ("providerTestPrices", "providerTestTimestamp", "providerTestSkew"):
                self.assert_in_viewport(self.root.findChild(QObject, name), viewport)
            for column, top in ((1, "goldTopBuy"), (2, "goldTopSell")):
                self.assertEqual(self.visual_item(f"providerTestPrices-{column}-21").property("color"),
                                 self.root.findChild(QObject, top).property("color"))
                self.assertEqual(self.visual_item(f"goldCurrentPrices-{column}-21",
                    self.root.findChild(QObject, "goldCurrentPrices")).property("color"),
                                 self.root.findChild(QObject, top).property("color"))
            target = os.getenv("MASGOLD_UI_REVIEW_DIR")
            if target:
                self.assertTrue(self.root.grabWindow().save(str(Path(target) / f"settings-result-{width}x{height}.png")))
        self.assertIn("1 ثانية", self.root.findChild(QObject, "providerTestSkew").property("value"))
        self.assertEqual(self.visual_item("providerTestPrices-1-18").property("value"), "⁦5,320⁩")
        stamp = self.root.findChild(QObject, "providerTestTimestamp").property("value")
        self.assertIn("⁦2026/09/24 ", stamp)
        self.assertNotIn("T", stamp)
        self.assertIn("المفتاح المحفوظ", self.root.findChild(QObject, "providerTestCredential").property("value"))
        result = self.root.findChild(QObject, "providerTestResult")
        self.assertEqual(result.property("sectionTitle"), "تم الاتصال بنجاح")
        self.assertNotIn("✓", self.root.findChild(QObject, "providerKeyStatus").property("value"))
        self.assertFalse(self.root.findChild(QObject, "providerTestError").property("visible"))
        self.assertEqual(self.warnings, [])

    def test_age_text_uses_arabic_singular_dual_and_plural(self):
        vm = self.controller.gold_prices
        expected = {1: "ثانية", 2: "ثانيتين", 3: "3 ثوانٍ", 10: "10 ثوانٍ", 11: "11 ثانية",
                    59: "59 ثانية", 60: "دقيقة", 120: "دقيقتين", 240: "4 دقائق",
                    660: "11 دقيقة", 3600: "ساعة", 7200: "ساعتين", 18000: "5 ساعات",
                    39600: "11 ساعة", 360000: "100 ساعة", 367200: "102 ساعة", 370800: "103 ساعات"}
        for age, text in expected.items():
            vm._data = dict(vm._data, age_seconds=age)
            self.assertEqual(vm.ageText, text, age)
        vm._data = dict(vm._data, age_seconds=240, mode="AUTO", status="FRESH", source="GOLDTIMO")
        self.assertTrue(vm.compactStatus.endswith("منذ 4 دقائق"))

    def test_settings_rejected_set_shows_observations_as_error(self):
        service = Mock()
        service.load.return_value = {"base_url": "https://goldtimo.com/api/v1", "timeout": "10",
            "refresh": "60", "stale": "300", "stored": True, "configured": True}
        service.test.return_value = {"source": "GOLDTIMO", "fetched_at": None,
            "provider_timestamp": "2026-09-24T13:15:14+00:00", "timestamp_skew_seconds": 9.0,
            "credential_source": "ENTERED", "coherent_set": "FAIL", "error_code": "QUOTE_VALIDATION",
            "error": "أسعار المصدر أو توقيتها لا تجتاز التحقق.",
            "prices": [{"karat": karat, "SHOP_BUY_from_gramSell": "6207", "SHOP_SELL_from_gramBuy": "6232"}
                for karat in (18, 21, 24)]}
        self.controller.gold_settings.service = service
        self.open_panel()
        QMetaObject.invokeMethod(self.dialog, "close")
        self.wait_until(lambda: not self.dialog.property("visible"))
        self.root.setProperty("currentPage", 6)
        self.wait_until(lambda: self.controller.gold_settings.data.get("stored", False))
        QMetaObject.invokeMethod(self.root.findChild(QObject, "testProviderConnection"), "clicked")
        self.wait_until(lambda: bool(self.controller.gold_settings.result) and not self.controller.gold_settings.busy)
        self.assertTrue(self.controller.gold_settings.isError)
        result = self.root.findChild(QObject, "providerTestResult")
        self.assertIn("لم تجتز التحقق", result.property("sectionTitle"))
        self.assertTrue(self.root.findChild(QObject, "providerTestError").property("visible"))
        self.assertNotEqual(result.property("accentColor"), self.root.findChild(QObject, "goldTopSell").property("color"))
        for width, height in ((1440, 900), (1280, 800), (1080, 680)):
            self.root.setWidth(width); self.root.setHeight(height)
            QTest.qWait(60)
            viewport = self.root.findChild(QObject, "settingsScroll")
            flick = viewport.property("contentItem")
            # Scroll to the Test Connection result card (Settings → الطباعة now follows it).
            card = self.root.findChild(QObject, "providerTestResult")
            bottom = card.mapToItem(flick.property("contentItem"), QPointF(0, card.height())).y()
            flick.setProperty("contentY", max(0, min(flick.property("contentHeight") - viewport.height(),
                                                     bottom - viewport.height() + 12)))
            QTest.qWait(60)
            for name in ("providerTestError", "providerTestPrices", "providerTestSkew", "providerTestCredential"):
                self.assert_in_viewport(self.root.findChild(QObject, name), viewport)
            target = os.getenv("MASGOLD_UI_REVIEW_DIR")
            if target:
                self.assertTrue(self.root.grabWindow().save(str(Path(target) / f"settings-rejected-{width}x{height}.png")))
        self.assertIn("9 ثانية", self.root.findChild(QObject, "providerTestSkew").property("value"))
        self.assertIn("المُدخل", self.root.findChild(QObject, "providerTestCredential").property("value"))
        self.assertEqual(self.warnings, [])

    def test_provider_settings_worker_test_then_save_preserves_correct_key(self):
        service = Mock()
        service.load.return_value = {"base_url": "https://goldtimo.com/api/v1", "timeout": "10",
            "refresh": "120", "stale": "300", "stored": True, "configured": True}
        gate, started = Event(), Event()
        thread_ids = []
        def diagnostic(*args):
            thread_ids.append(get_ident())
            started.set()
            gate.wait(2)
            return {"source": "GOLDTIMO", "fetched_at": "2026-09-23T12:00:00Z", "prices": []}
        service.test.side_effect = diagnostic
        self.controller.gold_settings.service = service
        self.open_panel()
        QMetaObject.invokeMethod(self.dialog, "close")
        self.wait_until(lambda: not self.dialog.property("visible"))
        self.root.setProperty("currentPage", 6)
        self.wait_until(lambda: self.controller.gold_settings.data.get("stored", False))
        panel = self.root.findChild(QObject, "goldProviderSettings")
        key = self.root.findChild(QObject, "providerApiKey")
        self.assertEqual(key.property("text"), "")
        for width, height in ((1440,900), (1280,800), (1080,680)):
            self.root.setProperty("width", width); self.root.setProperty("height", height)
            QTest.qWait(30)
            self.assertGreater(panel.property("height"), 140)
            self.assertLessEqual(panel.property("width"), width)
        key.setProperty("text", "unsaved-private-key")
        button = self.root.findChild(QObject, "testProviderConnection")
        flickable = self.root.findChild(QObject, "settingsScroll").property("contentItem")
        flickable.setProperty("contentY", 0)
        QTest.qWait(50)
        point = button.mapToScene(QPointF(button.width() / 2, button.height() / 2)).toPoint()
        self.assertLess(point.y(), self.root.height())
        QTest.mouseClick(self.root, Qt.LeftButton, Qt.NoModifier, point)
        self.assertEqual(key.property("text"), "unsaved-private-key")
        self.wait_until(started.is_set)
        self.assertNotEqual(thread_ids[0], get_ident())
        self.assertTrue(self.controller.gold_settings.busy)
        gate.set()
        self.wait_until(lambda: not self.controller.gold_settings.busy)
        self.assertEqual(service.test.call_args.args[-1], "unsaved-private-key")
        self.assertNotIn("unsaved-private-key", repr(self.controller.gold_settings.data))
        self.assertNotIn("unsaved-private-key", self.controller.gold_settings.message)
        # The actual QML Save button must receive the same tested draft, not
        # an empty key which would leave the prior OS credential unchanged.
        self.assertEqual(key.property("text"), "unsaved-private-key")
        service.save.return_value = "saved"
        QMetaObject.invokeMethod(self.root.findChild(QObject, "saveProviderSettings"), "clicked")
        self.wait_until(lambda: service.save.called and not self.controller.gold_settings.busy)
        self.assertEqual(key.property("text"), "")
        self.assertEqual(service.save.call_args.args[-1], "unsaved-private-key")
        self.assertEqual(self.warnings, [])

    def setUp(self):
        self.block = patch("database.connection.DatabaseConnection.connect",
                           side_effect=AssertionError("Live database forbidden"))
        self.block.start()
        self.addCleanup(self.block.stop)
        self.service = FakeGoldPrices()
        self.auth = authentication_fixture.FakeAuthentication()

        def factory():
            empty = authentication_fixture.EmptyService
            with patch("app.viewmodels.master_data_viewmodel.MasterDataService", empty), \
                 patch("app.viewmodels.inventory_viewmodel.InventoryService", empty), \
                 patch("app.viewmodels.old_gold_conversion_viewmodel.OldGoldConversionService", empty), \
                 patch("app.viewmodels.barcode_viewmodel.BarcodePrintService", empty), \
                 patch("app.viewmodels.billing_viewmodel.BillingService", empty):
                return create_operational_viewmodels()
        self.controller = ApplicationController(self.app, authentication_service=self.auth,
                                                operational_factory=factory,
                                                gold_price_service=self.service)
        self.wait_until(lambda: self.controller.state == "LOGIN")

    def tearDown(self):
        self.service.gate.set()
        self.controller.authentication.shutdown()
        self.controller.gold_prices.shutdown()
        for engine in (self.controller.login_engine, self.controller.setup_engine):
            for root in engine.rootObjects():
                root.hide()
        QTest.qWait(30)
        self.controller.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)

    def open_panel(self, manage=True):
        self.establish(self.controller.session, manage)
        self.controller._open_operational()
        self.wait_until(lambda: bool(self.controller.gold_prices.prices)
                        and not self.controller.gold_prices.busy)
        self.root = self.controller.operational_engine.rootObjects()[0]
        self.warnings = []
        self.controller.operational_engine.warnings.connect(
            lambda errors: self.warnings.extend(e.toString() for e in errors))
        self.dialog = self.root.findChild(QObject, "goldPriceDialog")
        QMetaObject.invokeMethod(self.dialog, "open")
        self.wait_until(lambda: self.dialog.property("opened"))

    def test_panel_preview_confirm_and_layout_sizes(self):
        self.open_panel()
        for width, height in ((1440, 900), (1280, 800), (1080, 680)):
            self.root.setProperty("width", width)
            self.root.setProperty("height", height)
            QTest.qWait(30)
        self.assertTrue(self.dialog.property("visible"))
        self.assertTrue(self.root.findChild(QObject, "goldPriceManagement").property("visible"))
        self.assertIn("6,125", self.root.findChild(QObject, "goldPriceHeadline").property("text"))
        for name, text in (("goldBuy21", "6125"), ("goldSell21", "6225"),
                           ("goldManualReason", "تأكيد المالك")):
            self.root.findChild(QObject, name).setProperty("text", text)
        QMetaObject.invokeMethod(self.root.findChild(QObject, "goldPreviewButton"), "clicked")
        self.assertTrue(self.controller.gold_prices.previewReady)
        self.assertEqual(self.service.calls, [("refresh",)])
        self.click("goldConfirmManualButton")
        self.wait_until(lambda: self.root.findChild(QObject, "goldManualConfirmation").property("opened"))
        self.assertEqual(self.service.calls, [("refresh",)])
        self.click("goldApplyManualButton")
        self.wait_until(lambda: self.controller.gold_prices.mode == "MANUAL"
                        and not self.controller.gold_prices.busy)
        QMetaObject.invokeMethod(self.root.findChild(QObject, "goldReturnAutoButton"), "clicked")
        self.assertEqual(self.controller.gold_prices.mode, "MANUAL")
        QMetaObject.invokeMethod(self.root.findChild(QObject, "goldConfirmAutoButton"), "clicked")
        self.wait_until(lambda: self.controller.gold_prices.mode == "AUTO"
                        and not self.controller.gold_prices.busy)
        self.assertEqual(self.warnings, [])

    def click(self, name):
        QTest.qWait(40)
        item = self.root.findChild(QObject, name)
        self.assertTrue(item.property("visible"), name)
        self.assertTrue(item.property("enabled"), name)
        top_left = item.mapToScene(QPointF(0, 0))
        self.assertGreaterEqual(top_left.y(), 0, name)
        self.assertLessEqual(top_left.y() + item.height(), self.root.height(), name)
        point = item.mapToScene(QPointF(item.width() / 2, item.height() / 2)).toPoint()
        QTest.mouseClick(self.root, Qt.LeftButton, Qt.NoModifier, point)
        QTest.qWait(30)

    def fill_manual(self, buy="6125", sell="6225"):
        for name, value in (("goldBuy21", buy), ("goldSell21", sell), ("goldManualReason", "مراجعة المالك")):
            self.root.findChild(QObject, name).setProperty("text", value)

    def test_compact_empty_state_and_save_action_reachable_at_all_sizes(self):
        self.open_panel()
        vm = self.controller.gold_prices
        vm._data.update(prices=[], mode="MANUAL", status="UNAVAILABLE", warnings=[])
        vm.changed.emit()
        for width, height in ((1440, 900), (1280, 800), (1080, 680)):
            with self.subTest(size=(width, height)):
                self.root.setWidth(width); self.root.setHeight(height)
                self.fill_manual()
                QTest.qWait(50)
                empty = self.root.findChild(QObject, "goldPriceEmptyState")
                self.assertTrue(empty.property("visible"))
                self.assertEqual(empty.property("value"), "لا توجد أسعار ذهب معتمدة حالياً")
                self.assertLessEqual(empty.property("height"), 48)
                self.assertFalse(self.root.findChild(QObject, "goldCurrentPrices").property("visible"))
                self.assertLessEqual(self.dialog.property("width"), 620)
                self.assertLessEqual(self.root.findChild(QObject, "goldBuy21").property("width"), 260)
                self.click("goldPreviewButton")
                self.assertTrue(vm.previewReady)
                self.assertEqual(self.root.findChild(QObject, "goldConfirmManualButton").property("text"), "اعتماد السعر اليدوي")
                self.click("goldConfirmManualButton")
                self.wait_until(lambda: self.root.findChild(QObject, "goldManualConfirmation").property("opened"))
                self.click("goldCancelManualButton")
                self.wait_until(lambda: not self.root.findChild(QObject, "goldManualConfirmation").property("visible"))
                self.assertNotIn("manual", [call[0] for call in self.service.calls])
        self.assertEqual(self.warnings, [])

    def test_real_sql_activation_updates_dialog_topbar_and_reopened_application(self):
        self.open_panel()
        database = PriceDatabase()
        self.addCleanup(database.close)
        vm = self.controller.gold_prices
        vm._service = database.service(self.controller.session)
        vm._start("snapshot")
        self.wait_until(lambda: not vm.busy)
        for width, height in ((1440, 900), (1280, 800), (1080, 680)):
            with self.subTest(size=(width, height)):
                self.root.setWidth(width); self.root.setHeight(height)
                self.fill_manual("6100", "6200")
                # Bring inputs into view after any prior user scrolling.
                scroll = self.root.findChild(QObject, "goldPriceScroll")
                scroll.property("contentItem").setProperty("contentY", 0)
                self.click("goldPreviewButton")
                self.click("goldConfirmManualButton")
                self.wait_until(lambda: self.root.findChild(QObject, "goldManualConfirmation").property("opened"))
                self.click("goldApplyManualButton")
                self.wait_until(lambda: not vm.busy and bool(vm.successMessage))
                self.assertEqual(vm.mode, "MANUAL")
                self.assertEqual(vm._data["source"], "MANUAL")
                self.assertEqual(len(vm.prices), 3)
                self.assertIn("6,100", self.root.findChild(QObject, "goldPriceHeadline").property("text"))
                self.assertTrue(self.root.findChild(QObject, "goldPriceSuccess").property("visible"))
                self.assertFalse(vm.previewReady)
                self.assertEqual(self.warnings, [])
        saved = vm.prices
        with database.connect() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM gold_price_quotes").fetchone()[0], 9)
        self.controller.authentication.logout()
        vm._service = database.service(self.controller.session)
        self.open_panel()
        self.assertEqual(vm.prices, saved)
        self.assertEqual(vm.mode, "MANUAL")
        self.assertIn("6,100", vm.headline)

    def test_read_only_panel_hides_management_and_logout_destroys_dialog(self):
        self.open_panel(manage=False)
        self.assertFalse(self.root.findChild(QObject, "goldPriceManagement").property("visible"))
        self.controller.authentication.logout()
        self.assertIsNone(self.controller.operational_engine)
        self.assertEqual(self.controller.gold_prices.prices, [])
        self.assertFalse(self.controller.gold_prices._timer.isActive())


if __name__ == "__main__":
    unittest.main()
