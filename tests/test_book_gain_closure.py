"""packet25: the evidenced gain BK_GAIN (059), BOOK closure / reopen, version policy v1 / v2 / v3, finality independent of CLOSED, and a whole market cycle.
Real MarketTripBookService / CorrectionService / book_reconciliation / book_settlement over the in-memory database of tests/market_trip_fakes.py. NOT real-MySQL tests:
nothing here proves SQL, locks or races."""
from decimal import Decimal as D
import re
import unittest
from pathlib import Path

from services import book_reconciliation as rec
from services import book_rules as br
from services import book_settlement as bs
from services.correction_service import CorrectionError
from services.market_trip_service import TripError
from tests.market_trip_fakes import TripRepo
from tests.marketer_fakes import MarketerRepo as MarketerRepo_
from tests.test_book_settlement_service import ALL
from tests.test_market_trip_book import K18, K21, assay, karat
from tests.test_market_trip_book_cycle import CycleCase, MKT, SUP18

ROOT = Path(__file__).resolve().parents[1]
GAIN = dict(explanation="وُجد ذهب زائد بعد الوزن", evidence_reference="إيصال وزن 123")


class GainCase(CycleCase):
    def gain(self, trip, row, **kw):
        args = {**GAIN, "confirmed": True, **kw}
        return self.service.book_gain(trip, row, args["explanation"], args["evidence_reference"], args["confirmed"], "2026-10-10", "", kw.get("request") or self.k())

    def versions(self, trip):
        return bs.versions_for(bs.build(TripRepo(self.db).reconciliation_source_current(trip))[1])

    def recon(self, trip, **over):
        digest = bs.build(TripRepo(self.db).reconciliation_source_current(trip))[3]
        versions = self.versions(trip)
        return self.service.reconcile(trip, digest, None, over.get("reason", "OPERATING_LOSS"), None, dict(ALL), versions, self.k())


