"""Gold price indicators: every formula with hand-checkable numbers (pure functions, no database)."""
from datetime import date, timedelta
from decimal import Decimal as D
import unittest

from services import price_indicators as pi

START = date(2026, 9, 1)


def series(prices, spread=50, start=START):
    return [(start + timedelta(days=i), D(p), D(p) + spread) for i, p in enumerate(prices)]


RISING = series([6000 + 10 * i for i in range(40)])             # 40 days, +10 a day, latest 6390 (sell 6440)
FALLING = series([6400 - 10 * i for i in range(40)])             # latest 6010


class ChangeTests(unittest.TestCase):
    def test_change_against_the_last_price_on_or_before_the_cutoff(self):
        self.assertEqual(pi.change(RISING, 1, 1)[:2], (D("10"), D("0.16")))                    # 10 / 6380
        self.assertEqual(pi.change(RISING, 1, 7)[:2], (D("70"), D("1.11")))                    # latest 6390 against day 32 (6320): 70 / 6320
        self.assertEqual(pi.change(RISING, 2, 30)[0], D("300"))                                 # sell side, 30 days: 6440 - 6140

    def test_no_earlier_price_means_none(self):
        self.assertIsNone(pi.change(series([6000]), 1, 1))
        self.assertIsNone(pi.change(series([6000, 6010, 6020]), 1, 7))                          # nothing 7 days back


class MovingAverageTests(unittest.TestCase):
    def test_the_average_of_the_last_n_calendar_days_and_the_latest_against_it(self):
        average, percent, count = pi.moving_average(RISING, 1, 7)
        self.assertEqual((average, percent, count), (D("6360.00"), D("0.47"), 7))               # mean(6330..6390) = 6360; 30 / 6360
        self.assertEqual(pi.moving_average(RISING, 1, 30)[0], D("6245.00"))                     # mean(6100..6390)

    def test_too_few_observations_is_unavailable_never_invented(self):
        self.assertIsNone(pi.moving_average(series([6000, 6010]), 1, 7))
        self.assertIsNone(pi.moving_average(series([6000 + i for i in range(9)]), 1, 30))        # 9 < 10 observations


class RangeTests(unittest.TestCase):
    def test_position_between_the_low_and_the_high(self):
        self.assertEqual(pi.range_position(RISING, 1, 30)[:3], (D("1.000"), D("6100"), D("6390")))     # at the high
        self.assertEqual(pi.range_position(FALLING, 1, 30)[0], D("0.000"))                              # at the low
        middle = series([6000, 6100, 6200, 6300, 6400, 6200])
        self.assertEqual(pi.range_position(middle, 1, 30)[0], D("0.500"))                               # (6200 - 6000) / 400

    def test_a_flat_or_short_window_has_no_position(self):
        self.assertIsNone(pi.range_position(series([6000] * 10), 1, 30))
        self.assertIsNone(pi.range_position(series([6000, 6100, 6200]), 1, 30))


class VolatilityAndSpreadTests(unittest.TestCase):
    def test_volatility_is_the_standard_deviation_of_the_daily_percent_moves(self):
        zigzag = series([100, 110] * 4 + [100])                                                          # moves +10 % and -9.09 % four times each
        self.assertEqual(pi.volatility(zigzag, 1), D("9.55"))
        self.assertIsNone(pi.volatility(series([100, 110, 100]), 1))

    def test_spread_of_the_latest_day(self):
        self.assertEqual(pi.spread_percent(series([6000, 6200], spread=62)), D("1.00"))                  # 62 / 6200


class HintTests(unittest.TestCase):
    def test_buy_is_favourable_in_the_lower_quarter_and_sell_in_the_upper_quarter(self):
        self.assertEqual(pi.hint(FALLING)[0], pi.BUY_FAVOURABLE)
        self.assertEqual(pi.hint(RISING)[0], pi.SELL_FAVOURABLE)
        middle = series([6000, 6100, 6200, 6300, 6400, 6200])
        self.assertEqual(pi.hint(middle)[0], pi.NEUTRAL)

    def test_the_quarter_limits_are_inclusive_for_buy_and_for_sell(self):
        buy_edge = series([6000, 6400, 6400, 6400, 6400, 6100])                                          # position 0.25 exactly
        self.assertEqual(pi.hint(buy_edge)[0], pi.BUY_FAVOURABLE)
        sell_edge = series([6000, 6000, 6000, 6000, 6400, 6300])                                         # position 0.75 exactly
        self.assertEqual(pi.hint(sell_edge)[0], pi.SELL_FAVOURABLE)

    def test_unknown_when_the_range_cannot_be_measured(self):
        self.assertEqual(pi.hint(series([6000, 6010]))[0], pi.UNKNOWN)
        self.assertEqual(pi.hint(series([6000] * 10))[0], pi.UNKNOWN)


class MergeTests(unittest.TestCase):
    def test_legacy_days_stop_before_the_first_live_day(self):
        legacy = series([5000, 5010, 5020, 5030], start=date(2026, 9, 25))                               # 25..28 Sep
        live = series([6000, 6010], start=date(2026, 9, 27))                                              # 27..28 Sep
        merged = pi.merge_series(legacy, live)
        self.assertEqual([(r[0].day, int(r[1])) for r in merged], [(25, 5000), (26, 5010), (27, 6000), (28, 6010)])
        self.assertEqual(pi.merge_series(legacy, []), legacy)


if __name__ == "__main__":
    unittest.main()
