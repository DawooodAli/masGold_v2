"""Gold price indicators from the stored DAILY prices (pure functions, no database, no network).

These are INDICATIVE figures computed from the shop's own price history with standard formulas; they are not a forecast and not financial advice. A figure that needs more history than
available is reported as unavailable (None), never invented. A series is a list of (date, shop_buy, shop_sell) in ascending date order, one entry per day (the last price of the day).

Indicators (all on the SHOP price the owner actually trades at: SHOP_BUY = what the shop pays, SHOP_SELL = what the shop charges):
  change over N days   latest vs the last price on or before (latest date - N days), absolute and percent;
  moving average       mean of the daily prices of the last N CALENDAR days (needs MIN_POINTS observations), and the latest price against it in percent;
  range position       where the latest price stands between the lowest and the highest daily price of the last N days: 0 = at the low, 1 = at the high (needs MIN_RANGE_POINTS observations);
  volatility           standard deviation of the percent change between consecutive observations of the last 30 days (needs MIN_VOLATILITY_POINTS observations);
  spread               (sell - buy) / buy of the latest day;
  hint                 BUY_FAVOURABLE when the SHOP_BUY price is in the lower quarter of its last HINT_DAYS range, SELL_FAVOURABLE when the SHOP_SELL price is in the upper quarter,
                       NEUTRAL otherwise, UNKNOWN when the range cannot be measured."""
from datetime import timedelta
from decimal import Decimal, ROUND_HALF_UP
from statistics import pstdev

HINT_DAYS = 90
QUARTER = Decimal("0.25")
MIN_POINTS = {7: 3, 14: 5, 30: 10}              # observations a moving average of N days needs
MIN_RANGE_POINTS = 5
MIN_VOLATILITY_POINTS = 8
RANGE_WINDOWS = (30, 90, 365)
CHANGE_WINDOWS = (1, 7, 30)

BUY_FAVOURABLE, SELL_FAVOURABLE, NEUTRAL, UNKNOWN = "BUY_FAVOURABLE", "SELL_FAVOURABLE", "NEUTRAL", "UNKNOWN"


def _pct(delta, base):
    return (Decimal(delta) / Decimal(base) * 100).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP) if base else None


def window(series, days, end=None):
    """The entries whose date is within the last `days` calendar days up to the latest date (inclusive)."""
    if not series:
        return []
    last = end or series[-1][0]
    first = last - timedelta(days=days - 1)
    return [row for row in series if first <= row[0] <= last]


def change(series, side, days):
    """(absolute, percent, from_date) of the latest `side` price (1 = buy, 2 = sell) against the last price on or before latest date - days; None when there is none."""
    if len(series) < 2:
        return None
    latest = series[-1]
    cutoff = latest[0] - timedelta(days=days)
    earlier = [r for r in series[:-1] if r[0] <= cutoff] if days > 1 else series[-2:-1]
    if not earlier:
        return None
    base = earlier[-1]
    delta = Decimal(latest[side]) - Decimal(base[side])
    return delta, _pct(delta, base[side]), base[0]


def moving_average(series, side, days):
    """(average, latest vs average in percent, observations) or None when fewer than MIN_POINTS[days] observations exist in the window."""
    rows = window(series, days)
    if len(rows) < MIN_POINTS.get(days, 3):
        return None
    average = sum((Decimal(r[side]) for r in rows), Decimal(0)) / len(rows)
    return average.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP), _pct(Decimal(series[-1][side]) - average, average), len(rows)


def range_position(series, side, days):
    """(position 0..1, low, high, observations) or None when the window holds fewer than MIN_RANGE_POINTS observations or the range is a single price."""
    rows = window(series, days)
    if len(rows) < MIN_RANGE_POINTS:
        return None
    values = [Decimal(r[side]) for r in rows]
    low, high = min(values), max(values)
    if high == low:
        return None
    return ((Decimal(series[-1][side]) - low) / (high - low)).quantize(Decimal("0.001"), rounding=ROUND_HALF_UP), low, high, len(rows)


def volatility(series, side, days=30):
    """Standard deviation (percent) of the percent change between consecutive observations of the last `days` days, or None."""
    rows = window(series, days)
    if len(rows) < MIN_VOLATILITY_POINTS:
        return None
    moves = [(Decimal(b[side]) - Decimal(a[side])) / Decimal(a[side]) * 100 for a, b in zip(rows, rows[1:]) if a[side]]
    if len(moves) < MIN_VOLATILITY_POINTS - 1:
        return None
    return Decimal(str(pstdev([float(m) for m in moves]))).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def spread_percent(series):
    latest = series[-1]
    return _pct(Decimal(latest[2]) - Decimal(latest[1]), latest[1])


def hint(series):
    """(code, buy position, sell position) over the last HINT_DAYS days."""
    buy = range_position(series, 1, HINT_DAYS)
    sell = range_position(series, 2, HINT_DAYS)
    if buy is None or sell is None:
        return UNKNOWN, None, None
    if buy[0] <= QUARTER:
        return BUY_FAVOURABLE, buy[0], sell[0]
    if sell[0] >= 1 - QUARTER:
        return SELL_FAVOURABLE, buy[0], sell[0]
    return NEUTRAL, buy[0], sell[0]


def merge_series(legacy, recent):
    """The long series of ONE karat: the legacy days up to the day BEFORE the first recent (live) price, then every live day. Both inputs are (date, buy, sell) ascending."""
    if not recent:
        return list(legacy)
    first_live = recent[0][0]
    return [r for r in legacy if r[0] < first_live] + list(recent)
