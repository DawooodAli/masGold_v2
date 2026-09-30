"""Authorized provider configuration; connection tests are strictly fetch-only."""
from datetime import datetime, timezone
import logging

from config.gold_price_config import GoldTimoConfig, credential_diagnostics
from repositories.gold_price_repository import GoldPriceRepository, gold_price_transaction
from services.authorization_service import AuthorizationService
from services.secret_store import SecretStore, SecretStoreError
from services.gold_price_diagnostic import diagnose
from services.gold_price_errors import ConnectionTestError


class GoldPriceSettingsService:
    def __init__(self, session, *, secrets=None, transaction=gold_price_transaction,
                 repository=GoldPriceRepository, diagnostic=diagnose):
        self.session = session
        self.secrets = secrets or SecretStore()
        self.transaction, self.repository = transaction, repository
        self.authorization = AuthorizationService(session)
        self.diagnostic = diagnostic

    def _authorize(self, connection, actor):
        return self.authorization.require_permission(connection, actor, "SETTINGS_MANAGE")

    @staticmethod
    def _options(url, timeout):
        # A stored credential must not be sent to an arbitrary host entered in UI.
        url = url.strip().rstrip("/")
        if url != "https://goldtimo.com/api/v1":
            raise ValueError("رابط GoldTimo المعتمد هو https://goldtimo.com/api/v1")
        try:
            config = GoldTimoConfig(base_url=url, timeout_seconds=float(timeout))
        except (ValueError, TypeError):
            raise ValueError("مهلة الاتصال يجب أن تكون بين ٠٫١ و٣٠ ثانية.") from None
        return {"base_url": config.base_url, "timeout_seconds": config.timeout_seconds}

    def load(self, actor):
        with self.transaction() as connection:
            # Read-only: current database permission, no row lock (save keeps the strict write check).
            self.authorization.require_permission_read(connection, actor, "SETTINGS_MANAGE")
            repo = self.repository(connection)
            settings = repo.get_settings()
            options = repo.get_provider_options()
        stored = bool(self.secrets.get_secret("goldtimo_api_key"))
        config = GoldTimoConfig.from_env(self.secrets, options)
        return {"base_url": config.base_url, "timeout": format(config.timeout_seconds, "g"),
                "refresh": str(settings["refresh_seconds"]),
                "stale": str(settings["stale_after_seconds"]), "stored": stored,
                "configured": bool(config.api_key)}

    def save(self, actor, url, timeout, refresh, stale, key):
        options = self._options(url, timeout)
        try:
            refresh, stale = int(refresh), int(stale)
            if not 10 <= refresh <= stale <= 86400:
                raise ValueError()
            entered_key = key
            key = GoldTimoConfig(api_key=key).api_key
        except (ValueError, TypeError):
            raise ValueError("راجع المفتاح وفترة التحديث وحد القِدم؛ الحد الأدنى للتحديث ١٠ ثوانٍ.") from None
        with self.transaction() as connection:
            fresh = self._authorize(connection, actor)
            self.repository(connection).save_provider_settings(
                options, refresh, stale, fresh, datetime.now(timezone.utc))
            if not self.session.is_current(actor):
                raise PermissionError("انتهت جلسة الدخول.")
        # DB and OS keyring cannot share a transaction. Report partial success.
        if key:
            try:
                with self.transaction() as connection:
                    self._authorize(connection, actor)
                    self.secrets.set_secret("goldtimo_api_key", key)
                    loaded = self.secrets.get_secret("goldtimo_api_key")
                    if isinstance(loaded, str) or loaded is None:
                        metadata = credential_diagnostics(entered_key, loaded, "ENTERED", entered_key)
                        logging.getLogger(__name__).info(" ".join(
                            f"{name}={value}" for name, value in metadata.items()))
                    if not isinstance(loaded, str) or loaded != key:
                        raise SecretStoreError("تعذر التحقق من حفظ المفتاح بشكل صحيح.")
                    if not self.session.is_current(actor):
                        raise PermissionError("انتهت جلسة الدخول.")
            except Exception:
                raise SecretStoreError("حُفظت إعدادات الاتصال، لكن تعذر حفظ المفتاح؛ أعد إدخاله وحاول مجدداً.") from None
        return "حُفظت الإعدادات دون تغيير الوضع أو الأسعار المعتمدة."

    def delete(self, actor):
        with self.transaction() as connection:
            self._authorize(connection, actor)
            self.secrets.delete_secret("goldtimo_api_key")
        return "حُذف مفتاح API المحلي. قد يظل مفتاح بيئة التطوير متاحاً."

    def test(self, actor, url, timeout, key):
        options = self._options(url, timeout)
        with self.transaction() as connection:
            self._authorize(connection, actor)
            stale = self.repository(connection).get_settings()["stale_after_seconds"]
        try:
            config = GoldTimoConfig.from_env(self.secrets, options,
                                             entered_key=key, trace_stored=True)
        except SecretStoreError:
            raise ConnectionTestError("CREDENTIAL_STORE") from None
        except ValueError:
            raise ConnectionTestError("CONFIGURATION") from None
        result = self.diagnostic(config, stale_after_seconds=int(stale))
        if not self.session.is_current(actor):
            raise PermissionError("انتهت جلسة الدخول.")
        passed = result["validation"] == "PASS"
        # A partial fetch (e.g. a later karat returned 401) keeps its real error.
        if not passed and len(result.get("prices") or ()) != 3:
            raise ConnectionTestError(result.get("error_code", "QUOTE_VALIDATION"), result.get("http_status"))
        # Only allowlisted display fields; never forward transport messages or config.
        summary = {"source": "GOLDTIMO", "fetched_at": result["fetched_at"],
                   "prices": result["prices"],
                   "provider_timestamp": result.get("provider_timestamp", result["fetched_at"]),
                   "timestamp_skew_seconds": result.get("timestamp_skew_seconds", 0),
                   "credential_source": config.credential_trace["credential_source"],
                   "configured": bool(config.api_key), "character_count": len(config.api_key),
                   "http_status": 200, "coherent_set": "PASS" if passed else "FAIL"}
        if not passed:
            # Every endpoint answered 200 but the set was rejected (skew, stale,
            # values). Show the observations; the message is allowlisted only.
            error = ConnectionTestError(result.get("error_code", "QUOTE_VALIDATION"))
            summary.update(error_code=error.code, error=str(error))
        return summary
