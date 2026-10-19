"""BOOK-mode arithmetic of a market trip (Phase 1): the ONE definition of analytical equivalents, posted equivalents, the book balance and the
availability / integrity checks (pure functions, no database, no service import).

A BOOK trip keeps its gold as ONE number, the book balance in 21K-equivalent grams. Every document line keeps its PHYSICAL facts (weight and either a master
karat or an assay in permille) and posts ONE equivalent, computed once from those facts:

  analytical a_i   exact rational:  master karat   grams x K / 21
                                    assay (permille) grams x permille / 875      (= grams x permille / 1000 x 24 / 21)
  posted     p_i   round3(a_i): 0.001 g, ROUND_HALF_UP, ONCE per line; a document's figure is the SUM of its posted lines (never re-rounded)
  difference d_i   p_i - a_i, stored with the line (six-decimal evidence); |d_i| <= 0.0005 for an independently rounded line

  Lambda_open   = A_in - A_out   (analytical, exact)      the AVAILABILITY authority
  Bk            = P_in - P_out   (posted, the sum of the stored posted amounts)
  rho           = Bk - Lambda_open                         posting drift (neither loss nor gain; never booked as either)

An outgoing document is allowed when its analytical total does not exceed Lambda_open (exact). The posted book may then dip below zero by at most the
posting drift of the lines involved; that is displayed, never refused, and only a defect (an altered or missing line) makes the integrity check fail.
Nothing here uses a tolerance, an Eq21 conversion of a rounded figure, a float or a chained rounding. The stored six-decimal analytical value is audit
evidence of a_i and is never an input of a comparison: comparisons recompute a_i from the stored physical facts."""
from dataclasses import dataclass
from decimal import Decimal
from fractions import Fraction

from services.trip_errors import TripError

MASTER_KARAT, FINENESS = "MASTER_KARAT", "FINENESS"
BASES = (MASTER_KARAT, FINENESS)
LEGACY, BOOK = "LEGACY", "BOOK"
BK_ISSUE, BK_BUY, BK_SELL, BK_RETURN = "BK_ISSUE", "BK_BUY", "BK_SELL", "BK_RETURN"
PHASE1_KINDS = (BK_ISSUE, BK_BUY, BK_SELL, BK_RETURN)                    # the kinds Phase 1 posts
# 058 (Phase 2, the market cycle): stock of the SHOP -> trip, a marketer's 21K entitlement -> trip, trip -> a SUPPLIER's gold account, trip -> a marketer
BK_ISS_STK, BK_RECEIVE, BK_DLV_SUP, BK_DLV_MKT = "BK_ISS_STK", "BK_RECEIVE", "BK_DLV_SUP", "BK_DLV_MKT"
PHASE2_KINDS = (BK_ISS_STK, BK_RECEIVE, BK_DLV_SUP, BK_DLV_MKT)
# 059 (Phase 3): the evidenced gain - ONE incoming line computed from the surplus's own physical facts, with the owner's explanation and evidence; never automatic
BK_GAIN = "BK_GAIN"
GAIN_KINDS = (BK_GAIN,)
BOOK_KINDS = PHASE1_KINDS + PHASE2_KINDS + GAIN_KINDS                    # every BOOK gold kind (each has book lines)
BK_RECON = "BK_RECON"                                                    # 057: the reconciliation document of a BOOK trip. NOT a gold kind: it has no book line, leg or stock row
RECON_KINDS = (BK_RECON,)
INCOMING_KINDS, OUTGOING_KINDS = (BK_ISSUE, BK_BUY, BK_ISS_STK, BK_RECEIVE, BK_GAIN), (BK_SELL, BK_RETURN, BK_DLV_SUP, BK_DLV_MKT)
INDEPENDENT_BASIS, ALLOCATED_BASIS = "INDEPENDENT", "ALLOCATED"          # a line's posted figure: round3 of its own exact value, or an allocated share of stored stock
IN, OUT = "IN", "OUT"
SHARED_CASH_KINDS = ("ISSUE", "RETURN", "SUPPLIER_PAY")                 # cash-only entries, valid in both modes (the cash ledger is shared, never duplicated)
LEGACY_ONLY_KINDS = ("MELT", "LOT_RETURN", "LOT_ISSUE", "HEAT", "GOLD_SETTLE", "LOT_SETTLE", "LOT_HOLD", "HOLD_RETURN", "LOGO_HOLD", "HOLD_BACK",
                     "GOLD_SALE", "GOLD_BUY")
