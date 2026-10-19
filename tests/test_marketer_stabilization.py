"""Stabilization of the marketer account (055) against the independent snapshot review: the marketer's 21K gold in the consolidated Gold Position and in the party
summary, location / total conservation along purchase -> settlement -> sale -> return -> reversals, and the request-key conflicts between the older custody /
holding / settlement flows and the marketer documents. Real services over the in-memory transactional database (tests/market_trip_fakes.py +
tests/marketer_fakes.py). NOT real-MySQL tests: the SQL, the locking and any concurrency are NOT exercised here."""
from contextlib import contextmanager
from decimal import Decimal as D
import unittest

from app.viewmodels.supplier_viewmodel import SupplierViewModel, held_gold_21k_equivalent
from repositories.request_keys import RequestKeyInUse
from services.gold_position_service import GoldPositionService, REQUIRED_PERMISSIONS
from services.market_trip_service import MarketTripService
from services.supplier_gold_settlement_service import SupplierGoldSettlementService
from tests.market_trip_fakes import (AccountsRepo, HoldingRepo, OWNER, ScrapRepo, SettlementRepo, SupplierPayRepo, TripRepo, session_for)
from tests.marketer_fakes import MarketerRepo
from tests.test_marketer_custody import DUAL, MARKETER, MarketerCase, SUPPLIER24, TODAY, line


class PositionReads:
    """The repositories GoldPositionService reads, all over the ONE fake database (the connection argument is ignored, as in the other fakes)."""

    def __init__(self, db):
        self.db, self.trips, self.holdings, self.marketers = db, TripRepo(db), HoldingRepo(db), MarketerRepo(db)

    def all_trip_ids(self):
        return [{"id": t["id"], "status": t["status"]} for t in sorted(self.db.trips.values(), key=lambda t: t["id"])]

    def custody_gold_by_trip(self):
        return {t["id"]: self.trips.custody_gold(t["id"]) for t in self.db.trips.values()}

    def custody_lots_by_trip(self):
        return {t["id"]: self.trips.custody_lots(t["id"]) for t in self.db.trips.values()}

    def book_groups_by_trip(self):
        return self.trips.book_groups_by_trip()

    def effective_reconciliations_display(self):
        return self.trips.effective_reconciliations_display()

    def assayed_stock_items(self):
        return self.trips.assayed_stock_items()

    def shop_scrap(self):
        return self.trips.shop_scrap()

    def shop_lots(self):
        return self.trips.shop_lots()

    def receipt_stock(self):
        return []

    def open_pools(self, **kw):
        return self.holdings.open_pools(**kw)

    def logo_open_pools(self, **kw):
        return self.holdings.logo_open_pools(**kw)

    def get_stock_weight_by_karat(self):
        return []

    def get_returned_pending_weight_by_karat(self):
        return []

    def party_gold_balances(self, party_id=None):
        return self.marketers.party_gold_balances(party_id)


class _Authorization:
    def require_permission_read(self, connection, actor, code):
        if code not in actor.permissions:
            raise PermissionError(code)


class StabilizationCase(MarketerCase):
    def setUp(self):
        super().setUp()
        self.owner = session_for(OWNER | {"MANAGEMENT_REVIEW"})
        reads = PositionReads(self.db)

        @contextmanager
        def one_snapshot():
            yield object()
        self.position_service = GoldPositionService(self.owner, transaction_factory=one_snapshot, authorization=_Authorization(), trip_repository=lambda c: reads,
                                                    holding_repository=lambda c: reads, inventory_repository=lambda c: reads, marketer_repository=lambda c: reads)
        self.trips = MarketTripService(self.session, transaction_factory=self.db.transaction, repository=TripRepo, accounts=AccountsRepo, scrap=ScrapRepo,
                                       authorization=self.authorization, corrections=self.corrections, supplier_payments=SupplierPayRepo,
                                       gold_settlements=SettlementRepo, holdings=HoldingRepo)
        self.direct = SupplierGoldSettlementService(self.session, transaction_factory=self.db.transaction, repository=SettlementRepo, scrap=ScrapRepo,
                                                    authorization=self.authorization, corrections=self.corrections)

    def position(self):
        return self.position_service.position()


