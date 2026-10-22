"""Sales history cleaning and the demand / seasonality / forecast module: every rule with hand-checkable numbers (pure functions, no database)."""
from datetime import date
from decimal import Decimal as D
import unittest

from services import demand_forecast as df
from services.sales_history_rules import clean_sales


def row(i, bill, day, weight):
    return {"id": i, "bill_id": bill, "date": day, "weight": weight}


def days(year, month, grams, bills=10):
    return {"date": date(year, month, 15), "bills": bills, "bills_with_weight": bills if grams is not None else 0, "grams": D(0) if grams is None else D(str(grams))}


class CleaningTests(unittest.TestCase):
    def test_one_bill_one_row_missing_weights_count_as_bills_but_add_no_grams(self):
        d1, d2 = date(2025, 2, 1), date(2025, 2, 2)
        result = clean_sales([row(1, "A", d1, 10), row(2, "A", d1, 12), row(3, "B", d1, None), row(4, "C", d2, 5), row(5, "D", d2, -1)])
        self.assertEqual([(d["date"], d["bills"], d["bills_with_weight"], d["grams"]) for d in result["daily"]], [(d1, 2, 1, D("12.000")), (d2, 2, 1, D("5.000"))])      # bill A: the highest id wins
        self.assertEqual(result["counts"], {"rows": 5, "bills": 4, "days": 2, "bills_without_weight": 2})

    def test_the_input_order_does_not_matter(self):
        rows = [row(2, "A", date(2025, 2, 1), 12), row(1, "A", date(2025, 2, 1), 10)]
        self.assertEqual(clean_sales(rows)["daily"][0]["grams"], D("12.000"))


