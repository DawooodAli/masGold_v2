"""Prerequisite hardening before 048: (A) the resource order of a mixed custody creation and (B) CURRENT reads for the nested reversal
filters of the custody decision queries.

Real MarketTripService / CorrectionService over the in-memory transactional database of tests/market_trip_fakes.py, plus the real
MarketTripRepository SQL text captured through a stub cursor. These are NOT real-MySQL tests and prove nothing about snapshots or
blocking: the stale-snapshot behaviour and the acquisition order on separate connections are exercised by
tests/test_market_trip_hardening_mysql.py (opt-in, masgold_rehearsal only)."""
from decimal import Decimal as D
import re
from unittest.mock import patch
import unittest

from repositories.market_trip_repository import MarketTripRepository
from services.market_trip_service import TripError
from tests.market_trip_fakes import TripRepo
from tests.test_market_trip_gold_settle import GoldSettleCase, S18_INACTIVE
from tests.test_market_trip_lot_issues import BarCase
from tests.test_market_trips import K18, K21, TODAY, gold, key


def kinds(db):
    return [entry[0] for entry in db.lock_log]


class MixedCreationOrderTests(BarCase):
    """Intended order after authorization: new trip -> existing bars (ascending, availability checked) -> cash account -> Scrap."""

    def create(self, cash="", rows=(), lots=()):
        self.n += 1
        self.db.lock_log.clear()
        return self.service.create_trip(cash, gold(*rows), [{"lot_id": str(l)} for l in lots], TODAY, "", key(1800 + self.n))

    def test_a_mixed_creation_locks_the_new_trip_then_the_bar_then_cash_then_scrap(self):
        self.create("100", [(K21, "1"), (K18, "1")], [self.lot])
        self.assertEqual(self.db.lock_log, [("trip",), ("lots", (self.lot,)), ("account", "CASH", 1), ("scrap", 1), ("scrap", 2)])

    def test_the_other_creations_keep_their_own_order_and_skip_unused_resources(self):
        self.create("100", [(K21, "1")])
        self.assertEqual(kinds(self.db), ["trip", "account", "scrap"])
        self.create(lots=[self.lot])
        self.assertEqual(kinds(self.db), ["trip", "lots"])                       # a bar-only custody takes no cash / Scrap lock
        self.create("100")
        self.assertEqual(kinds(self.db), ["trip", "account"])
        self.create(rows=[(K21, "1")])
        self.assertEqual(kinds(self.db), ["trip", "scrap"])                      # an empty bar range takes no lot lock at all

    def test_an_unavailable_bar_is_refused_before_any_cash_or_scrap_lock(self):
        holder = self.custody()
        self.give_out(holder, [self.lot])                                         # the bar is now in another custody
        before = self.state()
        self.db.lock_log.clear()
        with self.assertRaises(TripError) as context:
            self.create("100", [(K21, "1")], [self.lot])
        self.assertEqual(context.exception.code, "LOT_NOT_IN_SHOP")
        self.assertEqual(kinds(self.db), ["trip", "lots"], "cash / Scrap were locked before the bar was refused")
        self.assertEqual(self.state(), before)

    def test_a_partial_shop_stock_bar_is_refused_before_any_cash_or_scrap_lock(self):
        holder = self.custody()
        self.give_out(holder, [self.lot])
        self.legacy_partial_shop(holder)
        before = self.state()
        self.db.lock_log.clear()
        with self.assertRaises(TripError) as context:
            self.create("100", [(K21, "1")], [self.lot])
        self.assertEqual(context.exception.code, "LOT_PARTIAL_SHOP_STOCK")
        self.assertEqual(kinds(self.db), ["trip", "lots"])
        self.assertEqual(self.state(), before)

    def test_an_unknown_bar_is_refused_before_cash_and_scrap(self):
        with self.assertRaises(TripError) as context:
            self.create("100", [(K21, "1")], [999999])
        self.assertEqual((context.exception.code, kinds(self.db)), ("LOT_NOT_FOUND", ["trip", "lots"]))

    def test_the_bar_is_checked_first_when_the_scrap_is_short_too(self):
        holder = self.custody()
        self.give_out(holder, [self.lot])
        with self.assertRaises(TripError) as context:
            self.create("100", [(K21, "99999")], [self.lot])
        self.assertEqual(context.exception.code, "LOT_NOT_IN_SHOP")              # was SCRAP_SHORTAGE while cash / Scrap came first

    def test_a_scrap_shortage_after_a_good_bar_still_rolls_everything_back(self):
        before = self.state()
        self.db.lock_log.clear()
        with self.assertRaises(TripError) as context:
            self.create("100", [(K21, "99999")], [self.lot])
        self.assertEqual(context.exception.code, "SCRAP_SHORTAGE")
        self.assertEqual(kinds(self.db), ["trip", "lots", "account", "scrap"])
        self.assertEqual(self.state(), before)
        self.assertEqual(self.where(), (D("2.950"), {}))

    def test_conservation_and_unchanged_posting_of_a_successful_mixed_creation(self):
        cash = sum((m["amount_delta"] for m in self.db.cash_moves), D("0"))
        scrap = sum((m["weight_delta"] for m in self.db.scrap if m["karat_id"] == 1), D("0"))
        done = self.create("100", [(K21, "1")], [self.lot])
        trip = int(done["trip_id"])
        self.assertEqual([e["kind"] for e in done["entries"]], ["ISSUE", "LOT_ISSUE"])
        self.assertEqual(sum((m["amount_delta"] for m in self.db.cash_moves), D("0")), cash - D("100"))
        self.assertEqual(sum((m["weight_delta"] for m in self.db.scrap if m["karat_id"] == 1), D("0")), scrap - D("1"))
        self.assertEqual(self.where(), (D("0.000"), {trip: D("2.950")}))          # the bar is in exactly one place
        self.assertEqual(self.service.trip_detail(str(trip))["custody"]["cash"]["outstanding"], D("100.00"))


