"""BOOK core of a market trip (Phase 1, migration 056): the real MarketTripBookService / MarketTripService / CorrectionService over the in-memory transactional
database of tests/market_trip_fakes.py (a failed transaction restores every table; the 056 CHECK / unique / composite-key rules are mirrored as asserts). NOT
real-MySQL tests: the SQL, the constraints and the races are covered by the static schema test and the opt-in rehearsal test (prepared, not run).

Covers: analytical vs posted figures and rounding-only residuals, the exact Scrap / cash / stock effects and conservation, insufficient analytical availability,
mode isolation and unsupported-operation refusal, replay / conflict / authorization / audit-failure rollback, canonical reversals with stored figures, and the
fact that normal creation stays LEGACY and BOOK stays inactive."""
import copy
from decimal import Decimal as D
from fractions import Fraction
from pathlib import Path
import re
from unittest.mock import patch
import unittest

from services import book_rules as br
from services.correction_service import CorrectionError, CorrectionService
from services.market_trip_book_service import MarketTripBookService
from services.market_trip_service import TripError
from repositories.request_keys import RequestKeyInUse
from tests.auth_fakes import FakeAuthorization
from tests.market_trip_fakes import (AccountsRepo, Corrections, Customers, HoldingRepo, Invoices, OWNER, ScrapRepo, SettlementRepo, SupplierPayRepo, TripRepo, build,
                                     session_for)

ROOT = Path(__file__).resolve().parents[1]
TODAY = "2026-10-10"
K21, K18, K24 = "1", "2", "3"
PRICES = {21: D("6000.0000"), 18: D("5142.8571"), 24: D("7000.0000")}


def key(n):
    return "77777777-7777-4777-8777-%012d" % n


def karat(karat_id, weight, price=None):
    row = {"basis": "MASTER_KARAT", "karat_id": karat_id, "weight": weight}
    if price is not None:
        row["price"] = price
    return row


def assay(permille, weight, price=None):
    row = {"basis": "FINENESS", "fineness": permille, "weight": weight}
    if price is not None:
        row["price"] = price
    return row


class BookCase(unittest.TestCase):
    """A BOOK-capable service (the way a test, or the future release step, builds it) over the fake database."""

    def setUp(self):
        self.db, self.session, self.authorization, self.service, self.corrections = build(book=True)
        self.service._price_reference = lambda: dict(PRICES)
        self.n = 0
        for target, value in (("repositories.market_trip_repository.MarketTripRepository", TripRepo),
                              ("repositories.supplier_payment_repository.SupplierPaymentRepository", SupplierPayRepo),
                              ("repositories.financial_account_repository.FinancialAccountRepository", AccountsRepo),
                              ("repositories.intermediary_holding_repository.IntermediaryHoldingRepository", HoldingRepo)):
            started = patch(target, value)
            started.start()
            self.addCleanup(started.stop)

    # ------------------------------------------------------------ helpers
    def k(self):
        self.n += 1
        return key(self.n)

    def issue(self, rows=None, trip="", **kw):
        return self.service.book_issue(trip, rows if rows is not None else [karat(K21, "20")], TODAY, kw.get("notes", ""), kw.get("request") or self.k())

    def buy(self, trip, rows=None, party="", name="تاجر السوق", ack=False, **kw):
        return self.service.book_buy(trip, rows if rows is not None else [assay("916.7", "5", "32000")], party, name, TODAY, kw.get("reference", ""), "",
                                     kw.get("request") or self.k(), ack)

    def sell(self, trip, rows=None, party="", name="مشتري", ack=False, **kw):
        return self.service.book_sell(trip, rows if rows is not None else [karat(K21, "6", "36000")], party, name, TODAY, "", "", kw.get("request") or self.k(), ack)

    def ret(self, trip, rows=None, **kw):
        return self.service.book_return(trip, rows if rows is not None else [karat(K21, "5")], TODAY, "", kw.get("request") or self.k())

    def cash(self, trip, amount="100000"):
        return self.service.issue(trip, amount, [], TODAY, "", self.k())

    def trip(self):
        return self.issue()["trip_id"]

    def detail(self, trip):
        return self.service.trip_detail(trip)

    def balance(self, trip):
        return self.detail(trip)["book"]["balance"]

    def custody_cash(self, trip):
        return self.detail(trip)["custody"]["cash"]

    def state(self):
        return copy.deepcopy({k: v for k, v in vars(self.db).items() if k not in ("fail_at", "transactions", "lock_log")})

    def refused(self, code, call, error=TripError):
        before = self.state()
        with self.assertRaises(error) as context:
            call()
        self.assertEqual(getattr(context.exception, "code", None), code)
        self.assertEqual(self.state(), before, "a refused request left a trace")
        return context.exception

    def other_service(self, permissions=OWNER, user_id=9):
        session = session_for(permissions, user_id)
        authorization = FakeAuthorization(session)
        corrections = CorrectionService(session, authorization=authorization, transaction_factory=self.db.transaction, invoice_repository=Invoices,
                                        correction_repository=Corrections, customer_repository=Customers)
        service = MarketTripBookService(session, transaction_factory=self.db.transaction, repository=TripRepo, accounts=AccountsRepo, scrap=ScrapRepo,
                                        authorization=authorization, corrections=corrections, supplier_payments=SupplierPayRepo, gold_settlements=SettlementRepo,
                                        holdings=HoldingRepo, book_creation_enabled=True)
        return service, authorization

    def scrap_rows(self, entry_id):
        return [m for m in self.db.scrap if m["reference_type"] == "MARKET_TRIP_ENTRY" and m["reference_id"] == str(entry_id)]


