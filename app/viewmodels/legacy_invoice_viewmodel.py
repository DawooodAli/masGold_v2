"""مراجعات المدير → إلغاء فاتورة قديمة (view model). It decides nothing: the service rechecks TRANSACTION_CORRECT inside the transaction and validates the invoice, its pieces, the reason and the confirmation.
Calls run off the GUI thread; an answer that arrives after the session changed is ignored."""
from PySide6.QtCore import Property, QObject, Signal, Slot

from app.workers.invoice_worker import InvoiceWorker


class LegacyInvoiceViewModel(QObject):
    changed = Signal()
    voided = Signal(str, int)                   # invoice number, pieces put back in stock

    def __init__(self, session=None, service=None, parent=None):
        super().__init__(parent)
        self._session, self._service = session, service
        self._busy, self._message, self._is_error, self._info = False, "", False, {}
        self._request, self._workers = 0, set()

    @Property(bool, notify=changed)
    def canVoid(self):
        return self._service is not None and self._service.can_void()

    @Property(bool, notify=changed)
    def busy(self):
        return self._busy

    @Property(str, notify=changed)
    def message(self):
        return self._message

    @Property(bool, notify=changed)
    def messageIsError(self):
        return self._is_error

    @Property("QVariantMap", notify=changed)
    def info(self):
        return self._info

    @Slot()
    def reset(self):
        self._request += 1
        self._busy, self._message, self._is_error, self._info = False, "", False, {}
        self.changed.emit()

    def _run(self, call, done):
        if self._service is None or self._session is None or self._busy:
            return
        actor = self._session.current_actor()
        if actor is None:
            return
        self._request += 1
        request = self._request
        self._busy, self._message, self._is_error = True, "", False
        self.changed.emit()
        worker = InvoiceWorker(call, self._session, actor, request, "legacy-invoice", self)
        worker.completed.connect(lambda r, generation, op, result, error, code, kind: self._completed(request, generation, result, error, done))
        worker.finished.connect(lambda w=worker: self._workers.discard(w))
        self._workers.add(worker)
        worker.start()

    def _completed(self, request, generation, result, error, done):
        actor = self._session.current_actor() if self._session is not None else None
        if request != self._request or actor is None or str(actor.generation) != generation:
            return
        self._busy = False
        if error:
            self._message, self._is_error = error, True
            self.changed.emit()
            return
        done(result)

    @Slot(str)
    def lookup(self, number):
        self._info = {}
        self._run(lambda _expected_actor: self._service.lookup(number, _expected_actor=_expected_actor), self._looked_up)

    def _looked_up(self, result):
        pieces = [{"barcode": p["barcode"], "item": p["item"], "karat": p["karat"], "weight": f"{p['weight']:,.3f}", "status": p["status"] or "", "ok": p["status"] == "SOLD"} for p in result["pieces"]]
        self._info = {"number": result["number"], "date": str(result["date"])[:10], "customer": result["customer"], "total": f"{result['total']:,.2f}", "pieces": pieces,
                      "oldGoldLines": result["old_gold_lines"], "blocked": bool(result["blocked"])}
        self.changed.emit()

    @Slot(str, str, bool)
    def void(self, number, reason, confirmed):
        self._run(lambda _expected_actor: self._service.void(number, reason, bool(confirmed), _expected_actor=_expected_actor), self._voided)

    def _voided(self, result):
        self._info = {}
        self._message, self._is_error = f"تم إلغاء الفاتورة {result['number']} وإرجاع {result['restocked']} قطعة للمخزون.", False
        self.changed.emit()
        self.voided.emit(result["number"], int(result["restocked"]))

    def shutdown(self):
        for worker in list(self._workers):
            worker.wait(2000)


def create_legacy_invoice_viewmodel(session):
    from services.legacy_invoice_service import LegacyInvoiceService
    return LegacyInvoiceViewModel(session, LegacyInvoiceService(session))
