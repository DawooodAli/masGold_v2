"""Whole assayed lot holdings at a market intermediary (048). Real MarketTripService, IntermediaryHoldingService and CorrectionService over the
in-memory transactional database of tests/market_trip_fakes.py (a failed transaction restores every table; the 048 CHECK / unique / composite
foreign keys are mirrored as asserts). NOT real-MySQL tests: the SQL, the constraints and the lock behaviour were exercised on masgold_rehearsal
(tests/test_intermediary_holdings_mysql.py, opt-in)."""
from decimal import Decimal as D
from unittest.mock import patch
import unittest

from services.correction_service import CorrectionError
from services.intermediary_holding_service import IntermediaryHoldingService
from services.market_trip_service import MarketTripService, TripError
from tests.auth_fakes import FakeAuthorization
from tests.market_trip_fakes import (AccountsRepo, Corrections, Customers, HoldingRepo, Invoices, OWNER, ScrapRepo, SettlementRepo, SupplierPayRepo,
                                     TripRepo, session_for)
from tests.test_market_trips import K21, TODAY, TripCase, key

OWNER_PARTY, DUAL, INACTIVE_PARTY, SUPPLIER_ONLY = "20", "21", "22", "10"
ERASED = "PARTY_NOT_FOUND"


def hkey(n):
    return "66666666-6666-4666-8666-%012d" % n


