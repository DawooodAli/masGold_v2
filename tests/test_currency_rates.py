"""GoldTimo currency rates (home page card): the EGP arithmetic, the cache, the settings bounds and the provider's /rates read with a fake opener (no network)."""
from datetime import datetime, timezone
from decimal import Decimal
import json
import unittest

from services import currency_rates as fx
from services.gold_price_provider import GoldTimoProvider, ProviderError, QuoteValidationError
from config.gold_price_config import GoldTimoConfig

D = Decimal
RATES = {"EGP": D("48.52"), "SAR": D("3.75"), "EUR": D("0.92"), "USD": D("1")}


class MemoryStore:
    def __init__(self):
        self.data = {}

    def value(self, key, default=None):
        return self.data.get(key, default)

    def setValue(self, key, value):
        self.data[key] = value

    def sync(self):
        pass


class Provider:
    def __init__(self, result=None, error=None):
        self.result, self.error, self.calls = result, error, 0

    def fetch_rates(self):
        self.calls += 1
        if self.error:
            raise self.error
        return self.result


class RulesTests(unittest.TestCase):
    def test_egp_per_unit_divides_the_egp_quote_by_the_currency_quote(self):
        self.assertEqual(fx.egp_per_unit(RATES, "USD"), D("48.52"))
        self.assertEqual(fx.egp_per_unit(RATES, "SAR"), D("12.94"))                  # 48.52 / 3.75 = 12.9387
        self.assertEqual(fx.egp_per_unit(RATES, "EUR"), D("52.74"))                  # 48.52 / 0.92 = 52.7391
        self.assertIsNone(fx.egp_per_unit(RATES, "GBP"))                             # not quoted: no invented figure
        self.assertEqual([r["code"] for r in fx.build_rows(RATES, ("USD", "GBP", "SAR"))], ["USD", "SAR"])

    def test_settings_bounds_and_defaults(self):
        settings = fx.FxSettings(MemoryStore())
        self.assertEqual((settings.enabled(), settings.minutes(), settings.codes()), (True, 30, fx.DEFAULT_CODES))
        settings.save(False, "15", ["sar", "XXX", "USD", "usd"])
        self.assertEqual((settings.enabled(), settings.minutes(), settings.codes()), (False, 15, ("SAR", "USD")))
        for minutes in ("5", "abc", "2000"):
            with self.assertRaises(ValueError):
                settings.save(True, minutes, ["USD"])
        with self.assertRaises(ValueError):
            settings.save(True, "30", ["XXX"])


class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.now = [0.0]
        self.fetched = {"rates": RATES, "fetched_at": datetime(2026, 11, 1, 12, 0, tzinfo=timezone.utc)}

    def service(self, provider):
        store = MemoryStore()
        return fx.CurrencyRatesService(fx.FxSettings(store), lambda: provider, lambda: self.now[0]), store

    def test_the_answer_is_cached_until_the_interval_passes(self):
        provider = Provider(self.fetched)
        service, _ = self.service(provider)
        first = service.snapshot()
        self.assertTrue(first["available"])
        self.assertEqual(first["rows"][0], {"code": "USD", "name": "دولار أمريكي", "price": "48.52"})
        service.snapshot()
        self.assertEqual(provider.calls, 1)
        self.now[0] = 30 * 60
        service.snapshot()
        self.assertEqual(provider.calls, 2)

    def test_a_failure_is_reported_plainly_and_retried_after_five_minutes_only(self):
        provider = Provider(error=ProviderError("x", code="HTTP_AUTH"))
        service, _ = self.service(provider)
        answer = service.snapshot()
        self.assertEqual((answer["available"], answer["error"]), (False, "GoldTimo رفض المفتاح."))
        self.now[0] = 100
        service.snapshot()
        self.assertEqual(provider.calls, 1)
        self.now[0] = 301
        service.snapshot()
        self.assertEqual(provider.calls, 2)

    def test_a_failure_after_a_success_keeps_the_last_rates_with_the_error(self):
        provider = Provider(self.fetched)
        service, _ = self.service(provider)
        service.snapshot()
        provider.error = ProviderError("x", code="TIMEOUT")
        self.now[0] = 31 * 60
        answer = service.snapshot()
        self.assertTrue(answer["available"])
        self.assertEqual(answer["error"], "انتهت مهلة الاتصال بـ GoldTimo.")

    def test_disabled_never_calls_the_provider(self):
        provider = Provider(self.fetched)
        service, store = self.service(provider)
        fx.FxSettings(store).save(False, "30", ["USD"])
        self.assertFalse(service.snapshot()["enabled"])
        self.assertEqual(provider.calls, 0)


