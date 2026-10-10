"""Supplier MAKING payments (EGP): a cash payment from MAIN_SAFE or a payment from a selected active bank / wallet account.

A payment (SMxxxxxx) is one immutable document that posts, atomically in ONE transaction:
  * one real money OUT movement (cash_movements on MAIN_SAFE, or bank_movements on the selected account), and
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
Lock order: actor -> supplier row -> financial account -> request key (a reversal takes the same order).
"""
from datetime import date, datetime
import hashlib
import json
from uuid import UUID

from mysql.connector import IntegrityError

from repositories.financial_account_repository import FinancialAccountRepository
from repositories.market_trip_repository import entry_code
from repositories.supplier_payment_repository import (BANK, CASH, PAYMENT_MOVEMENT, SOURCE, SupplierPaymentRepository,
                                                      payment_code)
from services import supplier_rules as rules
from services.authorization_service import AuthorizationService
from services.correction_rules import correction_code
from services.invoice_service import invoice_transaction
from services.number_text import normalize_digits
from services.supplier_service import VIEW, authenticated_actor, clean_text

PAY, REVERSE = "SUPPLIER_MAKING_PAY", "SUPPLIER_MAKING_PAY_REVERSE"
METHODS = (CASH, BANK)
REFERENCE_MAX, NOTES_MAX = 100, 1000
CUSTODY = "CUSTODY"                         # paid with cash already in an open market custody (043): never a direct payment method
METHOD_TEXT = {CASH: "نقدًا من الخزنة الرئيسية", BANK: "تحويل من حساب بنكي", CUSTODY: "من عهدة السوق"}


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
        """What the payment dialog shows: the supplier, its CURRENT making position, MAIN_SAFE and the active bank accounts."""
        actor = authenticated_actor(self._session, PAY, "ليس لديك صلاحية دفع مصنعيات الموردين.", _expected_actor)
        supplier_id = _supplier_id(supplier_id)
        with self._transaction() as connection:
            self._authorization.require_permission_read(connection, actor, PAY)
            repository = self._repository(connection)
            supplier = repository.get_supplier(supplier_id)
            if supplier is None:
                raise rules.SupplierRuleError("SUPPLIER_NOT_FOUND", "المورد غير موجود.")
            safe = repository.main_safe()
            banks = repository.active_bank_accounts()
            balance = rules.money_amount(repository.making_balance(supplier_id))
        return {"supplier": {"id": str(supplier["id"]), "code": rules.supplier_code(supplier["id"]),
                             "name": supplier["name"], "active": bool(supplier["is_active"])},
                "making": balance, "making_direction": rules.direction(balance),
                "safe": None if safe is None else {"id": str(safe["id"]), "name": safe["name"],
                                                   "active": bool(safe["is_active"])},
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
        return {"id": str(row["id"]), "code": payment_code(row["id"]), "supplier_id": str(row["supplier_id"]),
                "supplier_name": row["supplier_name"], "method": row["payment_method"],
                "method_text": METHOD_TEXT[row["payment_method"]],
                "account_id": "" if custody else str(row["bank_account_id"] if bank else row["cash_account_id"]),
                "account_name": (entry_code(row["custody_trip_id"], row["custody_entry_no"]) if custody
                                 else row["bank_account_name"] if bank else row["cash_account_name"]),
                "custody_reference": entry_code(row["custody_trip_id"], row["custody_entry_no"]) if custody else "",
                "amount": row["amount"], "payment_date": row["payment_date"], "reference": row["reference"] or "",
                "notes": row["notes"] or "", "balance_before": row["balance_before"],
                "balance_after": row["balance_after"], "actor": row["actor_name"] or "",
                "occurred_at": row["occurred_at"],
                "reversed": row["reversal_id"] is not None,
                "reversal_code": None if row["reversal_id"] is None else correction_code("FULL_REVERSAL", row["reversal_id"]),
                "reversal_reason": row["reversal_reason"], "reversal_at": row["reversal_at"],
                "reversal_actor": row["reversal_actor_name"]}

    # ------------------------------------------------------------------ the payment
    def pay(self, supplier_id, method, bank_account_id, amount, payment_date, reference, notes, request_key, *,
            _expected_actor=None):
        session_actor = authenticated_actor(self._session, PAY, "ليس لديك صلاحية دفع مصنعيات الموردين.", _expected_actor)
        supplier_id = _supplier_id(supplier_id)
        method = str(method or "").strip().upper()
        if method not in METHODS:
            raise rules.SupplierRuleError("METHOD_INVALID", "اختر طريقة الدفع: نقدًا من الخزنة الرئيسية أو تحويل بنكي.")
        bank_id = _bank_id(bank_account_id) if method == BANK else None
        value = rules.parse_amount(amount)
        paid_on = _payment_date(payment_date)
        reference_text = clean_text(reference, REFERENCE_MAX, "المرجع")
        notes_text = clean_text(notes, NOTES_MAX, "الملاحظات")
        key = _key(request_key)
        digest = hashlib.sha256(json.dumps(
            [supplier_id, method, bank_id, str(value), paid_on.isoformat(), reference_text, notes_text],
            ensure_ascii=False).encode()).hexdigest()
        try:
            return self._post(session_actor, supplier_id, method, bank_id, value, paid_on, reference_text, notes_text, key,
                              digest)
        except IntegrityError as exc:
            if not _is_request_key_violation(exc):
                raise                                  # any other integrity error is a real defect: never hidden
            # Two requests with the same key passed their checks together (different suppliers do not share a lock): the
            # database let one commit and refused this one, whose transaction is rolled back. Decide from a FRESH read.
            return self._replay(session_actor, key, digest, exc)

    def _post(self, session_actor, supplier_id, method, bank_id, value, paid_on, reference_text, notes_text, key, digest):
        with self._transaction() as connection:
            actor = self._authorization.require_permission_for_write(connection, session_actor, PAY)
            repository, accounts = self._repository(connection), self._accounts(connection)
            supplier = repository.get_supplier(supplier_id, lock=True)
            if supplier is None:
                raise rules.SupplierRuleError("SUPPLIER_NOT_FOUND", "المورد غير موجود.")
            rules.require_supplier_role(supplier)                            # making payments belong to a SUPPLIER
            if method == CASH:
                safe = repository.main_safe()
                if safe is None:
                    raise rules.SupplierRuleError("ACCOUNT_NOT_FOUND", "الخزنة الرئيسية غير موجودة.")
                account = accounts.account("CASH", safe["id"], lock=True)
            else:
                account = accounts.account("BANK", bank_id, lock=True)
            if account is None:
                raise rules.SupplierRuleError("ACCOUNT_NOT_FOUND", "الحساب غير موجود.")
            prior = repository.by_request(key)
            if prior is not None:
                if prior["request_hash"] != digest or int(prior["performed_by_user_id"]) != int(actor.user_id):
                    raise rules.SupplierRuleError("REQUEST_CONFLICT", "طلب الدفع مستخدم لعملية مختلفة.")
                return self._result(prior, duplicate=True)
            # An INACTIVE supplier may still be paid: deactivation stops new purchases, never the settlement of what is owed.
            if not account["is_active"]:
                raise rules.SupplierRuleError("ACCOUNT_INACTIVE", f"الحساب «{account['name']}» غير نشط.")
            making_type = repository.making_type_id(PAYMENT_MOVEMENT)
            if making_type is None:
                raise RuntimeError("supplier making movement type PAYMENT is missing (migration 036)")
            before = rules.money_amount(repository.making_balance_current(supplier_id))
            after = rules.making_after_payment(before, value)
            now = repository.db_now()
            payment_id = repository.insert_header(
                supplier_id=supplier_id, method=method, cash_account_id=int(account["id"]) if method == CASH else None,
                bank_account_id=int(account["id"]) if method == BANK else None, amount=value, payment_date=paid_on,
                reference=reference_text, notes=notes_text, before=before, after=after, key=key, digest=digest,
                actor=actor, now=now)
            code = payment_code(payment_id)
            label = f"دفعة مصنعيات للمورد {code}"
            money_id = repository.post_money(
                ledger=method, account_id=account["id"], payment_id=payment_id, code=code, amount=value,
                description=f"{label} — {supplier['name']}", notes=notes_text, actor=actor, now=now)
            making_id = repository.insert_making_movement(
                supplier_id=supplier_id, type_id=making_type, amount_delta=-value, source_type=SOURCE,
                source_id=payment_id, reference=code, description=label, now=now, user_id=actor.user_id)
            repository.insert_leg(payment_id=payment_id, kind="MONEY",
                                  cash_movement_id=money_id if method == CASH else None,
                                  bank_movement_id=money_id if method == BANK else None)
            repository.insert_leg(payment_id=payment_id, kind="MAKING", making_movement_id=making_id)
            repository.audit(actor=actor, event_type="SUPPLIER_MAKING_PAYMENT", entity_id=payment_id,
                             entity_type=SOURCE, reference=code, description=f"{label} {value:,.2f} ج",
                             metadata={"supplier_id": supplier_id, "method": method, "account_id": int(account["id"]),
                                       "amount": str(value), "before": str(before), "after": str(after),
                                       "payment_date": paid_on.isoformat(), "reference": reference_text,
                                       "employee_id": actor.employee_id}, now=now)
            if not self._session.is_current(session_actor):
                raise PermissionError("انتهت جلسة الدخول.")
        return {"id": str(payment_id), "code": code, "supplier_id": str(supplier_id), "method": method,
                "amount": str(value), "balance_before": str(before), "balance_after": str(after), "duplicate": False}

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
