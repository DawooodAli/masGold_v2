"""سجل الفواتير (read-only): search, filters, paging, invoice detail and reprint.

All text is formatted here (app.formatting: one money format, bidi-safe dates / signed values);
QML only displays it. Nothing in this ViewModel can change an invoice."""
from datetime import date, datetime
from decimal import Decimal

from PySide6.QtCore import QObject, Property, QTimer, Signal, Slot

from app.formatting import datetime_text, grams, ltr, money, number
from app.workers.invoice_worker import InvoiceWorker

UNAVAILABLE = "غير متاح"
RECEIVED_TEXT, PAID_TEXT, NO_MONEY_TEXT = "المستلم من العميل", "المدفوع للعميل", "لا يوجد دفع فعلي"
TYPE_TEXT = {"SELL": "بيع", "BUY": "شراء كسر", "MIXED": "بيع مع كسر"}
SOURCE_TEXT = {"SCAN": "بالباركود", "SEARCH": "بالبحث", "MANUAL": "إدخال يدوي", "OLD_GOLD_STOCK": "من الكسر"}
KIND_TYPE_TEXT = {"SALE": "بيع", "OLD_GOLD_PURCHASE": "شراء كسر", "MIXED": "بيع مع كسر"}
SETTLEMENT_TEXT = {"SETTLED": "مسددة", "PARTIAL": "مسددة جزئياً", "UNSETTLED": "غير مسددة", "OVERPAID": "مدفوعة بزيادة"}
STATUS_TEXT = {"FINALIZED": "نهائية", "VOIDED": "ملغاة"}
DIRECTION_TEXT = {"CUSTOMER_PAYS_SHOP": "يدفع العميل", "SHOP_PAYS_CUSTOMER": "يدفع المحل", "SETTLED": "متعادلة"}
KIND_TEXT = {"INVENTORY": "من المخزون", "MANUAL": "بيع يدوي (غير محدد في المخزون)", "OLD_GOLD_STOCK": "من رصيد الكسر"}
IDENTIFIED_TEXT = {"SCAN": "بالباركود", "SEARCH": "بالبحث"}
EVENT_TEXT = {"CREATED": "تم إنشاء الفاتورة", "FINALIZED": "تم حفظ الفاتورة نهائياً", "PRINTED": "طباعة",
              "REPRINTED": "إعادة طباعة", "PAYMENT_ADDED": "إضافة دفعة", "PAYMENT_CORRECTED": "تصحيح دفعة",
              "VOIDED": "إلغاء", "MIGRATED": "ترحيل من النظام القديم", "EXCHANGED": "تم إجراء استبدال"}
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
        self._explicit_range = False
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
        """Opening the history tab loads TODAY's invoices only (one bounded, date-restricted query)."""
        if not self._activated:
            self._activated = True
            today = self._clock().date()
            self._date_from = self._date_to = today
            self._explicit_range = False
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
        self._explicit_range = False                     # back to the default browsing scope
        self._run_search(1)

    @Slot(str, str)
    def setDateRange(self, date_from, date_to):
        try:
            self._date_from, self._date_to = self._parse_date(date_from), self._parse_date(date_to)
            self._explicit_range = True                  # the user chose this range: always respected
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
        """Back to the default view: today, every type and status (never "all history")."""
        today = self._clock().date()
        self._text, self._date_from, self._date_to, self._type, self._status = "", today, today, "ALL", "ALL"
        self._explicit_range = False
        self._run_search(1)

    @Property(bool, notify=changed)
    def todayActive(self):
        """اليوم is the active range (the default on open and after مسح)."""
        today = self._clock().date()
        return self._date_from == today and self._date_to == today

    @Property(str, notify=changed)
    def rangeText(self):
        """What the list shows, for the user (e.g. فواتير اليوم / من … إلى …)."""
        if self.searchesAllHistory:
            return "بحث العميل في كل الفواتير"
        if self.todayActive:
            return "فواتير اليوم"
        if self._date_from and self._date_to:
            return f"من {ltr(self._date_from.isoformat())} إلى {ltr(self._date_to.isoformat())}"
        if self._date_from:
            return f"من {ltr(self._date_from.isoformat())}"
        if self._date_to:
            return f"حتى {ltr(self._date_to.isoformat())}"
        return "كل الفترات"

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

    @staticmethod
    def _is_customer_search(text):
        """A customer name or phone (letters, or digits starting with 0 like 010…) — not a partial invoice
        number (digits not starting with 0, e.g. 2609…), which keeps its usual in-range behaviour."""
        text = (text or "").strip()
        return bool(text) and not (text.isascii() and text.isdigit() and not text.startswith("0"))

    def _search_dates(self):
        """Default اليوم is only the opening scope: a customer name / phone searches all history (still one
        bounded page per query). An explicit From / To range is the user's intent and is always respected."""
        if not self._explicit_range and self._is_customer_search(self._text):
            return None, None
        return self._date_from, self._date_to

    @Property(bool, notify=changed)
    def searchesAllHistory(self):
        return self._search_dates() == (None, None)

    def _run_search(self, page):
        service = self._service_instance()
        date_from, date_to = self._search_dates()
        filters = {"text": self._text, "date_from": date_from, "date_to": date_to,
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
                "typeText": KIND_TYPE_TEXT.get(item.get("transaction_kind"),
                                               TYPE_TEXT.get(item["invoice_type"], item["invoice_type"])),
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
        """Anything beyond the default view (today, every type and status)."""
        dates = (self._date_from or self._date_to) and not self.todayActive
        return bool(self._text or dates or self._type != "ALL" or self._status != "ALL")

    @Property(str, notify=changed)
    def emptyText(self):
        if self.busy and not self._loaded:
            return "جاري التحميل..."
        if self._results:
            return ""
        if self.filtered:
            return "لا توجد نتائج مطابقة"
        return "لا توجد فواتير اليوم" if self.todayActive else "لا توجد فواتير"

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
        moved_label, moved_amount = self.money_direction(d)
        account = d["account_signed"]
        account_text = ("لا يوجد" if not account else
                        f"{'على العميل' if account > 0 else 'للعميل'}: {money(abs(account))}")
        incomplete = not d["is_legacy"] and (e["gold_value"] is None and d["sale_lines"])
        return {
            "invoiceId": d["invoice_id"], "number": d["invoice_number"],
            "finalizedAt": datetime_text(d["finalized_at"]),
            # From the invoice CONTENT (sold lines / customer old gold), never from the money direction.
            "typeText": KIND_TYPE_TEXT[self._kind(d)],
            "transactionKind": self._kind(d),
            "statusText": STATUS_TEXT.get(d["invoice_status"], d["invoice_status"]),
            "settlementText": SETTLEMENT_TEXT.get(d["settlement_status"], d["settlement_status"] or ""),
            "salesperson": d.get("salesperson") or "—", "finalizedBy": d.get("finalized_by") or "—",
            "isLegacy": d["is_legacy"], "legacyLabel": d["legacy_label"],
            "incompleteText": "بيانات الفاتورة غير مكتملة (فاتورة قديمة)" if incomplete else "",
            "customerName": customer.get("name") or "بدون عميل", "customerCode": customer.get("code") or "",
            "customerMobile": customer.get("mobile") or "", "customerPlace": customer.get("place") or "",
            "invoiceTotal": known_money(d["invoice_total"]),
            "directionText": DIRECTION_TEXT.get(d["net_direction"], ""),
            # Direction-aware title of the real money (also the payments card title); never "الدفع الفعلي".
            "movedLabel": moved_label,
            "movedAmount": moved_amount,
            # Compact secondary line under the money figure (the amount is never repeated).
            "paymentMethods": " • ".join(self._method_label(p) for p in d["payments"]),
            # Pure شراء كسر: the customer sold gold to the shop; nothing was sold from the shop.
            "isOldGoldOnly": self._kind(d) == "OLD_GOLD_PURCHASE",
            "accountText": account_text,
            "accountDirection": "" if not account else ("CUSTOMER_OWES" if account > 0 else "SHOP_OWES"),
            "goldValue": known_money(e["gold_value"]),
            "averageMaking": UNAVAILABLE if e["average_making"] is None else money(e["average_making"]),
            "makingTotal": "" if not d["can_see_manager_details"] else known_money(e["making_total"]),
            "makingStatus": MAKING_STATUS_TEXT.get(e.get("making_status") or "", ""),
            "oldGoldTotal": known_money(d.get("buy_total")) if d["old_gold_lines"] else "",
            "notes": d.get("notes") or "",
            "canReprint": bool(d.get("can_reprint")),
            "canExchange": bool(d.get("can_exchange")),
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

    @staticmethod
    def _kind(d):
        """SALE / OLD_GOLD_PURCHASE / MIXED from the saved lines (services.invoice_history_service.transaction_kind)."""
        if d.get("transaction_kind"):
            return d["transaction_kind"]
        from services.invoice_history_service import transaction_kind
        return transaction_kind(d.get("sale_lines"), d.get("old_gold_lines"))

    @staticmethod
    def money_direction(d):
        """(title, amount) of the real money, from the SAVED payments' own direction (IN: received from the
        customer, OUT: paid to the customer); the finalized net direction only when no payment was saved.
        One invoice has one money direction (Billing never mixes IN and OUT legs)."""
        received, paid = d["money_received"], d["money_paid"]
        if received > 0 and not paid:
            return RECEIVED_TEXT, money(received)
        if paid > 0 and not received:
            return PAID_TEXT, money(paid)
        if received > 0 and paid > 0:                     # legacy data only: show the net, by its sign
            net = received - paid
            return (RECEIVED_TEXT if net >= 0 else PAID_TEXT), money(abs(net))
        return NO_MONEY_TEXT, ""

    @staticmethod
    def _weights(lines):
        """Presentation totals of the saved line weights: pieces, grams, and grams per karat."""
        by_karat = {}
        for line in lines:
            by_karat[line["karat"]] = by_karat.get(line["karat"], Decimal("0.000")) + line["weight"]
        total = sum(by_karat.values(), Decimal("0.000"))
        return {"count": str(len(lines)), "total": grams(total),
                "karats": [{"karat": str(k), "weight": grams(v)} for k, v in sorted(by_karat.items(), reverse=True)]}

    @Property("QVariantMap", notify=changed)
    def soldSummary(self):
        """Shop-sold pieces only (sold stock, manual and from old-gold stock); never the customer's old gold."""
        return self._weights(self._detail.get("sale_lines", [])) if self._detail else {}

    @Property("QVariantMap", notify=changed)
    def oldGoldSummary(self):
        """The customer's old gold (كسر) bought by the shop: its own totals."""
        lines = self._detail.get("old_gold_lines", []) if self._detail else []
        if not lines:
            return {}
        summary = self._weights(lines)
        values = [line["line_total"] for line in lines]
        summary["value"] = UNAVAILABLE if any(v is None for v in values) else money(sum(values, Decimal("0.00")))
        return summary

    @Property("QVariantList", notify=changed)
    def exchanges(self):
        """This invoice's exchanges, inline (exchange-specific facts only; the invoice is not repeated)."""
        rows = []
        for x in self._detail.get("exchanges", []) if self._detail else []:
            def pieces(items):
                return [{"item": p.get("item_name") or "—", "karat": str(p["karat"]), "weight": grams(p["weight_grams"]),
                         "source": SOURCE_TEXT.get(p.get("source") or "", "")} for p in items]
            same = x["valuation_basis"] == "SAME_KARAT"
            making = ""
            if x["making_refund_applies"]:
                making = f"استرداد مصنعية {money(x['making_refund'])}"
            elif Decimal(x["making_charge"]) > 0:
                making = f"مصنعية الذهب الزائد {money(x['making_charge'])}"
            legs = [f"{METHOD_TEXT.get(p['method_code'], p['method_code'])}"
                    + (f" — {p['account_name']}" if p.get("account_name") else "")
                    + f": {money(p['amount'])}" for p in x["payments"]]
            # Real money title from the exchange's SAVED money (received / paid); the account part stays apart.
            moved_title, _ = self.money_direction({"money_received": Decimal(x["money_received"]),
                                                   "money_paid": Decimal(x["money_paid"])})
            account = Decimal(x["account_signed"])
            account_text = ("" if not account else
                            f"حساب العميل: {'على العميل' if account > 0 else 'للعميل'} {money(abs(account))}")
            delta = Decimal(x["valued_delta_grams"])
            rows.append({
                "code": x["code"], "time": datetime_text(datetime.fromisoformat(x["occurred_at"])),
                "actor": x["actor"] or "—", "returned": pieces(x["returned"]), "replacements": pieces(x["replacements"]),
                "deltaText": ("فرق الوزن " if same else "فرق الذهب الصافي ")
                             + number(delta, max(3, len(x['valued_delta_grams'].split('.')[-1])), True),
                "priceText": "" if not x["price_basis"] else
                f"{'سعر بيع المحل' if x['price_basis'] == 'SHOP_SELL' else 'سعر شراء المحل'} عيار {x['price_karat']}"
                f" {money(x['price_per_gram'])}",
                "goldText": "" if delta == 0 else f"قيمة فرق الذهب {money(x['gold_amount'])}",
                "makingText": making, "direction": x["direction"], "directionText": x["direction_text"],
                "amount": "" if x["direction"] == "NONE" else money(x["amount"]),
                "moneyTitle": moved_title, "money": " • ".join(legs), "accountText": account_text,
                "pendingText": x["returned_pending_text"]})
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
            rows.append({"method": self._method_label(p), "direction": p["direction"], "amount": money(p["amount"])})
        return rows

    @staticmethod
    def _method_label(p):
        label = METHOD_TEXT.get(p["method_code"], p.get("method_name") or "")
        return label + (" — " + p["account_name"] if p.get("account_name") else "")

    @Property("QVariantList", notify=changed)
    def priceSnapshots(self):
        """The historical quotes of the karats USED on this invoice only (its sold pieces and its customer
        old gold), not every karat that happened to be snapshotted."""
        if not self._detail:
            return []
        used = {int(line["karat"]) for line in self._detail.get("sale_lines", []) + self._detail.get("old_gold_lines", [])}
        return [{"karat": str(p["karat"]),
                 "sell": UNAVAILABLE if p["sell"] is None else f"{p['sell']:,.2f} ج",
                 "buy": UNAVAILABLE if p["buy"] is None else f"{p['buy']:,.2f} ج",
                 "source": p.get("source") or ""}
                for p in self._detail.get("price_snapshots", []) if int(p["karat"]) in used]

    @staticmethod
    def _event_amount(event):
        if event.amount is None:
            return ""
        if event.event_type == "EXCHANGED":
            # The exchange's signed difference: + على العميل, − للعميل, 0 لا يوجد فرق.
            if event.amount == 0:
                return "لا يوجد فرق"
            return f"{'على العميل' if event.amount > 0 else 'للعميل'} {money(abs(event.amount))}"
        return money(event.amount)

    @Property("QVariantList", notify=changed)
    def timeline(self):
        return [{"eventType": e.event_type, "text": EVENT_TEXT.get(e.event_type, e.event_type),
                 "time": datetime_text(e.occurred_at), "actor": e.actor or "—",
                 "amount": self._event_amount(e), "linked": e.linked_record_id or "",
                 "isExchange": e.event_type == "EXCHANGED" and bool(e.linked_record_id),
                 "code": f"EX{int(e.linked_record_id):06d}" if e.event_type == "EXCHANGED"
                 and str(e.linked_record_id or "").isdigit() else ""}
                for e in (self._detail.get("timeline", []) if self._detail else [])]

    @Slot(str)
    def refreshInvoice(self, invoice_id):
        """After an exchange: reload the open detail of that invoice (its timeline changed)."""
        if self._detail and self._detail.get("invoice_id") == invoice_id:
            self.openInvoice(invoice_id)

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
