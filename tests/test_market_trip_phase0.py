"""Phase 0 of the market-trip workflow: active / archive trip lists (OPEN / CLOSED, the existing status only), pagination, newest-first history and the
view-model behaviour around close / reopen, tab switches and late answers. Real MarketTripService / MarketTripViewModel over the in-memory transactional database
of tests/market_trip_fakes.py (no database, no SQL: the SQL of `trip_page` and of the ordered history is covered by the real-MySQL check proposed in
REVIEW-PHASE-0.md). Nothing here changes an accounting amount."""
from datetime import datetime, timedelta
from decimal import Decimal as D
import unittest

from services.market_trip_service import MarketTripService, TripError, TRIP_PAGE_SIZE
from tests.market_trip_fakes import MANAGER, OWNER, TripRepo, build, session_for

BASE = datetime(2026, 10, 1, 9, 0, 0)
TODAY = "2026-10-17"
KEYS = iter(range(1, 10 ** 6))


def key():
    return "55555555-5555-4555-8555-%012d" % next(KEYS)


class Case(unittest.TestCase):
    def setUp(self):
        self.db, self.session, self.authorization, self.service, self.corrections = build()

    def new_trip(self, minutes=None, cash="100"):
        trip = self.service.issue("", cash, [], TODAY, "", key())
        tid = int(trip["trip_id"])
        if minutes is not None:
            self.db.trips[tid]["created_at"] = BASE + timedelta(minutes=minutes)
        return tid

    def close(self, tid, minutes=None, cash="100"):
        self.service.return_custody(str(tid), cash, [], TODAY, "", key())
        self.service.close(str(tid))
        if minutes is not None:
            self.db.trips[tid]["status_changed_at"] = BASE + timedelta(minutes=minutes)

    def ids(self, page):
        return [int(r["id"]) for r in page["rows"]]


class ListFilterAndOrderTests(Case):
    def test_active_shows_open_trips_newest_created_first_with_the_id_as_tie_breaker(self):
        a, b, c, d = self.new_trip(10), self.new_trip(30), self.new_trip(30), self.new_trip(20)      # b and c share the same created_at
        self.close(a, 90)                                                                              # a leaves the active list
        page = self.service.trip_page("OPEN")
        self.assertEqual((page["status"], page["page"], page["has_more"]), ("OPEN", 1, False))
        self.assertEqual(self.ids(page), [c, b, d])                                                    # created_at DESC, id DESC; only OPEN
        self.assertTrue(all(r["status"] == "OPEN" for r in page["rows"]))

    def test_archive_shows_closed_trips_by_closing_time_then_id_and_a_reopened_trip_returns_to_active(self):
        a, b, c, d = self.new_trip(10), self.new_trip(20), self.new_trip(30), self.new_trip(40)
        self.close(a, 100)                                                                             # closing order differs from creation order
        self.close(c, 300)
        self.close(b, 300)                                                                             # same closing time as c: the id breaks the tie
        page = self.service.trip_page("CLOSED")
        self.assertEqual(self.ids(page), [c, b, a])                                                    # status_changed_at DESC, id DESC
        self.assertEqual(self.ids(self.service.trip_page("OPEN")), [d])
        self.service.reopen(str(b), "مراجعة")
        self.assertEqual(self.ids(self.service.trip_page("CLOSED")), [c, a])
        self.assertEqual(self.ids(self.service.trip_page("OPEN")), [d, b])                             # a reopened trip returns at its CREATED position: d (40) first, then b (20)

    def test_the_status_filter_is_applied_before_the_page_is_cut(self):
        open_ids = [self.new_trip(i) for i in range(60)]
        closed = [self.new_trip(1000 + i) for i in range(10)]                                          # the 10 NEWEST trips are closed ones
        for n, tid in enumerate(closed):
            self.close(tid, 2000 + n)
        first = self.service.trip_page("OPEN", 1, TRIP_PAGE_SIZE)
        self.assertEqual(len(first["rows"]), TRIP_PAGE_SIZE)                                           # not 40: no closed trip occupies an open page
        self.assertTrue(first["has_more"])
        self.assertEqual(self.ids(first), sorted(open_ids, reverse=True)[:TRIP_PAGE_SIZE])

    def test_the_legacy_unpaged_list_is_unchanged(self):
        a, b = self.new_trip(), self.new_trip()
        self.close(a)
        self.assertEqual([int(t["id"]) for t in self.service.trips()], [b, a])                         # every status, id DESC, as before

    def test_invalid_status_or_page_is_refused(self):
        for args in (("ARCHIVED", 1, 50), ("OPEN", 0, 50), ("OPEN", "x", 50), ("OPEN", 1, 0), ("OPEN", 1, 101)):
            with self.assertRaises(TripError) as refused:
                self.service.trip_page(*args)
            self.assertIn(refused.exception.code, ("STATUS_INVALID", "PAGE_INVALID"))

    def test_a_user_without_the_custody_right_cannot_read_a_page(self):
        weak = session_for(MANAGER)
        db, _, authorization, service, _ = build(permissions=MANAGER, session=weak)
        with self.assertRaises(PermissionError):
            service.trip_page("OPEN")


