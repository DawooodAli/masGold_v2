"""قفل مراجعات المدير والتقارير: the page asks for the signed-in user's own login password (owner decision: the same password as the admin login, no separate secret).

The check is the same re-authentication the Dead Cash write-off uses: AuthenticationService.verify_user_password (Argon2id, generic error, a failure cooldown). ONE AuthenticationService lives as long as this
service so the cooldown spans attempts. The password is never stored or logged. This is a convenience lock in front of the screen on top of the permissions every service still checks; it grants no right."""
from repositories.user_admin_repository import UserAdminRepository
from services.authentication_service import AuthenticationError, AuthenticationService
from services.invoice_service import invoice_transaction
from services.password_policy import validate_new_password
from services.password_service import PasswordService

WRONG = "كلمة المرور غير صحيحة أو انتظر قليلاً ثم أعد المحاولة."


class ManagerAccessError(ValueError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


class ManagerAccessService:
    def __init__(self, session, authentication=None, *, transaction_factory=invoice_transaction, repository=UserAdminRepository, passwords=None):
        self._session = session
        self._authentication = authentication
        self._transaction, self._repository = transaction_factory, repository
        self._passwords = passwords or PasswordService()

    def verify(self, password, *, _expected_actor=None):
        actor = self._session.require_authenticated()
        if _expected_actor is not None and not self._session.is_current(_expected_actor):
            raise PermissionError("انتهت جلسة الدخول.")
        if self._authentication is None:
            self._authentication = AuthenticationService()
        try:
            self._authentication.verify_user_password(int(actor.user_id), password)
        except AuthenticationError:
            raise ManagerAccessError("PASSWORD_INVALID", WRONG) from None
        if not self._session.is_current(actor):
            raise PermissionError("انتهت جلسة الدخول.")
        return True

    def change_password(self, current, new_password, confirmation, *, _expected_actor=None):
        """The signed-in user changes their OWN login password (it is also the manager-lock password): the current one is re-verified first (same cooldown), the new one follows the password policy and
        must differ; only the hash is replaced and the audit row carries no secret."""
        actor = self._session.require_authenticated()
        if _expected_actor is not None and not self._session.is_current(_expected_actor):
            raise PermissionError("انتهت جلسة الدخول.")
        try:
            validate_new_password(new_password, confirmation)
        except ValueError as exc:
            raise ManagerAccessError("PASSWORD_POLICY", str(exc)) from None
        if self._authentication is None:
            self._authentication = AuthenticationService()
        try:
            self._authentication.verify_user_password(int(actor.user_id), current)
        except AuthenticationError:
            raise ManagerAccessError("PASSWORD_INVALID", WRONG) from None
        encoded = self._passwords.hash(new_password)               # slow Argon2 work before any row lock
        with self._transaction() as connection:
            repository = self._repository(connection)
            identity = repository.get_identity(int(actor.user_id), lock=True)
            if not identity or not identity.get("password_hash"):
                raise ManagerAccessError("NOT_FOUND", "المستخدم غير موجود.")
            if self._passwords.verify(identity["password_hash"], new_password):
                raise ManagerAccessError("SAME_PASSWORD", "كلمة المرور الجديدة مطابقة للحالية.")
            repository.set_password(int(actor.user_id), encoded)
            repository.record_security_event("USER_PASSWORD_CHANGED", "USER", int(actor.user_id), str(identity.get("username") or actor.user_id), "تغيير كلمة المرور من الإعدادات",
                                             {"actor_user_id": actor.user_id}, user_id=actor.user_id)
            if not self._session.is_current(actor):
                raise PermissionError("انتهت جلسة الدخول.")
        return {"changed": True}