class PositionConservationTests(StabilizationCase):
    """Figures are measured against what the fixture database already holds (its own Scrap), so only the marketer cycle shows up."""

    def setUp(self):
        super().setUp()
        p = self.position()
        self.base = {"scrap": p["shop"]["parts"]["scrap"]["grams"], "total": p["summary"]["total_grams"], "eq21": p["summary"]["total_eq21"]}

    def figures(self):
        p = self.position()
        s = p["summary"]
        return {"marketer": p["intermediary"]["parts"]["marketer"]["grams"], "scrap": p["shop"]["parts"]["scrap"]["grams"] - self.base["scrap"],
                "total": s["total_grams"] - self.base["total"], "eq21": s["total_eq21"] - self.base["eq21"]}

    def test_the_total_follows_the_physical_gold_and_a_location_change_never_changes_it(self):
        self.assertEqual(self.figures(), {"marketer": D("0.000"), "scrap": D("0.000"), "total": D("0.000"), "eq21": D("0.000")})
        self.hand("200000")
        self.assertEqual(self.figures()["total"], D("0.000"))                          # cash is never gold
        purchase = self.buy([line("10", "60000")])
        self.assertEqual(self.figures(), {"marketer": D("10.000"), "scrap": D("0.000"), "total": D("10.000"), "eq21": D("10.000")})
        settlement = self.settle("4", SUPPLIER24)                                       # physical shop-owned gold leaves: the supplier is credited instead
        self.assertEqual(self.figures(), {"marketer": D("6.000"), "scrap": D("0.000"), "total": D("6.000"), "eq21": D("6.000")})
        sale = self.sell("2", "12000")
        self.assertEqual(self.figures(), {"marketer": D("4.000"), "scrap": D("0.000"), "total": D("4.000"), "eq21": D("4.000")})
        back = self.gold_back("3")                                                      # location only: marketer -> shop Scrap
        self.assertEqual(self.figures(), {"marketer": D("1.000"), "scrap": D("3.000"), "total": D("4.000"), "eq21": D("4.000")})
        self.reverse(back)                                                              # the compensations restore the location, not a new quantity
        self.assertEqual(self.figures(), {"marketer": D("4.000"), "scrap": D("0.000"), "total": D("4.000"), "eq21": D("4.000")})
        self.reverse(sale)
        self.assertEqual(self.figures()["total"], D("6.000"))
        self.service.reverse_settlement(settlement["id"], "تصحيح")
        self.assertEqual(self.figures(), {"marketer": D("10.000"), "scrap": D("0.000"), "total": D("10.000"), "eq21": D("10.000")})
        self.reverse(purchase)
        self.assertEqual(self.figures(), {"marketer": D("0.000"), "scrap": D("0.000"), "total": D("0.000"), "eq21": D("0.000")})

    def test_the_position_is_read_from_the_signed_ledger_including_compensations(self):
        self.hand("100000")
        done = self.buy([line("5", "30000")])
        self.reverse(done)
        rows = [m["weight_delta"] for m in self.db.mk_gold]
        self.assertEqual(rows, [D("5.000"), D("-5.000")])                               # the original stays counted beside its compensation
        self.assertEqual(self.figures()["total"], D("0.000"))
        self.assertEqual(self.position()["intermediary"]["parties"], [])

    def test_a_dual_role_party_shows_the_marketer_gold_once_and_never_the_supplier_account(self):
        self.hand("100000", party=DUAL)
        self.buy([line("5", "30000")], party=DUAL)
        parties = self.position()["intermediary"]["parties"]
        self.assertEqual([(p["party_id"], p["marketer"]["grams"]) for p in parties], [(DUAL, D("5.000"))])
        self.assertNotIn("payable", repr(self.position()).lower())
        self.service.settle_supplier(DUAL, DUAL, "2", True, TODAY, "", "", self.key())   # a self-settlement: the supplier account is credited, the holding shrinks
        self.assertEqual(self.position()["intermediary"]["parts"]["marketer"]["grams"], D("3.000"))
        self.assertEqual(self.figures()["total"], D("3.000"))


