from datetime import timedelta
import json
import unittest

from config.gold_price_config import GoldTimoConfig
from tests.test_gold_price_provider import NOW, Response
from tools.diagnose_gold_prices import diagnose


class GoldPriceDiagnosticTests(unittest.TestCase):
    def response(self, request, **kwargs):
        karat = int(request.full_url.rsplit("/", 1)[1])
        return Response(json.dumps({"symbol": "XAU", "currency": "EGP", "unit": "oz",
            "karat": karat, "gramBuy": "6200", "gramSell": "6100",
            "updatedAt": "2026-09-23T14:59:59.123456+03:00"}).replace(
                '"6200"', '6200.12345').replace('"6100"', '6100.54321').encode())

    def test_missing_key_never_calls_network(self):
        def forbidden(*args, **kwargs):
            self.fail("Network must not be called without a key")
        result = diagnose(GoldTimoConfig(), opener=forbidden, clock=lambda: NOW)
        self.assertEqual(result["validation"], "NOT_FETCHED")
        self.assertIsNone(result["fetched_at"])

    def test_raw_timestamp_mapping_precision_and_no_secret(self):
        result = diagnose(GoldTimoConfig(api_key="private-test-key"),
                          opener=self.response, clock=lambda: NOW)
        self.assertEqual(result["validation"], "PASS")
        self.assertFalse(result["activation_performed"])
        self.assertEqual([p["karat"] for p in result["prices"]], [18, 21, 24])
        row = result["prices"][0]
        self.assertEqual(row["raw_provider_timestamp"], "2026-09-23T14:59:59.123456+03:00")
        self.assertEqual(row["SHOP_BUY_from_gramSell"], "6100.5432")
        self.assertEqual(row["SHOP_SELL_from_gramBuy"], "6200.1235")
        self.assertNotIn("private-test-key", json.dumps(result))

    def test_stale_result_is_not_reported_as_pass(self):
        result = diagnose(GoldTimoConfig(api_key="test"), opener=self.response,
                          clock=lambda: NOW + timedelta(minutes=6))
        self.assertEqual(result["validation"], "REJECTED_STALE")
        self.assertEqual(len(result["prices"]), 3)

    def test_transport_failure_does_not_echo_credentials(self):
        def failing(*args, **kwargs):
            raise OSError("private-test-key")
        result = diagnose(GoldTimoConfig(api_key="private-test-key"), opener=failing,
                          clock=lambda: NOW)
        self.assertEqual(result["validation"], "REJECTED")
        self.assertNotIn("private-test-key", json.dumps(result))
