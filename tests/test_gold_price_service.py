"""Price-engine business behavior with isolated transactional state; no live writes."""
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from threading import Event, RLock, Thread
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

from app.session import AppSession
from services.authentication_service import AuthenticationResult, _VERIFIED
from services.authorization_service import AuthorizationService
from services.gold_price_provider import NormalizedQuote, ProviderError
from services.gold_price_service import GoldPriceService, PriceChanged, PriceUnavailable


NOW = datetime(2026, 9, 23, 9, 0, tzinfo=timezone.utc)


class Store:
    def __init__(self):
        self.lock = RLock()
        self.settings = dict(id=1, mode="AUTO", primary_source_id=10, fallback_source_id=11,
                             reference_source_id=None, refresh_seconds=60, stale_after_seconds=300,
                             max_deviation_percent=Decimal("5"), updated_at=NOW)
        self.sources = {code: dict(id=source_id, code=code, name=code, is_enabled=True,
                                  source_type=kind)
                        for code, source_id, kind in (("MANUAL", 9, "MANUAL"),
                            ("PRIMARY", 10, "LOCAL_MARKET"), ("FALLBACK", 11, "LOCAL_MARKET"),
                            ("REFERENCE", 12, "GLOBAL_REFERENCE"))}
        self.karats = {18: 2, 21: 1, 24: 3}
        self.active, self.history, self.audit = [], [], []
        self.publications = 0
        self.fail_publish = False
        self.commits, self.rollbacks, self.transactions = 0, 0, 0
        self.lock_requests = []

    @contextmanager
    def transaction(self):
        with self.lock:
            previous = deepcopy((self.settings, self.active, self.history, self.audit, self.publications))
            self.transactions += 1
            try:
                yield SimpleNamespace(in_transaction=True)
                self.commits += 1
            except BaseException:
                self.settings, self.active, self.history, self.audit, self.publications = previous
                self.rollbacks += 1
                raise
            finally:
                self.transactions -= 1


class MemoryRepository:
    def __init__(self, store):
        self.store = store

    def get_settings(self, lock=False):
        self.store.lock_requests.append(("settings", lock))
        return deepcopy(self.store.settings)

    def get_sources(self):
        return deepcopy(self.store.sources)

    def get_karats(self):
        return dict(self.store.karats)

    def get_active(self, lock=False):
        self.store.lock_requests.append(("active", lock))
        return deepcopy(self.store.active)

    def latest_source_quotes(self, source_id):
        groups = [rows for rows in self.store.history if rows[0]["source_id"] == source_id]
        return deepcopy(groups[-1]) if groups else []

    def append_publication(self, quotes, *, karat_ids, source_id, activation_type,
                           actor, reason, activated_at, derivation=None):
        self.store.publications += 1
        group = str(self.store.publications)
        rows = []
        for index, quote in enumerate(quotes):
            rows.append(dict(karat_id=karat_ids[quote.karat], karat_value=quote.karat,
                quote_id=self.store.publications * 10 + index,
                activation_id=self.store.publications * 100 + index,
                quote_group_id=group, source_id=source_id, source_code=quote.source_code,
                source_type="MANUAL" if quote.source_code == "MANUAL" else "LOCAL_MARKET",
                shop_buy_price_per_gram=quote.shop_buy_price_per_gram,
                shop_sell_price_per_gram=quote.shop_sell_price_per_gram,
                provider_timestamp=quote.provider_timestamp, fetched_at=quote.fetched_at,
                activated_at=activated_at.replace(microsecond=0), activation_type=activation_type,
                activated_by_user_id=actor.user_id if actor else None,
                validation_status="ACCEPTED", quote_method="MANUAL" if actor else "DIRECT",
                reason=reason, notes=str(derivation)))
            self.store.active = deepcopy(rows)
            if self.store.fail_publish and index == 1:
                raise RuntimeError("Simulated second-karat persistence failure")
        self.store.history.append(deepcopy(rows))
        self.store.audit.append(dict(actor=actor, reason=reason, derivation=derivation, group=group))
        return group

    def set_mode(self, mode, actor, reason, occurred_at):
        self.store.settings.update(mode=mode, updated_at=occurred_at, updated_by_user_id=actor.user_id)
        self.store.audit.append(dict(mode=mode, actor=actor, reason=reason))

    def append_rejection(self, source_code, message, occurred_at):
        self.store.audit.append(dict(rejection=source_code, message=message))


