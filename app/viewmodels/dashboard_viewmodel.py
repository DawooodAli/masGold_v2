"""الرئيسية: today's activity, formatted in Python (QML only binds strings and lists)."""
from datetime import datetime
from decimal import Decimal

from PySide6.QtCore import QObject, Property, QTimer, Signal, Slot

from app.formatting import grams, ltr, money, number
from app.workers.invoice_worker import InvoiceWorker

# Staff wording for who pays whom on an invoice (an internal screen, not the customer's bill).
DIRECTION_TEXT = {"CUSTOMER_PAYS_SHOP": "يدفع العميل", "SHOP_PAYS_CUSTOMER": "يدفع المحل", "SETTLED": "متعادلة"}
WEEKDAYS = ("الاثنين", "الثلاثاء", "الأربعاء", "الخميس", "الجمعة", "السبت", "الأحد")


class DashboardViewModel(QObject):
    changed = Signal()

    def __init__(self, session, service=None, parent=None, refresh_ms=120000, clock=datetime.now):
        super().__init__(parent)
        self._session, self._service, self._clock = session, service, clock
        self._workers, self._request, self._shutdown = set(), 0, False
        self._data, self._error, self._busy = {}, "", False
        # A gentle safety refresh; the real triggers are opening the page and a finalized invoice.
        self._timer = QTimer(self)
        self._timer.setInterval(refresh_ms)
        self._timer.timeout.connect(self.refresh)

    def _service_instance(self):
        if self._service is None:
            from services.dashboard_service import DashboardService
            self._service = DashboardService(self._session)
        return self._service

    @Slot()
    def activate(self):
        self.refresh()
        self._timer.start()

    @Slot()
    @Slot(str)
    def refresh(self, _reason=""):
        actor = self._session.current_actor()
        if self._shutdown or actor is None:
            return
        service = self._service_instance()
        self._request += 1
        self._busy = True
        worker = InvoiceWorker(lambda _expected_actor: service.today(_expected_actor=_expected_actor),
                               self._session, actor, self._request, "dashboard", self)
        worker.completed.connect(self._completed)
        worker.finished.connect(lambda w=worker: self._workers.discard(w))
        self._workers.add(worker)
        self.changed.emit()
        worker.start()

    @Slot(int, str, str, object, str, str, str)
    def _completed(self, request, generation, operation, result, error, code, kind):
        actor = self._session.current_actor()
        if self._shutdown or actor is None or str(actor.generation) != generation or request != self._request:
            return
        self._busy = False
        if error:
            self._error = "تعذر تحميل ملخص اليوم. سيعاد المحاولة تلقائياً."
        else:
            self._data, self._error = result, ""
        self.changed.emit()

    @Slot()
    def shutdown(self):
        self._shutdown = True
        self._timer.stop()
        for worker in list(self._workers):
            worker.wait(5000)

    # ------------------------------------------------------------ state
    @Property(bool, notify=changed)
    def ready(self):
        return bool(self._data)

    @Property(bool, notify=changed)
    def busy(self):
        return self._busy

    @Property(str, notify=changed)
    def errorMessage(self):
        return self._error

    @Property(str, notify=changed)
    def dateText(self):
        now = self._clock()
        return f"{WEEKDAYS[now.weekday()]} {ltr(f'{now:%d-%m-%Y}')}"

    @Property(bool, notify=changed)
    def canSeeSales(self):
        return bool(self._data.get("can_see_sales"))

    # ------------------------------------------------------------ today (billing permission)
    @Property(int, notify=changed)
    def invoiceCount(self):
        return int(self._data.get("invoice_count", 0))

    @Property(str, notify=changed)
    def invoiceBreakdown(self):
        if not self.invoiceCount:
            return "لا توجد فواتير اليوم بعد"
        return f"بيع {self._data.get('sale_invoice_count', 0)} • شراء كسر {self._data.get('buy_invoice_count', 0)}"

    @Property(str, notify=changed)
    def sellTotalText(self):
        return money(self._data.get("sell_total", "0"))

    @Property(str, notify=changed)
    def buyTotalText(self):
        return money(self._data.get("buy_total", "0"))

    @Property(int, notify=changed)
    def unidentifiedCount(self):
        return int(self._data.get("unidentified_lines", 0))

    @Property("QVariantList", notify=changed)
    def soldByKarat(self):
        return [{"karat": str(r["karat"]), "grams": grams(r["grams"]),
                 "pieces": f"{r['pieces']} قطعة",
                 "fromOldGold": (f"منها من الكسر {grams(r['from_old_gold'])}"
                                 if Decimal(r["from_old_gold"]) else "")}
                for r in self._data.get("sold", [])]

    @Property("QVariantList", notify=changed)
    def recentInvoices(self):
        rows = []
        for r in self._data.get("recent", []):
            when = r.get("finalized_at")
            today = self._clock().date()
            time_text = ("" if when is None else when.strftime("%H:%M") if when.date() == today
                         else ltr(when.strftime("%d-%m %H:%M")))
            rows.append({"number": r["invoice_number"], "time": time_text,
                         "customer": r["customer"] or "بدون عميل", "salesperson": r["salesperson"],
                         "amount": money(r["net_amount"]),
                         "direction": DIRECTION_TEXT.get(r["net_direction"], "")})
        return rows

    # ------------------------------------------------------------ old gold (every logged-in user)
    @Property("QVariantList", notify=changed)
    def oldGold(self):
        rows = []
        for r in self._data.get("old_gold", []):
            grams_in, grams_out = Decimal(r["in"]), Decimal(r["out"])
            moved = grams_in or grams_out
            rows.append({"karat": str(r["karat"]), "balance": grams(r["balance"]),
                         "zero": Decimal(r["balance"]) == 0,
                         "inText": number(grams_in, 3, signed=True) if grams_in else "",
                         "outText": number(-grams_out, 3) if grams_out else "",
                         "todayText": "" if moved else "لا حركة اليوم"})
        return rows
