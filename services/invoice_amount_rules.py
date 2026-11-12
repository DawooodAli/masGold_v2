"""The corrected amount of an invoice (pure, no database).

Owner 2026-11-12: a settlement correction (FC) may record an amount above or below the invoice's recorded total (the amount was typed wrong, the customer actually paid
less / more). The finalized invoice and its lines stay exactly as stored; the reports and the home page read the LATEST settlement of the invoice instead:

    delta = settled total of the latest FC of the invoice - the settlement before its first FC (the legs + the customer-account part stored in that FC's `before`)

The delta belongs to the side the money moves on: money IN (a sale, or a mixed invoice the customer pays) -> the SALE lines, as making (the gold value is fixed by grams and
price, so only the making absorbs it); money OUT (a scrap purchase, or a mixed invoice the shop pays) -> the SCRAP-PURCHASE lines, as value paid (a bigger discount when the
shop paid less). It is spread by weight, rounded to 0.01 once per line, and the last line takes the remainder so the shares add up to the delta exactly.
Only FCs of the invoice itself count (Exchange / Return corrections are their own documents)."""
import json
from decimal import Decimal, ROUND_HALF_UP

SALE_TYPES = frozenset({"SELL_INVENTORY", "SELL_UNIDENTIFIED", "SELL_OLD_GOLD", "SELL_RAFAYEA"})
BUY_TYPE = "BUY_OLD_GOLD"
ZERO = Decimal("0.00")
CENT = Decimal("0.01")


def _d(value):
    return ZERO if value in (None, "") else Decimal(str(value))


def _json(value):
    if isinstance(value, (bytes, bytearray)):
        value = value.decode("utf-8")
    if isinstance(value, str):
        return json.loads(value) if value.strip() else {}
    return value or {}


def invoice_changes(rows):
    """rows: every SETTLEMENT_CORRECTION of source INVOICE (invoice_id, chain_sequence, money_direction, settled_total, before_json, money_posted, account_posted).
    -> {invoice_id: {"direction": IN / OUT, "delta": latest settled - original, "money": signed money the FCs posted, "account": signed customer-account delta they posted}}"""
    chains = {}
    for r in rows or []:
        chains.setdefault(str(r["invoice_id"]), []).append(r)
    out = {}
    for invoice_id, chain in chains.items():
        chain.sort(key=lambda r: int(r["chain_sequence"]))
        before = _json(chain[0].get("before_json"))
        original = sum((_d(l.get("amount")) for l in before.get("legs") or []), ZERO) + _d(before.get("account"))
        latest = chain[-1]
        out[invoice_id] = {"direction": latest["money_direction"], "delta": _d(latest["settled_total"]) - original,
                           "money": sum((_d(r.get("money_posted")) for r in chain), ZERO),
                           "account": sum((_d(r.get("account_posted")) for r in chain), ZERO)}
    return out


def line_shares(changes, lines):
    """lines: the lines of the changed invoices (line_id, invoice_id, line_type, weight_grams). -> {line_id: Decimal share of the delta}."""
    by_invoice = {}
    for line in lines or []:
        by_invoice.setdefault(str(line["invoice_id"]), []).append(line)
    shares = {}
    for invoice_id, change in (changes or {}).items():
        delta = change["delta"]
        if not delta:
            continue
        side = [l for l in by_invoice.get(invoice_id, [])
                if (l["line_type"] in SALE_TYPES if change["direction"] == "IN" else l["line_type"] == BUY_TYPE)]
        if not side:
            continue
        side.sort(key=lambda l: int(l["line_id"]))
        weights = [_d(l.get("weight_grams")) for l in side]
        whole = sum(weights, ZERO)
        given = ZERO
        for index, line in enumerate(side):
            if index == len(side) - 1:
                share = delta - given
            else:
                portion = weights[index] / whole if whole else Decimal(1) / len(side)
                share = (delta * portion).quantize(CENT, rounding=ROUND_HALF_UP)
            given += share
            shares[str(line["line_id"])] = share
    return shares


def corrected(rows, shares):
    """The report rows with the corrected amounts: a sale line's making_total / line_total gain its share; a scrap-purchase line's line_total gains it and its discount loses
    it (paid less = a bigger discount). A row without `line_id` or without a share is returned as it is; an unknown (NULL) amount stays unknown."""
    if not shares:
        return list(rows)
    out = []
    for row in rows:
        share = shares.get(str(row.get("line_id")))
        if not share:
            out.append(row)
            continue
        row = dict(row)
        if row.get("line_total") is not None:
            row["line_total"] = _d(row["line_total"]) + share
        if row.get("line_type") == BUY_TYPE:
            if row.get("discount_amount") is not None:
                row["discount_amount"] = _d(row["discount_amount"]) - share
        elif row.get("making_total") is not None:
            row["making_total"] = _d(row["making_total"]) + share
        out.append(row)
    return out


def read_full(repo):
    """(changes, lines, shares) from a repository that has `invoice_settlement_corrections()` and `lines_of_invoices(ids)`; a repository without them (older fakes) changes nothing."""
    reader = getattr(repo, "invoice_settlement_corrections", None)
    if reader is None:
        return {}, [], {}
    changes = invoice_changes(reader())
    ids = [invoice_id for invoice_id, c in changes.items() if c["delta"]]
    lines = repo.lines_of_invoices(ids) if ids else []
    return changes, lines, line_shares(changes, lines)


def read(repo):
    changes, _, shares = read_full(repo)
    return changes, shares
