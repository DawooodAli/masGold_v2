"""Cash gold purchase (054, «شراء ذهب نقدي»): real MarketTripService and CorrectionService over the in-memory transactional database of
tests/market_trip_fakes.py (a failed transaction restores every table; the 054 CHECK / foreign-key constraints are mirrored as asserts). NOT
real-MySQL tests: the SQL and the migration are covered by the static schema test and the opt-in rehearsal test."""
import copy
from decimal import Decimal as D
import unittest

from services.correction_service import CorrectionError
from services.market_trip_service import MarketTripService, TripError
from tests.market_trip_fakes import OWNER_NO_CORRECT
from tests.test_market_trips import K18, K21, K24, TODAY, TripCase

ACTIVE_SUPPLIER, INACTIVE_SUPPLIER, INTERMEDIARY = "10", "11", "12"
REFERENCE_PRICES = {21: D("6000.0000"), 18: D("5142.8571"), 24: D("7000.0000")}


def bkey(n):
    return "66666666-6666-4666-8666-%012d" % n


def line(karat=K21, weight="5", price="30000", designation="LOGO"):
    return {"karat_id": karat, "designation": designation, "weight": weight, "price": price}


class BuyCase(TripCase):
    """OPEN custody of 100,000 EGP (and no gold); the price reference is 6,000 EGP per 21K gram."""

    def setUp(self):
        super().setUp()
        self.service._price_reference = lambda: dict(REFERENCE_PRICES)
        self.trip = self.issue("100000")["trip_id"]

    def buy(self, lines=None, party="", name="تاجر السوق", trip=None, request=None, service=None, ack=False, reference="", notes="", when=TODAY):
        self.n += 1
        return (service or self.service).buy_gold(self.trip if trip is None else trip, [line()] if lines is None else lines, party, name, when,
                                                  reference, notes, request or bkey(self.n), ack)

    def untouched(self):
        db = self.db
        return copy.deepcopy((db.scrap, db.cash_moves, db.sup_gold, db.sm_making, db.settlements, db.settlement_legs, db.supplier_links,
                              db.gold_settle_links, db.shop_moves, db.holding_moves, db.logo_moves, db.sm_payments, db.gold_sales, db.lot_moves))

    def gold(self, karat=21, designation="LOGO", trip=None):
        rows = {(g["karat_value"], g["designation"]): g for g in self.service.trip_detail(trip or self.trip)["custody"]["gold"]}
        return rows[(karat, designation)]["outstanding"] if (karat, designation) in rows else D("0.000")

    def cash(self, trip=None):
        return self.service.trip_detail(trip or self.trip)["custody"]["cash"]


