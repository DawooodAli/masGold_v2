"""Shared, side-effect free facts of the intermediary (مسوقجي) lot holdings (048): rights, the owner's confirmation text and the display
shape of a holding pool / document. No database, no authorization: the services decide, this module only names and formats."""
from decimal import ROUND_HALF_UP, Decimal, localcontext

from repositories.intermediary_holding_repository import CONFIRMATION_CODE, holding_code, logo_holding_code
from repositories.market_trip_repository import entry_code, trip_code
from services.correction_rules import correction_code

VIEW_PERMISSION = "INTERMEDIARY_HOLDING_VIEW"
MANAGE_PERMISSION = "INTERMEDIARY_HOLDING_MANAGE"
REVERSE_PERMISSION = "INTERMEDIARY_HOLDING_REVERSE"
TRIP_PERMISSION = "MARKET_TRIP_MANAGE"
CONFIRMATION_TEXT = "تم استلام نفس السبكة كاملة دون تجزئة"          # the NORMAL RETURN (048)
SETTLE_CONFIRMATION_TEXT = "تم تسليم نفس السبكة كاملة دون تجزئة"     # a settlement of the whole original bar (050)
SETTLE_PERMISSION = "SUPPLIER_GOLD_SETTLE"
SETTLE_REVERSE_PERMISSION = "SUPPLIER_GOLD_SETTLE_REVERSE"
POOL_LOT, POOL_LOGO = "LOT", "LOGO"                                   # the two kinds of holding source a supplier can be settled from
SOURCE_LOT, SOURCE_LOGO = "HOLDING_LOT", "HOLDING_LOGO"               # the SG source kinds (050)
WHOLE, ENTITLEMENT, DIVIDED = "WHOLE_ORIGINAL_BAR", "GRAM_ENTITLEMENT", "PHYSICALLY_DIVIDED"
ALLOCATING = (ENTITLEMENT, DIVIDED)                                   # a declaration that creates PERMANENT allocation history
DECLARATION_TEXT = {WHOLE: SETTLE_CONFIRMATION_TEXT,
                    ENTITLEMENT: "تخصيص جرامات فقط: الحالة الفعلية للسبكة غير معلومة",
                    DIVIDED: "السبكة قُسِّمت فعليًا (تأكيد صريح)"}
LOT_TEXT = {"MAIN": "الناتج الرئيسي", "RESIDUE": "رواسب"}
ANALYSIS_STEP = Decimal("0.000001")
NOT_ANALYSED = "غير معلومة"
CLOSURE_NOTE = ("هذه العهدة قد تُغلق بعد التسليم لأن رصيدها الخاص صار صفرًا، لكن الذهب ما زال خارج المحل لدى المسوقجي ولم يرجع بعد؛ "
                "لا يعني إغلاق العهدة أن الذهب عاد إلى المحل.")
HOLDING_NOTE = "ذهب ملك المحل موجود فعلًا لدى المسوقجي؛ ليس دينًا للمورد ولا بيعًا ولا مبلغًا نقديًا، وكل سبكة مصدر مستقل ولو تطابق عيارها."


def pure_grams(weight, fineness):
    """Pure gold of a whole lot (exact, six decimals); None when the assay is unknown. Display only."""
    if fineness is None:
        return None
    with localcontext() as context:
        context.prec = 50
        return (Decimal(str(weight)) * Decimal(str(fineness)) / Decimal(1000)).quantize(ANALYSIS_STEP, rounding=ROUND_HALF_UP)


def source_text(kind, origin_trip_id, origin_entry_no, lot_no, label=None):
    return (f"{LOT_TEXT[kind]} • {entry_code(origin_trip_id, origin_entry_no)} • شحنة {int(lot_no)}"
            + (f" • {label}" if label else ""))


