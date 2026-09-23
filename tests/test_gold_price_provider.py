"""Provider contract tests use fake HTTP responses, never keys or live services."""
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from http.client import IncompleteRead
import io
import json
import unittest
from unittest.mock import patch
from urllib.error import HTTPError, URLError

from config.gold_price_config import GoldTimoConfig
from services.gold_price_provider import (
    GoldTimoProvider, NormalizedQuote, ProviderError, QuoteValidationError,
    _RejectRedirects, configured_providers, validate_quotes,
)


NOW = datetime(2026, 9, 23, 12, 0, tzinfo=timezone.utc)


def complete_quotes():
    return tuple(NormalizedQuote(
        source_code="TEST", karat=karat,
        shop_buy_price_per_gram=Decimal("6100.12345"),
        shop_sell_price_per_gram=Decimal("6200.67895"),
        currency="EGP", unit="GRAM", provider_timestamp=NOW, fetched_at=NOW,
    ) for karat in (18, 21, 24))


class Response(io.BytesIO):
    status = 200


class NormalizedQuoteTests(unittest.TestCase):
    def test_valid_set_rounds_decimal_and_orders_karats(self):
        values = validate_quotes(reversed(complete_quotes()), "TEST", NOW)
        self.assertEqual([q.karat for q in values], [18, 21, 24])
        self.assertEqual(values[0].shop_buy_price_per_gram, Decimal("6100.1235"))
        self.assertEqual(values[0].shop_sell_price_per_gram, Decimal("6200.6790"))
        self.assertIsInstance(values[0].shop_buy_price_per_gram, Decimal)

    def test_currency_unit_karat_and_source_rejected(self):
        for changed in ({"currency": "USD"}, {"currency": "egp"}, {"unit": "oz"},
                        {"karat": 22}, {"karat": "18"}, {"karat": 18.0},
                        {"source_code": "OTHER"}):
            with self.subTest(changed=changed), self.assertRaises(QuoteValidationError):
                values = list(complete_quotes())
                values[0] = replace(values[0], **changed)
                validate_quotes(values, "TEST", NOW)

    def test_gram_aliases_are_canonicalized(self):
        for unit in ("g", "gram", "GRAM", "grams", "per_gram", "price_per_gram", "EGP/g"):
            with self.subTest(unit=unit):
                values = [replace(q, unit=unit) for q in complete_quotes()]
                self.assertTrue(all(q.unit == "GRAM" for q in validate_quotes(values, "TEST", NOW)))

    def test_invalid_prices_rejected_on_both_sides(self):
        for price in (Decimal("0"), Decimal("-1"), Decimal("NaN"), Decimal("sNaN"),
                      Decimal("Infinity"), Decimal("-Infinity"), Decimal("10000000000"),
                      Decimal("0.000001"), 6100.0, 6100, "6100", None):
            for side in ("shop_buy_price_per_gram", "shop_sell_price_per_gram"):
                with self.subTest(price=price, side=side), self.assertRaises(QuoteValidationError):
                    values = list(complete_quotes())
                    values[0] = replace(values[0], **{side: price})
                    validate_quotes(values, "TEST", NOW)

    def test_reversed_spread_is_not_swapped_or_hidden_by_rounding(self):
        for buy, sell in (("6200", "6100"), ("6200.00002", "6200.00001")):
            with self.subTest(buy=buy), self.assertRaises(QuoteValidationError):
                values = [replace(q, shop_buy_price_per_gram=Decimal(buy),
                                  shop_sell_price_per_gram=Decimal(sell)) for q in complete_quotes()]
                validate_quotes(values, "TEST", NOW)

    def test_missing_duplicate_extra_and_mixed_timestamps_rejected(self):
        values = complete_quotes()
        invalid = (values[:2], values + (values[0],), (values[0], values[0], values[2]),
                   (replace(values[0], provider_timestamp=NOW - timedelta(seconds=1)), *values[1:]))
        for quotes in invalid:
            with self.subTest(quotes=quotes), self.assertRaises(QuoteValidationError):
                validate_quotes(quotes, "TEST", NOW)

    def test_future_and_naive_timestamps_rejected(self):
        for field in ("provider_timestamp", "fetched_at"):
            for stamp in (NOW + timedelta(seconds=121), NOW.replace(tzinfo=None), None):
                with self.subTest(field=field, stamp=stamp), self.assertRaises(QuoteValidationError):
                    values = [replace(q, **{field: stamp}) for q in complete_quotes()]
                    validate_quotes(values, "TEST", NOW)

    def test_timezone_normalized_to_utc(self):
        cairo = timezone(timedelta(hours=3))
        values = [replace(q, provider_timestamp=NOW.astimezone(cairo)) for q in complete_quotes()]
        result = validate_quotes(values, "TEST", NOW)
        self.assertEqual(result[0].provider_timestamp.tzinfo, timezone.utc)

    def test_database_timestamp_precision_and_common_fetch_time(self):
        values = [replace(q, provider_timestamp=NOW.replace(microsecond=123456),
                          fetched_at=NOW.replace(microsecond=654321)) for q in complete_quotes()]
        result = validate_quotes(values, "TEST", NOW)
        self.assertEqual(result[0].provider_timestamp, NOW)
        self.assertEqual(result[0].fetched_at, NOW)
        values[0] = replace(values[0], fetched_at=NOW - timedelta(seconds=1))
        with self.assertRaisesRegex(QuoteValidationError, "completion time"):
            validate_quotes(values, "TEST", NOW)

    def test_distinct_subsecond_provider_updates_cannot_share_a_publication(self):
        values = [replace(q, provider_timestamp=NOW.replace(microsecond=123456))
                  for q in complete_quotes()]
        values[0] = replace(values[0], provider_timestamp=NOW.replace(microsecond=654321))
        with self.assertRaisesRegex(QuoteValidationError, "coherent timestamp"):
            validate_quotes(values, "TEST", NOW)

    def test_old_and_repeated_timestamps_preserve_age(self):
        old = NOW - timedelta(days=2)
        values = [replace(q, provider_timestamp=old) for q in complete_quotes()]
        first = validate_quotes(values, "TEST", NOW)
        later = NOW + timedelta(minutes=30)
        second = validate_quotes([replace(q, fetched_at=later) for q in values], "TEST", later)
        self.assertEqual(first[0].provider_timestamp, second[0].provider_timestamp)
        self.assertGreater(later - second[0].provider_timestamp, NOW - first[0].provider_timestamp)


