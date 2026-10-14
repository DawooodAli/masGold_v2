"""Pure supplier-accounting rules (no I/O, no database): parsing, making groups, balances, variance, intake limits.

Sign convention (documented once, used by both supplier ledgers and every read model):
    delta > 0  → the SHOP OWES the supplier   (علينا للمورد)  direction SHOP_OWES
    delta < 0  → the SUPPLIER owes the SHOP   (لنا عند المورد) direction SUPPLIER_OWES
    delta = 0  → settled                                      direction SETTLED
Gold (physical grams of the supplier's one dedicated karat; no Eq21 conversion in the supplier account) and making (EGP)
are independent dimensions and are never combined into one amount. The supplier invoice weight is the contractual gold truth; shop-scale
barcoded weight is only compared with it (variance), never substituted for it.
"""
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP, localcontext

from services.number_text import normalize_digits

SUPPORTED_KARATS = (18, 21, 22, 24)   # a supplier is dedicated to ONE of these; its gold account is in physical grams of that karat
WEIGHT_STEP = Decimal("0.001")
MONEY_STEP = Decimal("0.01")
EQ21_STEP = Decimal("0.000001")
BASE_KARAT = Decimal("21")

SHOP_OWES, SUPPLIER_OWES, SETTLED = "SHOP_OWES", "SUPPLIER_OWES", "SETTLED"
DRAFT, POSTED, CANCELLED = "DRAFT", "POSTED", "CANCELLED"

PURCHASE_SOURCE = "SUPPLIER_PURCHASE_INVOICE"
PURCHASE_MOVEMENT = "PURCHASE_INVOICE"

MAX_GROUPS = 50                      # a sanity bound on one invoice's making groups, not a business tolerance

# OWNER rule: the cumulative ACTUAL weight of the pieces entered from one making group may not exceed the group's
# supplier weight + exactly 0.333 g. Fixed: not a percentage, not per piece, not a setting, no override. Physical
# grams only (never Eq21). Each group has its own allowance; another group's unused weight never adds to it.
INTAKE_TOLERANCE = Decimal("0.333")


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


def parse_delivered_weight(value, label="الوزن المُسلَّم"):
    """The ACTUAL physical grams delivered: exact Decimal, strictly positive, at most three decimals. Unlike parse_weight it never rounds the
    figure the employee typed: a physical fact is refused when it cannot be stored as entered."""
    weight = _number(value, label, "WEIGHT_INVALID")
    if weight <= 0:
        raise SupplierRuleError("WEIGHT_INVALID", f"{label} يجب أن يكون أكبر من صفر.")
    if weight != weight.quantize(WEIGHT_STEP):
        raise SupplierRuleError("WEIGHT_INVALID", f"{label}: ثلاث خانات عشرية على الأكثر.")
    return weight.quantize(WEIGHT_STEP)


ANALYTICAL_STEP = Decimal("0.000001")


def settlement_credit(weight, delivered_karat_value, target_karat_value):
    """The supplier gold credit of a settlement: delivered grams x delivered karat / supplier karat.

    The POSTED credit comes straight from the high-precision value (Decimal, precision 50), quantized ONCE to 0.001 g ROUND_HALF_UP: it is never
    derived from the six-decimal analytical figure. The analytical value is quantized once to six decimals from the same high-precision value
    (it is NOT exact: a ratio such as 21/18 has no finite decimal). The rounding difference is computed from the two quantized figures.
    Returns (posted, analytical, difference); a positive delivery whose credit would post as zero is refused."""
    with localcontext() as context:
        context.prec = 50
        raw = Decimal(weight) * Decimal(str(delivered_karat_value)) / Decimal(str(target_karat_value))
        posted = raw.quantize(WEIGHT_STEP, rounding=ROUND_HALF_UP)
        analytical = raw.quantize(ANALYTICAL_STEP, rounding=ROUND_HALF_UP)
    if posted <= 0:
        raise SupplierRuleError("CREDIT_ROUNDS_TO_ZERO", "الوزن المُسلَّم صغير بحيث يُحتسب للمورد صفرًا: زِد الوزن.")
    return posted, analytical, posted - analytical


