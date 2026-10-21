"""Cleaning of the OLD shop's gold price history (golddb.goldprice: id, low, high, date) before it is imported as a daily history (pure functions, no database).

The old table was typed by hand: several entries per day, zeros, a few rows with the buy price above the sell price, and an absurd value (a typing slip). The rules (owner-approved):

1. a row with a zero / negative / non-integer price is REJECTED («صفر أو غير صالح»);
2. a row whose buy price is above its sell price is SWAPPED (the two were typed in the wrong boxes) and flagged;
3. a row whose sell price is more than SPREAD_LIMIT (15 %) above its buy price AFTER the swap is REJECTED («فرق الشراء والبيع غير منطقي»: the two numbers do not belong together);
4. a row whose buy or sell price is more than OUTLIER_FACTOR times above, or below 1 / OUTLIER_FACTOR of, the MEDIAN of the nearest accepted rows (the WINDOW rows around it) is REJECTED («قيمة شاذة»);
5. the LAST accepted entry of each day (highest id) is the day's price; the number of entries of the day is kept.

`low` is the price the shop PAYS (SHOP_BUY) and `high` the price the shop CHARGES (SHOP_SELL); the whole table is karat 21 (owner-confirmed; the old values equal today's 21K prices).
Nothing here reads or writes a database."""
from statistics import median

OUTLIER_FACTOR = 3
SPREAD_LIMIT = 0.15                          # the real buy / sell spread is under 9 % in the whole old table; more than 15 % means two unrelated numbers
WINDOW = 5                                   # rows on each side of a row whose median is the reference


def _int_price(value):
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 and float(value) == number else None


def clean_prices(rows):
    """rows: iterable of {"id", "date", "low", "high"}. Returns {"daily": [...], "rejected": [...], "swapped": [...], "counts": {...}} with
    daily    = [{"date", "shop_buy", "shop_sell", "entries", "swapped"}] ordered by date,
    rejected = [{"id", "date", "low", "high", "reason"}]   # rejected rows are listed in the order they were found, swapped = [{"id", "date", "low", "high"}] (the original values)."""
    ordered = sorted(({"id": int(r["id"]), "date": r["date"], "low": r["low"], "high": r["high"]} for r in rows), key=lambda r: (r["date"], r["id"]))
    rejected, valid = [], []
    for r in ordered:
        low, high = _int_price(r["low"]), _int_price(r["high"])
        if low is None or high is None:
            rejected.append({**r, "reason": "صفر أو سعر غير صالح"})
            continue
        valid.append({"id": r["id"], "date": r["date"], "low": low, "high": high, "swapped": False, "original": (r["low"], r["high"])})
    for r in valid:
        if r["low"] > r["high"]:
            r["low"], r["high"], r["swapped"] = r["high"], r["low"], True
    kept = []
    for r in valid:
        if (r["high"] - r["low"]) > SPREAD_LIMIT * r["low"]:
            rejected.append({"id": r["id"], "date": r["date"], "low": r["original"][0], "high": r["original"][1], "reason": "فرق الشراء والبيع غير منطقي"})
        else:
            kept.append(r)
    valid = kept
    accepted, swapped = [], []
    for index, r in enumerate(valid):
        around = valid[max(0, index - WINDOW):index] + valid[index + 1:index + 1 + WINDOW]
        outlier = False
        if around:
            m_low, m_high = median(x["low"] for x in around), median(x["high"] for x in around)
            outlier = (r["low"] > OUTLIER_FACTOR * m_low or r["low"] * OUTLIER_FACTOR < m_low or r["high"] > OUTLIER_FACTOR * m_high or r["high"] * OUTLIER_FACTOR < m_high)
        if outlier:
            rejected.append({"id": r["id"], "date": r["date"], "low": r["original"][0], "high": r["original"][1], "reason": "قيمة شاذة عن الأسعار المجاورة"})
            continue
        accepted.append(r)
        if r["swapped"]:
            swapped.append({"id": r["id"], "date": r["date"], "low": r["original"][0], "high": r["original"][1]})
    by_day = {}
    for r in accepted:
        day = by_day.setdefault(r["date"], {"date": r["date"], "entries": 0})
        day.update(shop_buy=r["low"], shop_sell=r["high"], swapped=r["swapped"])          # the last entry of the day wins (rows are in id order)
        day["entries"] += 1
    daily = [by_day[d] for d in sorted(by_day)]
    return {"daily": daily, "rejected": rejected, "swapped": swapped,
            "counts": {"rows": len(ordered), "days": len(daily), "rejected": len(rejected), "swapped": len(swapped)}}
