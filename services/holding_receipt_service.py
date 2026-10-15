"""Legacy physical-receipt history and corrections for intermediary holdings (051).

New LOT physical receipts are disabled. Existing HR/RP records remain immutable, readable, and correctable through the one canonical full reversal below.
Exact replay of an already-successful receipt request still returns its original records and never creates another receipt.

Measured grams must equal the consumed holding grams: one weight column, so a mismatch is refused atomically and no discrepancy record exists. The first slice
records an UNKNOWN assay only: no fineness, no pure-gold figure, no inherited source assay and no claim of purity equivalence; an unmeasured purity discrepancy
is neither discharged nor recorded here. Received stock is SHOP stock, visible but unavailable for sale, settlement, issue or melt (no such path exists).

Rights (explicit grants, re-checked inside the transaction; the actor is the session user): INTERMEDIARY_HOLDING_MANAGE to post; the reversal is the ONE canonical
correction on the HR document (CorrectionService, source HOLDING_RECEIPT) and needs TRANSACTION_CORRECT + INTERMEDIARY_HOLDING_REVERSE. No custody trip is locked or
reopened (the origin custody may be CLOSED).

Lock order (every writer, this one and CorrectionService): the lot row -> the holder party row -> the holding source pool -> the receipt piece. Every decision is a
CURRENT locking read. ANY future consumer of receipt stock must follow the same protocol (lock the piece first, then a current stock read, then a negative
movement): nothing in this module makes that automatic."""
import hashlib
import json
from uuid import UUID

from mysql.connector import IntegrityError

from repositories.intermediary_holding_repository import IntermediaryHoldingRepository, piece_code, receipt_code
from repositories.market_trip_repository import MarketTripRepository
from repositories.supplier_gold_settlement_repository import SupplierGoldSettlementRepository
from services import supplier_rules as rules
from services.authorization_service import AuthorizationService
from services.intermediary_holding_rules import ALLOCATING, MANAGE_PERMISSION, REVERSE_PERMISSION, VIEW_PERMISSION
from services.invoice_service import invoice_transaction
from services.market_trip_service import NOTES_MAX, PAYMENT_REFERENCE_MAX, TripError, _entry_date, _text
from decimal import Decimal

CORRECT_PERMISSION = "TRANSACTION_CORRECT"
SOURCE = "HOLDING_RECEIPT"
KEY_CONSTRAINTS = ("uq_hr_request", "uq_mte_request", "uq_ihd_request", "uq_lhd_request", "uq_sgs_request")


def _number(value, code, message):
    try:
        number = int(str(value).strip())
    except (TypeError, ValueError):
        raise TripError(code, message) from None
    if number <= 0:
        raise TripError(code, message)
    return number


def _key(value):
    try:
        return str(UUID(str(value)))
    except (ValueError, TypeError, AttributeError):
        raise TripError("REQUEST_INVALID", "أعد فتح النموذج وحاول مرة أخرى.") from None


def _grams(value, label):
    try:
        return rules.parse_delivered_weight(value, label)
    except rules.SupplierRuleError as exc:
        raise TripError(exc.code, str(exc)) from None


