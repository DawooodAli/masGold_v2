"""Sale of custody gold (053, «بيع ذهب العهدة»): real MarketTripService and CorrectionService over the in-memory transactional database of
tests/market_trip_fakes.py (a failed transaction restores every table; the 053 CHECK / foreign-key constraints are mirrored as asserts). NOT
real-MySQL tests: the SQL and the migration are covered by the static schema test; concurrency is not exercised."""
import copy
from decimal import Decimal as D
import unittest

from services import custody_rules
from services.correction_service import CorrectionError
from services.market_trip_service import MarketTripService, TripError
from tests.market_trip_fakes import OWNER, OWNER_NO_CORRECT
from tests.test_market_trip_lot_settle import LotSettleTests
from tests.test_market_trips import K18, K21, K24, TODAY, TripCase, key

ACTIVE_SUPPLIER, INACTIVE_SUPPLIER, INTERMEDIARY = "10", "11", "12"
REFERENCE_PRICES = {21: D("6000.0000"), 18: D("5142.8571"), 24: D("7000.0000")}


def skey(n):
    return "55555555-5555-4555-8555-%012d" % n


class SaleCase(TripCase):
    """OPEN custody of 20 g 21K LOGO + 5 g 18K LOGO + 3 g 21K MELT + 10,000 EGP; the price reference is 6,000 EGP per 21K gram."""

    def setUp(self):
        super().setUp()
        self.service._price_reference = lambda: dict(REFERENCE_PRICES)
        self.trip = self.issue("10000", [(K21, "20.000", "LOGO"), (K18, "5.000", "LOGO"), (K21, "3.000", "MELT")])["trip_id"]

    # ------------------------------------------------------------ helpers
    def sell(self, grams="10", total="60000", mode="TOTAL", karat=K21, designation="LOGO", party="", name="تاجر الصاغة", trip=None,
             request=None, service=None, ack=False, reference="", notes="", when=TODAY, kind="CUSTODY_GOLD", lot=None):
        self.n += 1
        return (service or self.service).sell_gold(self.trip if trip is None else trip, kind, karat, designation, lot, grams, mode, total, party,
                                                   name, when, reference, notes, request or skey(self.n), ack)

    def untouched(self):
        """Every ledger a sale must never write."""
        db = self.db
        return copy.deepcopy((db.scrap, db.cash_moves, db.sup_gold, db.sm_making, db.settlements, db.settlement_legs, db.supplier_links,
                              db.gold_settle_links, db.shop_moves, db.holding_moves, db.logo_moves, db.sm_payments))

    def gold(self, trip=None, karat=21, designation="LOGO"):
        rows = {(g["karat_value"], g["designation"]): g for g in self.service.trip_detail(trip or self.trip)["custody"]["gold"]}
        return rows[(karat, designation)]["outstanding"] if (karat, designation) in rows else D("0.000")

    def cash(self, trip=None):
        return self.service.trip_detail(trip or self.trip)["custody"]["cash"]


