"""Market-trip custody: the OWNER takes cash (from MAIN_SAFE) and Scrap (existing master karat, physical grams) to the market,
decides what each gold line is for, melts designated gold into assayed lots and brings back what was not used.

  ISSUE   cash OUT of MAIN_SAFE once + counted Scrap OUT per (karat, designation)  -> the same quantities enter the trip's CUSTODY
          every new gold line carries the OWNER's designation: MELT (للسبك) or LOGO (لوجو)
  RETURN  custody leaves the trip -> cash back into MAIN_SAFE + counted Scrap IN of the SAME karat (never converted, no Eq21);
          the designation is preserved and a return never exceeds that (karat, designation) balance
  LOT_RETURN  returns whole assayed lots from custody to the shop's separate lot stock (a melt bar is indivisible: complete or not at all):
          the lot keeps its identity, melt reference, kind and exact assay; nothing is classified into a master karat; no cash, bank,
          supplier, inventory-piece or Scrap movement exists
  LOT_ISSUE   sends a whole shop bar (a lot that returned to the shop) out again into a DIFFERENT open custody: one shop OUT and one
          custody IN, atomically. A lot is in the shop or in exactly ONE custody; its location is derived from the effective movements,
          never from its origin melt, so the origin trip may stay closed. Returning it is the exact opposite transfer (LOT_RETURN)
  HEAT    (حمّية اللوجو) heats LOGO-designated custody without melting it: the declared processed grams of each karat leave and the
          ACTUAL resulting grams return under the SAME karat and the LOGO designation. Only physical weights are stored (no purity, pure
          gold or Eq21); a weight gain needs an explanation; the signed difference is permanent evidence, never a shortage or write-off.
          No cash, Scrap, supplier, inventory or lot movement exists and a heat is not part of any melt analysis
  GOLD_SETTLE  (تسوية ذهب من العهدة, 046) delivers LOGO custody gold to a SUPPLIER against its gold account: the custody grams leave (no
          second Scrap movement, no cash, making, bank or lot movement) and the supplier is credited in its own dedicated karat
          (delivered grams x delivered karat / supplier karat, the same rounding as the direct settlement). One SG document per source line
          and supplier; MELT and unclassified gold are outside this slice
  LOT_SETTLE (047) delivers one whole MAIN or known-assay RESIDUE lot from its CURRENT custody to one supplier; one SG document,
          negative custody lot movement and supplier gold movement, with original lot/assay preserved and no shop-lot movement.
  MELT    consumes MELT-designated custody (any active master karat, physical grams) and creates assayed LOTS: one MAIN output with
          its fineness (parts per thousand, 0.1) and optional RESIDUE lots whose fineness may be unknown. No cash, shop Scrap or
          supplier movement exists in a melt; the processing difference is stored permanently with the document.

Custody = issued - returned - melted (+ the effective Logo heating differences on LOGO) - delivered to suppliers (046) over the
entries that were not reversed, per (karat, designation) and for cash (the arithmetic lives in services.custody_rules); none can
ever go negative. Gold written before the designation existed is UNCLASSIFIED: it can only be returned. Gold in custody is
shop-owned but unavailable inside the shop (it really left the counted Scrap balance), and master-karat custody is shown apart
from assayed lots. A trip closes only when cash, every (karat, designation) and every lot are exactly zero. Whole lots can return
to the shop or settle a supplier; partial allocation, sales and merchandise allocation remain outside this slice.

Right: MARKET_TRIP_MANAGE (OWNER only), re-checked inside every transaction through AuthorizationService; the actor is the
AppSession user. Entries are immutable; their only correction is a full reversal (RV) through CorrectionService, which also
needs TRANSACTION_CORRECT and refuses a closed trip, an issue or melt that a later entry depends on, and a melt whose lots were
moved after their creation. OWNER may reopen a closed trip (reason mandatory, audited).

Idempotent on the request key. Lock order: actor -> trip -> MAIN_SAFE -> Scrap karats (ascending) -> request key.
"""
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation, localcontext
import hashlib
import json
from uuid import UUID, uuid5

from mysql.connector import IntegrityError

from repositories.financial_account_repository import FinancialAccountRepository
from repositories.market_trip_repository import (CLOSED, CREATE_MOVEMENT, GOLD_BUY, GOLD_SALE, SALE_MOVEMENT, GOLD_SETTLE, LOT_SETTLE, LOT_SETTLE_MOVEMENT, HEAT, ISSUE, LOGO_DESIGNATION, SUPPLIER_PAY, LOT_ISSUE, LOT_RETURN, MAIN_LOT, MELT,
                                                 MELT_DESIGNATION, OPEN, RESIDUE_LOT, RETURN, SHOP_ISSUE_MOVEMENT, SHOP_ISSUE_OUT,
                                                 SHOP_RETURN_IN, SHOP_RETURN_MOVEMENT, SOURCE, UNCLASSIFIED, MarketTripRepository,
                                                 entry_code, trip_code)
from repositories.intermediary_holding_repository import IntermediaryHoldingRepository, holding_code, logo_holding_code
from repositories.old_gold_conversion_repository import OldGoldConversionRepository
from repositories.supplier_gold_settlement_repository import (CUSTODY_SOURCE_KIND, LOT_SOURCE_KIND, GOLD_MOVEMENT, SupplierGoldSettlementRepository,
                                                              settlement_code)
from repositories.supplier_payment_repository import BAR_FEE_MOVEMENT, PAYMENT_MOVEMENT, SOURCE as SMP_SOURCE, SupplierPaymentRepository, payment_code
from services.authorization_service import AuthorizationService
from services import book_rules, setting_rules
from services.correction_rules import correction_code
from services.intermediary_holding_rules import (MANAGE_PERMISSION as MANAGE_HOLDINGS, VIEW_PERMISSION as VIEW_HOLDINGS, logo_pool_row, pool_row)
from services.invoice_service import invoice_transaction
from services.number_text import normalize_digits
from services.old_gold_availability import held_grams, shortage_message
from services.trip_errors import TripError
from services import custody_rules, settlement_rules, supplier_rules as rules

PERMISSION = "MARKET_TRIP_MANAGE"
ST_MELT = "ST_MELT"                                # 060: the melt recorded AFTER the owner returns from the market (settlement step 2a)
ST_BAR_SUP, BAR_SOURCE_KIND = "ST_BAR_SUP", "TRIP_BAR"      # 061: the melted bar (21K-equivalent grams) delivered to a supplier (settlement step 2b)
ST_RET_MKT, ST_LOSS, ST_ADJUST = "ST_RET_MKT", "ST_LOSS", "ST_ADJUST"      # 065: remainder returned to the shop / lost, issue-weight correction
ST_EXPENSE, ST_CASH_BANK, ST_CASH_OFF = "ST_EXPENSE", "ST_CASH_BANK", "ST_CASH_OFF"      # 064: the custody cash settled after the trip
ST_BAR_FEE = "ST_BAR_FEE"                      # 080: the cash fee a supplier / marketer pays when a bar is handed over
ST_LOGO = "ST_LOGO"                                # 062: the unmelted remainder classified as LOGO (the owner rule: what was not melted is logo)
ST_BAR_MKT = "ST_BAR_MKT"                          # 062: the melted bar handed to a marketer: his 21K entitlement gains the grams (settlement step 2c)
BASE_KARAT_VALUE = 21                                  # the bar is delivered as N grams of the 21K master karat (its equivalent)
SUPPLIER_PAY_PERMISSION = "SUPPLIER_MAKING_PAY"      # a supplier payment made from custody needs this right too (043)
GOLD_SETTLE_PERMISSION = "SUPPLIER_GOLD_SETTLE"      # a gold settlement made from custody needs this right too (046)
SALE_SOURCE_GOLD, SALE_SOURCE_LOT, SALE_SOURCE_BAR = "CUSTODY_GOLD", "CUSTODY_LOT", "TRIP_BAR"             # 053: what a sale consumes (an aggregate line / one whole lot)
SALE_BASIS_TOTAL, SALE_BASIS_PER_GRAM = "TOTAL", "PER_GRAM"
BUYER_REGISTERED, BUYER_THIRD_PARTY = "REGISTERED_PARTY", "THIRD_PARTY"
BUYER_NAME_MAX = 150
_VENDOR_MAP = {ord(c): "ا" for c in "أإآٱ"}
_VENDOR_MAP.update({ord("ى"): "ي", ord("ة"): "ه", ord("ؤ"): "و", ord("ئ"): "ي"})
_VENDOR_MAP.update({ord(c): None for c in "\u064b\u064c\u064d\u064e\u064f\u0650\u0651\u0652\u0640"})            # diacritics and tatweel
SALE_PER_GRAM_STEP = Decimal("0.0001")
# A sale price farther than this (a percentage, from System Settings 081: `price_deviation_warning_pct`, default 10) from the SHOP_BUY
# reference needs the owner's acknowledgement — a soft warning, never an automatic refusal.
SETTLEABLE_DESIGNATIONS = (LOGO_DESIGNATION, UNCLASSIFIED)   # 046: Logo gold; 061: also gold that left WITHOUT a destination and was not melted (it stays in its OWN karat)
CUSTODY_METHOD = "CUSTODY"
PAYMENT_REFERENCE_MAX = 100
SETTLE_TEXT_MAX = 300
MONEY_STEP, WEIGHT_STEP, FINENESS_STEP, ANALYSIS_STEP = Decimal("0.01"), Decimal("0.001"), Decimal("0.1"), Decimal("0.000001")
MAX_MONEY, MAX_WEIGHT = Decimal("100000000000000"), Decimal("1000000000")
NOTES_MAX, REASON_MAX, WORKSHOP_MAX, ASSAY_MAX, LABEL_MAX, MAX_RESIDUES = 1000, 1000, 120, 120, 120, 10
ZERO_MONEY, ZERO_WEIGHT = Decimal("0.00"), Decimal("0.000")
KIND_TEXT = {ISSUE: "إخراج عهدة", RETURN: "رجوع من العهدة", MELT: "سبك", LOT_RETURN: "رجوع ناتج السبك", LOT_ISSUE: "إخراج سبكة من المحل",
             HEAT: "حمّية اللوجو", SUPPLIER_PAY: "دفع مصنعيات من العهدة", GOLD_SETTLE: "تسوية ذهب من العهدة", LOT_SETTLE: "تسوية سبكة من العهدة",
             GOLD_SALE: "بيع ذهب من العهدة", GOLD_BUY: "شراء ذهب نقدي", "BK_ISSUE": "إخراج ذهب (دفتري)", "BK_BUY": "شراء ذهب (دفتري)",
             "BK_SELL": "بيع ذهب (دفتري)", "BK_RETURN": "رجوع ذهب للمحل (دفتري)", "BK_RECON": "تسوية الرصيد الدفتري", "BK_ISS_STK": "إخراج مخزون مرتجع إلى العهدة (دفتري)", "BK_DLV_SUP": "تسليم ذهب لمورد (دفتري)", "BK_DLV_MKT": "تسليم ذهب لمسوقجي (دفتري)", "BK_RECEIVE": "استلام ذهب من مسوقجي (دفتري)", "BK_GAIN": "زيادة مثبتة (دفتري)", "LOT_HOLD": "تسليم ذهب لمسوقجي", "HOLD_RETURN": "رجوع سبكة من مسوقجي", "LOGO_HOLD": "تسليم لوجو لمسوقجي", "HOLD_BACK": "رجوع لوجو من مسوقجي", "ST_MELT": "تسجيل سبك (بعد الرجوع)", "ST_BAR_SUP": "تسليم سبيكة لمورد (بعد الرجوع)", "ST_BAR_MKT": "تسليم سبيكة لمسوقجي (بعد الرجوع)", "ST_LOGO": "اعتبار المتبقي لوجو", "ST_EXPENSE": "مصروف من العهدة", "ST_CASH_BANK": "إيداع باقي النقدية في حساب", "ST_CASH_OFF": "إعدام باقي النقدية", "ST_RET_MKT": "مرتجع سوق للمحل", "ST_LOSS": "خسية", "ST_ADJUST": "تصحيح وزن الإخراج", "ST_BAR_FEE": "أجر سبيكة"}
DESIGNATION_TEXT = {MELT_DESIGNATION: "للسبك", LOGO_DESIGNATION: "لوجو", UNCLASSIFIED: "بدون وجهة"}
_DESIGNATION_INPUT = {"MELT": MELT_DESIGNATION, "للسبك": MELT_DESIGNATION, "LOGO": LOGO_DESIGNATION, "لوجو": LOGO_DESIGNATION,
                      "UNCLASSIFIED": UNCLASSIFIED, "غير مصنف": UNCLASSIFIED}
LOT_RETURN_NOTE = "صفِّ السبائك المتبقية برجوع ناتج السبك إلى المحل أو بتسوية سبكة معلومة العيار من العهدة قبل الإغلاق."


TRIP_PAGE_SIZE, MAX_TRIP_PAGE_SIZE = 50, 100          # trips per page of the custody list (the page filter/order is SQL, never a cap on the data)


def _is_request_key_violation(exc):
    return getattr(exc, "errno", None) == 1062 and "uq_mte_request" in str(exc)


def _decimal(value, label, code):
    text = normalize_digits(str(value if value is not None else "")).strip().replace(",", "")
    if not text:
        return None
    try:
        number = Decimal(text)
    except InvalidOperation:
        raise TripError(code, f"{label} غير صحيح.") from None
    if not number.is_finite():
        raise TripError(code, f"{label} غير صحيح.")
    return number


def parse_cash(value):
    """EGP, exact Decimal with at most two decimals; empty / zero means no cash. Never rounded."""
    number = _decimal(value, "مبلغ النقدية", "CASH_INVALID")
    if number is None or number == 0:
        return ZERO_MONEY
    if number < 0 or number >= MAX_MONEY or number != number.quantize(MONEY_STEP):
        raise TripError("CASH_INVALID", "اكتب مبلغ النقدية بالأرقام بخانتين عشريتين على الأكثر وبقيمة موجبة مقبولة.")
    return number.quantize(MONEY_STEP)


def parse_weight(value):
    """Physical grams, exact Decimal with at most three decimals. Never rounded: the physical fact is kept as entered."""
    number = _decimal(value, "الوزن", "WEIGHT_INVALID")
    if number is None or number == 0:
        return ZERO_WEIGHT
    if number < 0 or number >= MAX_WEIGHT or number != number.quantize(WEIGHT_STEP):
        raise TripError("WEIGHT_INVALID", "اكتب الوزن بالجرام بثلاث خانات عشرية على الأكثر وبقيمة موجبة مقبولة.")
    return number.quantize(WEIGHT_STEP)


def parse_fineness(value, label="العيار الفعلي", required=True):
    """Fineness in parts per thousand (870 or 870.5): one decimal at most, > 0 and <= 1000, an exact Decimal. Never rounded and
    never mapped onto a master karat. Blank means unknown and is allowed only where `required` is False (a residue)."""
    text = normalize_digits(str(value if value is not None else "")).strip().replace("٫", ".")
    if not text:
        if required:
            raise TripError("FINENESS_REQUIRED", f"{label} مطلوب: اكتبه بالألف (مثل 870 أو 870.5).")
        return None
    if "," in text or "،" in text:
        raise TripError("FINENESS_INVALID", f"{label}: اكتب العدد بنقطة عشرية وبلا فواصل (مثل 870.5).")
    try:
        number = Decimal(text)
    except InvalidOperation:
        raise TripError("FINENESS_INVALID", f"{label} غير صحيح: اكتبه بالألف (مثل 870 أو 870.5).") from None
    if not number.is_finite() or number <= 0 or number > 1000:
        raise TripError("FINENESS_RANGE", f"{label} يجب أن يكون أكبر من صفر وحتى 1000 في الألف.")
    if number != number.quantize(FINENESS_STEP):
        raise TripError("FINENESS_PRECISION", f"{label} بخانة عشرية واحدة على الأكثر (مثل 870.5).")
    return number.quantize(FINENESS_STEP)


def parse_designation(value, kind, allow_blank=False):
    """The OWNER's decision on a gold line. A market-custody ISSUE (stage 1) carries NO designation (`allow_blank`: stored UNCLASSIFIED, shown «بدون وجهة»); every other
    caller that issues gold (the cash gold purchase) must choose MELT or LOGO; a RETURN preserves the line's own designation. Nothing is ever guessed."""
    text = str(value or "").strip()
    if allow_blank and not text:
        return UNCLASSIFIED                                      # market custody stage 1: gold leaves by weight and karat only; its fate is decided at settlement
    designation = _DESIGNATION_INPUT.get(text.upper() if text.isascii() else text)
    if designation is None:
        raise TripError("DESIGNATION_REQUIRED", "اختر وجهة الذهب لكل سطر: للسبك أو لوجو.")
    if kind == ISSUE and designation == UNCLASSIFIED and not allow_blank:
        raise TripError("DESIGNATION_REQUIRED", "اختر وجهة الذهب لكل سطر: للسبك أو لوجو.")
    return designation


def _entry_date(value):
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = normalize_digits(str(value or "")).strip()
    if not text:
        raise TripError("DATE_REQUIRED", "التاريخ مطلوب.")
    try:
        return date.fromisoformat(text)
    except ValueError:
        raise TripError("DATE_INVALID", "التاريخ غير صحيح.") from None


def _text(value, limit, label, required=False):
    text = str(value or "").strip()
    if required and not text:
        raise TripError("REASON_REQUIRED", f"{label} مطلوب.")
    if len(text) > limit:
        raise TripError("TEXT_LONG", f"{label} أطول من الحد المسموح.")
    return text or None


def _key(value):
    try:
        return str(UUID(str(value)))
    except (ValueError, TypeError, AttributeError):
        raise TripError("REQUEST_INVALID", "أعد فتح النموذج وحاول مرة أخرى.") from None


def _trip_id(value):
    try:
        number = int(str(value).strip())
    except (TypeError, ValueError):
        raise TripError("TRIP_NOT_FOUND", "العهدة غير موجودة.") from None
    if number <= 0:
        raise TripError("TRIP_NOT_FOUND", "العهدة غير موجودة.")
    return number


def custody_view(cash_rows, gold_rows, lot_rows=(), trip_id=None, bar=0):
    """Issued / returned / melted / OUTSTANDING for cash and for every (karat, designation), plus the outstanding assayed lots, from
    the repository's derived sums. A trip is settled only when all of them are exactly zero."""
    issued, returned = (Decimal(str(cash_rows[0])), Decimal(str(cash_rows[1])))
    paid = Decimal(str(cash_rows[2])) if len(cash_rows) > 2 else Decimal("0.00")          # paid to suppliers from custody (043)
    spent = Decimal(str(cash_rows[4])) if len(cash_rows) > 4 else Decimal("0.00")         # spent on gold bought in the market (054)
    received = Decimal(str(cash_rows[3])) if len(cash_rows) > 3 else Decimal("0.00")      # proceeds of custody gold sold for cash (053)
    settled = Decimal(str(cash_rows[5])) if len(cash_rows) > 5 else Decimal("0.00")       # expense / bank deposit / write-off after the trip (064)
    fee = Decimal(str(cash_rows[6])) if len(cash_rows) > 6 else Decimal("0.00")           # bar fees received in cash (080)
    gold = []
    for r in gold_rows:
        g_issued, g_returned = Decimal(str(r["issued"])), Decimal(str(r["returned"]))
        g_melted, g_heat = Decimal(str(r.get("melted", 0) or 0)), Decimal(str(r.get("heat_delta", 0) or 0))
        g_settled = Decimal(str(r.get("supplier_settled", 0) or 0))                       # delivered to suppliers from custody (046)
        g_sold = Decimal(str(r.get("sold", 0) or 0))                                      # sold for cash from custody (053)
        g_bought = Decimal(str(r.get("purchased", 0) or 0))                               # bought for cash in the market (054)
        g_held_out, g_held_back = Decimal(str(r.get("held_out", 0) or 0)), Decimal(str(r.get("held_back", 0) or 0))   # at / back from an intermediary (049)
        gold.append({"karat_id": int(r["karat_id"]), "karat_value": int(r["karat_value"]),
                     "designation": r.get("designation", UNCLASSIFIED), "issued": g_issued, "returned": g_returned,
                     "melted": g_melted, "heat_delta": g_heat, "supplier_settled": g_settled, "sold": g_sold, "purchased": g_bought,
                     "held_out": g_held_out, "held_back": g_held_back, "settle_out": Decimal(str(r.get("settle_out", 0) or 0)),
                     "outstanding": custody_rules.gold_outstanding(r)})
    lots = [{"lot_id": str(r["id"]), "entry_id": str(r["entry_id"]), "lot_no": int(r["lot_no"]), "kind": r["lot_kind"],
             "label": r.get("label") or "", "weight": Decimal(str(r["weight_grams"])),
             "fineness": None if r.get("fineness_permille") is None else Decimal(str(r["fineness_permille"])),
             "pure": None if r.get("pure_grams") is None else Decimal(str(r["pure_grams"])),
             "entry_no": int(r["entry_no"]), "outstanding": Decimal(str(r["outstanding_grams"])),
             "origin_trip_id": int(r.get("origin_trip_id") or trip_id or 0) or None,
             "code": None if not (r.get("origin_trip_id") or trip_id) else entry_code(r.get("origin_trip_id") or trip_id, r["entry_no"])}
            for r in lot_rows if Decimal(str(r["outstanding_grams"])) != 0]
    cash_left = custody_rules.cash_outstanding(issued, returned, paid, received, spent, settled, fee)
    bar_left = Decimal(str(bar or 0))                                                       # 060: Eq21 grams of the melted bars not yet distributed
    return {"cash": {"issued": issued, "returned": returned, "paid": paid, "received": received, "spent": spent, "settled": settled, "fee": fee, "outstanding": cash_left}, "gold": gold,
            "lots": lots, "bar": bar_left,
            "settled": cash_left == 0 and all(g["outstanding"] == 0 for g in gold) and not lots and bar_left == 0}


def analyse(inputs, main, residues):
    """The permanent processing analysis of one melt, from raw facts only.

    inputs   [(karat_value, grams)]            physical grams of the master karats consumed
    main     (grams, fineness_permille)         the one assayed main output
    residues [(grams, fineness_permille|None)]  recoverable residues; None = the fineness is unknown (never inferred)

    Nominal input pure gold = SUM(grams * karat / 24); known output pure gold = SUM(grams * fineness / 1000) over the lots whose
    fineness is known; Eq21 = pure * 24 / 21. Everything is computed exactly and quantized once (6 decimals, ROUND_HALF_UP); weights
    stay exact (3 decimals). The signed differences are outputs - inputs. When any residue fineness is unknown the pure-gold and
    Eq21 differences are None (incomplete): a known subtotal is never presented as a definitive loss or gain."""
    with localcontext() as context:
        context.prec = 50
        input_weight = sum((w for _, w in inputs), ZERO_WEIGHT)
        nominal = sum((w * Decimal(k) for k, w in inputs), Decimal(0)) / Decimal(24)
        lots = [main] + list(residues)
        output_weight = sum((w for w, _ in lots), ZERO_WEIGHT)
        known = sum((w * f / Decimal(1000) for w, f in lots if f is not None), Decimal(0))
        complete = all(f is not None for _, f in lots)

        def q(value):
            return None if value is None else value.quantize(ANALYSIS_STEP, rounding=ROUND_HALF_UP)

        return {"input_weight": input_weight, "nominal_pure": q(nominal), "output_weight": output_weight,
                "known_output_pure": q(known), "weight_difference": output_weight - input_weight, "pure_complete": complete,
                "pure_difference": q(known - nominal) if complete else None, "nominal_eq21": q(nominal * 24 / 21),
                "known_output_eq21": q(known * 24 / 21),
                "eq21_difference": q((known - nominal) * 24 / 21) if complete else None}


def line_pure(weight, karat_value):
    with localcontext() as context:
        context.prec = 50
        return (weight * Decimal(karat_value) / Decimal(24)).quantize(ANALYSIS_STEP, rounding=ROUND_HALF_UP)


def lot_pure(weight, fineness):
    if fineness is None:
        return None
    with localcontext() as context:
        context.prec = 50
        return (weight * fineness / Decimal(1000)).quantize(ANALYSIS_STEP, rounding=ROUND_HALF_UP)


def outstanding_text(custody):
    """The exact reason a trip cannot close, in the words an employee reads."""
    parts = []
    if custody["cash"]["outstanding"] != 0:
        parts.append(f"نقدية {custody['cash']['outstanding']:,.2f} ج")
    parts += [f"عيار {g['karat_value']} ({DESIGNATION_TEXT[g['designation']]}): {g['outstanding']:,.3f} جرام"
              for g in custody["gold"] if g["outstanding"] != 0]
    parts += [f"شحنة سبك {l['lot_no']} من {entry_label(l)}: {l['outstanding']:,.3f} جرام" for l in custody["lots"]]
    if custody.get("bar", 0) != 0:
        parts.append(f"رصيد السبيكة (مكافئ عيار 21): {custody['bar']:,.3f} جرام")
    return ("لا يمكن إغلاق العهدة قبل إرجاع كل ما أُخرج منها. المتبقي في العهدة: " + "، ".join(parts) + "."
            + (" " + LOT_RETURN_NOTE if custody["lots"] else ""))


def entry_label(lot):
    return f"الحركة {lot['entry_no']}"


