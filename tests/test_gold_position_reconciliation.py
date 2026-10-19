"""Gold Position with BOOK reconciliation documents (057): an EFFECTIVE BK_RECON makes its trip count B - b_star (stored record values), the effect -b_star = -L - r is reported
split, a reversal stops it, only the effective generation counts, and any inconsistency refuses the whole read. Real MarketTripBookService / CorrectionService / GoldPositionService
over the in-memory database of tests/market_trip_fakes.py, plus the repository statement through a recording connection. NO database: nothing here proves MySQL SQL or isolation."""
from contextlib import contextmanager
from decimal import Decimal as D
import re
import unittest

from repositories.market_trip_repository import MarketTripRepository
from services.gold_position_service import GoldPositionError, GoldPositionService, REQUIRED_PERMISSIONS, merge_reconciliations
from tests.market_trip_fakes import session_for
from tests.test_book_reconciliation_service import Recorder
from tests.test_book_settlement_service import ReconCase
from tests.test_gold_position_book import Reads, _Authorization
from tests.test_market_trip_book import K18, K21, TODAY, assay, karat


class PositionCase(ReconCase):
    def setUp(self):
        super().setUp()
        reads = Reads(self.db)
        self.snapshots = 0

        @contextmanager
        def snapshot():
            self.snapshots += 1
            yield object()
        self.positions = GoldPositionService(session_for(frozenset(REQUIRED_PERMISSIONS)), transaction_factory=snapshot, authorization=_Authorization(),
                                             trip_repository=lambda c: reads, holding_repository=lambda c: reads, inventory_repository=lambda c: reads,
                                             marketer_repository=lambda c: reads)

    def total(self):
        return self.positions.position()["summary"]["total_eq21"]

    def book(self):
        return self.positions.position()["book"]


