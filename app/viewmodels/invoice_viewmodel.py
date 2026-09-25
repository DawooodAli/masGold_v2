"""Billing V2 screen state. The draft lives here (in memory only); every total
shown is recalculated by InvoiceService and formatted in Python. QML only
displays strings and forwards employee input."""
from decimal import Decimal, InvalidOperation

from PySide6.QtCore import QAbstractListModel, QByteArray, QModelIndex, QObject, Property, QTimer, Qt, Signal, Slot

from app.workers.invoice_worker import InvoiceWorker


NET_TEXT = {"CUSTOMER_PAYS_SHOP": "على العميل", "SHOP_PAYS_CUSTOMER": "للعميل", "SETTLED": "متعادلة"}
RESIDUAL_TEXT = {"CUSTOMER_OWES_SHOP": "متبقي على العميل", "SHOP_OWES_CUSTOMER": "متبقي للعميل",
                 "NONE": "مسددة بالكامل"}
STATUS_TEXT = {"SETTLED": "مسددة", "PARTIAL": "مسددة جزئياً", "UNSETTLED": "غير مسددة"}
# Salesman wording: what the customer must pay / receive and what stays on the account.
DUE_TEXT = {"CUSTOMER_PAYS_SHOP": "المطلوب من العميل", "SHOP_PAYS_CUSTOMER": "المستحق للعميل",
            "SETTLED": "الفاتورة متعادلة"}
REMAINING_TEXT = {"CUSTOMER_OWES_SHOP": "متبقي على العميل (يُسجل على حسابه)",
                  "SHOP_OWES_CUSTOMER": "متبقي للعميل (يُسجل لحسابه)", "NONE": "لا يوجد متبقٍ"}
WEIGHT_KARATS = ("18", "21", "24")
PRICE_STATUS_TEXT = {"FRESH": "", "STALE_ALLOWED": "السعر قديم", "STALE": "السعر قديم"}


def money(value):
    if value in (None, ""):
        return ""
    return f"{Decimal(str(value)):,.2f} ج"


def price(value):
    return "" if value in (None, "") else f"{Decimal(str(value)):,.2f}"


def grams(value):
    return "" if value in (None, "") else f"{Decimal(str(value)):.3f} جم"


def balance_text(value):
    if value in (None, ""):
        return ""
    amount = Decimal(str(value))
    if amount > 0:
        return f"على العميل {money(amount)}"
    if amount < 0:
        return f"للعميل {money(-amount)}"
    return "لا يوجد رصيد"


class LinesModel(QAbstractListModel):
    """Rows for a QML Repeater. Value updates emit dataChanged so delegates (and
    a text field being typed in) survive; only adding/removing rows resets."""

    countChanged = Signal()

    def __init__(self, keys, parent=None):
        super().__init__(parent)
        self._keys = tuple(keys)
        self._roles = {Qt.UserRole + i: QByteArray(key.encode()) for i, key in enumerate(self._keys)}
        self._rows = []

    def roleNames(self):
        return self._roles

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self._rows)

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid() or not 0 <= index.row() < len(self._rows) or role not in self._roles:
            return None
        return self._rows[index.row()].get(self._keys[role - Qt.UserRole], "")

    @Property(int, notify=countChanged)
    def count(self):
        return len(self._rows)

    def rows(self):
        return [dict(row) for row in self._rows]

    def set_rows(self, rows):
        rows = [dict(row) for row in rows]
        if len(rows) != len(self._rows):
            self.beginResetModel()
            self._rows = rows
            self.endResetModel()
            self.countChanged.emit()
            return
        for row, (old, new) in enumerate(zip(self._rows, rows)):
            if old != new:
                self._rows[row] = new
                self.dataChanged.emit(self.index(row), self.index(row))


SALE_KEYS = ("barcode", "itemName", "karat", "weight", "makingMode", "makingValue", "goldPrice",
             "makingTotal", "lineTotal", "factory", "makingOverride")
OLD_GOLD_KEYS = ("karat", "weight", "discountType", "discountValue", "goldPrice", "grossTotal",
                 "discountAmount", "lineTotal")