class PaginationTests(Case):
    def test_130_trips_are_all_reachable_without_a_100_row_cap_and_nothing_repeats(self):
        made = [self.new_trip(i) for i in range(130)]
        seen, page_no = [], 1
        while True:
            page = self.service.trip_page("OPEN", page_no, 50)
            seen += self.ids(page)
            self.assertEqual(len(page["rows"]), 50 if page["has_more"] else 130 - 100)
            if not page["has_more"]:
                break
            page_no += 1
        self.assertEqual((page_no, len(seen)), (3, 130))
        self.assertEqual(seen, sorted(made, reverse=True))                                             # continuous newest-first order across pages
        self.assertEqual(len(set(seen)), 130)
        self.assertEqual(self.ids(self.service.trip_page("OPEN", 4, 50)), [])                          # past the end: an empty page, not an error
        self.assertEqual(self.ids(self.service.trip_page("OPEN", 1, 100)), sorted(made, reverse=True)[:100])

    def test_the_old_trip_is_never_hidden_behind_newer_ones(self):
        oldest = self.new_trip(0)
        for i in range(1, 121):
            self.new_trip(i)
        self.assertIn(oldest, self.ids(self.service.trip_page("OPEN", 3, 50)))


class HistoryOrderTests(Case):
    def test_documents_melts_and_heats_are_newest_first_by_posting_time_then_id(self):
        db, service = self.db, self.service
        trip = service.issue("", "", [{"karat_id": "1", "weight": "30", "designation": "MELT"}], TODAY, "", key())
        tid = trip["trip_id"]
        service.issue(tid, "", [{"karat_id": "1", "weight": "10", "designation": "LOGO"}], TODAY, "", key())
        heat = service.heat(tid, [{"karat_id": "1", "input_weight": "5", "output_weight": "4.9"}], TODAY, "ws", "", "", "", key())
        service.melt(tid, [{"karat_id": "1", "weight": "20"}], TODAY, "ws", {"weight": "10", "fineness": "875"}, [], "assay", "", "", key())
        service.melt(tid, [{"karat_id": "1", "weight": "5"}], TODAY, "ws2", {"weight": "4.9", "fineness": "875"}, [], "assay", "", "", key())
        ids = sorted(db.entries)
        self.assertEqual(len(ids), 5)
        for n, eid in enumerate(ids):                                                                  # distinct posting times, then force a tie on the last two
            db.entries[eid]["occurred_at"] = BASE + timedelta(minutes=n)
        db.entries[ids[-1]]["occurred_at"] = db.entries[ids[-2]]["occurred_at"]
        detail = service.trip_detail(tid)
        order = [int(e["id"]) for e in detail["entries"]]
        self.assertEqual(order, sorted(ids, key=lambda i: (db.entries[i]["occurred_at"], i), reverse=True))
        self.assertEqual(order[:2], [ids[-1], ids[-2]])                                                # the tie is broken by the descending id
        self.assertEqual(sorted(e["code"] for e in detail["entries"]), [f"MT000001-{n}" for n in range(1, 6)])      # reference numbers are untouched
        melts = [int(m["entry_id"]) for m in detail["melts"]]
        self.assertEqual(melts, [ids[-1], ids[-2]])                                                    # both melts, newest first (equal times -> descending id)
        self.assertEqual([int(h["entry_id"]) for h in detail["heats"]], [int(heat["entry_id"])])
        # the same rows, only reordered
        asc = [int(e["id"]) for e in TripRepo(db).entries(int(tid))]                                  # the default (chronological, by entry_no) read
        self.assertEqual(asc, sorted(ids))
        self.assertEqual(sorted(asc), sorted(order))

    def test_status_events_are_newest_first(self):
        tid = self.new_trip()
        self.close(tid)
        self.service.reopen(str(tid), "مراجعة")
        for n, event in enumerate(self.db.events):
            event["occurred_at"] = BASE + timedelta(minutes=n)
        chronological = [e["event_type"] for e in sorted(self.db.events, key=lambda e: e["occurred_at"])]
        events = self.service.trip_detail(str(tid))["events"]
        self.assertEqual([e["type"] for e in events], list(reversed(chronological)))
        self.assertEqual(events[0]["type"], "REOPENED")
        times = [e["at"] for e in events]
        self.assertEqual(times, sorted(times, reverse=True))

    def test_ordering_changes_no_amount(self):
        tid = self.new_trip(cash="250")
        before = self.service.trip_detail(str(tid))["custody"]
        self.service.return_custody(str(tid), "100", [], TODAY, "", key())
        after = self.service.trip_detail(str(tid))["custody"]
        self.assertEqual((before["cash"]["outstanding"], after["cash"]["outstanding"]), (D("250.00"), D("150.00")))
        self.assertEqual(after["cash"]["issued"], D("250.00"))


