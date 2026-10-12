"""Master-karat LOGO holdings at a market intermediary (049). Real MarketTripService, IntermediaryHoldingService and CorrectionService over the
in-memory transactional database of tests/market_trip_fakes.py (the 049 CHECK / unique / composite keys are mirrored as asserts). NOT real-MySQL
tests: the SQL, the constraints and the lock behaviour were exercised on masgold_rehearsal (tests/test_logo_holdings_mysql.py, opt-in).
LOGO gold is pooled physical grams of ONE master karat: no whole-bar rule, no intact confirmation, no fineness."""
from decimal import Decimal as D
from unittest.mock import patch
import unittest

from services.correction_service import CorrectionError
from services.intermediary_holding_service import IntermediaryHoldingService
from services.market_trip_service import TripError
from tests.market_trip_fakes import HoldingRepo, OWNER, ScrapRepo, SettlementRepo, TripRepo
from tests.test_intermediary_holdings import HoldingCase, hkey
from tests.test_market_trips import K18, K21, TODAY, key

PARTY, DUAL, INACTIVE, SUPPLIER = "20", "21", "22", "10"


class LogoCase(HoldingCase):
    """Custody MT000001 holds 20 g 21K LOGO, 5 g 18K LOGO and 3 g 21K MELT (the 048 melt of the base case is made on top of the same custody)."""

    def setUp(self):
        super().setUp()
        self.logo_trip = self.issue("", [(K21, "20", "LOGO"), (K18, "5", "LOGO"), (K21, "3", "MELT")])["trip_id"]
        self.baseline = self.untouched()
        self.l = 0

    # ---- helpers
    def put(self, grams, karat=K21, party=PARTY, trip=None, request=None, service=None, when=TODAY):
        self.l += 1
        return (service or self.holding).hand_over_logo(trip or self.logo_trip, party, karat, grams, when, "", "", request or hkey(2000 + self.l))

    def take(self, pool, grams, request=None, service=None):
        self.l += 1
        return (service or self.holding).return_logo(pool, grams, TODAY, "", "", request or hkey(3000 + self.l))

    def logo(self, karat=K21, trip=None):
        rows = {(g["karat_id"], g["designation"]): g for g in self.service.trip_detail(trip or self.logo_trip)["custody"]["gold"]}
        return rows[(int(karat), "LOGO")]["outstanding"]

    def balance(self, pool):
        return HoldingRepo(self.db).logo_pool_balance(pool)

    def held(self, party=PARTY):
        return sum((self.balance(p["id"]) for p in self.db.logo_pools.values() if p["party_id"] == int(party)), D("0.000"))

    def conserved(self, karat=K21, issued=None):
        """custody + at the intermediary == what was taken out of the shop: nothing is created or lost by handovers and returns."""
        held = sum((self.balance(p["id"]) for p in self.db.logo_pools.values() if p["karat_id"] == int(karat)), D("0.000"))
        return self.logo(karat) + held

    def pool_ids(self):
        return sorted(self.db.logo_pools)


