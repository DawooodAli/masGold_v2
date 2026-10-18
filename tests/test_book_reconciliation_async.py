"""R1 follow-up (F1): the asynchronous behaviour of the reconciliation preview in MarketTripViewModel, driven through the REAL `_start` / `_completed` bookkeeping with a
CONTROLLABLE stand-in for InvoiceWorker: every request is held until the test delivers its answer (success or failure) in the order the test chooses - no thread, no sleep,
no timing. Covers a late error of trip A reaching trip B, a change to a LEGACY trip or a tab while a request is in flight, a refresh of the same trip, out-of-order answers,
detail / preview failures, and the session / shutdown guards. No database (fakes)."""
import unittest
from unittest.mock import patch

from app.viewmodels.market_trip_viewmodel import MarketTripViewModel
from tests.market_trip_fakes import AccountsRepo, HoldingRepo, SupplierPayRepo, TripRepo, build
from tests.test_market_trip_book import K18, K21, PRICES, TODAY, karat, key


class FakeSignal:
    def __init__(self):
        self.slots = []

    def connect(self, slot):
        self.slots.append(slot)


class ControlledWorker:
    """Stands in for app.workers.invoice_worker.InvoiceWorker: it records the request and answers ONLY when the test says so, through the view model's own `_completed`."""
    created = []

    def __init__(self, call, session, actor, request, operation, parent=None):
        self.call, self.session, self.actor, self.request, self.operation = call, session, actor, request, operation
        self.completed, self.finished = FakeSignal(), FakeSignal()
        self.delivered = False
        ControlledWorker.created.append(self)

    def start(self):
        pass

    def wait(self, milliseconds=0):
        return True

    def deliver(self, vm, *, result=None, error="", code="", run=False, generation=None):
        if run:
            try:
                result = self.call(_expected_actor=self.actor)
            except (ValueError, PermissionError) as exc:
                result, error, code = None, str(exc), getattr(exc, "code", "")
        self.delivered = True
        vm._completed(self.request, str(self.actor.generation) if generation is None else generation, self.operation, result, error, code, "")
        for slot in self.finished.slots:
            slot()


class AsyncCase(unittest.TestCase):
    def setUp(self):
        ControlledWorker.created = []
        self.db, self.session, self.authorization, self.service, self.corrections = build(book=True)
        self.service._price_reference = lambda: dict(PRICES)
        for target, value in (("repositories.market_trip_repository.MarketTripRepository", TripRepo),
                              ("repositories.financial_account_repository.FinancialAccountRepository", AccountsRepo),
                              ("repositories.supplier_payment_repository.SupplierPaymentRepository", SupplierPayRepo),
                              ("repositories.intermediary_holding_repository.IntermediaryHoldingRepository", HoldingRepo),
                              ("app.workers.invoice_worker.InvoiceWorker", ControlledWorker)):
            started = patch(target, value)
            started.start()
            self.addCleanup(started.stop)
        self.vm = MarketTripViewModel(self.session, self.service)
        self.n = 0
        self.a = self.service.book_issue("", [karat(K21, "20")], TODAY, "", self.k())["trip_id"]
        self.b = self.service.book_issue("", [karat(K18, "10")], TODAY, "", self.k())["trip_id"]
        self.legacy = self.service.issue("", "500", [], TODAY, "", self.k())["trip_id"]

    def k(self):
        self.n += 1
        return key(self.n + 1200)

    # ------------------------------------------------------------ helpers
    def pending(self, operation):
        return [w for w in ControlledWorker.created if w.operation == operation and not w.delivered]

    def latest(self, operation):
        found = self.pending(operation)
        self.assertTrue(found, f"no pending {operation} request")
        return found[-1]

    def run_detail(self):
        self.latest("detail").deliver(self.vm, run=True)

    def run_recon(self):
        self.latest("recon").deliver(self.vm, run=True)

    def load(self, trip):
        """selectTrip + the real answers of detail and preview (run against the fake database)."""
        self.vm.selectTrip(trip)
        self.run_detail()
        self.run_recon()

    def card(self):
        return {k: v for k, v in self.vm.reconciliation.items() if k != "loading"}

    def loading(self):
        return self.vm.reconciliation["loading"]


