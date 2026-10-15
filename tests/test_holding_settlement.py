"""Supplier gold settlement from shop-owned gold held by a market intermediary (050). Real HoldingSettlementService, IntermediaryHoldingService, MarketTripService
and CorrectionService over the in-memory transactional database of tests/market_trip_fakes.py (a failed transaction restores every table; the 050 CHECK / unique
/ composite keys are mirrored as asserts). NOT real-MySQL tests: the SQL, the constraints and the lock behaviour were exercised on masgold_rehearsal
(tests/test_holding_settlement_mysql.py, opt-in)."""
import copy
from decimal import Decimal as D
from unittest.mock import patch
import unittest

from mysql.connector import IntegrityError

from services.correction_service import CorrectionError, CorrectionService
from services.holding_settlement_service import HoldingSettlementService
from services.intermediary_holding_rules import (DIVIDED, ENTITLEMENT, WHOLE, declaration_options, holding_preview)
from services.market_trip_service import TripError
from services.supplier_rules import SupplierRuleError
from tests.market_trip_fakes import (Corrections, Customers, HoldingRepo, Invoices, OWNER, SettlementRepo, TripRepo, session_for)
from tests.auth_fakes import FakeAuthorization
from tests.test_intermediary_holdings import OWNER_PARTY, hkey
from tests.test_logo_holdings import LogoCase
from tests.test_market_trips import K18, K21, K24, TODAY, key

DUAL, INACTIVE_PARTY = "21", "22"
S21, S18_INACTIVE, S24, NO_KARAT, INTERMEDIARY_ONLY = "10", "11", "13", "14", "12"
SG = "SUPPLIER_GOLD_SETTLEMENT"


def skey(n):
    return "99999999-9999-4999-8999-%012d" % n


class SettleCase(LogoCase):
    """LogoCase: the 048 melt trip (MAIN 10 g at 875, RESIDUE 1 g at 500, RESIDUE 2 g unknown) and the LOGO custody (20 g 21K, 5 g 18K)."""

    def setUp(self):
        super().setUp()
        self.db.suppliers[13] = {"id": 13, "name": "مورد ٢٤", "is_active": 1, "is_supplier": True, "is_intermediary": False, "karat_id": 3}
        self.db.suppliers[14] = {"id": 14, "name": "مورد بلا عيار", "is_active": 1, "is_supplier": True, "is_intermediary": False, "karat_id": None}
        self.settle_service = HoldingSettlementService(self.session, transaction_factory=self.db.transaction, repository=TripRepo, holdings=HoldingRepo,
                                                       parties=SettlementRepo, authorization=self.authorization, corrections=self.corrections)
        self.baseline = self.untouched()                    # SG / sup_gold excluded: they are the 050 effects under test
        self.q = 0

    # ------------------------------------------------------------ helpers
    def untouched(self):
        return copy.deepcopy((self.db.cash_moves, self.db.scrap, self.db.sm_making, self.db.shop_moves, self.db.lot_moves, self.db.entries))

    def sett(self, kind, pool, supplier, grams, declaration=None, confirmed=False, request=None, service=None, when=TODAY, reference="", notes=""):
        self.q += 1
        return (service or self.settle_service).settle(kind, str(pool), supplier, grams, declaration, confirmed, when, reference, notes,
                                                       request or skey(self.q))

    def lot_pool(self, lot=None, party=OWNER_PARTY):
        return int(self.hand(lot, party)["pool_id"])

    def logo_pool(self, grams="10", party=OWNER_PARTY, karat=K21):
        return int(self.put(grams, karat, party)["pool_id"])

    def lot_balance(self, pool):
        return HoldingRepo(self.db).pool_balance(pool)

    def reverse(self, settlement, reason="تصحيح", service=None):
        return (service or self.settle_service).reverse(settlement, reason)

    def sg(self, result):
        return self.db.settlements[int(result["id"])]

    def writes(self, thunk):
        """The rights checked as WRITE by the authorization while `thunk` runs."""
        self.authorization.checked.clear()
        thunk()
        return {code for kind, code in self.authorization.checked if kind == "WRITE"}


# ====================================================================================================================== conversion and effects
class LogoSettlementTests(SettleCase):
    def test_exact_effects_conversion_and_a_single_gold_leg(self):
        pool = self.logo_pool("10")
        custody, before = self.logo(), self.untouched()
        done = self.sett("LOGO", pool, S18_INACTIVE, "1")                                     # an INACTIVE recipient is allowed
        self.assertEqual((done["code"], done["source_kind"], done["delivered_weight"], done["credited_weight"], done["duplicate"]),
                         ("SG000001", "HOLDING_LOGO", "1.000", "1.167", False))
        self.assertEqual((done["analytical"], done["rounding_difference"], done["remaining"]), ("1.166667", "0.000333", "9.000"))
        self.assertEqual((self.logo(), self.balance(pool)), (custody, D("9.000")))             # custody untouched; the pool paid the grams
        self.assertEqual(self.untouched(), before)                                              # no custody, Scrap, cash, making, shop-lot or inventory movement
        header = self.sg(done)
        self.assertEqual((header["delivered_karat_value"], header["target_karat_value"], header["holding_party_id"], header["logo_pool_id"],
                          header["lot_pool_id"], header["physical_declaration"]), (21, 18, 20, pool, None, None))
        legs = [l for l in self.db.settlement_legs if l["settlement_id"] == header["id"]]
        self.assertEqual([(l["leg_kind"], l["source_kind"], l["old_gold_movement_id"] is None) for l in legs], [("GOLD", "HOLDING_LOGO", True)])
        movement = self.db.sup_gold[-1]
        self.assertEqual((movement["supplier_id"], movement["karat_id"], movement["weight_delta"], movement["source_type"]), (11, 2, D("-1.167"), SG))
        self.assertLess(movement["eq21"], 0)
        settlement_move = [m for m in self.db.logo_moves if m.get("settlement_id")]
        self.assertEqual([(m["movement_type"], m["weight_delta"], m["document_id"]) for m in settlement_move], [("SETTLEMENT", D("-1.000"), None)])
        self.assertEqual([r["event_type"] for r in self.db.audit_rows][-2:], ["SUPPLIER_GOLD_SETTLEMENT", "INTERMEDIARY_HOLDING_SETTLEMENT"])

    def test_the_supplier_payable_may_cross_zero_into_supplier_credit(self):
        pool = self.logo_pool("10")
        self.sett("LOGO", pool, S21, "10")                                                      # the supplier owed nothing: its balance simply goes negative
        self.assertEqual(SettlementRepo(self.db).balance(10, 1), D("-10.000"))
        self.assertEqual(self.balance(pool), D("0.000"))

    def test_a_credit_is_posted_once_directly_from_the_raw_value(self):
        pool = self.logo_pool("10")
        done = self.sett("LOGO", pool, S24, "0.001")                                           # 0.001 x 21 / 24 = 0.000875 -> 0.001 (HALF_UP)
        self.assertEqual((done["credited_weight"], done["analytical"], done["rounding_difference"]), ("0.001", "0.000875", "0.000125"))


