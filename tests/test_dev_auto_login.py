"""TEMPORARY DEVELOPMENT AUTO-LOGIN — DISABLE BEFORE PRODUCTION.

The development bypass signs in a real, verified session; the normal Login
still works when it is disabled. No live database is used.
"""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
os.environ.setdefault('QT_QUICK_BACKEND', 'software')
os.environ.setdefault('QML_DISABLE_DISK_CACHE', '1')

from contextlib import contextmanager
from pathlib import Path
import unittest
from unittest.mock import MagicMock, patch

from PySide6.QtCore import QCoreApplication, QEvent, Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtQuickControls2 import QQuickStyle
from PySide6.QtTest import QTest

from app.session import AppSession
from config import dev_settings
from main import ApplicationController, create_operational_viewmodels
from services.authentication_service import (
    AuthenticationError, AuthenticationResult, AuthenticationService, CHANGE_REQUIRED, GENERIC_ERROR, _VERIFIED,
)
from services.authorization_service import AuthorizationService
from tests import test_authentication_qml as auth_fixture


OPT_IN = {"MASGOLD_DEV_AUTO_LOGIN": "1"}
DEV_ENV = {**OPT_IN, "MASGOLD_ENV": "development", "MASGOLD_DB_NAME": "masgold_v2"}


class DevSettingsTests(unittest.TestCase):
    def test_disabled_by_default_even_from_source_in_a_non_production_database(self):
        # Fail closed: running from source, a development-looking environment or a non-production database
        # name never enable the bypass by themselves.
        for env in ({}, {"MASGOLD_ENV": "development"}, {"MASGOLD_DB_NAME": "masgold_v2"},
                    {"MASGOLD_ENV": "development", "MASGOLD_DB_NAME": "masgold_v2"},
                    {"MASGOLD_DB_NAME": "anything_else"}):
            self.assertIsNone(dev_settings.dev_auto_login_username(env, frozen=False), env)
        with patch.dict(os.environ, {}, clear=True):                     # the real process environment, nothing set
            self.assertIsNone(dev_settings.dev_auto_login_username(frozen=False))

    def test_only_an_affirmative_explicit_opt_in_enables_it(self):
        for value in ("", "0", "false", "no", "true", "yes", "on", "2", " "):
            self.assertIsNone(dev_settings.dev_auto_login_username({"MASGOLD_DEV_AUTO_LOGIN": value}, frozen=False),
                              repr(value))
        self.assertEqual(dev_settings.dev_auto_login_username(OPT_IN, frozen=False), "admin")
        self.assertEqual(dev_settings.dev_auto_login_username({"MASGOLD_DEV_AUTO_LOGIN": " 1 "}, frozen=False), "admin")
        self.assertEqual(dev_settings.dev_auto_login_username(DEV_ENV, frozen=False), "admin")

    def test_never_in_production_release_or_protected_database_even_when_opted_in(self):
        for env in ({"MASGOLD_ENV": "production"}, {"MASGOLD_ENV": "Release"}, {"MASGOLD_ENV": " prod "},
                    {"MASGOLD_DB_NAME": "masgold_prod"}):
            self.assertIsNone(dev_settings.dev_auto_login_username({**OPT_IN, **env}, frozen=False), env)
        self.assertIsNone(dev_settings.dev_auto_login_username(DEV_ENV, frozen=True))   # packaged build

    def test_removing_the_single_opt_in_disables_it(self):
        self.assertEqual(dev_settings.dev_auto_login_username(DEV_ENV, frozen=False), "admin")
        without = {k: v for k, v in DEV_ENV.items() if k != "MASGOLD_DEV_AUTO_LOGIN"}
        self.assertIsNone(dev_settings.dev_auto_login_username(without, frozen=False))

    def test_there_is_no_code_level_default_on_flag(self):
        self.assertFalse(hasattr(dev_settings, "DEV_AUTO_LOGIN"))          # one switch only: the environment

    def test_marker_and_no_password_in_settings(self):
        source = Path(dev_settings.__file__).read_text(encoding="utf-8")
        self.assertIn("TEMPORARY DEVELOPMENT AUTO-LOGIN — DISABLE BEFORE PRODUCTION", source)
        self.assertNotIn("PASSWORD", source.upper().replace("PASSWORD CHANGE", "").replace("NO PASSWORD", ""))


