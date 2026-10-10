"""Market-trip lot returns (040, M2): a melt lot is an indivisible bar - it returns from custody to the shop's separate lot stock WHOLE
or stays in custody. Reversal, closure, idempotency, authorization and the refusal of a legacy partial state.

Real MarketTripService and the real CorrectionService over the in-memory transactional database of tests/market_trip_fakes.py: a
failed transaction restores every table and the 040 CHECK / unique constraints are mirrored as asserts. These are NOT real-MySQL
tests: the SQL, the constraints and the rollback were exercised on the masgold_rehearsal sandbox, single-threaded. Concurrency
(lock contention between two returns of one lot) is NOT exercised anywhere: the lot lock is inferred from the code, not proven."""
import copy
from datetime import date
from decimal import Decimal as D
from types import SimpleNamespace
import unittest

from mysql.connector import IntegrityError

from services.correction_service import CorrectionError
from tests.market_trip_fakes import MANAGER, OWNER_NO_CORRECT, TripRepo
from tests.test_market_trips import K21, K24, TODAY, TripCase, key


class LotCase(TripCase):
    """Trip 1: 21K 2 g + 24K 1 g (MELT) melted into 2.950 g at 930.0 (one MAIN lot) -> custody lot of 2.950 g."""

    def setUp(self):
        super().setUp()
        self.db.scrap.append({"id": 90, "karat_id": 3, "weight_delta": D("10.000"), "reference_type": "OPENING", "reference_id": "0",
                              "type_code": "OPENING"})
        self.db.sequences["scrap"] = 90
        self.trip = self.issue("", [(K21, "2", "MELT"), (K24, "1", "MELT")])["trip_id"]
        self.melt_result = self.melt()
        self.melt_entry = int(self.melt_result["entry_id"])
        self.lot = next(l["id"] for l in self.db.lots.values() if l["entry_id"] == self.melt_entry and l["lot_kind"] == "MAIN")
        self.baseline = self.untouched()

    def melt(self, inputs=None, main=("2.950", "930"), residues=(), trip=None, request=None):
        self.n += 1
        return self.service.melt(trip or self.trip, inputs or [{"karat_id": K21, "weight": "2"}, {"karat_id": K24, "weight": "1"}], TODAY,
                                 "ورشة النور", {"weight": main[0], "fineness": main[1]},
                                 [dict(weight=w, fineness=f, label=l) for w, f, l in residues], "", "", "", request or key(300 + self.n))

    def second_melt(self, main, residues=(), karat=K21, grams="1"):
        """More melt-designated custody on the same trip, melted into one more lot (equal weight: no gain explanation needed)."""
        self.issue("", [(karat, grams, "MELT")], trip=self.trip)
        return self.melt(inputs=[{"karat_id": karat, "weight": grams}], main=main, residues=residues)

    def give_back(self, lots, trip=None, request=None, service=None, when=TODAY, notes="", weights=None):
        """Select whole lots; `weights` (lot -> text) simulates a client that still sends a quantity."""
        self.n += 1
        lines = [{"lot_id": str(l), **({"weight": weights[l]} if weights and l in weights else {})} for l in lots]
        return (service or self.service).return_lots(trip or self.trip, lines, when, notes, request or key(500 + self.n))

    def legacy_partial(self, grams="1.000"):
        """A PARTIAL return as the pre-correction build could write it (kept as history): posted straight through the repository."""
        repo = TripRepo(self.db)
        entry = repo.insert_entry(trip_id=int(self.trip), entry_no=repo.next_entry_no(int(self.trip)), kind="LOT_RETURN", entry_date=date(2026, 10, 10),
                                  notes=None, key=key(777), digest="x" * 64, actor=SimpleNamespace(user_id=7, employee_id=None), now=self.db.now)
        custody = repo.insert_lot_movement(lot_id=self.lot, entry_id=entry, movement_type="SHOP_RETURN", weight_delta=-D(grams))
        shop = repo.insert_shop_lot_movement(lot_id=self.lot, entry_id=entry, movement_type="TRIP_RETURN", weight_delta=D(grams))
        repo.insert_lot_return(entry_id=entry, lot_id=self.lot, weight=D(grams), custody_movement_id=custody, shop_movement_id=shop)
        return entry

    def untouched(self):
        """What a lot return must never move: cash, every Scrap row, supplier ledgers (none exist here), corrections."""
        return copy.deepcopy((self.db.cash_moves, self.db.scrap, self.db.corrections))

    def outstanding(self, trip=None):
        return {l["lot_id"]: l["outstanding"] for l in self.service.trip_detail(trip or self.trip)["custody"]["lots"]}

    def shop(self):
        return {l["lot_id"]: l["stock"] for l in self.service.shop_lots()}

    def analysis(self, trip=None):
        return copy.deepcopy([m["analysis"] for m in self.service.trip_detail(trip or self.trip)["melts"]])


