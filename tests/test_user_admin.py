"""Users / Roles / Permissions administration: service rules over an in-memory store (no database)."""
import copy
from contextlib import contextmanager
import unittest

from app.session import AppSession
from services.authentication_service import AuthenticationError, AuthenticationResult, _VERIFIED
from services.user_admin_service import PERMISSION, UserAdminError, UserAdminService

ALL = ["APP_LOGIN", "BILLING_USE", "SETTINGS_MANAGE", "USERS_MANAGE", "DEAD_CASH_MANAGE"]


class Store:
    def __init__(self):
        self.roles = {1: {"id": 1, "code": "OWNER", "name": "Owner", "is_system": 1, "is_active": 1},
                      2: {"id": 2, "code": "MANAGER", "name": "Manager", "is_system": 1, "is_active": 1},
                      3: {"id": 3, "code": "CASHIER", "name": "Cashier", "is_system": 1, "is_active": 1}}
        self.permissions = {c: {"id": i + 1, "code": c, "name": c, "description": "", "is_active": 1}
                            for i, c in enumerate(ALL)}
        self.grants = {1: set(ALL), 2: {"APP_LOGIN", "BILLING_USE"}, 3: {"APP_LOGIN"}}
        self.users = {10: {"id": 10, "username": "admin", "display_name": "المالك", "is_active": 1,
                           "employee_id": None, "password_hash": "$argon2id$HASH-A", "last_login_at": None},
                      11: {"id": 11, "username": "owner2", "display_name": "مالك ثان", "is_active": 1,
                           "employee_id": None, "password_hash": "$argon2id$HASH-B", "last_login_at": None},
                      12: {"id": 12, "username": "sara", "display_name": "سارة", "is_active": 1,
                           "employee_id": 5, "password_hash": "$argon2id$HASH-C", "last_login_at": None}}
        self.user_roles = {10: {1}, 11: {1}, 12: {3}}
        self.employees = {5: {"id": 5, "name": "سارة", "is_active": 1}, 6: {"id": 6, "name": "علي", "is_active": 1,
                                                                            "employee_code": "E6"}}
        self.audit = []
        self.next_user = 13


class Repo:
    def __init__(self, connection):
        self.s = connection.store

    # read models
    def users(self):
        return [dict(u, must_change_password=0, employee_name=(self.s.employees.get(u["employee_id"]) or {}).get("name"),
                     employee_active=1) for u in self.s.users.values()]

    def user_roles(self):
        return [{"user_id": u, "role_id": r, "code": self.s.roles[r]["code"], "name": self.s.roles[r]["name"],
                 "is_active": 1} for u, rs in self.s.user_roles.items() for r in sorted(rs)]

    def roles(self):
        return [dict(r, user_count=sum(r["id"] in rs for rs in self.s.user_roles.values()))
                for r in self.s.roles.values()]

    def role_grants(self):
        return [{"role_id": r, "code": c} for r, cs in self.s.grants.items() for c in sorted(cs)]

    def permissions(self):
        return list(self.s.permissions.values())

    def employees_without_user(self):
        used = {u["employee_id"] for u in self.s.users.values()}
        return [dict(e, employee_code=e.get("employee_code")) for e in self.s.employees.values() if e["id"] not in used]

    # locked reads
    def lock_user(self, user_id):
        return self.s.users.get(int(user_id))

    def lock_role_by_code(self, code):
        return next((r for r in self.s.roles.values() if r["code"] == code), None)

    def permission_by_code(self, code):
        return self.s.permissions.get(code)

    def role_codes_of(self, user_id, *, lock=False):
        return sorted(self.s.roles[r]["code"] for r in self.s.user_roles.get(int(user_id), set()))

    def active_owner_ids(self, *, lock=True):
        return {u for u, rs in self.s.user_roles.items() if 1 in rs and self.s.users[u]["is_active"]
                and "APP_LOGIN" in self.s.grants[1]}

    def username_exists(self, username):
        return any(u["username"] == username for u in self.s.users.values())

    def employee_state(self, employee_id):
        e = self.s.employees.get(int(employee_id))
        return None if e is None else {"id": e["id"], "is_active": e["is_active"],
                                       "has_user": any(u["employee_id"] == e["id"] for u in self.s.users.values())}

    def get_identity(self, user_id, lock=False):
        u = self.s.users.get(int(user_id))
        return None if u is None else {"id": u["id"], "password_hash": u["password_hash"], "is_active": u["is_active"]}

    # writes
    def set_user_active(self, user_id, active):
        self.s.users[int(user_id)]["is_active"] = int(bool(active))

    def replace_user_roles(self, user_id, role_ids):
        self.s.user_roles[int(user_id)] = {int(r) for r in role_ids}

    def grant(self, role_id, permission_id):
        self.s.grants[int(role_id)].add(next(c for c, p in self.s.permissions.items() if p["id"] == permission_id))

    def revoke(self, role_id, permission_id):
        self.s.grants[int(role_id)].discard(next(c for c, p in self.s.permissions.items() if p["id"] == permission_id))

    def set_password(self, user_id, encoded):
        self.s.users[int(user_id)]["password_hash"] = encoded

    def create_user(self, username, display_name, encoded, employee_id):
        user_id = self.s.next_user
        self.s.next_user += 1
        self.s.users[user_id] = {"id": user_id, "username": username, "display_name": display_name, "is_active": 1,
                                 "employee_id": employee_id, "password_hash": encoded, "last_login_at": None}
        return user_id

    def record_security_event(self, event_type, entity_type, entity_id, reference, description, metadata, *,
                              user_id=None, workstation=None):
        self.s.audit.append({"event": event_type, "entity": (entity_type, entity_id), "reference": reference,
                             "metadata": metadata, "description": description})