class ViewModelCase(Case):
    """The real view model; only its worker start is replaced by an inline or a DEFERRED runner that keeps the view model's own request bookkeeping and
    calls its real `_completed`, so ordering, stale-answer and selection rules are the production ones."""

    def setUp(self):
        super().setUp()
        from app.viewmodels.market_trip_viewmodel import MarketTripViewModel
        self.vm = MarketTripViewModel(self.session, self.service)
        self.actor = self.session.current_actor()
        self.pending, self.deferred = [], False

        def start(operation, call):
            vm = self.vm
            vm._request += 1
            vm._latest[operation] = vm._request
            vm._busy.add(operation)
            item = (vm._request, operation, call)
            if self.deferred:
                self.pending.append(item)
            else:
                self.execute(item)
        self.vm._start = start

    def execute(self, item):
        request, operation, call = item
        try:
            result, error, code = call(_expected_actor=self.actor), "", ""
        except (ValueError, PermissionError) as exc:
            result, error, code = None, str(exc), getattr(exc, "code", "")
        self.vm._completed(request, str(self.actor.generation), operation, result, error, code, "")

    def complete(self, operation, which=0):
        item = [p for p in self.pending if p[1] == operation][which]
        self.pending.remove(item)
        self.execute(item)

    def shown(self):
        return [int(t["id"]) for t in self.vm.trips]


