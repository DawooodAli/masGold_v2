"""عهدة السوق والموردين on the iPad (owner request 2026-11-07, «كل العمليات حرفيًا»): ONE overview read plus ONE whitelisted write endpoint over the EXISTING services.

The forms are DATA: `OPS` below is a table of operations, each with its Arabic title, its fields and the service call. The overview sends the field specs to the page, the page draws the form
from them, and `op()` validates the body against the very same spec (only declared fields are read, every value is text, lines are bounded) before calling the service. Adding an operation is one
entry in `OPS`; nothing else changes.

Nothing here decides a business rule. Every write goes through the same service the desktop uses, which rechecks its own right inside its transaction with the actor taken from the verified
session (never from the request). On top of that every write re-verifies the user's CURRENT password (the shared `AuthenticationService`, so its failure cooldown spans attempts), needs the
explicit confirmation and the client's request key (an exact retry replays, never posts twice)."""
from decimal import Decimal
import logging

from services.authentication_service import AuthenticationError
from webapp.errors import ApiError

logger = logging.getLogger("webapp")
MAX_LINES = 12
MAX_FIELD = 500
DESIGNATIONS = [("UNCLASSIFIED", "بدون وجهة"), ("MELT", "للسبك"), ("LOGO", "لوجو")]
SOURCES = [("RAW", "ذهب غير مسبوك"), ("LOGO", "لوجو"), ("BAR", "سبيكة السبك (مكافئ 21)")]
LOSS = [("CRUCIBLE", "خسية بوتقة"), ("MARKET", "خسية سوق")]
SALE_SOURCES = [("CUSTODY_GOLD", "ذهب من سطر عيار"), ("TRIP_BAR", "سبيكة السبك"), ("CUSTODY_LOT", "شحنة معروفة العيار")]
PRICE_MODES = [("TOTAL", "إجمالي الصفقة"), ("PER_GRAM", "سعر الجرام")]
PAY_METHODS = [("CUSTODY", "من نقدية العهدة"), ("BANK", "من حساب بنك / محفظة")]
WAYS = [("INCREASE", "الوزن الفعلي أكبر من المسجّل"), ("DECREASE", "الوزن الفعلي أقل من المسجّل")]


def _str(value):
    return str(value).strip() if value is not None else ""


def _dec(value):
    return str(Decimal(str(value))) if value is not None else None


def _money(value):
    return f"{Decimal(str(value)):,.2f}"


def _grams(value):
    return f"{Decimal(str(value)):,.3f}"


def _side(delta, text):
    """One sign convention of the supplier ledgers: delta > 0 = the shop owes the supplier (علينا للمورد), delta < 0 = the supplier owes the shop (لنا عند المورد)."""
    value = Decimal(str(delta))
    return text + (" علينا للمورد" if value > 0 else " لنا عند المورد" if value < 0 else " (متوازن)")


# ---------------------------------------------------------------------------------------------- field helpers (the spec the page draws)
def F(k, label, kind="text", options=None, required=True, hint=None, trip=False):
    return {"k": k, "label": label, "type": kind, "options": options, "required": required, "hint": hint, "tripFilter": trip}


TRIP = F("trip_id", "العهدة", "select", "trips")
NOTES = F("notes", "ملاحظة", "text", required=False)
DATE = F("date", "التاريخ (اتركه فارغًا لليوم)", "date", required=False)
GOLD_LINES = lambda label, cols: {"k": "lines", "label": label, "type": "lines", "cols": cols, "required": True}
KARAT_W = [F("karat_id", "العيار", "select", "karats"), F("weight", "الوزن (جرام)", "grams")]
KARAT_W_DES = [F("karat_id", "العيار", "select", "karats"), F("designation", "الوجهة", "select", "designations", required=False), F("weight", "الوزن (جرام)", "grams")]
SOURCE_COLS = [F("source", "المصدر", "select", "sources"), F("karat_id", "العيار (للذهب واللوجو)", "select", "karats", required=False), F("weight", "الوزن (جرام)", "grams")]


class Ctx:
    """What an operation's `run` receives: the services, the verified actor, today's date, the request key and the validated values."""

    def __init__(self, session, actor, today, key, v, lines):
        from services.market_trip_service import MarketTripService
        from services.supplier_gold_settlement_service import SupplierGoldSettlementService
        from services.supplier_payment_service import SupplierPaymentService
        from services.intermediary_holding_service import IntermediaryHoldingService
        from services.marketer_custody_service import MarketerCustodyService
        from services.supplier_purchase_service import SupplierPurchaseService
        from services.supplier_service import SupplierService
        self.purchases = SupplierPurchaseService(session)
        self.trips, self.payments, self.settlements = MarketTripService(session), SupplierPaymentService(session), SupplierGoldSettlementService(session)
        self.marketers, self.holdings, self.suppliers = MarketerCustodyService(session), IntermediaryHoldingService(session), SupplierService(session)
        self.session, self.actor, self.key, self.v, self.lines = session, actor, key, v, lines
        self.date = v.get("date") or today

    def call(self, name, *args, **kwargs):
        return getattr(self.trips, name)(*args, _expected_actor=self.actor, **kwargs)


def _gold(lines):
    return [{"karat_id": r.get("karat_id", ""), "weight": r.get("weight", ""), **({"designation": r["designation"]} if r.get("designation") else {})} for r in lines]


def _groups(lines):
    return [{"description": r.get("description", ""), "weight": r.get("weight", ""), "making_per_gram": r.get("making_per_gram", "")} for r in lines]


def _doc(text):
    """The numeric id inside a document code typed by the owner (SM000009 -> 9)."""
    digits = "".join(ch for ch in str(text) if ch.isdigit())
    if not digits or int(digits) <= 0:
        raise ValueError("رقم المستند غير صحيح.")
    return str(int(digits))