class HandoverReturnTests(LogoCase):
    def test_handover_moves_exact_grams_out_of_custody_into_a_separate_source_and_touches_nothing_else(self):
        done = self.put("8")
        self.assertEqual((done["kind"], done["document_code"], done["weight"], done["duplicate"]), ("LOGO_HOLD", "IG000001", "8.000", False))
        self.assertEqual((self.logo(), self.held(), self.conserved()), (D("12.000"), D("8.000"), D("20.000")))
        self.assertEqual(self.logo(K18), D("5.000"))                                              # another karat is untouched
        self.assertEqual(self.untouched(), self.baseline)                                          # no Scrap, cash, making, supplier or shop-lot movement
        self.assertEqual([r["event_type"] for r in self.db.audit_rows][-2:], ["MARKET_TRIP_LOGO_HOLD", "INTERMEDIARY_LOGO_HANDOVER"])
        detail = self.service.trip_detail(self.logo_trip)
        self.assertEqual([p["balance"] for p in detail["external_holdings"] if p.get("logo")], [D("8.000")])

    def test_partial_returns_are_allowed_and_capped_by_the_selected_source(self):
        self.put("8")
        self.take(1, "3")
        self.assertEqual((self.logo(), self.balance(1), self.conserved()), (D("15.000"), D("5.000"), D("20.000")))
        self.refuse("LOGO_RETURN_EXCEEDS_HOLDING", lambda: self.take(1, "5.001"))
        self.take(1, "5")
        self.assertEqual((self.logo(), self.held()), (D("20.000"), D("0.000")))
        self.refuse("HOLDING_NOT_AVAILABLE", lambda: self.take(1, "0.001"))
        self.assertEqual(self.untouched(), self.baseline)

    def test_quantities_are_positive_three_decimal_decimals_within_the_available_custody(self):
        for bad in ("0", "-1", "abc", "1.0001", ""):
            before = self.state()
            with self.assertRaises(TripError):
                self.put(bad)
            self.assertEqual(self.state(), before)
        self.refuse("LOGO_EXCEEDS_CUSTODY", lambda: self.put("20.001"))
        self.put("20")                                                                              # all of it is allowed
        self.assertEqual(self.logo(), D("0.000"))
        self.refuse("LOGO_EXCEEDS_CUSTODY", lambda: self.put("0.001"))

    def test_only_effective_logo_is_available_melt_and_other_karats_are_not(self):
        self.refuse("LOGO_EXCEEDS_CUSTODY", lambda: self.put("6", K18))                           # only 5 g of 18K LOGO
        melt_only = self.issue("", [(K21, "4", "MELT")])["trip_id"]
        self.refuse("LOGO_EXCEEDS_CUSTODY", lambda: self.put("1", K21, trip=melt_only))           # MELT-designated gold is outside this slice
        self.assertEqual(self.held(), D("0.000"))

    def test_the_party_must_be_an_active_intermediary_and_its_supplier_karat_does_not_decide_the_holding_karat(self):
        self.refuse("INTERMEDIARY_ROLE_REQUIRED", lambda: self.put("1", party=SUPPLIER))
        self.refuse("PARTY_INACTIVE", lambda: self.put("1", party=INACTIVE))
        self.refuse("PARTY_NOT_FOUND", lambda: self.put("1", party="999"))
        self.put("2", K18, party=DUAL)                                                              # DUAL is a 21K supplier: the holding is 18K grams
        self.assertEqual(self.db.logo_pools[1]["karat_id"], int(K18))
        self.assertEqual((self.db.sup_gold, self.db.settlements), ([], {}))                        # its supplier account is untouched

    def test_each_handover_is_a_separate_source_even_for_the_same_party_karat_and_custody(self):
        self.put("4")
        self.put("6")
        self.assertEqual(self.pool_ids(), [1, 2])
        self.assertEqual((self.balance(1), self.balance(2), self.logo()), (D("4.000"), D("6.000"), D("10.000")))
        self.take(2, "6")                                                                           # a return names ONE source; the other is not touched
        self.assertEqual((self.balance(1), self.balance(2)), (D("4.000"), D("0.000")))
        self.refuse("LOGO_RETURN_EXCEEDS_HOLDING", lambda: self.take(1, "5"))                      # no cross-pool allocation

    def test_closure_with_an_external_holding_reopening_and_the_return_to_the_original_custody(self):
        self.put("20")
        self.put("5", K18)
        self.service.return_custody(self.logo_trip, "", [{"karat_id": K21, "weight": "3", "designation": "MELT"}], TODAY, "", key(8101))
        self.assertEqual(self.service.close(self.logo_trip)["status"], "CLOSED")                   # its OWN outstanding is zero
        detail = self.service.trip_detail(self.logo_trip)
        self.assertEqual(sorted(p["balance"] for p in detail["external_holdings"] if p.get("logo")), [D("5.000"), D("20.000")])
        self.refuse("TRIP_CLOSED", lambda: self.take(1, "20"))
        self.service.reopen(self.logo_trip, "x")
        self.take(1, "20")
        self.assertEqual(self.logo(), D("20.000"))

    def test_an_inactive_party_still_resolves_what_it_holds_and_gets_no_new_handover(self):
        self.put("5")
        self.db.suppliers[20]["is_active"] = 0
        self.take(1, "5")
        self.refuse("PARTY_INACTIVE", lambda: self.put("1"))

    def test_rights_are_rechecked_and_the_actor_is_the_session_user(self):
        for permissions in (OWNER - {"INTERMEDIARY_HOLDING_MANAGE"}, OWNER - {"MARKET_TRIP_MANAGE"}):
            _, holding = self.as_user(permissions)
            before = self.state()
            with self.assertRaises(PermissionError):
                self.put("1", service=holding)
            self.assertEqual(self.state(), before)
        self.authorization.permissions = OWNER - {"INTERMEDIARY_HOLDING_MANAGE"}
        with self.assertRaises(PermissionError):
            self.put("1")
        self.authorization.permissions = OWNER
        self.put("1")
        self.assertEqual(self.db.logo_docs[1]["performed_by_user_id"], 7)

    def test_every_stage_failure_leaves_no_trace(self):
        before = self.state()
        for step in ("logo_pool", "entry", "logo_document", "logo_movement", "logo_link", "audit", "holding_audit"):
            self.db.fail_at = step
            with self.assertRaises(RuntimeError):
                self.put("5")
            self.db.fail_at = None
            self.assertEqual(self.state(), before, step)
        self.put("5")
        before = self.state()
        for step in ("entry", "logo_document", "logo_movement", "logo_link", "audit", "holding_audit"):
            self.db.fail_at = step
            with self.assertRaises(RuntimeError):
                self.take(1, "2")
            self.db.fail_at = None
            self.assertEqual(self.state(), before, step)

    def test_lock_order_is_trip_party_pool_and_a_new_source_has_nothing_to_lock(self):
        self.db.lock_log.clear()
        self.put("5")
        self.assertEqual(self.db.lock_log, [("trip",), ("party", 20)])
        self.db.lock_log.clear()
        done = self.take(1, "2")
        self.assertEqual(self.db.lock_log, [("trip",), ("party", 20), ("logo_pool", 1)])
        self.db.lock_log.clear()
        self.service.reverse(done["entry_id"], "x")
        self.assertEqual([e[0] for e in self.db.lock_log if e != ("lots", ())][:3], ["trip", "party", "logo_pool"])   # an empty lot list locks nothing


