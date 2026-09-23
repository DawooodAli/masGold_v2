"""Committed SQL persistence, restart reads, audit attribution and rollback."""
import json
import unittest
from unittest.mock import patch

from app.session import AppSession
from services.authentication_service import AuthenticationResult, _VERIFIED
from tests.gold_price_database_fixture import PriceDatabase


class GoldPricePersistenceTests(unittest.TestCase):
    def setUp(self):
        self.db = PriceDatabase()
        self.addCleanup(self.db.close)
        self.session = AppSession()
        self.session._establish(AuthenticationResult(17, None, "Test owner",
            frozenset({"APP_LOGIN", "SETTINGS_MANAGE"}), _VERIFIED), 0)
        self.service = self.db.service(self.session)

    def test_manual_commit_refresh_restart_and_auto_protection(self):
        preview = self.service.preview_manual("6125", "6225")
        self.assertEqual(self.db.transactions, 0)
        with patch.object(self.service, "snapshot", side_effect=AssertionError("No post-commit connection")):
            saved = self.service.activate_manual("6125", "6225", "اعتماد المالك")
        self.assertEqual(self.db.transactions, 1)
        self.assertEqual(saved["mode"], "MANUAL")
        self.assertEqual(saved["source"], "MANUAL")
        self.assertEqual([r["shop_sell_price_per_gram"] for r in saved["prices"]],
                         [r["shop_sell_price_per_gram"] for r in preview])
        with self.db.connect() as connection:
            quotes = connection.execute("SELECT * FROM gold_price_quotes").fetchall()
            activations = connection.execute("SELECT * FROM gold_price_activations").fetchall()
            pointers = connection.execute("SELECT * FROM active_gold_prices").fetchall()
            audit = connection.execute("SELECT metadata_json FROM audit_log WHERE event_type='GOLD_PRICE_ACTIVATED'").fetchone()
        self.assertEqual(len(quotes), 3)
        self.assertEqual(len(activations), 3)
        self.assertEqual(len({r["quote_group_id"] for r in quotes}), 1)
        self.assertEqual({r["entered_by_user_id"] for r in quotes}, {17})
        self.assertEqual({r["activated_by_user_id"] for r in activations}, {17})
        self.assertEqual({r["reason"] for r in activations}, {"اعتماد المالك"})
        self.assertEqual({(r["activation_id"], r["quote_id"], r["karat_id"]) for r in pointers},
                         {(r["id"], r["quote_id"], r["karat_id"]) for r in activations})
        self.assertEqual(json.loads(audit[0])["employee_id"], 29)
        # Fresh service + fresh connections, with no in-memory active-price cache.
        restarted = self.db.service(self.session)
        self.assertEqual(restarted.snapshot()["prices"], saved["prices"])
        self.assertEqual(restarted.refresh()["price_token"], saved["price_token"])
        self.assertEqual(restarted.snapshot()["mode"], "MANUAL")

    def test_second_pointer_failure_preserves_prior_set_and_all_history(self):
        old = self.service.activate_manual("6125", "6225", "السعر السابق")
        self.db.fail_second_pointer = True
        with self.assertRaises(RuntimeError):
            self.service.activate_manual("6200", "6300", "لن يعتمد")
        self.db.fail_second_pointer = False
        self.assertEqual(self.service.snapshot()["price_token"], old["price_token"])
        with self.db.connect() as connection:
            for table in ("gold_price_quotes", "gold_price_activations", "active_gold_prices"):
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM " + table).fetchone()[0], 3)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0], 2)

    def test_failed_commit_returns_no_success_and_rolls_back_mode(self):
        self.db.fail_commit = True
        with self.assertRaises(RuntimeError):
            self.service.activate_manual("6125", "6225", "اختبار")
        self.db.fail_commit = False
        self.assertEqual(self.service.snapshot()["prices"], [])
        self.assertEqual(self.service.snapshot()["mode"], "AUTO")

    def test_invalid_values_reason_and_real_permission_revocation(self):
        for buy, sell, reason in (("0", "6225", "سبب"), ("6125", "-1", "سبب"),
                                 ("6125.00004", "6125.00003", "سبب"),
                                 ("6300", "6225", "سبب"), ("6125", "6225", "")):
            with self.assertRaises(ValueError):
                self.service.activate_manual(buy, sell, reason)
        with self.db.connect() as connection:
            connection.execute("UPDATE permissions SET is_active=0 WHERE code='SETTINGS_MANAGE'")
        with self.assertRaises(PermissionError):
            self.service.activate_manual("6125", "6225", "سبب")
        self.session.logout()
        with self.assertRaises(PermissionError):
            self.service.activate_manual("6125", "6225", "سبب")
        self.assertEqual(self.service.snapshot()["prices"], [])


if __name__ == "__main__":
    unittest.main()