class AggregateSaleTests(SaleCase):
    def test_a_sale_consumes_custody_gold_and_adds_the_proceeds_to_custody_cash_and_nothing_else(self):
        before = self.untouched()
        done = self.sell(grams="12.500", total="75000")
        self.assertEqual((done["kind"], done["code"], done["created"], done["duplicate"]), ("GOLD_SALE", "MT000001-2", False, False))
        self.assertEqual((done["weight"], done["total_price"], done["price_basis"], done["buyer_kind"], done["buyer_name"]),
                         ("12.500", "75000.00", "TOTAL", "THIRD_PARTY", "تاجر الصاغة"))
        self.assertEqual(self.gold(), D("7.500"))                                           # 20 - 12.5 (the MELT line is untouched)
        self.assertEqual(self.gold(designation="MELT"), D("3.000"))
        cash = self.cash()
        self.assertEqual((cash["issued"], cash["received"], cash["outstanding"]), (D("10000.00"), D("75000.00"), D("85000.00")))
        self.assertEqual(self.untouched(), before)                                          # no Scrap / cash / bank / supplier / making / lot ledger
        self.assertEqual(self.db.cash(), D("40000.00"))                                     # the Main Safe did not move: the cash stays in custody
        self.assertEqual(custody_rules.cash_outstanding(10000, 0, 0, 75000), D("85000"))

    def test_price_per_gram_is_rounded_once_to_the_piaster_and_stored_with_its_basis(self):
        done = self.sell(grams="7.333", total="6123.4567", mode="PER_GRAM")
        expected = (D("7.333") * D("6123.4567")).quantize(D("0.01"), rounding="ROUND_HALF_UP")
        self.assertEqual((done["total_price"], done["price_basis"], done["price_per_gram"]), (str(expected), "PER_GRAM", "6123.4567"))
        sale = self.db.gold_sales[int(done["entry_id"])]
        self.assertEqual((sale["total_price"], sale["price_per_gram"]), (expected, D("6123.4567")))

    def test_every_designation_is_sellable_and_the_line_must_exist_with_enough_grams(self):
        self.sell(grams="3.000", total="18000", designation="MELT")
        self.assertEqual(self.gold(designation="MELT"), D("0.000"))
        self.refused("SALE_EXCEEDS_CUSTODY", lambda: self.sell(grams="20.001", total="1"))
        self.refused("SALE_EXCEEDS_CUSTODY", lambda: self.sell(grams="1", total="1", karat=K24))
        self.refused("SALE_EXCEEDS_CUSTODY", lambda: self.sell(grams="0.001", total="1", designation="MELT"))      # the MELT line is already sold out

    def test_invalid_input_is_refused_with_no_trace(self):
        for code, call in (("WEIGHT_REQUIRED", lambda: self.sell(grams="0")), ("WEIGHT_INVALID", lambda: self.sell(grams="1.2345")),
                           ("PRICE_INVALID", lambda: self.sell(total="0")), ("PRICE_INVALID", lambda: self.sell(total="-5")),
                           ("PRICE_INVALID", lambda: self.sell(total="10.005")), ("PRICE_INVALID", lambda: self.sell(total="", mode="PER_GRAM")),
                           ("PRICE_INVALID", lambda: self.sell(total="1.00001", mode="PER_GRAM")),
                           ("PRICE_BASIS_REQUIRED", lambda: self.sell(mode="")), ("KARAT_REQUIRED", lambda: self.sell(karat="")),
                           ("DESIGNATION_REQUIRED", lambda: self.sell(designation="")), ("SOURCE_REQUIRED", lambda: self.sell(kind="OTHER")),
                           ("TRIP_NOT_FOUND", lambda: self.sell(trip="99")), ("DATE_REQUIRED", lambda: self.sell(when="")),
                           ("REQUEST_INVALID", lambda: self.sell(request="not-a-key"))):
            with self.subTest(code=code):
                self.refused(code, call)

    def test_a_closed_trip_and_a_missing_right_are_refused_with_no_trace(self):
        service, _ = self.owner_like(OWNER_NO_CORRECT - {"MARKET_TRIP_MANAGE"})
        before = self.state()
        with self.assertRaises(PermissionError):
            self.sell(grams="1", total="6000", service=service)
        self.assertEqual(self.state(), before)
        self.sell(grams="20", total="120000")
        self.sell(grams="5", total="25714", karat=K18)
        self.sell(grams="3", total="18000", designation="MELT")
        self.ret(self.trip, cash="173714")
        self.service.close(self.trip)
        self.refused("TRIP_CLOSED", lambda: self.sell(grams="1", total="6000"))


