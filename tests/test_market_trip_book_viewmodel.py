"""The BOOK part of the market-trip view model: the real MarketTripViewModel over the real MarketTripBookService and the in-memory database of the market-trip fakes
(its worker start is replaced by an inline runner that keeps the view model's own request bookkeeping and calls its real `_completed`). Covers the dialog preview (the
same pure rules as the posting), the write slots, the detail / list presentation of a BOOK trip, the blocked closure and the inactive creation path. No database."""
from unittest.mock import patch
import unittest

from app.viewmodels.market_trip_viewmodel import MarketTripViewModel
from tests.market_trip_fakes import AccountsRepo, HoldingRepo, SupplierPayRepo, TripRepo, build
from tests.test_market_trip_book import K18, K21, PRICES, TODAY, assay, karat, key


def row(basis, karat_id="", fineness="", weight="", price=""):
    return {"basis": basis, "karatId": karat_id, "fineness": fineness, "weight": weight, "price": price}


def inline_vm(session, service):
    """The real view model with an inline worker: the request bookkeeping and `_completed` are the production ones."""
    vm = MarketTripViewModel(session, service)
    actor = session.current_actor()
    posted = []
    vm.entryPosted.connect(posted.append)

    def start(operation, call):
        vm._request += 1
        vm._latest[operation] = vm._request
        vm._busy.add(operation)
        try:
            result, error, code = call(_expected_actor=actor), "", ""
        except (ValueError, PermissionError) as exc:
            result, error, code = None, str(exc), getattr(exc, "code", "")
        vm._completed(vm._request, str(actor.generation), operation, result, error, code, "")
    vm._start = start
    return vm, posted


class BookViewModelCase(unittest.TestCase):
    def setUp(self):
        self.db, self.session, self.authorization, self.service, self.corrections = build(book=True)
        self.service._price_reference = lambda: dict(PRICES)
        for target, value in (("repositories.market_trip_repository.MarketTripRepository", TripRepo),
                              ("repositories.financial_account_repository.FinancialAccountRepository", AccountsRepo),
                              ("repositories.supplier_payment_repository.SupplierPaymentRepository", SupplierPayRepo),
                              ("repositories.intermediary_holding_repository.IntermediaryHoldingRepository", HoldingRepo)):
            started = patch(target, value)
            started.start()
            self.addCleanup(started.stop)
        self.vm, self.posted = inline_vm(self.session, self.service)
        self.n = 0

    def k(self):
        self.n += 1
        return key(self.n + 900)

    def book_trip(self, weight="20", cash="100000"):
        trip = self.service.book_issue("", [karat(K21, weight)], TODAY, "", self.k())["trip_id"]
        if cash:
            self.service.issue(trip, cash, [], TODAY, "", self.k())
        self.vm.refresh()
        self.vm.selectTrip(trip)
        return trip


class PreviewTests(BookViewModelCase):
    def test_the_preview_states_the_posted_figures_the_service_will_post(self):
        trip = self.book_trip()
        preview = self.vm.previewBook("BK_BUY", [row("FINENESS", fineness="916.7", weight="5", price="32000"), row("MASTER_KARAT", K18, weight="4", price="20000")])
        self.assertTrue(preview["valid"])
        self.assertIn("5.238", preview["lines"][0])
        self.assertIn("3.429", preview["lines"][1])
        self.assertIn("8.667", preview["totalPosted"])                                                      # 5.238 + 3.429: the document is the SUM of its posted lines
        self.assertIn("52,000.00", preview["totalText"])
        done = self.service.book_buy(trip, [assay("916.7", "5", "32000"), karat(K18, "4", "20000")], "", "تاجر", TODAY, "", "", self.k(), False)
        self.assertEqual(done["document_posted"], "8.667")                                                   # the preview and the posting agree

    def test_the_preview_refuses_what_the_service_refuses_with_the_same_wording(self):
        trip = self.book_trip("10")
        preview = self.vm.previewBook("BK_RETURN", [row("MASTER_KARAT", K21, weight="10.001")])
        self.assertFalse(preview["valid"])
        with self.assertRaises(Exception) as refused:
            self.service.book_return(trip, [karat(K21, "10.001")], TODAY, "", self.k())
        self.assertEqual(preview["error"], str(refused.exception))
        cash = self.vm.previewBook("BK_BUY", [row("MASTER_KARAT", K21, weight="1", price="6000")])
        self.assertTrue(cash["valid"])
        broke = self.vm.previewBook("BK_BUY", [row("MASTER_KARAT", K21, weight="10", price="110000")])
        self.assertFalse(broke["valid"])                                                                     # more than the custody cash
        self.assertIn("المتاح من نقدية العهدة", broke["error"])

    def test_the_preview_warns_about_a_price_far_from_the_reference_and_never_about_a_rounding_residual(self):
        self.book_trip()
        self.vm.loadReferencePrices()                                                                        # the dialog loads the SHOP_BUY reference when it opens
        far = self.vm.previewBook("BK_SELL", [row("MASTER_KARAT", K21, weight="10", price="90000")])
        self.assertTrue(far["warning"] and far["valid"])
        self.assertIn("%", far["deviationText"])
        trip = self.service.book_issue("", [karat(K18, "10")], TODAY, "", self.k())["trip_id"]
        for weight in ("3.333", "3.333"):
            self.service.book_return(trip, [karat(K18, weight)], TODAY, "", self.k())
        self.vm.selectTrip(trip)
        last = self.vm.previewBook("BK_RETURN", [row("MASTER_KARAT", K18, weight="3.334")])
        self.assertEqual((last["valid"], last["error"]), (True, ""))                                         # analytically available: accepted although the POSTED book dips
        again = self.vm.previewBook("BK_RETURN", [row("MASTER_KARAT", K18, weight="3.334"), row("MASTER_KARAT", K18, weight="0.001")])
        self.assertFalse(again["valid"])

    def test_an_untouched_dialog_shows_no_error_and_a_half_filled_row_is_explained(self):
        self.book_trip()
        self.assertEqual(self.vm.previewBook("BK_RETURN", [row("MASTER_KARAT")])["error"], "")
        self.assertIn("الوزن", self.vm.previewBook("BK_RETURN", [row("MASTER_KARAT", K21)])["error"])
        self.assertFalse(self.vm.previewBook("BK_RETURN", [])["valid"])


