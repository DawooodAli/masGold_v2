"""مراجعات المدير • N: one sidebar indicator and one review page for all review types."""
from decimal import Decimal

from PySide6.QtCore import QObject, Property, QTimer, Signal, Slot

from app.formatting import datetime_text, ltr, number
from app.viewmodels.account_history_viewmodel import AccountHistoryViewModel
from app.workers.invoice_worker import InvoiceWorker


TYPE_TEXT = {"INVENTORY_MATCH": "مراجعة مخزون", "MAKING_PRICE": "مراجعة تسعير / مصنعية",
             "CASH_SETTLEMENT": "مراجعة تسوية نقدية", "OLD_GOLD_DISCOUNT": "تعديل نسبة خصم الكسر",
             "RETURNED_ITEM_INSPECTION": "قطع مرتجعة بانتظار قرار المدير", "RETURN_APPROVAL": "موافقة مرتجع"}
STATUS_TEXT = {"RETURNED_PENDING": "بانتظار الفحص", "IN_STOCK": "في المخزون", "SOLD": "مباعة",
               "CONVERTED_TO_OLD_GOLD": "محولة إلى الكسر"}
RESOLVED_TEXT = {"RETURN_TO_STOCK": "أعيدت القطعة إلى المخزون بنفس الباركود المسجل: {barcode}",
                 "CONVERT_TO_OLD_GOLD": "تم تحويل القطعة إلى رصيد الكسر (الباركود المسجل {barcode} لم يعد قابلاً للبيع)."}
# An Exchange piece already in Scrap (032): the decision moves it out of Scrap, or keeps it there.
HELD_RESOLVED_TEXT = {"RETURN_TO_STOCK": "تمت إعادة القطعة من الكسر إلى المخزون بنفس الباركود: {barcode}",
                      "CONVERT_TO_OLD_GOLD": "تم تثبيت القطعة داخل الكسر (الباركود {barcode} أصبح تاريخياً)."}
# The summary / navigation cards at the top of the page: only categories backed by a real review state
# (a count from ManagementReviewService.counts), plus the financial accounts section. A card FILTERS the
# page to its category — it never approves or rejects anything. ACCOUNTS has no pending count.
CARD_ORDER = ("MAKING_PRICE", "RETURNED_ITEM_INSPECTION", "RETURN_APPROVAL", "INVENTORY_MATCH",
              "CASH_SETTLEMENT", "OLD_GOLD_DISCOUNT")
CARD_TITLE = {"MAKING_PRICE": "تسعير / مصنعية", "RETURNED_ITEM_INSPECTION": "قطع مرتجعة بانتظار القرار",
              "RETURN_APPROVAL": "موافقة مرتجع", "INVENTORY_MATCH": "مراجعة مخزون",
              "CASH_SETTLEMENT": "تسوية نقدية", "OLD_GOLD_DISCOUNT": "خصم الكسر",
              "ACCOUNTS": "الحسابات المالية", "GOLD_POSITION": "موضع الذهب"}
GOLD_POSITION_PERMISSIONS = ("MANAGEMENT_REVIEW", "MARKET_TRIP_MANAGE", "INTERMEDIARY_HOLDING_VIEW")
ACCOUNT_TYPE_TEXT = {"CASH": "نقدية", "BANK": "بنك / InstaPay", "MOBILE_WALLET": "محفظة"}
MAKING_STATUS_TEXT = {"WITHIN_RANGE": "داخل النطاق", "OUT_OF_RANGE": "خارج النطاق",
                      "NOT_CONFIGURED": "غير محدد النطاق"}
REASON_TEXT = {"NO_BARCODE": "لم يُنشأ لها باركود", "BARCODE_LOST": "الباركود مفقود أو تالف",
               "NOT_FOUND": "لم أجد القطعة في البحث", "OTHER": "سبب آخر"}


def _id(value):
    """An immutable BIGINT id crossing into QML as a decimal string ('' when absent)."""
    return "" if value in (None, "") else str(value)


def _time(value):
    return datetime_text(value)


