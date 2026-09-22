"""Shared customer display values. Never changes stored profile or ledger text."""

from datetime import date, datetime
from decimal import Decimal
import logging


logger = logging.getLogger(__name__)
CUSTOMER_TYPE_LABELS = {"REGULAR": "عادي", "VIP": "مميز"}
BALANCE_LABELS = {
    "CUSTOMER_OWES_SHOP": "على العميل للمحل",
    "SHOP_OWES_CUSTOMER": "للعميل عند المحل",
    "SETTLED": "الحساب متزن",
}
TRANSACTION_LABELS = {
    "SALE_BALANCE_DUE": "متبقي بيع",
    "BUY_BALANCE_DUE": "متبقي شراء من العميل",
    "CUSTOMER_PAYMENT": "سداد من العميل",
    "SHOP_PAYMENT": "سداد للعميل",
    "CUSTOMER_ADVANCE": "دفعة مقدمة من العميل",
    "ADVANCE_APPLIED": "استخدام رصيد العميل",
    "DEAD_CASH_WRITE_OFF": "شطب دين معدوم",
    "DEAD_CASH_REVERSAL": "عكس شطب دين معدوم",
    "MANUAL_ADJUSTMENT": "تسوية يدوية",
    "LEGACY_ADJUSTMENT": "تسوية تاريخية مرحّلة",
}
BIGINT_FIELDS = {
    "id", "customer_id", "reference_id", "related_dead_cash_id",
    "writeoff_transaction_id", "reversal_transaction_id", "legacy_source_id",
    "transaction_id", "flag_id", "dead_cash_id", "ledger_max_id",
}


def qml_safe(value, key=None):
    if value is None:
        return None
    if key in BIGINT_FIELDS:
        return str(value)
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M:%S")
    if isinstance(value, date):
        return value.strftime("%Y-%m-%d")
    if isinstance(value, dict):
        return {k: qml_safe(v, k) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [qml_safe(v) for v in value]
    return value


def money_text(value, signed=False):
    if value is None:
        return "غير متاح"
    amount = value if isinstance(value, Decimal) else Decimal(str(value))
    return format(amount, "+,.2f" if signed and amount != 0 else ",.2f")


def balance_meaning(value):
    if value is None:
        return "الرصيد غير متاح"
    amount = value if isinstance(value, Decimal) else Decimal(str(value))
    state = "CUSTOMER_OWES_SHOP" if amount > 0 else "SHOP_OWES_CUSTOMER" if amount < 0 else "SETTLED"
    return BALANCE_LABELS[state]


def customer_row(raw):
    row = qml_safe(raw)
    row["id"] = str(raw["customer_id"])
    row["is_active"] = bool(raw.get("is_active"))
    row["status_label"] = "نشط" if row["is_active"] else "غير نشط"
    type_code = raw.get("customer_type_code")
    row["type_label"] = CUSTOMER_TYPE_LABELS.get(type_code, "نوع آخر" if type_code else "غير محدد")
    row["balance_text"] = money_text(raw.get("balance"), signed=True)
    row["balance_meaning"] = BALANCE_LABELS.get(raw.get("balance_status"), "الرصيد غير متاح")
    row["balance_nonzero"] = raw.get("balance") is not None and Decimal(str(raw["balance"])) != 0
    row["last_transaction_text"] = row.get("last_transaction_at") or "—"
    row["active_warning_count"] = int(raw.get("active_warning_count") or 0)
    row["has_dead_cash_history"] = bool(raw.get("has_dead_cash_history"))
    row["has_active_dead_cash_warning"] = bool(raw.get("has_active_dead_cash_warning"))
    row["has_unreversed_dead_cash"] = int(raw.get("unreversed_dead_cash_count") or 0) > 0
    row["dead_cash_attention"] = row["has_active_dead_cash_warning"] or row["has_unreversed_dead_cash"]
    if row["dead_cash_attention"]:
        row["dead_cash_message"] = "تنبيه: يوجد تحذير ديون معدومة نشط أو شطب غير معكوس. راجع سجل الرصيد المشطوب."
    elif row["has_dead_cash_history"]:
        row["dead_cash_message"] = "يوجد سجل شطب سابق تم عكسه؛ لا يوجد تحذير ديون معدومة نشط."
    else:
        row["dead_cash_message"] = ""
    return row


def transaction_row(raw):
    row = qml_safe(raw)
    row["id"] = str(raw["transaction_id"])
    row["amount_text"] = money_text(raw.get("amount_delta"), signed=True)
    row["type_label"] = TRANSACTION_LABELS.get(raw.get("transaction_type_code"), "حركة أخرى")
    amount = raw.get("amount_delta")
    if amount is None:
        row["direction_label"] = "اتجاه الحركة غير متاح"
    else:
        amount = Decimal(str(amount))
        row["direction_label"] = (
            "زيادة الرصيد لصالح المحل" if amount > 0
            else "خفض الرصيد لصالح العميل" if amount < 0 else "دون تغيير في الرصيد"
        )
    row["running_balance_text"] = money_text(raw.get("running_balance"), signed=True)
    row["running_balance_meaning"] = balance_meaning(raw.get("running_balance"))
    return row


def statement_summary(raw):
    summary = qml_safe(raw)
    for key in ("opening_balance", "period_net", "closing_balance", "current_balance", "matching_net"):
        summary[key + "_text"] = money_text(raw.get(key), signed=True)
        if key not in ("period_net", "matching_net"):
            summary[key + "_meaning"] = balance_meaning(raw.get(key))
    summary["type_filter_active"] = bool(raw.get("type_filter_active"))
    summary["history_basis_text"] = (
        "الكشف مبني على الحركات المسجلة المتاحة فقط؛ رصيد البداية صفر قبل أول حركة مسجلة، ولا يُعاد بناء التاريخ المفقود."
        if not raw.get("from_date") else
        "رصيد أول المدة محسوب من الحركات المسجلة قبل تاريخ البداية؛ الكشف يعتمد على التاريخ المسجل المتاح فقط."
    )
    return summary


def error_text(exc, fallback):
    if isinstance(exc, ValueError) and any("\u0600" <= char <= "\u06ff" for char in str(exc)):
        return str(exc)
    logger.error("Customer operation failed", exc_info=True)
    return fallback
