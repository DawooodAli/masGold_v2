"""Position snapshots: every rule with hand-checkable numbers (pure functions, no database)."""
from datetime import datetime
from decimal import Decimal as D
import unittest

from services import position_snapshot_rules as rules


def sheet(i, when, gold, cash="0", bank="0"):
    return {"id": i, "taken_at": when, "gold_shop_eq21": D(gold), "gold_custody_eq21": D(0), "gold_intermediary_eq21": D(0), "gold_book_eq21": D(0), "gold_suppliers_eq21": D(0),
            "gold_total_eq21": D(gold), "cash_egp": D(cash), "bank_egp": D(bank)}


class SnapshotFigureTests(unittest.TestCase):
    def test_supplier_gold_is_signed_from_the_shop_point_of_view_in_21k(self):
        # the shop owes 21 g of 18K (= 18 g eq21) and a supplier holds 10 g of 24K for the shop (ledger -10 -> +11.4286 eq21)
        owed = rules.suppliers_eq21([{"karat_value": 18, "grams": "21.000"}, {"karat_value": 24, "grams": "-10.000"}])
        self.assertEqual(owed, D("-6.571"))                           # -(21*18/21) + (10*24/21) = -18 + 11.42857 = -6.571
        self.assertEqual(rules.suppliers_eq21([]), D("0.000"))

    def test_snapshot_total_is_the_exact_sum_of_the_quantised_parts(self):
        summary = {"shop_eq21": "1000.0004", "custody_eq21": "50.0005", "intermediary_eq21": "20.0004", "book_eq21": "0", "unknown_grams": "3.2"}
        snap = rules.build_snapshot(summary, [{"karat_value": 21, "grams": "30.000"}], "1000.005", "250.004")
        self.assertEqual((snap["gold_shop_eq21"], snap["gold_custody_eq21"], snap["gold_intermediary_eq21"], snap["gold_suppliers_eq21"]),
                         (D("1000.000"), D("50.001"), D("20.000"), D("-30.000")))
        self.assertEqual(snap["gold_total_eq21"], D("1000.000") + D("50.001") + D("20.000") + D("-30.000"))
        self.assertEqual((snap["cash_egp"], snap["bank_egp"], snap["unknown_grams"]), (D("1000.01"), D("250.00"), D("3.200")))

    def test_the_opening_balance_keeps_other_parts_at_zero(self):
        snap = rules.opening_snapshot("5000", "-120.5", "200000", "0")
        self.assertEqual(snap["gold_total_eq21"], D("4879.500"))
        self.assertEqual((snap["gold_custody_eq21"], snap["gold_intermediary_eq21"], snap["gold_book_eq21"]), (D("0.000"),) * 3)


class ChoiceAndProfitTests(unittest.TestCase):
    def setUp(self):
        self.sheets = [sheet(1, datetime(2026, 1, 1, 9), "5000", "200000"), sheet(2, datetime(2026, 1, 15, 9), "5100", "210000"), sheet(3, datetime(2026, 2, 1, 9), "5300", "190000")]

    def test_profit_is_the_closing_sheet_minus_the_opening_sheet(self):
        opening, closing, problem = rules.choose(self.sheets, datetime(2026, 1, 1), datetime(2026, 2, 1))
        self.assertIsNone(problem)
        self.assertEqual((opening["id"], closing["id"], opening["before_first"]), (1, 3, False))             # the morning sheet of 1 Jan opens the period, the morning sheet of 1 Feb closes it
        result = rules.profit(opening, closing)
        self.assertEqual((result["gold_total_eq21"], result["cash_egp"]), (D("300.000"), D("-10000")))          # 5300 - 5000 gold, 190000 - 200000 cash

    def test_a_period_before_the_first_sheet_uses_it_and_flags_it(self):
        opening, closing, problem = rules.choose(self.sheets, datetime(2025, 12, 1), datetime(2026, 1, 21))
        self.assertTrue(opening["before_first"])
        self.assertEqual((opening["id"], closing["id"], problem), (1, 2, None))

    def test_no_closing_or_one_sheet_is_reported_never_a_zero_profit(self):
        self.assertEqual(rules.choose([], datetime(2026, 1, 1), datetime(2026, 2, 1))[2], "NO_SNAPSHOT")
        self.assertEqual(rules.choose(self.sheets, datetime(2025, 5, 1), datetime(2025, 6, 1))[2], "NO_CLOSING")
        self.assertEqual(rules.choose(self.sheets[:1], datetime(2026, 1, 1), datetime(2026, 3, 1))[2], "SAME_SNAPSHOT")

    def test_the_morning_sheet_of_each_boundary_day_or_the_latest_earlier_one_is_picked_in_any_input_order(self):
        opening, closing, problem = rules.choose(list(reversed(self.sheets)), datetime(2026, 1, 16), datetime(2026, 2, 1))
        self.assertEqual((opening["id"], closing["id"], problem), (2, 3, None))                 # no sheet on 16 Jan: the latest earlier one (15 Jan) stands for it
        self.assertEqual(rules.choose(list(reversed(self.sheets)), datetime(2026, 1, 16), datetime(2026, 1, 31))[2], "SAME_SNAPSHOT")


if __name__ == "__main__":
    unittest.main()