class BuyerTests(SaleCase):
    def test_a_registered_party_of_any_role_may_buy_and_keeps_a_name_snapshot(self):
        supplier = self.sell(grams="1", total="6000", party=ACTIVE_SUPPLIER, name="تجاهل هذا الاسم")
        intermediary = self.sell(grams="1", total="6000", party=INTERMEDIARY)
        self.assertEqual((supplier["buyer_kind"], supplier["buyer_party_id"], supplier["buyer_name"]), ("REGISTERED_PARTY", "10", "مورد نشط"))
        self.assertEqual((intermediary["buyer_kind"], intermediary["buyer_party_id"], intermediary["buyer_name"]), ("REGISTERED_PARTY", "12", "وسيط فقط"))
        self.db.suppliers[10]["name"] = "اسم جديد"                                          # a later rename never rewrites the sale
        self.assertEqual(self.db.gold_sales[int(supplier["entry_id"])]["buyer_name"], "مورد نشط")

    def test_an_inactive_party_is_refused_and_an_unknown_one_too(self):
        self.refused("BUYER_INACTIVE", lambda: self.sell(party=INACTIVE_SUPPLIER))
        self.refused("BUYER_NOT_FOUND", lambda: self.sell(party="999"))
        self.refused("BUYER_NOT_FOUND", lambda: self.sell(party="abc"))

    def test_a_third_party_needs_a_name(self):
        self.refused("BUYER_REQUIRED", lambda: self.sell(party="", name="   "))
        self.refused("TEXT_LONG", lambda: self.sell(name="ا" * 151))
        done = self.sell(party="", name="  مشتري عابر  ")
        self.assertEqual((done["buyer_kind"], done["buyer_party_id"], done["buyer_name"]), ("THIRD_PARTY", None, "مشتري عابر"))

    def test_a_sale_to_a_supplier_never_touches_its_gold_or_making_payables(self):
        before = self.untouched()
        self.sell(grams="5", total="30000", party=ACTIVE_SUPPLIER)
        self.assertEqual(self.untouched(), before)
        self.assertEqual((self.db.sup_gold, self.db.sm_making, self.db.settlements), ([], [], {}))
        buyers = {b["id"]: b for b in self.service.context()["buyers"]}
        self.assertEqual(set(buyers), {"10", "11", "12"})                                    # every party can be offered; the service refuses an inactive one


class PriceWarningTests(SaleCase):
    def test_a_price_far_from_the_reference_needs_the_owner_to_acknowledge_it(self):
        error = self.refused("PRICE_DEVIATION", lambda: self.sell(grams="10", total="40000"))     # reference 60,000 -> -33.33 %
        self.assertIn("33.33", str(error))
        self.assertEqual(len(self.db.gold_sales), 0)
        done = self.sell(grams="10", total="40000", ack=True)
        sale = self.db.gold_sales[int(done["entry_id"])]
        self.assertEqual((sale["price_warning_acknowledged"], sale["deviation_percent"], sale["reference_price_per_gram"]),
                         (1, D("-33.33"), D("6000.0000")))
        self.assertEqual(self.cash()["received"], D("40000.00"))

    def test_a_price_inside_the_threshold_needs_no_acknowledgement_but_keeps_the_evidence(self):
        done = self.sell(grams="10", total="64000")                                          # +6.67 %
        sale = self.db.gold_sales[int(done["entry_id"])]
        self.assertEqual((sale["price_warning_acknowledged"], sale["deviation_percent"]), (0, D("6.67")))

    def test_the_threshold_is_exclusive_ten_percent_passes_above_it_warns(self):
        self.sell(grams="10", total="66000")                                                 # exactly +10 %: no warning
        self.refused("PRICE_DEVIATION", lambda: self.sell(grams="10", total="66100"))

    def test_without_a_reference_price_there_is_no_warning_and_no_evidence(self):
        self.service._price_reference = lambda: {}
        done = self.sell(grams="10", total="1")
        sale = self.db.gold_sales[int(done["entry_id"])]
        self.assertEqual((sale["reference_price_per_gram"], sale["deviation_percent"], sale["price_warning_acknowledged"]), (None, None, 0))
        self.service._price_reference = lambda: (_ for _ in ()).throw(RuntimeError("price engine down"))
        self.sell(grams="1", total="1")                                                      # a failing price engine never blocks a sale

    def test_the_read_only_check_reports_the_total_the_reference_and_the_warning(self):
        check = self.service.sale_price_check(self.trip, "CUSTODY_GOLD", K21, "LOGO", None, "10", "TOTAL", "40000")
        self.assertEqual((check["total"], check["reference_total"], check["deviation_percent"], check["warning"]),
                         ("40000.00", "60000.00", "-33.33", True))
        self.assertIn("33.33", check["message"])
        quiet = self.service.sale_price_check(self.trip, "CUSTODY_GOLD", K21, "LOGO", None, "10", "PER_GRAM", "6000")
        self.assertEqual((quiet["total"], quiet["warning"], quiet["message"]), ("60000.00", False, ""))
        self.assertEqual(len(self.db.gold_sales), 0)                                         # read only