class HoldingReceiptService:
    def __init__(self, session, *, transaction_factory=invoice_transaction, repository=MarketTripRepository, holdings=IntermediaryHoldingRepository,
                 parties=SupplierGoldSettlementRepository, authorization=None, corrections=None):
        self._session, self._transaction = session, transaction_factory
        self._repository, self._holdings, self._parties = repository, holdings, parties
        self._authorization = authorization or AuthorizationService(session)
        self._corrections = corrections

    # ------------------------------------------------------------------ rights (UI hints; the transaction re-checks every one)
    def can_receive(self):
        return False

    def can_reverse(self):
        actor = self._session.current_actor()
        return actor is not None and {CORRECT_PERMISSION, REVERSE_PERMISSION} <= set(actor.permissions)

    def _writer(self, expected_actor):
        actor = self._session.require_authenticated()
        if expected_actor is not None and not self._session.is_current(expected_actor):
            raise PermissionError("انتهت جلسة الدخول.")
        if MANAGE_PERMISSION not in actor.permissions:
            raise PermissionError("تسجيل استلام الذهب من المسوقجي للمالك فقط.")
        return actor

    # ------------------------------------------------------------------ reads (display only: no decision locks)
    def context(self, pool_id, *, _expected_actor=None):
        """What the receipt dialog shows for ONE pool: its facts, the CURRENT holding balance and whether it can be received (the service re-derives everything
        under the locks at posting)."""
        actor = self._writer(_expected_actor)
        pool_number = _number(pool_id, "HOLDING_NOT_FOUND", "مصدر الذهب لدى المسوقجي غير موجود.")
        with self._transaction() as connection:
            self._authorization.require_permission_read(connection, actor, VIEW_PERMISSION)
            self._authorization.require_permission_read(connection, actor, MANAGE_PERMISSION)
            holdings, parties = self._holdings(connection), self._parties(connection)
            pool = holdings.pool(pool_number)
            if pool is None:
                raise TripError("HOLDING_NOT_FOUND", "مصدر الذهب لدى المسوقجي غير موجود.")
            holder = parties.get_supplier(pool["party_id"])
            balance = Decimal(str(holdings.display_balance(logo=False, pool_id=pool_number)))
            allocated = any(r["physical_declaration"] in ALLOCATING for r in holdings.display_pool_history(pool_number))
        return {"pool_id": str(pool_number), "holder": {"id": str(holder["id"]), "name": holder["name"], "active": bool(holder["is_active"])},
                "weight": Decimal(str(pool["weight_grams"])), "source_fineness": Decimal(str(pool["fineness_permille"])), "balance": balance,
                "allocated": allocated, "receivable": False}

    # ------------------------------------------------------------------ results and replay
    @staticmethod
    def _result(receipt, remaining, duplicate):
        return {"receipt_id": str(receipt["id"]), "code": receipt_code(receipt["id"]), "piece_id": str(receipt["piece_id"]),
                "piece_code": piece_code(receipt["piece_id"]), "pool_id": str(receipt["pool_id"]), "party_id": str(receipt["party_id"]),
                "grams": str(receipt["grams"]), "remaining": str(remaining), "duplicate": duplicate}

    def _prior(self, holdings, parties, key, digest, actor):
        """The original receipt of an already-recorded key, decided BEFORE any availability is derived. Any other use of the key (another operation, another
        payload, another actor) is a REFUSED conflict."""
        receipt = holdings.receipt_by_request(key)
        if receipt is None:
            if parties.by_request(key) is not None or parties.request_key_used_elsewhere(key):
                raise TripError("REQUEST_CONFLICT", "طلب العملية مستخدم لعملية مختلفة.")
            return None
        if receipt["request_hash"] != digest or int(receipt["performed_by_user_id"]) != int(actor.user_id):
            raise TripError("REQUEST_CONFLICT", "طلب العملية مستخدم لعملية مختلفة.")
        full = holdings.receipt(receipt["id"])
        return self._result(full, Decimal(str(holdings.display_balance(logo=False, pool_id=full["pool_id"]))), True)

    def _verified(self, connection, session_actor):
        return self._authorization.require_permission_for_write(connection, session_actor, MANAGE_PERMISSION)

    def _replay(self, session_actor, key, digest, original):
        with self._transaction() as connection:
            actor = self._verified(connection, session_actor)
            result = self._prior(self._holdings(connection), self._parties(connection), key, digest, actor)
            if result is None:
                raise original
            return result

    # ------------------------------------------------------------------ the receipt
    def receive(self, pool_id, entitlement_grams, measured_grams, receipt_date, reference, notes, request_key, *, _expected_actor=None):
        session_actor = self._writer(_expected_actor)
        pool_number = _number(pool_id, "HOLDING_NOT_FOUND", "مصدر الذهب لدى المسوقجي غير موجود.")
        consumed = _grams(entitlement_grams, "الجرامات المخصومة من رصيد المسوقجي")
        measured = _grams(measured_grams, "الوزن المقاس فعليًا")
        if consumed != measured:                                   # refused before anything is read: measured grams are exactly the consumed holding grams
            raise TripError("RECEIPT_WEIGHT_MISMATCH", "الوزن المقاس فعليًا يجب أن يساوي الجرامات المخصومة من رصيد المسوقجي؛ لا تُسجَّل فروق في هذه المرحلة.")
        received_on = _entry_date(receipt_date)
        reference, notes, key = _text(reference, PAYMENT_REFERENCE_MAX, "المرجع"), _text(notes, NOTES_MAX, "الملاحظات"), _key(request_key)
        digest = hashlib.sha256(json.dumps([SOURCE, pool_number, str(consumed), received_on.isoformat(), reference, notes], ensure_ascii=False).encode()).hexdigest()
        try:
            return self._post(session_actor, pool_number, consumed, received_on, reference, notes, key, digest)
        except IntegrityError as exc:
            if getattr(exc, "errno", None) != 1062 or not any(k in str(exc) for k in KEY_CONSTRAINTS):
                raise                                              # any other integrity error is a real defect: never hidden
            return self._replay(session_actor, key, digest, exc)

    def _post(self, session_actor, pool_id, grams, received_on, reference, notes, key, digest):
        with self._transaction() as connection:
            actor = self._verified(connection, session_actor)
            market, holdings, parties = self._repository(connection), self._holdings(connection), self._parties(connection)
            pool = holdings.pool(pool_id)                           # immutable facts: a plain read only finds what to lock
            if pool is None:
                raise TripError("HOLDING_NOT_FOUND", "مصدر الذهب لدى المسوقجي غير موجود.")
            if not market.lock_lots([int(pool["lot_id"])]):         # lot -> holder party -> pool; no custody trip is locked
                raise TripError("LOT_NOT_FOUND", "الشحنة غير موجودة.")
            if parties.get_supplier(int(pool["party_id"]), lock=True) is None:        # an inactive holder is resolved normally
                raise TripError("PARTY_NOT_FOUND", "المسوقجي غير موجود.")
            pool = holdings.lock_pool(pool_id)
            replay = self._prior(holdings, parties, key, digest, actor)
            if replay is not None:
                return replay                                       # the original records, before any availability is derived
            raise TripError("LOT_RECEIPTS_DISABLED",
                            "الاستلام المادي الجزئي للسبائك لدى المسوقجي غير متاح؛ استخدم الرجوع الكامل للسبكة الأصلية بعد مراجعة سجلها.")

    # ------------------------------------------------------------------ reversal: the ONE canonical correction on the HR document
    def reverse(self, receipt_id, reason, *, _expected_actor=None):
        """Full reversal (RV) of a receipt: the received stock is removed and the STORED holding grams are restored atomically. CorrectionService re-checks
        TRANSACTION_CORRECT + INTERMEDIARY_HOLDING_REVERSE inside the transaction."""
        actor = self._session.require_authenticated()
        if _expected_actor is not None and not self._session.is_current(_expected_actor):
            raise PermissionError("انتهت جلسة الدخول.")
        if not {CORRECT_PERMISSION, REVERSE_PERMISSION} <= set(actor.permissions):
            raise PermissionError("عكس استلام الذهب من المسوقجي للمالك فقط.")
        if self._corrections is None:
            from services.correction_service import CorrectionService
            self._corrections = CorrectionService(self._session)
        return self._corrections.reverse(SOURCE, str(receipt_id), reason, _expected_actor=_expected_actor)