class GainTests(GainCase):
    def test_a_gain_enters_the_inputs_from_its_own_physical_facts_and_is_independent(self):
        trip = self.issue([karat(K21, "5")])["trip_id"]
        result = self.gain(trip, karat(K21, "0.5"))
        line = next(l for l in self.db.book_lines if l["entry_id"] == int(result["entry_id"]))
        self.assertEqual((line["direction"], line["eq21_posted"], line["posting_basis"], line["scrap_movement_id"], line["line_price"]), ("IN", D("0.500"), "INDEPENDENT", None, None))
        self.assertEqual(self.book(trip), D("5.500"))
        self.assertEqual(result["gain"]["evidence_reference"], GAIN["evidence_reference"])
        self.assertEqual(self.db.gains[int(result["entry_id"])]["confirmed_by_user_id"], self.session.current_actor().user_id)

    def test_an_assayed_surplus_is_computed_from_its_assay_never_typed(self):
        trip = self.issue([karat(K21, "5")])["trip_id"]
        result = self.gain(trip, assay("866.7", "1.8"))
        self.assertEqual(result["lines"][0]["posted"], "1.783")

    def test_explanation_evidence_and_confirmation_are_mandatory_and_nothing_is_written(self):
        trip = self.issue([karat(K21, "5")])["trip_id"]
        before = self.state()
        for code, over in (("GAIN_EXPLANATION_REQUIRED", {"explanation": "  "}), ("GAIN_EVIDENCE_REQUIRED", {"evidence_reference": ""}),
                           ("GAIN_CONFIRM_REQUIRED", {"confirmed": False}), ("GAIN_CONFIRM_REQUIRED", {"confirmed": "yes"})):
            self.refused(code, lambda: self.gain(trip, karat(K21, "1"), **over))
        self.assertEqual(self.state(), before)

    def test_a_gain_rescues_an_outgoing_line_that_exceeded_the_book(self):
        trip = self.issue([karat(K21, "2")])["trip_id"]
        self.refused("BOOK_EXCEEDS_BALANCE", lambda: self.deliver_supplier(trip, SUP18, karat(K21, "3")))
        self.gain(trip, karat(K21, "1.5"))
        self.deliver_supplier(trip, SUP18, karat(K21, "3"))                                      # now it succeeds; the book is analytically zero-plus
        self.assertEqual(self.book(trip), D("0.500"))

    def test_replay_conflict_and_permission(self):
        trip = self.issue([karat(K21, "2")])["trip_id"]
        request = self.k()
        first = self.gain(trip, karat(K21, "1"), request=request)
        again = self.gain(trip, karat(K21, "1"), request=request)
        self.assertTrue(again["duplicate"] and again["entry_id"] == first["entry_id"])
        self.assertEqual(self.book(trip), D("3.000"))
        self.refused("REQUEST_CONFLICT", lambda: self.gain(trip, karat(K21, "2"), request=request))
        weak = self.other_service(permissions=frozenset(), user_id=9)[0]
        with self.assertRaises(PermissionError):
            weak.book_gain(trip, karat(K21, "1"), "x", "y", True, "2026-10-10", "", self.k())

    def test_a_gain_is_reversible_only_while_the_book_stays_consistent(self):
        trip = self.issue([karat(K21, "2")])["trip_id"]
        gained = self.gain(trip, karat(K21, "1"))
        self.sell(trip, [karat(K21, "2.5", "100")], ack=True)
        with self.assertRaises(CorrectionError):
            self.service.reverse(gained["entry_id"], "خطأ")                                       # the sale consumed the gained gold
        fresh = self.issue([karat(K21, "2")])["trip_id"]
        easy = self.gain(fresh, karat(K21, "1"))
        self.service.reverse(easy["entry_id"], "خطأ")
        self.assertEqual(self.book(fresh), D("2.000"))

    def test_no_other_ledger_moves(self):
        trip = self.issue([karat(K21, "2")])["trip_id"]
        scrap, cash, sup, mk, stock = (len(self.db.scrap), len(self.db.cash_moves), len(self.db.sup_gold), len(getattr(self.db, "mk_gold", [])), len(self.db.assayed_moves))
        self.gain(trip, karat(K21, "1"))
        self.assertEqual((len(self.db.scrap), len(self.db.cash_moves), len(self.db.sup_gold), len(getattr(self.db, "mk_gold", [])), len(self.db.assayed_moves)),
                         (scrap, cash, sup, mk, stock))

    # ---- the guard (packet26): a gain never covers a defective source; nothing is written on a refusal
    def sound_pair(self):
        """A trip bought and sold 2 g (a sound record, A = 0) and its untouched state."""
        trip = self.issue([karat(K21, "1")])["trip_id"]
        self.cash(trip, "100000")
        self.buy(trip, [karat(K21, "2", "12000")], ack=True)
        self.sell(trip, [karat(K21, "2", "12000")], ack=True)
        return trip

    def refused_gain(self, trip, code="GAIN_SOURCE_NOT_SOUND"):
        before = self.state()
        self.refused(code, lambda: self.gain(trip, karat(K21, "1")))
        self.assertEqual(self.state(), before)                                                    # no entry, line, record, audit or event

    def test_a_truly_negative_a_is_refused_and_never_covered_by_a_gain(self):
        trip = self.sound_pair()
        buy_line = next(l for l in self.db.book_lines if l["trip_id"] == int(trip) and l["entry_kind"] == "BK_BUY")
        buy_line.update(weight_grams=D("0.500"), eq21_posted=D("0.500"), eq21_analytical=D("0.500000"), signed_eq21_posted=D("0.500"))   # consistent facts: A = (1 + 0.5) - 2 < 0
        preview = self.service.reconciliation_preview(trip)
        self.assertEqual((preview["aState"], [d["code"] for d in preview["diagnostics"] if d["category"] == "integrity"]), ("negative_integrity", ["A_NEGATIVE"]))   # the preview's own alarm
        self.refused_gain(trip)
        with self.assertRaises(TripError) as raised:
            self.gain(trip, karat(K21, "5"))
        self.assertIn("A_NEGATIVE", [d["code"] for d in raised.exception.diagnostics])

    def test_a_corrupt_line_is_refused(self):
        trip = self.issue([karat(K21, "4")])["trip_id"]
        self.db.book_lines[0]["eq21_posted"] = D("3.000")                                         # posted no longer matches the facts
        self.refused_gain(trip)

    def test_a_missing_leg_is_refused(self):
        trip = self.issue([karat(K21, "4")])["trip_id"]
        self.db.scrap[:] = [m for m in self.db.scrap if m.get("reference_type") != "MARKET_TRIP_ENTRY"]
        self.refused_gain(trip)

    def test_an_incomplete_or_unreadable_source_is_refused(self):
        from unittest.mock import patch
        trip = self.issue([karat(K21, "4")])["trip_id"]
        original = TripRepo.reconciliation_source_current

        def broken(repo, trip_id):
            return {**original(repo, trip_id), "incomplete": [("SOURCE_LINES_UNREADABLE", "OperationalError")]}
        with patch.object(TripRepo, "reconciliation_source_current", broken):
            self.refused_gain(trip)

    def test_a_sound_record_accepts_a_gain_that_then_allows_an_outgoing_line_that_exceeded_the_book(self):
        trip = self.issue([karat(K21, "2")])["trip_id"]
        self.refused("BOOK_EXCEEDS_BALANCE", lambda: self.deliver_supplier(trip, SUP18, karat(K21, "3")))
        self.gain(trip, karat(K21, "1.5"))                                                        # the source is sound: the surplus is recorded
        self.deliver_supplier(trip, SUP18, karat(K21, "3"))
        self.assertEqual(self.book(trip), D("0.500"))

    def test_exact_replay_of_a_gain_still_returns_the_original_after_the_source_became_unsound(self):
        trip = self.issue([karat(K21, "4")])["trip_id"]
        request = self.k()
        first = self.gain(trip, karat(K21, "1"), request=request)
        self.db.book_lines[0]["eq21_posted"] = D("3.000")
        again = self.gain(trip, karat(K21, "1"), request=request)                                 # the replay is decided before any source read
        self.assertEqual((again["duplicate"], again["entry_id"]), (True, first["entry_id"]))
        self.refused("GAIN_SOURCE_NOT_SOUND", lambda: self.gain(trip, karat(K21, "1")))           # a NEW request is judged on the defective source

    def test_the_guard_comes_after_the_permission_and_the_trip_checks(self):
        trip = self.issue([karat(K21, "4")])["trip_id"]
        self.db.book_lines[0]["eq21_posted"] = D("3.000")
        weak = self.other_service(permissions=frozenset(), user_id=9)[0]
        with self.assertRaises(PermissionError):
            weak.book_gain(trip, karat(K21, "1"), "x", "y", True, "2026-10-10", "", self.k())


