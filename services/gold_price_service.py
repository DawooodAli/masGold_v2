"""Coherent, auditable gold prices. Network work precedes publication locks.

All I/O methods are synchronous for use by a worker, never by the GUI thread.
Database DATETIME values written by this engine are UTC, including audit times.
"""
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from hashlib import sha256
import json
from threading import Lock
from types import MappingProxyType
from typing import Mapping

from repositories.gold_price_repository import GoldPriceRepository, gold_price_transaction
from services.authorization_service import AuthorizationService
from services.gold_price_provider import (
    NormalizedQuote, ProviderError, QuoteValidationError, configured_providers,
    validate_quotes,
)


UTC = timezone.utc
PRICE = Decimal("0.0001")
KARATS = (18, 21, 24)
DERIVATION = {
    "method": "BASE_21_K_DIV_21", "base_karat": 21,
    "formula": "price(K) = price(21K) * K / 21",
    "rounding": "ROUND_HALF_UP", "decimal_places": 4,
}


class PriceUnavailable(ValueError):
    pass


class PriceChanged(ValueError):
    pass


@dataclass(frozen=True)
class BillingKaratPrice:
    karat_id: int
    karat: int
    shop_buy_price_per_gram: Decimal
    shop_sell_price_per_gram: Decimal
    quote_id: int
    activation_id: int


@dataclass(frozen=True)
class BillingPriceSet:
    price_token: str
    mode: str
    status: str
    warnings: tuple[str, ...]
    requires_acknowledgement: bool
    prices: Mapping[int, BillingKaratPrice]
    provenance: Mapping


def utc_now():
    return datetime.now(UTC)


def as_utc(value):
    if value is None:
        return None
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def stamp(value):
    return as_utc(value).isoformat() if value is not None else ""