# --------------------------------------------------------------------------- creation stays LEGACY, BOOK stays inactive
class InactiveByDefaultTests(unittest.TestCase):
    def setUp(self):
        self.db, self.session, self.authorization, self.service, self.corrections = build()                  # production behaviour: book=False
        self.n = 0
        for target, value in (("repositories.market_trip_repository.MarketTripRepository", TripRepo),
                              ("repositories.financial_account_repository.FinancialAccountRepository", AccountsRepo)):
            started = patch(target, value)
            started.start()
            self.addCleanup(started.stop)

    def test_normal_creation_is_legacy_and_the_book_path_is_refused_without_writing(self):
        trip = self.service.issue("", "1000", [], TODAY, "", key(1))
        self.assertEqual(self.db.trips[int(trip["trip_id"])]["accounting_mode"], "LEGACY")
        created = self.service.create_trip("500", [], [], TODAY, "", key(2))
        self.assertEqual(self.db.trips[int(created["trip_id"])]["accounting_mode"], "LEGACY")
        before = copy.deepcopy((self.db.trips, self.db.entries, self.db.scrap, self.db.cash_moves, self.db.book_lines))
        with self.assertRaises(TripError) as refused:
            self.service.book_issue("", [karat(K21, "5")], TODAY, "", key(3))
        self.assertEqual(refused.exception.code, "BOOK_NOT_ACTIVE")
        self.assertEqual(before, copy.deepcopy((self.db.trips, self.db.entries, self.db.scrap, self.db.cash_moves, self.db.book_lines)))

    def test_a_service_built_the_normal_way_cannot_create_a_book_trip_and_the_flag_is_not_an_input(self):
        self.assertFalse(self.service._book_creation_enabled)
        import inspect
        for name in ("issue", "create_trip", "book_issue"):
            self.assertNotIn("mode", inspect.signature(getattr(self.service, name)).parameters)              # no mode / activation argument anywhere

    def test_no_production_caller_enables_the_book_creation(self):
        """The ONLY places that name the enabling flag are the service that defines it and the tests; nothing in the app wiring, the view model or the QML does."""
        offenders = []
        for folder in ("app", "services", "repositories", "qml", "main.py", "tools"):
            path = ROOT / folder
            files = [path] if path.is_file() else [p for p in path.rglob("*") if p.suffix in (".py", ".qml")]
            for file in files:
                text = file.read_text(encoding="utf-8", errors="ignore")
                if re.search(r"book_creation_enabled\s*=\s*True|mode\s*=\s*BOOK|mode\s*=\s*['\"]BOOK['\"]|accounting_mode\s*=\s*['\"]BOOK", text):
                    offenders.append(file.relative_to(ROOT).as_posix())
        self.assertEqual(sorted(offenders), ["services/market_trip_book_service.py"])                       # the `mode=BOOK` of its own creation call


# --------------------------------------------------------------------------- BK_ISSUE
class IssueTests(BookCase):
    def test_issue_takes_scrap_posts_one_equivalent_per_line_and_creates_the_book_trip(self):
        done = self.issue([karat(K21, "20"), karat(K18, "10")])
        self.assertEqual((done["kind"], done["code"], done["created"], done["duplicate"], done["document_posted"]), ("BK_ISSUE", "MT000001-1", True, False, "28.571"))
        trip = self.db.trips[int(done["trip_id"])]
        self.assertEqual((trip["accounting_mode"], trip["status"]), ("BOOK", "OPEN"))
        self.assertEqual(self.db.entries[int(done["entry_id"])]["trip_mode"], "BOOK")
        self.assertEqual([(l["posted"], l["analytical"], l["difference"]) for l in done["lines"]],
                         [("20.000", "20.000000", "0.000000"), ("8.571", "8.571429", "-0.000429")])
        outs = {m["karat_id"]: m for m in self.scrap_rows(done["entry_id"])}
        self.assertEqual((outs[1]["weight_delta"], outs[1]["type_code"], outs[2]["weight_delta"]), (D("-20.000"), "TRIP_CUSTODY_OUT", D("-10.000")))
        self.assertEqual((self.db.physical(1), self.db.physical(2)), (D("80.000"), D("40.000")))              # physical grams, never converted
        self.assertEqual(self.db.cash(), D("50000.00"))                                                      # no cash
        self.assertEqual([e["event_type"] for e in self.db.events], ["OPENED"])
        self.assertEqual([(a["event_type"], a["reference"]) for a in self.db.audit_rows], [("MARKET_TRIP_BK_ISSUE", "MT000001-1")])
        balance = self.balance(done["trip_id"])
        self.assertEqual((balance["posted"], balance["analytical"], balance["drift"]), (D("28.571"), D("28.571429"), D("-0.000429")))
        self.assertEqual(self.detail(done["trip_id"])["custody"]["gold"], [])                                 # the legacy gold custody is not used by a BOOK trip

    def test_a_second_issue_on_the_same_book_trip_adds_to_the_book(self):
        first = self.issue()
        second = self.issue([karat(K18, "10")], trip=first["trip_id"])
        self.assertEqual((second["code"], second["created"]), ("MT000001-2", False))
        self.assertEqual(self.balance(first["trip_id"])["posted"], D("28.571"))

    def test_only_available_scrap_leaves_and_a_refused_issue_leaves_no_trace(self):
        self.assertEqual((self.db.physical(1), self.db.available(1)), (D("100.000"), D("95.000")))
        self.refused("SCRAP_SHORTAGE", lambda: self.issue([karat(K21, "95.001")]))
        message = self.refused("SCRAP_SHORTAGE", lambda: self.issue([karat(K21, "98")])).args[0]
        self.assertIn("محجوز لمراجعة المدير", message)
        self.refused("SCRAP_SHORTAGE", lambda: self.issue([karat(K24, "0.001")]))
        self.refused("SCRAP_SHORTAGE", lambda: self.issue([karat(K18, "10"), karat(K21, "500")]))             # the whole document is refused
        self.issue([karat(K21, "95.000")])                                                                    # exactly the available grams
        self.assertEqual(self.db.available(1), D("0.000"))

    def test_the_same_karat_on_two_lines_draws_on_the_same_scrap(self):
        self.refused("SCRAP_SHORTAGE", lambda: self.issue([karat(K21, "50"), karat(K21, "45.001")]))
        done = self.issue([karat(K21, "50"), karat(K21, "45")])
        self.assertEqual(len(self.scrap_rows(done["entry_id"])), 2)                                           # one Scrap movement per line

    def test_a_scrap_issue_takes_master_karats_only_and_every_line_is_explicit(self):
        self.refused("SCRAP_BASIS", lambda: self.issue([assay("875", "5")]))
        self.refused("BASIS_REQUIRED", lambda: self.issue([{"karat_id": K21, "weight": "5"}]))
        self.refused("BASIS_CONFLICT", lambda: self.issue([{"basis": "MASTER_KARAT", "karat_id": K21, "fineness": "875", "weight": "5"}]))
        self.refused("KARAT_REQUIRED", lambda: self.issue([{"basis": "MASTER_KARAT", "weight": "5"}]))
        self.refused("WEIGHT_REQUIRED", lambda: self.issue([karat(K21, "0")]))
        self.refused("WEIGHT_INVALID", lambda: self.issue([karat(K21, "1.2345")]))
        self.refused("LINES_REQUIRED", lambda: self.issue([]))
        self.refused("KARAT_NOT_FOUND", lambda: self.issue([karat("4", "1")]))                                # 22K is inactive in this fixture
        self.refused("PRICE_NOT_ALLOWED", lambda: self.issue([karat(K21, "5", "100")]))


