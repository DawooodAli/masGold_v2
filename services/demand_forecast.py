"""Demand history, seasonality and a simple forecast from the shop's own sold grams per month (pure functions, no database, standard library only).

This is a small, explainable statistical module, NOT a machine-learning model and NOT a promise: it works on a handful of months, says how many observations stand behind every number and refuses
to produce one when the history is too short. A month is an entry (year, month, grams, bills); `grams` is None when the weights of that month are unknown (never guessed).

  complete months      all months except the one that contains `today` (it is not finished)
  trend base           the mean of the last TREND_MONTHS complete months with known grams, each divided by its own seasonal index when it has one
  seasonal index       the mean over the years of (that month's grams / the mean of its own year's months); needs SEASON_MIN years that each have YEAR_MIN_MONTHS usable months
  forecast             base x the target month's seasonal index (when it exists), else the base alone; confidence «متوسطة» with an index, «منخفضة» without
  elasticity           least-squares slope of the month-to-month change of ln(grams) on the change of ln(price), with Pearson r and n; needs ELASTICITY_MIN pairs. A statistical association
                       over a short history: it does not prove that the price caused the change."""
import math
from decimal import Decimal
from statistics import StatisticsError, correlation, linear_regression, mean

TREND_MONTHS = 6
SEASON_MIN = 2
YEAR_MIN_MONTHS = 6
ELASTICITY_MIN = 8
FORECAST_MONTHS = 3


def month_key(year, month):
    return year * 12 + (month - 1)


def from_key(key):
    return key // 12, key % 12 + 1


def monthly(daily, today):
    """daily: [{date, bills, bills_with_weight, grams}] -> ordered months [{year, month, bills, grams (None when no bill had a weight), complete}]."""
    months = {}
    for d in daily:
        key = month_key(d["date"].year, d["date"].month)
        m = months.setdefault(key, {"bills": 0, "with_weight": 0, "grams": Decimal(0)})
        m["bills"] += int(d["bills"])
        m["with_weight"] += int(d["bills_with_weight"])
        m["grams"] += Decimal(str(d["grams"]))
    current = month_key(today.year, today.month)
    out = []
    for key in sorted(months):
        year, month = from_key(key)
        m = months[key]
        out.append({"year": year, "month": month, "bills": m["bills"], "grams": m["grams"] if m["with_weight"] else None, "complete": key < current})
    return out


def weekly(daily, today):
    """daily -> {(iso year, iso week): {bills, grams (None when no bill had a weight), complete}}; the running week is incomplete."""
    weeks = {}
    for d in daily:
        iso = d["date"].isocalendar()
        w = weeks.setdefault((iso[0], iso[1]), {"bills": 0, "with_weight": 0, "grams": Decimal(0)})
        w["bills"] += int(d["bills"])
        w["with_weight"] += int(d["bills_with_weight"])
        w["grams"] += Decimal(str(d["grams"]))
    current = today.isocalendar()[:2]
    return {key: {"bills": w["bills"], "grams": w["grams"] if w["with_weight"] else None, "complete": key < current} for key, w in weeks.items()}


def year_values(periods, year, size):
    """The chart line of one year: `size` positions (12 months or 53 ISO weeks), each the grams of that period as a float, or None when the period is missing, unfinished or has unknown weights.
    `periods` is {(year, position): {grams, complete}}."""
    out = []
    for position in range(1, size + 1):
        p = periods.get((year, position))
        out.append(float(p["grams"]) if p and p["complete"] and p["grams"] is not None else None)
    return out


def usable(series):
    """The complete months whose grams are known."""
    return [m for m in series if m["complete"] and m["grams"] is not None]


def seasonal_indices(series):
    """{calendar month: (index, observations)}. A month's value is divided by the mean of ITS OWN year's usable months (a year needs at least YEAR_MIN_MONTHS of them), so the general growth of the shop
    from one year to the next does not pose as seasonality; the index of a calendar month is the mean of those ratios and needs at least SEASON_MIN years. {} when nothing can be measured."""
    rows = usable(series)
    by_year = {}
    for m in rows:
        by_year.setdefault(m["year"], []).append(m)
    ratios = {}
    for year, months in by_year.items():
        if len(months) < YEAR_MIN_MONTHS:
            continue
        average = mean(float(m["grams"]) for m in months)
        if average <= 0:
            continue
        for m in months:
            ratios.setdefault(m["month"], []).append(float(m["grams"]) / average)
    return {month: (mean(values), len(values)) for month, values in ratios.items() if len(values) >= SEASON_MIN}


def forecast(series, today, months=FORECAST_MONTHS):
    """[{year, month, grams, confidence, index, observations}] for the next `months` months after `today`'s month, or [] when fewer than TREND_MONTHS usable months exist.
    The base is the mean of the last TREND_MONTHS usable months AFTER removing their own seasonal index (a month with no index is taken as it is), so a summer peak does not inflate a winter forecast;
    the forecast of a month is that base x the month's index, or the base alone when the month has no index."""
    rows = usable(series)
    if len(rows) < TREND_MONTHS:
        return []
    indices = seasonal_indices(series)
    base = mean(float(m["grams"]) / indices[m["month"]][0] if m["month"] in indices and indices[m["month"]][0] > 0 else float(m["grams"]) for m in rows[-TREND_MONTHS:])
    out = []
    key = month_key(today.year, today.month)
    for step in range(1, months + 1):
        year, month = from_key(key + step)
        index, observed = indices.get(month, (None, 0))
        out.append({"year": year, "month": month, "grams": Decimal(str(round(base * (index if index is not None else 1), 1))), "confidence": "متوسطة" if index is not None else "منخفضة",
                    "index": None if index is None else round(index, 2), "observations": observed})
    return out


def elasticity(series, prices):
    """prices: {(year, month): average price}. Works on MONTH-TO-MONTH CHANGES (the log difference of the grams against the log difference of the price over consecutive months), so two series that merely
    grow together over the years do not look related. Returns {slope, r, n} (slope = the % change of the grams for a +1% change of the price) or None when fewer than ELASTICITY_MIN consecutive-month
    pairs exist or one of the series does not move at all."""
    by_key = {month_key(m["year"], m["month"]): m for m in usable(series)}
    xs, ys = [], []
    for key, m in by_key.items():
        before = by_key.get(key - 1)
        if before is None:
            continue
        price_now, price_before = prices.get(from_key(key)), prices.get(from_key(key - 1))
        if not price_now or not price_before or float(price_now) <= 0 or float(price_before) <= 0 or float(m["grams"]) <= 0 or float(before["grams"]) <= 0:
            continue
        xs.append(math.log(float(price_now)) - math.log(float(price_before)))
        ys.append(math.log(float(m["grams"])) - math.log(float(before["grams"])))
    if len(xs) < ELASTICITY_MIN or max(xs) - min(xs) < 1e-9 or max(ys) - min(ys) < 1e-9:      # too few pairs, or a price / grams series that does not move
        return None
    try:
        slope, _ = linear_regression(xs, ys)
        r = correlation(xs, ys)
    except StatisticsError:                                  # a constant series: nothing can be measured
        return None
    return {"slope": round(slope, 2), "r": round(r, 2), "n": len(xs)}


def strength(r):
    """A plain word for |r|: weak below 0.3, moderate below 0.6, otherwise strong."""
    r = abs(r)
    return "ضعيفة" if r < 0.3 else "متوسطة" if r < 0.6 else "قوية"