class GoldPriceService:
    def __init__(self, session, *, transaction_factory=gold_price_transaction,
                 repository_factory=GoldPriceRepository, providers=None,
                 authorization=None, clock=utc_now):
        self._session = session
        self._transaction = transaction_factory
        self._repository = repository_factory
        self._configuration_error = ""
        try:
            self._providers = configured_providers() if providers is None else dict(providers)
        except ValueError:
            self._providers = {}
            self._configuration_error = "إعدادات موفر الأسعار غير صحيحة؛ راجع ملف الإعدادات."
        self._authorization = authorization or AuthorizationService(session)
        self._clock = clock
        self._refresh_lock = Lock()
        self._last_error = ""
        self._failed_group = None
        self._last_warnings = ()

    @staticmethod
    def _settings_key(settings):
        return tuple(settings.get(key) for key in (
            "mode", "primary_source_id", "fallback_source_id", "reference_source_id",
            "refresh_seconds", "stale_after_seconds", "max_deviation_percent", "updated_at"))

    @staticmethod
    def _check_settings(settings):
        if not settings or settings.get("mode") not in ("AUTO", "MANUAL"):
            raise PriceUnavailable("إعدادات محرك أسعار الذهب غير متاحة.")
        if (int(settings["refresh_seconds"]) < 10
                or int(settings["stale_after_seconds"]) < int(settings["refresh_seconds"])
                or not Decimal("0") < Decimal(settings["max_deviation_percent"]) <= Decimal("100")):
            raise PriceUnavailable("إعدادات تحديث أسعار الذهب غير صحيحة.")

    def _read(self):
        with self._transaction() as connection:
            repo = self._repository(connection)
            settings = repo.get_settings(lock=True)
            self._check_settings(settings)
            return settings, repo.get_sources(), repo.get_karats(), repo.get_active()

    @staticmethod
    def _coherent(rows):
        if len(rows) != 3 or {int(r["karat_value"]) for r in rows} != set(KARATS):
            return False
        for field in ("quote_group_id", "source_code", "provider_timestamp", "fetched_at",
                      "activation_type", "activated_at"):
            if len({r[field] for r in rows}) != 1:
                return False
        try:
            return all(
                isinstance(r["shop_buy_price_per_gram"], Decimal)
                and isinstance(r["shop_sell_price_per_gram"], Decimal)
                and r["shop_buy_price_per_gram"].is_finite()
                and r["shop_sell_price_per_gram"].is_finite()
                and Decimal("0") < r["shop_buy_price_per_gram"] <= r["shop_sell_price_per_gram"]
                and r["provider_timestamp"] is not None
                and r["validation_status"] == "ACCEPTED"
                for r in rows)
        except (TypeError, InvalidOperation):
            return False

    @staticmethod
    def _token(settings, rows):
        # Freshness/age/download errors intentionally do not change price identity.
        data = {"mode": settings["mode"], "prices": [
            {key: str(row[key]) for key in (
                "karat_id", "quote_id", "activation_id", "quote_group_id", "source_code",
                "shop_buy_price_per_gram", "shop_sell_price_per_gram", "provider_timestamp")}
            for row in sorted(rows, key=lambda r: int(r["karat_value"]))]}
        return sha256(json.dumps(data, sort_keys=True).encode("utf-8")).hexdigest()

    def _snapshot(self, settings, rows):
        now = as_utc(self._clock())
        coherent = self._coherent(rows)
        active_group = rows[0]["quote_group_id"] if coherent else None
        error = (self._last_error if settings["mode"] == "AUTO"
                 and active_group == self._failed_group else "")
        result = dict(mode=settings["mode"], status="UNAVAILABLE", source="",
                      provider_timestamp="", fetched_at="", activated_at="", age_seconds=None,
                      warnings=[], prices=[], refresh_seconds=int(settings["refresh_seconds"]),
                      price_token="")
        if not coherent:
            result["warnings"] = ["لا تتوفر مجموعة أسعار متكاملة لعيارات 18 و21 و24."]
            if self._configuration_error:
                result["warnings"].append(self._configuration_error)
            if error:
                result.update(status="ERROR", warnings=result["warnings"] + [error])
            return result
        first = rows[0]
        age = max(0, int((now - as_utc(first["provider_timestamp"])).total_seconds()))
        stale = age > int(settings["stale_after_seconds"])
        warnings = list(self._last_warnings) if settings["mode"] == "AUTO" else []
        if self._configuration_error:
            warnings.append(self._configuration_error)
        if stale:
            warnings.append("الأسعار قديمة؛ يلزم الإقرار بها قبل استخدامها في الفاتورة.")
        if settings["mode"] == "MANUAL":
            warnings.append("التسعير يدوي؛ التحديث التلقائي متوقف.")
        if first["activation_type"] == "FALLBACK":
            warnings.append("الأسعار من المصدر البديل.")
        if error:
            warnings.append(error)
        status = "ERROR" if error else ("STALE_ALLOWED" if stale else "FRESH")
        if as_utc(first["provider_timestamp"]) > now and (
                as_utc(first["provider_timestamp"]) - now).total_seconds() > 120:
            status = "ERROR"
            warnings.append("توقيت الأسعار في المستقبل؛ راجع الساعة والمصدر.")
        result.update(
            status=status, source=first["source_code"],
            provider_timestamp=stamp(first["provider_timestamp"]),
            fetched_at=stamp(first["fetched_at"]), activated_at=stamp(first["activated_at"]),
            age_seconds=age, warnings=list(dict.fromkeys(warnings)),
            price_token=self._token(settings, rows),
            prices=[dict(karat=str(r["karat_value"]), karat_id=str(r["karat_id"]),
                         shop_buy_price_per_gram=format(r["shop_buy_price_per_gram"], ".4f"),
                         shop_sell_price_per_gram=format(r["shop_sell_price_per_gram"], ".4f"))
                    for r in sorted(rows, key=lambda r: int(r["karat_value"]))],
        )
        return result

    def snapshot(self):
        settings, _, _, rows = self._read()
        return self._snapshot(settings, rows)

    @staticmethod
    def _manual_value(value):
        if not isinstance(value, (str, Decimal)):
            raise ValueError("أدخل السعر كرقم عشري صحيح.")
        try:
            number = Decimal(value.strip() if isinstance(value, str) else value)
            if not number.is_finite() or not Decimal("0") < number < Decimal("10000000000"):
                raise ValueError("يجب أن يكون السعر موجباً ومحدوداً.")
            rounded = number.quantize(PRICE, rounding=ROUND_HALF_UP)
            if rounded <= 0 or rounded >= Decimal("10000000000"):
                raise ValueError("السعر خارج المجال المسموح.")
            return number
        except InvalidOperation as exc:
            raise ValueError("أدخل سعراً عشرياً صحيحاً.") from exc

    def _manual_quotes(self, buy21, sell21):
        buy, sell = self._manual_value(buy21), self._manual_value(sell21)
        if sell < buy:
            raise ValueError("سعر بيع المحل يجب ألا يقل عن سعر شراء المحل.")
        # Compare the original Decimal values before rounding can hide a
        # reversed spread. Derivation still uses the approved four-place base.
        buy, sell = (value.quantize(PRICE, ROUND_HALF_UP) for value in (buy, sell))
        now = as_utc(self._clock()).replace(microsecond=0)
        quotes = tuple(NormalizedQuote(
            source_code="MANUAL", karat=karat,
            shop_buy_price_per_gram=(buy * Decimal(karat) / Decimal(21)).quantize(PRICE, ROUND_HALF_UP),
            shop_sell_price_per_gram=(sell * Decimal(karat) / Decimal(21)).quantize(PRICE, ROUND_HALF_UP),
            currency="EGP", unit="GRAM", provider_timestamp=now, fetched_at=now,
        ) for karat in KARATS)
        try:
            return validate_quotes(quotes, "MANUAL", now)
        except QuoteValidationError as exc:
            raise ValueError("الأسعار المشتقة خارج المجال المسموح؛ راجع أسعار عيار 21.") from exc

    def preview_manual(self, buy21, sell21):
        return [dict(karat=str(q.karat),
                     shop_buy_price_per_gram=format(q.shop_buy_price_per_gram, ".4f"),
                     shop_sell_price_per_gram=format(q.shop_sell_price_per_gram, ".4f"))
                for q in self._manual_quotes(buy21, sell21)]

    @staticmethod
    def _reason(reason, *, required=True):
        if not isinstance(reason, str) or (required and not reason.strip()):
            raise ValueError("سبب تغيير السعر مطلوب.")
        if len(reason.strip()) > 500:
            raise ValueError("السبب يجب ألا يتجاوز 500 حرف.")
        return reason.strip()

    def _actor(self, expected):
        original = expected if expected is not None else self._session.require_authenticated()
        if not self._session.is_current(original):
            raise PermissionError("انتهت جلسة الدخول.")
        return original

    def activate_manual(self, buy21, sell21, reason, *, _expected_actor=None):
        original = self._actor(_expected_actor)
        reason = self._reason(reason)
        quotes = self._manual_quotes(buy21, sell21)
        with self._transaction() as connection:
            repo = self._repository(connection)
            settings = repo.get_settings(lock=True)
            self._check_settings(settings)
            actor = self._authorization.require_permission(connection, original, "SETTINGS_MANAGE")
            sources, karats = repo.get_sources(), repo.get_karats()
            source = sources.get("MANUAL")
            if not source or not source["is_enabled"] or source["source_type"] != "MANUAL":
                raise PriceUnavailable("مصدر التسعير اليدوي غير متاح.")
            self._check_karats(karats)
            group_id = repo.append_publication(
                quotes, karat_ids=karats, source_id=source["id"], activation_type="MANUAL",
                actor=actor, reason=reason, activated_at=as_utc(self._clock()), derivation=DERIVATION)
            repo.set_mode("MANUAL", actor, reason, as_utc(self._clock()))
            # Read our own publication while the settings lock is still held.
            # A second connection after COMMIT could fail and misreport a saved
            # price as unsuccessful, leaving the dialog/top bar on old values.
            saved_settings, saved_rows = repo.get_settings(), repo.get_active()
            if (saved_settings["mode"] != "MANUAL" or not self._coherent(saved_rows)
                    or any(row["quote_group_id"] != group_id or row["source_code"] != "MANUAL"
                           for row in saved_rows)):
                raise PriceUnavailable("تعذر التحقق من تفعيل الأسعار اليدوية؛ لم يتم اعتماد التغيير.")
            saved_snapshot = self._snapshot(saved_settings, saved_rows)
            if not self._session.is_current(original):
                raise PermissionError("انتهت جلسة الدخول.")
        self._last_error, self._last_warnings = "", ()
        # Publish to the ViewModel only after the transaction successfully commits.
        return saved_snapshot

    @staticmethod
    def _check_karats(karats):
        if set(karats) != set(KARATS):
            raise PriceUnavailable("يلزم تفعيل عيارات 18 و21 و24 في البيانات الأساسية.")

    @staticmethod
    def _source_by_id(sources, source_id):
        return next((source for source in sources.values() if source["id"] == source_id), None)

    def _fetch(self, source, settings):
        if not source or not source["is_enabled"] or source["source_type"] not in (
                "LOCAL_MARKET", "GLOBAL_REFERENCE"):
            raise ProviderError("مصدر الأسعار التلقائي غير مهيأ أو غير مفعل.")
        provider = self._providers.get(source["code"])
        if provider is None:
            raise ProviderError("موفر الأسعار غير مهيأ في إعدادات الاتصال.")
        quotes = validate_quotes(provider.fetch(), source["code"], as_utc(self._clock()))
        age = (as_utc(self._clock()) - quotes[0].provider_timestamp).total_seconds()
        if age > int(settings["stale_after_seconds"]):
            raise QuoteValidationError("المصدر أعاد أسعاراً قديمة.")
        return quotes

    @staticmethod
    def _check_deviation(quotes, baseline, maximum):
        maximum = Decimal(maximum)
        for q in quotes:
            previous = baseline.get(q.karat)
            if previous is None:
                continue
            for field in ("shop_buy_price_per_gram", "shop_sell_price_per_gram"):
                prior = previous[field] if isinstance(previous, dict) else getattr(previous, field)
                current = getattr(q, field)
                if abs(current - prior) * Decimal(100) / prior > maximum:
                    raise QuoteValidationError("انحراف السعر يتجاوز الحد المسموح؛ يلزم مراجعة المصدر.")

    def _record_rejection(self, code, error):
        # Only stable error categories, never HTTP bodies/URLs/API keys.
        detail = type(error).__name__
        if isinstance(error, QuoteValidationError):
            # These messages are generated by our validation boundary, without
            # interpolating the untrusted response, request URL or credentials.
            detail += ": " + str(error)
        with self._transaction() as connection:
            self._repository(connection).append_rejection(
                code, detail, as_utc(self._clock()))

    def _candidate(self, settings, sources, active):
        baseline = {int(r["karat_value"]): r for r in active} if self._coherent(active) else {}
        reference_id = settings["reference_source_id"]
        reference = None
        if reference_id is not None:
            source = self._source_by_id(sources, reference_id)
            try:
                reference = self._fetch(source, settings)
            except (ProviderError, ValueError, TimeoutError, OSError) as exc:
                self._record_rejection(source["code"] if source else "REFERENCE", exc)
                raise PriceUnavailable("تعذر التحقق من المصدر المرجعي؛ تم الاحتفاظ بآخر أسعار.") from exc
        failures = []
        candidates = list(dict.fromkeys((settings["primary_source_id"], settings["fallback_source_id"])))
        for index, source_id in enumerate(candidates):
            if source_id is None:
                continue
            source = self._source_by_id(sources, source_id)
            try:
                quotes = (reference if source_id == reference_id else self._fetch(source, settings))
                self._check_deviation(quotes, baseline, settings["max_deviation_percent"])
                if reference:
                    self._check_deviation(quotes, {q.karat: q for q in reference},
                                          settings["max_deviation_percent"])
                # No network is in this transaction. Check before choosing the
                # candidate so stale/revised primary history can use fallback.
                with self._transaction() as connection:
                    latest = self._repository(connection).latest_source_quotes(source["id"])
                self._check_history(quotes, latest)
                return source, quotes, ("FALLBACK" if index or failures else "AUTO"), failures
            except (ProviderError, ValueError, TimeoutError, OSError) as exc:
                self._record_rejection(source["code"] if source else "UNCONFIGURED", exc)
                failures.append("تعذر اعتماد المصدر الأساسي؛ تمت محاولة المصدر البديل.")
        raise PriceUnavailable("تعذر جلب مجموعة أسعار صالحة؛ راجع تهيئة المصادر والاتصال.")

    @staticmethod
    def _same_quotes(quotes, rows):
        existing = {int(r["karat_value"]): r for r in rows}
        if set(existing) != set(KARATS):
            return False
        return all(as_utc(existing[q.karat]["provider_timestamp"]) == q.provider_timestamp
                   and existing[q.karat]["shop_buy_price_per_gram"] == q.shop_buy_price_per_gram
                   and existing[q.karat]["shop_sell_price_per_gram"] == q.shop_sell_price_per_gram
                   for q in quotes)

    @classmethod
    def _check_history(cls, quotes, latest):
        if not latest:
            return
        newest = max(as_utc(r["provider_timestamp"]) for r in latest)
        if quotes[0].provider_timestamp < newest:
            raise QuoteValidationError("المصدر أعاد توقيتاً أقدم من آخر سعر محفوظ.")
        if quotes[0].provider_timestamp == newest and not cls._same_quotes(quotes, latest):
            raise QuoteValidationError("المصدر غيّر الأسعار دون تغيير توقيت السعر.")

    def _publish_auto(self, original, initial, source, quotes, activation_type,
                      *, returning=False, reason=""):
        with self._transaction() as connection:
            repo = self._repository(connection)
            settings = repo.get_settings(lock=True)
            self._check_settings(settings)
            if not self._session.is_current(original):
                raise PermissionError("انتهت جلسة الدخول.")
            if self._settings_key(settings) != self._settings_key(initial):
                raise PriceChanged("تغير وضع التسعير أو إعداداته أثناء التحديث؛ أعد المحاولة.")
            if not returning and settings["mode"] != "AUTO":
                return
            actor = (self._authorization.require_permission(connection, original, "SETTINGS_MANAGE")
                     if returning else None)
            sources = repo.get_sources()
            current_source = sources.get(source["code"])
            if (not current_source or not current_source["is_enabled"]
                    or current_source["id"] != source["id"]
                    or current_source["source_type"] not in ("LOCAL_MARKET", "GLOBAL_REFERENCE")):
                raise PriceChanged("تغير المصدر أثناء التحديث؛ أعد المحاولة.")
            karats = repo.get_karats()
            self._check_karats(karats)
            now = as_utc(self._clock())
            if (now - quotes[0].provider_timestamp).total_seconds() > int(settings["stale_after_seconds"]):
                raise PriceUnavailable("أصبحت أسعار المصدر قديمة قبل اعتمادها.")
            active = repo.get_active(lock=True)
            if self._coherent(active):
                self._check_deviation(quotes, {int(r["karat_value"]): r for r in active},
                                      settings["max_deviation_percent"])
            latest = repo.latest_source_quotes(source["id"])
            self._check_history(quotes, latest)
            if not returning and self._coherent(active) and active[0]["source_code"] == source["code"]:
                if self._same_quotes(quotes, active):
                    return  # Preserve quote/token/fetched/activated timestamps.
            repo.append_publication(
                quotes, karat_ids=karats, source_id=source["id"], activation_type=activation_type,
                actor=actor, reason=reason or "Automatic provider refresh", activated_at=now)
            if returning:
                repo.set_mode("AUTO", actor, reason or "Explicit return to AUTO", now)
            if not self._session.is_current(original):
                raise PermissionError("انتهت جلسة الدخول.")

    def refresh(self, *, _expected_actor=None):
        original = self._actor(_expected_actor)
        if not self._refresh_lock.acquire(blocking=False):
            return self.snapshot()
        try:
            settings, sources, _, active = self._read()
            if settings["mode"] == "MANUAL":
                self._last_error, self._last_warnings = "", ()
                return self._snapshot(settings, active)
            try:
                source, quotes, kind, warnings = self._candidate(settings, sources, active)
                self._publish_auto(original, settings, source, quotes, kind)
                self._last_error, self._last_warnings = "", tuple(warnings)
            except PriceChanged:
                # Another process/user may have deliberately entered MANUAL.
                self._last_error, self._last_warnings = "", ()
            except PermissionError:
                raise
            except QuoteValidationError as exc:
                self._record_rejection(source["code"], exc)
                self._last_error = "رفض تحديث الأسعار بعد التحقق؛ تم الاحتفاظ بآخر مجموعة سليمة."
                self._failed_group = active[0]["quote_group_id"] if self._coherent(active) else None
            except Exception:
                self._last_error = "فشل التحديث التلقائي؛ تم الاحتفاظ بآخر مجموعة أسعار سليمة."
                self._failed_group = active[0]["quote_group_id"] if self._coherent(active) else None
            return self.snapshot()
        finally:
            self._refresh_lock.release()

    def return_to_auto(self, reason="", *, _expected_actor=None):
        original = self._actor(_expected_actor)
        reason = self._reason(reason, required=False)
        if not self._refresh_lock.acquire(blocking=False):
            raise ValueError("يوجد تحديث جارٍ؛ انتظر اكتماله.")
        try:
            # Revalidate before accessing the provider, then again under publication lock.
            with self._transaction() as connection:
                repo = self._repository(connection)
                settings = repo.get_settings(lock=True)
                self._check_settings(settings)
                self._authorization.require_permission(connection, original, "SETTINGS_MANAGE")
                sources, active = repo.get_sources(), repo.get_active()
            source, quotes, kind, warnings = self._candidate(settings, sources, active)
            self._publish_auto(original, settings, source, quotes, kind,
                               returning=True, reason=reason)
            self._last_error, self._last_warnings = "", tuple(warnings)
            return self.snapshot()
        finally:
            self._refresh_lock.release()

    def _billing_set(self, settings, rows, required_karat_ids):
        try:
            requested = tuple(required_karat_ids)
        except TypeError as exc:
            raise ValueError("عيارات الفاتورة غير صحيحة.") from exc
        if not requested or any(type(value) is not int for value in requested):
            raise ValueError("يلزم تحديد معرفات عيارات الفاتورة كأعداد صحيحة.")
        snapshot = self._snapshot(settings, rows)
        if not snapshot["price_token"]:
            raise PriceUnavailable("لا تتوفر مجموعة أسعار مكتملة للفاتورة.")
        by_id = {int(r["karat_id"]): r for r in rows}
        if not set(requested).issubset(by_id):
            raise PriceUnavailable("أحد عيارات الفاتورة لا يملك سعراً معتمداً.")
        first = rows[0]
        if (as_utc(first["provider_timestamp"]) - as_utc(self._clock())).total_seconds() > 120:
            raise PriceUnavailable("توقيت الأسعار في المستقبل؛ لا يمكن اعتمادها للفاتورة.")
        provenance = MappingProxyType(dict(
            source_code=first["source_code"], quote_group_id=first["quote_group_id"],
            currency="EGP", unit="GRAM", provider_timestamp=as_utc(first["provider_timestamp"]),
            fetched_at=as_utc(first["fetched_at"]), activated_at=as_utc(first["activated_at"]),
            activation_type=first["activation_type"],
            activated_by_user_id=first.get("activated_by_user_id"),
            reason=first.get("reason"), quote_notes=first.get("notes"),
        ))
        prices = MappingProxyType({key: BillingKaratPrice(
            karat_id=key, karat=int(by_id[key]["karat_value"]),
            shop_buy_price_per_gram=by_id[key]["shop_buy_price_per_gram"],
            shop_sell_price_per_gram=by_id[key]["shop_sell_price_per_gram"],
            quote_id=int(by_id[key]["quote_id"]), activation_id=int(by_id[key]["activation_id"]),
        ) for key in sorted(set(requested))})
        return BillingPriceSet(snapshot["price_token"], snapshot["mode"], snapshot["status"],
                               tuple(snapshot["warnings"]), snapshot["status"] != "FRESH",
                               prices, provenance)

    def get_billing_price_set(self, required_karat_ids):
        settings, _, _, rows = self._read()
        return self._billing_set(settings, rows, required_karat_ids)

    def lock_and_validate_for_finalization(self, connection, price_token, required_karat_ids,
                                         *, acknowledged=False):
        """Use INSIDE Billing's existing transaction, before its ledger locks.

        Never opens/commits/rolls back a transaction or performs provider I/O.
        Locks remain held by the caller until its existing transaction completes.
        Billing must compare the returned exact prices with employee-confirmed
        line snapshots and persist provenance in its existing snapshot tables.
        """
        if not connection.in_transaction:
            raise RuntimeError("Price finalization validation requires the caller's active transaction.")
        repo = self._repository(connection)
        settings = repo.get_settings(lock=True)
        self._check_settings(settings)
        result = self._billing_set(settings, repo.get_active(lock=True), required_karat_ids)
        if result.price_token != price_token:
            raise PriceChanged("تغيرت أسعار الذهب؛ راجع الأسعار وأكدها من جديد.")
        if result.requires_acknowledgement and acknowledged is not True:
            raise PriceUnavailable("يلزم الإقرار بتحذير الأسعار قبل إتمام الفاتورة.")
        return result
