"""Unified custody creation (M3 correction): ONE new custody from any combination of MAIN_SAFE cash, Scrap lines and whole shop bars,
posted in a single transaction. Real MarketTripService over the in-memory transactional database of tests/market_trip_fakes.py (a
failed transaction restores every table). NOT real-MySQL tests: the SQL and the rollback were exercised on the masgold_rehearsal
sandbox, single-threaded; concurrency is not exercised."""
import copy
from decimal import Decimal as D
import unittest

from services.market_trip_service import TripError
from tests.market_trip_fakes import MANAGER
from tests.test_market_trip_lot_issues import BarCase
from tests.test_market_trips import K21, TODAY, gold, key


class CreateCase(BarCase):
    def create(self, cash="", rows=(), lots=(), request=None, service=None, notes=""):
        self.n += 1
        return (service or self.service).create_trip(cash, gold(*rows), [{"lot_id": str(l)} for l in lots], TODAY, notes, request or key(1100 + self.n))

    def cash_total(self):
        return sum((m["amount_delta"] for m in self.db.cash_moves), D("0"))

    def scrap_total(self, karat=K21):
        return sum((m["weight_delta"] for m in self.db.scrap if str(m["karat_id"]) == str(karat)), D("0"))


class CreationTests(CreateCase):
    def test_a_bar_only_custody_needs_no_cash_or_scrap_and_posts_one_entry(self):
        cash, scrap = self.cash_total(), self.scrap_total()
        done = self.create(lots=[self.lot])
        self.assertEqual((done["created"], done["kind"], len(done["entries"])), (True, "LOT_ISSUE", 1))
        trip = done["trip_id"]
        self.assertEqual(self.db.trips[int(trip)]["status"], "OPEN")
        self.assertEqual(self.where(), (D("0.000"), {int(trip): D("2.950")}))
        self.assertEqual((self.cash_total(), self.scrap_total()), (cash, scrap))                  # no dummy cash / Scrap, nothing else moved
        self.assertEqual([e["event_type"] for e in self.db.events if e["trip_id"] == int(trip)], ["OPENED"])
        self.assertEqual(copy.deepcopy((self.db.melts, self.db.lots)), self.melt_snapshot)         # identity, weight, assay, analysis
        self.assertEqual(self.held(trip), {str(self.lot): D("2.950")})

    def test_whole_return_and_closure_of_a_bar_only_custody(self):
        trip = self.create(lots=[self.lot])["trip_id"]
        self.assertTrue(self.refused_close(trip))                                                  # holds the bar: cannot close
        self.give_back(trip, [self.lot])
        self.assertEqual(self.where(), (D("2.950"), {}))
        self.assertEqual(self.service.close(trip)["status"], "CLOSED")

    def refused_close(self, trip):
        with self.assertRaises(TripError):
            self.service.close(trip)
        return True

    def test_cash_scrap_and_a_bar_together_post_two_entries_atomically(self):
        cash, scrap = self.cash_total(), self.scrap_total()
        done = self.create("100", [(K21, "1")], [self.lot])
        trip = int(done["trip_id"])
        self.assertEqual([e["kind"] for e in done["entries"]], ["ISSUE", "LOT_ISSUE"])
        self.assertEqual([e["code"] for e in done["entries"]], [f"MT{trip:06d}-1", f"MT{trip:06d}-2"])
        self.assertEqual(self.cash_total(), cash - D("100"))
        self.assertEqual(self.scrap_total(), scrap - D("1.000"))
        self.assertEqual(self.where(), (D("0.000"), {trip: D("2.950")}))
        detail = self.service.trip_detail(str(trip))
        self.assertEqual(detail["custody"]["cash"]["outstanding"], D("100.00"))
        self.assertEqual(sorted(e["kind"] for e in detail["entries"]), ["ISSUE", "LOT_ISSUE"])    # individual history references
        self.assertEqual(len([e for e in self.db.events if e["trip_id"] == trip]), 1)               # opened once
        self.assertEqual(len(self.db.lot_issues), 1)

    def test_the_existing_cash_only_and_scrap_only_creations_still_work(self):
        cash_only = self.create("50")
        self.assertEqual((cash_only["kind"], len(cash_only["entries"])), ("ISSUE", 1))
        scrap_only = self.create(rows=[(K21, "1")])
        self.assertEqual((scrap_only["kind"], len(scrap_only["entries"])), ("ISSUE", 1))
        self.assertEqual(self.where(), (D("2.950"), {}))                                            # the bar was never touched
        self.assertEqual(self.issue("20", [], trip=cash_only["trip_id"])["kind"], "ISSUE")           # the OPEN-custody issue is unchanged
        second = self.give_out(cash_only["trip_id"], [self.lot])                                     # and so is adding a bar to it
        self.assertEqual(second["kind"], "LOT_ISSUE")

    def test_refusals_leave_no_trace(self):
        self.refused("NOTHING_TO_RECORD", lambda: self.create())
        self.refused("NOTHING_TO_RECORD", lambda: self.create("0", [(K21, "0")]))
        self.refused("LOT_NOT_FOUND", lambda: self.create(lots=[999999]))
        self.refused("LOT_DUPLICATE", lambda: self.create(lots=[self.lot, self.lot]))
        self.refused("SCRAP_SHORTAGE", lambda: self.create(rows=[(K21, "99999")], lots=[self.lot]))          # one bad source refuses all
        with self.assertRaises(PermissionError):
            self.create("100", lots=[self.lot], service=self.owner_like(MANAGER)[0])
        self.assertEqual(self.where(), (D("2.950"), {}))
        holder = self.custody()
        self.give_out(holder, [self.lot])
        self.refused("LOT_NOT_IN_SHOP", lambda: self.create("100", [(K21, "1")], [self.lot]))             # unavailable bar: cash/Scrap untouched
        self.legacy_partial_shop(holder)
        self.refused("LOT_PARTIAL_SHOP_STOCK", lambda: self.create(lots=[self.lot]))
        self.refused("LOT_PARTIAL_SHOP_STOCK", lambda: self.create("10", lots=[self.lot]))

    def test_a_supplied_weight_that_is_not_the_full_weight_is_refused(self):
        self.n += 1
        with self.assertRaises(TripError) as context:
            self.service.create_trip("", [], [{"lot_id": str(self.lot), "weight": "1.000"}], TODAY, "", key(1500))
        self.assertEqual(context.exception.code, "LOT_PARTIAL_NOT_ALLOWED")
        self.assertEqual(self.where(), (D("2.950"), {}))

    def test_a_failure_at_any_posting_stage_leaves_no_custody_or_partial_effect(self):
        before = self.state()
        for step in ("trip", "event", "entry", "cash", "scrap", "leg", "shop_movement", "lot_movement", "lot_issue", "audit"):
            self.db.fail_at = step
            with self.assertRaises(RuntimeError):
                self.create("100", [(K21, "1")], [self.lot])
            self.db.fail_at = None
            self.assertEqual(self.state(), before, f"a failure at {step} left a trace")
        for step in ("trip", "event", "entry", "shop_movement", "lot_movement", "lot_issue", "audit"):
            self.db.fail_at = step
            with self.assertRaises(RuntimeError):
                self.create(lots=[self.lot])
            self.db.fail_at = None
            self.assertEqual(self.state(), before, f"a bar-only failure at {step} left a trace")
        self.create("100", [(K21, "1")], [self.lot])
        self.assertEqual(len(self.db.lot_issues), 1)

    def test_idempotent_replay_returns_the_original_custody_and_every_entry_and_conflicts_are_refused(self):
        request = key(1600)
        first = self.create("100", [(K21, "1")], [self.lot], request=request)
        after = self.state()
        again = self.create("100", [(K21, "1")], [self.lot], request=request)
        self.assertEqual((again["duplicate"], again["trip_id"]), (True, first["trip_id"]))
        self.assertEqual([e["code"] for e in again["entries"]], [e["code"] for e in first["entries"]])
        self.assertEqual(self.state(), after)
        self.refused("REQUEST_CONFLICT", lambda: self.create("101", [(K21, "1")], [self.lot], request=request))

    def test_a_bar_only_replay_and_its_conflict(self):
        request = key(1700)
        first = self.create(lots=[self.lot], request=request)
        after = self.state()
        again = self.create(lots=[self.lot], request=request)
        self.assertEqual((again["duplicate"], again["trip_id"], len(again["entries"])), (True, first["trip_id"], 1))
        self.assertEqual(self.state(), after)
        self.refused("REQUEST_CONFLICT", lambda: self.create("1", lots=[self.lot], request=request))

    def test_the_created_entries_are_reversible_newest_first_and_the_guards_hold(self):
        done = self.create("100", [(K21, "1")], [self.lot])
        trip = done["trip_id"]
        issue_id, bar_id = (e["entry_id"] for e in done["entries"])
        self.give_back(trip, [self.lot])
        with self.assertRaises(Exception) as context:                                               # the bar's return depends on it
            self.service.reverse(bar_id, "x")
        self.assertEqual(getattr(context.exception, "code", None), "DEPENDENT_LOT_RETURN_EXISTS")
        return_id = str(max(self.db.entries))
        self.service.reverse(return_id, "x")
        self.service.reverse(bar_id, "x")
        self.assertEqual(self.where(), (D("2.950"), {}))
        self.service.reverse(issue_id, "x")                                                         # cash and Scrap back as well
        self.assertEqual(self.db.trips[int(trip)]["status"], "OPEN")


if __name__ == "__main__":
    unittest.main()
