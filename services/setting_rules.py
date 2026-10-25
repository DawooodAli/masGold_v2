"""Effective business settings (migration 081 `system_settings`), with the hard-coded figures as
fallback defaults.

The owner edits these values from the System Settings card; the services are what actually APPLY
them, so a change takes effect on the next report / invoice / market-trip operation without a
release. Before this module the same figures lived as Python constants, which is why they are kept
here as the documented defaults.

Reading is deliberately UNRESTRICTED: opening the settings screen needs SYSTEM_SETTINGS_VIEW, but
these are business rules, so they are read through the CALLER'S OWN repository inside its existing
transaction (`BaseRepository.business_values`) — no extra connection and no settings right. A
repository that cannot read them (a test fake, a database below 081) simply yields nothing.

Every value is stored as text and parsed here against a hard range. A key that is missing, blank,
unparseable or outside its range falls back to that key's default — never to zero and never with an
exception: a mistyped setting must not be able to change a financial rule by accident.

Not every seeded key is wired here. `enable_book_mode` and `auto_login_enabled` stay isolated on
purpose (BOOK is intentionally off and auto-login is governed by its environment switch), and the
display keys (shop name, address, phone, currency symbol, date format, print copies) have no
consumer yet. `SALE_DISCOUNT_MIN` (the 1.25% minimum on a MIXED invoice) has no key in 081 and stays
hard-coded in `invoice_service`."""
from decimal import Decimal, InvalidOperation
from typing import Any, Iterable, Optional

INT, DECIMAL = "INT", "DECIMAL"

# setting_key -> (kind, default, lowest accepted, highest accepted). The defaults are the figures the
# code used before 081; the ranges refuse nonsense (a negative window, a 400% discount, a 0-day cover).
SPEC: dict[str, tuple[str, Any, Any, Any]] = {
    "dead_stock_days":               (INT, 180, 1, 3650),
    "velocity_days":                 (INT, 90, 7, 3650),
    "low_cover_days":                (INT, 14, 1, 3650),
    "weight_band_top":               (INT, 10, 1, 10000),
    "min_sales_for_velocity":        (INT, 3, 1, 1000),
    "price_deviation_warning_pct":   (DECIMAL, Decimal("10"), Decimal("0"), Decimal("100")),
    "default_old_gold_discount_pct": (DECIMAL, Decimal("1.50"), Decimal("0"), Decimal("100")),
    "min_old_gold_discount_pct":     (DECIMAL, Decimal("1.00"), Decimal("0"), Decimal("100")),
    "max_old_gold_discount_pct":     (DECIMAL, Decimal("5.00"), Decimal("0"), Decimal("100")),
}

DEFAULTS: dict[str, Any] = {key: spec[1] for key, spec in SPEC.items()}

REPORT_KEYS = ("dead_stock_days", "velocity_days", "low_cover_days", "weight_band_top", "min_sales_for_velocity")
DISCOUNT_KEYS = ("default_old_gold_discount_pct", "min_old_gold_discount_pct", "max_old_gold_discount_pct")
PRICE_WARNING_KEY = "price_deviation_warning_pct"


def effective(raw: dict[str, Any], keys: Optional[Iterable[str]] = None) -> dict[str, Any]:
    """Typed values for `keys` (every SPEC key when None) out of stored text. Anything unusable
    yields that key's default."""
    chosen = tuple(keys) if keys is not None else tuple(SPEC)
    return {key: _parse(raw.get(key), *SPEC[key]) for key in chosen if key in SPEC}


def report_thresholds(repo) -> dict[str, Any]:
    """The five alert thresholds of the inventory-alerts report (dead stock, sales velocity, low
    cover, the top weight band and the minimum number of sales)."""
    return effective(_stored(repo, REPORT_KEYS), REPORT_KEYS)


def discount_limits(repo) -> dict[str, Any]:
    """The old-gold discount triple: the default the employee starts from and the range a line must
    stay inside."""
    return effective(_stored(repo, DISCOUNT_KEYS), DISCOUNT_KEYS)


def price_warning_percent(repo) -> Decimal:
    """How far a market-trip sale / purchase may sit from the SHOP_BUY reference before the owner has
    to acknowledge it (a soft warning, never an automatic refusal)."""
    return effective(_stored(repo, (PRICE_WARNING_KEY,)), (PRICE_WARNING_KEY,))[PRICE_WARNING_KEY]


def price_warning_from(payload) -> Decimal:
    """The same figure as carried by a payload the service already loaded (`context`, the marketer
    `account`), so a PREVIEW never reads the database itself — it must warn exactly when posting
    will, and posting re-reads the setting in its own transaction."""
    return _parse((payload or {}).get(PRICE_WARNING_KEY), *SPEC[PRICE_WARNING_KEY])


def writer_problem(key, text) -> Optional[str]:
    """Why the System Settings screen must REFUSE a value for a key this module applies, or None.

    The reader falls back silently (a mistyped setting must not change a financial rule), so the
    WRITER has to refuse first: a stored value outside the range would be shown as saved while the
    business rule keeps its default. Blank is refused too (to restore a default the owner types its
    value), and a key that is not one of ours (`enable_book_mode`, the display keys ...) is not our
    business and yields None."""
    spec = SPEC.get(str(key or "").strip())
    if spec is None:
        return None
    kind, default, low, high = spec
    wanted = (f"القيمة يجب أن تكون عددًا صحيحًا بين {low} و {high} (الافتراضي: {default})."
              if kind == INT else f"القيمة يجب أن تكون رقمًا بين {low} و {high} (الافتراضي: {default}).")
    if text is None or not str(text).strip():
        return wanted
    try:
        value = Decimal(str(text).strip())
    except InvalidOperation:
        return wanted
    if not value.is_finite():
        return wanted
    if kind == INT and value != value.to_integral_value():
        return wanted
    if value < low or value > high:
        return wanted
    return None


def _stored(repo, keys) -> dict[str, Any]:
    """The raw text of `keys` as the caller's repository can read it. A repository without that
    reader (a test fake) yields nothing, so the caller keeps its defaults."""
    reader = getattr(repo, "business_values", None)
    return dict(reader(tuple(keys))) if reader else {}


def _parse(stored, kind, default, low, high):
    """One stored text value -> its typed figure, or the default when it is unusable."""
    if stored is None:
        return default
    text = str(stored).strip()
    if not text:
        return default
    try:
        value = Decimal(text)
    except InvalidOperation:
        return default
    if not value.is_finite():
        return default
    if kind == INT:
        if value != value.to_integral_value():                           # "12.0" is an integer, "12.5" is not
            return default
        value = int(value)
    if value < low or value > high:
        return default
    return value
