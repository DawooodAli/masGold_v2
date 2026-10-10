"""Market-trip lot reissue (041, M3): a whole bar that returned to the shop leaves again in a DIFFERENT open custody and returns from it.
The bar is in the shop or in exactly one custody; its location is derived from the effective movements, never from its origin melt.

Real MarketTripService and the real CorrectionService over the in-memory transactional database of tests/market_trip_fakes.py: a
failed transaction restores every table and the 041 CHECK / unique constraints are mirrored as asserts. These are NOT real-MySQL
tests: the SQL, the constraints and the rollback were exercised on the masgold_rehearsal sandbox, single-threaded. Concurrency
(two custodies issuing the same bar at once) is NOT exercised anywhere: the lot lock is inferred from the code, not proven."""
import copy
from datetime import date
from decimal import Decimal as D
from types import SimpleNamespace
import unittest

from mysql.connector import IntegrityError

from services.correction_service import CorrectionError
from tests.market_trip_fakes import MANAGER, OWNER_NO_CORRECT, TripRepo
from tests.test_market_trips import K21, K24, TODAY, TripCase, key


class BarCase(TripCase):
    """Trip A melts 2.950 g at 930.0 (one lot), returns it to the shop and closes. Further custodies are opened with a little cash."""

    def setUp(self):
        super().setUp()
        self.db.scrap.append({"id": 90, "karat_id": 3, "weight_delta": D("10.000"), "reference_type": "OPENING", "reference_id": "0",
                              "type_code": "OPENING"})
        self.db.sequences["scrap"] = 90
        self.origin = self.issue("", [(K21, "2", "MELT"), (K24, "1", "MELT")])["trip_id"]
        self.n += 1
        self.melt_result = self.service.melt(self.origin, [{"karat_id": K21, "weight": "2"}, {"karat_id": K24, "weight": "1"}], TODAY, "ورشة",
                                             {"weight": "2.950", "fineness": "930"}, [], "", "", "", key(300 + self.n))
        self.melt_entry = int(self.melt_result["entry_id"])
        self.lot = next(l["id"] for l in self.db.lots.values() if l["entry_id"] == self.melt_entry)
        self.back = self.give_back(self.origin, [self.lot])                              # origin return to the shop
        self.service.close(self.origin)
        self.melt_snapshot = copy.deepcopy((self.db.melts, self.db.lots))
        self.baseline = self.untouched()

    # ---- helpers
    def custody(self, cash="100"):
        """A new OPEN custody (a little cash only: it holds no gold, so it can close as soon as the bar is back)."""
        return self.issue(cash, [])["trip_id"]

    def give_out(self, trip, lots, request=None, service=None, weights=None, when=TODAY, notes=""):
        self.n += 1
        lines = [{"lot_id": str(l), **({"weight": weights[l]} if weights and l in weights else {})} for l in lots]
        return (service or self.service).issue_lots(trip, lines, when, notes, request or key(700 + self.n))

    def give_back(self, trip, lots, request=None, service=None):
        self.n += 1
        return (service or self.service).return_lots(trip, [{"lot_id": str(l)} for l in lots], TODAY, "", request or key(500 + self.n))

    def held(self, trip):
        return {l["lot_id"]: l["outstanding"] for l in self.service.trip_detail(trip)["custody"]["lots"]}

    def shop(self):
        return {l["lot_id"]: l["stock"] for l in self.service.shop_lots()}

    def where(self):
        """(shop grams, {trip: grams}) of the bar: the invariant is that the sum is always exactly the bar's weight."""
        trips = {t: g[str(self.lot)] for t in self.db.trips for g in [self.held(str(t))] if str(self.lot) in g}
        shop = self.shop().get(str(self.lot), D("0.000"))
        self.assertEqual(shop + sum(trips.values(), D("0.000")), D("2.950"), "the bar must be in exactly one place")
        return shop, trips

    def untouched(self):
        return copy.deepcopy((self.db.cash_moves[:1], self.db.scrap, self.db.corrections))

    def legacy_partial_shop(self, trip, grams="1.000"):
        """A partial return written by the earlier build: 1.000 g in the shop, the rest in custody."""
        repo = TripRepo(self.db)
        entry = repo.insert_entry(trip_id=int(trip), entry_no=repo.next_entry_no(int(trip)), kind="LOT_RETURN", entry_date=date(2026, 10, 10),
                                  notes=None, key=key(778), digest="x" * 64, actor=SimpleNamespace(user_id=7, employee_id=None), now=self.db.now)
        custody = repo.insert_lot_movement(lot_id=self.lot, entry_id=entry, movement_type="SHOP_RETURN", weight_delta=-D(grams))
        shop = repo.insert_shop_lot_movement(lot_id=self.lot, entry_id=entry, movement_type="TRIP_RETURN", weight_delta=D(grams))
        repo.insert_lot_return(entry_id=entry, lot_id=self.lot, weight=D(grams), custody_movement_id=custody, shop_movement_id=shop)
        return entry


