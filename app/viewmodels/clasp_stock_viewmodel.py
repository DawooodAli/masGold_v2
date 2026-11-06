"""رصيد الرفايع (view model). It decides nothing: the service rechecks CLASP_STOCK_MANAGE inside the transaction and refuses a removal above the balance. The calls run off the GUI thread;
an answer that arrives after the session changed is ignored. One request key per attempt: a retry after a failure reuses it (an exact replay returns the original), a new attempt after a
success takes a new one."""
import uuid
from decimal import Decimal

from PySide6.QtCore import Property, QObject, Signal, Slot

from app.workers.invoice_worker import InvoiceWorker

WORDS = {"ENTRY": "إضافة وزن", "IN": "زيادة", "OUT": "نقص"}


class ClaspStockViewModel(QObject):
    changed = Signal()
    saved = Signal()

    def __init__(self, session=None, service=None, parent=None):
        super().__init__(parent)
        self._session, self._service = session, service
        self._busy, self._message, self._is_error = False, "", False
        self._request, self._workers, self._key = 0, set(), ""
        self._balances, self._recent = [], []

    @Property(bool, notify=changed)
    def canManage(self):
        return self._service is not None and self._service.can_manage()

    @Property(bool, notify=changed)
    def canView(self):
        return self._service is not None and self._service.can_view()

    @Property(bool, notify=changed)
    def busy(self):
        return self._busy

    @Property(str, notify=changed)
    def message(self):
        return self._message

    @Property(bool, notify=changed)
    def messageIsError(self):
        return self._is_error

    @Property("QVariantList", notify=changed)
    def balances(self):
        return self._balances

    @Property("QVariantList", notify=changed)
    def recent(self):
        return self._recent

    @Slot()
    def refresh(self):
        """The dialog opens: clear the old answer and read the grams per karat."""
        self._message, self._is_error = "", False
        self._start("overview", lambda _expected_actor, key: self._service.overview(_expected_actor=_expected_actor))

    @Slot(str, str, str, str)
    def save(self, karat_id, kind, grams, note):
        """kind: ENTRY (weight added), IN (increase) or OUT (decrease); the note (type or reason) is mandatory."""
        if kind == "ENTRY":
            work = lambda _expected_actor, key: self._service.receive(karat_id, grams, note, key, _expected_actor=_expected_actor)
        else:
            work = lambda _expected_actor, key: self._service.adjust(karat_id, kind, grams, note, key, _expected_actor=_expected_actor)
        self._pending_kind = kind
        self._start("save", work)

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
        if operation == "save":
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
            self._balances, self._recent = result["balances"], result["recent"]
            self.changed.emit()
            return
        self._key = ""
        word = WORDS.get(getattr(self, "_pending_kind", ""), "حركة")
        self._message, self._is_error = f"تم تسجيل {word} رصيد الرفايع عيار {result['karat']}. الرصيد الآن {Decimal(str(result['balance_after'])):,.3f} جرام.", False
        self.changed.emit()
        self.saved.emit()
        self._start("overview", lambda _expected_actor, key: self._service.overview(_expected_actor=_expected_actor))

    def shutdown(self):
        for worker in list(self._workers):
            worker.wait(2000)


def create_clasp_stock_viewmodel(session):
    from services.clasp_stock_service import ClaspStockService
    return ClaspStockViewModel(session, ClaspStockService(session))