class ViewModelTabTests(ViewModelCase):
    def test_the_default_tab_is_active_and_the_archive_tab_shows_only_closed_trips(self):
        a, b, c = self.new_trip(10), self.new_trip(20), self.new_trip(30)
        self.close(a, 100)
        self.vm.refresh()
        self.assertEqual((self.vm.tripTab, self.shown()), ("ACTIVE", [c, b]))
        self.vm.selectTrip(str(b))
        self.vm.showTripTab("ARCHIVE")
        self.assertEqual((self.vm.tripTab, self.shown(), self.vm.selectedTripId, self.vm.detail), ("ARCHIVE", [a], "", {}))      # the selection belongs to its tab
        self.assertEqual([t["statusText"] for t in self.vm.trips], ["مغلقة"])
        self.assertNotEqual(self.vm.trips[0]["closedDate"], "")
        self.assertEqual(self.vm.trips[0]["id"], str(a))

    def test_an_archived_trip_keeps_its_full_detail_and_history(self):
        tid = self.new_trip(10)
        self.close(tid, 100)
        self.vm.refresh()
        self.vm.showTripTab("ARCHIVE")
        self.vm.selectTrip(str(tid))
        self.assertEqual((self.vm.detail["id"], self.vm.detail["open"]), (str(tid), False))
        entries = self.vm._detail["entries"]
        self.assertEqual([e["code"] for e in entries], ["MT000001-2", "MT000001-1"])                   # the return then the issue: newest first, references untouched
        self.assertEqual(self.vm.errorMessage, "")

    def test_a_successful_close_leaves_the_active_list_and_keeps_the_selection_and_detail(self):
        a, b = self.new_trip(10), self.new_trip(20)
        self.service.return_custody(str(a), "100", [], TODAY, "", key())
        self.vm.refresh()
        self.vm.selectTrip(str(a))
        self.vm.closeTrip(str(a))
        self.assertEqual(self.db.trips[a]["status"], "CLOSED")
        self.assertEqual(self.shown(), [b])                                                            # a left Active
        self.assertEqual((self.vm.selectedTripId, self.vm.detail["open"]), (str(a), False))            # the detail follows the database, not the list
        self.vm.showTripTab("ARCHIVE")
        self.assertEqual(self.shown(), [a])

    def test_a_successful_reopen_moves_the_trip_back_to_active(self):
        a, b = self.new_trip(10), self.new_trip(20)
        self.close(a, 100)
        self.vm.refresh()
        self.vm.showTripTab("ARCHIVE")
        self.vm.selectTrip(str(a))
        self.vm.reopenTrip(str(a), "مراجعة")
        self.assertEqual(self.db.trips[a]["status"], "OPEN")
        self.assertEqual(self.shown(), [])                                                             # gone from the archive list
        self.assertEqual((self.vm.selectedTripId, self.vm.detail["open"]), (str(a), True))
        self.vm.showTripTab("ACTIVE")
        self.assertEqual(self.shown(), [b, a])                                                         # at its created position

    def test_a_failed_close_or_reopen_moves_nothing(self):
        a, b = self.new_trip(10), self.new_trip(20)                                                    # the custody still holds cash: it cannot be closed
        self.vm.refresh()
        self.vm.selectTrip(str(a))
        before = self.vm.trips
        self.vm.closeTrip(str(a))
        self.assertEqual(self.db.trips[a]["status"], "OPEN")
        self.assertNotEqual(self.vm.errorMessage, "")
        self.assertEqual((self.vm.trips, self.vm.selectedTripId), (before, str(a)))
        self.vm.clearMessage()
        self.vm.reopenTrip(str(a), "x")                                                                # an OPEN trip cannot be reopened
        self.assertEqual(self.db.trips[a]["status"], "OPEN")
        self.assertEqual(self.shown(), [b, a])

    def test_a_missing_trip_clears_the_selection_but_an_absent_one_does_not(self):
        ids = [self.new_trip(i) for i in range(60)]
        self.vm.refresh()
        oldest = min(ids)
        self.vm.selectTrip(str(oldest))                                                                # the oldest is on page 2
        self.assertNotIn(oldest, self.shown())
        self.assertEqual(self.vm.selectedTripId, str(oldest))                                          # not on this page != nonexistent
        self.assertEqual(self.vm.detail["id"], str(oldest))
        self.vm.selectTrip("999999")
        self.assertEqual((self.vm.selectedTripId, self.vm.detail), ("", {}))


