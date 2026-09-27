"""Verify credentials off-thread; no credential-bearing exception reaches QML."""
from dataclasses import dataclass, field
import logging
from threading import Lock
from time import monotonic

from repositories.authentication_repository import AuthenticationRepository, authentication_transaction
from services.password_service import PasswordService

GENERIC_ERROR = "تعذر تسجيل الدخول. تحقق من بيانات الدخول أو راجع المسؤول."
CHANGE_REQUIRED = "يلزم تغيير كلمة المرور بواسطة المسؤول قبل الدخول؛ تغيير كلمة المرور غير متاح في هذا الإصدار."
_VERIFIED = object()
logger = logging.getLogger(__name__)


class AuthenticationError(Exception):
    pass


class SetupAlreadyCompleted(ValueError):
    pass


@dataclass(frozen=True)
class AuthenticationResult:
    user_id: int
    employee_id: int | None
    display_name: str
    permissions: frozenset[str]
    _proof: object = field(repr=False, compare=False, default=None)


class AuthenticationService:
    def __init__(self, transaction_factory=authentication_transaction,
                 repository_factory=AuthenticationRepository, passwords=None, clock=monotonic):
        self._transaction = transaction_factory
        self._repository = repository_factory
        self._passwords = passwords or PasswordService()
        self._clock = clock
        self._lock = Lock()
        self._failures = 0
        self._retry_at = 0

    def requires_first_run_setup(self):
        with self._transaction() as connection:
            return self._repository(connection).user_count() == 0

    def first_run_status(self):
        with self._transaction() as connection:
            repository = self._repository(connection)
            required = repository.user_count() == 0
            employees = repository.active_employees() if required else []
            return {"setup_required": required, "employees": [
                {"id": str(row["id"]), "label": " | ".join(
                    [str(row["id"]), row["employee_code"] or "—", row["name"]])}
                for row in employees]}

    def create_first_owner(self, display_name, password, confirmation, employee_id=None):
        """The GUI and emergency CLI share this single bootstrap transaction."""
        if not isinstance(display_name, str) or not display_name.strip() or len(display_name) > 100:
            raise ValueError("اسم العرض مطلوب وبحد أقصى ١٠٠ حرف.")
        if not isinstance(password, str) or not 6 <= len(password) <= 1024:
            raise ValueError("استخدم كلمة مرور من ٦ إلى ١٠٢٤ حرفاً.")
        if not isinstance(confirmation, str) or not 6 <= len(confirmation) <= 1024:
            raise ValueError("تأكيد كلمة المرور غير مطابق.")
        if employee_id is not None and (type(employee_id) is not int or not 0 < employee_id <= 4294967295):
            raise ValueError("رقم الموظف غير صحيح.")
        if not self._lock.acquire(blocking=False):
            raise ValueError("جارٍ التحقق من طلب آخر. حاول مرة أخرى.")
        try:
            encoded = self._passwords.hash(password)
            if not self._passwords.verify(encoded, confirmation):
                raise ValueError("تأكيد كلمة المرور غير مطابق.")
            with self._transaction() as connection:
                repository = self._repository(connection)
                repository.acquire_bootstrap_lock()
                if repository.has_users():
                    raise SetupAlreadyCompleted("تم إعداد حساب بالفعل. سجّل الدخول بكلمة المرور.")
                role_id = repository.bootstrap_owner_role()
                user_id = repository.create_first_user(
                    "admin", display_name, encoded, employee_id, role_id)
                permissions = repository.effective_permissions(user_id, lock=True)
                if "APP_LOGIN" not in permissions:
                    raise ValueError("المستخدم الجديد لا يملك صلاحية APP_LOGIN النشطة.")
                repository.record_login(user_id)
                result = AuthenticationResult(int(user_id), employee_id, display_name,
                                              permissions, _VERIFIED)
            # A verified result is published only after the transaction commits.
            return result
        finally:
            password = confirmation = None
            self._lock.release()

    def authenticate(self, username, password):
        # One attempt in flight, plus bounded process-local failed-attempt cooldown.
        if not self._lock.acquire(blocking=False):
            raise AuthenticationError(GENERIC_ERROR)
        try:
            if self._clock() < self._retry_at:
                raise AuthenticationError(GENERIC_ERROR)
            try:
                result = self._authenticate(username, password)
            except AuthenticationError:
                self._failures += 1
                self._retry_at = self._clock() + min(30, 2 ** min(self._failures - 1, 5))
                raise
            except Exception:
                self._retry_at = self._clock() + 2
                raise AuthenticationError(GENERIC_ERROR) from None
            self._failures = 0
            self._retry_at = 0
            return result
        finally:
            password = None
            self._lock.release()

    def verify_credentials(self, username, password):
        """Check another user's credentials without signing them in (e.g. a manager
        approving a settlement at the counter). Same cooldown and generic errors as
        a login; nothing is recorded as a login and the session is not touched."""
        if not self._lock.acquire(blocking=False):
            raise AuthenticationError(GENERIC_ERROR)
        try:
            if self._clock() < self._retry_at:
                raise AuthenticationError(GENERIC_ERROR)
            try:
                result = self._check_credentials(username, password)
            except AuthenticationError:
                self._failures += 1
                self._retry_at = self._clock() + min(30, 2 ** min(self._failures - 1, 5))
                raise
            except Exception:
                self._retry_at = self._clock() + 2
                raise AuthenticationError(GENERIC_ERROR) from None
            self._failures = 0
            self._retry_at = 0
            return result
        finally:
            password = None
            self._lock.release()

    def _check_credentials(self, username, password):
        if not isinstance(username, str) or not 0 < len(username) <= 100:
            raise AuthenticationError(GENERIC_ERROR)
        if not isinstance(password, str) or not 0 < len(password) <= 1024:
            raise AuthenticationError(GENERIC_ERROR)
        with self._transaction() as connection:
            repository = self._repository(connection)
            credentials = repository.find_credentials(username)
            if credentials is None:
                self._passwords.dummy_verify(password)
                raise AuthenticationError(GENERIC_ERROR)
            if not self._passwords.verify(credentials["password_hash"], password):
                raise AuthenticationError(GENERIC_ERROR)
            user = repository.get_identity(credentials["id"])
            if (not user or not user["is_active"] or user["must_change_password"]
                    or (user["employee_id"] is not None and not user.get("employee_active"))):
                raise AuthenticationError(GENERIC_ERROR)
            permissions = repository.effective_permissions(user["id"])
            if "APP_LOGIN" not in permissions:
                raise AuthenticationError(GENERIC_ERROR)
            return AuthenticationResult(int(user["id"]), user["employee_id"], user["display_name"],
                                        permissions, None)

    def establish_development_session(self, username):
        """TEMPORARY DEVELOPMENT AUTO-LOGIN — DISABLE BEFORE PRODUCTION.

        Signs in an existing account without its password, only while
        config.dev_settings allows it. Every account check of a normal login
        still applies, and the login is recorded like a normal login.
        """
        from config.dev_settings import dev_auto_login_username
        if not isinstance(username, str) or not username or dev_auto_login_username() != username:
            raise AuthenticationError(GENERIC_ERROR)
        if not self._lock.acquire(blocking=False):
            raise AuthenticationError(GENERIC_ERROR)
        try:
            with self._transaction() as connection:
                repository = self._repository(connection)
                credentials = repository.find_credentials(username)
                user = credentials and repository.get_identity(credentials["id"], lock=True)
                if (not user or not user["is_active"]
                        or (user["employee_id"] is not None and not user.get("employee_active"))):
                    raise AuthenticationError(GENERIC_ERROR)
                permissions = repository.effective_permissions(user["id"], lock=True)
                if "APP_LOGIN" not in permissions:
                    raise AuthenticationError(GENERIC_ERROR)
                if user["must_change_password"]:
                    raise AuthenticationError(CHANGE_REQUIRED)
                repository.record_login(user["id"])
                result = AuthenticationResult(int(user["id"]), user["employee_id"],
                                              user["display_name"], permissions, _VERIFIED)
            logger.warning("TEMPORARY DEVELOPMENT AUTO-LOGIN — DISABLE BEFORE PRODUCTION (user id %s signed in automatically).",
                           result.user_id)
            return result
        finally:
            self._lock.release()

    def _authenticate(self, username, password):
        if not isinstance(username, str) or not 0 < len(username) <= 100:
            raise AuthenticationError(GENERIC_ERROR)
        if not isinstance(password, str) or not 0 < len(password) <= 1024:
            raise AuthenticationError(GENERIC_ERROR)
        # No customer normalization, trimming, or password mutation.
        with self._transaction() as connection:
            credentials = self._repository(connection).find_credentials(username)
        if credentials is None:
            self._passwords.dummy_verify(password)
            raise AuthenticationError(GENERIC_ERROR)
        encoded = credentials["password_hash"]
        if not self._passwords.verify(encoded, password):
            raise AuthenticationError(GENERIC_ERROR)
        replacement = self._passwords.hash(password) if self._passwords.needs_rehash(encoded) else None
        with self._transaction() as connection:
            repository = self._repository(connection)
            user = repository.get_identity(credentials["id"], lock=True)
            if (not user or not user["is_active"] or user["password_hash"] != encoded
                    or (user["employee_id"] is not None and not user.get("employee_active"))):
                raise AuthenticationError(GENERIC_ERROR)
            permissions = repository.effective_permissions(user["id"], lock=True)
            if "APP_LOGIN" not in permissions:
                raise AuthenticationError(GENERIC_ERROR)
            if user["must_change_password"]:
                raise AuthenticationError(CHANGE_REQUIRED)
            repository.record_login(user["id"], replacement)
            result = AuthenticationResult(int(user["id"]), user["employee_id"],
                                          user["display_name"], permissions, _VERIFIED)
        return result
