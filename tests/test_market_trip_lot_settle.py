"""047 real services over transactional fakes. No MySQL or concurrency claim."""
import copy
from decimal import Decimal as D
from unittest.mock import patch
from services.correction_service import CorrectionError
from services.market_trip_service import MarketTripService, TripError
from services.supplier_rules import SupplierRuleError, lot_settlement_credit
from tests.test_market_trip_gold_settle import GoldSettleCase, S18_INACTIVE, S24, INTERMEDIARY, NO_KARAT, lkey
from tests.test_market_trips import K21, TODAY


class LotSettleTests(GoldSettleCase):
    def make_lots(self, weight="10", fineness="875", residues=()):
        trip=self.issue("",[(K21,"20","MELT")])["trip_id"]
        self.n+=1
        melt=self.service.melt(trip,[{"karat_id":K21,"weight":"20"}],TODAY,"ورشة",
            {"weight":weight,"fineness":fineness},[{"weight":w,"fineness":f} for w,f in residues],"","","",lkey(self.n))
        lots=[l for l in self.db.lots.values() if l["entry_id"]==int(melt["entry_id"])]
        return trip,melt,sorted(lots,key=lambda l:l["lot_no"])

    def deliver(self, trip, lot, supplier=S18_INACTIVE, request=None, **kwargs):
        self.n+=1
        return self.service.settle_supplier_lot(trip,supplier,str(lot["id"]),TODAY,"مرجع","ملاحظة",request or lkey(self.n),**kwargs)

    def test_main_posts_only_custody_and_supplier_preserving_every_fact(self):
        trip,melt,lots=self.make_lots(); lot=lots[0]
        untouched=copy.deepcopy((self.db.scrap,self.db.cash_moves,self.db.shop_moves,self.db.sm_making,self.db.melts,self.db.lots))
        done=self.deliver(trip,lot)
        self.assertEqual((done["kind"],done["fineness"],done["credited_weight"],done["analytical"]),("LOT_SETTLE","875.0","11.667","11.666667"))
        self.assertEqual(self.custody(trip)["lots"],[])
        self.assertEqual(untouched,(self.db.scrap,self.db.cash_moves,self.db.shop_moves,self.db.sm_making,self.db.melts,self.db.lots))
        h=self.db.settlements[int(done["settlement_id"])]
        self.assertEqual((h["delivered_karat_id"],h["delivered_karat_value"],h["delivered_lot_id"]),(None,None,lot["id"]))
        self.assertEqual(self.db.lot_moves[-1]["weight_delta"],-lot["weight_grams"])
        self.assertEqual(self.db.sup_gold[-1]["weight_delta"],D("-11.667"))
        detail=self.service.trip_detail(trip)["entries"][0]["lot_settlement"]  # the detail is newest first
        self.assertEqual(detail["lot_id"],str(lot["id"]))
        self.assertEqual(detail["origin"],melt["code"])

    def test_known_residue_is_allowed_unknown_residue_is_not(self):
        trip,_,lots=self.make_lots(residues=[("1","500"),("2","")])
        known=self.deliver(trip,lots[1]); self.assertEqual(known["lot_kind"],"RESIDUE")
        self.refused("FINENESS_REQUIRED",lambda:self.deliver(trip,lots[2]),SupplierRuleError)

    def test_low_fineness_below_half_boundary_refuses_even_when_analysis_rounds_up(self):
        trip,_,lots=self.make_lots("4.999","0.1")
        exc=self.refused("CREDIT_ROUNDS_TO_ZERO",lambda:self.deliver(trip,lots[0],S24),SupplierRuleError)
        self.assertIn("تبقى السبكة في العهدة",str(exc));self.assertNotIn("زِد",str(exc))

    def test_exact_half_boundary_posts_from_raw_not_rounded_pure_grams(self):
        trip,_,lots=self.make_lots("5.000","0.1")
        self.db.lots[lots[0]["id"]]["pure_grams"]=D("99")  # prove this analytical snapshot is NOT used
        done=self.deliver(trip,lots[0],S24)
        self.assertEqual((done["credited_weight"],done["analytical"],done["rounding_difference"]),("0.001","0.000500","0.000500"))

    def test_one_gram_low_assay_zero_case(self):
        with self.assertRaisesRegex(SupplierRuleError,"تبقى السبكة"):
            lot_settlement_credit(D("1"),D("0.1"),24)

    def test_wrong_custody_consumed_and_partial_client_quantities_refuse(self):
        trip,_,lots=self.make_lots();lot=lots[0]
        self.refused("LOT_NOT_IN_CUSTODY",lambda:self.deliver(self.trip,lot))
        self.refused("LOT_PARTIAL_NOT_ALLOWED",lambda:self.deliver(trip,lot,weight="9.999"))
        self.deliver(trip,lot,weight="10")
        self.refused("LOT_NOT_IN_CUSTODY",lambda:self.deliver(trip,lot))
        self.refused("LOT_NOT_IN_CUSTODY",lambda:self.service.return_lots(trip,[{"lot_id":lot["id"]}],TODAY,"",lkey(801)))
        self.refused("LOT_NOT_IN_SHOP",lambda:self.service.issue_lots(self.trip,[{"lot_id":lot["id"]}],TODAY,"",lkey(802)))

    def test_partial_legacy_outstanding_cannot_be_settled(self):
        trip,melt,lots=self.make_lots();lot=lots[0]
        # Synthetic legacy partial return, never a newly allowed business flow.
        self.db.lot_moves.append({"id":99,"lot_id":lot["id"],"entry_id":int(melt["entry_id"]),"movement_type":"SHOP_RETURN","weight_delta":D("-1")})
        self.refused("LOT_PARTIAL_NOT_ALLOWED",lambda:self.deliver(trip,lot))

    def test_reissued_lot_uses_current_custody_and_keeps_origin(self):
        trip,melt,lots=self.make_lots();lot=lots[0]
        self.service.return_lots(trip,[{"lot_id":lot["id"]}],TODAY,"",lkey(810))
        self.service.close(trip)
        issued=self.service.issue_lots(self.trip,[{"lot_id":lot["id"]}],TODAY,"",lkey(811))
        done=self.deliver(self.trip,lot)
        self.assertEqual(done["origin"],melt["code"])
        self.assertEqual(self.db.trips[int(trip)]["status"],"CLOSED")
        self.refused("DEPENDENT_LOT_RETURN_EXISTS",lambda:self.service.reverse(issued["entry_id"],"تصحيح"),CorrectionError)
        self.service.reverse(done["entry_id"],"تصحيح")
        self.assertEqual(self.custody(self.trip)["lots"][0]["lot_id"],str(lot["id"]))

    def test_exact_replay_after_reversal_returns_original_then_new_key_can_deliver_again(self):
        trip,_,lots=self.make_lots();lot=lots[0]
        done=self.deliver(trip,lot,request=lkey(820)); self.service.reverse(done["entry_id"],"تصحيح")
        before=self.state();again=self.deliver(trip,lot,request=lkey(820))
        self.assertEqual((again["entry_id"],again["settlement_id"],again["duplicate"]),(done["entry_id"],done["settlement_id"],True))
        self.assertEqual(self.state(),before)
        next_done=self.deliver(trip,lot);self.assertNotEqual(next_done["settlement_id"],done["settlement_id"])

    def test_melt_dependency_and_custody_only_reverse(self):
        trip,melt,lots=self.make_lots();done=self.deliver(trip,lots[0])
        self.refused("DEPENDENT_LOT_USE_EXISTS",lambda:self.service.reverse(melt["entry_id"],"تصحيح"),CorrectionError)
        exc=self.refused("CUSTODY_SETTLEMENT_REVERSE_FROM_TRIP",lambda:self.direct.reverse(done["settlement_id"],"تصحيح"),CorrectionError)
        self.assertIn(done["code"],str(exc))
        stored=copy.deepcopy(self.db.sup_gold[-1]);self.service.reverse(done["entry_id"],"تصحيح")
        self.assertEqual((self.db.sup_gold[-1]["weight_delta"],self.db.sup_gold[-1]["eq21"]),(-stored["weight_delta"],-stored["eq21"]))
        self.refused("ALREADY_REVERSED",lambda:self.service.reverse(done["entry_id"],"تصحيح"),CorrectionError)
        self.service.reverse(melt["entry_id"],"تصحيح")

    def test_posting_rollback_at_every_mutation(self):
        trip,_,lots=self.make_lots();before=self.state()
        for stage in ("sg_header","entry","lot_movement","sg_gold","sg_leg","lot_link","sg_audit","audit"):
            with self.subTest(stage=stage):
                self.db.fail_at=stage
                with self.assertRaises(RuntimeError):self.deliver(trip,lots[0])
                self.db.fail_at=None;self.assertEqual(self.state(),before)

    def test_reversal_rollback_at_every_mutation(self):
        trip,_,lots=self.make_lots();done=self.deliver(trip,lots[0]);before=self.state()
        for stage in ("correction","sg_gold","audit"):
            with self.subTest(stage=stage):
                self.db.fail_at=stage
                with self.assertRaises(RuntimeError):self.service.reverse(done["entry_id"],"تصحيح")
                self.db.fail_at=None;self.assertEqual(self.state(),before)

    def test_permissions_rechecked_and_reason_required(self):
        trip,_,lots=self.make_lots()
        service=self.as_user({"APP_LOGIN","MARKET_TRIP_MANAGE"})
        self.denied(lambda:service.settle_supplier_lot(trip,S18_INACTIVE,str(lots[0]["id"]),TODAY,"","",lkey(850)))
        done=self.deliver(trip,lots[0])
        self.refused("REASON_REQUIRED",lambda:self.service.reverse(done["entry_id"],""),CorrectionError)
        self.authorization.permissions=frozenset({"APP_LOGIN","MARKET_TRIP_MANAGE","TRANSACTION_CORRECT"})
        self.denied(lambda:self.service.reverse(done["entry_id"],"تصحيح"))

    def test_invalid_supplier_roles_and_missing_karat(self):
        trip,_,lots=self.make_lots()
        self.refused("SUPPLIER_ROLE_REQUIRED",lambda:self.deliver(trip,lots[0],INTERMEDIARY),SupplierRuleError)
        self.refused("SUPPLIER_KARAT_MISSING",lambda:self.deliver(trip,lots[0],NO_KARAT))

    def test_request_payload_conflict_and_cross_flow_conflicts(self):
        trip,_,lots=self.make_lots();self.deliver(trip,lots[0],request=lkey(860))
        self.refused("REQUEST_CONFLICT",lambda:self.deliver(trip,lots[0],S24,request=lkey(860)))
        self.refused("REQUEST_CONFLICT",lambda:self.settle(request=lkey(860)))
        self.refused("REQUEST_CONFLICT",lambda:self.direct.settle(S18_INACTIVE,K21,"1",TODAY,"","",lkey(860)),SupplierRuleError)
        direct=self.direct.settle(S18_INACTIVE,K21,"1",TODAY,"","",lkey(861))
        self.refused("REQUEST_CONFLICT",lambda:self.deliver(trip,lots[0],request=lkey(861)))

    def test_close_reopen_reverse_restores_same_lot(self):
        trip,_,lots=self.make_lots();done=self.deliver(trip,lots[0]);self.service.close(trip)
        self.refused("TRIP_CLOSED",lambda:self.service.reverse(done["entry_id"],"تصحيح"),CorrectionError)
        self.service.reopen(trip,"إعادة فتح");self.service.reverse(done["entry_id"],"تصحيح")
        restored=self.custody(trip)["lots"][0]
        self.assertEqual((restored["lot_id"],restored["outstanding"],restored["fineness"]),(str(lots[0]["id"]),D("10"),D("875")))
        self.refused("OUTSTANDING_CUSTODY",lambda:self.service.close(trip))

    def test_melt_reverse_locks_its_original_lots_before_dependency_checks(self):
        trip,melt,lots=self.make_lots();from tests.market_trip_fakes import TripRepo
        calls=[];original=TripRepo.lock_lots
        with patch.object(TripRepo,"lock_lots",lambda repo,ids:(calls.append(sorted(ids)) or original(repo,ids))):
            self.service.reverse(melt["entry_id"],"تصحيح")
        self.assertIn([lots[0]["id"]],calls)
