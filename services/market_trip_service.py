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
from repositories.market_trip_repository import (CLOSED, CREATE_MOVEMENT, GOLD_SETTLE, LOT_SETTLE, LOT_SETTLE_MOVEMENT, HEAT, ISSUE, LOGO_DESIGNATION, SUPPLIER_PAY, LOT_ISSUE, LOT_RETURN, MAIN_LOT, MELT,
                                                 MELT_DESIGNATION, OPEN, RESIDUE_LOT, RETURN, SHOP_ISSUE_MOVEMENT, SHOP_ISSUE_OUT,
                                                 SHOP_RETURN_IN, SHOP_RETURN_MOVEMENT, SOURCE, UNCLASSIFIED, MarketTripRepository,
                                                 entry_code, trip_code)
from repositories.intermediary_holding_repository import IntermediaryHoldingRepository, holding_code
from repositories.old_gold_conversion_repository import OldGoldConversionRepository
from repositories.supplier_gold_settlement_repository import (CUSTODY_SOURCE_KIND, LOT_SOURCE_KIND, GOLD_MOVEMENT, SupplierGoldSettlementRepository,
                                                              settlement_code)
from repositories.supplier_payment_repository import PAYMENT_MOVEMENT, SOURCE as SMP_SOURCE, SupplierPaymentRepository, payment_code
from services.authorization_service import AuthorizationService
from services.correction_rules import correction_code
from services.intermediary_holding_rules import MANAGE_PERMISSION as MANAGE_HOLDINGS, VIEW_PERMISSION as VIEW_HOLDINGS, pool_row
from services.invoice_service import invoice_transaction
from services.number_text import normalize_digits
from services.old_gold_availability import held_grams, shortage_message
from services import custody_rules, supplier_rules as rules

PERMISSION = "MARKET_TRIP_MANAGE"
SUPPLIER_PAY_PERMISSION = "SUPPLIER_MAKING_PAY"      # a supplier payment made from custody needs this right too (043)
GOLD_SETTLE_PERMISSION = "SUPPLIER_GOLD_SETTLE"      # a gold settlement made from custody needs this right too (046)
SETTLEABLE_DESIGNATIONS = (LOGO_DESIGNATION,)        # 046: only Logo gold settles a supplier (this is a scope of the slice, not a business rule)
CUSTODY_METHOD = "CUSTODY"
PAYMENT_REFERENCE_MAX = 100
MONEY_STEP, WEIGHT_STEP, FINENESS_STEP, ANALYSIS_STEP = Decimal("0.01"), Decimal("0.001"), Decimal("0.1"), Decimal("0.000001")
MAX_MONEY, MAX_WEIGHT = Decimal("100000000000000"), Decimal("1000000000")
NOTES_MAX, REASON_MAX, WORKSHOP_MAX, ASSAY_MAX, LABEL_MAX, MAX_RESIDUES = 1000, 1000, 120, 120, 120, 10
ZERO_MONEY, ZERO_WEIGHT = Decimal("0.00"), Decimal("0.000")
KIND_TEXT = {ISSUE: "إخراج عهدة", RETURN: "رجوع من العهدة", MELT: "سبك", LOT_RETURN: "رجوع ناتج السبك", LOT_ISSUE: "إخراج سبكة من المحل",
             HEAT: "حمّية اللوجو", SUPPLIER_PAY: "دفع مصنعيات من العهدة", GOLD_SETTLE: "تسوية ذهب من العهدة", LOT_SETTLE: "تسوية سبكة من العهدة",
             "LOT_HOLD": "تسليم ذهب لمسوقجي", "HOLD_RETURN": "رجوع سبكة من مسوقجي"}
DESIGNATION_TEXT = {MELT_DESIGNATION: "للسبك", LOGO_DESIGNATION: "لوجو", UNCLASSIFIED: "غير مصنف (قبل التصنيف)"}
_DESIGNATION_INPUT = {"MELT": MELT_DESIGNATION, "للسبك": MELT_DESIGNATION, "LOGO": LOGO_DESIGNATION, "لوجو": LOGO_DESIGNATION,
                      "UNCLASSIFIED": UNCLASSIFIED, "غير مصنف": UNCLASSIFIED}
LOT_RETURN_NOTE = "صفِّ السبائك المتبقية برجوع ناتج السبك إلى المحل أو بتسوية سبكة معلومة العيار من العهدة قبل الإغلاق."


class TripError(ValueError):
    """A refused trip operation. `code` is a stable machine reason; the message is Arabic, ready to show."""

    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


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


