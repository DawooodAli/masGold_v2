"""Settings → تصنيفات المصروفات والإيرادات: the categories the treasury's «حركة نقدية جديدة» offers (add, rename, disable / enable, delete while unused).

The view model decides nothing: the service rechecks SETTINGS_MANAGE inside every transaction, validates the name and direction and refuses to delete a used category. Every call runs off
the GUI thread; an answer that arrives after the session changed is ignored."""
from PySide6.QtCore import Property, QObject, Signal, Slot

from app.workers.invoice_worker import InvoiceWorker

DIRECTION_TEXT = {"OUT": "خرج", "IN": "دخل", "BOTH": "الاتجاهين"}


class CashCategoryViewModel(QObject):
    changed = Signal()
    categoriesChanged = Signal()                       # the treasury screen re-reads its list after a change

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
        self._run(lambda _expected_actor: self._service.categories(_expected_actor=_expected_actor), self._listed, "", keep_message)

    @Slot(str, str)
    def add(self, name, direction):
        self._run(lambda _expected_actor: self._service.add(name, direction, _expected_actor=_expected_actor), self._changed, "تمت إضافة التصنيف.")

    @Slot(str, str)
    def rename(self, code, name):
        self._run(lambda _expected_actor: self._service.rename(code, name, _expected_actor=_expected_actor), self._changed, "تم تغيير الاسم.")

    @Slot(str, bool)
    def setActive(self, code, active):
        self._run(lambda _expected_actor: self._service.set_active(code, active, _expected_actor=_expected_actor), self._changed, "تم تفعيل التصنيف." if active else "تم تعطيل التصنيف.")

    @Slot(str)
    def remove(self, code):
        self._run(lambda _expected_actor: self._service.delete(code, _expected_actor=_expected_actor), self._changed, "تم حذف التصنيف.")

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
        worker = InvoiceWorker(call, self._session, actor, request, "cash-category", self)
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
        self._rows, self._loaded = list(result), True

    def _changed(self, result):
        self.categoriesChanged.emit()
        self.refresh(True)

    def shutdown(self):
        for worker in list(self._workers):
            worker.wait(2000)


def create_cash_category_viewmodel(session):
    from services.cash_category_service import CashCategoryService
    return CashCategoryViewModel(session, CashCategoryService(session))