class GoldTimoProviderTests(unittest.TestCase):
    def setUp(self):
        self.requests = []
        self.payload_transform = lambda payload: payload
        self.config = GoldTimoConfig(api_key="private-test-key", timeout_seconds=5)
        self.provider = GoldTimoProvider(self.config, opener=self.open, clock=lambda: NOW)

    def open(self, request, *, timeout):
        self.requests.append((request, timeout))
        karat = int(request.full_url.rsplit("/", 1)[-1])
        payload = {
            "symbol": "XAU", "currency": "EGP", "unit": "oz", "karat": karat,
            "gramBuy": 6200.25, "gramSell": 6100.15, "price": 220000,
            "updatedAt": NOW.isoformat(),
        }
        return Response(json.dumps(self.payload_transform(payload)).encode())

    def test_documented_mapping_decimal_prices_complete_set_and_api_key_header(self):
        quotes = self.provider.fetch()
        self.assertEqual([q.karat for q in quotes], [18, 21, 24])
        for quote in quotes:
            self.assertEqual(quote.source_code, "GOLDTIMO")
            self.assertEqual(quote.shop_buy_price_per_gram, Decimal("6100.1500"))
            self.assertEqual(quote.shop_sell_price_per_gram, Decimal("6200.2500"))
            self.assertEqual(quote.unit, "GRAM")
            self.assertEqual(quote.provider_timestamp, NOW)
        self.assertEqual(len(self.requests), 3)
        for request, timeout in self.requests:
            self.assertEqual(request.get_header("X-api-key"), "private-test-key")
            self.assertNotIn("private-test-key", request.full_url)
            self.assertGreater(timeout, 0)
            self.assertLessEqual(timeout, 5)

    def test_contract_violations_do_not_produce_a_partial_set(self):
        cases = ({"currency": "USD"}, {"unit": "g"}, {"symbol": "XAG"}, {"karat": 22},
                 {"gramSell": 0}, {"gramBuy": -1}, {"gramBuy": 6000},
                 {"gramBuy": "6200"}, {"gramBuy": float("nan")},
                 {"gramBuy": float("inf")}, {"updatedAt": "garbage"},
                 {"updatedAt": "2026-09-23T12:00:00"},
                 {"updatedAt": (NOW + timedelta(hours=1)).isoformat()})
        for changed in cases:
            with self.subTest(changed=changed), self.assertRaises(QuoteValidationError):
                self.payload_transform = lambda payload, changed=changed: {**payload, **changed}
                self.provider.fetch()

    def test_missing_buy_field_is_ambiguous_and_rejected(self):
        self.payload_transform = lambda payload: {k: v for k, v in payload.items() if k != "gramBuy"}
        with self.assertRaises(QuoteValidationError):
            self.provider.fetch()

    def test_mixed_provider_timestamps_are_rejected(self):
        self.payload_transform = lambda payload: {
            **payload, "updatedAt": (NOW - timedelta(seconds=payload["karat"])).isoformat(),
        }
        with self.assertRaisesRegex(QuoteValidationError, "coherent timestamp"):
            self.provider.fetch()

    def test_invalid_json_or_duplicate_fields(self):
        for content in (b"not json", b"[]", b'{"karat": 18, "karat": 21}', b"\xff"):
            with self.subTest(content=content), self.assertRaises(QuoteValidationError):
                self.provider._open = lambda *a, **k: Response(content)
                self.provider.fetch()

    def test_timeout_network_failure_and_http_errors_do_not_echo_secrets(self):
        failures = [TimeoutError("private-test-key"), URLError("private-test-key"),
                    IncompleteRead(b"private-test-key", 100)]
        failures.extend(HTTPError("https://test/private-test-key", code, "private-test-key", {}, None)
                        for code in (401, 403, 429, 500))
        for failure in failures:
            with self.subTest(failure=type(failure).__name__):
                def fail(*args, **kwargs):
                    raise failure
                self.provider._open = fail
                with self.assertRaises(ProviderError) as error:
                    self.provider.fetch()
                self.assertNotIn("private-test-key", str(error.exception))

    def test_credentials_cannot_follow_redirects(self):
        self.assertIsNone(_RejectRedirects().redirect_request(
            None, None, 302, "redirect", {}, "https://other-host.example",
        ))

    def test_fetch_time_is_whole_set_completion_time(self):
        times = iter([NOW - timedelta(seconds=2), NOW - timedelta(seconds=1), NOW, NOW])
        self.provider._clock = lambda: next(times)
        quotes = self.provider.fetch()
        self.assertEqual({quote.fetched_at for quote in quotes}, {NOW})

    def test_missing_key_and_oversized_payload(self):
        with self.assertRaises(ProviderError):
            GoldTimoProvider(GoldTimoConfig(), opener=self.open).fetch()
        self.provider._open = lambda *a, **k: Response(b"x" * 65537)
        with self.assertRaisesRegex(ProviderError, "size limit"):
            self.provider.fetch()

    def test_total_time_budget_stops_before_request(self):
        times = iter([0, 6])
        provider = GoldTimoProvider(self.config, opener=self.open, monotonic=lambda: next(times))
        with self.assertRaisesRegex(ProviderError, "timed out"):
            provider.fetch()
        self.assertFalse(self.requests)


class GoldTimoConfigTests(unittest.TestCase):
    def test_secret_excluded_from_repr_and_invalid_config_rejected(self):
        self.assertNotIn("private-test-key", repr(GoldTimoConfig(api_key="private-test-key")))
        invalid = ({"base_url": "http://example.com"}, {"base_url": "https://user:pass@example.com"},
                   {"base_url": "https://example.com?api_key=secret"}, {"api_key": "key\r\ninjected"},
                   {"timeout_seconds": 0}, {"timeout_seconds": 31}, {"timeout_seconds": float("nan")})
        for values in invalid:
            with self.subTest(values=values), self.assertRaises(ValueError):
                GoldTimoConfig(**values)

    @patch("services.gold_price_provider.GoldTimoConfig.from_env")
    def test_registry_skips_absent_key_without_network_io(self, config):
        config.return_value = GoldTimoConfig()
        self.assertEqual(configured_providers(), {})
        config.return_value = GoldTimoConfig(api_key="private-test-key")
        providers = configured_providers()
        self.assertEqual(list(providers), ["GOLDTIMO"])


if __name__ == "__main__":
    unittest.main()