def _safe_id(c):
    safe = c.trips.context(_expected_actor=c.actor).get("safe")
    if not safe or not safe.get("active"):
        raise ValueError("الخزنة الرئيسية غير متاحة.")
    return safe["id"]


# every operation: key -> (group, title, fields, run). `run(c)` returns the service's result dict.
OPS = {
    "trip_create": ("عهدة", "عهدة جديدة", [F("cash", "نقدية من الخزنة (ج)", "money", required=False), GOLD_LINES("كسر من المحل", KARAT_W), NOTES, DATE],
                    lambda c: c.call("create_trip", c.v.get("cash") or "0", _gold(c.lines), [], c.date, c.v.get("notes", ""), c.key)),
    "trip_issue": ("عهدة", "إخراج عهدة إضافية على عهدة مفتوحة", [TRIP, F("cash", "نقدية من الخزنة (ج)", "money", required=False), GOLD_LINES("كسر من المحل", KARAT_W), NOTES, DATE],
                   lambda c: c.call("issue", c.v["trip_id"], c.v.get("cash") or "0", _gold(c.lines), c.date, c.v.get("notes", ""), c.key)),
    "trip_return": ("عهدة", "رجوع عهدة للمحل", [TRIP, F("cash", "نقدية راجعة للخزنة (ج)", "money", required=False), GOLD_LINES("ذهب راجع", KARAT_W_DES), NOTES, DATE],
                    lambda c: c.call("return_custody", c.v["trip_id"], c.v.get("cash") or "0", _gold(c.lines), c.date, c.v.get("notes", ""), c.key)),
    "trip_close": ("عهدة", "إغلاق عهدة", [TRIP],
                   lambda c: c.call("close", c.v["trip_id"])),
    "trip_reopen": ("عهدة", "إعادة فتح عهدة مغلقة", [F("trip_id", "العهدة المغلقة", "select", "closed_trips"), F("reason", "السبب", "text")],
                    lambda c: c.call("reopen", c.v["trip_id"], c.v["reason"])),
    "trip_reverse": ("عهدة", "عكس حركة من العهدة (RV)", [TRIP, F("entry_id", "الحركة", "select", "entries", trip=True), F("reason", "سبب العكس", "text")],
                     lambda c: c.call("reverse", c.v["entry_id"], c.v["reason"])),
    "trip_pay_supplier": ("مصنعيات", "دفع مصنعيات مورد من العهدة", [TRIP, F("supplier_id", "المورد", "select", "suppliers"), F("amount", "المبلغ (ج)", "money"), NOTES],
                          lambda c: c.payments.pay(c.v["supplier_id"], "CUSTODY", c.v["trip_id"], c.v["amount"], c.date, "", c.v.get("notes", ""), c.key, _expected_actor=c.actor)),
    "trip_bar_fee": ("مصنعيات", "أجر سبيكة من مورد أو مسوقجي", [TRIP, F("party_id", "الطرف", "select", "parties"), F("amount", "الأجر (ج)", "money"), NOTES, DATE],
                     lambda c: c.call("bar_fee", c.v["trip_id"], c.v["party_id"], c.v["amount"], c.date, "", c.v.get("notes", ""), c.key)),
    "trip_expense": ("نقدية العهدة", "مصروف من العهدة", [TRIP, F("amount", "المبلغ (ج)", "money"), F("description", "بيان المصروف", "text"), NOTES, DATE],
                     lambda c: c.call("record_custody_expense", c.v["trip_id"], c.v["amount"], c.v["description"], c.date, "", c.v.get("notes", ""), c.key)),
    "trip_deposit": ("نقدية العهدة", "إيداع باقي النقدية في بنك / محفظة", [TRIP, F("amount", "المبلغ (ج)", "money"), F("bank_id", "الحساب", "select", "banks"), NOTES, DATE],
                     lambda c: c.call("deposit_custody_cash", c.v["trip_id"], c.v["amount"], c.v["bank_id"], c.date, "", c.v.get("notes", ""), c.key)),
    "trip_writeoff": ("نقدية العهدة", "إعدام نقدية من العهدة", [TRIP, F("amount", "المبلغ (ج)", "money"), F("reason", "السبب", "text"), NOTES, DATE],
                      lambda c: c.call("write_off_custody_cash", c.v["trip_id"], c.v["amount"], c.v["reason"], c.date, c.v.get("notes", ""), c.key)),
    "trip_buy": ("شراء وبيع", "شراء ذهب نقدي", [TRIP, GOLD_LINES("الذهب المشترى", [F("karat_id", "العيار", "select", "karats"), F("weight", "الوزن (جرام)", "grams"), F("price", "ثمن السطر (ج)", "money")]),
                                          F("vendor_party_id", "التاجر (مسجّل)", "select", "parties", required=False), F("vendor_name", "أو اسم التاجر", "text", required=False),
                                          F("payment_method", "الدفع", "select", "pay_methods", required=False), F("bank_id", "الحساب (للبنك)", "select", "banks", required=False),
                                          F("ack", "أؤكد السعر البعيد عن مرجع الشراء", "check", required=False), NOTES, DATE],
                 lambda c: c.call("buy_gold", c.v["trip_id"], c.lines, c.v.get("vendor_party_id", ""), c.v.get("vendor_name", ""), c.date, "", c.v.get("notes", ""), c.key,
                                  c.v.get("ack") == "1", payment_method=c.v.get("payment_method") or "CUSTODY", bank_account_id=c.v.get("bank_id") or None)),
    "trip_sell": ("شراء وبيع", "بيع ذهب من العهدة", [TRIP, F("source_kind", "المصدر", "select", "sale_sources"), F("karat_id", "العيار (لسطر عيار)", "select", "karats", required=False),
                                                 F("designation", "الوجهة (لسطر عيار)", "select", "designations", required=False), F("lot_id", "الشحنة (لشحنة)", "select", "lots", required=False, trip=True),
                                                 F("weight", "الوزن (جرام)", "grams", required=False), F("price_mode", "التسعير", "select", "price_modes"), F("price", "السعر", "money"),
                                                 F("buyer_party_id", "المشتري (مسجّل)", "select", "parties", required=False), F("buyer_name", "أو اسم المشتري", "text", required=False),
                                                 F("payment_method", "الاستلام", "select", "pay_methods", required=False), F("bank_id", "الحساب (للبنك)", "select", "banks", required=False),
                                                 F("ack", "أؤكد السعر البعيد عن مرجع البيع", "check", required=False), NOTES, DATE],
                 lambda c: c.call("sell_gold", c.v["trip_id"], c.v["source_kind"], c.v.get("karat_id", ""), c.v.get("designation", ""), c.v.get("lot_id", ""), c.v.get("weight", ""),
                                  c.v["price_mode"], c.v["price"], c.v.get("buyer_party_id", ""), c.v.get("buyer_name", ""), c.date, "", c.v.get("notes", ""), c.key, c.v.get("ack") == "1",
                                  payment_method=c.v.get("payment_method") or "CUSTODY", bank_account_id=c.v.get("bank_id") or None)),
    "trip_melt_settle": ("سبك وتسخين", "تسجيل سبك بعد الرجوع", [TRIP, GOLD_LINES("الذهب المسبوك", KARAT_W), F("output_weight", "وزن السبيكة الفعلي (جرام)", "grams"),
                                                           F("fineness", "العيار الفعلي بالألف (مثلًا 875)", "money"), F("workshop", "الورشة / المسؤول", "text"), NOTES, DATE],
                         lambda c: c.call("settle_melt", c.v["trip_id"], _gold(c.lines), c.v["output_weight"], c.v["fineness"], c.v["workshop"], c.date, c.v.get("notes", ""), c.key)),
    "trip_melt": ("سبك وتسخين", "سبك ذهب مخصص للسبك (شحنات معايرة)", [TRIP, GOLD_LINES("المدخلات", KARAT_W), F("output_weight", "وزن الناتج الرئيسي (جرام)", "grams"),
                                                                    F("output_fineness", "عيار الناتج بالألف", "money"), F("residue_weight", "وزن راسب (اختياري)", "grams", required=False),
                                                                    F("residue_fineness", "عيار الراسب بالألف (اختياري)", "money", required=False), F("residue_label", "وصف الراسب", "text", required=False),
                                                                    F("workshop", "الورشة / المسؤول", "text"), F("assay", "مرجع المعايرة", "text", required=False),
                                                                    F("gain", "سبب زيادة الوزن (لو الناتج أكبر)", "text", required=False), NOTES, DATE],
                  lambda c: c.call("melt", c.v["trip_id"], _gold(c.lines), c.date, c.v["workshop"], {"weight": c.v["output_weight"], "fineness": c.v["output_fineness"]},
                                   [{"weight": c.v.get("residue_weight", ""), "fineness": c.v.get("residue_fineness", ""), "label": c.v.get("residue_label", "")}],
                                   c.v.get("assay", ""), c.v.get("gain", ""), c.v.get("notes", ""), c.key)),
    "trip_heat": ("سبك وتسخين", "حمّية اللوجو", [TRIP, GOLD_LINES("الأوزان", [F("karat_id", "العيار", "select", "karats"), F("input_weight", "قبل الحمّية (جرام)", "grams"), F("output_weight", "بعد الحمّية (جرام)", "grams")]),
                                              F("workshop", "الورشة / المسؤول", "text"), F("reference", "مرجع", "text", required=False), F("gain", "سبب زيادة الوزن (لو زاد)", "text", required=False), NOTES, DATE],
                  lambda c: c.call("heat", c.v["trip_id"], c.lines, c.date, c.v["workshop"], c.v.get("reference", ""), c.v.get("gain", ""), c.v.get("notes", ""), c.key)),
    "trip_logo": ("سبك وتسخين", "اعتبار المتبقي لوجو", [TRIP, NOTES, DATE],
                  lambda c: c.call("classify_remaining_as_logo", c.v["trip_id"], c.date, c.v.get("notes", ""), c.key)),
    "trip_adjust": ("تصحيح", "تصحيح وزن الإخراج", [TRIP, F("karat_id", "العيار", "select", "karats"), F("way", "الاتجاه", "select", "ways"), F("weight", "الفرق (جرام)", "grams"), F("reason", "السبب", "text"), DATE],
                    lambda c: c.call("correct_issue_weight", c.v["trip_id"], c.v["karat_id"], c.v["way"], c.v["weight"], c.v["reason"], c.date, c.key)),
    "trip_return_shop": ("رجوع وخسية", "مرتجع سوق للمحل (بعد الرحلة)", [TRIP, GOLD_LINES("الراجع", SOURCE_COLS), NOTES, DATE],
                         lambda c: c.call("return_to_shop", c.v["trip_id"], c.lines, c.date, c.v.get("notes", ""), c.key)),
    "trip_loss": ("رجوع وخسية", "تسجيل خسية", [TRIP, GOLD_LINES("الخسية", SOURCE_COLS + [F("classification", "التصنيف", "select", "loss_classes")]), NOTES, DATE],
                  lambda c: c.call("record_loss", c.v["trip_id"], c.lines, c.date, c.v.get("notes", ""), c.key)),
    "trip_supplier_gold": ("تسوية موردين", "تسوية ذهب مورد من العهدة", [TRIP, F("supplier_id", "المورد", "select", "suppliers"), F("karat_id", "العيار", "select", "karats"),
                                                                   F("designation", "الوجهة", "select", "designations"), F("weight", "الوزن (جرام)", "grams"), NOTES, DATE],
                          lambda c: c.call("settle_supplier_gold", c.v["trip_id"], c.v["supplier_id"], c.v["karat_id"], c.v["designation"], c.v["weight"], c.date, "", c.v.get("notes", ""), c.key)),
    "trip_supplier_bar": ("تسوية موردين", "تسليم سبيكة السبك لمورد", [TRIP, F("supplier_id", "المورد", "select", "suppliers"), F("weight", "الوزن (مكافئ 21، جرام)", "grams"), NOTES, DATE],
                          lambda c: c.call("settle_supplier_bar", c.v["trip_id"], c.v["supplier_id"], c.v["weight"], c.date, "", c.v.get("notes", ""), c.key)),
    "trip_supplier_lot": ("تسوية موردين", "تسوية شحنة معايرة لمورد", [TRIP, F("supplier_id", "المورد", "select", "suppliers"), F("lot_id", "الشحنة", "select", "lots", trip=True), NOTES, DATE],
                          lambda c: c.call("settle_supplier_lot", c.v["trip_id"], c.v["supplier_id"], c.v["lot_id"], c.date, "", c.v.get("notes", ""), c.key)),
    "trip_bar_marketer": ("مسوقجي", "تسليم سبيكة السبك لمسوقجي", [TRIP, F("party_id", "المسوقجي", "select", "intermediaries"), F("weight", "الوزن (مكافئ 21، جرام)", "grams"), NOTES, DATE],
                          lambda c: c.call("settle_bar_to_marketer", c.v["trip_id"], c.v["party_id"], c.v["weight"], c.date, c.v.get("notes", ""), c.key)),
    "trip_lot_return": ("شحنات", "رجوع شحنة معايرة للمحل", [TRIP, F("lot_id", "الشحنة", "select", "lots", trip=True), NOTES, DATE],
                        lambda c: c.call("return_lots", c.v["trip_id"], [{"lot_id": c.v["lot_id"]}], c.date, c.v.get("notes", ""), c.key)),
    "trip_lot_issue": ("شحنات", "إخراج شحنة من المحل لعهدة", [TRIP, F("lot_id", "الشحنة (في المحل)", "select", "shop_lots"), NOTES, DATE],
                       lambda c: c.call("issue_lots", c.v["trip_id"], [{"lot_id": c.v["lot_id"]}], c.date, c.v.get("notes", ""), c.key)),
    "sup_pay_cash": ("موردين", "دفع مصنعيات مورد من الخزنة", [F("supplier_id", "المورد", "select", "suppliers"), F("amount", "المبلغ (ج)", "money"), NOTES, DATE],
                     lambda c: c.payments.pay(c.v["supplier_id"], "CASH", _safe_id(c), c.v["amount"], c.date, "", c.v.get("notes", ""), c.key, _expected_actor=c.actor)),
    "sup_pay_bank": ("موردين", "دفع مصنعيات مورد من بنك / محفظة", [F("supplier_id", "المورد", "select", "suppliers"), F("bank_id", "الحساب", "select", "banks"), F("amount", "المبلغ (ج)", "money"), NOTES, DATE],
                     lambda c: c.payments.pay(c.v["supplier_id"], "BANK", c.v["bank_id"], c.v["amount"], c.date, "", c.v.get("notes", ""), c.key, _expected_actor=c.actor)),
    "sup_gold_settle": ("موردين", "تسوية ذهب مورد من الكسر", [F("supplier_id", "المورد", "select", "suppliers"), F("karat_id", "عيار الكسر المُسلَّم", "select", "karats"), F("weight", "الوزن (جرام)", "grams"), NOTES, DATE],
                        lambda c: c.settlements.settle(c.v["supplier_id"], c.v["karat_id"], c.v["weight"], c.date, "", c.v.get("notes", ""), c.key, _expected_actor=c.actor)),
    "sup_receive_cash": ("موردين", "استلام فلوس من مورد (إلى الخزنة)", [F("supplier_id", "المورد", "select", "suppliers"), F("amount", "المبلغ (ج)", "money"), NOTES, DATE],
                         lambda c: c.payments.receive(c.v["supplier_id"], "CASH", _safe_id(c), c.v["amount"], c.date, "", c.v.get("notes", ""), c.key, _expected_actor=c.actor)),
    "sup_receive_bank": ("موردين", "استلام فلوس من مورد (إلى بنك / محفظة)", [F("supplier_id", "المورد", "select", "suppliers"), F("bank_id", "الحساب", "select", "banks"), F("amount", "المبلغ (ج)", "money"), NOTES, DATE],
                         lambda c: c.payments.receive(c.v["supplier_id"], "BANK", c.v["bank_id"], c.v["amount"], c.date, "", c.v.get("notes", ""), c.key, _expected_actor=c.actor)),
    "sup_reverse_payment": ("موردين", "عكس دفعة / استلام مصنعيات (SM)", [F("doc", "رقم المستند (مثل SM000009)", "text"), F("reason", "سبب العكس", "text")],
                            lambda c: c.payments.reverse(_doc(c.v["doc"]), c.v["reason"], _expected_actor=c.actor)),
    "sup_reverse_gold": ("موردين", "عكس تسوية ذهب مورد (SG)", [F("doc", "رقم المستند (مثل SG000004)", "text"), F("reason", "سبب العكس", "text")],
                         lambda c: c.settlements.reverse(_doc(c.v["doc"]), c.v["reason"], _expected_actor=c.actor)),
    "sup_create": ("موردين", "إضافة مورد أو مسوقجي", [F("name", "الاسم", "text"), F("phone", "الموبايل", "text", required=False), F("karat_id", "عيار حساب المورد", "select", "karats", required=False),
                                                   F("role", "الدور", "select", "roles"), NOTES],
                   lambda c: c.suppliers.create_supplier(c.v["name"], c.v.get("phone") or None, c.v.get("notes") or None, c.v.get("karat_id") or None,
                                                         is_supplier=c.v["role"] in ("SUPPLIER", "BOTH"), is_intermediary=c.v["role"] in ("MARKETER", "BOTH"), _expected_actor=c.actor)),
    "inv_create": ("فواتير الموردين", "فاتورة مورد جديدة (مسودة)", [F("supplier_id", "المورد", "select", "suppliers"), F("reference", "رقم فاتورة المورد", "text", required=False),
                                                                  F("invoice_date", "تاريخ الفاتورة (سنة-شهر-يوم)", "date"),
                                                                  GOLD_LINES("مجموعات المصنعية", [F("description", "الوصف", "text", required=False), F("weight", "وزن المورد (جرام)", "grams"),
                                                                                                F("making_per_gram", "المصنعية (ج/جرام)", "money")]), NOTES],
                   lambda c: c.purchases.create_draft(c.v["supplier_id"], None, c.v.get("reference", ""), c.v["invoice_date"], _groups(c.lines), c.v.get("notes", ""), c.key, _expected_actor=c.actor)),
    "inv_update": ("فواتير الموردين", "تعديل مسودة فاتورة مورد", [F("invoice_id", "المسودة", "select", "draft_invoices"), F("supplier_id", "المورد", "select", "suppliers"),
                                                              F("reference", "رقم فاتورة المورد", "text", required=False), F("invoice_date", "تاريخ الفاتورة (سنة-شهر-يوم)", "date"),
                                                              GOLD_LINES("مجموعات المصنعية (تحل محل القديمة)", [F("description", "الوصف", "text", required=False), F("weight", "وزن المورد (جرام)", "grams"),
                                                                                                             F("making_per_gram", "المصنعية (ج/جرام)", "money")]), NOTES],
                   lambda c: c.purchases.update_draft(c.v["invoice_id"], c.v["supplier_id"], None, c.v.get("reference", ""), c.v["invoice_date"], _groups(c.lines), c.v.get("notes", ""), _expected_actor=c.actor)),
    "inv_post": ("فواتير الموردين", "ترحيل فاتورة مورد", [F("invoice_id", "المسودة", "select", "draft_invoices")],
                 lambda c: c.purchases.post_invoice(c.v["invoice_id"], c.key, _expected_actor=c.actor)),
    "inv_cancel": ("فواتير الموردين", "إلغاء مسودة", [F("invoice_id", "المسودة", "select", "draft_invoices")],
                   lambda c: c.purchases.cancel_draft(c.v["invoice_id"], _expected_actor=c.actor)),
    "inv_intake": ("فواتير الموردين", "إدخال قطعة من مجموعة مورد", [F("group", "المجموعة", "select", "intake_groups"), F("item_type_id", "الصنف", "select", "item_types"), F("weight", "وزن القطعة (جرام)", "grams"),
                                                                 F("barcode_mode", "الباركود", "select", "barcode_modes"), F("barcode", "الباركود (لو يدوي)", "text", required=False), F("size", "المقاس", "text", required=False), NOTES],
                  lambda c: c.purchases.add_piece(c.v["group"].split(":")[0], c.v["group"].split(":")[1], c.v["item_type_id"], c.v["weight"], c.key, size=c.v.get("size") or None,
                                                  barcode_mode=c.v["barcode_mode"], barcode=c.v.get("barcode") or None, notes=c.v.get("notes") or None, _expected_actor=c.actor)),
    "inv_close_group": ("فواتير الموردين", "إخفاء مجموعة من قايمة الإدخال", [F("group", "المجموعة", "select", "intake_groups")],
                        lambda c: c.purchases.close_group(c.v["group"].split(":")[1], _expected_actor=c.actor)),
    "inv_adjust_group": ("فواتير الموردين", "تسوية وزن مجموعة في فاتورة مرحّلة", [F("invoice_id", "الفاتورة", "select", "posted_invoices"), F("group_id", "المجموعة", "select", "groups"),
                                                                              F("direction", "الاتجاه", "select", "adjust_ways"), F("weight", "الوزن (جرام)", "grams"), F("reason", "السبب", "text")],
                         lambda c: c.purchases.adjust_group(c.v["invoice_id"], c.v["group_id"], c.v["direction"], c.v["weight"], c.v["reason"], c.key, _expected_actor=c.actor)),
    "inv_reverse": ("فواتير الموردين", "عكس فاتورة مورد مرحّلة", [F("invoice_id", "الفاتورة", "select", "posted_invoices"), F("reason", "سبب العكس", "text")],
                    lambda c: c.purchases.reverse_invoice(c.v["invoice_id"], c.v["reason"], _expected_actor=c.actor)),
    "sup_adjust_account": ("فواتير الموردين", "تسوية فروق حساب مورد (ذهب / مصنعيات)", [F("supplier_id", "المورد", "select", "suppliers"), F("kind", "الحساب", "select", "account_kinds"),
                                                                                  F("direction", "الاتجاه", "select", "adjust_ways"), F("amount", "الوزن (جرام) أو المبلغ (ج)", "money"), F("reason", "السبب", "text")],
                          lambda c: c.purchases.adjust_account(c.v["supplier_id"], c.v["kind"], c.v["direction"], c.v["amount"], c.v["reason"], c.key, _expected_actor=c.actor)),
    "mk_cash_handover": ("مسوقجي", "تسليم نقدية لمسوقجي (من الخزنة)", [F("party_id", "المسوقجي", "select", "intermediaries"), F("amount", "المبلغ (ج)", "money"), NOTES, DATE],
                         lambda c: c.marketers.cash_handover(c.v["party_id"], c.v["amount"], c.date, "", c.v.get("notes", ""), c.key, _expected_actor=c.actor)),
    "mk_cash_return": ("مسوقجي", "استلام نقدية راجعة من مسوقجي", [F("party_id", "المسوقجي", "select", "intermediaries"), F("amount", "المبلغ (ج)", "money"), NOTES, DATE],
                       lambda c: c.marketers.cash_return(c.v["party_id"], c.v["amount"], c.date, "", c.v.get("notes", ""), c.key, _expected_actor=c.actor)),
    "mk_gold_return": ("مسوقجي", "استلام ذهب عيار 21 راجع من مسوقجي", [F("party_id", "المسوقجي", "select", "intermediaries"), F("weight", "الوزن (جرام)", "grams"), NOTES, DATE],
                       lambda c: c.marketers.gold_return(c.v["party_id"], c.v["weight"], c.date, "", c.v.get("notes", ""), c.key, _expected_actor=c.actor)),
    "mk_purchase": ("مسوقجي", "شراء ذهب 21 بنقدية المسوقجي", [F("party_id", "المسوقجي", "select", "intermediaries"), GOLD_LINES("الذهب المشترى", [F("weight", "الوزن (جرام)", "grams"), F("price", "ثمن السطر (ج)", "money")]),
                                                         F("vendor_party_id", "التاجر (مسجّل)", "select", "parties", required=False), F("vendor_name", "أو اسم التاجر", "text", required=False),
                                                         F("ack", "أؤكد السعر البعيد عن المرجع", "check", required=False), NOTES, DATE],
                   lambda c: c.marketers.purchase(c.v["party_id"], c.lines, c.v.get("vendor_party_id", ""), c.v.get("vendor_name", ""), c.date, "", c.v.get("notes", ""), c.key,
                                                  c.v.get("ack") == "1", _expected_actor=c.actor)),
    "mk_sale": ("مسوقجي", "بيع ذهب 21 من رصيد المسوقجي", [F("party_id", "المسوقجي", "select", "intermediaries"), F("weight", "الوزن (جرام)", "grams"), F("price_mode", "التسعير", "select", "price_modes"),
                                                      F("price", "السعر", "money"), F("buyer_party_id", "المشتري (مسجّل)", "select", "parties", required=False), F("buyer_name", "أو اسم المشتري", "text", required=False),
                                                      F("ack", "أؤكد السعر البعيد عن المرجع", "check", required=False), NOTES, DATE],
                lambda c: c.marketers.sale(c.v["party_id"], c.v["weight"], c.v["price_mode"], c.v["price"], c.v.get("buyer_party_id", ""), c.v.get("buyer_name", ""), c.date, "",
                                           c.v.get("notes", ""), c.key, c.v.get("ack") == "1", _expected_actor=c.actor)),
    "mk_settle_supplier": ("مسوقجي", "تسوية ذهب مورد من رصيد المسوقجي", [F("party_id", "المسوقجي", "select", "intermediaries"), F("supplier_id", "المورد", "select", "suppliers"),
                                                                    F("weight", "الوزن (جرام عيار 21)", "grams"), F("self_confirmed", "أؤكد إن المسوقجي والمورد نفس الطرف (لو كان كده)", "check", required=False), NOTES, DATE],
                           lambda c: c.marketers.settle_supplier(c.v["party_id"], c.v["supplier_id"], c.v["weight"], c.v.get("self_confirmed") == "1", c.date, "", c.v.get("notes", ""), c.key,
                                                                 _expected_actor=c.actor)),
    "mk_reverse": ("مسوقجي", "عكس مستند مسوقجي (رقم المستند)", [F("doc", "رقم المستند (مثل MH000003)", "text"), F("reason", "سبب العكس", "text")],
                   lambda c: c.marketers.reverse(_doc(c.v["doc"]), c.v["reason"], _expected_actor=c.actor)),
    "mk_reverse_settlement": ("مسوقجي", "عكس تسوية ذهب مسوقجي لمورد (SG)", [F("doc", "رقم المستند (مثل SG000004)", "text"), F("reason", "سبب العكس", "text")],
                              lambda c: c.marketers.reverse_settlement(_doc(c.v["doc"]), c.v["reason"], _expected_actor=c.actor)),
    "hold_logo": ("مسوقجي", "تسليم لوجو من العهدة لمسوقجي", [TRIP, F("party_id", "المسوقجي", "select", "intermediaries"), F("karat_id", "العيار", "select", "karats"), F("weight", "الوزن (جرام)", "grams"), NOTES, DATE],
                  lambda c: c.holdings.hand_over_logo(c.v["trip_id"], c.v["party_id"], c.v["karat_id"], c.v["weight"], c.date, "", c.v.get("notes", ""), c.key, _expected_actor=c.actor)),
    "hold_lot": ("مسوقجي", "تسليم شحنة معايرة من العهدة لمسوقجي", [TRIP, F("party_id", "المسوقجي", "select", "intermediaries"), F("lot_id", "الشحنة", "select", "lots", trip=True), NOTES, DATE],
                 lambda c: c.holdings.hand_over_lot(c.v["trip_id"], c.v["party_id"], c.v["lot_id"], c.date, "", c.v.get("notes", ""), c.key, _expected_actor=c.actor)),
}
OPERATIONS = frozenset(OPS)