def _num(value, places=2):
    if value in (None, ""):
        return "—"
    return f"{Decimal(str(value)):,.{places}f}"


def _age_text(days):
    """«بعد 4 أيام من الفاتورة» (Arabic count agreement for the Return's age)."""
    if days is None:
        return ""
    days = int(days)
    word = "يوم" if days in (0, 1) else "يومين" if days == 2 else "أيام" if 3 <= days <= 10 else "يوماً"
    return f"بعد {days} {word} من الفاتورة" if days != 2 else "بعد يومين من الفاتورة"


def _signed_money(value):
    return number(value, 2, signed=True)


def _signed_effect(value):
    """Effect on the invoice net in shop words (negative: the customer received more)."""
    amount = Decimal(str(value or "0"))
    if amount < 0:
        return f"لصالح العميل {_num(-amount)} ج"
    if amount > 0:
        return f"لصالح المحل {_num(amount)} ج"
    return "بدون أثر"


def review_details(code, details):
    """One or two readable lines of a review's stored details (manager only)."""
    details = details or {}
    if code == "OLD_GOLD_DISCOUNT":
        lines = details.get("lines") or []
        actual = " / ".join(ltr(f"{_num(line.get('discount_value'))}%") if line.get("discount_type") == "PERCENT"
                            else ("بدون خصم" if line.get("discount_type") in (None, "NONE")
                                  else f"مبلغ {_num(line.get('discount_value'))} ج") for line in lines)
        gross = sum((Decimal(str(line.get("gross_total") or "0")) for line in lines), Decimal("0"))
        return (f"الافتراضي {ltr(_num(details.get('default_percent')) + '%')} ← المستخدم {actual} • "
                f"قيمة الكسر {_num(gross)} ج • صافي الكسر {_num(details.get('old_gold_total'))} ج • "
                f"الأثر على الصافي: {_signed_effect(details.get('effect_on_net_signed'))}"
                + (f" • البائع {details['salesperson_name']}" if details.get("salesperson_name") else ""))
    if code == "MAKING_PRICE":
        groups = " • ".join(f"فئة {g.get('category_code') or 'بدون فئة'}: {_num(g.get('average_making_per_gram'))}/جرام "
                            f"({MAKING_STATUS_TEXT.get(g.get('status'), g.get('status') or '')})"
                            for g in details.get("groups") or [])
        head = (f"سعر البيع الفعلي {_num(details.get('effective_sale_total'))} ج"
                if details.get("effective_sale_total") else "")
        return " • ".join(v for v in (head, groups, details.get("salesperson_name") or "") if v)
    if code == "CASH_SETTLEMENT":
        parts = [f"فرق {_num(abs(Decimal(str(details.get('adjustment_signed') or '0'))))} ج"]
        if details.get("reason"):
            parts.append(f"السبب: {details['reason']}")
        return " • ".join(parts)
    return ""


def _g(value):
    return f"{Decimal(str(value)):,.3f}"


