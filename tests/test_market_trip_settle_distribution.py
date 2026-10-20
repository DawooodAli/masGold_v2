"""Market-custody settlement step 2b (061, owner 2026-10-20): after the melt, the owner DELIVERS the melted bar (BAR pool, 21K-equivalent grams) and the unmelted gold (in its
OWN karat) to suppliers, in parts. The supplier is credited in ITS dedicated karat (derived once, like 045/046); no Scrap, cash or Main Safe movement; every delivery is reversed
only through the custody entry.

Real MarketTripService, SupplierGoldSettlementService and CorrectionService over the in-memory transactional database of tests/market_trip_fakes.py (a failed transaction restores
every table; the 061 CHECK / composite-key constraints are mirrored as asserts). NOT real-MySQL tests."""
from decimal import Decimal as D
import unittest
from unittest.mock import patch

from services.correction_service import CorrectionError
from services.supplier_gold_settlement_service import SupplierGoldSettlementService
from tests.market_trip_fakes import ScrapRepo, SettlementRepo
from tests.test_market_trip_settle_melt import SettleMeltCase
from tests.test_market_trips import K18, K21, TODAY, key


class DistributionCase(SettleMeltCase):
    """OPEN trip 1: 21K 50 g and 18K 10 g left without a destination; 40 g of 21K + 5 g of 18K were melted into a bar worth 43.520 g (21K eq); raw left: 21K 10 g, 18K 5 g."""

    def setUp(self):
        super().setUp()
        self.db.suppliers[13] = {"id": 13, "name": "بلا عيار", "is_active": 1, "is_supplier": True, "is_intermediary": False, "karat_id": None}
        started = patch("repositories.supplier_gold_settlement_repository.SupplierGoldSettlementRepository", SettlementRepo)
        started.start()
        self.addCleanup(started.stop)
        self.done = self.melt()

    def bar_to(self, supplier="10", weight="18.8", trip=None, request=None, service=None, when=TODAY, reference="", notes=""):
        self.n += 1
        return (service or self.service).settle_supplier_bar(trip or self.trip, supplier, weight, when, reference, notes, request or key(700 + self.n))

    def raw_to(self, supplier="10", karat=K21, weight="10", trip=None, request=None):
        self.n += 1
        return self.service.settle_supplier_gold(trip or self.trip, supplier, karat, "UNCLASSIFIED", weight, TODAY, "", "", request or key(600 + self.n))

    def payable(self, supplier, karat_id):
        return sum((m["weight_delta"] for m in self.db.sup_gold if m["supplier_id"] == int(supplier) and m["karat_id"] == karat_id), D("0.000"))