def spec(key):
    group, title, fields, _ = OPS[key]
    return {"key": key, "group": group, "title": title, "fields": fields}


class Market:
    def __init__(self, api):
        self._api = api

    # ------------------------------------------------------------------ one read for the whole card
    def overview(self, token):
        from services.market_trip_service import CLOSED, OPEN, MarketTripService
        from services.supplier_service import SupplierService
        entry = self._api._entry(token)
        session, actor = entry.session, entry.session.current_actor()
        service = MarketTripService(session)
        try:
            context = service.context(_expected_actor=actor)
            page = service.trip_page(OPEN, 1, 30, _expected_actor=actor)
            closed = service.trip_page(CLOSED, 1, 15, _expected_actor=actor)
        except PermissionError as exc:
            raise ApiError(403, str(exc) or "عهدة السوق للمالك فقط.") from None
        except ValueError as exc:
            raise ApiError(400, str(exc)) from None
        except Exception:
            logger.exception("market overview failed")
            raise ApiError(500, "تعذر تحميل عهدة السوق؛ أعد المحاولة.") from None
        trips, lots = [], []
        for row in page["rows"]:
            custody = row["custody"]
            trips.append({"id": row["id"], "code": row["code"], "notes": row["notes"], "settled": bool(custody["settled"]),
                          "cashText": _money(custody["cash"]["outstanding"]),
                          "gold": [{"karat": g["karat_value"], "designation": g["designation"], "text": _grams(g["outstanding"])} for g in custody["gold"] if g["outstanding"] != 0],
                          "bar": _grams(custody["bar"]) if custody["bar"] else "", "lots": len(custody["lots"]), "held": len(row.get("external_holdings") or [])})
            for lot in custody["lots"]:
                lots.append({"v": lot["lot_id"], "t": f"{row['code']} • شحنة {lot['lot_no']} • {_grams(lot['outstanding'])} جرام" + (f" • عيار {lot['fineness']}‰" if lot["fineness"] is not None else " • عيار غير معروف"),
                             "trip": row["id"]})
        shop_lots = []
        try:
            shop_lots = [{"v": r["lot_id"], "t": f"{r['code']} • شحنة {r['lot_no']} • {_grams(r['stock_grams'])} جرام" if "stock_grams" in r else f"{r['code']} • شحنة {r['lot_no']}"} for r in service.shop_lots(_expected_actor=actor)]
        except Exception:
            logger.exception("market overview: shop lots failed")
        suppliers, parties = [], [{"v": b["id"], "t": b["name"] + ("" if b["active"] else " (موقوف)")} for b in context.get("buyers", [])]
        try:
            for s in SupplierService(session).list_suppliers("", False, None, _expected_actor=actor):
                if not s["is_supplier"] or not s["is_active"]:
                    continue
                suppliers.append({"id": s["id"], "name": s["name"], "karat": s["karat_value"],
                                  "gold": None if s["gold_balance"] is None else _side(s["gold_balance"], _grams(abs(Decimal(str(s["gold_balance"]))))),
                                  "making": _money(abs(Decimal(str(s["making"])))), "makingSide": _side(s["making"], _money(abs(Decimal(str(s["making"])))))})
        except PermissionError:
            pass
        except Exception:
            logger.exception("market overview: suppliers failed")
        invoices, intake, item_types = [], [], []
        try:
            invoices = self._invoices(session, actor)
        except Exception:
            logger.exception("market overview: supplier invoices failed")
        try:
            from services.supplier_purchase_service import SupplierPurchaseService
            intake = [{"v": g["invoice_id"] + ":" + g["group_id"], "t": f"{g['invoice_code']} • {g['supplier_name']} • مجموعة {g['group_no']} • الباقي {_grams(g['remaining'])} جرام"}
                      for g in SupplierPurchaseService(session).open_groups(_expected_actor=actor)]
        except Exception:
            logger.exception("market overview: intake groups failed")
        try:
            from services.inventory_service import InventoryService
            item_types = [{"v": str(r["id"]), "t": r["name"]} for r in InventoryService().get_stocktake_filters().get("item_types", [])]
        except Exception:
            logger.exception("market overview: item types failed")
        safe = context.get("safe")
        options = {
            "draft_invoices": [{"v": i["id"], "t": f"{i['code']} • {i['supplier_name']} • {i['weight']} جرام"} for i in invoices if i["status"] == "DRAFT"],
            "posted_invoices": [{"v": i["id"], "t": f"{i['code']} • {i['supplier_name']} • {i['weight']} جرام"} for i in invoices if i["status"] == "POSTED" and not i["reversed"]],
            "intake_groups": intake, "item_types": item_types,
            "barcode_modes": [{"v": "AUTO", "t": "تلقائي"}, {"v": "MANUAL", "t": "يدوي"}, {"v": "NONE", "t": "بدون باركود"}],
            "adjust_ways": [{"v": "ADD", "t": "زيادة (علينا للمورد أكتر)"}, {"v": "DEDUCT", "t": "نقص (علينا للمورد أقل)"}],
            "account_kinds": [{"v": "GOLD", "t": "ذهب (جرام بعيار المورد)"}, {"v": "MAKING", "t": "مصنعيات (ج)"}],
            "trips": [{"v": t["id"], "t": t["code"] + " • نقدية " + t["cashText"] + " ج" + (" • " + t["notes"] if t["notes"] else "")} for t in trips],
            "closed_trips": [{"v": r["id"], "t": r["code"]} for r in closed["rows"]],
            "suppliers": [{"v": s["id"], "t": s["name"] + " • مصنعيات " + s["makingSide"]} for s in suppliers],
            "karats": [{"v": str(k["id"]), "t": "عيار " + str(k["karat_value"])} for k in context["karats"]],
            "banks": [{"v": b["id"], "t": b["name"]} for b in context.get("banks", [])],
            "parties": parties, "intermediaries": [{"v": p["id"], "t": p["name"]} for p in context.get("intermediaries", [])],
            "lots": lots, "shop_lots": shop_lots,
            "designations": [{"v": v, "t": t} for v, t in DESIGNATIONS], "sources": [{"v": v, "t": t} for v, t in SOURCES], "loss_classes": [{"v": v, "t": t} for v, t in LOSS],
            "sale_sources": [{"v": v, "t": t} for v, t in SALE_SOURCES], "price_modes": [{"v": v, "t": t} for v, t in PRICE_MODES],
            "pay_methods": [{"v": v, "t": t} for v, t in PAY_METHODS], "ways": [{"v": v, "t": t} for v, t in WAYS],
            "roles": [{"v": "SUPPLIER", "t": "مورد"}, {"v": "MARKETER", "t": "مسوقجي"}, {"v": "BOTH", "t": "مورد ومسوقجي"}]}
        return {"safe": None if not safe else {"id": safe["id"], "name": safe["name"], "text": _money(safe["balance"])},
                "scrap": [{"karat": r["karat_value"], "text": _grams(r["available"])} for r in context["shop_scrap"]],
                "trips": trips, "hasMore": bool(page["has_more"]), "suppliers": suppliers, "invoices": invoices[:25], "options": options, "ops": [spec(k) for k in OPS]}

    @staticmethod
    def _invoices(session, actor):
        """The 60 newest supplier invoices (drafts and posted), for the pickers and the table."""
        from services.supplier_purchase_service import SupplierPurchaseService
        rows = SupplierPurchaseService(session).list_invoices(_expected_actor=actor)[:60]
        return [{"id": r["id"], "code": r["code"], "supplier_name": r["supplier_name"], "date": str(r["invoice_date"]), "weight": _grams(r["supplier_weight"]), "making": _money(r["gross_making"]),
                 "status": r["status"], "reversed": bool(r["reversed"]), "pieces": r["piece_count"]} for r in rows]

    def groups(self, token, params):
        """The making groups of ONE supplier invoice, for the «تسوية وزن مجموعة» picker."""
        from services.supplier_purchase_service import SupplierPurchaseService
        entry = self._api._entry(token)
        try:
            detail = SupplierPurchaseService(entry.session).invoice_detail(_str(params.get("invoice")), _expected_actor=entry.session.current_actor())
        except PermissionError as exc:
            raise ApiError(403, str(exc)) from None
        except ValueError as exc:
            raise ApiError(400, str(exc)) from None
        except Exception:
            logger.exception("market groups failed")
            raise ApiError(500, "تعذر تحميل مجموعات الفاتورة.") from None
        return {"groups": [{"v": g["id"], "t": f"مجموعة {g['group_no']} • {_grams(g['effective_weight'])} جرام"} for g in detail["groups"]]}

    def entries(self, token, params):
        """The entries of ONE trip, for the «عكس حركة» picker (loaded when the trip is chosen)."""
        from services.market_trip_service import MarketTripService
        entry = self._api._entry(token)
        actor = entry.session.current_actor()
        try:
            detail = MarketTripService(entry.session).trip_detail(_str(params.get("trip")), _expected_actor=actor)
        except PermissionError as exc:
            raise ApiError(403, str(exc)) from None
        except ValueError as exc:
            raise ApiError(400, str(exc)) from None
        except Exception:
            logger.exception("market entries failed")
            raise ApiError(500, "تعذر تحميل حركات العهدة.") from None
        return {"entries": [{"v": e["id"], "t": f"{e['code']} • {e['kind']} • {e['date']}"} for e in detail.get("entries", [])]}

    # ------------------------------------------------------------------ the writes
    def op(self, token, body):
        entry = self._api._entry(token)
        session, actor = entry.session, entry.session.current_actor()
        operation = _str(body.get("op"))
        if operation not in OPS:
            raise ApiError(400, "عملية غير معروفة.")
        if body.get("confirmed") is not True:
            raise ApiError(400, "أكّد العملية أولًا.")
        fields = OPS[operation][2]
        raw = body.get("v") if isinstance(body.get("v"), dict) else {}
        values, lines = {}, []
        for field in fields:
            if field["type"] == "lines":
                lines = self._lines(body.get("lines"), field["cols"])
                if not lines:
                    raise ApiError(400, "أضف سطرًا واحدًا على الأقل.")
                continue
            text = _str(raw.get(field["k"]))
            if len(text) > MAX_FIELD:
                raise ApiError(400, "قيمة طويلة جدًا: " + field["label"])
            if field["type"] == "check":
                text = "1" if text == "1" else ""
            if field["required"] and not text:
                raise ApiError(400, "اكتب أو اختر: " + field["label"])
            values[field["k"]] = text
        password = body.get("password")
        try:
            self._api._authentication.verify_user_password(int(actor.user_id), password if isinstance(password, str) else "")
        except AuthenticationError:
            raise ApiError(400, "كلمة المرور غير صحيحة أو انتظر قليلاً ثم أعد المحاولة.") from None
        finally:
            password = None
        today = self._api._today()
        if values.get("date"):
            try:
                from datetime import date as _date
                values["date"] = _date.fromisoformat(values["date"])
            except ValueError:
                raise ApiError(400, "التاريخ غير صحيح (سنة-شهر-يوم).") from None
        ctx = Ctx(session, actor, today, _str(body.get("request_key")), values, lines)
        try:
            result = OPS[operation][3](ctx)
        except ApiError:
            raise
        except PermissionError as exc:
            raise ApiError(403, str(exc) or "ليس لديك صلاحية لهذه العملية.") from None
        except ValueError as exc:
            code = getattr(exc, "code", "")
            raise ApiError(409 if code in ("REQUEST_CONFLICT", "STOCK_CHANGED", "BALANCE_CHANGED") else 400, str(exc) or "بيانات غير صحيحة.") from None
        except Exception:
            logger.exception("market op %s failed", operation)
            raise ApiError(500, "تعذر تنفيذ العملية؛ راجع الأرصدة ثم أعد المحاولة.") from None
        flat = {k: (v if isinstance(v, (str, int, float, bool, type(None))) else str(v)) for k, v in (result or {}).items() if not isinstance(v, (list, dict))}
        return {**flat, "op": operation}

    @staticmethod
    def _lines(value, cols):
        """Rows from the client: at most MAX_LINES, only the declared columns, every value text; a fully blank row is dropped."""
        if value in (None, ""):
            return []
        if not isinstance(value, list) or len(value) > MAX_LINES:
            raise ApiError(400, "بيانات السطور غير صحيحة.")
        names = [c["k"] for c in cols]
        rows = []
        for item in value:
            if not isinstance(item, dict):
                raise ApiError(400, "بيانات السطور غير صحيحة.")
            row = {k: _str(item.get(k)) for k in names}
            if any(len(v) > MAX_FIELD for v in row.values()):
                raise ApiError(400, "قيمة طويلة جدًا في أحد السطور.")
            if any(row.values()):
                rows.append(row)
        return rows