class StaleAnswerTests(AsyncCase):
    def test_a_late_error_of_trip_a_never_reaches_trip_b(self):
        self.vm.selectTrip(self.a)
        self.run_detail()
        stale_a = self.latest("recon")                                                       # recon(A) is in flight
        self.vm.selectTrip(self.b)                                                           # the user moves to B; detail(B) is pending, recon(B) not yet asked for
        self.assertEqual(self.card(), {})
        stale_a.deliver(self.vm, error="late error from A", code="X")                        # A's failure arrives BEFORE detail(B)
        self.assertEqual(self.card(), {})                                                    # no error attributed to B
        self.assertNotIn("recon", self.vm._busy)
        self.run_detail()                                                                    # detail(B)
        self.run_recon()
        self.assertIn("8.571429", self.vm.reconciliation["aText"])
        self.assertNotIn("error", self.vm.reconciliation)
        self.assertFalse(self.vm.busy and bool({"recon", "detail"} & self.vm._busy))

    def test_a_late_success_of_trip_a_never_reaches_trip_b_either(self):
        self.vm.selectTrip(self.a)
        self.run_detail()
        stale_a = self.latest("recon")
        self.vm.selectTrip(self.b)
        stale_a.deliver(self.vm, run=True)
        self.assertEqual(self.card(), {})
        self.run_detail()
        self.assertEqual(self.card(), {})                                                    # B's own preview has not been asked for / answered yet
        self.run_recon()
        self.assertIn("8.571429", self.vm.reconciliation["aText"])

    def test_changing_to_a_legacy_trip_while_a_request_is_in_flight_shows_nothing_and_leaves_nothing_busy(self):
        self.vm.selectTrip(self.a)
        self.run_detail()
        stale_a = self.latest("recon")
        self.vm.selectTrip(self.legacy)
        stale_a.deliver(self.vm, error="late error from A", code="X")
        self.assertEqual(self.card(), {})                                                    # BEFORE detail(LEGACY) arrives: A's failure is not shown for the new selection
        self.run_detail()                                                                    # detail(LEGACY): no preview is ever asked for
        self.assertEqual(self.pending("recon"), [])
        self.assertEqual(self.card(), {})
        self.assertEqual(self.vm.errorMessage, "")
        self.assertFalse({"recon", "detail"} & self.vm._busy)
        self.assertFalse(self.loading())

    def test_changing_the_tab_while_a_request_is_in_flight(self):
        self.vm.selectTrip(self.a)
        self.run_detail()
        stale_a = self.latest("recon")
        self.vm.showTripTab("ARCHIVE")
        stale_a.deliver(self.vm, run=True)
        self.assertEqual(self.card(), {})
        self.assertNotIn("recon", self.vm._busy)
        self.assertEqual(self.vm.detail, {})                                                 # the selection belongs to the tab it was made in
        self.assertEqual(self.pending("recon"), [])

    def test_a_refresh_of_the_same_trip_does_not_present_the_previous_preview_as_the_latest(self):
        self.load(self.a)
        self.assertIn("20.000000", self.vm.reconciliation["aText"])
        self.service.book_return(self.a, [karat(K21, "5")], TODAY, "", self.k())
        self.vm.refresh()                                                                    # detail(A) pending: the old numbers must not stay as «current»
        card = self.vm.reconciliation
        self.assertNotIn("aText", card)
        self.assertNotIn("statusText", card)
        self.assertTrue(card["loading"])
        self.run_detail()
        self.assertTrue(self.vm.reconciliation["loading"])                                   # preview(A) is now asked for
        self.assertNotIn("aText", self.vm.reconciliation)
        self.run_recon()
        self.assertIn("15.000000", self.vm.reconciliation["aText"])
        self.assertFalse(self.loading())

    def test_an_answer_of_an_older_refresh_does_not_bring_the_old_preview_back(self):
        self.vm.selectTrip(self.a)
        self.run_detail()
        old = self.latest("recon")                                                           # asked for before the movement
        old_view = self.service.reconciliation_preview(self.a)                               # what it would show: 20.000000
        self.service.book_return(self.a, [karat(K21, "5")], TODAY, "", self.k())
        self.vm.refresh()                                                                    # a newer cycle starts: the old request is cancelled
        old.deliver(self.vm, result=old_view)
        self.assertNotIn("aText", self.vm.reconciliation)                                    # not restored
        self.run_detail()
        newest = self.latest("recon")
        old.deliver(self.vm, result=old_view)                                                # delivered again, after the newer request exists: still ignored
        self.assertNotIn("aText", self.vm.reconciliation)
        newest.deliver(self.vm, run=True)
        self.assertIn("15.000000", self.vm.reconciliation["aText"])

    def test_the_newest_answer_wins_in_either_delivery_order_success_or_failure(self):
        for order in ("newest_first", "oldest_first"):
            for old_outcome in ("success", "failure"):
                with self.subTest(order=order, old=old_outcome):
                    ControlledWorker.created = []
                    self.vm = MarketTripViewModel(self.session, self.service)
                    self.vm.selectTrip(self.a)
                    self.run_detail()
                    old = self.latest("recon")
                    old_view = self.service.reconciliation_preview(self.a)
                    self.vm.refresh()
                    self.run_detail()
                    newest = self.latest("recon")
                    deliveries = [lambda: newest.deliver(self.vm, run=True),
                                  lambda: old.deliver(self.vm, result=old_view) if old_outcome == "success" else old.deliver(self.vm, error="old failure", code="X")]
                    for deliver in (deliveries if order == "newest_first" else reversed(deliveries)):
                        deliver()
                    card = self.vm.reconciliation
                    self.assertIn("20.000000", card["aText"])
                    self.assertNotIn("error", card)
                    self.assertFalse(self.loading())