def parse_designation(value, kind):
    """The OWNER's decision on a gold line. An ISSUE must choose MELT or LOGO; a RETURN preserves the line's own designation (which
    may be UNCLASSIFIED for custody written before designations existed). Nothing is ever guessed."""
    text = str(value or "").strip()
    designation = _DESIGNATION_INPUT.get(text.upper() if text.isascii() else text)
    if designation is None:
        raise TripError("DESIGNATION_REQUIRED", "اختر وجهة الذهب لكل سطر: للسبك أو لوجو.")
    if kind == ISSUE and designation == UNCLASSIFIED:
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


def custody_view(cash_rows, gold_rows, lot_rows=(), trip_id=None):
    """Issued / returned / melted / OUTSTANDING for cash and for every (karat, designation), plus the outstanding assayed lots, from
    the repository's derived sums. A trip is settled only when all of them are exactly zero."""
    issued, returned = (Decimal(str(cash_rows[0])), Decimal(str(cash_rows[1])))
    paid = Decimal(str(cash_rows[2])) if len(cash_rows) > 2 else Decimal("0.00")          # paid to suppliers from custody (043)
    gold = []
    for r in gold_rows:
        g_issued, g_returned = Decimal(str(r["issued"])), Decimal(str(r["returned"]))
        g_melted, g_heat = Decimal(str(r.get("melted", 0) or 0)), Decimal(str(r.get("heat_delta", 0) or 0))
        g_settled = Decimal(str(r.get("supplier_settled", 0) or 0))                       # delivered to suppliers from custody (046)
        gold.append({"karat_id": int(r["karat_id"]), "karat_value": int(r["karat_value"]),
                     "designation": r.get("designation", UNCLASSIFIED), "issued": g_issued, "returned": g_returned,
                     "melted": g_melted, "heat_delta": g_heat, "supplier_settled": g_settled,
                     "outstanding": custody_rules.gold_outstanding(r)})
    lots = [{"lot_id": str(r["id"]), "entry_id": str(r["entry_id"]), "lot_no": int(r["lot_no"]), "kind": r["lot_kind"],
             "label": r.get("label") or "", "weight": Decimal(str(r["weight_grams"])),
             "fineness": None if r.get("fineness_permille") is None else Decimal(str(r["fineness_permille"])),
             "pure": None if r.get("pure_grams") is None else Decimal(str(r["pure_grams"])),
             "entry_no": int(r["entry_no"]), "outstanding": Decimal(str(r["outstanding_grams"])),
             "origin_trip_id": int(r.get("origin_trip_id") or trip_id or 0) or None,
             "code": None if not (r.get("origin_trip_id") or trip_id) else entry_code(r.get("origin_trip_id") or trip_id, r["entry_no"])}
            for r in lot_rows if Decimal(str(r["outstanding_grams"])) != 0]
    cash_left = custody_rules.cash_outstanding(issued, returned, paid)
    return {"cash": {"issued": issued, "returned": returned, "paid": paid, "outstanding": cash_left}, "gold": gold,
            "lots": lots,
            "settled": cash_left == 0 and all(g["outstanding"] == 0 for g in gold) and not lots}


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
    return ("لا يمكن إغلاق العهدة قبل إرجاع كل ما أُخرج منها. المتبقي في العهدة: " + "، ".join(parts) + "."
            + (" " + LOT_RETURN_NOTE if custody["lots"] else ""))


def entry_label(lot):
    return f"الحركة {lot['entry_no']}"


