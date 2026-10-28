"""Display formatting shared by the ViewModels (presentation only — never used for calculation).

Arabic (RTL) labels reorder "neutral" runs: in plain Qt text "2026-09-27 13:05" shows as
"13:05 2026-09-27", "−2.000" as "2.000−" and "1.25%" as "%1.25". Every value whose
characters are not all strong-LTR (dates, times, signed numbers, percentages) is therefore
wrapped in a Unicode left-to-right isolate (U+2066 … U+2069): it stays one intact left-to-right
unit wherever it sits in an Arabic sentence. No LayoutMirroring, no per-screen hacks.

Money: one convention everywhere — "23,000.00 ج" (two decimals, thousands separators).
"""
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal

LRI, PDI = "⁦", "⁩"
MINUS = "−"


def ltr(text):
    """Keep `text` as one left-to-right unit inside Arabic text (idempotent)."""
    text = "" if text is None else str(text)
    if not text or (text.startswith(LRI) and text.endswith(PDI)):
        return text
    return LRI + text + PDI


def _decimal(value):
    return Decimal(str(value if value not in (None, "") else "0"))


def number(value, places=2, signed=False):
    """'1,250.00'; negative (or signed=True) values are isolated with a real minus sign."""
    amount = _decimal(value)
    shown = abs(amount).quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP)       # half-up, never banker's rounding
    text = f"{shown:,.{places}f}"
    if amount < 0:
        return ltr(MINUS + text)
    if signed and amount > 0:
        return ltr("+" + text)
    return text


STEP = Decimal(5)


def whole(value, signed=False):
    """An AMOUNT of money for display: no fractions, in multiples of 5 EGP (half-up): '23,000'. A non-zero amount that would round to
    zero is shown exactly ('2.00') so a real balance never reads as nothing."""
    amount = _decimal(value)
    five = (abs(amount) / STEP).quantize(Decimal(1), rounding=ROUND_HALF_UP) * STEP
    if amount != 0 and five == 0:
        return number(amount, 2, signed)
    text = f"{five:,.0f}"
    if amount < 0:
        return ltr(MINUS + text)
    if signed and amount > 0:
        return ltr("+" + text)
    return text


def money(value, signed=False):
    """'23,000 ج' — the one money format of the application (whole, multiples of 5)."""
    return f"{whole(value, signed)} ج"


def rate(value, signed=False):
    """A per-gram RATE (gold price, making per gram, average price per gram), never rounded to 5: '6,207 ج' or '25.50 ج'."""
    amount = _decimal(value)
    return f"{number(amount, 0 if amount == amount.to_integral_value() else 2, signed)} ج"


def grams(value, signed=False):
    """'3.60' — a gold weight; the label / column (الوزن, الإجمالي, عيار 21) already says it is grams,
    so the unit is not repeated on screen."""
    return number(value, 2, signed)


def grams3(value, signed=False):
    """'3.600' — the EXACT posted weight (three decimals). Used only where the third decimal is an accounting figure:
    the supplier ledgers, market-trip custody / melts / settlements and the marketer ledger. Every other screen shows `grams` (two decimals)."""
    return number(value, 3, signed)


def percent(value, places=2):
    """'1.50%' kept as one left-to-right unit."""
    return ltr(f"{_decimal(value):.{places}f}%")


def date_text(value, fmt="%Y-%m-%d"):
    if value is None:
        return ""
    return ltr(value.strftime(fmt)) if isinstance(value, (date, datetime)) else ltr(str(value))


def datetime_text(value, seconds=False):
    return date_text(value, "%Y-%m-%d %H:%M:%S" if seconds else "%Y-%m-%d %H:%M")