class BarDeliveryTests(DistributionCase):
    def test_a_part_of_the_bar_is_delivered_and_credited_in_the_suppliers_own_karat(self):
        done = self.bar_to("10", "18.8")                                                            # supplier 10 is 21K: credit 18.800
        self.assertEqual((done["kind"], done["delivered_weight"], done["credited_weight"], done["target_karat"]), ("ST_BAR_SUP", "18.800", "18.800", 21))
        self.assertEqual(self.bar(), D("24.720"))                                                   # 43.520 - 18.800
        self.assertEqual(self.payable(10, 1), D("-18.800"))
        other = self.bar_to("11", "9")                                                              # supplier 11 is 18K: 9 x 21 / 18 = 10.5
        self.assertEqual((other["credited_weight"], other["target_karat"]), ("10.500", 18))
        self.assertEqual((self.bar(), self.payable(11, 2)), (D("15.720"), D("-10.500")))
        header = self.db.settlements[int(done["settlement_id"])]
        self.assertEqual((header["source_kind"], header["delivered_karat_value"], header["delivered_weight_grams"]), ("TRIP_BAR", 21, D("18.800")))

    def test_no_scrap_cash_or_lot_moves(self):
        cash, scrap, lots = self.db.cash(), len(self.db.scrap), len(self.db.lots)
        self.bar_to("10", "5")
        self.raw_to("10", K21, "5")
        self.assertEqual((self.db.cash(), len(self.db.scrap), len(self.db.lots)), (cash, scrap, lots))

    def test_the_whole_bar_can_go_and_only_the_remainder_blocks_the_closure(self):
        self.bar_to("10", "43.520")
        self.assertEqual(self.bar(), D("0.000"))
        self.refused("OUTSTANDING_CUSTODY", lambda: self.service.close(self.trip))                  # the unmelted gold is still in custody
        self.raw_to("10", K21, "10")
        self.raw_to("11", K18, "5")
        self.assertEqual(self.service.close(self.trip)["status"], "CLOSED")                         # every pool is zero

    def test_unmelted_gold_is_delivered_in_its_own_karat(self):
        done = self.raw_to("11", K21, "10")                                                         # 10 g of 21K to an 18K supplier: 10 x 21 / 18
        self.assertEqual((done["delivered_weight"], done["credited_weight"], done["target_karat"], done["designation"]), ("10.000", "11.667", 18, "UNCLASSIFIED"))
        self.assertEqual(self.gold()[(21, "UNCLASSIFIED")]["outstanding"], D("0.000"))
        self.assertEqual(self.payable(11, 2), D("-11.667"))
        self.assertEqual(self.bar(), D("43.520"))                                                   # the bar is untouched

    def test_refusals_leave_no_trace(self):
        self.refused("BAR_EXCEEDS_POOL", lambda: self.bar_to("10", "43.521"))
        self.refused("WEIGHT_REQUIRED", lambda: self.bar_to("10", "0"))
        self.refused("SUPPLIER_NOT_FOUND", lambda: self.bar_to("", "1"))
        self.refused("SUPPLIER_NOT_FOUND", lambda: self.bar_to("99", "1"))
        self.refused("SUPPLIER_ROLE_REQUIRED", lambda: self.bar_to("12", "1"))                      # a مسوقجي-only party has no gold account
        self.refused("SUPPLIER_KARAT_MISSING", lambda: self.bar_to("13", "1"))
        self.refused("SETTLEMENT_EXCEEDS_CUSTODY", lambda: self.raw_to("10", K21, "10.001"))
        self.assertEqual(self.bar(), D("43.520"))

    def test_exact_replay_and_a_changed_payload(self):
        first = self.bar_to("10", "5", request=key(33))
        again = self.bar_to("10", "5", request=key(33))
        self.assertEqual((again["entry_id"], again["duplicate"], again["settlement_id"]), (first["entry_id"], True, first["settlement_id"]))
        self.assertEqual((self.bar(), self.payable(10, 1)), (D("38.520"), D("-5.000")))             # posted once
        self.refused("REQUEST_CONFLICT", lambda: self.bar_to("10", "6", request=key(33)))

    def test_a_failure_in_any_step_rolls_everything_back(self):
        for step in ("sg_header", "entry", "sg_leg", "bar_link", "settle_move"):
            self.db.fail_at = step
            before = self.state()
            with self.assertRaises(RuntimeError):
                self.bar_to("10", "5")
            self.db.fail_at = None
            self.assertEqual(self.state(), before, step)

    def test_a_closed_trip_is_refused(self):
        self.raw_to("10", K21, "10")
        self.raw_to("11", K18, "5")
        self.bar_to("10", "43.520")
        self.service.close(self.trip)
        self.refused("TRIP_CLOSED", lambda: self.bar_to("10", "1"))


class BarReversalTests(DistributionCase):
    def test_a_reversal_restores_the_pool_and_the_stored_credit(self):
        done = self.bar_to("11", "9")                                                               # credit 10.500 in 18K
        scrap = len(self.db.scrap)
        rv = self.service.reverse(done["entry_id"], "تسليم خاطئ")
        self.assertEqual(rv["code"][:2], "RV")
        self.assertEqual((self.bar(), self.payable(11, 2)), (D("43.520"), D("0.000")))              # the pool and the supplier are exactly as before
        movement = [m for m in self.db.sup_gold if m["source_type"] == "TRANSACTION_CORRECTION"]
        self.assertEqual([(m["supplier_id"], m["karat_id"], m["weight_delta"]) for m in movement], [(11, 2, D("10.500"))])
        self.assertEqual(len(self.db.scrap), scrap)                                                 # no Scrap movement either way

    def test_the_melt_cannot_be_reversed_while_its_bar_was_delivered(self):
        self.bar_to("10", "10")
        with self.assertRaises(CorrectionError) as refused:
            self.service.reverse(self.done["entry_id"], "خطأ")
        self.assertEqual(refused.exception.code, "DEPENDENT_SETTLEMENT_USE_EXISTS")

    def test_the_supplier_side_reversal_is_refused_with_guidance(self):
        done = self.bar_to("10", "4")
        direct = SupplierGoldSettlementService(self.session, transaction_factory=self.db.transaction, repository=SettlementRepo, scrap=ScrapRepo,
                                               authorization=self.authorization, corrections=self.corrections)
        before = self.state()
        with self.assertRaises(CorrectionError) as refused:
            direct.reverse(done["settlement_id"], "من صفحة المورد")
        self.assertEqual(refused.exception.code, "CUSTODY_SETTLEMENT_REVERSE_FROM_TRIP")
        self.assertEqual(self.state(), before)


