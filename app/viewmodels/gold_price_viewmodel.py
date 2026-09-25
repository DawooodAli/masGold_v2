"""Session-scoped presentation with controller-owned worker lifetime."""
from decimal import Decimal

from PySide6.QtCore import QObject, Property, QTimer, Signal, Slot

from app.workers.gold_price_worker import GoldPriceWorker


class GoldPriceViewModel(QObject):
    changed = Signal()

    def __init__(self, session, service=None, parent=None):
        super().__init__(parent)
        if service is None:
            from services.gold_price_service import GoldPriceService
            service = GoldPriceService(session)
        self._session, self._service = session, service
        self._workers = set()
        self._request = 0
        self._shutdown = False
        self._reload_pending = False
        self._refresh_pending = False
        self._data = {}
        self._error = ""
        self._success = ""
        self._preview = []
        self._preview_inputs = None
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.refresh)
        session.changed.connect(self._session_changed)
        if session.authenticated:
            QTimer.singleShot(0, self._session_changed)

    @Property(bool, notify=changed)
    def busy(self):
        return bool(self._workers) and self._session.authenticated

    @Property(bool, notify=changed)
    def canManage(self):
        return self._session.hasPermission("SETTINGS_MANAGE")

    @Property(str, notify=changed)
    def mode(self):
        return self._data.get("mode", "AUTO")

    @Property(str, notify=changed)
    def status(self):
        return self._data.get("status", "UNAVAILABLE")

    @Property(str, notify=changed)
    def statusText(self):
        return {"FRESH": "محدّث", "STALE_ALLOWED": "قديم — يلزم الانتباه",
                "UNAVAILABLE": "السعر غير متاح", "ERROR": "تعذر التحديث"}.get(
                    self.status, "السعر غير متاح")

    @Property(str, notify=changed)
    def modeText(self):
        return "يدوي" if self.mode == "MANUAL" else "تلقائي"

    @Property(str, notify=changed)
    def source(self):
        source = self._data.get("source")
        return "يدوي" if source == "MANUAL" else str(source or "—")

    @Property(str, notify=changed)
    def priceToken(self):
        """Identity of the active price set; changes when a new set is published."""
        return str(self._data.get("price_token") or "")

    @Property(str, notify=changed)
    def providerTimestamp(self):
        return str(self._data.get("provider_timestamp") or "—")

    @Property(str, notify=changed)
    def fetchedAt(self):
        return str(self._data.get("fetched_at") or "—")

    @Property(str, notify=changed)
    def activatedAt(self):
        return str(self._data.get("activated_at") or "—")

    @Property(str, notify=changed)
    def ageText(self):
        age = self._data.get("age_seconds")
        if age is None:
            return "—"
        seconds = max(0, int(age))
        if seconds < 60:
            return self._arabic_count(seconds, "ثانية", "ثانيتين", "ثوانٍ")
        if seconds < 3600:
            return self._arabic_count(seconds // 60, "دقيقة", "دقيقتين", "دقائق")
        return self._arabic_count(seconds // 3600, "ساعة", "ساعتين", "ساعات")

    @staticmethod
    def _arabic_count(count, singular, dual, plural):
        # Arabic counted nouns: 1 singular, 2 dual, 3-10 plural, 11+ singular.
        if count == 1:
            return singular
        if count == 2:
            return dual
        return f"{count} {plural if 3 <= count % 100 <= 10 else singular}"

    @Property("QVariantList", notify=changed)
    def prices(self):
        return self._data.get("prices", [])

    @Property("QStringList", notify=changed)
    def warnings(self):
        # The panel has its own single compact empty state. Keep real provider
        # warnings visible, only omit the duplicate absence-of-prices message.
        return [warning for warning in self._data.get("warnings", [])
                if self.prices or warning != "لا تتوفر مجموعة أسعار متكاملة لعيارات 18 و21 و24."]

    @Property(str, notify=changed)
    def errorMessage(self):
        return self._error

    @Property(str, notify=changed)
    def successMessage(self):
        return self._success

    @Property("QVariantList", notify=changed)
    def previewPrices(self):
        return self._preview

    @Property(bool, notify=changed)
    def previewReady(self):
        return self._preview_inputs is not None and bool(self._preview)

    @Property(str, notify=changed)
    def headline(self):
        row = next((price for price in self.prices if str(price["karat"]) == "21"), None)
        if row is None:
            return "عيار 21 | السعر غير متاح"
        return (f"عيار 21 | شراء {self._compact_price(row['shop_buy_price_per_gram'])} | "
                f"بيع {self._compact_price(row['shop_sell_price_per_gram'])}")

    def _headline_value(self, field):
        row = next((price for price in self.prices if str(price["karat"]) == "21"), None)
        return self._compact_price(row[field]) if row else "—"

    @Property(str, notify=changed)
    def headlineBuy(self):
        return self._headline_value("shop_buy_price_per_gram")

    @Property(str, notify=changed)
    def headlineSell(self):
        return self._headline_value("shop_sell_price_per_gram")

    @Property(str, notify=changed)
    def compactStatus(self):
        source = "GoldTimo" if self._data.get("source") == "GOLDTIMO" else self.source
        prefix = self.modeText if self.mode == "MANUAL" else self.modeText + " • " + source
        if self.status == "STALE_ALLOWED":
            return prefix + " • السعر قديم"
        if self.status != "FRESH":
            return prefix + " • " + self.statusText
        age = self._data.get("age_seconds")
        return prefix + " • " + ("محدث الآن" if age is not None and int(age) < 10 else "منذ " + self.ageText)

    @staticmethod
    def _compact_price(value):
        text = format(Decimal(value), ",f")
        return text.rstrip("0").rstrip(".") if "." in text else text

    @Slot()
    def _session_changed(self):
        self._request += 1
        self._timer.stop()
        self._data, self._error = {}, ""
        self._success = ""
        self._preview, self._preview_inputs = [], None
        self._reload_pending = self._session.authenticated and not self._shutdown
        self._refresh_pending = False
        self.changed.emit()
        if self._reload_pending and not self._workers:
            self._reload_pending = False
            self._refresh_pending = True
            self._start("snapshot")

    def _start(self, operation, payload=()):
        if self._shutdown or self._workers or not self._session.authenticated:
            return False
        self._timer.stop()
        self._request += 1
        self._error = ""
        if operation in ("manual", "auto"):
            self._success = ""
        worker = GoldPriceWorker(self._service, self._session, self._session.current_actor(),
                                 self._request, operation, payload, self)
        self._workers.add(worker)
        worker.completed.connect(self._completed)
        worker.finished.connect(self._finished)
        self.changed.emit()
        worker.start()
        return True

    @Slot()
    def refresh(self):
        self._start("snapshot" if self.mode == "MANUAL" else "refresh")

    @Slot(str, str, str, result=bool)
    def previewManual(self, buy21, sell21, reason):
        self.invalidatePreview()
        self._success = ""
        if not self._session.authenticated:
            self._error = "يلزم تسجيل الدخول لاعتماد الأسعار."
        elif self.busy:
            self._error = "يوجد تحديث جارٍ؛ انتظر اكتماله ثم أعد المحاولة."
        elif not self.canManage:
            self._error = "يلزم امتلاك صلاحية إدارة الإعدادات."
        elif not reason.strip():
            self._error = "أدخل سبب تغيير السعر اليدوي."
        elif len(reason.strip()) > 500:
            self._error = "سبب التغيير يجب ألا يتجاوز 500 حرف."
        else:
            try:
                self._preview = self._service.preview_manual(buy21, sell21)
                self._preview_inputs = (buy21, sell21, reason.strip())
                self._error = ""
            except (ValueError, PermissionError) as exc:
                self._error = str(exc)
        self.changed.emit()
        return self.previewReady

    @Slot()
    def invalidatePreview(self):
        self._preview, self._preview_inputs = [], None
        self.changed.emit()

    @Slot(result=bool)
    def confirmManual(self):
        if not self._session.authenticated:
            self._error = "يلزم تسجيل الدخول لاعتماد الأسعار."
        elif not self.canManage:
            self._error = "لا توجد صلاحية لإدارة أسعار الذهب."
        elif self.busy:
            self._error = "يوجد تحديث جارٍ؛ انتظر اكتماله ثم أكد الاعتماد."
        elif not self.previewReady:
            self._error = "اعرض الأسعار المشتقة وراجعها قبل الاعتماد."
        elif self._start("manual", self._preview_inputs):
            return True
        else:
            self._error = "تعذر بدء اعتماد الأسعار؛ أعد فتح النافذة وحاول مرة أخرى."
        self.changed.emit()
        return False

    @Slot(str)
    def returnToAuto(self, reason):
        if self.canManage and self.mode == "MANUAL":
            self._start("auto", (reason,))

    @Slot(int, str, str, object, str)
    def _completed(self, request, generation, operation, result, error):
        if self._shutdown or request != self._request or generation != self._session.generation:
            return
        self._error = error
        if result is not None and not error:
            self._data = result
            if operation in ("manual", "auto"):
                self._preview, self._preview_inputs = [], None
                self._success = ("تم اعتماد السعر اليدوي وحفظ أسعار العيارات الثلاثة."
                                 if operation == "manual" else "تم اعتماد الوضع التلقائي.")
            if operation == "auto":
                self._refresh_pending = True
        elif operation in ("refresh", "snapshot"):
            self._data = {**self._data, "status": "ERROR"}
        self.changed.emit()

    @Slot()
    def _finished(self):
        worker = self.sender()
        self._workers.discard(worker)
        worker.deleteLater()
        self.changed.emit()
        if self._shutdown or not self._session.authenticated:
            return
        if self._reload_pending:
            self._reload_pending = False
            self._refresh_pending = True
            self._start("snapshot")
        elif self._refresh_pending:
            self._refresh_pending = False
            if self.mode != "MANUAL":
                self._start("refresh")
            else:
                self._schedule()
        else:
            self._schedule()

    def _schedule(self):
        seconds = int(self._data.get("refresh_seconds", 60))
        self._timer.start(max(1, min(seconds, 86400)) * 1000)

    def shutdown(self):
        self._shutdown = True
        self._request += 1
        self._timer.stop()
        # Requests have bounded service/provider timeouts. Never terminate a DB write.
        for worker in list(self._workers):
            worker.wait()