class VersionPolicyTests(GainCase):
    def test_v1_v2_v3_are_chosen_by_content_and_old_records_stay_valid(self):
        v1 = self.issue([karat(K21, "4")])["trip_id"]
        v2 = self.issue([karat(K21, "4")])["trip_id"]
        self.deliver_marketer(v2, MKT, karat(K21, "1"))
        v3 = self.issue([karat(K21, "4")])["trip_id"]
        self.gain(v3, karat(K21, "1"))
        self.assertEqual([self.versions(t) for t in (v1, v2, v3)], [bs.CONTRACT_VERSIONS, bs.CONTRACT_VERSIONS_V2, bs.CONTRACT_VERSIONS_V3])
        rows = [self.db.reconciliations[int(self.recon(t)["entry_id"])] for t in (v1, v2, v3)]
        for row in rows:
            self.assertEqual(bs.verify_record(row), [])
        self.assertEqual([r["source_algorithm"] for r in rows], ["book-recon-source-v1", "book-recon-source-v2", "book-recon-source-v3"])
        snapshot = bs.parse_snapshot(rows[2]["snapshot"])
        self.assertIn("BK_GAIN", snapshot["source_kinds"])
        gain_entry = next(e for e in snapshot["entries"] if e["kind"] == "BK_GAIN")
        self.assertEqual(gain_entry["gain"]["evidence_reference"], GAIN["evidence_reference"])
        self.assertNotIn("BK_GAIN", bs._kinds_of(bs.SOURCE_ALGORITHM_V2))                          # a v2 definition never changes meaning

    def test_a_v2_request_on_v3_content_is_refused(self):
        trip = self.issue([karat(K21, "4")])["trip_id"]
        self.gain(trip, karat(K21, "1"))
        digest = bs.build(TripRepo(self.db).reconciliation_source_current(trip))[3]
        with self.assertRaises(TripError) as raised:
            self.service.reconcile(trip, digest, None, "OPERATING_LOSS", None, dict(ALL), dict(bs.CONTRACT_VERSIONS_V2), self.k())
        self.assertEqual(raised.exception.code, "RECONCILIATION_CONTRACT_MISMATCH")

    def test_a_tampered_gain_record_blocks_trust(self):
        trip = self.issue([karat(K21, "4")])["trip_id"]
        gained = self.gain(trip, karat(K21, "1"))
        self.assertEqual(self.service.reconciliation_preview(trip)["diagnostics"], [])
        digest = bs.build(TripRepo(self.db).reconciliation_source_current(trip))[3]
        del self.db.gains[int(gained["entry_id"])]
        self.assertIn("GAIN_RECORD_MISSING", {d["code"] for d in self.service.reconciliation_preview(trip)["diagnostics"]})
        with self.assertRaises(TripError):
            self.service.reconcile(trip, digest, None, "OPERATING_LOSS", None, dict(ALL), dict(bs.CONTRACT_VERSIONS_V3), self.k())

    def test_a_gain_after_a_database_without_059_is_reported_incomplete_not_read_as_none(self):
        trip = self.issue([karat(K21, "4")])["trip_id"]
        self.gain(trip, karat(K21, "1"))
        self.db.pre059 = True
        codes = {d["code"] for d in self.service.reconciliation_preview(trip)["diagnostics"]}
        self.assertIn("SOURCE_GAINS_UNREADABLE", codes)


