import json
import os
import unittest
from unittest.mock import Mock, patch

from app.session import AppSession
from config.gold_price_config import GoldTimoConfig
from services.authentication_service import AuthenticationResult, _VERIFIED
from services.gold_price_settings_service import GoldPriceSettingsService
from services.secret_store import SecretStore, SecretStoreError
from tests.gold_price_database_fixture import PriceDatabase
from app.viewmodels.gold_price_settings_viewmodel import SettingsWorker
from services.gold_price_errors import ConnectionTestError


class SecretStoreTests(unittest.TestCase):
    def test_store_retrieve_delete_uses_keyring_identity(self):
        backend = Mock()
        backend.get_password.return_value = "private"
        store = SecretStore(backend)
        store.set_secret("goldtimo_api_key", "private")
        self.assertEqual(store.get_secret("goldtimo_api_key"), "private")
        store.delete_secret("goldtimo_api_key")
        backend.set_password.assert_called_once_with("masGold", "goldtimo_api_key", "private")
        backend.delete_password.assert_called_once_with("masGold", "goldtimo_api_key")

    def test_errors_do_not_expose_backend_message(self):
        backend = Mock()
        backend.set_password.side_effect = RuntimeError("private")
        with self.assertRaises(SecretStoreError) as caught:
            SecretStore(backend).set_secret("goldtimo_api_key", "private")
        self.assertNotIn("private", str(caught.exception))

    def test_stored_precedes_environment_and_restart_resolves_again(self):
        store = Mock()
        with patch.dict(os.environ, {"MASGOLD_GOLDTIMO_API_KEY": "developer"}), patch(
                "config.gold_price_config.load_dotenv"):
            store.get_secret.return_value = "stored"
            self.assertEqual(GoldTimoConfig.from_env(store).api_key, "stored")
            self.assertEqual(GoldTimoConfig.from_env(store).api_key, "stored")
            store.get_secret.return_value = None
            self.assertEqual(GoldTimoConfig.from_env(store).api_key, "developer")


