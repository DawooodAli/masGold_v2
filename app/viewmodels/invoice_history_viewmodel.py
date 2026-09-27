"""Invoice history and invoice detail (read-only). All text is formatted here;
QML only displays it. Legacy invoices are labelled as partial historical data."""
from datetime import datetime, timezone
import json

from PySide6.QtCore import QObject, Property, Signal, Slot

from app.viewmodels.invoice_viewmodel import grams, money, price
from app.workers.invoice_worker import InvoiceWorker


UNKNOWN = "غير معروف"
TYPE_TEXT = {"SELL": "بيع", "BUY": "شراء كسر", "MIXED": "استبدال"}
STATUS_TEXT = {"FINALIZED": "نهائية", "VOIDED": "ملغاة", "DRAFT": "مسودة"}
SETTLEMENT_TEXT = {"SETTLED": "مسددة", "PARTIAL": "مسددة جزئياً", "UNSETTLED": "غير مسددة", "OVERPAID": "مدفوعة بزيادة"}
NET_TEXT = {"CUSTOMER_PAYS_SHOP": "على العميل", "SHOP_PAYS_CUSTOMER": "للعميل", "SETTLED": "متعادلة"}
EVENT_TEXT = {"CREATED": "إنشاء", "FINALIZED": "إتمام", "PRINTED": "طباعة", "REPRINTED": "إعادة طباعة",
              "PAYMENT_ADDED": "إضافة دفعة", "PAYMENT_CORRECTED": "تصحيح دفعة", "COPIED_TO_NEW_BILL": "نسخ لفاتورة جديدة",
              "VOIDED": "إلغاء", "MIGRATED": "ترحيل من النظام القديم"}
DISCOUNT_TEXT = {"PERCENT": "نسبة", "AMOUNT": "مبلغ"}
FILTER_STATUS = {"ALL": None, "FINALIZED": "FINALIZED", "VOIDED": "VOIDED"}


def known_money(value):
    return UNKNOWN if value is None else money(value)


def local_time(value):
    return "" if value is None else value.strftime("%Y-%m-%d %H:%M")


def utc_time(value):
    """Provider timestamps are stored in UTC; show them in local time with the UTC value."""
    if value is None:
        return ""
    local = value.replace(tzinfo=timezone.utc).astimezone()
    return f"{local:%Y-%m-%d %H:%M:%S} ({value:%H:%M:%S} UTC)"


def net_text(direction, amount):
    if direction == "SETTLED":
        return "متعادلة"
    return f"{NET_TEXT.get(direction, direction)} {known_money(amount)}"