# --------------------------------------------------------------------------- BK_BUY
class BuyTests(BookCase):
    def setUp(self):
        super().setUp()
        self.trip_id = self.issue()["trip_id"]
        self.cash(self.trip_id, "100000")

    def test_a_purchase_spends_custody_cash_and_adds_the_posted_equivalent_without_any_other_ledger(self):
        before = copy.deepcopy((self.db.scrap, self.db.cash_moves, self.db.sup_gold, self.db.sm_making, self.db.settlements, self.db.assayed_moves))
        done = self.buy(self.trip_id, [assay("916.7", "5", "32000.00"), karat(K18, "4", "20000")])
        self.assertEqual((done["kind"], done["created"], done["duplicate"]), ("BK_BUY", False, False))
        self.assertEqual([(l["posted"], l["difference"], l["price"]) for l in done["lines"]], [("5.238", "-0.000286", "32000.00"), ("3.429", "0.000429", "20000.00")])
        self.assertEqual(done["trade"]["total_price"], "52000.00")
        cash = self.custody_cash(self.trip_id)
        self.assertEqual((cash["issued"], cash["spent"], cash["outstanding"]), (D("100000.00"), D("52000.00"), D("48000.00")))
        self.assertEqual(before, copy.deepcopy((self.db.scrap, self.db.cash_moves, self.db.sup_gold, self.db.sm_making, self.db.settlements, self.db.assayed_moves)))
        self.assertEqual(self.balance(self.trip_id)["posted"], D("20.000") + D("5.238") + D("3.429"))

    def test_the_vendor_is_mandatory_and_a_registered_party_must_be_active(self):
        self.refused("VENDOR_REQUIRED", lambda: self.buy(self.trip_id, name=""))
        self.refused("VENDOR_NOT_FOUND", lambda: self.buy(self.trip_id, party="999"))
        self.refused("VENDOR_INACTIVE", lambda: self.buy(self.trip_id, party="11"))
        done = self.buy(self.trip_id, party="10")
        self.assertEqual((done["trade"]["counterparty_kind"], done["trade"]["name"]), ("REGISTERED_PARTY", "مورد نشط"))

    def test_the_total_may_not_exceed_the_custody_cash(self):
        self.refused("PURCHASE_EXCEEDS_CASH", lambda: self.buy(self.trip_id, [assay("916.7", "5", "100000.01")]))
        self.buy(self.trip_id, [assay("916.7", "5", "100000.00")], ack=True)                                    # exactly the cash
        self.assertEqual(self.custody_cash(self.trip_id)["outstanding"], D("0.00"))

    def test_a_price_far_from_the_reference_needs_the_explicit_acknowledgement(self):
        error = self.refused("PRICE_DEVIATION", lambda: self.buy(self.trip_id, [karat(K21, "10", "40000")]))
        self.assertIn("المرجع", str(error))
        done = self.buy(self.trip_id, [karat(K21, "10", "40000")], ack=True)
        self.assertEqual((done["trade"]["price_warning_acknowledged"], done["trade"]["deviation_percent"], done["trade"]["reference_total"]),
                         (True, "-33.33", "60000.00"))
        self.buy(self.trip_id, [karat(K21, "10", "60000")])                                                    # at the reference: no warning

    def test_price_lines_are_validated(self):
        self.refused("PRICE_INVALID", lambda: self.buy(self.trip_id, [assay("916.7", "5", "0")]))
        self.refused("PRICE_INVALID", lambda: self.buy(self.trip_id, [assay("916.7", "5", "10.005")]))
        self.refused("PRICE_INVALID", lambda: self.buy(self.trip_id, [assay("916.7", "5")]))
        self.refused("FINENESS_RANGE", lambda: self.buy(self.trip_id, [assay("1000.1", "5", "10")]))
        self.refused("FINENESS_PRECISION", lambda: self.buy(self.trip_id, [assay("916.75", "5", "10")]))
        self.refused("FINENESS_REQUIRED", lambda: self.buy(self.trip_id, [{"basis": "FINENESS", "weight": "5", "price": "10"}]))