class GoldPriceSettingsTests(unittest.TestCase):
    def test_save_normalizes_and_compares_readback_lengths_without_secret(self):
        with self.assertLogs("services.gold_price_settings_service", level="INFO") as logs:
            self.save("  private-test-secret  ")
        self.secrets.set_secret.assert_called_once_with("goldtimo_api_key", "private-test-secret")
        self.assertIn("character_count=19", str(logs.output))
        self.assertIn("configured=True", str(logs.output))
        self.assertNotIn("equals", str(logs.output))
        self.assertNotIn("private-test-secret", str(logs.output))

    def test_save_detects_corrupt_keyring_readback(self):
        self.secrets.set_secret.side_effect = None
        self.secrets.get_secret.return_value = "different"
        with self.assertRaises(SecretStoreError): self.save()

    def test_rejection_code_survives_service_and_worker_without_raw_error(self):
        self.diagnostic.return_value = {"validation": "REJECTED", "error_code": "HTTP_AUTH",
                                        "http_status":403, "error":"private-test-secret"}
        with self.assertRaises(ConnectionTestError) as caught:
            self.service.test(self.actor,"https://goldtimo.com/api/v1","10","test")
        self.assertEqual(caught.exception.code,"HTTP_AUTH")
        fake = Mock(); fake.test.side_effect = caught.exception
        worker = SettingsWorker(fake,"test",self.actor,(),None)
        results=[]; worker.completed.connect(lambda result,error: results.append(error))
        with self.assertLogs("app.viewmodels.gold_price_settings_viewmodel") as logs: worker.run()
        self.assertIn("code=HTTP_AUTH",str(logs.output))
        self.assertIn("http_status=403",str(logs.output))
        self.assertNotIn("private-test-secret",str(logs.output)+repr(results))
        self.assertIn("401/403",results[0])

    def test_credential_store_failure_has_distinct_code(self):
        self.secrets.get_secret.side_effect = SecretStoreError("private-test-secret")
        with self.assertRaises(ConnectionTestError) as caught:
            self.service.test(self.actor,"https://goldtimo.com/api/v1","10","")
        self.assertEqual(caught.exception.code,"CREDENTIAL_STORE")
        self.assertNotIn("private-test-secret",str(caught.exception))

    def setUp(self):
        self.db = PriceDatabase()
        self.addCleanup(self.db.close)
        with self.db.connect() as connection:
            connection.execute("ALTER TABLE gold_price_sources ADD COLUMN notes TEXT")
        self.session = AppSession()
        self.session._establish(AuthenticationResult(17, None, "Owner",
            frozenset({"APP_LOGIN", "SETTINGS_MANAGE"}), _VERIFIED), 0)
        self.actor = self.session.current_actor()
        self.secrets = Mock()
        self.secrets.get_secret.return_value = None
        self.secrets.set_secret.side_effect = lambda name, value: setattr(self.secrets.get_secret, "return_value", value)
        self.diagnostic = Mock(return_value={"validation": "PASS", "fetched_at": "test", "prices": []})
        self.service = GoldPriceSettingsService(self.session, secrets=self.secrets,
            transaction=self.db.transaction, diagnostic=self.diagnostic)
        self.prices = self.db.service(self.session)
        self.prices.activate_manual("6190", "6240", "baseline")

    def save(self, key="private-test-secret"):
        return self.service.save(self.actor, "https://goldtimo.com/api/v1", "10", "120", "300", key)

    def test_save_has_no_secret_in_db_and_preserves_mode_history_and_prices(self):
        before = self.prices.snapshot()
        self.save()
        self.assertEqual(self.prices.snapshot()["prices"], before["prices"])
        self.assertEqual(self.prices.snapshot()["mode"], "MANUAL")
        with self.db.connect() as c:
            dump = '\n'.join(c.iterdump())
            self.assertNotIn("private-test-secret", dump)
            self.assertEqual(c.execute("SELECT COUNT(*) FROM gold_price_quotes").fetchone()[0], 3)
            self.assertEqual(c.execute("SELECT COUNT(*) FROM gold_price_activations").fetchone()[0], 3)
            row = c.execute("SELECT notes FROM gold_price_sources WHERE code='GOLDTIMO'").fetchone()
            self.assertEqual(json.loads(row[0])["connection"]["timeout_seconds"], 10)
        self.secrets.set_secret.assert_called_once_with("goldtimo_api_key", "private-test-secret")

    def test_revoked_permission_and_logged_out_actor_cannot_modify_secrets_or_db(self):
        with self.db.connect() as c:
            c.execute("UPDATE permissions SET is_active=0 WHERE code='SETTINGS_MANAGE'")
        for operation in (self.save, lambda: self.service.delete(self.actor)):
            with self.assertRaises(PermissionError): operation()
        self.secrets.set_secret.assert_not_called()
        self.secrets.delete_secret.assert_not_called()
        self.session.logout()
        with self.assertRaises(PermissionError): self.save()

    def test_fetch_only_uses_unsaved_key_then_stored_key_without_writes(self):
        with self.db.connect() as c: before = '\n'.join(c.iterdump())
        self.service.test(self.actor, "https://goldtimo.com/api/v1", "10", "unsaved")
        self.assertEqual(self.diagnostic.call_args.args[0].api_key, "unsaved")
        self.secrets.get_secret.return_value = "stored"
        self.service.test(self.actor, "https://goldtimo.com/api/v1", "10", "")
        self.assertEqual(self.diagnostic.call_args.args[0].api_key, "stored")
        with self.db.connect() as c: self.assertEqual('\n'.join(c.iterdump()), before)
        self.secrets.set_secret.assert_not_called()

    def test_entered_key_wins_over_different_stored_and_environment_keys(self):
        self.secrets.get_secret.return_value = "old-stored"
        with patch.dict(os.environ, {"MASGOLD_GOLDTIMO_API_KEY": "environment"}), \
             patch("config.gold_price_config.load_dotenv"):
            self.service.test(self.actor, "https://goldtimo.com/api/v1", "10", "  MiXeD_+=/  ")
        config = self.diagnostic.call_args.args[0]
        self.assertEqual(config.api_key, "MiXeD_+=/")
        self.assertEqual(config.credential_trace["credential_source"], "ENTERED")
        self.assertEqual(config.credential_trace["character_count"], len("MiXeD_+=/"))
        self.secrets.set_secret.assert_not_called()

    def test_entered_key_still_works_when_keyring_comparison_fails(self):
        self.secrets.get_secret.side_effect = SecretStoreError("private-test-secret")
        self.service.test(self.actor, "https://goldtimo.com/api/v1", "10", "direct")
        config = self.diagnostic.call_args.args[0]
        self.assertEqual(config.api_key, "direct")
        self.assertEqual(config.credential_trace["credential_source"], "ENTERED")

    def test_save_replaces_key_and_subsequent_and_restarted_test_use_keyring(self):
        self.secrets.get_secret.return_value = "old-stored"
        with patch.dict(os.environ, {"MASGOLD_GOLDTIMO_API_KEY": "environment"}), \
             patch("config.gold_price_config.load_dotenv"):
            self.save("  new-working-key  ")
            data = self.service.load(self.actor)
            self.assertTrue(data["stored"])
            self.assertEqual(data["timeout"], "10")
            self.assertNotIn("new-working-key", repr(data))
            self.service.test(self.actor, data["base_url"], data["timeout"], "")
            self.assertEqual(self.diagnostic.call_args.args[0].api_key, "new-working-key")
            fresh = GoldPriceSettingsService(self.session, secrets=self.secrets,
                transaction=self.db.transaction, diagnostic=self.diagnostic)
            result = fresh.test(self.actor, data["base_url"], data["timeout"], "")
            self.assertEqual(result["credential_source"], "KEYRING")
            self.assertEqual(self.diagnostic.call_args.args[0].api_key, "new-working-key")
            self.assertNotIn("new-working-key", repr(result))

    def test_blank_field_falls_back_to_environment_only_when_store_is_empty(self):
        with patch.dict(os.environ, {"MASGOLD_GOLDTIMO_API_KEY": "  environment  "}), \
             patch("config.gold_price_config.load_dotenv"):
            self.service.test(self.actor, "https://goldtimo.com/api/v1", "10", "")
            config = self.diagnostic.call_args.args[0]
            self.assertEqual(config.api_key, "environment")
            self.assertEqual(config.credential_trace["credential_source"], "ENV")
            self.secrets.get_secret.return_value = "stored"
            self.service.test(self.actor, "https://goldtimo.com/api/v1", "10", "")
            config = self.diagnostic.call_args.args[0]
            self.assertEqual(config.api_key, "stored")
            self.assertEqual(config.credential_trace["credential_source"], "KEYRING")

    def test_fractional_timeout_keeps_its_fraction(self):
        self.service.save(self.actor, "https://goldtimo.com/api/v1", "2.5", "120", "300", "")
        self.assertEqual(self.service.load(self.actor)["timeout"], "2.5")

    def test_whitespace_only_draft_falls_back_to_stored_key(self):
        self.secrets.get_secret.return_value = "stored"
        self.service.test(self.actor, "https://goldtimo.com/api/v1", "10", "   ")
        config = self.diagnostic.call_args.args[0]
        self.assertEqual(config.api_key, "stored")
        self.assertEqual(config.credential_trace["credential_source"], "KEYRING")

    def test_rejected_complete_set_returns_observations_without_writes(self):
        rows = [{"karat": karat, "SHOP_BUY_from_gramSell": "6207.0000", "SHOP_SELL_from_gramBuy": "6232.0000",
                 "raw_provider_timestamp": "x", "http_status": 200} for karat in (18, 21, 24)]
        self.diagnostic.return_value = {"validation": "REJECTED", "error_code": "QUOTE_VALIDATION",
            "error": "private-test-secret", "fetched_at": None, "prices": rows,
            "timestamp_skew_seconds": 9.0, "provider_timestamp": "2026-09-24T13:15:09+00:00"}
        with self.db.connect() as c: before = '\n'.join(c.iterdump())
        result = self.service.test(self.actor, "https://goldtimo.com/api/v1", "10", "entered")
        self.assertEqual(result["coherent_set"], "FAIL")
        self.assertEqual(result["error_code"], "QUOTE_VALIDATION")
        self.assertEqual(result["timestamp_skew_seconds"], 9.0)
        self.assertEqual(result["credential_source"], "ENTERED")
        self.assertNotIn("private-test-secret", repr(result))
        self.assertNotIn("entered", repr(result))
        with self.db.connect() as c: self.assertEqual('\n'.join(c.iterdump()), before)
        # A partial fetch (a later karat failed) keeps its real error instead.
        self.diagnostic.return_value = {"validation": "REJECTED", "error_code": "HTTP_AUTH",
            "http_status": 401, "fetched_at": None, "prices": rows[:1]}
        with self.assertRaises(ConnectionTestError) as caught:
            self.service.test(self.actor, "https://goldtimo.com/api/v1", "10", "entered")
        self.assertEqual(caught.exception.http_status, 401)

    def test_delete_keeps_manual_functional_and_never_exposes_stored_key(self):
        self.secrets.get_secret.return_value = "stored-secret"
        self.assertNotIn("stored-secret", repr(self.service.load(self.actor)))
        self.service.delete(self.actor)
        result = self.prices.activate_manual("6100", "6200", "after delete")
        self.assertEqual(result["mode"], "MANUAL")
        self.secrets.delete_secret.assert_called_once_with("goldtimo_api_key")

    def test_db_failure_does_not_write_credential(self):
        self.db.fail_commit = True
        with self.assertRaises(RuntimeError): self.save()
        self.secrets.set_secret.assert_not_called()

    def test_credential_failure_explicitly_reports_partial_save(self):
        self.secrets.set_secret.side_effect = RuntimeError("private-test-secret")
        with self.assertRaises(SecretStoreError) as caught: self.save()
        self.assertNotIn("private-test-secret", str(caught.exception))
        self.assertEqual(self.prices.snapshot()["mode"], "MANUAL")

    def test_arbitrary_host_rejected_before_credential_access(self):
        with self.assertRaises(ValueError):
            self.service.test(self.actor, "https://example.com/api/v1", "10", "")
        self.secrets.get_secret.assert_not_called()

    def test_worker_logs_no_credentials_and_releases_args_on_failure(self):
        service = Mock()
        service.test.side_effect = RuntimeError("private-test-secret")
        worker = SettingsWorker(service, "test", self.actor, ("private-test-secret",), None)
        with self.assertLogs("app.viewmodels.gold_price_settings_viewmodel") as logs:
            worker.run()
        self.assertNotIn("private-test-secret", str(logs.output))
        self.assertEqual(worker.args, ())
