"""Marketer (مسوقجي) custody account: the shop's CASH and 21K GOLD held in trust by a market intermediary, and the cycle gold -> cash -> gold.

Documents (all immutable; the codes are MH cash handover, MR cash / gold return, MB cash purchase of gold, MS sale of gold):
  cash handover   Main Safe -> marketer cash (a real cash_movements OUT of MAIN_SAFE + a marketer cash movement)
  cash return     marketer cash -> Main Safe, capped at the marketer's cash balance
  gold return     marketer 21K grams -> the counted shop Scrap (physical 21K grams, never converted), capped at the marketer's gold balance
  purchase        marketer cash -> 21K gold: the cash is spent (never above the balance), the grams are added; the VENDOR (a registered party or a typed name) is mandatory
  sale            21K gold -> marketer cash: the grams leave (never above the balance), the agreed total is added; the BUYER is mandatory
  supplier settlement   the existing SG document with the source HOLDING_MARKETER: 21K grams are delivered to a supplier and credited by the existing rule
                  (grams x 21 / supplier karat); ONE negative marketer gold movement and ONE supplier gold movement, tied by composite keys.

Scope: gold is 21K ONLY (no other karat, no lots). Rights are the intermediary-holding ones (INTERMEDIARY_HOLDING_MANAGE / _VIEW / _REVERSE); a settlement also needs
SUPPLIER_GOLD_SETTLE, a reversal TRANSACTION_CORRECT and a reason, all rechecked in the transaction (the actor is the session user).

ISOLATION: the marketer ledgers are separate tables with no key to any supplier ledger; nothing here reads or writes supplier_gold_movements /
supplier_making_movements except the ONE explicit settlement, and a party that is both a supplier and a marketer keeps two unrelated balances (a settlement to the
same party needs the owner's explicit confirmation, exactly as in 050). Lock order: the party row (the account lock; for a settlement the holder and the recipient
in ascending id) -> the Main Safe account / the Scrap karat. Every decision (balance, replay) is a CURRENT read; an exact replay of a request returns the original
document before any availability is derived; one request key and hash cover each payload and a key used by another flow is a REQUEST_CONFLICT."""
from decimal import Decimal
import hashlib
import json

from mysql.connector import IntegrityError

from repositories.financial_account_repository import FinancialAccountRepository
from repositories.marketer_custody_repository import (CASH_HANDOVER, CASH_MOVEMENT, CASH_RETURN, GOLD_MOVEMENT as MARKETER_GOLD, GOLD_RETURN, HOLDING_MARKETER,
                                                      KARAT_VALUE, MOVE_SETTLEMENT, PURCHASE, SALE, SOURCE, MarketerCustodyRepository, document_code)
from repositories.old_gold_conversion_repository import OldGoldConversionRepository
from repositories.supplier_gold_settlement_repository import GOLD_MOVEMENT as SUPPLIER_GOLD_MOVEMENT, SupplierGoldSettlementRepository, settlement_code
from services import supplier_rules as rules
from services.authorization_service import AuthorizationService
from services.invoice_service import invoice_transaction
from services.market_trip_service import (BUYER_NAME_MAX, BUYER_REGISTERED, BUYER_THIRD_PARTY, MAX_MONEY, MONEY_STEP, NOTES_MAX, PAYMENT_REFERENCE_MAX,
                                          PRICE_WARNING_PERCENT, ZERO_MONEY, ZERO_WEIGHT, MarketTripService, TripError, _decimal, _entry_date, _key, _text,
                                          parse_weight)

MANAGE_PERMISSION = "INTERMEDIARY_HOLDING_MANAGE"
VIEW_PERMISSION = "INTERMEDIARY_HOLDING_VIEW"
REVERSE_PERMISSION = "INTERMEDIARY_HOLDING_REVERSE"
SETTLE_PERMISSION = "SUPPLIER_GOLD_SETTLE"
KIND_TEXT = {CASH_HANDOVER: "تسليم نقدية للمسوقجي", CASH_RETURN: "استرجاع نقدية من المسوقجي", GOLD_RETURN: "استرجاع ذهب عيار 21 من المسوقجي",
             PURCHASE: "شراء ذهب نقدي للمسوقجي", SALE: "بيع ذهب المسوقجي"}
SELF_SETTLEMENT_TEXT = ("المسوقجي هو نفسه المورد المستلم: أكّد صراحةً أن ذهب المحل لديه (أمانة) يبقى منفصلًا تمامًا عن حساب المورد وأن هذه التسوية "
                        "تنقل الجرامات من عهدته إلى حسابه كمورد.")


def positive_money(value, label="المبلغ", code="AMOUNT_INVALID"):
    number = _decimal(value, label, code)
    if number is None or number <= 0 or number >= MAX_MONEY or number != number.quantize(MONEY_STEP):
        raise TripError(code, f"{label}: أدخل مبلغًا موجبًا بخانتين عشريتين على الأكثر.")
    return number.quantize(MONEY_STEP)


def positive_weight(value, label="الوزن"):
    grams = parse_weight(value)
    if grams == 0:
        raise TripError("WEIGHT_REQUIRED", f"{label}: أدخل وزنًا فعليًا موجبًا بالجرام (ثلاث خانات عشرية على الأكثر).")
    return grams