# --------------------------------------------------------------------------- BK_SELL
class SellTests(BookCase):
    def setUp(self):
        super().setUp()
        self.trip_id = self.issue()["trip_id"]

    def test_a_sale_adds_the_proceeds_to_the_custody_cash_and_takes_the_equivalent_from_the_book(self):
        before = copy.deepcopy((self.db.scrap, self.db.cash_moves, self.db.assayed_moves))
        done = self.sell(self.trip_id)
        self.assertEqual((done["kind"], done["document_posted"], done["trade"]["total_price"]), ("BK_SELL", "6.000", "36000.00"))
        cash = self.custody_cash(self.trip_id)
        self.assertEqual((cash["issued"], cash["received"], cash["outstanding"]), (D("0.00"), D("36000.00"), D("36000.00")))
        self.assertEqual(before, copy.deepcopy((self.db.scrap, self.db.cash_moves, self.db.assayed_moves)))     # the cash stays in custody: no Main Safe row
        self.assertEqual(self.balance(self.trip_id)["posted"], D("14.000"))
        self.assertEqual(self.db.cash(), D("50000.00"))

    def test_a_sale_above_the_analytical_balance_is_refused_and_the_exact_balance_is_accepted(self):
        error = self.refused("BOOK_EXCEEDS_BALANCE", lambda: self.sell(self.trip_id, [karat(K21, "20.001", "100000")]))
        self.assertIn("20.000", str(error))
        self.sell(self.trip_id, [karat(K21, "20", "120000")], ack=True)
        self.assertEqual(self.balance(self.trip_id)["posted"], D("0.000"))
        self.refused("BOOK_EXCEEDS_BALANCE", lambda: self.sell(self.trip_id, [karat(K21, "0.001", "6")]))

    def test_the_book_is_one_currency_a_different_assay_may_leave_what_another_brought(self):
        done = self.sell(self.trip_id, [assay("866.7", "11", "60000")], ack=True)                              # 11.000 g @ 866.7 = 10.896 equivalent
        self.assertEqual(done["lines"][0]["posted"], "10.896")
        self.assertEqual(self.balance(self.trip_id)["posted"], D("9.104"))

    def test_the_buyer_is_mandatory_and_an_inactive_party_is_refused(self):
        self.refused("BUYER_REQUIRED", lambda: self.sell(self.trip_id, name=""))
        self.refused("BUYER_INACTIVE", lambda: self.sell(self.trip_id, party="11"))
        self.refused("BUYER_NOT_FOUND", lambda: self.sell(self.trip_id, party="999"))
        self.sell(self.trip_id, party="12")                                                                    # any role, active

    def test_the_price_warning_applies_to_a_sale_too(self):
        self.refused("PRICE_DEVIATION", lambda: self.sell(self.trip_id, [karat(K21, "10", "90000")]))
        done = self.sell(self.trip_id, [karat(K21, "10", "90000")], ack=True)
        self.assertTrue(done["trade"]["price_warning_acknowledged"])


# --------------------------------------------------------------------------- BK_RETURN
class ReturnTests(BookCase):
    def setUp(self):
        super().setUp()
        self.trip_id = self.issue([karat(K21, "20"), karat(K18, "10")])["trip_id"]

    def test_a_master_karat_return_goes_back_into_the_scrap_of_the_same_karat(self):
        done = self.ret(self.trip_id, [karat(K21, "5"), karat(K18, "10")])
        self.assertEqual(done["document_posted"], "13.571")
        ins = {m["karat_id"]: m for m in self.scrap_rows(done["entry_id"])}
        self.assertEqual((ins[1]["weight_delta"], ins[1]["type_code"], ins[2]["weight_delta"]), (D("5.000"), "TRIP_CUSTODY_RETURN_IN", D("10.000")))
        self.assertEqual((self.db.physical(1), self.db.physical(2)), (D("85.000"), D("50.000")))               # 18K is whole again
        self.assertEqual(self.db.assayed_moves, [])                                                           # nothing in the assayed stock
        self.assertEqual(self.balance(self.trip_id)["posted"], D("15.000"))

    def test_other_assays_become_identified_assayed_stock_with_facts_origin_and_posted_amount(self):
        done = self.ret(self.trip_id, [assay("866.7", "1.8")])
        self.assertEqual(done["lines"][0]["stock_item"], True)
        (move,) = self.db.assayed_moves
        self.assertEqual((move["movement_type"], move["grams_delta"], move["eq21_posted_delta"], move["eq21_analytical"], move["posting_difference"]),
                         ("RECEIVE", D("1.800"), D("1.783"), D("1.782926"), D("0.000074")))
        self.assertEqual((move["item_entry_id"], move["item_line_no"], move["source_entry_id"], move["correction_id"], move["posting_basis"], move["eq21_independent"]),
                         (int(done["entry_id"]), 1, int(done["entry_id"]), None, "INDEPENDENT", D("1.783")))
        self.assertEqual([m for m in self.scrap_rows(done["entry_id"])], [])                                  # never booked into Scrap under another karat
        self.assertEqual((self.db.physical(1), self.db.physical(2)), (D("80.000"), D("40.000")))
        item = self.detail(self.trip_id)["book"]["stock_items"][0]
        self.assertEqual((item["fineness"], item["original_weight"], item["original_posted"], item["grams"], item["posted"], item["code"]),
                         (D("866.7"), D("1.800"), D("1.783"), D("1.800"), D("1.783"), "MT000001-2"))
        self.assertEqual(self.balance(self.trip_id)["posted"], D("28.571") - D("1.783"))

    def test_a_875_assay_is_still_an_assayed_item_never_silently_a_scrap_karat(self):
        self.ret(self.trip_id, [assay("875.0", "2")])
        self.assertEqual(self.db.physical(1), D("80.000"))
        self.assertEqual(len(self.db.assayed_moves), 1)

    def test_a_return_above_the_balance_is_refused(self):
        self.refused("BOOK_EXCEEDS_BALANCE", lambda: self.ret(self.trip_id, [karat(K21, "28.572")]))
        self.refused("BOOK_EXCEEDS_BALANCE", lambda: self.ret(self.trip_id, [assay("1000", "28.572")]))

    def test_the_rounding_only_example_closes_analytically_and_leaves_the_posted_book_at_minus_one_milli(self):
        """Design addendum 1.5: 10.000 g of 18K came out of Scrap and goes back in three pieces. No physical loss; posting drift -0.001; nothing is refused."""
        trip = self.issue([karat(K18, "10")])["trip_id"]
        for weight in ("3.333", "3.333", "3.334"):
            self.ret(trip, [karat(K18, weight)])
        balance = self.balance(trip)
        self.assertEqual((balance["posted"], balance["analytical"], balance["drift"], balance["analytical_available"]), (D("-0.001"), D("0.000000"), D("-0.001000"), D("0.000")))
        self.assertEqual(self.db.physical(2), D("40.000"))                                                    # Scrap conservation: every gram of this trip is back (setUp holds 10 g)
        self.refused("BOOK_EXCEEDS_BALANCE", lambda: self.ret(trip, [karat(K18, "0.001")]))
        self.assertEqual(self.detail(trip)["book"]["balance"]["lines"], 4)


