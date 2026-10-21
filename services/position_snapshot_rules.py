"""Position snapshots (pure functions, no database): the stocktaking sheets behind the gold profit report.

A snapshot records, at one moment, the shop's gold in 21K-equivalent grams by part (the consolidated gold position: shop, market custody, intermediary holdings, BOOK trips; plus the NET
supplier gold) and the cash / bank balances in EGP. The profit of a period is the difference between the closing and the opening snapshot; nothing is recomputed historically.

Supplier gold is SIGNED from the shop's point of view: the supplier ledger is positive when the shop OWES the supplier, so the shop position is minus its 21K equivalent
(gold the suppliers hold for the shop counts as shop gold, gold the shop owes is subtracted). Every part is quantized once (0.001 g, ROUND_HALF_UP) and the total is the exact sum of the
quantized parts. A gram whose assay is unknown is never inferred: it is kept apart in `unknown_grams` and is not in the total."""
from decimal import ROUND_HALF_UP, Decimal, localcontext

STEP = Decimal("0.001")
MONEY_STEP = Decimal("0.01")
PARTS = ("gold_shop_eq21", "gold_custody_eq21", "gold_intermediary_eq21", "gold_book_eq21", "gold_suppliers_eq21")


def _dec(value):
    return Decimal(str(value if value is not None else 0))


def _q(value):
    return _dec(value).quantize(STEP, rounding=ROUND_HALF_UP)


def _money(value):
    return _dec(value).quantize(MONEY_STEP, rounding=ROUND_HALF_UP)


def suppliers_eq21(rows):
    """rows: [{karat_value, grams}] = the supplier gold ledger sums in each supplier's OWN karat (positive = the shop owes). Returns the SIGNED shop position in 21K equivalent."""
    with localcontext() as context:
        context.prec = 50
        owed = sum((_dec(r["grams"]) * _dec(r["karat_value"]) / Decimal(21) for r in rows), Decimal(0))
    return _q(-owed)


def build_snapshot(summary, supplier_rows, cash, bank):
    """summary = GoldPositionService.position()["summary"]; returns the snapshot figures (parts, total, unknown grams, cash, bank)."""
    parts = {"gold_shop_eq21": _q(summary["shop_eq21"]), "gold_custody_eq21": _q(summary["custody_eq21"]),
             "gold_intermediary_eq21": _q(summary["intermediary_eq21"]), "gold_book_eq21": _q(summary["book_eq21"]),
             "gold_suppliers_eq21": suppliers_eq21(supplier_rows)}
    return {**parts, "gold_total_eq21": sum(parts.values(), Decimal(0)), "unknown_grams": _q(summary.get("unknown_grams") or 0),
            "cash_egp": _money(cash), "bank_egp": _money(bank)}


def opening_snapshot(gold_shop_eq21, gold_suppliers_eq21, cash, bank):
    """The owner's initial balance: the gold of the shop and the NET supplier gold (signed: + the suppliers hold it for the shop, - the shop owes it), cash and bank. Other parts start at zero."""
    parts = {"gold_shop_eq21": _q(gold_shop_eq21), "gold_custody_eq21": Decimal("0.000"), "gold_intermediary_eq21": Decimal("0.000"), "gold_book_eq21": Decimal("0.000"),
             "gold_suppliers_eq21": _q(gold_suppliers_eq21)}
    return {**parts, "gold_total_eq21": sum(parts.values(), Decimal(0)), "unknown_grams": Decimal("0.000"), "cash_egp": _money(cash), "bank_egp": _money(bank)}


def _reference(ordered, boundary):
    """The sheet that stands for the state at a midnight boundary: the first sheet taken during that calendar day (the 'morning' sheet), otherwise the latest sheet taken before it."""
    from datetime import timedelta
    same_day = [r for r in ordered if boundary <= r["taken_at"] < boundary + timedelta(days=1)]
    if same_day:
        return same_day[0]
    before = [r for r in ordered if r["taken_at"] < boundary]
    return before[-1] if before else None


def choose(snapshots, start, end):
    """snapshots: rows with id, taken_at (datetime) and the figures, any order; `start` is the first midnight of the period and `end` the midnight AFTER its last day. Each boundary is stood for by
    the first sheet of that calendar day, otherwise the latest earlier sheet (see `_reference`). When nothing exists before the period starts the earliest sheet is used and flagged
    (`before_first`). Returns (opening, closing, problem) with problem None, 'NO_SNAPSHOT', 'NO_CLOSING' or 'SAME_SNAPSHOT' (the two references are one sheet: no movement can be measured)."""
    ordered = sorted(snapshots, key=lambda r: (r["taken_at"], int(r["id"])))
    if not ordered:
        return None, None, "NO_SNAPSHOT"
    reference = _reference(ordered, start)
    opening = dict(reference, before_first=False) if reference is not None else dict(ordered[0], before_first=True)
    closing = _reference(ordered, end)
    if closing is None:
        return opening, None, "NO_CLOSING"
    if int(closing["id"]) == int(opening["id"]):
        return opening, closing, "SAME_SNAPSHOT"
    return opening, closing, None


def profit(opening, closing):
    """Closing minus opening, part by part (gold in 21K grams, cash and bank in EGP). A positive gold total is a gain."""
    out = {key: _dec(closing[key]) - _dec(opening[key]) for key in (*PARTS, "gold_total_eq21", "cash_egp", "bank_egp")}
    out["gold_total_eq21"] = _q(out["gold_total_eq21"])
    return out
