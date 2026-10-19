"""Gold Position with BOOK balances and assayed return stock (056): every source counted ONCE. Pure aggregation (`build_position`) and the real
GoldPositionService over the in-memory transactional database of the market-trip fakes. No database."""
from contextlib import contextmanager
from decimal import Decimal as D
import unittest
from unittest.mock import patch

from services import book_rules as br
from services.gold_position_service import GoldPositionService, REQUIRED_PERMISSIONS, build_position
from tests.market_trip_fakes import AccountsRepo, HoldingRepo, SupplierPayRepo, TripRepo, build, session_for
from tests.test_market_trip_book import K21, PRICES, TODAY, assay, karat, key


def book_trip(trip_id, *lines, status="OPEN"):
    return {"id": trip_id, "code": f"MT{trip_id:06d}", "status": status, "balance": br.balance_of(list(lines))}


def line(direction, weight, karat_value=21):
    return br.plan_line(direction, D(weight), br.MASTER_KARAT, 1, karat_value, None)


def build_pure(**kw):
    base = dict(scrap=[], shop_lots=[], receipt_stock=[], inventory=[], returned_pending=[], trips=[], pools=[], logo_pools=[])
    base.update(kw)
    return build_position(**base)


class PureBookPositionTests(unittest.TestCase):
    def test_a_book_trip_is_counted_once_by_its_posted_balance_with_unspecified_physical_grams(self):
        position = build_pure(book_trips=[book_trip(1, line(br.IN, "10.000", 18), line(br.OUT, "4.000", 18))])
        row = position["book"]["trips"][0]
        self.assertEqual((row["eq21"], row["analytical"], row["drift"], row["label"]), (D("5.142"), D("5.142857"), D("-0.000857"), "رصيد دفتري — قبل التسوية النهائية"))
        self.assertEqual(position["book"]["physical"], "غير محدد")
        self.assertEqual((position["summary"]["book_eq21"], position["summary"]["total_eq21"], position["summary"]["total_grams"]), (D("5.142"), D("5.142"), D("0.000")))
        self.assertEqual(position["custody"]["trips"], [])                                       # not a custody source line: nothing is counted twice

    def test_a_negative_posted_balance_from_rounding_is_shown_as_it_is_never_hidden_or_netted(self):
        lines = [line(br.IN, "10.000", 18), line(br.OUT, "3.333", 18), line(br.OUT, "3.333", 18), line(br.OUT, "3.334", 18)]
        position = build_pure(book_trips=[book_trip(1, *lines)])
        self.assertEqual(position["book"]["trips"][0]["eq21"], D("-0.001"))
        self.assertEqual((position["book"]["trips"][0]["analytical"], position["book"]["trips"][0]["drift"]), (D("0.000000"), D("-0.001000")))
        self.assertEqual(position["summary"]["total_eq21"], D("-0.001"))

    def test_an_exactly_empty_trip_and_a_closed_state_show_nothing(self):
        position = build_pure(book_trips=[book_trip(1, line(br.IN, "5.000"), line(br.OUT, "5.000"))])
        self.assertEqual((position["book"]["trips"], position["book"]["eq21"], position["summary"]["book_eq21"]), ([], D("0.000"), D("0.000")))

    def test_assayed_stock_counts_the_physical_grams_and_the_stored_posted_amount_with_the_analytical_beside_it(self):
        item = {"entry_id": 5, "line_no": 1, "code": "MT000001-2", "trip_id": 1, "fineness_permille": D("866.7"), "grams": D("1.800"), "posted": D("1.783")}
        position = build_pure(assayed_stock=[item])
        row = position["shop"]["assayed"][0]
        self.assertEqual((row["grams"], row["eq21"], row["analytical"], row["rounding"], row["fineness"]), (D("1.800"), D("1.783"), D("1.782926"), D("0.000074"), D("866.7")))
        self.assertEqual((position["shop"]["parts"]["assayed"]["grams"], position["shop"]["parts"]["assayed"]["eq21"]), (D("1.800"), D("1.783")))
        self.assertEqual((position["summary"]["total_grams"], position["summary"]["total_eq21"], position["summary"]["unknown_grams"]), (D("1.800"), D("1.783"), D("0.000")))

    def test_stock_with_grams_and_a_zero_posted_amount_is_listed_and_a_posted_amount_without_grams_is_a_warning(self):
        tiny = {"entry_id": 6, "line_no": 1, "code": "MT000001-3", "trip_id": 1, "fineness_permille": D("500.0"), "grams": D("0.001"), "posted": D("0.000")}
        broken = {"entry_id": 7, "line_no": 1, "code": "MT000001-4", "trip_id": 1, "fineness_permille": D("500.0"), "grams": D("0.000"), "posted": D("0.001")}
        position = build_pure(assayed_stock=[tiny, broken])
        self.assertEqual([(r["grams"], r["eq21"]) for r in position["shop"]["assayed"]], [(D("0.001"), D("0.000"))])
        self.assertEqual([w["code"] for w in position["warnings"]], ["STOCK_POSTED_WITHOUT_GRAMS"])
        self.assertEqual(position["summary"]["total_eq21"], D("0.000"))                          # the ledger invariant violation is never netted

    def test_legacy_only_data_is_unchanged(self):
        position = build_pure(scrap=[{"karat_id": 1, "karat_value": 21, "physical_grams": D("10"), "held_grams": D("0"), "available_grams": D("10")}])
        self.assertEqual((position["book"]["trips"], position["shop"]["assayed"]), ([], []))
        self.assertEqual((position["summary"]["total_eq21"], position["summary"]["total_grams"]), (D("10.000"), D("10.000")))