def lot_settlement_credit(weight, fineness, target_karat_value):
    """Credit an indivisible assayed lot from its physical facts, never rounded pure_grams."""
    if fineness is None:
        raise SupplierRuleError("FINENESS_REQUIRED", "العيار الفعلي لهذه الشحنة غير معلوم: لا يمكن تسويتها للمورد.")
    with localcontext() as context:
        context.prec = 50
        assay = Decimal(str(fineness))
        if not assay.is_finite() or not 0 < assay <= 1000:
            raise SupplierRuleError("FINENESS_INVALID", "العيار الفعلي للشحنة غير صالح.")
        raw = Decimal(str(weight)) * assay / Decimal(1000) * Decimal(24) / Decimal(str(target_karat_value))
        posted = raw.quantize(WEIGHT_STEP, rounding=ROUND_HALF_UP)
        analytical = raw.quantize(ANALYTICAL_STEP, rounding=ROUND_HALF_UP)
        difference = posted - analytical
    if posted <= 0:
        raise SupplierRuleError("CREDIT_ROUNDS_TO_ZERO",
                                "الذهب المحتسب لهذه السبكة يُقرَّب إلى صفر جرام في حساب المورد؛ لا يمكن تسجيل التسوية وتبقى السبكة في العهدة.")
    return posted, analytical, difference


def parse_rate(value, label="المصنعية (ج/جرام)"):
    """EGP per gram, exact Decimal, rounded once to 0.01, never negative."""
    rate = _number(value, label, "RATE_INVALID").quantize(MONEY_STEP, rounding=ROUND_HALF_UP)
    if rate < 0:
        raise SupplierRuleError("RATE_INVALID", f"{label} لا يمكن أن تكون سالبة.")
    return rate


MAX_PAYMENT = Decimal("100000000000000")      # the money columns are DECIMAL(16,2)


def parse_amount(value, label="المبلغ"):
    """A money amount in EGP, exact Decimal: strictly positive and at most two decimals. It is refused, never rounded,
    because a payment moves real money and the entered figure is the authoritative one."""
    amount = _number(value, label, "AMOUNT_INVALID")
    if amount <= 0:
        raise SupplierRuleError("AMOUNT_INVALID", f"{label} يجب أن يكون أكبر من صفر.")
    if amount >= MAX_PAYMENT or amount != amount.quantize(MONEY_STEP):      # magnitude first: quantize() overflows on 1e30
        raise SupplierRuleError("AMOUNT_INVALID", f"اكتب {label} بالأرقام بخانتين عشريتين على الأكثر وبقيمة مقبولة.")
    return amount.quantize(MONEY_STEP)


def money_amount(value):
    """A stored EGP figure as an exact two-decimal Decimal."""
    return Decimal(str(value)).quantize(MONEY_STEP)


def making_after_payment(before, amount):
    """The making position after paying `amount`: before - amount, exact. It is NOT capped at the payable balance: a larger
    payment crosses zero into supplier credit (the sign convention above: delta < 0 = the supplier owes the shop)."""
    return (Decimal(str(before)) - Decimal(str(amount))).quantize(MONEY_STEP)


def group_making_total(weight, rate):
    """Contractual group making = supplier group weight x EGP/gram, rounded once to 0.01 (Decimal only)."""
    return (Decimal(weight) * Decimal(rate)).quantize(MONEY_STEP, rounding=ROUND_HALF_UP)


def equivalent_21(weight, karat_value):
    """Eq21 = physical weight x actual karat / 21 (6 decimals, no premature rounding). In the supplier ledger it is only
    an internal compatibility value stored beside the physical weight and karat: it drives no supplier-account amount or
    direction, and the physical weight and karat are never replaced by it."""
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


def check_supplier_karat_change(current_karat_id, wanted_karat_id, history):
    """May this supplier carry `wanted_karat_id`? `history` = the distinct karats ({karat_id, karat_value}) of ALL its
    invoices (drafts and cancelled included) and gold movements.
      * no history: free (choose or change);
      * karat already assigned + any history: it can never change;
      * legacy supplier without a karat: only the ONE karat its whole history agrees on; mixed history stays unresolved."""
    wanted = int(wanted_karat_id)
    ids = {int(h["karat_id"]) for h in history}
    values = sorted({int(h["karat_value"]) for h in history})
    if not ids:
        return
    if current_karat_id is not None:
        if int(current_karat_id) != wanted:
            raise SupplierRuleError("KARAT_LOCKED", "لا يمكن تغيير عيار المورد بعد وجود فاتورة له، حتى لو كانت مسودة أو ملغاة.")
        return
    if len(ids) > 1:
        raise SupplierRuleError(
            "KARAT_HISTORY_MIXED",
            f"تاريخ هذا المورد يحتوي أعيرة مختلفة ({'، '.join(str(v) for v in values)}) ولا يمكن تحديد عيار واحد له. "
            "راجع فواتيره قبل المتابعة.")
    if wanted not in ids:
        raise SupplierRuleError("KARAT_HISTORY_MISMATCH",
                                f"العيار المختار لا يتوافق مع فواتير المورد السابقة (عيار {values[0]}).")


