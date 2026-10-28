"""The owner-chosen customer warnings (pure, no I/O). A warning is a row of `customer_flags` (flag_type = one of these codes, severity WARNING).
It shows beside the customer when the name is entered in a new invoice; it never blocks a sale. The Dead Cash warning (DEAD_CASH_WARNING) is
separate: it is created by a write-off and keeps its own confirmation rule."""

OPTIONS = (
    ("NOT_COMMITTED", "عميل غير ملتزم"),
    ("DELAYS_PAYMENT", "يتأخر في السداد"),
    ("DISPUTES_BILLS", "يعترض على الفواتير"),
    ("NEEDS_FOLLOWUP", "يحتاج متابعة"),
)
LABELS = dict(OPTIONS)
CODES = tuple(code for code, _ in OPTIONS)


def clean(codes):
    """The valid, de-duplicated codes in option order (an unknown code is refused by the caller, never silently dropped)."""
    wanted = {str(c).strip() for c in (codes or []) if str(c).strip()}
    unknown = wanted - set(CODES)
    if unknown:
        raise ValueError("تحذير غير معروف.")
    return [c for c in CODES if c in wanted]


def labels(csv_or_list):
    """Arabic labels from the stored codes (a GROUP_CONCAT string or a list); unknown codes are left out."""
    if not csv_or_list:
        return []
    codes = csv_or_list.split(",") if isinstance(csv_or_list, str) else list(csv_or_list)
    return [LABELS[c.strip()] for c in codes if c.strip() in LABELS]