class MarketTripService:
    def __init__(self, session, *, transaction_factory=invoice_transaction, repository=MarketTripRepository,
                 accounts=FinancialAccountRepository, scrap=OldGoldConversionRepository, authorization=None, corrections=None,
                 supplier_payments=SupplierPaymentRepository, gold_settlements=SupplierGoldSettlementRepository,
                 holdings=IntermediaryHoldingRepository, price_reference=None, vendor_registry=None):
        self._vendor_registry = vendor_registry
        self._price_reference = price_reference if price_reference is not None else self._default_price_reference(session)
        self._session, self._transaction, self._repository = session, transaction_factory, repository
        self._accounts, self._scrap, self._supplier_payments = accounts, scrap, supplier_payments
        self._gold_settlements = gold_settlements
        self._holdings = holdings
        self._authorization = authorization or AuthorizationService(session)
        self._corrections = corrections

    @staticmethod
    def _default_price_reference(session):
        """SHOP_BUY per gram of the active price set, by karat (the reference of the sale-price warning). Read lazily and never required."""
        def reference():
            from services.gold_price_service import GoldPriceService
            snapshot = GoldPriceService(session).snapshot()
            return {int(p["karat"]): Decimal(str(p["shop_buy_price_per_gram"])) for p in snapshot.get("prices", [])}
        return reference

    # ------------------------------------------------------------------ UI hint (never the authority)
    def can_manage(self):
        actor = self._session.current_actor()
        return actor is not None and PERMISSION in actor.permissions

    def _actor(self, expected_actor):
        actor = self._session.require_authenticated()
        if expected_actor is not None and not self._session.is_current(expected_actor):
            raise PermissionError("انتهت جلسة الدخول.")
        if PERMISSION not in actor.permissions:
            raise PermissionError("عهدة السوق للمالك فقط.")
        return actor

    @staticmethod
    def _custody(repository, trip_id, *, current=False):
        """The derived custody. `current=True` is for DECISIONS made under the trip lock: the cash, gold and lot reads lock their rows and their nested reversal
        lookups (current reads). The default `current=False` is the DISPLAY form: plain snapshot reads of the connection's transaction, no locking clause at all."""
        return custody_view(repository.custody_cash(trip_id, current=current), repository.custody_gold(trip_id, current=current),
                            repository.custody_lots(trip_id, current=current), trip_id=trip_id,
                            bar=repository.settle_bar_balance(trip_id, current=current))

    # ------------------------------------------------------------------ reads
    def context(self, *, _expected_actor=None):
        """What the entry dialogs need: the active master karats and the SHOP balances (never custody)."""
        actor = self._actor(_expected_actor)
        with self._transaction() as connection:
            self._authorization.require_permission_read(connection, actor, PERMISSION)
            repository = self._repository(connection)
            safe = repository.main_safe()
            karats = repository.active_karats()
            scrap = repository.shop_scrap()
            cash = None if safe is None else Decimal(str(repository.cash_balance(safe["id"])))
            suppliers = repository.suppliers()
            buyers = repository.buyers()
            intermediaries = repository.intermediaries() if {VIEW_HOLDINGS, MANAGE_HOLDINGS} <= set(actor.permissions) else []
            banks = repository.active_bank_accounts()
            warning_pct = setting_rules.price_warning_percent(repository)    # System Settings (081): the previews must warn exactly when posting will
        return {"price_deviation_warning_pct": warning_pct,
                "banks": [{"id": str(b["id"]), "name": b["name"]} for b in banks],
                "buyers": [{"id": str(r["id"]), "name": r["name"], "active": bool(r["is_active"]),
                            "supplier": bool(r["is_supplier"]), "intermediary": bool(r["is_intermediary"])} for r in buyers],
                "intermediaries": [{"id": str(r["id"]), "name": r["name"], "active": bool(r["is_active"])} for r in intermediaries],
                "safe": None if safe is None else {"id": str(safe["id"]), "name": safe["name"], "active": bool(safe["is_active"]),
                                                   "balance": cash},
                "karats": [{"id": int(k["id"]), "karat_value": int(k["karat_value"]), "name": k["name"]} for k in karats],
                "suppliers": [{"id": str(r["id"]), "name": r["name"], "active": bool(r["is_active"]),
                               "karat_id": None if r.get("karat_id") is None else int(r["karat_id"]),
                               "karat_value": None if r.get("karat_value") is None else int(r["karat_value"]),
                               "making_balance": Decimal(str(r["making_balance"]))} for r in suppliers],
                "shop_scrap": [{"karat_id": int(r["karat_id"]), "karat_value": int(r["karat_value"]),
                                "physical": Decimal(str(r["physical_grams"])), "held": Decimal(str(r["held_grams"])),
                                "available": Decimal(str(r["available_grams"]))} for r in scrap]}

    def supplier_settlement_sources(self, *, _expected_actor=None):
        """Display-only catalog of currently eligible gold in OPEN LEGACY custodies.

        The posting methods remain authoritative and re-derive every balance under
        their transaction locks. BOOK trips and unknown-assay lots are not offered.
        """
        actor = self._actor(_expected_actor)
        if GOLD_SETTLE_PERMISSION not in actor.permissions:
            raise PermissionError("تسوية ذهب الموردين غير متاحة لك.")
        with self._transaction() as connection:
            self._authorization.require_permission_read(connection, actor, PERMISSION)
            self._authorization.require_permission_read(connection, actor, GOLD_SETTLE_PERMISSION)
            repository = self._repository(connection)
            trips, page = [], 1
            while True:
                batch = repository.trip_page(OPEN, page, MAX_TRIP_PAGE_SIZE)
                trips.extend(batch[:MAX_TRIP_PAGE_SIZE])
                if len(batch) <= MAX_TRIP_PAGE_SIZE:
                    break
                page += 1
            sources = []
            for trip in trips:
                if book_rules.trip_mode(trip) != book_rules.LEGACY:
                    continue
                custody = self._custody(repository, trip["id"], current=False)
                trip_id, code = str(trip["id"]), trip_code(trip["id"])
                for line in custody["gold"]:
                    if line["designation"] not in (LOGO_DESIGNATION, UNCLASSIFIED) or line["outstanding"] <= 0:
                        continue
                    designation_text = "لوجو" if line["designation"] == LOGO_DESIGNATION else "بدون وجهة"
                    sources.append({
                        "key": f"custody-gold:{trip_id}:{line['karat_id']}:{line['designation']}",
                        "source_type": "CUSTODY_GOLD", "trip_id": trip_id, "trip_code": code, "karat_id": str(line["karat_id"]),
                        "designation": line["designation"], "available": str(line["outstanding"]),
                        "karat_value": int(line["karat_value"]),
                        "text": f"{code} • عيار {line['karat_value']} ({designation_text}) • المتاح {line['outstanding']:,.3f} جرام"})
                if custody.get("bar", 0) > 0:
                    sources.append({
                        "key": f"custody-bar:{trip_id}", "source_type": "TRIP_BAR", "trip_id": trip_id, "trip_code": code,
                        "karat_id": "", "designation": "BAR", "available": str(custody["bar"]), "karat_value": BASE_KARAT_VALUE,
                        "text": f"{code} • سبيكة السبك (مكافئ عيار 21) • المتاح {custody['bar']:,.3f} جرام"})
                for lot in custody["lots"]:
                    if lot["fineness"] is None or lot["outstanding"] != lot["weight"]:
                        continue
                    sources.append({
                        "key": f"custody-lot:{trip_id}:{lot['lot_id']}", "source_type": "CUSTODY_LOT",
                        "trip_id": trip_id, "trip_code": code, "lot_id": str(lot["lot_id"]), "karat_id": "",
                        "designation": "", "available": str(lot["weight"]), "karat_value": None,
                        "fineness": str(lot["fineness"]), "lot_kind": lot["kind"], "lot_no": int(lot["lot_no"]),
                        "text": f"{code} • شحنة {lot['lot_no']} • عيار فعلي {lot['fineness']} بالألف • "
                                f"{lot['weight']:,.3f} جرام"})
        return sources

    def trip_page(self, status=OPEN, page=1, page_size=TRIP_PAGE_SIZE, *, _expected_actor=None):
        """ONE page of the OPEN (active) or CLOSED (archive) trips, newest first (see `MarketTripRepository.trip_page`). The status filter and the order are
        applied before the limit; `has_more` says whether another page exists. Closed trips stay fully readable through `trip_detail`."""
        actor = self._actor(_expected_actor)
        if status not in (OPEN, CLOSED):
            raise TripError("STATUS_INVALID", "حالة العهدة غير صحيحة.")
        try:
            page, page_size = int(page), int(page_size)
        except (TypeError, ValueError):
            raise TripError("PAGE_INVALID", "رقم الصفحة غير صحيح.") from None
        if page < 1 or not 1 <= page_size <= MAX_TRIP_PAGE_SIZE:
            raise TripError("PAGE_INVALID", "رقم الصفحة غير صحيح.")
        with self._transaction() as connection:
            self._authorization.require_permission_read(connection, actor, PERMISSION)
            repository = self._repository(connection)
            rows = repository.trip_page(status, page, page_size)
            has_more, rows = len(rows) > page_size, rows[:page_size]
            external = self._external_holdings(connection, actor)
            result = [self._trip_row(row, self._custody(repository, row["id"]), external.get(int(row["id"]), []), self._book_summary(repository, row))
                      for row in rows]
        return {"status": status, "page": page, "page_size": page_size, "has_more": has_more, "rows": result}

    def trips(self, *, _expected_actor=None):
        """The unpaged legacy list (the 100 newest, every status). The custody screen uses `trip_page`; this stays for existing callers."""
        actor = self._actor(_expected_actor)
        with self._transaction() as connection:
            self._authorization.require_permission_read(connection, actor, PERMISSION)
            repository = self._repository(connection)
            rows = repository.trips()
            external = self._external_holdings(connection, actor)
            result = []
            for row in rows:
                result.append(self._trip_row(row, self._custody(repository, row["id"]), external.get(int(row["id"]), []),
                                             self._book_summary(repository, row)))
        return result

    def _external_holdings(self, connection, actor):
        """Sources of this trip's gold that still sit with an intermediary, per trip (only for an actor who may VIEW holdings)."""
        if VIEW_HOLDINGS not in actor.permissions:
            return {}
        grouped = {}
        ledger = self._holdings(connection)
        for row in ledger.open_pools():
            grouped.setdefault(int(row["trip_id"]), []).append(pool_row(row))
        for row in ledger.logo_open_pools():
            grouped.setdefault(int(row["trip_id"]), []).append({**logo_pool_row(row), "logo": True})
        return grouped

    def trip_detail(self, trip_id, *, _expected_actor=None):
        actor = self._actor(_expected_actor)
        trip_id = _trip_id(trip_id)
        with self._transaction() as connection:
            self._authorization.require_permission_read(connection, actor, PERMISSION)
            repository = self._repository(connection)
            trip = repository.trip(trip_id)
            if trip is None:
                raise TripError("TRIP_NOT_FOUND", "العهدة غير موجودة.")
            custody = self._custody(repository, trip_id)
            entries, events = repository.entries(trip_id, newest_first=True), repository.events(trip_id, newest_first=True)
            melts, heats = repository.melts(trip_id, newest_first=True), repository.heats(trip_id, newest_first=True)
            external = self._external_holdings(connection, actor).get(trip_id, [])
            book = self._book_view(repository, trip)
        return {"trip": self._trip_row(trip, custody, external, None if book is None else book["balance"]), "custody": custody, "external_holdings": external,
                "mode": book_rules.trip_mode(trip), "book": book,
                "entries": [self._entry_row(e) for e in entries],
                "melts": [self._melt_row(m) for m in melts],
                "heats": [self._heat_row(h) for h in heats], "heat_totals": self._heat_totals(heats),
                "events": [{"type": e["event_type"], "reason": e["reason"] or "", "at": e["occurred_at"],
                            "actor": e["actor_name"] or ""} for e in events]}

    @staticmethod
    def _trip_row(row, custody, external=(), book=None):
        return {"id": str(row["id"]), "code": trip_code(row["id"]), "status": row["status"], "notes": row["notes"] or "",
                "mode": book_rules.trip_mode(row), "book": book,
                "created_at": row["created_at"], "status_changed_at": row["status_changed_at"],
                "entry_count": int(row.get("entry_count", 0) or 0), "custody": custody, "external_holdings": list(external)}

    @staticmethod
    def _book_summary(repository, row):
        """The book balance view of a BOOK trip (None for a LEGACY trip: no query is made for it)."""
        if book_rules.trip_mode(row) != book_rules.BOOK:
            return None
        return book_rules.balance_of_rows(repository.book_lines(row["id"])).view()

    @staticmethod
    def _book_view(repository, trip):
        """The BOOK part of a trip detail: the balance (posted, analytical, drift), and the assayed return-stock items this trip created with what is left
        of each in the shop. None for a LEGACY trip."""
        if book_rules.trip_mode(trip) != book_rules.BOOK:
            return None
        balance = book_rules.balance_of_rows(repository.book_lines(trip["id"]))
        items = [{"entry_id": str(i["entry_id"]), "line_no": int(i["line_no"]), "code": entry_code(i["trip_id"], i["entry_no"]),
                  "fineness": Decimal(str(i["fineness_permille"])), "original_weight": Decimal(str(i["original_weight"])),
                  "original_posted": Decimal(str(i["original_posted"])), "grams": Decimal(str(i["grams"])), "posted": Decimal(str(i["posted"]))}
                 for i in repository.assayed_stock_items() if int(i["trip_id"]) == int(trip["id"])]
        shop_stock = [{"entry_id": str(i["entry_id"]), "line_no": int(i["line_no"]), "code": entry_code(i["trip_id"], i["entry_no"]),
                       "fineness": Decimal(str(i["fineness_permille"])), "grams": Decimal(str(i["grams"])), "posted": Decimal(str(i["posted"]))}
                      for i in repository.assayed_stock_items() if Decimal(str(i["grams"])) > 0]       # 058: what the SHOP holds, any origin trip (the stock reissue source)
        return {"balance": balance.view(), "balance_exact": balance, "stock_items": items, "shop_stock": shop_stock}

    @staticmethod
    def _entry_row(e):
        return {"id": str(e["id"]), "trip_id": str(e["trip_id"]), "code": entry_code(e["trip_id"], e["entry_no"]),
                "entry_no": int(e["entry_no"]), "kind": e["entry_kind"], "date": e["entry_date"], "notes": e["notes"] or "",
                "at": e["occurred_at"], "actor": e["actor_name"] or "",
                "cash": None if e["cash_amount"] is None else Decimal(str(e["cash_amount"])),
                "gold": [{"karat_id": int(g["karat_id"]), "karat_value": int(g["karat_value"]),
                          "designation": g.get("designation", UNCLASSIFIED), "weight": Decimal(str(g["weight_grams"]))}
                         for g in e["gold"]],
                "lot_returns": [{"lot_id": str(l["lot_id"]), "lot_no": int(l["lot_no"]), "kind": l["lot_kind"],
                                 "code": entry_code(l["trip_id"], l["melt_entry_no"]), "weight": Decimal(str(l["weight_grams"])),
                                 "fineness": None if l["fineness_permille"] is None else Decimal(str(l["fineness_permille"]))}
                                for l in e.get("lot_returns", [])],
                "lot_issues": [{"lot_id": str(l["lot_id"]), "lot_no": int(l["lot_no"]), "kind": l["lot_kind"],
                                "code": entry_code(l["trip_id"], l["melt_entry_no"]), "weight": Decimal(str(l["weight_grams"])),
                                "fineness": None if l["fineness_permille"] is None else Decimal(str(l["fineness_permille"]))}
                               for l in e.get("lot_issues", [])],
                "supplier_payment": None if not e.get("supplier_payment") else {
                    "payment_id": str(e["supplier_payment"]["payment_id"]), "code": payment_code(e["supplier_payment"]["payment_id"]),
                    "supplier_id": str(e["supplier_payment"]["supplier_id"]), "supplier_name": e["supplier_payment"]["supplier_name"],
                    "amount": Decimal(str(e["supplier_payment"]["amount"]))},
                "gold_settlement": None if not e.get("gold_settlement") else {
                    "settlement_id": str(e["gold_settlement"]["settlement_id"]), "code": settlement_code(e["gold_settlement"]["settlement_id"]),
                    "supplier_id": str(e["gold_settlement"]["supplier_id"]), "supplier_name": e["gold_settlement"]["supplier_name"],
                    "karat_value": int(e["gold_settlement"]["karat_value"]), "designation": e["gold_settlement"]["designation"],
                    "delivered_weight": Decimal(str(e["gold_settlement"]["weight_grams"])),
                    "target_karat": int(e["gold_settlement"]["target_karat_value"]),
                    "credited_weight": Decimal(str(e["gold_settlement"]["credited_weight_grams"])),
                    "analytical": Decimal(str(e["gold_settlement"]["credited_analytical_grams"])),
                    "rounding_difference": Decimal(str(e["gold_settlement"]["rounding_difference_grams"]))},
                "gold_sale": None if not e.get("gold_sale") else MarketTripService._gold_sale_result(e, e["gold_sale"], False),
                "gold_buy": None if not e.get("gold_buy") else MarketTripService._gold_buy_result(e, e["gold_buy"], False),
                "lot_settlement": None if not e.get("lot_settlement") else {
                    **MarketTripService._lot_settle_result(e, e["lot_settlement"], False),
                    "supplier_name": e["lot_settlement"]["supplier_name"]},
                "logo_holding": None if not e.get("logo_holding") else {
                    "document_id": str(e["logo_holding"]["document_id"]), "code": logo_holding_code(e["logo_holding"]["document_id"]),
                    "kind": e["logo_holding"]["doc_kind"], "party_name": e["logo_holding"]["party_name"],
                    "karat_value": int(e["logo_holding"]["karat_value"]), "weight": Decimal(str(e["logo_holding"]["weight_grams"]))},
                "holding": None if not e.get("holding") else {
                    "document_id": str(e["holding"]["document_id"]), "code": holding_code(e["holding"]["document_id"]),
                    "kind": e["holding"]["doc_kind"], "party_id": str(e["holding"]["party_id"]), "party_name": e["holding"]["party_name"],
                    "lot_id": str(e["holding"]["lot_id"]), "lot_no": int(e["holding"]["lot_no"]), "lot_kind": e["holding"]["lot_kind"],
                    "origin": entry_code(e["holding"]["origin_trip_id"], e["holding"]["origin_entry_no"]),
                    "weight": Decimal(str(e["holding"]["weight_grams"])), "fineness": Decimal(str(e["holding"]["fineness_permille"]))},
                "book_lines": [{"line_no": int(l["line_no"]), "direction": l["direction"], "weight": Decimal(str(l["weight_grams"])), "basis": l["basis"],
                                "karat_value": None if l["karat_value"] is None else int(l["karat_value"]),
                                "fineness": None if l["fineness_permille"] is None else Decimal(str(l["fineness_permille"])),
                                "analytical": Decimal(str(l["eq21_analytical"])), "posted": Decimal(str(l["eq21_posted"])),
                                "difference": Decimal(str(l["posting_difference"])),
                                "price": None if l["line_price"] is None else Decimal(str(l["line_price"])),
                                "scrap": l["scrap_movement_id"] is not None} for l in e.get("book_lines", [])],
                "book_trade": None if not e.get("book_trade") else {
                    "kind": e["book_trade"]["entry_kind"], "counterparty_kind": e["book_trade"]["counterparty_kind"],
                    "party_id": None if e["book_trade"]["party_id"] is None else str(e["book_trade"]["party_id"]),
                    "name": e["book_trade"]["counterparty_name"], "total": Decimal(str(e["book_trade"]["total_price"])),
                    "reference_total": None if e["book_trade"]["reference_total"] is None else Decimal(str(e["book_trade"]["reference_total"])),
                    "deviation": None if e["book_trade"]["deviation_percent"] is None else Decimal(str(e["book_trade"]["deviation_percent"])),
                    "acknowledged": bool(e["book_trade"]["price_warning_acknowledged"])},
                "gain": None if not e.get("gain") else {"explanation": e["gain"]["explanation"], "evidence_reference": e["gain"]["evidence_reference"],
                                                        "confirmed_by": e["gain"].get("confirmed_by") or ""},
                "bar_supplier": None if not e.get("bar_supplier") else {
                    "settlement_id": str(e["bar_supplier"]["settlement_id"]), "code": settlement_code(e["bar_supplier"]["settlement_id"]),
                    "supplier_id": str(e["bar_supplier"]["supplier_id"]), "supplier_name": e["bar_supplier"]["supplier_name"],
                    "delivered_weight": Decimal(str(e["bar_supplier"]["weight_grams"])), "target_karat": int(e["bar_supplier"]["target_karat_value"]),
                    "credited_weight": Decimal(str(e["bar_supplier"]["credited_weight_grams"])),
                    "analytical": Decimal(str(e["bar_supplier"]["credited_analytical_grams"])),
                    "rounding_difference": Decimal(str(e["bar_supplier"]["rounding_difference_grams"]))},
                "settle_logo": [{"karat_value": int(g["karat_value"]), "weight": Decimal(str(g["weight_grams"]))} for g in (e.get("settle_logo") or [])],
                "settle_lines": [{"source": l["source"], "karat_value": None if l.get("karat_value") is None else int(l["karat_value"]),
                                  "weight": Decimal(str(l["weight_grams"])), "classification": l.get("classification")} for l in (e.get("settle_lines") or [])],
                "settle_adjust": None if not e.get("settle_adjust") else {"karat_value": int(e["settle_adjust"]["karat_value"]), "direction": e["settle_adjust"]["direction"],
                                                                          "weight": Decimal(str(e["settle_adjust"]["weight_grams"])), "reason": e["settle_adjust"]["reason"]},
                "settle_cash": None if not e.get("settle_cash") else {"amount": Decimal(str(e["settle_cash"]["amount"])), "description": e["settle_cash"].get("description") or "",
                                                                      "reference": e["settle_cash"].get("reference") or "", "bank_account": e["settle_cash"].get("bank_account_name") or ""},
                "bar_marketer": None if not e.get("bar_marketer") else {
                    "party_id": str(e["bar_marketer"]["party_id"]), "party_name": e["bar_marketer"]["party_name"], "grams": Decimal(str(e["bar_marketer"]["weight_grams"]))},
                "settle_melt": None if not e.get("settle_melt") else {
                    "workshop": e["settle_melt"]["workshop"], "date": e["settle_melt"]["melt_date"],
                    "input_weight": Decimal(str(e["settle_melt"]["input_weight_grams"])), "output_weight": Decimal(str(e["settle_melt"]["output_weight_grams"])),
                    "fineness": int(e["settle_melt"]["fineness_permille"]), "theoretical": Decimal(str(e["settle_melt"]["theoretical_eq21"])),
                    "actual": Decimal(str(e["settle_melt"]["actual_eq21"])), "loss": Decimal(str(e["settle_melt"]["loss_eq21"])),
                    "weight_loss": Decimal(str(e["settle_melt"]["weight_loss_eq21"])), "fineness_loss": Decimal(str(e["settle_melt"]["fineness_loss_eq21"])),
                    "inputs": [{"karat_value": int(i["karat_value"]), "weight": Decimal(str(i["weight_grams"]))} for i in e["settle_melt"]["inputs"]]},
                "recon": None if not e.get("recon") else {
                    "generation": int(e["recon"]["generation"]), "effective_date": e["recon"]["effective_date"], "a": Decimal(str(e["recon"]["a_star"])),
                    "b": Decimal(str(e["recon"]["b_star"])), "loss": Decimal(str(e["recon"]["loss_posted"])),
                    "rounding": Decimal(str(e["recon"]["rounding_settlement"])), "reason_code": e["recon"]["reason_code"], "note": e["recon"]["note"] or "",
                    "confirmed_at": e["recon"]["confirmed_at"], "confirmed_by": e["recon"].get("confirmed_by") or ""},
                "reversed": e["reversal_id"] is not None,
                "reversal_code": None if e["reversal_id"] is None else correction_code("FULL_REVERSAL", e["reversal_id"]),
                "reversal_reason": e["reversal_reason"], "reversal_at": e["reversal_at"]}

    @staticmethod
    def _melt_row(m):
        """One melt with its raw facts and its permanent analysis (kept after closure and after reversal)."""
        def dec(value):
            return None if value is None else Decimal(str(value))
        return {"entry_id": str(m["entry_id"]), "code": entry_code(m["trip_id"], m["entry_no"]), "date": m["entry_date"],
                "workshop": m["workshop"], "assay_reference": m["assay_reference"] or "",
                "gain_explanation": m["gain_explanation"] or "", "notes": m["notes"] or "", "actor": m.get("actor_name") or "",
                "at": m["occurred_at"],
                "inputs": [{"karat_id": int(i["karat_id"]), "karat_value": int(i["karat_value"]),
                            "weight": Decimal(str(i["weight_grams"])), "pure": Decimal(str(i["pure_grams"]))} for i in m["inputs"]],
                "lots": [{"lot_no": int(l["lot_no"]), "kind": l["lot_kind"], "label": l["label"] or "",
                          "weight": Decimal(str(l["weight_grams"])), "fineness": dec(l["fineness_permille"]),
                          "pure": dec(l["pure_grams"])} for l in m["lots"]],
                "analysis": {"input_weight": dec(m["input_weight_grams"]), "nominal_pure": dec(m["nominal_pure_grams"]),
                             "output_weight": dec(m["output_weight_grams"]), "known_output_pure": dec(m["known_output_pure_grams"]),
                             "weight_difference": dec(m["weight_difference_grams"]), "pure_complete": bool(m["pure_complete"]),
                             "pure_difference": dec(m["pure_difference_grams"]), "nominal_eq21": dec(m["nominal_eq21_grams"]),
                             "known_output_eq21": dec(m["known_output_eq21_grams"]), "eq21_difference": dec(m["eq21_difference_grams"])},
                "reversed": m["reversal_id"] is not None,
                "reversal_code": None if m["reversal_id"] is None else correction_code("FULL_REVERSAL", m["reversal_id"]),
                "reversal_reason": m["reversal_reason"]}

    # ------------------------------------------------------------------ issue and return entries
    @staticmethod
    def _lines(cash, gold, kind, allow_empty=False):
        """Validated (cash, {(karat_id, designation): grams}) with at least one positive quantity; the same pair twice is summed.
        The same karat may carry both designations (separate custody balances, separate Scrap movements)."""
        amount = parse_cash(cash)
        grams = {}
        for line in gold or []:
            try:
                karat_id = int(str(line.get("karat_id") if isinstance(line, dict) else line[0]).strip())
            except (TypeError, ValueError, AttributeError, IndexError):
                raise TripError("KARAT_REQUIRED", "اختر العيار.") from None
            weight = parse_weight(line.get("weight") if isinstance(line, dict) else line[1])
            if weight == 0:
                continue
            raw = line.get("designation") if isinstance(line, dict) else (line[2] if len(line) > 2 else None)
            key = (karat_id, parse_designation(raw, kind, allow_blank=(kind == ISSUE)))
            grams[key] = grams.get(key, ZERO_WEIGHT) + weight
        if amount == 0 and not grams and not allow_empty:
            raise TripError("NOTHING_TO_RECORD", "أدخل مبلغًا نقديًا أو وزن ذهب موجبًا واحدًا على الأقل.")
        return amount, grams

    def issue(self, trip_id, cash, gold, entry_date, notes, request_key, *, _expected_actor=None):
        """ISSUE custody on a new trip (trip_id empty) or an OPEN one."""
        return self._entry(ISSUE, trip_id, cash, gold, entry_date, notes, request_key, _expected_actor)

    def return_custody(self, trip_id, cash, gold, entry_date, notes, request_key, *, _expected_actor=None):
        """RETURN part or all of the outstanding custody of an OPEN trip."""
        if str(trip_id or "").strip() == "":
            raise TripError("TRIP_NOT_FOUND", "اختر العهدة.")
        return self._entry(RETURN, trip_id, cash, gold, entry_date, notes, request_key, _expected_actor)

    def _entry(self, kind, trip_id, cash, gold, entry_date, notes, request_key, expected_actor):
        session_actor = self._actor(expected_actor)
        new_trip = str(trip_id or "").strip() == ""
        trip_number = None if new_trip else _trip_id(trip_id)
        amount, grams = self._lines(cash, gold, kind)
        paid_on, notes_text, key = _entry_date(entry_date), _text(notes, NOTES_MAX, "الملاحظات"), _key(request_key)
        digest = hashlib.sha256(json.dumps(
            [kind, trip_number, str(amount), sorted((k, d, str(w)) for (k, d), w in grams.items()), paid_on.isoformat(), notes_text],
            ensure_ascii=False).encode()).hexdigest()
        try:
            return self._post(kind, session_actor, trip_number, amount, grams, paid_on, notes_text, key, digest)
        except IntegrityError as exc:
            if not _is_request_key_violation(exc):
                raise                                              # any other integrity error is a real defect
            return self._replay(session_actor, key, digest, exc)   # a twin committed first: decide from a FRESH read

    def _open_trip(self, repository, trip_number, key, digest, actor, *, legacy_only=True):
        """The locked OPEN trip for an entry, or the replay of an already-recorded request (returned as the second item). A legacy-only operation refuses a
        BOOK trip (the cash entries pass legacy_only=False and refuse gold themselves)."""
        trip = repository.trip(trip_number, lock=True)
        if trip is None:
            raise TripError("TRIP_NOT_FOUND", "العهدة غير موجودة.")
        prior = repository.by_request(key)
        if prior is not None:
            return None, self._same_request(prior, digest, actor)
        if legacy_only:
            book_rules.require_legacy_trip(trip)
        if trip["status"] != OPEN:
            raise TripError("TRIP_CLOSED", "العهدة مغلقة: أعد فتحها أولاً لإخراج عهدة أو تسجيل رجوع أو سبك.")
        return trip, None

    def _post(self, kind, session_actor, trip_number, amount, grams, paid_on, notes_text, key, digest):
        with self._transaction() as connection:
            actor = self._authorization.require_permission_for_write(connection, session_actor, PERMISSION)
            repository = self._repository(connection)
            created = trip_number is None
            if created:
                prior = repository.by_request(key)
                if prior is not None:
                    return self._same_request(prior, digest, actor)
                trip_id = repository.insert_trip(notes=notes_text, actor=actor, now=repository.db_now())
            else:
                trip, replay = self._open_trip(repository, trip_number, key, digest, actor, legacy_only=False)
                if replay is not None:
                    return replay
                if grams and book_rules.trip_mode(trip) == book_rules.BOOK:
                    raise TripError("BOOK_GOLD_PATH", "العهدة الدفترية لا تقبل سطور ذهب بالنمط القديم: استخدم حركات الذهب الدفترية (إخراج / شراء / بيع / رجوع).")
                trip_id = trip_number
            account, karats = self._issue_resources(connection, repository, kind, trip_id, amount, grams)
            entry_id, code = self._write_entry(repository, kind=kind, trip_id=trip_id, paid_on=paid_on, notes_text=notes_text, key=key,
                                               digest=digest, actor=actor, amount=amount, grams=grams, account=account, karats=karats,
                                               created=created)
            if not self._session.is_current(session_actor):
                raise PermissionError("انتهت جلسة الدخول.")
        return {"entry_id": str(entry_id), "trip_id": str(trip_id), "trip_code": trip_code(trip_id), "code": code, "kind": kind,
                "created": created, "duplicate": False}

    def _issue_resources(self, connection, repository, kind, trip_id, amount, grams):
        """The locked MAIN_SAFE account (when cash is moved), the active karats and - for an issue - the AVAILABLE Scrap per karat
        (both designations of a karat draw on the same Scrap) or - for a return - the outstanding custody. Locks and checks only."""
        account = None
        if amount > 0:
            safe = repository.main_safe()
            if safe is None:
                raise TripError("ACCOUNT_NOT_FOUND", "الخزنة الرئيسية غير موجودة.")
            account = self._accounts(connection).account("CASH", safe["id"], lock=True)
            if account is None:
                raise TripError("ACCOUNT_NOT_FOUND", "الخزنة الرئيسية غير موجودة.")
            if not account["is_active"]:
                raise TripError("ACCOUNT_INACTIVE", f"الحساب «{account['name']}» غير نشط.")
            if kind == ISSUE:                                            # the safe may never go negative (read under the account lock)
                balance = Decimal(str(repository.cash_balance(account["id"])))
                if amount > balance:
                    raise TripError("SAFE_SHORTAGE", f"رصيد الخزنة الرئيسية غير كافٍ: المطلوب {amount:,.2f} والمتاح {balance:,.2f} جنيه.")
        karats = {}
        for karat_id in sorted({k for k, _ in grams}):
            karat = repository.karat(karat_id)
            if karat is None or not karat["is_active"]:
                raise TripError("KARAT_NOT_FOUND", "العيار غير موجود أو غير نشط.")
            karats[karat_id] = karat
        scrap = self._scrap(connection)
        if kind == ISSUE:
            per_karat = {}
            for (karat_id, _), weight in grams.items():                 # both designations of a karat draw on the SAME Scrap
                per_karat[karat_id] = per_karat.get(karat_id, ZERO_WEIGHT) + weight
            for karat_id in sorted(per_karat):                           # only AVAILABLE Scrap: manager-review holds are excluded
                available = sum((Decimal(str(r["weight_delta_grams"])) for r in scrap.lock_old_gold_balance(karat_id)),
                                ZERO_WEIGHT)
                if per_karat[karat_id] > available:
                    raise TripError("SCRAP_SHORTAGE", shortage_message(
                        karats[karat_id]["karat_value"], per_karat[karat_id], available, held_grams(scrap, karat_id)))
        else:
            self._check_returnable(repository, trip_id, amount, grams, karats)
        return account, karats

    def _write_entry(self, repository, *, kind, trip_id, paid_on, notes_text, key, digest, actor, amount, grams, account, karats, created):
        """Post one ISSUE / RETURN entry on the caller's connection: the entry, its cash leg, one Scrap leg per (karat, designation),
        the OPENED event of a new trip and the audit row. Returns (entry_id, code)."""
        now = repository.db_now()
        entry_no = repository.next_entry_no(trip_id)
        entry_id = repository.insert_entry(trip_id=trip_id, entry_no=entry_no, kind=kind, entry_date=paid_on,
                                           notes=notes_text, key=key, digest=digest, actor=actor, now=now)
        code = entry_code(trip_id, entry_no)
        label = f"{KIND_TEXT[kind]} {code}"
        if amount > 0:
            movement = repository.post_cash(kind=kind, account_id=account["id"], entry_id=entry_id, code=code, amount=amount,
                                            description=label, actor=actor, now=now)
            repository.insert_cash_leg(entry_id=entry_id, amount=amount, cash_account_id=account["id"], cash_movement_id=movement)
        for (karat_id, designation), weight in sorted(grams.items()):   # one Scrap movement per (karat, designation) leg
            movement = repository.post_scrap(kind=kind, karat_id=karat_id, weight=weight, entry_id=entry_id, code=code,
                                             notes=f"{label} — {DESIGNATION_TEXT[designation]}", actor=actor, now=now)
            repository.insert_gold_leg(entry_id=entry_id, karat_id=karat_id, designation=designation, weight=weight,
                                       scrap_movement_id=movement)
        if created:
            repository.insert_event(trip_id=trip_id, event_type="OPENED", reason=None, actor=actor, now=now)
        gold_meta = {}
        for (karat_id, designation), weight in sorted(grams.items()):
            gold_meta.setdefault(str(karats[karat_id]["karat_value"]), {})[designation] = str(weight)
        repository.audit(actor=actor, event_type="MARKET_TRIP_ISSUE" if kind == ISSUE else "MARKET_TRIP_RETURN",
                         entity_id=entry_id, reference=code, description=f"{label} — {trip_code(trip_id)}",
                         metadata={"trip_id": trip_id, "kind": kind, "cash": str(amount), "date": paid_on.isoformat(),
                                   "gold": gold_meta, "employee_id": actor.employee_id}, now=now)
        return entry_id, code

    @staticmethod
    def _same_request(prior, digest, actor):
        if prior["request_hash"] != digest or int(prior["performed_by_user_id"]) != int(actor.user_id):
            raise TripError("REQUEST_CONFLICT", "طلب العملية مستخدم لعملية مختلفة.")
        return {"entry_id": str(prior["id"]), "trip_id": str(prior["trip_id"]), "trip_code": trip_code(prior["trip_id"]),
                "code": entry_code(prior["trip_id"], prior["entry_no"]), "kind": prior["entry_kind"], "created": False,
                "duplicate": True}

    def _replay(self, session_actor, key, digest, original):
        with self._transaction() as connection:
            actor = self._authorization.require_permission_for_write(connection, session_actor, PERMISSION)
            prior = self._repository(connection).by_request(key)
            if prior is None:
                raise original
            return self._same_request(prior, digest, actor)

    @staticmethod
    def _check_returnable(repository, trip_id, amount, grams, karats):
        """A return can never exceed the outstanding custody: cash and each (karat, designation) on their own (read under the trip lock)."""
        custody = custody_view(repository.custody_cash(trip_id, current=True), repository.custody_gold(trip_id, current=True))
        if amount > custody["cash"]["outstanding"]:
            raise TripError("RETURN_EXCEEDS_CUSTODY",
                            f"النقدية المتبقية في العهدة {custody['cash']['outstanding']:,.2f} ج فقط.")
        outstanding = {(g["karat_id"], g["designation"]): g["outstanding"] for g in custody["gold"]}
        for (karat_id, designation), weight in sorted(grams.items()):
            left = outstanding.get((karat_id, designation), ZERO_WEIGHT)
            if weight > left:
                raise TripError("RETURN_EXCEEDS_CUSTODY",
                                f"المتبقي في العهدة من عيار {karats[karat_id]['karat_value']} ({DESIGNATION_TEXT[designation]}) "
                                f"هو {left:,.3f} جرام فقط.")

    # ------------------------------------------------------------------ melting
    @staticmethod
    def _melt_plan(inputs, output, residues):
        """Validated melt facts: {karat_id: grams}, (main weight, main fineness) and [(weight, fineness|None, label)]. Specific
        refusals; nothing is rounded and no fineness is inferred."""
        grams = {}
        for line in inputs or []:
            try:
                karat_id = int(str(line.get("karat_id") if isinstance(line, dict) else line[0]).strip())
            except (TypeError, ValueError, AttributeError, IndexError):
                raise TripError("KARAT_REQUIRED", "اختر عيار كل سطر من مدخلات السبك.") from None
            weight = parse_weight(line.get("weight") if isinstance(line, dict) else line[1])
            if weight == 0:
                continue
            grams[karat_id] = grams.get(karat_id, ZERO_WEIGHT) + weight
        if not grams:
            raise TripError("MELT_INPUT_REQUIRED", "أدخل وزنًا موجبًا واحدًا على الأقل من ذهب العهدة المخصص للسبك.")
        output = output or {}
        main_weight = parse_weight(output.get("weight"))
        if main_weight == 0:
            raise TripError("OUTPUT_WEIGHT_REQUIRED", "وزن ناتج السبك الرئيسي مطلوب بقيمة موجبة.")
        main_fineness = parse_fineness(output.get("fineness"), "العيار الفعلي للناتج الرئيسي")
        lots = []
        for row in residues or []:
            raw_weight, raw_fineness, label = row.get("weight"), row.get("fineness"), _text(row.get("label"), LABEL_MAX, "وصف الراسب")
            if not str(raw_weight or "").strip() and not str(raw_fineness or "").strip() and not label:
                continue                                          # an empty row is just an unused line
            if not str(raw_weight or "").strip():
                raise TripError("RESIDUE_WEIGHT_REQUIRED", "وزن الراسب مطلوب بقيمة موجبة.")
            weight = parse_weight(raw_weight)
            if weight == 0:
                raise TripError("RESIDUE_WEIGHT_REQUIRED", "وزن الراسب يجب أن يكون أكبر من صفر.")
            lots.append((weight, parse_fineness(raw_fineness, "العيار الفعلي للراسب", required=False), label))
        if len(lots) > MAX_RESIDUES:
            raise TripError("TOO_MANY_RESIDUES", f"الحد الأقصى {MAX_RESIDUES} رواسب في السبكة الواحدة.")
        return grams, (main_weight, main_fineness), lots

    def melt(self, trip_id, inputs, entry_date, workshop, output, residues, assay_reference, gain_explanation, notes, request_key,
             *, _expected_actor=None):
        """MELT designated custody of an OPEN trip into an assayed main lot (+ optional residue lots), atomically and permanently
        analysed. Consumes MELT-designated custody only: never LOGO or UNCLASSIFIED gold, never shop Scrap, no cash, no supplier."""
        session_actor = self._actor(_expected_actor)
        if str(trip_id or "").strip() == "":
            raise TripError("TRIP_NOT_FOUND", "اختر العهدة.")
        trip_number = _trip_id(trip_id)
        grams, main, residue_lots = self._melt_plan(inputs, output, residues)
        melted_on = _entry_date(entry_date)
        workshop_text = _text(workshop, WORKSHOP_MAX, "اسم الورشة أو المسؤول", required=True)
        assay_text, notes_text = _text(assay_reference, ASSAY_MAX, "مرجع المعايرة"), _text(notes, NOTES_MAX, "الملاحظات")
        gain_text = _text(gain_explanation, NOTES_MAX, "سبب زيادة الوزن")
        key = _key(request_key)
        total_out = main[0] + sum((w for w, _, _ in residue_lots), ZERO_WEIGHT)
        if total_out > sum(grams.values(), ZERO_WEIGHT) and not gain_text:
            raise TripError("GAIN_EXPLANATION_REQUIRED",
                            f"وزن النواتج ({total_out:,.3f}) أكبر من وزن المدخلات ({sum(grams.values(), ZERO_WEIGHT):,.3f}): "
                            "اكتب سبب زيادة الوزن.")
        digest = hashlib.sha256(json.dumps(
            [MELT, trip_number, sorted((k, str(w)) for k, w in grams.items()), melted_on.isoformat(), workshop_text, assay_text,
             gain_text, notes_text, [str(main[0]), str(main[1])],
             [[str(w), None if f is None else str(f), label] for w, f, label in residue_lots]], ensure_ascii=False).encode()).hexdigest()
        try:
            return self._post_melt(session_actor, trip_number, grams, main, residue_lots, melted_on, workshop_text, assay_text,
                                   gain_text, notes_text, key, digest)
        except IntegrityError as exc:
            if not _is_request_key_violation(exc):
                raise
            return self._replay(session_actor, key, digest, exc)

    def _post_melt(self, session_actor, trip_id, grams, main, residue_lots, melted_on, workshop, assay, gain, notes, key, digest):
        with self._transaction() as connection:
            actor = self._authorization.require_permission_for_write(connection, session_actor, PERMISSION)
            repository = self._repository(connection)
            trip, replay = self._open_trip(repository, trip_id, key, digest, actor)
            if replay is not None:
                return replay
            karats = {}
            for karat_id in sorted(grams):
                karat = repository.karat(karat_id)
                if karat is None or not karat["is_active"]:
                    raise TripError("KARAT_NOT_FOUND", "العيار غير موجود أو غير نشط.")
                karats[karat_id] = karat
            custody = self._custody(repository, trip_id, current=True)      # read under the trip lock
            by_key = {(g["karat_id"], g["designation"]): g["outstanding"] for g in custody["gold"]}
            for karat_id in sorted(grams):
                available = by_key.get((karat_id, MELT_DESIGNATION), ZERO_WEIGHT)
                if grams[karat_id] > available:
                    others = [f"{DESIGNATION_TEXT[d]} {by_key[(karat_id, d)]:,.3f}" for d in (LOGO_DESIGNATION, UNCLASSIFIED)
                              if by_key.get((karat_id, d), ZERO_WEIGHT) > 0]
                    raise TripError("MELT_EXCEEDS_CUSTODY",
                                    f"المتاح للسبك في العهدة من عيار {karats[karat_id]['karat_value']} هو {available:,.3f} جرام فقط."
                                    + (f" (ذهب هذا العيار الآخر لا يُسبك: {'، '.join(others)} جرام)." if others else ""))
            residues = [(w, f) for w, f, _ in residue_lots]
            analysis = analyse([(karats[k]["karat_value"], w) for k, w in sorted(grams.items())], main, residues)
            now = repository.db_now()
            entry_no = repository.next_entry_no(trip_id)
            entry_id = repository.insert_entry(trip_id=trip_id, entry_no=entry_no, kind=MELT, entry_date=melted_on, notes=notes,
                                               key=key, digest=digest, actor=actor, now=now)
            code = entry_code(trip_id, entry_no)
            repository.insert_melt(entry_id=entry_id, workshop=workshop, assay_reference=assay, gain_explanation=gain,
                                   analysis=analysis)
            for karat_id, weight in sorted(grams.items()):
                repository.insert_melt_input(entry_id=entry_id, karat_id=karat_id, weight=weight,
                                             pure=line_pure(weight, karats[karat_id]["karat_value"]))
            lots = [(MAIN_LOT, None, main[0], main[1])] + [(RESIDUE_LOT, label, w, f) for w, f, label in residue_lots]
            for number, (kind, label, weight, fineness) in enumerate(lots, start=1):
                lot_id = repository.insert_lot(entry_id=entry_id, lot_no=number, kind=kind, label=label, weight=weight,
                                               fineness=fineness, pure=lot_pure(weight, fineness))
                repository.insert_lot_movement(lot_id=lot_id, entry_id=entry_id, movement_type=CREATE_MOVEMENT, weight_delta=weight)
            repository.audit(actor=actor, event_type="MARKET_TRIP_MELT", entity_id=entry_id, reference=code,
                             description=f"{KIND_TEXT[MELT]} {code} — {trip_code(trip_id)}",
                             metadata={"trip_id": trip_id, "kind": MELT, "date": melted_on.isoformat(), "workshop": workshop,
                                       "inputs": {str(karats[k]["karat_value"]): str(w) for k, w in grams.items()},
                                       "main": {"weight": str(main[0]), "fineness": str(main[1])},
                                       "residues": [{"weight": str(w), "fineness": None if f is None else str(f)} for w, f in residues],
                                       "weight_difference": str(analysis["weight_difference"]),
                                       "pure_difference": None if analysis["pure_difference"] is None else str(analysis["pure_difference"]),
                                       "pure_complete": analysis["pure_complete"], "employee_id": actor.employee_id}, now=now)
            if not self._session.is_current(session_actor):
                raise PermissionError("انتهت جلسة الدخول.")
        return {"entry_id": str(entry_id), "trip_id": str(trip_id), "trip_code": trip_code(trip_id), "code": code, "kind": MELT,
                "created": False, "duplicate": False, "analysis": analysis}

    @staticmethod
    def _heat_row(h):
        """One heating document with its raw measurements (kept after closure and after reversal)."""
        lines = [{"karat_id": int(l["karat_id"]), "karat_value": int(l["karat_value"]), "input": Decimal(str(l["input_weight_grams"])),
                  "output": Decimal(str(l["output_weight_grams"])), "difference": Decimal(str(l["difference_grams"]))}
                 for l in h["lines"]]
        return {"entry_id": str(h["entry_id"]), "code": entry_code(h["trip_id"], h["entry_no"]), "date": h["entry_date"],
                "workshop": h["workshop"], "reference": h["reference"] or "", "gain_explanation": h["gain_explanation"] or "",
                "notes": h["notes"] or "", "actor": h.get("actor_name") or "", "at": h["occurred_at"], "lines": lines,
                "total_input": Decimal(str(h["total_input_grams"])), "total_output": Decimal(str(h["total_output_grams"])),
                "total_difference": Decimal(str(h["total_difference_grams"])),
                "reversed": h["reversal_id"] is not None,
                "reversal_code": None if h["reversal_id"] is None else correction_code("FULL_REVERSAL", h["reversal_id"]),
                "reversal_reason": h["reversal_reason"]}

    @staticmethod
    def _heat_totals(heats):
        """The cumulative EFFECTIVE heating per karat: the sum of the non-reversed documents' own differences. Each document is
        independent evidence (Logo custody is pooled per karat; nothing links one heat's output to the next heat's input)."""
        totals = {}
        for h in heats:
            if h["reversal_id"] is not None:
                continue
            for l in h["lines"]:
                row = totals.setdefault(int(l["karat_id"]), {"karat_id": int(l["karat_id"]), "karat_value": int(l["karat_value"]),
                                                             "input": ZERO_WEIGHT, "output": ZERO_WEIGHT, "difference": ZERO_WEIGHT})
                row["input"] += Decimal(str(l["input_weight_grams"]))
                row["output"] += Decimal(str(l["output_weight_grams"]))
                row["difference"] += Decimal(str(l["difference_grams"]))
        return sorted(totals.values(), key=lambda r: -r["karat_value"])

    # ------------------------------------------------------------------ Logo heating (042)
    @staticmethod
    def _heat_lines(lines):
        """Validated {karat_id: (input grams, output grams)}: one line per karat, both weights positive. A fully blank line is unused."""
        plan = {}
        for line in lines or []:
            raw_karat = str(line.get("karat_id") if isinstance(line, dict) else line[0]).strip()
            raw_in = line.get("input_weight") if isinstance(line, dict) else line[1]
            raw_out = line.get("output_weight") if isinstance(line, dict) else line[2]
            if raw_karat in ("", "None") and not str(raw_in or "").strip() and not str(raw_out or "").strip():
                continue
            try:
                karat_id = int(raw_karat)
            except ValueError:
                raise TripError("KARAT_REQUIRED", "اختر عيار كل سطر من سطور الحمّية.") from None
            if karat_id in plan:
                raise TripError("KARAT_DUPLICATE", "العيار نفسه مكرر: سطر واحد لكل عيار في المستند الواحد.")
            before, after = parse_weight(raw_in), parse_weight(raw_out)
            if before == 0:
                raise TripError("HEAT_INPUT_REQUIRED", "وزن اللوجو المُسخَّن قبل الحمّية مطلوب بقيمة موجبة.")
            if after == 0:
                raise TripError("HEAT_OUTPUT_REQUIRED", "الوزن الفعلي بعد الحمّية مطلوب بقيمة موجبة.")
            plan[karat_id] = (before, after)
        if not plan:
            raise TripError("HEAT_LINES_REQUIRED", "أدخل سطر حمّية واحدًا على الأقل: العيار والوزن قبل وبعد الحمّية.")
        return plan

    def heat(self, trip_id, lines, entry_date, workshop, reference, gain_explanation, notes, request_key, *, _expected_actor=None):
        """HEAT LOGO custody of an OPEN trip: the declared processed grams leave and the actual resulting grams return under the same
        karat and LOGO designation. Weights only; permanent difference; no cash, Scrap, supplier, inventory or lot effect."""
        session_actor = self._actor(_expected_actor)
        if str(trip_id or "").strip() == "":
            raise TripError("TRIP_NOT_FOUND", "اختر العهدة.")
        trip_number = _trip_id(trip_id)
        plan = self._heat_lines(lines)
        heated_on = _entry_date(entry_date)
        workshop_text = _text(workshop, WORKSHOP_MAX, "اسم الورشة أو المسؤول", required=True)
        reference_text, notes_text = _text(reference, ASSAY_MAX, "مرجع الحمّية"), _text(notes, NOTES_MAX, "الملاحظات")
        gain_text = _text(gain_explanation, NOTES_MAX, "سبب زيادة الوزن")
        key = _key(request_key)
        if any(after > before for before, after in plan.values()) and not gain_text:
            raise TripError("GAIN_EXPLANATION_REQUIRED", "الوزن بعد الحمّية أكبر من الوزن قبلها في أحد السطور: اكتب سبب زيادة الوزن.")
        digest = hashlib.sha256(json.dumps(
            [HEAT, trip_number, sorted((k, str(b), str(a)) for k, (b, a) in plan.items()), heated_on.isoformat(), workshop_text,
             reference_text, gain_text, notes_text], ensure_ascii=False).encode()).hexdigest()
        try:
            return self._post_heat(session_actor, trip_number, plan, heated_on, workshop_text, reference_text, gain_text, notes_text, key, digest)
        except IntegrityError as exc:
            if not _is_request_key_violation(exc):
                raise
            return self._replay(session_actor, key, digest, exc)

    def _post_heat(self, session_actor, trip_id, plan, heated_on, workshop, reference, gain, notes, key, digest):
        with self._transaction() as connection:
            actor = self._authorization.require_permission_for_write(connection, session_actor, PERMISSION)
            repository = self._repository(connection)
            trip, replay = self._open_trip(repository, trip_id, key, digest, actor)          # the trip lock is the only lock
            if replay is not None:
                return replay
            karats = {}
            for karat_id in sorted(plan):
                karat = repository.karat(karat_id)
                if karat is None or not karat["is_active"]:
                    raise TripError("KARAT_NOT_FOUND", "العيار غير موجود أو غير نشط.")
                karats[karat_id] = karat
            custody = self._custody(repository, trip_id, current=True)                         # read under the trip lock
            by_key = {(g["karat_id"], g["designation"]): g["outstanding"] for g in custody["gold"]}
            for karat_id in sorted(plan):
                available = by_key.get((karat_id, LOGO_DESIGNATION), ZERO_WEIGHT)
                if plan[karat_id][0] > available:
                    others = [f"{DESIGNATION_TEXT[d]} {by_key[(karat_id, d)]:,.3f}" for d in (MELT_DESIGNATION, UNCLASSIFIED)
                              if by_key.get((karat_id, d), ZERO_WEIGHT) > 0]
                    raise TripError("HEAT_EXCEEDS_CUSTODY",
                                    f"المتاح للحمّية في العهدة من لوجو عيار {karats[karat_id]['karat_value']} هو {available:,.3f} جرام فقط."
                                    + (f" (ذهب هذا العيار الآخر لا يُحمَّى: {'، '.join(others)} جرام)." if others else ""))
            total_in = sum((b for b, _ in plan.values()), ZERO_WEIGHT)
            total_out = sum((a for _, a in plan.values()), ZERO_WEIGHT)
            now = repository.db_now()
            entry_no = repository.next_entry_no(trip_id)
            entry_id = repository.insert_entry(trip_id=trip_id, entry_no=entry_no, kind=HEAT, entry_date=heated_on, notes=notes,
                                               key=key, digest=digest, actor=actor, now=now)
            code = entry_code(trip_id, entry_no)
            repository.insert_heat(entry_id=entry_id, workshop=workshop, reference=reference, gain_explanation=gain,
                                   total_input=total_in, total_output=total_out)
            for karat_id, (before, after) in sorted(plan.items()):
                repository.insert_heat_line(entry_id=entry_id, karat_id=karat_id, input_weight=before, output_weight=after)
            repository.audit(actor=actor, event_type="MARKET_TRIP_HEAT", entity_id=entry_id, reference=code,
                             description=f"{KIND_TEXT[HEAT]} {code} — {trip_code(trip_id)}",
                             metadata={"trip_id": trip_id, "kind": HEAT, "date": heated_on.isoformat(), "workshop": workshop,
                                       "lines": {str(karats[k]["karat_value"]): {"input": str(b), "output": str(a), "difference": str(a - b)}
                                                 for k, (b, a) in plan.items()},
                                       "total_difference": str(total_out - total_in), "employee_id": actor.employee_id}, now=now)
            if not self._session.is_current(session_actor):
                raise PermissionError("انتهت جلسة الدخول.")
        return {"entry_id": str(entry_id), "trip_id": str(trip_id), "trip_code": trip_code(trip_id), "code": code, "kind": HEAT,
                "created": False, "duplicate": False, "difference": total_out - total_in}

    # ------------------------------------------------------------------ supplier making payment from custody (043)
    def pay_supplier(self, trip_id, supplier_id, amount, payment_date, reference, notes, request_key, *, _expected_actor=None):
        """PAY a supplier's making (EGP) with cash that is already in an OPEN custody (دفع مصنعيات من العهدة). One transaction posts the
        SUPPLIER_PAY entry, the SM document (method CUSTODY), its negative making movement and MAKING leg, the entry-payment link and the
        audit rows. No Main Safe / bank movement and no MONEY leg: Main Safe moved when the cash left it. Capped at the outstanding
        custody cash; an inactive supplier and a crossing into supplier credit are allowed; supplier gold is never touched."""
        session_actor = self._actor(_expected_actor)
        if SUPPLIER_PAY_PERMISSION not in session_actor.permissions:
            raise PermissionError("دفع مصنعيات الموردين غير متاح لك.")
        if str(trip_id or "").strip() == "":
            raise TripError("TRIP_NOT_FOUND", "اختر العهدة.")
        trip_number = _trip_id(trip_id)
        try:
            supplier_number = int(str(supplier_id).strip())
        except (TypeError, ValueError):
            raise TripError("SUPPLIER_NOT_FOUND", "اختر المورد.") from None
        if supplier_number <= 0:
            raise TripError("SUPPLIER_NOT_FOUND", "اختر المورد.")
        value = parse_cash(amount)
        if value == 0:
            raise TripError("AMOUNT_REQUIRED", "أدخل مبلغ الدفعة بقيمة موجبة.")
        paid_on = _entry_date(payment_date)
        reference_text, notes_text = _text(reference, PAYMENT_REFERENCE_MAX, "المرجع"), _text(notes, NOTES_MAX, "الملاحظات")
        key = _key(request_key)
        digest = hashlib.sha256(json.dumps(
            [SUPPLIER_PAY, trip_number, supplier_number, str(value), paid_on.isoformat(), reference_text, notes_text],
            ensure_ascii=False).encode()).hexdigest()
        try:
            return self._post_supplier_pay(session_actor, trip_number, supplier_number, value, paid_on, reference_text, notes_text, key, digest)
        except IntegrityError as exc:
            if getattr(exc, "errno", None) != 1062 or not ("uq_smp_request" in str(exc) or "uq_mte_request" in str(exc)):
                raise
            with self._transaction() as connection:                       # a twin committed first: decide from a FRESH read
                actor = self._authorization.require_permission_for_write(connection, session_actor, PERMISSION)
                replay = self._supplier_pay_prior(self._repository(connection), self._supplier_payments(connection), key, digest, actor)
                if replay is None:
                    raise
                return replay

    @staticmethod
    def _supplier_pay_prior(repository, suppliers, key, digest, actor):
        """The replay of an already-recorded request (None when the key is unused). BOTH the SM header and the custody entry carry the
        request key (each table is unique on it), so any other flow reusing the key meets it in its own table. A replay needs the
        entry AND the payment to exist, linked to each other, with the SAME complete payload (trip and supplier included) by the SAME
        user; anything else is a conflict and writes nothing."""
        entry, payment = repository.by_request(key), suppliers.by_request(key)
        if entry is None and payment is None:
            return None
        link = None if payment is None else repository.payment_entry(payment["id"])
        if (entry is None or payment is None or link is None or int(link["entry_id"]) != int(entry["id"]) or entry["request_hash"] != digest
                or int(entry["performed_by_user_id"]) != int(actor.user_id)):
            raise TripError("REQUEST_CONFLICT", "طلب العملية مستخدم لعملية مختلفة.")
        return {"entry_id": str(link["entry_id"]), "trip_id": str(link["trip_id"]), "trip_code": trip_code(link["trip_id"]),
                "code": entry_code(link["trip_id"], link["entry_no"]), "kind": SUPPLIER_PAY, "created": False, "duplicate": True,
                "payment_id": str(payment["id"]), "payment_code": payment_code(payment["id"]), "supplier_id": str(payment["supplier_id"]),
                "amount": str(payment["amount"]), "balance_before": str(payment["balance_before"]),
                "balance_after": str(payment["balance_after"])}

    def _post_supplier_pay(self, session_actor, trip_id, supplier_id, value, paid_on, reference, notes, key, digest):
        with self._transaction() as connection:
            self._authorization.require_permission_for_write(connection, session_actor, PERMISSION)
            actor = self._authorization.require_permission_for_write(connection, session_actor, SUPPLIER_PAY_PERMISSION)
            repository, suppliers = self._repository(connection), self._supplier_payments(connection)
            trip = repository.trip(trip_id, lock=True)                           # lock order: trip -> supplier
            if trip is None:
                raise TripError("TRIP_NOT_FOUND", "العهدة غير موجودة.")
            replay = self._supplier_pay_prior(repository, suppliers, key, digest, actor)
            if replay is not None:
                return replay
            if trip["status"] != OPEN:
                raise TripError("TRIP_CLOSED", "العهدة مغلقة: أعد فتحها أولاً لدفع مصنعيات من العهدة.")
            supplier = suppliers.get_supplier(supplier_id, lock=True)            # an INACTIVE supplier may still be paid
            if supplier is None:
                raise TripError("SUPPLIER_NOT_FOUND", "المورد غير موجود.")
            if not rules.has_supplier_role(supplier):                        # making belongs to a SUPPLIER, never to an intermediary-only party
                raise TripError("SUPPLIER_ROLE_REQUIRED", "هذا الطرف مسوقجي فقط وليس مورِّدًا: لا يُدفع له مصنعيات.")
            outstanding = self._custody(repository, trip_id, current=True)["cash"]["outstanding"]   # read under the trip lock
            if value > outstanding:
                raise TripError("PAYMENT_EXCEEDS_CUSTODY_CASH", f"النقدية المتبقية في العهدة {outstanding:,.2f} ج فقط: لا يمكن دفع {value:,.2f} ج.")
            making_type = suppliers.making_type_id(PAYMENT_MOVEMENT)
            if making_type is None:
                raise RuntimeError("supplier making movement type PAYMENT is missing (migration 036)")
            before = rules.money_amount(suppliers.making_balance_current(supplier_id))
            after = rules.making_after_payment(before, value)
            now = repository.db_now()
            payment_id = suppliers.insert_header(supplier_id=supplier_id, method=CUSTODY_METHOD, cash_account_id=None, bank_account_id=None,
                                                 amount=value, payment_date=paid_on, reference=reference, notes=notes, before=before,
                                                 after=after, key=key, digest=digest, actor=actor, now=now)
            pcode = payment_code(payment_id)
            entry_no = repository.next_entry_no(trip_id)
            entry_id = repository.insert_entry(trip_id=trip_id, entry_no=entry_no, kind=SUPPLIER_PAY, entry_date=paid_on, notes=notes,
                                               key=key, digest=digest, actor=actor, now=now)
            code = entry_code(trip_id, entry_no)
            label = f"دفعة مصنعيات للمورد {pcode} من عهدة السوق {code}"
            making_id = suppliers.insert_making_movement(supplier_id=supplier_id, type_id=making_type, amount_delta=-value,
                                                         source_type=SMP_SOURCE, source_id=payment_id, reference=pcode, description=label,
                                                         now=now, user_id=actor.user_id)
            suppliers.insert_leg(payment_id=payment_id, kind="MAKING", making_movement_id=making_id)
            repository.insert_supplier_payment_link(entry_id=entry_id, payment_id=payment_id, supplier_id=supplier_id, amount=value)
            suppliers.audit(actor=actor, event_type="SUPPLIER_MAKING_PAYMENT", entity_id=payment_id, entity_type=SMP_SOURCE, reference=pcode,
                            description=f"{label} {value:,.2f} ج",
                            metadata={"supplier_id": supplier_id, "method": CUSTODY_METHOD, "custody_entry": code, "amount": str(value),
                                      "before": str(before), "after": str(after), "payment_date": paid_on.isoformat(),
                                      "reference": reference, "employee_id": actor.employee_id}, now=now)
            repository.audit(actor=actor, event_type="MARKET_TRIP_SUPPLIER_PAY", entity_id=entry_id, reference=code,
                             description=f"{KIND_TEXT[SUPPLIER_PAY]} {code} — {trip_code(trip_id)} — {pcode}",
                             metadata={"trip_id": trip_id, "kind": SUPPLIER_PAY, "supplier_id": supplier_id, "payment": pcode,
                                       "amount": str(value), "date": paid_on.isoformat(), "employee_id": actor.employee_id}, now=now)
            if not self._session.is_current(session_actor):
                raise PermissionError("انتهت جلسة الدخول.")
        return {"entry_id": str(entry_id), "trip_id": str(trip_id), "trip_code": trip_code(trip_id), "code": code, "kind": SUPPLIER_PAY,
                "created": False, "duplicate": False, "payment_id": str(payment_id), "payment_code": pcode, "supplier_id": str(supplier_id),
                "amount": str(value), "balance_before": str(before), "balance_after": str(after)}

    # ------------------------------------------------------------------ bar fee received in cash (080)
    def bar_fee(self, trip_id, party_id, amount, fee_date, reference, notes, request_key, *, _expected_actor=None):
        """RECORD the cash fee (أجر السبيكة) a supplier / marketer pays the owner when a bar is handed to them. The cash enters the
        custody cash (no Main Safe / bank movement); for a SUPPLIER party the fee is also deducted from its making account through a
        negative BAR_FEE making movement (needs SUPPLIER_MAKING_PAY); a marketer-only party gets no making movement."""
        session_actor = self._actor(_expected_actor)
        if str(trip_id or "").strip() == "":
            raise TripError("TRIP_NOT_FOUND", "اختر العهدة.")
        trip_number = _trip_id(trip_id)
        try:
            party_number = int(str(party_id).strip())
        except (TypeError, ValueError):
            raise TripError("SUPPLIER_NOT_FOUND", "اختر المورد أو المسوقجي.") from None
        if party_number <= 0:
            raise TripError("SUPPLIER_NOT_FOUND", "اختر المورد أو المسوقجي.")
        value = parse_cash(amount)
        if value == 0:
            raise TripError("AMOUNT_REQUIRED", "أدخل أجر السبيكة بقيمة موجبة.")
        dated = _entry_date(fee_date)
        reference_text, notes_text = _text(reference, PAYMENT_REFERENCE_MAX, "المرجع"), _text(notes, NOTES_MAX, "الملاحظات")
        key = _key(request_key)
        digest = hashlib.sha256(json.dumps([ST_BAR_FEE, trip_number, party_number, str(value), dated.isoformat(), reference_text, notes_text],
                                           ensure_ascii=False).encode()).hexdigest()
        try:
            return self._post_bar_fee(session_actor, trip_number, party_number, value, dated, reference_text, notes_text, key, digest)
        except IntegrityError as exc:
            if getattr(exc, "errno", None) != 1062 or "uq_mte_request" not in str(exc):
                raise
            with self._transaction() as connection:
                actor = self._authorization.require_permission_for_write(connection, session_actor, PERMISSION)
                replay = self._bar_fee_prior(self._repository(connection), key, digest, actor)
                if replay is None:
                    raise
                return replay

    @staticmethod
    def _bar_fee_prior(repository, key, digest, actor):
        entry = repository.by_request(key)
        if entry is None:
            return None
        if entry["entry_kind"] != ST_BAR_FEE or entry["request_hash"] != digest or int(entry["performed_by_user_id"]) != int(actor.user_id):
            raise TripError("REQUEST_CONFLICT", "طلب العملية مستخدم لعملية مختلفة.")
        fee = repository.entry_bar_fee(entry["id"]) or {}
        return {"entry_id": str(entry["id"]), "trip_id": str(entry["trip_id"]), "trip_code": trip_code(entry["trip_id"]),
                "code": entry_code(entry["trip_id"], entry["entry_no"]), "kind": ST_BAR_FEE, "created": False, "duplicate": True,
                "party_id": str(fee.get("party_id", "")), "amount": str(fee.get("amount", ""))}

    def _post_bar_fee(self, session_actor, trip_id, party_id, value, dated, reference, notes, key, digest):
        with self._transaction() as connection:
            actor = self._authorization.require_permission_for_write(connection, session_actor, PERMISSION)
            repository, suppliers = self._repository(connection), self._supplier_payments(connection)
            if not repository.has_bar_fee_schema():
                raise TripError("SCHEMA_OUTDATED", "أجر السبيكة يحتاج تحديث قاعدة البيانات (080).")
            trip = repository.trip(trip_id, lock=True)                           # lock order: trip -> party
            if trip is None:
                raise TripError("TRIP_NOT_FOUND", "العهدة غير موجودة.")
            replay = self._bar_fee_prior(repository, key, digest, actor)
            if replay is not None:
                return replay
            if trip["status"] != OPEN:
                raise TripError("TRIP_CLOSED", "العهدة مغلقة: أعد فتحها أولاً لتسجيل أجر السبيكة.")
            party = suppliers.get_supplier(party_id, lock=True)
            if party is None:
                raise TripError("SUPPLIER_NOT_FOUND", "المورد أو المسوقجي غير موجود.")
            is_supplier = rules.has_supplier_role(party)
            if not is_supplier and not party.get("is_intermediary"):
                raise TripError("PARTY_ROLE", "الطرف المختار ليس موردًا ولا مسوقجيًا.")
            if is_supplier:
                actor = self._authorization.require_permission_for_write(connection, session_actor, SUPPLIER_PAY_PERMISSION)
            now = repository.db_now()
            entry_no = repository.next_entry_no(trip_id)
            entry_id = repository.insert_entry(trip_id=trip_id, entry_no=entry_no, kind=ST_BAR_FEE, entry_date=dated, notes=notes,
                                               key=key, digest=digest, actor=actor, now=now)
            code = entry_code(trip_id, entry_no)
            label = f"{KIND_TEXT[ST_BAR_FEE]} {code} — {trip_code(trip_id)}: {value:,.2f} ج من {party['name']}"
            making_id = None
            if is_supplier:
                making_type = suppliers.making_type_id(BAR_FEE_MOVEMENT)
                if making_type is None:
                    raise RuntimeError("supplier making movement type BAR_FEE is missing (migration 080)")
                making_id = suppliers.insert_making_movement(supplier_id=party_id, type_id=making_type, amount_delta=-value,
                                                             source_type=BAR_FEE_MOVEMENT, source_id=entry_id, reference=code,
                                                             description=label, now=now, user_id=actor.user_id)
            repository.insert_bar_fee(entry_id=entry_id, party_id=party_id, party_kind="SUPPLIER" if is_supplier else "INTERMEDIARY",
                                      amount=value, making_movement_id=making_id, reference=reference)
            repository.audit(actor=actor, event_type="MARKET_TRIP_BAR_FEE", entity_id=entry_id, reference=code, description=label,
                             metadata={"trip_id": trip_id, "kind": ST_BAR_FEE, "party_id": party_id, "amount": str(value),
                                       "date": dated.isoformat(), "employee_id": actor.employee_id}, now=now)
            if not self._session.is_current(session_actor):
                raise PermissionError("انتهت جلسة الدخول.")
        return {"entry_id": str(entry_id), "trip_id": str(trip_id), "trip_code": trip_code(trip_id), "code": code, "kind": ST_BAR_FEE,
                "created": False, "duplicate": False, "party_id": str(party_id), "amount": str(value)}

    # ------------------------------------------------------------------ supplier GOLD settlement from custody (046)
    def settle_supplier_gold(self, trip_id, supplier_id, karat_id, designation, weight, settlement_date, reference, notes, request_key,
                             *, _expected_actor=None):
        """DELIVER LOGO gold that is already in an OPEN custody to a supplier against its gold account (تسوية ذهب من العهدة).
        One transaction posts the GOLD_SETTLE custody entry, the immutable SG document (source CUSTODY_GOLD), the supplier's negative gold
        movement in ITS dedicated karat, the GOLD leg, the entry-settlement link and both audit rows. The custody grams are the ones that
        left the shop at issue: no Scrap movement is posted again, and no cash, making, bank or lot movement exists. Capped at the
        effective outstanding grams of the (karat, LOGO) source; delivered physical grams are authoritative and the credit is computed by
        the service (delivered grams x delivered karat / supplier karat). An inactive supplier and a crossing into supplier credit are allowed."""
        session_actor = self._actor(_expected_actor)
        if GOLD_SETTLE_PERMISSION not in session_actor.permissions:
            raise PermissionError("تسوية ذهب الموردين غير متاحة لك.")
        if str(trip_id or "").strip() == "":
            raise TripError("TRIP_NOT_FOUND", "اختر العهدة.")
        trip_number = _trip_id(trip_id)
        try:
            supplier_number = int(str(supplier_id).strip())
        except (TypeError, ValueError):
            raise TripError("SUPPLIER_NOT_FOUND", "اختر المورد.") from None
        if supplier_number <= 0:
            raise TripError("SUPPLIER_NOT_FOUND", "اختر المورد.")
        try:
            karat_number = int(str(karat_id).strip())
        except (TypeError, ValueError):
            raise TripError("KARAT_REQUIRED", "اختر عيار الذهب المُسلَّم.") from None
        if karat_number <= 0:
            raise TripError("KARAT_REQUIRED", "اختر عيار الذهب المُسلَّم.")
        source = parse_designation(designation, GOLD_SETTLE)
        if source not in SETTLEABLE_DESIGNATIONS:
            raise TripError("DESIGNATION_NOT_SETTLEABLE",
                            f"في هذه المرحلة تُسوّى للمورد من العهدة ذهب «لوجو» أو الذهب الذي خرج بدون وجهة؛ ذهب ({DESIGNATION_TEXT[source]}) غير متاح للتسوية.")
        grams = parse_weight(weight)
        if grams == 0:
            raise TripError("WEIGHT_REQUIRED", "أدخل وزن الذهب المُسلَّم الفعلي بقيمة موجبة.")
        settled_on = _entry_date(settlement_date)
        reference_text, notes_text = _text(reference, PAYMENT_REFERENCE_MAX, "المرجع"), _text(notes, NOTES_MAX, "الملاحظات")
        key = _key(request_key)
        digest = hashlib.sha256(json.dumps(
            [GOLD_SETTLE, trip_number, supplier_number, karat_number, source, str(grams), settled_on.isoformat(), reference_text, notes_text],
            ensure_ascii=False).encode()).hexdigest()
        try:
            return self._post_gold_settle(session_actor, trip_number, supplier_number, karat_number, source, grams, settled_on, reference_text,
                                          notes_text, key, digest)
        except IntegrityError as exc:
            if getattr(exc, "errno", None) != 1062 or not ("uq_sgs_request" in str(exc) or "uq_mte_request" in str(exc)):
                raise
            with self._transaction() as connection:                       # a twin committed first: decide from a FRESH read
                actor = self._authorization.require_permission_for_write(connection, session_actor, PERMISSION)
                replay = self._gold_settle_prior(self._repository(connection), self._gold_settlements(connection), key, digest, actor)
                if replay is None:
                    raise
                return replay

    @staticmethod
    def _gold_settle_result(entry, link, duplicate):
        return {"entry_id": str(entry["id"]), "trip_id": str(entry["trip_id"]), "trip_code": trip_code(entry["trip_id"]),
                "code": entry_code(entry["trip_id"], entry["entry_no"]), "kind": GOLD_SETTLE, "created": False, "duplicate": duplicate,
                "settlement_id": str(link["settlement_id"]), "settlement_code": settlement_code(link["settlement_id"]),
                "supplier_id": str(link["supplier_id"]), "designation": link["designation"], "delivered_karat": int(link["karat_value"]),
                "target_karat": int(link["target_karat_value"]), "delivered_weight": str(link["weight_grams"]),
                "credited_weight": str(link["credited_weight_grams"]), "analytical": str(link["credited_analytical_grams"]),
                "rounding_difference": str(link["rounding_difference_grams"])}

    @classmethod
    def _gold_settle_prior(cls, repository, settlements, key, digest, actor):
        """The replay of an already-recorded request (None when the key is unused). BOTH the SG header and the custody entry carry the
        request key (each table is unique on it), so any other flow reusing the key meets it in its own table. A replay needs the entry
        AND the document to exist, linked to each other, with the SAME complete payload by the SAME user; anything else is a conflict
        and writes nothing."""
        entry, header = repository.by_request(key), settlements.by_request(key)
        if entry is None and header is None:
            return None
        link = None if entry is None else repository.entry_gold_settlement(entry["id"])
        if (entry is None or header is None or link is None or int(link["settlement_id"]) != int(header["id"])
                or entry["request_hash"] != digest or header["request_hash"] != digest
                or int(entry["performed_by_user_id"]) != int(actor.user_id)):
            raise TripError("REQUEST_CONFLICT", "طلب العملية مستخدم لعملية مختلفة.")
        return cls._gold_settle_result(entry, link, True)

    def _post_gold_settle(self, session_actor, trip_id, supplier_id, karat_id, designation, grams, settled_on, reference, notes, key, digest):
        with self._transaction() as connection:
            self._authorization.require_permission_for_write(connection, session_actor, PERMISSION)
            actor = self._authorization.require_permission_for_write(connection, session_actor, GOLD_SETTLE_PERMISSION)
            repository, settlements = self._repository(connection), self._gold_settlements(connection)
            trip = repository.trip(trip_id, lock=True)                           # lock order: trip -> supplier
            if trip is None:
                raise TripError("TRIP_NOT_FOUND", "العهدة غير موجودة.")
            replay = self._gold_settle_prior(repository, settlements, key, digest, actor)
            if replay is not None:
                return replay
            book_rules.require_legacy_trip(trip)                               # 056: a BOOK trip has no legacy gold path
            if trip["status"] != OPEN:
                raise TripError("TRIP_CLOSED", "العهدة مغلقة: أعد فتحها أولاً لتسوية ذهب من العهدة.")
            supplier = settlements.get_supplier(supplier_id, lock=True)         # an INACTIVE supplier may still receive it
            if supplier is None:
                raise TripError("SUPPLIER_NOT_FOUND", "المورد غير موجود.")
            if not rules.has_supplier_role(supplier):                        # the gold account belongs to a SUPPLIER, never to a مسوقجي-only party
                raise TripError("SUPPLIER_ROLE_REQUIRED", "هذا الطرف مسوقجي فقط وليس مورِّدًا: لا حساب ذهب له لتسوية الذهب.")
            if supplier["karat_id"] is None:
                raise TripError("SUPPLIER_KARAT_MISSING", "عيار هذا المورد غير محدد. حدّد عياره من تعديل المورد قبل تسوية الذهب له.")
            delivered = settlements.get_karat(karat_id)
            if delivered is None or not delivered["is_active"]:
                raise TripError("KARAT_NOT_FOUND", "العيار غير موجود أو غير نشط.")
            target = settlements.get_karat(supplier["karat_id"])
            custody = self._custody(repository, trip_id, current=True)            # read under the trip lock: the effective outstanding
            line = next((g for g in custody["gold"] if g["karat_id"] == karat_id and g["designation"] == designation), None)
            outstanding = ZERO_WEIGHT if line is None else line["outstanding"]
            if grams > outstanding:
                raise TripError("SETTLEMENT_EXCEEDS_CUSTODY",
                                f"المتبقي في العهدة من عيار {int(delivered['karat_value'])} ({DESIGNATION_TEXT[designation]}) هو "
                                f"{outstanding:,.3f} جرام فقط: لا يمكن تسوية {grams:,.3f} جرام.")
            credited, analytical, difference = rules.settlement_credit(grams, delivered["karat_value"], target["karat_value"])
            type_id = settlements.gold_type_id(GOLD_MOVEMENT)
            if type_id is None:
                raise RuntimeError("supplier gold movement type SETTLEMENT is missing (migration 045)")
            now = repository.db_now()
            settlement_id = settlements.insert_header(
                supplier_id=supplier_id, delivered_karat=delivered, target_karat=target, delivered_weight=grams, credited=credited,
                analytical=analytical, difference=difference, settlement_date=settled_on, reference=reference, notes=notes, key=key,
                digest=digest, actor=actor, now=now, source_kind=CUSTODY_SOURCE_KIND)
            sg_code = settlement_code(settlement_id)
            entry_no = repository.next_entry_no(trip_id)
            entry_id = repository.insert_entry(trip_id=trip_id, entry_no=entry_no, kind=GOLD_SETTLE, entry_date=settled_on, notes=notes,
                                               key=key, digest=digest, actor=actor, now=now)
            code = entry_code(trip_id, entry_no)
            label = (f"تسوية ذهب للمورد {sg_code} من عهدة السوق {code}: سُلِّم {grams:,.3f} جرام عيار {int(delivered['karat_value'])} "
                     f"({DESIGNATION_TEXT[designation]}) ← احتُسب {credited:,.3f} جرام عيار {int(target['karat_value'])}")
            gold_id = settlements.insert_gold_movement(
                supplier_id=supplier_id, type_id=type_id, karat_id=target["id"], weight_delta=-credited,
                eq21_delta=-rules.equivalent_21(credited, target["karat_value"]), source_type="SUPPLIER_GOLD_SETTLEMENT",
                source_id=settlement_id, reference=sg_code, description=label, now=now, user_id=actor.user_id)
            settlements.insert_leg(settlement_id=settlement_id, kind="GOLD", source_kind=CUSTODY_SOURCE_KIND, supplier_gold_movement_id=gold_id)
            repository.insert_gold_settlement_link(entry_id=entry_id, settlement_id=settlement_id, supplier_id=supplier_id, karat_id=karat_id,
                                                   designation=designation, weight=grams)
            settlements.audit(actor=actor, event_type="SUPPLIER_GOLD_SETTLEMENT", entity_id=settlement_id, entity_type="SUPPLIER_GOLD_SETTLEMENT",
                              reference=sg_code, description=label,
                              metadata={"supplier_id": supplier_id, "source": CUSTODY_SOURCE_KIND, "custody_entry": code,
                                        "delivered_karat": int(delivered["karat_value"]), "designation": designation,
                                        "delivered_weight": str(grams), "target_karat": int(target["karat_value"]), "credited": str(credited),
                                        "analytical": str(analytical), "rounding_difference": str(difference),
                                        "settlement_date": settled_on.isoformat(), "reference": reference, "employee_id": actor.employee_id}, now=now)
            repository.audit(actor=actor, event_type="MARKET_TRIP_GOLD_SETTLE", entity_id=entry_id, reference=code,
                             description=f"{KIND_TEXT[GOLD_SETTLE]} {code} — {trip_code(trip_id)} — {sg_code}",
                             metadata={"trip_id": trip_id, "kind": GOLD_SETTLE, "supplier_id": supplier_id, "settlement": sg_code,
                                       "karat": int(delivered["karat_value"]), "designation": designation, "delivered_weight": str(grams),
                                       "credited": str(credited), "date": settled_on.isoformat(), "employee_id": actor.employee_id}, now=now)
            if not self._session.is_current(session_actor):
                raise PermissionError("انتهت جلسة الدخول.")
        return {"entry_id": str(entry_id), "trip_id": str(trip_id), "trip_code": trip_code(trip_id), "code": code, "kind": GOLD_SETTLE,
                "created": False, "duplicate": False, "settlement_id": str(settlement_id), "settlement_code": sg_code,
                "supplier_id": str(supplier_id), "designation": designation, "delivered_karat": int(delivered["karat_value"]),
                "target_karat": int(target["karat_value"]), "delivered_weight": str(grams), "credited_weight": str(credited),
                "analytical": str(analytical), "rounding_difference": str(difference)}

    # ------------------------------------------------------------------ whole assayed lot supplier settlement (047)
    @staticmethod
    def lot_settlement_preview(lot, supplier):
        """Shared preview and posting calculation. The caller supplies stored facts, never rounded pure_grams."""
        rules.require_supplier_role(supplier)
        if supplier.get("karat_value") is None:
            raise TripError("SUPPLIER_KARAT_MISSING", "عيار هذا المورد غير محدد: حدّده من تعديل المورد أولًا.")
        if lot["outstanding"] != lot["weight"]:
            raise TripError("LOT_PARTIAL_NOT_ALLOWED", "رصيد الشحنة في العهدة جزئي: اعكس الرجوع الجزئي أولًا؛ تُسوّى الشحنة كاملة فقط.")
        return rules.lot_settlement_credit(lot["weight"], lot["fineness"], supplier["karat_value"])

    def settle_supplier_lot(self, trip_id, supplier_id, lot_id, settlement_date, reference, notes, request_key,
                            *, weight=None, _expected_actor=None):
        actor = self._actor(_expected_actor)
        if GOLD_SETTLE_PERMISSION not in actor.permissions:
            raise PermissionError("تسوية ذهب الموردين غير متاحة لك.")
        trip_id, supplier_id, lot_id = _trip_id(trip_id), _trip_id(supplier_id), _trip_id(lot_id)
        supplied = None if weight is None else parse_weight(weight)
        settled_on = _entry_date(settlement_date)
        reference, notes, key = _text(reference, PAYMENT_REFERENCE_MAX, "المرجع"), _text(notes, NOTES_MAX, "الملاحظات"), _key(request_key)
        digest = hashlib.sha256(json.dumps([LOT_SETTLE, trip_id, supplier_id, lot_id, settled_on.isoformat(), reference, notes,
                                           None if supplied is None else str(supplied)], ensure_ascii=False).encode()).hexdigest()
        try:
            return self._post_lot_settlement(actor, trip_id, supplier_id, lot_id, supplied, settled_on, reference, notes, key, digest)
        except IntegrityError as exc:
            if getattr(exc, "errno", None) != 1062 or not any(k in str(exc) for k in ("uq_sgs_request", "uq_mte_request")):
                raise
            with self._transaction() as connection:
                self._authorization.require_permission_for_write(connection, actor, PERMISSION)
                checked = self._authorization.require_permission_for_write(connection, actor, GOLD_SETTLE_PERMISSION)
                result = self._lot_settle_prior(self._repository(connection), self._gold_settlements(connection), key, digest, checked)
                if result is None:
                    raise
                return result

    @staticmethod
    def _lot_settle_result(entry, link, duplicate):
        return {"entry_id": str(entry["id"]), "trip_id": str(entry["trip_id"]), "trip_code": trip_code(entry["trip_id"]),
                "code": entry_code(entry["trip_id"], entry["entry_no"]), "kind": LOT_SETTLE, "created": False, "duplicate": duplicate,
                "settlement_id": str(link["settlement_id"]), "settlement_code": settlement_code(link["settlement_id"]),
                "supplier_id": str(link["supplier_id"]), "lot_id": str(link["lot_id"]), "lot_kind": link["lot_kind"],
                "origin": entry_code(link["origin_trip_id"], link["origin_entry_no"]), "lot_no": int(link["lot_no"]),
                "fineness": str(link["fineness_permille"]), "delivered_weight": str(link["weight_grams"]),
                "target_karat": int(link["target_karat_value"]), "credited_weight": str(link["credited_weight_grams"]),
                "analytical": str(link["credited_analytical_grams"]), "rounding_difference": str(link["rounding_difference_grams"])}

    @classmethod
    def _lot_settle_prior(cls, repository, settlements, key, digest, actor):
        entry, header = repository.by_request(key), settlements.by_request(key)
        if entry is None and header is None:
            return None
        link = None if entry is None else repository.entry_lot_settlement(entry["id"])
        if (entry is None or header is None or link is None or header["source_kind"] != LOT_SOURCE_KIND
                or entry["entry_kind"] != LOT_SETTLE or int(link["settlement_id"]) != int(header["id"])
                or entry["request_hash"] != digest or header["request_hash"] != digest
                or int(entry["performed_by_user_id"]) != int(actor.user_id)):
            raise TripError("REQUEST_CONFLICT", "طلب العملية مستخدم لعملية مختلفة.")
        return cls._lot_settle_result(entry, link, True)

    def _post_lot_settlement(self, session_actor, trip_id, supplier_id, lot_id, supplied, settled_on, reference, notes, key, digest):
        with self._transaction() as connection:
            self._authorization.require_permission_for_write(connection, session_actor, PERMISSION)
            actor = self._authorization.require_permission_for_write(connection, session_actor, GOLD_SETTLE_PERMISSION)
            repository, settlements = self._repository(connection), self._gold_settlements(connection)
            trip = repository.trip(trip_id, lock=True)                       # trip -> lot -> supplier, also used by RV
            if trip is None:
                raise TripError("TRIP_NOT_FOUND", "العهدة غير موجودة.")
            replay = self._lot_settle_prior(repository, settlements, key, digest, actor)
            if replay is not None:
                return replay                                             # even after reversal: no new weight derivation
            book_rules.require_legacy_trip(trip)                               # 056: a BOOK trip has no legacy gold path
            if trip["status"] != OPEN:
                raise TripError("TRIP_CLOSED", "العهدة مغلقة: أعد فتحها أولًا.")
            locked = repository.lock_lots([lot_id])
            if not locked:
                raise TripError("LOT_NOT_FOUND", "الشحنة غير موجودة.")
            lot = locked[0]
            full = Decimal(str(lot["weight_grams"]))
            current = next(iter(repository.custody_lots(trip_id, lot_id=lot_id)), None)
            if current is None or Decimal(str(current["outstanding_grams"])) == 0:
                raise TripError("LOT_NOT_IN_CUSTODY", "الشحنة ليست في هذه العهدة أو تمت تسويتها أو عُكس سبكها.")
            outstanding = Decimal(str(current["outstanding_grams"]))
            if outstanding != full:
                raise TripError("LOT_PARTIAL_NOT_ALLOWED", "رصيد الشحنة في العهدة جزئي: اعكس الرجوع الجزئي أولًا؛ تُسوّى الشحنة كاملة فقط.")
            if supplied is not None and supplied != full:
                raise TripError("LOT_PARTIAL_NOT_ALLOWED", "تُسوّى الشحنة كاملة فقط؛ الكمية المرسلة لا تطابق وزنها الكامل.")
            supplier = settlements.get_supplier(supplier_id, lock=True)     # inactive and crossing zero are allowed
            if supplier is None:
                raise TripError("SUPPLIER_NOT_FOUND", "المورد غير موجود.")
            rules.require_supplier_role(supplier)
            if supplier["karat_id"] is None:
                raise TripError("SUPPLIER_KARAT_MISSING", "عيار هذا المورد غير محدد: حدّده من تعديل المورد أولًا.")
            target = settlements.get_karat(supplier["karat_id"])
            credited, analytical, difference = self.lot_settlement_preview(
                {"weight": full, "outstanding": outstanding, "fineness": lot["fineness_permille"]},
                {**supplier, "karat_value": target["karat_value"]})
            type_id = settlements.gold_type_id(GOLD_MOVEMENT)
            if type_id is None:
                raise RuntimeError("supplier gold movement type SETTLEMENT is missing")
            now = repository.db_now()
            settlement_id = settlements.insert_lot_header(supplier_id=supplier_id, lot=lot, target_karat=target,
                credited=credited, analytical=analytical, difference=difference, settlement_date=settled_on, reference=reference,
                notes=notes, key=key, digest=digest, actor=actor, now=now)
            sg_code = settlement_code(settlement_id)
            entry_id = repository.insert_entry(trip_id=trip_id, entry_no=repository.next_entry_no(trip_id), kind=LOT_SETTLE,
                entry_date=settled_on, notes=notes, key=key, digest=digest, actor=actor, now=now)
            entry = repository.by_request(key)
            code = entry_code(trip_id, entry["entry_no"])
            label = f"تسوية سبكة من العهدة {code} — {sg_code}: {full:,.3f} جرام، عيار فعلي {lot['fineness_permille']} بالألف"
            movement_id = repository.insert_lot_movement(lot_id=lot_id, entry_id=entry_id, movement_type=LOT_SETTLE_MOVEMENT, weight_delta=-full)
            gold_id = settlements.insert_gold_movement(supplier_id=supplier_id, type_id=type_id, karat_id=target["id"],
                weight_delta=-credited, eq21_delta=-rules.equivalent_21(credited, target["karat_value"]), source_type="SUPPLIER_GOLD_SETTLEMENT",
                source_id=settlement_id, reference=sg_code, description=label, now=now, user_id=actor.user_id)
            settlements.insert_leg(settlement_id=settlement_id, kind="GOLD", source_kind=LOT_SOURCE_KIND, supplier_gold_movement_id=gold_id)
            repository.insert_lot_settlement_link(entry_id=entry_id, settlement_id=settlement_id, supplier_id=supplier_id, lot=lot,
                                                  weight=full, movement_id=movement_id)
            metadata = {"trip_id": trip_id, "lot_id": str(lot_id), "origin_melt_entry_id": str(lot["entry_id"]),
                        "lot_kind": lot["lot_kind"], "source": LOT_SOURCE_KIND, "custody_entry": code, "settlement": sg_code,
                        "weight": str(full), "fineness": str(lot["fineness_permille"]), "credited": str(credited),
                        "analytical": str(analytical), "rounding_difference": str(difference), "supplier_id": supplier_id}
            settlements.audit(actor=actor, event_type="SUPPLIER_GOLD_SETTLEMENT", entity_id=settlement_id,
                entity_type="SUPPLIER_GOLD_SETTLEMENT", reference=sg_code, description=label, metadata=metadata, now=now)
            repository.audit(actor=actor, event_type="MARKET_TRIP_LOT_SETTLE", entity_id=entry_id, reference=code,
                             description=label, metadata=metadata, now=now)
            result = self._lot_settle_result(entry, repository.entry_lot_settlement(entry_id), False)
            if not self._session.is_current(session_actor):
                raise PermissionError("انتهت جلسة الدخول.")
        return result

    # ------------------------------------------------------------------ cash gold purchase (054, شراء ذهب نقدي)
    @staticmethod
    def vendor_name_key(name):
        """The normalized vendor name used to group KPI figures: Arabic letter variants unified, diacritics / tatweel dropped, digits and spaces normalized."""
        text = normalize_digits(str(name or "")).strip().casefold()
        text = text.translate(_VENDOR_MAP)
        return " ".join(text.split())

    @staticmethod
    def parse_buy_lines(lines):
        """The purchased scrap: one row per (karat, designation, actual grams, agreed price). Every line is complete or it is refused; the new
        gold is MELT or LOGO (never unclassified). The total of the document is the exact SUM of the line prices."""
        parsed = []
        for index, line in enumerate(lines or [], 1):
            karat_text, weight_text, price_text = (str(line.get(k, "") or "").strip() for k in ("karat_id", "weight", "price"))
            if not (karat_text or weight_text or price_text):
                continue                                                                  # an untouched row is ignored
            try:
                karat_number = int(karat_text)
            except ValueError:
                raise TripError("KARAT_REQUIRED", f"السطر {index}: اختر العيار.") from None
            if karat_number <= 0:
                raise TripError("KARAT_REQUIRED", f"السطر {index}: اختر العيار.")
            designation = parse_designation(line.get("designation"), ISSUE, allow_blank=True)         # 064: bought gold has no melt / logo decision until the settlement
            grams = parse_weight(weight_text)
            if grams == 0:
                raise TripError("WEIGHT_REQUIRED", f"السطر {index}: أدخل الوزن الفعلي بقيمة موجبة.")
            price = _decimal(price_text, "ثمن السطر", "PRICE_INVALID")
            if price is None or price <= 0 or price >= MAX_MONEY or price != price.quantize(MONEY_STEP):
                raise TripError("PRICE_INVALID", f"السطر {index}: أدخل الثمن المتفق عليه بقيمة موجبة وخانتين عشريتين على الأكثر.")
            parsed.append({"karat_id": karat_number, "designation": designation, "weight": grams, "price": price.quantize(MONEY_STEP)})
        if not parsed:
            raise TripError("LINES_REQUIRED", "أضف سطر ذهب واحدًا على الأقل (العيار والوزن والثمن).")
        return parsed

    @staticmethod
    def buy_reference(prices, parsed, karat_values, total):
        """(reference total, deviation %) of a purchase against the SHOP_BUY reference of every karat bought; (None, None) when any karat has no
        reference (a partial reference would mislead)."""
        reference = ZERO_MONEY
        with localcontext() as context:
            context.prec = 50
            for line in parsed:
                price = prices.get(karat_values.get(line["karat_id"]))
                if price is None:
                    return None, None
                reference += line["weight"] * price
            if reference <= 0:
                return None, None
            deviation = ((total - reference) / reference * Decimal(100)).quantize(MONEY_STEP, rounding=ROUND_HALF_UP)
            return reference.quantize(MONEY_STEP, rounding=ROUND_HALF_UP), deviation

    def _vendor(self, repository, party_id, name):
        """The vendor (MANDATORY): an ACTIVE registered party or a typed name; the name is a snapshot and its normalized key groups the KPI figures."""
        text = str(party_id if party_id is not None else "").strip()
        if text:
            try:
                number = int(text)
            except ValueError:
                raise TripError("VENDOR_NOT_FOUND", "الطرف البائع غير موجود.") from None
            party = repository.party(number, lock=True)
            if party is None:
                raise TripError("VENDOR_NOT_FOUND", "الطرف البائع غير موجود.")
            if not party["is_active"]:
                raise TripError("VENDOR_INACTIVE", "هذا الطرف موقوف: لا يمكن الشراء منه. فعّله أولًا أو اختر بائعًا آخر.")
            label, kind, party_number = str(party["name"]).strip(), BUYER_REGISTERED, number
        else:
            label = _text(name, BUYER_NAME_MAX, "اسم البائع", required=False)
            if not label:
                raise TripError("VENDOR_REQUIRED", "اسم البائع إجباري: اختره من الأطراف المسجلة أو اكتب اسمه.")
            kind, party_number = BUYER_THIRD_PARTY, None
        key = self.vendor_name_key(label)
        if not key:
            raise TripError("VENDOR_REQUIRED", "اسم البائع إجباري: اختره من الأطراف المسجلة أو اكتب اسمه.")
        return kind, party_number, label, key

    def register_vendor(self, name, karat_id, *, _expected_actor=None):
        """Register a typed vendor name as a NEW ACTIVE supplier party right from the purchase dialog. The supplier master needs a dedicated account
        karat, so the owner names it (never guessed). Delegates to SupplierService (its own right, validation, unique name and audit)."""
        self._actor(_expected_actor)
        label = _text(name, BUYER_NAME_MAX, "اسم البائع", required=False)
        if not label:
            raise TripError("VENDOR_REQUIRED", "اكتب اسم البائع أولًا.")
        if not str(karat_id or "").strip():
            raise TripError("KARAT_REQUIRED", "اختر عيار حساب هذا الطرف (مطلوب لتسجيله موردًا).")
        registry = self._vendor_registry() if self._vendor_registry is not None else None
        if registry is None:
            from services.supplier_service import SupplierService
            registry = SupplierService(self._session, transaction_factory=self._transaction)       # the SAME database as this service
        party_id = registry.create_supplier(label, karat_id=karat_id)
        return {"id": str(party_id), "name": label}

    def buy_gold(self, trip_id, lines, vendor_party_id, vendor_name, buy_date, reference, notes, request_key, acknowledge_price=False,
                 *, _expected_actor=None):
        """BUY scrap gold with custody CASH (شراء ذهب نقدي): the cash leaves the custody cash and the gold (per karat, designation, actual grams)
        enters the custody gold in ONE transaction. No cash / bank / Main Safe, Scrap, supplier, inventory or lot movement is posted (the gold
        reaches the shop Scrap only through a normal custody return). The vendor identity is MANDATORY. The total (the sum of the line prices) may
        not exceed the custody cash; a total farther than the warning threshold from the SHOP_BUY reference needs the owner's acknowledgement."""
        session_actor = self._actor(_expected_actor)
        if str(trip_id or "").strip() == "":
            raise TripError("TRIP_NOT_FOUND", "اختر العهدة.")
        trip_number = _trip_id(trip_id)
        parsed = self.parse_buy_lines(lines)
        bought_on = _entry_date(buy_date)
        reference_text, notes_text = _text(reference, PAYMENT_REFERENCE_MAX, "المرجع"), _text(notes, NOTES_MAX, "الملاحظات")
        key = _key(request_key)
        party_text = str(vendor_party_id if vendor_party_id is not None else "").strip()
        digest = hashlib.sha256(json.dumps(
            [GOLD_BUY, trip_number, [[l["karat_id"], l["designation"], str(l["weight"]), str(l["price"])] for l in parsed], party_text,
             "" if party_text else str(vendor_name or "").strip(), bought_on.isoformat(), reference_text, notes_text],
            ensure_ascii=False).encode()).hexdigest()
        try:
            return self._post_gold_buy(session_actor, trip_number, parsed, party_text, vendor_name, bought_on, reference_text, notes_text, key, digest,
                                       bool(acknowledge_price))
        except IntegrityError as exc:
            if not _is_request_key_violation(exc):
                raise
            with self._transaction() as connection:                          # a twin committed first: decide from a FRESH read
                actor = self._authorization.require_permission_for_write(connection, session_actor, PERMISSION)
                replay = self._gold_buy_prior(self._repository(connection), key, digest, actor)
                if replay is None:
                    raise
                return replay

    @staticmethod
    def _gold_buy_result(entry, buy, duplicate):
        return {"entry_id": str(entry["id"]), "trip_id": str(entry["trip_id"]), "trip_code": trip_code(entry["trip_id"]),
                "code": entry_code(entry["trip_id"], entry["entry_no"]), "kind": GOLD_BUY, "created": False, "duplicate": duplicate,
                "vendor_kind": buy["vendor_kind"], "vendor_party_id": None if buy.get("vendor_party_id") is None else str(buy["vendor_party_id"]),
                "vendor_name": buy["vendor_name"], "total_price": str(buy["total_price"]),
                "reference_total": None if buy.get("reference_total") is None else str(buy["reference_total"]),
                "deviation_percent": None if buy.get("deviation_percent") is None else str(buy["deviation_percent"]),
                "price_warning_acknowledged": bool(buy.get("price_warning_acknowledged")),
                "lines": [{"karat_value": int(l["karat_value"]), "designation": l["designation"], "weight": str(l["weight_grams"]),
                           "price": str(l["line_price"])} for l in buy.get("lines", [])]}

    @classmethod
    def _gold_buy_prior(cls, repository, key, digest, actor):
        """The replay of an already-recorded request (None when the key is unused): the entry AND its document with the SAME payload by the SAME user."""
        entry = repository.by_request(key)
        if entry is None:
            return None
        buy = repository.entry_gold_buy(entry["id"])
        if buy is None or entry["request_hash"] != digest or int(entry["performed_by_user_id"]) != int(actor.user_id):
            raise TripError("REQUEST_CONFLICT", "طلب العملية مستخدم لعملية مختلفة.")
        return cls._gold_buy_result(entry, buy, True)

    def _post_gold_buy(self, session_actor, trip_id, parsed, party_text, vendor_name, bought_on, reference, notes, key, digest, acknowledged):
        with self._transaction() as connection:
            actor = self._authorization.require_permission_for_write(connection, session_actor, PERMISSION)
            repository = self._repository(connection)
            trip = repository.trip(trip_id, lock=True)                          # lock order: trip -> vendor party
            if trip is None:
                raise TripError("TRIP_NOT_FOUND", "العهدة غير موجودة.")
            replay = self._gold_buy_prior(repository, key, digest, actor)
            if replay is not None:
                return replay
            book_rules.require_legacy_trip(trip)                               # 056: a BOOK trip has no legacy gold path
            if trip["status"] != OPEN:
                raise TripError("TRIP_CLOSED", "العهدة مغلقة: أعد فتحها أولاً لشراء ذهب منها.")
            karat_values = {}
            for line in parsed:
                karat = repository.karat(line["karat_id"])
                if karat is None or not karat["is_active"]:
                    raise TripError("KARAT_NOT_FOUND", "العيار غير موجود أو غير نشط.")
                karat_values[line["karat_id"]] = int(karat["karat_value"])
            vendor_kind, vendor_id, vendor_label, vendor_key = self._vendor(repository, party_text, vendor_name)
            total = sum((l["price"] for l in parsed), ZERO_MONEY)
            if total >= MAX_MONEY:
                raise TripError("PRICE_INVALID", "إجمالي الشراء كبير جدًا.")
            custody = self._custody(repository, trip_id, current=True)         # read under the trip lock: the effective custody cash
            available = custody["cash"]["outstanding"]
            if total > available:
                raise TripError("PURCHASE_EXCEEDS_CASH",
                                f"نقدية العهدة المتاحة {available:,.2f} ج فقط: لا يمكن شراء بإجمالي {total:,.2f} ج.")
            reference_total, deviation = self.buy_reference(self._reference_prices(), parsed, karat_values, total)
            warned = deviation is not None and abs(deviation) > setting_rules.price_warning_percent(repository)
            if warned and not acknowledged:
                raise TripError("PRICE_DEVIATION", self._buy_warning_text(deviation, reference_total))
            now = repository.db_now()
            entry_no = repository.next_entry_no(trip_id)
            entry_id = repository.insert_entry(trip_id=trip_id, entry_no=entry_no, kind=GOLD_BUY, entry_date=bought_on, notes=notes, key=key,
                                               digest=digest, actor=actor, now=now)
            code = entry_code(trip_id, entry_no)
            repository.insert_gold_buy(entry_id=entry_id, vendor_kind=vendor_kind, vendor_party_id=vendor_id, vendor_name=vendor_label,
                                       vendor_key=vendor_key, total=total, reference_total=reference_total, deviation=deviation,
                                       acknowledged=warned and acknowledged, buy_date=bought_on, reference=reference)
            for number, line in enumerate(parsed, 1):
                repository.insert_gold_buy_line(entry_id=entry_id, line_no=number, karat_id=line["karat_id"], designation=line["designation"],
                                                weight=line["weight"], price=line["price"])
            what = " • ".join(f"{l['weight']:,.3f} جرام عيار {karat_values[l['karat_id']]} ({DESIGNATION_TEXT[l['designation']]}) بـ {l['price']:,.2f} ج"
                              for l in parsed)
            label = f"{KIND_TEXT[GOLD_BUY]} {code} — {trip_code(trip_id)}: {what} من {vendor_label} بإجمالي {total:,.2f} ج"
            repository.audit(actor=actor, event_type="MARKET_TRIP_GOLD_BUY", entity_id=entry_id, reference=code, description=label,
                             metadata={"trip_id": trip_id, "kind": GOLD_BUY, "vendor_kind": vendor_kind, "vendor_party_id": vendor_id,
                                       "vendor_name": vendor_label, "total_price": str(total),
                                       "lines": [{"karat": karat_values[l["karat_id"]], "designation": l["designation"], "weight": str(l["weight"]),
                                                  "price": str(l["price"])} for l in parsed],
                                       "reference_total": None if reference_total is None else str(reference_total),
                                       "deviation_percent": None if deviation is None else str(deviation),
                                       "price_warning_acknowledged": bool(warned and acknowledged), "date": bought_on.isoformat(),
                                       "employee_id": actor.employee_id}, now=now)
            entry = repository.by_request(key)
            result = self._gold_buy_result(entry, repository.entry_gold_buy(entry_id), False)
            if not self._session.is_current(session_actor):
                raise PermissionError("انتهت جلسة الدخول.")
        return result

    @staticmethod
    def _buy_warning_text(deviation, reference_total):
        direction = "أقل من" if deviation < 0 else "أعلى من"
        return (f"إجمالي الشراء {direction} المرجع (سعر شراء المحل لنفس العيارات) بنسبة {abs(deviation):,.2f}% "
                f"(المرجع {reference_total:,.2f} ج). تأكد من الأسعار قبل التسجيل.")

    # ------------------------------------------------------------------ sale of custody gold for cash (053, بيع ذهب العهدة)
    @staticmethod
    def _sale_price(grams, price_mode, price):
        """The AGREED price. TOTAL: the exact amount (two decimals at most). PER_GRAM: grams x the per-gram price (up to four decimals),
        rounded ONCE to the piaster (ROUND_HALF_UP). The total is the authoritative figure; it must be positive."""
        mode = str(price_mode or "").strip().upper()
        if mode not in (SALE_BASIS_TOTAL, SALE_BASIS_PER_GRAM):
            raise TripError("PRICE_BASIS_REQUIRED", "اختر طريقة التسعير: إجمالي الصفقة أو سعر الجرام.")
        number = _decimal(price, "السعر", "PRICE_INVALID")
        if number is None or number <= 0 or number >= MAX_MONEY:
            raise TripError("PRICE_INVALID", "أدخل السعر المتفق عليه بقيمة موجبة.")
        if mode == SALE_BASIS_TOTAL:
            if number != number.quantize(MONEY_STEP):
                raise TripError("PRICE_INVALID", "الإجمالي بخانتين عشريتين على الأكثر.")
            return mode, number.quantize(MONEY_STEP), None
        if number != number.quantize(SALE_PER_GRAM_STEP):
            raise TripError("PRICE_INVALID", "سعر الجرام بأربع خانات عشرية على الأكثر.")
        with localcontext() as context:
            context.prec = 50
            total = (grams * number).quantize(MONEY_STEP, rounding=ROUND_HALF_UP)
        if total <= 0:
            raise TripError("PRICE_INVALID", "إجمالي الصفقة يساوي صفرًا: راجع سعر الجرام والوزن.")
        return mode, total, number.quantize(SALE_PER_GRAM_STEP)

    def _reference_prices(self):
        """{karat: SHOP_BUY per gram} of the active price set, or {} when none is available (then no reference warning exists)."""
        try:
            prices = self._price_reference() if self._price_reference is not None else {}
        except Exception:                                               # a price-engine problem must never block a sale
            return {}
        return {int(k): Decimal(str(v)) for k, v in (prices or {}).items()}

    def reference_prices(self, *, _expected_actor=None):
        """{karat: SHOP_BUY per gram} of the active price set for the sale dialog's reference (empty when none is available). Read only; a
        price-engine problem never raises: the sale then simply has no reference warning."""
        self._actor(_expected_actor)
        return {str(k): str(v) for k, v in self._reference_prices().items()}

    def _sale_reference(self, grams, karat_value, fineness, total):
        return self.sale_reference(self._reference_prices(), grams, karat_value, fineness, total)

    @staticmethod
    def sale_reference(prices, grams, karat_value, fineness, total):
        """(reference total, reference per gram, deviation %) against the shop's SHOP_BUY reference, or (None, None, None).
        A master karat uses that karat's price; an assayed lot uses its pure grams x the 24K price (never a mapped master karat)."""
        with localcontext() as context:
            context.prec = 50
            if fineness is None:
                price = prices.get(int(karat_value))
                reference = None if price is None else grams * price
            else:
                price = prices.get(24)
                reference = None if price is None else grams * Decimal(str(fineness)) / Decimal(1000) * price
            if not reference or reference <= 0:
                return None, None, None
            deviation = ((total - reference) / reference * Decimal(100)).quantize(MONEY_STEP, rounding=ROUND_HALF_UP)
            return reference.quantize(MONEY_STEP, rounding=ROUND_HALF_UP), (reference / grams).quantize(SALE_PER_GRAM_STEP, rounding=ROUND_HALF_UP), deviation

    @staticmethod
    def _price_warning_text(deviation, reference_total):
        direction = "أقل من" if deviation < 0 else "أعلى من"
        return (f"السعر المتفق عليه {direction} سعر شراء المحل المرجعي بنسبة {abs(deviation):,.2f}% "
                f"(المرجع {reference_total:,.2f} ج). تأكد من السعر قبل التسجيل.")

    def _sale_buyer(self, repository, party_id, name):
        """The buyer: a registered party (any role, must be ACTIVE) or a third-party name. The name is a snapshot kept with the sale."""
        text = str(party_id if party_id is not None else "").strip()
        if text:
            try:
                number = int(text)
            except ValueError:
                raise TripError("BUYER_NOT_FOUND", "الطرف المشتري غير موجود.") from None
            party = repository.party(number, lock=True)
            if party is None:
                raise TripError("BUYER_NOT_FOUND", "الطرف المشتري غير موجود.")
            if not party["is_active"]:
                raise TripError("BUYER_INACTIVE", "هذا الطرف موقوف: لا يمكن البيع له. فعّله أولًا أو اختر مشتريًا آخر.")
            return BUYER_REGISTERED, number, str(party["name"])
        label = _text(name, BUYER_NAME_MAX, "اسم المشتري", required=False)
        if not label:
            raise TripError("BUYER_REQUIRED", "اختر المشتري من الأطراف المسجلة أو اكتب اسمه.")
        return BUYER_THIRD_PARTY, None, label

    def sell_gold(self, trip_id, source_kind, karat_id, designation, lot_id, weight, price_mode, price, buyer_party_id, buyer_name,
                  sale_date, reference, notes, request_key, acknowledge_price=False, *, _expected_actor=None):
        """SELL custody gold for cash to a buyer at a price agreed by hand (بيع ذهب العهدة).

        ONE transaction posts the GOLD_SALE custody entry and its immutable sale document (the source, the agreed total, the buyer and the
        price-warning evidence). The sold grams leave the custody (an aggregate line, or one WHOLE known-assay lot through a SALE_OUT custody
        movement) and the proceeds ENTER the custody cash: no cash, bank, Scrap, supplier, making, settlement or inventory movement is
        posted (the money stays in custody until a normal return or a supplier payment from custody). Right: MARKET_TRIP_MANAGE only, the
        buyer needs no role. A price farther than the warning threshold from the shop's SHOP_BUY reference is refused until the owner
        acknowledges it (a soft warning, never a hard block)."""
        session_actor = self._actor(_expected_actor)
        if str(trip_id or "").strip() == "":
            raise TripError("TRIP_NOT_FOUND", "اختر العهدة.")
        trip_number = _trip_id(trip_id)
        kind = str(source_kind or "").strip().upper()
        if kind not in (SALE_SOURCE_GOLD, SALE_SOURCE_LOT, SALE_SOURCE_BAR):
            raise TripError("SOURCE_REQUIRED", "اختر ما تبيعه: ذهبًا من سطر عيار أو سبيكة السبك أو سبيكة معروفة العيار.")
        karat_number, source, lot_number = None, None, None
        if kind == SALE_SOURCE_GOLD:
            try:
                karat_number = int(str(karat_id).strip())
            except (TypeError, ValueError):
                raise TripError("KARAT_REQUIRED", "اختر عيار الذهب المباع.") from None
            if karat_number <= 0:
                raise TripError("KARAT_REQUIRED", "اختر عيار الذهب المباع.")
            source = parse_designation(designation, GOLD_SALE)
        elif kind == SALE_SOURCE_LOT:
            try:
                lot_number = int(str(lot_id).strip())
            except (TypeError, ValueError):
                raise TripError("LOT_NOT_FOUND", "اختر السبيكة.") from None
            if lot_number <= 0:
                raise TripError("LOT_NOT_FOUND", "اختر السبيكة.")
        grams = parse_weight(weight)
        if kind in (SALE_SOURCE_GOLD, SALE_SOURCE_BAR) and grams == 0:
            raise TripError("WEIGHT_REQUIRED", "أدخل وزن الذهب المباع الفعلي بقيمة موجبة.")
        if kind == SALE_SOURCE_LOT and grams == 0:
            grams = None                                                      # a whole lot: the weight is derived under the locks
        sold_on = _entry_date(sale_date)
        reference_text, notes_text = _text(reference, PAYMENT_REFERENCE_MAX, "المرجع"), _text(notes, NOTES_MAX, "الملاحظات")
        key = _key(request_key)
        party_text = str(buyer_party_id if buyer_party_id is not None else "").strip()
        digest = hashlib.sha256(json.dumps(
            [GOLD_SALE, trip_number, kind, karat_number, source, lot_number, None if grams is None else str(grams),
             str(price_mode or "").strip().upper(), normalize_digits(str(price or "")).strip().replace(",", ""), party_text,
             "" if party_text else str(buyer_name or "").strip(), sold_on.isoformat(), reference_text, notes_text],
            ensure_ascii=False).encode()).hexdigest()
        try:
            return self._post_gold_sale(session_actor, trip_number, kind, karat_number, source, lot_number, grams, price_mode, price, party_text,
                                        buyer_name, sold_on, reference_text, notes_text, key, digest, bool(acknowledge_price))
        except IntegrityError as exc:
            if not _is_request_key_violation(exc):
                raise
            with self._transaction() as connection:                          # a twin committed first: decide from a FRESH read
                actor = self._authorization.require_permission_for_write(connection, session_actor, PERMISSION)
                replay = self._gold_sale_prior(self._repository(connection), key, digest, actor)
                if replay is None:
                    raise
                return replay

    @staticmethod
    def _gold_sale_result(entry, sale, duplicate):
        lot = sale["source_kind"] == SALE_SOURCE_LOT
        return {"entry_id": str(entry["id"]), "trip_id": str(entry["trip_id"]), "trip_code": trip_code(entry["trip_id"]),
                "code": entry_code(entry["trip_id"], entry["entry_no"]), "kind": GOLD_SALE, "created": False, "duplicate": duplicate,
                "source_kind": sale["source_kind"], "karat_value": None if sale.get("karat_value") is None else int(sale["karat_value"]),
                "designation": sale.get("designation"), "lot_id": None if sale.get("lot_id") is None else str(sale["lot_id"]),
                "weight": str(sale["weight_grams"]), "total_price": str(sale["total_price"]), "price_basis": sale["price_basis"],
                "price_per_gram": None if sale.get("price_per_gram") is None else str(sale["price_per_gram"]),
                "buyer_kind": sale["buyer_kind"], "buyer_party_id": None if sale.get("buyer_party_id") is None else str(sale["buyer_party_id"]),
                "buyer_name": sale["buyer_name"],
                "reference_price_per_gram": None if sale.get("reference_price_per_gram") is None else str(sale["reference_price_per_gram"]),
                "deviation_percent": None if sale.get("deviation_percent") is None else str(sale["deviation_percent"]),
                "price_warning_acknowledged": bool(sale.get("price_warning_acknowledged")), "is_lot": lot}

    @classmethod
    def _gold_sale_prior(cls, repository, key, digest, actor):
        """The replay of an already-recorded request (None when the key is unused). It needs the entry AND its sale document with the SAME
        payload by the SAME user; anything else is a conflict and writes nothing. A replay is decided before any availability is derived."""
        entry = repository.by_request(key)
        if entry is None:
            return None
        sale = repository.entry_gold_sale(entry["id"])
        if sale is None or entry["request_hash"] != digest or int(entry["performed_by_user_id"]) != int(actor.user_id):
            raise TripError("REQUEST_CONFLICT", "طلب العملية مستخدم لعملية مختلفة.")
        return cls._gold_sale_result(entry, sale, True)

    def _post_gold_sale(self, session_actor, trip_id, kind, karat_id, designation, lot_id, grams, price_mode, price, party_text, buyer_name,
                        sold_on, reference, notes, key, digest, acknowledged):
        with self._transaction() as connection:
            actor = self._authorization.require_permission_for_write(connection, session_actor, PERMISSION)
            repository = self._repository(connection)
            trip = repository.trip(trip_id, lock=True)                          # lock order: trip -> lot -> buyer party
            if trip is None:
                raise TripError("TRIP_NOT_FOUND", "العهدة غير موجودة.")
            replay = self._gold_sale_prior(repository, key, digest, actor)
            if replay is not None:
                return replay
            book_rules.require_legacy_trip(trip)                               # 056: a BOOK trip has no legacy gold path
            if trip["status"] != OPEN:
                raise TripError("TRIP_CLOSED", "العهدة مغلقة: أعد فتحها أولاً لبيع ذهب منها.")
            lot, movement_id, karat_value, fineness = None, None, None, None
            if kind == SALE_SOURCE_BAR:
                pool = self._custody(repository, trip_id, current=True)["bar"]          # 063: the melted bar (Eq21 grams) read under the trip lock
                if grams > pool:
                    raise TripError("BAR_EXCEEDS_POOL", f"رصيد السبيكة المتاح للبيع {pool:,.3f} جرام (مكافئ عيار 21) فقط: لا يمكن بيع {grams:,.3f} جرام.")
                karat_value = BASE_KARAT_VALUE
            elif kind == SALE_SOURCE_GOLD:
                karat = repository.karat(karat_id)
                if karat is None or not karat["is_active"]:
                    raise TripError("KARAT_NOT_FOUND", "العيار غير موجود أو غير نشط.")
                karat_value = int(karat["karat_value"])
                custody = self._custody(repository, trip_id, current=True)         # read under the trip lock: the effective outstanding
                line = next((g for g in custody["gold"] if g["karat_id"] == karat_id and g["designation"] == designation), None)
                outstanding = ZERO_WEIGHT if line is None else line["outstanding"]
                if grams > outstanding:
                    raise TripError("SALE_EXCEEDS_CUSTODY",
                                    f"المتبقي في العهدة من عيار {karat_value} ({DESIGNATION_TEXT[designation]}) هو "
                                    f"{outstanding:,.3f} جرام فقط: لا يمكن بيع {grams:,.3f} جرام.")
            else:
                locked = repository.lock_lots([lot_id])
                if not locked:
                    raise TripError("LOT_NOT_FOUND", "السبيكة غير موجودة.")
                lot = locked[0]
                if lot["fineness_permille"] is None:
                    raise TripError("LOT_ASSAY_UNKNOWN", "عيار هذه السبيكة الفعلي غير معلوم: لا تُباع قبل معرفة عيارها (لا يُستنتج العيار).")
                full = Decimal(str(lot["weight_grams"]))
                current = next(iter(repository.custody_lots(trip_id, lot_id=lot_id)), None)
                if current is None or Decimal(str(current["outstanding_grams"])) == 0:
                    raise TripError("LOT_NOT_IN_CUSTODY", "السبيكة ليست في هذه العهدة أو سبق بيعها أو تسويتها أو عُكس سبكها.")
                if Decimal(str(current["outstanding_grams"])) != full:
                    raise TripError("LOT_PARTIAL_NOT_ALLOWED", "رصيد السبيكة في العهدة جزئي: اعكس الرجوع الجزئي أولًا؛ تُباع السبيكة كاملة فقط.")
                if grams is not None and grams != full:
                    raise TripError("LOT_PARTIAL_NOT_ALLOWED", "تُباع السبيكة كاملة فقط؛ الكمية المرسلة لا تطابق وزنها الكامل.")
                grams, fineness = full, Decimal(str(lot["fineness_permille"]))
            buyer_kind, buyer_id, buyer_label = self._sale_buyer(repository, party_text, buyer_name)
            basis, total, per_gram = self._sale_price(grams, price_mode, price)
            reference_total, reference_per_gram, deviation = self._sale_reference(grams, karat_value, fineness, total)
            warned = deviation is not None and abs(deviation) > setting_rules.price_warning_percent(repository)
            if warned and not acknowledged:
                raise TripError("PRICE_DEVIATION", self._price_warning_text(deviation, reference_total))
            now = repository.db_now()
            entry_no = repository.next_entry_no(trip_id)
            entry_id = repository.insert_entry(trip_id=trip_id, entry_no=entry_no, kind=GOLD_SALE, entry_date=sold_on, notes=notes, key=key,
                                               digest=digest, actor=actor, now=now)
            code = entry_code(trip_id, entry_no)
            if kind == SALE_SOURCE_LOT:
                movement_id = repository.insert_lot_movement(lot_id=lot_id, entry_id=entry_id, movement_type=SALE_MOVEMENT, weight_delta=-grams)
            if kind == SALE_SOURCE_BAR:
                repository.insert_settle_move(entry_id=entry_id, trip_id=trip_id, kind=GOLD_SALE, pool="BAR", karat_id=None, delta=-grams)
            repository.insert_gold_sale(
                entry_id=entry_id, source_kind=kind, karat_id=karat_id, designation=designation, lot=lot, weight=grams, movement_id=movement_id,
                total=total, per_gram=per_gram, basis=basis, buyer_kind=buyer_kind, buyer_party_id=buyer_id, buyer_name=buyer_label,
                reference_price=reference_per_gram, deviation=deviation, acknowledged=warned and acknowledged, sale_date=sold_on, reference=reference)
            what = (f"سبيكة {grams:,.3f} جرام (عيار فعلي {fineness} بالألف)" if lot is not None
                    else f"سبيكة السبك {grams:,.3f} جرام (مكافئ عيار 21)" if kind == SALE_SOURCE_BAR
                    else f"{grams:,.3f} جرام عيار {karat_value} ({DESIGNATION_TEXT[designation]})")
            label = f"{KIND_TEXT[GOLD_SALE]} {code} — {trip_code(trip_id)}: {what} إلى {buyer_label} بمبلغ {total:,.2f} ج"
            repository.audit(actor=actor, event_type="MARKET_TRIP_GOLD_SALE", entity_id=entry_id, reference=code, description=label,
                             metadata={"trip_id": trip_id, "kind": GOLD_SALE, "source_kind": kind, "karat": karat_value, "designation": designation,
                                       "lot_id": None if lot is None else str(lot_id), "weight": str(grams), "total_price": str(total),
                                       "price_basis": basis, "price_per_gram": None if per_gram is None else str(per_gram),
                                       "buyer_kind": buyer_kind, "buyer_party_id": buyer_id, "buyer_name": buyer_label,
                                       "reference_price_per_gram": None if reference_per_gram is None else str(reference_per_gram),
                                       "deviation_percent": None if deviation is None else str(deviation),
                                       "price_warning_acknowledged": bool(warned and acknowledged), "date": sold_on.isoformat(),
                                       "employee_id": actor.employee_id}, now=now)
            entry = repository.by_request(key)
            result = self._gold_sale_result(entry, repository.entry_gold_sale(entry_id), False)
            if not self._session.is_current(session_actor):
                raise PermissionError("انتهت جلسة الدخول.")
        return result

    # ------------------------------------------------------------------ settlement step 2f: the remainder after the trip and the issue-weight correction (065)
    LOSS_CLASSES = ("CRUCIBLE", "MARKET")

    @classmethod
    def _disposal_lines(cls, lines, kind):
        """Validated, merged lines [(source, karat_id|None, grams, classification|None)]: source RAW (unmelted gold of a karat), LOGO (logo of a karat) or BAR (21K-equivalent
        grams of the melted bar). A loss line carries CRUCIBLE or MARKET; a return line carries none. An untouched row is ignored; the same source twice is summed."""
        merged = {}
        for index, line in enumerate(lines or [], 1):
            source = str(line.get("source", "") or "").strip().upper()
            weight_text, karat_text = str(line.get("weight", "") or "").strip(), str(line.get("karat_id", "") or "").strip()
            classification = str(line.get("classification", "") or "").strip().upper()
            if not (source or weight_text or karat_text):
                continue
            if source not in ("RAW", "LOGO", "BAR"):
                raise TripError("SOURCE_REQUIRED", f"السطر {index}: اختر المصدر (ذهب غير مسبوك أو لوجو أو سبيكة).")
            karat_number = None
            if source != "BAR":
                try:
                    karat_number = int(karat_text)
                except ValueError:
                    raise TripError("KARAT_REQUIRED", f"السطر {index}: اختر العيار.") from None
                if karat_number <= 0:
                    raise TripError("KARAT_REQUIRED", f"السطر {index}: اختر العيار.")
            grams = parse_weight(weight_text)
            if grams == 0:
                raise TripError("WEIGHT_REQUIRED", f"السطر {index}: أدخل وزنًا موجبًا.")
            if kind == ST_LOSS:
                if classification not in cls.LOSS_CLASSES:
                    raise TripError("CLASSIFICATION_REQUIRED", f"السطر {index}: اختر تصنيف الخسية (خسية بوتقة أو خسية سوق).")
            else:
                classification = ""
            key = (source, karat_number, classification or None)
            merged[key] = merged.get(key, ZERO_WEIGHT) + grams
        if not merged:
            raise TripError("LINES_REQUIRED", "أضف سطرًا واحدًا على الأقل.")
        return [(src, kn, g, cl) for (src, kn, cl), g in sorted(merged.items(), key=lambda kv: (kv[0][0], kv[0][1] or 0, kv[0][2] or ""))]

    def return_to_shop(self, trip_id, lines, entry_date, notes, request_key, *, _expected_actor=None):
        """RETURN what is left after the trip to the shop (مرتجع سوق): unmelted gold and logo go back to counted Scrap in their OWN karat, the melted bar as 21K-equivalent
        grams. One Scrap IN movement per target karat; the sources lose the grams; no cash, supplier or lot movement."""
        return self._dispose(ST_RET_MKT, trip_id, lines, entry_date, notes, request_key, _expected_actor)

    def record_loss(self, trip_id, lines, entry_date, notes, request_key, *, _expected_actor=None):
        """Record a LOSS (خسية) of custody gold: the grams leave the sources with a classification (crucible / market). Nothing else moves."""
        return self._dispose(ST_LOSS, trip_id, lines, entry_date, notes, request_key, _expected_actor)

    def _dispose(self, kind, trip_id, lines, entry_date, notes, request_key, expected_actor):
        session_actor = self._actor(expected_actor)
        if str(trip_id or "").strip() == "":
            raise TripError("TRIP_NOT_FOUND", "اختر العهدة.")
        trip_number = _trip_id(trip_id)
        parsed = self._disposal_lines(lines, kind)
        on_date, notes_text, key = _entry_date(entry_date), _text(notes, NOTES_MAX, "الملاحظات"), _key(request_key)
        digest = hashlib.sha256(json.dumps([kind, trip_number, [[s, k, str(g), c] for s, k, g, c in parsed], on_date.isoformat(), notes_text], ensure_ascii=False).encode()).hexdigest()
        try:
            return self._post_dispose(session_actor, kind, trip_number, parsed, on_date, notes_text, key, digest)
        except IntegrityError as exc:
            if not _is_request_key_violation(exc):
                raise
            with self._transaction() as connection:
                actor = self._authorization.require_permission_for_write(connection, session_actor, PERMISSION)
                replay = self._dispose_prior(self._repository(connection), key, digest, actor)
                if replay is None:
                    raise
                return replay

    @staticmethod
    def _dispose_result(entry, lines, duplicate):
        return {"entry_id": str(entry["id"]), "trip_id": str(entry["trip_id"]), "trip_code": trip_code(entry["trip_id"]),
                "code": entry_code(entry["trip_id"], entry["entry_no"]), "kind": entry["entry_kind"], "created": False, "duplicate": duplicate,
                "lines": [{"source": l["source"], "karat_value": None if l.get("karat_value") is None else int(l["karat_value"]),
                           "weight": str(l["weight_grams"]), "classification": l.get("classification")} for l in lines]}

    @classmethod
    def _dispose_prior(cls, repository, key, digest, actor):
        entry = repository.by_request(key)
        if entry is None:
            return None
        lines = repository.entry_settle_lines(entry["id"])
        if not lines or entry["request_hash"] != digest or int(entry["performed_by_user_id"]) != int(actor.user_id):
            raise TripError("REQUEST_CONFLICT", "طلب العملية مستخدم لعملية مختلفة.")
        return cls._dispose_result(entry, lines, True)

    def _post_dispose(self, session_actor, kind, trip_id, parsed, on_date, notes, key, digest):
        with self._transaction() as connection:
            actor = self._authorization.require_permission_for_write(connection, session_actor, PERMISSION)
            repository = self._repository(connection)
            trip = repository.trip(trip_id, lock=True)
            if trip is None:
                raise TripError("TRIP_NOT_FOUND", "العهدة غير موجودة.")
            replay = self._dispose_prior(repository, key, digest, actor)
            if replay is not None:
                return replay
            book_rules.require_legacy_trip(trip)
            if trip["status"] != OPEN:
                raise TripError("TRIP_CLOSED", "العهدة مغلقة: أعد فتحها أولاً.")
            custody = self._custody(repository, trip_id, current=True)
            rows = {(g["karat_id"], g["designation"]): g for g in custody["gold"]}
            needed = {}
            for source, karat_id, grams, _ in parsed:
                needed[(source, karat_id)] = needed.get((source, karat_id), ZERO_WEIGHT) + grams
            karats = {}
            for (source, karat_id), grams in sorted(needed.items(), key=lambda kv: (kv[0][0], kv[0][1] or 0)):
                if source == "BAR":
                    left, label = custody["bar"], "رصيد السبيكة (مكافئ عيار 21)"
                else:
                    karat = repository.karat(karat_id)
                    if karat is None or not karat["is_active"]:
                        raise TripError("KARAT_NOT_FOUND", "العيار غير موجود أو غير نشط.")
                    karats[karat_id] = karat
                    row = rows.get((karat_id, UNCLASSIFIED if source == "RAW" else LOGO_DESIGNATION))
                    left = ZERO_WEIGHT if row is None else row["outstanding"]
                    label = f"المتبقي من عيار {karat['karat_value']} ({'بدون وجهة' if source == 'RAW' else 'لوجو'})"
                if grams > left:
                    raise TripError("DISPOSE_EXCEEDS_CUSTODY", f"{label} هو {left:,.3f} جرام فقط: لا يمكن التصرف في {grams:,.3f} جرام.")
            targets = {}
            if kind == ST_RET_MKT:
                for source, karat_id, grams, _ in parsed:
                    if source == "BAR":
                        if BASE_KARAT_VALUE not in targets:
                            base = next((k for k in repository.active_karats() if int(k["karat_value"]) == BASE_KARAT_VALUE), None)
                            if base is None:
                                raise TripError("KARAT_NOT_FOUND", "عيار 21 غير موجود أو غير نشط.")
                            targets[BASE_KARAT_VALUE] = [base, ZERO_WEIGHT]
                        targets[BASE_KARAT_VALUE][1] += grams
                    else:
                        entry_for = targets.setdefault(int(karats[karat_id]["karat_value"]), [karats[karat_id], ZERO_WEIGHT])
                        entry_for[1] += grams
            now = repository.db_now()
            entry_no = repository.next_entry_no(trip_id)
            entry_id = repository.insert_entry(trip_id=trip_id, entry_no=entry_no, kind=kind, entry_date=on_date, notes=notes, key=key, digest=digest, actor=actor, now=now)
            code = entry_code(trip_id, entry_no)
            for number, (source, karat_id, grams, classification) in enumerate(parsed, 1):
                repository.insert_settle_line(entry_id=entry_id, kind=kind, line_no=number, source=source, karat_id=karat_id, weight=grams, classification=classification)
                repository.insert_settle_move(entry_id=entry_id, trip_id=trip_id, kind=kind, pool=source, karat_id=karat_id, delta=-grams)
            for value in sorted(targets):
                karat, grams = targets[value]
                movement = repository.post_scrap(kind=RETURN, karat_id=karat["id"], weight=grams, entry_id=entry_id, code=code, notes=notes, actor=actor, now=now)
                repository.insert_gold_leg(entry_id=entry_id, karat_id=karat["id"], designation=UNCLASSIFIED, weight=grams, scrap_movement_id=movement)
            text = "، ".join(self._disposal_text(source, karats.get(karat_id), grams, classification) for source, karat_id, grams, classification in parsed)
            repository.audit(actor=actor, event_type="MARKET_TRIP_" + kind, entity_id=entry_id, reference=code, description=f"{KIND_TEXT[kind]} {code} — {trip_code(trip_id)}: {text}",
                             metadata={"trip_id": trip_id, "kind": kind, "date": on_date.isoformat(),
                                       "lines": [{"source": s_, "karat_id": k_, "weight": str(g_), "classification": c_} for s_, k_, g_, c_ in parsed], "employee_id": actor.employee_id}, now=now)
            entry = repository.by_request(key)
            result = self._dispose_result(entry, repository.entry_settle_lines(entry_id), False)
            if not self._session.is_current(session_actor):
                raise PermissionError("انتهت جلسة الدخول.")
        return result

    @staticmethod
    def _disposal_text(source, karat, grams, classification):
        what = "سبيكة (مكافئ عيار 21)" if source == "BAR" else f"عيار {karat['karat_value']} ({'بدون وجهة' if source == 'RAW' else 'لوجو'})"
        return f"{what} {grams:,.3f} جرام" + {"CRUCIBLE": " — خسية بوتقة", "MARKET": " — خسية سوق"}.get(classification or "", "")

    def correct_issue_weight(self, trip_id, karat_id, direction, weight, reason, entry_date, request_key, *,
                              source_kind="SHOP_SCRAP", target_weight=None, _expected_actor=None):
        """Correct the weight of gold issued without a destination (تصحيح وزن الإخراج), both ways, reason mandatory. INCREASE: the real issued weight was HIGHER, so the
        difference leaves Scrap and enters the custody (refused when Scrap would go negative), or is recorded without a Scrap movement. DECREASE remains Scrap-backed.
        When `target_weight` is supplied, it is the desired physical RAW balance and the difference is derived under the custody lock."""
        session_actor = self._actor(_expected_actor)
        if str(trip_id or "").strip() == "":
            raise TripError("TRIP_NOT_FOUND", "اختر العهدة.")
        trip_number = _trip_id(trip_id)
        way = str(direction or "").strip().upper()
        if way not in ("INCREASE", "DECREASE"):
            raise TripError("DIRECTION_REQUIRED", "اختر اتجاه التصحيح: الوزن الفعلي أكبر أو أقل من المسجّل.")
        try:
            karat_number = int(str(karat_id).strip())
        except (TypeError, ValueError):
            raise TripError("KARAT_REQUIRED", "اختر العيار.") from None
        if karat_number <= 0:
            raise TripError("KARAT_REQUIRED", "اختر العيار.")
        source = str(source_kind or "").strip().upper()
        if source not in ("SHOP_SCRAP", "UNSOURCED"):
            raise TripError("ADJUST_SOURCE_INVALID", "اختر مصدر تصحيح الوزن.")
        if way == "DECREASE" and source != "SHOP_SCRAP":
            raise TripError("ADJUST_SOURCE_INVALID", "إنقاص وزن العهدة يجب أن يعيد الذهب إلى كسر المحل.")
        desired = None if target_weight is None else parse_weight(target_weight)
        if desired is not None and (way != "INCREASE" or desired == 0):
            raise TripError("WEIGHT_REQUIRED", "أدخل الوزن الفعلي المطلوب بزيادة موجبة.")
        if source == "UNSOURCED" and desired is None:
            raise TripError("WEIGHT_REQUIRED", "أدخل الوزن الفعلي الإجمالي؛ فرق الزيادة يُحسب من رصيد العهدة المقفول.")
        grams = None if desired is not None else parse_weight(weight)
        if grams == 0:
            raise TripError("WEIGHT_REQUIRED", "أدخل فرق الوزن بقيمة موجبة.")
        reason_text = _text(reason, SETTLE_TEXT_MAX, "السبب", required=True)
        on_date, key = _entry_date(entry_date), _key(request_key)
        if desired is None and source == "SHOP_SCRAP":
            payload = [ST_ADJUST, trip_number, karat_number, way, str(grams), reason_text, on_date.isoformat()]
        else:
            payload = [ST_ADJUST, trip_number, karat_number, way, source, str(desired if desired is not None else grams),
                       "TARGET" if desired is not None else "DELTA", reason_text, on_date.isoformat()]
        digest = hashlib.sha256(json.dumps(payload, ensure_ascii=False).encode()).hexdigest()
        try:
            return self._post_adjust(session_actor, trip_number, karat_number, way, grams, source, desired, reason_text, on_date, key, digest)
        except IntegrityError as exc:
            if not _is_request_key_violation(exc):
                raise
            with self._transaction() as connection:
                actor = self._authorization.require_permission_for_write(connection, session_actor, PERMISSION)
                replay = self._adjust_prior(self._repository(connection), key, digest, actor)
                if replay is None:
                    raise
                return replay

    @staticmethod
    def _adjust_result(entry, adjust, duplicate):
        return {"entry_id": str(entry["id"]), "trip_id": str(entry["trip_id"]), "trip_code": trip_code(entry["trip_id"]),
                "code": entry_code(entry["trip_id"], entry["entry_no"]), "kind": ST_ADJUST, "created": False, "duplicate": duplicate,
                "karat_value": int(adjust["karat_value"]), "direction": adjust["direction"], "weight": str(adjust["weight_grams"]),
                "source_kind": adjust.get("source_kind", "SHOP_SCRAP"), "reason": adjust["reason"]}

    @classmethod
    def _adjust_prior(cls, repository, key, digest, actor):
        entry = repository.by_request(key)
        if entry is None:
            return None
        adjust = repository.entry_settle_adjust(entry["id"])
        if adjust is None or entry["request_hash"] != digest or int(entry["performed_by_user_id"]) != int(actor.user_id):
            raise TripError("REQUEST_CONFLICT", "طلب العملية مستخدم لعملية مختلفة.")
        return cls._adjust_result(entry, adjust, True)

    def _post_adjust(self, session_actor, trip_id, karat_id, way, grams, source, desired, reason, on_date, key, digest):
        with self._transaction() as connection:
            actor = self._authorization.require_permission_for_write(connection, session_actor, PERMISSION)
            repository = self._repository(connection)
            trip = repository.trip(trip_id, lock=True)
            if trip is None:
                raise TripError("TRIP_NOT_FOUND", "العهدة غير موجودة.")
            replay = self._adjust_prior(repository, key, digest, actor)
            if replay is not None:
                return replay
            book_rules.require_legacy_trip(trip)
            if trip["status"] != OPEN:
                raise TripError("TRIP_CLOSED", "العهدة مغلقة: أعد فتحها أولاً.")
            if source == "UNSOURCED" and not repository.has_adjustment_source_schema():
                raise TripError("SCHEMA_OUTDATED", "لا يمكن تسجيل زيادة دون حركة كسر قبل تطبيق تحديث قاعدة البيانات 079.")
            karat = repository.karat(karat_id)
            if karat is None or not karat["is_active"]:
                raise TripError("KARAT_NOT_FOUND", "العيار غير موجود أو غير نشط.")
            if way == "INCREASE":
                if desired is not None:
                    custody = self._custody(repository, trip_id, current=True)
                    row = next((g for g in custody["gold"] if g["karat_id"] == karat_id and g["designation"] == UNCLASSIFIED), None)
                    left = ZERO_WEIGHT if row is None else row["outstanding"]
                    if desired <= left:
                        raise TripError("ADJUST_TARGET_NOT_HIGHER",
                                        f"الرصيد الحالي من عيار {karat['karat_value']} (بدون وجهة) هو {left:,.3f} جرام؛ الوزن المطلوب ليس أكبر منه.")
                    grams = desired - left
                if source == "SHOP_SCRAP":
                    scrap = self._scrap(connection)
                    available = sum((Decimal(str(r["weight_delta_grams"])) for r in scrap.lock_old_gold_balance(karat_id)), ZERO_WEIGHT)
                    if grams > available:
                        raise TripError("SCRAP_SHORTAGE", shortage_message(karat["karat_value"], grams, available, held_grams(scrap, karat_id)))
            else:
                custody = self._custody(repository, trip_id, current=True)
                row = next((g for g in custody["gold"] if g["karat_id"] == karat_id and g["designation"] == UNCLASSIFIED), None)
                left = ZERO_WEIGHT if row is None else row["outstanding"]
                if grams > left:
                    raise TripError("ADJUST_EXCEEDS_CUSTODY", f"المتبقي في العهدة من عيار {karat['karat_value']} (بدون وجهة) هو {left:,.3f} جرام فقط: لا يمكن إنقاص {grams:,.3f} جرام.")
            now = repository.db_now()
            entry_no = repository.next_entry_no(trip_id)
            entry_id = repository.insert_entry(trip_id=trip_id, entry_no=entry_no, kind=ST_ADJUST, entry_date=on_date, notes=reason, key=key, digest=digest, actor=actor, now=now)
            code = entry_code(trip_id, entry_no)
            movement = None
            if way == "DECREASE" or source == "SHOP_SCRAP":
                movement = repository.post_scrap(kind=ISSUE if way == "INCREASE" else RETURN, karat_id=karat_id, weight=grams, entry_id=entry_id, code=code, notes=reason, actor=actor, now=now)
                repository.insert_gold_leg(entry_id=entry_id, karat_id=karat_id, designation=UNCLASSIFIED, weight=grams, scrap_movement_id=movement)
            repository.insert_settle_move(entry_id=entry_id, trip_id=trip_id, kind=ST_ADJUST, pool="RAW", karat_id=karat_id, delta=grams if way == "INCREASE" else -grams)
            repository.insert_settle_adjust(entry_id=entry_id, karat_id=karat_id, direction=way, source_kind=source, weight=grams, reason=reason)
            source_text = "من كسر المحل" if source == "SHOP_SCRAP" else "تسجيل فعلي دون حركة كسر"
            label = f"{KIND_TEXT[ST_ADJUST]} {code} — {trip_code(trip_id)}: عيار {karat['karat_value']} {'+' if way == 'INCREASE' else '−'}{grams:,.3f} جرام • {source_text} • {reason}"
            repository.audit(actor=actor, event_type="MARKET_TRIP_ST_ADJUST", entity_id=entry_id, reference=code, description=label,
                             metadata={"trip_id": trip_id, "kind": ST_ADJUST, "karat": karat["karat_value"], "direction": way, "weight": str(grams),
                                       "source_kind": source, "target_weight": None if desired is None else str(desired), "reason": reason,
                                       "date": on_date.isoformat(), "employee_id": actor.employee_id}, now=now)
            entry = repository.by_request(key)
            result = self._adjust_result(entry, repository.entry_settle_adjust(entry_id), False)
            if not self._session.is_current(session_actor):
                raise PermissionError("انتهت جلسة الدخول.")
        return result

    # ------------------------------------------------------------------ settlement step 2e: the custody cash settled after the trip (064)
    @staticmethod
    def _settle_amount(value):
        number = _decimal(value, "المبلغ", "AMOUNT_INVALID")
        if number is None or number <= 0 or number >= MAX_MONEY:
            raise TripError("AMOUNT_INVALID", "أدخل مبلغًا موجبًا.")
        if number != number.quantize(MONEY_STEP):
            raise TripError("AMOUNT_INVALID", "المبلغ بخانتين عشريتين على الأكثر.")
        return number.quantize(MONEY_STEP)

    def record_custody_expense(self, trip_id, amount, description, entry_date, reference, notes, request_key, *, _expected_actor=None):
        """A free-text EXPENSE paid from the custody cash after the trip (مصروف من العهدة). The description is mandatory. The amount leaves the custody cash;
        no cash, bank, Scrap or supplier movement exists. Right: MARKET_TRIP_MANAGE (rechecked in the transaction)."""
        return self._settle_cash(ST_EXPENSE, trip_id, amount, description, None, entry_date, reference, notes, request_key, _expected_actor)

    def deposit_custody_cash(self, trip_id, amount, bank_account_id, entry_date, reference, notes, request_key, *, _expected_actor=None):
        """Deposit remaining custody cash into an ACTIVE bank / wallet account (إيداع باقي النقدية في حساب): the amount leaves the custody cash and ONE bank
        movement enters the account."""
        return self._settle_cash(ST_CASH_BANK, trip_id, amount, "", bank_account_id, entry_date, reference, notes, request_key, _expected_actor)

    def write_off_custody_cash(self, trip_id, amount, reason, entry_date, notes, request_key, *, _expected_actor=None):
        """Write off remaining custody cash (إعدام): the amount leaves the custody cash; a reason is mandatory; no other movement."""
        return self._settle_cash(ST_CASH_OFF, trip_id, amount, reason, None, entry_date, "", notes, request_key, _expected_actor)

    def _settle_cash(self, kind, trip_id, amount, description, bank_account_id, entry_date, reference, notes, request_key, expected_actor):
        session_actor = self._actor(expected_actor)
        if str(trip_id or "").strip() == "":
            raise TripError("TRIP_NOT_FOUND", "اختر العهدة.")
        trip_number = _trip_id(trip_id)
        total = self._settle_amount(amount)
        text = _text(description, SETTLE_TEXT_MAX, "البيان" if kind == ST_EXPENSE else "السبب", required=kind != ST_CASH_BANK)
        account_number = None
        if kind == ST_CASH_BANK:
            try:
                account_number = int(str(bank_account_id).strip())
            except (TypeError, ValueError):
                raise TripError("BANK_ACCOUNT_REQUIRED", "اختر حساب البنك أو المحفظة.") from None
            if account_number <= 0:
                raise TripError("BANK_ACCOUNT_REQUIRED", "اختر حساب البنك أو المحفظة.")
        on_date, reference_text, notes_text, key = _entry_date(entry_date), _text(reference, PAYMENT_REFERENCE_MAX, "المرجع"), _text(notes, NOTES_MAX, "الملاحظات"), _key(request_key)
        digest = hashlib.sha256(json.dumps([kind, trip_number, str(total), text, account_number, on_date.isoformat(), reference_text, notes_text], ensure_ascii=False).encode()).hexdigest()
        try:
            return self._post_settle_cash(session_actor, kind, trip_number, total, text, account_number, on_date, reference_text, notes_text, key, digest)
        except IntegrityError as exc:
            if not _is_request_key_violation(exc):
                raise
            with self._transaction() as connection:
                actor = self._authorization.require_permission_for_write(connection, session_actor, PERMISSION)
                replay = self._settle_cash_prior(self._repository(connection), key, digest, actor)
                if replay is None:
                    raise
                return replay

    @staticmethod
    def _settle_cash_result(entry, document, duplicate):
        return {"entry_id": str(entry["id"]), "trip_id": str(entry["trip_id"]), "trip_code": trip_code(entry["trip_id"]),
                "code": entry_code(entry["trip_id"], entry["entry_no"]), "kind": entry["entry_kind"], "created": False, "duplicate": duplicate,
                "amount": str(document["amount"]), "description": document.get("description") or "",
                "bank_account_id": None if document.get("bank_account_id") is None else str(document["bank_account_id"])}

    @classmethod
    def _settle_cash_prior(cls, repository, key, digest, actor):
        entry = repository.by_request(key)
        if entry is None:
            return None
        document = repository.entry_settle_cash(entry["id"])
        if document is None or entry["request_hash"] != digest or int(entry["performed_by_user_id"]) != int(actor.user_id):
            raise TripError("REQUEST_CONFLICT", "طلب العملية مستخدم لعملية مختلفة.")
        return cls._settle_cash_result(entry, document, True)

    def _post_settle_cash(self, session_actor, kind, trip_id, total, text, account_id, on_date, reference, notes, key, digest):
        with self._transaction() as connection:
            actor = self._authorization.require_permission_for_write(connection, session_actor, PERMISSION)
            repository = self._repository(connection)
            trip = repository.trip(trip_id, lock=True)                          # lock order: trip -> bank account
            if trip is None:
                raise TripError("TRIP_NOT_FOUND", "العهدة غير موجودة.")
            replay = self._settle_cash_prior(repository, key, digest, actor)
            if replay is not None:
                return replay
            book_rules.require_legacy_trip(trip)
            if trip["status"] != OPEN:
                raise TripError("TRIP_CLOSED", "العهدة مغلقة: أعد فتحها أولاً.")
            outstanding = custody_rules.cash_rows_outstanding(repository.custody_cash(trip_id, current=True))
            if total > outstanding:
                raise TripError("CASH_EXCEEDS_CUSTODY", f"المتبقي في نقدية العهدة {outstanding:,.2f} ج فقط: لا يمكن تسوية {total:,.2f} ج.")
            bank = None
            if kind == ST_CASH_BANK:
                bank = repository.bank_account(account_id, lock=True)
                if bank is None or not bank["is_active"]:
                    raise TripError("BANK_ACCOUNT_NOT_FOUND", "حساب البنك أو المحفظة غير موجود أو غير نشط.")
            now = repository.db_now()
            entry_no = repository.next_entry_no(trip_id)
            entry_id = repository.insert_entry(trip_id=trip_id, entry_no=entry_no, kind=kind, entry_date=on_date, notes=notes, key=key, digest=digest, actor=actor, now=now)
            code = entry_code(trip_id, entry_no)
            movement_id = None
            if kind == ST_CASH_BANK:
                movement_id = repository.post_bank_in(account_id=account_id, entry_id=entry_id, code=code, amount=total,
                                                      description=f"{KIND_TEXT[kind]} {code} — {trip_code(trip_id)}", actor=actor, now=now)
            repository.insert_settle_cash(entry_id=entry_id, kind=kind, amount=total, description=text, reference=reference, bank_account_id=account_id, bank_movement_id=movement_id)
            label = f"{KIND_TEXT[kind]} {code} — {trip_code(trip_id)}: {total:,.2f} ج" + (f" ← {bank['name']}" if bank else f" • {text}")
            repository.audit(actor=actor, event_type="MARKET_TRIP_" + kind, entity_id=entry_id, reference=code, description=label,
                             metadata={"trip_id": trip_id, "kind": kind, "amount": str(total), "description": text, "bank_account_id": account_id,
                                       "date": on_date.isoformat(), "employee_id": actor.employee_id}, now=now)
            entry = repository.by_request(key)
            result = self._settle_cash_result(entry, repository.entry_settle_cash(entry_id), False)
            if not self._session.is_current(session_actor):
                raise PermissionError("انتهت جلسة الدخول.")
        return result

    def sale_price_check(self, trip_id, source_kind, karat_id, designation, lot_id, weight, price_mode, price, *, _expected_actor=None):
        """READ ONLY preview for the dialog: the agreed total, the per-gram price, the SHOP_BUY reference and the deviation. It never
        writes and never reserves anything; the posting repeats every check under the locks."""
        self._actor(_expected_actor)
        kind = str(source_kind or "").strip().upper()
        with self._transaction() as connection:
            self._authorization.require_permission_read(connection, self._session.require_authenticated(), PERMISSION)
            repository = self._repository(connection)
            fineness, karat_value = None, None
            if kind == SALE_SOURCE_BAR:
                karat_value, grams = BASE_KARAT_VALUE, parse_weight(weight)
                if grams == 0:
                    raise TripError("WEIGHT_REQUIRED", "أدخل وزن الذهب المباع.")
            elif kind == SALE_SOURCE_LOT:
                lot = repository.lot_by_id(int(str(lot_id).strip()))
                if lot is None or lot["fineness_permille"] is None:
                    raise TripError("LOT_ASSAY_UNKNOWN", "عيار هذه السبيكة الفعلي غير معلوم.")
                grams, fineness = Decimal(str(lot["weight_grams"])), Decimal(str(lot["fineness_permille"]))
            else:
                karat = repository.karat(int(str(karat_id).strip()))
                if karat is None:
                    raise TripError("KARAT_REQUIRED", "اختر عيار الذهب المباع.")
                karat_value, grams = int(karat["karat_value"]), parse_weight(weight)
                if grams == 0:
                    raise TripError("WEIGHT_REQUIRED", "أدخل وزن الذهب المباع.")
        basis, total, per_gram = self._sale_price(grams, price_mode, price)
        reference_total, reference_per_gram, deviation = self._sale_reference(grams, karat_value, fineness, total)
        warned = deviation is not None and abs(deviation) > setting_rules.price_warning_percent(repository)
        return {"basis": basis, "total": str(total), "per_gram": str(per_gram if per_gram is not None else (total / grams).quantize(SALE_PER_GRAM_STEP, rounding=ROUND_HALF_UP)),
                "weight": str(grams), "reference_total": None if reference_total is None else str(reference_total),
                "reference_per_gram": None if reference_per_gram is None else str(reference_per_gram),
                "deviation_percent": None if deviation is None else str(deviation), "warning": warned,
                "message": self._price_warning_text(deviation, reference_total) if warned else ""}

    # ------------------------------------------------------------------ lot returns to the shop (M2)
    def shop_lots(self, *, _expected_actor=None):
        """The shop-side lot stock: physical grams and exact assay per lot; pure grams only when the assay is known. Kept apart from
        Scrap, inventory pieces and every karat total."""
        actor = self._actor(_expected_actor)
        with self._transaction() as connection:
            self._authorization.require_permission_read(connection, actor, PERMISSION)
            rows = self._repository(connection).shop_lots()
        result = []
        for r in rows:
            stock = Decimal(str(r["stock_grams"]))
            fineness = None if r["fineness_permille"] is None else Decimal(str(r["fineness_permille"]))
            result.append({"lot_id": str(r["id"]), "trip_id": str(r["trip_id"]), "trip_code": trip_code(r["trip_id"]),
                           "code": entry_code(r["trip_id"], r["entry_no"]), "lot_no": int(r["lot_no"]), "kind": r["lot_kind"],
                           "label": r.get("label") or "", "original_weight": Decimal(str(r["original_weight"])), "stock": stock,
                           "fineness": fineness, "pure": lot_pure(stock, fineness)})
        return result

    @staticmethod
    def _lot_lines(lines, required=True):
        """Validated {lot_id: supplied_weight|None}. A melt lot is an indivisible bar: the caller only SELECTS lots. A weight may still be
        sent (an old client) but it is never used - it is only compared with the full weight under the locks. A lot twice is refused."""
        selected = {}
        for line in lines or []:
            raw_id = str(line.get("lot_id") if isinstance(line, dict) else line[0]).strip()
            raw_weight = line.get("weight") if isinstance(line, dict) else (line[1] if len(line) > 1 else None)
            if raw_id in ("", "None"):
                if not str(raw_weight or "").strip():
                    continue
                raise TripError("LOT_REQUIRED", "اختر الشحنة.")
            try:
                lot_id = int(raw_id)
            except ValueError:
                raise TripError("LOT_NOT_FOUND", "الشحنة غير موجودة.") from None
            if lot_id in selected:
                raise TripError("LOT_DUPLICATE", "الشحنة نفسها مكررة في الطلب: اخترها مرة واحدة.")
            supplied = None if not str(raw_weight if raw_weight is not None else "").strip() else parse_weight(raw_weight)
            selected[lot_id] = supplied
        if not selected and required:
            raise TripError("NOTHING_TO_RECORD", "اختر شحنة واحدة على الأقل لإرجاعها.")
        return selected

    def return_lots(self, trip_id, lines, entry_date, notes, request_key, *, _expected_actor=None):
        """RETURN whole melt lots from custody to the shop's lot stock (OPEN trip). A lot returns complete or stays in custody: the
        posted grams are the lot's full outstanding weight, derived under the locks, never taken from the caller. Each lot keeps its
        identity and assay; the grams leave custody once and enter the shop lot ledger once, atomically."""
        session_actor = self._actor(_expected_actor)
        if str(trip_id or "").strip() == "":
            raise TripError("TRIP_NOT_FOUND", "اختر العهدة.")
        trip_number = _trip_id(trip_id)
        selected = self._lot_lines(lines)
        returned_on, notes_text, key = _entry_date(entry_date), _text(notes, NOTES_MAX, "الملاحظات"), _key(request_key)
        digest = hashlib.sha256(json.dumps(
            [LOT_RETURN, trip_number, sorted((k, None if w is None else str(w)) for k, w in selected.items()), returned_on.isoformat(),
             notes_text], ensure_ascii=False).encode()).hexdigest()
        try:
            return self._post_lot_return(session_actor, trip_number, selected, returned_on, notes_text, key, digest)
        except IntegrityError as exc:
            if not _is_request_key_violation(exc):
                raise
            return self._replay(session_actor, key, digest, exc)

    def _post_lot_return(self, session_actor, trip_id, selected, returned_on, notes, key, digest):
        with self._transaction() as connection:
            actor = self._authorization.require_permission_for_write(connection, session_actor, PERMISSION)
            repository = self._repository(connection)
            trip, replay = self._open_trip(repository, trip_id, key, digest, actor)      # trip lock first
            if replay is not None:
                return replay
            locked = {int(r["id"]): r for r in repository.lock_lots(selected)}            # then the lots, ascending
            for lot_id in sorted(selected):
                if lot_id not in locked:
                    raise TripError("LOT_NOT_FOUND", "الشحنة غير موجودة.")
            outstanding = {int(l["lot_id"]): l for l in self._custody(repository, trip_id, current=True)["lots"]}   # a DECISION read under the locks
            grams = {}
            for lot_id in sorted(selected):
                row = locked[lot_id]
                name = f"الشحنة {int(row['lot_no'])} من {entry_code(row['trip_id'], row['entry_no'])}"
                lot = outstanding.get(lot_id)
                if lot is None:
                    raise TripError("LOT_NOT_IN_CUSTODY", f"{name} ليست في هذه العهدة (هي بالمحل أو في عهدة أخرى أو عُكس سبكها).")
                if lot["outstanding"] != lot["weight"]:
                    raise TripError("LOT_PARTIALLY_RETURNED",
                                    f"{name} رُجع جزء منها سابقًا (المتبقي {lot['outstanding']:,.3f} من {lot['weight']:,.3f} جرام). "
                                    "لا يُكمل النظام الرجوع الجزئي: اعكس الرجوع الجزئي أولًا ثم أرجع الشحنة كاملة.")
                if selected[lot_id] is not None and selected[lot_id] != lot["weight"]:
                    raise TripError("LOT_PARTIAL_NOT_ALLOWED",
                                    f"ترجع السبكة كاملة ولا يمكن إرجاع جزء منها: وزن {name} الكامل {lot['weight']:,.3f} جرام.")
                grams[lot_id] = lot["outstanding"]                                         # the full weight, derived here
            now = repository.db_now()
            entry_no = repository.next_entry_no(trip_id)
            entry_id = repository.insert_entry(trip_id=trip_id, entry_no=entry_no, kind=LOT_RETURN, entry_date=returned_on, notes=notes,
                                               key=key, digest=digest, actor=actor, now=now)
            code = entry_code(trip_id, entry_no)
            for lot_id in sorted(grams):
                custody_movement = repository.insert_lot_movement(lot_id=lot_id, entry_id=entry_id, movement_type=SHOP_RETURN_MOVEMENT,
                                                                  weight_delta=-grams[lot_id])
                shop_movement = repository.insert_shop_lot_movement(lot_id=lot_id, entry_id=entry_id, movement_type=SHOP_RETURN_IN,
                                                                    weight_delta=grams[lot_id])
                repository.insert_lot_return(entry_id=entry_id, lot_id=lot_id, weight=grams[lot_id],
                                             custody_movement_id=custody_movement, shop_movement_id=shop_movement)
            repository.audit(actor=actor, event_type="MARKET_TRIP_LOT_RETURN", entity_id=entry_id, reference=code,
                             description=f"{KIND_TEXT[LOT_RETURN]} {code} — {trip_code(trip_id)}",
                             metadata={"trip_id": trip_id, "kind": LOT_RETURN, "date": returned_on.isoformat(),
                                       "lots": {str(k): str(w) for k, w in sorted(grams.items())}, "employee_id": actor.employee_id}, now=now)
            if not self._session.is_current(session_actor):
                raise PermissionError("انتهت جلسة الدخول.")
        return {"entry_id": str(entry_id), "trip_id": str(trip_id), "trip_code": trip_code(trip_id), "code": code, "kind": LOT_RETURN,
                "created": False, "duplicate": False}

    # ------------------------------------------------------------------ reissue whole shop bars into a custody (M3)
    def issue_lots(self, trip_id, lines, entry_date, notes, request_key, *, _expected_actor=None):
        """ISSUE whole shop bars (lots that returned to the shop) into an OPEN custody. The bar keeps its identity, weight, kind and assay;
        one shop OUT and one custody IN post atomically, the full weight derived under the locks (a client quantity is only compared).
        A lot is in the shop or in one custody only: a bar already out, or with a partial shop balance, is refused."""
        session_actor = self._actor(_expected_actor)
        if str(trip_id or "").strip() == "":
            raise TripError("TRIP_NOT_FOUND", "اختر العهدة.")
        trip_number = _trip_id(trip_id)
        selected = self._lot_lines(lines)
        issued_on, notes_text, key = _entry_date(entry_date), _text(notes, NOTES_MAX, "الملاحظات"), _key(request_key)
        digest = hashlib.sha256(json.dumps(
            [LOT_ISSUE, trip_number, sorted((k, None if w is None else str(w)) for k, w in selected.items()), issued_on.isoformat(),
             notes_text], ensure_ascii=False).encode()).hexdigest()
        try:
            return self._post_lot_issue(session_actor, trip_number, selected, issued_on, notes_text, key, digest)
        except IntegrityError as exc:
            if not _is_request_key_violation(exc):
                raise
            return self._replay(session_actor, key, digest, exc)

    def _plan_lot_issue(self, repository, selected):
        """Lock the selected bars (ascending, after the trip) and derive each one's FULL weight under the locks: {lot_id: grams}.
        A bar not in the shop, with a partial shop balance, or with a supplied weight that is not the full weight is refused."""
        locked = {int(r["id"]): r for r in repository.lock_lots(selected)}            # then the lots, ascending (same order as a return)
        for lot_id in sorted(selected):
            if lot_id not in locked:
                raise TripError("LOT_NOT_FOUND", "الشحنة غير موجودة.")
        grams = {}
        for lot_id in sorted(selected):
            row = locked[lot_id]
            name = f"الشحنة {int(row['lot_no'])} من {entry_code(row['trip_id'], row['entry_no'])}"
            full = Decimal(str(repository.lot_by_id(lot_id)["weight_grams"]))
            held = Decimal(str(repository.shop_lot_balance(lot_id)))                 # read under the lot lock
            if held == 0:
                raise TripError("LOT_NOT_IN_SHOP", f"{name} ليست بالمحل (هي في عهدة أخرى أو لم تُرجع بعد): لا تُخرج إلا سبكة موجودة بالمحل.")
            if held != full:
                raise TripError("LOT_PARTIAL_SHOP_STOCK",
                                f"رصيد {name} بالمحل جزئي ({held:,.3f} من {full:,.3f} جرام): لا تُخرج السبكة إلا كاملة؛ "
                                "اعكس الرجوع الجزئي أولًا.")
            if selected[lot_id] is not None and selected[lot_id] != full:
                raise TripError("LOT_PARTIAL_NOT_ALLOWED",
                                f"تخرج السبكة كاملة ولا يمكن إخراج جزء منها: وزن {name} الكامل {full:,.3f} جرام.")
            grams[lot_id] = held                                                       # the full weight, derived here
        return grams

    def _write_lot_issue(self, repository, *, trip_id, grams, issued_on, notes, key, digest, actor):
        """Post one LOT_ISSUE entry on the caller's connection: per bar one shop OUT, one custody IN and the link row, then the audit."""
        now = repository.db_now()
        entry_no = repository.next_entry_no(trip_id)
        entry_id = repository.insert_entry(trip_id=trip_id, entry_no=entry_no, kind=LOT_ISSUE, entry_date=issued_on, notes=notes,
                                           key=key, digest=digest, actor=actor, now=now)
        code = entry_code(trip_id, entry_no)
        for lot_id in sorted(grams):
            shop_movement = repository.insert_shop_lot_movement(lot_id=lot_id, entry_id=entry_id, movement_type=SHOP_ISSUE_OUT,
                                                                weight_delta=-grams[lot_id])
            custody_movement = repository.insert_lot_movement(lot_id=lot_id, entry_id=entry_id, movement_type=SHOP_ISSUE_MOVEMENT,
                                                              weight_delta=grams[lot_id])
            repository.insert_lot_issue(entry_id=entry_id, lot_id=lot_id, weight=grams[lot_id], custody_movement_id=custody_movement,
                                        shop_movement_id=shop_movement)
        repository.audit(actor=actor, event_type="MARKET_TRIP_LOT_ISSUE", entity_id=entry_id, reference=code,
                         description=f"{KIND_TEXT[LOT_ISSUE]} {code} — {trip_code(trip_id)}",
                         metadata={"trip_id": trip_id, "kind": LOT_ISSUE, "date": issued_on.isoformat(),
                                   "lots": {str(k): str(w) for k, w in sorted(grams.items())}, "employee_id": actor.employee_id}, now=now)
        return entry_id, code

    def create_trip(self, cash, gold, lots, entry_date, notes, request_key, *, _expected_actor=None):
        """ONE new custody holding any combination of MAIN_SAFE cash, Scrap lines (karat + MELT/LOGO) and whole shop bars (at least one
        positive quantity or one bar). Everything posts in ONE transaction: the trip, the ISSUE entry (cash / Scrap, when present) and the
        LOT_ISSUE entry (bars, when present); any failure rolls back every row. One request key and hash cover the whole payload (the
        bar entry of a mixed creation carries a key derived from it); a replay returns the original custody with all its entries."""
        session_actor = self._actor(_expected_actor)
        amount, grams = self._lines(cash, gold, ISSUE, allow_empty=True)
        selected = self._lot_lines(lots, required=False)
        if amount == 0 and not grams and not selected:
            raise TripError("NOTHING_TO_RECORD", "أدخل مبلغًا نقديًا أو وزن ذهب موجبًا، أو اختر سبيكة موجودة بالمحل.")
        paid_on, notes_text, key = _entry_date(entry_date), _text(notes, NOTES_MAX, "الملاحظات"), _key(request_key)
        digest = hashlib.sha256(json.dumps(
            ["CREATE_TRIP", str(amount), sorted((k, d, str(w)) for (k, d), w in grams.items()),
             sorted((k, None if w is None else str(w)) for k, w in selected.items()), paid_on.isoformat(), notes_text],
            ensure_ascii=False).encode()).hexdigest()
        try:
            return self._post_create(session_actor, amount, grams, selected, paid_on, notes_text, key, digest)
        except IntegrityError as exc:
            if not _is_request_key_violation(exc):
                raise
            with self._transaction() as connection:                      # a twin committed first: decide from a FRESH read
                actor = self._authorization.require_permission_for_write(connection, session_actor, PERMISSION)
                repository = self._repository(connection)
                prior = repository.by_request(key)
                if prior is None:
                    raise
                return self._created_replay(repository, prior, digest, actor)

    def _created_replay(self, repository, prior, digest, actor):
        result = self._same_request(prior, digest, actor)
        result["entries"] = [{"entry_id": str(e["id"]), "code": entry_code(e["trip_id"], e["entry_no"]), "kind": e["entry_kind"]}
                             for e in repository.entries(prior["trip_id"])]
        return result

    def _post_create(self, session_actor, amount, grams, selected, paid_on, notes_text, key, digest):
        with self._transaction() as connection:
            actor = self._authorization.require_permission_for_write(connection, session_actor, PERMISSION)
            repository = self._repository(connection)
            prior = repository.by_request(key)
            if prior is not None:
                return self._created_replay(repository, prior, digest, actor)
            trip_id = repository.insert_trip(notes=notes_text, actor=actor, now=repository.db_now())      # the new trip: locked by this transaction
            has_gold = amount > 0 or bool(grams)
            account = karats = None
            # Resource order: new trip -> existing bars (ascending id, shop availability checked) -> cash account -> Scrap per karat
            # ascending. A bar is locked before any financial account or Scrap row, so a refused bar never holds cash / Scrap locks, and the
            # order agrees with every other path that takes a bar after the trip (whole-lot settlement, lot return, reissue).
            lot_grams = self._plan_lot_issue(repository, selected) if selected else {}
            if has_gold:
                account, karats = self._issue_resources(connection, repository, ISSUE, trip_id, amount, grams)
            entries = []
            if has_gold:
                entry_id, code = self._write_entry(repository, kind=ISSUE, trip_id=trip_id, paid_on=paid_on, notes_text=notes_text, key=key,
                                                   digest=digest, actor=actor, amount=amount, grams=grams, account=account, karats=karats,
                                                   created=True)
                entries.append({"entry_id": str(entry_id), "code": code, "kind": ISSUE})
            if lot_grams:
                bar_key = key if not has_gold else str(uuid5(UUID(key), "market-trip-create-lots"))
                entry_id, code = self._write_lot_issue(repository, trip_id=trip_id, grams=lot_grams, issued_on=paid_on, notes=notes_text,
                                                       key=bar_key, digest=digest, actor=actor)
                entries.append({"entry_id": str(entry_id), "code": code, "kind": LOT_ISSUE})
                if not has_gold:
                    repository.insert_event(trip_id=trip_id, event_type="OPENED", reason=None, actor=actor, now=repository.db_now())
            if not self._session.is_current(session_actor):
                raise PermissionError("انتهت جلسة الدخول.")
        first = entries[0]
        return {"entry_id": first["entry_id"], "trip_id": str(trip_id), "trip_code": trip_code(trip_id), "code": first["code"],
                "kind": first["kind"], "created": True, "duplicate": False, "entries": entries}

    def _post_lot_issue(self, session_actor, trip_id, selected, issued_on, notes, key, digest):
        with self._transaction() as connection:
            actor = self._authorization.require_permission_for_write(connection, session_actor, PERMISSION)
            repository = self._repository(connection)
            trip, replay = self._open_trip(repository, trip_id, key, digest, actor)      # destination trip lock first
            if replay is not None:
                return replay
            grams = self._plan_lot_issue(repository, selected)
            entry_id, code = self._write_lot_issue(repository, trip_id=trip_id, grams=grams, issued_on=issued_on, notes=notes, key=key,
                                                   digest=digest, actor=actor)
            if not self._session.is_current(session_actor):
                raise PermissionError("انتهت جلسة الدخول.")
        return {"entry_id": str(entry_id), "trip_id": str(trip_id), "trip_code": trip_code(trip_id), "code": code, "kind": LOT_ISSUE,
                "created": False, "duplicate": False}

    # ------------------------------------------------------------------ settlement (060): the melt recorded AFTER the owner returns from the market
    @staticmethod
    def _settle_melt_plan(lines):
        """{karat_id: grams} of the gold the owner melted (a karat twice is summed; a zero row is ignored). Nothing is rounded."""
        grams = {}
        for line in lines or []:
            try:
                karat_id = int(str(line.get("karat_id") if isinstance(line, dict) else line[0]).strip())
            except (TypeError, ValueError, AttributeError, IndexError):
                raise TripError("KARAT_REQUIRED", "اختر عيار كل سطر من الذهب المسبوك.") from None
            weight = parse_weight(line.get("weight") if isinstance(line, dict) else line[1])
            if weight == 0:
                continue
            grams[karat_id] = grams.get(karat_id, ZERO_WEIGHT) + weight
        if not grams:
            raise TripError("MELT_INPUT_REQUIRED", "أدخل وزنًا موجبًا واحدًا على الأقل من الذهب المسبوك.")
        return grams

    @staticmethod
    def _settle_melt_check(repository, custody, grams):
        """The active karats of the melted lines and the melted grams checked against the UNCLASSIFIED custody of each karat. `custody` is read by the CALLER: the locking
        form under the trip lock for the posting, the display form for the preview."""
        karats = {}
        for karat_id in sorted(grams):
            karat = repository.karat(karat_id)
            if karat is None or not karat["is_active"]:
                raise TripError("KARAT_NOT_FOUND", "العيار غير موجود أو غير نشط.")
            karats[karat_id] = karat
        by_key = {(g["karat_id"], g["designation"]): g["outstanding"] for g in custody["gold"]}
        for karat_id in sorted(grams):
            available = by_key.get((karat_id, UNCLASSIFIED), ZERO_WEIGHT)
            if grams[karat_id] > available:
                raise TripError("SETTLE_MELT_EXCEEDS_CUSTODY",
                                f"المتاح للسبك في العهدة من عيار {karats[karat_id]['karat_value']} هو {available:,.3f} جرام فقط (الذهب الذي خرج بدون وجهة).")
        return karats

    def preview_settle_melt(self, trip_id, lines, output_weight, fineness, *, _expected_actor=None):
        """READ ONLY: the exact figures of a melt before it is recorded (theoretical / actual Eq21, weight loss, fineness loss). The same validation as the posting; raises
        the posting's refusal when the facts are invalid or exceed the custody. Nothing is locked or written."""
        actor = self._actor(_expected_actor)
        if str(trip_id or "").strip() == "":
            raise TripError("TRIP_NOT_FOUND", "اختر العهدة.")
        trip_number = _trip_id(trip_id)
        grams = self._settle_melt_plan(lines)
        weight = parse_weight(output_weight)
        if weight == 0:
            raise TripError("OUTPUT_WEIGHT_REQUIRED", "وزن السبك الفعلي مطلوب بقيمة موجبة.")
        permille = settlement_rules.parse_fineness(fineness)
        with self._transaction() as connection:
            self._authorization.require_permission_read(connection, actor, PERMISSION)
            repository = self._repository(connection)
            if repository.trip(trip_number) is None:
                raise TripError("TRIP_NOT_FOUND", "العهدة غير موجودة.")
            karats = self._settle_melt_check(repository, self._custody(repository, trip_number), grams)
        return settlement_rules.melt_figures([(int(karats[k]["karat_value"]), w) for k, w in sorted(grams.items())], weight, permille)

    def settle_melt(self, trip_id, lines, output_weight, fineness, workshop, entry_date, notes, request_key, *, _expected_actor=None):
        """Record a melt of gold that left WITHOUT a destination (UNCLASSIFIED custody) of an OPEN legacy trip: the melted grams leave the RAW pool of their karats and the
        bar enters the BAR pool by its 21K equivalent. The bar's weight and integer fineness are kept in the document for the reports; the loss is split in weight loss and
        fineness loss (negative = gain). No cash, Scrap, supplier or lot movement."""
        session_actor = self._actor(_expected_actor)
        if str(trip_id or "").strip() == "":
            raise TripError("TRIP_NOT_FOUND", "اختر العهدة.")
        trip_number = _trip_id(trip_id)
        grams = self._settle_melt_plan(lines)
        weight = parse_weight(output_weight)
        if weight == 0:
            raise TripError("OUTPUT_WEIGHT_REQUIRED", "وزن السبك الفعلي مطلوب بقيمة موجبة.")
        permille = settlement_rules.parse_fineness(fineness)
        melted_on = _entry_date(entry_date)
        workshop_text = _text(workshop, WORKSHOP_MAX, "اسم الورشة أو المسؤول", required=True)
        notes_text, key = _text(notes, NOTES_MAX, "الملاحظات"), _key(request_key)
        digest = hashlib.sha256(json.dumps(
            [ST_MELT, trip_number, sorted((k, str(w)) for k, w in grams.items()), str(weight), permille, melted_on.isoformat(), workshop_text, notes_text],
            ensure_ascii=False).encode()).hexdigest()
        try:
            return self._post_settle_melt(session_actor, trip_number, grams, weight, permille, melted_on, workshop_text, notes_text, key, digest)
        except IntegrityError as exc:
            if not _is_request_key_violation(exc):
                raise
            return self._replay(session_actor, key, digest, exc)

    def _post_settle_melt(self, session_actor, trip_id, grams, weight, permille, melted_on, workshop, notes, key, digest):
        with self._transaction() as connection:
            actor = self._authorization.require_permission_for_write(connection, session_actor, PERMISSION)
            repository = self._repository(connection)
            trip, replay = self._open_trip(repository, trip_id, key, digest, actor)
            if replay is not None:
                return replay
            if not repository.has_settlement_schema():
                raise TripError("SCHEMA_OUTDATED", "قاعدة البيانات تحتاج التحديث رقم 60 قبل تسجيل السبك بعد الرجوع.")
            karats = self._settle_melt_check(repository, self._custody(repository, trip_id, current=True), grams)     # read under the trip lock
            figures = settlement_rules.melt_figures([(int(karats[k]["karat_value"]), w) for k, w in sorted(grams.items())], weight, permille)
            if figures["actual_eq21"] <= 0:
                raise TripError("MELT_RESULT_TOO_SMALL", "مكافئ السبيكة بعيار 21 يساوي صفرًا: راجع وزن السبيكة وعيارها.")
            now = repository.db_now()
            entry_no = repository.next_entry_no(trip_id)
            entry_id = repository.insert_entry(trip_id=trip_id, entry_no=entry_no, kind=ST_MELT, entry_date=melted_on, notes=notes, key=key, digest=digest, actor=actor, now=now)
            code = entry_code(trip_id, entry_no)
            repository.insert_settle_melt(entry_id=entry_id, workshop=workshop, melt_date=melted_on, figures=figures)
            for karat_id, grams_in in sorted(grams.items()):
                repository.insert_settle_melt_input(entry_id=entry_id, karat_id=karat_id, weight=grams_in)
                repository.insert_settle_move(entry_id=entry_id, trip_id=trip_id, kind=ST_MELT, pool="RAW", karat_id=karat_id, delta=-grams_in)
            repository.insert_settle_move(entry_id=entry_id, trip_id=trip_id, kind=ST_MELT, pool="BAR", karat_id=None, delta=figures["actual_eq21"])
            repository.audit(actor=actor, event_type="MARKET_TRIP_ST_MELT", entity_id=entry_id, reference=code,
                             description=f"{KIND_TEXT[ST_MELT]} {code} — {trip_code(trip_id)}",
                             metadata={"trip_id": trip_id, "kind": ST_MELT, "date": melted_on.isoformat(), "workshop": workshop,
                                       "inputs": {str(karats[k]["karat_value"]): str(w) for k, w in grams.items()},
                                       "figures": {k: str(v) for k, v in figures.items()}, "employee_id": actor.employee_id}, now=now)
            if not self._session.is_current(session_actor):
                raise PermissionError("انتهت جلسة الدخول.")
        return {"entry_id": str(entry_id), "trip_id": str(trip_id), "trip_code": trip_code(trip_id), "code": code, "kind": ST_MELT, "created": False,
                "duplicate": False, "figures": {k: str(v) for k, v in figures.items()}}

    def settle_supplier_bar(self, trip_id, supplier_id, weight, settlement_date, reference, notes, request_key, *, _expected_actor=None):
        """DELIVER part of the melted bar (BAR pool, 21K-equivalent grams) to a supplier against its gold account (settlement step 2b). One transaction posts the ST_BAR_SUP
        custody entry, the immutable SG document (source TRIP_BAR: the delivered amount is `weight` grams of the 21K master karat), the supplier's negative gold movement in
        ITS dedicated karat (credit = weight x 21 / supplier karat, derived once like 045/046), its GOLD leg, the entry <-> SG link, the BAR move and both audit rows. No Scrap,
        cash, making, bank or lot movement. Capped at the BAR pool; an inactive supplier and a crossing into supplier credit are allowed."""
        session_actor = self._actor(_expected_actor)
        if GOLD_SETTLE_PERMISSION not in session_actor.permissions:
            raise PermissionError("تسوية ذهب الموردين غير متاحة لك.")
        if str(trip_id or "").strip() == "":
            raise TripError("TRIP_NOT_FOUND", "اختر العهدة.")
        trip_number = _trip_id(trip_id)
        try:
            supplier_number = int(str(supplier_id).strip())
        except (TypeError, ValueError):
            raise TripError("SUPPLIER_NOT_FOUND", "اختر المورد.") from None
        if supplier_number <= 0:
            raise TripError("SUPPLIER_NOT_FOUND", "اختر المورد.")
        grams = parse_weight(weight)
        if grams == 0:
            raise TripError("WEIGHT_REQUIRED", "أدخل وزن السبيكة المُسلَّم (مكافئ عيار 21) بقيمة موجبة.")
        settled_on = _entry_date(settlement_date)
        reference_text, notes_text = _text(reference, PAYMENT_REFERENCE_MAX, "المرجع"), _text(notes, NOTES_MAX, "الملاحظات")
        key = _key(request_key)
        digest = hashlib.sha256(json.dumps([ST_BAR_SUP, trip_number, supplier_number, str(grams), settled_on.isoformat(), reference_text, notes_text],
                                           ensure_ascii=False).encode()).hexdigest()
        try:
            return self._post_bar_settle(session_actor, trip_number, supplier_number, grams, settled_on, reference_text, notes_text, key, digest)
        except IntegrityError as exc:
            if getattr(exc, "errno", None) != 1062 or not ("uq_sgs_request" in str(exc) or "uq_mte_request" in str(exc)):
                raise
            with self._transaction() as connection:                       # a twin committed first: decide from a FRESH read
                actor = self._authorization.require_permission_for_write(connection, session_actor, PERMISSION)
                replay = self._bar_settle_prior(self._repository(connection), self._gold_settlements(connection), key, digest, actor)
                if replay is None:
                    raise
                return replay

    @staticmethod
    def _bar_settle_result(entry, link, duplicate):
        return {"entry_id": str(entry["id"]), "trip_id": str(entry["trip_id"]), "trip_code": trip_code(entry["trip_id"]),
                "code": entry_code(entry["trip_id"], entry["entry_no"]), "kind": ST_BAR_SUP, "created": False, "duplicate": duplicate,
                "settlement_id": str(link["settlement_id"]), "settlement_code": settlement_code(link["settlement_id"]), "supplier_id": str(link["supplier_id"]),
                "delivered_weight": str(link["weight_grams"]), "target_karat": int(link["target_karat_value"]), "credited_weight": str(link["credited_weight_grams"]),
                "analytical": str(link["credited_analytical_grams"]), "rounding_difference": str(link["rounding_difference_grams"])}

    @classmethod
    def _bar_settle_prior(cls, repository, settlements, key, digest, actor):
        """The replay of an already-recorded request (None when the key is unused): the entry AND the SG document must exist, be linked to each other and carry the SAME
        payload by the SAME user; anything else is a conflict and writes nothing."""
        entry, header = repository.by_request(key), settlements.by_request(key)
        if entry is None and header is None:
            return None
        link = None if entry is None else repository.entry_bar_supplier(entry["id"])
        if (entry is None or header is None or link is None or int(link["settlement_id"]) != int(header["id"])
                or entry["request_hash"] != digest or header["request_hash"] != digest or int(entry["performed_by_user_id"]) != int(actor.user_id)):
            raise TripError("REQUEST_CONFLICT", "طلب العملية مستخدم لعملية مختلفة.")
        return cls._bar_settle_result(entry, link, True)

    def _post_bar_settle(self, session_actor, trip_id, supplier_id, grams, settled_on, reference, notes, key, digest):
        with self._transaction() as connection:
            self._authorization.require_permission_for_write(connection, session_actor, PERMISSION)
            actor = self._authorization.require_permission_for_write(connection, session_actor, GOLD_SETTLE_PERMISSION)
            repository, settlements = self._repository(connection), self._gold_settlements(connection)
            trip = repository.trip(trip_id, lock=True)                           # lock order: trip -> supplier
            if trip is None:
                raise TripError("TRIP_NOT_FOUND", "العهدة غير موجودة.")
            replay = self._bar_settle_prior(repository, settlements, key, digest, actor)
            if replay is not None:
                return replay
            book_rules.require_legacy_trip(trip)
            if trip["status"] != OPEN:
                raise TripError("TRIP_CLOSED", "العهدة مغلقة: أعد فتحها أولاً لتسليم السبيكة لمورد.")
            if not repository.has_distribution_schema():
                raise TripError("SCHEMA_OUTDATED", "قاعدة البيانات تحتاج التحديث رقم 61 قبل تسليم السبيكة لمورد.")
            supplier = settlements.get_supplier(supplier_id, lock=True)         # an INACTIVE supplier may still receive it
            if supplier is None:
                raise TripError("SUPPLIER_NOT_FOUND", "المورد غير موجود.")
            if not rules.has_supplier_role(supplier):
                raise TripError("SUPPLIER_ROLE_REQUIRED", "هذا الطرف مسوقجي فقط وليس مورِّدًا: لا حساب ذهب له لتسوية الذهب.")
            if supplier["karat_id"] is None:
                raise TripError("SUPPLIER_KARAT_MISSING", "عيار هذا المورد غير محدد. حدّد عياره من تعديل المورد قبل تسوية الذهب له.")
            base = next((k for k in repository.active_karats() if int(k["karat_value"]) == BASE_KARAT_VALUE), None)
            if base is None:
                raise TripError("KARAT_NOT_FOUND", "عيار 21 غير موجود أو غير نشط.")
            delivered = settlements.get_karat(base["id"])
            target = settlements.get_karat(supplier["karat_id"])
            pool = self._custody(repository, trip_id, current=True)["bar"]       # read under the trip lock
            if grams > pool:
                raise TripError("BAR_EXCEEDS_POOL", f"رصيد السبيكة في العهدة هو {pool:,.3f} جرام (مكافئ عيار 21) فقط: لا يمكن تسليم {grams:,.3f} جرام.")
            credited, analytical, difference = rules.settlement_credit(grams, delivered["karat_value"], target["karat_value"])
            type_id = settlements.gold_type_id(GOLD_MOVEMENT)
            if type_id is None:
                raise RuntimeError("supplier gold movement type SETTLEMENT is missing (migration 045)")
            now = repository.db_now()
            settlement_id = settlements.insert_header(
                supplier_id=supplier_id, delivered_karat=delivered, target_karat=target, delivered_weight=grams, credited=credited, analytical=analytical,
                difference=difference, settlement_date=settled_on, reference=reference, notes=notes, key=key, digest=digest, actor=actor, now=now,
                source_kind=BAR_SOURCE_KIND)
            sg_code = settlement_code(settlement_id)
            entry_no = repository.next_entry_no(trip_id)
            entry_id = repository.insert_entry(trip_id=trip_id, entry_no=entry_no, kind=ST_BAR_SUP, entry_date=settled_on, notes=notes, key=key, digest=digest,
                                               actor=actor, now=now)
            code = entry_code(trip_id, entry_no)
            label = (f"تسليم سبيكة للمورد {sg_code} من عهدة السوق {code}: سُلِّم {grams:,.3f} جرام (مكافئ عيار 21) ← احتُسب {credited:,.3f} جرام "
                     f"عيار {int(target['karat_value'])}")
            gold_id = settlements.insert_gold_movement(
                supplier_id=supplier_id, type_id=type_id, karat_id=target["id"], weight_delta=-credited,
                eq21_delta=-rules.equivalent_21(credited, target["karat_value"]), source_type="SUPPLIER_GOLD_SETTLEMENT", source_id=settlement_id,
                reference=sg_code, description=label, now=now, user_id=actor.user_id)
            settlements.insert_leg(settlement_id=settlement_id, kind="GOLD", source_kind=BAR_SOURCE_KIND, supplier_gold_movement_id=gold_id)
            repository.insert_bar_supplier_link(entry_id=entry_id, trip_id=trip_id, settlement_id=settlement_id, supplier_id=supplier_id, weight=grams)
            repository.insert_settle_move(entry_id=entry_id, trip_id=trip_id, kind=ST_BAR_SUP, pool="BAR", karat_id=None, delta=-grams)
            settlements.audit(actor=actor, event_type="SUPPLIER_GOLD_SETTLEMENT", entity_id=settlement_id, entity_type="SUPPLIER_GOLD_SETTLEMENT",
                              reference=sg_code, description=label,
                              metadata={"supplier_id": supplier_id, "source": BAR_SOURCE_KIND, "custody_entry": code, "delivered_karat": BASE_KARAT_VALUE,
                                        "delivered_weight": str(grams), "target_karat": int(target["karat_value"]), "credited": str(credited),
                                        "analytical": str(analytical), "rounding_difference": str(difference), "settlement_date": settled_on.isoformat(),
                                        "reference": reference, "employee_id": actor.employee_id}, now=now)
            repository.audit(actor=actor, event_type="MARKET_TRIP_ST_BAR_SUP", entity_id=entry_id, reference=code,
                             description=f"{KIND_TEXT[ST_BAR_SUP]} {code} — {trip_code(trip_id)} — {sg_code}",
                             metadata={"trip_id": trip_id, "kind": ST_BAR_SUP, "supplier_id": supplier_id, "settlement": sg_code, "delivered_weight": str(grams),
                                       "credited": str(credited), "date": settled_on.isoformat(), "employee_id": actor.employee_id}, now=now)
            if not self._session.is_current(session_actor):
                raise PermissionError("انتهت جلسة الدخول.")
        return {"entry_id": str(entry_id), "trip_id": str(trip_id), "trip_code": trip_code(trip_id), "code": code, "kind": ST_BAR_SUP, "created": False,
                "duplicate": False, "settlement_id": str(settlement_id), "settlement_code": sg_code, "supplier_id": str(supplier_id), "delivered_weight": str(grams),
                "target_karat": int(target["karat_value"]), "credited_weight": str(credited), "analytical": str(analytical), "rounding_difference": str(difference)}

    def classify_remaining_as_logo(self, trip_id, entry_date, notes, request_key, *, _expected_actor=None):
        """Classify EVERY remaining gram that left WITHOUT a destination and was not melted as LOGO of its own karat (the owner rule: what was not melted is logo). The grams are
        derived under the trip lock from the custody (never typed); after it every LOGO flow applies (heating, supplier settlement, marketer hand-over, sale, return) unchanged.
        Nothing but the settlement ledger moves: no Scrap, cash, supplier or lot movement. Reversible only through the ordinary full reversal."""
        session_actor = self._actor(_expected_actor)
        if str(trip_id or "").strip() == "":
            raise TripError("TRIP_NOT_FOUND", "اختر العهدة.")
        trip_number = _trip_id(trip_id)
        on_date, notes_text, key = _entry_date(entry_date), _text(notes, NOTES_MAX, "الملاحظات"), _key(request_key)
        digest = hashlib.sha256(json.dumps([ST_LOGO, trip_number, on_date.isoformat(), notes_text], ensure_ascii=False).encode()).hexdigest()
        try:
            return self._post_classify_logo(session_actor, trip_number, on_date, notes_text, key, digest)
        except IntegrityError as exc:
            if not _is_request_key_violation(exc):
                raise
            return self._replay(session_actor, key, digest, exc)

    def _post_classify_logo(self, session_actor, trip_id, on_date, notes, key, digest):
        with self._transaction() as connection:
            actor = self._authorization.require_permission_for_write(connection, session_actor, PERMISSION)
            repository = self._repository(connection)
            trip, replay = self._open_trip(repository, trip_id, key, digest, actor)
            if replay is not None:
                return replay
            if not repository.has_settlement_schema():
                raise TripError("SCHEMA_OUTDATED", "قاعدة البيانات تحتاج التحديث رقم 62 قبل اعتبار المتبقي لوجو.")
            custody = self._custody(repository, trip_id, current=True)                         # read under the trip lock
            lines = [(g["karat_id"], g["karat_value"], g["outstanding"]) for g in custody["gold"] if g["designation"] == UNCLASSIFIED and g["outstanding"] > 0]
            if not lines:
                raise TripError("NOTHING_TO_CLASSIFY", "لا يوجد ذهب خرج بدون وجهة ولم يُسبك أو يُسلَّم: لا شيء يُعتبر لوجو.")
            now = repository.db_now()
            entry_no = repository.next_entry_no(trip_id)
            entry_id = repository.insert_entry(trip_id=trip_id, entry_no=entry_no, kind=ST_LOGO, entry_date=on_date, notes=notes, key=key, digest=digest, actor=actor, now=now)
            code = entry_code(trip_id, entry_no)
            for karat_id, _, grams_left in sorted(lines):
                repository.insert_settle_move(entry_id=entry_id, trip_id=trip_id, kind=ST_LOGO, pool="RAW", karat_id=karat_id, delta=-grams_left)
                repository.insert_settle_move(entry_id=entry_id, trip_id=trip_id, kind=ST_LOGO, pool="LOGO", karat_id=karat_id, delta=grams_left)
            repository.audit(actor=actor, event_type="MARKET_TRIP_ST_LOGO", entity_id=entry_id, reference=code,
                             description=f"{KIND_TEXT[ST_LOGO]} {code} — {trip_code(trip_id)}: " + "، ".join(f"عيار {v} {g:,.3f} جرام" for _, v, g in sorted(lines)),
                             metadata={"trip_id": trip_id, "kind": ST_LOGO, "date": on_date.isoformat(), "lines": {str(v): str(g) for _, v, g in lines},
                                       "employee_id": actor.employee_id}, now=now)
            if not self._session.is_current(session_actor):
                raise PermissionError("انتهت جلسة الدخول.")
        return {"entry_id": str(entry_id), "trip_id": str(trip_id), "trip_code": trip_code(trip_id), "code": code, "kind": ST_LOGO, "created": False, "duplicate": False,
                "lines": [{"karat": int(v), "grams": str(g)} for _, v, g in sorted(lines)]}

    def settle_bar_to_marketer(self, trip_id, party_id, weight, entry_date, notes, request_key, *, _expected_actor=None):
        """HAND part of the melted bar (BAR pool, 21K-equivalent grams) to a marketer (settlement step 2c): the marketer's 21K entitlement (055 account) gains exactly the grams,
        the BAR pool loses them. The gold stays SHOP-OWNED at the marketer (a trust entitlement, not a sale): what he does next (settles a supplier from it, returns it) is the
        existing 055 flow. One transaction posts the ST_BAR_MKT custody entry, the marketer's TRIP_BOOK_IN movement tied to it, the negative BAR move and the audit row; no Scrap,
        cash, supplier or lot movement. Capped at the BAR pool; the marketer must be an ACTIVE intermediary-role party. Rights: MARKET_TRIP_MANAGE + INTERMEDIARY_HOLDING_MANAGE."""
        session_actor = self._actor(_expected_actor)
        if MANAGE_HOLDINGS not in session_actor.permissions:
            raise PermissionError("تسليم الذهب للمسوقجي غير متاح لك.")
        if str(trip_id or "").strip() == "":
            raise TripError("TRIP_NOT_FOUND", "اختر العهدة.")
        trip_number = _trip_id(trip_id)
        try:
            party_number = int(str(party_id).strip())
        except (TypeError, ValueError):
            raise TripError("MARKETER_NOT_FOUND", "اختر مسوقجيًا مسجلًا.") from None
        if party_number <= 0:
            raise TripError("MARKETER_NOT_FOUND", "اختر مسوقجيًا مسجلًا.")
        grams = parse_weight(weight)
        if grams == 0:
            raise TripError("WEIGHT_REQUIRED", "أدخل وزن السبيكة المُسلَّم (مكافئ عيار 21) بقيمة موجبة.")
        given_on, notes_text, key = _entry_date(entry_date), _text(notes, NOTES_MAX, "الملاحظات"), _key(request_key)
        digest = hashlib.sha256(json.dumps([ST_BAR_MKT, trip_number, party_number, str(grams), given_on.isoformat(), notes_text], ensure_ascii=False).encode()).hexdigest()
        try:
            return self._post_bar_marketer(session_actor, trip_number, party_number, grams, given_on, notes_text, key, digest)
        except IntegrityError as exc:
            if not _is_request_key_violation(exc):
                raise
            return self._replay(session_actor, key, digest, exc)

    def _post_bar_marketer(self, session_actor, trip_id, party_id, grams, given_on, notes, key, digest):
        from repositories.marketer_custody_repository import MarketerCustodyRepository
        with self._transaction() as connection:
            self._authorization.require_permission_for_write(connection, session_actor, PERMISSION)
            actor = self._authorization.require_permission_for_write(connection, session_actor, MANAGE_HOLDINGS)
            repository = self._repository(connection)
            trip, replay = self._open_trip(repository, trip_id, key, digest, actor)           # trip lock first, then the replay, then the open check
            if replay is not None:
                return replay
            marketers = MarketerCustodyRepository(connection)
            party = marketers.party(party_id, lock=True)                                       # trip -> the marketer account lock
            if party is None or not party["is_intermediary"]:
                raise TripError("MARKETER_NOT_FOUND", "اختر مسوقجيًا مسجلًا.")
            if not party["is_active"]:
                raise TripError("MARKETER_INACTIVE", "هذا المسوقجي غير نشط: لا يُسلَّم له ذهب جديد.")
            pool = self._custody(repository, trip_id, current=True)["bar"]                     # read under the trip lock
            if grams > pool:
                raise TripError("BAR_EXCEEDS_POOL", f"رصيد السبيكة في العهدة هو {pool:,.3f} جرام (مكافئ عيار 21) فقط: لا يمكن تسليم {grams:,.3f} جرام.")
            now = repository.db_now()
            entry_no = repository.next_entry_no(trip_id)
            entry_id = repository.insert_entry(trip_id=trip_id, entry_no=entry_no, kind=ST_BAR_MKT, entry_date=given_on, notes=notes, key=key, digest=digest, actor=actor, now=now)
            code = entry_code(trip_id, entry_no)
            marketers.insert_gold_movement(party_id=party_id, movement_type="TRIP_BOOK_IN", weight=grams, delta=grams, trip_entry_id=entry_id, trip_entry_kind=ST_BAR_MKT)
            repository.insert_settle_move(entry_id=entry_id, trip_id=trip_id, kind=ST_BAR_MKT, pool="BAR", karat_id=None, delta=-grams)
            repository.audit(actor=actor, event_type="MARKET_TRIP_ST_BAR_MKT", entity_id=entry_id, reference=code,
                             description=f"{KIND_TEXT[ST_BAR_MKT]} {code} — {trip_code(trip_id)} — {party['name']}: {grams:,.3f} جرام (مكافئ عيار 21)",
                             metadata={"trip_id": trip_id, "kind": ST_BAR_MKT, "party_id": party_id, "grams": str(grams), "date": given_on.isoformat(),
                                       "employee_id": actor.employee_id}, now=now)
            if not self._session.is_current(session_actor):
                raise PermissionError("انتهت جلسة الدخول.")
        return {"entry_id": str(entry_id), "trip_id": str(trip_id), "trip_code": trip_code(trip_id), "code": code, "kind": ST_BAR_MKT, "created": False, "duplicate": False,
                "party_id": str(party_id), "party_name": party["name"], "marketer_grams": str(grams)}

    # ------------------------------------------------------------------ closure and reopening
    def close(self, trip_id, *, _expected_actor=None):
        """Close only when cash, every (karat, designation) and every assayed lot are exactly zero. Never adjusts anything."""
        session_actor = self._actor(_expected_actor)
        trip_id = _trip_id(trip_id)
        with self._transaction() as connection:
            actor = self._authorization.require_permission_for_write(connection, session_actor, PERMISSION)
            repository = self._repository(connection)
            trip = repository.trip(trip_id, lock=True)
            if trip is None:
                raise TripError("TRIP_NOT_FOUND", "العهدة غير موجودة.")
            if trip["status"] != OPEN:
                raise TripError("TRIP_ALREADY_CLOSED", "العهدة مغلقة بالفعل.")
            closure = None
            if book_rules.trip_mode(trip) == book_rules.BOOK:
                closure = self._book_closure(repository, trip)                       # the BOOK rules (MarketTripBookService); never a zero-balance shortcut
            else:
                custody = self._custody(repository, trip_id, current=True)
                if not custody["settled"]:
                    raise TripError("OUTSTANDING_CUSTODY", self.outstanding_text(custody))
            now = repository.db_now()
            repository.set_status(trip_id=trip_id, status=CLOSED, now=now)
            repository.insert_event(trip_id=trip_id, event_type="CLOSED", reason=None, actor=actor, now=now)
            repository.audit(actor=actor, event_type="MARKET_TRIP_CLOSED", entity_id=trip_id, reference=trip_code(trip_id),
                             description=f"إغلاق العهدة {trip_code(trip_id)}", metadata={"trip_id": trip_id, **(closure or {})}, now=now)
            if not self._session.is_current(session_actor):
                raise PermissionError("انتهت جلسة الدخول.")
        return {"trip_id": str(trip_id), "trip_code": trip_code(trip_id), "status": CLOSED}

    def _book_closure(self, repository, trip):
        """The closure rules of a BOOK trip live in MarketTripBookService; this service alone never closes one."""
        raise TripError("BOOK_CLOSURE_NOT_AVAILABLE", "إغلاق العهدة الدفترية غير متاح في هذه الخدمة.")

    @staticmethod
    def outstanding_text(custody):
        return outstanding_text(custody)

    def reopen(self, trip_id, reason, *, _expected_actor=None):
        """OWNER reopens a CLOSED trip (reason mandatory, audited). Nothing else changes."""
        session_actor = self._actor(_expected_actor)
        trip_id = _trip_id(trip_id)
        reason = _text(reason, REASON_MAX, "سبب إعادة الفتح", required=True)
        with self._transaction() as connection:
            actor = self._authorization.require_permission_for_write(connection, session_actor, PERMISSION)
            repository = self._repository(connection)
            trip = repository.trip(trip_id, lock=True)
            if trip is None:
                raise TripError("TRIP_NOT_FOUND", "العهدة غير موجودة.")
            if trip["status"] != CLOSED:
                raise TripError("TRIP_NOT_CLOSED", "العهدة مفتوحة بالفعل.")
            now = repository.db_now()
            repository.set_status(trip_id=trip_id, status=OPEN, now=now)
            repository.insert_event(trip_id=trip_id, event_type="REOPENED", reason=reason, actor=actor, now=now)
            repository.audit(actor=actor, event_type="MARKET_TRIP_REOPENED", entity_id=trip_id, reference=trip_code(trip_id),
                             description=f"إعادة فتح العهدة {trip_code(trip_id)}", metadata={"trip_id": trip_id, "reason": reason}, now=now)
            if not self._session.is_current(session_actor):
                raise PermissionError("انتهت جلسة الدخول.")
        return {"trip_id": str(trip_id), "trip_code": trip_code(trip_id), "status": OPEN}

    # ------------------------------------------------------------------ reversal (CorrectionService conventions)
    def reverse(self, entry_id, reason, *, _expected_actor=None):
        """Full reversal (RV) of one entry; OWNER-only here and again inside CorrectionService's transaction."""
        self._actor(_expected_actor)
        if self._corrections is None:
            from services.correction_service import CorrectionService
            self._corrections = CorrectionService(self._session)
        return self._corrections.reverse(SOURCE, str(entry_id), reason, _expected_actor=_expected_actor)
