"""Market-trip custody core: the OWNER takes cash (from MAIN_SAFE) and Scrap (existing master karat, physical grams) to the
market and brings back what was not used.

  ISSUE   cash OUT of MAIN_SAFE once + counted Scrap OUT per karat   -> the same quantities enter the trip's CUSTODY
  RETURN  custody leaves the trip -> cash back into MAIN_SAFE + counted Scrap IN of the SAME karat (never converted, no Eq21)

Custody = issued - returned over the entries that were not reversed; cash and every karat are independent and can never go
negative. Gold in custody is shop-owned but unavailable inside the shop (it really left the counted Scrap balance), and it is
shown apart from the shop balances. There is NO consumption here (no sale, supplier settlement, merchandise, heating, melting,
loss or write-off): a trip closes only when cash and every karat are exactly back to zero. Closing never adjusts anything.

Right: MARKET_TRIP_MANAGE (OWNER only), re-checked inside every transaction through AuthorizationService; the actor is the
AppSession user. Entries are immutable; their only correction is a full reversal (RV) through CorrectionService, which also
needs TRANSACTION_CORRECT and refuses a closed trip or an issue that a later return depends on. OWNER may reopen a closed trip
(reason mandatory, audited).

Idempotent on the request key. Lock order: actor -> trip -> MAIN_SAFE -> Scrap karats (ascending) -> request key.
"""
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
import hashlib
import json
from uuid import UUID

from mysql.connector import IntegrityError

from repositories.financial_account_repository import FinancialAccountRepository
from repositories.market_trip_repository import (CLOSED, ISSUE, OPEN, RETURN, SOURCE, MarketTripRepository, entry_code,
                                                 trip_code)
from repositories.old_gold_conversion_repository import OldGoldConversionRepository
from services.authorization_service import AuthorizationService
from services.correction_rules import correction_code
from services.invoice_service import invoice_transaction
from services.number_text import normalize_digits
from services.old_gold_availability import held_grams, shortage_message

PERMISSION = "MARKET_TRIP_MANAGE"
MONEY_STEP, WEIGHT_STEP = Decimal("0.01"), Decimal("0.001")
MAX_MONEY, MAX_WEIGHT = Decimal("100000000000000"), Decimal("1000000000")
NOTES_MAX, REASON_MAX = 1000, 1000
ZERO_MONEY, ZERO_WEIGHT = Decimal("0.00"), Decimal("0.000")
KIND_TEXT = {ISSUE: "إخراج عهدة", RETURN: "رجوع من العهدة"}


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