class WholeLotFlowTests(LotCase):
    def test_the_documented_flow_whole_return_closure_and_reversal(self):
        analysis = self.analysis()
        self.assertEqual((analysis[0]["weight_difference"], analysis[0]["pure_difference"]), (D("-0.050"), D("-0.006500")))
        self.assertEqual(self.outstanding(), {str(self.lot): D("2.950")})
        # a partial quantity is refused with no persisted effect at all
        before = self.state()
        for weights in ({self.lot: "1.000"}, {self.lot: "2.949"}, {self.lot: "2.951"}, {self.lot: "0"}):
            with self.subTest(weights=weights):
                message = self.refused("LOT_PARTIAL_NOT_ALLOWED", lambda w=weights: self.give_back([self.lot], weights=w)).args[0]
                self.assertIn("ترجع السبكة كاملة ولا يمكن إرجاع جزء منها", message)
                self.assertIn("2.950", message)
        self.assertEqual(self.state(), before)
        # the whole lot: custody zero, shop lot stock 2.950; the posted weight is derived, never taken from the caller
        result = self.give_back([self.lot], weights={self.lot: "2.950"})
        self.assertEqual((result["code"], result["kind"], result["duplicate"]), ("MT000001-3", "LOT_RETURN", False))
        self.assertEqual((self.outstanding(), self.shop()), ({}, {str(self.lot): D("2.950")}))
        self.assertEqual(self.db.lot_returns[0]["weight_grams"], D("2.950"))
        self.assertEqual(self.untouched(), self.baseline)                                  # no cash, Scrap, supplier or correction row
        self.assertEqual(self.analysis(), analysis)                                        # the stored differences never move
        self.assertEqual(self.service.close(self.trip)["status"], "CLOSED")
        with self.assertRaises(CorrectionError) as closed:
            self.service.reverse(result["entry_id"], "رجوع خاطئ")
        self.assertEqual(closed.exception.code, "TRIP_CLOSED")
        self.service.reopen(self.trip, "مراجعة")
        reversal = self.service.reverse(result["entry_id"], "رجوع خاطئ")
        self.assertTrue(reversal["code"].startswith("RV"))
        self.assertEqual((self.outstanding(), self.shop()), ({str(self.lot): D("2.950")}, {}))     # the whole lot is back in custody
        self.assertEqual(self.analysis(), analysis)
        self.assertEqual(sorted((m["movement_type"], m["weight_delta"]) for m in self.db.shop_moves),
                         [("RETURN_REVERSAL", D("-2.950")), ("TRIP_RETURN", D("2.950"))])      # original kept, a new opposite movement added
        again = self.give_back([self.lot])                                                  # and it can be returned whole again
        self.assertEqual(self.shop(), {str(self.lot): D("2.950")})
        self.assertNotEqual(again["entry_id"], result["entry_id"])

    def test_effective_returns_block_the_melt_reversal_and_reversed_returns_do_not(self):
        done = self.give_back([self.lot])
        with self.assertRaises(CorrectionError) as blocked:
            self.service.reverse(self.melt_entry, "وزن خاطئ")
        self.assertEqual(blocked.exception.code, "DEPENDENT_LOT_USE_EXISTS")
        self.assertIn("MT000001-3", blocked.exception.args[0])
        self.service.reverse(done["entry_id"], "x")
        self.service.reverse(self.melt_entry, "وزن خاطئ")
        self.assertEqual((self.outstanding(), self.shop()), ({}, {}))
        self.assertTrue(self.service.trip_detail(self.trip)["melts"][0]["reversed"])

    def test_the_reversal_needs_a_reason_the_rights_and_is_protected_against_a_second_run(self):
        done = self.give_back([self.lot])
        before = self.state()
        for reason in ("", "   ", None):
            with self.assertRaises(CorrectionError) as refused:
                self.service.reverse(done["entry_id"], reason)
            self.assertEqual(refused.exception.code, "REASON_REQUIRED")
        with self.assertRaises(PermissionError):
            self.owner_like(MANAGER)[0].reverse(done["entry_id"], "x")
        with self.assertRaises(PermissionError):
            self.owner_like(OWNER_NO_CORRECT)[0].reverse(done["entry_id"], "x")
        self.assertEqual(self.state(), before)
        self.service.reverse(done["entry_id"], "x")
        with self.assertRaises(CorrectionError) as again:
            self.service.reverse(done["entry_id"], "x")
        self.assertEqual(again.exception.code, "ALREADY_REVERSED")
        self.assertEqual(len(self.db.shop_moves), 2)

    def test_a_reversal_is_blocked_when_the_shop_no_longer_holds_the_grams(self):
        done = self.give_back([self.lot])
        self.db.shop_moves.append({"id": 99, "lot_id": self.lot, "entry_id": int(done["entry_id"]), "movement_type": "TEST_DOWNSTREAM_USE",
                                   "weight_delta": D("-0.400"), "correction_id": None})        # a later use (no writer exists in M2)
        with self.assertRaises(CorrectionError) as blocked:
            self.service.reverse(done["entry_id"], "x")
        self.assertEqual(blocked.exception.code, "SHOP_LOT_STOCK_INSUFFICIENT")
        self.assertIn("2.550", blocked.exception.args[0])
        self.assertEqual(self.outstanding(), {})

    def test_a_failure_inside_the_reversal_changes_nothing(self):
        done = self.give_back([self.lot])
        before = self.state()
        for step in ("correction", "shop_movement", "audit"):
            self.db.fail_at = step
            with self.assertRaises(RuntimeError):
                self.service.reverse(done["entry_id"], "x")
            self.db.fail_at = None
            self.assertEqual(self.state(), before, step)