class PurchaseTests(BuyCase):
    def test_a_purchase_spends_custody_cash_and_adds_the_gold_and_writes_nothing_else(self):
        before = self.untouched()
        done = self.buy([line(K21, "10.250", "61500.50"), line(K18, "4.000", "20000", "MELT")])
        self.assertEqual((done["kind"], done["code"], done["created"], done["duplicate"]), ("GOLD_BUY", "MT000001-2", False, False))
        self.assertEqual((done["vendor_kind"], done["vendor_name"], done["total_price"]), ("THIRD_PARTY", "تاجر السوق", "81500.50"))
        self.assertEqual((self.gold(21, "LOGO"), self.gold(18, "MELT")), (D("10.250"), D("4.000")))
        cash = self.cash()
        self.assertEqual((cash["issued"], cash["spent"], cash["outstanding"]), (D("100000.00"), D("81500.50"), D("18499.50")))
        self.assertEqual(self.untouched(), before)                                          # no Scrap / cash / bank / supplier / making / lot ledger
        self.assertEqual(self.db.cash(), D("50000.00") - D("100000.00"))                    # the Main Safe moved only at the ISSUE
        head = self.db.gold_buys[int(done["entry_id"])]
        self.assertEqual((head["total_price"], head["vendor_kind"], head["vendor_party_id"]), (D("81500.50"), "THIRD_PARTY", None))
        self.assertEqual(sum((l["line_price"] for l in self.db.gold_buy_lines), D("0")), head["total_price"])
        self.assertEqual([(l["line_no"], l["designation"]) for l in self.db.gold_buy_lines], [(1, "LOGO"), (2, "MELT")])

    def test_the_purchased_gold_is_ordinary_custody_gold_it_returns_to_the_shop_scrap(self):
        self.buy([line(K21, "10", "60000")])
        scrap = self.db.physical(1)
        self.ret(self.trip, rows=[(K21, "4", "LOGO")])
        self.assertEqual((self.gold(), self.db.physical(1)), (D("6.000"), scrap + D("4.000")))
        self.refused("RETURN_EXCEEDS_CUSTODY", lambda: self.ret(self.trip, rows=[(K21, "6.001", "LOGO")]))

    def test_a_purchase_cannot_exceed_the_custody_cash_and_may_spend_exactly_all_of_it(self):
        self.refused("PURCHASE_EXCEEDS_CASH", lambda: self.buy([line(price="100000.01")], ack=True))
        self.buy([line(price="100000")], ack=True)
        self.assertEqual(self.cash()["outstanding"], D("0.00"))
        self.refused("PURCHASE_EXCEEDS_CASH", lambda: self.buy([line(price="0.01")], ack=True))

    def test_the_custody_is_not_settled_while_purchased_gold_remains(self):
        self.buy([line(price="30000")])
        self.assertFalse(self.service.trip_detail(self.trip)["custody"]["settled"])

    def test_invalid_input_is_refused_with_no_trace(self):
        for code, call in (("LINES_REQUIRED", lambda: self.buy([])), ("LINES_REQUIRED", lambda: self.buy([line("", "", "")])),
                           ("KARAT_REQUIRED", lambda: self.buy([line(karat="")])), ("KARAT_REQUIRED", lambda: self.buy([line(karat="x")])),
                           ("WEIGHT_REQUIRED", lambda: self.buy([line(weight="0")])), ("WEIGHT_INVALID", lambda: self.buy([line(weight="1.2345")])),
                           ("PRICE_INVALID", lambda: self.buy([line(price="")])), ("PRICE_INVALID", lambda: self.buy([line(price="0")])),
                           ("PRICE_INVALID", lambda: self.buy([line(price="10.005")])), ("PRICE_INVALID", lambda: self.buy([line(price="-1")])),
                           ("DESIGNATION_REQUIRED", lambda: self.buy([line(designation="")])),
                           ("DESIGNATION_REQUIRED", lambda: self.buy([line(designation="UNCLASSIFIED")])),
                           ("KARAT_NOT_FOUND", lambda: self.buy([line(karat=99)])), ("TRIP_NOT_FOUND", lambda: self.buy(trip="99")),
                           ("DATE_REQUIRED", lambda: self.buy(when="")), ("REQUEST_INVALID", lambda: self.buy(request="not-a-key"))):
            with self.subTest(code=code):
                self.refused(code, call)

    def test_a_missing_right_and_a_closed_trip_are_refused_with_no_trace(self):
        service, _ = self.owner_like(OWNER_NO_CORRECT - {"MARKET_TRIP_MANAGE"})
        before = self.state()
        with self.assertRaises(PermissionError):
            self.buy(service=service)
        self.assertEqual(self.state(), before)
        self.ret(self.trip, cash="100000")
        self.service.close(self.trip)
        self.refused("TRIP_CLOSED", lambda: self.buy())


