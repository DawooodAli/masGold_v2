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
             "makingTotal", "lineTotal")
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
        self._preview_error = ""
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

    @Slot()
    def activate(self):
        """Load salespeople and the price board (called when the page opens)."""
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
        if self._salesperson_id and self._salesperson_id not in {s["employeeId"] for s in self._salespeople}:
            self._salesperson_id = ""

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
        self._salesperson_id = employee_id or ""
        self._touched()

    @Slot(str)
    def setCustomer(self, customer_id):
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
        self._sales.append({**result, "making_mode": "PER_GRAM", "making_value": ""})
        self._touched()

    @Slot(int)
    def removeSaleLine(self, index):
        if 0 <= index < len(self._sales):
            del self._sales[index]
            self._touched()

    @Slot(int, str, str)
    def setSaleMaking(self, index, mode, value):
        if 0 <= index < len(self._sales) and mode in ("PER_GRAM", "TOTAL"):
            self._sales[index]["making_mode"], self._sales[index]["making_value"] = mode, (value or "").strip()
            self._touched()

    @Slot(int, str)
    def addOldGold(self, karat_id, weight):
        weight = (weight or "").strip()
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
        sales = tuple(SaleLineInput(line["inventory_item_id"],
                                    making_per_gram=line["making_value"] or None
                                    if line["making_mode"] == "PER_GRAM" else None,
                                    making_total=line["making_value"] or None
                                    if line["making_mode"] == "TOTAL" else None)
                      for line in self._sales)
        olds = tuple(OldGoldLineInput(line["karat_id"], line["weight"],
                                      None if line["discount_type"] == "NONE" else line["discount_type"],
                                      None if line["discount_type"] == "NONE" else (line["discount_value"] or None))
                     for line in self._olds)
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
        salespeople, board = self._salespeople, self._board
        self._timer.stop()
        self._reset_state()
        self._salespeople, self._board = salespeople, board
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
                         "makingMode": line["making_mode"], "makingValue": line["making_value"],
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

    @Property(str, notify=changed)
    def cashAmount(self):
        return self._cash

    @Property(str, notify=changed)
    def walletAmount(self):
        return self._wallet

    @Property(bool, notify=changed)
    def previewReady(self):
        return bool(self._preview) and self._preview_version == self._version

    @Property("QVariantMap", notify=changed)
    def summary(self):
        p = self._preview if self.previewReady else {}
        if not p:
            return {"saleTotal": "", "oldGoldTotal": "", "netText": "", "netDirection": "", "receivedText": "",
                    "paidText": "", "residualText": "", "residualDirection": "", "statusText": ""}
        residual = p["residual_direction"]
        return {"saleTotal": money(p["sale_total"]), "oldGoldTotal": money(p["old_gold_total"]),
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