class HoldingCase(TripCase):
    """Custody MT000001: 20 g of 21K (MELT) melted into a MAIN bar of 10 g at 875, a RESIDUE of 1 g at 500 and a RESIDUE of 2 g with unknown assay."""

    def setUp(self):
        super().setUp()
        started = patch("repositories.supplier_gold_settlement_repository.SupplierGoldSettlementRepository", SettlementRepo)
        started.start()
        self.addCleanup(started.stop)
        self.db.suppliers[20] = {"id": 20, "name": "مسوقجي أول", "is_active": 1, "is_supplier": False, "is_intermediary": True, "karat_id": None}
        self.db.suppliers[21] = {"id": 21, "name": "مورد ومسوقجي", "is_active": 1, "is_supplier": True, "is_intermediary": True, "karat_id": 1}
        self.db.suppliers[22] = {"id": 22, "name": "مسوقجي موقوف", "is_active": 0, "is_supplier": False, "is_intermediary": True, "karat_id": None}
        self.holding = IntermediaryHoldingService(self.session, transaction_factory=self.db.transaction, repository=TripRepo, holdings=HoldingRepo,
                                                  parties=SettlementRepo, authorization=self.authorization)
        self.trip = self.issue("", [(K21, "20", "MELT")])["trip_id"]
        self.n += 1
        melt = self.service.melt(self.trip, [{"karat_id": K21, "weight": "20"}], TODAY, "ورشة", {"weight": "10", "fineness": "875"},
                                 [{"weight": "1", "fineness": "500"}, {"weight": "2", "fineness": ""}], "", "", "", key(900 + self.n))
        lots = sorted((l for l in self.db.lots.values() if l["entry_id"] == int(melt["entry_id"])), key=lambda l: l["lot_no"])
        self.main, self.residue, self.unknown = (l["id"] for l in lots)
        self.baseline = self.untouched()
        self.h = 0

    # ------------------------------------------------------------ helpers
    def untouched(self):
        import copy
        return copy.deepcopy((self.db.cash_moves, self.db.scrap, self.db.sup_gold, self.db.sm_making, self.db.shop_moves, self.db.settlements))

    def hand(self, lot=None, party=OWNER_PARTY, trip=None, request=None, service=None, weight=None, when=TODAY, reference="", notes=""):
        self.h += 1
        return (service or self.holding).hand_over_lot(trip or self.trip, party, self.main if lot is None else lot, when, reference, notes,
                                                       request or hkey(self.h), weight=weight)

    def back(self, pool=None, confirmed=True, request=None, service=None, when=TODAY):
        self.h += 1
        pool = pool or max(self.db.holding_pools)
        return (service or self.holding).return_holding(pool, confirmed, when, "", "", request or hkey(1000 + self.h))

    def held_by(self, party=OWNER_PARTY):
        return sum((self.holding_repo().pool_balance(p["id"]) for p in self.db.holding_pools.values() if p["party_id"] == int(party)), D("0.000"))

    def holding_repo(self):
        return HoldingRepo(self.db)

    def custody_lots(self, trip=None):
        return {l["lot_id"]: l["outstanding"] for l in self.service.trip_detail(trip or self.trip)["custody"]["lots"]}

    def places(self, lot=None):
        """(custody grams, shop grams, held grams) of one lot: the invariant is that they never exceed the bar and add up to it."""
        lot = self.main if lot is None else lot
        custody = sum((l["outstanding"] for t in self.db.trips for l in self.service.trip_detail(str(t))["custody"]["lots"] if l["lot_id"] == str(lot)), D("0"))
        shop = sum((m["weight_delta"] for m in self.db.shop_moves if m["lot_id"] == lot), D("0"))
        held = self.holding_repo().lot_held_total(lot)
        self.assertEqual(custody + shop + held, self.db.lots[lot]["weight_grams"], "the bar must be in exactly one place")
        return custody, shop, held

    def refuse(self, code, call, error=TripError):
        before = self.state()
        with self.assertRaises(error) as context:
            call()
        self.assertEqual(getattr(context.exception, "code", None), code)
        self.assertEqual(self.state(), before, "a refusal left a trace")
        return context.exception

    def entry_ids(self, kind):
        return [e["id"] for e in sorted(self.db.entries.values(), key=lambda e: e["id"]) if e["entry_kind"] == kind]

    def as_user(self, permissions, user_id=9):
        session = session_for(permissions, user_id)
        authorization = FakeAuthorization(session)
        corrections = __import__("services.correction_service", fromlist=["CorrectionService"]).CorrectionService(
            session, authorization=authorization, transaction_factory=self.db.transaction, invoice_repository=Invoices,
            correction_repository=Corrections, customer_repository=Customers)
        trips = MarketTripService(session, transaction_factory=self.db.transaction, repository=TripRepo, accounts=AccountsRepo, scrap=ScrapRepo,
                                  authorization=authorization, corrections=corrections, supplier_payments=SupplierPayRepo, gold_settlements=SettlementRepo,
                                  holdings=HoldingRepo)
        holding = IntermediaryHoldingService(session, transaction_factory=self.db.transaction, repository=TripRepo, holdings=HoldingRepo,
                                             parties=SettlementRepo, authorization=authorization)
        return trips, holding


