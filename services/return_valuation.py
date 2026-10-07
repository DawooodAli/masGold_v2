"""Return V2 historical valuation (owner decisions 2026-10-03). Pure functions over stored values only.

HISTORICAL mode (inside the full-refund window) refunds what the customer paid for the returned portion —
never today's gold price:

  original invoice piece      its invoice line_total (Billing already split the authoritative invoice total
                              across lines; Σ line_total = sell_total).
  F7 grams, partial           line_total × returned grams ÷ line grams (money precision, half up);
  F7 grams, the last ones     line_total − Σ the historical bases already refunded for that line, so the
                              refunds of one line always add up to its line_total exactly.
  Exchange replacement        pool = Σ values of the pieces the customer surrendered in that Exchange
                              + the Exchange's signed FINAL amount (+ customer paid, − shop paid);
                              the pool is split by weight across ALL its replacement lines (stored line
                              order; the last line takes the remainder), identified or not. A surrendered
                              piece that was itself a replacement is valued the same way (chains).

The EGP-5 rounding is NOT applied here: the Return applies it once to its final total."""
from decimal import ROUND_HALF_UP, Decimal

MONEY = Decimal("0.01")
ZERO = Decimal("0.00")


def money(value):
    return Decimal(str(value)).quantize(MONEY, rounding=ROUND_HALF_UP)


def share(total, part, whole):
    """total × part ÷ whole at money precision (half up); Decimal only."""
    total, part, whole = Decimal(str(total)), Decimal(str(part)), Decimal(str(whole))
    if whole <= 0:
        raise ValueError("weight must be positive")
    return money(total * part / whole)


def f7_historical_basis(line_total, line_grams, returned_before, prior_bases, grams):
    """The historical basis of `grams` returned now from one F7 line (line_total over line_grams), given the grams
    and historical bases already refunded from it. The final grams take the exact remainder."""
    line_total, line_grams = Decimal(str(line_total)), Decimal(str(line_grams))
    returned_before, grams = Decimal(str(returned_before)), Decimal(str(grams))
    if returned_before + grams > line_grams:
        raise ValueError("more grams than the line still has")
    if returned_before + grams == line_grams:
        return money(line_total - Decimal(str(prior_bases)))
    return share(line_total, grams, line_grams)


def allocate_by_weight(pool, lines):
    """Split `pool` over [(key, weight)] in the given (stored) order: each line its weight share, the last line the
    remainder, so the parts add up to the pool exactly."""
    pool = money(pool)
    lines = list(lines)
    if not lines:
        return {}
    total_weight = sum((Decimal(str(w)) for _, w in lines), Decimal("0"))
    if total_weight <= 0:
        raise ValueError("replacement weights must be positive")
    values, allocated = {}, ZERO
    for key, weight in lines[:-1]:
        values[key] = share(pool, weight, total_weight)
        allocated += values[key]
    values[lines[-1][0]] = pool - allocated
    return values


class ChainResolver:
    """Historical values of Exchange replacement pieces, recursively through Exchange chains.

    `exchanges`: lifecycle_id → {"signed_amount": Decimal,
                                 "surrendered": [source refs of the pieces the customer gave back],
                                 "replacements": [(lifecycle_line_id, weight) in stored line order]}
    `invoice_line_totals`: invoice_line_id → line_total.
    A source ref is ("INVOICE_LINE", invoice_line_id) or ("LIFECYCLE_LINE", lifecycle_line_id)."""

    def __init__(self, exchanges, invoice_line_totals, replacement_owner):
        self._exchanges = exchanges
        self._invoice = {str(k): Decimal(str(v)) for k, v in invoice_line_totals.items()}
        self._owner = {str(k): str(v) for k, v in replacement_owner.items()}   # replacement line → lifecycle id
        self._cache = {}

    def value(self, ref, _path=()):
        kind, key = ref[0], str(ref[1])
        if kind == "INVOICE_LINE":
            if key not in self._invoice:
                raise LookupError(f"invoice line {key} has no stored line_total")
            return money(self._invoice[key])
        if kind != "LIFECYCLE_LINE":
            raise ValueError(f"unknown source {kind}")
        if key in _path:
            raise ValueError("Exchange lifecycle loop")                 # never valued twice through a cycle
        exchange_id = self._owner.get(key)
        if exchange_id is None:
            raise LookupError(f"replacement line {key} belongs to no Exchange")
        if exchange_id not in self._cache:
            exchange = self._exchanges[exchange_id]
            pool = sum((self.value(source, _path + (key,)) for source in exchange["surrendered"]), ZERO)
            pool += money(exchange["signed_amount"])
            self._cache[exchange_id] = allocate_by_weight(pool, exchange["replacements"])
        return self._cache[exchange_id][key]