class IdempotencyTests(SaleCase):
    def test_an_exact_replay_returns_the_original_and_a_different_payload_conflicts(self):
        first = self.sell(grams="2", total="12000", request=skey(900))
        again = self.sell(grams="2", total="12000", request=skey(900))
        self.assertEqual((again["duplicate"], again["entry_id"], again["code"]), (True, first["entry_id"], first["code"]))
        self.assertEqual(len(self.db.gold_sales), 1)
        self.refused("REQUEST_CONFLICT", lambda: self.sell(grams="3", total="12000", request=skey(900)))
        self.refused("REQUEST_CONFLICT", lambda: self.sell(grams="2", total="12000", name="غيره", request=skey(900)))

    def test_the_replay_is_decided_before_availability(self):
        self.sell(grams="20", total="120000", request=skey(901))                             # sells the whole line
        again = self.sell(grams="20", total="120000", request=skey(901))
        self.assertTrue(again["duplicate"])

    def test_a_key_used_by_another_flow_is_a_conflict(self):
        self.n += 1
        used = self.issue("1", request=skey(902))
        self.assertTrue(used["code"])
        self.refused("REQUEST_CONFLICT", lambda: self.sell(request=skey(902)))

    def test_the_acknowledgement_is_not_part_of_the_request_identity(self):
        self.sell(grams="10", total="40000", ack=True, request=skey(903))
        again = self.sell(grams="10", total="40000", ack=False, request=skey(903))
        self.assertTrue(again["duplicate"])


class AtomicityTests(SaleCase):
    def test_a_failure_at_any_step_leaves_no_trace(self):
        for step in ("entry", "gold_sale", "audit"):
            with self.subTest(step=step):
                before = self.state()
                self.db.fail_at = step
                with self.assertRaises(Exception):
                    self.sell(grams="1", total="6000")
                self.db.fail_at = None
                self.assertEqual(self.state(), before)


class CustodyCashTests(SaleCase):
    def test_the_proceeds_can_be_returned_to_the_main_safe_or_paid_to_a_supplier_and_the_trip_closes_only_at_zero(self):
        self.sell(grams="20", total="120000", designation="LOGO")
        self.sell(grams="5", total="25714", karat=K18)
        self.sell(grams="3", total="18000", designation="MELT")
        custody = self.service.trip_detail(self.trip)["custody"]
        self.assertEqual((custody["cash"]["outstanding"], custody["settled"]), (D("173714.00"), False))
        self.ret(self.trip, cash="100000")                                                    # back to the Main Safe
        self.assertEqual(self.db.cash(), D("40000.00") + D("100000.00"))
        self.service.pay_supplier(self.trip, ACTIVE_SUPPLIER, "73714", TODAY, "", "", key(4000))   # paid from custody (existing feature)
        self.assertEqual(self.service.trip_detail(self.trip)["custody"]["cash"]["outstanding"], D("0.00"))
        self.assertTrue(self.service.trip_detail(self.trip)["custody"]["settled"])
        self.service.close(self.trip)

    def test_returning_more_than_the_custody_cash_is_still_refused(self):
        self.sell(grams="1", total="6000")
        self.refused("RETURN_EXCEEDS_CUSTODY", lambda: self.ret(self.trip, cash="16000.01"))


