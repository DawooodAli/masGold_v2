"""Patterns inside the year, week and month of the shop's sold grams per DAY, and the effect of the seasons that move with the Islamic calendar (pure functions, standard library only).

Why: a calendar-month seasonality blurs the Eids, because the Islamic months move about eleven days earlier every Gregorian year. Here the Eid and Ramadan windows are placed on the real days of each
year (the tabular Islamic calendar: within a day or two of the observed date, so every window is at least a week wide) and the daily grams inside a window are compared with the daily grams AROUND it.

  daily values     only the days from the first day with a usable weight up to yesterday are used; a day without a bill inside that range is a real zero (the shop sold nothing) - provided
                   at least MIN_DENSITY of those days have a bill; a sparser history is treated as missing data and no pattern or uplift is produced
  weekday pattern  the mean grams of each weekday / the mean of all days; needs MIN_WEEKDAY_DAYS days of every weekday
  month pattern    the mean grams per day of days 1-7, 8-14, 15-21 and 22-end / the mean of all days; needs MIN_MONTH_PART_DAYS days of every part
  season uplift    for each year the mean daily grams of the season's window / the mean daily grams of the BASELINE (BASELINE_DAYS days before and after the window, leaving out every day that
                   belongs to any season window); a year whose window or baseline is not fully inside the usable days is skipped. The mean over the measured years is the typical uplift.
Everything says how many days / years stand behind it; with two years of history a season has at most two observations, so the figures are an indication, not a promise."""
import math
from datetime import date, timedelta
from statistics import mean

MIN_WEEKDAY_DAYS = 8
MIN_MONTH_PART_DAYS = 20
BASELINE_DAYS = 35
MIN_BASELINE_DAYS = 14
MIN_DENSITY = 0.4                    # at least this share of the days of the range must have a bill, otherwise the zeros are missing data rather than quiet days
WEEKDAYS = ("الاثنين", "الثلاثاء", "الأربعاء", "الخميس", "الجمعة", "السبت", "الأحد")           # date.weekday(): Monday = 0
MONTH_PARTS = ((1, 7, "من 1 إلى 7"), (8, 14, "من 8 إلى 14"), (15, 21, "من 15 إلى 21"), (22, 31, "من 22 إلى آخر الشهر"))
SEASONS = (("RAMADAN", "رمضان"), ("FITR_BEFORE", "قبل عيد الفطر (7 أيام)"), ("FITR_DURING", "أسبوع عيد الفطر"), ("FITR_AFTER", "بعد عيد الفطر (3 أسابيع)"),
           ("ADHA_BEFORE", "قبل عيد الأضحى (7 أيام)"), ("ADHA_DURING", "أسبوع عيد الأضحى"), ("ADHA_AFTER", "بعد عيد الأضحى (3 أسابيع)"), ("MOTHERS_DAY", "عيد الأم (الأسبوع قبله)"))
SEASON_NAMES = dict(SEASONS)


def islamic_to_date(year, month, day):
    """The Gregorian date of a day of the TABULAR (civil) Islamic calendar; within one or two days of the sighted date."""
    jd = day + math.ceil(29.5 * (month - 1)) + (year - 1) * 354 + (3 + 11 * year) // 30 + 1948440 - 1
    return date.fromordinal(int(jd - 1721425))


def season_windows(year):
    """[{key, name, start, end}] of the seasons whose window starts in the Gregorian `year`. Around each Eid three windows: the 7 days BEFORE it, the Eid week itself (holiday: the shop is often
    closed) and the 3 weeks AFTER it (the owner's data shows the demand peaks after the Eid, not before it); Ramadan ends where the pre-Eid week starts."""
    out = []
    for hijri in (year - 580, year - 579, year - 578):
        ramadan, fitr, adha = islamic_to_date(hijri, 9, 1), islamic_to_date(hijri, 10, 1), islamic_to_date(hijri, 12, 10)
        windows = [("RAMADAN", ramadan, fitr - timedelta(days=8))]
        for prefix, eid in (("FITR", fitr), ("ADHA", adha)):
            windows += [(f"{prefix}_BEFORE", eid - timedelta(days=7), eid - timedelta(days=1)), (f"{prefix}_DURING", eid, eid + timedelta(days=6)), (f"{prefix}_AFTER", eid + timedelta(days=7), eid + timedelta(days=27))]
        for key, start, end in windows:
            if start.year == year:
                out.append({"key": key, "name": SEASON_NAMES[key], "start": start, "end": end})
    mothers = date(year, 3, 21)
    out.append({"key": "MOTHERS_DAY", "name": SEASON_NAMES["MOTHERS_DAY"], "start": mothers - timedelta(days=7), "end": mothers})
    return sorted(out, key=lambda w: w["start"])


