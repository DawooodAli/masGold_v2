"""English (ASCII) digits only: Arabic-Indic and Persian digits typed or pasted are the same digits.

The UI normalizes key presses (app/digit_filter.py); this protects every parser below QML as well,
so a value like "١٢٣٤٥٫٥" can never be refused, mis-read or stored with mixed digit systems."""

_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹٫", "01234567890123456789.")


def normalize_digits(text):
    """'١٢٣٤٥' -> '12345', '۱۲۳۴۵' -> '12345', '٣٫٦' -> '3.6'; anything that is not a str is returned as is."""
    return text.translate(_DIGITS) if isinstance(text, str) else text
