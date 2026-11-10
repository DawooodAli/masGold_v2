"""Currency exchange rates for the home page (GoldTimo `GET /rates`, free plan): how many Egyptian pounds one unit of a foreign currency is worth.

Display only: nothing here is stored, posted or used by any accounting rule. GoldTimo quotes every rate against USD (USD = 1), so
EGP per unit of X = rate[EGP] / rate[X]. The choices (shown or not, refresh interval, which currencies) are a look of THIS computer, kept in QSettings like the theme.
The same GoldTimo keys as the gold price engine are used; no key is stored or shown here."""
from datetime import timezone
from decimal import Decimal, ROUND_HALF_UP
import time

from PySide6.QtCore import QSettings

ORGANIZATION, APPLICATION = "masGold", "masGold_v2"
CURRENCIES = (("USD", "دولار أمريكي"), ("EUR", "يورو"), ("GBP", "جنيه إسترليني"), ("SAR", "ريال سعودي"), ("AED", "درهم إماراتي"), ("KWD", "دينار كويتي"),
              ("QAR", "ريال قطري"), ("BHD", "دينار بحريني"), ("OMR", "ريال عماني"), ("JOD", "دينار أردني"), ("CAD", "دولار كندي"), ("AUD", "دولار أسترالي"), ("CHF", "فرنك سويسري"))
NAMES = dict(CURRENCIES)
DEFAULT_CODES = ("USD", "EUR", "GBP", "SAR", "AED", "KWD")
MIN_MINUTES, MAX_MINUTES, DEFAULT_MINUTES = 10, 1440, 30          # the free plan allows 100 requests an hour for ALL keys together, so never faster than every 10 minutes
FAILURE_RETRY_SECONDS = 300