class VendorTests(BuyCase):
    def test_the_vendor_is_mandatory_and_a_typed_name_is_kept_with_its_normalized_key(self):
        for blank in ("", "   ", None):
            self.refused("VENDOR_REQUIRED", lambda b=blank: self.buy(name=b))
        done = self.buy(name="  أحمَد   الصّاغة ")
        head = self.db.gold_buys[int(done["entry_id"])]
        self.assertEqual((done["vendor_name"], head["vendor_name"]), ("أحمَد   الصّاغة", "أحمَد   الصّاغة"))
        self.assertEqual(head["vendor_name_key"], MarketTripService.vendor_name_key("احمد الصاغه"))
        self.assertEqual(MarketTripService.vendor_name_key("  أحمَد   الصّاغة  "), MarketTripService.vendor_name_key("احمد الصاغه"))
        self.assertEqual(MarketTripService.vendor_name_key("إبراهيم ١٢"), MarketTripService.vendor_name_key("ابراهيم 12"))
        self.refused("TEXT_LONG", lambda: self.buy(name="ا" * 151))

    def test_a_registered_party_of_any_role_may_sell_and_an_inactive_or_unknown_one_is_refused(self):
        supplier = self.buy(party=ACTIVE_SUPPLIER, name="يُتجاهل")
        intermediary = self.buy(party=INTERMEDIARY)
        self.assertEqual((supplier["vendor_kind"], supplier["vendor_party_id"], supplier["vendor_name"]), ("REGISTERED_PARTY", "10", "مورد نشط"))
        self.assertEqual((intermediary["vendor_kind"], intermediary["vendor_party_id"]), ("REGISTERED_PARTY", "12"))
        self.refused("VENDOR_INACTIVE", lambda: self.buy(party=INACTIVE_SUPPLIER))
        self.refused("VENDOR_NOT_FOUND", lambda: self.buy(party="999"))
        self.refused("VENDOR_NOT_FOUND", lambda: self.buy(party="abc"))

    def test_buying_from_a_supplier_never_touches_its_gold_or_making_accounts(self):
        before = self.untouched()
        self.buy(party=ACTIVE_SUPPLIER)
        self.assertEqual(self.untouched(), before)
        self.assertEqual((self.db.sup_gold, self.db.sm_making, self.db.settlements), ([], [], {}))

    def test_registering_the_typed_vendor_as_a_party_goes_through_the_supplier_service(self):
        calls = []

        class Registry:
            def create_supplier(self, name, karat_id=None, **kwargs):
                calls.append((name, karat_id, kwargs))
                return 77
        self.service._vendor_registry = Registry
        result = self.service.register_vendor("  بائع جديد ", "1")
        self.assertEqual((result, calls), ({"id": "77", "name": "بائع جديد"}, [("بائع جديد", "1", {})]))
        self.refused("VENDOR_REQUIRED", lambda: self.service.register_vendor("  ", "1"))
        self.refused("KARAT_REQUIRED", lambda: self.service.register_vendor("بائع", ""))
        self.assertEqual(len(calls), 1)
        service, _ = self.owner_like(OWNER_NO_CORRECT - {"MARKET_TRIP_MANAGE"})
        with self.assertRaises(PermissionError):
            service.register_vendor("بائع", "1")


class PriceWarningTests(BuyCase):
    def test_a_total_far_from_the_reference_needs_the_owner_to_acknowledge_it(self):
        error = self.refused("PRICE_DEVIATION", lambda: self.buy([line(weight="10", price="40000")]))        # reference 60,000: -33.33 %
        self.assertIn("33.33", str(error))
        self.assertEqual((len(self.db.gold_buys), len(self.db.gold_buy_lines)), (0, 0))
        done = self.buy([line(weight="10", price="40000")], ack=True)
        head = self.db.gold_buys[int(done["entry_id"])]
        self.assertEqual((head["price_warning_acknowledged"], head["deviation_percent"], head["reference_total"]), (1, D("-33.33"), D("60000.00")))

    def test_the_reference_sums_every_line_and_a_total_inside_the_threshold_needs_no_acknowledgement(self):
        done = self.buy([line(K21, "5", "31500"), line(K24, "0.5", "3500")])                                 # 70,000 vs the 67,000 reference
        head = self.db.gold_buys[int(done["entry_id"])]
        self.assertEqual((head["reference_total"], head["deviation_percent"], head["price_warning_acknowledged"]), (D("33500.00"), D("4.48"), 0))
        self.buy([line(weight="5", price="33000")])                                                         # exactly +10 %: no warning
        self.refused("PRICE_DEVIATION", lambda: self.buy([line(weight="1", price="6610")]))

    def test_without_a_complete_reference_there_is_no_warning_and_no_evidence(self):
        self.service._price_reference = lambda: {21: D("6000")}                                              # no 18K reference
        done = self.buy([line(K18, "10", "1"), line(K21, "1", "6000")])
        head = self.db.gold_buys[int(done["entry_id"])]
        self.assertEqual((head["reference_total"], head["deviation_percent"], head["price_warning_acknowledged"]), (None, None, 0))
        self.service._price_reference = lambda: (_ for _ in ()).throw(RuntimeError("price engine down"))
        self.buy([line(weight="1", price="1")])                                                              # a failing price engine never blocks a purchase