class HandoverTests(HoldingCase):
    def test_a_whole_known_lot_leaves_custody_and_credits_a_separate_holding_atomically(self):
        lot_before = dict(self.db.lots[self.main])
        done = self.hand()
        self.assertEqual((done["kind"], done["document_code"], done["duplicate"], done["weight"]), ("LOT_HOLD", "IH000001", False, "10.000"))
        self.assertEqual(self.places(), (D("0"), D("0"), D("10.000")))
        self.assertNotIn(str(self.main), self.custody_lots())
        self.assertEqual(self.db.lots[self.main], lot_before)                                    # identity, melt origin, kind, weight and assay are untouched
        pool = self.db.holding_pools[1]
        self.assertEqual((pool["party_id"], pool["lot_id"], pool["weight_grams"], pool["fineness_permille"]), (20, self.main, D("10.000"), D("875.0")))
        self.assertEqual(self.untouched(), self.baseline)                                        # no cash, Scrap, supplier, making, shop-lot or SG movement
        entry = self.db.entries[int(done["entry_id"])]
        self.assertEqual((entry["entry_kind"], entry["trip_id"]), ("LOT_HOLD", int(self.trip)))
        move = next(m for m in self.db.lot_moves if m["entry_id"] == entry["id"])
        self.assertEqual((move["movement_type"], move["weight_delta"]), ("HOLD_OUT", D("-10.000")))
        self.assertEqual([r["event_type"] for r in self.db.audit_rows][-2:], ["MARKET_TRIP_LOT_HOLD", "INTERMEDIARY_HOLDING_HANDOVER"])

    def test_known_assay_residues_hand_over_too_and_unknown_assay_is_refused(self):
        self.hand(self.residue)
        self.refuse("LOT_ASSAY_UNKNOWN", lambda: self.hand(self.unknown))
        self.assertEqual(self.places(self.residue), (D("0"), D("0"), D("1.000")))
        self.assertEqual(self.places(self.unknown), (D("2.000"), D("0"), D("0")))               # an unknown assay is never inferred

    def test_partial_quantities_and_partially_available_lots_are_refused(self):
        self.refuse("LOT_PARTIAL_NOT_ALLOWED", lambda: self.hand(weight="9.999"))
        repo = TripRepo(self.db)                                                                # an earlier build left only 4 g in this custody
        entry = repo.insert_entry(trip_id=int(self.trip), entry_no=repo.next_entry_no(int(self.trip)), kind="LOT_RETURN", entry_date=__import__("datetime").date(2026, 10, 10),
                                  notes=None, key=key(777), digest="x" * 64, actor=type("A", (), {"user_id": 7, "employee_id": None})(), now=self.db.now)
        repo.insert_lot_movement(lot_id=self.main, entry_id=entry, movement_type="SHOP_RETURN", weight_delta=D("-6"))
        self.refuse("LOT_PARTIAL_NOT_ALLOWED", lambda: self.hand())
        self.assertEqual(self.holding_repo().lot_held_total(self.main), 0)

    def test_the_party_must_be_an_active_intermediary(self):
        self.refuse("INTERMEDIARY_ROLE_REQUIRED", lambda: self.hand(party=SUPPLIER_ONLY))
        self.refuse("PARTY_INACTIVE", lambda: self.hand(party=INACTIVE_PARTY))
        self.refuse("PARTY_NOT_FOUND", lambda: self.hand(party="999"))
        done = self.hand(party=DUAL)                                                             # a dual-role party is a valid recipient ...
        self.assertEqual(done["party_id"], DUAL)
        self.assertEqual((self.db.sup_gold, self.db.settlements), ([], {}))                      # ... and its supplier account is not touched

    def test_a_closed_custody_and_a_lot_that_is_not_in_it_are_refused(self):
        other = self.issue("100")["trip_id"]
        self.refuse("LOT_NOT_IN_CUSTODY", lambda: self.hand(trip=other))
        self.refuse("LOT_NOT_FOUND", lambda: self.hand(lot=999999))
        self.service.return_lots(self.trip, [{"lot_id": str(self.main)}], TODAY, "", key(8301))     # the bar goes to the shop: nothing to hand over
        self.refuse("LOT_NOT_IN_CUSTODY", lambda: self.hand())
        self.service.reverse(max(self.db.entries), "x")
        self.assertEqual(self.hand()["kind"], "LOT_HOLD")

    def test_a_closed_custody_refuses_a_new_handover(self):
        self.service.return_lots(self.trip, [{"lot_id": str(l)} for l in (self.main, self.residue, self.unknown)], TODAY, "", key(8302))
        self.service.reverse(max(self.db.entries), "x")
        self.service.return_lots(self.trip, [{"lot_id": str(l)} for l in (self.main, self.residue, self.unknown)], TODAY, "", key(8303))
        self.service.close(self.trip)
        self.refuse("TRIP_CLOSED", lambda: self.hand())

    def test_rights_are_rechecked_in_the_transaction_and_the_actor_is_the_session_user(self):
        for permissions in (OWNER - {"INTERMEDIARY_HOLDING_MANAGE"}, OWNER - {"MARKET_TRIP_MANAGE"}):
            trips, holding = self.as_user(permissions)
            before = self.state()
            with self.assertRaises(PermissionError):
                self.hand(service=holding)
            self.assertEqual(self.state(), before)
        self.authorization.permissions = OWNER - {"INTERMEDIARY_HOLDING_MANAGE"}                  # revoked in the database while the session is still open
        before = self.state()
        with self.assertRaises(PermissionError):
            self.hand()
        self.assertEqual(self.state(), before)
        self.authorization.permissions = OWNER
        self.hand()
        self.assertEqual(self.db.holding_docs[1]["performed_by_user_id"], 7)                      # taken from the session, never from the caller

    def test_every_stage_failure_leaves_no_document_pool_movement_or_link(self):
        before = self.state()
        for step in ("pool", "entry", "document", "lot_movement", "holding_movement", "holding_link", "audit", "holding_audit"):
            self.db.fail_at = step
            with self.assertRaises(RuntimeError):
                self.hand()
            self.db.fail_at = None
            self.assertEqual(self.state(), before, f"a failure at {step} left a trace")
        self.hand()
        self.assertEqual(self.places(), (D("0"), D("0"), D("10.000")))