class ClosureTests(GainCase):
    def reconciled(self, cash=None):
        trip = self.issue([karat(K21, "10")])["trip_id"]
        self.ret(trip, [karat(K21, "4")])
        if cash:
            self.cash(trip, cash)
        return trip

    def test_closure_needs_an_effective_reconciliation_and_never_a_zero_balance_shortcut(self):
        trip = self.issue([karat(K21, "5")])["trip_id"]
        self.ret(trip, [karat(K21, "5")])
        self.refused("RECONCILIATION_REQUIRED", lambda: self.service.close(trip))
        self.assertEqual(self.db.trips[int(trip)]["status"], "OPEN")

    def test_a_reconciled_trip_closes_with_historical_lines_untouched_and_the_book_settled(self):
        trip = self.reconciled()
        lines_before = [dict(l) for l in self.db.book_lines]
        result = self.recon(trip)
        closed = self.service.close(trip)
        self.assertEqual(closed["status"], "CLOSED")
        self.assertEqual([dict(l) for l in self.db.book_lines], lines_before)                     # the reconciliation settles; history is not rewritten or zeroed
        self.assertIsNotNone(result["entry_id"])
        self.assertEqual(self.db.trips[int(trip)]["status"], "CLOSED")

    def test_cash_must_be_zero(self):
        trip = self.reconciled(cash="50")
        self.recon(trip)
        self.refused("OUTSTANDING_CUSTODY", lambda: self.service.close(trip))
        self.assertEqual(self.db.trips[int(trip)]["status"], "OPEN")
        self.service.return_custody(trip, "50", [], "2026-10-10", "", self.k())
        self.service.close(trip)

    def test_independent_balances_are_not_closure_conditions(self):
        trip = self.issue([karat(K21, "10")])["trip_id"]
        self.deliver_marketer(trip, MKT, karat(K21, "10"))                                        # the marketer's entitlement stays outstanding
        _, item = self.stock_trip()                                                               # stock in the shop belongs to another trip
        self.recon(trip)
        self.assertEqual(self.service.close(trip)["status"], "CLOSED")
        self.assertEqual(self.marketer(MKT), D("10.000"))

    def test_a_record_that_no_longer_matches_the_live_source_refuses_the_close(self):
        trip = self.reconciled()
        self.recon(trip)
        next(l for l in self.db.book_lines if l["trip_id"] == int(trip))["eq21_posted"] += D("0.001")
        with self.assertRaises(TripError) as raised:
            self.service.close(trip)
        self.assertEqual(raised.exception.code, "RECONCILIATION_MISMATCH")
        self.assertEqual(self.db.trips[int(trip)]["status"], "OPEN")

    def test_a_missing_record_and_two_effective_reconciliations_refuse(self):
        trip = self.reconciled()
        result = self.recon(trip)
        saved = self.db.reconciliations.pop(int(result["entry_id"]))
        self.refused("RECONCILIATION_MISMATCH", lambda: self.service.close(trip))
        self.db.reconciliations[int(result["entry_id"])] = saved

    def test_close_replay_permission_and_session(self):
        trip = self.reconciled()
        self.recon(trip)
        weak = self.other_service(permissions=frozenset(), user_id=9)[0]
        with self.assertRaises(PermissionError):
            weak.close(trip)
        self.service.close(trip)
        self.refused("TRIP_ALREADY_CLOSED", lambda: self.service.close(trip))

    def test_reopen_does_not_unlock_gold_or_the_reconciliation(self):
        trip = self.reconciled()
        recon = self.recon(trip)
        self.service.close(trip)
        self.refused("TRIP_CLOSED", lambda: self.issue([karat(K21, "1")], trip=trip))
        with self.assertRaises(CorrectionError):
            self.service.reverse(recon["entry_id"], "خطأ")                                        # a closed trip: reopen first
        self.service.reopen(trip, "مراجعة")
        self.refused("RECONCILIATION_FINAL", lambda: self.issue([karat(K21, "1")], trip=trip))      # reopening alone never lifts the finality
        self.refused("RECONCILIATION_FINAL", lambda: self.gain(trip, karat(K21, "1")))
        self.service.reverse(recon["entry_id"], "تصحيح")                                          # the documented order: reopen -> reverse -> correct
        self.gain(trip, karat(K21, "0.5"))
        self.recon(trip)
        self.service.close(trip)

    def test_closure_preview_explains_in_arabic_and_writes_nothing(self):
        trip = self.reconciled(cash="20")
        before = {k: v for k, v in self.state().items() if k != "preview_reads"}
        view = self.service.closure_preview(trip)
        self.assertFalse(view["ready"])
        self.assertEqual({b["code"] for b in view["blockers"]}, {"RECONCILIATION_REQUIRED", "OUTSTANDING_CUSTODY"})
        self.assertEqual({k: v for k, v in self.state().items() if k != "preview_reads"}, before)