class ReversalTests(SaleCase):
    def reverse(self, done, reason="خطأ في الصفقة", service=None):
        return (service or self.service).reverse(done["entry_id"], reason)

    def test_a_reversal_restores_the_gold_and_removes_the_proceeds_without_any_other_movement(self):
        done = self.sell(grams="12.500", total="75000")
        before = self.untouched()
        self.reverse(done)
        self.assertEqual(self.gold(), D("20.000"))
        cash = self.cash()
        self.assertEqual((cash["received"], cash["outstanding"]), (D("0.00"), D("10000.00")))
        self.assertEqual(self.untouched(), before)
        self.assertEqual(self.db.gold_sales[int(done["entry_id"])]["total_price"], D("75000.00"))       # the document is never rewritten
        entry = [e for e in self.service.trip_detail(self.trip)["entries"] if e["id"] == done["entry_id"]][0]
        self.assertTrue(entry["reversed"])
        self.assertEqual(entry["gold_sale"]["total_price"], "75000.00")
        self.refused("ALREADY_REVERSED", lambda: self.reverse(done), CorrectionError)

    def test_a_reversal_needs_the_correction_right_and_a_reason(self):
        done = self.sell(grams="1", total="6000")
        service, _ = self.owner_like(OWNER_NO_CORRECT)
        with self.assertRaises(Exception):
            self.reverse(done, service=service)
        with self.assertRaises(Exception):
            self.reverse(done, reason="")
        self.assertEqual(self.gold(), D("19.000"))

    def test_a_reversal_is_refused_after_the_proceeds_were_used(self):
        done = self.sell(grams="20", total="120000")
        self.ret(self.trip, cash="115000")                                                    # the proceeds went back to the Main Safe
        error = self.refused("DEPENDENT_CASH_USE_EXISTS", lambda: self.reverse(done), CorrectionError)
        self.assertIn("اعكس", str(error))
        self.assertEqual(self.gold(), D("0.000"))

    def test_the_issue_cannot_be_reversed_while_a_sale_depends_on_its_gold(self):
        first = self.service.trip_detail(self.trip)["entries"][0]
        self.sell(grams="20", total="1", designation="LOGO", ack=True)
        self.refused("DEPENDENT_RETURN_EXISTS", lambda: self.service.reverse(first["id"], "تصحيح"), CorrectionError)

    def test_a_closed_trip_blocks_the_reversal(self):
        done = self.sell(grams="20", total="120000")
        self.sell(grams="5", total="25714", karat=K18)
        self.sell(grams="3", total="18000", designation="MELT")
        self.ret(self.trip, cash="173714")
        self.service.close(self.trip)
        self.refused("TRIP_CLOSED", lambda: self.reverse(done), CorrectionError)