# --------------------------------------------------------------------------- the service preview (read only)
class ServicePreviewTests(BookCase):
    def test_the_preview_agrees_with_the_posting_and_writes_nothing(self):
        trip = self.issue()["trip_id"]
        self.cash(trip, "100000")
        before = self.state()
        lines = [assay("916.7", "5", "32000"), karat(K18, "4", "20000")]
        preview = self.service.book_preview("BK_BUY", trip, lines)
        self.assertEqual((preview["error"], preview["document_posted"], preview["total"]), ("", "8.667", "52000.00"))
        self.assertEqual(self.state(), before)                                                              # nothing was written (a read transaction)
        done = self.buy(trip, lines)
        self.assertEqual((done["document_posted"], [l["posted"] for l in done["lines"]]), (preview["document_posted"], [l["posted"] for l in preview["lines"]]))

    def test_the_preview_reports_the_first_refusal_the_posting_would_raise(self):
        trip = self.issue()["trip_id"]
        for kind, rows, code in (("BK_RETURN", [karat(K21, "20.001")], "BOOK_EXCEEDS_BALANCE"), ("BK_SELL", [karat(K21, "21", "126000")], "BOOK_EXCEEDS_BALANCE"),
                                 ("BK_BUY", [karat(K21, "10", "60000")], "PURCHASE_EXCEEDS_CASH"), ("BK_ISSUE", [karat(K21, "500")], "SCRAP_SHORTAGE")):
            self.assertEqual(self.service.book_preview(kind, trip, rows)["code"], code, kind)
        self.assertEqual(self.service.book_preview("BK_RETURN", trip, [{"basis": "FINENESS", "weight": "1"}])["code"], "FINENESS_REQUIRED")
        self.assertEqual(self.service.book_preview("BK_RETURN", trip, [karat("4", "1")])["code"], "KARAT_NOT_FOUND")
        with self.assertRaises(TripError):
            self.service.book_preview("MELT", trip, [karat(K21, "1")])


# --------------------------------------------------------------------------- conservation
class ConservationTests(BookCase):
    def test_every_gram_and_every_pound_is_somewhere_after_a_full_sequence(self):
        scrap_before = (self.db.physical(1), self.db.physical(2), self.db.physical(3))
        trip = self.issue([karat(K21, "20"), karat(K18, "10")])["trip_id"]
        self.cash(trip, "100000")
        self.buy(trip, [assay("916.7", "5", "28000"), karat(K24, "2", "14000")])
        self.sell(trip, [karat(K21, "6", "36000")])
        self.ret(trip, [karat(K21, "9.5"), karat(K18, "6")])
        self.ret(trip, [assay("866.7", "1.8")])
        balance = self.balance(trip)
        lines = self.service._repository(self.db).book_lines(int(trip))
        self.assertEqual(sum((l["signed_eq21_posted"] for l in lines), D("0.000")), balance["posted"])        # the posted balance IS the sum of the stored posted amounts
        # physical Scrap: issued 21K 20 + 18K 10; returned 21K 9.5 + 18K 6 -> net out 10.5 g of 21K and 4 g of 18K
        self.assertEqual((self.db.physical(1), self.db.physical(2), self.db.physical(3)), (scrap_before[0] - D("10.5"), scrap_before[1] - D("4"), scrap_before[2]))
        cash = self.custody_cash(trip)
        self.assertEqual(cash["outstanding"], D("100000.00") - D("28000.00") - D("14000.00") + D("36000.00"))
        self.assertEqual(self.db.cash(), D("50000.00") - D("100000.00"))                                       # only the cash issue ever touched Main Safe
        stock = self.detail(trip)["book"]["stock_items"]
        self.assertEqual([(i["grams"], i["posted"]) for i in stock], [(D("1.800"), D("1.783"))])
        exact = br.balance_of_rows(lines)
        self.assertEqual(exact.analytical_open, exact.analytical_in - exact.analytical_out)
        self.assertEqual(exact.drift, Fraction(exact.posted) - exact.analytical_open)


