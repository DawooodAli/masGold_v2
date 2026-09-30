"""Dead Cash V1 — customer receivable write-off (028 on the 005 scaffolding).

A write-off (DCxxxxxx) removes part or all of a POSITIVE customer receivable (على العميل) judged
uncollectible. It posts ONE canonical customer-ledger row (DEAD_CASH_WRITE_OFF, −amount) and one immutable
dead_cash_writeoffs record, marks the customer with DEAD_CASH_WARNING (kept even at zero balance) and audits
it. It is NOT a payment: no cash / bank / wallet movement is ever created, and no invoice or earlier ledger
row is touched. A mistake is corrected by a FULL reversal only (DEAD_CASH_REVERSAL, +amount); the write-off
record stays and is shown as معكوس. Recovery of written-off money is not part of V1.

Both actions need the actor's CURRENT password (re-authentication through AuthenticationService — never a
fixed password) AND the strict locking write permission inside the transaction:
  write-off  DEAD_CASH_MANAGE      reversal  DEAD_CASH_REVERSE      (existing permissions, OWNER only)
Lock order: actor → customer (+ its ledger rows) → write-off record / warning flag.
"""
from decimal import Decimal
import hashlib
import json
from uuid import UUID

from repositories.customer_account_repository import CustomerAccountRepository
from repositories.customer_repository import CustomerRepository
from repositories.dead_cash_repository import DeadCashRepository
from services.authorization_service import AuthorizationService
from services.correction_service import _money_value
from services.customer_service import CustomerService
from services.invoice_service import invoice_transaction

MANAGE, REVERSE, VIEW = "DEAD_CASH_MANAGE", "DEAD_CASH_REVERSE", "DEAD_CASH_VIEW"
SOURCE = "DEAD_CASH"
CHANGED_MESSAGE = "رصيد العميل تغير منذ فتح نافذة الشطب. أعد فتح العملية للمراجعة."


def writeoff_code(writeoff_id):
    return f"DC{int(writeoff_id):06d}"


def reversal_code(writeoff_id):
    return f"DCR{int(writeoff_id):06d}"