class PartySummaryTests(unittest.TestCase):
    """The party page's «ذهب المحل لديه بصفته مسوقجي» headline comes from the SAME detail read as the pools (data["marketer_gold"])."""

    @staticmethod
    def panel(**data):
        vm = SupplierViewModel(session_for(OWNER))
        base = {"pools": [], "history": [], "logo_pools": [], "logo_history": [], "settlements": [], "receipts": [], "can_manage": True, "can_settle": True,
                "can_reverse": True, "can_receive": True, "can_reverse_receipt": True, "total": D("0"), "pure": D("0")}
        vm._detail = {"supplier": {"id": "20", "is_intermediary": True}, "holdings": {**base, **data}}
        return vm.holdingsPanel

    def test_a_party_holding_only_marketer_gold_still_shows_the_summary(self):
        panel = self.panel(marketer_gold=D("10.000"))
        self.assertTrue(panel["goldPositionVisible"])
        self.assertIn("10.000", panel["goldPositionText"])
        self.assertIn("ما يعادل", panel["goldPositionText"])
        self.assertEqual([g["text"] for g in panel["holdGroups"]], ["عهدة المسوقجي (عيار 21): 10.000 جرام"])      # the component that explains the total
        self.assertFalse(panel["empty"])

    def test_without_any_held_gold_the_summary_stays_hidden(self):
        panel = self.panel(marketer_gold=D("0.000"))
        self.assertFalse(panel["goldPositionVisible"])
        self.assertTrue(panel["empty"])

    def test_a_mixed_party_includes_each_source_exactly_once(self):
        logo = {"pool_id": "5", "karat_value": 18, "balance": D("6.300"), "weight": D("6.300"), "document": "IG000001", "custody_entry": "MT000001-1",
                "trip_code": "MT000001", "date": "2026-10-10", "returnable": True, "reason": "", "settleable": True}
        panel = self.panel(logo_pools=[logo], marketer_gold=D("4.000"))
        expected = (D("6.300") * 18 / 21 + D("4")).quantize(D("0.001"))
        self.assertIn(f"{expected:,.3f}", panel["goldPositionText"])
        texts = [g["text"] for g in panel["holdGroups"]]
        self.assertEqual(len(texts), 2)
        self.assertTrue(any("لوجو عيار 18" in t for t in texts) and any("عهدة المسوقجي" in t for t in texts))

    def test_an_unknown_basis_is_never_presented_as_a_complete_equivalent(self):
        self.assertIsNone(held_gold_21k_equivalent([{"balance": D("2"), "fineness": None}], [], D("10")))
        self.assertEqual(held_gold_21k_equivalent([], [{"balance": D("6.3"), "karat_value": 18}], D("4")).quantize(D("0.001")), D("9.400"))
        self.assertEqual(held_gold_21k_equivalent([], [], D("10")), D("10"))


