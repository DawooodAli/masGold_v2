"""سجل حركات حساب مالي (بنك / InstaPay / محفظة): the movement history behind one account balance.

Every row is a real ledger movement of bank_movements / cash_movements, read through
FinancialAccountService.statement (running balance computed from the complete ledger BEFORE the filters
and paging, source resolved by immutable ids). This ViewModel only formats; navigation uses ids:
the invoice id opens the existing Invoice History detail, the customer id the existing customer details.
"""
from decimal import Decimal

from PySide6.QtCore import QObject, Property, Signal, Slot

from app.formatting import datetime_text, money
from app.viewmodels.financial_accounts_viewmodel import movement_line, reference_text


class AccountHistoryViewModel(QObject):
    changed = Signal()
    invoiceRequested = Signal(str)           # exact invoice id → existing Invoice History detail
    customerRequested = Signal(str)          # exact customer id → existing customer details

    def __init__(self, session, service=None, parent=None):
        super().__init__(parent)
        self._session, self._service = session, service
        self._workers, self._request, self._latest, self._busy = set(), 0, {}, set()
        self._shutdown = False
        self._key, self._name, self._statement, self._error = "", "", {}, ""
        self._direction, self._page = "", 1

    # ------------------------------------------------------------ plumbing
    def _service_instance(self):
        if self._service is None:
            from services.financial_account_service import FinancialAccountService
            self._service = FinancialAccountService(self._session)
        return self._service

    def _start(self, operation, call):
        from app.workers.invoice_worker import InvoiceWorker
        actor = self._session.current_actor()
        if self._shutdown or actor is None:
            return
        self._request += 1
        self._latest[operation] = self._request
        self._busy.add(operation)
        worker = InvoiceWorker(call, self._session, actor, self._request, operation, self)
        worker.completed.connect(self._completed)
        worker.finished.connect(lambda w=worker: self._workers.discard(w))
        self._workers.add(worker)
        self.changed.emit()
        worker.start()

    @Slot(int, str, str, object, str, str, str)
    def _completed(self, request, generation, operation, result, error, code, kind):
        actor = self._session.current_actor()
        if self._shutdown or actor is None or str(actor.generation) != generation:
            return
        if self._latest.get(operation) != request:
            return
        self._busy.discard(operation)
        getattr(self, f"_on_{operation}")(result, error)
        self.changed.emit()

    @Slot()
    def shutdown(self):
        self._shutdown = True
        for worker in list(self._workers):
            worker.wait(5000)

    # ------------------------------------------------------------ open / filter / page
    @Slot(str, str)
    def open(self, key, name):
        """Open the history of one account by its stable key («BANK:3» / «CASH:1»)."""
        if not key:
            return
        self._key, self._name, self._direction, self._page = key, name or "", "", 1
        self._statement, self._error = {}, ""
        self._load()

    @Slot()
    def close(self):
        self._key, self._statement, self._error, self._page = "", {}, "", 1
        self.changed.emit()

    @Slot()
    def refresh(self):
        if self._key:
            self._load()

    def _load(self):
        service, key, direction, page = self._service_instance(), self._key, self._direction, self._page
        self._start("statement", lambda _expected_actor: service.statement(
            key, "", "", direction, "", "", page, _expected_actor=_expected_actor))

    def _on_statement(self, result, error):
        if error:
            self._error = error
            return
        self._statement, self._error = result, ""

    @Slot(str)
    def setDirection(self, direction):
        if self._key and direction in ("", "IN", "OUT") and direction != self._direction:
            self._direction, self._page = direction, 1
            self._load()

    @Slot()
    def nextPage(self):
        if self._key and self._statement.get("has_more"):
            self._page += 1
            self._load()

    @Slot()
    def previousPage(self):
        if self._key and self._page > 1:
            self._page -= 1
            self._load()

    # ------------------------------------------------------------ source navigation (immutable ids only)
    @Slot(str)
    def openInvoice(self, invoice_id):
        if str(invoice_id or "").isdigit():
            self.invoiceRequested.emit(str(invoice_id))

    @Slot(str)
    def openCustomer(self, customer_id):
        if str(customer_id or "").isdigit():
            self.customerRequested.emit(str(customer_id))

    # ------------------------------------------------------------ properties
    @Property(bool, notify=changed)
    def isOpen(self):
        return bool(self._key)

    @Property(bool, notify=changed)
    def busy(self):
        return bool(self._busy)

    @Property(str, notify=changed)
    def errorMessage(self):
        return self._error

    @Property(str, notify=changed)
    def accountName(self):
        return self._statement.get("account", {}).get("name") or self._name

    @Property(str, notify=changed)
    def balanceText(self):
        balance = self._statement.get("account", {}).get("balance")
        return "—" if balance is None else money(balance)

    @Property(str, notify=changed)
    def direction(self):
        return self._direction

    @Property(int, notify=changed)
    def page(self):
        return self._page

    @Property(bool, notify=changed)
    def hasMore(self):
        return bool(self._statement.get("has_more"))

    @Property(bool, notify=changed)
    def empty(self):
        return bool(self._statement) and not self._statement.get("rows") and not self._busy

    @Property("QVariantList", notify=changed)
    def rows(self):
        out = []
        for r in self._statement.get("rows", []):
            delta = Decimal(str(r["amount_delta"]))
            incoming = delta > 0
            amount = money(abs(delta))
            out.append({
                "key": r["id"], "time": datetime_text(r.get("occurred_at")), "label": r["label"],
                "incoming": incoming, "directionText": "وارد" if incoming else "منصرف",
                "amount": amount, "balanceAfter": money(r["balance_after"]),
                "reference": reference_text(r), "customer": r.get("customer") or "",
                "customerId": r.get("customer_id") or "", "invoiceId": r.get("invoice_id") or "",
                "actor": r.get("actor_name") or "", "note": r.get("operation_reason") or r.get("description") or "",
                "line": movement_line(r["label"], r, amount)})
        return out