class CycleTests(BarCase):
    def test_the_documented_cycle_origin_closed_issue_return_close_and_repeat(self):
        self.assertEqual(self.where(), (D("2.950"), {}))                                   # 1. back in the shop, origin closed
        self.assertEqual(self.db.trips[int(self.origin)]["status"], "CLOSED")
        second = self.custody()
        issued = self.give_out(second, [self.lot])                                         # 2. a different custody takes the same bar
        self.assertEqual((issued["kind"], issued["code"].split("-")[0]), ("LOT_ISSUE", "MT000002"))
        self.assertEqual(self.where(), (D("0.000"), {2: D("2.950")}))                       # shop zero, destination 2.950
        third = self.custody()
        self.refused("LOT_NOT_IN_SHOP", lambda: self.give_out(third, [self.lot]))          # 3. never in two places
        self.assertEqual(self.where(), (D("0.000"), {2: D("2.950")}))
        self.give_back(second, [self.lot])                                                  # 4. returned whole from the destination
        self.assertEqual(self.where(), (D("2.950"), {}))
        self.assertEqual(self.held(second), {})
        # 5. the destination can close, and the cycle repeats in another custody
        self.service.return_custody(second, "100", [], TODAY, "", key(910))
        self.assertEqual(self.service.close(second)["status"], "CLOSED")
        self.give_out(third, [self.lot])
        self.assertEqual(self.where(), (D("0.000"), {3: D("2.950")}))
        self.give_back(third, [self.lot])
        self.assertEqual(self.where(), (D("2.950"), {}))
        # 6. identity, assay and the stored melt analysis never moved
        self.assertEqual(copy.deepcopy((self.db.melts, self.db.lots)), self.melt_snapshot)
        self.assertEqual([m["movement_type"] for m in self.db.shop_moves], ["TRIP_RETURN", "TRIP_ISSUE", "TRIP_RETURN", "TRIP_ISSUE", "TRIP_RETURN"])
        # 9. no money, Scrap or supplier effect of any transfer (the cash used to open the custodies is returned)
        self.assertEqual(self.db.scrap, self.baseline[1])

    def test_the_bar_in_a_destination_custody_keeps_its_origin_reference_and_blocks_that_closure(self):
        second = self.custody()
        self.give_out(second, [self.lot])
        (lot,) = self.service.trip_detail(second)["custody"]["lots"]
        self.assertEqual((lot["code"], lot["origin_trip_id"], lot["weight"], lot["fineness"]), ("MT000001-2", 1, D("2.950"), D("930.0")))
        self.service.return_custody(second, "100", [], TODAY, "", key(911))
        message = self.refused("OUTSTANDING_CUSTODY", lambda: self.service.close(second)).args[0]
        self.assertIn("2.950 جرام", message)
        self.assertEqual(self.service.trip_detail(self.origin)["custody"]["lots"], [])    # and the origin shows nothing: location is derived
        self.assertEqual(self.service.trip_detail(self.origin)["trip"]["status"], "CLOSED")

    def test_an_unassayed_residue_can_be_reissued_with_its_assay_still_unknown(self):
        self.issue("", [(K21, "2", "MELT")], trip=self.custody())
        trip = str(max(self.db.trips))
        self.n += 1
        result = self.service.melt(trip, [{"karat_id": K21, "weight": "2"}], TODAY, "ورشة", {"weight": "1.500", "fineness": "900"},
                                   [{"weight": "0.400", "fineness": "", "label": "خبث"}], "", "", "", key(300 + self.n))
        residue = next(l["id"] for l in self.db.lots.values() if l["entry_id"] == int(result["entry_id"]) and l["lot_kind"] == "RESIDUE")
        self.give_back(trip, [residue])
        other = self.custody()
        self.give_out(other, [residue])
        (row,) = [l for l in self.service.trip_detail(other)["custody"]["lots"]]
        self.assertEqual((row["kind"], row["fineness"], row["pure"], row["outstanding"]), ("RESIDUE", None, None, D("0.400")))
        self.assertIsNone(self.db.lots[residue]["fineness_permille"])
        self.give_back(other, [residue])
        self.assertEqual(self.shop()[str(residue)], D("0.400"))