class LotSettlementTests(SettleCase):
    def test_original_lot_facts_stay_separate_from_the_delivered_grams(self):
        pool = self.lot_pool()
        lot_before, before = dict(self.db.lots[self.main]), self.untouched()
        done = self.sett("LOT", pool, S18_INACTIVE, "4", ENTITLEMENT)
        self.assertEqual((done["source_kind"], done["delivered_weight"], done["credited_weight"], done["analytical"], done["rounding_difference"],
                          done["declaration"], done["remaining"]),
                         ("HOLDING_LOT", "4.000", "4.667", "4.666667", "0.000333", ENTITLEMENT, "6.000"))
        header = self.sg(done)
        self.assertEqual((header["delivered_lot_weight_grams"], header["delivered_weight_grams"], header["delivered_fineness_permille"], header["delivered_lot_id"],
                          header["origin_melt_entry_id"], header["lot_pool_id"], header["holding_party_id"], header["delivered_karat_id"]),
                         (D("10.000"), D("4.000"), D("875.0"), self.main, self.db.lots[self.main]["entry_id"], pool, 20, None))
        self.assertEqual(self.db.lots[self.main], lot_before)                                   # identity, origin, weight and assay never change
        self.assertEqual(self.untouched(), before)
        self.assertEqual(self.db.holding_pools[pool]["weight_grams"], D("10.000"))             # the pool's original quantity is unchanged
        self.assertEqual([l["leg_kind"] for l in self.db.settlement_legs], ["GOLD"])

    def test_two_suppliers_consume_the_same_source_sequentially(self):
        pool = self.lot_pool()
        first = self.sett("LOT", pool, S21, "4", ENTITLEMENT)
        second = self.sett("LOT", pool, S18_INACTIVE, "6", ENTITLEMENT)
        self.assertEqual((first["credited_weight"], second["credited_weight"]), ("4.000", "7.000"))
        self.assertEqual((self.lot_balance(pool), SettlementRepo(self.db).balance(10, 1), SettlementRepo(self.db).balance(11, 2)),
                         (D("0.000"), D("-4.000"), D("-7.000")))
        self.refuse("HOLDING_NOT_AVAILABLE", lambda: self.sett("LOT", pool, S21, "0.001", ENTITLEMENT))
        self.assertEqual([self.sg(r)["delivered_lot_weight_grams"] for r in (first, second)], [D("10.000"), D("10.000")])

    def test_known_assay_residues_settle_too(self):
        pool = self.lot_pool(self.residue)
        done = self.sett("LOT", pool, S24, "0.001", ENTITLEMENT)                                # 0.001 x 500/1000 x 24/24 = 0.0005 -> 0.001 (HALF_UP)
        self.assertEqual((done["credited_weight"], done["analytical"], done["rounding_difference"]), ("0.001", "0.000500", "0.000500"))

    def test_double_rounding_is_never_used_and_a_zero_credit_refuses_without_a_trace(self):
        self.db.lots[self.residue]["fineness_permille"] = D("499.6")
        pool = self.lot_pool(self.residue)
        before = self.state()
        # raw 0.001 x 499.6/1000 x 24/24 = 0.0004996: directly 0.000 (refused). Through the six-decimal analytical value (0.000500) it would be 0.001.
        self.refuse("CREDIT_ROUNDS_TO_ZERO", lambda: self.sett("LOT", pool, S24, "0.001", ENTITLEMENT), SupplierRuleError)
        self.assertEqual(self.state(), before)

    def test_an_unknown_assay_is_refused_before_anything_is_consumed(self):
        pool = self.lot_pool()
        self.db.holding_pools[pool]["fineness_permille"] = None                                 # unreachable through 048; the service still never infers one
        self.refuse("FINENESS_REQUIRED", lambda: self.sett("LOT", pool, S21, "1", ENTITLEMENT), SupplierRuleError)


class RefusalTests(SettleCase):
    def test_quantity_precision_and_excess_recipient_and_source_are_refused_atomically(self):
        lot, logo = self.lot_pool(), self.logo_pool("2")
        for grams, code in (("1.0001", "WEIGHT_INVALID"), ("0", "WEIGHT_INVALID"), ("-1", "WEIGHT_INVALID"), ("", "WEIGHT_INVALID"), ("abc", "WEIGHT_INVALID")):
            self.refuse(code, lambda g=grams: self.sett("LOGO", logo, S21, g), SupplierRuleError)
        self.refuse("HOLDING_EXCEEDED", lambda: self.sett("LOGO", logo, S21, "2.001"))
        self.refuse("HOLDING_EXCEEDED", lambda: self.sett("LOT", lot, S21, "10.001", ENTITLEMENT))
        self.refuse("SUPPLIER_NOT_FOUND", lambda: self.sett("LOGO", logo, "999", "1"))
        self.refuse("HOLDING_NOT_FOUND", lambda: self.sett("LOGO", "999", S21, "1"))
        self.refuse("HOLDING_NOT_FOUND", lambda: self.sett("BAR", logo, S21, "1"))
        self.refuse("SUPPLIER_ROLE_REQUIRED", lambda: self.sett("LOGO", logo, INTERMEDIARY_ONLY, "1"), SupplierRuleError)
        self.refuse("SUPPLIER_KARAT_MISSING", lambda: self.sett("LOGO", logo, NO_KARAT, "1"), SupplierRuleError)
        self.refuse("REQUEST_INVALID", lambda: self.settle_service.settle("LOGO", str(logo), S21, "1", None, False, TODAY, "", "", "not-a-key"))
        self.assertEqual(self.db.settlements, {})

    def test_failure_at_every_persistent_stage_leaves_no_trace(self):
        lot, logo = self.lot_pool(), self.logo_pool("2")
        for kind, pool, grams, declaration in (("LOT", lot, "4", ENTITLEMENT), ("LOGO", logo, "1", None)):
            for step in ("sg_header", "sg_gold", "sg_leg", "settlement_movement", "sg_audit", "holding_audit"):
                self.db.fail_at = step
                before = self.state()
                with self.assertRaises(RuntimeError):
                    self.sett(kind, pool, S21, grams, declaration)
                self.db.fail_at = None
                self.assertEqual(self.state(), before, f"{kind} {step}")
        self.assertEqual(self.db.settlements, {})
        self.sett("LOT", lot, S21, "4", ENTITLEMENT)                                             # and the same request still works afterwards

    def test_a_session_that_ended_during_the_post_rolls_everything_back(self):
        logo = self.logo_pool("2")
        before = self.state()
        with patch.object(self.session, "is_current", return_value=False), self.assertRaises(PermissionError):
            self.sett("LOGO", logo, S21, "1")
        self.assertEqual(self.state(), before)