class Provider:
    def __init__(self, code, store):
        self.source_code, self.store = code, store
        self.timestamp, self.buy, self.sell = NOW, Decimal("6000"), Decimal("6100")
        self.calls, self.error, self.before_fetch = 0, None, None

    def fetch(self):
        if self.store.transactions:
            raise AssertionError("Provider called during a database transaction")
        self.calls += 1
        if self.before_fetch:
            self.before_fetch()
        if self.error:
            raise self.error
        return tuple(NormalizedQuote(self.source_code, k,
                        (self.buy * k / 21).quantize(Decimal("0.0001")),
                        (self.sell * k / 21).quantize(Decimal("0.0001")),
                        "EGP", "GRAM", self.timestamp, self.timestamp)
                     for k in (18, 21, 24))


class GoldPriceServiceTests(unittest.TestCase):
    def setUp(self):
        self.store = Store()
        self.session = AppSession()
        self.login()
        self.now = NOW
        self.identity = dict(id=44, employee_id=55, display_name="مدير", is_active=True,
                             must_change_password=False, employee_active=True)
        self.auth_repo = MagicMock()
        self.auth_repo.get_identity.side_effect = lambda *a, **kw: dict(self.identity)
        self.auth_repo.effective_permissions.return_value = frozenset({"APP_LOGIN", "SETTINGS_MANAGE"})
        self.authorization = AuthorizationService(self.session, lambda connection: self.auth_repo)
        self.primary, self.fallback = Provider("PRIMARY", self.store), Provider("FALLBACK", self.store)
        self.reference = Provider("REFERENCE", self.store)
        self.providers = {p.source_code: p for p in (self.primary, self.fallback, self.reference)}
        self.service = self.new_service()

    def login(self, user=44, permissions=frozenset({"APP_LOGIN", "SETTINGS_MANAGE"})):
        self.session._establish(AuthenticationResult(user, None, "مستخدم", permissions, _VERIFIED),
                                int(self.session.generation))

    def new_service(self):
        return GoldPriceService(self.session, transaction_factory=self.store.transaction,
            repository_factory=lambda connection: MemoryRepository(self.store),
            providers=self.providers, authorization=self.authorization, clock=lambda: self.now)

    def test_valid_automatic_response_publishes_one_complete_set(self):
        result = self.service.refresh()
        self.assertEqual(result["status"], "FRESH")
        self.assertEqual(result["source"], "PRIMARY")
        self.assertEqual(len(self.store.history), 1)
        self.assertEqual(len({row["quote_group_id"] for row in self.store.active}), 1)
        self.assertEqual({row["karat_value"] for row in self.store.active}, {18, 21, 24})
        self.assertTrue(all(isinstance(row["shop_buy_price_per_gram"], str) for row in result["prices"]))
        self.assertEqual(self.auth_repo.get_identity.call_count, 0)

    def test_wrong_currency_rejected_and_fallback_selected(self):
        quote = NormalizedQuote("PRIMARY", 18, Decimal("5000"), Decimal("5100"), "USD", "GRAM", NOW, NOW)
        self.primary.fetch = lambda: tuple(replace(quote, karat=k) for k in (18, 21, 24))
        result = self.service.refresh()
        self.assertEqual(result["source"], "FALLBACK")
        self.assertEqual(self.store.active[0]["activation_type"], "FALLBACK")
        self.assertTrue(any(event.get("rejection") == "PRIMARY" for event in self.store.audit))

    def test_invalid_provider_configuration_does_not_break_application_startup(self):
        with patch('services.gold_price_service.configured_providers', side_effect=ValueError('bad timeout')):
            service = GoldPriceService(self.session, transaction_factory=self.store.transaction,
                repository_factory=lambda connection: MemoryRepository(self.store),
                authorization=self.authorization, clock=lambda: self.now)
        self.assertTrue(service.snapshot()["warnings"])
        self.assertEqual(service.activate_manual("6000", "6100", "يدوي")["mode"], "MANUAL")

    def test_timeout_fallback_and_provider_failure_preserve_last_good(self):
        self.primary.error = TimeoutError()
        result = self.service.refresh()
        self.assertEqual(result["source"], "FALLBACK")
        old = deepcopy(self.store.active)
        self.fallback.error = ProviderError("failed")
        result = self.service.refresh()
        self.assertEqual(result["status"], "ERROR")
        self.assertEqual(len(result["prices"]), 3)
        self.assertEqual(self.store.active, old)

    def test_another_workstations_publication_clears_obsolete_local_failure_status(self):
        self.service.refresh()
        self.primary.error = self.fallback.error = ProviderError("failed")
        self.assertEqual(self.service.refresh()["status"], "ERROR")
        self.new_service().activate_manual("6000", "6100", "سعر معتمد من محطة أخرى")
        self.assertEqual(self.service.snapshot()["status"], "FRESH")
        self.primary.error = self.fallback.error = None
        self.new_service().return_to_auto()
        self.assertEqual(self.service.snapshot()["status"], "FRESH")

    def test_no_provider_config_has_unavailable_prices(self):
        self.store.settings.update(mode="MANUAL", primary_source_id=None, fallback_source_id=None)
        result = self.service.snapshot()
        self.assertEqual(result["status"], "UNAVAILABLE")
        self.assertEqual(result["prices"], [])
        with self.assertRaises(PriceUnavailable):
            self.service.return_to_auto()
        self.assertEqual(self.store.settings["mode"], "MANUAL")

    def test_repeated_timestamp_does_not_refresh_age_or_identity(self):
        first = self.service.refresh()
        old = deepcopy(self.store.active)
        self.now += timedelta(seconds=120)
        second = self.service.refresh()
        self.assertEqual(first["price_token"], second["price_token"])
        self.assertEqual(second["age_seconds"], 120)
        self.assertEqual(self.store.active, old)
        self.assertEqual(self.store.publications, 1)
        self.now += timedelta(seconds=200)
        self.assertEqual(self.service.snapshot()["status"], "STALE_ALLOWED")
        third = self.service.refresh()
        self.assertEqual(third["status"], "ERROR")
        self.assertEqual(third["age_seconds"], 320)
        self.assertEqual(third["price_token"], first["price_token"])
        self.assertEqual(self.store.active, old)

    def test_same_timestamp_changed_prices_and_backwards_timestamp_rejected(self):
        initial = self.service.refresh()
        self.store.settings["fallback_source_id"] = None
        self.primary.buy += Decimal("1")
        self.assertEqual(self.service.refresh()["status"], "ERROR")
        self.primary.timestamp -= timedelta(seconds=1)
        self.assertEqual(self.service.refresh()["status"], "ERROR")
        self.assertEqual(self.service.snapshot()["price_token"], initial["price_token"])
        self.assertEqual(self.store.publications, 1)

    def test_new_timestamp_changes_price_token(self):
        first = self.service.refresh()
        self.primary.timestamp += timedelta(seconds=1)
        self.now += timedelta(seconds=1)
        second = self.service.refresh()
        self.assertNotEqual(first["price_token"], second["price_token"])

    def test_invalid_primary_history_uses_valid_fallback(self):
        self.service.refresh()
        self.primary.buy += Decimal("1")
        result = self.service.refresh()
        self.assertEqual(result["source"], "FALLBACK")
        rejected = [event for event in self.store.audit if event.get("rejection") == "PRIMARY"]
        self.assertTrue(rejected)
        self.assertIn("توقيت", rejected[-1]["message"])

    def test_atomic_failure_rolls_back_all_karats_and_history(self):
        self.service.refresh()
        active, history = deepcopy(self.store.active), deepcopy(self.store.history)
        self.now += timedelta(seconds=1)
        self.primary.timestamp = self.now
        self.store.fail_publish = True
        result = self.service.refresh()
        self.assertEqual(result["status"], "ERROR")
        self.assertEqual(self.store.active, active)
        self.assertEqual(self.store.history, history)
        self.assertGreater(self.store.rollbacks, 0)

    def test_decimal_manual_derivation_preview_matches_activation(self):
        preview = self.service.preview_manual("6125", "6225")
        self.assertEqual(preview[0]["shop_buy_price_per_gram"], "5250.0000")
        self.assertEqual(preview[0]["shop_sell_price_per_gram"], "5335.7143")
        self.assertEqual(preview[2]["shop_sell_price_per_gram"], "7114.2857")
        result = self.service.activate_manual("6125", "6225", "اعتماد سعر المحل")
        self.assertEqual(result["mode"], "MANUAL")
        self.assertEqual(result["source"], "MANUAL")
        self.assertEqual(result["prices"][0]["shop_sell_price_per_gram"], preview[0]["shop_sell_price_per_gram"])
        audit = self.store.audit[0]
        self.assertEqual(audit["actor"].user_id, 44)
        self.assertEqual(audit["actor"].employee_id, 55)  # Fresh DB relationship, not cached session.
        self.assertEqual(audit["derivation"]["rounding"], "ROUND_HALF_UP")

    def test_manual_prices_require_valid_decimal_and_reason(self):
        for value in (0.1, "0", "-1", "NaN", "Infinity", "10000000000", "not a number"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.service.preview_manual(value, "6225")
        with self.assertRaises(ValueError):
            self.service.preview_manual("6226", "6225")
        for reason in ("", "  ", "x" * 501):
            with self.assertRaises(ValueError):
                self.service.activate_manual("6000", "6100", reason)
        self.assertEqual(self.store.publications, 0)

    def test_manual_mutations_revalidate_permission_and_active_identity(self):
        for update in (dict(is_active=False), dict(must_change_password=True), dict(employee_active=False)):
            original = dict(self.identity)
            self.identity.update(update)
            with self.assertRaises(PermissionError):
                self.service.activate_manual("6000", "6100", "اختبار")
            self.identity = original
        self.auth_repo.effective_permissions.return_value = frozenset({"APP_LOGIN"})
        with self.assertRaises(PermissionError):
            self.service.activate_manual("6000", "6100", "اختبار")
        with self.assertRaises(PermissionError):
            self.service.return_to_auto()
        self.assertEqual(self.store.publications, 0)
        self.assertEqual(self.primary.calls, 0)

    def test_viewing_prices_needs_no_management_permission(self):
        self.service.refresh()
        self.session.logout()
        self.login(permissions=frozenset({"APP_LOGIN"}))
        self.auth_repo.effective_permissions.return_value = frozenset({"APP_LOGIN"})
        self.assertEqual(len(self.service.snapshot()["prices"]), 3)
        self.assertEqual(len(self.service.get_billing_price_set([1, 2]).prices), 2)

    def test_owner_without_employee_is_supported(self):
        self.identity.update(employee_id=None)
        self.service.activate_manual("6000", "6100", "اختبار")
        self.assertIsNone(self.store.audit[0]["actor"].employee_id)

    def test_qml_cannot_supply_arbitrary_actor_or_user(self):
        with self.assertRaises(TypeError):
            self.service.activate_manual("6000", "6100", "سبب", user_id=999)
        with self.assertRaises(TypeError):
            self.service.return_to_auto(actor_id=999)

    def test_manual_mode_cannot_be_overwritten_by_auto(self):
        self.service.activate_manual("6000", "6100", "اختبار")
        old = deepcopy(self.store.active)
        result = self.service.refresh()
        self.assertEqual(result["mode"], "MANUAL")
        self.assertEqual(self.primary.calls, 0)
        self.assertEqual(self.store.active, old)

    def test_manual_wins_against_inflight_auto_fetch(self):
        self.primary.before_fetch = lambda: self.new_service().activate_manual("6000", "6100", "يدوي")
        result = self.service.refresh()
        self.assertEqual(result["mode"], "MANUAL")
        self.assertEqual(result["source"], "MANUAL")
        self.assertEqual(self.store.publications, 1)

    def test_explicit_return_to_auto_is_authorized_and_audited(self):
        manual = self.service.activate_manual("6000", "6100", "يدوي")
        automatic = self.service.return_to_auto("عودة للتلقائي")
        self.assertEqual(automatic["mode"], "AUTO")
        self.assertEqual(automatic["source"], "PRIMARY")
        self.assertNotEqual(manual["price_token"], automatic["price_token"])
        self.assertEqual(self.auth_repo.get_identity.call_count, 3)
        self.assertEqual(self.store.active[0]["activated_by_user_id"], 44)

    def test_return_auto_failure_keeps_manual_mode_and_prices(self):
        self.service.activate_manual("6000", "6100", "يدوي")
        old = deepcopy(self.store.active)
        self.primary.error = self.fallback.error = ProviderError("failure")
        with self.assertRaises(PriceUnavailable):
            self.service.return_to_auto()
        self.assertEqual(self.store.settings["mode"], "MANUAL")
        self.assertEqual(self.store.active, old)

    def test_logout_during_fetch_cannot_publish(self):
        self.primary.before_fetch = self.session.logout
        with self.assertRaises(PermissionError):
            self.service.refresh()
        self.assertEqual(self.store.publications, 0)

    def test_logout_and_new_login_cannot_reassign_inflight_actor(self):
        def change_session():
            self.session.logout()
            self.login(user=77)
        self.primary.before_fetch = change_session
        with self.assertRaises(PermissionError):
            self.service.return_to_auto()
        self.assertEqual(self.store.publications, 0)

    def test_worker_bound_actor_rejected_after_another_login(self):
        old_actor = self.session.require_authenticated()
        self.session.logout()
        self.login(user=77)
        for operation, args in ((self.service.activate_manual, ("6000", "6100", "يدوي")),
                                (self.service.return_to_auto, ()), (self.service.refresh, ())):
            with self.assertRaises(PermissionError):
                operation(*args, _expected_actor=old_actor)
        self.assertEqual(self.store.publications, 0)

    def test_settings_and_source_changes_during_fetch_prevent_publication(self):
        self.primary.before_fetch = lambda: self.store.settings.update(max_deviation_percent=Decimal("6"))
        self.service.refresh()
        self.assertEqual(self.store.publications, 0)
        self.primary.before_fetch = lambda: self.store.sources["PRIMARY"].update(is_enabled=False)
        self.service.refresh()
        self.assertEqual(self.store.publications, 0)

    def test_maximum_deviation_rejects_primary_and_uses_fallback(self):
        self.service.refresh()
        self.now += timedelta(seconds=1)
        self.primary.timestamp = self.fallback.timestamp = self.now
        self.primary.buy, self.primary.sell = Decimal("9000"), Decimal("9100")
        result = self.service.refresh()
        self.assertEqual(result["source"], "FALLBACK")

    def test_reference_source_and_deviation_settings_are_enforced(self):
        self.store.settings["reference_source_id"] = 12
        self.reference.buy, self.reference.sell = Decimal("9000"), Decimal("9100")
        result = self.service.refresh()
        self.assertEqual(result["status"], "ERROR")
        self.assertEqual(self.store.publications, 0)
        self.assertEqual(self.reference.calls, 1)
        self.reference.error = ProviderError("reference unavailable")
        self.assertEqual(self.service.refresh()["status"], "ERROR")

    def test_overlapping_refresh_runs_fetch_only_once(self):
        started, release = Event(), Event()
        def block():
            started.set()
            self.assertTrue(release.wait(2))
        self.primary.before_fetch = block
        errors = []
        def run():
            try:
                self.service.refresh()
            except BaseException as exc:
                errors.append(exc)
        worker = Thread(target=run)
        worker.start()
        self.assertTrue(started.wait(1))
        try:
            self.service.refresh()
            self.assertEqual(self.primary.calls, 1)
        finally:
            release.set()
            worker.join(3)
        self.assertFalse(worker.is_alive())
        self.assertEqual(errors, [])

    def test_billing_exact_decimals_and_stable_token(self):
        self.service.refresh()
        prices = self.service.get_billing_price_set([1, 2])
        self.assertEqual(prices.prices[1].shop_buy_price_per_gram, Decimal("6000.0000"))
        self.assertIsInstance(prices.prices[2].shop_buy_price_per_gram, Decimal)
        self.assertFalse(prices.requires_acknowledgement)
        self.now += timedelta(seconds=20)
        self.assertEqual(prices.price_token, self.service.get_billing_price_set([3]).price_token)
        with self.assertRaises(TypeError):
            prices.prices[1] = None
        for ids in ([999], [1.0], [], [True]):
            with self.assertRaises(ValueError):
                self.service.get_billing_price_set(ids)

    def test_finalization_locks_callers_transaction_and_acknowledges_staleness(self):
        self.service.refresh()
        price = self.service.get_billing_price_set([1])
        self.now += timedelta(seconds=301)
        self.store.lock_requests.clear()
        calls, commits = self.primary.calls, self.store.commits
        connection = SimpleNamespace(in_transaction=True)
        with self.assertRaises(PriceUnavailable):
            self.service.lock_and_validate_for_finalization(connection, price.price_token, [1])
        result = self.service.lock_and_validate_for_finalization(
            connection, price.price_token, [1], acknowledged=True)
        self.assertEqual(result.status, "STALE_ALLOWED")
        self.assertEqual(self.store.commits, commits)
        self.assertEqual(self.primary.calls, calls)
        self.assertEqual(self.store.lock_requests, [("settings", True), ("active", True)] * 2)
        with self.assertRaises(RuntimeError):
            self.service.lock_and_validate_for_finalization(
                SimpleNamespace(in_transaction=False), price.price_token, [1])

    def test_finalization_rejects_changed_token_even_when_acknowledged(self):
        self.service.refresh()
        price = self.service.get_billing_price_set([1])
        self.service.activate_manual("6000", "6100", "يدوي")
        with self.assertRaises(PriceChanged):
            self.service.lock_and_validate_for_finalization(
                SimpleNamespace(in_transaction=True), price.price_token, [1], acknowledged=True)

    def test_mixed_or_incomplete_active_rows_are_unavailable(self):
        self.service.refresh()
        original = deepcopy(self.store.active)
        self.store.active.pop()
        self.assertEqual(self.service.snapshot()["status"], "UNAVAILABLE")
        self.store.active = original
        self.store.active[1]["quote_group_id"] = "mixed"
        with self.assertRaises(PriceUnavailable):
            self.service.get_billing_price_set([1])

    def test_rejected_or_future_active_prices_cannot_finalize_even_with_acknowledgement(self):
        self.service.refresh()
        token = self.service.get_billing_price_set([1]).price_token
        for row in self.store.active:
            row["validation_status"] = "REJECTED"
        with self.assertRaises(PriceUnavailable):
            self.service.lock_and_validate_for_finalization(
                SimpleNamespace(in_transaction=True), token, [1], acknowledged=True)
        for row in self.store.active:
            row["validation_status"] = "ACCEPTED"
            row["provider_timestamp"] = NOW + timedelta(seconds=121)
        with self.assertRaises(PriceUnavailable):
            self.service.get_billing_price_set([1])


if __name__ == "__main__":
    unittest.main()
