"""Market-custody arithmetic: the ONE definition of what is still outstanding in a custody (pure functions, no database).

Every caller uses these functions, so posting, return checks, closure and the correction guards can never disagree:

  gold (per karat and designation)   outstanding = issued - returned - melted + effective heating differences - effective supplier settlements
  cash                               outstanding = issued - returned - paid to suppliers

The inputs are the repository's derived sums over the entries that were NOT reversed (a reversed entry simply stops counting), so a
reversal restores a settled quantity to custody without any extra movement. Missing terms are zero (a row read before a migration)."""
from decimal import Decimal

ZERO_WEIGHT, ZERO_MONEY = Decimal("0.000"), Decimal("0.00")


def _dec(value):
    return Decimal(str(value or 0))


def gold_outstanding(row):
    """Grams of one (karat, designation) still in custody. `row` is a repository row (issued / returned / melted / heat_delta /
    supplier_settled); the Logo heating difference is signed and only ever non-zero on LOGO, a melt only on MELT, a supplier settlement
    (046) only on LOGO in this slice."""
    return (_dec(row.get("issued")) - _dec(row.get("returned")) - _dec(row.get("melted")) + _dec(row.get("heat_delta"))
            - _dec(row.get("supplier_settled")))


def cash_outstanding(issued, returned, paid=0):
    """Custody cash still outside Main Safe: issued - returned - paid to suppliers from custody."""
    return _dec(issued) - _dec(returned) - _dec(paid)


def cash_rows_outstanding(cash_rows):
    """The same, from the repository's (issued, returned[, paid]) tuple."""
    return cash_outstanding(cash_rows[0], cash_rows[1], cash_rows[2] if len(cash_rows) > 2 else 0)


def any_negative(cash_rows, gold_rows, designation=None):
    """True when the custody (as the repository derived it, typically WITHOUT one entry) would hold a negative quantity. With a
    designation only the gold of that designation is judged and the cash is ignored."""
    if designation is None and cash_rows_outstanding(cash_rows) < 0:
        return True
    return any(gold_outstanding(g) < 0 for g in gold_rows if designation is None or g.get("designation") == designation)