# Supplier gold ledger: the display label of a movement TYPE by its stable code (the stored English type names are history and are never rewritten).
GOLD_MOVEMENT_TEXT = {"PURCHASE_INVOICE": "فاتورة شراء",
                      "SETTLEMENT": "تسوية ذهب مورد",
                      "SETTLEMENT_REVERSAL": "عكس تسوية ذهب مورد"}


def gold_movement_text(code, stored_name):
    """The Arabic label of a supplier gold movement type when one is defined, else the stored name (unchanged behaviour for the other types)."""
    return GOLD_MOVEMENT_TEXT.get(code, stored_name)


ROLE_SUPPLIER, ROLE_INTERMEDIARY = "SUPPLIER", "INTERMEDIARY"
ROLE_FILTERS = (None, ROLE_SUPPLIER, ROLE_INTERMEDIARY)      # all / suppliers (alone or with the other role) / intermediaries (idem)


def role_text(is_supplier, is_intermediary):
    """The party's role in words: «مورد» / «مسوقجي» / both."""
    parts = (["مورد"] if is_supplier else []) + (["مسوقجي"] if is_intermediary else [])
    return " • ".join(parts)


def validate_roles(is_supplier, is_intermediary):
    """The two role flags as booleans; at least one is required."""
    is_supplier, is_intermediary = bool(is_supplier), bool(is_intermediary)
    if not (is_supplier or is_intermediary):
        raise SupplierRuleError("ROLE_REQUIRED", "اختر دورًا واحدًا على الأقل: مورد أو مسوقجي.")
    return is_supplier, is_intermediary


def has_supplier_role(supplier):
    """True when the party carries the supplier role. A row read from a database older than migration 044 has no flag and is a supplier."""
    return bool(supplier.get("is_supplier", True))


def require_supplier_role(supplier):
    """Supplier invoices, making payments and the gold account belong to a SUPPLIER; an intermediary-only party has none of them."""
    if not has_supplier_role(supplier):
        raise SupplierRuleError("SUPPLIER_ROLE_REQUIRED", "هذا الطرف مسوقجي فقط وليس مورِّدًا: لا فواتير شراء ولا دفعات مصنعيات ولا حساب ذهب له.")


def direction(delta):
    delta = Decimal(str(delta or 0))
    return SHOP_OWES if delta > 0 else SUPPLIER_OWES if delta < 0 else SETTLED


def variance(actual_weight, supplier_weight):
    """فرق الوزن = actual barcoded weight − supplier weight. Informational only: it never changes a supplier
    balance, a making liability, an inventory weight, and it is not a shortage claim (either scale may differ)."""
    return (Decimal(str(actual_weight or 0)) - Decimal(str(supplier_weight or 0))).quantize(WEIGHT_STEP)


CATEGORY_RESOLVED, CATEGORY_NO_MATCH, CATEGORY_AMBIGUOUS = "RESOLVED", "NO_MATCH", "AMBIGUOUS"


def resolve_category(making_rate, categories):
    """The inventory category (grade) of a making group, from its MAKING RATE and the EXISTING category rules
    (item_categories.purchase_making_min_per_gram / purchase_making_max_per_gram). Never from a category name, never
    from the item type, no threshold of its own. Same convention as the rest of the application (invoice / exchange
    making ranges): inclusive at both ends, a missing bound is open, a category with neither bound is not configured
    and never matches; only ACTIVE categories count. Exactly one match resolves; none or several is reported with the
    codes involved and never guessed. Returns {status, category (the row or None), matches (codes)}."""
    rate = Decimal(str(making_rate))
    matches = []
    for row in categories:
        if not row.get("is_active"):
            continue
        low, high = row.get("purchase_making_min_per_gram"), row.get("purchase_making_max_per_gram")
        if low is None and high is None:
            continue
        if low is not None and rate < Decimal(str(low)):
            continue
        if high is not None and rate > Decimal(str(high)):
            continue
        matches.append(row)
    status = CATEGORY_RESOLVED if len(matches) == 1 else CATEGORY_NO_MATCH if not matches else CATEGORY_AMBIGUOUS
    return {"status": status, "category": matches[0] if len(matches) == 1 else None,
            "matches": [row["code"] for row in matches]}