class ReplayTests(HoldingCase):
    def test_exact_replay_returns_the_original_records_even_after_the_lot_is_held_returned_or_reversed(self):
        request = hkey(500)
        first = self.hand(request=request)
        after = self.state()
        again = self.hand(request=request)
        self.assertEqual((again["duplicate"], again["document_id"], again["entry_id"]), (True, first["document_id"], first["entry_id"]))
        self.assertEqual(self.state(), after)
        self.back()
        self.assertEqual(self.hand(request=request)["document_id"], first["document_id"])        # availability is not derived again
        self.service.reverse(max(self.db.entries), "x")
        self.service.reverse(first["entry_id"], "x")
        self.assertEqual(self.hand(request=request)["duplicate"], True)

    def test_a_different_payload_actor_or_flow_with_the_same_key_is_a_conflict(self):
        request = hkey(501)
        self.hand(request=request)
        self.refuse("REQUEST_CONFLICT", lambda: self.hand(lot=self.residue, request=request))
        self.refuse("REQUEST_CONFLICT", lambda: self.hand(party=DUAL, request=request))
        self.refuse("REQUEST_CONFLICT", lambda: self.hand(request=request, notes="مختلف"))
        self.refuse("REQUEST_CONFLICT", lambda: self.back(request=request))                       # the return flow cannot reuse a handover key
        self.refuse("REQUEST_CONFLICT", lambda: self.issue("5", request=request))                 # nor any other custody flow
        self.refuse("REQUEST_CONFLICT", lambda: self.service.settle_supplier_lot(self.trip, "21", str(self.residue), TODAY, "", "", request))
        direct = __import__("services.supplier_gold_settlement_service", fromlist=["x"]).SupplierGoldSettlementService(
            self.session, transaction_factory=self.db.transaction, repository=SettlementRepo, scrap=ScrapRepo, authorization=self.authorization,
            corrections=self.corrections)
        before = self.state()
        with self.assertRaises(__import__("services.supplier_rules", fromlist=["x"]).SupplierRuleError) as context:
            direct.settle("21", K21, "1", TODAY, "", "", request)                                 # a direct supplier settlement may not reuse it either
        self.assertEqual(context.exception.code, "REQUEST_CONFLICT")
        self.assertEqual(self.state(), before)

    def test_a_key_already_used_by_a_custody_entry_cannot_start_a_handover(self):
        request = hkey(502)
        self.issue("5", request=request)
        self.refuse("REQUEST_CONFLICT", lambda: self.hand(request=request))
        self.assertEqual(self.db.holding_docs, {})


