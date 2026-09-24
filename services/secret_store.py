"""OS-backed application secrets; never fall back to a plaintext backend."""
import sys


class SecretStoreError(ValueError):
    pass


class SecretStore:
    service = "masGold"

    def __init__(self, backend=None):
        self._backend = backend

    def _get_backend(self):
        if self._backend is not None:
            return self._backend
        if sys.platform == "win32":
            from keyring.backends.Windows import WinVaultKeyring
            return WinVaultKeyring()
        import keyring
        backend = keyring.get_keyring()
        if type(backend).__module__ not in (
                "keyring.backends.macOS", "keyring.backends.SecretService",
                "keyring.backends.kwallet"):
            raise SecretStoreError("مخزن بيانات الاعتماد الآمن غير متاح.")
        return backend

    def _call(self, method, name, *args):
        try:
            return getattr(self._get_backend(), method)(self.service, name, *args)
        except Exception:
            raise SecretStoreError("تعذر الوصول إلى مخزن بيانات الاعتماد الآمن.") from None

    def set_secret(self, name, value):
        self._call("set_password", name, value)

    def get_secret(self, name):
        return self._call("get_password", name)

    def delete_secret(self, name):
        if self.get_secret(name) is not None:
            self._call("delete_password", name)