def category_problem(making_rate, resolution):
    """The Arabic explanation of an unresolved category ('' when resolved)."""
    rate = f"{Decimal(str(making_rate)).quantize(MONEY_STEP):,.2f}"
    if resolution["status"] == CATEGORY_NO_MATCH:
        return (f"لا توجد فئة نشطة تطابق مصنعية المجموعة ({rate} ج/جرام) حسب حدود مصنعية الشراء المعرّفة للفئات. "
                "راجع حدود الفئات في الإعدادات.")
    if resolution["status"] == CATEGORY_AMBIGUOUS:
        return (f"مصنعية المجموعة ({rate} ج/جرام) تطابق أكثر من فئة ({'، '.join(resolution['matches'])}) "
                "حسب حدود مصنعية الشراء. عدّل حدود الفئات في الإعدادات حتى تتطابق فئة واحدة فقط.")
    return ""


def require_category(making_rate, categories):
    """The resolved category row, or a SupplierRuleError naming the exact problem (no match / ambiguity)."""
    resolution = resolve_category(making_rate, categories)
    if resolution["status"] != CATEGORY_RESOLVED:
        code = "CATEGORY_NO_MATCH" if resolution["status"] == CATEGORY_NO_MATCH else "CATEGORY_AMBIGUOUS"
        raise SupplierRuleError(code, category_problem(making_rate, resolution))
    return resolution["category"]


def intake_limit(supplier_weight):
    """The most actual weight a group may hold: its supplier weight + 0.333 g."""
    return (Decimal(str(supplier_weight or 0)) + INTAKE_TOLERANCE).quantize(WEIGHT_STEP)


def intake_allowance(supplier_weight, actual_weight):
    """What can still be entered, tolerance included. Negative for a group that already exceeds its maximum
    (legacy data): the value is kept as is — only a display may show it as zero."""
    return (intake_limit(supplier_weight) - Decimal(str(actual_weight or 0))).quantize(WEIGHT_STEP)


def _grams_text(value):
    return f"{Decimal(str(value)).quantize(WEIGHT_STEP):,.3f}"


def check_intake(supplier_weight, existing_actual_weight, piece_weight):
    """Refuse a piece that would take the group's cumulative actual weight above supplier weight + 0.333 g
    (equality is allowed). Pure Decimal arithmetic; the service calls it under the group lock."""
    existing, piece = Decimal(str(existing_actual_weight or 0)), Decimal(str(piece_weight))
    limit = intake_limit(supplier_weight)
    if existing + piece <= limit:
        return
    if existing > limit:
        raise SupplierRuleError(
            "GROUP_OVER_LIMIT",
            f"تجاوزت هذه المجموعة الحد الأقصى المسموح للإدخال (وزن المورد + {INTAKE_TOLERANCE} جرام)، "
            "ولا يمكن إدخال قطع إضافية منها.")
    raise SupplierRuleError(
        "GROUP_WEIGHT_LIMIT",
        f"وزن القطعة يتجاوز المتاح للمجموعة. المتاح: {_grams_text(limit - existing)} جرام، "
        f"والسماحية المسموحة: {INTAKE_TOLERANCE} جرام.")


def progress(actual_weight, supplier_weight):
    """Intake progress context: nominal remaining reference, never a supplier financial remainder."""
    actual, supplier = Decimal(str(actual_weight or 0)), Decimal(str(supplier_weight or 0))
    percent = (actual / supplier * 100).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP) if supplier > 0 else Decimal("0.0")
    return {"actual_weight": actual.quantize(WEIGHT_STEP), "supplier_weight": supplier.quantize(WEIGHT_STEP),
            "variance": variance(actual, supplier),
            "remaining_reference": (supplier - actual).quantize(WEIGHT_STEP),
            "percent": percent, "over_supplier_weight": actual > supplier}
