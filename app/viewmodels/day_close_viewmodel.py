"""تسليم وإقفال اليوم (view model). It decides nothing: the service rechecks DAY_CLOSE inside the transaction, derives the expected cash and the difference, and demands the reason of a
difference. Calls run off the GUI thread; an answer that arrives after the session changed is ignored. One request key per attempt: a retry after a failure reuses it, a new attempt after
a success takes a new one."""
import uuid
from decimal import Decimal, InvalidOperation

from PySide6.QtCore import Property, QObject, Signal, Slot

from app.workers.invoice_worker import InvoiceWorker


class DayCloseViewModel(QObject):
    changed = Signal()
    saved = Signal()

    def __init__(self, session=None, service=None, parent=None):
        super().__init__(parent)
        self._session, self._service = session, service
        self._busy, self._message, self._is_error = False, "", False
        self._request, self._workers, self._key = 0, set(), ""
        self._overview = {"date": "", "expected": "0.00", "cash": [], "bank": [], "drafts": 0, "recent": []}

    @Property(bool, notify=changed)
    def canClose(self):
        return self._service is not None and self._service.can_close()

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
    def dateText(self):
        return self._overview["date"]

    @Property(str, notify=changed)
    def expectedText(self):
        return f"{Decimal(self._overview['expected']):,.2f} ج"

    @Property("QVariantList", notify=changed)
    def cashAccounts(self):
        return [{"name": a["name"], "text": f"{Decimal(a['balance']):,.2f} ج"} for a in self._overview["cash"]]

    @Property("QVariantList", notify=changed)
    def bankAccounts(self):
        return [{"name": a["name"], "text": f"{Decimal(a['balance']):,.2f} ج"} for a in self._overview["bank"]]

    @Property(int, notify=changed)
    def drafts(self):
        return int(self._overview["drafts"])

    @Property("QVariantList", notify=changed)
    def recent(self):
        return [{"text": f"{r['date']} • متوقع {Decimal(r['expected']):,.2f} • معدود {Decimal(r['counted']):,.2f} • فرق {Decimal(r['difference']):,.2f}"
                         + (f" • {r['reason']}" if r["reason"] else "") + f" • {r['user']}"} for r in self._overview["recent"]]

    @Slot(str, result=str)
    def differenceText(self, counted):
        """The live difference shown while typing (display only; the service derives the real one)."""
        try:
            value = Decimal(str(counted).strip().replace(",", ""))
        except (InvalidOperation, ValueError):
            return ""
        if not value.is_finite():
            return ""
        diff = value - Decimal(self._overview["expected"])
        return "مطابق" if diff == 0 else (f"زيادة {diff:,.2f} ج" if diff > 0 else f"عجز {-diff:,.2f} ج")

    @Slot()
    def refresh(self):
        """The dialog opens: clear the old answer and read the expected cash."""
        self._message, self._is_error = "", False
        self._start("overview", lambda _expected_actor, key: self._service.overview(_expected_actor=_expected_actor))

    @Slot(str, str, str)
    def close(self, counted, reason, note):
        self._start("close", lambda _expected_actor, key: self._service.close(counted, reason, note, key, _expected_actor=_expected_actor))

    def _start(self, operation, work):
        if self._service is None or self._session is None or self._busy:
            return
        actor = self._session.current_actor()
        if actor is None:
            return
        if not self._key:
            self._key = str(uuid.uuid4())
        key = self._key
        self._request += 1
        request = self._request
        self._busy = True
        if operation == "close":
            self._message, self._is_error = "", False
        self.changed.emit()
        worker = InvoiceWorker(lambda _expected_actor: work(_expected_actor, key), self._session, actor, request, operation, self)
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
            self.changed.emit()
            return
        self._key = ""
        diff = Decimal(result["difference"])
        self._message, self._is_error = ("تم إقفال اليوم — الكاش مطابق." if diff == 0 else f"تم إقفال اليوم — الفرق {diff:,.2f} ج اتسجل بسببه."), False
        self.changed.emit()
        self.saved.emit()
        self._start("overview", lambda _expected_actor, key: self._service.overview(_expected_actor=_expected_actor))

    def shutdown(self):
        for worker in list(self._workers):
            worker.wait(2000)


def create_day_close_viewmodel(session):
    from services.day_close_service import DayCloseService
    return DayCloseViewModel(session, DayCloseService(session))
