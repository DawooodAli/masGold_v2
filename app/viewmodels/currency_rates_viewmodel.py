"""Settings → أسعار العملات (GoldTimo /rates): show the card on the home page or not, the refresh interval and the currencies. The rates use the GoldTimo keys already stored for the gold price.

The service rechecks SETTINGS_MANAGE on every call; calls run off the GUI thread and an answer that arrives after the session changed is ignored."""
from PySide6.QtCore import Property, QObject, Signal, Slot

from app.workers.invoice_worker import InvoiceWorker


class CurrencyRatesViewModel(QObject):
    changed = Signal()

    def __init__(self, session=None, service=None, parent=None):
        super().__init__(parent)
        self._session, self._service = session, service
        self._enabled, self._minutes, self._currencies = True, "30", []
        self._busy, self._message, self._is_error, self._test_rows, self._test_updated = False, "", False, [], ""
        self._request, self._workers = 0, set()

    @Property(bool, notify=changed)
    def canManage(self):
        return self._service is not None and self._service.can_manage()

    @Property(bool, notify=changed)
    def enabled(self):
        return self._enabled

    @Property(str, notify=changed)
    def minutes(self):
        return self._minutes

    @Property("QVariantList", notify=changed)
    def currencies(self):
        return self._currencies

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
    def testRows(self):
        return self._test_rows

    @Property(str, notify=changed)
    def testUpdated(self):
        return self._test_updated

    @Slot()
    def refresh(self):
        self._run(lambda _expected_actor: self._service.current(_expected_actor=_expected_actor), self._loaded, "")

    @Slot(bool, str, str)
    def save(self, enabled, minutes, codes):
        self._run(lambda _expected_actor: self._service.save(enabled, minutes, codes, _expected_actor=_expected_actor), self._loaded, "تم حفظ إعدادات أسعار العملات.")

    @Slot()
    def test(self):
        self._test_rows, self._test_updated = [], ""
        self._run(lambda _expected_actor: self._service.test(_expected_actor=_expected_actor), self._tested, "")

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
        worker = InvoiceWorker(call, self._session, actor, request, "currency-rates", self)
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
        self._enabled, self._minutes, self._currencies = result["enabled"], result["minutes"], result["currencies"]

    def _tested(self, result):
        if result["available"]:
            self._test_rows, self._test_updated = [{"code": r["code"], "name": r["name"], "price": r["price"]} for r in result["rows"]], result["updated"]
            self._message = f"نجح الاتصال بـ GoldTimo ({len(result['rows'])} عملة)."
        else:
            self._message, self._is_error = result["error"] or "تعذر جلب أسعار العملات.", True

    def shutdown(self):
        for worker in list(self._workers):
            worker.wait(2000)


def create_currency_rates_viewmodel(session):
    from services.currency_rates import CurrencyRatesSettingsService
    return CurrencyRatesViewModel(session, CurrencyRatesSettingsService(session))
