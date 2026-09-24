from datetime import timedelta
import json
import unittest
from urllib.error import HTTPError, URLError

from config.gold_price_config import GoldTimoConfig
from tests.test_gold_price_provider import NOW, Response
from tools.diagnose_gold_prices import diagnose
from unittest.mock import patch
from tools.diagnose_gold_prices import compare_entered


class GoldPriceDiagnosticTests(unittest.TestCase):
    def test_gold_timo_one_second_skew_passes_without_replacing_timestamps(self):
        def response(request, **kwargs):
            payload = json.loads(self.response(request).getvalue())
            karat = int(request.full_url.rsplit("/", 1)[1])
            payload["updatedAt"] = (NOW - timedelta(seconds=1 if karat == 18 else 0)).isoformat()
            return Response(json.dumps(payload).encode())
        result = diagnose(GoldTimoConfig(api_key="test"), opener=response, clock=lambda: NOW)
        self.assertEqual(result["validation"], "PASS")
        self.assertEqual(result["timestamp_skew_seconds"], 1)
        self.assertEqual(result["provider_timestamp"], NOW.isoformat())
        self.assertNotEqual(result["prices"][0]["raw_provider_timestamp"], result["prices"][1]["raw_provider_timestamp"])

    def test_one_stale_response_rejected_even_with_acceptable_skew(self):
        def response(request, **kwargs):
            payload = json.loads(self.response(request).getvalue())
            karat = int(request.full_url.rsplit("/", 1)[1])
            payload["updatedAt"] = (NOW - timedelta(seconds=301 if karat == 21 else 299)).isoformat()
            return Response(json.dumps(payload).encode())
        result = diagnose(GoldTimoConfig(api_key="test"), opener=response, clock=lambda: NOW)
        self.assertEqual(result["timestamp_skew_seconds"], 2)
        self.assertEqual(result["validation"], "REJECTED_STALE")

    def test_mismatched_timestamps_preserve_observations_but_reject_complete_set(self):
        def response(request, **kwargs):
            payload = json.loads(self.response(request).getvalue())
            karat = int(request.full_url.rsplit("/", 1)[1])
            payload["updatedAt"] = f"2026-09-23T14:59:{karat}+03:00"
            return Response(json.dumps(payload).encode())
        result = diagnose(GoldTimoConfig(api_key="private-test-key"), opener=response, clock=lambda: NOW)
        self.assertEqual(result["validation"], "REJECTED")
        self.assertEqual(result["error_code"], "QUOTE_VALIDATION")
        self.assertEqual([p["karat"] for p in result["prices"]], [18, 21, 24])
        self.assertEqual([p["http_status"] for p in result["prices"]], [200, 200, 200])
        self.assertFalse(result["activation_performed"])
        # 18s..24s: the rejected set still reports its 6-second skew and newest time.
        self.assertEqual(result["timestamp_skew_seconds"], 6)
        self.assertEqual(result["provider_timestamp"], "2026-09-23T11:59:24+00:00")
        self.assertEqual([p["raw_provider_timestamp"] for p in result["prices"]],
                         [f"2026-09-23T14:59:{k}+03:00" for k in (18, 21, 24)])

    def test_21_preflight_is_not_a_validated_publication_and_has_real_utc(self):
        result = diagnose(GoldTimoConfig(), entered_key="private-test-key", probe_21=True,
                          opener=self.response, clock=lambda: NOW)
        self.assertEqual(result["validation"], "FETCHED_21_ONLY")
        self.assertEqual([p["karat"] for p in result["prices"]], [21])
        self.assertTrue(result["prices"][0]["provider_timestamp_utc"].endswith("+00:00"))

    def test_direct_comparison_runs_full_fetch_only_after_21_succeeds(self):
        for succeeds in (True, False):
            with self.subTest(succeeds=succeeds), \
                 patch("services.secret_store.SecretStore") as store, \
                 patch("tools.diagnose_gold_prices.diagnose") as fetch:
                store.return_value.get_secret.return_value = "stored-private"
                responses = [{"validation": "FETCHED_21_ONLY" if succeeds else "REJECTED"},
                             {"validation": "REJECTED"}]
                if succeeds:
                    responses.append({"validation": "PASS"})
                fetch.side_effect = responses
                result = compare_entered("transient-private")
                self.assertEqual(fetch.call_count, 3 if succeeds else 2)
                self.assertEqual(fetch.call_args_list[0].kwargs["entered_key"], "transient-private")
                self.assertEqual(fetch.call_args_list[1].args[0].api_key, "stored-private")
                store.return_value.set_secret.assert_not_called()
                self.assertNotIn("private", json.dumps(result))

    def test_corrupt_stored_key_does_not_prevent_complete_direct_fetch(self):
        with patch("services.secret_store.SecretStore") as store, \
             patch("tools.diagnose_gold_prices.diagnose") as fetch:
            store.return_value.get_secret.return_value = "private\r\ninvalid"
            fetch.side_effect = [{"validation": "FETCHED_21_ONLY"}, {"validation": "PASS"}]
            result = compare_entered("transient-private")
            self.assertEqual(result["stored_21"]["validation"], "INVALID_CONFIGURATION")
            self.assertEqual(result["entered_all_karats"]["validation"], "PASS")
            self.assertEqual(fetch.call_count, 2)
            self.assertNotIn("private", json.dumps(result))

    def test_failure_categories_never_include_response_credentials(self):
        cases = [(HTTPError("https://example.com/private-test-key", status,
                  "private-test-key", {}, None), code)
                 for status, code in ((401,"HTTP_AUTH"),(403,"HTTP_AUTH"),
                                      (429,"RATE_LIMIT"),(500,"HTTP_ERROR"))]
        cases += [(TimeoutError("private-test-key"), "TIMEOUT"),
                  (URLError("private-test-key"), "NETWORK")]
        for failure, expected in cases:
            def failing(*args, **kwargs): raise failure
            with self.subTest(expected=expected):
                result = diagnose(GoldTimoConfig(api_key="private-test-key"), opener=failing, clock=lambda: NOW)
                self.assertEqual(result["error_code"], expected)
                self.assertNotIn("private-test-key", json.dumps(result))

    def test_validation_categories(self):
        for change, code in (({"currency":"USD"},"CURRENCY_MISMATCH"),
                             ({"karat":22},"KARAT_MISMATCH"),
                             ({"unit":"gram"},"SCHEMA_UNIT"),
                             ({"symbol":"XAG"},"SCHEMA_UNIT")):
            def response(request, **kwargs):
                payload=json.loads(self.response(request).getvalue())
                payload.update(change)
                return Response(json.dumps(payload).encode())
            with self.subTest(code=code):
                result=diagnose(GoldTimoConfig(api_key="test"),opener=response,clock=lambda: NOW)
                self.assertEqual(result["error_code"],code)
        result=diagnose(GoldTimoConfig(api_key="test"),opener=lambda *a,**k: Response(b'not JSON private-test-key'),clock=lambda: NOW)
        self.assertEqual(result["error_code"],"INVALID_JSON")
        self.assertNotIn("private-test-key",json.dumps(result))

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
        self.assertEqual(result["error_code"], "KEY_MISSING")

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