class InvoiceViewModel(QObject):
    changed = Signal()
    finalized = Signal(str)

    def __init__(self, session, service=None, master_data=None, parent=None, preview_delay_ms=250):
        super().__init__(parent)
        # Services are built lazily so constructing the view-model never touches the database.
        self._session = session
        self._service = service
        self._master_data = master_data
        self._workers = set()
        self._request = 0
        self._latest = {}
        self._shutdown = False
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(preview_delay_ms)
        self._timer.timeout.connect(self._start_preview)
        self._price_source, self._seen_price_key, self._activated = None, None, False
        self._sale_model = LinesModel(SALE_KEYS, self)
        self._old_gold_model = LinesModel(OLD_GOLD_KEYS, self)
        self.changed.connect(self._sync_models)
        self._reset_state(keep_reference=False)

    # ------------------------------------------------------------ state

    def _reset_state(self, keep_reference=True):
        if not keep_reference:
            self._salespeople, self._board = [], {}
        self._salesperson_id, self._customer_id = "", ""
        self._sales, self._olds = [], []
        self._cash, self._wallet = "", ""
        # Invoice-level entry: one making per gram and one old-gold discount % for the
        # whole invoice; a sale line may still override its own making.
        self._invoice_making, self._old_gold_discount = "", ""
        self._old_gold_karat_id = -1                  # explicit choice required for every line
        self._version, self._preview, self._preview_version = 0, {}, -1
        self._preview_error, self._error = "", ""
        self._busy_ops, self._success = set(), {}

    def _service_instance(self):
        if self._service is None:
            from services.invoice_service import InvoiceService
            self._service = InvoiceService(self._session)
        return self._service

    def _master(self):
        if self._master_data is None:
            from services.master_data_service import MasterDataService
            self._master_data = MasterDataService()
        return self._master_data

    def _touched(self):
        self._version += 1
        self._preview_error, self._success = "", {}
        self.changed.emit()
        if self._sales or self._olds:
            self._timer.start()
        else:
            self._preview, self._preview_version = {}, self._version

    # ------------------------------------------------------------ workers

    def _start(self, operation, call):
        if self._shutdown:
            return
        actor = self._session.current_actor()
        if actor is None:
            return
        self._request += 1
        self._latest[operation] = self._request
        self._busy_ops.add(operation)
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
            return                                   # a newer request of this kind is pending
        self._busy_ops.discard(operation)
        handler = getattr(self, f"_on_{operation}")
        handler(result, error, code, kind)
        self.changed.emit()

    @Slot()
    def shutdown(self):
        self._shutdown = True
        self._timer.stop()
        for worker in list(self._workers):
            worker.wait(5000)

    # ------------------------------------------------------------ reference data

    def attach_price_source(self, source):
        """Keep the Billing price bar in step with the global Gold Price view-model.

        Only the displayed board is reloaded; finalize still revalidates the
        authoritative price token inside InvoiceService.
        """
        self._price_source = source
        source.changed.connect(self._global_prices_changed)

    @Slot()
    def _global_prices_changed(self):
        source = self._price_source
        key = (source.priceToken, source.status)
        if not key[0] or key == self._seen_price_key:
            return
        self._seen_price_key = key
        board_key = (self._board.get("price_token"), self._board.get("price_status"))
        if self._activated and board_key != key:
            self.refreshPrices()

    @Slot()
    def activate(self):
        """Load salespeople and the price board (called when the page opens)."""
        self._activated = True
        master = self._master()
        self._start("salespeople", lambda _expected_actor: master.get_employees())
        self.refreshPrices()

    @Slot()
    def refreshPrices(self):
        service = self._service_instance()
        self._start("prices", lambda _expected_actor: service.price_board(_expected_actor=_expected_actor))

    def _on_salespeople(self, result, error, code, kind):
        if error:
            self._error = error
            return
        self._salespeople = [{"employeeId": str(r["id"]), "name": r["name"]} for r in result or []]
        known = {s["employeeId"] for s in self._salespeople}
        if self._salesperson_id and self._salesperson_id not in known:
            self._salesperson_id = ""
        # Default to the logged-in user's own employee record; it stays visible and changeable.
        actor = self._session.current_actor()
        own = str(actor.employee_id) if actor is not None and actor.employee_id is not None else ""
        if not self._salesperson_id and own in known:
            self._salesperson_id = own

    def _on_prices(self, result, error, code, kind):
        if error:
            self._board, self._error = {}, error
            return
        self._board = result or {}
        if self._sales or self._olds:
            self._timer.start()

    # ------------------------------------------------------------ draft editing

    @Slot(str)
    def setSalesperson(self, employee_id):
        if (employee_id or "") == self._salesperson_id:
            return                                    # no change: keep the preview and success banner
        self._salesperson_id = employee_id or ""
        self._touched()

    @Slot(str)
    def setCustomer(self, customer_id):
        if (customer_id or "") == self._customer_id:
            return                                    # e.g. the picker cleared after a finalized invoice
        self._customer_id = customer_id or ""
        self._touched()

    @Slot(str)
    def addBarcode(self, barcode):
        barcode = (barcode or "").strip()
        if not barcode:
            return
        self._error = ""
        service = self._service_instance()
        self._start("barcode", lambda _expected_actor: service.find_item_by_barcode(
            barcode, _expected_actor=_expected_actor))

    def _on_barcode(self, result, error, code, kind):
        if error:
            self._error = error
            return
        if any(line["inventory_item_id"] == result["inventory_item_id"] for line in self._sales):
            self._error = "القطعة مضافة بالفعل إلى الفاتورة."
            return
        self._sales.append({**result, "making_mode": "PER_GRAM", "making_value": "", "making_override": False})
        self._touched()

    @Slot(int)
    def removeSaleLine(self, index):
        if 0 <= index < len(self._sales):
            del self._sales[index]
            self._touched()

    @Slot(int, str, str)
    def setSaleMaking(self, index, mode, value):
        if 0 <= index < len(self._sales) and mode in ("PER_GRAM", "TOTAL"):
            value = (value or "").strip()
            line = self._sales[index]
            # An empty override returns the line to the invoice making.
            line["making_mode"], line["making_value"], line["making_override"] = mode, value, bool(value)
            self._touched()

    @Slot(str)
    def setInvoiceMaking(self, value):
        self._invoice_making = (value or "").strip()
        self._touched()

    @Slot(str)
    def setOldGoldDiscountPercent(self, value):
        self._old_gold_discount = (value or "").strip()
        self._touched()

    @Slot()
    def payFullAmount(self):
        """Fill the cash field with the amount still due (after any wallet amount)."""
        if not self.previewReady or self._preview["net_direction"] == "SETTLED":
            return
        try:
            wallet = Decimal(self._wallet) if self._wallet else Decimal("0")
        except InvalidOperation:
            wallet = Decimal("0")
        amount = Decimal(str(self._preview["net_amount"])) - wallet
        self._cash = f"{max(amount, Decimal('0')):.2f}"
        self._touched()

    @Slot(int)
    def setOldGoldKarat(self, karat_id):
        known = {int(p["karat_id"]) for p in self._board.get("prices", [])}
        self._old_gold_karat_id = karat_id if karat_id in known else -1
        self.changed.emit()

    @Slot(int, str)
    def addOldGold(self, karat_id, weight):
        weight = (weight or "").strip()
        if karat_id < 0:
            karat_id = self._old_gold_karat_id
        karat = next((p for p in self._board.get("prices", []) if int(p["karat_id"]) == karat_id), None)
        if karat is None:
            self._error = "اختر عيار الكسر."
            self.changed.emit()
            return
        if not weight:
            self._error = "أدخل وزن الكسر."
            self.changed.emit()
            return
        self._error = ""
        self._olds.append({"karat_id": karat_id, "karat": karat["karat"], "weight": weight,
                           "discount_type": "NONE", "discount_value": ""})
        self._old_gold_karat_id = -1                  # the next line needs its own explicit karat
        self._touched()

    @Slot(int)
    def removeOldGoldLine(self, index):
        if 0 <= index < len(self._olds):
            del self._olds[index]
            self._touched()

    @Slot(int, str, str)
    def setOldGoldDiscount(self, index, discount_type, value):
        if 0 <= index < len(self._olds) and discount_type in ("NONE", "PERCENT", "AMOUNT"):
            self._olds[index]["discount_type"] = discount_type
            self._olds[index]["discount_value"] = "" if discount_type == "NONE" else (value or "").strip()
            self._touched()

    @Slot(str)
    def setCashAmount(self, amount):
        self._cash = (amount or "").strip()
        self._touched()

    @Slot(str)
    def setWalletAmount(self, amount):
        self._wallet = (amount or "").strip()
        self._touched()

    # ------------------------------------------------------------ preview / finalize

    def _draft(self):
        from services.invoice_service import InvoiceDraft, OldGoldLineInput, PaymentInput, SaleLineInput
        sales = []
        for line in self._sales:
            mode, value = ((line["making_mode"], line["making_value"]) if line.get("making_override")
                           else ("PER_GRAM", self._invoice_making))
            sales.append(SaleLineInput(line["inventory_item_id"],
                                       making_per_gram=(value or None) if mode == "PER_GRAM" else None,
                                       making_total=(value or None) if mode == "TOTAL" else None))
        olds = []
        for line in self._olds:
            kind, value = line["discount_type"], line["discount_value"]
            if kind == "NONE" and self._old_gold_discount and self._nonzero(self._old_gold_discount):
                kind, value = "PERCENT", self._old_gold_discount
            olds.append(OldGoldLineInput(line["karat_id"], line["weight"], None if kind == "NONE" else kind,
                                         None if kind == "NONE" else (value or None)))
        sales, olds = tuple(sales), tuple(olds)
        payments = tuple(PaymentInput(code, amount) for code, amount in
                         (("PHYSICAL_CASH", self._cash), ("MOBILE_WALLET", self._wallet))
                         if amount and self._nonzero(amount))
        salesperson = int(self._salesperson_id) if self._salesperson_id.isdigit() else None
        return InvoiceDraft(salesperson, self._customer_id or None, sales, olds, payments)

    @staticmethod
    def _nonzero(amount):
        try:
            return Decimal(amount) != 0
        except InvalidOperation:
            return True                               # let the calculator report the bad value

    def _start_preview(self):
        if not (self._sales or self._olds) or not self._salesperson_id:
            if not self._salesperson_id and (self._sales or self._olds):
                self._preview_error = "اختر البائع."
                self.changed.emit()
            return
        draft, version = self._draft(), self._version
        service = self._service_instance()
        self._pending_preview_version = version
        self._start("preview", lambda _expected_actor: (version, service.preview(
            draft, _expected_actor=_expected_actor)))

    def _on_preview(self, result, error, code, kind):
        if error:
            self._preview, self._preview_version, self._preview_error = {}, -1, error
            return
        version, preview = result
        if version != self._version:
            return
        self._preview, self._preview_version, self._preview_error = preview, version, ""

    @Slot(bool, bool)
    def finalizeInvoice(self, stale_acknowledged, dead_cash_confirmed):
        if not self.canFinalize:
            return
        draft, token = self._draft(), self._preview["price_token"]
        service = self._service_instance()
        self._error = ""
        self._start("finalize", lambda _expected_actor: service.finalize(
            draft, token, stale_acknowledged=bool(stale_acknowledged),
            dead_cash_confirmed=bool(dead_cash_confirmed), _expected_actor=_expected_actor))

    def _on_finalize(self, result, error, code, kind):
        if error:
            self._error = error
            if kind in ("PriceChanged", "PriceUnavailable"):
                self.refreshPrices()
            self._preview_version = -1
            self._timer.start()
            return
        success = {
            "invoiceNumber": result["invoice_number"], "finalizedAt": result["finalized_at"],
            "saleTotal": money(result["sale_total"]), "oldGoldTotal": money(result["old_gold_total"]),
            "netText": f"{NET_TEXT[result['net_direction']]} {money(result['net_amount'])}",
            "receivedText": money(result["money_received"]), "paidText": money(result["money_paid"]),
            "residualText": f"{RESIDUAL_TEXT[result['residual_direction']]}"
                            + ("" if result["residual_direction"] == "NONE" else f" {money(result['residual_amount'])}"),
            "statusText": STATUS_TEXT.get(result["settlement_status"], result["settlement_status"]),
        }
        # The draft is discarded; reference data, the salesperson and the success summary stay.
        salespeople, board, salesperson = self._salespeople, self._board, self._salesperson_id
        self._timer.stop()
        self._reset_state()
        self._salespeople, self._board, self._salesperson_id = salespeople, board, salesperson
        self._success = success
        self.finalized.emit(result["invoice_number"])
        self.refreshPrices()

    @Slot()
    def newInvoice(self):
        # The salesperson stays for the next customer, as after a finalized invoice.
        salespeople, board, salesperson = self._salespeople, self._board, self._salesperson_id
        self._timer.stop()
        self._reset_state()
        self._salespeople, self._board, self._salesperson_id = salespeople, board, salesperson
        self.changed.emit()

    @Slot()
    def dismissSuccess(self):
        self._success = {}
        self.changed.emit()

    @Slot()
    def clearError(self):
        self._error = ""
        self.changed.emit()

    # ------------------------------------------------------------ properties

    @Property(bool, notify=changed)
    def busy(self):
        return bool(self._busy_ops)

    @Property(bool, notify=changed)
    def finalizing(self):
        return "finalize" in self._busy_ops

    @Property(str, notify=changed)
    def errorMessage(self):
        return self._error

    @Property(str, notify=changed)
    def previewError(self):
        return self._preview_error

    @Property("QVariantList", notify=changed)
    def salespeople(self):
        return self._salespeople

    @Property(str, notify=changed)
    def salespersonId(self):
        return self._salesperson_id

    @Property(str, notify=changed)
    def customerId(self):
        return self._customer_id

    @Property("QVariantList", notify=changed)
    def priceRows(self):
        return [{"karatId": int(p["karat_id"]), "karat": str(p["karat"]), "buy": price(p["shop_buy"]),
                 "sell": price(p["shop_sell"])} for p in self._board.get("prices", [])]

    @Property(str, notify=changed)
    def priceStatusText(self):
        if not self._board:
            return "الأسعار غير متاحة"
        return PRICE_STATUS_TEXT.get(self._board.get("price_status"), self._board.get("price_status", ""))

    @Property(bool, notify=changed)
    def priceStale(self):
        return bool(self._board.get("requires_price_acknowledgement"))

    def _preview_line(self, key, index):
        lines = self._preview.get(key, []) if self._preview_version == self._version else []
        return lines[index] if index < len(lines) else {}

    @Slot()
    def _sync_models(self):
        self._sale_model.set_rows(self._sale_rows())
        self._old_gold_model.set_rows(self._old_gold_rows())

    @Property(QObject, constant=True)
    def saleLinesModel(self):
        return self._sale_model

    @Property(QObject, constant=True)
    def oldGoldLinesModel(self):
        return self._old_gold_model

    def _sale_rows(self):
        rows = []
        for index, line in enumerate(self._sales):
            calc = self._preview_line("sale_lines", index)
            rows.append({"barcode": line.get("barcode") or "", "itemName": line.get("item_name") or "",
                         "karat": str(line.get("karat") or ""), "weight": grams(line["weight_grams"]),
                         "makingMode": line["making_mode"],
                         "makingValue": line["making_value"] if line.get("making_override") else "",
                         "makingOverride": bool(line.get("making_override")),
                         "factory": line.get("factory_name") or "",
                         "goldPrice": price(calc.get("gold_price_per_gram")),
                         "makingTotal": money(calc.get("making_total")),
                         "lineTotal": money(calc.get("line_total"))})
        return rows

    def _old_gold_rows(self):
        rows = []
        for index, line in enumerate(self._olds):
            calc = self._preview_line("old_gold_lines", index)
            rows.append({"karat": str(line["karat"]), "weight": line["weight"],
                         "discountType": line["discount_type"], "discountValue": line["discount_value"],
                         "goldPrice": price(calc.get("gold_price_per_gram")),
                         "grossTotal": money(calc.get("gross_total")),
                         "discountAmount": money(calc.get("discount_amount")),
                         "lineTotal": money(calc.get("line_total"))})
        return rows

    @Property(int, notify=changed)
    def saleCount(self):
        return len(self._sales)

    @Property(int, notify=changed)
    def oldGoldCount(self):
        return len(self._olds)

    @Property(int, notify=changed)
    def oldGoldKaratId(self):
        return self._old_gold_karat_id

    @Property(str, notify=changed)
    def cashAmount(self):
        return self._cash

    @Property(str, notify=changed)
    def walletAmount(self):
        return self._wallet

    @Property(str, notify=changed)
    def invoiceMaking(self):
        return self._invoice_making

    @Property(str, notify=changed)
    def oldGoldDiscountPercent(self):
        return self._old_gold_discount

    @Property("QVariantMap", notify=changed)
    def weightTotals(self):
        """Display-only grams per karat (the prototype's مباع / مشترى strip)."""
        def totals(lines, weight_key):
            sums = {karat: Decimal("0") for karat in WEIGHT_KARATS}
            for line in lines:
                karat = str(line.get("karat") or "")
                try:
                    weight = Decimal(str(line.get(weight_key) or "0"))
                except InvalidOperation:
                    continue
                if karat in sums:
                    sums[karat] += weight
            return {karat: (f"{value:.3f}" if value else "—") for karat, value in sums.items()}
        return {"sold": totals(self._sales, "weight_grams"), "bought": totals(self._olds, "weight")}

    @Property(bool, notify=changed)
    def previewReady(self):
        return bool(self._preview) and self._preview_version == self._version

    @Property("QVariantMap", notify=changed)
    def summary(self):
        p = self._preview if self.previewReady else {}
        if not p:
            return {"saleTotal": "", "oldGoldTotal": "", "netText": "", "netDirection": "", "receivedText": "",
                    "paidText": "", "residualText": "", "residualDirection": "", "statusText": "",
                    "dueLabel": "صافي الفاتورة", "dueAmount": "", "paidLabel": "المدفوع", "paidAmount": "",
                    "remainingLabel": "المتبقي", "remainingAmount": ""}
        residual = p["residual_direction"]
        pays_customer = p["net_direction"] == "SHOP_PAYS_CUSTOMER"
        return {"dueLabel": DUE_TEXT[p["net_direction"]],
                "dueAmount": "" if p["net_direction"] == "SETTLED" else money(p["net_amount"]),
                "paidLabel": "المدفوع للعميل" if pays_customer else "المدفوع من العميل",
                "paidAmount": money(p["money_paid"] if pays_customer else p["money_received"]),
                "remainingLabel": REMAINING_TEXT[residual],
                "remainingAmount": "" if residual == "NONE" else money(p["residual_amount"]),
                "saleTotal": money(p["sale_total"]), "oldGoldTotal": money(p["old_gold_total"]),
                "netText": f"{NET_TEXT[p['net_direction']]} {money(p['net_amount'])}",
                "netDirection": p["net_direction"],
                "receivedText": money(p["money_received"]), "paidText": money(p["money_paid"]),
                "residualText": RESIDUAL_TEXT[residual] + ("" if residual == "NONE" else f" {money(p['residual_amount'])}"),
                "residualDirection": residual,
                "statusText": STATUS_TEXT.get(p["settlement_status"], p["settlement_status"])}

    @Property(str, notify=changed)
    def paymentLabel(self):
        direction = self._preview.get("net_direction") if self.previewReady else None
        return "مبلغ مدفوع للعميل" if direction == "SHOP_PAYS_CUSTOMER" else "مبلغ مستلم من العميل"

    @Property(str, notify=changed)
    def customerBalanceText(self):
        return balance_text(self._preview.get("customer_balance_before")) if self.previewReady else ""

    @Property(str, notify=changed)
    def customerBalanceAfterText(self):
        return balance_text(self._preview.get("customer_balance_after")) if self.previewReady else ""

    @Property(bool, notify=changed)
    def deadCashWarning(self):
        return bool(self.previewReady and self._preview.get("dead_cash_warning_active"))

    @Property(bool, notify=changed)
    def requiresDeadCashConfirmation(self):
        return bool(self.previewReady and self._preview.get("requires_dead_cash_confirmation"))

    @Property(bool, notify=changed)
    def requiresPriceAcknowledgement(self):
        return bool(self.previewReady and self._preview.get("requires_price_acknowledgement"))

    @Property("QStringList", notify=changed)
    def blockingProblems(self):
        return [p["message"] for p in self._preview.get("blocking_problems", [])] if self.previewReady else []

    @Property(bool, notify=changed)
    def canFinalize(self):
        return (self.previewReady and not self.busy and not self.blockingProblems
                and bool(self._salesperson_id) and bool(self._sales or self._olds))

    @Property("QVariantMap", notify=changed)
    def success(self):
        return self._success

    @Property(bool, notify=changed)
    def hasSuccess(self):
        return bool(self._success)