class Response:
    def __init__(self, body, status=200):
        self.body, self.status = body, status

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self, size=-1):
        return self.body


class ProviderRatesTests(unittest.TestCase):
    def provider(self, responses):
        seen = []

        def opener(request, timeout=None):
            seen.append((request.full_url, request.get_header("X-api-key")))
            result = responses.pop(0)
            if isinstance(result, Exception):
                raise result
            return Response(result)
        provider = GoldTimoProvider(GoldTimoConfig(api_key="key-one", extra_keys=("key-two",)), opener=opener,
                                    clock=lambda: datetime(2026, 11, 1, tzinfo=timezone.utc))
        return provider, seen

    def test_rates_are_read_as_exact_decimals_from_the_rates_endpoint(self):
        provider, seen = self.provider([json.dumps({"base": "USD", "rates": {"EGP": 48.52, "SAR": 3.75, "BAD": -1}}).encode()])
        result = provider.fetch_rates()
        self.assertEqual(result["rates"], {"EGP": D("48.52"), "SAR": D("3.75")})              # a non-positive quote is dropped
        self.assertTrue(seen[0][0].endswith("/api/v1/rates"))

    def test_a_refused_key_moves_on_to_the_next_one(self):
        from urllib.error import HTTPError
        good = json.dumps({"base": "USD", "rates": {"EGP": 48.52}}).encode()
        provider, seen = self.provider([HTTPError("u", 401, "no", {}, None), good])
        provider.fetch_rates()
        self.assertEqual(len(seen), 2)

    def test_the_starting_key_rotates_between_fetches(self):
        good = json.dumps({"base": "USD", "rates": {"EGP": 48.52}}).encode()
        provider, seen = self.provider([good] * 4)
        for _ in range(4):
            provider.fetch_rates()
        self.assertEqual({key for _, key in seen}, {"key-one", "key-two"})                 # both stored keys take turns

    def test_a_response_without_egp_or_the_usd_base_is_refused(self):
        for payload in ({"base": "USD", "rates": {"SAR": 3.75}}, {"base": "EUR", "rates": {"EGP": 50}}, ["x"]):
            provider, _ = self.provider([json.dumps(payload).encode()])
            with self.assertRaises(QuoteValidationError):
                provider.fetch_rates()


class FakeSession:
    class Actor:
        def __init__(self, permissions):
            self.permissions, self.generation = frozenset(permissions), 1

    def __init__(self, permissions):
        self.actor = self.Actor(permissions)

    def require_authenticated(self):
        return self.actor

    def current_actor(self):
        return self.actor

    def is_current(self, actor):
        return actor is self.actor


class SettingsServiceTests(unittest.TestCase):
    def build(self, permissions, provider=None):
        store = MemoryStore()
        settings = fx.FxSettings(store)
        rates = fx.CurrencyRatesService(settings, lambda: provider or Provider({"rates": RATES, "fetched_at": datetime(2026, 11, 1, 12, 0, tzinfo=timezone.utc)}), lambda: 0.0)
        return fx.CurrencyRatesSettingsService(FakeSession(permissions), settings, rates), settings

    def test_only_the_settings_right_may_read_save_or_test(self):
        service, _ = self.build({"APP_LOGIN"})
        self.assertFalse(service.can_manage())
        for call in (service.current, lambda: service.save(True, "30", "USD"), service.test):
            with self.assertRaises(PermissionError):
                call()

    def test_save_validates_and_current_reflects_the_choice(self):
        service, settings = self.build({"SETTINGS_MANAGE"})
        view = service.save(True, "20", "SAR,USD")
        self.assertEqual((view["minutes"], [c["code"] for c in view["currencies"] if c["checked"]]), ("20", ["USD", "SAR"]))
        with self.assertRaises(ValueError):
            service.save(True, "3", "USD")
        self.assertEqual(settings.minutes(), 20)                                                  # a refused save changes nothing

    def test_the_test_button_works_even_while_the_card_is_switched_off(self):
        service, settings = self.build({"SETTINGS_MANAGE"})
        settings.save(False, "30", ["USD"])
        answer = service.test()
        self.assertTrue(answer["available"])
        self.assertEqual([r["code"] for r in answer["rows"]], ["USD"])


if __name__ == "__main__":
    unittest.main()