class MarketTripService:
    def __init__(self, session, *, transaction_factory=invoice_transaction, repository=MarketTripRepository,
                 accounts=FinancialAccountRepository, scrap=OldGoldConversionRepository, authorization=None, corrections=None,
                 supplier_payments=SupplierPaymentRepository, gold_settlements=SupplierGoldSettlementRepository,
                 holdings=IntermediaryHoldingRepository):
        self._session, self._transaction, self._repository = session, transaction_factory, repository
        self._accounts, self._scrap, self._supplier_payments = accounts, scrap, supplier_payments
        self._gold_settlements = gold_settlements
        self._holdings = holdings
        self._authorization = authorization or AuthorizationService(session)
        self._corrections = corrections

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
        """The derived custody. `current=True` is for DECISIONS made under the trip lock: the nested reversal lookups of the cash and
        gold reads are then current reads too, not the connection's REPEATABLE READ snapshot (a display read keeps the cheaper form)."""
        return custody_view(repository.custody_cash(trip_id, current=current), repository.custody_gold(trip_id, current=current),
                            repository.custody_lots(trip_id), trip_id=trip_id)

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
            intermediaries = repository.intermediaries() if {VIEW_HOLDINGS, MANAGE_HOLDINGS} <= set(actor.permissions) else []
        return {"intermediaries": [{"id": str(r["id"]), "name": r["name"], "active": bool(r["is_active"])} for r in intermediaries],
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

    def trips(self, *, _expected_actor=None):
        actor = self._actor(_expected_actor)
        with self._transaction() as connection:
            self._authorization.require_permission_read(connection, actor, PERMISSION)
            repository = self._repository(connection)
            rows = repository.trips()
            external = self._external_holdings(connection, actor)
            result = []
            for row in rows:
                result.append(self._trip_row(row, self._custody(repository, row["id"]), external.get(int(row["id"]), [])))
        return result

    def _external_holdings(self, connection, actor):
        """Sources of this trip's gold that still sit with an intermediary, per trip (only for an actor who may VIEW holdings)."""
        if VIEW_HOLDINGS not in actor.permissions:
            return {}
        grouped = {}
        for row in self._holdings(connection).open_pools():
            grouped.setdefault(int(row["trip_id"]), []).append(pool_row(row))
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
            entries, events, melts = repository.entries(trip_id), repository.events(trip_id), repository.melts(trip_id)
            heats = repository.heats(trip_id)
            external = self._external_holdings(connection, actor).get(trip_id, [])
        return {"trip": self._trip_row(trip, custody, external), "custody": custody, "external_holdings": external,
                "entries": [self._entry_row(e) for e in entries],
                "melts": [self._melt_row(m) for m in melts],
                "heats": [self._heat_row(h) for h in heats], "heat_totals": self._heat_totals(heats),
                "events": [{"type": e["event_type"], "reason": e["reason"] or "", "at": e["occurred_at"],
                            "actor": e["actor_name"] or ""} for e in events]}

    @staticmethod
    def _trip_row(row, custody, external=()):
        return {"id": str(row["id"]), "code": trip_code(row["id"]), "status": row["status"], "notes": row["notes"] or "",
                "created_at": row["created_at"], "status_changed_at": row["status_changed_at"],
                "entry_count": int(row.get("entry_count", 0) or 0), "custody": custody, "external_holdings": list(external)}

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
                "lot_settlement": None if not e.get("lot_settlement") else {
                    **MarketTripService._lot_settle_result(e, e["lot_settlement"], False),
                    "supplier_name": e["lot_settlement"]["supplier_name"]},
                "holding": None if not e.get("holding") else {
                    "document_id": str(e["holding"]["document_id"]), "code": holding_code(e["holding"]["document_id"]),
                    "kind": e["holding"]["doc_kind"], "party_id": str(e["holding"]["party_id"]), "party_name": e["holding"]["party_name"],
                    "lot_id": str(e["holding"]["lot_id"]), "lot_no": int(e["holding"]["lot_no"]), "lot_kind": e["holding"]["lot_kind"],
                    "origin": entry_code(e["holding"]["origin_trip_id"], e["holding"]["origin_entry_no"]),
                    "weight": Decimal(str(e["holding"]["weight_grams"])), "fineness": Decimal(str(e["holding"]["fineness_permille"]))},
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
            key = (karat_id, parse_designation(raw, kind))
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

    def _open_trip(self, repository, trip_number, key, digest, actor):
        """The locked OPEN trip for an entry, or the replay of an already-recorded request (returned as the second item)."""
        trip = repository.trip(trip_number, lock=True)
        if trip is None:
            raise TripError("TRIP_NOT_FOUND", "العهدة غير موجودة.")
        prior = repository.by_request(key)
        if prior is not None:
            return None, self._same_request(prior, digest, actor)
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
                trip, replay = self._open_trip(repository, trip_number, key, digest, actor)
                if replay is not None:
                    return replay
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
                            f"في هذه المرحلة تُسوّى للمورد من العهدة ذهب «لوجو» فقط؛ ذهب ({DESIGNATION_TEXT[source]}) غير متاح للتسوية.")
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
            outstanding = {int(l["lot_id"]): l for l in self._custody(repository, trip_id)["lots"]}   # read under the locks
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
            custody = self._custody(repository, trip_id, current=True)
            if not custody["settled"]:
                raise TripError("OUTSTANDING_CUSTODY", self.outstanding_text(custody))
            now = repository.db_now()
            repository.set_status(trip_id=trip_id, status=CLOSED, now=now)
            repository.insert_event(trip_id=trip_id, event_type="CLOSED", reason=None, actor=actor, now=now)
            repository.audit(actor=actor, event_type="MARKET_TRIP_CLOSED", entity_id=trip_id, reference=trip_code(trip_id),
                             description=f"إغلاق العهدة {trip_code(trip_id)}", metadata={"trip_id": trip_id}, now=now)
            if not self._session.is_current(session_actor):
                raise PermissionError("انتهت جلسة الدخول.")
        return {"trip_id": str(trip_id), "trip_code": trip_code(trip_id), "status": CLOSED}

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