class PresentationTests(unittest.TestCase):
    def test_the_view_shows_the_book_group_and_the_assayed_stock_line_without_touching_the_legacy_groups(self):
        from app.viewmodels.management_review_viewmodel import gold_position_view
        item = {"entry_id": 5, "line_no": 1, "code": "MT000001-2", "trip_id": 1, "fineness_permille": D("866.7"), "grams": D("1.800"), "posted": D("1.783")}
        position = build_pure(book_trips=[book_trip(1, line(br.IN, "10.000", 18), line(br.OUT, "4.000", 18))], assayed_stock=[item])
        view = gold_position_view(position)
        self.assertEqual([s["key"] for s in view["summary"]], ["shop", "custody", "intermediary", "book"])
        book = view["summary"][3]
        self.assertEqual((book["grams"], book["eq21"]), ("غير محدد", "5.142"))
        self.assertEqual(len(view["book"]), 1)
        self.assertIn("الرصيد المرحَّل 5.142", view["book"][0]["text"])
        self.assertIn("الوزن الفعلي غير محدد", view["book"][0]["text"])
        assayed = [r for r in view["shop"] if r["label"].startswith("ذهب مرتجع بعيار فعلي")]
        self.assertEqual((len(assayed), assayed[0]["grams"], assayed[0]["eq21"]), (1, "1.800", "1.783"))
        self.assertEqual(view["total"], "6.925")                                                 # 5.142 (book) + 1.783 (assayed stock)
        legacy = gold_position_view(build_pure())
        self.assertEqual(([s["key"] for s in legacy["summary"]], legacy["book"]), (["shop", "custody", "intermediary"], []))


class _Authorization:
    def require_permission_read(self, connection, actor, code):
        if code not in actor.permissions:
            raise PermissionError(code)


class Reads:
    """The repositories GoldPositionService reads over the one fake database."""

    def __init__(self, db):
        self.db, self.trips = db, TripRepo(db)

    def all_trip_ids(self):
        return [{"id": t["id"], "status": t["status"]} for t in sorted(self.db.trips.values(), key=lambda t: t["id"])]

    def custody_gold_by_trip(self):
        return {t["id"]: self.trips.custody_gold(t["id"]) for t in self.db.trips.values()}

    def custody_lots_by_trip(self):
        return {}

    def book_groups_by_trip(self):
        return self.trips.book_groups_by_trip()

    def effective_reconciliations_display(self):
        return self.trips.effective_reconciliations_display()

    def assayed_stock_items(self):
        return self.trips.assayed_stock_items()

    def shop_scrap(self):
        return self.trips.shop_scrap()

    def shop_lots(self):
        return []

    def receipt_stock(self):
        return []

    def open_pools(self, **_):
        return []

    def logo_open_pools(self, **_):
        return []

    def get_stock_weight_by_karat(self):
        return []

    def get_returned_pending_weight_by_karat(self):
        return []

    def party_gold_balances(self, party_id=None):
        return []


