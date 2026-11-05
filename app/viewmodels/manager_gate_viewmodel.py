"""قفل مراجعات المدير والتقارير (view model): `unlocked` is false until the signed-in user types their own login password; the page locks again 60 seconds after the employee leaves it (the timer lives in
Main.qml) or at logout (this view model belongs to one login). The password check runs off the GUI thread (Argon2 is deliberately slow) and the password is never kept."""
import time

from PySide6.QtCore import Property, QEvent, QObject, QTimer, Signal, Slot

from app.workers.invoice_worker import InvoiceWorker

IDLE_LOCK_SECONDS = 900          # owner 2026-11-05 (was 5 minutes): an unlocked manager page left untouched for 15 minutes locks itself
_ACTIVITY = {QEvent.MouseButtonPress, QEvent.MouseMove, QEvent.Wheel, QEvent.KeyPress, QEvent.TouchBegin}


class _ActivityFilter(QObject):
    """Application-wide: any mouse / keyboard / touch event counts as the employee being there (a barcode scanner types keys)."""

    def __init__(self, gate):
        super().__init__(gate)
        self._gate = gate

    def eventFilter(self, watched, event):
        if event.type() in _ACTIVITY:
            self._gate.touch()
        return False


class ManagerGateViewModel(QObject):
    changed = Signal()

    def __init__(self, session=None, service=None, parent=None, clock=time.monotonic):
        super().__init__(parent)
        self._session, self._service = session, service
        self._unlocked, self._busy, self._message = False, False, ""
        self._change_busy, self._change_message, self._change_error = False, "", False
        self._request, self._workers = 0, set()
        self._clock = clock
        self._last_activity = self._clock()
        self._timer = self._filter = None

    # ------------------------------------------------------------ idle lock
    @Slot()
    def touch(self):
        """The employee did something (mouse, keys, touch): the idle clock restarts."""
        self._last_activity = self._clock()

    @Slot(result=bool)
    def checkIdle(self):
        """Locks when the page is unlocked and nothing was touched for IDLE_LOCK_SECONDS. True when it locked."""
        if self._unlocked and self._clock() - self._last_activity >= IDLE_LOCK_SECONDS:
            self.lock()
            return True
        return False

    def start_idle_watch(self, app):
        """Real application only: watch every input event and check every 15 seconds."""
        if self._timer is not None:
            return
        self._filter = _ActivityFilter(self)
        app.installEventFilter(self._filter)
        self._timer = QTimer(self)
        self._timer.setInterval(15000)
        self._timer.timeout.connect(self.checkIdle)
        self._timer.start()

    @Property(bool, notify=changed)
    def unlocked(self):
        return self._unlocked

    @Property(bool, notify=changed)
    def busy(self):
        return self._busy

    @Property(str, notify=changed)
    def message(self):
        return self._message

    @Property(bool, notify=changed)
    def changeBusy(self):
        return self._change_busy

    @Property(str, notify=changed)
    def changeMessage(self):
        return self._change_message

    @Property(bool, notify=changed)
    def changeIsError(self):
        return self._change_error

    @Slot(str, str, str)
    def changePassword(self, current, new_password, confirmation):
        """Settings: the user changes their own login password (also the manager-lock password)."""
        if self._service is None or self._session is None or self._change_busy:
            return
        actor = self._session.current_actor()
        if actor is None:
            return
        self._change_busy, self._change_message, self._change_error = True, "", False
        self.changed.emit()

        def call(_expected_actor):
            return self._service.change_password(current, new_password, confirmation, _expected_actor=_expected_actor)
        worker = InvoiceWorker(call, self._session, actor, 0, "password-change", self)
        worker.completed.connect(lambda r, generation, op, result, error, code, kind: self._changed(generation, error))
        worker.finished.connect(lambda w=worker: self._workers.discard(w))
        self._workers.add(worker)
        worker.start()

    def _changed(self, generation, error):
        actor = self._session.current_actor() if self._session is not None else None
        if actor is None or str(actor.generation) != generation:
            return
        self._change_busy = False
        self._change_error = bool(error)
        self._change_message = error or "تم تغيير كلمة المرور. استخدم الجديدة في الدخول ومراجعات المدير."
        self.changed.emit()

    @Slot()
    def clearChangeMessage(self):
        if self._change_message:
            self._change_message, self._change_error = "", False
            self.changed.emit()

    @Slot(str)
    def unlock(self, password):
        if self._service is None or self._session is None or self._busy:
            return
        actor = self._session.current_actor()
        if actor is None:
            return
        self._request += 1
        request = self._request
        self._busy, self._message = True, ""
        self.changed.emit()
        value = password

        def call(_expected_actor):
            return self._service.verify(value, _expected_actor=_expected_actor)
        worker = InvoiceWorker(call, self._session, actor, request, "manager-gate", self)
        worker.completed.connect(lambda r, generation, op, result, error, code, kind: self._completed(request, generation, result, error))
        worker.finished.connect(lambda w=worker: self._workers.discard(w))
        self._workers.add(worker)
        worker.start()

    def _completed(self, request, generation, result, error):
        if request != self._request:
            return
        actor = self._session.current_actor() if self._session is not None else None
        if actor is None or str(actor.generation) != generation:
            return
        self._busy = False
        if error:
            self._unlocked, self._message = False, error
        else:
            self._unlocked, self._message = True, ""
            self._last_activity = self._clock()
        self.changed.emit()

    @Slot()
    def lock(self):
        self._request += 1                      # an answer still on its way is ignored
        if self._unlocked or self._message or self._busy:
            self._unlocked, self._busy, self._message = False, False, ""
            self.changed.emit()

    def shutdown(self):
        for worker in list(self._workers):
            worker.wait(2000)


def create_manager_gate_viewmodel(session):
    from services.manager_access_service import ManagerAccessService
    gate = ManagerGateViewModel(session, ManagerAccessService(session))
    try:
        from PySide6.QtGui import QGuiApplication
        app = QGuiApplication.instance()
        if app is not None and QGuiApplication.platformName() not in ("offscreen", "minimal", ""):
            gate.start_idle_watch(app)                       # never on the offscreen / test platforms
    except Exception:
        pass
    return gate
