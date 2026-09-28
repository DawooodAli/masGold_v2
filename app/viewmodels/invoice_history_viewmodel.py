"""سجل الفواتير (read-only): search, filters, paging, invoice detail and reprint.

All text is formatted here (app.formatting: one money format, bidi-safe dates / signed values);
QML only displays it. Nothing in this ViewModel can change an invoice."""
from datetime import date, datetime

from PySide6.QtCore import QObject, Property, QTimer, Signal, Slot

from app.formatting import datetime_text, grams, ltr, money
from app.workers.invoice_worker import InvoiceWorker

UNAVAILABLE = "غير متاح"
TYPE_TEXT = {"SELL": "بيع", "BUY": "شراء كسر", "MIXED": "بيع مع كسر"}
SETTLEMENT_TEXT = {"SETTLED": "مسددة", "PARTIAL": "مسددة جزئياً", "UNSETTLED": "غير مسددة", "OVERPAID": "مدفوعة بزيادة"}
STATUS_TEXT = {"FINALIZED": "نهائية", "VOIDED": "ملغاة"}
DIRECTION_TEXT = {"CUSTOMER_PAYS_SHOP": "يدفع العميل", "SHOP_PAYS_CUSTOMER": "يدفع المحل", "SETTLED": "متعادلة"}
KIND_TEXT = {"INVENTORY": "من المخزون", "MANUAL": "بيع يدوي (غير محدد في المخزون)", "OLD_GOLD_STOCK": "من رصيد الكسر"}
IDENTIFIED_TEXT = {"SCAN": "بالباركود", "SEARCH": "بالبحث"}
EVENT_TEXT = {"CREATED": "تم إنشاء الفاتورة", "FINALIZED": "تم حفظ الفاتورة نهائياً", "PRINTED": "طباعة",
              "REPRINTED": "إعادة طباعة", "PAYMENT_ADDED": "إضافة دفعة", "PAYMENT_CORRECTED": "تصحيح دفعة",
              "VOIDED": "إلغاء", "MIGRATED": "ترحيل من النظام القديم"}
METHOD_TEXT = {"PHYSICAL_CASH": "نقدي", "MOBILE_WALLET": "محفظة", "INSTAPAY": "InstaPay", "BANK_TRANSFER": "تحويل بنكي"}
DISCOUNT_TEXT = {"PERCENT": "%", "AMOUNT": "ج"}
MAKING_STATUS_TEXT = {"WITHIN_RANGE": "داخل النطاق", "OUT_OF_RANGE": "خارج النطاق", "NOT_CONFIGURED": "النطاق غير محدد"}
TYPE_FILTERS = {"ALL": None, "SELL": "SELL", "BUY": "BUY", "MIXED": "MIXED"}
STATUS_FILTERS = {"ALL": None, "SETTLED": "SETTLED", "PARTIAL": "PARTIAL", "UNSETTLED": "UNSETTLED"}


def known_money(value):
    return UNAVAILABLE if value is None else money(value)


