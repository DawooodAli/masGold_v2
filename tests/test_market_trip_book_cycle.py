"""BOOK Phase 2 (058): stock reissue (ALLOCATED), supplier delivery in the supplier's own karat, marketer delivery / receipt, their reversals, the reconciliation
integration (v2 only for trips with a Phase 2 kind) and the gold position. Real MarketTripBookService / CorrectionService / GoldPositionService over the in-memory
database of tests/market_trip_fakes.py (+ tests/marketer_fakes.py). NOT real-MySQL tests: nothing here proves SQL, locks or races."""
from contextlib import contextmanager
from decimal import Decimal as D
import re
import unittest
from unittest.mock import patch

from repositories.market_trip_repository import MarketTripRepository
from services import book_reconciliation as rec
from services import book_settlement as bs
from services.correction_service import CorrectionError
from services.gold_position_service import GoldPositionService, REQUIRED_PERMISSIONS
from services.market_trip_service import TripError
from tests.market_trip_fakes import TripRepo, session_for
from tests.marketer_fakes import MarketerRepo, prepare
from tests.test_book_reconciliation_service import Recorder
from tests.test_book_settlement_service import ALL, ReconCase
from tests.test_gold_position_book import Reads, _Authorization
from tests.test_market_trip_book import K18, K21, TODAY, assay, karat

SUP21, SUP18, MKT, MKT_OFF = "10", "21", "20", "22"


class CycleCase(ReconCase):
    def setUp(self):
        super().setUp()
        prepare(self.db)
        from tests.market_trip_fakes import SettlementRepo
        for target, value in (("repositories.marketer_custody_repository.MarketerCustodyRepository", MarketerRepo),
                              ("repositories.supplier_gold_settlement_repository.SupplierGoldSettlementRepository", SettlementRepo)):
            started = patch(target, value)
            started.start()
            self.addCleanup(started.stop)
        reads = Reads(self.db)
        reads.party_gold_balances = lambda party_id=None: MarketerRepo(self.db).party_gold_balances(party_id)

        @contextmanager
        def snapshot():
            yield object()
        self.positions = GoldPositionService(session_for(frozenset(REQUIRED_PERMISSIONS)), transaction_factory=snapshot, authorization=_Authorization(),
                                             trip_repository=lambda c: reads, holding_repository=lambda c: reads, inventory_repository=lambda c: reads,
                                             marketer_repository=lambda c: reads)

    def total(self):
        return self.positions.position()["summary"]["total_eq21"]

    def book(self, trip):
        return self.detail(trip)["book"]["balance"]["posted"]

    def stock_trip(self):
        """Trip A: 20 g 21K issued, 1.8 g at 866.7 permille back as an identified stock item (posted 1.783)."""
        trip = self.issue([karat(K21, "20")])["trip_id"]
        back = self.ret(trip, [assay("866.7", "1.8")])
        return trip, back["entry_id"]

    def issue_stock(self, trip, items, **kw):
        return self.service.book_issue_stock(trip, items, TODAY, "", kw.get("request") or self.k())

    def deliver_supplier(self, trip, supplier, row, **kw):
        return self.service.book_deliver_supplier(trip, supplier, row, TODAY, "", "", kw.get("request") or self.k())

    def deliver_marketer(self, trip, party, row, **kw):
        return self.service.book_deliver_marketer(trip, party, row, TODAY, "", kw.get("request") or self.k())

    def receive_marketer(self, trip, party, row, **kw):
        return self.service.book_receive_marketer(trip, party, row, TODAY, "", kw.get("request") or self.k())

    def stock_left(self, entry_id, line_no=1):
        return TripRepo(self.db).stock_balance(entry_id, line_no)

    def marketer(self, party):
        return MarketerRepo(self.db).gold_balance(int(party))