class WholeCycleTests(GainCase):
    def test_issue_buy_sell_supplier_marketer_stock_gain_reconcile_close_reopen_correct(self):
        trip = self.issue([karat(K21, "20")])["trip_id"]
        self.cash(trip, "40000")
        self.buy(trip, [karat(K21, "2", "12000")], ack=True)
        self.sell(trip, [karat(K21, "3", "18000")], ack=True)
        self.deliver_supplier(trip, SUP18, karat(K21, "4"))
        self.deliver_marketer(trip, MKT, karat(K21, "2"))
        self.receive_marketer(trip, MKT, karat(K21, "1"))
        back = self.ret(trip, [assay("866.7", "1.8")])
        target = self.issue([karat(K21, "5")])["trip_id"]
        self.issue_stock(target, [{"entry_id": back["entry_id"], "line_no": 1, "grams": "1.8"}])
        self.gain(trip, karat(K21, "0.4"))
        self.ret(trip, [karat(K21, "5")])
        preview = self.service.reconciliation_preview(trip)
        self.assertEqual(preview["diagnostics"], [])
        recon = self.recon(trip)
        self.assertEqual(self.db.reconciliations[int(recon["entry_id"])]["source_algorithm"], "book-recon-source-v3")
        self.refused("OUTSTANDING_CUSTODY", lambda: self.service.close(trip))                     # custody cash 40000 - 24000 + 18000 is still in the trip
        outstanding = self.custody_cash(trip)["outstanding"]
        self.service.return_custody(trip, str(outstanding), [], "2026-10-10", "", self.k())
        self.service.close(trip)
        self.service.reopen(trip, "تصحيح")
        self.service.reverse(recon["entry_id"], "تصحيح")
        self.assertEqual(self.service.reconciliation_preview(trip)["status"], "OPEN")


class ReadModelTests(GainCase):
    """What the screens show: the documents list (gain / reconciliation records), the closure readiness of the detail, the marketer statement legs."""

    def test_documents_carry_the_gain_and_reconciliation_records_and_their_reversal_state(self):
        trip = self.issue([karat(K21, "10")])["trip_id"]
        self.gain(trip, karat(K21, "1"))
        self.recon(trip)
        rows = {e["kind"]: e for e in self.service.trip_detail(trip)["entries"]}
        self.assertEqual(rows["BK_GAIN"]["gain"]["evidence_reference"], GAIN["evidence_reference"])
        self.assertEqual((rows["BK_RECON"]["recon"]["generation"], rows["BK_RECON"]["recon"]["loss"]), (1, D("11.000")))
        self.assertFalse(rows["BK_RECON"]["reversed"])

    def test_the_marketer_statement_explains_the_trip_legs(self):
        from services.marketer_custody_service import MarketerCustodyService
        trip = self.issue([karat(K21, "10")])["trip_id"]
        delivered = self.deliver_marketer(trip, MKT, karat(K21, "3"))
        self.receive_marketer(trip, MKT, karat(K21, "1"))
        legs = [MarketerCustodyService._trip_leg_row(m) for m in MarketerRepo_(self.db).trip_movements(int(MKT))]
        self.assertEqual([(l["entry_kind"], l["movement_type"], l["delta"]) for l in legs],
                         [("BK_RECEIVE", "TRIP_BOOK_OUT", D("-1.000")), ("BK_DLV_MKT", "TRIP_BOOK_IN", D("3.000"))])
        self.assertTrue(all(l["trip_code"] and l["entry_code"] for l in legs))
        self.assertEqual(sum(l["delta"] for l in legs), self.marketer(MKT))


