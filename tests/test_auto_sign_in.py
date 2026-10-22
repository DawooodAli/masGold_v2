"""Opening the program without the login window on one workstation: the local choice and the service rules (fakes, no database)."""
import contextlib
from contextlib import contextmanager
import os
import tempfile
import unittest
from unittest import mock

from PySide6.QtCore import QSettings

from config import workstation_settings as ws
from services.authentication_service import AuthenticationError, AuthenticationService, CHANGE_REQUIRED


class StoreTests(unittest.TestCase):
    def setUp(self):
        handle, self.path = tempfile.mkstemp(suffix=".ini")
        os.close(handle)
        self.addCleanup(lambda: os.path.exists(self.path) and os.remove(self.path))

    def test_it_is_off_by_default_and_can_be_set_and_cleared(self):
        store = QSettings(self.path, QSettings.IniFormat)
        self.assertIsNone(ws.auto_sign_in_user(store))
        ws.set_auto_sign_in_user("admin", store)
        self.assertEqual(ws.auto_sign_in_user(QSettings(self.path, QSettings.IniFormat)), "admin")
        ws.set_auto_sign_in_user("", store)
        self.assertIsNone(ws.auto_sign_in_user(store))


class Repo:
    state = None

    def __init__(self, connection=None):
        pass

    def find_credentials(self, username):
        return {"id": 1} if username == "admin" else None

    def get_identity(self, user_id, lock=False):
        return dict(self.state)

    def effective_permissions(self, user_id, lock=False):
        return frozenset({"APP_LOGIN", "SETTINGS_MANAGE"})

    def record_login(self, user_id):
        self.state["logins"] = self.state.get("logins", 0) + 1


class ServiceTests(unittest.TestCase):
    def build(self, **user):
        Repo.state = {"id": 1, "employee_id": None, "display_name": "admin", "is_active": True, "must_change_password": False, **user}

        @contextmanager
        def transaction():
            yield object()
        return AuthenticationService(transaction_factory=transaction, repository_factory=Repo)

    def chosen(self, name):
        """This workstation's choice only; the development switch (which a developer's .env may turn on) is held off so the test is about the workstation rule."""
        stack = contextlib.ExitStack()
        stack.enter_context(mock.patch("config.workstation_settings.auto_sign_in_user", return_value=name))
        stack.enter_context(mock.patch("config.dev_settings.dev_auto_login_username", return_value=None))
        return stack

    def test_the_chosen_account_opens_without_a_password_and_the_login_is_recorded(self):
        service = self.build()
        with self.chosen("admin"):
            result = service.establish_automatic_session("admin")
        self.assertEqual((result.user_id, "APP_LOGIN" in result.permissions, Repo.state["logins"]), (1, True, 1))

    def test_only_the_chosen_account_may_be_opened(self):
        service = self.build()
        with self.chosen(None):                                                         # nothing chosen: refused
            with self.assertRaises(AuthenticationError):
                service.establish_automatic_session("admin")
        with self.chosen("someone"):                                                    # another account chosen: refused
            with self.assertRaises(AuthenticationError):
                service.establish_automatic_session("admin")
        with self.chosen("admin"):
            for bad in ("", None, 5):
                with self.assertRaises(AuthenticationError):
                    service.establish_automatic_session(bad)

    def test_an_inactive_account_or_a_pending_password_change_still_stops_it(self):
        with self.chosen("admin"):
            with self.assertRaises(AuthenticationError):
                self.build(is_active=False).establish_automatic_session("admin")
            with self.assertRaises(AuthenticationError) as raised:
                self.build(must_change_password=True).establish_automatic_session("admin")
            self.assertEqual(str(raised.exception), CHANGE_REQUIRED)


if __name__ == "__main__":
    unittest.main()
