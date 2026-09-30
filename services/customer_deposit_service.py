"""عربون / استرداد عربون (027): a registered customer's cash deposit held by the shop, and its refund.

Deposit (DP): real cash IN to the safe; the canonical customer balance moves toward للعميل by the amount.
  It may create customer credit from zero. If the customer already owes the shop, the one canonical balance
  simply moves by the amount (owes 3,000 + عربون 5,000 → للعميل 2,000).
Refund (DR): real cash OUT; allowed only up to the AVAILABLE deposit = the smaller of
  (a) the customer's outstanding deposits (deposits − refunds, reversed rows excluded) and
  (b) the customer's current credit (للعميل); so a refund never crosses zero.
This is NOT Customer Account Settlement (CS): CS only settles an existing balance toward zero.

Permission: CUSTOMER_ACCOUNT_SETTLE (the existing customer-money right: OWNER / MANAGER / CASHIER), re-checked
inside the transaction. Idempotent on the request key; finalized rows are immutable; the only correction is a
full reversal (RV) through CorrectionService. Lock order: actor → customer (+ its ledger) → cash account → key.
"""
from decimal import Decimal
import hashlib
import json
from uuid import UUID

from repositories.customer_account_repository import CustomerAccountRepository
from repositories.customer_deposit_repository import CustomerDepositRepository, SOURCE, deposit_code
from repositories.customer_repository import CustomerRepository
from repositories.financial_account_repository import FinancialAccountRepository
from repositories.invoice_repository import InvoiceRepository
from services.authorization_service import AuthorizationService
from services.correction_service import CorrectionError
from services.customer_service import CustomerService
from services.financial_account_service import _amount, _text
from services.invoice_service import invoice_transaction

PERMISSION = "CUSTOMER_ACCOUNT_SETTLE"
TEXT = {"IN": "عربون", "OUT": "استرداد عربون"}


class CustomerDepositService:
    def __init__(self, session, *, transaction_factory=invoice_transaction, repository=CustomerDepositRepository,
                 balances=CustomerAccountRepository, customers=CustomerRepository, accounts=FinancialAccountRepository,
                 invoices=InvoiceRepository, authorization=None):
        self._session, self._transaction = session, transaction_factory
        self._repository, self._balances, self._customers = repository, balances, customers
        self._accounts, self._invoices = accounts, invoices
        self._authorization = authorization or AuthorizationService(session)

    def deposit(self, customer_id, amount, note, request_key, *, _expected_actor=None):
        """عربون: cash IN, customer balance toward للعميل (may start from zero)."""
        return self._finalize("IN", customer_id, amount, note, request_key, _expected_actor)

    def refund(self, customer_id, amount, note, request_key, *, _expected_actor=None):
        """استرداد عربون: cash OUT up to the available deposit; never crosses zero."""
        return self._finalize("OUT", customer_id, amount, note, request_key, _expected_actor)

    def available(self, customer_id, *, _expected_actor=None):
        """The refundable deposit right now (read-only)."""
        actor = self._actor(_expected_actor)
        cid = str(CustomerService._customer_id(customer_id))

        class _Done(Exception):
            pass
        try:
            with self._transaction() as connection:
                self._authorization.require_permission(connection, actor, PERMISSION)
                raise _Done(self._available(connection, cid, lock=False)[0])
        except _Done as done:
            return str(done.args[0])

    def _actor(self, expected_actor):
        actor = self._session.require_authenticated()
        if expected_actor is not None and not self._session.is_current(expected_actor):
            raise PermissionError("انتهت جلسة الدخول.")
        if PERMISSION not in actor.permissions:
            raise PermissionError("ليس لديك صلاحية تسجيل العربون.")
        return actor

    def _available(self, connection, customer_id, lock):
        balance = self._balances(connection).lock_balance(customer_id) if lock else \
            Decimal(str(self._customers(connection).get_balance(int(customer_id))))
        outstanding = Decimal(str(self._repository(connection).outstanding(customer_id)))
        return max(Decimal("0.00"), min(outstanding, -balance)), balance

    def _finalize(self, direction, customer_id, amount, note, request_key, expected_actor):
        session_actor = self._actor(expected_actor)
        cid = str(CustomerService._customer_id(customer_id))
        value, text = _amount(amount), _text(note, required=False, label="الملاحظة")
        try:
            key = str(UUID(str(request_key)))
        except (ValueError, TypeError, AttributeError):
            raise CorrectionError("REQUEST_INVALID", "أعد فتح النموذج وحاول مرة أخرى.") from None
        digest = hashlib.sha256(json.dumps([direction, cid, str(value), text], ensure_ascii=False).encode()).hexdigest()
        with self._transaction() as connection:
            actor = self._authorization.require_permission(connection, session_actor, PERMISSION)
            repository, invoices = self._repository(connection), self._invoices(connection)
            customer = invoices.lock_customer(cid)
            if customer is None:
                raise CorrectionError("CUSTOMER_REQUIRED", "العربون لعميل مسجل فقط.")
            available, before = self._available(connection, cid, lock=True)
            cash_rows = invoices.active_cash_accounts()
            if not cash_rows:
                raise CorrectionError("ACCOUNT_NOT_FOUND", "لا توجد خزنة نشطة.")
            cash = self._accounts(connection).account("CASH", cash_rows[0]["id"], lock=True)
            prior = repository.by_request(key)
            if prior:
                if prior["request_hash"] != digest or int(prior["performed_by_user_id"]) != int(actor.user_id):
                    raise CorrectionError("REQUEST_CONFLICT", "طلب العربون مستخدم لعملية مختلفة.")
                return self._result(prior["direction"], prior["id"], prior["amount"], prior["balance_after"], True)
            if not customer.get("is_active", True):
                raise CorrectionError("CUSTOMER_INACTIVE", "العميل غير نشط.")
            if direction == "OUT" and value > available:
                raise CorrectionError("REFUND_EXCEEDS_DEPOSIT",
                                      f"المتاح للاسترداد {available:,.2f} ج فقط.")
            after = before - value if direction == "IN" else before + value
            now = repository.db_now()
            deposit_id = repository.insert_header(customer_id=cid, direction=direction, amount=value, key=key,
                                                  digest=digest, cash_account_id=cash["id"], before=before, after=after,
                                                  notes=text or None, actor=actor, now=now)
            code = deposit_code(direction, deposit_id)
            label = f"{TEXT[direction]} {code}"
            repository.post_cash(deposit_id=deposit_id, code=code, direction=direction, amount=value,
                                 cash_account_id=cash["id"], text=label, actor=actor, now=now)
            self._customers(connection).add_transaction(
                customer_id=int(cid), type_code=SOURCE if direction == "IN" else "CUSTOMER_DEPOSIT_REFUND",
                amount_delta=-value if direction == "IN" else value, created_by_user_id=actor.user_id,
                reference_type=SOURCE, reference_id=deposit_id, reference_code=code, description=label,
                notes=text or None)
            repository.audit(actor=actor, deposit_id=deposit_id, code=code, direction=direction, description=label,
                             metadata={"customer_id": cid, "amount": str(value), "before": str(before),
                                       "after": str(after), "employee_id": actor.employee_id}, now=now)
            if not self._session.is_current(session_actor):
                raise PermissionError("انتهت جلسة الدخول.")
        return self._result(direction, deposit_id, value, after)

    @staticmethod
    def _result(direction, deposit_id, amount, after, duplicate=False):
        return {"id": str(deposit_id), "code": deposit_code(direction, deposit_id), "direction": direction,
                "amount": str(amount), "balance_after": str(after), "duplicate": duplicate}