# --------------------------------------------------------------------------- mode isolation
class ModeIsolationTests(BookCase):
    def setUp(self):
        super().setUp()
        self.book_trip = self.issue()["trip_id"]
        self.legacy_trip = self.service.issue("", "1000", [{"karat_id": K21, "weight": "10", "designation": "MELT"}], TODAY, "", self.k())["trip_id"]

    def test_normal_creation_on_a_book_capable_service_is_still_legacy(self):
        self.assertEqual(self.db.trips[int(self.legacy_trip)]["accounting_mode"], "LEGACY")
        self.assertEqual(self.db.trips[int(self.book_trip)]["accounting_mode"], "BOOK")
        again = self.service.create_trip("10", [], [], TODAY, "", self.k())
        self.assertEqual(self.db.trips[int(again["trip_id"])]["accounting_mode"], "LEGACY")

    def test_book_operations_refuse_a_legacy_trip(self):
        for call in (lambda: self.issue(trip=self.legacy_trip), lambda: self.buy(self.legacy_trip), lambda: self.sell(self.legacy_trip), lambda: self.ret(self.legacy_trip)):
            self.refused("TRIP_MODE_MISMATCH", call)

    def test_legacy_operations_refuse_a_book_trip(self):
        t = self.book_trip
        s = self.service
        self.refused("BOOK_GOLD_PATH", lambda: s.issue(t, "", [{"karat_id": K21, "weight": "1", "designation": "MELT"}], TODAY, "", self.k()))
        self.refused("TRIP_MODE_MISMATCH", lambda: s.melt(t, [{"karat_id": K21, "weight": "1"}], TODAY, "w", {"weight": "1", "fineness": "875"}, [], "a", "", "", self.k()))
        self.refused("TRIP_MODE_MISMATCH", lambda: s.heat(t, [{"karat_id": K21, "input_weight": "1", "output_weight": "1"}], TODAY, "w", "", "", "", self.k()))
        self.refused("TRIP_MODE_MISMATCH", lambda: s.buy_gold(t, [{"karat_id": K21, "designation": "LOGO", "weight": "1", "price": "1000"}], "", "x", TODAY, "", "", self.k()))
        self.refused("TRIP_MODE_MISMATCH", lambda: s.sell_gold(t, "CUSTODY_GOLD", K21, "LOGO", "", "1", "TOTAL", "1000", "", "x", TODAY, "", "", self.k()))
        self.refused("TRIP_MODE_MISMATCH", lambda: s.return_lots(t, [{"lot_id": "1"}], TODAY, "", self.k()))
        self.refused("TRIP_MODE_MISMATCH", lambda: s.issue_lots(t, [{"lot_id": "1"}], TODAY, "", self.k()))
        self.refused("TRIP_MODE_MISMATCH", lambda: s.settle_supplier_gold(t, "10", K21, "LOGO", "1", TODAY, "", "", self.k()))
        self.refused("TRIP_MODE_MISMATCH", lambda: s.settle_supplier_lot(t, "10", "1", TODAY, "", "", self.k()))

    def test_the_shared_cash_entries_work_in_both_modes_and_book_trades_use_the_same_cash_arithmetic(self):
        self.cash(self.book_trip, "5000")
        self.service.return_custody(self.book_trip, "1000", [], TODAY, "", self.k())
        self.assertEqual(self.custody_cash(self.book_trip)["outstanding"], D("4000.00"))
        self.assertEqual(self.db.cash(), D("50000.00") - D("1000.00") - D("5000.00") + D("1000.00"))      # the legacy trip of setUp took 1000 first

    def test_the_repository_refuses_a_kind_in_the_wrong_mode_even_if_a_caller_forgot(self):
        with self.db.transaction():
            repo = TripRepo(self.db)
            with self.assertRaises(TripError) as refused:
                repo.insert_entry(trip_id=int(self.book_trip), entry_no=99, kind="MELT", entry_date=TODAY, notes=None, key=self.k(), digest="x",
                                  actor=self.session.current_actor(), now=self.db.now)
            self.assertEqual(refused.exception.code, "TRIP_MODE_MISMATCH")
            with self.assertRaises(TripError):
                repo.insert_entry(trip_id=int(self.legacy_trip), entry_no=99, kind="BK_ISSUE", entry_date=TODAY, notes=None, key=self.k(), digest="x",
                                  actor=self.session.current_actor(), now=self.db.now)

    def test_a_book_trip_cannot_be_closed_even_when_its_balance_and_cash_are_zero(self):
        trip = self.issue([karat(K21, "5")])["trip_id"]
        self.ret(trip, [karat(K21, "5")])
        self.assertEqual((self.balance(trip)["posted"], self.custody_cash(trip)["outstanding"]), (D("0.000"), D("0.00")))
        self.refused("BOOK_CLOSURE_NOT_AVAILABLE", lambda: self.service.close(trip))
        self.assertEqual(self.db.trips[int(trip)]["status"], "OPEN")

    def test_the_legacy_trip_still_closes_the_old_way(self):
        trip = self.service.issue("", "100", [], TODAY, "", self.k())["trip_id"]
        self.service.return_custody(trip, "100", [], TODAY, "", self.k())
        self.assertEqual(self.service.close(trip)["status"], "CLOSED")


