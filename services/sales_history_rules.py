"""Cleaning of the OLD shop's sales history (golddb.employeebill: id, employee_name, bill_id, average, weight, date) before it is imported as a DAILY history (pure functions, no database).

The old table has one row per bill (a few bills appear twice) with the bill's weight in grams (no karat). Rules (all mechanical, no judgement):
1. several rows of one `bill_id` are ONE bill: the row with the highest id wins (a later correction replaces an earlier row);
2. a bill without a weight (NULL, zero or negative) still counts as a bill but adds no grams (the weights of 2024 are unknown, never guessed);
3. per day: `bills` = distinct bills, `bills_with_weight` = those with a usable weight, `grams` = the sum of their weights.
Nothing here reads or writes a database."""
from datetime import date
from decimal import Decimal


def _weight(value):
    try:
        number = Decimal(str(value))
    except Exception:
        return None
    return number if number.is_finite() and number > 0 else None


def clean_sales(rows):
    """rows: iterable of {"id", "bill_id", "date" (date), "weight"}. Returns {"daily": [{date, bills, bills_with_weight, grams}], "counts": {...}} ordered by date."""
    rows = list(rows)
    last = {}
    for r in sorted(rows, key=lambda r: int(r["id"])):
        last[str(r["bill_id"])] = r                                                    # the highest id of a bill wins
    days = {}
    for r in last.values():
        day = r["date"] if isinstance(r["date"], date) else date.fromisoformat(str(r["date"]))
        d = days.setdefault(day, {"date": day, "bills": 0, "bills_with_weight": 0, "grams": Decimal("0.000")})
        d["bills"] += 1
        weight = _weight(r["weight"])
        if weight is not None:
            d["bills_with_weight"] += 1
            d["grams"] += weight
    daily = [days[k] for k in sorted(days)]
    for d in daily:
        d["grams"] = d["grams"].quantize(Decimal("0.001"))
    return {"daily": daily, "counts": {"rows": len(rows), "bills": len(last), "days": len(daily),
                                       "bills_without_weight": sum(d["bills"] - d["bills_with_weight"] for d in daily)}}
