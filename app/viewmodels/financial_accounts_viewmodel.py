"""الخزنة / حركة النقدية: the daily cash screen — النقدية الداخلة and النقدية الخارجة, name + amount only.

Every figure and every movement name comes from FinancialAccountService.cash_view (the one central label
mapping); this ViewModel only formats. Bank / wallet balances are never shown here. Refresh is event driven
(page entry and the finalize signals wired in main.py) — no timer polling. A new movement keeps one request
key until it succeeds, so a double click / retry can never post twice.
"""
from decimal import Decimal, InvalidOperation
from services.number_text import normalize_digits
from uuid import uuid4

from PySide6.QtCore import QObject, Property, QTimer, Signal, Slot

MESSAGE_MS = 2500                       # a success message is shown briefly, then hidden

from app.formatting import date_text, ltr, money


def reference_text(row):
    """«فاتورة 26100601» / «RT000001 — فاتورة 26100601» / «فاتورة 26100601 — FC000001»: the identifying part of a
    ledger movement, composed from the stored references only (no guessing; empty when nothing is stored)."""
    reference, invoice = row.get("reference") or "", row.get("invoice_number") or ""
    kind = row.get("kind") or ""
    if kind in ("SALE", "OLD_GOLD_PURCHASE"):
        return f"فاتورة {ltr(invoice or reference)}" if (invoice or reference) else ""
    if kind in ("CORRECTION", "REVERSAL"):
        target = row.get("corrected_reference") or invoice
        head = (f"فاتورة {ltr(target)}" if target and target == invoice else ltr(target)) if target else ""
        return " — ".join(part for part in (head, ltr(reference) if reference else "") if part)
    if kind in ("EXCHANGE", "RETURN"):
        return " — ".join(part for part in (ltr(reference) if reference else "",
                                            f"فاتورة {ltr(invoice)}" if invoice and invoice != reference else "") if part)
    return ltr(reference) if reference else ""


def movement_line(label, row, amount):
    """The accountant's one-line reading: «بيع — فاتورة 26100601 — عميل — 2,000.00 ج»."""
    return " — ".join(part for part in (label, reference_text(row), row.get("customer") or "", amount) if part)


def _decimal(text):
    try:
        value = Decimal(normalize_digits(str(text)).strip())
        return value if value.is_finite() else None
    except (InvalidOperation, ValueError):
        return None


