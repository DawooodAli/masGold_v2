"""Pure supplier-accounting rules (no I/O, no database): parsing, making groups, Eq21, balances, variance.

Sign convention (documented once, used by both supplier ledgers and every read model):
    delta > 0  → the SHOP OWES the supplier   (علينا للمورد)  direction SHOP_OWES
    delta < 0  → the SUPPLIER owes the SHOP   (لنا عند المورد) direction SUPPLIER_OWES
    delta = 0  → settled                                      direction SETTLED
Gold (grams, per karat; Eq21 normalizes across karats) and making (EGP) are independent dimensions and are
never combined into one amount. The supplier invoice weight is the contractual gold truth; shop-scale
barcoded weight is only compared with it (variance), never substituted for it.
"""
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from services.number_text import normalize_digits

WEIGHT_STEP = Decimal("0.001")
MONEY_STEP = Decimal("0.01")
EQ21_STEP = Decimal("0.000001")
BASE_KARAT = Decimal("21")

SHOP_OWES, SUPPLIER_OWES, SETTLED = "SHOP_OWES", "SUPPLIER_OWES", "SETTLED"
DRAFT, POSTED, CANCELLED = "DRAFT", "POSTED", "CANCELLED"

PURCHASE_SOURCE = "SUPPLIER_PURCHASE_INVOICE"
PURCHASE_MOVEMENT = "PURCHASE_INVOICE"

MAX_GROUPS = 50                      # a sanity bound on one invoice's making groups, not a business tolerance


class SupplierRuleError(ValueError):
    """A refused supplier operation. `code` is a stable machine reason; the message is Arabic, ready to show."""

    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def supplier_code(supplier_id):
    return f"S{int(supplier_id):06d}"


def invoice_code(invoice_id):
    return f"SP{int(invoice_id):06d}"


def _number(value, label, code):
    text = normalize_digits(str(value if value is not None else "")).strip().replace(",", "")
    if not text:
        raise SupplierRuleError(code, f"{label} مطلوب.")
    try:
        number = Decimal(text)
    except InvalidOperation:
        raise SupplierRuleError(code, f"{label} غير صحيح.") from None
    if not number.is_finite():
        raise SupplierRuleError(code, f"{label} غير صحيح.")
    return number


def parse_weight(value, label="الوزن"):
    """Grams, exact Decimal, rounded once to 0.001, strictly positive."""
    weight = _number(value, label, "WEIGHT_INVALID").quantize(WEIGHT_STEP, rounding=ROUND_HALF_UP)
    if weight <= 0:
        raise SupplierRuleError("WEIGHT_INVALID", f"{label} يجب أن يكون أكبر من صفر.")
    return weight


def parse_rate(value, label="المصنعية (ج/جرام)"):
    """EGP per gram, exact Decimal, rounded once to 0.01, never negative."""
    rate = _number(value, label, "RATE_INVALID").quantize(MONEY_STEP, rounding=ROUND_HALF_UP)
    if rate < 0:
        raise SupplierRuleError("RATE_INVALID", f"{label} لا يمكن أن تكون سالبة.")
    return rate


def group_making_total(weight, rate):
    """Contractual group making = supplier group weight x EGP/gram, rounded once to 0.01 (Decimal only)."""
    return (Decimal(weight) * Decimal(rate)).quantize(MONEY_STEP, rounding=ROUND_HALF_UP)


def equivalent_21(weight, karat_value):
    """Eq21 = physical weight x actual karat / 21 (6 decimals, no premature rounding). The physical weight and the
    karat are never replaced by this normalized accounting value."""
    return (Decimal(weight) * Decimal(str(karat_value)) / BASE_KARAT).quantize(EQ21_STEP, rounding=ROUND_HALF_UP)


def normalize_groups(groups):
    """Validated making groups with their exact totals.

    Returns {"groups": [...], "total_weight": Decimal, "gross_making": Decimal}. The invoice supplier weight is the
    sum of the group weights, so the groups always reconcile with it. Raises SupplierRuleError otherwise."""
    groups = list(groups or [])
    if not groups:
        raise SupplierRuleError("GROUPS_REQUIRED", "أضف مجموعة مصنعية واحدة على الأقل.")
    if len(groups) > MAX_GROUPS:
        raise SupplierRuleError("GROUPS_TOO_MANY", f"الحد الأقصى {MAX_GROUPS} مجموعة في الفاتورة.")
    cleaned, total_weight, gross = [], Decimal("0.000"), Decimal("0.00")
    for number, group in enumerate(groups, 1):
        group = group or {}
        weight = parse_weight(group.get("weight"), f"وزن المجموعة {number}")
        rate = parse_rate(group.get("making_per_gram"), f"مصنعية المجموعة {number} (ج/جرام)")
        description = str(group.get("description") or "").strip() or None
        if description and len(description) > 150:
            raise SupplierRuleError("DESCRIPTION_LONG", f"وصف المجموعة {number} أطول من الحد المسموح.")
        making = group_making_total(weight, rate)
        cleaned.append({"group_no": number, "description": description, "supplier_weight_grams": weight,
                        "making_per_gram": rate, "making_total": making})
        total_weight += weight
        gross += making
    return {"groups": cleaned, "total_weight": total_weight, "gross_making": gross}


def direction(delta):
    delta = Decimal(str(delta or 0))
    return SHOP_OWES if delta > 0 else SUPPLIER_OWES if delta < 0 else SETTLED


def variance(actual_weight, supplier_weight):
    """فرق الوزن = actual barcoded weight − supplier weight. Informational only: it never changes a supplier
    balance, a making liability, an inventory weight, and it is not a shortage claim (either scale may differ)."""
    return (Decimal(str(actual_weight or 0)) - Decimal(str(supplier_weight or 0))).quantize(WEIGHT_STEP)


def progress(actual_weight, supplier_weight):
    """Intake progress context: nominal remaining reference, never a supplier financial remainder."""
    actual, supplier = Decimal(str(actual_weight or 0)), Decimal(str(supplier_weight or 0))
    percent = (actual / supplier * 100).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP) if supplier > 0 else Decimal("0.0")
    return {"actual_weight": actual.quantize(WEIGHT_STEP), "supplier_weight": supplier.quantize(WEIGHT_STEP),
            "variance": variance(actual, supplier),
            "remaining_reference": (supplier - actual).quantize(WEIGHT_STEP),
            "percent": percent, "over_supplier_weight": actual > supplier}
