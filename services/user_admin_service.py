"""Users / Roles / Permissions administration (Settings → المستخدمون والصلاحيات).

Reuses the existing authorization model only — users, roles, permissions, user_roles, role_permissions,
AppSession, AuthorizationService, Argon2id (PasswordService) and the audit_log. No parallel system and no
schema change. Everything is gated by the existing `USERS_MANAGE` permission (OWNER only today):

  reads   non-locking re-check of USERS_MANAGE from the database (never the screen's visibility)
  writes  1. the actor's CURRENT password is re-verified first (AuthenticationService.verify_user_password,
             one instance per service so its cooldown really applies), then
          2. the strict locking write check inside the transaction (identity and grants cannot change under it)

Safety rules (each refused with a stable code, before any change):
  * the OWNER role's grants are fixed (never silently weakened, never a bypass: the OWNER role keeps its
    explicit grants like every other role);
  * USERS_MANAGE can only belong to the OWNER role (no privilege escalation through another role);
  * a user can neither deactivate, nor re-role themselves (no self-lockout);
  * the last active OWNER can never be deactivated or lose the OWNER role (locked count);
  * a password reset never reveals or stores anything: the hash is replaced, the audit row carries the reason only.

No password, hash or PIN is ever returned by a read model, logged, or placed in an audit row or exception.
"""
import re

from repositories.user_admin_repository import UserAdminRepository
from services.authorization_service import AuthorizationService
from services.invoice_service import invoice_transaction
from services.password_policy import validate_new_password
from services.password_service import PasswordService

PERMISSION = "USERS_MANAGE"
OWNER = "OWNER"
OWNER_ONLY_PERMISSIONS = frozenset({PERMISSION})
USERNAME = re.compile(r"[A-Za-z0-9._-]{3,50}")
PASSWORD_INVALID = "كلمة مرورك الحالية غير صحيحة أو انتظر قليلاً ثم أعد المحاولة."