class CurrentReadRequestTests(GoldSettleCase):
    """Every custody DECISION asks the repository for CURRENT nested reversal filters; display reads do not."""

    def record(self):
        calls = []
        cash, gold_reads = TripRepo.custody_cash, TripRepo.custody_gold

        def wrap_cash(repo, trip_id, exclude_entry_id=None, *, current=False):
            calls.append(("cash", current))
            return cash(repo, trip_id, exclude_entry_id, current=current)

        def wrap_gold(repo, trip_id, exclude_entry_id=None, *, current=False):
            calls.append(("gold", current))
            return gold_reads(repo, trip_id, exclude_entry_id, current=current)

        for target, replacement in (("custody_cash", wrap_cash), ("custody_gold", wrap_gold)):
            started = patch.object(TripRepo, target, replacement)
            started.start()
            self.addCleanup(started.stop)
        return calls

    def assert_current(self, calls, label):
        self.assertTrue(calls, f"{label}: the custody was not read")
        self.assertTrue(all(current for _, current in calls), f"{label}: a decision read used a snapshot filter: {calls}")

    def test_every_posting_decision_reads_current_filters(self):
        calls = self.record()
        operations = (
            ("return", lambda: self.ret(self.trip, "1")),
            ("melt", lambda: self.service.melt(self.trip, [{"karat_id": K21, "weight": "1"}], TODAY, "ورشة", {"weight": "0.9", "fineness": "900"},
                                               [], "", "", "", key(5001))),
            ("heat", lambda: self.service.heat(self.trip, [{"karat_id": K21, "input_weight": "1", "output_weight": "0.9"}], TODAY, "ورشة", "", "",
                                               "", key(5002))),
            ("supplier payment", lambda: self.service.pay_supplier(self.trip, "10", "10", TODAY, "", "", key(5003))),
            ("gold settlement", lambda: self.settle(S18_INACTIVE, K21, "1")),
        )
        for label, call in operations:
            calls.clear()
            call()
            self.assert_current(calls, label)
        calls.clear()
        with self.assertRaises(TripError) as context:                              # the closing check reads the custody before it refuses
            self.service.close(self.trip)
        self.assertEqual(context.exception.code, "OUTSTANDING_CUSTODY")
        self.assert_current(calls, "close")

    def test_the_reversal_guards_read_current_filters(self):
        calls = self.record()
        heat = self.service.heat(self.trip, [{"karat_id": K21, "input_weight": "1", "output_weight": "0.9"}], TODAY, "ورشة", "", "", "", key(5010))
        calls.clear()
        self.service.reverse(heat["entry_id"], "x")
        self.assert_current(calls, "reversal of a heat")
        calls.clear()
        self.service.reverse(min(self.db.entries), "x")                            # nothing depends on the issue any more: it reverses
        self.assert_current(calls, "reversal of an issue")

    def test_display_reads_keep_the_snapshot_filters(self):
        calls = self.record()
        self.service.trips()
        self.service.trip_detail(self.trip)
        self.assertTrue(calls)
        self.assertFalse(any(current for _, current in calls), calls)