BASE_KARAT = 21
FINENESS_DIVISOR = 875                                                  # permille / 875 = permille / 1000 x 24 / 21
POST_PLACES, EVIDENCE_PLACES = 3, 6
INDEPENDENT = "INDEPENDENT"
# stock ledger vocabulary (shop_assayed_stock_movements); Phase 1 posts RECEIVE and RECEIVE_REVERSAL, Phase 2 adds the issue rows
RECEIVE, RECEIVE_REVERSAL, ISSUE_STOCK, ISSUE_REVERSAL = "RECEIVE", "RECEIVE_REVERSAL", "ISSUE", "ISSUE_REVERSAL"
ZERO3 = Decimal("0.000")

MODE_TEXT = {LEGACY: "قديم", BOOK: "دفتري"}


BookError = TripError                                              # one refusal type for the whole market-trip family (code + Arabic message)


def kind_allowed_in_mode(kind, mode):
    """The rule the database also enforces (chk_mte_mode_kind): book kinds only in BOOK trips, legacy-only kinds only in LEGACY trips, cash kinds in both."""
    if kind in SHARED_CASH_KINDS:
        return True
    if kind in BOOK_KINDS or kind in RECON_KINDS:
        return mode == BOOK
    return mode == LEGACY


def require_kind_in_mode(kind, mode):
    if not kind_allowed_in_mode(kind, mode or LEGACY):
        raise BookError("TRIP_MODE_MISMATCH",
                        "هذه الحركة لا تناسب نمط هذه العهدة: عهدة النمط الدفتري لا تقبل حركات النمط القديم، والعكس صحيح." if kind not in BOOK_KINDS and kind not in RECON_KINDS
                        else "هذه حركة نمط دفتري ولا تُسجَّل على عهدة قديمة.")


def trip_mode(row):
    """The mode of a trip row (a row read before migration 056 has no column: it is LEGACY)."""
    return (row or {}).get("accounting_mode") or LEGACY


def require_legacy_trip(row):
    if trip_mode(row) != LEGACY:
        raise BookError("TRIP_MODE_MISMATCH", "هذه العهدة بالنمط الدفتري: هذه العملية متاحة للعهد القديمة فقط.")


def require_book_trip(row):
    if trip_mode(row) != BOOK:
        raise BookError("TRIP_MODE_MISMATCH", "هذه العهدة بالنمط القديم: هذه العملية للعهد الدفترية فقط.")


# ------------------------------------------------------------------ exact arithmetic
def _fraction(value):
    return value if isinstance(value, Fraction) else Fraction(Decimal(str(value)))


def analytical(weight, basis, karat_value=None, fineness=None):
    """The exact 21K-equivalent grams of ONE line, computed from its physical facts only (a Fraction)."""
    grams = _fraction(weight)
    if basis == MASTER_KARAT:
        if karat_value is None:
            raise BookError("KARAT_REQUIRED", "اختر العيار.")
        return grams * int(karat_value) / BASE_KARAT
    if basis == FINENESS:
        if fineness is None:
            raise BookError("FINENESS_REQUIRED", "العيار الفعلي مطلوب: اكتبه بالألف.")
        return grams * _fraction(fineness) / FINENESS_DIVISOR
    raise BookError("BASIS_INVALID", "أسلوب العيار غير صحيح.")


def round_half_up(value, places):
    """ROUND_HALF_UP (away from zero on a tie) of an exact Fraction to `places` decimals, as a Decimal. No float anywhere."""
    scaled = _fraction(value) * (10 ** places)
    sign = -1 if scaled < 0 else 1
    scaled = abs(scaled)
    quotient = (scaled.numerator * 2 + scaled.denominator) // (scaled.denominator * 2)
    return (Decimal(sign * quotient) / (Decimal(10) ** places)).quantize(Decimal(1).scaleb(-places))


def post(value):
    """The posted equivalent of an analytical figure: 0.001 g, ROUND_HALF_UP, once."""
    return round_half_up(value, POST_PLACES)


def evidence(value):
    """Six-decimal audit evidence of an exact figure (never an input of a comparison)."""
    return round_half_up(value, EVIDENCE_PLACES)


@dataclass(frozen=True)
class Line:
    """One planned / stored line: the physical facts, the exact analytical value, the posted value and the stored difference."""
    direction: str
    weight: Decimal
    basis: str
    karat_id: int | None
    karat_value: int | None
    fineness: Decimal | None
    analytical: Fraction
    posted: Decimal
    analytical_evidence: Decimal
    difference: Decimal

    @property
    def signed(self):
        return self.posted if self.direction == IN else -self.posted

    @property
    def exact_difference(self):
        return Fraction(self.posted) - self.analytical