class InvoiceHistoryViewModel(QObject):
    changed = Signal()

    def __init__(self, session, service=None, parent=None, debounce_ms=300, clock=datetime.now):
        super().__init__(parent)
        self._session, self._service, self._clock = session, service, clock
        self._workers, self._request, self._latest = set(), 0, {}
        self._busy, self._shutdown, self._activated = set(), False, False
        self._text, self._date_from, self._date_to = "", None, None
        self._type, self._status = "ALL", "ALL"
        self._results, self._page, self._has_more, self._loaded = [], 1, False, False
        self._detail, self._error, self._detail_error = {}, "", ""
        self._reprint = {}
        # Typing in the search box: one query after a short pause, never one per keystroke.
        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(debounce_ms)
        self._debounce.timeout.connect(lambda: self._run_search(1))

    # ------------------------------------------------------------ plumbing
    def _service_instance(self):
        if self._service is None:
            from services.invoice_history_service import InvoiceHistoryService
            self._service = InvoiceHistoryService(self._session)
        return self._service

    def _start(self, operation, call):
        if self._shutdown:
            return
        actor = self._session.current_actor()
        if actor is None:
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
        self._debounce.stop()
        for worker in list(self._workers):
            worker.wait(5000)

    @Slot()
    def activate(self):
        """Opening the history tab loads the newest page once."""
        if not self._activated:
            self._activated = True
            self._run_search(1)

    # ------------------------------------------------------------ search / filters / paging
    @Slot(str)
    def setSearchText(self, text):
        text = (text or "").strip()
        if text != self._text:
            self._text = text
            self._debounce.start()

    @Slot()
    def searchNow(self):
        self._debounce.stop()
        self._run_search(1)

    @Slot(str)
    def submitSearch(self, text):
        """Enter in the History search box (typed or scanned). An exact invoice number — e.g. the
        printed invoice barcode — opens that invoice directly; anything else (partial number, name,
        phone) stays an ordinary search. Only an exact number ever auto-opens."""
        from app.barcode_input import canonical_barcode
        self._debounce.stop()
        self._text = (text or "").strip()
        number, _ = canonical_barcode(self._text)          # same normalization as every barcode field
        if not number:
            self._run_search(1)
            return
        self._text = number
        service = self._service_instance()
        self._error = ""
        self._start("exact", lambda _expected_actor: (number, service.find_by_number(
            number, _expected_actor=_expected_actor)))

    def _on_exact(self, result, error):
        if error:
            self._error = error
            return
        number, invoice_id = result
        if invoice_id:
            self.openInvoice(invoice_id)
            return
        self._run_search(1)                                 # not an exact number: the normal search
        if number.isdigit() and len(number) == 8:            # an invoice number's length (phones are 11)
            self._error = "لم يتم العثور على الفاتورة"

    @Slot()
    def setToday(self):
        today = self._clock().date()
        self._date_from = self._date_to = today
        self._run_search(1)

    @Slot(str, str)
    def setDateRange(self, date_from, date_to):
        try:
            self._date_from, self._date_to = self._parse_date(date_from), self._parse_date(date_to)
        except ValueError as exc:
            self._error = str(exc)
            self.changed.emit()
            return
        self._run_search(1)

    @Slot(str)
    def setTypeFilter(self, code):
        self._type = code if code in TYPE_FILTERS else "ALL"
        self._run_search(1)

    @Slot(str)
    def setStatusFilter(self, code):
        self._status = code if code in STATUS_FILTERS else "ALL"
        self._run_search(1)

    @Slot()
    def clearFilters(self):
        self._text, self._date_from, self._date_to, self._type, self._status = "", None, None, "ALL", "ALL"
        self._run_search(1)

    @staticmethod
    def _parse_date(text):
        text = (text or "").strip()
        if not text:
            return None
        try:
            return datetime.strptime(text, "%Y-%m-%d").date()
        except ValueError:
            raise ValueError(f"اكتب التاريخ بالشكل 2026-09-27 (القيمة: {text}).") from None

    @Slot()
    def nextPage(self):
        if self._has_more:
            self._run_search(self._page + 1)

    @Slot()
    def previousPage(self):
        if self._page > 1:
            self._run_search(self._page - 1)

    def _run_search(self, page):
        service = self._service_instance()
        filters = {"text": self._text, "date_from": self._date_from, "date_to": self._date_to,
                   "invoice_type": TYPE_FILTERS[self._type], "settlement_status": STATUS_FILTERS[self._status]}
        self._error = ""
        self._start("search", lambda _expected_actor: service.search(page=page, _expected_actor=_expected_actor,
                                                                     **filters))

    def _on_search(self, result, error):
        if error:
            self._error = error
            return
        self._results = [self._row(item) for item in result["items"]]
        self._page, self._has_more, self._loaded = result["page"], result["has_more"], True

    @staticmethod
    def _row(item):
        moved_label = "مدفوع" if item["net_direction"] == "SHOP_PAYS_CUSTOMER" else "مستلم"
        when = item.get("finalized_at")
        return {"invoiceId": item["invoice_id"], "number": item["invoice_number"],
                "date": ltr(when.strftime("%Y-%m-%d")) if when else "", "time": when.strftime("%H:%M") if when else "",
                "customer": item.get("customer_name") or "بدون عميل", "salesperson": item.get("salesperson") or "—",
                "typeText": TYPE_TEXT.get(item["invoice_type"], item["invoice_type"]),
                "total": known_money(item.get("invoice_total")),
                "moved": f"{moved_label} {money(item['money_moved'])}",
                "statusText": SETTLEMENT_TEXT.get(item["settlement_status"], item["settlement_status"] or ""),
                "isLegacy": bool(item.get("is_legacy"))}

    # ------------------------------------------------------------ detail
    @Slot(str)
    def openInvoice(self, invoice_id):
        if not invoice_id:
            return
        service = self._service_instance()
        self._detail, self._detail_error, self._reprint = {}, "", {}
        self._start("detail", lambda _expected_actor: service.detail(invoice_id, _expected_actor=_expected_actor))

    @Slot()
    def closeDetail(self):
        self._detail, self._detail_error, self._reprint = {}, "", {}
        self._busy.discard("detail")
        self.changed.emit()

    def _on_detail(self, result, error):
        if error:
            self._detail_error = error or "تعذر تحميل الفاتورة."
            return
        self._detail = result

    # ------------------------------------------------------------ reprint (read-only)
    @Slot()
    def reprint(self):
        """The original invoice through the frozen print service; never saves anything."""
        if not self._detail or not self._detail.get("can_reprint") or "reprint" in self._busy:
            return
        service, invoice_id = self._service_instance(), self._detail["invoice_id"]
        self._reprint = {}
        self._start("reprint", lambda _expected_actor: service.reprint(invoice_id, _expected_actor=_expected_actor))

    def _on_reprint(self, result, error):
        if error:
            self._reprint = {"printed": False, "message": error}
        else:
            self._reprint = {"printed": bool(result.printed),
                             "message": "تم إرسال الفاتورة إلى الطابعة." if result.printed else result.message}

    # ------------------------------------------------------------ list properties
    @Property(bool, notify=changed)
    def busy(self):
        return "search" in self._busy

    @Property(str, notify=changed)
    def errorMessage(self):
        return self._error

    @Property("QVariantList", notify=changed)
    def results(self):
        return self._results

    @Property(str, notify=changed)
    def searchText(self):
        return self._text

    @Property(str, notify=changed)
    def typeFilter(self):
        return self._type

    @Property(str, notify=changed)
    def statusFilter(self):
        return self._status

    @Property(str, notify=changed)
    def dateFrom(self):
        return "" if self._date_from is None else self._date_from.isoformat()

    @Property(str, notify=changed)
    def dateTo(self):
        return "" if self._date_to is None else self._date_to.isoformat()

    @Property(bool, notify=changed)
    def filtered(self):
        return bool(self._text or self._date_from or self._date_to or self._type != "ALL" or self._status != "ALL")

    @Property(str, notify=changed)
    def emptyText(self):
        if self.busy and not self._loaded:
            return "جاري التحميل..."
        if self._results:
            return ""
        return "لا توجد نتائج مطابقة" if self.filtered else "لا توجد فواتير"

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
        return f"صفحة {self._page}"

    # ------------------------------------------------------------ detail properties
    @Property(bool, notify=changed)
    def detailOpen(self):
        return bool(self._detail) or "detail" in self._busy or bool(self._detail_error)

    @Property(bool, notify=changed)
    def detailBusy(self):
        return "detail" in self._busy

    @Property(str, notify=changed)
    def detailError(self):
        return self._detail_error

    @Property("QVariantMap", notify=changed)
    def detail(self):
        d = self._detail
        if not d:
            return {}
        e, customer = d["economics"], d["customer"]
        outgoing = d["net_direction"] == "SHOP_PAYS_CUSTOMER"
        account = d["account_signed"]
        account_text = ("لا يوجد" if not account else
                        f"{'على العميل' if account > 0 else 'للعميل'}: {money(abs(account))}")
        incomplete = not d["is_legacy"] and (e["gold_value"] is None and d["sale_lines"])
        return {
            "invoiceId": d["invoice_id"], "number": d["invoice_number"],
            "finalizedAt": datetime_text(d["finalized_at"]),
            "typeText": TYPE_TEXT.get(d["invoice_type"], d["invoice_type"]),
            "statusText": STATUS_TEXT.get(d["invoice_status"], d["invoice_status"]),
            "settlementText": SETTLEMENT_TEXT.get(d["settlement_status"], d["settlement_status"] or ""),
            "salesperson": d.get("salesperson") or "—", "finalizedBy": d.get("finalized_by") or "—",
            "isLegacy": d["is_legacy"], "legacyLabel": d["legacy_label"],
            "incompleteText": "بيانات الفاتورة غير مكتملة (فاتورة قديمة)" if incomplete else "",
            "customerName": customer.get("name") or "بدون عميل", "customerCode": customer.get("code") or "",
            "customerMobile": customer.get("mobile") or "", "customerPlace": customer.get("place") or "",
            "invoiceTotal": known_money(d["invoice_total"]),
            "directionText": DIRECTION_TEXT.get(d["net_direction"], ""),
            "movedLabel": "المدفوع للعميل" if outgoing else "المستلم من العميل",
            "movedAmount": money(d["money_paid"] if outgoing else d["money_received"]),
            "accountText": account_text,
            "accountDirection": "" if not account else ("CUSTOMER_OWES" if account > 0 else "SHOP_OWES"),
            "goldValue": known_money(e["gold_value"]),
            "averageMaking": UNAVAILABLE if e["average_making"] is None else f"{e['average_making']:,.2f} ج/جم",
            "makingTotal": "" if not d["can_see_manager_details"] else known_money(e["making_total"]),
            "makingStatus": MAKING_STATUS_TEXT.get(e.get("making_status") or "", ""),
            "oldGoldTotal": known_money(d.get("buy_total")) if d["old_gold_lines"] else "",
            "notes": d.get("notes") or "",
            "canReprint": bool(d.get("can_reprint")),
            "showManager": bool(d["can_see_manager_details"]),
        }

    @Property("QVariantList", notify=changed)
    def saleLines(self):
        rows = []
        for i, line in enumerate(self._detail.get("sale_lines", []) if self._detail else []):
            kind = KIND_TEXT[line["kind"]]
            if line["kind"] == "INVENTORY" and line.get("identification_method") in IDENTIFIED_TEXT:
                kind += " — " + IDENTIFIED_TEXT[line["identification_method"]]
            rows.append({"serial": str(i + 1), "item": line.get("item_name") or line.get("category") or "—",
                         "category": line.get("category") or "", "factory": line.get("factory") or "—",
                         "karat": str(line["karat"]), "weight": grams(line["weight"]), "qty": str(line["quantity"]),
                         "size": line.get("size") or "—", "notes": line.get("notes") or "",
                         "barcode": line.get("barcode") or "", "kindText": kind,
                         "manual": line["kind"] == "MANUAL", "fromOldGold": line["kind"] == "OLD_GOLD_STOCK"})
        return rows

    @Property("QVariantList", notify=changed)
    def oldGoldLines(self):
        rows = []
        for line in self._detail.get("old_gold_lines", []) if self._detail else []:
            discount = ""
            if line.get("discount_type") in DISCOUNT_TEXT and line.get("discount_value") is not None:
                discount = ltr(f"{line['discount_value']:,.2f}{DISCOUNT_TEXT[line['discount_type']]}")
            rows.append({"karat": str(line["karat"]), "weight": grams(line["weight"]),
                         "price": UNAVAILABLE if line["gold_price"] is None else f"{line['gold_price']:,.2f}",
                         "gross": known_money(line["gross"]), "discount": discount or "—",
                         "value": known_money(line["line_total"])})
        return rows

    @Property("QVariantList", notify=changed)
    def payments(self):
        rows = []
        for p in self._detail.get("payments", []) if self._detail else []:
            label = METHOD_TEXT.get(p["method_code"], p.get("method_name") or "")
            if p.get("account_name"):
                label += " — " + p["account_name"]
            rows.append({"method": label, "directionText": "مستلم" if p["direction"] == "IN" else "مدفوع",
                         "amount": money(p["amount"])})
        return rows

    @Property("QVariantList", notify=changed)
    def priceSnapshots(self):
        return [{"karat": str(p["karat"]),
                 "sell": UNAVAILABLE if p["sell"] is None else f"{p['sell']:,.2f}",
                 "buy": UNAVAILABLE if p["buy"] is None else f"{p['buy']:,.2f}",
                 "source": p.get("source") or ""}
                for p in (self._detail.get("price_snapshots", []) if self._detail else [])]

    @Property("QVariantList", notify=changed)
    def timeline(self):
        return [{"eventType": e.event_type, "text": EVENT_TEXT.get(e.event_type, e.event_type),
                 "time": datetime_text(e.occurred_at), "actor": e.actor or "—",
                 "amount": "" if e.amount is None else money(e.amount), "linked": e.linked_record_id or ""}
                for e in (self._detail.get("timeline", []) if self._detail else [])]

    # ------------------------------------------------------------ reprint properties
    @Property(bool, notify=changed)
    def reprintBusy(self):
        return "reprint" in self._busy

    @Property(str, notify=changed)
    def reprintMessage(self):
        return self._reprint.get("message", "")

    @Property(bool, notify=changed)
    def reprintFailed(self):
        return bool(self._reprint) and not self._reprint.get("printed", True)