class ViewModelPagingTests(ViewModelCase):
    def test_pages_are_reachable_in_both_directions_beyond_100_trips(self):
        made = [self.new_trip(i) for i in range(130)]
        self.vm.refresh()
        every = []
        self.assertEqual((self.vm.tripPage, self.vm.tripHasNext, self.vm.tripHasPrevious), (1, True, False))
        while True:
            every += self.shown()
            if not self.vm.tripHasNext:
                break
            self.vm.nextTripPage()
        self.assertEqual((self.vm.tripPage, len(self.shown()), self.vm.tripHasPrevious), (3, 30, True))
        self.assertEqual(every, sorted(made, reverse=True))
        self.vm.previousTripPage()
        self.assertEqual((self.vm.tripPage, len(self.shown()), self.vm.tripHasNext), (2, 50, True))

    def test_closing_the_only_row_of_the_last_page_steps_back_one_page(self):
        ids = [self.new_trip(i) for i in range(51)]
        self.vm.refresh()
        self.vm.nextTripPage()
        oldest = min(ids)
        self.assertEqual((self.vm.tripPage, self.shown()), (2, [oldest]))
        self.service.return_custody(str(oldest), "100", [], TODAY, "", key())
        self.vm.selectTrip(str(oldest))
        self.vm.closeTrip(str(oldest))
        self.assertEqual((self.vm.tripPage, len(self.shown()), self.vm.tripHasNext), (1, 50, False))
        self.assertEqual(self.vm.selectedTripId, str(oldest))


class ViewModelCreateCustodyTests(ViewModelCase):
    def create(self):
        self.vm.createCustody("100", [], [], TODAY, "", key())

    def test_creating_from_the_archive_with_an_archived_trip_selected_shows_the_new_trip_in_active(self):
        old = self.new_trip(10)
        self.close(old, 50)
        self.vm.refresh()
        self.vm.showTripTab("ARCHIVE")
        self.vm.selectTrip(str(old))
        self.assertEqual(self.vm.selectedTripId, str(old))
        self.create()
        new = max(self.db.trips)
        self.assertEqual((self.vm.tripTab, self.vm.tripPage, self.vm.selectedTripId), ("ACTIVE", 1, str(new)))
        self.assertEqual(self.shown(), [new])
        self.assertEqual((self.vm.detail["id"], self.vm.detail["open"]), (str(new), True))

    def test_creating_with_an_active_trip_selected_on_a_later_page_selects_the_new_trip_on_page_1(self):
        ids = [self.new_trip(i) for i in range(60)]
        self.vm.refresh()
        self.vm.nextTripPage()
        oldest = min(ids)
        self.vm.selectTrip(str(oldest))
        self.assertEqual((self.vm.tripPage, self.vm.selectedTripId), (2, str(oldest)))
        self.create()
        new = max(self.db.trips)
        self.assertEqual((self.vm.tripTab, self.vm.tripPage, self.vm.selectedTripId), ("ACTIVE", 1, str(new)))
        self.assertEqual(self.shown()[0], new)
        self.assertEqual(self.vm.detail["id"], str(new))

    def test_a_failed_creation_keeps_the_tab_the_page_and_the_selection(self):
        old = self.new_trip(10)
        self.close(old, 50)
        self.vm.refresh()
        self.vm.showTripTab("ARCHIVE")
        self.vm.selectTrip(str(old))
        before = (self.vm.tripTab, self.vm.tripPage, self.vm.selectedTripId, self.vm.trips)
        trips = len(self.db.trips)
        self.vm.createCustody("", [], [], TODAY, "", key())                                            # nothing to issue: refused
        self.assertNotEqual(self.vm.errorMessage, "")
        self.assertEqual(len(self.db.trips), trips)
        self.assertEqual((self.vm.tripTab, self.vm.tripPage, self.vm.selectedTripId, self.vm.trips), before)
        self.assertEqual(self.vm.detail["id"], str(old))

    def test_an_operation_on_an_existing_trip_does_not_navigate(self):
        a = self.new_trip(10)
        self.vm.refresh()
        self.vm.showTripTab("ARCHIVE")                                                                 # nothing selected, Archive shown
        self.vm.issue(str(a), "10", [], TODAY, "", key())                                              # an entry on an existing trip
        self.assertEqual(self.vm.tripTab, "ARCHIVE")

    def test_late_answers_for_the_previous_view_cannot_overwrite_the_new_trip(self):
        old = self.new_trip(10)
        self.close(old, 50)
        self.vm.refresh()
        self.vm.showTripTab("ARCHIVE")
        self.vm.selectTrip(str(old))
        self.deferred = True
        self.vm.refresh()                                                                              # a list + detail for the ARCHIVE / old view stay in flight
        self.vm.createCustody("100", [], [], TODAY, "", key())
        self.complete("write", 0)                                                                      # the write answer arrives: navigation + newer requests
        new = max(self.db.trips)
        for operation in ("list", "detail"):                                                           # answers arrive in the worst order: stale first, then newer
            while [p for p in self.pending if p[1] == operation]:
                self.complete(operation, 0)
        self.assertEqual((self.vm.tripTab, self.vm.tripPage, self.vm.selectedTripId), ("ACTIVE", 1, str(new)))
        self.assertEqual(self.shown(), [new])
        self.assertEqual(self.vm.detail["id"], str(new))


