"""المرتبات (view model). It decides nothing: the service rechecks PAYROLL_MANAGE inside every transaction, derives the figures of a slip from the database and refuses a second slip for the same
month. Calls run off the GUI thread; an answer that arrives after the session changed is ignored. A payment keeps ONE request key per attempt: a retry after a failure reuses it, a new payment
after a success takes a new one."""
import uuid
from datetime import date
from decimal import Decimal

from PySide6.QtCore import Property, QObject, Signal, Slot

from app.workers.invoice_worker import InvoiceWorker

STATUS_TEXT = {"PAID": "اتصرف", "READY": "جاهز للصرف", "NO_SALARY": "بدون مرتب"}


def _money(text):
    return f"{Decimal(text):,.2f} ج"


class PayrollViewModel(QObject):
    changed = Signal()
    paid = Signal()
    done = Signal()

    def __init__(self, session=None, service=None, today=date.today, parent=None):
        super().__init__(parent)
        self._session, self._service, self._today = session, service, today
        self._busy, self._message, self._is_error = False, "", False
        self._request, self._workers, self._pay_key = 0, set(), ""
        self._month = today().strftime("%Y-%m")
        self._overview = {"month": self._month, "employees": [], "adjustments": [], "accounts": [], "paid_total": "0.00", "due_total": "0.00"}

    @Property(bool, notify=changed)
    def canManage(self):
        return self._service is not None and self._service.can_manage()

    @Property(bool, notify=changed)
    def busy(self):
        return self._busy

    @Property(str, notify=changed)
    def message(self):
        return self._message

    @Property(bool, notify=changed)
    def messageIsError(self):
        return self._is_error

    @Property(str, notify=changed)
    def month(self):
        return self._month

    @Property(bool, notify=changed)
    def isCurrentMonth(self):
        return self._month >= self._today().strftime("%Y-%m")

    @Property(str, notify=changed)
    def paidTotalText(self):
        return _money(self._overview["paid_total"])

    @Property(str, notify=changed)
    def dueTotalText(self):
        return _money(self._overview["due_total"])

    @Property("QVariantList", notify=changed)
    def employees(self):
        rows = []
        for e in self._overview["employees"]:
            paid = e["paid"]
            rows.append({
                "id": e["id"], "name": e["name"], "status": e["status"], "statusText": STATUS_TEXT[e["status"]],
                "baseText": "—" if e["base"] is None else _money(e["base"]), "base": e["base"] or "",
                "bonusText": _money(e["bonuses"]), "penaltyText": _money(e["penalties"]), "owedText": _money(e["owed"]), "owed": e["owed"],
                "defaultDeduction": e["default_deduction"],
                "netText": "—" if e["net_preview"] is None else _money(e["net_preview"]),
                "canPay": e["status"] == "READY",
                "paidText": "" if not paid else f"اتصرف {_money(paid['net'])} بتاريخ {paid['date']} (مرتب {_money(paid['base'])} + حوافز {_money(paid['bonuses'])} − جزاءات {_money(paid['penalties'])} − سلف {_money(paid['advances'])})"})
        return rows

    @Property("QVariantList", notify=changed)
    def adjustments(self):
        state = {"OPEN": "مسجّل", "PAID": "اتحسب في مرتب", "VOIDED": "ملغي"}
        return [{"id": a["id"], "canVoid": a["state"] == "OPEN",
                 "text": f"{a['date']} • {a['employee']} • {a['kind_text']} {_money(a['amount'])} • {a['reason']} • {state[a['state']]}" + (f" ({a['void_reason']})" if a["void_reason"] else "")}
                for a in self._overview["adjustments"]]

    @Property("QVariantList", notify=changed)
    def accounts(self):
        return [{"key": a["key"], "text": f"{a['name']} — {_money(a['balance'])}"} for a in self._overview["accounts"]]

    @Slot(str, str, result=str)
    def previewNet(self, employee_id, deduction):
        """The net shown while typing the advance deduction (display only; the service derives the real one under the locks)."""
        row = next((e for e in self._overview["employees"] if e["id"] == str(employee_id)), None)
        if row is None or row["base"] is None:
            return ""
        try:
            take = Decimal(str(deduction or "0").strip().replace(",", ""))
        except Exception:
            return ""
        if not take.is_finite() or take < 0:
            return ""
        net = Decimal(row["base"]) + Decimal(row["bonuses"]) - Decimal(row["penalties"]) - take
        return f"الصافي اللي هيتصرف: {net:,.2f} ج" if net > 0 else "الصافي صفر أو أقل: قلّل خصم السلف"

    # ------------------------------------------------------------ actions
    @Slot()
    def refresh(self):
        self._message, self._is_error = "", False
        self._start("overview", lambda actor, key: self._service.overview(self._month, _expected_actor=actor))

    @Slot(int)
    def shiftMonth(self, delta):
        year, month = int(self._month[:4]), int(self._month[5:7])
        index = year * 12 + (month - 1) + int(delta)
        year, month = divmod(index, 12)
        candidate = f"{year:04d}-{month + 1:02d}"
        if candidate > self._today().strftime("%Y-%m"):
            return
        self._month = candidate
        self._message, self._is_error = "", False
        self.changed.emit()
        self._start("overview", lambda actor, key: self._service.overview(self._month, _expected_actor=actor))

    @Slot(str, str)
    def setSalary(self, employee_id, amount):
        self._start("salary", lambda actor, key: self._service.set_salary(employee_id, amount, self._month, _expected_actor=actor))

    @Slot(str, str, str, str)
    def addAdjustment(self, employee_id, kind, amount, reason):
        self._start("adjustment", lambda actor, key: self._service.add_adjustment(employee_id, self._month, kind, amount, reason, _expected_actor=actor))

    @Slot(str, str)
    def voidAdjustment(self, adjustment_id, reason):
        self._start("adjustment", lambda actor, key: self._service.void_adjustment(adjustment_id, reason, _expected_actor=actor))

    @Slot(str, str, str, str)
    def pay(self, employee_id, account_key, deduction, note):
        if not self._pay_key:
            self._pay_key = str(uuid.uuid4())
        pay_key = self._pay_key
        self._start("pay", lambda actor, key: self._service.pay(employee_id, self._month, account_key, deduction, note, pay_key, _expected_actor=actor))

    def _start(self, operation, work):
        if self._service is None or self._session is None or self._busy:
            return
        actor = self._session.current_actor()
        if actor is None:
            return
        self._request += 1
        request = self._request
        self._busy = True
        if operation != "overview":
            self._message, self._is_error = "", False
        self.changed.emit()
        worker = InvoiceWorker(lambda _expected_actor: work(_expected_actor, ""), self._session, actor, request, operation, self)
        worker.completed.connect(lambda r, generation, op, result, error, code, kind: self._completed(generation, op, result, error))
        worker.finished.connect(lambda w=worker: self._workers.discard(w))
        self._workers.add(worker)
        worker.start()

    def _completed(self, generation, operation, result, error):
        actor = self._session.current_actor() if self._session is not None else None
        if actor is None or str(actor.generation) != generation:
            return
        self._busy = False
        if error:
            self._message, self._is_error = error, True
            self.changed.emit()
            return
        if operation == "overview":
            self._overview = result
            self._month = result["month"]
            self.changed.emit()
            return
        if operation == "pay":
            self._pay_key = ""
            self._message, self._is_error = ("اتصرف المرتب" + (" (العملية اتسجلت قبل كده)." if result.get("duplicate") else f": {_money(result['amount'])} من الحساب المختار."), False)
            self.paid.emit()
        else:
            self._message, self._is_error = "اتسجل.", False
        self.changed.emit()
        self.done.emit()
        self._start("overview", lambda actor_, key: self._service.overview(self._month, _expected_actor=actor_))

    def shutdown(self):
        for worker in list(self._workers):
            worker.wait(2000)


def create_payroll_viewmodel(session):
    from services.payroll_service import PayrollService
    return PayrollViewModel(session, PayrollService(session))