class ReplayAndGuardTests(LogoCase):
    def test_exact_replay_precedes_availability_and_other_uses_of_the_key_conflict(self):
        request = hkey(500)
        first = self.put("20", request=request)
        after = self.state()
        again = self.put("20", request=request)                                                     # the custody has 0 left: no availability is derived
        self.assertEqual((again["duplicate"], again["document_id"], again["entry_id"]), (True, first["document_id"], first["entry_id"]))
        self.assertEqual(self.state(), after)
        self.refuse("REQUEST_CONFLICT", lambda: self.put("19", request=request))
        self.refuse("REQUEST_CONFLICT", lambda: self.put("20", K18, request=request))
        self.refuse("REQUEST_CONFLICT", lambda: self.take(1, "1", request=request))
        self.refuse("REQUEST_CONFLICT", lambda: self.issue("5", request=request))
        self.refuse("REQUEST_CONFLICT", lambda: self.hand(lot=self.main, request=request))        # a 048 whole-lot handover cannot reuse it
        taken = self.take(1, "4", request=hkey(501))
        self.assertEqual(self.take(1, "4", request=hkey(501))["document_id"], taken["document_id"])

    def test_a_key_used_by_another_flow_cannot_start_a_logo_handover(self):
        request = hkey(502)
        self.hand(request=request)                                                                  # a 048 whole-lot handover
        self.refuse("REQUEST_CONFLICT", lambda: self.put("1", request=request))
        other = hkey(503)
        self.issue("5", request=other)
        self.refuse("REQUEST_CONFLICT", lambda: self.put("1", request=other))
        direct = __import__("services.supplier_gold_settlement_service", fromlist=["x"]).SupplierGoldSettlementService(
            self.session, transaction_factory=self.db.transaction, repository=SettlementRepo, scrap=ScrapRepo, authorization=self.authorization,
            corrections=self.corrections)
        logo_key = hkey(504)
        self.put("1", request=logo_key)
        before = self.state()
        with self.assertRaises(__import__("services.supplier_rules", fromlist=["x"]).SupplierRuleError) as context:
            direct.settle("21", K21, "1", TODAY, "", "", logo_key)
        self.assertEqual(context.exception.code, "REQUEST_CONFLICT")
        self.assertEqual(self.state(), before)

    def test_logo_history_keeps_the_intermediary_role_and_the_party_row(self):
        self.assertEqual(HoldingRepo(self.db).history_count(20), 0)
        self.put("1")
        self.assertGreaterEqual(HoldingRepo(self.db).history_count(20), 2)                         # one pool and one document, reversed or not