class LegacyPartialStateTests(LotCase):
    def test_a_lot_already_partially_returned_is_refused_with_an_explanation_and_history_is_not_rewritten(self):
        partial = self.legacy_partial("1.000")
        self.assertEqual((self.outstanding(), self.shop()), ({str(self.lot): D("1.950")}, {str(self.lot): D("1.000")}))
        history = copy.deepcopy((self.db.lot_returns, self.db.shop_moves, self.db.lot_moves, self.db.entries))
        before = self.state()
        message = self.refused("LOT_PARTIALLY_RETURNED", lambda: self.give_back([self.lot])).args[0]
        self.assertIn("1.950", message)
        self.assertIn("2.950", message)
        self.assertIn("اعكس الرجوع الجزئي أولًا", message)
        self.assertEqual(self.state(), before)                                              # nothing posted, nothing completed
        self.assertEqual(copy.deepcopy((self.db.lot_returns, self.db.shop_moves, self.db.lot_moves, self.db.entries)), history)
        # even a client that sends the remaining weight is not allowed to complete it
        self.refused("LOT_PARTIALLY_RETURNED", lambda: self.give_back([self.lot], weights={self.lot: "1.950"}))
        # the owner's way out: reverse the partial return (exactly what was posted), then return the lot whole
        self.service.reverse(partial, "رجوع جزئي قديم")
        self.assertEqual((self.outstanding(), self.shop()), ({str(self.lot): D("2.950")}, {}))
        self.give_back([self.lot])
        self.assertEqual((self.outstanding(), self.shop()), ({}, {str(self.lot): D("2.950")}))

    def test_the_stored_view_exposes_the_partial_state(self):
        self.legacy_partial("1.000")
        (lot,) = self.service.trip_detail(self.trip)["custody"]["lots"]
        self.assertEqual((lot["weight"], lot["outstanding"]), (D("2.950"), D("1.950")))