def pool_row(r):
    """One pool that still has grams at a party (the Arabic panel «ذهب المحل لدى المسوقجي»)."""
    trip_open = r["trip_status"] == "OPEN"
    balance = Decimal(str(r["balance"]))
    whole = balance == Decimal(str(r["weight_grams"]))
    allocated = int(r.get("allocation_count") or 0) > 0                  # a GRAM_ENTITLEMENT / PHYSICALLY_DIVIDED settlement ever existed (050)
    settled_before = int(r.get("settlement_count") or 0) > 0
    reason = ""
    if not trip_open:
        reason = f"العهدة الأصلية {trip_code(r['trip_id'])} مغلقة: أعد فتحها صراحةً قبل تسجيل الرجوع."
    elif allocated:
        reason = allocation_note(r["first_allocation_id"])
    elif not whole:
        reason = "رصيد المصدر جزئي: لا يُسجَّل الرجوع إلا لسبكة كاملة."
    return {"pool_id": str(r["pool_id"]), "party_id": str(r["party_id"]), "party_name": r["party_name"], "party_active": bool(r["party_active"]),
            "lot_id": str(r["lot_id"]), "kind": r["lot_kind"], "lot_no": int(r["lot_no"]), "label": r.get("label") or "",
            "source": source_text(r["lot_kind"], r["origin_trip_id"], r["origin_entry_no"], r["lot_no"], r.get("label")),
            "weight": Decimal(str(r["weight_grams"])), "balance": balance, "fineness": Decimal(str(r["fineness_permille"])),
            "pure": pure_grams(balance, r["fineness_permille"]), "document": holding_code(r["handover_id"]),
            "custody_entry": entry_code(r["trip_id"], r["handover_entry_no"]), "trip_id": str(r["trip_id"]), "trip_code": trip_code(r["trip_id"]),
            "trip_open": trip_open, "date": r["document_date"], "returnable": trip_open and whole and not allocated, "reason": reason,
            "allocated": allocated, "settleable": balance > 0,
            "history_note": ("سبق تسليم هذه السبكة كاملة لمورد وعُكس: يبقى رجوعها بتأكيد المالك الصريح."
                             if settled_before and not allocated and whole else "")}


LOGO_NOTE = ("ذهب لوجو بالجرام الفعلي لعيار واحد (غير محدد الهوية ولا العيار الفعلي): كل تسليم مصدر مستقل ولو كان لنفس المسوقجي والعيار والعهدة، "
             "والرجوع يكون من مصدر واحد تختاره وبأي كمية حتى رصيده، إلى العهدة الأصلية المفتوحة.")


def logo_pool_row(r):
    """One master-karat LOGO source that still has grams at a party (physical grams of ONE karat; no fineness, no pure-gold figure)."""
    trip_open = r["trip_status"] == "OPEN"
    balance = Decimal(str(r["balance"]))
    return {"pool_id": str(r["pool_id"]), "party_id": str(r["party_id"]), "party_name": r["party_name"], "party_active": bool(r["party_active"]),
            "karat_id": str(r["karat_id"]), "karat_value": int(r["karat_value"]), "weight": Decimal(str(r["weight_grams"])), "balance": balance,
            "document": logo_holding_code(r["handover_id"]), "custody_entry": entry_code(r["trip_id"], r["handover_entry_no"]),
            "trip_id": str(r["trip_id"]), "trip_code": trip_code(r["trip_id"]), "trip_open": trip_open, "date": r["document_date"],
            "returnable": trip_open and balance > 0, "settleable": balance > 0,
            "reason": "" if trip_open else f"العهدة الأصلية {trip_code(r['trip_id'])} مغلقة: أعد فتحها صراحةً قبل تسجيل الرجوع."}


def logo_document_row(r):
    reversed_by = None if r["reversal_id"] is None else correction_code("FULL_REVERSAL", r["reversal_id"])
    return {"document_id": str(r["document_id"]), "code": logo_holding_code(r["document_id"]), "kind": r["doc_kind"], "pool_id": str(r["pool_id"]),
            "karat_value": int(r["karat_value"]), "weight": Decimal(str(r["weight_grams"])), "custody_entry": entry_code(r["trip_id"], r["entry_no"]),
            "trip_code": trip_code(r["trip_id"]), "date": r["document_date"], "at": r["occurred_at"], "actor": r.get("actor_name") or "",
            "reference": r.get("reference") or "", "notes": r.get("notes") or "", "reversed": reversed_by is not None, "reversed_by": reversed_by,
            "reversal_reason": r.get("reversal_reason") or ""}


def document_row(r):
    reversed_by = None if r["reversal_id"] is None else correction_code("FULL_REVERSAL", r["reversal_id"])
    return {"document_id": str(r["document_id"]), "code": holding_code(r["document_id"]), "kind": r["doc_kind"],
            "pool_id": str(r["pool_id"]), "weight": Decimal(str(r["weight_grams"])), "fineness": Decimal(str(r["fineness_permille"])),
            "source": source_text(r["lot_kind"], r["origin_trip_id"], r["origin_entry_no"], r["lot_no"], r.get("label")),
            "custody_entry": entry_code(r["trip_id"], r["entry_no"]), "trip_code": trip_code(r["trip_id"]),
            "date": r["document_date"], "at": r["occurred_at"], "actor": r.get("actor_name") or "", "reference": r.get("reference") or "",
            "notes": r.get("notes") or "", "intact": r["intact_confirmation_code"] == CONFIRMATION_CODE,
            "confirmed_at": r.get("confirmed_at"), "confirmed_by": r.get("confirmed_by_name") or "",
            "reversed": reversed_by is not None, "reversed_by": reversed_by, "reversal_reason": r.get("reversal_reason") or ""}


