"""تغيير البائع: a manager corrects the employee a finalized invoice names as its seller (a wrong name typed at the till).

Only the seller changes: the invoice, its lines, payments, prices and ledgers stay exactly as finalized. The right is TRANSACTION_CORRECT (the same right as the other corrections), rechecked in the
transaction; the actor is the session user. A reason is mandatory and the change is audited with the old and the new seller. A voided or reversed invoice and a missing / inactive employee are refused."""
from repositories.invoice_seller_repository import InvoiceSellerRepository
from services.authorization_service import AuthorizationService
from services.invoice_service import invoice_transaction

PERMISSION = "TRANSACTION_CORRECT"
REASON_MAX = 300


class InvoiceSellerError(ValueError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


class InvoiceSellerService:
    def __init__(self, session, *, transaction_factory=invoice_transaction, repository=InvoiceSellerRepository, authorization=None):
        self._session = session
        self._transaction = transaction_factory
        self._repository = repository
        self._authorization = authorization or AuthorizationService(session)

    def can_change(self):
        actor = self._session.current_actor()
        return actor is not None and PERMISSION in actor.permissions

    def employees(self, *, _expected_actor=None):
        actor = self._session.require_authenticated()
        if _expected_actor is not None and not self._session.is_current(_expected_actor):
            raise PermissionError("انتهت جلسة الدخول.")
        if PERMISSION not in actor.permissions:
            raise PermissionError("لا توجد صلاحية لتغيير البائع.")
        with self._transaction() as connection:
            rows = self._repository(connection).active_employees()
        return [{"id": str(r["id"]), "name": r["name"]} for r in rows]

    def change(self, invoice_id, employee_id, reason, *, _expected_actor=None):
        session_actor = self._session.require_authenticated()
        if _expected_actor is not None and not self._session.is_current(_expected_actor):
            raise PermissionError("انتهت جلسة الدخول.")
        text = str(reason or "").strip()
        if not text:
            raise InvoiceSellerError("REASON_REQUIRED", "اكتب سبب تغيير البائع.")
        if len(text) > REASON_MAX:
            raise InvoiceSellerError("REASON_TOO_LONG", f"السبب {REASON_MAX} حرف على الأكثر.")
        raw_invoice, raw_employee = str(invoice_id or "").strip(), str(employee_id or "").strip()
        if not raw_invoice.isdigit() or int(raw_invoice) <= 0:
            raise InvoiceSellerError("INVOICE_NOT_FOUND", "الفاتورة غير موجودة.")
        if not raw_employee.isdigit() or int(raw_employee) <= 0:
            raise InvoiceSellerError("EMPLOYEE_REQUIRED", "اختر البائع الجديد.")
        with self._transaction() as connection:
            actor = self._authorization.require_permission_for_write(connection, session_actor, PERMISSION)
            repository = self._repository(connection)
            invoice = repository.lock_invoice(int(raw_invoice))
            if invoice is None:
                raise InvoiceSellerError("INVOICE_NOT_FOUND", "الفاتورة غير موجودة.")
            if invoice["invoice_status"] != "FINALIZED":
                raise InvoiceSellerError("INVOICE_NOT_FINALIZED", "يمكن تغيير البائع لفاتورة نهائية فقط.")
            employee = repository.employee(int(raw_employee))
            if employee is None or not employee["is_active"]:
                raise InvoiceSellerError("EMPLOYEE_INVALID", "البائع الجديد غير موجود أو غير فعّال.")
            if invoice["employee_id"] is not None and int(invoice["employee_id"]) == int(employee["id"]):
                raise InvoiceSellerError("NO_CHANGE", "هذا هو البائع الحالي بالفعل.")
            repository.set_seller(invoice["id"], employee["id"])
            repository.audit(user_id=actor.user_id, invoice=invoice, description=f"تغيير البائع في الفاتورة {invoice['invoice_number']}",
                             metadata={"reason": text, "old_employee_id": invoice["employee_id"], "old_employee_name": invoice["employee_name"],
                                       "new_employee_id": int(employee["id"]), "new_employee_name": employee["name"]})
            if not self._session.is_current(session_actor):
                raise PermissionError("انتهت جلسة الدخول.")
        return {"invoice_id": raw_invoice, "seller": employee["name"]}