class Capture:
    """A stub connection recording the SQL the real repository would send, answering every read with no rows."""

    def __init__(self):
        self.sql = []

    def cursor(self, dictionary=False):
        capture = self

        class Cursor:
            def execute(self, query, params=()):
                capture.sql.append(" ".join(query.split()))

            def fetchall(self):
                return []

            def fetchone(self):
                return {"issued": 0, "returned": 0, "paid": 0}

            def close(self):
                pass

        return Cursor()


NESTED_CURRENT = re.compile(r"NOT EXISTS \(SELECT 1 FROM transaction_corrections c WHERE .*? FOR SHARE\)")
NESTED_SNAPSHOT = re.compile(r"NOT EXISTS \(SELECT 1 FROM transaction_corrections c WHERE [^()]*?'FULL_REVERSAL'\)")


class RepositorySqlTests(unittest.TestCase):
    """The nested reversal lookup of each decision query is itself a locking (current) read; the display form is not."""

    def run_all(self, **flags):
        capture = Capture()
        repo = MarketTripRepository(capture)
        repo.custody_cash(1, 2, **flags)
        repo.custody_gold(1, 2, **flags)
        return capture.sql

    def test_the_current_form_of_custody_cash_and_gold_locks_every_nested_lookup(self):
        statements = self.run_all(current=True)
        self.assertEqual(len(statements), 7)                                       # 2 cash reads + 5 gold reads (legs, melts, heats, settlements, holdings)
        for sql in statements:
            self.assertEqual(len(NESTED_CURRENT.findall(sql)), 1, sql)
            self.assertNotRegex(sql, NESTED_SNAPSHOT.pattern)

    def test_the_default_form_stays_a_snapshot_lookup(self):
        for sql in self.run_all():
            self.assertRegex(sql, NESTED_SNAPSHOT.pattern)
            self.assertNotRegex(sql, NESTED_CURRENT.pattern)

    def test_the_reversal_guard_queries_always_use_the_current_form(self):
        capture = Capture()
        repo = MarketTripRepository(capture)
        repo.later_heat_entries(1, 1, [1])
        repo.later_logo_returns(1, 1, [1])
        repo.later_gold_settlements(1, 1, [1])
        self.assertEqual(len(capture.sql), 3)
        for sql in capture.sql:
            self.assertEqual(len(NESTED_CURRENT.findall(sql)), 1, sql)

    def test_empty_karat_ranges_run_no_query(self):
        capture = Capture()
        repo = MarketTripRepository(capture)
        self.assertEqual((repo.later_heat_entries(1, 1, []), repo.later_logo_returns(1, 1, []), repo.later_gold_settlements(1, 1, [])), ([], [], []))
        self.assertEqual(capture.sql, [])


if __name__ == "__main__":
    unittest.main()