class ReturnTests(HoldingCase):
    def test_return_needs_the_explicit_intact_confirmation_and_restores_the_same_lot_to_the_original_custody(self):
        self.hand()
        self.refuse("INTACT_CONFIRMATION_REQUIRED", lambda: self.back(confirmed=False))          # a full balance alone certifies nothing
        self.assertEqual(self.places(), (D("0"), D("0"), D("10.000")))
        done = self.back()
        self.assertEqual((done["kind"], done["document_code"], done["trip_id"]), ("HOLD_RETURN", "IH000002", self.trip))
        self.assertEqual(self.places(), (D("10.000"), D("0"), D("0")))
        self.assertEqual(self.custody_lots()[str(self.main)], D("10.000"))
        document = self.db.holding_docs[2]
        self.assertEqual((document["doc_kind"], document["intact_confirmation_code"], document["confirmed_by_user_id"]), ("RETURN", "SAME_BAR_WHOLE_UNDIVIDED", 7))
        self.assertIsNotNone(document["confirmed_at"])
        self.assertEqual(self.untouched(), self.baseline)
        self.refuse("HOLDING_NOT_AVAILABLE", lambda: self.back(pool=1))                          # cannot be returned twice

    def test_return_requires_the_original_custody_to_be_open_and_reopening_is_explicit(self):
        self.hand()
        # the custody holds only the residues now: put them back to the shop, then close it
        for lot in (self.residue, self.unknown):
            self.service.return_lots(self.trip, [{"lot_id": str(lot)}], TODAY, "", key(8100 + lot))
        self.assertEqual(self.service.close(self.trip)["status"], "CLOSED")                       # its own outstanding is zero although gold is outside
        detail = self.service.trip_detail(self.trip)
        self.assertEqual([p["balance"] for p in detail["external_holdings"]], [D("10.000")])      # still visibly outstanding at the intermediary
        self.refuse("TRIP_CLOSED", lambda: self.back())
        self.assertEqual(self.places(), (D("0"), D("0"), D("10.000")))
        self.service.reopen(self.trip, "استلام السبكة")
        self.back()
        self.assertEqual(self.places(), (D("10.000"), D("0"), D("0")))
        self.assertTrue(self.refused_close())

    def refused_close(self):
        with self.assertRaises(TripError) as context:
            self.service.close(self.trip)
        return context.exception.code == "OUTSTANDING_CUSTODY"

    def test_an_inactive_party_still_receives_its_return_and_a_second_handover_creates_a_new_source(self):
        self.hand()
        self.db.suppliers[20]["is_active"] = 0                                                    # deactivated while it holds the gold
        self.back()
        self.db.suppliers[20]["is_active"] = 1
        self.hand()
        self.assertEqual(sorted(self.db.holding_pools), [1, 2])                                    # no permanent uniqueness per lot
        self.assertEqual(self.places(), (D("0"), D("0"), D("10.000")))
        self.assertEqual([self.holding_repo().pool_balance(p) for p in (1, 2)], [D("0.000"), D("10.000")])

    def test_sources_stay_separate_even_when_assays_match(self):
        self.hand()
        self.hand(self.residue)
        self.assertEqual(sorted(p["lot_id"] for p in self.db.holding_pools.values()), sorted([self.main, self.residue]))
        self.assertEqual(self.held_by(), D("11.000"))
        self.back(pool=2)
        self.assertEqual((self.holding_repo().pool_balance(1), self.holding_repo().pool_balance(2)), (D("10.000"), D("0.000")))

    def test_every_stage_failure_of_a_return_leaves_no_trace(self):
        self.hand()
        before = self.state()
        for step in ("entry", "document", "lot_movement", "holding_movement", "holding_link", "audit", "holding_audit"):
            self.db.fail_at = step
            with self.assertRaises(RuntimeError):
                self.back()
            self.db.fail_at = None
            self.assertEqual(self.state(), before, f"a failure at {step} left a trace")
        self.back()

    def test_return_rights_and_unknown_source(self):
        self.refuse("HOLDING_NOT_FOUND", lambda: self.back(pool=99))
        self.hand()
        _, holding = self.as_user(OWNER - {"INTERMEDIARY_HOLDING_MANAGE"})
        before = self.state()
        with self.assertRaises(PermissionError):
            self.back(service=holding)
        self.assertEqual(self.state(), before)

    def test_replay_of_a_return_is_exact_and_conflicts_are_refused(self):
        self.hand()
        request = hkey(900)
        first = self.back(request=request)
        self.assertEqual(self.back(request=request)["document_id"], first["document_id"])
        self.refuse("REQUEST_CONFLICT", lambda: self.back(confirmed=False, request=request))