class ValidationAndAtomicityTests(LotCase):
    def test_bad_selections_are_refused_with_no_trace(self):
        before = self.state()
        for lots, code in (([], "NOTHING_TO_RECORD"), ([""], "NOTHING_TO_RECORD"), ([999], "LOT_NOT_FOUND"), (["x"], "LOT_NOT_FOUND"),
                           ([self.lot, self.lot], "LOT_DUPLICATE")):
            with self.subTest(lots=lots):
                self.refused(code, lambda l=lots: self.give_back(l))
        self.refused("WEIGHT_INVALID", lambda: self.give_back([self.lot], weights={self.lot: "abc"}))
        self.refused("WEIGHT_INVALID", lambda: self.give_back([self.lot], weights={self.lot: "-2.950"}))
        self.refused("TRIP_NOT_FOUND", lambda: self.give_back([self.lot], trip="0"))
        self.refused("DATE_REQUIRED", lambda: self.give_back([self.lot], when=""))
        self.refused("TEXT_LONG", lambda: self.give_back([self.lot], notes="ن" * 1001))
        self.refused("REQUEST_INVALID", lambda: self.give_back([self.lot], request="not-a-key"))
        self.assertEqual(self.state(), before)

    def test_a_lot_of_another_trip_or_already_returned_is_not_returnable(self):
        other = self.issue("", [(K21, "1", "MELT")])["trip_id"]
        self.refused("LOT_NOT_IN_CUSTODY", lambda: self.give_back([self.lot], trip=other))      # its location is derived, not its origin
        self.give_back([self.lot])
        self.refused("LOT_NOT_IN_CUSTODY", lambda: self.give_back([self.lot]))
        self.assertEqual(self.shop(), {str(self.lot): D("2.950")})

    def test_a_reversed_melt_lot_is_not_in_custody(self):
        self.service.reverse(self.melt_entry, "x")
        self.refused("LOT_NOT_IN_CUSTODY", lambda: self.give_back([self.lot]))
        self.assertEqual(self.shop(), {})

    def test_a_closed_trip_accepts_no_lot_return(self):
        self.give_back([self.lot])
        self.service.close(self.trip)
        self.refused("TRIP_CLOSED", lambda: self.give_back([self.lot]))

    def test_each_selected_lot_returns_whole_and_stays_separate(self):
        second = self.second_melt(main=("1", "930"))
        residue = self.second_melt(main=("0.700", "900"), residues=[("0.300", "", "خبث")])
        second_lot = next(l["id"] for l in self.db.lots.values() if l["entry_id"] == int(second["entry_id"]))
        residue_lot = next(l["id"] for l in self.db.lots.values() if l["entry_id"] == int(residue["entry_id"]) and l["lot_kind"] == "RESIDUE")
        self.give_back([self.lot, second_lot, residue_lot])
        shop = {r["lot_id"]: r for r in self.service.shop_lots()}
        self.assertEqual({k: v["stock"] for k, v in shop.items()}, {str(self.lot): D("2.950"), str(second_lot): D("1.000"), str(residue_lot): D("0.300")})
        self.assertEqual((shop[str(self.lot)]["fineness"], shop[str(second_lot)]["fineness"]), (D("930.0"), D("930.0")))     # equal assay, two lots
        self.assertEqual((shop[str(residue_lot)]["fineness"], shop[str(residue_lot)]["pure"], shop[str(residue_lot)]["kind"]), (None, None, "RESIDUE"))
        self.assertEqual(shop[str(self.lot)]["pure"], D("2.743500"))
        self.assertEqual(len(self.db.lot_returns), 3)

    def test_a_failure_at_any_step_persists_nothing(self):
        before = self.state()
        for step in ("entry", "lot_movement", "shop_movement", "lot_return", "audit"):
            self.db.fail_at = step
            with self.assertRaises(RuntimeError):
                self.give_back([self.lot])
            self.db.fail_at = None
            self.assertEqual(self.state(), before, f"a failure at {step} left a trace")
        self.give_back([self.lot])
        self.assertEqual((len(self.db.lot_returns), len(self.db.shop_moves)), (1, 1))

    def test_duplicate_submission_posts_once_and_conflicting_reuse_is_refused(self):
        request = key(900)
        first = self.give_back([self.lot], request=request)
        again = self.give_back([self.lot], request=request)
        self.assertEqual((first["duplicate"], again["duplicate"], again["code"]), (False, True, first["code"]))
        self.assertEqual((len(self.db.lot_returns), len(self.db.shop_moves)), (1, 1))
        self.assertEqual(self.shop(), {str(self.lot): D("2.950")})                       # the grams moved once
        other = self.second_melt(main=("1", "930"))
        other_lot = next(l["id"] for l in self.db.lots.values() if l["entry_id"] == int(other["entry_id"]))
        self.refused("REQUEST_CONFLICT", lambda: self.give_back([other_lot], request=request))
        self.refused("REQUEST_CONFLICT", lambda: self.give_back([self.lot], request=request, notes="ملاحظة أخرى"))
        self.refused("REQUEST_CONFLICT", lambda: self.give_back([self.lot], request=request, weights={self.lot: "2.950"}))
        self.assertEqual(len(self.db.lot_returns), 1)

    def test_the_request_key_collision_inside_the_transaction_is_resolved_from_a_fresh_read(self):
        original = TripRepo.insert_entry

        def twin(repo, **kw):
            TripRepo.insert_entry = original
            raise IntegrityError("1062 (23000): Duplicate entry 'x' for key 'market_trip_entries.uq_mte_request'", errno=1062)
        TripRepo.insert_entry = twin
        try:
            with self.assertRaises(IntegrityError):
                self.give_back([self.lot])                                                 # no twin committed: the real defect surfaces
        finally:
            TripRepo.insert_entry = original
        self.assertEqual(len(self.db.lot_returns), 0)

    def test_only_the_owner_may_return_lots(self):
        before = self.state()
        with self.assertRaises(PermissionError):
            self.give_back([self.lot], service=self.owner_like(MANAGER)[0])
        self.assertEqual(self.state(), before)