def plan_line(direction, weight, basis, karat_id=None, karat_value=None, fineness=None):
    a = analytical(weight, basis, karat_value, fineness)
    p = post(a)
    return Line(direction, Decimal(str(weight)), basis, None if karat_id is None else int(karat_id),
                None if karat_value is None else int(karat_value), None if fineness is None else Decimal(str(fineness)), a, p, evidence(a),
                round_half_up(Fraction(p) - a, EVIDENCE_PLACES))


def stored_line(row):
    """The Line of a stored row (physical facts + the stored posted figure); the analytical value is RECOMPUTED, never read."""
    basis = row["basis"]
    karat_value = row.get("karat_value")
    fineness = row.get("fineness_permille")
    a = analytical(row["weight_grams"], basis, karat_value, fineness)
    posted = Decimal(str(row["eq21_posted"]))
    return Line(row["direction"], Decimal(str(row["weight_grams"])), basis, None if row.get("karat_id") is None else int(row["karat_id"]),
                None if karat_value is None else int(karat_value), None if fineness is None else Decimal(str(fineness)), a, posted, evidence(a),
                round_half_up(Fraction(posted) - a, EVIDENCE_PLACES))


# ------------------------------------------------------------------ the balance
@dataclass(frozen=True)
class Balance:
    analytical_in: Fraction
    analytical_out: Fraction
    posted_in: Decimal
    posted_out: Decimal
    abs_difference: Fraction                 # sum of |p_i - a_i| over every line (the bound of the posting drift)
    lines: int

    @property
    def analytical_open(self):               # Lambda_open
        return self.analytical_in - self.analytical_out

    @property
    def posted(self):                        # Bk
        return self.posted_in - self.posted_out

    @property
    def drift(self):                         # rho = Bk - Lambda_open
        return Fraction(self.posted) - self.analytical_open

    def view(self):
        """Display figures: the posted balance (0.001), the analytical balance and the drift as six-decimal evidence."""
        return {"posted": self.posted.quantize(Decimal("0.001")), "posted_in": self.posted_in, "posted_out": self.posted_out,
                "analytical": evidence(self.analytical_open), "analytical_available": post(max(self.analytical_open, Fraction(0))),
                "drift": evidence(self.drift), "abs_difference": evidence(self.abs_difference), "lines": self.lines}


def balance_of(lines):
    """The balance of stored/planned `Line` objects (the effective ones; a reversed entry's lines are simply not passed)."""
    a_in = a_out = Fraction(0)
    p_in = p_out = ZERO3
    bound = Fraction(0)
    count = 0
    for line in lines:
        count += 1
        bound += abs(line.exact_difference)
        if line.direction == IN:
            a_in += line.analytical
            p_in += line.posted
        else:
            a_out += line.analytical
            p_out += line.posted
    return Balance(a_in, a_out, p_in, p_out, bound, count)


def balance_of_rows(rows):
    return balance_of([stored_line(r) for r in rows])


def balance_of_groups(groups):
    """The balance from AGGREGATED rows (Gold Position: one row per trip, direction, basis and karat / assay with SUM(weight), SUM(posted), line count and
    SUM(ABS(posted - analytical)) evidence). The analytical value is exact because the factor is identical inside a group."""
    a_in = a_out = Fraction(0)
    p_in = p_out = ZERO3
    bound = Fraction(0)
    count = 0
    for g in groups:
        a = analytical(g["weight_grams"], g["basis"], g.get("karat_value"), g.get("fineness_permille"))
        posted = Decimal(str(g["eq21_posted"]))
        count += int(g.get("lines", 1))
        bound += abs(Fraction(posted) - a)                      # the group's net drift is a lower bound of the per-line sum: shown as evidence only
        if g["direction"] == IN:
            a_in += a
            p_in += posted
        else:
            a_out += a
            p_out += posted
    return Balance(a_in, a_out, p_in, p_out, bound, count)


def _gold_text(value):
    return f"{post(max(value, Fraction(0))):,.3f}"