class SimultaneousAvailabilityTests(HoldingCase):
    def test_a_held_lot_cannot_be_used_anywhere_else(self):
        self.hand()
        self.refuse("LOT_NOT_IN_CUSTODY", lambda: self.hand())
        self.refuse("LOT_NOT_IN_CUSTODY", lambda: self.service.return_lots(self.trip, [{"lot_id": str(self.main)}], TODAY, "", key(8001)))
        self.refuse("LOT_NOT_IN_CUSTODY", lambda: self.service.settle_supplier_lot(self.trip, "21", str(self.main), TODAY, "", "", key(8002)))
        other = self.issue("100")["trip_id"]
        self.refuse("LOT_NOT_IN_SHOP", lambda: self.service.issue_lots(other, [{"lot_id": str(self.main)}], TODAY, "", key(8003)))
        self.refuse("LOT_NOT_IN_SHOP", lambda: self.service.create_trip("", [], [{"lot_id": str(self.main)}], TODAY, "", key(8004)))
        self.assertEqual(self.places(), (D("0"), D("0"), D("10.000")))

    def test_melt_issue_and_earlier_return_dependencies_are_protected(self):
        self.hand()
        with self.assertRaises(CorrectionError) as melt:
            self.service.reverse(self.entry_ids("MELT")[0], "x")
        self.assertEqual(melt.exception.code, "DEPENDENT_LOT_USE_EXISTS")
        self.service.return_lots(self.trip, [{"lot_id": str(self.residue)}], TODAY, "", key(8005))
        returned = max(self.db.entries)
        # a bar reissued from the shop into another custody and handed over: the reissue cannot be reversed underneath the handover
        other = self.issue("100")["trip_id"]
        self.service.issue_lots(other, [{"lot_id": str(self.residue)}], TODAY, "", key(8006))
        issue_entry = max(self.db.entries)
        self.hand(self.residue, trip=other)
        with self.assertRaises(CorrectionError) as reissue:
            self.service.reverse(issue_entry, "x")
        self.assertEqual(reissue.exception.code, "DEPENDENT_LOT_RETURN_EXISTS")
        with self.assertRaises(CorrectionError) as earlier:                                       # the earlier shop return is behind the reissue
            self.service.reverse(returned, "x")
        self.assertIn(earlier.exception.code, ("SHOP_LOT_STOCK_INSUFFICIENT", "DEPENDENT_LOT_USE_EXISTS", "DEPENDENT_LOT_RETURN_EXISTS"))
        self.assertEqual(self.places(self.residue), (D("0"), D("0"), D("1.000")))


