"""Settings → إعدادات النظام: the owner's keyed configuration values (migration 081).

The view model decides nothing: the service rechecks SYSTEM_SETTINGS_VIEW / SYSTEM_SETTINGS_MANAGE
inside every transaction and validates each value against its type. Calls run off the GUI thread;
an answer that arrives after the session changed is ignored."""
from PySide6.QtCore import Property, QObject, Signal, Slot

from app.workers.invoice_worker import InvoiceWorker


class SystemSettingsViewModel(QObject):
    changed = Signal()

    def __init__(self, session=None, service=None, parent=None):
        super().__init__(parent)
        self._session, self._service = session, service
        self._settings, self._message, self._is_error = [], "", False
        self._loaded, self._busy, self._can_manage = False, False, False
        self._request, self._workers = 0, set()

    @Property(bool, notify=changed)
    def loaded(self):
        return self._loaded

    @Property(bool, notify=changed)
    def busy(self):
        return self._busy

    @Property(bool, notify=changed)
    def canManage(self):
        return self._can_manage

    @Property(str, notify=changed)
    def message(self):
        return self._message

    @Property(bool, notify=changed)
    def messageIsError(self):
        return self._is_error

    @Property("QVariantList", notify=changed)
    def settings(self):
        return self._settings

    @Slot()
    def refresh(self, keep_message=False):
        self._run(lambda _expected_actor: self._service.overview(_expected_actor=_expected_actor),
                  self._listed, "", keep_message)

    @Slot(str, str, str, str, bool)
    def save(self, key, value, setting_type, description, is_public):
        self._run(lambda _expected_actor: self._service.set_value(
            key, value, setting_type, description, is_public, _expected_actor=_expected_actor),
            lambda _result: self.refresh(True), "تم حفظ الإعداد.")

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
        worker = InvoiceWorker(call, self._session, actor, request, "system-settings", self)
        worker.completed.connect(lambda _r, generation, _op, result, error, _code, _kind:
                                 self._completed(generation, result, error, done, success))
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
                self._message, self._is_error = success, False
        self.changed.emit()

    def _listed(self, result):
        self._settings = result["settings"]
        self._can_manage = bool(result["can_manage"])
        self._loaded = True

    def shutdown(self):
        for worker in list(self._workers):
            worker.wait(2000)


def create_system_settings_viewmodel(session):
    from services.system_settings_service import SystemSettingsService
    return SystemSettingsViewModel(session, SystemSettingsService(session))
