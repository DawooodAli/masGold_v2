"""تعديل رصيد الكسر (view model). It decides nothing: the service rechecks OLD_GOLD_ADJUST inside the transaction and refuses a decrease above the available Scrap. The calls run off the GUI
thread; an answer that arrives after the session changed is ignored. One request key per attempt: a retry after a failure reuses it (an exact replay returns the original), a new attempt after
a success takes a new one."""
import uuid
from decimal import Decimal

from PySide6.QtCore import Property, QObject, Signal, Slot

from app.workers.invoice_worker import InvoiceWorker


class OldGoldAdjustmentViewModel(QObject):
    changed = Signal()
    adjusted = Signal(str)                      # the movement id (the Scrap ledger changed)

    def __init__(self, session=None, service=None, parent=None):
        super().__init__(parent)
        self._session, self._service = session, service
        self._busy, self._message, self._is_error = False, "", False
        self._request, self._workers, self._key = 0, set(), ""
        self._balances = []

    @Property(bool, notify=changed)
    def canAdjust(self):
        return self._service is not None and self._service.can_adjust()

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

    @Slot()
    def refresh(self):
        """The dialog opens: clear the old answer and read the current Scrap per karat."""
        self._message, self._is_error = "", False
        self._start("balances", lambda _expected_actor, key: self._service.balances(_expected_actor=_expected_actor))

    @Slot(str, str, str, str)
    def adjust(self, karat_id, direction, grams, reason):
        self._start("adjust", lambda _expected_actor, key: self._service.adjust(karat_id, direction, grams, reason, key, _expected_actor=_expected_actor))

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
        if operation == "adjust":
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
        if operation == "balances":
            self._balances = result
            self.changed.emit()
            return
        self._key = ""
        word = "زيادة" if Decimal(str(result["delta"])) > 0 else "نقص"
        self._message, self._is_error = f"تم تسجيل {word} رصيد الكسر عيار {result['karat']}. الرصيد الآن {Decimal(str(result['physical_after'])):,.3f} جرام.", False
        self.changed.emit()
        self.adjusted.emit(result["movement_id"])
        self._start("balances", lambda _expected_actor, key: self._service.balances(_expected_actor=_expected_actor))

    def shutdown(self):
        for worker in list(self._workers):
            worker.wait(2000)


def create_old_gold_adjustment_viewmodel(session):
    from services.old_gold_adjustment_service import OldGoldAdjustmentService
    return OldGoldAdjustmentViewModel(session, OldGoldAdjustmentService(session))