def gold_position_view(position):
    """Presentation strings of the consolidated gold position (the service owns every figure; QML only shows these)."""
    s, shop, custody, inter = position["summary"], position["shop"], position["custody"], position["intermediary"]
    unknown = Decimal(str(s["unknown_grams"]))
    summary = [
        {"key": "shop", "label": "في المحل", "grams": _g(shop["grams"]), "eq21": _g(s["shop_eq21"])},
        {"key": "custody", "label": "في عهدة السوق", "grams": _g(custody["grams"]), "eq21": _g(s["custody_eq21"])},
        {"key": "intermediary", "label": "لدى المسوقجي", "grams": _g(inter["grams"]), "eq21": _g(s["intermediary_eq21"])},
    ]
    book = position.get("book") or {"trips": [], "eq21": Decimal(0)}
    if book["trips"]:                                                           # 056: BOOK balances, counted once, the physical grams are not specified
        summary.append({"key": "book", "label": "رصيد BOOK بعد احتساب التسويات المعتمدة", "grams": "غير محدد", "eq21": _g(s["book_eq21"])})
    shop_lines = []
    parts = shop["parts"]
    for name, label in (("scrap", "كسر"), ("lots", "سبائك في المحل"), ("inventory", "مخزون (قطع جاهزة)"),
                        ("returned", "مرتجع بانتظار الفحص"), ("receipts", "استلام فعلي قديم (عيار غير معلوم)")):
        part = parts[name]
        if Decimal(str(part["grams"])) != 0:
            shop_lines.append({"label": label, "grams": _g(part["grams"]),
                               "eq21": "غير محسوب" if name == "receipts" else _g(part["eq21"])})
    for item in shop.get("assayed") or []:                                       # 056: assayed gold returned from a BOOK trip: physical grams + its stored posted amount
        shop_lines.append({"label": f"ذهب مرتجع بعيار فعلي — {item['code']}",
                           "text": f"العيار الفعلي {Decimal(str(item['fineness'])):.1f}‰ • القيمة التحليلية {Decimal(str(item['analytical'])):,.6f} • "
                                   f"فرق التقريب (للعرض) {ltr(format(Decimal(str(item['rounding'])), '+,.6f'))}",
                           "grams": _g(item["grams"]), "eq21": _g(item["eq21"])})
    book_lines = [{"label": t["code"], "text": f"الرصيد المرحَّل {_g(t['eq21'])} • التحليلي {Decimal(str(t['analytical'])):,.6f} • "
                                                f"فرق التقريب (للعرض) {ltr(format(Decimal(str(t['drift'])), '+,.6f'))} • الوزن الفعلي غير محدد",
                   "eq21": _g(t["eq21"])} for t in book["trips"]]
    custody_lines = []
    for trip in custody["trips"]:
        bits = [f"{'عيار %d %s' % (g['karat'], g['designation_text'])}: {_g(g['grams'])} جرام" for g in trip["gold"]]
        bits += [f"سبيكة {l['code']}: {_g(l['grams'])} جرام" for l in trip["lots"]]
        custody_lines.append({"label": trip["code"], "text": " • ".join(bits),
                              "eq21": _g(sum((Decimal(str(g["eq21"])) for g in trip["gold"]), Decimal(0))
                                         + sum((Decimal(str(l["eq21"] or 0)) for l in trip["lots"]), Decimal(0)))})
    holder_lines = []
    for party in inter["parties"]:
        bits = [f"لوجو عيار {l['karat']}: {_g(l['grams'])} جرام" for l in party["logo"]]
        bits += [f"سبيكة {_g(l['grams'])} جرام" + (f" (عيار فعلي {Decimal(str(l['fineness'])):.1f}‰)" if l["fineness"] is not None else "")
                 for l in party["lots"]]
        marketer = party.get("marketer")                                       # the marketer account (055): 21K physical grams, a source of its own
        if marketer:
            bits.append(f"عهدة المسوقجي (عيار 21): {_g(marketer['grams'])} جرام")
        eq = (sum((Decimal(str(l["eq21"])) for l in party["logo"]), Decimal(0)) + sum((Decimal(str(l["eq21"] or 0)) for l in party["lots"]), Decimal(0))
              + (Decimal(str(marketer["eq21"])) if marketer else Decimal(0)))
        holder_lines.append({"label": party["party_name"], "text": " • ".join(bits), "eq21": _g(eq)})
    return {"summary": summary, "total": _g(s["total_eq21"]), "totalGrams": _g(s["total_grams"]),
            "unknown": _g(unknown), "hasUnknown": unknown != 0,
            "shop": shop_lines, "custody": custody_lines, "intermediary": holder_lines, "book": book_lines,
            "warnings": [(f"رصيد ذهب المسوقجي {w['party_name']} سالب ({_g(w['grams'])}) ولا يُضم إلى الإجمالي" if w["code"] == "MARKETER_GOLD_NEGATIVE"
                          else (f"بند المخزون المرتجع {w['item']} له قيمة مرحَّلة بلا وزن ({_g(w['posted'])}) ولا يُضم إلى الإجمالي" if w["code"] == "STOCK_POSTED_WITHOUT_GRAMS"
                                else f"السبيكة {w['lot_id']} ظاهرة في أكثر من مكان: {' / '.join(w['places'])}")) for w in position["warnings"]]}