class SeriesTests(unittest.TestCase):
    TODAY = date(2026, 12, 5)

    def pattern(self):
        """2024-01 .. 2025-12 without 2024-03: 100 g every month except February (200 g, both years)."""
        out = []
        for year in (2024, 2025):
            for month in range(1, 13):
                if (year, month) == (2024, 3):
                    continue
                out.append(days(year, month, 200 if month == 2 else 100))
        return out

    def test_monthly_marks_the_running_month_incomplete_and_unknown_weights_as_none(self):
        series = df.monthly([days(2024, 3, None), days(2026, 11, 50), days(2026, 12, 20)], self.TODAY)
        self.assertEqual([(m["year"], m["month"], m["grams"], m["complete"]) for m in series], [(2024, 3, None, True), (2026, 11, D("50"), True), (2026, 12, D("20"), False)])
        self.assertEqual(df.usable(series), [series[1]])

    def test_seasonal_index_is_relative_to_each_years_own_mean_and_needs_two_years(self):
        indices = df.seasonal_indices(df.monthly(self.pattern(), self.TODAY))
        mean_2024, mean_2025 = 1200 / 11, 1300 / 12                                       # 2024 has eleven months (no March): 200 + 10 x 100; 2025 has twelve: 200 + 11 x 100
        self.assertAlmostEqual(indices[2][0], (200 / mean_2024 + 200 / mean_2025) / 2, places=9)     # about 1.84
        self.assertEqual(indices[2][1], 2)
        self.assertAlmostEqual(indices[1][0], (100 / mean_2024 + 100 / mean_2025) / 2, places=9)     # about 0.92
        self.assertNotIn(3, indices)                                                       # March was seen in one year only: no index

    def test_general_growth_does_not_pose_as_seasonality(self):
        series = df.monthly([days(2024 + (i // 12), i % 12 + 1, 100 * 1.5 ** (i // 12)) for i in range(24)], self.TODAY)      # every month of the second year is 1.5 x the first: no season at all
        for index, _ in df.seasonal_indices(series).values():
            self.assertAlmostEqual(index, 1.0, places=9)

    def test_forecast_removes_the_season_from_the_base_and_puts_the_target_months_season_back(self):
        result = df.forecast(df.monthly(self.pattern(), self.TODAY), self.TODAY)
        self.assertEqual([(f["year"], f["month"]) for f in result], [(2027, 1), (2027, 2), (2027, 3)])
        indices = df.seasonal_indices(df.monthly(self.pattern(), self.TODAY))
        base = 100 / indices[7][0]                                                         # July-December are all 100 g and share the same index: the base is 100 / 0.92 = 108.7
        self.assertEqual(result[0]["grams"], D("100.0"))                                  # January: 108.7 x 0.92 = 100 g (a plain January)
        self.assertEqual(result[1]["grams"], D("200.0"))                                  # February: 108.7 x 1.84 = 200 g (the usual double February)
        self.assertEqual((result[0]["confidence"], result[1]["confidence"], result[2]["confidence"]), ("متوسطة", "متوسطة", "منخفضة"))
        self.assertEqual((result[2]["grams"], result[2]["index"]), (D(str(round(base, 1))), None))      # March was seen once: the de-seasonalised base alone

    def test_too_short_a_history_gives_no_forecast(self):
        self.assertEqual(df.forecast(df.monthly([days(2026, 5, 100), days(2026, 6, 100)], self.TODAY), self.TODAY), [])


class ElasticityTests(unittest.TestCase):
    STEPS = [1.02, 0.97, 1.04, 0.99, 1.03, 0.96, 1.05, 1.0, 0.98, 1.02, 1.01, 0.99]

    def build(self, count, exponent, moving=True):
        series, prices, price = [], {}, 1000.0
        for i in range(count):
            year, month = df.from_key(df.month_key(2025, 1) + i)
            price *= self.STEPS[i] if moving else 1.0
            prices[(year, month)] = D(str(round(price, 4)))
            series.append(days(year, month, round(5000 * price ** exponent, 3)))
        return df.monthly(series, date(2030, 1, 1)), prices

    def test_a_perfect_power_law_returns_its_exponent_and_a_correlation_of_minus_one(self):
        series, prices = self.build(10, -0.5)
        result = df.elasticity(series, prices)
        self.assertAlmostEqual(result["slope"], -0.5, places=1)
        self.assertAlmostEqual(result["r"], -1.0, places=1)
        self.assertEqual(result["n"], 9)                                                      # ten months give nine consecutive pairs
        self.assertEqual(df.strength(result["r"]), "قوية")

    def test_too_few_months_or_a_constant_series_measure_nothing(self):
        series, prices = self.build(8, -0.5)                                                  # seven month-to-month pairs: too few
        self.assertIsNone(df.elasticity(series, prices))
        series, prices = self.build(10, -0.5, moving=False)
        self.assertIsNone(df.elasticity(series, prices))                                      # a price that never moves: no slope
        self.assertEqual((df.strength(0.1), df.strength(0.4), df.strength(-0.9)), ("ضعيفة", "متوسطة", "قوية"))


class TrendIsNotAssociationTests(unittest.TestCase):
    def test_two_series_that_only_grow_together_show_no_relation_between_their_changes(self):
        series, prices = [], {}
        steps = [1.02, 0.97, 1.04, 0.99, 1.03, 0.96, 1.05, 1.0, 0.98, 1.02, 1.01]
        price = 1000.0
        for i, step in enumerate(steps):
            year, month = df.from_key(df.month_key(2025, 1) + i)
            price *= step
            prices[(year, month)] = D(str(round(price, 4)))
            series.append(days(year, month, round(500 * 1.03 ** i * [1.05, 1.0, 0.95, 1.0][i % 4], 3)))     # grams grow steadily with a pattern unrelated to the price
        result = df.elasticity(df.monthly(series, date(2030, 1, 1)), prices)
        self.assertIsNotNone(result)
        self.assertEqual(df.strength(result["r"]), "ضعيفة")


class WeeklyTests(unittest.TestCase):
    def test_iso_weeks_group_days_and_the_running_week_is_incomplete(self):
        def d(day, grams):
            return {"date": day, "bills": 1, "bills_with_weight": 1, "grams": D(str(grams))}
        daily = [d(date(2025, 12, 29), 5), d(date(2026, 1, 1), 10), d(date(2026, 1, 5), 20), d(date(2026, 1, 7), 30)]
        weeks = df.weekly(daily, date(2026, 1, 6))
        self.assertEqual(weeks[(2026, 1)]["grams"], D("15"))                    # 29 Dec 2025 belongs to ISO week 1 of 2026: 5 + 10
        self.assertEqual((weeks[(2026, 2)]["grams"], weeks[(2026, 2)]["complete"]), (D("50"), False))
        self.assertTrue(weeks[(2026, 1)]["complete"])
        values = df.year_values(weeks, 2026, 53)
        self.assertEqual((values[0], values[1], values[2]), (15.0, None, None))   # the unfinished week and the empty ones are gaps, never zeros

    def test_unknown_weights_are_a_gap(self):
        weeks = df.weekly([{"date": date(2024, 3, 6), "bills": 4, "bills_with_weight": 0, "grams": D(0)}], date(2026, 1, 1))
        self.assertIsNone(weeks[(2024, 10)]["grams"])
        self.assertEqual(df.year_values(weeks, 2024, 53)[9], None)


if __name__ == "__main__":
    unittest.main()
