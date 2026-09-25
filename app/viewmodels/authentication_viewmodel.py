from PySide6.QtCore import QObject, Property, Signal, Slot

from app.workers.authentication_worker import AuthenticationWorker
from services.authentication_service import AuthenticationService, GENERIC_ERROR


class AuthenticationViewModel(QObject):
    changed = Signal()
    authenticated = Signal()
    loggedOut = Signal()

    def __init__(self, session, service=None, parent=None):
        super().__init__(parent)
        # TEMPORARY DEVELOPMENT AUTO-LOGIN — DISABLE BEFORE PRODUCTION (config/dev_settings.py).
        # Only with the real AuthenticationService (never an injected one), once at startup;
        # after logout the normal Login window is shown.
        if service is None:
            from config.dev_settings import dev_auto_login_username
            self._dev_auto_login = dev_auto_login_username()
        else:
            self._dev_auto_login = None
        self._dev_login_pending = False
        self._session = session
        self._service = service or AuthenticationService()
        self._workers = set()
        self._request = 0
        self._busy = False
        self._error = ""
        self._stage = "CHECKING_SETUP"
        self._employees = []
        self._session.changed.connect(self._session_changed)

    @Slot()
    def _session_changed(self):
        if not self._session.authenticated:
            self._request += 1
            self._busy, self._error = False, ""
            if self._stage == "OPERATIONAL":
                self._stage = "LOGIN"
            self.changed.emit()

    @Property(str, notify=changed)
    def stage(self):
        return self._stage

    @Property("QVariantList", notify=changed)
    def employees(self):
        return self._employees

    @Slot()
    def checkStartup(self):
        if self._busy or self._session.authenticated or self._stage not in ("CHECKING_SETUP", "FIRST_RUN_SETUP"):
            return
        self._stage = "CHECKING_SETUP"
        self._start_worker(operation="check")

    @Slot(str, str, str, str)
    def createFirstOwner(self, display_name, password, confirmation, employee_id):
        if self._busy or self._session.authenticated or self._stage != "FIRST_RUN_SETUP":
            return
        try:
            employee = int(employee_id) if employee_id else None
        except ValueError:
            self._error = "رقم الموظف غير صحيح."
            self.changed.emit()
            return
        self._start_worker(operation="setup", payload={
            "display_name": display_name, "password": password,
            "confirmation": confirmation, "employee_id": employee})

    @Property(bool, notify=changed)
    def busy(self):
        return self._busy

    @Property(str, notify=changed)
    def errorMessage(self):
        return self._error

    @Slot(str, str)
    def login(self, username, password):
        if self._busy or self._session.authenticated or self._stage != "LOGIN":
            return
        self._start_worker(username, password)

    def _start_worker(self, username="", password="", *, operation="login", payload=None):
        self._request += 1
        self._busy, self._error = True, ""
        worker = AuthenticationWorker(self._service, username, password,
                                      self._request, self._session.generation, self,
                                      operation=operation, payload=payload)
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
        dev_login, self._dev_login_pending = self._dev_login_pending, False
        if isinstance(result, dict) and "setup_required" in result and not error:
            self._stage = "FIRST_RUN_SETUP" if result["setup_required"] else "LOGIN"
            self._employees = result["employees"] if result["setup_required"] else []
            self._error = ""
            username, self._dev_auto_login = self._dev_auto_login, None
            if username and not result["setup_required"]:
                self._stage = "CHECKING_SETUP"          # no Login window flash while signing in
                self._dev_login_pending = True
                self._start_worker(username, operation="dev_login")
                return
            self.changed.emit()
            return
        if result is not None and not error:
            if self._session._establish(result, int(generation)):
                self._stage = "OPERATIONAL"
                self._employees = []
                self._error = ""
                self.changed.emit()
                self.authenticated.emit()
                return
        if dev_login:
            self._stage = "LOGIN"                    # failed development auto-login: normal Login
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
