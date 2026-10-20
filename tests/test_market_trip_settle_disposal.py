"""Market-custody settlement step 2f (065): after the trip the remainder goes back to the shop (مرتجع سوق: the bar as 21K-equivalent grams, unmelted gold and logo in their own karat),
is recorded as a LOSS (خسية بوتقة / خسية سوق), or the issue weight is corrected both ways (reason mandatory). Real services over the in-memory transactional database of
tests/market_trip_fakes.py. NOT real-MySQL tests."""
from decimal import Decimal as D
import unittest

from services.correction_service import CorrectionError
from tests.test_market_trip_settle_distribution import DistributionCase
from tests.test_market_trips import K18, K21, TODAY, key


class DisposalCase(DistributionCase):
    """Melted trip: bar 43.520 (21K eq); raw left 21K 10 g, 18K 5 g."""

    def scrap(self, karat):
        return self.db.physical(karat)

    def give_back(self, lines, request=None, trip=None):
        self.n += 1
        return self.service.return_to_shop(trip or self.trip, lines, TODAY, "", request or key(500 + self.n))

    def lose(self, lines, request=None):
        self.n += 1
        return self.service.record_loss(self.trip, lines, TODAY, "", request or key(600 + self.n))

    def adjust(self, karat=K21, direction="INCREASE", weight="2", reason="وزن غلط", request=None):
        self.n += 1
        return self.service.correct_issue_weight(self.trip, karat, direction, weight, reason, TODAY, request or key(700 + self.n))

    def reverse(self, entry):
        return self.corrections.reverse("MARKET_TRIP_ENTRY", entry, "خطأ")


class ReturnToShopTests(DisposalCase):
    def test_the_bar_returns_as_21k_and_the_raw_and_logo_in_their_own_karat(self):
        self.classify_logo = self.service.classify_remaining_as_logo(self.trip, TODAY, "", key(80))     # 21K 10 g and 18K 5 g become LOGO
        s21, s18 = self.scrap(1), self.scrap(2)
        done = self.give_back([{"source": "BAR", "weight": "20"}, {"source": "LOGO", "karat_id": K21, "weight": "4"}, {"source": "LOGO", "karat_id": K18, "weight": "5"}])
        self.assertEqual(done["kind"], "ST_RET_MKT")
        self.assertEqual((self.scrap(1), self.scrap(2)), (s21 + D("24.000"), s18 + D("5.000")))          # 20 (bar eq21) + 4 of 21K ; 5 of 18K
        self.assertEqual(self.bar(), D("23.520"))
        self.assertEqual(self.gold()[(21, "LOGO")]["outstanding"], D("6.000"))
        self.assertEqual(self.gold()[(18, "LOGO")]["outstanding"], D("0.000"))

    def test_unmelted_gold_returns_in_its_own_karat_with_one_scrap_movement_per_karat(self):
        before = len(self.db.scrap)
        self.give_back([{"source": "RAW", "karat_id": K21, "weight": "3"}, {"source": "BAR", "weight": "2"}])     # both land on 21K
        self.assertEqual(len(self.db.scrap), before + 1)
        self.assertEqual(self.db.scrap[-1]["weight_delta"], D("5.000"))

    def test_no_cash_supplier_or_lot_movement(self):
        cash, sup, lots = self.db.cash(), len(self.db.sup_gold), len(self.db.lots)
        self.give_back([{"source": "BAR", "weight": "10"}])
        self.assertEqual((self.db.cash(), len(self.db.sup_gold), len(self.db.lots)), (cash, sup, lots))

    def test_refusals_leave_no_trace(self):
        self.refused("DISPOSE_EXCEEDS_CUSTODY", lambda: self.give_back([{"source": "BAR", "weight": "43.521"}]))
        self.refused("DISPOSE_EXCEEDS_CUSTODY", lambda: self.give_back([{"source": "RAW", "karat_id": K21, "weight": "10.001"}]))
        self.refused("DISPOSE_EXCEEDS_CUSTODY", lambda: self.give_back([{"source": "LOGO", "karat_id": K21, "weight": "1"}]))      # nothing classified as logo
        self.refused("LINES_REQUIRED", lambda: self.give_back([]))
        self.refused("SOURCE_REQUIRED", lambda: self.give_back([{"source": "X", "weight": "1"}]))
        self.refused("KARAT_REQUIRED", lambda: self.give_back([{"source": "RAW", "weight": "1"}]))
        self.refused("WEIGHT_REQUIRED", lambda: self.give_back([{"source": "BAR", "weight": "0"}]))

    def test_exact_replay_and_a_changed_payload(self):
        k = key(55)
        first = self.give_back([{"source": "BAR", "weight": "5"}], request=k)
        again = self.give_back([{"source": "BAR", "weight": "5"}], request=k)
        self.assertEqual((again["entry_id"], again["duplicate"]), (first["entry_id"], True))
        self.assertEqual(self.bar(), D("38.520"))
        self.refused("REQUEST_CONFLICT", lambda: self.give_back([{"source": "BAR", "weight": "6"}], request=k))

    def test_a_failure_in_any_step_rolls_everything_back(self):
        for step in ("entry", "settle_line", "settle_move", "cash", "scrap", "leg"):
            self.db.fail_at = step
            before = self.state()
            try:
                self.give_back([{"source": "BAR", "weight": "5"}])
            except RuntimeError:
                pass
            else:
                if step in ("settle_line", "settle_move", "entry", "scrap", "leg"):
                    self.fail(f"{step} did not fail")
            self.db.fail_at = None
            if step in ("settle_line", "settle_move", "entry", "scrap", "leg"):
                self.assertEqual(self.state(), before, step)

    def test_a_reversal_puts_the_grams_back_into_the_pool_and_takes_them_out_of_scrap(self):
        s21 = self.scrap(1)
        done = self.give_back([{"source": "BAR", "weight": "20"}])
        self.reverse(done["entry_id"])
        self.assertEqual((self.bar(), self.scrap(1)), (D("43.520"), s21))

    def test_the_closure_needs_every_pool_to_reach_zero(self):
        self.give_back([{"source": "BAR", "weight": "43.520"}, {"source": "RAW", "karat_id": K21, "weight": "10"}, {"source": "RAW", "karat_id": K18, "weight": "5"}])
        self.assertEqual(self.service.close(self.trip)["status"], "CLOSED")