class IdempotencyTests(BuyCase):
    def test_an_exact_replay_returns_the_original_and_a_different_payload_conflicts(self):
        first = self.buy([line(weight="2", price="12000")], request=bkey(900))
        again = self.buy([line(weight="2", price="12000")], request=bkey(900))
        self.assertEqual((again["duplicate"], again["entry_id"]), (True, first["entry_id"]))
        self.assertEqual((len(self.db.gold_buys), len(self.db.gold_buy_lines)), (1, 1))
        self.refused("REQUEST_CONFLICT", lambda: self.buy([line(weight="3", price="12000")], request=bkey(900)))
        self.refused("REQUEST_CONFLICT", lambda: self.buy([line(weight="2", price="12000")], name="غيره", request=bkey(900)))

    def test_the_replay_is_decided_before_availability_and_the_key_is_shared_with_every_flow(self):
        self.buy([line(price="100000")], request=bkey(901), ack=True)                                        # spends all the cash
        self.assertTrue(self.buy([line(price="100000")], request=bkey(901), ack=True)["duplicate"])
        self.issue("1", request=bkey(902))
        self.refused("REQUEST_CONFLICT", lambda: self.buy(request=bkey(902)))

    def test_the_acknowledgement_is_not_part_of_the_request_identity(self):
        self.buy([line(weight="10", price="40000")], ack=True, request=bkey(903))
        self.assertTrue(self.buy([line(weight="10", price="40000")], ack=False, request=bkey(903))["duplicate"])


class AtomicityTests(BuyCase):
    def test_a_failure_at_any_step_leaves_no_trace(self):
        for step in ("entry", "gold_buy", "gold_buy_line", "audit"):
            with self.subTest(step=step):
                before = self.state()
                self.db.fail_at = step
                with self.assertRaises(Exception):
                    self.buy([line(price="6000", weight="1")])
                self.db.fail_at = None
                self.assertEqual(self.state(), before)


class ReversalTests(BuyCase):
    def reverse(self, done, reason="خطأ في الشراء", service=None):
        return (service or self.service).reverse(done["entry_id"], reason)

    def test_a_reversal_restores_the_cash_and_removes_the_gold_without_any_other_movement(self):
        done = self.buy([line(K21, "10", "60000"), line(K18, "4", "20000", "MELT")])
        before = self.untouched()
        self.reverse(done)
        self.assertEqual((self.gold(), self.gold(18, "MELT")), (D("0.000"), D("0.000")))
        cash = self.cash()
        self.assertEqual((cash["spent"], cash["outstanding"]), (D("0.00"), D("100000.00")))
        self.assertEqual(self.untouched(), before)
        self.assertEqual(self.db.gold_buys[int(done["entry_id"])]["total_price"], D("80000.00"))             # the document is never rewritten
        entry = [e for e in self.service.trip_detail(self.trip)["entries"] if e["id"] == done["entry_id"]][0]
        self.assertTrue(entry["reversed"])
        self.assertEqual(entry["gold_buy"]["total_price"], "80000.00")
        self.refused("ALREADY_REVERSED", lambda: self.reverse(done), CorrectionError)

    def test_a_reversal_needs_the_correction_right_and_a_reason(self):
        done = self.buy()
        service, _ = self.owner_like(OWNER_NO_CORRECT)
        with self.assertRaises(Exception):
            self.reverse(done, service=service)
        with self.assertRaises(Exception):
            self.reverse(done, reason="")
        self.assertEqual(self.gold(), D("5.000"))

    def test_a_reversal_is_refused_after_the_gold_was_used_and_allowed_once_that_is_reversed(self):
        done = self.buy([line(weight="10", price="60000")])
        returned = self.ret(self.trip, rows=[(K21, "4", "LOGO")])
        error = self.refused("DEPENDENT_GOLD_USE_EXISTS", lambda: self.reverse(done), CorrectionError)
        self.assertIn("اعكس", str(error))
        self.service.reverse(returned["entry_id"], "تراجع")
        self.reverse(done)
        self.assertEqual((self.gold(), self.cash()["outstanding"]), (D("0.000"), D("100000.00")))

    def test_a_closed_trip_blocks_the_reversal(self):
        done = self.buy([line(weight="10", price="60000")])
        self.ret(self.trip, cash="40000", rows=[(K21, "10", "LOGO")])
        self.service.close(self.trip)
        self.refused("TRIP_CLOSED", lambda: self.reverse(done), CorrectionError)

    def test_the_issue_cannot_be_reversed_while_a_purchase_depends_on_its_cash(self):
        self.buy([line(weight="10", price="100000")], ack=True)
        first = self.service.trip_detail(self.trip)["entries"][0]
        self.refused("DEPENDENT_RETURN_EXISTS", lambda: self.service.reverse(first["id"], "تصحيح"), CorrectionError)


del TripCase                                       # the imported base must not run again in this module

if __name__ == "__main__":
    unittest.main()