class RightsTests(DistributionCase):
    def test_the_supplier_gold_right_is_required(self):
        before = self.state()
        limited = self.as_limited({"APP_LOGIN", "MARKET_TRIP_MANAGE"})
        with self.assertRaises(PermissionError):
            limited.settle_supplier_bar(self.trip, "10", "1", TODAY, "", "", key(990))
        self.assertEqual(self.state(), before)

    def as_limited(self, permissions, user_id=31):
        from services.correction_service import CorrectionService
        from services.market_trip_service import MarketTripService
        from tests.auth_fakes import FakeAuthorization
        from tests.market_trip_fakes import AccountsRepo, Corrections, Customers, Invoices, SupplierPayRepo, TripRepo, session_for
        session = session_for(permissions, user_id)
        authorization = FakeAuthorization(session)
        corrections = CorrectionService(session, authorization=authorization, transaction_factory=self.db.transaction, invoice_repository=Invoices,
                                        correction_repository=Corrections, customer_repository=Customers)
        return MarketTripService(session, transaction_factory=self.db.transaction, repository=TripRepo, accounts=AccountsRepo, scrap=ScrapRepo, authorization=authorization,
                                 corrections=corrections, supplier_payments=SupplierPayRepo, gold_settlements=SettlementRepo)


class MarketerCase(DistributionCase):
    """Step 2c (062): the melted bar handed to a MARKETER becomes his 21K entitlement (055 account); the gold stays shop-owned; no Scrap / cash / supplier / lot moves."""
    MKT, MKT_OFF, MKT_SUP = "20", "22", "21"

    def setUp(self):
        from tests.marketer_fakes import MarketerRepo, prepare
        super().setUp()
        prepare(self.db)
        started = patch("repositories.marketer_custody_repository.MarketerCustodyRepository", MarketerRepo)
        started.start()
        self.addCleanup(started.stop)
        self.marketers = MarketerRepo(self.db)

    def bar_to_marketer(self, party=None, weight="20", trip=None, request=None, service=None, when=TODAY, notes=""):
        self.n += 1
        return (service or self.service).settle_bar_to_marketer(trip or self.trip, party or self.MKT, weight, when, notes, request or key(800 + self.n))

    def held(self, party=None):
        return D(str(self.marketers.gold_balance(int(party or self.MKT))))