class ViewModelLateAnswerTests(ViewModelCase):
    def test_a_late_list_for_the_previous_tab_is_never_shown(self):
        a, b = self.new_trip(10), self.new_trip(20)
        self.close(a, 100)
        self.deferred = True
        self.vm.refresh()                                                                              # asks for ACTIVE
        self.vm.showTripTab("ARCHIVE")                                                                 # asks for ARCHIVE; the ACTIVE ask is now stale
        self.complete("list", 1)                                                                       # the archive answer arrives first
        self.complete("list", 0)                                                                       # then the stale active answer
        self.assertEqual((self.vm.tripTab, self.shown()), ("ARCHIVE", [a]))

    def test_a_stale_answer_arriving_first_is_replaced_by_the_newer_one(self):
        a, b = self.new_trip(10), self.new_trip(20)
        self.close(a, 100)
        self.deferred = True
        self.vm.refresh()
        self.vm.showTripTab("ARCHIVE")
        self.complete("list", 0)
        self.complete("list", 0)
        self.assertEqual(self.shown(), [a])

    def test_a_page_for_another_tab_or_page_is_dropped_even_as_the_latest_request(self):
        a, b = self.new_trip(10), self.new_trip(20)
        self.close(a, 100)
        self.vm.refresh()
        self.vm.showTripTab("ARCHIVE")
        self.vm._on_list(self.service.trip_page("OPEN", 1, 50), "")                                    # an ACTIVE page handed to the ARCHIVE tab
        self.assertEqual(self.shown(), [a])
        self.vm._on_list({**self.service.trip_page("CLOSED", 1, 50), "page": 2}, "")                   # a different page
        self.assertEqual(self.shown(), [a])

    def test_a_late_page_answer_is_ignored_after_the_user_changed_page(self):
        for i in range(130):
            self.new_trip(i)
        self.vm.refresh()
        self.deferred = True
        self.vm.nextTripPage()                                                                         # page 2 asked
        self.vm.previousTripPage()                                                                     # back to page 1 asked
        self.complete("list", 0)                                                                       # the page-2 answer arrives late
        self.assertEqual((self.vm.tripPage, len(self.shown())), (1, 0))                                # nothing from page 2 appears under page 1
        self.complete("list", 0)
        self.assertEqual((self.vm.tripPage, len(self.shown())), (1, 50))

    def test_a_late_detail_for_another_trip_is_ignored(self):
        a, b = self.new_trip(10), self.new_trip(20)
        self.deferred = True
        self.vm.selectTrip(str(a))
        self.vm.selectTrip(str(b))
        self.complete("detail", 0)
        self.complete("detail", 0)
        self.assertEqual(self.vm.detail["id"], str(b))


class ViewModelRightsTests(unittest.TestCase):
    def test_without_the_custody_right_nothing_is_asked(self):
        from app.viewmodels.market_trip_viewmodel import MarketTripViewModel
        db, session, authorization, service, _ = build(permissions=MANAGER)
        vm = MarketTripViewModel(session, service)
        calls = []
        vm._start = lambda operation, call: calls.append(operation)
        vm.refresh()
        self.assertEqual((vm.canManage, calls, vm.trips), (False, [], []))
        vm.showTripTab("ARCHIVE")
        vm.nextTripPage()
        self.assertEqual(calls, [])


if __name__ == "__main__":
    unittest.main()