def egp_per_unit(rates, code):
    """EGP per ONE unit of `code` (two decimals), or None when the quote lacks the currency."""
    egp, other = rates.get("EGP"), rates.get(code)
    if not egp or not other:
        return None
    return (Decimal(egp) / Decimal(other)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def build_rows(rates, codes):
    rows = []
    for code in codes:
        price = egp_per_unit(rates, code)
        if price is not None and code != "EGP":
            rows.append({"code": code, "name": NAMES.get(code, code), "price": str(price)})
    return rows


class FxSettings:
    """enabled / refresh minutes / shown currencies, per workstation. Bad stored values fall back to the defaults, never to an exception."""
    def __init__(self, store=None):
        self._store = store if store is not None else QSettings(QSettings.NativeFormat, QSettings.UserScope, ORGANIZATION, APPLICATION)

    def enabled(self):
        return str(self._store.value("fx/enabled", "true")).lower() not in ("false", "0", "")

    def minutes(self):
        try:
            value = int(str(self._store.value("fx/minutes", DEFAULT_MINUTES)))
        except ValueError:
            return DEFAULT_MINUTES
        return value if MIN_MINUTES <= value <= MAX_MINUTES else DEFAULT_MINUTES

    def codes(self):
        stored = [c for c in str(self._store.value("fx/codes", "") or "").upper().split(",") if c in NAMES]
        return tuple(dict.fromkeys(stored)) or DEFAULT_CODES

    def save(self, enabled, minutes, codes):
        try:
            minutes = int(str(minutes).strip())
        except ValueError:
            raise ValueError("اكتب عدد الدقائق بالأرقام.") from None
        if not MIN_MINUTES <= minutes <= MAX_MINUTES:
            raise ValueError(f"التحديث كل {MIN_MINUTES} دقائق على الأقل وحتى {MAX_MINUTES} دقيقة (الخطة المجانية 100 طلب في الساعة).")
        chosen = [c for c in dict.fromkeys(str(c).upper() for c in codes) if c in NAMES]
        if not chosen:
            raise ValueError("اختر عملة واحدة على الأقل.")
        self._store.setValue("fx/enabled", "true" if enabled else "false")
        self._store.setValue("fx/minutes", minutes)
        self._store.setValue("fx/codes", ",".join(chosen))
        self._store.sync()


class CurrencyRatesService:
    """The cached read the home page uses and the settings test. `provider_factory()` builds a GoldTimoProvider (the keys are read then, never stored here)."""
    def __init__(self, settings=None, provider_factory=None, monotonic=None, history=None):
        self._history = history                          # an FxHistory (the shared service passes one); None in tests: nothing is written
        self._settings = settings or FxSettings()
        self._factory = provider_factory or _default_provider
        self._monotonic = monotonic or time.monotonic
        self._cache, self._stamp, self._failed, self._error = None, None, False, ""

    def snapshot(self, force=False):
        """{enabled, available, rows, updated, error}: served from the cache until the refresh interval passed (a failure is retried after 5 minutes, never on every dashboard load)."""
        if not self._settings.enabled():
            return {"enabled": False, "available": False, "rows": [], "updated": "", "error": ""}
        wait = FAILURE_RETRY_SECONDS if self._failed else self._settings.minutes() * 60
        if force or self._stamp is None or self._monotonic() - self._stamp >= wait:
            self._refresh()
        return self._view()

    def _refresh(self):
        self._stamp = self._monotonic()
        try:
            self._cache, self._failed = self._factory().fetch_rates(), False
            self._error = ""
            if self._history is not None:
                self._history.record(build_rows(self._cache["rates"], ("USD", "EUR")))
        except Exception as exc:                      # the provider raises its own coded errors; the card must never break the home page
            self._failed, self._error = True, _reason(exc)

    def _view(self):
        if self._cache is None:
            return {"enabled": True, "available": False, "rows": [], "updated": "", "error": self._error}
        fetched = self._cache["fetched_at"]
        local = fetched.astimezone() if getattr(fetched, "tzinfo", None) else fetched
        return {"enabled": True, "available": True, "rows": build_rows(self._cache["rates"], self._settings.codes()), "updated": local.strftime("%H:%M"),
                "error": self._error if self._failed else ""}


def _reason(exc):
    code = getattr(exc, "code", "")
    return {"KEY_MISSING": "مفتاح GoldTimo غير مضبوط في الإعدادات.", "HTTP_AUTH": "GoldTimo رفض المفتاح.", "RATE_LIMIT": "تم تجاوز حد الطلبات في GoldTimo.",
            "TIMEOUT": "انتهت مهلة الاتصال بـ GoldTimo.", "NETWORK": "تعذر الاتصال بـ GoldTimo."}.get(code, "تعذر جلب أسعار العملات من GoldTimo.")


def _default_provider():
    from config.gold_price_config import GoldTimoConfig
    from services.gold_price_provider import GoldTimoProvider
    return GoldTimoProvider(GoldTimoConfig.from_env())


_SHARED = None


def shared():
    """The ONE service of this program (the home page and the settings test share its cache)."""
    global _SHARED
    if _SHARED is None:
        from services import fx_history
        _SHARED = CurrencyRatesService(history=fx_history.shared())
    return _SHARED


class CurrencyRatesSettingsService:
    """Settings → أسعار العملات. SETTINGS_MANAGE is rechecked from the AppSession on every call (the actor is never supplied by QML)."""
    MANAGE = "SETTINGS_MANAGE"

    def __init__(self, session, settings=None, rates=None):
        self._session = session
        self._settings = settings or FxSettings()
        self._rates = rates or shared()

    def _actor(self, expected_actor):
        actor = self._session.require_authenticated()
        if expected_actor is not None and not self._session.is_current(expected_actor):
            raise PermissionError("انتهت جلسة الدخول.")
        if self.MANAGE not in actor.permissions:
            raise PermissionError("ضبط أسعار العملات للمالك فقط.")
        return actor

    def can_manage(self):
        actor = self._session.current_actor()
        return actor is not None and self.MANAGE in actor.permissions

    def current(self, *, _expected_actor=None):
        self._actor(_expected_actor)
        chosen = set(self._settings.codes())
        return {"enabled": self._settings.enabled(), "minutes": str(self._settings.minutes()),
                "currencies": [{"code": code, "name": name, "checked": code in chosen} for code, name in CURRENCIES]}

    def save(self, enabled, minutes, codes, *, _expected_actor=None):
        self._actor(_expected_actor)
        self._settings.save(bool(enabled), minutes, [c for c in str(codes or "").split(",") if c])
        return self.current()

    def test(self, *, _expected_actor=None):
        """Fetch-only check with the stored GoldTimo keys: the rates the card would show now (the answer is not stored anywhere)."""
        self._actor(_expected_actor)
        answer = self._rates.snapshot(force=True) if self._settings.enabled() else None
        if answer is None:                                                                      # test even while the card is hidden: ask the provider directly
            probe = CurrencyRatesService(FxSettings(_Always(self._settings)), self._rates._factory, self._rates._monotonic)
            answer = probe.snapshot(force=True)
        return answer


class _Always:
    """A read-only settings view that says 'enabled' (the test button works while the card is switched off)."""
    def __init__(self, settings):
        self._settings = settings

    def value(self, key, default=None):
        return "true" if key == "fx/enabled" else self._settings._store.value(key, default)
