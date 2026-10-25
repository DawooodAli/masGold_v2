"""Supplier MAKING payments (EGP): payment from an open market custody, marketer cash, or a selected active bank / wallet account.

A payment (SMxxxxxx) is one immutable document that posts, atomically in ONE transaction:
  * one source movement (market-custody cash, marketer cash, or bank movement), and
  * one NEGATIVE supplier-making movement (the amount the shop owes the supplier decreases).
It is not allocated to an invoice: it moves the supplier's NET making account, which may cross zero into supplier credit
(the payment is never capped at the payable balance). The supplier GOLD account is never touched. Financial accounts may
go negative (the existing rule): nothing here invents a funds check, but an inactive or missing account is refused.

Permissions (explicit grants, re-checked inside the transaction through AuthorizationService; the actor is always the
AppSession user, never a caller-supplied identity):
  SUPPLIER_MAKING_PAY          OWNER, MANAGER   record a payment
  SUPPLIER_MAKING_PAY_REVERSE  OWNER only       reverse a payment (through CorrectionService, which also requires its own
                                                TRANSACTION_CORRECT right: this one never weakens it)

An inactive supplier can be paid (and its payment reversed); an inactive FINANCIAL ACCOUNT cannot receive a new payment.

Idempotent on the request key (same key + same payload returns the first result; same key + different payload is refused).
Marketer payments lock the supplier before the marketer party; custody payments delegate locking and posting to MarketTripService.
"""
from datetime import date, datetime
from decimal import Decimal
import hashlib
import json
from uuid import UUID

from mysql.connector import IntegrityError

from repositories.financial_account_repository import FinancialAccountRepository
from repositories.market_trip_repository import entry_code, trip_code
from repositories.supplier_payment_repository import (BANK, CASH, PAYMENT_MOVEMENT, SOURCE, SupplierPaymentRepository,
                                                      payment_code)
from services import supplier_rules as rules
from services.authorization_service import AuthorizationService
from services.correction_rules import correction_code
from services.invoice_service import invoice_transaction
from services.number_text import normalize_digits
from services.supplier_service import VIEW, authenticated_actor, clean_text

PAY, REVERSE = "SUPPLIER_MAKING_PAY", "SUPPLIER_MAKING_PAY_REVERSE"
REFERENCE_MAX, NOTES_MAX = 100, 1000
CUSTODY = "CUSTODY"                         # paid with cash already in an open market custody (043): never a direct payment method
MARKETER = "MARKETER"
METHODS = (CASH, BANK, CUSTODY, MARKETER)
TRIP_PERMISSION, HOLDING_MANAGE_PERMISSION = "MARKET_TRIP_MANAGE", "INTERMEDIARY_HOLDING_MANAGE"
METHOD_TEXT = {CASH: "نقدًا من الخزنة الرئيسية", BANK: "تحويل من حساب بنكي", CUSTODY: "من عهدة السوق",
               MARKETER: "من نقدية المسوقجي"}


def _key(value):
    try:
        return str(UUID(str(value)))
    except (ValueError, TypeError, AttributeError):
        raise rules.SupplierRuleError("REQUEST_INVALID", "أعد فتح النموذج وحاول مرة أخرى.") from None


def _is_request_key_violation(exc):
    """True only for the duplicate-entry error of THIS table's request-key unique key (uq_smp_request)."""
    return getattr(exc, "errno", None) == 1062 and "uq_smp_request" in str(exc)


def _supplier_id(value):
    try:
        number = int(str(value).strip())
    except (TypeError, ValueError):
        raise rules.SupplierRuleError("SUPPLIER_NOT_FOUND", "المورد غير موجود.") from None
    if number <= 0:
        raise rules.SupplierRuleError("SUPPLIER_NOT_FOUND", "المورد غير موجود.")
    return number