class UserAdminError(ValueError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


class _Discard(Exception):
    pass


def _text(value, label, *, limit, required=True):
    text = value.strip() if isinstance(value, str) else ""
    if required and not text:
        raise UserAdminError("TEXT_REQUIRED", f"اكتب {label}.")
    if len(text) > limit:
        raise UserAdminError("TEXT_TOO_LONG", f"{label} طويل جداً.")
    return text


def _id(value, label="المستخدم"):
    if isinstance(value, bool) or not str(value or "").isdigit() or not 0 < int(value) <= 4294967295:
        raise UserAdminError("NOT_FOUND", f"{label} غير موجود.")
    return int(value)


class UserAdminService:
    def __init__(self, session, *, transaction_factory=invoice_transaction, repository=UserAdminRepository,
                 authorization=None, authentication=None, passwords=None):
        self._session, self._transaction, self._repository = session, transaction_factory, repository
        self._authorization = authorization or AuthorizationService(session)
        self._authentication = authentication
        self._passwords = passwords or PasswordService()

    # ------------------------------------------------------------ plumbing
    def _actor(self, expected_actor):
        actor = self._session.require_authenticated()
        if expected_actor is not None and not self._session.is_current(expected_actor):
            raise PermissionError("انتهت جلسة الدخول.")
        if PERMISSION not in actor.permissions:
            raise PermissionError("إدارة المستخدمين والصلاحيات للمالك فقط.")
        return actor

    def can_manage(self):
        actor = self._session.current_actor()
        return bool(actor and PERMISSION in actor.permissions)

    def _confirm_password(self, actor, password):
        """Re-authenticate the logged-in administrator BEFORE any lock is taken (Argon2 is deliberately slow)."""
        from services.authentication_service import AuthenticationError, AuthenticationService
        if self._authentication is None:
            self._authentication = AuthenticationService()      # kept: its failure cooldown spans attempts
        try:
            self._authentication.verify_user_password(int(actor.user_id), password)
        except AuthenticationError:
            raise UserAdminError("PASSWORD_INVALID", PASSWORD_INVALID) from None

    def _write(self, expected_actor, password, work):
        session_actor = self._actor(expected_actor)
        self._confirm_password(session_actor, password)
        with self._transaction() as connection:
            actor = self._authorization.require_permission_for_write(connection, session_actor, PERMISSION)
            result = work(self._repository(connection), actor)
            if not self._session.is_current(session_actor):
                raise PermissionError("انتهت جلسة الدخول.")
        return result

    @staticmethod
    def _audit(repository, actor, event, user_id, reference, description, metadata):
        repository.record_security_event(event, "USER", int(user_id), reference, description,
                                         dict(metadata, actor_user_id=actor.user_id), user_id=actor.user_id)

    # ------------------------------------------------------------ read model
    def overview(self, *, _expected_actor=None):
        actor = self._actor(_expected_actor)
        try:
            with self._transaction() as connection:
                self._authorization.require_permission_read(connection, actor, PERMISSION)
                repository = self._repository(connection)
                grants, memberships = {}, {}
                for row in repository.role_grants():
                    grants.setdefault(int(row["role_id"]), []).append(row["code"])
                for row in repository.user_roles():
                    memberships.setdefault(int(row["user_id"]), []).append(
                        {"code": row["code"], "name": row["name"], "active": bool(row["is_active"])})
                owners = repository.active_owner_ids(lock=False)
                raise _Discard({
                    "current_user_id": str(actor.user_id),
                    "users": [{"id": str(u["id"]), "username": u["username"], "display_name": u["display_name"],
                               "is_active": bool(u["is_active"]), "last_login_at": u["last_login_at"],
                               "employee_id": None if u["employee_id"] is None else str(u["employee_id"]),
                               "employee_name": u["employee_name"],
                               "employee_active": None if u["employee_id"] is None else bool(u["employee_active"]),
                               "roles": memberships.get(int(u["id"]), []),
                               "is_owner": int(u["id"]) in owners,
                               "is_self": int(u["id"]) == int(actor.user_id)}
                              for u in repository.users()],
                    "roles": [{"id": str(r["id"]), "code": r["code"], "name": r["name"],
                               "is_system": bool(r["is_system"]), "is_active": bool(r["is_active"]),
                               "user_count": int(r["user_count"]), "locked": r["code"] == OWNER,
                               "permissions": grants.get(int(r["id"]), [])} for r in repository.roles()],
                    "permissions": [{"code": p["code"], "name": p["name"], "description": p["description"] or "",
                                     "owner_only": p["code"] in OWNER_ONLY_PERMISSIONS}
                                    for p in repository.permissions()],
                    "employees": [{"id": str(e["id"]), "name": e["name"], "code": e["employee_code"] or ""}
                                  for e in repository.employees_without_user()]})
        except _Discard as done:
            return done.args[0]

    # ------------------------------------------------------------ activation
    def set_user_active(self, user_id, active, password, *, _expected_actor=None):
        target_id, active = _id(user_id), bool(active)

        def work(repository, actor):
            user = repository.lock_user(target_id)
            if user is None:
                raise UserAdminError("NOT_FOUND", "المستخدم غير موجود.")
            if bool(user["is_active"]) == active:
                return {"user_id": str(target_id), "changed": False, "active": active}
            if not active:
                if target_id == int(actor.user_id):
                    raise UserAdminError("SELF_DEACTIVATION", "لا يمكنك تعطيل حسابك أنت.")
                owners = repository.active_owner_ids(lock=True)
                if target_id in owners and not owners - {target_id}:
                    raise UserAdminError("LAST_OWNER", "لا يمكن تعطيل آخر مالك نشط.")
            repository.set_user_active(target_id, active)
            self._audit(repository, actor, "USER_ACTIVATED" if active else "USER_DEACTIVATED", target_id,
                        user["username"], ("تفعيل المستخدم " if active else "تعطيل المستخدم ") + user["username"],
                        {"active": active})
            return {"user_id": str(target_id), "changed": True, "active": active}
        return self._write(_expected_actor, password, work)

    # ------------------------------------------------------------ roles of a user
    def set_user_roles(self, user_id, role_codes, password, *, _expected_actor=None):
        target_id = _id(user_id)
        codes = sorted({str(c) for c in (role_codes or [])})
        if not codes:
            raise UserAdminError("ROLE_REQUIRED", "اختر دوراً واحداً على الأقل للمستخدم.")

        def work(repository, actor):
            if target_id == int(actor.user_id):
                raise UserAdminError("SELF_ROLE_CHANGE", "لا يمكنك تغيير أدوار حسابك أنت.")
            roles = []
            for code in codes:                                       # ascending code: one lock order
                role = repository.lock_role_by_code(code)
                if role is None or not role["is_active"]:
                    raise UserAdminError("ROLE_INVALID", "الدور غير موجود أو غير نشط.")
                roles.append(role)
            user = repository.lock_user(target_id)
            if user is None:
                raise UserAdminError("NOT_FOUND", "المستخدم غير موجود.")
            before = repository.role_codes_of(target_id, lock=True)
            if before == codes:
                return {"user_id": str(target_id), "changed": False, "roles": codes}
            if OWNER in before and OWNER not in codes and user["is_active"]:
                owners = repository.active_owner_ids(lock=True)
                if target_id in owners and not owners - {target_id}:
                    raise UserAdminError("LAST_OWNER", "لا يمكن سحب دور المالك من آخر مالك نشط.")
            repository.replace_user_roles(target_id, [r["id"] for r in roles])
            self._audit(repository, actor, "USER_ROLES_CHANGED", target_id, user["username"],
                        f"تغيير أدوار المستخدم {user['username']}", {"before": before, "after": codes})
            return {"user_id": str(target_id), "changed": True, "roles": codes}
        return self._write(_expected_actor, password, work)

    # ------------------------------------------------------------ grants of a role
    def set_role_permission(self, role_code, permission_code, granted, password, *, _expected_actor=None):
        role_code, permission_code, granted = str(role_code or ""), str(permission_code or ""), bool(granted)

        def work(repository, actor):
            role = repository.lock_role_by_code(role_code)
            if role is None:
                raise UserAdminError("ROLE_INVALID", "الدور غير موجود.")
            if role["code"] == OWNER:
                raise UserAdminError("OWNER_FIXED", "صلاحيات دور المالك ثابتة ولا تُعدّل من الشاشة.")
            permission = repository.permission_by_code(permission_code)
            if permission is None or not permission["is_active"]:
                raise UserAdminError("PERMISSION_INVALID", "الصلاحية غير موجودة.")
            if granted and permission_code in OWNER_ONLY_PERMISSIONS:
                raise UserAdminError("OWNER_ONLY", "هذه الصلاحية للمالك فقط ولا تُمنح لدور آخر.")
            current = {row["code"] for row in repository.role_grants() if int(row["role_id"]) == int(role["id"])}
            if granted == (permission_code in current):
                return {"role": role_code, "permission": permission_code, "changed": False, "granted": granted}
            (repository.grant if granted else repository.revoke)(role["id"], permission["id"])
            repository.record_security_event(
                "ROLE_PERMISSION_GRANTED" if granted else "ROLE_PERMISSION_REVOKED", "ROLE", int(role["id"]),
                role_code, ("منح الصلاحية " if granted else "سحب الصلاحية ") + f"{permission_code} — {role_code}",
                {"role": role_code, "permission": permission_code, "granted": granted,
                 "actor_user_id": actor.user_id}, user_id=actor.user_id)
            return {"role": role_code, "permission": permission_code, "changed": True, "granted": granted}
        return self._write(_expected_actor, password, work)

    # ------------------------------------------------------------ credentials
    def reset_password(self, user_id, new_password, confirmation, reason, password, *, _expected_actor=None):
        target_id = _id(user_id)
        session_actor = self._actor(_expected_actor)
        try:
            validate_new_password(new_password, confirmation)
        except ValueError as exc:
            raise UserAdminError("PASSWORD_POLICY", str(exc)) from None
        reason_text = _text(reason, "سبب إعادة تعيين كلمة المرور", limit=500)
        if new_password in reason_text:
            raise UserAdminError("REASON_HAS_SECRET", "لا تكتب كلمة المرور في السبب.")
        self._confirm_password(session_actor, password)
        encoded = self._passwords.hash(new_password)              # slow Argon2 work before any row lock
        with self._transaction() as connection:
            actor = self._authorization.require_permission_for_write(connection, session_actor, PERMISSION)
            repository = self._repository(connection)
            user = repository.lock_user(target_id)
            if user is None:
                raise UserAdminError("NOT_FOUND", "المستخدم غير موجود.")
            identity = repository.get_identity(target_id, lock=True)
            if identity and self._passwords.verify(identity["password_hash"], new_password):
                raise UserAdminError("SAME_PASSWORD", "كلمة المرور الجديدة مطابقة للحالية.")
            repository.set_password(target_id, encoded)
            self._audit(repository, actor, "USER_PASSWORD_RESET", target_id, user["username"],
                        f"إعادة تعيين كلمة مرور {user['username']}", {"reason": reason_text})
            if not self._session.is_current(session_actor):
                raise PermissionError("انتهت جلسة الدخول.")
        return {"user_id": str(target_id), "changed": True}

    # ------------------------------------------------------------ a new user
    def create_user(self, username, display_name, employee_id, role_code, new_password, confirmation, password,
                    *, _expected_actor=None):
        session_actor = self._actor(_expected_actor)
        name = (username or "").strip() if isinstance(username, str) else ""
        if not USERNAME.fullmatch(name):
            raise UserAdminError("USERNAME_INVALID", "اسم المستخدم: ٣ إلى ٥٠ حرفاً إنجليزياً أو أرقاماً أو . _ -")
        shown = _text(display_name, "اسم العرض", limit=100)
        try:
            validate_new_password(new_password, confirmation)
        except ValueError as exc:
            raise UserAdminError("PASSWORD_POLICY", str(exc)) from None
        employee = None if employee_id in (None, "") else _id(employee_id, "الموظف")
        self._confirm_password(session_actor, password)
        encoded = self._passwords.hash(new_password)
        with self._transaction() as connection:
            actor = self._authorization.require_permission_for_write(connection, session_actor, PERMISSION)
            repository = self._repository(connection)
            role = repository.lock_role_by_code(str(role_code or ""))
            if role is None or not role["is_active"]:
                raise UserAdminError("ROLE_INVALID", "الدور غير موجود أو غير نشط.")
            if repository.username_exists(name):
                raise UserAdminError("USERNAME_TAKEN", "اسم المستخدم مستخدم من قبل.")
            if employee is not None:
                state = repository.employee_state(employee)
                if state is None or not state["is_active"]:
                    raise UserAdminError("EMPLOYEE_INVALID", "الموظف غير موجود أو غير نشط.")
                if state["has_user"]:
                    raise UserAdminError("EMPLOYEE_TAKEN", "هذا الموظف مرتبط بمستخدم آخر.")
            user_id = repository.create_user(name, shown, encoded, employee)
            repository.replace_user_roles(user_id, [role["id"]])
            self._audit(repository, actor, "USER_CREATED", user_id, name, f"إنشاء المستخدم {name}",
                        {"role": role["code"], "employee_id": employee})
            if not self._session.is_current(session_actor):
                raise PermissionError("انتهت جلسة الدخول.")
        return {"user_id": str(user_id), "username": name}
