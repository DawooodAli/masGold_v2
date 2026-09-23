"""Fetch-only operator diagnostic. No database imports, writes or activation.

Run: python -m tools.diagnose_gold_prices
Credentials are read exclusively by GoldTimoConfig.from_env().
"""
from datetime import datetime, timezone
import json

from config.gold_price_config import GoldTimoConfig
from services.gold_price_provider import GoldTimoProvider, ProviderError, QuoteValidationError


class DiagnosticProvider(GoldTimoProvider):
    """Use the production transport/validation, retain timestamp spelling only."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.raw_timestamps = {}

    def _normalize(self, payload, karat, fetched_at):
        quote = super()._normalize(payload, karat, fetched_at)
        # The parent has already parsed this field as an ISO timestamp.
        self.raw_timestamps[karat] = payload["updatedAt"]
        return quote


def diagnose(config, *, opener=None, clock=None, stale_after_seconds=300):
    clock = clock or (lambda: datetime.now(timezone.utc))
    result = {"source": "GOLDTIMO", "checked_at": clock().isoformat(),
              "validation": "NOT_FETCHED", "fetched_at": None, "prices": [],
              "activation_performed": False}
    if not config.api_key:
        result["error"] = "MASGOLD_GOLDTIMO_API_KEY is not configured."
        return result
    provider = DiagnosticProvider(config, opener=opener, clock=clock)
    try:
        quotes = provider.fetch()
    except (ProviderError, QuoteValidationError) as exc:
        result.update(validation="REJECTED", error=str(exc))
        return result
    result["fetched_at"] = quotes[0].fetched_at.isoformat()
    result["prices"] = [{
        "karat": q.karat, "raw_provider_timestamp": provider.raw_timestamps[q.karat],
        "provider_timestamp_utc": q.provider_timestamp.isoformat(),
        "currency": q.currency, "provider_ounce_unit": "oz", "price_unit": q.unit,
        "SHOP_BUY_from_gramSell": str(q.shop_buy_price_per_gram),
        "SHOP_SELL_from_gramBuy": str(q.shop_sell_price_per_gram),
    } for q in quotes]
    age = (clock() - quotes[0].provider_timestamp).total_seconds()
    result["validation"] = "PASS" if age <= stale_after_seconds else "REJECTED_STALE"
    result["stale_after_seconds"] = stale_after_seconds
    result["validation_scope"] = "Provider contract, coherent set and freshness only; no DB deviation or authorization check."
    return result


def main():
    try:
        result = diagnose(GoldTimoConfig.from_env())
    except ValueError:
        result = {"validation": "INVALID_CONFIGURATION", "activation_performed": False}
    print(json.dumps(result, ensure_ascii=True, indent=2))
    return 0 if result["validation"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
