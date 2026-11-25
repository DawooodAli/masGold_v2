"""تغيير العميل: a manager moves a finalized invoice to the right customer (owner 2026-11-25: the name is sometimes entered wrong).

What follows the invoice to the new customer: the invoice header, its printed customer snapshot (name, code, mobile, place), the account effect THIS invoice wrote («على العميل» / «للعميل») and the
old-gold movements it recorded. The account effect moves with two NEW ledger rows of type INVOICE_CUSTOMER_CHANGE (migration 110): the reverse on the old customer and the same amount on the new one; the
original ledger row is never rewritten, and a later Reversal of the invoice reverses the per-customer net. The old-gold movements only change their customer attribution (the stock grams do not change).
Nothing else moves: later settlements, deposits and the old customer's other invoices stay where they are.

Refused: an invoice that is not FINALIZED, one that already has an Exchange, a Return or a correction / reversal (their entries would be left behind), the same customer, and an inactive or merged
customer. TRANSACTION_CORRECT (OWNER / MANAGER) is rechecked in the transaction, the actor is the session user, a reason and one explicit confirmation are mandatory, and the change is audited with both
customers, the moved rows and both balances. Lock order: the two customers in ASCENDING id order, then the invoice and its ledger rows.

Same rule family as the seller change and the customer merge (migration 087); the old customer's balance changes by exactly what the invoice wrote, the new customer's by the same amount."""
from decimal import Decimal

from repositories.invoice_customer_repository import InvoiceCustomerRepository
from services.authorization_service import AuthorizationService
from services.invoice_service import invoice_transaction

PERMISSION = "TRANSACTION_CORRECT"
REASON_MAX = 300
DEPENDENT_TEXT = {"exchange": "استبدال", "return": "مرتجع", "correction": "تصحيح أو عكس"}