class Connection:
    def __init__(self, store):
        self.store = store


class Authorization:
    def __init__(self, session):
        self.session = session

    def _check(self, actor):
        if not self.session.is_current(actor) or PERMISSION not in actor.permissions:
            raise PermissionError("no permission")
        return actor

    def require_permission_read(self, connection, actor, code):
        return self._check(actor)

    require_permission_for_write = require_permission_read


class Authentication:
    def __init__(self):
        self.calls = 0

    def verify_user_password(self, user_id, password):
        self.calls += 1
        if password != "admin-pass":
            raise AuthenticationError("bad")
        return True


class Passwords:
    def hash(self, password):
        return "$argon2id$NEW:" + str(len(password))

    def verify(self, encoded, password):
        return encoded == "$argon2id$NEW:" + str(len(password)) and password == "same-password"


class UserAdminCase(unittest.TestCase):
    def make(self, permissions=frozenset({"APP_LOGIN", PERMISSION}), user_id=10):
        self.store = Store()
        self.session = AppSession()
        self.session._establish(AuthenticationResult(user_id, None, "x", permissions, _VERIFIED),
                                int(self.session.generation))

        @contextmanager
        def transaction():
            saved = copy.deepcopy(self.store.__dict__)
            try:
                yield Connection(self.store)
            except BaseException:
                self.store.__dict__.clear()
                self.store.__dict__.update(saved)
                raise
        self.auth = Authentication()
        self.service = UserAdminService(self.session, transaction_factory=transaction, repository=Repo,
                                        authorization=Authorization(self.session), authentication=self.auth,
                                        passwords=Passwords())
        return self.service

    def setUp(self):
        self.make()

    def refused(self, code, call):
        before = copy.deepcopy(self.store.__dict__)
        with self.assertRaises(UserAdminError) as context:
            call()
        self.assertEqual(context.exception.code, code)
        self.assertEqual(self.store.__dict__, before)                       # nothing changed

    OK = "admin-pass"


class OverviewTests(UserAdminCase):
    def test_overview_never_contains_a_hash_or_password(self):
        data = self.service.overview()
        self.assertEqual([u["username"] for u in data["users"]], ["admin", "owner2", "sara"])
        text = repr(data)
        for secret in ("HASH-A", "HASH-B", "argon2", "password_hash", "pin_hash"):
            self.assertNotIn(secret, text)
        owner = next(r for r in data["roles"] if r["code"] == "OWNER")
        self.assertEqual((owner["locked"], sorted(owner["permissions"])), (True, sorted(ALL)))
        self.assertTrue(next(u for u in data["users"] if u["username"] == "admin")["is_self"])
        self.assertEqual([e["name"] for e in data["employees"]], ["علي"])      # only employees without a user

    def test_reads_and_writes_need_users_manage(self):
        service = self.make(frozenset({"APP_LOGIN", "SETTINGS_MANAGE"}))
        with self.assertRaises(PermissionError):
            service.overview()
        with self.assertRaises(PermissionError):
            service.set_user_active("12", False, self.OK)
        self.assertEqual(self.auth.calls, 0)                                 # not even a password check