# --------------------------------------------------------------------------- replay, conflict, authorization, rollback
class ReplayAndAuthorizationTests(BookCase):
    def setUp(self):
        super().setUp()
        self.trip_id = self.issue()["trip_id"]
        self.cash(self.trip_id, "100000")

    def operations(self):
        t = self.trip_id
        return {
            "issue": lambda r: self.issue([karat(K18, "5")], trip=t, request=r),
            "buy": lambda r: self.buy(t, [assay("916.7", "2", "12800")], request=r),
            "sell": lambda r: self.sell(t, [karat(K21, "2", "12000")], request=r),
            "return": lambda r: self.ret(t, [karat(K21, "2")], request=r)}

    def test_an_exact_replay_returns_the_original_without_any_new_effect(self):
        for name, run in self.operations().items():
            with self.subTest(operation=name):
                request = self.k()
                first = run(request)
                after_first = self.state()
                again = run(request)
                self.assertEqual((again["duplicate"], again["entry_id"], again["code"], again["lines"]), (True, first["entry_id"], first["code"], first["lines"]))
                self.assertEqual(self.state(), after_first)

    def test_a_replay_is_resolved_before_availability_is_derived(self):
        request = self.k()
        self.ret(self.trip_id, [karat(K21, "20")], request=request)                                            # exhausts the book
        again = self.ret(self.trip_id, [karat(K21, "20")], request=request)                                    # would now be insufficient, but it is a replay
        self.assertTrue(again["duplicate"])

    def test_a_changed_payload_actor_kind_or_trip_under_a_used_key_is_a_conflict(self):
        request = self.k()
        self.sell(self.trip_id, [karat(K21, "2", "12000")], request=request)
        self.refused("REQUEST_CONFLICT", lambda: self.sell(self.trip_id, [karat(K21, "3", "12000")], request=request))
        self.refused("REQUEST_CONFLICT", lambda: self.sell(self.trip_id, [karat(K21, "2", "12000")], name="مشتري آخر", request=request))
        self.refused("REQUEST_CONFLICT", lambda: self.ret(self.trip_id, [karat(K21, "2")], request=request))
        other, _ = self.other_service()
        self.refused("REQUEST_CONFLICT", lambda: other.book_sell(self.trip_id, [karat(K21, "2", "12000")], "", "مشتري", TODAY, "", "", request, False))

    def test_a_key_owned_by_another_flow_is_a_conflict_and_nothing_is_written(self):
        legacy = self.k()
        self.service.issue("", "10", [], TODAY, "", legacy)
        self.refused("REQUEST_CONFLICT", lambda: self.ret(self.trip_id, request=legacy))
        taken = self.k()
        self.db.mk_docs = {1: {"request_key": taken}}                                                         # a marketer document owns this key
        self.refused("REQUEST_CONFLICT", lambda: self.ret(self.trip_id, request=taken), error=RequestKeyInUse)

    def test_a_twin_that_meets_the_unique_key_is_resolved_from_a_fresh_read(self):
        request = self.k()
        winner = self.sell(self.trip_id, [karat(K21, "2", "12000")], request=request)
        real = TripRepo.by_request
        calls = {"n": 0}

        def blind_first(self_, k):                                            # the FIRST read misses the committed winner, so the insert meets uq_mte_request
            calls["n"] += 1
            return None if calls["n"] == 1 else real(self_, k)
        with patch.object(TripRepo, "by_request", blind_first):
            again = self.sell(self.trip_id, [karat(K21, "2", "12000")], request=request)
        self.assertEqual((again["duplicate"], again["entry_id"]), (True, winner["entry_id"]))

    def test_authorization_is_rechecked_inside_the_transaction_and_a_refusal_leaves_no_trace(self):
        weak = session_for(frozenset({"APP_LOGIN"}), 12)
        service = MarketTripBookService(weak, transaction_factory=self.db.transaction, repository=TripRepo, scrap=ScrapRepo, accounts=AccountsRepo,
                                        authorization=FakeAuthorization(weak), book_creation_enabled=True)
        before = self.state()
        for call in (lambda: service.book_issue(self.trip_id, [karat(K21, "1")], TODAY, "", self.k()),
                     lambda: service.book_buy(self.trip_id, [assay("916.7", "1", "10")], "", "x", TODAY, "", "", self.k()),
                     lambda: service.book_sell(self.trip_id, [karat(K21, "1", "10")], "", "x", TODAY, "", "", self.k()),
                     lambda: service.book_return(self.trip_id, [karat(K21, "1")], TODAY, "", self.k()),
                     lambda: service.book_preview("BK_RETURN", self.trip_id, [karat(K21, "1")])):
            with self.assertRaises(PermissionError):
                call()
        self.authorization.permissions = {"APP_LOGIN"}                                                         # revoked in the "database" while the session stays
        for call in (lambda: self.issue(trip=self.trip_id), lambda: self.buy(self.trip_id), lambda: self.sell(self.trip_id), lambda: self.ret(self.trip_id)):
            with self.assertRaises(PermissionError):
                call()
        self.assertEqual(self.state(), before)

    def test_a_failure_at_any_step_rolls_the_whole_document_back(self):
        steps = {"issue": ("trip", "entry", "scrap", "book_line", "audit"), "buy": ("entry", "book_line", "book_trade", "audit"),
                 "sell": ("entry", "book_line", "book_trade", "audit"), "return": ("entry", "scrap", "book_line", "audit"),
                 "return_assay": ("entry", "book_line", "stock_move", "audit")}
        runs = {"issue": lambda: self.issue([karat(K18, "5")], request=self.k()), "buy": lambda: self.buy(self.trip_id, [assay("916.7", "2", "12800")]),
                "sell": lambda: self.sell(self.trip_id, [karat(K21, "2", "12000")]), "return": lambda: self.ret(self.trip_id, [karat(K21, "2")]),
                "return_assay": lambda: self.ret(self.trip_id, [assay("866.7", "1.8")])}
        for name, points in steps.items():
            for point in points:
                with self.subTest(operation=name, step=point):
                    before = self.state()
                    self.db.fail_at = point
                    try:
                        with self.assertRaises(RuntimeError):
                            runs[name]()
                    finally:
                        self.db.fail_at = None
                    self.assertEqual(self.state(), before, f"{name} left a trace after a failure at {point}")

    def test_lock_order_is_trip_then_scrap_karats_ascending(self):
        self.db.lock_log.clear()
        self.issue([karat(K21, "1"), karat(K18, "1")], trip=self.trip_id)
        order = [e for e in self.db.lock_log if e[0] in ("trip", "scrap")]
        self.assertEqual(order[0], ("trip",))
        self.assertEqual([e[1] for e in order if e[0] == "scrap"], sorted(e[1] for e in order if e[0] == "scrap"))