class FinancialAccountsViewModel(QObject):
    changed = Signal()
    finalized = Signal(str)                 # the operation code (money ledgers changed)
    invoiceRequested = Signal(str)          # exact invoice id → existing Invoice History detail
    correctionRequested = Signal(str)       # FO / TR operation id → existing correction workspace
    customerRequested = Signal(str)         # immutable customer id → the existing customer details

    def __init__(self, session, service=None, parent=None, message_ms=MESSAGE_MS):
        super().__init__(parent)
        self._message_timer = QTimer(self)
        self._message_timer.setSingleShot(True)
        self._message_timer.setInterval(message_ms)
        self._message_timer.timeout.connect(self._clear_message)
        self._session, self._service = session, service
        self._workers, self._request, self._latest, self._busy = set(), 0, {}, set()
        self._shutdown, self._active = False, False
        self._view, self._error, self._message = {}, "", ""
        self._filters = {"start": "", "end": "", "label": ""}
        self._detail, self._request_key, self._form = {}, str(uuid4()), False

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
        if error and kind not in ("ValueError", "PermissionError", "CorrectionError") and not code:
            error = "تعذر تنفيذ العملية؛ حدّث البيانات وأعد المحاولة."
        getattr(self, f"_on_{operation}")(result, error)
        self.changed.emit()

    def _clear_message(self):
        if self._message:
            self._message = ""
            self.changed.emit()

    @Slot()
    def shutdown(self):
        self._shutdown = True
        self._message_timer.stop()
        for worker in list(self._workers):
            worker.wait(5000)

    # ------------------------------------------------------------ refresh
    @Slot()
    def activate(self):
        """Page entry / return: always reload (the chosen period and type are kept)."""
        self._active = True
        self.refresh()

    @Slot()
    def deactivate(self):
        self._active = False

    @Slot()
    @Slot(str)
    def refresh(self, _reason=""):
        """Wired to every finalize that moves money; off-page nothing is read (the next entry reloads)."""
        if not self._active:
            return
        service, f = self._service_instance(), dict(self._filters)
        self._start("view", lambda _expected_actor: service.cash_view(
            f["start"], f["end"], f["label"], _expected_actor=_expected_actor))

    def _on_view(self, result, error):
        if error:
            self._error = error
            return
        self._view, self._error = result, ""

    @Slot(str, str, str)
    def filter(self, start, end, label):
        self._filters = {"start": (start or "").strip(), "end": (end or "").strip(), "label": label or ""}
        self._message = ""
        self.refresh()

    # ------------------------------------------------------------ حركة نقدية جديدة
    @Slot()
    def beginMovement(self):
        """Opening the form starts a fresh request (the key survives retries until success)."""
        if self.canOperate:
            self._form, self._request_key, self._error, self._message = True, str(uuid4()), "", ""
            self.changed.emit()

    @Slot()
    def cancelMovement(self):
        if "operate" not in self._busy:
            self._form, self._error = False, ""
            self.changed.emit()

    @Slot(str, result="QVariantList")
    def typesFor(self, direction):
        return [{"code": t["code"], "text": t["text"], "needsBank": t["code"].startswith("BANK_")}
                for t in self._view.get("types", []) if direction in t["directions"]]

    def _type_text(self, kind):
        return next((t["text"] for t in self._view.get("types", []) if t["code"] == kind), "")

    def _bank_name(self, key):
        return next((b["name"] for b in self._view.get("bank_accounts", []) if b["key"] == key), "")

    @Slot(str, str, str, str, result=str)
    def confirmText(self, direction, kind, amount, bank_key):
        """The explicit confirmation sentence (formatted here, never in QML); empty when incomplete."""
        value, name = _decimal(amount), self.accountName
        if value is None or value <= 0 or not self._type_text(kind) or direction not in ("IN", "OUT"):
            return ""
        if kind.startswith("BANK_"):
            if not self._bank_name(bank_key):
                return ""
            if kind == "BANK_DEPOSIT":
                return f"سيتم إيداع {money(value)} من {name} في {self._bank_name(bank_key)}."
            return f"سيتم سحب {money(value)} من {self._bank_name(bank_key)} إلى {name}."
        side = "داخل" if direction == "IN" else "خارج"
        return f"سيتم تسجيل {self._type_text(kind)} {money(value)} ({side} {name})."

    @Slot(str, str, str, str, str)
    def submit(self, direction, kind, amount, note, bank_key):
        if "operate" in self._busy or not self._form or not self.canOperate:
            return
        service, key, cash = self._service_instance(), self._request_key, self._view["account"]["key"]
        self._error = ""
        self._start("operate", lambda _expected_actor: service.cash_movement(
            cash, direction, kind, amount, note, key, bank_key, _expected_actor=_expected_actor))

    def _on_operate(self, result, error):
        if error:
            self._error = error
            return
        self._form, self._request_key = False, str(uuid4())
        self._message = "تم تسجيل الحركة."
        self._message_timer.start()
        self.finalized.emit(result["code"])
        self.refresh()

    # ------------------------------------------------------------ row → existing detail / correction
    def _rows(self):
        return self._view.get("in", {}).get("rows", []) + self._view.get("out", {}).get("rows", [])

    @Slot(str)
    def openRow(self, row_id):
        row = next((r for r in self._rows() if r["id"] == row_id), None)
        if row is None:
            return
        if row.get("invoice_id"):
            self.invoiceRequested.emit(row["invoice_id"])
        elif row.get("operation_id"):
            service, operation_id = self._service_instance(), row["operation_id"]
            self._start("detail", lambda _expected_actor: service.operation(
                operation_id, _expected_actor=_expected_actor))

    def _on_detail(self, result, error):
        self._detail, self._error = ({}, error) if error else (result, self._error)

    @Slot(str)
    def openCustomer(self, customer_id):
        if str(customer_id or "").isdigit():
            self.customerRequested.emit(str(customer_id))

    @Slot()
    def closeOperation(self):
        self._detail = {}
        self.changed.emit()

    @Slot()
    def reverseOperation(self):
        d = self._detail
        if d and self.canCorrect and not d.get("reversed_by"):
            self.correctionRequested.emit(d["operation_id"])

    # ------------------------------------------------------------ properties
    @Property(bool, notify=changed)
    def canView(self):
        return self._service_instance().can_view()

    @Property(bool, notify=changed)
    def busy(self):
        return bool(self._busy)

    @Property(bool, notify=changed)
    def saving(self):
        return "operate" in self._busy

    @Property(bool, notify=changed)
    def ready(self):
        return bool(self._view)

    @Property(bool, notify=changed)
    def canOperate(self):
        return bool(self._view.get("can_operate"))

    @Property(bool, notify=changed)
    def canCorrect(self):
        return bool(self._view.get("can_correct"))

    @Property(bool, notify=changed)
    def formOpen(self):
        return self._form

    @Property(str, notify=changed)
    def errorMessage(self):
        return self._error

    @Property(str, notify=changed)
    def message(self):
        return self._message

    @Property(str, notify=changed)
    def accountName(self):
        return self._view.get("account", {}).get("name", "")

    @Property(str, notify=changed)
    def balanceText(self):
        balance = self._view.get("account", {}).get("balance")
        return "—" if balance is None else money(balance)

    @Property(bool, notify=changed)
    def balanceNegative(self):
        return Decimal(self._view.get("account", {}).get("balance") or "0") < 0

    @Property(str, notify=changed)
    def periodText(self):
        v = self._view
        if not v:
            return ""
        return ltr(v["start"]) if v["start"] == v["end"] else f"{ltr(v['start'])} — {ltr(v['end'])}"

    def _side(self, name):
        side = self._view.get(name, {})
        return [{"key": r["id"], "label": r["label"], "amount": money(r["amount"]),
                 "linked": bool(r.get("invoice_id") or r.get("operation_id")),
                 # What identifies the movement (reference, invoice, customer): the row stays one clean line.
                 "reference": reference_text(r), "customer": r.get("customer") or "",
                 "customerId": r.get("customer_id") or "",
                 "line": movement_line(r["label"], r, money(r["amount"]))}
                for r in side.get("rows", [])]

    @Property("QVariantList", notify=changed)
    def inRows(self):
        return self._side("in")

    @Property("QVariantList", notify=changed)
    def outRows(self):
        return self._side("out")

    @Property(str, notify=changed)
    def inTotal(self):
        return money(self._view.get("in", {}).get("total", "0"))

    @Property(str, notify=changed)
    def outTotal(self):
        return money(self._view.get("out", {}).get("total", "0"))

    @Property("QVariantMap", notify=changed)
    def filters(self):
        return {**self._filters, "start": self._filters["start"] or self._view.get("start", ""),
                "end": self._filters["end"] or self._view.get("end", "")}

    @Property("QVariantList", notify=changed)
    def labels(self):
        return [{"code": "", "text": "كل الحركات"}] + list(self._view.get("labels", []))

    @Property("QVariantList", notify=changed)
    def bankAccounts(self):
        return [{"key": b["key"], "name": b["name"]} for b in self._view.get("bank_accounts", [])]

    @Property("QVariantMap", notify=changed)
    def detail(self):
        d = self._detail
        if not d:
            return {}
        return {"code": ltr(d["code"]), "typeText": d["type_text"], "amount": money(d["amount"]),
                "from": d.get("from_name") or "", "to": d.get("to_name") or "", "reason": d["reason"] or "—",
                "actor": d["actor_name"] or "—", "date": date_text(d["occurred_at"], "%Y-%m-%d %H:%M"),
                "reversedBy": ltr(d["reversed_by"]) if d.get("reversed_by") else "",
                "reversalReason": d.get("reversal_reason") or "",
                "canReverse": self.canCorrect and not d.get("reversed_by")}