class RefusalTests(BarCase):
    def test_bad_requests_are_refused_without_any_effect(self):
        second = self.custody()
        before = self.state()
        for lots, code in (([], "NOTHING_TO_RECORD"), ([""], "NOTHING_TO_RECORD"), ([999], "LOT_NOT_FOUND"), (["x"], "LOT_NOT_FOUND"),
                           ([self.lot, self.lot], "LOT_DUPLICATE")):
            with self.subTest(lots=lots):
                self.refused(code, lambda l=lots: self.give_out(second, l))
        for weights in ({self.lot: "1.000"}, {self.lot: "2.949"}, {self.lot: "2.951"}, {self.lot: "0"}):
            message = self.refused("LOT_PARTIAL_NOT_ALLOWED", lambda w=weights: self.give_out(second, [self.lot], weights=w)).args[0]
            self.assertIn("تخرج السبكة كاملة ولا يمكن إخراج جزء منها", message)
        self.refused("WEIGHT_INVALID", lambda: self.give_out(second, [self.lot], weights={self.lot: "abc"}))
        self.refused("TRIP_NOT_FOUND", lambda: self.give_out("0", [self.lot]))
        self.refused("DATE_REQUIRED", lambda: self.give_out(second, [self.lot], when=""))
        self.refused("TEXT_LONG", lambda: self.give_out(second, [self.lot], notes="ن" * 1001))
        self.refused("REQUEST_INVALID", lambda: self.give_out(second, [self.lot], request="not-a-key"))
        self.assertEqual(self.state(), before)

    def test_a_bar_that_is_not_in_the_shop_is_refused_and_a_closed_destination_too(self):
        trip = self.issue("", [(K21, "1", "MELT")])["trip_id"]
        self.n += 1
        fresh = self.service.melt(trip, [{"karat_id": K21, "weight": "1"}], TODAY, "ورشة", {"weight": "1", "fineness": "900"}, [], "", "", "", key(300 + self.n))
        fresh_lot = next(l["id"] for l in self.db.lots.values() if l["entry_id"] == int(fresh["entry_id"]))
        other = self.custody()
        self.refused("LOT_NOT_IN_SHOP", lambda: self.give_out(other, [fresh_lot]))          # still in its melt custody, never returned
        self.service.return_custody(other, "100", [], TODAY, "", key(912))
        self.service.close(other)
        self.refused("TRIP_CLOSED", lambda: self.give_out(other, [self.lot]))
        self.assertEqual(self.where(), (D("2.950"), {}))

    def test_a_partial_legacy_shop_stock_is_refused_with_an_explanation_and_not_completed(self):
        self.service.reopen(self.origin, "مراجعة")                                           # a closed custody needs the audited reopening
        self.service.reverse(self.back["entry_id"], "رجوع")                                   # the bar is back in its origin custody ...
        partial = self.legacy_partial_shop(self.origin, "1.000")                           # ... and an old partial return puts 1.000 g in the shop
        self.assertEqual(self.where(), (D("1.000"), {1: D("1.950")}))
        second = self.custody()
        history = copy.deepcopy((self.db.lot_returns, self.db.shop_moves, self.db.lot_moves))
        before = self.state()
        message = self.refused("LOT_PARTIAL_SHOP_STOCK", lambda: self.give_out(second, [self.lot])).args[0]
        self.assertIn("1.000", message)
        self.assertIn("2.950", message)
        self.assertIn("اعكس الرجوع الجزئي", message)
        self.assertEqual(self.state(), before)
        self.assertEqual(copy.deepcopy((self.db.lot_returns, self.db.shop_moves, self.db.lot_moves)), history)
        self.service.reverse(partial, "جزئي قديم")
        self.assertEqual(self.where(), (D("0.000"), {1: D("2.950")}))                       # whole again, in the origin custody only

    def test_only_the_owner_may_reissue(self):
        second = self.custody()
        before = self.state()
        with self.assertRaises(PermissionError):
            self.give_out(second, [self.lot], service=self.owner_like(MANAGER)[0])
        self.assertEqual(self.state(), before)

    def test_a_failure_at_any_posting_step_persists_nothing(self):
        second = self.custody()
        before = self.state()
        for step in ("entry", "shop_movement", "lot_movement", "lot_issue", "audit"):
            self.db.fail_at = step
            with self.assertRaises(RuntimeError):
                self.give_out(second, [self.lot])
            self.db.fail_at = None
            self.assertEqual(self.state(), before, f"a failure at {step} left a trace")
        self.give_out(second, [self.lot])
        self.assertEqual((len(self.db.lot_issues), self.where()), (1, (D("0.000"), {2: D("2.950")})))

    def test_duplicate_submission_posts_once_and_conflicting_reuse_is_refused(self):
        second = self.custody()
        request = key(920)
        first = self.give_out(second, [self.lot], request=request)
        again = self.give_out(second, [self.lot], request=request)
        self.assertEqual((first["duplicate"], again["duplicate"], again["code"]), (False, True, first["code"]))
        self.assertEqual(len(self.db.lot_issues), 1)
        self.assertEqual(self.where(), (D("0.000"), {2: D("2.950")}))
        self.refused("REQUEST_CONFLICT", lambda: self.give_out(second, [self.lot], request=request, notes="ملاحظة أخرى"))
        self.refused("REQUEST_CONFLICT", lambda: self.give_out(second, [self.lot], request=request, weights={self.lot: "2.950"}))
        third = self.custody()
        self.refused("REQUEST_CONFLICT", lambda: self.give_out(third, [self.lot], request=request))
        self.assertEqual(len(self.db.lot_issues), 1)

    def test_the_request_key_collision_inside_the_transaction_is_resolved_from_a_fresh_read(self):
        second = self.custody()
        original = TripRepo.insert_entry

        def twin(repo, **kw):
            TripRepo.insert_entry = original
            raise IntegrityError("1062 (23000): Duplicate entry 'x' for key 'market_trip_entries.uq_mte_request'", errno=1062)
        TripRepo.insert_entry = twin
        try:
            with self.assertRaises(IntegrityError):
                self.give_out(second, [self.lot])
        finally:
            TripRepo.insert_entry = original
        self.assertEqual(len(self.db.lot_issues), 0)


