from PySide6.QtCore import QObject, Property, Signal, Slot

from app.workers.authentication_worker import AuthenticationWorker
from services.authentication_service import AuthenticationService, GENERIC_ERROR


class AuthenticationViewModel(QObject):
    changed = Signal()
    authenticated = Signal()
    loggedOut = Signal()

    def __init__(self, session, service=None, parent=None):
        super().__init__(parent)
        self._session = session
        self._service = service or AuthenticationService()
        self._workers = set()
        self._request = 0
        self._busy = False
        self._error = ""
        self._session.changed.connect(self._session_changed)

    @Slot()
    def _session_changed(self):
        if not self._session.authenticated:
            self._request += 1
            self._busy, self._error = False, ""
            self.changed.emit()

    @Property(bool, notify=changed)
    def busy(self):
        return self._busy

    @Property(str, notify=changed)
    def errorMessage(self):
        return self._error

    @Slot(str, str)
    def login(self, username, password):
        if self._busy or self._session.authenticated:
            return
        self._request += 1
        self._busy, self._error = True, ""
        worker = AuthenticationWorker(self._service, username, password,
                                      self._request, self._session.generation, self)
        self._workers.add(worker)
        worker.completed.connect(self._completed)
        worker.finished.connect(self._worker_finished)
        self.changed.emit()
        worker.start()

    @Slot(int, str, object, str)
    def _completed(self, request, generation, result, error):
        if request != self._request or generation != self._session.generation:
            return
        self._busy = False
        if result is not None and not error:
            if self._session._establish(result, int(generation)):
                self._error = ""
                self.changed.emit()
                self.authenticated.emit()
                return
        self._error = error or GENERIC_ERROR
        self.changed.emit()

    @Slot()
    def _worker_finished(self):
        worker = self.sender()
        self._workers.discard(worker)
        worker.deleteLater()

    @Slot()
    def logout(self):
        self._request += 1
        self._session.logout()
        self._busy, self._error = False, ""
        self.changed.emit()
        self.loggedOut.emit()

    def shutdown(self):
        self._request += 1
        self._session.logout()
        for worker in list(self._workers):
            worker.wait()

    def show_application_error(self):
        self._error = "تعذر فتح التطبيق. راجع إعدادات الاتصال ثم حاول مرة أخرى."
        self.changed.emit()
