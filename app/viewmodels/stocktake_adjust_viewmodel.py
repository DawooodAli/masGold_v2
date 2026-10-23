"""الجرد: اعتماد الجرد وشطب المتبقي (view model).

It decides nothing: the service rechecks STOCKTAKE_ADJUST inside the transaction, validates the two id lists against the locked database rows and stores the reconciliation. The call runs off the
GUI thread; an answer that arrives after the session changed is ignored. One request key per approval attempt: a retry after a failure reuses it (an exact replay returns the original), a new
attempt after a success takes a new one."""
import uuid

from PySide6.QtCore import Property, QObject, Signal, Slot

from app.workers.invoice_worker import InvoiceWorker


class StocktakeAdjustViewModel(QObject):
    changed = Signal()
    approved = Signal(str, int, str)             # code, written-off count, grams text
    restored = Signal(str)                       # a written-off piece went back to the stock

    def __init__(self, session=None, service=None, parent=None):
        super().__init__(parent)
        self._session, self._service = session, service
        self._busy, self._message, self._is_error = False, "", False
        self._request, self._workers, self._key = 0, set(), ""

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

    @Slot()
    def prepare(self):
        """A dialog opens: clear the old answer (the request key of an unfinished attempt is kept)."""
        self._message, self._is_error = "", False
        self.changed.emit()

    @Slot("QVariantMap", str, str, bool)
    def approve(self, payload, reason, note, confirmed):
        data = dict(payload or {})
        self._start(lambda _expected_actor, key: self._service.approve(data.get("itemType"), data.get("karat"), data.get("scanned") or [], data.get("remaining") or [], reason, note,
                                                                         bool(confirmed), key, _expected_actor=_expected_actor))

    @Slot("QVariantList", str, str, bool)
    def writeOff(self, piece_ids, reason, note, confirmed):
        """One or a few pieces deleted from المتبقي."""
        ids = [str(i) for i in piece_ids]
        self._start(lambda _expected_actor, key: self._service.write_off_pieces(ids, reason, note, bool(confirmed), key, _expected_actor=_expected_actor))

    @Slot(str, str)
    def restore(self, piece_id, reason):
        """قطعة اتشطبت بالجرد ورجعت ظهرت: back to the stock with a reason (one attempt = one call; the service refuses a piece that is not removed by a stocktake)."""
        self._start(lambda _expected_actor, key: self._service.restore_piece(piece_id, reason, _expected_actor=_expected_actor))

    def _start(self, work):
        if self._service is None or self._session is None or self._busy:
            return
        actor = self._session.current_actor()
        if actor is None:
            return
        if not self._key:
            self._key = uuid.uuid4().hex
        key = self._key
        self._request += 1
        request = self._request
        self._busy, self._message, self._is_error = True, "", False
        self.changed.emit()

        def call(_expected_actor):
            return work(_expected_actor, key)
        worker = InvoiceWorker(call, self._session, actor, request, "stocktake-adjust", self)
        worker.completed.connect(lambda r, generation, op, result, error, code, kind: self._completed(generation, result, error))
        worker.finished.connect(lambda w=worker: self._workers.discard(w))
        self._workers.add(worker)
        worker.start()

    def _completed(self, generation, result, error):
        actor = self._session.current_actor() if self._session is not None else None
        if actor is None or str(actor.generation) != generation:
            return
        self._busy = False
        if error:
            self._message, self._is_error = error, True
            self.changed.emit()
            return
        self._key = ""
        if result.get("restored"):
            self._message, self._is_error = "تمت إعادة القطعة للمخزون.", False
            self.changed.emit()
            self.restored.emit(str(result["piece_id"]))
            return
        self._message, self._is_error = f"تم اعتماد الجرد {result['code']} وشطب {result['written_off_count']} قطعة.", False
        self.changed.emit()
        self.approved.emit(result["code"], int(result["written_off_count"]), f"{result['written_off_grams']:,.3f}")

    def shutdown(self):
        for worker in list(self._workers):
            worker.wait(2000)


def create_stocktake_adjust_viewmodel(session):
    from services.stocktake_adjust_service import StocktakeAdjustService
    return StocktakeAdjustViewModel(session, StocktakeAdjustService(session))