def daily_values(daily, today):
    """daily: [{date, bills_with_weight, grams}] -> (values {date: grams as float}, first, last) over the usable range, or (None, None, None) when no day has a weight.
    The range runs from the first day with a weight to YESTERDAY at most (today is not finished); days inside it without a bill are zeros."""
    known = {d["date"]: float(d["grams"]) for d in daily if int(d["bills_with_weight"]) > 0}
    if not known:
        return None, None, None
    first = min(known)
    last = min(max(known), today - timedelta(days=1))
    if last < first:
        return None, None, None
    return {first + timedelta(days=i): known.get(first + timedelta(days=i), 0.0) for i in range((last - first).days + 1)}, first, last


def dense(values):
    """True when enough days of the range have a bill for its zeros to mean 'quiet day'."""
    return bool(values) and sum(1 for v in values.values() if v > 0) / len(values) >= MIN_DENSITY


def weekday_pattern(values, first, last):
    """[{weekday, name, index, days}] for the seven weekdays, or [] when some weekday has fewer than MIN_WEEKDAY_DAYS days or the average is zero."""
    if not dense(values):
        return []
    overall = mean(values.values())
    buckets = {i: [v for d, v in values.items() if d.weekday() == i] for i in range(7)}
    if overall <= 0 or any(len(b) < MIN_WEEKDAY_DAYS for b in buckets.values()):
        return []
    return [{"weekday": i, "name": WEEKDAYS[i], "index": mean(buckets[i]) / overall, "days": len(buckets[i])} for i in range(7)]


def month_pattern(values, first, last):
    """[{label, index, days}] for the four parts of the month, or [] when a part has fewer than MIN_MONTH_PART_DAYS days or the average is zero."""
    if not dense(values):
        return []
    overall = mean(values.values())
    parts = [(label, [v for d, v in values.items() if low <= d.day <= high]) for low, high, label in MONTH_PARTS]
    if overall <= 0 or any(len(p) < MIN_MONTH_PART_DAYS for _, p in parts):
        return []
    return [{"label": label, "index": mean(p) / overall, "days": len(p)} for label, p in parts]


def season_uplifts(values, first, last, today):
    """{key: {name, years: [{year, window_avg, baseline_avg, uplift}], mean}} for every season; `mean` is None when no year could be measured."""
    result = {key: {"name": name, "years": [], "mean": None} for key, name in SEASONS}
    if not dense(values):
        return result
    windows = [w for y in range(first.year - 1, last.year + 2) for w in season_windows(y)]
    excluded = set()
    for w in windows:
        excluded.update(w["start"] + timedelta(days=i) for i in range((w["end"] - w["start"]).days + 1))
    for w in windows:
        days = [w["start"] + timedelta(days=i) for i in range((w["end"] - w["start"]).days + 1)]
        before = [w["start"] - timedelta(days=i) for i in range(1, BASELINE_DAYS + 1)]
        after = [w["end"] + timedelta(days=i) for i in range(1, BASELINE_DAYS + 1)]
        if days[0] < first or days[-1] > last or before[-1] < first or after[-1] > last:
            continue                                                                       # the window or its surroundings are not fully inside the usable days
        base = [values[d] for d in before + after if d not in excluded]
        if len(base) < MIN_BASELINE_DAYS or mean(base) <= 0:
            continue
        window_avg, baseline_avg = mean(values[d] for d in days), mean(base)
        result[w["key"]]["years"].append({"year": w["start"].year, "window_avg": window_avg, "baseline_avg": baseline_avg, "uplift": window_avg / baseline_avg})
    for entry in result.values():
        if entry["years"]:
            entry["mean"] = mean(y["uplift"] for y in entry["years"])
    return result


def next_season(today):
    """The next season whose window has not ended yet: {key, name, start, end, days_left} (days_left is 0 inside the window)."""
    for year in (today.year, today.year + 1):
        for w in season_windows(year):
            if w["end"] >= today:
                return {**w, "days_left": max(0, (w["start"] - today).days)}
    return None