def check_outgoing(balance, new_lines):
    """Availability + integrity of an OUTGOING document (the new lines are all OUT), decided under the trip lock from the CURRENT balance.

    1. analytical: the document's exact analytical total may not exceed Lambda_open -> BOOK_EXCEEDS_BALANCE;
    2. integrity: the posted book after the document may dip below zero by no more than the posting drift of every line involved (existing + new);
       anything beyond that is not rounding -> BOOK_INTEGRITY (never absorbed)."""
    if not new_lines:
        return
    needed = sum((l.analytical for l in new_lines), Fraction(0))
    if needed > balance.analytical_open:
        raise BookError("BOOK_EXCEEDS_BALANCE",
                        f"الرصيد الدفتري المتاح {_gold_text(balance.analytical_open)} جرام (مكافئ عيار 21) فقط، والمطلوب {_gold_text(needed)}: "
                        "لا يمكن تسجيل حركة أكبر من الرصيد.")
    posted_after = Fraction(balance.posted) - sum((Fraction(l.posted) for l in new_lines), Fraction(0))
    bound = balance.abs_difference + sum((abs(l.exact_difference) for l in new_lines), Fraction(0))
    if posted_after < -bound:
        raise BookError("BOOK_INTEGRITY", "اختلال في حساب الرصيد الدفتري لا يفسره التقريب: راجع حركات العهدة قبل المتابعة.")


def check_without_incoming(balance_without):
    """Reversal guard for an INCOMING entry (BK_ISSUE / BK_BUY): the balance of the OTHER effective lines must stay analytically non-negative and
    consistent. Returns a blocker (code, message) or None. Reversing an outgoing entry only adds to the book and needs no such check."""
    if balance_without.analytical_open < 0:
        return ("DEPENDENT_BOOK_USE_EXISTS",
                f"لا يمكن عكس هذه الحركة لأن الذهب الذي أدخلته استُخدم بعدها (بيع أو رجوع): الرصيد الدفتري بدونها يصبح سالبًا بمقدار "
                f"{_gold_text(-balance_without.analytical_open)} جرام — اعكس الحركات التالية أولًا.")
    if Fraction(balance_without.posted) < -balance_without.abs_difference:
        return ("BOOK_INTEGRITY", "اختلال في حساب الرصيد الدفتري لا يفسره التقريب: راجع حركات العهدة قبل المتابعة.")
    return None


def line_row(line, *, line_price=None):
    """The repository payload of a planned line (all Decimals)."""
    return {"direction": line.direction, "weight": line.weight, "basis": line.basis, "karat_id": line.karat_id, "karat_value": line.karat_value,
            "fineness": line.fineness, "analytical_evidence": line.analytical_evidence, "posted": line.posted, "difference": line.difference,
            "signed": line.signed, "line_price": line_price}


def allocate_stock(grams, fineness, held_grams, held_posted):
    """(Line, independent posted p, posting basis) for `grams` x taken out of an identified assayed-stock item that still holds `held_grams` g and `held_posted` P
    (its STORED posted amount). a = the exact analytical value of x, p = round3(a); the posting policy (plan v3, addendum 2):
      x == g             q = P   ALLOCATED    (the item ends at exactly zero grams AND zero posted)
      x <  g and p <= P  q = p   INDEPENDENT  (|q - a| <= 0.0005, like any other line)
      x <  g and p >  P  q = P   ALLOCATED    (capped: never more than what is left)
    so the sum of every share equals the item's posted amount exactly. An ALLOCATED difference q - a may exceed 0.0005 and a positive weight may carry q = 0.
    p is returned apart from q (it is the stored `eq21_independent` evidence). The line is an IN line of the receiving trip (basis FINENESS, the item's own assay)."""
    grams, held_grams, held_posted = Decimal(str(grams)), Decimal(str(held_grams)), Decimal(str(held_posted))
    if grams <= 0 or grams > held_grams:
        raise BookError("STOCK_EXCEEDS_BALANCE", f"المتبقي من بند المخزون {held_grams:,.3f} جرام فقط: لا يمكن إخراج {grams:,.3f} جرام.")
    exact = analytical(grams, FINENESS, None, fineness)
    independent = post(exact)
    if grams == held_grams:
        share, basis = held_posted, ALLOCATED_BASIS
    elif independent <= held_posted:
        share, basis = independent, INDEPENDENT_BASIS
    else:
        share, basis = held_posted, ALLOCATED_BASIS
    line = Line(IN, grams, FINENESS, None, None, Decimal(str(fineness)), exact, share, evidence(exact), round_half_up(Fraction(share) - exact, EVIDENCE_PLACES))
    return line, independent, basis