class StockReissueTests(CycleCase):
    def test_partial_then_whole_allocation_keeps_the_stored_grams_and_posted_amount(self):
        origin, item = self.stock_trip()
        target = self.issue([karat(K21, "5")])["trip_id"]
        base = self.total()
        first = self.issue_stock(target, [{"entry_id": item, "line_no": 1, "grams": "0.9"}])
        self.assertEqual((first["kind"], first["lines"][0]["posted"]), ("BK_ISS_STK", "0.891"))
        self.assertEqual(self.stock_left(item), (D("0.900"), D("0.892")))
        second = self.issue_stock(target, [{"entry_id": item, "line_no": 1, "grams": "0.900"}])
        self.assertEqual(second["lines"][0]["posted"], "0.892")                                   # the remainder: 0.891 + 0.892 = 1.783, never re-rounded
        self.assertEqual(self.stock_left(item), (D("0.000"), D("0.000")))
        line = next(l for l in self.db.book_lines if l["entry_id"] == int(second["entry_id"]))
        self.assertEqual((line["posting_basis"], line["posting_difference"]), ("ALLOCATED", D("0.000537")))          # legitimately above 0.0005
        self.assertEqual(self.total(), base)                                                      # the 21K total never moved: stock -> book
        self.refused("STOCK_ITEM_EMPTY", lambda: self.issue_stock(target, [{"entry_id": item, "line_no": 1, "grams": "0.001"}]))

    def test_a_positive_weight_may_carry_a_zero_posted_share(self):
        origin = self.issue([karat(K21, "2")])["trip_id"]
        back = self.ret(origin, [assay("400", "0.001")])                                          # 0.000457 g -> posted 0.000
        target = self.issue([karat(K21, "1")])["trip_id"]
        result = self.issue_stock(target, [{"entry_id": back["entry_id"], "line_no": 1, "grams": "0.001"}])
        self.assertEqual((result["lines"][0]["weight"], result["lines"][0]["posted"]), ("0.001", "0.000"))
        self.assertEqual(self.stock_left(back["entry_id"]), (D("0.000"), D("0.000")))

    def test_more_than_the_remaining_grams_is_refused(self):
        _, item = self.stock_trip()
        target = self.issue([karat(K21, "5")])["trip_id"]
        self.refused("STOCK_EXCEEDS_BALANCE", lambda: self.issue_stock(target, [{"entry_id": item, "line_no": 1, "grams": "1.801"}]))

    def test_reversal_restores_the_item_and_guards_the_origin(self):
        origin, item = self.stock_trip()
        target = self.issue([karat(K21, "5")])["trip_id"]
        issued = self.issue_stock(target, [{"entry_id": item, "line_no": 1, "grams": "0.9"}])
        self.refused("DEPENDENT_STOCK_USE_EXISTS", lambda: self.service.reverse(item, "خطأ"), error=CorrectionError)     # the origin return: its stock was used
        self.service.reverse(issued["entry_id"], "خطأ")
        self.assertEqual(self.stock_left(item), (D("1.800"), D("1.783")))
        rows = [m for m in self.db.assayed_moves if m["movement_type"] == "ISSUE_REVERSAL"]
        self.assertEqual([(m["grams_delta"], m["eq21_posted_delta"]) for m in rows], [(D("0.900"), D("0.891"))])
        self.service.reverse(item, "خطأ")                                                          # now the origin return can be undone

    def test_the_lock_order_is_trip_then_items_ascending(self):
        _, item = self.stock_trip()
        _, item2 = self.stock_trip()
        target = self.issue([karat(K21, "5")])["trip_id"]
        self.db.lock_log.clear()
        self.issue_stock(target, [{"entry_id": item2, "line_no": 1, "grams": "0.5"}, {"entry_id": item, "line_no": 1, "grams": "0.5"}])
        log = [x for x in self.db.lock_log if x[0] in ("trip", "stock_item")]
        self.assertEqual(log, [("trip",), ("stock_item", int(item), 1), ("stock_item", int(item2), 1)])