class ActivationTests(UserAdminCase):
    def test_deactivate_and_reactivate_with_audit(self):
        self.assertTrue(self.service.set_user_active("12", False, self.OK)["changed"])
        self.assertEqual(self.store.users[12]["is_active"], 0)
        self.service.set_user_active("12", True, self.OK)
        self.assertEqual([a["event"] for a in self.store.audit], ["USER_DEACTIVATED", "USER_ACTIVATED"])
        self.assertFalse(self.service.set_user_active("12", True, self.OK)["changed"])   # no-op: no audit row
        self.assertEqual(len(self.store.audit), 2)

    def test_wrong_password_changes_nothing(self):
        self.refused("PASSWORD_INVALID", lambda: self.service.set_user_active("12", False, "nope"))
        self.assertEqual(self.store.audit, [])

    def test_no_self_deactivation_and_never_the_last_owner(self):
        self.refused("SELF_DEACTIVATION", lambda: self.service.set_user_active("10", False, self.OK))
        self.service.set_user_active("11", False, self.OK)                  # one of two owners may go
        self.make(user_id=11)
        self.store.users[10]["is_active"] = 0                                # owner 10 inactive: 11 is the last owner
        self.store.users[11]["is_active"] = 1
        self.service = self.service
        other = self.make(user_id=12, permissions=frozenset({"APP_LOGIN", PERMISSION}))
        self.store.users[10]["is_active"] = 0
        self.refused("LAST_OWNER", lambda: other.set_user_active("11", False, self.OK))


class RoleAssignmentTests(UserAdminCase):
    def test_roles_are_replaced_and_audited_with_before_after(self):
        result = self.service.set_user_roles("12", ["CASHIER", "MANAGER"], self.OK)
        self.assertEqual((result["changed"], result["roles"]), (True, ["CASHIER", "MANAGER"]))
        self.assertEqual(self.store.user_roles[12], {2, 3})
        event = self.store.audit[-1]
        self.assertEqual((event["event"], event["metadata"]["before"], event["metadata"]["after"]),
                         ("USER_ROLES_CHANGED", ["CASHIER"], ["CASHIER", "MANAGER"]))

    def test_refusals(self):
        self.refused("ROLE_REQUIRED", lambda: self.service.set_user_roles("12", [], self.OK))
        self.refused("ROLE_INVALID", lambda: self.service.set_user_roles("12", ["GHOST"], self.OK))
        self.refused("SELF_ROLE_CHANGE", lambda: self.service.set_user_roles("10", ["CASHIER"], self.OK))
        self.refused("NOT_FOUND", lambda: self.service.set_user_roles("999", ["CASHIER"], self.OK))

    def test_last_owner_keeps_the_owner_role(self):
        self.store.users[10]["is_active"] = 0                                # owner2 (11) is now the only active owner
        service = self.make(user_id=12, permissions=frozenset({"APP_LOGIN", PERMISSION}))
        self.store.users[10]["is_active"] = 0
        self.refused("LAST_OWNER", lambda: service.set_user_roles("11", ["MANAGER"], self.OK))