def custody_view(cash_rows, gold_rows):
    """Issued / returned / OUTSTANDING for cash and for every karat, from the repository's derived sums."""
    issued, returned = (Decimal(str(cash_rows[0])), Decimal(str(cash_rows[1])))
    gold = [{"karat_id": int(r["karat_id"]), "karat_value": int(r["karat_value"]), "issued": Decimal(str(r["issued"])),
             "returned": Decimal(str(r["returned"])), "outstanding": Decimal(str(r["issued"])) - Decimal(str(r["returned"]))}
            for r in gold_rows]
    return {"cash": {"issued": issued, "returned": returned, "outstanding": issued - returned}, "gold": gold,
            "settled": issued == returned and all(g["outstanding"] == 0 for g in gold)}


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
                custody = custody_view(repository.custody_cash(row["id"]), repository.custody_gold(row["id"]))
                result.append(self._trip_row(row, custody))
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
            custody = custody_view(repository.custody_cash(trip_id), repository.custody_gold(trip_id))
            entries, events = repository.entries(trip_id), repository.events(trip_id)
        return {"trip": self._trip_row(trip, custody), "custody": custody,
                "entries": [self._entry_row(e) for e in entries],
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
                          "weight": Decimal(str(g["weight_grams"]))} for g in e["gold"]],
                "reversed": e["reversal_id"] is not None,
                "reversal_code": None if e["reversal_id"] is None else correction_code("FULL_REVERSAL", e["reversal_id"]),
                "reversal_reason": e["reversal_reason"], "reversal_at": e["reversal_at"]}

    # ------------------------------------------------------------------ entries
    @staticmethod
    def _lines(cash, gold):
        """Validated (cash, {karat_id: grams}) with at least one positive quantity; the same karat twice is summed."""
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
            grams[karat_id] = grams.get(karat_id, ZERO_WEIGHT) + weight
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
        amount, grams = self._lines(cash, gold)
        paid_on, notes_text, key = _entry_date(entry_date), _text(notes, NOTES_MAX, "الملاحظات"), _key(request_key)
        digest = hashlib.sha256(json.dumps(
            [kind, trip_number, str(amount), sorted((k, str(w)) for k, w in grams.items()), paid_on.isoformat(), notes_text],
            ensure_ascii=False).encode()).hexdigest()
        try:
            return self._post(kind, session_actor, trip_number, amount, grams, paid_on, notes_text, key, digest)
        except IntegrityError as exc:
            if not _is_request_key_violation(exc):
                raise                                              # any other integrity error is a real defect
            return self._replay(session_actor, key, digest, exc)   # a twin committed first: decide from a FRESH read

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
                trip = repository.trip(trip_number, lock=True)
                if trip is None:
                    raise TripError("TRIP_NOT_FOUND", "العهدة غير موجودة.")
                trip_id = trip_number
                prior = repository.by_request(key)
                if prior is not None:
                    return self._same_request(prior, digest, actor)
                if trip["status"] != OPEN:
                    raise TripError("TRIP_CLOSED", "العهدة مغلقة: أعد فتحها أولاً لإخراج عهدة أو تسجيل رجوع.")
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
            for karat_id in sorted(grams):
                karat = repository.karat(karat_id)
                if karat is None or not karat["is_active"]:
                    raise TripError("KARAT_NOT_FOUND", "العيار غير موجود أو غير نشط.")
                karats[karat_id] = karat
            scrap = self._scrap(connection)
            if kind == ISSUE:
                for karat_id in sorted(grams):                    # only AVAILABLE Scrap: manager-review holds are excluded
                    available = sum((Decimal(str(r["weight_delta_grams"])) for r in scrap.lock_old_gold_balance(karat_id)),
                                    ZERO_WEIGHT)
                    if grams[karat_id] > available:
                        raise TripError("SCRAP_SHORTAGE", shortage_message(
                            karats[karat_id]["karat_value"], grams[karat_id], available, held_grams(scrap, karat_id)))
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
            for karat_id in sorted(grams):
                movement = repository.post_scrap(kind=kind, karat_id=karat_id, weight=grams[karat_id], entry_id=entry_id, code=code,
                                                 notes=label, actor=actor, now=now)
                repository.insert_gold_leg(entry_id=entry_id, karat_id=karat_id, weight=grams[karat_id], scrap_movement_id=movement)
            if created:
                repository.insert_event(trip_id=trip_id, event_type="OPENED", reason=None, actor=actor, now=now)
            repository.audit(actor=actor, event_type="MARKET_TRIP_ISSUE" if kind == ISSUE else "MARKET_TRIP_RETURN",
                             entity_id=entry_id, reference=code, description=f"{label} — {trip_code(trip_id)}",
                             metadata={"trip_id": trip_id, "kind": kind, "cash": str(amount), "date": paid_on.isoformat(),
                                       "gold": {str(karats[k]["karat_value"]): str(w) for k, w in grams.items()},
                                       "employee_id": actor.employee_id}, now=now)
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
        """A return can never exceed the outstanding custody: cash and each karat on their own (read under the trip lock)."""
        custody = custody_view(repository.custody_cash(trip_id), repository.custody_gold(trip_id))
        if amount > custody["cash"]["outstanding"]:
            raise TripError("RETURN_EXCEEDS_CUSTODY",
                            f"النقدية المتبقية في العهدة {custody['cash']['outstanding']:,.2f} ج فقط.")
        outstanding = {g["karat_id"]: g["outstanding"] for g in custody["gold"]}
        for karat_id, weight in grams.items():
            left = outstanding.get(karat_id, ZERO_WEIGHT)
            if weight > left:
                raise TripError("RETURN_EXCEEDS_CUSTODY",
                                f"المتبقي في العهدة من عيار {karats[karat_id]['karat_value']} هو {left:,.3f} جرام فقط.")

    # ------------------------------------------------------------------ closure and reopening
    def close(self, trip_id, *, _expected_actor=None):
        """Close only when cash and every karat are exactly zero. Never adjusts, rounds or writes anything off."""
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
            custody = custody_view(repository.custody_cash(trip_id), repository.custody_gold(trip_id))
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
        """The exact reason a trip cannot close, in the words an employee reads."""
        parts = []
        if custody["cash"]["outstanding"] != 0:
            parts.append(f"نقدية {custody['cash']['outstanding']:,.2f} ج")
        parts += [f"عيار {g['karat_value']}: {g['outstanding']:,.3f} جرام" for g in custody["gold"] if g["outstanding"] != 0]
        return ("لا يمكن إغلاق العهدة قبل إرجاع كل ما أُخرج منها. المتبقي في العهدة: " + "، ".join(parts) + ".")

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