class BarToMarketerTests(MarketerCase):
    def test_the_bar_becomes_the_marketers_21k_entitlement_and_the_pool_follows(self):
        scrap, cash, lots = len(self.db.scrap), self.db.cash(), len(self.db.lots)
        done = self.bar_to_marketer(weight="20")
        self.assertEqual((done["kind"], done["marketer_grams"], done["party_name"]), ("ST_BAR_MKT", "20.000", "مسوقجي أول"))
        self.assertEqual((self.bar(), self.held()), (D("23.520"), D("20.000")))                    # 43.520 - 20.000; the marketer holds 20.000 g of 21K
        self.assertEqual((self.db.cash(), len(self.db.scrap), len(self.db.lots)), (cash, scrap, lots))
        leg = self.db.mk_gold[-1]
        self.assertEqual((leg["movement_type"], leg["trip_entry_kind"], leg["weight_delta"]), ("TRIP_BOOK_IN", "ST_BAR_MKT", D("20.000")))

    def test_the_whole_bar_can_go_and_only_the_unmelted_gold_blocks_the_closure(self):
        self.bar_to_marketer(weight="43.520")
        self.assertEqual(self.bar(), D("0.000"))
        self.refused("OUTSTANDING_CUSTODY", lambda: self.service.close(self.trip))
        self.raw_to("10", K21, "10")
        self.raw_to("11", K18, "5")
        self.assertEqual(self.service.close(self.trip)["status"], "CLOSED")

    def test_refusals_leave_no_trace(self):
        self.refused("BAR_EXCEEDS_POOL", lambda: self.bar_to_marketer(weight="43.521"))
        self.refused("WEIGHT_REQUIRED", lambda: self.bar_to_marketer(weight="0"))
        self.refused("MARKETER_NOT_FOUND", lambda: self.bar_to_marketer(party="99"))
        self.refused("MARKETER_NOT_FOUND", lambda: self.bar_to_marketer(party="23"))                # a supplier without the intermediary role is not a marketer
        self.refused("MARKETER_INACTIVE", lambda: self.bar_to_marketer(party=self.MKT_OFF))
        self.assertEqual(self.bar(), D("43.520"))

    def test_a_dual_role_party_is_accepted_and_keeps_its_supplier_account_apart(self):
        self.bar_to_marketer(party=self.MKT_SUP, weight="5")
        self.assertEqual(self.held(self.MKT_SUP), D("5.000"))
        self.assertEqual(self.payable(21, 2), D("0.000"))                                             # the supplier account of the same party is untouched

    def test_exact_replay_and_a_changed_payload(self):
        first = self.bar_to_marketer(weight="7", request=key(44))
        again = self.bar_to_marketer(weight="7", request=key(44))
        self.assertEqual((again["entry_id"], again["duplicate"]), (first["entry_id"], True))
        self.assertEqual((self.bar(), self.held()), (D("36.520"), D("7.000")))                       # posted once
        self.refused("REQUEST_CONFLICT", lambda: self.bar_to_marketer(weight="8", request=key(44)))

    def test_a_failure_in_any_step_rolls_everything_back(self):
        for step in ("entry", "mk_gold", "settle_move"):
            self.db.fail_at = step
            before = self.state()
            with self.assertRaises(RuntimeError):
                self.bar_to_marketer(weight="5")
            self.db.fail_at = None
            self.assertEqual(self.state(), before, step)

    def test_the_rights_are_required(self):
        before = self.state()
        limited = RightsTests.as_limited(self, {"APP_LOGIN", "MARKET_TRIP_MANAGE", "SUPPLIER_GOLD_SETTLE"})
        with self.assertRaises(PermissionError):
            limited.settle_bar_to_marketer(self.trip, self.MKT, "1", TODAY, "", key(991))
        self.assertEqual(self.state(), before)


class BarToMarketerReversalTests(MarketerCase):
    def test_a_reversal_restores_the_pool_and_compensates_the_stored_leg(self):
        done = self.bar_to_marketer(weight="20")
        rv = self.service.reverse(done["entry_id"], "تسليم خاطئ")
        self.assertEqual(rv["code"][:2], "RV")
        self.assertEqual((self.bar(), self.held()), (D("43.520"), D("0.000")))
        legs = [(m["movement_type"], m["weight_delta"]) for m in self.db.mk_gold]
        self.assertEqual(legs, [("TRIP_BOOK_IN", D("20.000")), ("TRIP_BOOK_IN_REVERSAL", D("-20.000"))])         # the original stays counted

    def test_a_reversal_is_refused_when_the_marketer_no_longer_holds_the_grams(self):
        done = self.bar_to_marketer(weight="20")
        self.db.mk_gold.append({"id": 900, "party_id": int(self.MKT), "document_id": None, "doc_kind": None, "settlement_id": None, "trip_entry_id": None,
                                "trip_entry_kind": None, "movement_type": "GOLD_RETURN", "weight_grams": D("15.000"), "weight_delta": D("-15.000"), "correction_id": None})
        with self.assertRaises(CorrectionError) as refused:
            self.service.reverse(done["entry_id"], "خطأ")
        self.assertEqual(refused.exception.code, "DEPENDENT_MARKETER_USE_EXISTS")

    def test_the_melt_cannot_be_reversed_while_its_bar_was_handed_over(self):
        self.bar_to_marketer(weight="10")
        with self.assertRaises(CorrectionError) as refused:
            self.service.reverse(self.done["entry_id"], "خطأ")
        self.assertEqual(refused.exception.code, "DEPENDENT_SETTLEMENT_USE_EXISTS")