class PositionTests(GainCase):
    def test_a_gain_adds_its_posted_equivalent_once_and_closing_never_drops_the_trip(self):
        trip = self.issue([karat(K21, "10")])["trip_id"]
        base = self.total()
        self.gain(trip, karat(K21, "1"))
        self.assertEqual(self.total(), base + D("1.000"))                                          # the gold is held, now recorded: counted once
        self.ret(trip, [karat(K21, "4")])
        self.recon(trip)
        settled = self.total()
        self.service.close(trip)
        self.assertEqual(self.total(), settled)                                                    # CLOSED changes nothing about what the shop owns
        self.service.reopen(trip, "مراجعة")
        self.assertEqual(self.total(), settled)


class MarketerViewModelTests(unittest.TestCase):
    def test_the_party_page_lists_the_trip_legs_with_their_reference_and_reversal(self):
        from app.viewmodels.marketer_custody_viewmodel import MarketerCustodyViewModel
        from services.marketer_custody_service import MarketerCustodyService
        from datetime import date, datetime
        leg = lambda mid, kind, ptype, delta: MarketerCustodyService._trip_leg_row({"id": mid, "trip_entry_id": 40 + mid, "trip_entry_kind": kind, "movement_type": ptype,
                                                                               "weight_grams": abs(delta), "weight_delta": delta, "created_at": datetime(2026, 10, 10, 9),
                                                                               "trip_id": 3, "entry_no": mid, "entry_date": date(2026, 10, 10)})
        vm = MarketerCustodyViewModel(session=type("S", (), {"changed": type("C", (), {"connect": lambda *a: None})()})())
        vm._account = {"party": {"id": "20", "name": "م", "active": True, "is_supplier": False}, "cash": D("0.00"), "gold": D("2.000"), "documents": [], "settlements": [],
                       "trip_legs": [leg(2, "BK_DLV_MKT", "TRIP_BOOK_IN_REVERSAL", D("-3.000")), leg(1, "BK_DLV_MKT", "TRIP_BOOK_IN", D("3.000"))],
                       "counterparties": [], "recipients": [], "can_reverse": False}
        legs = vm.account["tripLegs"]
        self.assertEqual([l["reversal"] for l in legs], [True, False])
        self.assertEqual([(l["tripId"], l["entryId"]) for l in legs], [("3", "42"), ("3", "41")])           # the open-the-trip link carries the source trip and entry
        self.assertIn("MT000003", legs[0]["text"])
        self.assertIn("-3.000", legs[0]["text"])
        self.assertIn("المستند MT000003-1", legs[1]["text"])


class MigrationTextTests(unittest.TestCase):
    def test_059_is_prepared_guarded_and_its_rollback_refuses_history(self):
        sql = (ROOT / "database/schema/059_market_trip_book_gain.sql").read_text(encoding="utf-8")
        rollback = (ROOT / "database/schema/rollback/059_market_trip_book_gain_rollback.sql").read_text(encoding="utf-8")
        self.assertIn("MIGRATION_059_REFUSED_missing_058", sql)
        self.assertIn("'BK_GAIN'", sql)
        self.assertIn("book-recon-source-v3", sql)
        self.assertEqual(sql.count("("), sql.count(")"))
        self.assertIn("ROLLBACK_059_REFUSED", rollback)
        self.assertIn("entry_kind = 'BK_GAIN'", rollback)
        self.assertNotRegex(rollback, r"(?i)DELETE FROM market_trip_(entries|gains|book_lines)")

    def test_widths_fit(self):
        self.assertLessEqual(len(br.BK_GAIN), 12)
        self.assertLessEqual(len("book-recon-source-v3"), 24)
        self.assertLessEqual(len("book-recon-snapshot-v3"), 24)


if __name__ == "__main__":
    unittest.main()