class IdentityAndSeparationTests(LotCase):
    def test_returning_never_changes_the_lot_or_the_stored_melt_and_posts_no_other_ledger(self):
        lots_before, melts_before, legs_before = copy.deepcopy(self.db.lots), copy.deepcopy(self.db.melts), copy.deepcopy(self.db.gold_legs)
        self.give_back([self.lot])
        self.assertEqual((self.db.lots, self.db.melts, self.db.gold_legs), (lots_before, melts_before, legs_before))
        self.assertEqual(self.untouched(), self.baseline)
        entry = self.db.entries[max(self.db.entries)]
        self.assertEqual(entry["entry_kind"], "LOT_RETURN")
        link = self.db.lot_returns[0]
        custody_move = next(m for m in self.db.lot_moves if m["id"] == link["custody_movement_id"])
        shop_move = next(m for m in self.db.shop_moves if m["id"] == link["shop_movement_id"])
        self.assertEqual((custody_move["weight_delta"], shop_move["weight_delta"], custody_move["entry_id"], shop_move["entry_id"]),
                         (D("-2.950"), D("2.950"), entry["id"], entry["id"]))              # trip -> melt -> lot -> return entry -> movements
        self.assertIn("MARKET_TRIP_LOT_RETURN", [a["event_type"] for a in self.db.audit_rows])

    def test_closure_waits_for_every_lot_and_a_reversed_melt_never_blocks_it(self):
        second = self.second_melt(main=("1", "930"))
        self.give_back([self.lot])
        message = self.refused("OUTSTANDING_CUSTODY", lambda: self.service.close(self.trip)).args[0]
        self.assertIn("1.000 جرام", message)
        self.assertIn("رجوع ناتج السبك", message)
        self.service.reverse(second["entry_id"], "x")
        self.ret(self.trip, "", [(K21, "1", "MELT")])
        self.assertEqual(self.service.close(self.trip)["status"], "CLOSED")