def parse_purchase_lines(lines):
    """The purchased gold: one row per (21K actual grams, agreed price); every started line must be complete. The total is the exact SUM of the line prices."""
    parsed = []
    for index, line in enumerate(lines or [], 1):
        weight_text, price_text = (str((line or {}).get(k, "") or "").strip() for k in ("weight", "price"))
        if not (weight_text or price_text):
            continue
        grams = parse_weight(weight_text)
        if grams == 0:
            raise TripError("WEIGHT_REQUIRED", f"السطر {index}: أدخل الوزن الفعلي بقيمة موجبة.")
        price = _decimal(price_text, "ثمن السطر", "PRICE_INVALID")
        if price is None or price <= 0 or price >= MAX_MONEY or price != price.quantize(MONEY_STEP):
            raise TripError("PRICE_INVALID", f"السطر {index}: أدخل الثمن المتفق عليه بقيمة موجبة وخانتين عشريتين على الأكثر.")
        parsed.append({"weight": grams, "price": price.quantize(MONEY_STEP)})
    if not parsed:
        raise TripError("LINES_REQUIRED", "أضف سطر ذهب واحدًا على الأقل (الوزن والثمن).")
    return parsed


def _is_request_key_violation(exc):
    return getattr(exc, "errno", None) == 1062 and any(k in str(exc) for k in ("uq_mkd_request", "uq_sgs_request"))