def declaration_options(pool_weight, balance, quantity, history):
    """The physical declarations a lot settlement may state (the service is the authority; the dialog only shows these).
    `history` is the pool's settlement declarations, reversed ones included. WHOLE_ORIGINAL_BAR needs the full original quantity, a full balance
    and NO allocation history; a full quantity alone never proves a whole-bar delivery, so GRAM_ENTITLEMENT and PHYSICALLY_DIVIDED are always allowed."""
    whole = (Decimal(str(quantity)) == Decimal(str(pool_weight)) == Decimal(str(balance))
             and not any(d in ALLOCATING for d in history))
    return ((WHOLE,) if whole else ()) + ALLOCATING


def declaration_choices(options):
    return [{"code": code, "text": DECLARATION_TEXT[code]} for code in options]


def allocation_note(first_allocation_id):
    """Why a pool can no longer be returned whole (names the SG)."""
    from repositories.supplier_gold_settlement_repository import settlement_code
    return (f"سبق تخصيص جرامات من هذه السبكة لمورد ({settlement_code(first_allocation_id)}): لا يُسجَّل رجوعها كاملة ولا يُعكس تسليمها "
            "قبل عملية استلام فعلي مستقبلية، ولو عاد الرصيد كاملًا.")


def holding_settlement_row(r):
    """One supplier gold settlement made from a holding (display)."""
    from repositories.supplier_gold_settlement_repository import settlement_code
    lot = r["source_kind"] == SOURCE_LOT
    reversed_by = None if r["reversal_id"] is None else correction_code("FULL_REVERSAL", r["reversal_id"])
    return {"id": str(r["id"]), "code": settlement_code(r["id"]), "source_kind": r["source_kind"], "pool_kind": POOL_LOT if lot else POOL_LOGO,
            "pool_id": str(r["lot_pool_id"] if lot else r["logo_pool_id"]), "supplier_id": str(r["supplier_id"]), "supplier_name": r["supplier_name"],
            "delivered": Decimal(str(r["delivered_weight_grams"])), "credited": Decimal(str(r["credited_weight_grams"])),
            "analytical": Decimal(str(r["credited_analytical_grams"])), "rounding_difference": Decimal(str(r["rounding_difference_grams"])),
            "target_karat": int(r["target_karat_value"]), "delivered_karat": None if r["delivered_karat_value"] is None else int(r["delivered_karat_value"]),
            "fineness": None if r["delivered_fineness_permille"] is None else Decimal(str(r["delivered_fineness_permille"])),
            "lot_weight": None if r["delivered_lot_weight_grams"] is None else Decimal(str(r["delivered_lot_weight_grams"])),
            "declaration": r["physical_declaration"], "declaration_text": DECLARATION_TEXT.get(r["physical_declaration"], ""),
            "self": bool(r["self_settlement_confirmed"]), "date": r["settlement_date"], "at": r["occurred_at"], "actor": r.get("actor_name") or "",
            "reversed": reversed_by is not None, "reversed_by": reversed_by, "reversal_reason": r.get("reversal_reason") or ""}


def holding_preview(context, supplier_id, quantity):
    """Pure: what a quantity would credit a recipient, from the loaded dialog context and the SAME credit functions the posting uses (no database, no QML
    arithmetic). Returns Decimals; `error` is a ready Arabic message and `valid` is False while anything refuses."""
    from services import supplier_rules as supplier
    result = {"valid": False, "error": "", "short": False, "options": [], "self": False}
    recipient = next((r for r in context["recipients"] if r["id"] == str(supplier_id)), None)
    if recipient is None:
        return result
    result["self"] = recipient["self"]
    if not str(quantity or "").strip():
        return result
    try:
        grams = supplier.parse_delivered_weight(quantity)
    except supplier.SupplierRuleError as exc:
        return {**result, "error": str(exc)}
    balance, facts = context["balance"], context["facts"]
    result.update(delivered=grams, remaining=balance - grams, supplier_karat=recipient["karat_value"])
    if grams > balance:
        return {**result, "short": True, "error": f"المتبقي من هذا المصدر لدى المسوقجي {balance:,.3f} جرام فقط."}
    try:
        if context["pool_kind"] == POOL_LOT:
            credited, analytical, difference = supplier.lot_settlement_credit(grams, facts["fineness"], recipient["karat_value"])
            result["options"] = declaration_choices(declaration_options(facts["weight"], balance, grams, context["history"]))
        else:
            credited, analytical, difference = supplier.settlement_credit(grams, facts["karat_value"], recipient["karat_value"])
    except supplier.SupplierRuleError as exc:
        return {**result, "error": str(exc)}
    return {**result, "valid": True, "credited": credited, "analytical": analytical, "rounding_difference": difference}
