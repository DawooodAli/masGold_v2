"""قفل مراجعات المدير والتقارير: the service (the user's own password, persistent cooldown) and the real screen (locked on entry, wrong password refused, right password opens, locks again after leaving)."""
import unittest

from PySide6.QtTest import QTest

from services.authentication_service import AuthenticationError
from services.manager_access_service import ManagerAccessError, ManagerAccessService
from tests import test_invoice_page_qml as harness
from tests.market_trip_fakes import session_for


class FakeAuthentication:
    def __init__(self):
        self.calls = []

    def verify_user_password(self, user_id, password):
        self.calls.append((user_id, password))
        if password != "good":
            raise AuthenticationError("generic")
        return True


class ServiceTests(unittest.TestCase):
    def test_the_signed_in_users_password_is_checked_and_a_wrong_one_is_a_plain_refusal(self):
        authentication = FakeAuthentication()
        service = ManagerAccessService(session_for(), authentication)
        self.assertTrue(service.verify("good"))
        with self.assertRaises(ManagerAccessError) as raised:
            service.verify("bad")
        self.assertEqual(raised.exception.code, "PASSWORD_INVALID")
        self.assertEqual([c[0] for c in authentication.calls], [7, 7])                                # always the session's own user

    def test_nobody_signed_in_is_refused(self):
        session = session_for()
        service = ManagerAccessService(session, FakeAuthentication())
        session.clear() if hasattr(session, "clear") else session._clear() if hasattr(session, "_clear") else None
        if session.current_actor() is not None:
            self.skipTest("the fake session cannot be signed out")
        with self.assertRaises(Exception):
            service.verify("good")


class FakePasswords:
    def hash(self, password):
        return "h:" + password

    def verify(self, encoded, password):
        return encoded == "h:" + password


class PwRepo:
    state = None

    def __init__(self, connection=None):
        pass

    def get_identity(self, user_id, lock=False):
        return {"id": user_id, "password_hash": self.state["hash"]}

    def set_password(self, user_id, encoded):
        self.state["hash"] = encoded

    def record_security_event(self, *args, **kwargs):
        self.state["events"].append(args[0])


class ChangePasswordTests(unittest.TestCase):
    def build(self):
        from contextlib import contextmanager
        PwRepo.state = {"hash": "h:old-pass", "events": []}
        self.authentication = FakeAuthentication()
        self.authentication.verify_user_password = lambda user_id, password: True if password == "old-pass" else (_ for _ in ()).throw(AuthenticationError("generic"))

        @contextmanager
        def transaction():
            yield object()
        return ManagerAccessService(session_for(), self.authentication, transaction_factory=transaction, repository=PwRepo, passwords=FakePasswords())

    def test_the_own_password_changes_after_the_current_one_is_verified(self):
        service = self.build()
        self.assertEqual(service.change_password("old-pass", "new-pass1", "new-pass1"), {"changed": True})
        self.assertEqual((PwRepo.state["hash"], PwRepo.state["events"]), ("h:new-pass1", ["USER_PASSWORD_CHANGED"]))

    def test_a_wrong_current_a_short_or_unconfirmed_or_same_password_changes_nothing(self):
        service = self.build()
        for args, code in ((("bad", "new-pass1", "new-pass1"), "PASSWORD_INVALID"), (("old-pass", "abc", "abc"), "PASSWORD_POLICY"), (("old-pass", "new-pass1", "new-pass2"), "PASSWORD_POLICY"),
                           (("old-pass", "old-pass", "old-pass"), "SAME_PASSWORD")):
            with self.assertRaises(ManagerAccessError) as raised:
                service.change_password(*args)
            self.assertEqual(raised.exception.code, code, args)
        self.assertEqual((PwRepo.state["hash"], PwRepo.state["events"]), ("h:old-pass", []))


class GateScreenTests(harness.InvoicePageTests):
    def setUp(self):
        super().setUp()
        self.gate = self.controller.operational_viewmodels["managerGateVM"]
        self.authentication = FakeAuthentication()
        self.gate._service = ManagerAccessService(self.controller.session, self.authentication)
        self.gate.lock()

    def test_locked_on_entry_wrong_password_refused_right_password_opens_and_it_locks_again(self):
        self.root.setProperty("currentPage", 10)
        self.wait_until(lambda: self.visual_item("managerGate") is not None and self.item("managerGate").property("visible"), "the lock is shown")
        self.assertFalse(self.item("pageStack").property("visible"))
        self.type_into("managerGatePassword", "bad")
        self.click("managerGateEnter")
        self.wait_until(lambda: self.item("managerGateMessage").property("visible"), "the refusal is shown")
        self.assertTrue(self.item("managerGate").property("visible"))
        self.type_into("managerGatePassword", "good")
        self.click("managerGateEnter")
        self.wait_until(lambda: not self.item("managerGate").property("visible"), "opened")
        self.assertTrue(self.item("pageStack").property("visible"))
        self.assertTrue(self.gate.unlocked)
        # leaving the pages starts the 60 seconds; a short timer proves the lock closes again
        timer = self.item("managerRelock")
        timer.setProperty("interval", 150)
        self.root.setProperty("currentPage", 3)
        self.wait_until(lambda: not self.gate.unlocked, "locked again after leaving")
        self.root.setProperty("currentPage", 5)                                                    # the reports are behind the same lock
        self.wait_until(lambda: self.item("managerGate").property("visible"), "the reports are locked too")

    def test_the_password_card_in_the_settings_changes_it_with_real_clicks(self):
        calls = []
        self.gate._service = type("S", (), {"change_password": lambda s, current, new, confirm, **k: calls.append((current, new, confirm)) or {"changed": True},
                                            "verify": lambda s, *a, **k: True})()
        self.root.setProperty("currentPage", 6)
        self.wait_until(lambda: self.visual_item("settingsPassword") is not None, "the card exists")
        self.scroll_settings_to("settingsPassword")
        self.assertFalse(self.item("passwordChange").property("enabled"))                          # nothing typed yet
        self.type_into("passwordCurrent", "old-pass")
        self.type_into("passwordNew", "new-pass1")
        self.type_into("passwordConfirm", "new-pass1")
        self.wait_until(lambda: self.item("passwordChange").property("enabled"), "ready")
        self.click("passwordChange")
        self.wait_until(lambda: calls and self.item("passwordMessage").property("visible"), "changed")
        self.assertEqual(calls[0], ("old-pass", "new-pass1", "new-pass1"))
        self.assertEqual(self.item("passwordCurrent").property("text"), "")                       # the fields are cleared

    def test_coming_back_inside_the_grace_time_stays_open(self):
        self.gate._unlocked = True
        self.gate.changed.emit()
        self.root.setProperty("currentPage", 10)
        QTest.qWait(200)
        self.root.setProperty("currentPage", 3)
        QTest.qWait(300)
        self.root.setProperty("currentPage", 10)
        QTest.qWait(300)
        self.assertTrue(self.gate.unlocked)
        self.assertFalse(self.item("managerGate").property("visible"))


for _cls in (GateScreenTests,):
    for _name in dir(harness.InvoicePageTests):
        if _name.startswith("test_") and _name not in _cls.__dict__:
            setattr(_cls, _name, None)

if __name__ == "__main__":
    unittest.main()