class SupplierDeliveryTests(CycleCase):
    def test_each_supplier_is_credited_in_its_own_karat_and_no_cash_or_scrap_moves(self):
        trip = self.issue([karat(K21, "20")])["trip_id"]
        scrap, cash = list(self.db.scrap), list(self.db.cash_moves)
        to21 = self.deliver_supplier(trip, SUP21, karat(K21, "7.924"))
        to18 = self.deliver_supplier(trip, SUP18, karat(K21, "7.924"))
        self.assertEqual((to21["credited_weight"], to21["target_karat"]), ("7.924", 21))
        self.assertEqual((to18["credited_weight"], to18["target_karat"]), ("9.245", 18))           # 7.924 x 21 / 18 = 9.244667 -> 9.245 (E5)
        self.assertEqual((self.db.scrap, self.db.cash_moves), (scrap, cash))
        moves = [(m["supplier_id"], m["karat_id"], m["weight_delta"]) for m in self.db.sup_gold]
        self.assertEqual(moves, [(10, 1, D("-7.924")), (21, 2, D("-9.245"))])                    # supplier karat ids: 21K = 1, 18K = 2 (never Eq21)
        self.assertEqual(self.book(trip), D("4.152"))
        self.assertEqual(self.db.settlements[int(to18["settlement_id"])]["source_kind"], "TRIP_BOOK")

    def test_an_assayed_line_is_credited_from_its_pure_gold(self):
        trip = self.issue([karat(K21, "20")])["trip_id"]
        self.ret(trip, [karat(K21, "1")])
        result = self.deliver_supplier(trip, SUP18, assay("875", "6"))
        self.assertEqual(result["credited_weight"], "7.000")                                     # 6 x 0.875 x 24 / 18

    def test_partial_deliveries_respect_the_book_and_one_line_per_document(self):
        trip = self.issue([karat(K21, "5")])["trip_id"]
        self.deliver_supplier(trip, SUP21, karat(K21, "2"))
        self.refused("BOOK_EXCEEDS_BALANCE", lambda: self.deliver_supplier(trip, SUP21, karat(K21, "3.001")))
        self.refused("LINE_SINGLE", lambda: self.deliver_supplier(trip, SUP21, [karat(K21, "1"), karat(K21, "1")]))
        self.refused("SUPPLIER_ROLE_REQUIRED", lambda: self.deliver_supplier(trip, MKT, karat(K21, "1")))

    def test_reversal_restores_the_stored_credit_and_the_direct_path_is_refused(self):
        trip = self.issue([karat(K21, "20")])["trip_id"]
        delivered = self.deliver_supplier(trip, SUP18, karat(K21, "7.924"))
        self.refused("CUSTODY_SETTLEMENT_REVERSE_FROM_TRIP", lambda: self.corrections.reverse("SUPPLIER_GOLD_SETTLEMENT", delivered["settlement_id"], "x"),
                     error=CorrectionError)
        self.service.reverse(delivered["entry_id"], "خطأ")
        self.assertEqual([m["weight_delta"] for m in self.db.sup_gold], [D("-9.245"), D("9.245")])
        self.assertEqual(self.book(trip), D("20.000"))