# ====================================================================================================================== physical declarations
class DeclarationTests(SettleCase):
    def test_the_rules_offer_whole_only_for_the_full_original_quantity_without_allocation_history(self):
        self.assertEqual(declaration_options("10", "10", "10", []), (WHOLE, ENTITLEMENT, DIVIDED))
        self.assertEqual(declaration_options("10", "10", "10", [WHOLE]), (WHOLE, ENTITLEMENT, DIVIDED))       # an earlier WHOLE delivery is no allocation
        self.assertEqual(declaration_options("10", "10", "10", [ENTITLEMENT]), (ENTITLEMENT, DIVIDED))
        self.assertEqual(declaration_options("10", "10", "10", [DIVIDED]), (ENTITLEMENT, DIVIDED))
        self.assertEqual(declaration_options("10", "10", "4", []), (ENTITLEMENT, DIVIDED))
        self.assertEqual(declaration_options("10", "6", "6", []), (ENTITLEMENT, DIVIDED))                      # the full REMAINING balance is not the original bar

    def test_the_declaration_is_mandatory_validated_by_the_service_and_never_applies_to_logo(self):
        lot, logo = self.lot_pool(), self.logo_pool("2")
        self.refuse("DECLARATION_REQUIRED", lambda: self.sett("LOT", lot, S21, "10"))
        self.refuse("DECLARATION_INVALID", lambda: self.sett("LOT", lot, S21, "10", "CUT"))
        self.refuse("WHOLE_NOT_ELIGIBLE", lambda: self.sett("LOT", lot, S21, "9.999", WHOLE))
        self.refuse("DECLARATION_NOT_APPLICABLE", lambda: self.sett("LOGO", logo, S21, "1", WHOLE))
        self.refuse("DECLARATION_NOT_APPLICABLE", lambda: self.sett("LOGO", logo, S21, "1", ENTITLEMENT))

    def test_cancelled_gram_entitlement_restores_return_eligibility_but_not_handover_reversal(self):
        pool = self.lot_pool()
        done = self.sett("LOT", pool, S21, "10", ENTITLEMENT)                                  # the full quantity, but only an entitlement
        self.assertEqual(self.lot_balance(pool), D("0.000"))
        self.reverse(done["id"])
        self.assertEqual(self.lot_balance(pool), D("10.000"))                                   # the entitlement is back ...
        self.refuse("WHOLE_NOT_ELIGIBLE", lambda: self.sett("LOT", pool, S21, "10", WHOLE))
        returned = self.back(pool, physical_confirmed=True)                                    # a client checkbox cannot declare a non-physical event
        self.assertEqual(returned["kind"], "HOLD_RETURN")
        audit = next(row for row in self.db.audit_rows if row["event_type"] == "MARKET_TRIP_HOLD_RETURN")
        review = audit["metadata"]["physical_event_review"]
        self.assertFalse(review["physical_events_recorded_in_error_and_did_not_occur"])
        self.assertEqual(review["physical_event_ids"], [])

    def test_whole_delivery_reversal_confirmed_normal_return_and_a_repeat_delivery(self):
        pool = self.lot_pool(self.residue)
        whole = self.sett("LOT", pool, S21, "1", WHOLE)
        self.assertEqual((whole["declaration"], self.lot_balance(pool)), (WHOLE, D("0.000")))
        self.reverse(whole["id"])
        self.assertEqual(self.lot_balance(pool), D("1.000"))
        self.refuse("INTACT_CONFIRMATION_REQUIRED", lambda: self.back(pool, confirmed=False))   # the existing OWNER confirmation is still required
        again = self.sett("LOT", pool, S18_INACTIVE, "1", WHOLE)                                 # a repeat whole delivery after the reversal is allowed
        self.reverse(again["id"])
        self.back(pool)                                                                          # a confirmed normal return is NOT blocked by whole-delivery history
        self.assertEqual((self.lot_balance(pool), self.places(self.residue)[0]), (D("0.000"), D("1.000")))

    def test_whole_delivery_history_blocks_the_handover_reversal_and_names_the_sg(self):
        pool = self.lot_pool(self.residue)
        whole = self.sett("LOT", pool, S21, "1", WHOLE)
        entry = min(self.db.holding_links)
        error = self.refuse("DEPENDENT_HOLDING_SETTLEMENT_EXISTS", lambda: self.corrections.reverse("MARKET_TRIP_ENTRY", str(entry), "x"), CorrectionError)
        self.assertIn("SG000001", str(error))                                                    # while the delivery is effective ...
        self.reverse(whole["id"])
        error = self.refuse("DEPENDENT_HOLDING_SETTLEMENT_EXISTS", lambda: self.corrections.reverse("MARKET_TRIP_ENTRY", str(entry), "x"), CorrectionError)
        self.assertIn("SG000001", str(error))                                                    # ... and after it was reversed: no bypass of the physical verification
        self.assertEqual(self.lot_balance(pool), D("1.000"))
        self.back(pool)                                                                          # the way back to the custody is the confirmed normal return

    def test_the_handover_reversal_refusal_advises_by_the_kind_of_history(self):
        whole_pool, allocated_pool = self.lot_pool(self.residue), self.lot_pool()
        whole = self.sett("LOT", whole_pool, S21, "1", WHOLE)
        self.reverse(whole["id"])
        allocation = self.sett("LOT", allocated_pool, S21, "4", ENTITLEMENT)
        self.reverse(allocation["id"])
        entries = {l["pool_id"]: l["entry_id"] for l in self.db.holding_links.values()}
        advice_whole = self.refuse("DEPENDENT_HOLDING_SETTLEMENT_EXISTS", lambda: self.corrections.reverse("MARKET_TRIP_ENTRY", str(entries[whole_pool]), "x"),
                                   CorrectionError)
        advice_allocated = self.refuse("DEPENDENT_HOLDING_SETTLEMENT_EXISTS", lambda: self.corrections.reverse("MARKET_TRIP_ENTRY", str(entries[allocated_pool]), "x"),
                                       CorrectionError)
        self.assertIn("SG000001", str(advice_whole))
        self.assertIn("رجوعها العادي بتأكيد المالك الصريح", str(advice_whole))                       # whole-delivery history only: the confirmed return is advised
        self.assertNotIn("محجوبًا", str(advice_whole))
        self.assertIn("SG000002", str(advice_allocated))
        self.assertIn("الرجوع العادي", str(advice_allocated))                                    # separate from reversing the handover
        self.assertIn("تأكيد المالك", str(advice_allocated))
        self.assertEqual(self.lot_balance(whole_pool), D("1.000"))                                  # the guards themselves are unchanged
        self.back(whole_pool)
        self.back(allocated_pool)

    def test_physical_division_requires_error_declaration_after_all_allocations_reverse(self):
        pool = self.lot_pool()
        first = self.sett("LOT", pool, S21, "4", ENTITLEMENT)
        # the full REMAINING balance is not an intact original bar: WHOLE is refused, an entitlement or a confirmed division is accepted
        self.refuse("WHOLE_NOT_ELIGIBLE", lambda: self.sett("LOT", pool, S21, "6", WHOLE))
        second = self.sett("LOT", pool, S18_INACTIVE, "6", DIVIDED)
        self.assertEqual((self.lot_balance(pool), second["declaration"]), (D("0.000"), DIVIDED))
        self.reverse(second["id"])
        self.reverse(first["id"])
        self.assertEqual(self.lot_balance(pool), D("10.000"))
        self.refuse("PHYSICAL_EVENT_DECLARATION_REQUIRED", lambda: self.back(pool))              # reversal alone does not prove the physical division was erroneous
        self.assertEqual(self.back(pool, physical_confirmed=True)["kind"], "HOLD_RETURN")
        audit = next(row for row in self.db.audit_rows if row["event_type"] == "MARKET_TRIP_HOLD_RETURN")
        review = audit["metadata"]["physical_event_review"]
        self.assertTrue(review["physical_events_recorded_in_error_and_did_not_occur"])
        self.assertTrue(review["physical_event_ids"])
        entry = min(self.db.holding_links)
        error = self.refuse("DEPENDENT_HOLDING_SETTLEMENT_EXISTS", lambda: self.corrections.reverse("MARKET_TRIP_ENTRY", str(entry), "x"), CorrectionError)
        self.assertTrue(all(code in str(error) for code in ("SG000001", "SG000002")))
        self.assertEqual(len([m for m in self.db.holding_moves if m.get("settlement_id")]), 4)  # two originals + two compensations, nothing deleted

    def test_the_panel_reflects_current_settlement_eligibility_and_notes_reversed_whole_delivery(self):
        allocated, whole = self.lot_pool(), self.lot_pool(self.residue)
        active = self.sett("LOT", allocated, S21, "4", ENTITLEMENT)
        self.reverse(self.sett("LOT", whole, S21, "1", WHOLE)["id"])
        panel = {p["pool_id"]: p for p in self.holding.party_holdings(OWNER_PARTY)["pools"]}
        self.assertEqual((panel[str(allocated)]["returnable"], panel[str(allocated)]["allocated"]), (False, True))
        self.assertIn(f"SG{int(active['id']):06d}", panel[str(allocated)]["history_note"])
        self.assertEqual((panel[str(whole)]["returnable"], panel[str(whole)]["allocated"]), (True, False))
        self.assertTrue(panel[str(whole)]["history_note"])
        self.reverse(active["id"])
        holdings = self.holding.party_holdings(OWNER_PARTY)
        panel = {p["pool_id"]: p for p in holdings["pools"]}
        self.assertEqual((panel[str(allocated)]["returnable"], panel[str(allocated)]["allocated"]), (True, True))
        self.assertEqual(panel[str(allocated)]["reason"], "")
        self.assertIn(f"SG{int(active['id']):06d}", panel[str(allocated)]["history_note"])
        self.assertTrue(any(s["code"] == f"SG{int(active['id']):06d}" and s["reversed"] for s in holdings["settlements"]))


