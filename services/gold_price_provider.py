"""Provider boundary: only explicit shop-perspective Decimal quotes leave here.

GoldTimo contract: https://goldtimo.com/eg/en/docs
Customer-perspective buy/sell: https://goldtimo.com/eg/en/gold
Its gramSell means the customer sells to the dealer (SHOP_BUY).
Its gramBuy means the customer buys from the dealer (SHOP_SELL).
This fixed semantic mapping never changes based on the numerical spread.
"""
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP, localcontext
from http.client import HTTPException, HTTPSConnection
import json
import logging
import os
import time
from typing import Iterable, Protocol
from uuid import uuid4
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, HTTPSHandler, Request, build_opener

from config.gold_price_config import GoldTimoConfig


EXPECTED_KARATS = frozenset({18, 21, 24})
PRICE_QUANTUM = Decimal("0.0001")
MAX_PRICE = Decimal("9999999999.9999")
MAX_RESPONSE_BYTES = 65536
GOLDTIMO_MAX_SKEW_SECONDS = 5


class ProviderError(RuntimeError):
    """Transport/configuration failure safe to show without credentials."""
    def __init__(self, message, *, code="NETWORK", http_status=None):
        super().__init__(message)
        self.code, self.http_status = code, http_status


class QuoteValidationError(ValueError):
    """Untrusted provider data must not be activated."""
    def __init__(self, message, *, code="QUOTE_VALIDATION"):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class NormalizedQuote:
    source_code: str
    karat: int
    shop_buy_price_per_gram: Decimal
    shop_sell_price_per_gram: Decimal
    currency: str
    unit: str
    provider_timestamp: datetime
    fetched_at: datetime
    refresh_attempt_id: str | None = None


class GoldPriceProvider(Protocol):
    source_code: str

    def fetch(self) -> tuple[NormalizedQuote, ...]:
        """Fetch a complete set off the GUI thread and outside DB transactions."""
        ...


def _utc(value, label):
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise QuoteValidationError(f"{label} must include a timezone.")
    return value.astimezone(timezone.utc)


def _price(value):
    if not isinstance(value, Decimal):
        raise QuoteValidationError("Normalized prices must be Decimal, never float.")
    if not value.is_finite() or value <= 0 or value > MAX_PRICE:
        raise QuoteValidationError("Price must be finite, positive and fit DECIMAL(14,4).")
    try:
        with localcontext() as context:
            context.prec = 32
            result = value.quantize(PRICE_QUANTUM, rounding=ROUND_HALF_UP)
    except InvalidOperation as exc:
        raise QuoteValidationError("Price cannot be represented at four decimal places.") from exc
    if result <= 0 or result > MAX_PRICE:
        raise QuoteValidationError("Rounded price must be positive and fit DECIMAL(14,4).")
    return result


def validate_quotes(
    quotes: Iterable[NormalizedQuote], expected_source: str, now: datetime,
    future_tolerance_seconds: int = 120,
) -> tuple[NormalizedQuote, ...]:
    """Validate an entire coherent publication, then return its canonical form.

    GoldTimo's separate requests may differ by at most five seconds within one
    refresh attempt. Other providers still require exact timestamp equality.
    Age is based on provider_timestamp, never a newly assigned fetched_at.
    """
    now = _utc(now, "Validation time")
    if future_tolerance_seconds < 0:
        raise ValueError("Future tolerance cannot be negative.")
    future_limit = now + timedelta(seconds=future_tolerance_seconds)
    quotes = tuple(quotes)
    if len(quotes) != len(EXPECTED_KARATS):
        raise QuoteValidationError("Provider must return exactly 18K, 21K and 24K.")
    normalized = []
    seen = set()
    provider_timestamps = set()
    for quote in quotes:
        if not isinstance(quote, NormalizedQuote):
            raise QuoteValidationError("Provider response is not a normalized quote.")
        if not expected_source or quote.source_code != expected_source:
            raise QuoteValidationError("Provider source does not match the configured source.")
        if type(quote.karat) is not int or quote.karat not in EXPECTED_KARATS or quote.karat in seen:
            raise QuoteValidationError("Provider returned an unexpected or duplicate karat.")
        seen.add(quote.karat)
        if quote.currency != "EGP":
            raise QuoteValidationError("Provider currency must be EGP.")
        if not isinstance(quote.unit, str) or quote.unit.strip().lower() not in {
            "g", "gram", "grams", "per_gram", "price_per_gram", "egp/g", "egp/gram",
        }:
            raise QuoteValidationError("Provider unit must resolve to a price per gram.")
        buy = _price(quote.shop_buy_price_per_gram)
        sell = _price(quote.shop_sell_price_per_gram)
        # Compare original values too: rounding must never hide reversed semantics.
        if quote.shop_sell_price_per_gram < quote.shop_buy_price_per_gram or sell < buy:
            raise QuoteValidationError("Shop sell price is below shop buy price; response rejected.")
        provider_timestamp = _utc(quote.provider_timestamp, "Provider timestamp")
        fetched_at = _utc(quote.fetched_at, "Fetch timestamp")
        if provider_timestamp > future_limit or fetched_at > future_limit:
            raise QuoteValidationError("Provider or fetch timestamp is implausibly in the future.")
        if provider_timestamp > fetched_at + timedelta(seconds=future_tolerance_seconds):
            raise QuoteValidationError("Provider timestamp is later than its fetch timestamp.")
        # Check exact provider instants before adapting to MySQL DATETIME's
        # whole-second precision; distinct subsecond updates cannot be mixed.
        provider_timestamps.add(provider_timestamp)
        normalized.append(replace(
            quote, shop_buy_price_per_gram=buy, shop_sell_price_per_gram=sell,
            unit="GRAM", provider_timestamp=provider_timestamp.replace(microsecond=0),
            fetched_at=fetched_at.replace(microsecond=0),
        ))
    if expected_source == "GOLDTIMO":
        attempts = {quote.refresh_attempt_id for quote in quotes}
        if len(attempts) != 1 or not next(iter(attempts)):
            raise QuoteValidationError("GoldTimo quotes must belong to the same refresh attempt.")
        if (max(provider_timestamps) - min(provider_timestamps)).total_seconds() > GOLDTIMO_MAX_SKEW_SECONDS:
            raise QuoteValidationError("GoldTimo timestamp skew exceeds five seconds.")
    elif len(provider_timestamps) != 1:
        raise QuoteValidationError("Provider karats do not share one coherent timestamp.")
    if len({quote.fetched_at for quote in normalized}) != 1:
        raise QuoteValidationError("Provider karats must share their set's fetch completion time.")
    return tuple(sorted(normalized, key=lambda quote: quote.karat))


