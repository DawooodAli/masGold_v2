"""Cleaning of the old gold price history: every rule with hand-checkable numbers (pure functions, no database)."""
from datetime import date
import unittest

from services.price_history_rules import clean_prices


def row(i, day, low, high):
    return {"id": i, "date": date(2026, 1, day), "low": low, "high": high}


class CleaningTests(unittest.TestCase):
    def clean(self, *rows):
        return clean_prices(rows)

    def test_the_last_entry_of_a_day_is_the_days_price_and_the_entries_are_counted(self):
        result = self.clean(row(1, 1, 6200, 6250), row(2, 1, 6210, 6260), row(3, 1, 6220, 6270), row(4, 2, 6230, 6280))
        self.assertEqual([(d["date"].day, d["shop_buy"], d["shop_sell"], d["entries"]) for d in result["daily"]], [(1, 6220, 6270, 3), (2, 6230, 6280, 1)])

    def test_zero_and_invalid_prices_are_rejected_with_a_reason(self):
        result = self.clean(row(1, 1, 6200, 6250), row(2, 2, 0, 6250), row(3, 3, 6200, None), row(4, 4, -5, 6250), row(5, 5, 6210, 6260))
        self.assertEqual([(r["id"], r["reason"]) for r in result["rejected"]], [(2, "صفر أو سعر غير صالح"), (3, "صفر أو سعر غير صالح"), (4, "صفر أو سعر غير صالح")])
        self.assertEqual([d["date"].day for d in result["daily"]], [1, 5])

    def test_a_buy_price_above_the_sell_price_is_swapped_and_flagged(self):
        result = self.clean(row(1, 1, 6200, 6250), row(2, 2, 6310, 6260), row(3, 3, 6210, 6270))
        day2 = next(d for d in result["daily"] if d["date"].day == 2)
        self.assertEqual((day2["shop_buy"], day2["shop_sell"], day2["swapped"]), (6260, 6310, True))
        self.assertEqual(result["swapped"], [{"id": 2, "date": date(2026, 1, 2), "low": 6310, "high": 6260}])        # the ORIGINAL values are reported

    def test_an_absurd_value_is_rejected_against_the_median_of_its_neighbours(self):
        rows = [row(i, i, 6200 + i, 6250 + i) for i in range(1, 10)] + [row(10, 10, 62050, 62500)]        # both prices typed ten times too big: a believable spread, so only the median catches it
        result = self.clean(*rows)
        self.assertEqual([(r["id"], r["reason"]) for r in result["rejected"]], [(10, "قيمة شاذة عن الأسعار المجاورة")])
        self.assertEqual(len(result["daily"]), 9)

    def test_two_unrelated_numbers_are_rejected_by_the_spread_even_after_the_swap(self):
        result = self.clean(row(1, 1, 6200, 6250), row(2, 2, 6240, 3270), row(3, 3, 6210, 6270))
        self.assertEqual([(r["id"], r["reason"]) for r in result["rejected"]], [(2, "فرق الشراء والبيع غير منطقي")])         # 3270 / 6240 would be a 91 % spread
        self.assertEqual([d["date"].day for d in result["daily"]], [1, 3])
        self.assertEqual(self.clean(row(1, 1, 3100, 3350))["counts"]["rejected"], 0)                                           # the widest REAL spread of the old table (8 %) is kept

    def test_a_real_move_is_not_an_outlier_a_price_may_double_over_the_years(self):
        slow = [row(i, i, 3800 + 40 * i, 3950 + 40 * i) for i in range(1, 25)]                                    # a steady climb: every step is small
        result = self.clean(*slow)
        self.assertEqual(result["counts"]["rejected"], 0)

    def test_counts_and_ordering_do_not_depend_on_the_input_order(self):
        rows = [row(3, 3, 6220, 6270), row(1, 1, 6200, 6250), row(2, 2, 6210, 6260)]
        self.assertEqual(clean_prices(rows)["daily"], clean_prices(sorted(rows, key=lambda r: r["id"]))["daily"])
        self.assertEqual(clean_prices(rows)["counts"], {"rows": 3, "days": 3, "rejected": 0, "swapped": 0})


if __name__ == "__main__":
    unittest.main()
