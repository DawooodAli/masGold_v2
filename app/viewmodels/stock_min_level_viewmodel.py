"""Settings → الحد الأدنى للمخزون: the owner's minimum pieces / grams per item type and karat (reorder alerts).

The view model decides nothing: the service rechecks SETTINGS_MANAGE inside every transaction and validates the numbers. Calls run off the GUI thread; an answer that arrives after the session
changed is ignored."""
from PySide6.QtCore import Property, QObject, Signal, Slot

from app.workers.invoice_worker import InvoiceWorker


class StockMinLevelViewModel(QObject):
    changed = Signal()
    choicesChanged = Signal()                   # the types / karats of the form: emitted only when they really changed, so the open form keeps its selection

    def __init__(self, session=None, service=None, parent=None):
        super().__init__(parent)
        self._session, self._service = session, service
        self._types, self._karats, self._levels = [], [], []
        self._loaded, self._busy, self._message, self._is_error = False, False, "", False
        self._request, self._workers = 0, set()

    @Property(bool, notify=changed)
    def canManage(self):
        return self._service is not None and self._service.can_manage()

    @Property(bool, notify=changed)
    def loaded(self):
        return self._loaded

    @Property(bool, notify=changed)
    def busy(self):
        return self._busy

    @Property(str, notify=changed)
    def message(self):
        return self._message

    @Property(bool, notify=changed)
    def messageIsError(self):
        return self._is_error

    @Property("QVariantList", notify=choicesChanged)
    def itemTypes(self):
        return self._types

    @Property("QVariantList", notify=choicesChanged)
    def karats(self):
        return self._karats

    @Property("QVariantList", notify=changed)
    def levels(self):
        return self._levels

    @Slot()
    def refresh(self, keep_message=False):
        self._run(lambda _expected_actor: self._service.overview(_expected_actor=_expected_actor), self._listed, "", keep_message)

    @Slot(str, str, str, str)
    def save(self, item_type_id, karat_id, pieces, grams):
        self._run(lambda _expected_actor: self._service.save(item_type_id, karat_id, pieces, grams, _expected_actor=_expected_actor), self._saved, "تم حفظ الحد الأدنى.")

    @Slot(str)
    def remove(self, level_id):
        self._run(lambda _expected_actor: self._service.remove(level_id, _expected_actor=_expected_actor), self._saved, "تم حذف الحد الأدنى.")

    def _run(self, call, done, success, keep_message=False):
        if self._service is None or self._session is None:
            return
        actor = self._session.current_actor()
        if actor is None:
            return
        self._request += 1
        request = self._request
        self._busy = True
        if not keep_message:
            self._message, self._is_error = "", False
        self.changed.emit()
        worker = InvoiceWorker(call, self._session, actor, request, "stock-min-level", self)
        worker.completed.connect(lambda r, generation, op, result, error, code, kind: self._completed(generation, result, error, done, success))
        worker.finished.connect(lambda w=worker: self._workers.discard(w))
        self._workers.add(worker)
        worker.start()

    def _completed(self, generation, result, error, done, success):
        actor = self._session.current_actor() if self._session is not None else None
        if actor is None or str(actor.generation) != generation:
            return
        self._busy = False
        if error:
            self._message, self._is_error = error, True
        else:
            done(result)
            if success:
                self._message = success
        self.changed.emit()

    def _listed(self, result):
        types = [{"id": t["id"], "name": t["name"]} for t in result["types"]]
        karats = [{"id": k["id"], "value": k["value"], "name": f"عيار {k['value']}"} for k in result["karats"]]
        if types != self._types or karats != self._karats:
            self._types, self._karats = types, karats
            self.choicesChanged.emit()
        self._levels = [{"id": r["id"], "type": r["type"], "karat": r["karat"],
                         "minimum": " و ".join(x for x in (f"{r['min_pieces']} قطعة" if r["min_pieces"] else "", f"{r['min_grams']:,.3f} جرام" if r["min_grams"] else "") if x),
                         "have": f"{r['pieces']} قطعة • {r['grams']:,.3f} جرام",
                         "below": bool((r["min_pieces"] and r["pieces"] < r["min_pieces"]) or (r["min_grams"] and r["grams"] < r["min_grams"]))} for r in result["levels"]]
        self._loaded = True

    def _saved(self, result):
        self.refresh(True)

    def shutdown(self):
        for worker in list(self._workers):
            worker.wait(2000)


def create_stock_min_level_viewmodel(session):
    from services.stock_min_level_service import StockMinLevelService
    return StockMinLevelViewModel(session, StockMinLevelService(session))