# ====================================================================================================================== reversal
class ReversalTests(SettleCase):
    def test_one_canonical_correction_restores_the_stored_credit_and_grams_with_explicit_compensation(self):
        pool = self.lot_pool()
        done = self.sett("LOT", pool, S18_INACTIVE, "4", ENTITLEMENT)
        credit = self.db.sup_gold[-1]
        result = self.reverse(done["id"])
        self.assertEqual(result["effects"]["holding_settlements"], [{"pool_id": pool, "settlement_id": int(done["id"]), "logo": False, "delta": "4.000"}])
        self.assertEqual((self.lot_balance(pool), SettlementRepo(self.db).balance(11, 2)), (D("10.000"), D("0.000")))
        restore = self.db.sup_gold[-1]
        self.assertEqual((restore["weight_delta"], restore["eq21"], restore["source_type"], restore["source_id"]),
                         (-credit["weight_delta"], -credit["eq21"], "TRANSACTION_CORRECTION", int(result["correction_id"])))
        moves = [m for m in self.db.holding_moves if m.get("settlement_id")]
        self.assertEqual([(m["movement_type"], m["weight_delta"]) for m in moves], [("SETTLEMENT", D("-4.000")), ("SETTLEMENT_REVERSAL", D("4.000"))])
        self.assertEqual(moves[1]["correction_id"], int(result["correction_id"]))                    # both histories share the one correction record
        self.assertEqual(moves[0]["correction_id"], None)                                        # the original is never rewritten
        self.assertEqual(self.untouched()[0:3], self.baseline[0:3])
        self.refuse("ALREADY_REVERSED", lambda: self.reverse(done["id"]), CorrectionError)
        detail = SettlementRepo(self.db).settlement(done["id"])
        self.assertEqual(int(detail["reversal_id"]), int(result["correction_id"]))

    def test_the_stored_credit_is_restored_even_when_the_inputs_would_no_longer_compute_it(self):
        pool = self.lot_pool()
        done = self.sett("LOT", pool, S18_INACTIVE, "4", ENTITLEMENT)
        self.db.karats[2]["karat_value"] = 20                                                    # nothing recomputed: the STORED movement is negated
        self.db.suppliers[11]["karat_id"] = 2
        self.reverse(done["id"])
        self.assertEqual(SettlementRepo(self.db).balance(11, 2), D("0.000"))

    def test_logo_settlement_return_and_reversal_arithmetic_in_every_valid_order(self):
        pool = self.logo_pool("10")
        s1 = self.sett("LOGO", pool, S21, "4")
        self.take(pool, "3")
        s2 = self.sett("LOGO", pool, S21, "3")
        self.assertEqual((self.balance(pool), self.logo()), (D("0.000"), D("13.000")))
        entry = min(self.db.logo_links)
        self.refuse("DEPENDENT_HOLDING_RETURN_EXISTS", lambda: self.corrections.reverse("MARKET_TRIP_ENTRY", str(entry), "x"), CorrectionError)
        self.reverse(s2["id"])                                                                   # 3 g are back at the intermediary
        self.assertEqual(self.balance(pool), D("3.000"))
        self.reverse(s1["id"])
        self.assertEqual(self.balance(pool), D("7.000"))
        returned = max(l["entry_id"] for l in self.db.logo_links.values() if l["entry_kind"] == "HOLD_BACK")
        self.corrections.reverse("MARKET_TRIP_ENTRY", str(returned), "x")
        self.assertEqual((self.balance(pool), self.logo()), (D("10.000"), D("10.000")))
        self.corrections.reverse("MARKET_TRIP_ENTRY", str(entry), "x")                          # the handover reverses only once everything else is undone
        self.assertEqual((self.balance(pool), self.logo()), (D("0.000"), D("20.000")))

    def test_an_effective_settlement_blocks_the_logo_handover_reversal_and_names_the_sg(self):
        pool = self.logo_pool("10")
        done = self.sett("LOGO", pool, S21, "4")
        entry = min(self.db.logo_links)
        error = self.refuse("DEPENDENT_HOLDING_RETURN_EXISTS", lambda: self.corrections.reverse("MARKET_TRIP_ENTRY", str(entry), "x"), CorrectionError)
        self.assertIn("SG000001", str(error))
        self.reverse(done["id"])
        self.corrections.reverse("MARKET_TRIP_ENTRY", str(entry), "x")
        self.assertEqual(self.balance(pool), D("0.000"))

    def test_the_resulting_pool_balance_is_checked_explicitly(self):
        pool = self.logo_pool("10")
        done = self.sett("LOGO", pool, S21, "4")
        self.db.logo_moves.append({"id": 900, "pool_id": pool, "document_id": None, "doc_kind": None, "settlement_id": None, "movement_type": "RETURN",
                                   "weight_grams": D("5.000"), "weight_delta": D("5.000"), "correction_id": None})        # a stray +5: balance 11 > the initial 10
        self.refuse("HOLDING_BALANCE_INVALID", lambda: self.reverse(done["id"]), CorrectionError)

    def test_a_reversal_needs_a_reason_and_neither_locks_nor_reopens_the_origin_trip(self):
        pool = self.lot_pool()
        done = self.sett("LOT", pool, S21, "4", ENTITLEMENT)
        self.refuse("REASON_REQUIRED", lambda: self.reverse(done["id"], ""), CorrectionError)
        self.db.trips[int(self.trip)]["status"] = "CLOSED"                                       # the originating custody may be CLOSED
        self.db.lock_log.clear()
        self.reverse(done["id"])
        self.assertNotIn(("trip",), self.db.lock_log)
        self.assertEqual(self.db.trips[int(self.trip)]["status"], "CLOSED")
        self.assertEqual(self.lot_balance(pool), D("10.000"))

    def test_the_supplier_page_and_the_holder_page_use_the_same_correction(self):
        from services.supplier_gold_settlement_service import SupplierGoldSettlementService
        pool = self.logo_pool("10")
        done = self.sett("LOGO", pool, S21, "4")
        supplier_side = SupplierGoldSettlementService(self.session, transaction_factory=self.db.transaction, repository=SettlementRepo, authorization=self.authorization,
                                                      corrections=self.corrections)
        result = supplier_side.reverse(done["id"], "from the supplier page")
        self.assertEqual(self.db.corrections[-1]["source_type"], SG)
        self.refuse("ALREADY_REVERSED", lambda: self.reverse(done["id"]), CorrectionError)       # the holder-page path finds the very same record
        self.assertEqual(len([c for c in self.db.corrections if c["correction_type"] == "FULL_REVERSAL"]), 1)
        self.assertEqual(int(result["correction_id"]), int(self.db.corrections[-1]["correction_id"]))

    def test_no_entry_reversal_path_exists_for_a_holding_settlement(self):
        done = self.sett("LOGO", self.logo_pool("10"), S21, "4")
        self.assertFalse(any(l["settlement_id"] == int(done["id"]) for l in list(self.db.gold_settle_links.values()) + list(self.db.lot_settle_links.values())))
        self.assertEqual({e["entry_kind"] for e in self.db.entries.values()} & {"GOLD_SETTLE", "LOT_SETTLE"}, set())


