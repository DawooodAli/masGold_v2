"""Weekday / month-part patterns and the Eid / Ramadan / Mother's Day windows: every rule with hand-checkable synthetic days (pure functions, no database)."""
from datetime import date, timedelta
from decimal import Decimal as D
import unittest

from services import season_patterns as sp

START, END, TODAY = date(2025, 2, 1), date(2026, 8, 31), date(2026, 9, 5)


def build(value_of):
    """One 'daily' row per day from START to END; value_of(day) = the grams of that day (0 = no bill: the row is left out)."""
    return [{"date": d, "bills": 1, "bills_with_weight": 1, "grams": D(str(value_of(d)))} for d in (START + timedelta(days=i) for i in range((END - START).days + 1)) if value_of(d) > 0]


class CalendarTests(unittest.TestCase):
    def test_the_tabular_islamic_calendar_is_within_a_day_of_the_observed_dates(self):
        known = ((1446, 9, 1, date(2025, 3, 1)), (1446, 10, 1, date(2025, 3, 30)), (1446, 12, 10, date(2025, 6, 6)), (1447, 9, 1, date(2026, 2, 18)), (1447, 10, 1, date(2026, 3, 20)), (1447, 12, 10, date(2026, 5, 27)))
        for y, m, d, observed in known:
            self.assertLessEqual(abs((sp.islamic_to_date(y, m, d) - observed).days), 1, (y, m, d))

    def test_the_windows_of_a_year_and_their_shape(self):
        by_key = {w["key"]: w for w in sp.season_windows(2026)}
        self.assertEqual(set(by_key), {key for key, _ in sp.SEASONS})
        self.assertEqual((by_key["MOTHERS_DAY"]["start"], by_key["MOTHERS_DAY"]["end"]), (date(2026, 3, 14), date(2026, 3, 21)))
        fitr = {part: by_key[f"FITR_{part}"] for part in ("BEFORE", "DURING", "AFTER")}
        self.assertEqual([(w["end"] - w["start"]).days + 1 for w in fitr.values()], [7, 7, 21])           # a week before, the Eid week, three weeks after
        self.assertEqual(fitr["BEFORE"]["end"] + timedelta(days=1), fitr["DURING"]["start"])
        self.assertEqual(fitr["DURING"]["end"] + timedelta(days=1), fitr["AFTER"]["start"])
        self.assertLess(by_key["RAMADAN"]["end"], fitr["BEFORE"]["start"])                                  # Ramadan ends where the pre-Eid week starts: no shared day
        self.assertLess(by_key["ADHA_BEFORE"]["start"], by_key["ADHA_DURING"]["start"])
        self.assertEqual(len({w["start"].year for w in sp.season_windows(2030)}), 1)


class PatternTests(unittest.TestCase):
    def test_a_weekday_pattern_needs_every_weekday_to_be_seen_enough_and_closed_days_are_zeros(self):
        daily = build(lambda d: 0 if d.weekday() == 4 else 10)                                              # the shop is closed on Fridays
        values, first, last = sp.daily_values(daily, TODAY)
        self.assertEqual((first, last), (START, END))                                                       # up to yesterday at most
        pattern = {p["weekday"]: p for p in sp.weekday_pattern(values, first, last)}
        overall = sum(values.values()) / len(values)                                                        # the range is not a whole number of weeks: use the real mean of its days
        self.assertAlmostEqual(pattern[0]["index"], 10 / overall, places=9)
        self.assertEqual(pattern[4]["index"], 0.0)
        self.assertEqual(pattern[4]["name"], "الجمعة")
        short = sp.daily_values(build(lambda d: 10 if d < START + timedelta(days=20) else 0), TODAY)           # three weeks of days only
        self.assertEqual(sp.weekday_pattern(*short), [])

    def test_a_month_pattern_compares_the_four_parts_of_the_month(self):
        daily = build(lambda d: 20 if d.day >= 22 else 10)
        values, first, last = sp.daily_values(daily, TODAY)
        pattern = sp.month_pattern(values, first, last)
        self.assertEqual([p["label"] for p in pattern], ["من 1 إلى 7", "من 8 إلى 14", "من 15 إلى 21", "من 22 إلى آخر الشهر"])
        self.assertAlmostEqual(pattern[3]["index"] / pattern[0]["index"], 2.0, places=9)                    # the end of the month sells twice as much
        self.assertEqual(sp.month_pattern(None, None, None), [])

    def test_a_sparse_history_is_missing_data_not_quiet_days(self):
        daily = build(lambda d: 10 if d.day == 15 else 0)                                                   # one bill a month: the zeros between are unknown, not real quiet days
        values, first, last = sp.daily_values(daily, TODAY)
        self.assertFalse(sp.dense(values))
        self.assertEqual((sp.weekday_pattern(values, first, last), sp.month_pattern(values, first, last)), ([], []))
        self.assertTrue(all(v["mean"] is None for v in sp.season_uplifts(values, first, last, TODAY).values()))

    def test_the_running_day_and_a_history_without_weights_give_nothing(self):
        self.assertEqual(sp.daily_values([], TODAY), (None, None, None))
        unknown = [{"date": date(2025, 5, 1), "bills": 3, "bills_with_weight": 0, "grams": D(0)}]
        self.assertEqual(sp.daily_values(unknown, TODAY), (None, None, None))
        values, first, last = sp.daily_values(build(lambda d: 10), date(2026, 8, 20))
        self.assertEqual(last, date(2026, 8, 19))


