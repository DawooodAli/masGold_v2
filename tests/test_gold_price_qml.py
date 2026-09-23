"""Real Qt event-loop and price panel checks with no live DB/network access."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QUICK_BACKEND", "software")
os.environ.setdefault("QML_DISABLE_DISK_CACHE", "1")

from threading import Event, get_ident
from pathlib import Path
import unittest
from unittest.mock import patch

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
        fallback = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts" / "arial.ttf"
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