class EffectTests(PositionCase):
    def test_a_book_trip_without_reconciliation_and_a_legacy_trip_are_unchanged(self):
        self.service.issue("", "100", [], TODAY, "", self.k())
        trip = self.healthy_trip()
        book = self.book()
        self.assertEqual([(r["code"], r["eq21"], r["label"]) for r in book["trips"]], [(f"MT{int(trip):06d}", D("6.000"), "رصيد دفتري — قبل التسوية النهائية")])
        self.assertEqual((book["reconciliations"], book["reconciliation_effect"]), ([], {"loss": D("0.000"), "rounding": D("0.000"), "total": D("0.000")}))

    def test_a_zero_reconciliation_changes_nothing(self):
        trip = self.zero_trip()
        before = self.total()
        self.reconcile(trip, reason_code=None)
        self.assertEqual(self.total(), before)
        self.assertEqual([(r["b_star"], r["loss_effect"], r["rounding_effect"]) for r in self.book()["reconciliations"]], [(D("0.000"), D("0.000"), D("0.000"))])

    def test_a_positive_posted_balance_is_removed_once_by_b_star_not_by_the_loss_alone(self):
        trip = self.healthy_trip()
        before = self.total()
        self.reconcile(trip)
        book = self.book()
        self.assertEqual(self.total(), before - D("6.000"))
        self.assertEqual(book["trips"], [])                                                       # the trip contributes B - b_star = 0
        self.assertEqual(book["reconciliation_effect"], {"loss": D("-6.000"), "rounding": D("0.000"), "total": D("-6.000")})

    def test_b_zero_with_a_loss_of_a_milligram_is_offset_by_the_rounding_settlement(self):
        trip = self.zero_trip()
        self.buy(trip, [assay("47.5", "0.025", "1.00")], ack=True)                              # A in = 35,625 units, posted 0.001
        self.sell(trip, [assay("437.5", "0.001", "1.00")], ack=True)                            # A out = 13,125 units, posted 0.001 -> A = 3/3500 g, B = 0
        before = self.total()
        self.reconcile(trip, reason_code="TRADING_LOSS")
        recon, = self.book()["reconciliations"]
        self.assertEqual((recon["a_star"], recon["b_star"], recon["loss_effect"], recon["rounding_effect"]), (D("0.000857"), D("0.000"), D("-0.001"), D("0.001")))
        self.assertEqual(self.total(), before)                                                    # the loss part and the rounding part cancel: no change

    def test_a_rounding_only_negative_balance_moves_the_position_up_without_any_gain(self):
        trip = self.issue([karat(K18, "10")])["trip_id"]
        self.ret(trip, [karat(K18, "3.333"), karat(K18, "3.333"), karat(K18, "3.334")])           # A = 0, B = -0.001
        before = self.total()
        self.reconcile(trip, reason_code=None)
        recon, = self.book()["reconciliations"]
        self.assertEqual((recon["a_star"], recon["b_star"], recon["loss_effect"], recon["rounding_effect"]), (D("0.000000"), D("-0.001"), D("0.000"), D("0.001")))
        self.assertEqual(self.total(), before + D("0.001"))

    def test_a_below_precision_residual_leaves_the_position_but_is_not_claimed_to_be_zero(self):
        trip = self.zero_trip()
        self.buy(trip, [assay("100", "0.001", "1.00")], ack=True)                               # A = 1/8750 g, B = 0
        before_book = self.book()
        self.assertEqual([(r["eq21"], r["analytical"]) for r in before_book["trips"]], [(D("0.000"), D("0.000114"))])
        before = self.total()
        self.reconcile(trip, reason_code=None)
        recon, = self.book()["reconciliations"]
        self.assertEqual((recon["a_star"], recon["b_star"], recon["loss_effect"], recon["rounding_effect"]), (D("0.000114"), D("0.000"), D("0.000"), D("0.000")))
        self.assertEqual((self.total(), self.book()["trips"]), (before, []))

    def test_reversal_restores_the_source_and_only_the_new_generation_counts(self):
        trip = self.healthy_trip()
        before = self.total()
        first = self.reconcile(trip)
        self.service.reverse(first["entry_id"], "إعادة")
        self.assertEqual(self.total(), before)                                                    # the reversed document stops counting
        self.assertEqual(self.book()["reconciliations"], [])
        second = self.reconcile(trip)
        self.assertEqual(second["generation"], 2)
        self.assertEqual(self.total(), before - D("6.000"))                                       # once, not once per generation
        self.assertEqual([r["generation"] for r in self.book()["reconciliations"]], [2])

    def test_a_closed_status_never_removes_the_trip_by_itself(self):
        trip = self.healthy_trip()
        before = self.total()
        self.db.trips[int(trip)]["status"] = "CLOSED"
        self.assertEqual(self.total(), before)

    def test_several_trips_with_assayed_stock_are_not_mixed(self):
        base = self.total()
        one = self.issue([karat(K21, "20")])["trip_id"]
        self.ret(one, [assay("866.7", "1.8")])                                                    # assayed stock: a SHOP part, counted once
        two = self.healthy_trip()
        self.reconcile(one, reason_code="OPERATING_LOSS")
        position = self.positions.position()
        self.assertEqual([r["code"] for r in position["book"]["trips"]], [f"MT{int(two):06d}"])
        self.assertEqual(position["book"]["trips"][0]["eq21"], D("6.000"))
        self.assertEqual(position["shop"]["assayed"][0]["eq21"], D("1.783"))                     # the stock is untouched by the reconciliation
        recon, = position["book"]["reconciliations"]
        self.assertEqual((recon["trip_id"], recon["b_star"]), (str(one), D("18.217")))
        self.assertEqual(position["summary"]["total_eq21"], base - D("18.217"))                   # only trip one's settled b_star leaves; trip two only moved gold (21K conserved)


