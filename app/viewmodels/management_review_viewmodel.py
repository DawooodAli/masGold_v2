"""مراجعات المدير • N: one sidebar indicator and one review page for all review types."""
from decimal import Decimal

from PySide6.QtCore import QObject, Property, QTimer, Signal, Slot

from app.formatting import datetime_text, ltr, number
from app.workers.invoice_worker import InvoiceWorker


TYPE_TEXT = {"INVENTORY_MATCH": "مراجعة مخزون", "MAKING_PRICE": "مراجعة تسعير / مصنعية",
             "CASH_SETTLEMENT": "مراجعة تسوية نقدية", "OLD_GOLD_DISCOUNT": "تعديل نسبة خصم الكسر",
             "RETURNED_ITEM_INSPECTION": "فحص قطع مرتجعة"}
STATUS_TEXT = {"RETURNED_PENDING": "بانتظار الفحص", "IN_STOCK": "في المخزون", "SOLD": "مباعة",
               "CONVERTED_TO_OLD_GOLD": "محولة إلى الكسر"}
RESOLVED_TEXT = {"RETURN_TO_STOCK": "أعيدت القطعة إلى المخزون بنفس الباركود المسجل: {barcode}",
                 "CONVERT_TO_OLD_GOLD": "تم تحويل القطعة إلى رصيد الكسر (الباركود المسجل {barcode} لم يعد قابلاً للبيع)."}
ACCOUNT_TYPE_TEXT = {"CASH": "نقدية", "BANK": "بنك / InstaPay", "MOBILE_WALLET": "محفظة"}
MAKING_STATUS_TEXT = {"WITHIN_RANGE": "داخل النطاق", "OUT_OF_RANGE": "خارج النطاق",
                      "NOT_CONFIGURED": "غير محدد النطاق"}
REASON_TEXT = {"NO_BARCODE": "لم يُنشأ لها باركود", "BARCODE_LOST": "الباركود مفقود أو تالف",
               "NOT_FOUND": "لم أجد القطعة في البحث", "OTHER": "سبب آخر"}


def _time(value):
    return datetime_text(value)


def _num(value, places=2):
    if value in (None, ""):
        return "—"
    return f"{Decimal(str(value)):,.{places}f}"


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


class ManagementReviewViewModel(QObject):
    changed = Signal()

    def __init__(self, session, service=None, parent=None, refresh_ms=60000, balance_service=None):
        super().__init__(parent)
        self._session, self._service = session, service
        self._workers, self._request, self._latest = set(), 0, {}
        self._busy, self._shutdown = set(), False
        self._counts = {key: 0 for key in TYPE_TEXT}
        self._reviews, self._error, self._accounts = {}, "", []
        self._balance_service = balance_service
        self._resolving, self._inspection_message, self._inspection_error = "", "", ""
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
        self.refresh()

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
        for worker in list(self._workers):
            worker.wait(5000)

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
            rows.append({"title": f"فاتورة {r.get('invoice_number') or ''}", "time": _time(r.get("sold_at")),
                         "details": details, "reason": REASON_TEXT.get(r.get("reason_code"), r.get("reason") or ""),
                         "notes": r.get("notes") or "", "salesperson": r.get("salesperson_name") or "",
                         "customer": r.get("customer_name_snapshot") or "بدون عميل"})
        return rows

    def _generic(self, code):
        return [{"title": r.get("summary") or "", "time": _time(r.get("created_at")),
                 "by": r.get("created_by_name") or "", "invoice": r.get("invoice_number") or "",
                 "details": review_details(code, r.get("details_json"))}
                for r in self._reviews.get(code, [])]

    # ---- returned-item inspection (manager decision; the service re-checks everything)
    @Property("QVariantList", notify=changed)
    def inspections(self):
        rows = []
        for r in self._reviews.get("RETURNED_ITEM_INSPECTION", []):
            weight = r.get("item_weight_grams") if r.get("item_weight_grams") is not None else r.get("weight_grams")
            barcode = r.get("stored_barcode") or ""
            rows.append({
                "reviewId": str(r["review_id"]), "exchange": r.get("exchange_code") or "",
                "invoice": r.get("invoice_number") or "", "customer": r.get("customer_name_snapshot") or "بدون عميل",
                "time": _time(r.get("exchanged_at") or r.get("created_at")),
                "item": r.get("item_type_name") or r.get("item_name_snapshot") or "—",
                "category": r.get("category_name") or "",
                "karat": str(r.get("karat_value") or ""), "weight": f"{Decimal(str(weight)):.3f}" if weight is not None else "—",
                "factory": r.get("factory_name") or "", "size": r.get("size") or "", "notes": r.get("item_notes") or "",
                "barcode": barcode, "barcodeText": f"الباركود المسجل: {barcode or 'غير مسجل'}",
                "status": r.get("inventory_status") or "",
                "statusText": STATUS_TEXT.get(r.get("inventory_status"), r.get("inventory_status") or ""),
                "busy": self._resolving == str(r["review_id"])})
        return rows

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
            self._inspection_message = RESOLVED_TEXT[result["resolution"]].format(
                barcode=result.get("stored_barcode") or "—")
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
