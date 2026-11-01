"""Exact 21K-equivalent arithmetic of the market-trip melt analysis (pure functions, no database, no service import).

  analytical   exact rational:  master karat   grams x K / 21
                                assay (permille) grams x permille / 875      (= grams x permille / 1000 x 24 / 21)
  posted       round3(analytical): 0.001 g, ROUND_HALF_UP, ONCE per figure

Nothing here uses a tolerance, a float or a chained rounding."""
from decimal import Decimal
from fractions import Fraction

from services.trip_errors import TripError

MASTER_KARAT, FINENESS = "MASTER_KARAT", "FINENESS"
BASE_KARAT = 21
FINENESS_DIVISOR = 875                                                  # permille / 875 = permille / 1000 x 24 / 21
POST_PLACES, EVIDENCE_PLACES = 3, 6


def _fraction(value):
    return value if isinstance(value, Fraction) else Fraction(Decimal(str(value)))


def analytical(weight, basis, karat_value=None, fineness=None):
    """The exact 21K-equivalent grams of ONE figure, computed from its physical facts only (a Fraction)."""
    grams = _fraction(weight)
    if basis == MASTER_KARAT:
        if karat_value is None:
            raise TripError("KARAT_REQUIRED", "اختر العيار.")
        return grams * int(karat_value) / BASE_KARAT
    if basis == FINENESS:
        if fineness is None:
            raise TripError("FINENESS_REQUIRED", "العيار الفعلي مطلوب: اكتبه بالألف.")
        return grams * _fraction(fineness) / FINENESS_DIVISOR
    raise TripError("BASIS_INVALID", "أسلوب العيار غير صحيح.")


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
