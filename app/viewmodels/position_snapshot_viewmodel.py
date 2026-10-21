"""Settings → الرصيد الافتتاحي: the owner's initial gold / cash balance (entered ONCE) and the stocktaking sheets behind the gold profit report.

The view model never decides a right or a number: the service rechecks the owner rights inside its transaction, validates the figures and takes the sheet. The daily sheet is requested once
when an authorised user opens the application (`ensureDaily`); a manual sheet is a button. Every call runs off the GUI thread and its answer is ignored when the session changed meanwhile."""
from PySide6.QtCore import Property, QObject, Signal, Slot

from app.workers.invoice_worker import InvoiceWorker


def _grams(value):
    return f"{value:,.3f} جرام"


class PositionSnapshotViewModel(QObject):
    changed = Signal()
    snapshotTaken = Signal()

    def __init__(self, session=None, service=None, parent=None):
        super().__init__(parent)
        self._session, self._service = session, service
        self._opening, self._loaded, self._busy = None, False, False
        self._message, self._is_error = "", False
        self._request, self._workers = 0, set()

    # ------------------------------------------------------------------ state for the Settings card
    @Property(bool, notify=changed)
    def canManage(self):
        return self._service is not None and self._service.can_snapshot()

    @Property(bool, notify=changed)
    def loaded(self):
        return self._loaded

    @Property(bool, notify=changed)
    def hasOpening(self):
        return self._opening is not None

    @Property(bool, notify=changed)
    def busy(self):
        return self._busy

    @Property(str, notify=changed)
    def message(self):
        return self._message

    @Property(bool, notify=changed)
    def messageIsError(self):
        return self._is_error

    @Property(str, notify=changed)
    def openingText(self):
        o = self._opening
        if o is None:
            return ""
        return (f"سُجّل في {o['taken_at']:%Y-%m-%d %H:%M} — ذهب المحل {_grams(o['gold_shop_eq21'])} • ذهب الموردين (صافي) {_grams(o['gold_suppliers_eq21'])} • "
                f"الكاش {o['cash_egp']:,.2f} ج • البنوك {o['bank_egp']:,.2f} ج")

    # ------------------------------------------------------------------ actions
    @Slot()
    def refresh(self):
        self._run("opening", lambda _expected_actor: self._service.opening_state(_expected_actor=_expected_actor), self._opening_loaded)

    @Slot(str, str, str, str)
    def saveOpening(self, gold, suppliers, cash, bank):
        self._run("opening-save", lambda _expected_actor: self._service.set_opening(gold, suppliers, cash, bank, _expected_actor=_expected_actor), self._opening_saved)

    @Slot()
    def takeNow(self):
        self._run("snapshot", lambda _expected_actor: self._service.take("MANUAL", _expected_actor=_expected_actor), self._manual_taken)

    @Slot()
    def ensureDaily(self):
        """The first authorised opening of the application each day; silent when not applicable."""
        self._run("daily", lambda _expected_actor: self._service.ensure_daily(_expected_actor=_expected_actor), self._daily_done, silent=True)

    # ------------------------------------------------------------------ plumbing
    def _run(self, operation, call, done, silent=False):
        if self._service is None or self._session is None:
            return
        actor = self._session.current_actor()
        if actor is None:
            return
        self._request += 1
        request = self._request
        if not silent:
            self._busy, self._message, self._is_error = True, "", False
            self.changed.emit()
        worker = InvoiceWorker(call, self._session, actor, request, operation, self)
        worker.completed.connect(lambda r, generation, op, result, error, code, kind: self._completed(r, generation, result, error, done, silent))
        worker.finished.connect(lambda w=worker: self._workers.discard(w))
        self._workers.add(worker)
        worker.start()

    def _completed(self, request, generation, result, error, done, silent):
        actor = self._session.current_actor() if self._session is not None else None
        if actor is None or str(actor.generation) != generation:
            return
        if not silent:
            self._busy = False
        if error:
            if not silent:
                self._message, self._is_error = error, True
        else:
            done(result)
        self.changed.emit()

    def _opening_loaded(self, result):
        self._opening, self._loaded = result, True

    def _opening_saved(self, result):
        self._message, self._is_error = "تم تسجيل الرصيد الافتتاحي ولا يمكن تعديله.", False
        self.refresh()
        self.snapshotTaken.emit()

    def _manual_taken(self, result):
        self._message, self._is_error = "تم حفظ لقطة الرصيد الآن.", False
        self.snapshotTaken.emit()

    def _daily_done(self, result):
        if result.get("taken"):
            self.snapshotTaken.emit()

    def shutdown(self):
        for worker in list(self._workers):
            worker.wait(2000)


def create_position_snapshot_viewmodel(session):
    from services.position_snapshot_service import PositionSnapshotService
    return PositionSnapshotViewModel(session, PositionSnapshotService(session))
