"""Verify credentials off-thread; no credential-bearing exception reaches QML."""
from dataclasses import dataclass, field
import getpass
import logging
import socket
from threading import Lock
from time import monotonic

from repositories.authentication_repository import AuthenticationRepository, authentication_transaction
from services.password_policy import validate_new_password
from services.password_service import PasswordService

GENERIC_ERROR = "تعذر تسجيل الدخول. تحقق من بيانات الدخول أو راجع المسؤول."
CHANGE_REQUIRED = "يلزم تغيير كلمة المرور بواسطة المسؤول قبل الدخول؛ تغيير كلمة المرور غير متاح في هذا الإصدار."
_VERIFIED = object()
logger = logging.getLogger(__name__)


class AuthenticationError(Exception):
    pass


class SetupAlreadyCompleted(ValueError):
    pass


class RecoveryRefused(ValueError):
    """OWNER recovery refused before any change; the message is safe to show (never a secret)."""


class RecoveryFailed(Exception):
    """OWNER recovery did not complete and nothing was committed; deliberately carries no cause."""


RECOVERY_TARGET_REFUSED = "تعذر تنفيذ الاستعادة: يجب أن يكون الحساب موجوداً ونشطاً ومالكاً (OWNER)."
RECOVERY_SAME_PASSWORD = "كلمة المرور الجديدة يجب أن تختلف عن كلمة المرور الحالية."
RECOVERY_FAILED = "تعذر تنفيذ الاستعادة؛ لم تُعتمد أي تغييرات."
OWNER_PASSWORD_RECOVERED = "OWNER_PASSWORD_RECOVERED"


def _local_identity():
    """Operating-system user and host of the machine running an emergency tool (audit context only)."""
    try:
        operator = getpass.getuser()
    except Exception:
        operator = "unknown"
    try:
        host = socket.gethostname()
    except Exception:
        host = "unknown"
    return operator[:100], host[:150]


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

    def recover_owner_password(self, username, password, confirmation, reason):
        """EMERGENCY, local-tool-only: replace an existing ACTIVE OWNER's password and audit it atomically.

        The target is resolved from the database (user exists, is active and currently holds an active OWNER
        role); a caller-supplied designation is never trusted. A password equal to the current one is refused.
        Only users.password_hash changes; roles and permissions are untouched. The password, its hash and
        its length never reach the audit row, a log or an exception."""
        try:
            validate_new_password(password, confirmation)
            if not isinstance(username, str) or not 0 < len(username) <= 100:
                raise RecoveryRefused(RECOVERY_TARGET_REFUSED)
            reason_text = reason.strip() if isinstance(reason, str) else ""
            if not reason_text:
                raise RecoveryRefused("اكتب سبب الاستعادة.")
            if len(reason_text) > 500:
                raise RecoveryRefused("سبب الاستعادة طويل جداً.")
            if password in reason_text:
                raise RecoveryRefused("لا تكتب كلمة المرور في سبب الاستعادة.")
            if not self._lock.acquire(blocking=False):
                raise RecoveryRefused("جارٍ تنفيذ عملية أخرى. حاول مرة أخرى.")
        except (ValueError, RecoveryRefused):
            password = confirmation = None
            raise
        try:
            encoded = self._passwords.hash(password)          # slow Argon2 work before any row lock
            operator, host = _local_identity()
            with self._transaction() as connection:
                repository = self._repository(connection)
                target = repository.find_recovery_target(username)
                if not target or not target["is_active"] or not target["is_owner"]:
                    raise RecoveryRefused(RECOVERY_TARGET_REFUSED)
                if self._passwords.verify(target["password_hash"], password):
                    raise RecoveryRefused(RECOVERY_SAME_PASSWORD)
                repository.replace_password_hash(target["id"], encoded)
                repository.record_security_event(
                    OWNER_PASSWORD_RECOVERED, "USER", int(target["id"]), target["username"],
                    "استعادة طارئة لكلمة مرور المالك",
                    {"source": "CLI_EMERGENCY", "reason": reason_text, "os_user": operator, "host": host},
                    workstation=host)
                user_id = int(target["id"])
            return {"user_id": str(user_id)}
        except (ValueError, RecoveryRefused):
            raise
        except Exception:
            raise RecoveryFailed(RECOVERY_FAILED) from None
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

    def verify_user_password(self, user_id, password):
        """Re-authentication of the LOGGED-IN user before a sensitive action (e.g. a Dead Cash write-off):
        checks this user's current password against its stored Argon2 hash without signing in again.
        Same cooldown and generic error as a login; the password is never stored or logged."""
        if not self._lock.acquire(blocking=False):
            raise AuthenticationError(GENERIC_ERROR)
        try:
            if self._clock() < self._retry_at:
                raise AuthenticationError(GENERIC_ERROR)
            try:
                if not isinstance(password, str) or not 0 < len(password) <= 1024 or isinstance(user_id, bool) \
                        or not isinstance(user_id, int) or user_id <= 0:
                    raise AuthenticationError(GENERIC_ERROR)
                with self._transaction() as connection:
                    user = self._repository(connection).get_identity(user_id)
                if not user or not user.get("password_hash"):
                    self._passwords.dummy_verify(password)
                    raise AuthenticationError(GENERIC_ERROR)
                if not self._passwords.verify(user["password_hash"], password):
                    raise AuthenticationError(GENERIC_ERROR)
            except AuthenticationError:
                self._failures += 1
                self._retry_at = self._clock() + min(30, 2 ** min(self._failures - 1, 5))
                raise
            except Exception:
                self._retry_at = self._clock() + 2
                raise AuthenticationError(GENERIC_ERROR) from None
            self._failures = 0
            self._retry_at = 0
            return True
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

    def establish_automatic_session(self, username):
        """Open the program as ONE named account without its password, only when that account is the one this workstation chose (config.workstation_settings) or the development auto-login allows it.
        Every account check of a normal login still applies (exists, active, linked employee active, APP_LOGIN, no pending password change) and the login is recorded like a normal login."""
        from config.dev_settings import dev_auto_login_username
        from config.workstation_settings import auto_sign_in_user
        if not isinstance(username, str) or not username or username not in (dev_auto_login_username(), auto_sign_in_user()):
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
                result = AuthenticationResult(int(user["id"]), user["employee_id"], user["display_name"], permissions, _VERIFIED)
            logger.info("Automatic sign-in of user id %s (chosen on this workstation).", result.user_id)
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