class ReversalTests(LogoCase):
    def test_reversing_a_handover_restores_the_stored_quantity_with_an_explicit_compensation(self):
        done = self.put("8")
        self.service.reverse(done["entry_id"], "تسليم بالخطأ")
        self.assertEqual((self.logo(), self.held(), self.conserved()), (D("20.000"), D("0.000"), D("20.000")))
        original, compensation = self.db.logo_moves
        self.assertEqual((original["movement_type"], original["weight_delta"], original["correction_id"]), ("HANDOVER", D("8.000"), None))
        self.assertEqual((compensation["movement_type"], compensation["weight_delta"]), ("HANDOVER_REVERSAL", D("-8.000")))
        self.assertIsNotNone(compensation["correction_id"])
        self.assertEqual(self.untouched(), self.baseline)
        self.refuse("ALREADY_REVERSED", lambda: self.service.reverse(done["entry_id"], "x"), CorrectionError)
        self.refuse("HOLDING_NOT_AVAILABLE", lambda: self.take(1, "1"))                            # nothing left at the party

    def test_a_handover_with_effective_returns_cannot_reverse_until_they_are_resolved_in_any_valid_order(self):
        handed = self.put("10")
        r1, r2 = self.take(1, "3"), self.take(1, "2")
        self.refuse("DEPENDENT_HOLDING_RETURN_EXISTS", lambda: self.service.reverse(handed["entry_id"], "x"), CorrectionError)
        self.service.reverse(r1["entry_id"], "x")                                                  # the OLDER return first: arithmetic allows it
        self.assertEqual((self.balance(1), self.logo(), self.conserved()), (D("8.000"), D("12.000"), D("20.000")))
        self.refuse("DEPENDENT_HOLDING_RETURN_EXISTS", lambda: self.service.reverse(handed["entry_id"], "x"), CorrectionError)
        self.service.reverse(r2["entry_id"], "x")
        self.service.reverse(handed["entry_id"], "x")
        self.assertEqual((self.balance(1), self.logo()), (D("0.000"), D("20.000")))
        self.assertEqual(sum(m["weight_delta"] for m in self.db.logo_moves), D("0.000"))           # every compensation counted once, originals stay

    def test_the_other_return_order_works_too(self):
        handed = self.put("10")
        r1, r2 = self.take(1, "3"), self.take(1, "2")
        self.service.reverse(r2["entry_id"], "x")
        self.service.reverse(r1["entry_id"], "x")
        self.service.reverse(handed["entry_id"], "x")
        self.assertEqual((self.balance(1), self.logo()), (D("0.000"), D("20.000")))

    def test_returned_grams_that_were_reused_block_the_return_reversal_by_arithmetic_only(self):
        self.put("10")
        returned = self.take(1, "10")
        # the custody then returns 20 g to the shop: only 20 of the 30 g LOGO outstanding remain... use the whole outstanding to starve the return
        outstanding = self.logo()
        self.service.return_custody(self.logo_trip, "", [{"karat_id": K21, "weight": str(outstanding), "designation": "LOGO"}], TODAY, "", key(8202))
        self.refuse("DEPENDENT_CUSTODY_USE_EXISTS", lambda: self.service.reverse(returned["entry_id"], "x"), CorrectionError)
        self.assertEqual(self.balance(1), D("0.000"))                                              # nothing changed

    def test_a_return_reversal_is_allowed_while_custody_stays_valid(self):
        self.put("10")
        returned = self.take(1, "4")
        self.service.reverse(returned["entry_id"], "x")
        self.assertEqual((self.balance(1), self.logo(), self.conserved()), (D("10.000"), D("10.000"), D("20.000")))
        self.refuse("ALREADY_REVERSED", lambda: self.service.reverse(returned["entry_id"], "x"), CorrectionError)

    def test_heating_before_and_after_a_handover_keeps_both_balances_valid_for_loss_and_gain(self):
        heat = lambda before, after: self.service.heat(self.logo_trip, [{"karat_id": K21, "input_weight": before, "output_weight": after}], TODAY, "ورشة",
                                                       "", "كسب" if D(after) > D(before) else "", "", key(8300 + self.l))
        self.l += 1
        loss = heat("5", "4.5")                                                                     # 20 g -> 19.5 g
        handed = self.put("19.5")
        self.refuse("HEAT_EXCEEDS_CUSTODY", lambda: heat("1", "1"))                                # the handed-over grams are not heatable
        self.l += 1
        self.take(1, "19.5")
        self.l += 1
        gain = heat("19.5", "20")                                                                   # a gain after the return
        self.assertEqual(self.logo(), D("20.000"))
        self.put("20")
        with self.assertRaises(CorrectionError) as blocked:                                         # reversing the gain would make custody negative
            self.service.reverse(gain["entry_id"], "x")
        self.assertIn(blocked.exception.code, ("DEPENDENT_RETURN_EXISTS", "DEPENDENT_SETTLEMENT_EXISTS", "DEPENDENT_LOGO_RETURN_EXISTS", "DEPENDENT_HEAT_EXISTS"))

    def test_reversal_rights_the_reason_and_the_open_custody(self):
        done = self.put("5")
        trips, _ = self.as_user(OWNER - {"INTERMEDIARY_HOLDING_REVERSE"})
        before = self.state()
        with self.assertRaises(PermissionError):
            trips.reverse(done["entry_id"], "x")
        self.assertEqual(self.state(), before)
        self.refuse("REASON_REQUIRED", lambda: self.service.reverse(done["entry_id"], " "), CorrectionError)
        self.put("15")
        self.put("5", K18)
        self.service.return_custody(self.logo_trip, "", [{"karat_id": K21, "weight": "3", "designation": "MELT"}], TODAY, "", key(8401))
        self.service.close(self.logo_trip)
        self.refuse("TRIP_CLOSED", lambda: self.service.reverse(done["entry_id"], "x"), CorrectionError)

    def test_reversal_rolls_back_at_every_stage(self):
        done = self.put("5")
        before = self.state()
        for step in ("logo_movement", "audit"):
            self.db.fail_at = step
            with self.assertRaises(RuntimeError):
                self.service.reverse(done["entry_id"], "x")
            self.db.fail_at = None
            self.assertEqual(self.state(), before, step)


class ExistingBehaviourTests(LogoCase):
    def test_048_whole_lot_handover_and_the_logo_flow_do_not_interfere(self):
        self.put("10")
        done = self.hand()                                                                          # the 048 case's own custody and bar
        self.assertEqual(done["document_code"], "IH000001")
        self.assertEqual(self.places(), (D("0"), D("0"), D("10.000")))
        self.assertEqual(self.held(), D("10.000"))

    def test_the_issue_reversal_is_blocked_by_arithmetic_while_the_issued_grams_are_out(self):
        issue = self.db.entries[min(e for e, v in self.db.entries.items() if v["trip_id"] == int(self.logo_trip))]["id"]
        self.put("20")
        with self.assertRaises(CorrectionError):
            self.service.reverse(issue, "x")
        self.take(1, "20")
        self.assertEqual(self.logo(), D("20.000"))


if __name__ == "__main__":
    unittest.main()