class SlotAndPresentationTests(BookViewModelCase):
    def test_the_four_slots_post_through_the_service_and_the_detail_follows_the_database(self):
        trip = self.book_trip()
        self.vm.bookBuy(trip, [row("FINENESS", fineness="916.7", weight="5", price="32000")], "", "تاجر السوق", TODAY, "", "", self.k(), False)
        self.assertEqual(self.vm.errorMessage, "")
        self.vm.bookSell(trip, [row("MASTER_KARAT", K21, weight="6", price="36000")], "", "مشتري", TODAY, "", "", self.k(), False)
        self.vm.bookReturn(trip, [row("MASTER_KARAT", K21, weight="4"), row("FINENESS", fineness="866.7", weight="1.8")], TODAY, "", self.k())
        self.vm.bookIssue(trip, [row("MASTER_KARAT", K18, weight="10")], TODAY, "", self.k())
        self.assertEqual(len(self.posted), 4)
        detail = self.vm.detail
        self.assertTrue(detail["isBook"])
        self.assertEqual(detail["book"]["posted"], "22.026")                                                 # 20 + 5.238 - 6 - 4 - 1.783 + 8.571
        self.assertIn("1.800", detail["book"]["stockItems"][0]["text"])
        self.assertIn("ليس مخزونًا فعليًا", detail["book"]["note"])
        what = " ".join(e["what"] for e in detail["entries"])
        self.assertIn("مرحَّل", what)
        self.assertIn("مخزون مرتجع بعيار فعلي", what)
        self.assertIn("كسر المحل", what)

    def test_a_refusal_keeps_the_dialog_state_and_writes_nothing(self):
        trip = self.book_trip("5")
        before = len(self.db.entries)
        self.vm.bookReturn(trip, [row("MASTER_KARAT", K21, weight="6")], TODAY, "", self.k())
        self.assertEqual(self.vm.errorCode, "BOOK_EXCEEDS_BALANCE")
        self.assertIn("الرصيد الدفتري المتاح", self.vm.errorMessage)
        self.assertEqual((len(self.db.entries), self.posted), (before, []))

    def test_a_book_trip_cannot_be_closed_and_says_why(self):
        trip = self.book_trip("5", cash="")
        self.service.book_return(trip, [karat(K21, "5")], TODAY, "", self.k())
        self.vm.refresh()
        self.vm.selectTrip(trip)
        detail = self.vm.detail
        self.assertEqual((detail["closable"], detail["isBook"]), (False, True))
        self.assertIn("اعتماد تسوية", detail["closeHint"])
        self.vm.closeTrip(trip)
        self.assertEqual(self.vm.errorCode, "RECONCILIATION_REQUIRED")
        self.assertEqual(self.db.trips[int(trip)]["status"], "OPEN")

    def test_the_list_marks_a_book_trip_and_shows_its_balance(self):
        trip = self.book_trip("20", cash="1000")
        legacy = self.service.issue("", "500", [], TODAY, "", self.k())["trip_id"]
        self.vm.refresh()
        rows = {t["id"]: t for t in self.vm.trips}
        self.assertEqual((rows[trip]["modeText"], rows[legacy]["modeText"]), ("دفتري", ""))
        self.assertIn("رصيد دفتري 20.000", rows[trip]["outstandingText"])
        self.assertIn("نقدية", rows[trip]["outstandingText"])
        self.assertNotIn("رصيد دفتري", rows[legacy]["outstandingText"])

    def test_a_legacy_trip_has_no_book_part_and_keeps_its_legacy_actions(self):
        legacy = self.service.issue("", "500", [{"karat_id": K21, "weight": "10", "designation": "MELT"}], TODAY, "", self.k())["trip_id"]
        self.vm.refresh()
        self.vm.selectTrip(legacy)
        detail = self.vm.detail
        self.assertEqual((detail["isBook"], detail["book"], detail["modeText"]), (False, {}, "قديم"))
        self.assertTrue(detail["meltAvailable"])
        self.assertEqual(self.vm.previewBook("BK_RETURN", [row("MASTER_KARAT", K21, weight="1")])["valid"], False)       # no book on a legacy trip

    def test_the_book_creation_stays_inactive_for_the_production_construction(self):
        db, session, authorization, service, corrections = build()                                           # the service exactly as production builds it
        vm, posted = inline_vm(session, service)
        before = (dict(db.trips), dict(db.entries), list(db.scrap))
        vm.bookIssue("", [row("MASTER_KARAT", K21, weight="1")], TODAY, "", self.k())                       # an issue with NO trip id is the only creation path
        self.assertEqual(vm.errorCode, "BOOK_NOT_ACTIVE")
        self.assertEqual((dict(db.trips), dict(db.entries), list(db.scrap), posted), (before[0], before[1], before[2], []))
        default = MarketTripViewModel(self.session)._service_instance()                                     # the default construction of the view model
        self.assertFalse(default._book_creation_enabled)
        for name in dir(MarketTripViewModel):                                                                # no slot takes a mode or an activation
            self.assertNotIn("mode", name.lower().replace("modetext", ""))


if __name__ == "__main__":
    unittest.main()