class LossTests(DisposalCase):
    def test_a_loss_removes_the_grams_with_its_classification_and_moves_nothing_else(self):
        scrap, cash, sup = (self.scrap(1), self.scrap(2)), self.db.cash(), len(self.db.sup_gold)
        done = self.lose([{"source": "BAR", "weight": "0.5", "classification": "CRUCIBLE"}, {"source": "RAW", "karat_id": K18, "weight": "1.25", "classification": "MARKET"}])
        self.assertEqual(done["kind"], "ST_LOSS")
        self.assertEqual([(l["source"], l["classification"]) for l in done["lines"]], [("BAR", "CRUCIBLE"), ("RAW", "MARKET")])
        self.assertEqual(self.bar(), D("43.020"))
        self.assertEqual(self.gold()[(18, "UNCLASSIFIED")]["outstanding"], D("3.750"))
        self.assertEqual(((self.scrap(1), self.scrap(2)), self.db.cash(), len(self.db.sup_gold)), (scrap, cash, sup))

    def test_the_classification_is_mandatory_and_validated(self):
        self.refused("CLASSIFICATION_REQUIRED", lambda: self.lose([{"source": "BAR", "weight": "1"}]))
        self.refused("CLASSIFICATION_REQUIRED", lambda: self.lose([{"source": "BAR", "weight": "1", "classification": "OTHER"}]))
        self.refused("DISPOSE_EXCEEDS_CUSTODY", lambda: self.lose([{"source": "BAR", "weight": "43.521", "classification": "MARKET"}]))

    def test_the_same_source_with_two_classifications_is_checked_together(self):
        self.refused("DISPOSE_EXCEEDS_CUSTODY", lambda: self.lose([{"source": "BAR", "weight": "30", "classification": "CRUCIBLE"},
                                                                  {"source": "BAR", "weight": "14", "classification": "MARKET"}]))
        self.lose([{"source": "BAR", "weight": "30", "classification": "CRUCIBLE"}, {"source": "BAR", "weight": "13.52", "classification": "MARKET"}])
        self.assertEqual(self.bar(), D("0.000"))

    def test_a_reversal_gives_the_grams_back(self):
        done = self.lose([{"source": "BAR", "weight": "3", "classification": "CRUCIBLE"}])
        self.reverse(done["entry_id"])
        self.assertEqual(self.bar(), D("43.520"))

    def test_the_melt_cannot_be_reversed_while_its_bar_was_lost(self):
        self.lose([{"source": "BAR", "weight": "3", "classification": "CRUCIBLE"}])
        with self.assertRaises(CorrectionError) as refused:
            self.reverse(self.done["entry_id"])
        self.assertEqual(refused.exception.code, "DEPENDENT_SETTLEMENT_USE_EXISTS")