def _payment_date(value):
    """The business date of the payment (required). Same ISO date convention as the supplier invoice date."""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = normalize_digits(str(value or "")).strip()
    if not text:
        raise rules.SupplierRuleError("DATE_REQUIRED", "تاريخ الدفع مطلوب.")
    try:
        return date.fromisoformat(text)
    except ValueError:
        raise rules.SupplierRuleError("DATE_INVALID", "تاريخ الدفع غير صحيح.") from None


def _bank_id(value):
    try:
        number = int(str(value).strip())
    except (TypeError, ValueError):
        raise rules.SupplierRuleError("ACCOUNT_REQUIRED", "اختر الحساب البنكي.") from None
    if number <= 0:
        raise rules.SupplierRuleError("ACCOUNT_REQUIRED", "اختر الحساب البنكي.")
    return number


def _source_id(value, code, message):
    try:
        number = int(str(value).strip())
    except (TypeError, ValueError):
        raise rules.SupplierRuleError(code, message) from None
    if number <= 0:
        raise rules.SupplierRuleError(code, message)
    return number


class SupplierPaymentService:
    def __init__(self, session, *, transaction_factory=invoice_transaction, repository=SupplierPaymentRepository,
                 accounts=FinancialAccountRepository, authorization=None, corrections=None):
        self._session, self._transaction, self._repository = session, transaction_factory, repository
        self._accounts = accounts
        self._authorization = authorization or AuthorizationService(session)
        self._corrections = corrections

    # ------------------------------------------------------------------ UI hints (never the authority)
    def _can(self, permission):
        actor = self._session.current_actor()
        return actor is not None and permission in actor.permissions

    def can_pay(self):
        return self._can(PAY)

    def can_reverse(self):
        return self._can(REVERSE)

    # ------------------------------------------------------------------ reads
    def payment_context(self, supplier_id, *, _expected_actor=None):
        """Dialog hints: supplier position, active bank accounts, open custody cash and marketer cash."""
        actor = authenticated_actor(self._session, PAY, "ليس لديك صلاحية دفع مصنعيات الموردين.", _expected_actor)
        supplier_id = _supplier_id(supplier_id)
        with self._transaction() as connection:
            self._authorization.require_permission_read(connection, actor, PAY)
            repository = self._repository(connection)
            supplier = repository.get_supplier(supplier_id)
            if supplier is None:
                raise rules.SupplierRuleError("SUPPLIER_NOT_FOUND", "المورد غير موجود.")
            banks = repository.active_bank_accounts()
            balance = rules.money_amount(repository.making_balance(supplier_id))
            custodies, marketers = [], []
            if TRIP_PERMISSION in actor.permissions:
                self._authorization.require_permission_read(connection, actor, TRIP_PERMISSION)
                from repositories.market_trip_repository import MarketTripRepository
                from services.market_trip_service import OPEN, MarketTripService
                trip_repository = MarketTripRepository(connection)
                page, page_size = 1, 50
                while True:
                    rows = trip_repository.trip_page(OPEN, page, page_size)
                    for trip in rows[:page_size]:
                        cash = MarketTripService._custody(trip_repository, trip["id"])["cash"]["outstanding"]
                        if cash > 0:
                            custodies.append({"id": str(trip["id"]), "code": trip_code(trip["id"]), "cash": cash})
                    if len(rows) <= page_size:
                        break
                    page += 1
            if HOLDING_MANAGE_PERMISSION in actor.permissions:
                self._authorization.require_permission_read(connection, actor, HOLDING_MANAGE_PERMISSION)
                from repositories.marketer_custody_repository import MarketerCustodyRepository
                rows = MarketerCustodyRepository(connection).cash_payment_sources()
                marketers = [{"id": str(r["id"]), "name": r["name"], "active": bool(r["is_active"]),
                              "supplier": bool(r["is_supplier"]), "cash": rules.money_amount(r["balance"]),
                              "self": int(r["id"]) == supplier_id} for r in rows]
        return {"supplier": {"id": str(supplier["id"]), "code": rules.supplier_code(supplier["id"]),
                             "name": supplier["name"], "active": bool(supplier["is_active"])},
                "making": balance, "making_direction": rules.direction(balance),
                "custodies": custodies, "marketers": marketers,
                "banks": [{"id": str(b["id"]), "name": b["name"], "account_type": b["account_type"],
                           "label": b["account_label"] or ""} for b in banks]}

    def payment_detail(self, payment_id, *, _expected_actor=None):
        """One payment with its method, account, actor, linked movements and reversal (the original is never altered)."""
        actor = authenticated_actor(self._session, VIEW, "لا توجد صلاحية لعرض الموردين.", _expected_actor)
        with self._transaction() as connection:
            self._authorization.require_permission_read(connection, actor, VIEW)
            repository = self._repository(connection)
            row = repository.payment(payment_id)
            if row is None:
                raise rules.SupplierRuleError("PAYMENT_NOT_FOUND", "دفعة المصنعيات غير موجودة.")
        return self.document(row)

    @staticmethod
    def document(row):
        bank = row["payment_method"] == BANK
        custody = row["payment_method"] == CUSTODY
        marketer = row["payment_method"] == MARKETER
        return {"id": str(row["id"]), "code": payment_code(row["id"]), "supplier_id": str(row["supplier_id"]),
                "supplier_name": row["supplier_name"], "method": row["payment_method"],
                "method_text": METHOD_TEXT.get(row["payment_method"], METHOD_TEXT[CASH]),
                "account_id": "" if custody else str(row["marketer_party_id"] if marketer
                                                       else row["bank_account_id"] if bank else row["cash_account_id"]),
                "account_name": (entry_code(row["custody_trip_id"], row["custody_entry_no"]) if custody
                                 else row["marketer_name"] if marketer
                                 else row["bank_account_name"] if bank else row["cash_account_name"]),
                "custody_reference": entry_code(row["custody_trip_id"], row["custody_entry_no"]) if custody else "",
                "amount": row["amount"], "payment_date": row["payment_date"], "reference": row["reference"] or "",
                "notes": row["notes"] or "", "balance_before": row["balance_before"],
                "balance_after": row["balance_after"], "actor": row["actor_name"] or "",
                "occurred_at": row["occurred_at"],
                "self_payment_confirmed": bool(row.get("self_payment_confirmed")),
                "reversed": row["reversal_id"] is not None,
                "reversal_code": None if row["reversal_id"] is None else correction_code("FULL_REVERSAL", row["reversal_id"]),
                "reversal_reason": row["reversal_reason"], "reversal_at": row["reversal_at"],
                "reversal_actor": row["reversal_actor_name"]}

    # ------------------------------------------------------------------ the payment
    def pay(self, supplier_id, method, source_id, amount, payment_date, reference, notes, request_key, *,
            confirm_same_party=False, _expected_actor=None):
        session_actor = authenticated_actor(self._session, PAY, "ليس لديك صلاحية دفع مصنعيات الموردين.", _expected_actor)
        supplier_id = _supplier_id(supplier_id)
        method = str(method or "").strip().upper()
        if method not in METHODS:
            raise rules.SupplierRuleError("METHOD_INVALID", "اختر نقدًا من الخزنة أو تحويلًا بنكيًا أو عهدة السوق أو نقدية المسوقجي.")
        value = rules.parse_amount(amount)
        paid_on = _payment_date(payment_date)
        reference_text = clean_text(reference, REFERENCE_MAX, "المرجع")
        notes_text = clean_text(notes, NOTES_MAX, "الملاحظات")
        key = _key(request_key)
        # Validate supplier role early: CASH, BANK, CUSTODY need supplier role; MARKETER can use intermediary-only party
        if method != MARKETER:
            with self._transaction() as connection:
                repository = self._repository(connection)
                supplier = repository.get_supplier(supplier_id)
                if supplier is None:
                    raise rules.SupplierRuleError("SUPPLIER_NOT_FOUND", "المورد غير موجود.")
                if not supplier["is_supplier"]:
                    raise rules.SupplierRuleError("SUPPLIER_ROLE_REQUIRED", "المورد يجب أن يكون له دور المورد.")
        if method == CASH:
            cash_account_id = _source_id(source_id, "CASH_ACCOUNT_REQUIRED", "اختر خزنة رئيسية.")
            bank_id = cash_account_id
            marketer_id = None
        else:
            selected_id = (_bank_id(source_id) if method == BANK else
                           _source_id(source_id, "CUSTODY_REQUIRED", "اختر عهدة السوق.")
                           if method == CUSTODY else
                           _source_id(source_id, "MARKETER_NOT_FOUND", "اختر مسوقجيًا لديه نقدية."))
            marketer_id = selected_id if method == MARKETER else None
            bank_id = selected_id if method == BANK else None
        if not isinstance(confirm_same_party, bool):
            raise rules.SupplierRuleError("CONFIRMATION_INVALID", "تأكيد الطرف نفسه غير صحيح.")
        confirmed = confirm_same_party
        if method != MARKETER and confirmed:
            raise rules.SupplierRuleError("CONFIRMATION_INVALID", "تأكيد الطرف نفسه لا ينطبق على مصدر الدفع المختار.")
        if method == CUSTODY:
            from services.market_trip_service import MarketTripService
            result = MarketTripService(self._session).pay_supplier(
                selected_id, supplier_id, value, paid_on, reference_text, notes_text, key, _expected_actor=session_actor)
            return {"id": result["payment_id"], "code": result["payment_code"], "supplier_id": str(supplier_id),
                    "method": CUSTODY, "amount": str(value), "balance_before": result["balance_before"],
                    "balance_after": result["balance_after"], "duplicate": bool(result.get("duplicate")),
                    "custody_trip_id": result["trip_id"], "custody_code": result["code"]}
        digest = hashlib.sha256(json.dumps(
            [supplier_id, method, bank_id, marketer_id, confirmed, str(value), paid_on.isoformat(), reference_text, notes_text],
            ensure_ascii=False).encode()).hexdigest()
        try:
            return self._post(session_actor, supplier_id, method, bank_id, marketer_id, confirmed, value, paid_on,
                              reference_text, notes_text, key, digest)
        except IntegrityError as exc:
            if not _is_request_key_violation(exc):
                raise                                  # any other integrity error is a real defect: never hidden
            # Two requests with the same key passed their checks together (different suppliers do not share a lock): the
            # database let one commit and refused this one, whose transaction is rolled back. Decide from a FRESH read.
            return self._replay(session_actor, key, digest, exc)

    def _post(self, session_actor, supplier_id, method, bank_id, marketer_id, confirm_same_party, value, paid_on,
              reference_text, notes_text, key, digest):
        with self._transaction() as connection:
            actor = self._authorization.require_permission_for_write(connection, session_actor, PAY)
            repository, accounts = self._repository(connection), self._accounts(connection)
            supplier = repository.get_supplier(supplier_id, lock=True)
            if supplier is None:
                raise rules.SupplierRuleError("SUPPLIER_NOT_FOUND", "المورد غير موجود.")
            rules.require_supplier_role(supplier)                            # making payments belong to a SUPPLIER
            prior = repository.by_request(key)
            if prior is not None:
                if prior["request_hash"] != digest or int(prior["performed_by_user_id"]) != int(actor.user_id):
                    raise rules.SupplierRuleError("REQUEST_CONFLICT", "طلب الدفع مستخدم لعملية مختلفة.")
                return self._result(prior, duplicate=True)
            marketer_repository = None
            if method == MARKETER:
                self._authorization.require_permission_for_write(connection, session_actor, HOLDING_MANAGE_PERMISSION)
                from repositories.marketer_custody_repository import MarketerCustodyRepository
                marketer_repository = MarketerCustodyRepository(connection)
                if marketer_repository.by_request(key) is not None or marketer_repository.key_used_elsewhere(key):
                    raise rules.SupplierRuleError("REQUEST_CONFLICT", "طلب الدفع مستخدم لعملية مختلفة.")
                marketer = marketer_repository.party(marketer_id, lock=True)
                if marketer is None or not marketer["is_intermediary"]:
                    raise rules.SupplierRuleError("MARKETER_NOT_FOUND", "اختر مسوقجيًا لديه نقدية.")
                is_self = int(marketer["id"]) == int(supplier_id)
                if is_self and not confirm_same_party:
                    raise rules.SupplierRuleError("SELF_PAYMENT_CONFIRMATION_REQUIRED",
                                                  "المورد هو نفسه المسوقجي: أكّد صراحةً تسجيل الدفعة من عهدته.")
                if confirm_same_party and not is_self:
                    raise rules.SupplierRuleError("CONFIRMATION_INVALID", "تأكيد الطرف نفسه لا يطابق المسوقجي المختار.")
                held_cash = Decimal(str(marketer_repository.cash_balance(marketer_id)))
                if value > held_cash:
                    raise rules.SupplierRuleError("MARKETER_CASH_INSUFFICIENT",
                                                  f"النقدية المتاحة لدى المسوقجي {held_cash:,.2f} ج فقط.")
                account = None
            elif method == CASH:
                account = accounts.account("CASH", bank_id, lock=True)
                if account is None:
                    raise rules.SupplierRuleError("ACCOUNT_NOT_FOUND", "الحساب النقدي غير موجود.")
            else:
                account = accounts.account("BANK", bank_id, lock=True)
                if account is None:
                    raise rules.SupplierRuleError("ACCOUNT_NOT_FOUND", "الحساب البنكي غير موجود.")
            # An INACTIVE supplier may still be paid: deactivation stops new purchases, never the settlement of what is owed.
            if account is not None and not account["is_active"]:
                raise rules.SupplierRuleError("ACCOUNT_INACTIVE", f"الحساب «{account['name']}» غير نشط.")
            making_type = repository.making_type_id(PAYMENT_MOVEMENT)
            if making_type is None:
                raise RuntimeError("supplier making movement type PAYMENT is missing (migration 036)")
            before = rules.money_amount(repository.making_balance_current(supplier_id))
            after = rules.making_after_payment(before, value)
            now = repository.db_now()
            payment_id = repository.insert_header(
                supplier_id=supplier_id, method=method, cash_account_id=int(account["id"]) if method == CASH else None,
                bank_account_id=int(account["id"]) if method == BANK else None, marketer_party_id=marketer_id,
                self_payment_confirmed=confirm_same_party, amount=value, payment_date=paid_on,
                reference=reference_text, notes=notes_text, before=before, after=after, key=key, digest=digest,
                actor=actor, now=now)
            code = payment_code(payment_id)
            label = f"دفعة مصنعيات للمورد {code}"
            if method == BANK:
                money_id = repository.post_money(
                    ledger=method, account_id=account["id"], payment_id=payment_id, code=code, amount=value,
                    description=f"{label} — {supplier['name']}", notes=notes_text, actor=actor, now=now)
                repository.insert_leg(payment_id=payment_id, kind="MONEY", bank_movement_id=money_id)
            elif method == CASH:
                money_id = repository.post_money(
                    ledger=method, account_id=account["id"], payment_id=payment_id, code=code, amount=value,
                    description=f"{label} — {supplier['name']}", notes=notes_text, actor=actor, now=now)
                repository.insert_leg(payment_id=payment_id, kind="MONEY", cash_movement_id=money_id)
            else:
                from repositories.marketer_custody_repository import document_code
                marketer = marketer_repository.party(marketer_id)
                document_id = marketer_repository.insert_document(
                    kind="SUPPLIER_PAY", party_id=marketer_id, cash_amount=value, weight=None, counterparty=None,
                    reference_total=None, deviation=None, acknowledged=False, document_date=paid_on, reference=reference_text,
                    notes=notes_text, key=key, digest=digest, actor=actor, now=now)
                marketer_code = document_code("SUPPLIER_PAY", document_id)
                marketer_repository.insert_cash_movement(
                    party_id=marketer_id, document_id=document_id, doc_kind="SUPPLIER_PAY", movement_type="SUPPLIER_PAY",
                    amount=value, delta=-value)
                marketer_repository.insert_supplier_payment_link(
                    payment_id=payment_id, document_id=document_id, party_id=marketer_id, amount=value)
                marketer_repository.audit(actor=actor, event_type="MARKETER_SUPPLIER_MAKING_PAYMENT", entity_id=document_id,
                                          reference=marketer_code, description=f"{label} — من {marketer['name']} {value:,.2f} ج",
                                          metadata={"supplier_id": supplier_id, "marketer_party_id": marketer_id,
                                                    "payment_id": payment_id, "amount": str(value)},
                                          now=now, entity_type="MARKETER_DOCUMENT")
            making_id = repository.insert_making_movement(
                supplier_id=supplier_id, type_id=making_type, amount_delta=-value, source_type=SOURCE,
                source_id=payment_id, reference=code, description=label, now=now, user_id=actor.user_id)
            repository.insert_leg(payment_id=payment_id, kind="MAKING", making_movement_id=making_id)
            repository.audit(actor=actor, event_type="SUPPLIER_MAKING_PAYMENT", entity_id=payment_id,
                             entity_type=SOURCE, reference=code, description=f"{label} {value:,.2f} ج",
                             metadata={"supplier_id": supplier_id, "method": method,
                                       "account_id": int(account["id"]) if account is not None else None,
                                       "marketer_party_id": marketer_id,
                                       "self_payment_confirmed": confirm_same_party,
                                       "amount": str(value), "before": str(before), "after": str(after),
                                       "payment_date": paid_on.isoformat(), "reference": reference_text,
                                       "employee_id": actor.employee_id}, now=now)
            if not self._session.is_current(session_actor):
                raise PermissionError("انتهت جلسة الدخول.")
        return {"id": str(payment_id), "code": code, "supplier_id": str(supplier_id), "method": method,
                "amount": str(value), "balance_before": str(before), "balance_after": str(after), "duplicate": False,
                "marketer_party_id": None if marketer_id is None else str(marketer_id)}

    def _replay(self, session_actor, key, digest, original):
        """The losing side of a concurrent same-key request, in a new transaction: the committed document with the same
        payload and user is returned as the duplicate; any other payload is a REQUEST_CONFLICT. Nothing is written."""
        with self._transaction() as connection:
            actor = self._authorization.require_permission_for_write(connection, session_actor, PAY)
            prior = self._repository(connection).by_request(key)
            if prior is None:
                raise original                                           # nothing explains the violation: do not hide it
            if prior["request_hash"] != digest or int(prior["performed_by_user_id"]) != int(actor.user_id):
                raise rules.SupplierRuleError("REQUEST_CONFLICT", "طلب الدفع مستخدم لعملية مختلفة.")
            return self._result(prior, duplicate=True)

    @staticmethod
    def _result(row, duplicate):
        return {"id": str(row["id"]), "code": payment_code(row["id"]), "supplier_id": str(row["supplier_id"]),
                "method": row["payment_method"], "amount": str(row["amount"]),
                "balance_before": str(row["balance_before"]), "balance_after": str(row["balance_after"]),
                "marketer_party_id": None if row.get("marketer_party_id") is None else str(row["marketer_party_id"]),
                "duplicate": duplicate}

    # ------------------------------------------------------------------ reversal (CorrectionService conventions)
    def reverse(self, payment_id, reason, *, _expected_actor=None):
        """Full reversal (RV) of one payment: the OWNER-only right is checked here as a hint and again, inside the
        transaction, by CorrectionService (together with its own TRANSACTION_CORRECT right)."""
        authenticated_actor(self._session, REVERSE, "عكس دفعة المصنعيات للمالك فقط.", _expected_actor)
        if self._corrections is None:
            from services.correction_service import CorrectionService
            self._corrections = CorrectionService(self._session)
        return self._corrections.reverse(SOURCE, str(payment_id), reason, _expected_actor=_expected_actor)