class FailureTests(AsyncCase):
    def test_a_failing_detail_shows_a_plain_failure_without_an_old_suggestion_and_a_later_success_restores_the_card(self):
        self.load(self.a)
        self.assertTrue(self.vm.reconciliation["lossAvailable"])
        self.vm.refresh()
        self.latest("detail").deliver(self.vm, error="الخادم غير متاح", code="DB")
        card = self.vm.reconciliation
        self.assertIn("تعذّر تحديث تفاصيل العهدة", card["error"])
        self.assertNotIn("aText", card)
        self.assertNotIn("lossText", card)                                                   # no old suggestion is kept as a trusted result
        self.assertFalse(card["loading"])
        self.vm.refresh()
        self.run_detail()
        self.run_recon()
        card = self.vm.reconciliation
        self.assertNotIn("error", card)
        self.assertIn("20.000000", card["aText"])

    def test_a_failing_preview_shows_a_plain_failure_and_a_later_success_restores_the_card(self):
        self.vm.selectTrip(self.a)
        self.run_detail()
        self.latest("recon").deliver(self.vm, error="انقطع الاتصال", code="DB")
        card = self.vm.reconciliation
        self.assertIn("تعذّر تحميل مطابقة العهدة", card["error"])
        self.assertNotIn("aText", card)
        self.assertFalse(card["loading"])
        self.vm.refresh()
        self.run_detail()
        self.run_recon()
        self.assertIn("20.000000", self.vm.reconciliation["aText"])
        self.assertNotIn("error", self.vm.reconciliation)

    def test_a_missing_trip_clears_the_selection_and_the_card(self):
        self.vm.selectTrip(self.a)
        self.latest("detail").deliver(self.vm, error="العهدة غير موجودة.", code="TRIP_NOT_FOUND")
        self.assertEqual((self.vm._selected, self.card()), ("", {}))


class GuardTests(AsyncCase):
    def test_an_answer_of_another_session_generation_is_ignored(self):
        self.vm.selectTrip(self.a)
        self.run_detail()
        worker = self.latest("recon")
        worker.deliver(self.vm, run=True, generation="a-previous-session")
        self.assertEqual(self.card(), {})                                                    # the project's generation guard is untouched
        worker.delivered = False
        worker.deliver(self.vm, run=True)                                                    # the same request with the CURRENT generation is accepted
        self.assertIn("20.000000", self.vm.reconciliation["aText"])

    def test_after_shutdown_nothing_is_started_or_shown(self):
        self.vm.selectTrip(self.a)
        self.run_detail()
        worker = self.latest("recon")
        self.vm.shutdown()
        worker.deliver(self.vm, run=True)
        self.assertEqual(self.card(), {})
        before = len(ControlledWorker.created)
        self.vm.refresh()
        self.assertEqual(len(ControlledWorker.created), before)                              # _start refuses once shut down

    def test_without_a_signed_in_actor_nothing_is_shown(self):
        self.vm.selectTrip(self.a)
        self.run_detail()
        worker = self.latest("recon")
        with patch.object(self.session, "current_actor", return_value=None):
            worker.deliver(self.vm, run=True)
        self.assertEqual(self.card(), {})


if __name__ == "__main__":
    unittest.main()
