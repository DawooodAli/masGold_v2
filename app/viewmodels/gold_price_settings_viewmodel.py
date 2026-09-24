"""Controller-owned background workers; secrets never become QML properties."""
from datetime import datetime
import logging
from PySide6.QtCore import QObject, QThread, Property, Signal, Slot
from app.viewmodels.gold_price_viewmodel import GoldPriceViewModel
from services.gold_price_settings_service import GoldPriceSettingsService
from services.secret_store import SecretStoreError
from services.gold_price_errors import ConnectionTestError


CREDENTIAL_SOURCES = {"ENTERED": "المفتاح المُدخل (غير محفوظ)", "KEYRING": "المفتاح المحفوظ",
                      "ENV": "متغير بيئة التطوير"}


class SettingsWorker(QThread):
    completed = Signal(object, str)

    def __init__(self, service, operation, actor, args, parent):
        super().__init__(parent)
        self.service, self.operation, self.actor, self.args = service, operation, actor, args

    def run(self):
        try:
            result = getattr(self.service, self.operation)(self.actor, *self.args)
            self.completed.emit(result, "")
        except Exception as exc:
            # Backend exceptions can contain the secret. Never log their messages/tracebacks.
            diagnostic = (ConnectionTestError(exc.code, exc.http_status) if isinstance(exc, ConnectionTestError)
                          else ConnectionTestError("CREDENTIAL_STORE" if isinstance(exc, SecretStoreError)
                              else "PERMISSION" if isinstance(exc, PermissionError) else "INTERNAL"))
            logging.getLogger(__name__).warning(
                "Provider settings operation=%s code=%s http_status=%s",
                self.operation, diagnostic.code, diagnostic.http_status)
            safe = str(exc) if isinstance(exc, SecretStoreError) else str(diagnostic)
            self.completed.emit(None, safe)
        finally:
            self.args = ()


class GoldPriceSettingsViewModel(QObject):
    changed = Signal()
    loaded = Signal()
    clearSecret = Signal()
    configurationSaved = Signal()
    testCompleted = Signal()

    def __init__(self, session, parent=None, service=None):
        super().__init__(parent)
        self.session = session
        self.service = service or GoldPriceSettingsService(session)
        self.workers = set()
        self._data, self._result, self._message = {}, {}, ""
        self._is_error = False
        session.changed.connect(self._reset)

    @Property('QVariantMap', notify=changed)
    def data(self): return self._data

    @Property('QVariantMap', notify=changed)
    def result(self): return self._result

    @Property(str, notify=changed)
    def message(self): return self._message

    @Property(bool, notify=changed)
    def isError(self): return self._is_error

    @Property(bool, notify=changed)
    def busy(self): return bool(self.workers)

    @Property(bool, notify=changed)
    def canManage(self): return self.session.hasPermission("SETTINGS_MANAGE")

    def _reset(self):
        self._data, self._result, self._message = {}, {}, ""
        self._is_error = False
        self.clearSecret.emit()
        self.changed.emit()

    def _start(self, operation, args=()):
        if self.busy or not self.canManage:
            self._message = "لا توجد صلاحية أو توجد عملية قيد التنفيذ."
            self._is_error = True
            self.changed.emit()
            return
        actor = self.session.current_actor()
        worker = SettingsWorker(self.service, operation, actor, args, self)
        self.workers.add(worker)
        if operation != "load":
            self._message, self._result = "", {}
            self._is_error = False
        worker.completed.connect(lambda result, error: self._complete(worker, result, error))
        worker.finished.connect(lambda: self._finished(worker))
        worker.start()
        self.changed.emit()

    def _complete(self, worker, result, error):
        if not self.session.is_current(worker.actor): return
        if error:
            self._message = error
            self._is_error = True
        elif worker.operation == "load":
            self._data = result
            self.loaded.emit()
        elif worker.operation == "test":
            self._result = self._display(result)
            if self._result["passed"]:
                self._message = "تم الاتصال بنجاح — لم تُعتمد الأسعار."
            else:
                self._message = result.get("error", "")
                self._is_error = True
        else:
            self._message = result
            self._data = {}
            if worker.operation == "save":
                self.clearSecret.emit()
            self.configurationSaved.emit()
        self.changed.emit()
        if worker.operation == "test" and not error:
            self.testCompleted.emit()

    @staticmethod
    def _display(result):
        """Formatted, RTL-safe display fields; raw fields stay for diagnostics."""
        display = dict(result)
        display["passed"] = result.get("coherent_set", "PASS") == "PASS"
        display["title_text"] = ("تم الاتصال بنجاح" if display["passed"]
                                 else "تم الاتصال لكن الأسعار لم تجتز التحقق")
        display["display_prices"] = [{
            "karat": row["karat"],
            "shop_buy_price_per_gram": GoldPriceViewModel._compact_price(row["SHOP_BUY_from_gramSell"]),
            "shop_sell_price_per_gram": GoldPriceViewModel._compact_price(row["SHOP_SELL_from_gramBuy"]),
        } for row in result.get("prices") or ()]
        try:
            local = datetime.fromisoformat(str(result.get("provider_timestamp"))).astimezone()
            display["provider_time_text"] = "⁦" + local.strftime("%Y/%m/%d %H:%M:%S") + "⁩"
        except ValueError:
            display["provider_time_text"] = "—"
        skew = result.get("timestamp_skew_seconds")
        display["skew_text"] = "—" if skew is None else format(skew, "g")
        display["credential_source_text"] = CREDENTIAL_SOURCES.get(result.get("credential_source"), "—")
        return display

    def _finished(self, worker):
        self.workers.discard(worker)
        worker.deleteLater()
        self.changed.emit()
        if worker.operation in ("save", "delete") and self.session.is_current(worker.actor):
            self._start("load")

    @Slot()
    def load(self): self._start("load")

    @Slot(str, str, str, str, str)
    def save(self, url, timeout, refresh, stale, key):
        self._start("save", (url, timeout, refresh, stale, key))

    @Slot(str, str, str)
    def test(self, url, timeout, key):
        # Keep the masked draft for Test -> Save. Clearing it here makes Save
        # submit an empty key and silently retain an older stored credential.
        # Save, page visibility changes and logout still clear the field.
        self._start("test", (url, timeout, key))

    @Slot()
    def delete(self): self._start("delete")

    @Slot()
    def shutdown(self):
        for worker in tuple(self.workers): worker.wait()
