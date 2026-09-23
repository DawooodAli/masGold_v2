"""Verify credentials off-thread; no credential-bearing exception reaches QML."""
from dataclasses import dataclass, field
from threading import Lock
from time import monotonic

from repositories.authentication_repository import AuthenticationRepository, authentication_transaction
from services.password_service import PasswordService

GENERIC_ERROR = "تعذر تسجيل الدخول. تحقق من بيانات الدخول أو راجع المسؤول."
CHANGE_REQUIRED = "يلزم تغيير كلمة المرور بواسطة المسؤول قبل الدخول؛ تغيير كلمة المرور غير متاح في هذا الإصدار."
_VERIFIED = object()


class AuthenticationError(Exception):
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
