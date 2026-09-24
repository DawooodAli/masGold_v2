"""Fetch-only operator diagnostic. No database imports, writes or activation.

Run: python -m tools.diagnose_gold_prices
Supports a direct transient key without any credential-store access.
"""
from datetime import datetime, timezone

from config.gold_price_config import GoldTimoConfig
from services.gold_price_provider import GoldTimoProvider, ProviderError, QuoteValidationError, _price, _utc
from services.gold_price_errors import ConnectionTestError


class DiagnosticProvider(GoldTimoProvider):
    """Use the production transport/validation, retain timestamp spelling only."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.raw_timestamps = {}
        self.observed_prices = []

    def _fetch_quotes(self, config, karats):
        self.raw_timestamps.clear()
        self.observed_prices.clear()
        return super()._fetch_quotes(config, karats)

    def _normalize(self, payload, karat, fetched_at):
        quote = super()._normalize(payload, karat, fetched_at)
        # The parent has already parsed this field as an ISO timestamp.
        self.raw_timestamps[karat] = payload["updatedAt"]
        # Preserve numeric per-endpoint observations even if the complete set
        # fails coherence validation. Never forward an arbitrary response field.
        try:
            buy = _price(quote.shop_buy_price_per_gram)
            sell = _price(quote.shop_sell_price_per_gram)
            stamp = _utc(quote.provider_timestamp, "Provider timestamp")
        except QuoteValidationError:
            return quote
        self.observed_prices.append({
            "karat": karat, "raw_provider_timestamp": self.raw_timestamps[karat],
            "provider_timestamp_utc": stamp.isoformat(), "currency": "EGP",
            "provider_ounce_unit": "oz", "price_unit": "GRAM",
            "SHOP_BUY_from_gramSell": str(buy), "SHOP_SELL_from_gramBuy": str(sell),
            "http_status": 200,
        })
        return quote


def diagnose(config, *, opener=None, clock=None, stale_after_seconds=300,
             entered_key=None, probe_21=False):
    clock = clock or (lambda: datetime.now(timezone.utc))
    result = {"source": "GOLDTIMO", "checked_at": clock().isoformat(),
              "validation": "NOT_FETCHED", "fetched_at": None, "prices": [],
              "activation_performed": False}
    if not (config.api_key if entered_key is None else entered_key.strip()):
        result.update(error_code="KEY_MISSING", error=str(ConnectionTestError("KEY_MISSING")))
        return result
    provider = DiagnosticProvider(config, opener=opener, clock=clock)
    try:
        quotes = ((provider.probe_21(entered_key=entered_key),) if probe_21 else
                  provider.fetch(entered_key=entered_key))
    except (ProviderError, QuoteValidationError) as exc:
        safe = ConnectionTestError(exc.code, getattr(exc, "http_status", None))
        result.update(validation="REJECTED", error_code=safe.code,
                      http_status=safe.http_status, error=str(safe),
                      diagnostics=provider.request_diagnostics,
                      prices=provider.observed_prices,
                      validation_scope="Per-endpoint observations only; complete set rejected, never approved for activation.")
        if len(provider.observed_prices) > 1:
            observed = [datetime.fromisoformat(row["provider_timestamp_utc"])
                        for row in provider.observed_prices]
            result["timestamp_skew_seconds"] = (max(observed) - min(observed)).total_seconds()
            result["provider_timestamp"] = max(observed).isoformat()
        return result
    result["diagnostics"] = provider.request_diagnostics
    result["fetched_at"] = quotes[0].fetched_at.isoformat()
    result["prices"] = [{
        "karat": q.karat, "raw_provider_timestamp": provider.raw_timestamps[q.karat],
        "provider_timestamp_utc": q.provider_timestamp.astimezone(timezone.utc).isoformat(),
        "currency": q.currency, "provider_ounce_unit": "oz", "price_unit": q.unit,
        "SHOP_BUY_from_gramSell": str(q.shop_buy_price_per_gram),
        "SHOP_SELL_from_gramBuy": str(q.shop_sell_price_per_gram),
        "http_status": 200,
    } for q in quotes]
    if probe_21:
        result["validation"] = "FETCHED_21_ONLY"
        result["validation_scope"] = "21K transport/contract preflight only; not a validated publication."
        return result
    timestamps = [q.provider_timestamp for q in quotes]
    result["timestamp_skew_seconds"] = (max(timestamps) - min(timestamps)).total_seconds()
    result["provider_timestamp"] = max(timestamps).isoformat()
    age = (clock() - min(timestamps)).total_seconds()
    result["validation"] = "PASS" if age <= stale_after_seconds else "REJECTED_STALE"
    if result["validation"] == "REJECTED_STALE":
        result.update(error_code="STALE", error=str(ConnectionTestError("STALE")))
    result["stale_after_seconds"] = stale_after_seconds
    result["validation_scope"] = "Provider contract, coherent set and freshness only; no DB deviation or authorization check."
    return result