# ====================================================================================================================== rights, parties, replay, locks
class RightsTests(SettleCase):
    def test_posting_needs_both_rights_rechecked_inside_the_transaction(self):
        logo = self.logo_pool("10")
        base = self.state()
        for missing in ("INTERMEDIARY_HOLDING_MANAGE", "SUPPLIER_GOLD_SETTLE"):
            self.authorization.permissions = OWNER - {missing}                                    # the session still shows the right: only the database says no
            with self.assertRaises(PermissionError):
                self.sett("LOGO", logo, S21, "1")
            self.assertEqual(self.state(), base)
        self.authorization.permissions = OWNER
        self.assertEqual(self.sett("LOGO", logo, S21, "1")["code"], "SG000001")
        weak = session_for(frozenset({"APP_LOGIN", "SUPPLIER_GOLD_SETTLE"}))                      # a session without the holding right is refused up front
        service = HoldingSettlementService(weak, transaction_factory=self.db.transaction, repository=TripRepo, holdings=HoldingRepo, parties=SettlementRepo,
                                           authorization=FakeAuthorization(weak), corrections=self.corrections)
        with self.assertRaises(PermissionError):
            service.settle("LOGO", str(logo), S21, "1", None, False, TODAY, "", "", skey(900))
        self.assertFalse(service.can_settle())

    def test_every_reversal_right_is_required_and_the_existing_source_rights_are_not_weakened(self):
        lot, logo = self.lot_pool(), self.logo_pool("10")
        holding_lot = self.sett("LOT", lot, S21, "4", ENTITLEMENT)
        holding_logo = self.sett("LOGO", logo, S21, "4")
        base = self.state()
        for missing in ("TRANSACTION_CORRECT", "SUPPLIER_GOLD_SETTLE_REVERSE", "INTERMEDIARY_HOLDING_REVERSE"):
            self.authorization.permissions = OWNER - {missing}
            with self.assertRaises(PermissionError):
                self.reverse(holding_lot["id"])
            self.assertEqual(self.state(), base, missing)
        self.authorization.permissions = OWNER
        needs = {}
        needs["holding SG (lot)"] = self.writes(lambda: self.reverse(holding_lot["id"]))
        needs["holding SG (logo)"] = self.writes(lambda: self.reverse(holding_logo["id"]))
        # the rights every EXISTING correction source needs stay exactly what they were
        hand = self.hand(self.residue)
        needs["lot handover entry"] = self.writes(lambda: self.corrections.reverse("MARKET_TRIP_ENTRY", hand["entry_id"], "x"))
        put = self.put("1")
        needs["logo handover entry"] = self.writes(lambda: self.corrections.reverse("MARKET_TRIP_ENTRY", put["entry_id"], "x"))
        issue = self.issue("", [(K21, "1", "LOGO")], trip=self.logo_trip)
        needs["issue entry"] = self.writes(lambda: self.corrections.reverse("MARKET_TRIP_ENTRY", issue["entry_id"], "x"))
        expected = {"holding SG (lot)": {"TRANSACTION_CORRECT", "SUPPLIER_GOLD_SETTLE_REVERSE", "INTERMEDIARY_HOLDING_REVERSE"},
                    "holding SG (logo)": {"TRANSACTION_CORRECT", "SUPPLIER_GOLD_SETTLE_REVERSE", "INTERMEDIARY_HOLDING_REVERSE"},
                    "lot handover entry": {"TRANSACTION_CORRECT", "MARKET_TRIP_MANAGE", "INTERMEDIARY_HOLDING_REVERSE"},
                    "logo handover entry": {"TRANSACTION_CORRECT", "MARKET_TRIP_MANAGE", "INTERMEDIARY_HOLDING_REVERSE"},
                    "issue entry": {"TRANSACTION_CORRECT", "MARKET_TRIP_MANAGE"}}
        for name, rights in expected.items():
            self.assertEqual(needs[name], rights, name)
        from services import correction_service as module
        self.assertEqual(module.SOURCE_RIGHTS, {module.SMP: module.SMP_REVERSE_PERMISSION, module.MTE: module.MTE_PERMISSION, module.SGS: module.SGS_REVERSE_PERMISSION,
                                                module.SPI: module.SPI_REVERSE_PERMISSION})                    # 052: the supplier invoice reversal right is additive

    def test_a_direct_shop_scrap_settlement_still_needs_only_its_own_rights(self):
        from services.supplier_gold_settlement_service import SupplierGoldSettlementService
        direct = SupplierGoldSettlementService(self.session, transaction_factory=self.db.transaction, repository=SettlementRepo, scrap=__import__("tests.market_trip_fakes", fromlist=["ScrapRepo"]).ScrapRepo,
                                               authorization=self.authorization, corrections=self.corrections)
        done = direct.settle("10", "1", "5", TODAY, "", "", skey(700))
        rights = self.writes(lambda: direct.reverse(done["id"], "x"))
        self.assertEqual(rights, {"TRANSACTION_CORRECT", "SUPPLIER_GOLD_SETTLE_REVERSE"})

    def test_extra_permissions_are_all_checked_even_when_a_source_adds_several(self):
        from services.correction_service import _Plan
        plan = _Plan("X", "1", "ref", {}, None)
        plan.extra_permissions.extend(["A", "B"])
        self.assertEqual(plan.extra_permissions, ["A", "B"])
        self.assertFalse(hasattr(plan, "extra_permission"))                                      # the old single-right field is gone: nothing can set only one


