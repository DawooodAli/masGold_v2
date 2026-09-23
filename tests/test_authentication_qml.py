"""Offscreen login/session lifecycle with all live database access blocked."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
os.environ.setdefault('QT_QUICK_BACKEND', 'software')
os.environ.setdefault('QML_DISABLE_DISK_CACHE', '1')

from threading import Event, get_ident
import unittest
from unittest.mock import patch

from PySide6.QtCore import QObject, QEvent, QCoreApplication, QMetaObject, QTimer, Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtQuickControls2 import QQuickStyle
from PySide6.QtTest import QTest

from main import ApplicationController, create_operational_viewmodels
from services.authentication_service import AuthenticationResult, _VERIFIED, AuthenticationError, GENERIC_ERROR


class FakeAuthentication:
    def __init__(self):
        self.gate = Event()
        self.gate.set()
        self.started = Event()
        self.thread = None
        self.error = False

    def authenticate(self, username, password):
        self.thread = get_ident()
        self.started.set()
        self.gate.wait(3)
        if self.error:
            raise AuthenticationError(GENERIC_ERROR)
        return AuthenticationResult(9007199254740993, None, 'مستخدم الاختبار',
                                    frozenset({'APP_LOGIN', 'SETTINGS_MANAGE'}), _VERIFIED)


class EmptyService:
    def __getattr__(self, name):
        return lambda *args, **kwargs: []


class AuthenticationQmlTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if QGuiApplication.instance() is None:
            QQuickStyle.setStyle('Material')
        cls.app = QGuiApplication.instance() or QGuiApplication([])
        cls.app.setQuitOnLastWindowClosed(False)
        cls.app.setLayoutDirection(Qt.RightToLeft)

    def setUp(self):
        self.block = patch('database.connection.DatabaseConnection.connect',
                           side_effect=AssertionError('Live database forbidden'))
        self.block.start()
        self.addCleanup(self.block.stop)
        self.fake = FakeAuthentication()
        self.factory_calls = 0
        def factory():
            self.factory_calls += 1
            with patch('app.viewmodels.master_data_viewmodel.MasterDataService', EmptyService), \
                 patch('app.viewmodels.inventory_viewmodel.InventoryService', EmptyService), \
                 patch('app.viewmodels.old_gold_conversion_viewmodel.OldGoldConversionService', EmptyService), \
                 patch('app.viewmodels.barcode_viewmodel.BarcodePrintService', EmptyService), \
                 patch('app.viewmodels.billing_viewmodel.BillingService', EmptyService):
                return create_operational_viewmodels()
        self.controller = ApplicationController(self.app, authentication_service=self.fake,
                                                operational_factory=factory)
        self.warnings = []
        self.controller.login_engine.warnings.connect(
            lambda errors: self.warnings.extend(e.toString() for e in errors))
        self.root = self.controller.login_engine.rootObjects()[0]
        QTest.qWait(30)

    def tearDown(self):
        self.fake.gate.set()
        self.controller.authentication.shutdown()
        QTest.qWait(30)
        self.root.hide()
        self.controller.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)

    def wait_until(self, condition):
        for _ in range(150):
            if condition():
                return
            QTest.qWait(20)
        self.fail('Timed out waiting for authentication state')

    def submit(self):
        self.root.findChild(QObject, 'loginUsername').setProperty('text', 'test-user')
        self.root.findChild(QObject, 'loginPassword').setProperty('text', 'test-password')
        QMetaObject.invokeMethod(self.root.findChild(QObject, 'loginButton'), 'clicked')

    def test_startup_has_only_login_and_no_operational_context(self):
        self.assertEqual(self.factory_calls, 0)
        self.assertIsNone(self.controller.operational_engine)
        self.assertIsNone(self.controller.login_engine.rootContext().contextProperty('customerVM'))
        self.assertTrue(self.root.property('visible'))
        self.assertEqual(self.warnings, [])

    def test_login_logout_destroy_and_recreate_operational_state(self):
        self.submit()
        self.assertEqual(self.root.findChild(QObject, 'loginPassword').property('text'), '')
        self.wait_until(lambda: self.controller.operational_engine is not None)
        self.assertTrue(self.controller.session.authenticated)
        self.assertEqual(self.controller.session.userId, '9007199254740993')
        self.assertEqual(self.factory_calls, 1)
        old_vm = self.controller.operational_viewmodels['billingVM']
        old_vm._sale_lines = [{'private_draft': True}]
        operational = self.controller.operational_engine.rootObjects()[0]
        for width, height in ((1280, 800), (1080, 680)):
            operational.setProperty('width', width)
            operational.setProperty('height', height)
            QTest.qWait(40)
        QMetaObject.invokeMethod(operational.findChild(QObject, 'logoutButton'), 'clicked')
        self.assertFalse(self.controller.session.authenticated)
        self.assertIsNone(self.controller.operational_engine)
        self.assertEqual(self.controller.operational_viewmodels, {})
        self.assertTrue(self.root.property('visible'))
        self.assertEqual(self.root.findChild(QObject, 'loginUsername').property('text'), '')
        self.submit()
        self.wait_until(lambda: self.controller.operational_engine is not None)
        self.assertEqual(self.factory_calls, 2)
        self.assertEqual(self.controller.operational_viewmodels['billingVM']._sale_lines, [])
        self.assertEqual(self.warnings, [])

    def test_worker_does_not_block_gui_and_logout_rejects_late_result(self):
        self.fake.gate.clear()
        self.submit()
        self.wait_until(self.fake.started.is_set)
        heartbeat = []
        QTimer.singleShot(10, lambda: heartbeat.append(True))
        QTest.qWait(40)
        self.assertTrue(heartbeat)
        self.assertNotEqual(self.fake.thread, get_ident())
        self.assertTrue(self.controller.authentication.busy)
        self.controller.authentication.logout()
        self.fake.gate.set()
        QTest.qWait(100)
        self.assertFalse(self.controller.session.authenticated)
        self.assertEqual(self.factory_calls, 0)

    def test_old_result_cannot_replace_newer_attempt(self):
        self.fake.gate.clear()
        self.submit()
        self.wait_until(self.fake.started.is_set)
        vm = self.controller.authentication
        old_request = vm._request
        old_generation = self.controller.session.generation
        vm.logout()
        self.fake.gate.set()
        self.submit()
        self.wait_until(lambda: self.controller.session.authenticated)
        current_generation = self.controller.session.generation
        forged_late = AuthenticationResult(7, None, 'old', frozenset({'APP_LOGIN'}), _VERIFIED)
        vm._completed(old_request, old_generation, forged_late, '')
        self.assertEqual(self.controller.session.userId, '9007199254740993')
        self.assertEqual(self.controller.session.generation, current_generation)

    def test_generic_failure_clears_password_and_does_not_load_main(self):
        self.fake.error = True
        self.submit()
        self.wait_until(lambda: not self.controller.authentication.busy)
        self.assertEqual(self.controller.authentication.errorMessage, GENERIC_ERROR)
        self.assertEqual(self.root.findChild(QObject, 'loginPassword').property('text'), '')
        self.assertEqual(self.factory_calls, 0)


if __name__ == '__main__':
    unittest.main()