class RoleGrantTests(UserAdminCase):
    def test_grant_and_revoke_for_a_non_owner_role(self):
        self.assertTrue(self.service.set_role_permission("CASHIER", "BILLING_USE", True, self.OK)["changed"])
        self.assertIn("BILLING_USE", self.store.grants[3])
        self.service.set_role_permission("CASHIER", "BILLING_USE", False, self.OK)
        self.assertNotIn("BILLING_USE", self.store.grants[3])
        self.assertEqual([a["event"] for a in self.store.audit], ["ROLE_PERMISSION_GRANTED", "ROLE_PERMISSION_REVOKED"])
        self.assertFalse(self.service.set_role_permission("CASHIER", "BILLING_USE", False, self.OK)["changed"])

    def test_owner_role_is_fixed_and_users_manage_is_owner_only(self):
        self.refused("OWNER_FIXED", lambda: self.service.set_role_permission("OWNER", "BILLING_USE", False, self.OK))
        self.refused("OWNER_ONLY", lambda: self.service.set_role_permission("MANAGER", "USERS_MANAGE", True, self.OK))
        self.refused("PERMISSION_INVALID", lambda: self.service.set_role_permission("MANAGER", "GHOST", True, self.OK))
        self.refused("ROLE_INVALID", lambda: self.service.set_role_permission("GHOST", "BILLING_USE", True, self.OK))
        self.assertEqual(self.store.grants[1], set(ALL))                     # the owner never loses an explicit grant


class CredentialTests(UserAdminCase):
    def test_reset_replaces_the_hash_and_audits_only_the_reason(self):
        self.service.reset_password("12", "new-secret-1", "new-secret-1", "نسيت كلمة المرور", self.OK)
        self.assertTrue(self.store.users[12]["password_hash"].startswith("$argon2id$"))
        self.assertNotEqual(self.store.users[12]["password_hash"], "$argon2id$HASH-C")
        event = self.store.audit[-1]
        self.assertEqual(event["event"], "USER_PASSWORD_RESET")
        self.assertEqual(event["metadata"]["reason"], "نسيت كلمة المرور")
        for value in (event["description"], repr(event["metadata"])):
            self.assertNotIn("new-secret-1", value)
            self.assertNotIn("argon2", value)

    def test_policy_reason_and_re_authentication(self):
        self.refused("PASSWORD_POLICY", lambda: self.service.reset_password("12", "123", "123", "x", self.OK))
        self.refused("PASSWORD_POLICY", lambda: self.service.reset_password("12", "abcdef1", "abcdef2", "x", self.OK))
        self.refused("TEXT_REQUIRED", lambda: self.service.reset_password("12", "abcdef1", "abcdef1", " ", self.OK))
        self.refused("REASON_HAS_SECRET", lambda: self.service.reset_password(
            "12", "abcdef1", "abcdef1", "كلمة abcdef1", self.OK))
        self.refused("PASSWORD_INVALID", lambda: self.service.reset_password("12", "abcdef1", "abcdef1", "س", "bad"))
        self.store.users[12]["password_hash"] = "$argon2id$NEW:13"           # the stored hash of "same-password"
        self.refused("SAME_PASSWORD", lambda: self.service.reset_password(
            "12", "same-password", "same-password", "س", self.OK))

    def test_create_user(self):
        result = self.service.create_user("cashier1", "أمين صندوق", "6", "CASHIER", "secret-123", "secret-123", self.OK)
        user = self.store.users[int(result["user_id"])]
        self.assertEqual((user["username"], user["employee_id"], self.store.user_roles[user["id"]]), ("cashier1", 6, {3}))
        self.assertTrue(user["password_hash"].startswith("$argon2id$"))
        self.assertEqual(self.store.audit[-1]["event"], "USER_CREATED")
        self.assertNotIn("secret-123", repr(self.store.audit[-1]))

    def test_create_user_refusals(self):
        args = lambda **kw: dict(dict(username="cashier1", display="س", employee="", role="CASHIER",
                                      password="secret-123", confirm="secret-123", admin=self.OK), **kw)

        def go(**kw):
            a = args(**kw)
            return lambda: self.service.create_user(a["username"], a["display"], a["employee"], a["role"],
                                                    a["password"], a["confirm"], a["admin"])
        self.refused("USERNAME_INVALID", go(username="ab"))
        self.refused("USERNAME_INVALID", go(username="أحمد"))
        self.refused("USERNAME_TAKEN", go(username="sara"))
        self.refused("ROLE_INVALID", go(role="GHOST"))
        self.refused("EMPLOYEE_TAKEN", go(employee="5"))
        self.refused("EMPLOYEE_INVALID", go(employee="99"))
        self.refused("PASSWORD_POLICY", go(confirm="different"))
        self.refused("PASSWORD_INVALID", go(admin="bad"))


if __name__ == "__main__":
    unittest.main()