class InvoiceHistoryViewModel(QObject):
    changed = Signal()

    def __init__(self, session, service=None, parent=None):
        super().__init__(parent)
        self._session, self._service = session, service
        self._workers, self._request, self._latest = set(), 0, {}
        self._busy, self._shutdown, self._activated = set(), False, False
        self._filters = {"number": "", "customer_id": None, "date_from": None, "date_to": None, "status": None}
        self._results, self._page, self._has_more = [], 1, False
        self._detail, self._error, self._detail_error = {}, "", ""

    def _service_instance(self):
        if self._service is None:
            from services.invoice_history_service import InvoiceHistoryService
            self._service = InvoiceHistoryService(self._session)
        return self._service

    # ------------------------------------------------------------ workers
    def _start(self, operation, call):
        actor = self._session.current_actor()
        if self._shutdown or actor is None:
            return
        self._request += 1
        self._latest[operation] = self._request
        self._busy.add(operation)
        worker = InvoiceWorker(call, self._session, actor, self._request, operation, self)
        worker.completed.connect(self._completed)
        worker.finished.connect(lambda w=worker: self._workers.discard(w))
        self._workers.add(worker)
        self.changed.emit()
        worker.start()

    @Slot(int, str, str, object, str, str, str)
    def _completed(self, request, generation, operation, result, error, code, kind):
        actor = self._session.current_actor()
        if self._shutdown or actor is None or str(actor.generation) != generation:
            return
        if self._latest.get(operation) != request:
            return
        self._busy.discard(operation)
        getattr(self, f"_on_{operation}")(result, error)
        self.changed.emit()

    @Slot()
    def shutdown(self):
        self._shutdown = True
        for worker in list(self._workers):
            worker.wait(5000)

    # ------------------------------------------------------------ search
    @Slot()
    def activate(self):
        if not self._activated:
            self._activated = True
            self._run_search(1)

    @Slot(str)
    def setCustomer(self, customer_id):
        self._filters["customer_id"] = customer_id or None

    @Slot(str, str, str, str)
    def search(self, number, date_from, date_to, status):
        try:
            parsed = [self._parse_date(date_from), self._parse_date(date_to)]
        except ValueError as exc:
            self._error = str(exc)
            self.changed.emit()
            return
        self._filters.update(number=(number or "").strip(), date_from=parsed[0], date_to=parsed[1],
                             status=FILTER_STATUS.get(status))
        self._error = ""
        self._run_search(1)

    @staticmethod
    def _parse_date(text):
        text = (text or "").strip()
        if not text:
            return None
        try:
            return datetime.strptime(text, "%Y-%m-%d").date()
        except ValueError:
            raise ValueError(f"اكتب التاريخ بالشكل 2026-09-25 (القيمة: {text}).") from None

    @Slot()
    def nextPage(self):
        if self._has_more:
            self._run_search(self._page + 1)

    @Slot()
    def previousPage(self):
        if self._page > 1:
            self._run_search(self._page - 1)

    def _run_search(self, page):
        filters, service = dict(self._filters), self._service_instance()
        self._start("search", lambda _expected_actor: service.search(page=page, _expected_actor=_expected_actor,
                                                                     **filters))

    def _on_search(self, result, error):
        if error:
            self._error = error
            return
        self._results = [self._row(item) for item in result["items"]]
        self._page, self._has_more, self._error = result["page"], result["has_more"], ""

    @staticmethod
    def _row(item):
        customer = " | ".join(v for v in (item.get("customer_code"), item.get("customer_name")) if v)
        return {"invoiceId": item["invoice_id"], "number": item["invoice_number"],
                "date": local_time(item["invoice_date"]),
                "typeText": TYPE_TEXT.get(item["invoice_type"], item["invoice_type"]),
                "statusText": STATUS_TEXT.get(item["invoice_status"], item["invoice_status"]),
                "customerText": customer or "بدون عميل", "salesperson": item.get("salesperson") or "—",
                "netText": net_text(item["net_direction"], item["net_amount"]),
                "isLegacy": item["is_legacy"], "legacyLabel": item["legacy_label"]}

    # ------------------------------------------------------------ detail
    @Slot(str)
    def openInvoice(self, invoice_id):
        service = self._service_instance()
        self._detail, self._detail_error = {}, ""
        self._start("detail", lambda _expected_actor: service.detail(invoice_id, _expected_actor=_expected_actor))

    @Slot()
    def closeDetail(self):
        self._detail, self._detail_error = {}, ""
        self.changed.emit()

    def _on_detail(self, result, error):
        if error:
            self._detail_error = error
            return
        self._detail = result

    # ------------------------------------------------------------ properties
    @Property(bool, notify=changed)
    def busy(self):
        return bool(self._busy)

    @Property(str, notify=changed)
    def errorMessage(self):
        return self._error

    @Property("QVariantList", notify=changed)
    def results(self):
        return self._results

    @Property(int, notify=changed)
    def page(self):
        return self._page

    @Property(bool, notify=changed)
    def hasMore(self):
        return self._has_more

    @Property(bool, notify=changed)
    def hasPrevious(self):
        return self._page > 1

    @Property(str, notify=changed)
    def pageText(self):
        return f"صفحة {self._page}" + ("" if self._results else " — لا توجد فواتير مطابقة")

    @Property(bool, notify=changed)
    def detailOpen(self):
        return bool(self._detail) or "detail" in self._busy or bool(self._detail_error)

    @Property(str, notify=changed)
    def detailError(self):
        return self._detail_error

    @Property("QVariantMap", notify=changed)
    def detail(self):
        d = self._detail
        if not d:
            return {}
        s = d["settlement"]
        return {
            "number": d["invoice_number"], "isLegacy": d["is_legacy"], "legacyLabel": d["legacy_label"],
            "legacyNumber": d.get("legacy_bill_number") or "",
            "completeness": d.get("legacy_completeness") or "",
            "typeText": TYPE_TEXT.get(d["invoice_type"], d["invoice_type"]),
            "statusText": STATUS_TEXT.get(d["invoice_status"], d["invoice_status"]),
            "date": local_time(d["invoice_date"]), "finalizedAt": local_time(d.get("finalized_at")),
            "salesperson": d.get("employee_name") or "—",
            "createdBy": d.get("created_by_name") or "—", "finalizedBy": d.get("finalized_by_name") or "—",
            "customerName": d.get("customer_name_snapshot") or "بدون عميل",
            "customerCode": d.get("customer_code_snapshot") or "",
            "customerMobile": d.get("mobile_snapshot") or "", "customerPlace": d.get("place_snapshot") or "",
            "balanceBefore": known_money(d.get("balance_before")) if d.get("customer_id") else "",
            "balanceAfter": known_money(d.get("balance_after")) if d.get("customer_id") else "",
            "deadCashWarning": bool(d.get("dead_cash_warning_active")),
            "saleTotal": known_money(d.get("sell_total")), "oldGoldTotal": known_money(d.get("buy_total")),
            "netText": net_text(s["net_direction"], s["net_amount"]),
            "receivedText": money(s["money_received"]), "paidText": money(s["money_paid"]),
            "residualText": UNKNOWN + " (بيانات قديمة)" if s["residual_signed"] is None
            else ("مسددة بالكامل" if s["residual_signed"] == 0 else
                  f"{'متبقي على العميل' if s['residual_signed'] > 0 else 'متبقي للعميل'} {money(s['residual_amount'])}"),
            "settlementText": SETTLEMENT_TEXT.get(d["settlement_status"], d["settlement_status"]),
            "notes": d.get("notes") or "",
        }

    def _lines(self, key):
        return self._detail.get(key, []) if self._detail else []

    @Property("QVariantList", notify=changed)
    def saleLines(self):
        return [{"barcode": l.get("barcode_snapshot") or "", "itemName": l.get("item_name_snapshot") or "",
                 "karat": str(l["karat_value"]), "weight": grams(l["weight_grams"]),
                 "goldPrice": price(l["gold_price_per_gram"]) or UNKNOWN,
                 "making": known_money(l.get("making_total")), "lineTotal": known_money(l.get("line_total"))}
                for l in self._lines("sale_lines")]

    @Property("QVariantList", notify=changed)
    def oldGoldLines(self):
        return [{"karat": str(l["karat_value"]), "weight": grams(l["weight_grams"]),
                 "goldPrice": price(l["gold_price_per_gram"]) or UNKNOWN,
                 "gross": known_money(l.get("gross_total")),
                 "discount": ("" if not l.get("discount_type") else
                              f"{DISCOUNT_TEXT[l['discount_type']]} {l['discount_value']} = {money(l['discount_amount'])}"),
                 "lineTotal": known_money(l.get("line_total"))}
                for l in self._lines("old_gold_lines")]

    @Property("QVariantList", notify=changed)
    def otherLines(self):
        return [{"itemName": l.get("item_name_snapshot") or "بند", "karat": str(l["karat_value"]),
                 "weight": grams(l["weight_grams"]), "lineTotal": known_money(l.get("line_total"))}
                for l in self._lines("other_lines")]

    @Property("QVariantList", notify=changed)
    def payments(self):
        return [{"method": p["payment_method_name"], "directionText": "مستلم" if p["direction"] == "IN" else "مدفوع",
                 "amount": money(p["amount"]), "time": local_time(p["occurred_at"]),
                 "reference": p.get("payment_reference") or ""}
                for p in self._lines("payments")]

    @Property("QVariantList", notify=changed)
    def prices(self):
        return [{"karat": str(p["karat_value"]), "buy": price(p.get("shop_buy_price_per_gram")) or UNKNOWN,
                 "sell": price(p.get("shop_sell_price_per_gram")) or UNKNOWN,
                 "source": p.get("source_code_snapshot") or "", "providerTime": utc_time(p.get("provider_timestamp"))}
                for p in self._lines("price_snapshots")]

    @Property("QVariantList", notify=changed)
    def events(self):
        rows = []
        for e in self._lines("events"):
            details = json.loads(e["details_json"]) if isinstance(e.get("details_json"), str) else (e.get("details_json") or {})
            notes = []
            if details.get("stale_acknowledged"):
                notes.append("تم الإقرار بسعر قديم")
            if details.get("dead_cash_confirmed"):
                notes.append("تأكيد مديونية مع تحذير ديون معدومة")
            rows.append({"eventText": EVENT_TEXT.get(e["event_type"], e["event_type"]),
                         "time": local_time(e["occurred_at"]), "by": e.get("performed_by_name") or "—",
                         "notes": "، ".join(notes) or (e.get("notes") or "")})
        return rows
