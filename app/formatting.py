"""Display formatting shared by the ViewModels (presentation only — never used for calculation).

Arabic (RTL) labels reorder "neutral" runs: in plain Qt text "2026-09-27 13:05" shows as
"13:05 2026-09-27", "−2.000" as "2.000−" and "1.25%" as "%1.25". Every value whose
characters are not all strong-LTR (dates, times, signed numbers, percentages) is therefore
wrapped in a Unicode left-to-right isolate (U+2066 … U+2069): it stays one intact left-to-right
unit wherever it sits in an Arabic sentence. No LayoutMirroring, no per-screen hacks.

Money: one convention everywhere — "23,000.00 ج" (two decimals, thousands separators).
"""
from datetime import date, datetime
from decimal import Decimal

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
    text = f"{abs(amount):,.{places}f}"
    if amount < 0:
        return ltr(MINUS + text)
    if signed and amount > 0:
        return ltr("+" + text)
    return text


def money(value, signed=False):
    """'23,000.00 ج' — the one money format of the application."""
    return f"{number(value, 2, signed)} ج"


def grams(value, signed=False):
    """'3.600 جم'."""
    return f"{number(value, 3, signed)} جم"


def percent(value, places=2):
    """'1.50%' kept as one left-to-right unit."""
    return ltr(f"{_decimal(value):.{places}f}%")


def date_text(value, fmt="%Y-%m-%d"):
    if value is None:
        return ""
    return ltr(value.strftime(fmt)) if isinstance(value, (date, datetime)) else ltr(str(value))


def datetime_text(value, seconds=False):
    return date_text(value, "%Y-%m-%d %H:%M:%S" if seconds else "%Y-%m-%d %H:%M")