# --------------------------------------------------------------------------- reversal / correction
class ReversalTests(BookCase):
    def setUp(self):
        super().setUp()
        self.issued = self.issue([karat(K21, "20"), karat(K18, "10")])
        self.trip_id = self.issued["trip_id"]
        self.cash(self.trip_id, "100000")

    def reverse(self, entry, reason="خطأ إدخال"):
        return self.service.reverse(entry["entry_id"], reason)

    def test_reversing_an_issue_returns_the_stored_scrap_and_removes_the_book_amount(self):
        before = (self.db.physical(1), self.db.physical(2))
        self.reverse(self.issued)
        self.assertEqual((self.db.physical(1), self.db.physical(2)), (before[0] + D("20.000"), before[1] + D("10.000")))
        moves = [m for m in self.db.scrap if m["type_code"] == "REVERSAL_TRIP_ISSUE_IN"]
        self.assertEqual(sorted(m["weight_delta"] for m in moves), [D("10.000"), D("20.000")])               # the exact STORED movements, negated
        self.assertEqual(self.balance(self.trip_id)["posted"], D("0.000"))
        self.assertEqual(self.detail(self.trip_id)["entries"][-1]["reversed"], True)
        again = self.refused("ALREADY_REVERSED", lambda: self.reverse(self.issued), error=CorrectionError)

    def test_an_issue_cannot_be_reversed_once_its_gold_was_used_and_can_after_the_user_is_reversed(self):
        sale = self.sell(self.trip_id, [karat(K21, "6", "36000")])
        self.refused("DEPENDENT_BOOK_USE_EXISTS", lambda: self.reverse(self.issued), error=CorrectionError)
        self.reverse(sale)
        self.assertEqual(self.custody_cash(self.trip_id)["received"], D("0.00"))
        self.reverse(self.issued)                                                                              # newest first, no cascade

    def test_reversing_a_purchase_returns_the_cash_to_the_custody_and_removes_the_gold_from_the_book(self):
        buy = self.buy(self.trip_id, [karat(K24, "2", "14000")])
        self.assertEqual((self.custody_cash(self.trip_id)["outstanding"], self.balance(self.trip_id)["posted"]), (D("86000.00"), D("30.857")))
        self.reverse(buy)
        self.assertEqual((self.custody_cash(self.trip_id)["outstanding"], self.balance(self.trip_id)["posted"]), (D("100000.00"), D("28.571")))
        self.assertEqual(self.custody_cash(self.trip_id)["spent"], D("0.00"))
        self.assertEqual(self.db.cash(), D("50000.00") - D("100000.00"))                                       # the reversal posts nothing in Main Safe

    def test_reversing_a_purchase_that_the_book_depends_on_is_blocked(self):
        trip = self.issue([karat(K21, "1")])["trip_id"]
        self.cash(trip, "60000")
        buy = self.buy(trip, [karat(K21, "9", "54000")])
        self.sell(trip, [karat(K21, "10", "60000")])
        self.refused("DEPENDENT_BOOK_USE_EXISTS", lambda: self.reverse(buy), error=CorrectionError)

    def test_reversing_a_sale_takes_the_proceeds_out_of_the_custody_cash_unless_they_were_used(self):
        sale = self.sell(self.trip_id, [karat(K21, "6", "36000")])
        self.service.return_custody(self.trip_id, "130000", [], TODAY, "", self.k())                         # the proceeds went back to Main Safe
        self.refused("DEPENDENT_CASH_USE_EXISTS", lambda: self.reverse(sale), error=CorrectionError)
        self.service.issue(self.trip_id, "130000", [], TODAY, "", self.k())
        self.reverse(sale)
        self.assertEqual(self.balance(self.trip_id)["posted"], D("28.571"))
        self.assertEqual(self.custody_cash(self.trip_id)["received"], D("0.00"))

    def test_reversing_a_scrap_return_takes_the_stored_grams_out_and_is_blocked_when_they_were_used(self):
        back = self.ret(self.trip_id, [karat(K21, "5")])
        self.assertEqual(self.db.physical(1), D("85.000"))
        self.db.scrap.append({"id": 900, "karat_id": 1, "weight_delta": D("-80.000"), "reference_type": "X", "reference_id": "0", "type_code": "X"})   # Scrap was consumed
        self.refused("OLD_GOLD_DEPENDENCY_EXISTS", lambda: self.reverse(back), error=CorrectionError)
        self.db.scrap.pop()
        self.reverse(back)
        self.assertEqual((self.db.physical(1), self.balance(self.trip_id)["posted"]), (D("80.000"), D("28.571")))
        moves = [m for m in self.db.scrap if m["type_code"] == "REVERSAL_TRIP_RETURN_OUT"]
        self.assertEqual([m["weight_delta"] for m in moves], [D("-5.000")])

    def test_reversing_an_assayed_return_removes_the_item_with_the_stored_figures(self):
        back = self.ret(self.trip_id, [assay("866.7", "1.8"), assay("500", "0.003")])
        self.assertEqual([(i["grams"], i["posted"]) for i in self.detail(self.trip_id)["book"]["stock_items"]], [(D("1.800"), D("1.783")), (D("0.003"), D("0.002"))])
        self.reverse(back)
        compensations = [m for m in self.db.assayed_moves if m["movement_type"] == "RECEIVE_REVERSAL"]
        self.assertEqual([(m["grams_delta"], m["eq21_posted_delta"], m["correction_id"] is not None) for m in compensations],
                         [(D("-1.800"), D("-1.783"), True), (D("-0.003"), D("-0.002"), True)])
        self.assertEqual(len(self.db.assayed_moves), 4)                                                        # the originals stay counted beside the compensation
        self.assertEqual(self.detail(self.trip_id)["book"]["stock_items"], [])
        self.assertEqual(self.balance(self.trip_id)["posted"], D("28.571"))

    def test_an_assayed_return_cannot_be_reversed_after_the_stock_was_used(self):
        back = self.ret(self.trip_id, [assay("866.7", "1.8")])
        later = self.ret(self.trip_id, [karat(K21, "1")])
        with self.db.transaction():                                                                            # a later consumer (Phase 2) takes part of the item
            TripRepo(self.db).insert_stock_movement(item_entry_id=int(back["entry_id"]), item_line_no=1, movement_type="ISSUE", grams_delta=D("-0.900"),
                                                    posted_delta=D("-0.891"), analytical=D("0.891463"), difference=D("-0.000463"), independent=D("0.891"),
                                                    posting_basis="INDEPENDENT", source_entry_id=int(later["entry_id"]), correction_id=None, user_id=7)
        error = self.refused("DEPENDENT_STOCK_USE_EXISTS", lambda: self.reverse(back), error=CorrectionError)
        self.assertIn("0.900", str(error))

    def test_a_reversal_needs_the_trip_open_the_owner_right_and_a_reason(self):
        self.refused("REASON_REQUIRED", lambda: self.reverse(self.issued, "  "), error=CorrectionError)
        weak, _ = self.other_service(frozenset({"APP_LOGIN", "MARKET_TRIP_MANAGE"}), 14)
        with self.assertRaises((PermissionError, CorrectionError)):
            weak.reverse(self.issued["entry_id"], "x")
        self.assertEqual(len(self.db.corrections), 0)

    def test_a_reversal_failure_rolls_back_every_effect(self):
        back = self.ret(self.trip_id, [assay("866.7", "1.8"), karat(K21, "3")])
        for point in ("correction", "reversal_scrap", "stock_move", "audit"):
            with self.subTest(step=point):
                before = self.state()
                self.db.fail_at = point
                try:
                    with self.assertRaises(RuntimeError):
                        self.reverse(back)
                finally:
                    self.db.fail_at = None
                self.assertEqual(self.state(), before)


if __name__ == "__main__":
    unittest.main()
