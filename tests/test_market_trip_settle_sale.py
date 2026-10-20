"""Market-custody settlement step 2d (063): after the melt the owner SELLS gold from the custody for an agreed total that ENTERS the custody cash (never Main Safe): unmelted
gold (UNCLASSIFIED, its own karat) through the existing 053 path and the melted BAR (BAR pool, 21K-equivalent grams) through the new TRIP_BAR source. Real services over the
in-memory transactional database of tests/market_trip_fakes.py. NOT real-MySQL tests."""
from decimal import Decimal as D
import unittest

from services.correction_service import CorrectionError
from tests.test_market_trip_settle_distribution import DistributionCase
from tests.test_market_trips import K18, K21, TODAY, key

REFERENCE = {21: D("6000.0000"), 18: D("5142.8571"), 24: D("7000.0000")}


class SaleOfSettlementGoldCase(DistributionCase):
    def setUp(self):
        super().setUp()
        self.service._price_reference = lambda: dict(REFERENCE)

    def sell_bar(self, weight="10", total="60000", mode="TOTAL", name="تاجر الصاغة", request=None, ack=False, trip=None, service=None):
        self.n += 1
        return (service or self.service).sell_gold(trip or self.trip, "TRIP_BAR", None, None, None, weight, mode, total, "", name, TODAY, "", "", request or key(500 + self.n), ack)

    def sell_raw(self, karat=K21, weight="10", total="60000", request=None):
        self.n += 1
        return self.service.sell_gold(self.trip, "CUSTODY_GOLD", karat, "UNCLASSIFIED", None, weight, "TOTAL", total, "", "تاجر", TODAY, "", "", request or key(400 + self.n), False)

    def cash(self):
        return self.service.trip_detail(self.trip)["custody"]["cash"]["outstanding"]


class BarSaleTests(SaleOfSettlementGoldCase):
    def test_a_part_of_the_bar_is_sold_and_the_proceeds_enter_the_custody_cash(self):
        cash_before, safe_before = self.cash(), self.db.cash()
        done = self.sell_bar("10", "60000")
        self.assertEqual((done["kind"], done["source_kind"], done["weight"], done["total_price"], done["karat_value"]), ("GOLD_SALE", "TRIP_BAR", "10.000", "60000.00", None))
        self.assertEqual(self.bar(), D("33.520"))
        self.assertEqual(self.cash(), cash_before + D("60000.00"))
        self.assertEqual(self.db.cash(), safe_before)                                              # Main Safe never moves

    def test_only_the_pool_and_the_sale_document_are_written(self):
        scrap, lots, sup = len(self.db.scrap), len(self.db.lots), len(self.db.sup_gold)
        self.sell_bar("5", "30000")
        self.assertEqual((len(self.db.scrap), len(self.db.lots), len(self.db.sup_gold)), (scrap, lots, sup))
        self.assertEqual(self.db.settle_moves[-1]["kind"], "GOLD_SALE")

    def test_the_whole_bar_can_be_sold_and_the_pool_is_then_zero(self):
        self.sell_bar("43.520", "260000")
        self.assertEqual(self.bar(), D("0.000"))

    def test_refusals_leave_no_trace(self):
        self.refused("BAR_EXCEEDS_POOL", lambda: self.sell_bar("43.521"))
        self.refused("WEIGHT_REQUIRED", lambda: self.sell_bar("0"))
        self.refused("BUYER_REQUIRED", lambda: self.sell_bar("1", name=""))
        self.refused("PRICE_INVALID", lambda: self.sell_bar("1", total="0"))

    def test_the_price_warning_uses_the_21k_reference_and_needs_acknowledgement(self):
        self.refused("PRICE_DEVIATION", lambda: self.sell_bar("10", "30000"))                        # reference 60,000: -50%
        done = self.sell_bar("10", "30000", ack=True)
        self.assertTrue(done["price_warning_acknowledged"])
        self.assertEqual(done["reference_price_per_gram"], "6000.0000")

    def test_per_gram_pricing_rounds_once(self):
        done = self.sell_bar("10.5", "6000", mode="PER_GRAM")
        self.assertEqual((done["total_price"], done["price_per_gram"]), ("63000.00", "6000.0000"))

    def test_exact_replay_posts_once_and_a_changed_payload_conflicts(self):
        k = key(777)
        first = self.sell_bar("10", "60000", request=k)
        again = self.sell_bar("10", "60000", request=k)
        self.assertEqual((again["entry_id"], again["duplicate"]), (first["entry_id"], True))
        self.assertEqual(self.bar(), D("33.520"))
        self.refused("REQUEST_CONFLICT", lambda: self.sell_bar("11", "60000", request=k))

    def test_a_failure_in_any_step_rolls_everything_back(self):
        for step in ("entry", "settle_move", "gold_sale"):
            self.db.fail_at = step
            before = self.state()
            with self.assertRaises(RuntimeError):
                self.sell_bar("10", "60000")
            self.db.fail_at = None
            self.assertEqual(self.state(), before, step)

    def test_a_closed_trip_is_refused(self):
        self.bar_to("10", "43.520")
        self.raw_to("10", K21, "10")
        self.raw_to("11", K18, "5")
        self.service.close(self.trip)
        self.refused("TRIP_CLOSED", lambda: self.sell_bar("1"))


class BarSaleReversalTests(SaleOfSettlementGoldCase):
    def reverse(self, entry):
        return self.corrections.reverse("MARKET_TRIP_ENTRY", entry, "خطأ")

    def test_a_reversal_restores_the_pool_and_removes_the_proceeds(self):
        cash_before = self.cash()
        done = self.sell_bar("10", "60000")
        self.reverse(done["entry_id"])
        self.assertEqual(self.bar(), D("43.520"))
        self.assertEqual(self.cash(), cash_before)

    def test_a_reversal_is_blocked_when_the_proceeds_were_used(self):
        done = self.sell_bar("10", "60000")
        self.n += 1
        self.service.return_custody(self.trip, "60000", [], TODAY, "", key(300 + self.n))          # the money went back to Main Safe
        with self.assertRaises(CorrectionError) as refused:
            self.reverse(done["entry_id"])
        self.assertEqual(refused.exception.code, "DEPENDENT_CASH_USE_EXISTS")

    def test_the_melt_cannot_be_reversed_while_its_bar_was_sold(self):
        self.sell_bar("10", "60000")
        with self.assertRaises(CorrectionError) as refused:
            self.reverse(self.done["entry_id"])
        self.assertEqual(refused.exception.code, "DEPENDENT_SETTLEMENT_USE_EXISTS")


class UnmeltedSaleTests(SaleOfSettlementGoldCase):
    def test_unmelted_gold_is_sold_in_its_own_karat(self):
        done = self.sell_raw(K21, "10", "60000")
        self.assertEqual((done["designation"], done["weight"]), ("UNCLASSIFIED", "10.000"))
        self.assertEqual(self.gold()[(21, "UNCLASSIFIED")]["outstanding"], D("0.000"))
        self.assertEqual(self.bar(), D("43.520"))
        self.refused("SALE_EXCEEDS_CUSTODY", lambda: self.sell_raw(K18, "5.001", "30000"))


if __name__ == "__main__":
    unittest.main()
