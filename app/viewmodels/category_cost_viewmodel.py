"""Settings → تكلفة المصنعية للحروف: the owner's actual making cost per gram of each category (A B C D L T), one number for every karat.

The view model decides nothing: the service rechecks SETTINGS_MANAGE inside every transaction and validates the amount. Calls run off the GUI thread; an answer that arrives after the session
changed is ignored."""
from PySide6.QtCore import Property, QObject, Signal, Slot

from app.workers.invoice_worker import InvoiceWorker


def _text(value):
    return "" if value is None else f"{value:.2f}"


class CategoryCostViewModel(QObject):
    changed = Signal()

    def __init__(self, session=None, service=None, parent=None):
        super().__init__(parent)
        self._session, self._service = session, service
        self._rows, self._loaded, self._busy = [], False, False
        self._message, self._is_error = "", False
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

    @Property("QVariantList", notify=changed)
    def categories(self):
        return self._rows

    @Slot()
    def refresh(self, keep_message=False):
        self._run(lambda _expected_actor: self._service.costs(_expected_actor=_expected_actor), self._listed, "", keep_message)

    @Slot(str, str)
    def setCost(self, category_id, amount):
        self._run(lambda _expected_actor: self._service.set_cost(category_id, amount, _expected_actor=_expected_actor), self._saved, "تم حفظ التكلفة.")

    @Slot(str)
    def clearCost(self, category_id):
        self._run(lambda _expected_actor: self._service.clear_cost(category_id, _expected_actor=_expected_actor), self._saved, "تم مسح التكلفة.")

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
        worker = InvoiceWorker(call, self._session, actor, request, "category-cost", self)
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
        self._rows = [{"id": r["id"], "code": r["code"], "name": r["name"], "cost": _text(r["cost"]), "hasCost": r["cost"] is not None,
                       "range": "" if r["range_min"] is None and r["range_max"] is None else f"رينج الشراء {_text(r['range_min']) or '—'} إلى {_text(r['range_max']) or '—'}"}
                      for r in result]
        self._loaded = True

    def _saved(self, result):
        self.refresh(True)

    def shutdown(self):
        for worker in list(self._workers):
            worker.wait(2000)


def create_category_cost_viewmodel(session):
    from services.category_cost_service import CategoryCostService
    return CategoryCostViewModel(session, CategoryCostService(session))