class PartyTests(SettleCase):
    def test_an_inactive_holder_can_still_be_settled_but_gets_no_new_handover(self):
        pool = self.logo_pool("10")
        self.db.suppliers[20]["is_active"] = 0
        self.assertEqual(self.sett("LOGO", pool, S21, "4")["remaining"], "6.000")
        self.refuse("PARTY_INACTIVE", lambda: self.put("1"))
        self.assertEqual(self.balance(pool), D("6.000"))

    def test_self_settlement_needs_the_confirmation_in_the_service_and_keeps_both_accounts_separate(self):
        pool = self.logo_pool("10", party=DUAL)
        self.refuse("SELF_SETTLEMENT_CONFIRMATION_REQUIRED", lambda: self.sett("LOGO", pool, DUAL, "4"))
        done = self.sett("LOGO", pool, DUAL, "4", confirmed=True, request=skey(50))
        self.assertEqual((done["holder_id"], done["supplier_id"], self.sg(done)["self_settlement_confirmed"]), ("21", "21", 1))
        self.assertEqual((self.balance(pool), SettlementRepo(self.db).balance(21, 1)), (D("6.000"), D("-4.000")))     # holdings and payable move separately
        other = self.sett("LOGO", pool, S21, "1", confirmed=True)                                 # the flag is only recorded for a real self-settlement
        self.assertEqual(self.sg(other)["self_settlement_confirmed"], 0)
        self.assertEqual(self.settle_service.settle("LOGO", str(pool), DUAL, "4", None, True, TODAY, "", "", skey(50))["duplicate"], True)
        self.refuse("REQUEST_CONFLICT", lambda: self.settle_service.settle("LOGO", str(pool), DUAL, "4", None, False, TODAY, "", "", skey(50)))     # the flag is in the digest

    def test_the_dual_role_party_keeps_its_holdings_when_the_recipient_changes(self):
        pool = self.logo_pool("10", party=DUAL)
        self.sett("LOGO", pool, S21, "3")
        self.assertEqual(SettlementRepo(self.db).balance(21, 1), D("0.000"))                      # the holder's own supplier account is untouched
        self.assertEqual(self.balance(pool), D("7.000"))

    def test_history_keeps_the_intermediary_role_and_the_party_row(self):
        self.sett("LOGO", self.logo_pool("10"), S21, "4")
        self.assertGreater(HoldingRepo(self.db).history_count(20), 2)
        self.assertEqual(sum(1 for s in self.db.settlements.values() if s["holding_party_id"] == 20), 1)


