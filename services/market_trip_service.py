"""Market-trip custody: the OWNER takes cash (from MAIN_SAFE) and Scrap (existing master karat, physical grams) to the market,
decides what each gold line is for, melts designated gold into assayed lots and brings back what was not used.

  ISSUE   cash OUT of MAIN_SAFE once + counted Scrap OUT per (karat, designation)  -> the same quantities enter the trip's CUSTODY
          every new gold line carries the OWNER's designation: MELT (للسبك) or LOGO (لوجو)
  RETURN  custody leaves the trip -> cash back into MAIN_SAFE + counted Scrap IN of the SAME karat (never converted, no Eq21);
          the designation is preserved and a return never exceeds that (karat, designation) balance
  MELT    consumes MELT-designated custody (any active master karat, physical grams) and creates assayed LOTS: one MAIN output with
          its fineness (parts per thousand, 0.1) and optional RESIDUE lots whose fineness may be unknown. No cash, shop Scrap or
          supplier movement exists in a melt; the processing difference is stored permanently with the document.

Custody = issued - returned - melted over the entries that were not reversed, per (karat, designation) and for cash; none can
ever go negative. Gold written before the designation existed is UNCLASSIFIED: it can only be returned. Gold in custody is
shop-owned but unavailable inside the shop (it really left the counted Scrap balance), and master-karat custody is shown apart
from assayed lots. A trip closes only when cash, every (karat, designation) and every lot are exactly zero. Shop return of an
assayed lot, sales, supplier settlement and merchandise allocation are NOT part of this slice.

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
from uuid import UUID

from mysql.connector import IntegrityError

from repositories.financial_account_repository import FinancialAccountRepository
from repositories.market_trip_repository import (CLOSED, CREATE_MOVEMENT, ISSUE, LOGO_DESIGNATION, MAIN_LOT, MELT, MELT_DESIGNATION, OPEN,
                                                 RESIDUE_LOT, RETURN, SOURCE, UNCLASSIFIED, MarketTripRepository, entry_code,
                                                 trip_code)
from repositories.old_gold_conversion_repository import OldGoldConversionRepository
from services.authorization_service import AuthorizationService
from services.correction_rules import correction_code
from services.invoice_service import invoice_transaction
from services.number_text import normalize_digits
from services.old_gold_availability import held_grams, shortage_message

PERMISSION = "MARKET_TRIP_MANAGE"
MONEY_STEP, WEIGHT_STEP, FINENESS_STEP, ANALYSIS_STEP = Decimal("0.01"), Decimal("0.001"), Decimal("0.1"), Decimal("0.000001")
MAX_MONEY, MAX_WEIGHT = Decimal("100000000000000"), Decimal("1000000000")
NOTES_MAX, REASON_MAX, WORKSHOP_MAX, ASSAY_MAX, LABEL_MAX, MAX_RESIDUES = 1000, 1000, 120, 120, 120, 10
ZERO_MONEY, ZERO_WEIGHT = Decimal("0.00"), Decimal("0.000")
KIND_TEXT = {ISSUE: "إخراج عهدة", RETURN: "رجوع من العهدة", MELT: "سبك"}
DESIGNATION_TEXT = {MELT_DESIGNATION: "للسبك", LOGO_DESIGNATION: "لوجو", UNCLASSIFIED: "غير مصنف (قبل التصنيف)"}
_DESIGNATION_INPUT = {"MELT": MELT_DESIGNATION, "للسبك": MELT_DESIGNATION, "LOGO": LOGO_DESIGNATION, "لوجو": LOGO_DESIGNATION,
                      "UNCLASSIFIED": UNCLASSIFIED, "غير مصنف": UNCLASSIFIED}
LOT_RETURN_NOTE = "إرجاع الشحنات المعايَرة إلى المحل سيتاح في مرحلة لاحقة؛ لا يمكن إغلاق العهدة قبل ذلك."


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


def custody_view(cash_rows, gold_rows, lot_rows=()):
    """Issued / returned / melted / OUTSTANDING for cash and for every (karat, designation), plus the outstanding assayed lots, from
    the repository's derived sums. A trip is settled only when all of them are exactly zero."""
    issued, returned = (Decimal(str(cash_rows[0])), Decimal(str(cash_rows[1])))
    gold = []
    for r in gold_rows:
        g_issued, g_returned = Decimal(str(r["issued"])), Decimal(str(r["returned"]))
        g_melted = Decimal(str(r.get("melted", 0) or 0))
        gold.append({"karat_id": int(r["karat_id"]), "karat_value": int(r["karat_value"]),
                     "designation": r.get("designation", UNCLASSIFIED), "issued": g_issued, "returned": g_returned,
                     "melted": g_melted, "outstanding": g_issued - g_returned - g_melted})
    lots = [{"lot_id": str(r["id"]), "entry_id": str(r["entry_id"]), "lot_no": int(r["lot_no"]), "kind": r["lot_kind"],
             "label": r.get("label") or "", "weight": Decimal(str(r["weight_grams"])),
             "fineness": None if r.get("fineness_permille") is None else Decimal(str(r["fineness_permille"])),
             "pure": None if r.get("pure_grams") is None else Decimal(str(r["pure_grams"])),
             "entry_no": int(r["entry_no"]), "outstanding": Decimal(str(r["outstanding_grams"]))}
            for r in lot_rows if Decimal(str(r["outstanding_grams"])) != 0]
    return {"cash": {"issued": issued, "returned": returned, "outstanding": issued - returned}, "gold": gold, "lots": lots,
            "settled": issued == returned and all(g["outstanding"] == 0 for g in gold) and not lots}


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
                 accounts=FinancialAccountRepository, scrap=OldGoldConversionRepository, authorization=None, corrections=None):
        self._session, self._transaction, self._repository = session, transaction_factory, repository
        self._accounts, self._scrap = accounts, scrap
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
    def _custody(repository, trip_id):
        return custody_view(repository.custody_cash(trip_id), repository.custody_gold(trip_id), repository.custody_lots(trip_id))

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
        return {"safe": None if safe is None else {"id": str(safe["id"]), "name": safe["name"], "active": bool(safe["is_active"]),
                                                   "balance": cash},
                "karats": [{"id": int(k["id"]), "karat_value": int(k["karat_value"]), "name": k["name"]} for k in karats],
                "shop_scrap": [{"karat_id": int(r["karat_id"]), "karat_value": int(r["karat_value"]),
                                "physical": Decimal(str(r["physical_grams"])), "held": Decimal(str(r["held_grams"])),
                                "available": Decimal(str(r["available_grams"]))} for r in scrap]}

    def trips(self, *, _expected_actor=None):
        actor = self._actor(_expected_actor)
        with self._transaction() as connection:
            self._authorization.require_permission_read(connection, actor, PERMISSION)
            repository = self._repository(connection)
            rows = repository.trips()
            result = []
            for row in rows:
                result.append(self._trip_row(row, self._custody(repository, row["id"])))
        return result

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
        return {"trip": self._trip_row(trip, custody), "custody": custody,
                "entries": [self._entry_row(e) for e in entries],
                "melts": [self._melt_row(m) for m in melts],
                "events": [{"type": e["event_type"], "reason": e["reason"] or "", "at": e["occurred_at"],
                            "actor": e["actor_name"] or ""} for e in events]}

    @staticmethod
    def _trip_row(row, custody):
        return {"id": str(row["id"]), "code": trip_code(row["id"]), "status": row["status"], "notes": row["notes"] or "",
                "created_at": row["created_at"], "status_changed_at": row["status_changed_at"],
                "entry_count": int(row.get("entry_count", 0) or 0), "custody": custody}

    @staticmethod
    def _entry_row(e):
        return {"id": str(e["id"]), "trip_id": str(e["trip_id"]), "code": entry_code(e["trip_id"], e["entry_no"]),
                "entry_no": int(e["entry_no"]), "kind": e["entry_kind"], "date": e["entry_date"], "notes": e["notes"] or "",
                "at": e["occurred_at"], "actor": e["actor_name"] or "",
                "cash": None if e["cash_amount"] is None else Decimal(str(e["cash_amount"])),
                "gold": [{"karat_id": int(g["karat_id"]), "karat_value": int(g["karat_value"]),
                          "designation": g.get("designation", UNCLASSIFIED), "weight": Decimal(str(g["weight_grams"]))}
                         for g in e["gold"]],
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
    def _lines(cash, gold, kind):
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
        if amount == 0 and not grams:
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
            if not self._session.is_current(session_actor):
                raise PermissionError("انتهت جلسة الدخول.")
        return {"entry_id": str(entry_id), "trip_id": str(trip_id), "trip_code": trip_code(trip_id), "code": code, "kind": kind,
                "created": created, "duplicate": False}

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
        custody = custody_view(repository.custody_cash(trip_id), repository.custody_gold(trip_id))
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
            custody = self._custody(repository, trip_id)                    # read under the trip lock
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
            custody = self._custody(repository, trip_id)
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