class ServiceBookPositionTests(unittest.TestCase):
    def setUp(self):
        self.db, self.session, self.authorization, self.service, self.corrections = build(book=True)
        self.service._price_reference = lambda: dict(PRICES)
        self.n = 0
        for target, value in (("repositories.market_trip_repository.MarketTripRepository", TripRepo), ("repositories.financial_account_repository.FinancialAccountRepository", AccountsRepo),
                              ("repositories.supplier_payment_repository.SupplierPaymentRepository", SupplierPayRepo),
                              ("repositories.intermediary_holding_repository.IntermediaryHoldingRepository", HoldingRepo)):
            started = patch(target, value)
            started.start()
            self.addCleanup(started.stop)
        reads = Reads(self.db)

        @contextmanager
        def snapshot():
            yield object()
        owner = session_for(frozenset(REQUIRED_PERMISSIONS))
        self.positions = GoldPositionService(owner, transaction_factory=snapshot, authorization=_Authorization(), trip_repository=lambda c: reads,
                                             holding_repository=lambda c: reads, inventory_repository=lambda c: reads, marketer_repository=lambda c: reads)

    def k(self):
        self.n += 1
        return key(self.n + 500)

    def figures(self):
        s = self.positions.position()["summary"]
        return s["total_eq21"], s["total_grams"]

    def test_a_gram_is_counted_once_while_it_moves_scrap_book_scrap_and_stock(self):
        base = self.figures()
        trip = self.service.book_issue("", [karat(K21, "20")], TODAY, "", self.k())["trip_id"]       # Scrap -> book
        self.assertEqual(self.figures(), (base[0], base[1] - D("20.000")))                            # the 20 g LEFT the physical total (book: unspecified grams)
        position = self.positions.position()
        self.assertEqual((position["book"]["trips"][0]["eq21"], position["summary"]["book_eq21"]), (D("20.000"), D("20.000")))
        self.assertEqual(position["summary"]["total_eq21"], base[0])                                  # ...and its equivalent is in the book: the 21K total never moved
        self.service.book_return(trip, [karat(K21, "5")], TODAY, "", self.k())                        # book -> Scrap
        self.assertEqual(self.figures(), (base[0], base[1] - D("15.000")))
        self.service.book_return(trip, [assay("866.7", "1.8")], TODAY, "", self.k())                  # book -> assayed stock
        position = self.positions.position()
        self.assertEqual(position["shop"]["assayed"][0]["eq21"], D("1.783"))
        self.assertEqual((position["summary"]["total_eq21"], position["summary"]["total_grams"]), (base[0], base[1] - D("15.000") + D("1.800")))
        self.assertEqual(position["book"]["trips"][0]["eq21"], D("20.000") - D("5.000") - D("1.783"))
        self.assertEqual(position["custody"]["trips"], [])
        # the 21K total is conserved at every step: Scrap + book + assayed stock = what the Scrap was before
        self.assertEqual(position["summary"]["total_eq21"], base[0])

    def test_a_reversal_removes_the_assayed_stock_and_restores_the_scrap_figures(self):
        base = self.figures()
        trip = self.service.book_issue("", [karat(K21, "20")], TODAY, "", self.k())["trip_id"]
        back = self.service.book_return(trip, [assay("866.7", "1.8")], TODAY, "", self.k())
        self.service.reverse(back["entry_id"], "خطأ")
        position = self.positions.position()
        self.assertEqual(position["shop"]["assayed"], [])
        self.assertEqual(position["book"]["trips"][0]["eq21"], D("20.000"))
        self.assertEqual(position["summary"]["total_eq21"], base[0])

    def test_the_position_uses_the_aggregate_reads_only_and_a_legacy_trip_is_unchanged(self):
        self.service.issue("", "100", [], TODAY, "", self.k())
        position = self.positions.position()
        self.assertEqual((position["book"]["trips"], position["shop"]["assayed"]), ([], []))


if __name__ == "__main__":
    unittest.main()