class ViewModelTests(LotCase):
    def setUp(self):
        super().setUp()
        from app.viewmodels.market_trip_viewmodel import MarketTripViewModel
        self.vm = MarketTripViewModel(self.session, self.service)
        actor = self.session.current_actor()

        def start(operation, call):
            try:
                result, error = call(_expected_actor=actor), ""
            except (ValueError, PermissionError) as exc:
                result, error = None, str(exc)
            getattr(self.vm, f"_on_{operation}")(result, error)
            self.vm.changed.emit()
        self.vm._start = start
        self.signals = {"custody": 0, "entry": []}
        self.vm.custodyChanged.connect(lambda: self.signals.__setitem__("custody", self.signals["custody"] + 1))
        self.vm.entryPosted.connect(self.signals["entry"].append)
        self.vm.selectTrip(self.trip)
        self.vm.refresh()

    def test_the_dialog_choices_show_reference_assay_and_full_weight_read_only(self):
        detail = self.vm.detail
        (lot,) = detail["returnableLots"]
        self.assertEqual((lot["ref"], lot["weight"], lot["available"], lot["returnable"], lot["reason"]),
                         ("MT000001-2 • شحنة 1", "2.950", "2.950", True, ""))
        self.assertIn("930.0", lot["fineness"])
        self.assertTrue(detail["lotReturnAvailable"])
        self.assertFalse(hasattr(self.vm, "remainingText"))                                  # no editable quantity exists any more

    def test_a_partially_returned_lot_is_shown_as_not_returnable_with_the_reason(self):
        self.legacy_partial("1.000")
        self.vm.refresh()
        (lot,) = self.vm.detail["returnableLots"]
        self.assertFalse(lot["returnable"])
        self.assertIn("1.950", lot["reason"])
        self.assertIn("اعكس الرجوع الجزئي", lot["reason"])

    def test_a_whole_return_refreshes_custody_and_shows_the_separate_shop_lot_stock(self):
        self.vm.returnLots(self.trip, [{"lotId": str(self.lot)}], TODAY, "", self.vm.newRequestKey())
        self.assertEqual((self.signals["entry"], self.signals["custody"]), (["MT000001-3"], 0))   # no cash / Scrap balance moved
        self.assertEqual(self.vm.message, "تم رجوع ناتج السبك MT000001-3")
        self.assertEqual(self.vm.detail["returnableLots"], [])
        (row,) = self.vm.shopLots
        self.assertEqual((row["ref"], row["weight"], row["pure"]), ("MT000001-2 • شحنة 1", "2.950", "2.743500"))
        self.assertIn("930.0", row["fineness"])
        entry = self.vm.detail["entries"][-1]
        self.assertEqual(entry["kindText"], "رجوع ناتج السبك")
        self.assertIn("شحنة 1 من MT000001-2: 2.950 جرام", entry["what"])
        self.assertTrue(entry["canReverse"])

    def test_the_shop_stock_is_readable_with_no_trip_selected_and_unknown_assays_stay_unknown(self):
        result = self.second_melt(main=("1.100", "900"), residues=[("0.400", "", "خبث")], grams="1.500")
        residue = next(l["id"] for l in self.db.lots.values() if l["entry_id"] == int(result["entry_id"]) and l["lot_kind"] == "RESIDUE")
        self.vm.returnLots(self.trip, [{"lotId": str(residue)}, {"lotId": str(self.lot)}], TODAY, "", self.vm.newRequestKey())
        self.vm.selectTrip("")
        self.vm.refresh()
        rows = {r["lotId"]: r for r in self.vm.shopLots}
        self.assertEqual((rows[str(residue)]["fineness"], rows[str(residue)]["pure"]), ("غير معلومة", "غير معلومة"))
        self.assertEqual(rows[str(self.lot)]["pure"], "2.743500")
        self.assertIn("الذهب الصافي المعروف 2.743500", self.vm.shopLotSummary)
        self.assertIn("1 شحنة بلا عيار معلوم", self.vm.shopLotSummary)
        self.assertEqual(self.vm.detail, {})

    def test_a_refusal_keeps_the_error_duplicate_posts_once_and_a_reversal_restores_the_whole_lot(self):
        legacy = self.legacy_partial("1.000")
        before = self.state()
        self.vm.returnLots(self.trip, [{"lotId": str(self.lot)}], TODAY, "", self.vm.newRequestKey())
        self.assertIn("اعكس الرجوع الجزئي", self.vm.errorMessage)
        self.assertEqual(self.state(), before)
        self.vm.reverseEntry(str(legacy), "جزئي")
        request = self.vm.newRequestKey()
        self.vm.returnLots(self.trip, [{"lotId": str(self.lot)}], TODAY, "", request)
        self.vm.returnLots(self.trip, [{"lotId": str(self.lot)}], TODAY, "", request)
        self.assertIn("مسجلة من قبل", self.vm.message)
        self.assertEqual(len([r for r in self.db.lot_returns if r["weight_grams"] == D("2.950")]), 1)
        self.vm.reverseEntry(str(max(self.db.entries)), "خطأ")
        self.assertTrue(self.vm.detail["entries"][-1]["reversed"])
        self.assertEqual(self.vm.shopLots, [])
        self.assertEqual(self.vm.detail["returnableLots"][0]["available"], "2.950")


if __name__ == "__main__":
    unittest.main()