class InvoiceCustomerError(ValueError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def _id(value, code, message):
    text = str(value or "").strip()
    if not text.isdigit() or int(text) <= 0:
        raise InvoiceCustomerError(code, message)
    return int(text)


def _identity(row):
    return None if row is None else {"id": str(row["id"]), "code": row["customer_code"], "name": row["name"]}


class InvoiceCustomerService:
    def __init__(self, session, *, transaction_factory=invoice_transaction, repository=InvoiceCustomerRepository, authorization=None):
        self._session, self._transaction, self._repository = session, transaction_factory, repository
        self._authorization = authorization or AuthorizationService(session)

    def can_change(self):
        actor = self._session.current_actor()
        return actor is not None and PERMISSION in actor.permissions

    def _actor(self, expected_actor):
        actor = self._session.require_authenticated()
        if expected_actor is not None and not self._session.is_current(expected_actor):
            raise PermissionError("انتهت جلسة الدخول.")
        return actor

    # ------------------------------------------------------------------ the shared rules
    def _checked(self, repository, invoice_id, customer_id, *, lock):
        """Read (and with `lock` lock) everything the change depends on, refuse what is not allowed, and return the facts. The customers are locked in ascending id order, then the invoice."""
        invoice = repository.invoice(invoice_id)
        if invoice is None:
            raise InvoiceCustomerError("INVOICE_NOT_FOUND", "الفاتورة غير موجودة.")
        old_id = None if invoice["customer_id"] is None else int(invoice["customer_id"])
        locked = {}
        for cid in sorted({c for c in (old_id, customer_id) if c is not None}):
            locked[cid] = repository.customer(cid, lock=lock)
        if lock:
            invoice = repository.invoice(invoice_id, lock=True)                                  # the customers first, then the invoice
            if invoice is None or (None if invoice["customer_id"] is None else int(invoice["customer_id"])) != old_id:
                raise InvoiceCustomerError("INVOICE_CHANGED", "تغيرت الفاتورة أثناء العملية. أعد المحاولة.")
        if invoice["invoice_status"] != "FINALIZED":
            raise InvoiceCustomerError("INVOICE_NOT_FINALIZED", "يمكن تغيير العميل لفاتورة نهائية فقط.")
        new = locked.get(customer_id)
        if new is None:
            raise InvoiceCustomerError("CUSTOMER_NOT_FOUND", "العميل الجديد غير موجود.")
        if not new["is_active"] or new["merged_into_customer_id"] is not None:
            raise InvoiceCustomerError("CUSTOMER_INACTIVE", "العميل الجديد غير فعّال أو تم دمجه.")
        if old_id == customer_id:
            raise InvoiceCustomerError("NO_CHANGE", "هذا هو عميل الفاتورة الحالي بالفعل.")
        found = [DEPENDENT_TEXT[name] for name, count in repository.dependents(invoice_id).items() if count]
        if found:
            raise InvoiceCustomerError("HAS_DEPENDENTS", "لا يمكن تغيير العميل: على الفاتورة " + " و".join(found) + ". عالج ذلك أولًا.")
        nets = {}
        for r in repository.ledger_rows(invoice_id, lock=lock):                                   # the invoice's own entry plus any earlier change: the NET per customer is what the invoice left
            key = int(r["customer_id"])
            nets[key] = nets.get(key, Decimal("0.00")) + Decimal(str(r["amount_delta"]))
        if any(net != 0 for key, net in nets.items() if key != old_id):
            raise InvoiceCustomerError("LEDGER_MISMATCH", "حركات الحساب المسجلة لهذه الفاتورة لا تطابق عميلها الحالي. راجع المدير.")
        effect = nets.get(old_id, Decimal("0.00")) if old_id is not None else Decimal("0.00")
        return invoice, old_id, locked.get(old_id), new, effect

    # ------------------------------------------------------------------ read: what would change
    def preview(self, invoice_id, customer_id, *, _expected_actor=None):
        """The two customers and the balances before / after; nothing is written. Raises the same refusals as `change`."""
        actor = self._actor(_expected_actor)
        if PERMISSION not in actor.permissions:
            raise PermissionError("لا توجد صلاحية لتغيير العميل.")
        invoice_number = _id(invoice_id, "INVOICE_NOT_FOUND", "الفاتورة غير موجودة.")
        new_number = _id(customer_id, "CUSTOMER_REQUIRED", "اختر العميل الجديد.")
        with self._transaction() as connection:
            repository = self._repository(connection)
            invoice, old_id, old, new, effect = self._checked(repository, invoice_number, new_number, lock=False)
            old_balance = None if old_id is None else repository.balance(old_id)
            new_balance = repository.balance(new_number)
        return self._facts(invoice, old, new, effect, old_balance, new_balance)

    @staticmethod
    def _facts(invoice, old, new, effect, old_balance, new_balance):
        return {"invoice_number": invoice["invoice_number"], "old": _identity(old) if old else {"id": None, "code": None, "name": invoice.get("customer_name_snapshot")},
                "new": _identity(new), "account_effect": str(effect),
                "old_balance": None if old_balance is None else str(old_balance), "old_balance_after": None if old_balance is None else str(Decimal(str(old_balance)) - effect),
                "new_balance": str(new_balance), "new_balance_after": str(Decimal(str(new_balance)) + effect)}

    # ------------------------------------------------------------------ write
    def change(self, invoice_id, customer_id, reason, confirmed, *, _expected_actor=None):
        session_actor = self._actor(_expected_actor)
        text = str(reason or "").strip()
        if not text:
            raise InvoiceCustomerError("REASON_REQUIRED", "اكتب سبب تغيير العميل.")
        if len(text) > REASON_MAX:
            raise InvoiceCustomerError("REASON_TOO_LONG", f"السبب {REASON_MAX} حرف على الأكثر.")
        if confirmed is not True:
            raise InvoiceCustomerError("CONFIRMATION_REQUIRED", "أكد أن هذا هو العميل الصحيح لهذه الفاتورة.")
        invoice_number = _id(invoice_id, "INVOICE_NOT_FOUND", "الفاتورة غير موجودة.")
        new_number = _id(customer_id, "CUSTOMER_REQUIRED", "اختر العميل الجديد.")
        with self._transaction() as connection:
            actor = self._authorization.require_permission_for_write(connection, session_actor, PERMISSION)
            repository = self._repository(connection)
            invoice, old_id, old, new, effect = self._checked(repository, invoice_number, new_number, lock=True)
            old_balance = None if old_id is None else repository.balance(old_id)
            new_balance = repository.balance(new_number)
            ledger_posted, gold_moved = repository.move(invoice, old, new, effect, user_id=actor.user_id, reason=text)
            facts = self._facts(invoice, old, new, effect, old_balance, new_balance)
            repository.audit(user_id=actor.user_id, invoice=invoice, description=f"تغيير العميل في الفاتورة {invoice['invoice_number']}",
                             metadata={"reason": text, "old_customer_id": old_id, "old_customer_name": facts["old"]["name"], "new_customer_id": new_number, "new_customer_name": new["name"],
                                       "account_effect": facts["account_effect"], "ledger_rows_posted": ledger_posted, "old_gold_rows_moved": gold_moved,
                                       "old_balance": facts["old_balance"], "old_balance_after": facts["old_balance_after"],
                                       "new_balance": facts["new_balance"], "new_balance_after": facts["new_balance_after"]})
            if not self._session.is_current(session_actor):
                raise PermissionError("انتهت جلسة الدخول.")
        return {"invoice_id": str(invoice_number), "customer": new["name"], "ledger_rows_posted": ledger_posted}