class ReversalTests(HoldingCase):
    def test_reversing_a_handover_restores_custody_and_compensates_the_holding_without_excluding_the_original(self):
        done = self.hand()
        moves_before = len(self.db.holding_moves)
        reversal = self.service.reverse(done["entry_id"], "تسليم بالخطأ")
        self.assertEqual(self.places(), (D("10.000"), D("0"), D("0")))
        self.assertEqual(len(self.db.holding_moves), moves_before + 1)                           # the original stays; a compensation is a real row
        original, compensation = self.db.holding_moves
        self.assertEqual((original["movement_type"], original["weight_delta"], original["correction_id"]), ("HANDOVER", D("10.000"), None))
        self.assertEqual((compensation["movement_type"], compensation["weight_delta"]), ("HANDOVER_REVERSAL", D("-10.000")))
        self.assertEqual(compensation["document_id"], original["document_id"])
        self.assertIsNotNone(compensation["correction_id"])
        self.assertEqual(self.holding_repo().pool_balance(1), 0)
        self.assertEqual(self.untouched(), self.baseline)
        again = self.refuse("ALREADY_REVERSED", lambda: self.service.reverse(done["entry_id"], "x"), CorrectionError)
        self.assertIn("RV", str(again))
        self.assertEqual(self.hand()["document_code"], "IH000002")                                # the restored lot can be handed over again

    def test_a_handover_cannot_be_reversed_while_its_return_is_effective_and_the_order_is_newest_first(self):
        handed, returned = self.hand(), self.back()
        self.refuse("DEPENDENT_HOLDING_RETURN_EXISTS", lambda: self.service.reverse(handed["entry_id"], "x"), CorrectionError)
        self.service.reverse(returned["entry_id"], "x")                                          # the return first: the lot is at the party again
        self.assertEqual(self.places(), (D("0"), D("0"), D("10.000")))
        self.assertEqual([m["movement_type"] for m in self.db.holding_moves], ["HANDOVER", "RETURN", "RETURN_REVERSAL"])
        self.service.reverse(handed["entry_id"], "x")
        self.assertEqual(self.places(), (D("10.000"), D("0"), D("0")))
        self.assertEqual(self.holding_repo().pool_balance(1), 0)

    def test_a_return_cannot_be_reversed_after_the_lot_was_used_and_no_double_restoration_happens(self):
        self.hand()
        returned = self.back()
        self.service.settle_supplier_lot(self.trip, "21", str(self.main), TODAY, "", "", key(8040))     # the restored lot goes to a supplier
        self.refuse("DEPENDENT_LOT_USE_EXISTS", lambda: self.service.reverse(returned["entry_id"], "x"), CorrectionError)
        self.assertEqual(self.holding_repo().pool_balance(1), 0)
        self.service.reverse(max(self.db.entries), "x")                                           # undo the settlement first
        self.service.reverse(returned["entry_id"], "x")
        self.refuse("ALREADY_REVERSED", lambda: self.service.reverse(returned["entry_id"], "x"), CorrectionError)
        self.assertEqual(self.places(), (D("0"), D("0"), D("10.000")))

    def test_reversal_rights_the_reason_and_the_open_custody_are_enforced(self):
        done = self.hand()
        trips, _ = self.as_user(OWNER - {"INTERMEDIARY_HOLDING_REVERSE"})
        before = self.state()
        with self.assertRaises(PermissionError):
            trips.reverse(done["entry_id"], "x")
        self.assertEqual(self.state(), before)
        trips, _ = self.as_user(OWNER - {"TRANSACTION_CORRECT"})
        with self.assertRaises(PermissionError):
            trips.reverse(done["entry_id"], "x")
        self.refuse("REASON_REQUIRED", lambda: self.service.reverse(done["entry_id"], "  "), CorrectionError)
        for lot in (self.residue, self.unknown):
            self.service.return_lots(self.trip, [{"lot_id": str(lot)}], TODAY, "", key(8200 + lot))
        self.service.close(self.trip)
        self.refuse("TRIP_CLOSED", lambda: self.service.reverse(done["entry_id"], "x"), CorrectionError)
        self.service.reopen(self.trip, "x")
        self.service.reverse(done["entry_id"], "x")

    def test_reversal_failure_at_any_stage_rolls_everything_back(self):
        done = self.hand()
        before = self.state()
        for step in ("holding_movement", "audit"):
            self.db.fail_at = step
            with self.assertRaises(RuntimeError):
                self.service.reverse(done["entry_id"], "x")
            self.db.fail_at = None
            self.assertEqual(self.state(), before, f"a failure at {step} left a trace")
        self.assertEqual(self.holding_repo().pool_balance(1), D("10.000"))
        self.service.reverse(done["entry_id"], "x")
        self.assertEqual(self.holding_repo().pool_balance(1), D("0.000"))