class LotSaleTests(LotSettleTests):
    """Whole known-assay lots (the melt fixtures of the 047 tests)."""

    def setUp(self):
        super().setUp()
        self.service._price_reference = lambda: dict(REFERENCE_PRICES)

    def sell_lot(self, trip, lot, total="60000", mode="TOTAL", grams=None, party="", name="تاجر", request=None, ack=False):
        self.n += 1
        return self.service.sell_gold(trip, "CUSTODY_LOT", None, None, str(lot["id"]), "" if grams is None else grams, mode, total, party, name,
                                      TODAY, "", "", request or skey(5000 + self.n), ack)

    def test_a_whole_lot_leaves_the_custody_through_a_sale_out_movement_and_proceeds_enter_the_cash(self):
        trip, melt, lots = self.make_lots()
        lot = lots[0]
        before = copy.deepcopy((self.db.scrap, self.db.cash_moves, self.db.sup_gold, self.db.sm_making, self.db.settlements, self.db.shop_moves))
        done = self.sell_lot(trip, lot, total="55000", ack=True)                                # 10 g x 0.875 x 7,000 = 61,250 reference
        self.assertEqual((done["source_kind"], done["weight"], done["total_price"], done["lot_id"]), ("CUSTODY_LOT", "10.000", "55000.00", str(lot["id"])))
        self.assertEqual(self.custody(trip)["lots"], [])
        self.assertEqual(self.db.lot_moves[-1]["movement_type"], "SALE_OUT")
        self.assertEqual(self.db.lot_moves[-1]["weight_delta"], -lot["weight_grams"])
        self.assertEqual(self.custody(trip)["cash"]["received"], D("55000.00"))
        self.assertEqual(before, copy.deepcopy((self.db.scrap, self.db.cash_moves, self.db.sup_gold, self.db.sm_making, self.db.settlements, self.db.shop_moves)))
        sale = self.db.gold_sales[int(done["entry_id"])]
        self.assertEqual((sale["fineness_permille"], sale["karat_id"], sale["designation"]), (lot["fineness_permille"], None, None))

    def test_a_lot_is_sold_whole_only_and_needs_a_known_assay_and_must_be_in_custody(self):
        trip, melt, lots = self.make_lots(residues=[("2", "")])
        main, residue = lots[0], lots[1]
        self.refused("LOT_PARTIAL_NOT_ALLOWED", lambda: self.sell_lot(trip, main, grams="5"))
        self.refused("LOT_ASSAY_UNKNOWN", lambda: self.sell_lot(trip, residue))
        self.refused("LOT_NOT_FOUND", lambda: self.sell_lot(trip, {"id": 9999}))
        self.sell_lot(trip, main, ack=True)
        self.refused("LOT_NOT_IN_CUSTODY", lambda: self.sell_lot(trip, main, ack=True))

    def test_the_lot_warning_uses_pure_grams_at_the_24k_reference(self):
        trip, melt, lots = self.make_lots()
        self.refused("PRICE_DEVIATION", lambda: self.sell_lot(trip, lots[0], total="30000"))
        check = self.service.sale_price_check(trip, "CUSTODY_LOT", None, None, str(lots[0]["id"]), "", "TOTAL", "61250")
        self.assertEqual((check["reference_total"], check["warning"]), ("61250.00", False))

    def test_reversing_a_lot_sale_puts_the_same_lot_back_and_a_later_use_blocks_it(self):
        trip, melt, lots = self.make_lots()
        done = self.sell_lot(trip, lots[0], total="61250")
        self.assertEqual(len(self.custody(trip)["lots"]), 0)
        self.service.reverse(done["entry_id"], "خطأ")
        self.assertEqual([l["lot_id"] for l in self.custody(trip)["lots"]], [str(lots[0]["id"])])
        self.assertEqual(self.custody(trip)["cash"]["received"], D("0.00"))
        again = self.sell_lot(trip, lots[0], total="61250")                                       # a fresh key sells the restored lot
        self.assertNotEqual(again["entry_id"], done["entry_id"])

    def test_a_sold_lot_blocks_the_reversal_of_its_melt_until_the_sale_is_reversed(self):
        trip, melt, lots = self.make_lots()
        done = self.sell_lot(trip, lots[0], total="61250")
        with self.assertRaises(CorrectionError):
            self.service.reverse(melt["entry_id"], "تصحيح")
        self.service.reverse(done["entry_id"], "تصحيح")
        self.service.reverse(melt["entry_id"], "تصحيح")


del LotSettleTests, TripCase                      # the imported bases must not run again in this module

if __name__ == "__main__":
    unittest.main()
