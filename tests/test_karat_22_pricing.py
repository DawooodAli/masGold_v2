"""22K price derivation, provenance, coherent publication and the legacy 3-karat transition.
Real GoldPriceService + real GoldPriceRepository over the SQLite price fixture (fake provider, fixed clock); no MySQL, no network."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
import json
from types import SimpleNamespace
import unittest

from app.session import AppSession
from repositories.gold_price_repository import GoldPriceRepository
from services.authentication_service import AuthenticationResult, _VERIFIED
from services.gold_price_provider import NormalizedQuote, QuoteValidationError, validate_quotes
from services.gold_price_service import GoldPriceService, PriceUnavailable, derive_22k, usable_rows
from tests.gold_price_database_fixture import PriceDatabase

D = Decimal
NOW = datetime(2026, 10, 9, 9, 0, tzinfo=timezone.utc)
PROVIDER = {18: (D("5320.0000"), D("5341.0000")), 21: (D("6207.0000"), D("6232.0000")),
            24: (D("7093.0000"), D("7122.0000"))}
# karat ids in the SQLite fixture
ID18, ID21, ID24, ID22 = 4, 8, 9, 10


def quote(karat, buy, sell, source="PRIMARY", at=NOW):
    return NormalizedQuote(source, karat, buy, sell, "EGP", "GRAM", at, at)


class Fixture(unittest.TestCase):
    with_22k = True

    def setUp(self):
        self.db = PriceDatabase(with_22k=self.with_22k)
        self.addCleanup(self.db.close)
        with self.db.connect() as c:
            c.execute("INSERT INTO gold_price_sources VALUES (10,'PRIMARY','Primary','LOCAL_MARKET',2,1)")
            c.execute("UPDATE gold_price_engine_settings SET primary_source_id=10")
        self.now = NOW
        self.prices = dict(PROVIDER)
        self.at = NOW
        self.session = AppSession()
        self.session._establish(AuthenticationResult(17, None, "Test owner",
                                frozenset({"APP_LOGIN", "SETTINGS_MANAGE"}), _VERIFIED), 0)
        self.calls = 0
        self.service = GoldPriceService(
            self.session, transaction_factory=self.db.transaction, repository_factory=GoldPriceRepository,
            providers={"PRIMARY": SimpleNamespace(source_code="PRIMARY", fetch=self.fetch)}, clock=lambda: self.now)

    def fetch(self):
        self.calls += 1
        return tuple(quote(k, *self.prices[k], at=self.at) for k in (18, 21, 24))

    def rows(self):
        with self.db.connect() as c:
            return [dict(r) for r in c.execute(
                "SELECT q.*, k.karat_value FROM gold_price_quotes q JOIN karats k ON k.id=q.karat_id ORDER BY q.id").fetchall()]

    def count(self, table):
        with self.db.connect() as c:
            return c.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]

    @staticmethod
    def by_karat(snapshot):
        return {p["karat"]: p for p in snapshot["prices"]}


class DerivationMathTests(unittest.TestCase):
    def test_each_side_is_the_stored_24k_price_times_22_over_24_to_four_places(self):
        derived = derive_22k(quote(24, D("7093.0000"), D("7122.0000")))
        self.assertEqual((derived.karat, derived.shop_buy_price_per_gram, derived.shop_sell_price_per_gram),
                         (22, D("6501.9167"), D("6528.5000")))
        self.assertEqual(derived.shop_buy_price_per_gram.as_tuple().exponent, -4)

    def test_a_tie_rounds_half_up_not_half_even(self):
        derived = derive_22k(quote(24, D("7093.0002"), D("7122.0000")))      # exact 6501.91685
        self.assertEqual(derived.shop_buy_price_per_gram, D("6501.9169"))

    def test_provider_timestamps_and_attempt_are_preserved_not_fabricated(self):
        at = NOW - timedelta(seconds=3)
        source = NormalizedQuote("GOLDTIMO", 24, D("7093"), D("7122"), "EGP", "GRAM", at, NOW, "attempt-1")
        derived = derive_22k(source)
        self.assertIsInstance(derived.shop_buy_price_per_gram, Decimal)
        self.assertEqual((derived.source_code, derived.provider_timestamp, derived.fetched_at, derived.refresh_attempt_id),
                         ("GOLDTIMO", at, NOW, "attempt-1"))

    def test_only_the_24k_quote_may_be_the_source_and_prices_must_stay_positive_with_buy_not_above_sell(self):
        for karat in (18, 21, 22):
            with self.assertRaises(ValueError):
                derive_22k(quote(karat, D("100"), D("101")))
        with self.assertRaises(ValueError):
            derive_22k(quote(24, D("7200"), D("7100")))
        with self.assertRaises(ValueError):
            derive_22k(quote(24, D("0"), D("7100")))

    def test_the_provider_contract_stays_exactly_three_karats(self):
        four = tuple(quote(k, D("100") + k, D("110") + k) for k in (18, 21, 22, 24))
        with self.assertRaises(QuoteValidationError):
            validate_quotes(four, "PRIMARY", NOW)
        with self.assertRaises(QuoteValidationError):
            validate_quotes((quote(18, D("1"), D("2")), quote(21, D("1"), D("2")), quote(22, D("1"), D("2"))), "PRIMARY", NOW)
        self.assertEqual(len(validate_quotes(tuple(quote(k, *PROVIDER[k]) for k in (18, 21, 24)), "PRIMARY", NOW)), 3)


class AutoPublicationTests(Fixture):
    def test_one_refresh_publishes_one_group_of_four_with_22k_derived_from_the_exact_24k_quote(self):
        snapshot = self.service.refresh()
        self.assertEqual(snapshot["status"], "FRESH")
        self.assertEqual(sorted(self.by_karat(snapshot)), ["18", "21", "22", "24"])
        rows = self.rows()
        self.assertEqual(len(rows), 4)
        self.assertEqual(len({r["quote_group_id"] for r in rows}), 1)
        q = {r["karat_value"]: r for r in rows}
        self.assertEqual(q[22]["quote_method"], "DERIVED")
        self.assertEqual(q[22]["derived_from_quote_id"], q[24]["id"])
        self.assertTrue(all(q[k]["derived_from_quote_id"] is None for k in (18, 21, 24)))
        self.assertEqual((D(q[22]["shop_buy_price_per_gram"]), D(q[22]["shop_sell_price_per_gram"])),
                         (D("6501.9167"), D("6528.5000")))
        self.assertEqual(q[22]["provider_timestamp"], q[24]["provider_timestamp"])
        self.assertEqual(q[22]["source_id"], q[24]["source_id"])
        self.assertEqual((self.count("gold_price_activations"), self.count("active_gold_prices")), (4, 4))
        note = json.loads(q[22]["notes"])["derivation"]
        self.assertEqual((note["method"], note["base_karat"], note["source_quote_id"]), ("KARAT_RATIO_FROM_24K", 24, q[24]["id"]))

    def test_the_snapshot_labels_22k_as_derived_and_only_22k(self):
        prices = self.by_karat(self.service.refresh())
        self.assertTrue(prices["22"]["derived"])
        self.assertEqual(prices["22"]["derived_note"], "مشتق من عيار 24")
        for karat in ("18", "21", "24"):
            self.assertFalse(prices[karat]["derived"])
            self.assertEqual(prices[karat]["derived_note"], "")
        self.assertEqual((prices["22"]["shop_buy_price_per_gram"], prices["22"]["shop_sell_price_per_gram"]),
                         ("6501.9167", "6528.5000"))

    def test_a_repeat_refresh_with_unchanged_prices_does_not_republish(self):
        first = self.service.refresh()
        again = self.service.refresh()
        self.assertEqual(first["price_token"], again["price_token"])
        self.assertEqual(self.count("gold_price_quotes"), 4)

    def test_a_failed_publication_leaves_no_partial_22k_set(self):
        self.db.fail_second_pointer = True
        snapshot = self.service.refresh()
        self.db.fail_second_pointer = False
        self.assertEqual(snapshot["prices"], [])
        for table in ("gold_price_quotes", "gold_price_activations", "active_gold_prices"):
            self.assertEqual(self.count(table), 0)

    def test_stale_provider_prices_are_never_published_so_22k_is_not_derived(self):
        self.at = NOW - timedelta(seconds=900)
        snapshot = self.service.refresh()
        self.assertEqual(snapshot["prices"], [])
        self.assertEqual(self.count("gold_price_quotes"), 0)

    def test_a_new_provider_price_republishes_a_new_group_and_keeps_history_untouched(self):
        self.service.refresh()
        before = self.rows()
        self.at = NOW + timedelta(seconds=30)
        self.now = NOW + timedelta(seconds=40)
        self.prices[24] = (D("7100.0000"), D("7130.0000"))
        snapshot = self.service.refresh()
        after = self.rows()
        self.assertEqual(after[:4], before)                       # nothing historical is rewritten
        self.assertEqual(len(after), 8)
        self.assertEqual(self.by_karat(snapshot)["22"]["shop_buy_price_per_gram"], "6508.3333")
        new = {r["karat_value"]: r for r in after[4:]}
        old24 = next(r for r in before if r["karat_value"] == 24)
        self.assertEqual(new[22]["derived_from_quote_id"], new[24]["id"])        # the NEW 24K, never the old one
        self.assertNotEqual(new[22]["derived_from_quote_id"], old24["id"])


class LegacyTransitionTests(Fixture):
    with_22k = False

    def add_master_row(self):
        with self.db.connect() as c:
            c.execute("INSERT INTO karats VALUES (10,22,1)")

    def test_without_a_22k_master_row_the_legacy_three_karat_set_is_published_and_usable(self):
        snapshot = self.service.refresh()
        self.assertEqual(sorted(self.by_karat(snapshot)), ["18", "21", "24"])
        self.assertEqual(self.count("gold_price_quotes"), 3)
        self.assertEqual(snapshot["status"], "FRESH")

    def test_a_legacy_set_keeps_working_after_the_master_row_appears_but_22k_is_unavailable(self):
        legacy = self.service.refresh()
        self.add_master_row()
        snapshot = self.service.snapshot()
        self.assertEqual(snapshot["price_token"], legacy["price_token"])
        self.assertEqual(sorted(self.by_karat(snapshot)), ["18", "21", "24"])
        with self.assertRaises(PriceUnavailable):                  # never another karat's price
            self.service.get_billing_price_set([ID22])
        self.assertEqual(sorted(self.service.get_billing_price_set([ID18, ID21, ID24]).prices), [ID18, ID21, ID24])

    def test_auto_republishes_once_with_22k_even_though_the_provider_prices_are_unchanged(self):
        self.service.refresh()
        old_group = {r["quote_group_id"] for r in self.rows()}
        self.add_master_row()
        calls = self.calls
        snapshot = self.service.refresh()
        self.assertGreater(self.calls, calls)
        self.assertEqual(sorted(self.by_karat(snapshot)), ["18", "21", "22", "24"])
        rows = self.rows()
        self.assertEqual(len(rows), 7)
        self.assertEqual({r["quote_group_id"] for r in rows[:3]}, old_group)       # the legacy set stays as history
        self.assertEqual(len({r["quote_group_id"] for r in rows[3:]}), 1)
        self.assertEqual(self.service.refresh()["price_token"], snapshot["price_token"])    # and is stable again
        self.assertEqual(self.count("gold_price_quotes"), 7)

    def test_manual_activation_adds_22k_derived_from_the_rounded_stored_24k_only_after_a_new_owner_activation(self):
        self.service.activate_manual("6207.3333", "6232.6666", "اعتماد المالك")
        self.assertEqual(sorted({r["karat_value"] for r in self.rows()}), [18, 21, 24])    # legacy: no 22K master row yet
        self.add_master_row()
        self.assertEqual(sorted(self.by_karat(self.service.snapshot())), ["18", "21", "24"])   # unavailable until an OWNER activates
        snapshot = self.service.activate_manual("6207.3333", "6232.6666", "إضافة عيار 22")
        prices = self.by_karat(snapshot)
        self.assertEqual(sorted(prices), ["18", "21", "22", "24"])
        q = {r["karat_value"]: r for r in self.rows() if r["id"] > 3}
        for side in ("shop_buy_price_per_gram", "shop_sell_price_per_gram"):
            self.assertEqual(D(q[22][side]), (D(q[24][side]) * 22 / 24).quantize(D("0.0001"), ROUND_HALF_UP))
        self.assertEqual((q[22]["quote_method"], q[22]["derived_from_quote_id"]), ("DERIVED", q[24]["id"]))
        self.assertTrue(prices["22"]["derived"])


class ConsistencyGuardTests(Fixture):
    def tamper(self, sql):
        with self.db.connect() as c:
            c.execute(sql)

    def assert_22k_unavailable_others_intact(self):
        self.assertEqual(sorted(self.by_karat(self.service.snapshot())), ["18", "21", "24"])
        with self.assertRaises(PriceUnavailable):
            self.service.get_billing_price_set([ID22])
        self.assertEqual(sorted(self.service.get_billing_price_set([ID18, ID21, ID24]).prices), [ID18, ID21, ID24])

    def test_a_22k_price_that_is_not_the_derivation_of_its_own_24k_is_dropped(self):
        self.service.refresh()
        self.tamper("UPDATE gold_price_quotes SET shop_buy_price_per_gram='6000.0000' WHERE karat_id=10")
        self.assert_22k_unavailable_others_intact()

    def test_a_22k_row_without_provenance_is_dropped(self):
        self.service.refresh()
        self.tamper("UPDATE gold_price_quotes SET derived_from_quote_id=NULL WHERE karat_id=10")
        self.assert_22k_unavailable_others_intact()

    def test_a_22k_row_pointing_at_another_quote_is_dropped(self):
        self.service.refresh()
        self.tamper("UPDATE gold_price_quotes SET derived_from_quote_id=(SELECT id FROM gold_price_quotes WHERE karat_id=8) "
                    "WHERE karat_id=10")
        self.assert_22k_unavailable_others_intact()

    def test_a_22k_row_from_a_different_group_is_dropped(self):
        self.service.refresh()
        self.tamper("UPDATE gold_price_quotes SET quote_group_id='other' WHERE karat_id=10")
        self.assert_22k_unavailable_others_intact()

    def test_usable_rows_never_keeps_22k_without_a_24k_row_or_with_two_22k_rows(self):
        self.service.refresh()
        rows = self.service._read()[3]
        self.assertEqual(len(rows), 4)
        without24 = [r for r in rows if r["karat_value"] != 24]
        self.assertEqual(sorted(r["karat_value"] for r in usable_rows(without24)), [18, 21])
        self.assertEqual(len(usable_rows(rows + [r for r in rows if r["karat_value"] == 22])), 3)

    def test_a_missing_24k_quote_cannot_produce_a_22k_price(self):
        with self.assertRaises(PriceUnavailable):
            GoldPriceService._with_derived((quote(18, D("1"), D("2")), quote(21, D("1"), D("2"))), {18: 4, 21: 8, 22: 10})


class InvoiceSnapshotProvenanceTests(Fixture):
    def test_billing_set_returns_the_22k_quote_id_and_activation_for_the_invoice_snapshot(self):
        self.service.refresh()
        price_set = self.service.get_billing_price_set([ID22, ID24])
        q22, q24 = price_set.prices[ID22], price_set.prices[ID24]
        rows = {r["karat_value"]: r for r in self.rows()}
        self.assertEqual((q22.quote_id, q24.quote_id), (rows[22]["id"], rows[24]["id"]))
        self.assertEqual((q22.shop_buy_price_per_gram, q22.shop_sell_price_per_gram), (D("6501.9167"), D("6528.5000")))
        self.assertEqual(q22.karat, 22)
        self.assertEqual(price_set.provenance["quote_group_id"], rows[22]["quote_group_id"])

    def test_the_price_token_covers_the_22k_quote(self):
        self.service.refresh()
        token = self.service.snapshot()["price_token"]
        settings, _, _, rows = self.service._read()
        changed = [dict(r, quote_id=int(r["quote_id"]) + 1000) if r["karat_value"] == 22 else r for r in rows]
        self.assertNotEqual(self.service._token(settings, changed), token)


if __name__ == "__main__":
    unittest.main()