class LockOrderTests(HoldingCase):
    def test_handover_return_and_reversal_follow_trip_lot_party_pool(self):
        self.db.lock_log.clear()
        self.hand()
        self.assertEqual(self.db.lock_log, [("trip",), ("lots", (self.main,)), ("party", 20)])                  # a new pool has nothing to lock yet
        self.db.lock_log.clear()
        returned = self.back()
        self.assertEqual(self.db.lock_log, [("trip",), ("lots", (self.main,)), ("party", 20), ("pool", 1)])
        self.db.lock_log.clear()
        self.service.reverse(returned["entry_id"], "x")
        kinds = [entry[0] for entry in self.db.lock_log]
        self.assertEqual(kinds[:4], ["trip", "lots", "party", "pool"])


class RoleHistoryTests(unittest.TestCase):
    """The Supplier Master: any holding history keeps the intermediary role and the party row (the supplier account stays separate)."""

    def setUp(self):
        from tests.supplier_fakes import build_payments
        self.db, self.session, self.authorization, self.suppliers, self.purchases, self.payments = build_payments()

    def party(self, name="مسوقجي", supplier=False):
        return self.suppliers.create_supplier(name, None, None, 1 if supplier else None, is_supplier=supplier, is_intermediary=True)

    def test_holding_history_blocks_role_removal_and_deletion_even_when_reversed(self):
        from services.supplier_rules import SupplierRuleError
        sid = self.party()
        self.db.holding_history = {int(sid): 2}                                                  # one pool and one document, both reversed
        for call, code in ((lambda: self.suppliers.update_supplier(sid, "مسوقجي", is_supplier=True, is_intermediary=False, karat_id=1), "ROLE_HISTORY_EXISTS"),
                           (lambda: self.suppliers.delete_unused_supplier(sid), "SUPPLIER_IN_USE")):
            with self.assertRaises(SupplierRuleError) as context:
                call()
            self.assertEqual(context.exception.code, code)
        self.assertIn(int(sid), self.db.suppliers)
        self.assertTrue(self.db.suppliers[int(sid)]["is_intermediary"])
        self.suppliers.set_active(sid, False)                                                    # deactivation stays possible
        other = self.party("مسوقجي ثان")
        self.suppliers.update_supplier(other, "مسوقجي ثان", is_supplier=True, is_intermediary=False, karat_id=1)    # no history: allowed

    def test_the_holdings_panel_is_only_visible_with_the_view_right_and_is_independent_of_the_supplier_account(self):
        sid = self.party("مورد ومسوقجي", supplier=True)
        self.assertIsNone(self.suppliers.supplier_detail(sid)["holdings"])                       # this actor holds no INTERMEDIARY_HOLDING_VIEW
        from tests.supplier_fakes import OWNER_RIGHTS, build_payments
        db, session, authorization, suppliers, *_ = build_payments(OWNER_RIGHTS | {"INTERMEDIARY_HOLDING_VIEW"})
        sid = suppliers.create_supplier("كلاهما", None, None, 1, is_supplier=True, is_intermediary=True)
        detail = suppliers.supplier_detail(sid)
        self.assertEqual((detail["holdings"]["pools"], detail["holdings"]["history"], detail["holdings"]["total"]), ([], [], D("0.000")))
        self.assertEqual(detail["balances"]["gold_status"], "RESOLVED")                          # the supplier account is separate and unchanged


if __name__ == "__main__":
    unittest.main()