class ClassifyLogoTests(DistributionCase):
    """Step 2c (062): what was not melted is LOGO (the owner's rule): one explicit action classifies the remainder, after which every LOGO flow applies unchanged."""

    def classify(self, trip=None, request=None, when=TODAY, notes="", service=None):
        self.n += 1
        return (service or self.service).classify_remaining_as_logo(trip or self.trip, when, notes, request or key(900 + self.n))

    def logo(self, karat_value):
        return self.gold().get((karat_value, "LOGO"), {"outstanding": D("0.000")})["outstanding"]

    def test_the_whole_remainder_becomes_logo_of_its_own_karat(self):
        cash, scrap, lots, settlements = self.db.cash(), len(self.db.scrap), len(self.db.lots), len(self.db.settlements)
        done = self.classify()
        self.assertEqual((done["kind"], done["lines"]), ("ST_LOGO", [{"karat": 21, "grams": "10.000"}, {"karat": 18, "grams": "5.000"}]))
        gold = self.gold()
        self.assertEqual((gold[(21, "UNCLASSIFIED")]["outstanding"], gold[(18, "UNCLASSIFIED")]["outstanding"]), (D("0.000"), D("0.000")))
        self.assertEqual((self.logo(21), self.logo(18)), (D("10.000"), D("5.000")))
        self.assertEqual(self.bar(), D("43.520"))                                                   # the bar is untouched
        self.assertEqual((self.db.cash(), len(self.db.scrap), len(self.db.lots), len(self.db.settlements)), (cash, scrap, lots, settlements))

    def test_nothing_to_classify_and_a_second_classification_are_refused(self):
        self.classify()
        self.refused("NOTHING_TO_CLASSIFY", lambda: self.classify())

    def test_the_classified_gold_flows_through_the_existing_logo_paths(self):
        self.classify()
        done = self.service.settle_supplier_gold(self.trip, "11", K21, "LOGO", "4", TODAY, "", "", key(61))                    # a LOGO supplier settlement (046)
        self.assertEqual((done["designation"], done["credited_weight"], done["target_karat"]), ("LOGO", "4.667", 18))
        self.service.return_custody(self.trip, "", [{"karat_id": K21, "weight": "6", "designation": "LOGO"}], TODAY, "", key(62))   # a LOGO return to the shop
        self.assertEqual(self.logo(21), D("0.000"))

    def test_exact_replay_posts_once(self):
        first = self.classify(request=key(63))
        again = self.classify(request=key(63))
        self.assertEqual((again["entry_id"], again["duplicate"]), (first["entry_id"], True))
        self.assertEqual(self.logo(21), D("10.000"))                                                # posted once

    def test_a_failure_in_any_step_rolls_everything_back(self):
        for step in ("entry", "settle_move"):
            self.db.fail_at = step
            before = self.state()
            with self.assertRaises(RuntimeError):
                self.classify()
            self.db.fail_at = None
            self.assertEqual(self.state(), before, step)

    def test_a_closed_trip_is_refused(self):
        self.raw_to("10", K21, "10")
        self.raw_to("11", K18, "5")
        self.bar_to("10", "43.520")
        self.service.close(self.trip)
        self.refused("TRIP_CLOSED", lambda: self.classify())

    def test_the_reversal_restores_the_unclassified_gold_until_the_logo_gold_is_used(self):
        done = self.classify()
        self.service.reverse(done["entry_id"], "خطأ")
        self.assertEqual((self.gold()[(21, "UNCLASSIFIED")]["outstanding"], self.logo(21)), (D("10.000"), D("0.000")))
        again = self.classify()
        self.service.settle_supplier_gold(self.trip, "11", K21, "LOGO", "1", TODAY, "", "", key(64))
        with self.assertRaises(CorrectionError) as refused:
            self.service.reverse(again["entry_id"], "خطأ")
        self.assertEqual(refused.exception.code, "DEPENDENT_LOGO_USE_EXISTS")


if __name__ == "__main__":
    unittest.main()