class MarketerCustodyService:
    def __init__(self, session, *, transaction_factory=invoice_transaction, repository=MarketerCustodyRepository, accounts=FinancialAccountRepository,
                 scrap=OldGoldConversionRepository, settlements=SupplierGoldSettlementRepository, authorization=None, corrections=None, price_reference=None):
        self._session, self._transaction, self._repository = session, transaction_factory, repository
        self._accounts, self._scrap, self._settlements = accounts, scrap, settlements
        self._authorization = authorization or AuthorizationService(session)
        self._corrections = corrections
        self._price_reference = price_reference if price_reference is not None else MarketTripService._default_price_reference(session)

    # ------------------------------------------------------------------ UI hints (never the authority)
    def can_manage(self):
        actor = self._session.current_actor()
        return actor is not None and MANAGE_PERMISSION in actor.permissions

    def can_view(self):
        actor = self._session.current_actor()
        return actor is not None and VIEW_PERMISSION in actor.permissions

    def can_settle(self):
        actor = self._session.current_actor()
        return actor is not None and {MANAGE_PERMISSION, SETTLE_PERMISSION} <= set(actor.permissions)

    def can_reverse(self):
        actor = self._session.current_actor()
        return actor is not None and {REVERSE_PERMISSION, "TRANSACTION_CORRECT"} <= set(actor.permissions)

    def _writer(self, expected_actor):
        actor = self._session.require_authenticated()
        if expected_actor is not None and not self._session.is_current(expected_actor):
            raise PermissionError("انتهت جلسة الدخول.")
        if MANAGE_PERMISSION not in actor.permissions:
            raise PermissionError("عهدة المسوقجي للمالك فقط.")
        return actor

    @staticmethod
    def _party_number(party_id):
        try:
            number = int(str(party_id).strip())
        except (TypeError, ValueError):
            raise TripError("PARTY_NOT_FOUND", "اختر المسوقجي.") from None
        if number <= 0:
            raise TripError("PARTY_NOT_FOUND", "اختر المسوقجي.")
        return number

    # ------------------------------------------------------------------ reads
    def _reference_prices(self):
        try:
            prices = self._price_reference() if self._price_reference is not None else {}
        except Exception:                                               # a price-engine problem never blocks a document
            return {}
        return {int(k): Decimal(str(v)) for k, v in (prices or {}).items()}

    def reference_prices(self, *, _expected_actor=None):
        """{karat: SHOP_BUY per gram} of the active price set (the reference of the price warning); empty when none is available."""
        self._writer(_expected_actor)
        return {str(k): str(v) for k, v in self._reference_prices().items()}

    def marketers(self, *, _expected_actor=None):
        """The parties with the marketer role (a list for pickers)."""
        actor = self._session.require_authenticated()
        if _expected_actor is not None and not self._session.is_current(_expected_actor):
            raise PermissionError("انتهت جلسة الدخول.")
        if VIEW_PERMISSION not in actor.permissions:
            raise PermissionError("عرض عهدة المسوقجي للمالك فقط.")
        with self._transaction() as connection:
            self._authorization.require_permission_read(connection, actor, VIEW_PERMISSION)
            rows = self._repository(connection).marketers()
        return [{"id": str(r["id"]), "name": r["name"], "active": bool(r["is_active"]), "supplier": bool(r["is_supplier"])} for r in rows]

    def account(self, party_id, *, _expected_actor=None):
        """The marketer account of one party: cash and 21K gold balances (separate figures, no total), the documents and the settlements. Read only; the supplier
        ledger of the same party is NEVER read or netted here."""
        actor = self._session.require_authenticated()
        if _expected_actor is not None and not self._session.is_current(_expected_actor):
            raise PermissionError("انتهت جلسة الدخول.")
        if VIEW_PERMISSION not in actor.permissions:
            raise PermissionError("عرض عهدة المسوقجي للمالك فقط.")
        party_number = self._party_number(party_id)
        with self._transaction() as connection:
            self._authorization.require_permission_read(connection, actor, VIEW_PERMISSION)
            repo = self._repository(connection)
            party = repo.party(party_number)
            if party is None or not party["is_intermediary"]:
                raise TripError("PARTY_NOT_FOUND", "هذا الطرف ليس مسوقجيًا.")
            cash, gold = Decimal(str(repo.display_cash_balance(party_number))), Decimal(str(repo.display_gold_balance(party_number)))
            documents, settlements = repo.documents(party_number), repo.settlements(party_number)
            counterparties = repo.counterparties()
            recipients = repo.recipients()
        return {"party": {"id": str(party["id"]), "name": party["name"], "active": bool(party["is_active"]), "is_supplier": bool(party["is_supplier"]),
                          "is_intermediary": True},
                "cash": cash.quantize(MONEY_STEP), "gold": gold.quantize(Decimal("0.001")), "karat": KARAT_VALUE,
                "documents": [self._document_row(d) for d in documents], "settlements": [self._settlement_row(s) for s in settlements],
                "counterparties": [{"id": str(r["id"]), "name": r["name"], "active": bool(r["is_active"]), "supplier": bool(r["is_supplier"]),
                                    "intermediary": bool(r["is_intermediary"])} for r in counterparties],
                "recipients": [{"id": str(r["id"]), "name": r["name"], "active": bool(r["is_active"]), "karat_value": int(r["karat_value"]),
                                "self": int(r["id"]) == party_number} for r in recipients],
                "can_manage": MANAGE_PERMISSION in actor.permissions, "can_settle": {MANAGE_PERMISSION, SETTLE_PERMISSION} <= set(actor.permissions),
                "can_reverse": {REVERSE_PERMISSION, "TRANSACTION_CORRECT"} <= set(actor.permissions)}

    @staticmethod
    def _document_row(d):
        reversed_by = None if d.get("reversal_id") is None else d["reversal_id"]
        return {"id": str(d["id"]), "code": document_code(d["doc_kind"], d["id"]), "kind": d["doc_kind"], "kind_text": KIND_TEXT[d["doc_kind"]],
                "cash": None if d["cash_amount"] is None else Decimal(str(d["cash_amount"])),
                "weight": None if d["weight_grams"] is None else Decimal(str(d["weight_grams"])),
                "counterparty": d.get("counterparty_name"), "counterparty_kind": d.get("counterparty_kind"),
                "counterparty_party_id": None if d.get("counterparty_party_id") is None else str(d["counterparty_party_id"]),
                "price_warning_acknowledged": bool(d.get("price_warning_acknowledged")),
                "deviation_percent": None if d.get("deviation_percent") is None else Decimal(str(d["deviation_percent"])),
                "lines": [{"weight": Decimal(str(l["weight_grams"])), "price": Decimal(str(l["line_price"]))} for l in d.get("lines", [])],
                "date": d["document_date"], "reference": d.get("reference") or "", "notes": d.get("notes") or "", "at": d["occurred_at"],
                "actor": d.get("actor_name") or "", "reversed": reversed_by is not None, "reversal_reason": d.get("reversal_reason") or ""}

    @staticmethod
    def _settlement_row(s):
        return {"id": str(s["id"]), "code": settlement_code(s["id"]), "supplier_id": str(s["supplier_id"]), "supplier_name": s["supplier_name"],
                "delivered": Decimal(str(s["delivered_weight_grams"])), "credited": Decimal(str(s["credited_weight_grams"])),
                "target_karat": int(s["target_karat_value"]), "self_settlement": bool(s["self_settlement_confirmed"]), "date": s["settlement_date"],
                "reference": s.get("reference") or "", "at": s["occurred_at"], "actor": s.get("actor_name") or "", "reversed": s.get("reversal_id") is not None,
                "reversal_reason": s.get("reversal_reason") or ""}

    # ------------------------------------------------------------------ replay and results
    @staticmethod
    def _result(repo, document, duplicate):
        kind = document["doc_kind"]
        return {"id": str(document["id"]), "code": document_code(kind, document["id"]), "kind": kind, "party_id": str(document["party_id"]),
                "cash": None if document["cash_amount"] is None else str(document["cash_amount"]),
                "weight": None if document["weight_grams"] is None else str(document["weight_grams"]),
                "counterparty_kind": document.get("counterparty_kind"), "counterparty_name": document.get("counterparty_name"),
                "reference_total": None if document.get("reference_total") is None else str(document["reference_total"]),
                "deviation_percent": None if document.get("deviation_percent") is None else str(document["deviation_percent"]),
                "price_warning_acknowledged": bool(document.get("price_warning_acknowledged")), "duplicate": duplicate}

    def _prior(self, repo, key, digest, actor, kind):
        """The original document of an already-recorded request (None when the key is unused). A replay needs the SAME payload by the SAME user."""
        document = repo.by_request(key)
        if document is None:
            if repo.key_used_elsewhere(key):
                raise TripError("REQUEST_CONFLICT", "طلب العملية مستخدم لعملية مختلفة.")
            return None
        if document["doc_kind"] != kind or document["request_hash"] != digest or int(document["performed_by_user_id"]) != int(actor.user_id):
            raise TripError("REQUEST_CONFLICT", "طلب العملية مستخدم لعملية مختلفة.")
        return self._result(repo, document, True)

    def _transact(self, session_actor, party_number, key, digest, kind, work, *, active_required):
        try:
            return self._transact_once(session_actor, party_number, key, digest, kind, work, active_required)
        except IntegrityError as exc:
            if not _is_request_key_violation(exc):
                raise
            with self._transaction() as connection:                          # a twin committed first: decide from a FRESH read
                actor = self._authorization.require_permission_for_write(connection, session_actor, MANAGE_PERMISSION)
                replay = self._prior(self._repository(connection), key, digest, actor, kind)
                if replay is None:
                    raise
                return replay

    def _transact_once(self, session_actor, party_number, key, digest, kind, work, active_required):
        with self._transaction() as connection:
            actor = self._authorization.require_permission_for_write(connection, session_actor, MANAGE_PERMISSION)
            repo = self._repository(connection)
            party = repo.party(party_number, lock=True)                       # the account lock
            if party is None:
                raise TripError("PARTY_NOT_FOUND", "المسوقجي غير موجود.")
            replay = self._prior(repo, key, digest, actor, kind)
            if replay is not None:
                return replay                                                # the original, before any availability is derived
            if not party["is_intermediary"]:
                raise TripError("MARKETER_ROLE_REQUIRED", "هذا الطرف ليس مسوقجيًا: أضف له دور المسوقجي أولًا.")
            if active_required and not party["is_active"]:
                raise TripError("MARKETER_INACTIVE", "المسوقجي موقوف: لا يُسلَّم له نقدية ولا يشتري بعهدته (ما عنده يمكن استلامه أو بيعه).")
            result = work(connection, repo, actor, party)
            if not self._session.is_current(session_actor):
                raise PermissionError("انتهت جلسة الدخول.")
        return result

    def _digest(self, *parts):
        return hashlib.sha256(json.dumps(list(parts), ensure_ascii=False).encode()).hexdigest()

    def _audit(self, repo, actor, document_id, kind, code, label, metadata, now):
        repo.audit(actor=actor, event_type="MARKETER_" + kind, entity_id=document_id, reference=code, description=label,
                   metadata={"kind": kind, **metadata, "employee_id": actor.employee_id}, now=now)

    # ------------------------------------------------------------------ cash handover / cash return
    def cash_handover(self, party_id, amount, document_date, reference, notes, request_key, *, _expected_actor=None):
        """Hand cash from the Main Safe to the marketer (held in trust: not a payment, not a supplier movement)."""
        return self._cash(CASH_HANDOVER, party_id, amount, document_date, reference, notes, request_key, _expected_actor)

    def cash_return(self, party_id, amount, document_date, reference, notes, request_key, *, _expected_actor=None):
        """The marketer returns cash to the Main Safe, never more than the cash he holds."""
        return self._cash(CASH_RETURN, party_id, amount, document_date, reference, notes, request_key, _expected_actor)

    def _cash(self, kind, party_id, amount, document_date, reference, notes, request_key, expected_actor):
        session_actor = self._writer(expected_actor)
        party_number = self._party_number(party_id)
        value = positive_money(amount, "مبلغ النقدية")
        dated = _entry_date(document_date)
        reference, notes, key = _text(reference, PAYMENT_REFERENCE_MAX, "المرجع"), _text(notes, NOTES_MAX, "الملاحظات"), _key(request_key)
        digest = self._digest(kind, party_number, str(value), dated.isoformat(), reference, notes)

        def work(connection, repo, actor, party):
            safe = repo.main_safe()
            if safe is None or not safe["is_active"]:
                raise TripError("SAFE_UNAVAILABLE", "الخزنة الرئيسية غير متاحة.")
            self._accounts(connection).account("CASH", safe["id"], lock=True)           # party -> Main Safe
            if kind == CASH_RETURN:
                held = Decimal(str(repo.cash_balance(party_number)))
                if value > held:
                    raise TripError("CASH_EXCEEDS_HELD", f"نقدية المسوقجي {held:,.2f} ج فقط: لا يمكن استرجاع {value:,.2f} ج.")
            now = repo.db_now()
            document_id = repo.insert_document(kind=kind, party_id=party_number, cash_amount=value, weight=None, counterparty=None, reference_total=None,
                                               deviation=None, acknowledged=False, document_date=dated, reference=reference, notes=notes, key=key,
                                               digest=digest, actor=actor, now=now)
            code = document_code(kind, document_id)
            label = f"{KIND_TEXT[kind]} {code}: {value:,.2f} ج — {party['name']}"
            shop_id = repo.post_shop_cash(kind=kind, account_id=safe["id"], document_id=document_id, code=code, amount=value, description=label,
                                          actor=actor, now=now)
            original, _, sign = CASH_MOVEMENT[kind]
            repo.insert_cash_movement(party_id=party_number, document_id=document_id, doc_kind=kind, movement_type=original, amount=value, delta=sign * value)
            repo.insert_shop_link(document_id=document_id, doc_kind=kind, shop_cash_movement_id=shop_id)
            self._audit(repo, actor, document_id, kind, code, label, {"party_id": party_number, "amount": str(value), "date": dated.isoformat()}, now)
            return self._result(repo, repo.document(document_id), False)
        return self._transact(session_actor, party_number, key, digest, kind, work, active_required=kind == CASH_HANDOVER)

    # ------------------------------------------------------------------ gold return to the shop Scrap
    def gold_return(self, party_id, weight, document_date, reference, notes, request_key, *, _expected_actor=None):
        """The marketer returns 21K gold to the counted shop Scrap (physical 21K grams), never more than the gold he holds."""
        session_actor = self._writer(_expected_actor)
        party_number = self._party_number(party_id)
        grams = positive_weight(weight)
        dated = _entry_date(document_date)
        reference, notes, key = _text(reference, PAYMENT_REFERENCE_MAX, "المرجع"), _text(notes, NOTES_MAX, "الملاحظات"), _key(request_key)
        digest = self._digest(GOLD_RETURN, party_number, str(grams), dated.isoformat(), reference, notes)

        def work(connection, repo, actor, party):
            karat = repo.karat21()
            if karat is None or not karat["is_active"]:
                raise TripError("KARAT_NOT_FOUND", "عيار 21 غير موجود أو غير نشط.")
            self._scrap(connection).lock_old_gold_balance(karat["id"])                    # party -> Scrap karat
            held = Decimal(str(repo.gold_balance(party_number)))
            if grams > held:
                raise TripError("GOLD_EXCEEDS_HELD", f"ذهب المسوقجي (عيار 21) {held:,.3f} جرام فقط: لا يمكن استرجاع {grams:,.3f} جرام.")
            now = repo.db_now()
            document_id = repo.insert_document(kind=GOLD_RETURN, party_id=party_number, cash_amount=None, weight=grams, counterparty=None,
                                               reference_total=None, deviation=None, acknowledged=False, document_date=dated, reference=reference, notes=notes,
                                               key=key, digest=digest, actor=actor, now=now)
            code = document_code(GOLD_RETURN, document_id)
            label = f"{KIND_TEXT[GOLD_RETURN]} {code}: {grams:,.3f} جرام وزنة عيار 21 — {party['name']}"
            scrap_id = repo.post_shop_scrap_in(karat_id=karat["id"], weight=grams, document_id=document_id, code=code, notes=label, actor=actor, now=now)
            original, _, sign = MARKETER_GOLD[GOLD_RETURN]
            repo.insert_gold_movement(party_id=party_number, document_id=document_id, doc_kind=GOLD_RETURN, movement_type=original, weight=grams,
                                      delta=sign * grams)
            repo.insert_shop_link(document_id=document_id, doc_kind=GOLD_RETURN, shop_scrap_movement_id=scrap_id)
            self._audit(repo, actor, document_id, GOLD_RETURN, code, label, {"party_id": party_number, "weight": str(grams), "karat": KARAT_VALUE,
                                                                             "date": dated.isoformat()}, now)
            return self._result(repo, repo.document(document_id), False)
        return self._transact(session_actor, party_number, key, digest, GOLD_RETURN, work, active_required=False)

    # ------------------------------------------------------------------ counterparty (the mandatory vendor / buyer)
    @staticmethod
    def _counterparty(repo, party_number, counterparty_party_id, counterparty_name, role):
        """An ACTIVE registered party (any role, never the marketer himself) or a typed name; the name is a snapshot and its normalized key groups KPIs."""
        text = str(counterparty_party_id if counterparty_party_id is not None else "").strip()
        word = "البائع" if role == "vendor" else "المشتري"
        if text:
            try:
                number = int(text)
            except ValueError:
                raise TripError("COUNTERPARTY_NOT_FOUND", f"الطرف {word} غير موجود.") from None
            if number == party_number:
                raise TripError("COUNTERPARTY_IS_HOLDER", f"لا يكون {word} هو المسوقجي نفسه.")
            party = repo.party(number, lock=True)
            if party is None:
                raise TripError("COUNTERPARTY_NOT_FOUND", f"الطرف {word} غير موجود.")
            if not party["is_active"]:
                raise TripError("COUNTERPARTY_INACTIVE", "هذا الطرف موقوف: لا يُتعامل معه. فعّله أولًا أو اختر طرفًا آخر.")
            label, kind, owner = str(party["name"]).strip(), BUYER_REGISTERED, number
        else:
            label = _text(counterparty_name, BUYER_NAME_MAX, f"اسم {word}", required=False)
            if not label:
                raise TripError("COUNTERPARTY_REQUIRED", f"اسم {word} إجباري: اختره من الأطراف المسجلة أو اكتب اسمه.")
            kind, owner = BUYER_THIRD_PARTY, None
        key = MarketTripService.vendor_name_key(label)
        if not key:
            raise TripError("COUNTERPARTY_REQUIRED", f"اسم {word} إجباري: اختره من الأطراف المسجلة أو اكتب اسمه.")
        return kind, owner, label, key

    # ------------------------------------------------------------------ purchase: cash -> 21K gold
    def purchase(self, party_id, lines, vendor_party_id, vendor_name, document_date, reference, notes, request_key, acknowledge_price=False, *,
                 _expected_actor=None):
        """The marketer buys 21K gold with the cash he holds: the total (the sum of the line prices) leaves his cash, the grams enter his gold."""
        session_actor = self._writer(_expected_actor)
        party_number = self._party_number(party_id)
        parsed = parse_purchase_lines(lines)
        dated = _entry_date(document_date)
        reference, notes, key = _text(reference, PAYMENT_REFERENCE_MAX, "المرجع"), _text(notes, NOTES_MAX, "الملاحظات"), _key(request_key)
        vendor_text = str(vendor_party_id if vendor_party_id is not None else "").strip()
        digest = self._digest(PURCHASE, party_number, [[str(l["weight"]), str(l["price"])] for l in parsed], vendor_text,
                              "" if vendor_text else str(vendor_name or "").strip(), dated.isoformat(), reference, notes)

        def work(connection, repo, actor, party):
            vendor = self._counterparty(repo, party_number, vendor_text, vendor_name, "vendor")
            karat = repo.karat21()
            if karat is None or not karat["is_active"]:
                raise TripError("KARAT_NOT_FOUND", "عيار 21 غير موجود أو غير نشط.")
            total = sum((l["price"] for l in parsed), ZERO_MONEY)
            grams = sum((l["weight"] for l in parsed), ZERO_WEIGHT)
            if total >= MAX_MONEY:
                raise TripError("PRICE_INVALID", "إجمالي الشراء كبير جدًا.")
            held = Decimal(str(repo.cash_balance(party_number)))
            if total > held:
                raise TripError("PURCHASE_EXCEEDS_CASH", f"نقدية المسوقجي {held:,.2f} ج فقط: لا يمكن شراء بإجمالي {total:,.2f} ج.")
            reference_total, deviation = MarketTripService.buy_reference(
                self._reference_prices(), [{"karat_id": karat["id"], "weight": l["weight"]} for l in parsed], {karat["id"]: KARAT_VALUE}, total)
            warned = deviation is not None and abs(deviation) > PRICE_WARNING_PERCENT
            if warned and not acknowledge_price:
                raise TripError("PRICE_DEVIATION", MarketTripService._buy_warning_text(deviation, reference_total))
            now = repo.db_now()
            document_id = repo.insert_document(kind=PURCHASE, party_id=party_number, cash_amount=total, weight=grams, counterparty=vendor,
                                               reference_total=reference_total, deviation=deviation, acknowledged=warned and acknowledge_price,
                                               document_date=dated, reference=reference, notes=notes, key=key, digest=digest, actor=actor, now=now)
            for number, line in enumerate(parsed, 1):
                repo.insert_purchase_line(document_id=document_id, line_no=number, weight=line["weight"], price=line["price"])
            code = document_code(PURCHASE, document_id)
            label = (f"{KIND_TEXT[PURCHASE]} {code}: {grams:,.3f} جرام وزنة عيار 21 بإجمالي {total:,.2f} ج من {vendor[2]} — {party['name']}")
            cash_type, _, cash_sign = CASH_MOVEMENT[PURCHASE]
            gold_type, _, gold_sign = MARKETER_GOLD[PURCHASE]
            repo.insert_cash_movement(party_id=party_number, document_id=document_id, doc_kind=PURCHASE, movement_type=cash_type, amount=total,
                                      delta=cash_sign * total)
            repo.insert_gold_movement(party_id=party_number, document_id=document_id, doc_kind=PURCHASE, movement_type=gold_type, weight=grams,
                                      delta=gold_sign * grams)
            self._audit(repo, actor, document_id, PURCHASE, code, label,
                        {"party_id": party_number, "total": str(total), "weight": str(grams), "vendor_kind": vendor[0], "vendor_party_id": vendor[1],
                         "vendor_name": vendor[2], "lines": [{"weight": str(l["weight"]), "price": str(l["price"])} for l in parsed],
                         "reference_total": None if reference_total is None else str(reference_total),
                         "deviation_percent": None if deviation is None else str(deviation), "price_warning_acknowledged": bool(warned and acknowledge_price),
                         "date": dated.isoformat()}, now)
            return self._result(repo, repo.document(document_id), False)
        return self._transact(session_actor, party_number, key, digest, PURCHASE, work, active_required=True)

    # ------------------------------------------------------------------ sale: 21K gold -> cash
    def sale(self, party_id, weight, price_mode, price, buyer_party_id, buyer_name, document_date, reference, notes, request_key, acknowledge_price=False, *,
             _expected_actor=None):
        """The marketer sells 21K gold he holds: the grams leave his gold, the agreed total (entered as a total or per gram) enters his cash."""
        session_actor = self._writer(_expected_actor)
        party_number = self._party_number(party_id)
        grams = positive_weight(weight)
        mode, total, per_gram = MarketTripService._sale_price(grams, price_mode, price)
        dated = _entry_date(document_date)
        reference, notes, key = _text(reference, PAYMENT_REFERENCE_MAX, "المرجع"), _text(notes, NOTES_MAX, "الملاحظات"), _key(request_key)
        buyer_text = str(buyer_party_id if buyer_party_id is not None else "").strip()
        digest = self._digest(SALE, party_number, str(grams), mode, str(total), None if per_gram is None else str(per_gram), buyer_text,
                              "" if buyer_text else str(buyer_name or "").strip(), dated.isoformat(), reference, notes)

        def work(connection, repo, actor, party):
            buyer = self._counterparty(repo, party_number, buyer_text, buyer_name, "buyer")
            held = Decimal(str(repo.gold_balance(party_number)))
            if grams > held:
                raise TripError("SALE_EXCEEDS_GOLD", f"ذهب المسوقجي (عيار 21) {held:,.3f} جرام فقط: لا يمكن بيع {grams:,.3f} جرام.")
            reference_total, _, deviation = MarketTripService.sale_reference(self._reference_prices(), grams, KARAT_VALUE, None, total)
            warned = deviation is not None and abs(deviation) > PRICE_WARNING_PERCENT
            if warned and not acknowledge_price:
                raise TripError("PRICE_DEVIATION", MarketTripService._price_warning_text(deviation, reference_total))
            now = repo.db_now()
            document_id = repo.insert_document(kind=SALE, party_id=party_number, cash_amount=total, weight=grams, counterparty=buyer,
                                               reference_total=reference_total, deviation=deviation, acknowledged=warned and acknowledge_price,
                                               document_date=dated, reference=reference, notes=notes, key=key, digest=digest, actor=actor, now=now)
            code = document_code(SALE, document_id)
            label = f"{KIND_TEXT[SALE]} {code}: {grams:,.3f} جرام وزنة عيار 21 إلى {buyer[2]} بمبلغ {total:,.2f} ج — {party['name']}"
            gold_type, _, gold_sign = MARKETER_GOLD[SALE]
            cash_type, _, cash_sign = CASH_MOVEMENT[SALE]
            repo.insert_gold_movement(party_id=party_number, document_id=document_id, doc_kind=SALE, movement_type=gold_type, weight=grams,
                                      delta=gold_sign * grams)
            repo.insert_cash_movement(party_id=party_number, document_id=document_id, doc_kind=SALE, movement_type=cash_type, amount=total,
                                      delta=cash_sign * total)
            self._audit(repo, actor, document_id, SALE, code, label,
                        {"party_id": party_number, "total": str(total), "weight": str(grams), "price_basis": mode,
                         "price_per_gram": None if per_gram is None else str(per_gram), "buyer_kind": buyer[0], "buyer_party_id": buyer[1],
                         "buyer_name": buyer[2], "reference_total": None if reference_total is None else str(reference_total),
                         "deviation_percent": None if deviation is None else str(deviation), "price_warning_acknowledged": bool(warned and acknowledge_price),
                         "date": dated.isoformat()}, now)
            return self._result(repo, repo.document(document_id), False)
        return self._transact(session_actor, party_number, key, digest, SALE, work, active_required=False)

    # ------------------------------------------------------------------ settlement: 21K grams -> a supplier (the ONE bridge)
    def settle_supplier(self, party_id, supplier_id, weight, self_confirmed, settlement_date, reference, notes, request_key, *, _expected_actor=None):
        """Deliver 21K grams of the marketer's gold to a SUPPLIER against its gold account (the existing SG document, source HOLDING_MARKETER). The credited grams
        follow the existing rule (grams x 21 / supplier karat); the marketer gold and the supplier account change together and nowhere else. A recipient that
        is the marketer himself needs the owner's explicit confirmation."""
        session_actor = self._writer(_expected_actor)
        party_number = self._party_number(party_id)
        try:
            supplier_number = int(str(supplier_id).strip())
        except (TypeError, ValueError):
            raise TripError("SUPPLIER_NOT_FOUND", "اختر المورد المستلم.") from None
        if supplier_number <= 0:
            raise TripError("SUPPLIER_NOT_FOUND", "اختر المورد المستلم.")
        grams = rules.parse_delivered_weight(weight)
        confirmed = self_confirmed is True or str(self_confirmed).strip().lower() in ("1", "true")
        settled_on = _entry_date(settlement_date)
        reference, notes, key = _text(reference, PAYMENT_REFERENCE_MAX, "المرجع"), _text(notes, NOTES_MAX, "الملاحظات"), _key(request_key)
        digest = self._digest(HOLDING_MARKETER, party_number, supplier_number, str(grams), confirmed, settled_on.isoformat(), reference, notes)
        try:
            return self._settle_once(session_actor, party_number, supplier_number, grams, confirmed, settled_on, reference, notes, key, digest)
        except IntegrityError as exc:
            if not _is_request_key_violation(exc):
                raise
            with self._transaction() as connection:
                actor = self._authorization.require_permission_for_write(connection, session_actor, MANAGE_PERMISSION)
                replay = self._settlement_prior(self._repository(connection), self._settlements(connection), key, digest, actor)
                if replay is None:
                    raise
                return replay

    def _settlement_result(self, header, duplicate, remaining=None):
        return {"id": str(header["id"]), "code": settlement_code(header["id"]), "party_id": str(header["holding_party_id"]),
                "supplier_id": str(header["supplier_id"]), "delivered_weight": str(header["delivered_weight_grams"]),
                "credited_weight": str(header["credited_weight_grams"]), "analytical": str(header["credited_analytical_grams"]),
                "rounding_difference": str(header["rounding_difference_grams"]), "target_karat": int(header["target_karat_value"]),
                "remaining": None if remaining is None else str(remaining), "duplicate": duplicate}

    def _settlement_prior(self, repo, parties, key, digest, actor):
        header = parties.by_request(key)
        if header is None:
            if repo.by_request(key) is not None or repo.key_used_elsewhere(key):
                raise TripError("REQUEST_CONFLICT", "طلب العملية مستخدم لعملية مختلفة.")
            return None
        if (header["source_kind"] != HOLDING_MARKETER or header["request_hash"] != digest or int(header["performed_by_user_id"]) != int(actor.user_id)):
            raise TripError("REQUEST_CONFLICT", "طلب العملية مستخدم لعملية مختلفة.")
        return self._settlement_result(header, True)

    def _settle_once(self, session_actor, party_number, supplier_number, grams, confirmed, settled_on, reference, notes, key, digest):
        with self._transaction() as connection:
            self._authorization.require_permission_for_write(connection, session_actor, MANAGE_PERMISSION)
            actor = self._authorization.require_permission_for_write(connection, session_actor, SETTLE_PERMISSION)
            repo, parties = self._repository(connection), self._settlements(connection)
            locked = {party: repo.party(party, lock=True) for party in sorted({party_number, supplier_number})}      # holder and recipient, ascending
            holder, supplier = locked[party_number], locked[supplier_number]
            if holder is None:
                raise TripError("PARTY_NOT_FOUND", "المسوقجي غير موجود.")
            replay = self._settlement_prior(repo, parties, key, digest, actor)
            if replay is not None:
                return replay                                                # the original, before any availability is derived
            if not holder["is_intermediary"]:
                raise TripError("MARKETER_ROLE_REQUIRED", "هذا الطرف ليس مسوقجيًا.")
            if supplier is None:
                raise TripError("SUPPLIER_NOT_FOUND", "المورد غير موجود.")
            rules.require_supplier_role(supplier)                           # an intermediary-only party has no gold account
            if supplier["karat_id"] is None:
                raise rules.SupplierRuleError("SUPPLIER_KARAT_MISSING", "عيار هذا المورد غير محدد: حدّده من تعديل المورد أولًا.")
            self_settlement = supplier_number == party_number
            if self_settlement and not confirmed:
                raise TripError("SELF_SETTLEMENT_CONFIRMATION_REQUIRED", SELF_SETTLEMENT_TEXT)
            karat = repo.karat21()
            if karat is None or not karat["is_active"]:
                raise TripError("KARAT_NOT_FOUND", "عيار 21 غير موجود أو غير نشط.")
            held = Decimal(str(repo.gold_balance(party_number)))
            if held <= 0:
                raise TripError("GOLD_NOT_AVAILABLE", "لا يوجد ذهب عيار 21 لدى المسوقجي.")
            if grams > held:
                raise TripError("GOLD_EXCEEDS_HELD", f"ذهب المسوقجي (عيار 21) {held:,.3f} جرام فقط: لا يمكن تسليم {grams:,.3f} جرام.")
            target = parties.get_karat(supplier["karat_id"])
            credited, analytical, difference = rules.settlement_credit(grams, KARAT_VALUE, target["karat_value"])        # zero credit refuses here
            type_id = parties.gold_type_id(SUPPLIER_GOLD_MOVEMENT)
            if type_id is None:
                raise RuntimeError("supplier gold movement type SETTLEMENT is missing (migration 045)")
            now = repo.db_now()
            settlement_id = repo.insert_settlement_header(
                supplier_id=supplier_number, party_id=party_number, karat=karat, target_karat=target, quantity=grams, credited=credited, analytical=analytical,
                difference=difference, self_confirmed=self_settlement, settlement_date=settled_on, reference=reference, notes=notes, key=key, digest=digest,
                actor=actor, now=now)
            code = settlement_code(settlement_id)
            label = (f"تسوية ذهب للمورد {code} من عهدة المسوقجي {holder['name']}: سُلِّم {grams:,.3f} جرام وزنة عيار 21 "
                     f"← احتُسب {credited:,.3f} جرام عيار {int(target['karat_value'])}")
            gold_id = parties.insert_gold_movement(
                supplier_id=supplier_number, type_id=type_id, karat_id=target["id"], weight_delta=-credited,
                eq21_delta=-rules.equivalent_21(credited, target["karat_value"]), source_type="SUPPLIER_GOLD_SETTLEMENT", source_id=settlement_id,
                reference=code, description=label, now=now, user_id=actor.user_id)
            parties.insert_leg(settlement_id=settlement_id, kind="GOLD", source_kind=HOLDING_MARKETER, supplier_gold_movement_id=gold_id)
            repo.insert_gold_movement(party_id=party_number, settlement_id=settlement_id, movement_type=MOVE_SETTLEMENT, weight=grams, delta=-grams)
            metadata = {"settlement": code, "source_kind": HOLDING_MARKETER, "holder_id": party_number, "supplier_id": supplier_number, "delivered": str(grams),
                        "karat": KARAT_VALUE, "target_karat": int(target["karat_value"]), "credited": str(credited), "analytical": str(analytical),
                        "rounding_difference": str(difference), "remaining": str(held - grams), "self_settlement": self_settlement,
                        "date": settled_on.isoformat(), "reference": reference, "employee_id": actor.employee_id}
            parties.audit(actor=actor, event_type="SUPPLIER_GOLD_SETTLEMENT", entity_id=settlement_id, entity_type="SUPPLIER_GOLD_SETTLEMENT", reference=code,
                          description=label, metadata=metadata, now=now)
            repo.audit(actor=actor, event_type="MARKETER_SETTLEMENT", entity_id=settlement_id, reference=code, description=label, metadata=metadata, now=now)
            result = self._settlement_result(parties.settlement(settlement_id), False, remaining=held - grams)
            if not self._session.is_current(session_actor):
                raise PermissionError("انتهت جلسة الدخول.")
        return result

    # ------------------------------------------------------------------ reversal (the canonical correction)
    def _correction_service(self):
        if self._corrections is None:
            from services.correction_service import CorrectionService
            self._corrections = CorrectionService(self._session)
        return self._corrections

    def reverse(self, document_id, reason, *, _expected_actor=None):
        """Full reversal (RV) of one marketer document. The rights are checked here as a hint and again, inside the transaction, by CorrectionService
        (INTERMEDIARY_HOLDING_REVERSE + TRANSACTION_CORRECT, reason mandatory). Explicit compensations are posted; the original movements stay counted."""
        actor = self._session.require_authenticated()
        if _expected_actor is not None and not self._session.is_current(_expected_actor):
            raise PermissionError("انتهت جلسة الدخول.")
        if not {REVERSE_PERMISSION, "TRANSACTION_CORRECT"} <= set(actor.permissions):
            raise PermissionError("عكس مستندات المسوقجي للمالك فقط.")
        return self._correction_service().reverse(SOURCE, str(document_id), reason, _expected_actor=_expected_actor)

    def reverse_settlement(self, settlement_id, reason, *, _expected_actor=None):
        """Full reversal of a settlement made from the marketer account (the one canonical correction on the SG): the STORED supplier credit and the STORED
        delivered grams are restored; nothing is recalculated."""
        actor = self._session.require_authenticated()
        if _expected_actor is not None and not self._session.is_current(_expected_actor):
            raise PermissionError("انتهت جلسة الدخول.")
        if not {"SUPPLIER_GOLD_SETTLE_REVERSE", REVERSE_PERMISSION, "TRANSACTION_CORRECT"} <= set(actor.permissions):
            raise PermissionError("عكس تسوية ذهب المسوقجي للمالك فقط.")
        return self._correction_service().reverse("SUPPLIER_GOLD_SETTLEMENT", str(settlement_id), reason, _expected_actor=_expected_actor)