class UpliftTests(unittest.TestCase):
    def test_uplift_compares_the_window_with_the_days_around_it_and_ignores_other_seasons(self):
        windows = {(w["key"], w["start"].year): w for y in (2025, 2026) for w in sp.season_windows(y)}

        def grams(day):
            for (key, _year), w in windows.items():
                if w["start"] <= day <= w["end"] and key in ("FITR_BEFORE", "ADHA_BEFORE"):
                    return 30                                                                                # the week before an Eid sells three times the normal 10 g
            return 10
        values, first, last = sp.daily_values(build(grams), TODAY)
        result = sp.season_uplifts(values, first, last, TODAY)
        for key in ("FITR_BEFORE", "ADHA_BEFORE"):
            self.assertEqual([round(y["uplift"], 6) for y in result[key]["years"]], [3.0, 3.0], key)       # 2025 and 2026, both measured
            self.assertAlmostEqual(result[key]["mean"], 3.0, places=6)
        for key in ("RAMADAN", "FITR_DURING", "FITR_AFTER", "ADHA_DURING", "ADHA_AFTER"):
            self.assertAlmostEqual(result[key]["mean"], 1.0, places=6, msg=key)                            # nothing special happens there, and the 30 g weeks are not in the baseline
        mothers = {y["year"]: y["uplift"] for y in result["MOTHERS_DAY"]["years"]}
        self.assertAlmostEqual(mothers[2025], 1.0, places=6)
        self.assertAlmostEqual(mothers[2026], 2.5, places=6)                                                # 14-21 March 2026: six days fall in the pre-Eid week (30 g) and two in the Eid week (10 g): (6 x 30 + 2 x 10) / 8 = 25 g against 10 g

    def test_a_year_whose_window_or_surroundings_are_not_in_the_usable_days_is_skipped(self):
        values, first, last = sp.daily_values(build(lambda d: 10 if d >= date(2026, 1, 1) else 0), TODAY)    # history starts on 1 January 2026
        result = sp.season_uplifts(values, first, last, TODAY)
        self.assertEqual([y["year"] for y in result["RAMADAN"]["years"]], [2026])                          # Ramadan 2025 is before the data
        self.assertEqual(result["ADHA_AFTER"]["years"][0]["year"], 2026)
        none = sp.season_uplifts(None, None, None, TODAY)
        self.assertTrue(all(v["mean"] is None and v["years"] == [] for v in none.values()))

    def test_the_next_season_counts_the_days_left(self):
        upcoming = sp.next_season(date(2026, 5, 1))
        self.assertEqual(upcoming["key"], "ADHA_BEFORE")
        self.assertEqual(upcoming["days_left"], (upcoming["start"] - date(2026, 5, 1)).days)
        inside = sp.next_season(upcoming["start"] + timedelta(days=2))
        self.assertEqual((inside["key"], inside["days_left"]), ("ADHA_BEFORE", 0))                          # inside the window: zero days left
        self.assertIsNotNone(sp.next_season(date(2026, 12, 30)))                                            # the next year is searched too


if __name__ == "__main__":
    unittest.main()