class RefusalTests(PositionCase):
    def setUp(self):
        super().setUp()
        self.trip = self.healthy_trip()
        self.first = self.reconcile(self.trip)
        self.record = self.db.reconciliations[int(self.first["entry_id"])]

    def refused(self, code):
        with self.assertRaises(GoldPositionError) as refused:
            self.positions.position()
        self.assertEqual(refused.exception.code, code)

    def test_two_effective_reconciliations_are_refused(self):
        clone = dict(self.db.entries[int(self.first["entry_id"])], id=900, entry_no=99, request_key="k900")
        self.db.entries[900] = clone
        self.db.reconciliations[900] = dict(self.record, entry_id=900, generation=2)
        self.refused("RECONCILIATION_DUPLICATE")

    def test_a_missing_or_invalid_record_is_refused(self):
        self.db.reconciliations.pop(int(self.first["entry_id"]))
        self.refused("RECONCILIATION_RECORD_MISSING")

    def test_inconsistent_record_figures_are_refused(self):
        self.record["loss_posted"] = D("5.999")
        self.refused("RECONCILIATION_RECORD_INVALID")
        self.record["loss_posted"] = D("6.000")
        self.record["trip_mode"] = "LEGACY"
        self.refused("RECONCILIATION_RECORD_INVALID")
        self.record["trip_mode"] = "BOOK"
        self.record["b_star"] = None
        self.refused("RECONCILIATION_RECORD_INVALID")

    def test_a_source_that_no_longer_matches_is_refused_not_zeroed(self):
        line = next(l for l in self.db.book_lines if l["trip_id"] == int(self.trip) and l["entry_kind"] == "BK_RETURN")
        line["eq21_posted"] = D("3.999")
        self.refused("RECONCILIATION_SOURCE_MISMATCH")

    def test_the_analytical_side_is_matched_too(self):
        self.record["a_star_units"] += 1
        self.refused("RECONCILIATION_SOURCE_MISMATCH")


class CompatibilityTests(PositionCase):
    def test_before_057_without_any_reconciliation_the_position_works(self):
        trip = self.healthy_trip()
        self.db.no_recon_table = True
        self.assertEqual(self.book()["trips"][0]["code"], f"MT{int(trip):06d}")

    def test_a_reconciliation_entry_without_its_record_is_never_ignored(self):
        trip = self.healthy_trip()
        self.reconcile(trip)
        self.db.no_recon_table = True                                                             # a record the read cannot see
        with self.assertRaises(GoldPositionError) as refused:
            self.positions.position()
        self.assertEqual(refused.exception.code, "RECONCILIATION_RECORD_MISSING")

    def test_one_snapshot_one_reconciliation_read_and_the_disabled_service_untouched(self):
        self.healthy_trip()
        self.db.display_reads = 0
        self.positions.position()
        self.assertEqual((self.snapshots, self.db.display_reads), (1, 1))

    def test_the_pure_merge_keeps_the_previous_behaviour_without_reconciliations(self):
        self.assertEqual(merge_reconciliations({}, []), [])


class _TableRecorder(Recorder):
    """A recording connection whose capability check reports the 057 table as present (n = 1) or absent (n = 0)."""

    def __init__(self, present):
        super().__init__()
        self.present = present

    def cursor(self, dictionary=False):
        inner, present = super().cursor(dictionary), self.present

        class Cursor:
            lastrowid, rowcount = 1, 0

            def execute(self, sql, params=()):
                inner.execute(sql, params)

            def fetchone(self):
                return {"n": 1 if present else 0}

            def fetchall(self):
                return []

            def close(self):
                pass
        return Cursor()


class StatementTests(unittest.TestCase):
    def statements(self, present):
        connection = _TableRecorder(present)
        MarketTripRepository(connection).effective_reconciliations_display()
        return [" ".join((s if isinstance(s, str) else s[0]).split()) for s in connection.statements]

    def test_with_057_one_lock_free_read_joined_one_to_one(self):
        statements = self.statements(True)
        self.assertEqual(len(statements), 2)                                                      # the capability check + ONE read for every trip (no per-trip query)
        self.assertIn("information_schema.TABLES", statements[0])
        for sql in statements:
            self.assertIsNone(re.search(r"FOR\s+(SHARE|UPDATE)", sql), sql)
            self.assertNotRegex(sql, r"LIMIT")
        read = statements[1]
        self.assertIn("LEFT JOIN market_trip_reconciliations r ON r.entry_id = e.id", read)        # 1:1 by the record's primary key: no source line is multiplied
        self.assertNotIn("market_trip_book_lines", read)
        self.assertIn("e.entry_kind = 'BK_RECON'", read)
        self.assertIn("transaction_corrections", read)                                            # effectiveness from the correction relation, no status column

    def test_before_057_the_table_is_never_named_but_the_entries_are_still_read(self):
        read = self.statements(False)[1]
        self.assertNotIn("market_trip_reconciliations", read)
        self.assertIn("e.entry_kind = 'BK_RECON'", read)
        self.assertIn("NULL AS record_entry_id", read)
        self.assertIsNone(re.search(r"FOR\s+(SHARE|UPDATE)", read))


if __name__ == "__main__":
    unittest.main()