class ReplayAndConflictTests(SettleCase):
    def test_exact_replay_returns_the_original_records_before_availability_even_after_reversal(self):
        pool = self.logo_pool("10")
        first = self.sett("LOGO", pool, S21, "10", request=skey(5))
        state = self.state()
        again = self.sett("LOGO", pool, S21, "10", request=skey(5))
        self.assertEqual((again["duplicate"], again["id"], again["credited_weight"]), (True, first["id"], first["credited_weight"]))
        self.assertEqual(self.state(), state)
        self.reverse(first["id"])
        after = self.sett("LOGO", pool, S21, "10", request=skey(5))                                # reversed: the original linked record, not a new posting
        self.assertEqual((after["duplicate"], after["id"], len(self.db.settlements)), (True, first["id"], 1))
        self.sett("LOGO", pool, S21, "10", request=skey(6))
        self.refuse("HOLDING_NOT_AVAILABLE", lambda: self.sett("LOGO", pool, S21, "1", request=skey(7)))

    def test_a_key_used_for_another_payload_actor_source_or_flow_is_a_conflict_in_both_directions(self):
        pool, lot = self.logo_pool("10"), self.lot_pool()
        self.sett("LOGO", pool, S21, "1", request=skey(5))
        self.refuse("REQUEST_CONFLICT", lambda: self.sett("LOGO", pool, S21, "2", request=skey(5)))                 # another quantity
        self.refuse("REQUEST_CONFLICT", lambda: self.sett("LOGO", pool, S18_INACTIVE, "1", request=skey(5)))         # another supplier
        self.refuse("REQUEST_CONFLICT", lambda: self.sett("LOT", lot, S21, "1", ENTITLEMENT, request=skey(5)))        # another source kind
        other = self.owner_like(OWNER, user_id=9)
        service = HoldingSettlementService(other[0]._session, transaction_factory=self.db.transaction, repository=TripRepo, holdings=HoldingRepo,
                                           parties=SettlementRepo, authorization=other[1], corrections=self.corrections)
        self.refuse("REQUEST_CONFLICT", lambda: self.sett("LOGO", pool, S21, "1", request=skey(5), service=service))  # another actor
        # an SG key reused by the holding flows ...
        self.refuse("REQUEST_CONFLICT", lambda: self.holding.hand_over_logo(self.logo_trip, OWNER_PARTY, K21, "1", TODAY, "", "", skey(5)))
        self.refuse("REQUEST_CONFLICT", lambda: self.holding.hand_over_lot(self.trip, OWNER_PARTY, self.residue, TODAY, "", "", skey(5)))
        self.refuse("REQUEST_CONFLICT", lambda: self.holding.return_logo(str(pool), "1", TODAY, "", "", skey(5)))
        self.refuse("REQUEST_CONFLICT", lambda: self.service.settle_supplier_gold(self.logo_trip, S21, K21, "LOGO", "1", TODAY, "", "", skey(5)))   # a custody settlement
        from services.supplier_gold_settlement_service import SupplierGoldSettlementService
        direct = SupplierGoldSettlementService(self.session, transaction_factory=self.db.transaction, repository=SettlementRepo, authorization=self.authorization,
                                               scrap=__import__("tests.market_trip_fakes", fromlist=["ScrapRepo"]).ScrapRepo)
        self.refuse("REQUEST_CONFLICT", lambda: direct.settle(S21, K21, "1", TODAY, "", "", skey(5)), SupplierRuleError)                          # a direct settlement
        # ... and a holding / custody key reused by the settlement
        self.hand(self.residue, request=hkey(40))
        self.refuse("REQUEST_CONFLICT", lambda: self.sett("LOGO", pool, S21, "1", request=hkey(40)))                                              # an IH key
        put = self.put("1", request=hkey(41))
        self.refuse("REQUEST_CONFLICT", lambda: self.sett("LOGO", pool, S21, "1", request=hkey(41)))                                              # an IG / entry key
        self.assertTrue(put["document_code"])

    def test_the_losing_side_of_a_concurrent_same_key_request_replays_without_writing(self):
        pool = self.logo_pool("10")
        done = self.sett("LOGO", pool, S21, "4", request=skey(5))
        error = IntegrityError(msg="Duplicate entry 'x' for key 'supplier_gold_settlements.uq_sgs_request'", errno=1062)
        state = self.state()
        with patch.object(HoldingSettlementService, "_post", side_effect=error):
            replay = self.sett("LOGO", pool, S21, "4", request=skey(5))
            self.assertEqual((replay["duplicate"], replay["id"]), (True, done["id"]))
            with self.assertRaises(IntegrityError):                                              # no record exists for another key: the real error is never hidden
                self.sett("LOGO", pool, S21, "4", request=skey(6))
        self.assertEqual(self.state(), state)
        with patch.object(HoldingSettlementService, "_post", side_effect=IntegrityError(msg="Duplicate entry for key 'x.uq_other'", errno=1062)):
            with self.assertRaises(IntegrityError):
                self.sett("LOGO", pool, S21, "4", request=skey(8))