class DependencyAndReversalTests(BarCase):
    def test_reversals_in_the_proper_order_never_put_the_bar_in_two_places(self):
        second, third = self.custody(), self.custody()
        out_b = self.give_out(second, [self.lot])
        back_b = self.give_back(second, [self.lot])
        out_c = self.give_out(third, [self.lot])
        self.assertEqual(self.where(), (D("0.000"), {3: D("2.950")}))
        # the return into the shop cannot be undone while the shop stock has been reissued elsewhere
        with self.assertRaises(CorrectionError) as blocked:
            self.service.reverse(back_b["entry_id"], "x")
        self.assertEqual(blocked.exception.code, "SHOP_LOT_STOCK_INSUFFICIENT")
        # the origin return and the melt are protected while any downstream transfer is effective (the origin custody is reopened first)
        with self.assertRaises(CorrectionError) as closed_origin:
            self.service.reverse(self.back["entry_id"], "x")
        self.assertEqual(closed_origin.exception.code, "TRIP_CLOSED")
        self.service.reopen(self.origin, "مراجعة")
        with self.assertRaises(CorrectionError) as origin_blocked:
            self.service.reverse(self.back["entry_id"], "x")
        self.assertEqual(origin_blocked.exception.code, "SHOP_LOT_STOCK_INSUFFICIENT")
        with self.assertRaises(CorrectionError) as melt_blocked:
            self.service.reverse(self.melt_entry, "x")
        self.assertEqual(melt_blocked.exception.code, "DEPENDENT_LOT_USE_EXISTS")
        # newest first: C's issue, B's return, B's issue, origin return, melt
        self.service.reverse(out_c["entry_id"], "x")
        self.assertEqual(self.where(), (D("2.950"), {}))
        with self.assertRaises(CorrectionError) as dependent:                               # B's issue still has its return after it
            self.service.reverse(out_b["entry_id"], "x")
        self.assertEqual(dependent.exception.code, "DEPENDENT_LOT_RETURN_EXISTS")
        self.service.reverse(back_b["entry_id"], "x")
        self.assertEqual(self.where(), (D("0.000"), {2: D("2.950")}))                       # back in B's custody, whole
        self.service.reverse(out_b["entry_id"], "x")
        self.assertEqual(self.where(), (D("2.950"), {}))
        self.service.reverse(self.back["entry_id"], "x")                                    # the origin return: the bar is in its origin custody
        self.assertEqual(self.where(), (D("0.000"), {1: D("2.950")}))
        self.assertEqual(copy.deepcopy((self.db.melts, self.db.lots)), self.melt_snapshot)

    def test_a_reissue_reversal_restores_the_shop_stock_exactly_and_is_double_protected(self):
        second = self.custody()
        done = self.give_out(second, [self.lot])
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
        result = self.service.reverse(done["entry_id"], "إخراج خاطئ")
        self.assertTrue(result["code"].startswith("RV"))
        self.assertEqual(self.where(), (D("2.950"), {}))
        self.assertEqual(sorted((m["movement_type"], m["weight_delta"]) for m in self.db.shop_moves if m["movement_type"] in ("TRIP_ISSUE", "ISSUE_REVERSAL")),
                         [("ISSUE_REVERSAL", D("2.950")), ("TRIP_ISSUE", D("-2.950"))])
        with self.assertRaises(CorrectionError) as again:
            self.service.reverse(done["entry_id"], "x")
        self.assertEqual(again.exception.code, "ALREADY_REVERSED")
        self.give_out(second, [self.lot])                                                   # and it can be issued again
        self.assertEqual(self.where(), (D("0.000"), {2: D("2.950")}))

    def test_a_closed_destination_needs_the_audited_reopening_before_a_reversal(self):
        second = self.custody()
        done = self.give_out(second, [self.lot])
        self.give_back(second, [self.lot])
        self.service.return_custody(second, "100", [], TODAY, "", key(930))
        self.service.close(second)
        with self.assertRaises(CorrectionError) as closed:
            self.service.reverse(done["entry_id"], "x")
        self.assertEqual(closed.exception.code, "TRIP_CLOSED")
        self.service.reopen(second, "مراجعة")
        with self.assertRaises(CorrectionError) as dependent:                               # its own return is still after it
            self.service.reverse(done["entry_id"], "x")
        self.assertEqual(dependent.exception.code, "DEPENDENT_LOT_RETURN_EXISTS")

    def test_a_failure_inside_the_reversal_changes_nothing(self):
        second = self.custody()
        done = self.give_out(second, [self.lot])
        before = self.state()
        for step in ("correction", "shop_movement", "audit"):
            self.db.fail_at = step
            with self.assertRaises(RuntimeError):
                self.service.reverse(done["entry_id"], "x")
            self.db.fail_at = None
            self.assertEqual(self.state(), before, step)


