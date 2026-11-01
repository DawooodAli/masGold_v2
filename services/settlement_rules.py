"""Market-custody SETTLEMENT arithmetic (stage 2): pure functions, no database. The owner records the market trip AFTER returning; gold that left by weight and karat is
melted, distributed, sold or written off. Every figure here is exact (Fractions) and posted ONCE at 0.001 g (ROUND_HALF_UP), reusing the 21K-equivalent definitions of
`eq21_math` (a master karat: grams x K / 21; an assay: grams x permille / 875).

A melt turns grams of several karats into ONE bar:
  theoretical Eq21 = sum(grams x K / 21)                 what the melted grams are worth on paper
  actual Eq21      = bar weight x fineness / 875         what the bar is worth (fineness: an INTEGER per mille, e.g. 850)
  loss (posted)    = posted theoretical - posted actual   positive = loss, negative = gain (surplus)
  weight loss      = (melted grams - bar weight) x expected fineness / 875 (the grams that vanished, at the fineness the input mix promised)
  fineness loss    = loss - weight loss                   (the remainder: the bar came out less fine than the input mix promised; negative = finer)
weight loss + fineness loss == loss EXACTLY (the fineness loss is the posted difference), so a report can add them."""
from decimal import Decimal
from fractions import Fraction

from services import eq21_math
from services.trip_errors import TripError

ZERO3 = Decimal("0.000")
MAX_FINENESS = 1000


def parse_fineness(value):
    """The bar's fineness: an INTEGER per mille (1..1000), no decimals (the owner's rule: 850, not 850.5)."""
    text = str(value if value is not None else "").strip().replace(",", "")
    if not text:
        raise TripError("FINENESS_REQUIRED", "اكتب عيار السبيكة بالألف (رقم صحيح مثل 850).")
    if not text.isascii() or not text.isdigit():
        raise TripError("FINENESS_INVALID", "عيار السبيكة رقم صحيح بالألف بدون كسور (مثل 850).")
    number = int(text)
    if number < 1 or number > MAX_FINENESS:
        raise TripError("FINENESS_RANGE", "عيار السبيكة يجب أن يكون بين 1 و1000 في الألف.")
    return number


def melt_figures(inputs, output_weight, fineness):
    """`inputs` = [(karat_value, grams)] (Decimal grams > 0), `output_weight` Decimal grams, `fineness` int per mille. Returns the exact analysis (all Decimals at 0.001):
    input_weight, theoretical_eq21, actual_eq21, difference_eq21 (actual - theoretical, signed), loss_eq21 (theoretical - actual), weight_loss_eq21, fineness_loss_eq21,
    expected_fineness (per mille, six decimals). A negative loss is a GAIN."""
    if not inputs:
        raise TripError("MELT_INPUTS_REQUIRED", "اختر عيارًا واحدًا على الأقل مع وزنه.")
    grams_total = sum((Fraction(Decimal(str(g))) for _, g in inputs), Fraction(0))
    if grams_total <= 0 or Decimal(str(output_weight)) <= 0:
        raise TripError("MELT_WEIGHT_INVALID", "وزن السبك ووزن السبيكة يجب أن يكونا موجبين.")
    theoretical = sum((eq21_math.analytical(g, eq21_math.MASTER_KARAT, karat_value=k) for k, g in inputs), Fraction(0))
    pure_in = sum((Fraction(Decimal(str(g))) * int(k) / 24 for k, g in inputs), Fraction(0))           # grams of 24K gold the melted grams contain
    actual = eq21_math.analytical(output_weight, eq21_math.FINENESS, fineness=fineness)
    theoretical_posted, actual_posted = eq21_math.post(theoretical), eq21_math.post(actual)
    loss_posted = theoretical_posted - actual_posted
    expected_per_gram = pure_in / grams_total / Fraction(7, 8)                                         # Eq21 of one melted gram on average
    weight_loss = eq21_math.post((grams_total - Fraction(Decimal(str(output_weight)))) * expected_per_gram)
    return {"input_weight": eq21_math.post(grams_total), "output_weight": Decimal(str(output_weight)).quantize(ZERO3),
            "fineness": int(fineness), "theoretical_eq21": theoretical_posted, "actual_eq21": actual_posted,
            "difference_eq21": actual_posted - theoretical_posted, "loss_eq21": loss_posted, "weight_loss_eq21": weight_loss,
            "fineness_loss_eq21": loss_posted - weight_loss,
            "expected_fineness": eq21_math.round_half_up(pure_in / grams_total * 1000, 6)}