class MarketerTests(CycleCase):
    def test_deliver_then_partial_and_full_receipt_without_double_counting(self):
        trip = self.issue([karat(K21, "10")])["trip_id"]
        base = self.total()
        self.deliver_marketer(trip, MKT, karat(K21, "5"))
        self.assertEqual((self.marketer(MKT), self.book(trip), self.total()), (D("5.000"), D("5.000"), base))
        self.receive_marketer(trip, MKT, karat(K21, "2"))
        self.assertEqual((self.marketer(MKT), self.book(trip), self.total()), (D("3.000"), D("7.000"), base))
        self.refused("MARKETER_BALANCE_SHORT", lambda: self.receive_marketer(trip, MKT, karat(K21, "3.001")))
        self.receive_marketer(trip, MKT, karat(K21, "3"))
        self.assertEqual((self.marketer(MKT), self.book(trip), self.total()), (D("0.000"), D("10.000"), base))

    def test_the_entitlement_is_the_posted_21k_equivalent_of_any_assay(self):
        trip = self.issue([karat(K21, "10")])["trip_id"]
        self.deliver_marketer(trip, MKT, karat(K18, "7"))                                         # 7 x 18 / 21 = 6.000
        self.assertEqual(self.marketer(MKT), D("6.000"))
        self.refused("MARKETER_INACTIVE", lambda: self.deliver_marketer(trip, MKT_OFF, karat(K21, "1")))

    def test_reversals_and_the_used_entitlement_guard(self):
        trip = self.issue([karat(K21, "10")])["trip_id"]
        delivered = self.deliver_marketer(trip, MKT, karat(K21, "5"))
        received = self.receive_marketer(trip, MKT, karat(K21, "2"))
        self.refused("DEPENDENT_MARKETER_USE_EXISTS", lambda: self.service.reverse(delivered["entry_id"], "خطأ"), error=CorrectionError)
        self.service.reverse(received["entry_id"], "خطأ")
        self.service.reverse(delivered["entry_id"], "خطأ")
        self.assertEqual((self.marketer(MKT), self.book(trip)), (D("0.000"), D("10.000")))
        self.assertEqual(sorted(m["movement_type"] for m in self.db.mk_gold),
                         ["TRIP_BOOK_IN", "TRIP_BOOK_IN_REVERSAL", "TRIP_BOOK_OUT", "TRIP_BOOK_OUT_REVERSAL"])


class ReconciliationTests(CycleCase):
    def test_a_trip_with_phase2_kinds_reconciles_in_v2_and_finality_guards_them(self):
        trip = self.issue([karat(K21, "10")])["trip_id"]
        self.deliver_marketer(trip, MKT, karat(K21, "4"))
        self.deliver_supplier(trip, SUP18, karat(K21, "3"))
        source = TripRepo(self.db).reconciliation_source_current(trip)
        _, entries, _, digest, snapshot, _ = bs.build(source)
        self.assertEqual(bs.versions_for(entries), bs.CONTRACT_VERSIONS_V2)
        self.assertEqual(bs.verify_snapshot(snapshot), [])
        result = self.service.reconcile(trip, digest, None, "OPERATING_LOSS", None, dict(ALL), dict(bs.CONTRACT_VERSIONS_V2), self.k())
        row = self.db.reconciliations[int(result["entry_id"])]
        self.assertEqual((row["source_algorithm"], row["snapshot_format"]), ("book-recon-source-v2", "book-recon-snapshot-v2"))
        self.assertEqual(bs.verify_record(row), [])
        for call in (lambda: self.deliver_marketer(trip, MKT, karat(K21, "1")), lambda: self.receive_marketer(trip, MKT, karat(K21, "1")),
                     lambda: self.deliver_supplier(trip, SUP21, karat(K21, "1"))):
            self.refused("RECONCILIATION_FINAL", call)

    def test_v1_content_keeps_the_v1_bytes_and_a_v1_request_on_v2_content_is_refused(self):
        plain = self.issue([karat(K21, "10")])["trip_id"]
        _, entries, *_ = bs.build(TripRepo(self.db).reconciliation_source_current(plain))
        payload = bs.source_payload(int(plain), entries)
        self.assertEqual((payload["algorithm"], payload["source_kinds"]), ("book-recon-source-v1", ["BK_ISSUE", "BK_BUY", "BK_SELL", "BK_RETURN"]))
        self.assertNotIn("posting_basis", str(payload))
        mixed = self.issue([karat(K21, "10")])["trip_id"]
        self.deliver_marketer(mixed, MKT, karat(K21, "1"))
        digest = bs.build(TripRepo(self.db).reconciliation_source_current(mixed))[3]
        self.refused("RECONCILIATION_CONTRACT_MISMATCH",
                     lambda: self.service.reconcile(mixed, digest, None, "OPERATING_LOSS", None, dict(ALL), dict(bs.CONTRACT_VERSIONS), self.k()))

    def test_the_preview_judges_every_phase2_leg(self):
        trip = self.issue([karat(K21, "10")])["trip_id"]
        _, item = self.stock_trip()
        self.issue_stock(trip, [{"entry_id": item, "line_no": 1, "grams": "0.9"}])
        delivered = self.deliver_marketer(trip, MKT, karat(K21, "2"))
        supplied = self.deliver_supplier(trip, SUP21, karat(K21, "1"))
        clean = self.service.reconciliation_preview(trip)
        self.assertEqual([d["code"] for d in clean["diagnostics"]], [])
        next(m for m in self.db.mk_gold if m.get("trip_entry_id") == int(delivered["entry_id"]))["weight_grams"] = D("2.001")
        self.db.book_sup_links.pop(int(supplied["entry_id"]))
        next(m for m in self.db.assayed_moves if m["movement_type"] == "ISSUE")["eq21_posted_delta"] = D("-0.890")
        codes = {d["code"] for d in self.service.reconciliation_preview(trip)["diagnostics"]}
        self.assertTrue({"MARKETER_LEG_MISMATCH", "SUPPLIER_LINK_MISSING", "STOCK_ISSUE_MISMATCH"} <= codes, codes)