class DeadCashError(ValueError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


class _Discard(Exception):
    pass


def _reason(text, label):
    reason = (text or "").strip()
    if not reason:
        raise DeadCashError("REASON_REQUIRED", f"اكتب {label}.")
    if len(reason) > 1000:
        raise DeadCashError("REASON_TOO_LONG", f"{label} طويل جداً.")
    return reason


def _money(text, code, message):
    try:
        return _money_value(text, code, message)
    except ValueError as exc:                       # CorrectionError → the Dead Cash refusal code
        raise DeadCashError(code, message) from exc


class DeadCashService:
    def __init__(self, session, *, transaction_factory=invoice_transaction, repository=DeadCashRepository,
                 balances=CustomerAccountRepository, customers=CustomerRepository, authorization=None,
                 authentication=None):
        self._session, self._transaction = session, transaction_factory
        self._repository, self._balances, self._customers = repository, balances, customers
        self._authorization = authorization or AuthorizationService(session)
        self._authentication = authentication

    # ------------------------------------------------------------ plumbing
    def _actor(self, expected_actor, permission):
        actor = self._session.require_authenticated()
        if expected_actor is not None and not self._session.is_current(expected_actor):
            raise PermissionError("انتهت جلسة الدخول.")
        if permission not in actor.permissions:
            raise PermissionError("لا توجد صلاحية لشطب رصيد العملاء." if permission == MANAGE
                                  else "لا توجد صلاحية لعكس شطب الرصيد.")
        return actor

    def _confirm_password(self, actor, password):
        """Re-authenticate the logged-in actor BEFORE any lock is taken (Argon2 is deliberately slow)."""
        from services.authentication_service import AuthenticationError, AuthenticationService
        authentication = self._authentication or AuthenticationService()
        try:
            authentication.verify_user_password(int(actor.user_id), password)
        except AuthenticationError:
            raise DeadCashError("PASSWORD_INVALID", "كلمة المرور غير صحيحة أو انتظر قليلاً ثم أعد المحاولة.") from None

    def can_write_off(self):
        actor = self._session.current_actor()
        return bool(actor and MANAGE in actor.permissions)

    def can_reverse(self):
        actor = self._session.current_actor()
        return bool(actor and REVERSE in actor.permissions)

    # ------------------------------------------------------------ read: the dialog's starting point
    def context(self, customer_id, *, _expected_actor=None):
        """The authoritative balance the write-off dialog opens with (non-locking permission check)."""
        actor = self._actor(_expected_actor, MANAGE)
        cid = str(CustomerService._customer_id(customer_id))
        try:
            with self._transaction() as connection:
                self._authorization.require_permission_read(connection, actor, MANAGE)
                customer = self._customers(connection).get_customer_by_id(int(cid))
                if not customer:
                    raise DeadCashError("CUSTOMER_NOT_FOUND", "العميل غير موجود.")
                balance = Decimal(str(self._customers(connection).get_balance(int(cid))))
                raise _Discard({"customer_id": cid, "name": customer["name"], "balance": str(balance),
                                "eligible": balance > 0})
        except _Discard as done:
            return done.args[0]

    # ------------------------------------------------------------ write-off
    def write_off(self, customer_id, amount, reason, password, expected_balance, request_key, *,
                  _expected_actor=None):
        session_actor = self._actor(_expected_actor, MANAGE)
        cid = str(CustomerService._customer_id(customer_id))
        value = _money(amount, "AMOUNT_INVALID", "اكتب مبلغ الشطب بالأرقام بخانتين عشريتين على الأكثر.")
        if value <= 0:
            raise DeadCashError("AMOUNT_INVALID", "مبلغ الشطب يجب أن يكون أكبر من صفر.")
        value = value.quantize(Decimal("0.01"))                 # money is always two decimals
        reason = _reason(reason, "سبب شطب الرصيد")
        try:
            expected = Decimal(str(expected_balance).strip())
            key = str(UUID(str(request_key)))
        except (ArithmeticError, ValueError, TypeError, AttributeError):
            raise DeadCashError("REQUEST_INVALID", "أعد فتح نافذة الشطب وحاول مرة أخرى.") from None
        digest = hashlib.sha256(json.dumps([cid, str(value), reason, str(expected)], ensure_ascii=False)
                                .encode()).hexdigest()
        self._confirm_password(session_actor, password)
        with self._transaction() as connection:
            actor = self._authorization.require_permission_for_write(connection, session_actor, MANAGE)
            repository = self._repository(connection)
            before = self._balances(connection).lock_balance(cid)        # customer + ledger rows, locked
            prior = repository.by_request(key)
            if prior:
                if prior["request_hash"] != digest or int(prior["created_by_user_id"]) != int(actor.user_id):
                    raise DeadCashError("REQUEST_CONFLICT", "طلب الشطب مستخدم لعملية مختلفة.")
                return self._result(prior["writeoff_id"], prior["amount"], prior["balance_before"],
                                    prior["balance_after"], duplicate=True)
            if before != expected:
                raise DeadCashError("BALANCE_CHANGED", CHANGED_MESSAGE)   # never silently adjusted
            if before <= 0:
                raise DeadCashError("NOT_RECEIVABLE", "لا يوجد رصيد على العميل لشطبه.")
            if value > before:
                raise DeadCashError("EXCEEDS_BALANCE", "مبلغ الشطب أكبر من الرصيد المستحق على العميل.")
            after = before - value
            transaction_id = self._customers(connection).add_transaction(
                customer_id=int(cid), type_code="DEAD_CASH_WRITE_OFF", amount_delta=-value,
                created_by_user_id=actor.user_id, reference_type=SOURCE, description="شطب رصيد", notes=reason)
            writeoff_id = repository.insert_writeoff(
                customer_id=cid, amount=value, balance_before=before, balance_after=after,
                writeoff_transaction_id=transaction_id, reason=reason, created_by_user_id=actor.user_id,
                request_key=key, request_hash=digest)
            code = writeoff_code(writeoff_id)
            if repository.get_active_warning(cid, for_update=True) is None:
                repository.create_warning(customer_id=cid, reason=f"سبق شطب رصيد لهذا العميل — {code}",
                                          created_by_user_id=actor.user_id, related_dead_cash_id=writeoff_id)
            self._audit(repository, actor, "DEAD_CASH_WRITTEN_OFF", writeoff_id, code, f"شطب رصيد {code}",
                        {"reference": code, "customer_id": cid, "amount": str(value), "before": str(before),
                         "after": str(after), "reason": reason, "employee_id": actor.employee_id})
            if not self._session.is_current(session_actor):
                raise PermissionError("انتهت جلسة الدخول.")
        return self._result(writeoff_id, value, before, after)

    # ------------------------------------------------------------ full reversal (a mistake)
    def reverse(self, writeoff_id, reason, password, *, _expected_actor=None):
        session_actor = self._actor(_expected_actor, REVERSE)
        if not str(writeoff_id or "").isdigit():
            raise DeadCashError("NOT_FOUND", "سجل الشطب غير موجود.")
        reason = _reason(reason, "سبب عكس الشطب")
        self._confirm_password(session_actor, password)
        with self._transaction() as connection:
            actor = self._authorization.require_permission_for_write(connection, session_actor, REVERSE)
            repository = self._repository(connection)
            found = repository.get_writeoff(writeoff_id)
            if found is None:
                raise DeadCashError("NOT_FOUND", "سجل الشطب غير موجود.")
            self._balances(connection).lock_balance(found["customer_id"])  # canonical order: customer first
            row = repository.lock_writeoff(writeoff_id)
            if row["is_reversed"]:
                raise DeadCashError("ALREADY_REVERSED", "تم عكس هذا الشطب من قبل.")
            code, amount = writeoff_code(writeoff_id), Decimal(str(row["amount"]))
            transaction_id = self._customers(connection).add_transaction(
                customer_id=int(row["customer_id"]), type_code="DEAD_CASH_REVERSAL", amount_delta=amount,
                created_by_user_id=actor.user_id, reference_type=SOURCE, reference_id=int(writeoff_id),
                reference_code=reversal_code(writeoff_id), description=f"عكس شطب رصيد {code}", notes=reason)
            repository.mark_writeoff_reversed(writeoff_id=writeoff_id, reversal_transaction_id=transaction_id,
                                              reversed_by_user_id=actor.user_id, reason=reason)
            self._audit(repository, actor, "DEAD_CASH_REVERSED", writeoff_id, reversal_code(writeoff_id),
                        f"عكس شطب رصيد {code}",
                        {"reference": code, "reversal_reference": reversal_code(writeoff_id),
                         "customer_id": row["customer_id"], "amount": str(amount), "reason": reason,
                         "employee_id": actor.employee_id})
            if not self._session.is_current(session_actor):
                raise PermissionError("انتهت جلسة الدخول.")
        return {"id": str(writeoff_id), "code": code, "reversal_code": reversal_code(writeoff_id),
                "amount": str(amount)}

    # ------------------------------------------------------------ helpers
    @staticmethod
    def _audit(repository, actor, event_type, writeoff_id, reference, description, metadata):
        repository._execute(
            "INSERT INTO audit_log (user_id, event_type, entity_type, entity_id, entity_reference, description, "
            "metadata_json) VALUES (%s, %s, %s, %s, %s, %s, %s)",
            (actor.user_id, event_type, "DEAD_CASH_WRITEOFF", int(writeoff_id), reference, description,
             json.dumps(metadata, ensure_ascii=False, default=str)))

    @staticmethod
    def _result(writeoff_id, amount, before, after, duplicate=False):
        return {"id": str(writeoff_id), "code": writeoff_code(writeoff_id), "amount": str(amount),
                "balance_before": str(before), "balance_after": str(after), "duplicate": duplicate}