class ManagementReviewViewModel(QObject):
    changed = Signal()
    inspectionResolved = Signal()
    invoiceRequested = Signal(str)          # exact invoice id → the existing Invoice History detail

    def __init__(self, session, service=None, parent=None, refresh_ms=60000, balance_service=None):
        super().__init__(parent)
        self._session, self._service = session, service
        self._workers, self._request, self._latest = set(), 0, {}
        self._busy, self._shutdown = set(), False
        self._counts = {key: 0 for key in TYPE_TEXT}
        self._reviews, self._error, self._accounts = {}, "", []
        self._balance_service = balance_service
        self._resolving, self._inspection_message, self._inspection_error = "", "", ""
        self._selected_category = ""
        self._position, self._position_error, self._gold_service = None, "", None
        self._history = AccountHistoryViewModel(session, parent=self)
        self._history.invoiceRequested.connect(self.invoiceRequested)
        self._timer = QTimer(self)
        self._timer.setInterval(refresh_ms)
        self._timer.timeout.connect(self.refresh)

    def _service_instance(self):
        if self._service is None:
            from services.management_review_service import ManagementReviewService
            self._service = ManagementReviewService(self._session)
        return self._service

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
    def activate(self):
        """Start the indicator (called once the MainWindow is shown)."""
        self.refresh()
        self._timer.start()

    @Slot()
    def refresh(self):
        service = self._service_instance()
        self._start("counts", lambda _expected_actor: service.counts(_expected_actor=_expected_actor))

    def _on_counts(self, result, error):
        if not error:
            self._counts = dict(result)

    @Slot()
    def openReviews(self):
        service = self._service_instance()
        self._error = ""
        self._start("reviews", lambda _expected_actor: service.open_reviews(_expected_actor=_expected_actor))
        balances = self._balances()
        self._start("accounts", lambda _expected_actor: balances.financial_accounts(_expected_actor=_expected_actor))
        self.loadGoldPosition()
        self.refresh()

    # ---- موضع الذهب: a read-only consolidated view (never netted against supplier balances)
    def _gold(self):
        if self._gold_service is None:
            from services.gold_position_service import GoldPositionService
            self._gold_service = GoldPositionService(self._session)
        return self._gold_service

    @Property(bool, notify=changed)
    def canViewGoldPosition(self):
        actor = self._session.current_actor()
        return bool(actor and set(GOLD_POSITION_PERMISSIONS) <= set(actor.permissions))

    @Slot()
    def loadGoldPosition(self):
        if not self.canViewGoldPosition:
            self._position, self._position_error = None, ""
            return
        service = self._gold()
        self._start("gold_position", lambda _expected_actor: service.position(_expected_actor=_expected_actor))

    def _on_gold_position(self, result, error):
        self._position, self._position_error = (None, error) if error else (result, "")

    @Property(bool, notify=changed)
    def goldPositionBusy(self):
        return "gold_position" in self._busy

    @Property(str, notify=changed)
    def goldPositionError(self):
        return self._position_error

    @Property("QVariantMap", notify=changed)
    def goldPosition(self):
        return gold_position_view(self._position) if self._position else {}

    @Slot()
    @Slot(str)
    def reloadAccounts(self, _reason=""):
        """Called after every finalized invoice: wallet / InstaPay / bank balances just changed."""
        balances = self._balances()
        self._start("accounts", lambda _expected_actor: balances.financial_accounts(_expected_actor=_expected_actor))

    def _on_reviews(self, result, error):
        self._reviews, self._error = ({}, error) if error else (result, "")

    def _balances(self):
        if getattr(self, "_balance_service", None) is None:
            from services.operational_balance_service import OperationalBalanceService
            self._balance_service = OperationalBalanceService(self._session)
        return self._balance_service

    def _on_accounts(self, result, error):
        self._accounts = [] if error else list(result or [])

    # ---- controlled setup of real accounts (Manager / Owner; the service re-checks everything)
    def _setup(self, call, done_text):
        balances = self._balances()
        self._account_message, self._account_error = "", ""
        self._done_text = done_text
        self._start("account_setup", lambda _expected_actor: call(balances, _expected_actor))
        self.changed.emit()

    @Slot(str, str, str, str)
    def createAccount(self, name, account_type, opening_balance, bank_name):
        self._setup(lambda s, actor: s.create_account(name, account_type, opening_balance, bank_name,
                                                      _expected_actor=actor), "تم إنشاء الحساب.")

    @Slot(str, bool)
    def setAccountActive(self, account_id, active):
        self._setup(lambda s, actor: s.set_account_active(account_id, active, _expected_actor=actor),
                    "تم تفعيل الحساب." if active else "تم إيقاف الحساب.")

    @Slot(str, str, str)
    def initializeOpeningBalance(self, ledger, account_id, amount):
        self._setup(lambda s, actor: s.initialize_opening_balance(ledger, account_id, amount,
                                                                  _expected_actor=actor), "تم تسجيل الرصيد الافتتاحي.")

    def _on_account_setup(self, result, error):
        self._account_message, self._account_error = ("", error) if error else (self._done_text, "")
        if not error:
            balances = self._balances()
            self._start("accounts", lambda _expected_actor: balances.financial_accounts(_expected_actor=_expected_actor))

    @Property(bool, notify=changed)
    def canSetupAccounts(self):
        actor = self._session.current_actor()
        return bool(actor and {"MANAGEMENT_REVIEW", "BANK_MANAGE"} <= set(actor.permissions))

    @Property(str, notify=changed)
    def accountMessage(self):
        return getattr(self, "_account_error", "") or getattr(self, "_account_message", "")

    @Property(bool, notify=changed)
    def accountMessageIsError(self):
        return bool(getattr(self, "_account_error", ""))

    @Property(bool, notify=changed)
    def accountBusy(self):
        return "account_setup" in self._busy

    @Slot()
    def shutdown(self):
        self._shutdown = True
        self._timer.stop()
        self._history.shutdown()
        for worker in list(self._workers):
            worker.wait(5000)

    # ---- dashboard cards: navigation / filter only (never an approve / reject gate)
    @Property("QVariantList", notify=changed)
    def dashboardCards(self):
        cards = [{"code": code, "title": CARD_TITLE[code], "count": self._counts.get(code, 0),
                  "pending": self._counts.get(code, 0) > 0, "selected": self._selected_category == code}
                 for code in CARD_ORDER]
        cards.append({"code": "ACCOUNTS", "title": CARD_TITLE["ACCOUNTS"], "count": len(self._accounts),
                      "pending": False, "selected": self._selected_category == "ACCOUNTS"})
        if self.canViewGoldPosition:
            cards.append({"code": "GOLD_POSITION", "title": CARD_TITLE["GOLD_POSITION"], "count": 0, "pending": False,
                          "selected": self._selected_category == "GOLD_POSITION"})
        return cards

    @Property(str, notify=changed)
    def selectedCategory(self):
        return self._selected_category

    @Slot(str)
    def selectCategory(self, code):
        """Click a card: show only that category; click it again (or «الكل») to show everything."""
        valid = code in CARD_TITLE
        self._selected_category = "" if (not valid or code == self._selected_category) else code
        self.changed.emit()

    @Slot(str, result=bool)
    def showsCategory(self, code):
        return not self._selected_category or self._selected_category == code

    # ---- source deep links (immutable invoice id; the existing Invoice History detail shows the
    # invoice, its pricing review context and every Exchange / Return / correction of it)
    @Slot(str)
    def openSource(self, invoice_id):
        if str(invoice_id or "").isdigit():
            self.invoiceRequested.emit(str(invoice_id))

    @Property(QObject, constant=True)
    def history(self):
        return self._history

    @Property(int, notify=changed)
    def totalCount(self):
        return sum(self._counts.values())

    @Property("QVariantList", notify=changed)
    def categories(self):
        return [{"code": code, "title": text, "count": self._counts.get(code, 0)} for code, text in TYPE_TEXT.items()]

    @Property(bool, notify=changed)
    def canOpen(self):
        actor = self._session.current_actor()
        return bool(actor and ({"MANAGEMENT_REVIEW", "INVENTORY_RECONCILE"} & set(actor.permissions)))

    @Property(str, notify=changed)
    def errorMessage(self):
        return self._error

    @Property(bool, notify=changed)
    def busy(self):
        return "reviews" in self._busy

    @Property("QVariantList", notify=changed)
    def inventoryCases(self):
        rows = []
        for r in self._reviews.get("INVENTORY_MATCH", []):
            weight = r.get("observed_weight_grams")
            details = " • ".join(v for v in (
                r.get("item_type_name") or "", f"عيار {r['karat_value']}" if r.get("karat_value") else "",
                f"{Decimal(str(weight)):.3f} جرام" if weight is not None else "", r.get("factory_name") or "",
                f"مقاس {r['observed_size']}" if r.get("observed_size") else "") if v)
            rows.append({"invoiceId": _id(r.get("invoice_id")),
                         "title": f"فاتورة {r.get('invoice_number') or ''}", "time": _time(r.get("sold_at")),
                         "details": details, "reason": REASON_TEXT.get(r.get("reason_code"), r.get("reason") or ""),
                         "notes": r.get("notes") or "", "salesperson": r.get("salesperson_name") or "",
                         "customer": r.get("customer_name_snapshot") or "بدون عميل"})
        return rows

    def _generic(self, code):
        return [{"invoiceId": _id(r.get("invoice_id")),
                 "title": r.get("summary") or "", "time": _time(r.get("created_at")),
                 "by": r.get("created_by_name") or "", "invoice": r.get("invoice_number") or "",
                 "details": review_details(code, r.get("details_json"))}
                for r in self._reviews.get(code, [])]

    # ---- returned-item inspection (manager decision; the service re-checks everything)
    @Property("QVariantList", notify=changed)
    def inspections(self):
        """Returned pieces waiting for inspection outside Scrap (Return V1 / Exchanges before 032)."""
        return [r for r in self._inspection_rows() if not r["inScrap"]]

    @Property("QVariantList", notify=changed)
    def scrapPieces(self):
        """Exchange pieces already in Scrap (032), held for the manager's decision."""
        return [r for r in self._inspection_rows() if r["inScrap"]]

    def _inspection_rows(self):
        rows = []
        for r in self._reviews.get("RETURNED_ITEM_INSPECTION", []):
            weight = r.get("item_weight_grams") if r.get("item_weight_grams") is not None else r.get("weight_grams")
            barcode = r.get("stored_barcode") or ""
            rows.append({
                "invoiceId": _id(r.get("invoice_id")),
                "reviewId": str(r["review_id"]), "exchange": r.get("exchange_code") or "",
                # «استبدال EX000014» or «مرتجع RT000001»: the real source of the returned piece.
                "sourceTitle": r.get("source_label") or ("استبدال " + (r.get("exchange_code") or "")),
                "invoice": r.get("invoice_number") or "", "customer": r.get("customer_name_snapshot") or "بدون عميل",
                "time": _time(r.get("exchanged_at") or r.get("created_at")),
                "item": r.get("item_type_name") or r.get("item_name_snapshot") or "—",
                "category": r.get("category_name") or "",
                "karat": str(r.get("karat_value") or ""), "weight": f"{Decimal(str(weight)):.3f}" if weight is not None else "—",
                "factory": r.get("factory_name") or "", "size": r.get("size") or "", "notes": r.get("item_notes") or "",
                "barcode": barcode, "barcodeText": f"الباركود المسجل: {barcode or 'غير مسجل'}",
                "status": r.get("inventory_status") or "",
                "statusText": STATUS_TEXT.get(r.get("inventory_status"), r.get("inventory_status") or ""),
                "busy": self._resolving == str(r["review_id"]),
                # 032: an Exchange piece already in Scrap (held for this decision).
                "inScrap": bool(r.get("hold_id")), "itemId": str(r.get("inventory_item_id") or ""),
                "isReturn": r.get("source") == "RETURN",
                "scrapDestination": r.get("scrap_destination") or "",
                "scrapState": r.get("scrap_state_text") or "",
                "scrapEnteredAt": _time(r.get("scrap_entered_at")) if r.get("scrap_entered_at") else "",
                "karatPhysical": _num(r.get("karat_physical_grams"), 3),
                "karatAvailable": _num(r.get("karat_available_grams"), 3),
                "karatHeld": _num(r.get("karat_held_grams"), 3),
                "sourceLine": str(r.get("source_invoice_line_id") or ""),
                "timeline": [{"title": e["title"], "detail": e.get("detail") or "", "by": e.get("by") or "",
                              "at": _time(e["at"]) if e.get("at") else ""} for e in r.get("timeline", [])]})
        return rows

    # ---- مرتجع بعد 72 ساعة: موافقة / رفض of the exact selected goods (no reason required)
    @Property("QVariantList", notify=changed)
    def returnApprovals(self):
        rows = []
        for r in self._reviews.get("RETURN_APPROVAL", []):
            d = r.get("details_json") or {}
            lines = " • ".join(f"{l.get('item_name') or 'من الكسر'} عيار {l.get('karat')} — "
                               f"{Decimal(str(l.get('weight_grams'))):.3f} جرام" for l in d.get("lines", []))
            rows.append({"invoiceId": _id(r.get("invoice_id")),
                         "reviewId": str(r["review_id"]), "code": d.get("return_code") or "",
                         "invoice": r.get("invoice_number") or "", "customer": r.get("customer_name_snapshot") or "بدون عميل",
                         "invoiceDate": _time(r.get("source_finalized_at")),
                         "age": _age_text(d.get("age_days")),
                         "deadline": "آخر موعد: " + ltr(str(d.get("deadline_at") or "").replace("T", " ")[:16]),
                         "lines": lines, "estimate": f"القيمة التقديرية {_num(r.get('gold_amount'))} ج",
                         "making": "رد المصنعية: 0 ج", "notes": d.get("notes") or "",
                         "by": r.get("created_by_name") or "", "time": _time(r.get("created_at")),
                         "busy": self._resolving == str(r["review_id"])})
        return rows

    def _decide(self, review_id, approve):
        if getattr(self, "_resolving", ""):
            return
        service = self._service_instance()
        call = service.approve_return if approve else service.reject_return
        self._resolving, self._inspection_message, self._inspection_error = str(review_id), "", ""
        self._start("decide", lambda _expected_actor: call(str(review_id), _expected_actor=_expected_actor))
        self.changed.emit()

    @Slot(str)
    def approveReturn(self, review_id):
        self._decide(review_id, True)

    @Slot(str)
    def rejectReturn(self, review_id):
        self._decide(review_id, False)

    def _on_decide(self, result, error):
        self._resolving = ""
        if error:
            self._inspection_message, self._inspection_error = "", error
        else:
            self._inspection_error = ""
            self._inspection_message = (f"تمت الموافقة على المرتجع {result['code']}." if result["status"] == "APPROVED"
                                        else f"تم رفض المرتجع {result['code']}.")
        self.openReviews()

    @Property(bool, notify=changed)
    def canResolveInspections(self):
        actor = self._session.current_actor()
        return bool(actor and "MANAGEMENT_REVIEW" in actor.permissions)

    def _resolve(self, review_id, outcome):
        if getattr(self, "_resolving", ""):
            return
        service = self._service_instance()
        call = service.return_to_stock if outcome == "RETURN_TO_STOCK" else service.convert_to_old_gold
        self._resolving, self._inspection_message, self._inspection_error = str(review_id), "", ""
        self._start("resolve", lambda _expected_actor: call(str(review_id), _expected_actor=_expected_actor))
        self.changed.emit()

    def _barcodes(self):
        if getattr(self, "_barcode_service", None) is None:
            from services.barcode_print_service import BarcodePrintService
            self._barcode_service = BarcodePrintService()
        return self._barcode_service

    @Slot(str)
    def reprintBarcode(self, item_id):
        """إعادة طباعة الباركود: the SAME stored barcode of this piece (never a new one)."""
        if not item_id or "reprint" in self._busy:
            return
        barcodes = self._barcodes()
        self._inspection_message, self._inspection_error = "", ""
        self._start("reprint", lambda _expected_actor: barcodes.reprint_item_barcode(item_id))
        self.changed.emit()

    def _on_reprint(self, result, error):
        if error:
            self._inspection_message, self._inspection_error = "", f"تعذرت إعادة طباعة الباركود: {error}"
        else:
            self._inspection_error = ""
            self._inspection_message = (f"تمت إعادة طباعة الباركود {result['barcode_value']} "
                                        f"للقطعة #{result['inventory_item_id']}.")

    @Slot(str)
    def returnToStock(self, review_id):
        self._resolve(review_id, "RETURN_TO_STOCK")

    @Slot(str)
    def convertToOldGold(self, review_id):
        self._resolve(review_id, "CONVERT_TO_OLD_GOLD")

    def _on_resolve(self, result, error):
        self._resolving = ""
        if error:
            self._inspection_message, self._inspection_error = "", error
        else:
            self._inspection_error = ""
            self._inspection_message = (HELD_RESOLVED_TEXT if result.get("hold_status") else RESOLVED_TEXT)[
                result["resolution"]].format(
                barcode=result.get("stored_barcode") or "—")
            self.inspectionResolved.emit()
        self.openReviews()

    @Property(str, notify=changed)
    def inspectionMessage(self):
        return self._inspection_error or self._inspection_message

    @Property(bool, notify=changed)
    def inspectionMessageIsError(self):
        return bool(self._inspection_error)

    @Property(bool, notify=changed)
    def inspectionBusy(self):
        return bool(self._resolving)

    @Property("QVariantList", notify=changed)
    def makingReviews(self):
        return self._generic("MAKING_PRICE")

    @Property("QVariantList", notify=changed)
    def settlementReviews(self):
        return self._generic("CASH_SETTLEMENT")

    @Property("QVariantList", notify=changed)
    def discountReviews(self):
        return self._generic("OLD_GOLD_DISCOUNT")

    @Property("QVariantList", notify=changed)
    def financialAccounts(self):
        """Cash, wallet, InstaPay / bank accounts: balance, money in / out, latest movements."""
        rows = []
        for a in self._accounts:
            recent = " • ".join(f"{_time(m.get('occurred_at'))} {_signed_money(m.get('amount_delta'))}"
                                f"{' — ' + m['reference_code'] if m.get('reference_code') else ''}"
                                for m in a.get("recent", []))
            rows.append({"accountId": str(a["account_id"]), "ledger": a["ledger"],
                         "accountKey": f"{a['ledger']}:{int(a['account_id'])}",
                         "active": bool(a.get("is_active", 1)),
                         "canInitialize": bool(a.get("can_initialize")), "canManage": bool(a.get("can_manage")),
                         "title": f"{a['name']} — {ACCOUNT_TYPE_TEXT.get(a['account_type'], a['account_type'])}"
                                  + ("" if a.get("is_active", 1) else " (غير نشط)"),
                         # The summary row: name, type, state and balance only (no inline movements).
                         "name": a["name"],
                         "typeText": ACCOUNT_TYPE_TEXT.get(a["account_type"], a["account_type"]),
                         "stateText": "نشط" if a.get("is_active", 1) else "غير نشط",
                         "balance": f"{_num(a['balance'])} ج",
                         # Kept for a later drill-down view; not shown in the summary row.
                         "details": f"وارد {_num(a['money_in'])} ج • منصرف {_num(a['money_out'])} ج • "
                                    f"{int(a['movement_count'])} حركة",
                         "recent": recent or "لا توجد حركات"})
        return rows