class SafetyTests(CycleCase):
    def test_replay_conflict_and_atomic_failure(self):
        trip = self.issue([karat(K21, "10")])["trip_id"]
        request = self.k()
        first = self.deliver_supplier(trip, SUP21, karat(K21, "1"), request=request)
        again = self.deliver_supplier(trip, SUP21, karat(K21, "1"), request=request)
        self.assertEqual((again["duplicate"], again["entry_id"]), (True, first["entry_id"]))
        self.refused("REQUEST_CONFLICT", lambda: self.deliver_supplier(trip, SUP21, karat(K21, "1.5"), request=request))
        for step, call in (("book_sup_link", lambda: self.deliver_supplier(trip, SUP21, karat(K21, "1"))), ("mk_gold", lambda: self.deliver_marketer(trip, MKT, karat(K21, "1"))),
                           ("stock_move", lambda: self.issue_stock(trip, [{"entry_id": self.stock_trip()[1], "line_no": 1, "grams": "0.5"}]))):
            with self.subTest(step=step):
                before = self.state()
                self.db.fail_at = step
                with self.assertRaises(RuntimeError):
                    call()
                self.db.fail_at = None
                if step != "stock_move":
                    self.assertEqual(self.state(), before)

    def test_the_counterparty_right_is_rechecked(self):
        trip = self.issue([karat(K21, "10")])["trip_id"]
        self.authorization.permissions = {"APP_LOGIN", "MARKET_TRIP_MANAGE"}
        with self.assertRaises(PermissionError):
            self.deliver_supplier(trip, SUP21, karat(K21, "1"))
        with self.assertRaises(PermissionError):
            self.deliver_marketer(trip, MKT, karat(K21, "1"))

    def test_before_058_every_existing_path_still_works(self):
        self.db.pre058 = True
        trip = self.issue([karat(K21, "10")])["trip_id"]
        self.ret(trip, [karat(K21, "2")])
        self.assertEqual(self.service.reconciliation_preview(trip)["diagnostics"], [])
        self.positions.position()


class StatementTests(unittest.TestCase):
    def test_the_line_columns_and_phase2_reads_are_guarded_by_the_058_capability(self):
        old = Recorder()
        MarketTripRepository(old).reconciliation_source(5)
        lines = next(" ".join(s.split()) for s in (x if isinstance(x, str) else x[0] for x in old.statements) if "FROM market_trip_book_lines" in s)
        self.assertIn("'INDEPENDENT' AS posting_basis", lines)                                     # ZeroRow: the capability check reads 0 -> pre-058 constants
        self.assertFalse(any("FROM market_trip_book_supplier_links" in (x if isinstance(x, str) else x[0]) for x in old.statements))   # only the capability check names it


if __name__ == "__main__":
    unittest.main()
