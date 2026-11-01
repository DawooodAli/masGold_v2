"""Settings → النسخ الاحتياطي التلقائي: show / change / test the folder of the daily backups. Calls run off the GUI thread; an answer for an old session is ignored."""
from PySide6.QtCore import Property, QObject, Signal, Slot

from app.workers.invoice_worker import InvoiceWorker


class BackupLocationViewModel(QObject):
    changed = Signal()

    def __init__(self, session=None, service=None, parent=None):
        super().__init__(parent)
        self._session, self._service = session, service
        self._folder, self._default, self._busy = "", "", False
        self._message, self._is_error = "", False
        self._request, self._workers = 0, set()

    @Property(bool, notify=changed)
    def canManage(self):
        return self._service is not None and self._service.can_manage()

    @Property(str, notify=changed)
    def folder(self):
        return self._folder

    @Property(str, notify=changed)
    def defaultFolder(self):
        return self._default

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
    def refresh(self):
        self._run(lambda _expected_actor: self._service.current(_expected_actor=_expected_actor), self._loaded, "")

    @Slot(str)
    def save(self, folder):
        self._run(lambda _expected_actor: self._service.save(folder, _expected_actor=_expected_actor), self._loaded,
                  "تم حفظ مكان النسخ الاحتياطي. النسخة القادمة ستُكتب فيه.")

    @Slot(str)
    def test(self, folder):
        self._run(lambda _expected_actor: self._service.test(folder, _expected_actor=_expected_actor), self._tested, "")

    def _run(self, call, done, success):
        if self._service is None or self._session is None:
            return
        actor = self._session.current_actor()
        if actor is None:
            return
        self._request += 1
        request = self._request
        self._busy, self._message, self._is_error = True, "", False
        self.changed.emit()
        worker = InvoiceWorker(call, self._session, actor, request, "backup-location", self)
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

    def _loaded(self, result):
        self._folder, self._default = result["folder"], result["default"]
        if not result.get("ok", True):
            self._message, self._is_error = result.get("message", ""), True

    def _tested(self, result):
        self._message, self._is_error = result["message"], not result["ok"]

    def shutdown(self):
        for worker in list(self._workers):
            worker.wait(2000)


def create_backup_location_viewmodel(session):
    from services.backup_location import BackupLocationService
    return BackupLocationViewModel(session, BackupLocationService(session))