class _RejectRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # API keys must never follow a redirect to another host.
        return None


def _reject_constant(value):
    raise QuoteValidationError("Provider JSON contains a non-finite number.", code="INVALID_JSON")


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise QuoteValidationError("Provider JSON contains a duplicate field.", code="INVALID_JSON")
        result[key] = value
    return result


class _GoldTimoHTTPSConnection(HTTPSConnection):
    def request(self, method, url, body=None, headers=None, *, encode_chunked=False):
        headers = {("x-api-key" if name.lower() == "x-api-key" else name): value
                   for name, value in (headers or {}).items()}
        return super().request(method, url, body, headers, encode_chunked=encode_chunked)


class _GoldTimoHTTPSHandler(HTTPSHandler):
    def https_open(self, request):
        return self.do_open(_GoldTimoHTTPSConnection, request, context=self._context)


class GoldTimoProvider:
    source_code = "GOLDTIMO"

    def __init__(self, config: GoldTimoConfig, *, opener=None, clock=None, monotonic=None):
        self.config = config
        self._open = opener or build_opener(_RejectRedirects(), _GoldTimoHTTPSHandler()).open
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._monotonic = monotonic or time.monotonic
        self.request_diagnostics = []

    def _request_config(self, entered_key):
        if entered_key is None:
            return self.config
        # Debug path: direct transient input, with no keyring/environment read.
        return GoldTimoConfig(base_url=self.config.base_url, api_key=entered_key,
                              timeout_seconds=self.config.timeout_seconds)

    def fetch(self, *, entered_key=None) -> tuple[NormalizedQuote, ...]:
        config = self._request_config(entered_key)
        quotes = self._fetch_quotes(config, sorted(EXPECTED_KARATS))
        fetched_at = self._clock()
        quotes = [replace(quote, fetched_at=fetched_at) for quote in quotes]
        return validate_quotes(quotes, self.source_code, fetched_at)

    def probe_21(self, *, entered_key=None):
        """Fetch-only diagnostic preflight, never a complete publishable set."""
        return self._fetch_quotes(self._request_config(entered_key), (21,))[0]

    def _fetch_quotes(self, config, karats):
        self.request_diagnostics = []
        attempt_id = str(uuid4())
        if not config.api_key:
            raise ProviderError("GoldTimo API key is not configured.", code="KEY_MISSING")
        deadline = self._monotonic() + config.timeout_seconds
        quotes = []
        for karat in karats:
            remaining = deadline - self._monotonic()
            if remaining <= 0:
                raise ProviderError("GoldTimo request timed out.", code="TIMEOUT")
            request = Request(
                f"{config.base_url.rstrip('/')}/price/XAU/EGP/{karat}",
                headers={"x-api-key": config.api_key.strip(), "Accept": "application/json",
                         "User-Agent": "masGold/2 GoldTimoProvider"},
                method="GET",
            )
            try:
                with self._open(request, timeout=remaining) as response:
                    self._log_request(karat, response.status, config)
                    if response.status != 200:
                        raise self._http_error(response.status)
                    chunks = []
                    size = 0
                    while True:
                        if self._monotonic() >= deadline:
                            raise ProviderError("GoldTimo request timed out.", code="TIMEOUT")
                        chunk = response.read1(min(8192, MAX_RESPONSE_BYTES + 1 - size))
                        if not chunk:
                            break
                        size += len(chunk)
                        if size > MAX_RESPONSE_BYTES:
                            raise ProviderError("GoldTimo response exceeds the size limit.")
                        chunks.append(chunk)
                if self._monotonic() >= deadline:
                    raise ProviderError("GoldTimo request timed out.", code="TIMEOUT")
                payload = json.loads(
                    b"".join(chunks), parse_float=Decimal, parse_int=Decimal,
                    parse_constant=_reject_constant, object_pairs_hook=_unique_object,
                )
            except HTTPError as exc:
                self._log_request(karat, exc.code, config)
                # HTTP error bodies/URLs may echo credentials; expose only status.
                raise self._http_error(exc.code) from None
            except TimeoutError:
                self._log_request(karat, None, config)
                raise ProviderError("GoldTimo request timed out.", code="TIMEOUT") from None
            except URLError as exc:
                self._log_request(karat, None, config)
                code = "TIMEOUT" if isinstance(exc.reason, TimeoutError) else "NETWORK"
                raise ProviderError("GoldTimo request failed or timed out.", code=code) from None
            except (OSError, HTTPException):
                self._log_request(karat, None, config)
                raise ProviderError("GoldTimo request failed or timed out.") from None
            except (json.JSONDecodeError, UnicodeDecodeError, RecursionError):
                raise QuoteValidationError("GoldTimo returned invalid JSON.", code="INVALID_JSON") from None
            except QuoteValidationError:
                raise
            except (ValueError, UnicodeEncodeError):
                raise ProviderError("GoldTimo API key has an invalid header format.", code="CONFIGURATION") from None
            quotes.append(replace(self._normalize(payload, karat, self._clock()),
                                  refresh_attempt_id=attempt_id))
        return quotes

    def _log_request(self, karat, status, config):
        # Temporary credential diagnostics expose only these four safe fields.
        source = config.credential_trace.get("credential_source")
        metadata = {"credential_source": source if source in ("ENTERED", "KEYRING", "ENV", "NONE") else "NONE",
                    "configured": bool(config.api_key), "character_count": len(config.api_key),
                    "http_status": status if type(status) is int and 100 <= status <= 599 else None}
        self.request_diagnostics.append(metadata)
        level = logging.WARNING if os.getenv("MASGOLD_GOLDTIMO_TRACE") == "1" else logging.INFO
        logging.getLogger(__name__).log(level, " ".join(f"{key}={value}" for key, value in metadata.items()))

    @staticmethod
    def _http_error(status):
        code = "HTTP_AUTH" if status in (401, 403) else "RATE_LIMIT" if status == 429 else "HTTP_ERROR"
        return ProviderError(f"GoldTimo returned HTTP {status}.", code=code, http_status=status)

    def _normalize(self, payload, karat, fetched_at):
        if not isinstance(payload, dict):
            raise QuoteValidationError("GoldTimo response must be a JSON object.", code="SCHEMA_UNIT")
        required = {"symbol", "currency", "unit", "karat", "gramBuy", "gramSell", "updatedAt"}
        if not required.issubset(payload):
            raise QuoteValidationError("GoldTimo response is missing required fields.", code="SCHEMA_UNIT")
        if payload["symbol"] != "XAU":
            raise QuoteValidationError("GoldTimo returned an unexpected metal.", code="SCHEMA_UNIT")
        if payload["karat"] != Decimal(karat):
            raise QuoteValidationError("GoldTimo returned an unexpected karat.", code="KARAT_MISMATCH")
        if payload["currency"] != "EGP":
            raise QuoteValidationError("GoldTimo currency must be EGP.", code="CURRENCY_MISMATCH")
        # The documented ounce price and gram fields coexist. Do not convert
        # gramBuy/gramSell by the ounce unit, and reject an unknown contract.
        if payload["unit"] != "oz":
            raise QuoteValidationError("GoldTimo ounce unit does not match its documented contract.", code="SCHEMA_UNIT")
        if not isinstance(payload["updatedAt"], str):
            raise QuoteValidationError("GoldTimo timestamp must be an ISO 8601 string.")
        try:
            provider_timestamp = datetime.fromisoformat(payload["updatedAt"].replace("Z", "+00:00"))
        except ValueError:
            raise QuoteValidationError("GoldTimo timestamp is invalid.") from None
        return NormalizedQuote(
            source_code=self.source_code, karat=karat,
            shop_buy_price_per_gram=payload["gramSell"],
            shop_sell_price_per_gram=payload["gramBuy"],
            currency=payload["currency"], unit="GRAM",
            provider_timestamp=provider_timestamp, fetched_at=fetched_at,
        )


def configured_providers() -> dict[str, GoldPriceProvider]:
    """No I/O beyond configuration; database source selection stays in the service."""
    config = GoldTimoConfig.from_env()
    if not config.api_key:
        return {}
    return {GoldTimoProvider.source_code: GoldTimoProvider(config)}