class ViewModelTests(BarCase):
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
        self.second = self.custody()
        self.vm.selectTrip(self.second)
        self.vm.refresh()

    def test_the_shop_choices_are_whole_bars_with_reference_assay_and_full_weight(self):
        (row,) = self.vm.shopLots
        self.assertEqual((row["ref"], row["fullWeight"], row["weight"], row["issuable"], row["reason"]), ("MT000001-2 • شحنة 1", "2.950", "2.950", True, ""))
        self.assertIn("930.0", row["fineness"])
        self.assertFalse(hasattr(self.vm, "remainingText"))

    def test_a_reissue_moves_the_bar_between_the_shop_panel_and_the_destination_custody(self):
        self.vm.issueLots(self.second, [{"lotId": str(self.lot)}], TODAY, "", self.vm.newRequestKey())
        self.assertEqual((self.signals["entry"][-1].split("-")[0], self.signals["custody"]), ("MT000002", 0))   # no cash / Scrap balance moved
        self.assertEqual(self.vm.message.split(" MT")[0], "تم إخراج السبكة من المحل")
        self.assertEqual(self.vm.shopLots, [])                                              # the shop stock disappears while the bar is out
        (lot,) = self.vm.detail["lots"]
        self.assertEqual((lot["weight"], lot["outstanding"]), ("2.950", "2.950"))
        self.assertIn("أُعيد إخراجها من المحل", lot["title"])
        self.assertIn("MT000001-2", lot["title"])
        (returnable,) = self.vm.detail["returnableLots"]
        self.assertEqual((returnable["ref"], returnable["returnable"]), ("MT000001-2 • شحنة 1", True))
        entry = self.vm.detail["entries"][-1]
        self.assertEqual(entry["kindText"], "إخراج سبكة من المحل")
        self.assertIn("شحنة 1 من MT000001-2: 2.950 جرام", entry["what"])
        self.vm.returnLots(self.second, [{"lotId": str(self.lot)}], TODAY, "", self.vm.newRequestKey())
        (row,) = self.vm.shopLots                                                           # and it reappears on return
        self.assertEqual(row["weight"], "2.950")
        self.assertEqual(self.vm.detail["lots"], [])

    def test_a_refusal_keeps_the_error_and_a_duplicate_posts_once(self):
        self.vm.issueLots(self.second, [{"lotId": "999"}], TODAY, "", self.vm.newRequestKey())
        self.assertIn("غير موجودة", self.vm.errorMessage)
        request = self.vm.newRequestKey()
        self.vm.issueLots(self.second, [{"lotId": str(self.lot)}], TODAY, "", request)
        self.vm.issueLots(self.second, [{"lotId": str(self.lot)}], TODAY, "", request)
        self.assertIn("مسجلة من قبل", self.vm.message)
        self.assertEqual(len(self.db.lot_issues), 1)
        self.vm.issueLots(self.second, [{"lotId": str(self.lot)}], TODAY, "", self.vm.newRequestKey())
        self.assertIn("ليست بالمحل", self.vm.errorMessage)


if __name__ == "__main__":
    unittest.main()