class LockOrderTests(SettleCase):
    def test_settlement_locks_lot_then_parties_ascending_then_pool_and_never_the_trip(self):
        lot = self.lot_pool()
        self.db.lock_log.clear()
        self.sett("LOT", lot, S18_INACTIVE, "4", ENTITLEMENT)                                    # holder 20, recipient 11 -> ascending: 11, 20
        self.assertEqual(self.db.lock_log, [("lots", (self.main,)), ("party", 11), ("party", 20), ("pool", lot)])
        self.db.lock_log.clear()
        self.sett("LOT", lot, "21", "1", ENTITLEMENT)                                             # recipient 21 > holder 20 -> ascending: 20, 21
        self.assertEqual(self.db.lock_log, [("lots", (self.main,)), ("party", 20), ("party", 21), ("pool", lot)])
        logo = self.logo_pool("10")
        self.db.lock_log.clear()
        self.sett("LOGO", logo, DUAL, "1")
        self.assertEqual(self.db.lock_log, [("party", 20), ("party", 21), ("logo_pool", logo)])
        self.db.lock_log.clear()
        self.sett("LOGO", logo, S21, "1")
        self.assertEqual(self.db.lock_log, [("party", 10), ("party", 20), ("logo_pool", logo)])

    def test_a_self_settlement_locks_the_one_party_once(self):
        pool = self.logo_pool("10", party=DUAL)
        self.db.lock_log.clear()
        self.sett("LOGO", pool, DUAL, "1", confirmed=True)
        self.assertEqual(self.db.lock_log, [("party", 21), ("logo_pool", pool)])

    def test_the_reversal_follows_the_same_order(self):
        lot, logo = self.lot_pool(), self.logo_pool("10", party=DUAL)
        a = self.sett("LOT", lot, S18_INACTIVE, "4", ENTITLEMENT)
        b = self.sett("LOGO", logo, S21, "1")
        self.db.lock_log.clear()
        self.reverse(a["id"])
        self.assertEqual(self.db.lock_log[:4], [("lots", (self.main,)), ("party", 11), ("party", 20), ("pool", lot)])
        self.db.lock_log.clear()
        self.reverse(b["id"])
        self.assertEqual(self.db.lock_log[:3], [("party", 10), ("party", 21), ("logo_pool", logo)])


class ContextTests(SettleCase):
    def test_the_dialog_context_and_the_pure_preview_use_the_same_credit_functions(self):
        pool, logo_pool = self.lot_pool(), self.logo_pool("2")
        baseline = self.untouched()
        context = self.settle_service.context("LOT", str(pool))
        self.assertEqual((context["balance"], context["allocated"], context["facts"]["fineness"], context["holder"]["name"]), (D("10.000"), False, D("875.0"), "مسوقجي أول"))
        recipients = {r["id"]: r for r in context["recipients"]}
        self.assertEqual((recipients["11"]["active"], recipients["21"]["self"], "12" in recipients, "14" in recipients), (False, False, False, False))
        preview = holding_preview(context, "11", "4")
        self.assertEqual((preview["valid"], preview["credited"], preview["analytical"], preview["remaining"]), (True, D("4.667"), D("4.666667"), D("6.000")))
        self.assertEqual([o["code"] for o in preview["options"]], [ENTITLEMENT, DIVIDED])
        full = holding_preview(context, "10", "10")
        self.assertEqual([o["code"] for o in full["options"]], [WHOLE, ENTITLEMENT, DIVIDED])
        self.assertEqual(holding_preview(context, "10", "10.001")["short"], True)
        self.assertEqual(holding_preview(context, "10", "1.0001")["valid"], False)
        self.assertEqual(holding_preview(context, "", "1")["valid"], False)
        self.assertEqual(holding_preview(context, "10", "")["error"], "")
        self.assertEqual(holding_preview(context, "10", "1")["self"], False)
        logo = holding_preview(self.settle_service.context("LOGO", str(logo_pool)), "11", "1")
        self.assertEqual((logo["credited"], logo["options"]), (D("1.167"), []))
        self.assertEqual(self.untouched(), baseline)                                               # a read never writes

    def test_a_reader_without_the_rights_cannot_open_the_context(self):
        pool = self.lot_pool()
        weak = session_for(frozenset({"APP_LOGIN", "INTERMEDIARY_HOLDING_VIEW"}))
        service = HoldingSettlementService(weak, transaction_factory=self.db.transaction, repository=TripRepo, holdings=HoldingRepo, parties=SettlementRepo,
                                           authorization=FakeAuthorization(weak))
        with self.assertRaises(PermissionError):
            service.context("LOT", str(pool))


class PanelTests(SettleCase):
    def test_the_holder_panel_lists_settlements_with_their_state_and_the_supplier_ledger_names_the_holder(self):
        pool = self.lot_pool()
        done = self.sett("LOT", pool, S21, "4", ENTITLEMENT)
        self.reverse(self.sett("LOT", pool, S18_INACTIVE, "6", DIVIDED)["id"])
        panel = self.holding.party_holdings(OWNER_PARTY)
        rows = {r["code"]: r for r in panel["settlements"]}
        self.assertEqual((rows["SG000001"]["reversed"], rows["SG000002"]["reversed"], rows["SG000002"]["declaration"]), (False, True, DIVIDED))
        self.assertEqual((rows["SG000001"]["delivered"], rows["SG000001"]["lot_weight"], rows["SG000001"]["pool_kind"]), (D("4.000"), D("10.000"), "LOT"))
        detail = SettlementRepo(self.db).settlement(done["id"])
        document = __import__("services.supplier_gold_settlement_service", fromlist=["x"]).SupplierGoldSettlementService.document(detail)
        self.assertEqual((document["holding_party_name"], document["physical_declaration"], document["lot_weight"], document["custody_reference"]),
                         ("مسوقجي أول", ENTITLEMENT, D("10.000"), None))


if __name__ == "__main__":
    unittest.main()