class IssueWeightCorrectionTests(DisposalCase):
    def test_an_increase_takes_the_difference_from_scrap_and_adds_it_to_the_custody(self):
        s21 = self.scrap(1)
        done = self.adjust(K21, "INCREASE", "2.5")
        self.assertEqual((done["kind"], done["direction"], done["weight"]), ("ST_ADJUST", "INCREASE", "2.500"))
        self.assertEqual(self.scrap(1), s21 - D("2.500"))
        self.assertEqual(self.gold()[(21, "UNCLASSIFIED")]["outstanding"], D("12.500"))

    def test_a_decrease_returns_the_difference_to_scrap_and_removes_it_from_the_custody(self):
        s21 = self.scrap(1)
        self.adjust(K21, "DECREASE", "4")
        self.assertEqual(self.scrap(1), s21 + D("4.000"))
        self.assertEqual(self.gold()[(21, "UNCLASSIFIED")]["outstanding"], D("6.000"))

    def test_scrap_can_never_go_negative_and_the_custody_cannot_go_below_zero(self):
        self.refused("SCRAP_SHORTAGE", lambda: self.adjust(K21, "INCREASE", "500"))
        self.refused("ADJUST_EXCEEDS_CUSTODY", lambda: self.adjust(K21, "DECREASE", "10.001"))

    def test_a_reason_and_a_direction_are_mandatory(self):
        self.refused("REASON_REQUIRED", lambda: self.adjust(reason=""))
        self.refused("DIRECTION_REQUIRED", lambda: self.adjust(direction="SIDEWAYS"))
        self.refused("WEIGHT_REQUIRED", lambda: self.adjust(weight="0"))

    def test_exact_replay_posts_once(self):
        k = key(66)
        first = self.adjust(request=k)
        again = self.adjust(request=k)
        self.assertEqual((again["entry_id"], again["duplicate"]), (first["entry_id"], True))
        self.assertEqual(self.gold()[(21, "UNCLASSIFIED")]["outstanding"], D("12.000"))
        self.refused("REQUEST_CONFLICT", lambda: self.adjust(weight="3", request=k))

    def test_a_reversal_of_an_increase_is_blocked_once_the_added_grams_were_used(self):
        done = self.adjust(K21, "INCREASE", "2")
        self.give_back([{"source": "RAW", "karat_id": K21, "weight": "12"}])                       # the added grams went back to the shop
        with self.assertRaises(CorrectionError) as refused:
            self.reverse(done["entry_id"])
        self.assertEqual(refused.exception.code, "DEPENDENT_GOLD_USE_EXISTS")

    def test_a_reversal_restores_scrap_and_the_custody(self):
        s21 = self.scrap(1)
        done = self.adjust(K21, "INCREASE", "2")
        self.reverse(done["entry_id"])
        self.assertEqual((self.scrap(1), self.gold()[(21, "UNCLASSIFIED")]["outstanding"]), (s21, D("10.000")))
        decrease = self.adjust(K21, "DECREASE", "3")
        self.reverse(decrease["entry_id"])
        self.assertEqual((self.scrap(1), self.gold()[(21, "UNCLASSIFIED")]["outstanding"]), (s21, D("10.000")))

    def test_a_failure_in_any_step_rolls_everything_back(self):
        for step in ("entry", "scrap", "leg", "settle_move", "settle_adjust"):
            self.db.fail_at = step
            before = self.state()
            with self.assertRaises(RuntimeError):
                self.adjust(K21, "INCREASE", "1")
            self.db.fail_at = None
            self.assertEqual(self.state(), before, step)


if __name__ == "__main__":
    unittest.main()
