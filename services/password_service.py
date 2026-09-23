"""Credentials never leave the verification layer as hashes or log messages."""
from argon2 import PasswordHasher, Type
from argon2.exceptions import Argon2Error


class PasswordService:
    def __init__(self, hasher=None):
        self._hasher = hasher or PasswordHasher(type=Type.ID)
        self._dummy = None

    def hash(self, password):
        if not isinstance(password, str) or not password or len(password) > 1024:
            raise ValueError("كلمة المرور مطلوبة وبحد أقصى ١٠٢٤ حرفاً.")
        return self._hasher.hash(password)

    def verify(self, encoded, password):
        if not isinstance(password, str) or not password or len(password) > 1024:
            return False
        if not isinstance(encoded, str) or not encoded.startswith("$argon2id$"):
            self.dummy_verify(password)
            return False
        try:
            return self._hasher.verify(encoded, password)
        except (Argon2Error, ValueError, TypeError):
            return False

    def dummy_verify(self, password):
        # Initialized on the authentication worker, never at GUI startup.
        if self._dummy is None:
            from secrets import token_urlsafe
            self._dummy = self._hasher.hash(token_urlsafe(32))
        try:
            self._hasher.verify(self._dummy, password)
        except (Argon2Error, ValueError, TypeError):
            pass

    def needs_rehash(self, encoded):
        return self._hasher.check_needs_rehash(encoded)