class DevelopmentSessionServiceTests(unittest.TestCase):
    def setUp(self):
        block = patch('database.connection.DatabaseConnection.connect',
                      side_effect=AssertionError('Live database forbidden'))
        block.start()
        self.addCleanup(block.stop)
        env = patch.dict(os.environ, DEV_ENV)
        env.start()
        self.addCleanup(env.stop)
        self.user = dict(id=3, employee_id=5, employee_active=True, display_name='مدير التطوير',
                         is_active=True, must_change_password=False, password_hash='$argon2id$stored')
        self.repo = MagicMock()
        self.repo.find_credentials.return_value = dict(id=3, password_hash='$argon2id$stored')
        self.repo.get_identity.side_effect = lambda *a, **kw: dict(self.user)
        self.repo.effective_permissions.return_value = frozenset({'APP_LOGIN', 'INVOICE_FINALIZE'})
        self.commits = 0

        @contextmanager
        def transaction():
            yield object()
            self.commits += 1
        self.service = AuthenticationService(transaction, lambda c: self.repo)

    def test_real_account_identity_permissions_and_recorded_login(self):
        result = self.service.establish_development_session("admin")
        self.assertEqual((result.user_id, result.employee_id, result.display_name, result.permissions),
                         (3, 5, 'مدير التطوير', frozenset({'APP_LOGIN', 'INVOICE_FINALIZE'})))
        self.assertIs(result._proof, _VERIFIED)
        self.repo.find_credentials.assert_called_once_with("admin")
        self.repo.record_login.assert_called_once_with(3)
        self.assertEqual(self.commits, 1)
        session = AppSession()
        self.assertTrue(session._establish(result, int(session.generation)))

    def test_refused_when_disabled_or_for_another_account(self):
        with patch.dict(os.environ, {"MASGOLD_DEV_AUTO_LOGIN": "0"}):
            with self.assertRaises(AuthenticationError):
                self.service.establish_development_session("admin")
        with patch.dict(os.environ, {"MASGOLD_ENV": "production"}):
            with self.assertRaises(AuthenticationError):
                self.service.establish_development_session("admin")
        with self.assertRaises(AuthenticationError):
            self.service.establish_development_session("someone_else")
        self.repo.find_credentials.assert_not_called()
        self.repo.record_login.assert_not_called()

    def test_every_normal_account_check_still_applies(self):
        cases = [("is_active", False, GENERIC_ERROR), ("employee_active", False, GENERIC_ERROR),
                 ("must_change_password", True, CHANGE_REQUIRED)]
        for key, value, message in cases:
            original = self.user[key]
            self.user[key] = value
            with self.assertRaises(AuthenticationError) as denied:
                self.service.establish_development_session("admin")
            self.assertEqual(str(denied.exception), message, key)
            self.user[key] = original
        self.repo.effective_permissions.return_value = frozenset({'SETTINGS_MANAGE'})
        with self.assertRaises(AuthenticationError):
            self.service.establish_development_session("admin")
        self.repo.find_credentials.return_value = None
        with self.assertRaises(AuthenticationError):
            self.service.establish_development_session("admin")
        self.repo.record_login.assert_not_called()


class DevAuthentication(auth_fixture.FakeAuthentication):
    def __init__(self):
        super().__init__()
        self.dev_calls = []

    def establish_development_session(self, username):
        self.dev_calls.append(username)
        return AuthenticationResult(3, 5, 'مدير التطوير',
                                    frozenset({'APP_LOGIN', 'SETTINGS_MANAGE', 'INVOICE_FINALIZE'}), _VERIFIED)


class DevAutoLoginStartupTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if QGuiApplication.instance() is None:
            QQuickStyle.setStyle('Material')
        cls.app = QGuiApplication.instance() or QGuiApplication([])
        cls.app.setQuitOnLastWindowClosed(False)
        cls.app.setLayoutDirection(Qt.RightToLeft)

    def setUp(self):
        block = patch('database.connection.DatabaseConnection.connect',
                      side_effect=AssertionError('Live database forbidden'))
        block.start()
        self.addCleanup(block.stop)
        self.fake = DevAuthentication()
        self.controller = None

    def start(self, dev_auto_login):
        def factory():
            empty = auth_fixture.EmptyService
            with patch('app.viewmodels.master_data_viewmodel.MasterDataService', empty), \
                    patch('app.viewmodels.inventory_viewmodel.InventoryService', empty), \
                    patch('app.viewmodels.old_gold_conversion_viewmodel.OldGoldConversionService', empty), \
                    patch('app.viewmodels.barcode_viewmodel.BarcodePrintService', empty), \
                    patch('app.viewmodels.billing_viewmodel.BillingService', empty):
                return create_operational_viewmodels()
        self.controller = ApplicationController(self.app, authentication_service=self.fake,
                                                operational_factory=factory,
                                                gold_price_service=auth_fixture.EmptyGoldPrices())
        # An injected service never reads the setting; the tests switch the bypass on explicitly
        # before the startup check runs (it is queued on the event loop).
        self.controller.authentication._dev_auto_login = dev_auto_login

    def tearDown(self):
        if self.controller is None:
            return
        self.controller.authentication.shutdown()
        self.controller.gold_prices.shutdown()
        QTest.qWait(30)
        for engine in (self.controller.login_engine, self.controller.setup_engine, self.controller.operational_engine):
            for root in (engine.rootObjects() if engine is not None else []):
                root.hide()
        self.controller.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)

    def wait_until(self, condition, message='timed out'):
        for _ in range(150):
            if condition():
                return
            QTest.qWait(20)
        self.fail(message)

    def test_dev_mode_opens_main_window_with_a_verified_session(self):
        self.start("admin")
        self.wait_until(lambda: self.controller.operational_engine is not None
                        and self.controller.operational_engine.rootObjects(), "MainWindow not opened")
        self.assertEqual(self.fake.dev_calls, ["admin"])
        self.assertEqual(self.controller.state, "OPERATIONAL")
        self.assertFalse(self.controller.login_engine.rootObjects()[0].property("visible"))
        actor = self.controller.session.require_authenticated()
        self.assertEqual((actor.user_id, actor.employee_id), (3, 5))
        self.assertIn("INVOICE_FINALIZE", actor.permissions)
        # Billing sees the same actor object; service-side authorization re-checks it.
        invoice_vm = self.controller.operational_viewmodels["invoiceVM"]
        self.assertIs(invoice_vm._session.current_actor(), actor)
        repo = MagicMock()
        repo.get_identity.return_value = dict(id=3, employee_id=5, employee_active=True, display_name='مدير التطوير',
                                              is_active=True, must_change_password=False)
        repo.effective_permissions.return_value = frozenset({'APP_LOGIN', 'INVOICE_FINALIZE'})
        checked = AuthorizationService(self.controller.session, lambda c: repo).require_permission(
            object(), actor, "INVOICE_FINALIZE", require_employee=True)
        self.assertEqual((checked.user_id, checked.employee_id), (3, 5))
        repo.effective_permissions.return_value = frozenset({'APP_LOGIN'})
        with self.assertRaises(PermissionError):                   # permissions are not bypassed
            AuthorizationService(self.controller.session, lambda c: repo).require_permission(
                object(), actor, "INVOICE_FINALIZE")

    def test_logout_after_dev_login_shows_normal_login_and_does_not_sign_in_again(self):
        self.start("admin")
        self.wait_until(lambda: self.controller.state == "OPERATIONAL")
        actor = self.controller.session.require_authenticated()
        self.controller.authentication.logout()
        self.wait_until(lambda: self.controller.state == "LOGIN")
        QTest.qWait(100)
        self.assertFalse(self.controller.session.authenticated)
        self.assertFalse(self.controller.session.is_current(actor))
        self.assertEqual(self.fake.dev_calls, ["admin"])
        self.assertTrue(self.controller.login_engine.rootObjects()[0].property("visible"))

    def test_normal_mode_still_shows_login(self):
        self.start(None)
        self.wait_until(lambda: self.controller.state == "LOGIN")
        QTest.qWait(60)
        self.assertEqual(self.fake.dev_calls, [])
        self.assertIsNone(self.controller.operational_engine)
        self.assertFalse(self.controller.session.authenticated)
        self.assertTrue(self.controller.login_engine.rootObjects()[0].property("visible"))

    def test_failed_dev_login_falls_back_to_normal_login(self):
        def refuse(username):
            raise AuthenticationError(GENERIC_ERROR)
        self.fake.establish_development_session = refuse
        self.start("admin")
        self.wait_until(lambda: self.controller.state == "LOGIN")
        self.assertFalse(self.controller.session.authenticated)
        self.assertIsNone(self.controller.operational_engine)

    def test_setting_is_read_only_for_the_real_service(self):
        with patch.dict(os.environ, {"MASGOLD_ENV": "development", "MASGOLD_DB_NAME": "masgold_v2",
                                     "MASGOLD_DEV_AUTO_LOGIN": "0"}):         # no opt-in: the normal Login window
            controller = ApplicationController(self.app, gold_price_service=auth_fixture.EmptyGoldPrices())
            self.assertIsNone(controller.authentication._dev_auto_login)
            self._dispose(controller)
        with patch.dict(os.environ, DEV_ENV):
            controller = ApplicationController(self.app, gold_price_service=auth_fixture.EmptyGoldPrices())
            self.assertEqual(controller.authentication._dev_auto_login, "admin")
            self._dispose(controller)
        self.start(None)                                           # injected service: setting ignored
        self.assertIsNone(self.controller.authentication._dev_auto_login)

    def _dispose(self, controller):
        controller.authentication._dev_auto_login = None           # never reach the database
        controller.authentication.shutdown()
        controller.gold_prices.shutdown()
        for engine in (controller.login_engine, controller.setup_engine):
            for root in engine.rootObjects():
                root.hide()
        controller.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)


if __name__ == '__main__':
    unittest.main()