class RequestKeyDirectionTests(StabilizationCase):
    """The same key in two different flows of the family. Sequential checks only: NO claim about concurrent reuse (see REVIEW-STABILIZATION.md)."""

    def test_a_marketer_document_key_cannot_create_an_unrelated_custody_entry(self):
        key = self.key()
        done = self.hand("1000", key=key)
        self.refused("REQUEST_CONFLICT", lambda: self.trips.issue("", "500", [], TODAY, "", key), error=ValueError)
        self.assertEqual(len(self.db.entries), 0)                                       # nothing was written (the refusal rolled the transaction back)
        self.assertEqual(self.hand("1000", key=key)["id"], done["id"])                  # the marketer flow still replays its own request

    def test_a_marketer_document_key_cannot_create_an_unrelated_supplier_settlement(self):
        self.hand("100000")
        self.buy([line("10", "60000")])
        self.gold_back("5")                                                             # 5 g of 21K in the shop Scrap
        key = self.key()
        self.hand("1000", key=key)
        self.refused("REQUEST_CONFLICT", lambda: self.direct.settle(SUPPLIER24, "1", "1", TODAY, "", "", key), error=ValueError)
        self.assertEqual(len(self.db.settlements), 0)

    def test_a_legacy_flow_key_cannot_create_a_marketer_document(self):
        key = self.key()
        self.trips.issue("", "500", [], TODAY, "", key)
        self.refused("REQUEST_CONFLICT", lambda: self.hand("1000", key=key))
        self.refused("REQUEST_CONFLICT", lambda: self.service.cash_return(MARKETER, "1", TODAY, "", "", key))
        self.assertEqual(len(self.db.mk_docs), 0)

    def test_a_supplier_payment_key_cannot_create_a_marketer_document(self):
        key = self.key()
        self.db.sm_payments[1] = {"id": 1, "request_key": key}                          # an SM document that already owns the key
        self.refused("REQUEST_CONFLICT", lambda: self.hand("1000", key=key))
        self.assertEqual(len(self.db.mk_docs), 0)

    def test_a_legacy_key_cannot_create_a_marketer_settlement_either(self):
        self.hand("100000")
        self.buy([line("10", "60000")])
        key = self.key()
        self.trips.issue("", "500", [], TODAY, "", key)
        self.refused("REQUEST_CONFLICT", lambda: self.service.settle_supplier(MARKETER, SUPPLIER24, "1", False, TODAY, "", "", key))
        self.assertEqual(len(self.db.settlements), 0)

    def test_exact_replay_in_its_own_flow_writes_nothing_even_after_a_reversal(self):
        key = self.key()
        done = self.hand("1000", key=key)
        trip_key = self.key()
        issued = self.trips.issue("", "500", [], TODAY, "", trip_key)
        before = self.state()
        self.assertEqual((self.hand("1000", key=key)["id"], self.trips.issue("", "500", [], TODAY, "", trip_key)["entry_id"]), (done["id"], issued["entry_id"]))
        self.assertEqual(self.state(), before)                                           # exact replay: no write at all
        self.reverse(done)
        before = self.state()
        again = self.hand("1000", key=key)
        self.assertEqual((again["id"], again["duplicate"]), (done["id"], True))          # the already-supported post-reversal replay
        self.assertEqual(self.state(), before)

    def test_a_changed_payload_or_actor_or_operation_is_still_a_conflict(self):
        key = self.key()
        self.hand("1000", key=key)
        self.refused("REQUEST_CONFLICT", lambda: self.hand("1001", key=key))
        self.refused("REQUEST_CONFLICT", lambda: self.back("1000", key=key))             # another operation on the same key
        another = session_for(OWNER, user_id=99)
        other = self.make_service(another, type(self.authorization)(another))
        self.refused("REQUEST_CONFLICT", lambda: other.cash_handover(MARKETER, "1000", TODAY, "مرجع", "ملاحظة", key))

    def test_the_guard_is_the_chokepoint_of_every_older_insert(self):
        """The fakes mirror repositories.request_keys; the real repository code is covered by tests/test_request_keys.py."""
        key = self.key()
        self.hand("1000", key=key)
        for insert in (lambda: TripRepo(self.db).insert_entry(trip_id=1, entry_no=1, kind="ISSUE", entry_date=TODAY, notes="", key=key, digest="d",
                                                              actor=self.session.require_authenticated(), now=None),
                       lambda: SupplierPayRepo(self.db).insert_header(supplier_id=23, method="CUSTODY", cash_account_id=None, bank_account_id=None, amount=D("1"),
                                                                      payment_date=TODAY, reference="", notes="", before=D("0"), after=D("-1"), key=key, digest="d",
                                                                      actor=self.session.require_authenticated(), now=None)):
            with self.assertRaises(RequestKeyInUse):
                insert()


if __name__ == "__main__":
    unittest.main()
