"""MainWindow balance strip: الكاش and كسر 21 / 18 / 24 (COUNTED grams), formatted in Python."""
from decimal import Decimal

from PySide6.QtCore import QObject, Property, QTimer, Signal, Slot

from app.formatting import money
from app.workers.invoice_worker import InvoiceWorker


class OperationalBalanceViewModel(QObject):
    changed = Signal()

    def __init__(self, session, service=None, parent=None, refresh_ms=60000):
        super().__init__(parent)
        self._session, self._service = session, service
        self._workers, self._request, self._shutdown = set(), 0, False
        self._strip, self._error = {}, ""
        self._timer = QTimer(self)
        self._timer.setInterval(refresh_ms)
        self._timer.timeout.connect(self.refresh)

    def _service_instance(self):
        if self._service is None:
            from services.operational_balance_service import OperationalBalanceService
            self._service = OperationalBalanceService(self._session)
        return self._service

    @Slot()
    def activate(self):
        self.refresh()
        self._timer.start()

    @Slot()
    @Slot(str)
    def refresh(self, _reason=""):
        """Also called after every finalized invoice (the ledgers just changed)."""
        actor = self._session.current_actor()
        if self._shutdown or actor is None:
            return
        service = self._service_instance()
        self._request += 1
        worker = InvoiceWorker(lambda _expected_actor: service.strip(_expected_actor=_expected_actor),
                               self._session, actor, self._request, "strip", self)
        worker.completed.connect(self._completed)
        worker.finished.connect(lambda w=worker: self._workers.discard(w))
        self._workers.add(worker)
        worker.start()

    @Slot(int, str, str, object, str, str, str)
    def _completed(self, request, generation, operation, result, error, code, kind):
        actor = self._session.current_actor()
        if self._shutdown or actor is None or str(actor.generation) != generation or request != self._request:
            return
        self._strip, self._error = ({}, error) if error else (result, "")
        self.changed.emit()

    @Slot()
    def shutdown(self):
        self._shutdown = True
        self._timer.stop()
        for worker in list(self._workers):
            worker.wait(5000)

    @Property(str, notify=changed)
    def cashText(self):
        value = self._strip.get("cash_balance")
        return "—" if value is None else money(value)             # the one money format: 23,000.00 ج

    @Property(bool, notify=changed)
    def cashZero(self):
        value = self._strip.get("cash_balance")
        return value is not None and Decimal(value) == 0

    @Property("QVariantList", notify=changed)
    def oldGold(self):
        # zero: shown muted (never hidden) so non-zero balances stand out at a glance.
        return [{"karat": str(r["karat"]), "text": f"{Decimal(r['grams']):,.3f} جم",
                 "zero": Decimal(r["grams"]) == 0}
                for r in self._strip.get("old_gold", [])]

    @Property(bool, notify=changed)
    def ready(self):
        return bool(self._strip)

    @Property(str, notify=changed)
    def errorMessage(self):
        return self._error
