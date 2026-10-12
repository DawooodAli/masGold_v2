"""Shared, side-effect free facts of the intermediary (مسوقجي) lot holdings (048): rights, the owner's confirmation text and the display
shape of a holding pool / document. No database, no authorization: the services decide, this module only names and formats."""
from decimal import ROUND_HALF_UP, Decimal, localcontext

from repositories.intermediary_holding_repository import CONFIRMATION_CODE, holding_code
from repositories.market_trip_repository import entry_code, trip_code
from services.correction_rules import correction_code

VIEW_PERMISSION = "INTERMEDIARY_HOLDING_VIEW"
MANAGE_PERMISSION = "INTERMEDIARY_HOLDING_MANAGE"
REVERSE_PERMISSION = "INTERMEDIARY_HOLDING_REVERSE"
TRIP_PERMISSION = "MARKET_TRIP_MANAGE"
CONFIRMATION_TEXT = "تم استلام نفس السبكة كاملة دون تجزئة"
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
    reason = ""
    if not trip_open:
        reason = f"العهدة الأصلية {trip_code(r['trip_id'])} مغلقة: أعد فتحها صراحةً قبل تسجيل الرجوع."
    elif not whole:
        reason = "رصيد المصدر جزئي: لا يُسجَّل الرجوع إلا لسبكة كاملة."
    return {"pool_id": str(r["pool_id"]), "party_id": str(r["party_id"]), "party_name": r["party_name"], "party_active": bool(r["party_active"]),
            "lot_id": str(r["lot_id"]), "kind": r["lot_kind"], "lot_no": int(r["lot_no"]), "label": r.get("label") or "",
            "source": source_text(r["lot_kind"], r["origin_trip_id"], r["origin_entry_no"], r["lot_no"], r.get("label")),
            "weight": Decimal(str(r["weight_grams"])), "balance": balance, "fineness": Decimal(str(r["fineness_permille"])),
            "pure": pure_grams(balance, r["fineness_permille"]), "document": holding_code(r["handover_id"]),
            "custody_entry": entry_code(r["trip_id"], r["handover_entry_no"]), "trip_id": str(r["trip_id"]), "trip_code": trip_code(r["trip_id"]),
            "trip_open": trip_open, "date": r["document_date"], "returnable": trip_open and whole, "reason": reason}


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
