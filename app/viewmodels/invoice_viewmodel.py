"""Billing V2 screen state. The draft lives here (in memory only); every total
shown is recalculated by InvoiceService and formatted in Python. QML only
displays strings and forwards employee input.

Shop workflow: the salesman adds pieces by barcode, F3 search, F5 manual entry
or F7 sale from old-gold stock, weighs the customer's old gold, and types only
the real money (one or more payment legs in one direction) and, if any, the
amount left on the customer account. The service derives the sale price from
that money (effective sale = O + P_in − P_out + L), then making and its average.

The salesman sees the average making per gram, never the making total, and
the money direction in words (المستلم من العميل / المدفوع للعميل), never a sign."""
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
REMAINING_TEXT = {"CUSTOMER_OWES_SHOP": "على حساب العميل (عليه)",
                  "SHOP_OWES_CUSTOMER": "لحساب العميل (له)", "NONE": "لا يوجد على الحساب"}
WEIGHT_KARATS = ("18", "21", "24")
PRICE_STATUS_TEXT = {"FRESH": "", "STALE_ALLOWED": "السعر قديم", "STALE": "السعر قديم"}
# Why the exact inventory piece could not be identified (a piece found through F3 is a normal sale).
REASONS = (("NO_BARCODE", "لم يُنشأ لها باركود"), ("BARCODE_LOST", "الباركود مفقود أو تالف"),
           ("NOT_FOUND", "لم أجد القطعة في البحث"), ("OTHER", "سبب آخر"))
UNIDENTIFIED, OLD_GOLD_STOCK = "UNIDENTIFIED", "OLD_GOLD_STOCK"
# UX mirror of the service's hard rule (the service validates again on preview and finalize).
DISCOUNT_MIN, DISCOUNT_MAX = Decimal("1.25"), Decimal("3.00")
DISCOUNT_WARNING = "النسبة المسموح بها من \u20661.25%\u2069 إلى \u20663%\u2069"
PAYMENT_TEXT = {"PHYSICAL_CASH": "نقدي", "MOBILE_WALLET": "محفظة", "INSTAPAY": "InstaPay",
                "BANK_TRANSFER": "تحويل بنكي"}
ACCOUNT_TYPE_FOR_METHOD = {"MOBILE_WALLET": "MOBILE_WALLET", "INSTAPAY": "BANK", "BANK_TRANSFER": "BANK"}
NO_ACCOUNT_TEXT = {"MOBILE_WALLET": "لا توجد محفظة مفعلة", "BANK": "لا يوجد حساب بنكي مفعّل"}
DIRECTION_TEXT = {"IN": "المستلم من العميل", "OUT": "المدفوع للعميل"}
# The salesman's simple making warning; the manager sees the category details.
MAKING_NOTICE = {"OUT_OF_RANGE": "السعر يحتاج مراجعة", "NOT_CONFIGURED": "نطاق المصنعية غير محدد"}


def money(value):
    if value in (None, ""):
        return ""
    return f"{Decimal(str(value)):,.2f} ج"


def price(value):
    return "" if value in (None, "") else f"{Decimal(str(value)):,.2f}"


def short_price(value):
    """Compact price: 6,218 (or 6,218.50 when not whole)."""
    if value in (None, ""):
        return ""
    amount = Decimal(str(value))
    return f"{amount:,.0f}" if amount == amount.to_integral_value() else f"{amount:,.2f}"


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


def account_text(value):
    """Customer account status in shop words: عليه / له / الحساب مسدد."""
    if value in (None, ""):
        return ""
    amount = Decimal(str(value))
    if amount > 0:
        return f"عليه {amount:,.2f} ج"
    if amount < 0:
        return f"له {-amount:,.2f} ج"
    return "الحساب مسدد"


def _weight(value):
    number = _decimal(value) if value not in (None, "") else None
    return "" if number is None else f"{number:.3f}"


def _decimal(text):
    try:
        value = Decimal(str(text).strip())
        return value if value.is_finite() else None
    except (InvalidOperation, ValueError):
        return None


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


SALE_KEYS = ("kind", "barcode", "itemName", "karat", "weight", "factory", "size", "notes", "lineTotal",
             "identifiedBy", "tag")
LEG_KEYS = ("legIndex", "method", "account", "amount", "needsAccount", "accounts", "unavailableText")
OLD_GOLD_KEYS = ("karat", "weight", "discountType", "discountValue", "goldPrice", "grossTotal",
                 "discountAmount", "lineTotal")


class InvoiceViewModel(QObject):
    changed = Signal()
    finalized = Signal(str)
    # حفظ وطباعة without a usable Bill printer: Main opens Settings → الطباعة.
    printingSettingsRequested = Signal()

    def __init__(self, session, service=None, master_data=None, print_service=None, parent=None,
                preview_delay_ms=250):
        super().__init__(parent)
        # Services are built lazily so constructing the view-model never touches the database.
        self._session = session
        self._service = service
        self._master_data = master_data
        self._print_service = print_service
        # Printing is a separate step after finalize: it never affects whether the invoice was saved.
        self._pending_print, self._print_result, self._last_invoice_id = False, {}, ""
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
        # Payment legs update in place, so a leg's amount field keeps focus while typing.
        self._leg_model = LinesModel(LEG_KEYS, self)
        self.changed.connect(self._sync_models)
        self._reference = {"item_types": [], "factories": [], "old_gold_stock": [], "settings": {}}
        self._reset_state(keep_reference=False)

    # ------------------------------------------------------------ state

    def _reset_state(self, keep_reference=True):
        if not keep_reference:
            self._salespeople, self._board = [], {}
        self._salesperson_id, self._customer_id, self._customer = "", "", {}
        self._new_customer = {"name": "", "mobile": "", "place": ""}
        self._customer_suggestions = []
        self._sales, self._described, self._olds = [], [], []
        # Real money: payment legs in the invoice's one direction.
        self._legs = [{"method": "PHYSICAL_CASH", "account": "", "amount": ""}]
        self._direction_choice, self._account_amount = "", ""
        # Customer account = the deferred part of the commercial money (P + L); its direction
        # follows the money direction. With several legs the commercial amount is held here while
        # the legs must add up to what is required now.
        self._account_direction = ""
        self._commercial = None
        # إجمالي الفاتورة (with sold pieces): the ONE authoritative commercial total the employee
        # types. Payment legs and the customer-account part only allocate it. While the single
        # first leg was never edited by hand it mirrors the total (the fast cash sale).
        self._invoice_total, self._leg0_auto, self._payment_warning = "", True, ""
        self._discount_warning = ""
        default_discount = self._reference["settings"].get("old_gold_discount_default_percent") or "1.50"
        self._old_gold_discount = self._trim(default_discount)
        self._settlement_choice, self._settlement_reason, self._approver = "", "", None
        self._approval_error = ""
        # بحث عن قطعة: cascading الصنف → العيار → المصنع; candidates are only shown.
        self._facets = {"item_types": [], "karats": [], "factories": []}
        self._search_type, self._search_karat, self._search_factory = 0, 0, 0
        self._search_results, self._search_error, self._search_truncated = [], "", False
        self._search_ran, self._barcode_not_found = False, ""
        self._old_gold_karat_id = -1                  # explicit choice required for every line
        self._version, self._preview, self._preview_version = 0, {}, -1
        self._preview_error, self._error = "", ""
        self._busy_ops, self._success = set(), {}

    @staticmethod
    def _trim(value):
        text = str(value)
        return text.rstrip("0").rstrip(".") if "." in text else text

    def _service_instance(self):
        if self._service is None:
            from services.invoice_service import InvoiceService
            self._service = InvoiceService(self._session)
        return self._service

    def _print_service_instance(self):
        if self._print_service is None:
            from services.invoice_print_service import InvoicePrintService
            self._print_service = InvoicePrintService(self._session)
        return self._print_service

    def _master(self):
        if self._master_data is None:
            from services.master_data_service import MasterDataService
            self._master_data = MasterDataService()
        return self._master_data

    def _has_lines(self):
        return bool(self._sales or self._described or self._olds)

    def _touched(self):
        self._version += 1
        self._preview_error, self._success = "", {}
        self.changed.emit()
        if self._has_lines():
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
        """Keep the Billing price set in step with the global Gold Price view-model.

        Prices are shown in the MainWindow top bar only; Billing keeps the board
        for karats and the price token. Finalize still revalidates the token."""
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
        """Load salespeople, prices and reference lists (called when the page opens)."""
        self._activated = True
        master = self._master()
        self._start("salespeople", lambda _expected_actor: master.get_employees())
        self.refreshPrices()
        self.refreshReference()

    @Slot()
    def refreshPrices(self):
        service = self._service_instance()
        self._start("prices", lambda _expected_actor: service.price_board(_expected_actor=_expected_actor))

    @Slot()
    def refreshReference(self):
        service = self._service_instance()
        if hasattr(service, "reference_lists"):
            self._start("reference", lambda _expected_actor: service.reference_lists(_expected_actor=_expected_actor))

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
        if self._has_lines():
            self._timer.start()

    def _on_reference(self, result, error, code, kind):
        if error:
            return
        first = not self._reference["settings"]
        self._reference = result or self._reference
        known = {row["id"] for row in self._reference.get("bank_accounts", [])}
        for leg in self._legs:
            if leg["account"] not in known:
                leg["account"] = ""
        if first and not self._has_lines():
            default = self._reference["settings"].get("old_gold_discount_default_percent")
            if default:
                self._old_gold_discount = self._trim(default)

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
        if not self._customer_id:
            self._customer = {}
        self._touched()

    # ---- inline customer: name or phone, suggestions, or a new customer
    @Slot(str)
    def searchCustomers(self, text):
        text = (text or "").strip()
        if len(text) < 2:
            self._customer_suggestions = []
            self.changed.emit()
            return
        service = self._service_instance()
        self._start("customers", lambda _expected_actor: service.search_customers(
            text, _expected_actor=_expected_actor))

    def _on_customers(self, result, error, code, kind):
        self._customer_suggestions = [] if error else list(result or [])

    @Slot(str)
    def chooseCustomer(self, customer_id):
        chosen = next((c for c in self._customer_suggestions if c["customer_id"] == customer_id), None)
        if chosen is None:
            return
        self._customer = dict(chosen)
        self._customer_suggestions = []
        self._new_customer = {"name": "", "mobile": "", "place": ""}
        self._customer_id = ""
        self.setCustomer(chosen["customer_id"])

    @Slot(str, str, str)
    def setNewCustomer(self, name, mobile, place):
        """Typed details of a customer who does not exist yet (created on finalize only)."""
        values = {"name": (name or "").strip(), "mobile": (mobile or "").strip(), "place": (place or "").strip()}
        if values == self._new_customer:
            return
        self._new_customer = values
        if values["name"] and self._customer_id:
            self._customer_id, self._customer = "", {}
        self._touched()

    @Slot()
    def clearCustomer(self):
        self._customer, self._customer_suggestions = {}, []
        self._new_customer = {"name": "", "mobile": "", "place": ""}
        self._customer_id = "x"                       # force the change notification below
        self.setCustomer("")

    # ---- sale lines
    @Slot(str)
    def addBarcode(self, barcode):
        from app.barcode_input import canonical_barcode
        barcode, problem = canonical_barcode(barcode)            # never a guessed barcode
        if problem:
            self._error, self._barcode_not_found = problem, ""
            self.changed.emit()
            return
        if not barcode:
            return
        self._barcode_not_found = ""
        if self._sale_limit_reached():
            return
        self._error = ""
        service = self._service_instance()
        self._start("barcode", lambda _expected_actor: service.find_item_by_barcode(
            barcode, _expected_actor=_expected_actor))

    def _on_barcode(self, result, error, code, kind):
        if error:
            self._error = error
            # Offer بحث عن قطعة / إدخال يدوي inline instead of a dead end.
            self._barcode_not_found = "1" if code == "BARCODE_NOT_FOUND" else ""
            return
        self._add_sale_item(result, "SCAN")

    def _sale_limit_reached(self):
        """The 11th sale row is refused here at once; InvoiceService.finalize re-checks it."""
        from services.invoice_service import MAX_SALE_ITEMS, MAX_SALE_ITEMS_TEXT
        if len(self._sales) + len(self._described) >= MAX_SALE_ITEMS:
            self._error = MAX_SALE_ITEMS_TEXT
            self.changed.emit()
            return MAX_SALE_ITEMS_TEXT
        return ""

    def _add_sale_item(self, item, identified_by):
        if self._sale_limit_reached():
            return False
        if any(line["inventory_item_id"] == item["inventory_item_id"] for line in self._sales):
            self._error = "هذه القطعة مضافة بالفعل إلى الفاتورة"          # one physical piece, once
            self.changed.emit()
            return False
        self._sales.append({**item, "identified_by": identified_by})
        self._touched()
        return True

    def _lookup(self, key, item_id):
        return next((row for row in self._reference.get(key, []) if int(row["id"]) == int(item_id)), None)

    def _karat(self, karat_id):
        return next((p for p in self._board.get("prices", []) if int(p["karat_id"]) == int(karat_id)), None)

    def _described_line(self, kind, item_type_id, karat_id, factory_id, weight, size, notes, description="",
                        reason=""):
        limit = self._sale_limit_reached()
        if limit:
            return limit
        karat = self._karat(karat_id) if karat_id > 0 else None
        weight_value = _decimal(weight)
        if karat is None:
            return "اختر العيار."
        if weight_value is None or weight_value <= 0:
            return "اكتب الوزن."
        item_type = self._lookup("item_types", item_type_id) if item_type_id > 0 else None
        if kind == UNIDENTIFIED and item_type is None:
            return "اختر الصنف."
        if kind == UNIDENTIFIED and reason not in dict(REASONS):
            return "اختر سبب الإدخال اليدوي."
        if kind == UNIDENTIFIED and reason == "OTHER" and not (notes or "").strip():
            return "اكتب السبب في الملاحظات عند اختيار «سبب آخر»."
        if kind == OLD_GOLD_STOCK and item_type is None and not (description or "").strip():
            return "اختر الصنف أو اكتب الوصف."
        factory = self._lookup("factories", factory_id) if factory_id > 0 else None
        self._described.append({
            "kind": kind, "item_type_id": item_type_id if item_type else None,
            "item_type_name": item_type["name"] if item_type else "", "karat_id": int(karat_id),
            "karat": str(karat["karat"]), "weight": (weight or "").strip(),
            "factory_id": factory_id if factory else None, "factory_name": factory["name"] if factory else "",
            "size": (size or "").strip(), "notes": (notes or "").strip(), "description": (description or "").strip(),
            "reason_code": reason if kind == UNIDENTIFIED else None})
        self._error = ""
        self._touched()
        return ""

    @Slot(int, int, int, str, str, str, str, result=str)
    def addManualLine(self, item_type_id, karat_id, factory_id, weight, size, notes, reason_code):
        """F5: a physical piece whose exact stock record cannot be identified."""
        return self._described_line(UNIDENTIFIED, item_type_id, karat_id, factory_id, weight, size, notes,
                                    reason=reason_code)

    @Slot(int, str, int, int, str, str, str, result=str)
    def addOldGoldStockLine(self, item_type_id, description, karat_id, factory_id, weight, size, notes):
        """F7: sold from the bulk old-gold stock (grams per karat)."""
        return self._described_line(OLD_GOLD_STOCK, item_type_id, karat_id, factory_id, weight, size, notes,
                                    description=description)

    @Slot(int)
    def removeSaleLine(self, index):
        """Index over the whole sales table: stock pieces first, then described lines."""
        if 0 <= index < len(self._sales):
            del self._sales[index]
            self._touched()
        elif 0 <= index - len(self._sales) < len(self._described):
            del self._described[index - len(self._sales)]
            self._touched()

    # ---- old gold bought from the customer
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
        karat = self._karat(karat_id) if karat_id >= 0 else None
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

    def _discount_range_active(self):
        """1.25–3.00% applies whenever new gold is sold on the invoice (never to old gold only)."""
        return bool(self._sales or self._described)

    @Slot(str, result=str)
    def commitOldGoldDiscount(self, value):
        """Called when the field is committed (Enter, focus lost, before save).

        A valid value becomes the last valid one; an invalid value is never applied:
        the last valid value is restored (no clamping) and a short warning is shown.
        Returns the value the field must show. The service validates again."""
        value = (value or "").strip()
        number = _decimal(value) if value else None
        in_range = number is not None and (not self._discount_range_active()
                                          or DISCOUNT_MIN <= number <= DISCOUNT_MAX)
        if number is None or number < 0 or number > 100 or not in_range:
            self._discount_warning = DISCOUNT_WARNING if number is None or not in_range else "نسبة الخصم غير صحيحة"
            self.changed.emit()
            return self._old_gold_discount
        self._discount_warning = ""
        value = self._trim(value)
        if value != self._old_gold_discount:
            self._old_gold_discount = value
            self._touched()
        else:
            self.changed.emit()
        return self._old_gold_discount

    @Slot(str)
    def setOldGoldDiscountPercent(self, value):
        self.commitOldGoldDiscount(value)

    # ---- real money (invoice level only)
    def _methods(self):
        """Supported methods that are active in the database (cash until the list loads)."""
        rows = self._reference.get("payment_methods") or [
            {"code": "PHYSICAL_CASH", "requires_bank_account": False, "affects_physical_cash": True}]
        return [row for row in rows if row["code"] in PAYMENT_TEXT]

    def _method(self, code):
        return next((row for row in self._methods() if row["code"] == code), None)

    def _accounts_for(self, code):
        """The real accounts a method posts to (wallet accounts for محفظة, bank accounts otherwise)."""
        method = self._method(code)
        if method is None or not method.get("requires_bank_account"):
            return []
        wanted = ACCOUNT_TYPE_FOR_METHOD.get(code, "BANK")
        return [row for row in self._reference.get("bank_accounts", []) if (row.get("account_type") or "BANK") == wanted]

    @Slot(int, str)
    def setLegMethod(self, index, code):
        if not 0 <= index < len(self._legs) or self._method(code) is None or self._legs[index]["method"] == code:
            return
        # An account belongs to one method; never carried to another method.
        accounts = self._accounts_for(code)
        self._legs[index].update(method=code, account=accounts[0]["id"] if len(accounts) == 1 else "")
        self._touched()

    @Slot(int, str)
    def setLegAccount(self, index, account_id):
        if not 0 <= index < len(self._legs):
            return
        account_id = account_id or ""
        if account_id and account_id not in {row["id"] for row in self._accounts_for(self._legs[index]["method"])}:
            return
        if account_id != self._legs[index]["account"]:
            self._legs[index]["account"] = account_id
            self._touched()

    # ---- إجمالي الفاتورة: one total, payments allocate it
    def _total_mode(self):
        """With sold pieces the employee types إجمالي الفاتورة; old gold only keeps its own flow."""
        return bool(self._sales or self._described)

    def _total_value(self):
        value = _decimal(self._invoice_total) if self._invoice_total else None
        return value if value is not None and value > 0 else None

    def _remaining_now(self):
        """The one remainder: total − real payment legs − customer-account part (None: no total yet)."""
        total = self._total_value()
        if not self._total_mode() or total is None:
            return None
        return total - self._legs_total() - self._account_value()

    def _reject(self, message):
        """An edit that would break the allocation is never applied: the last valid value stays."""
        self._payment_warning = message
        self.changed.emit()
        return False

    def _accepted(self):
        self._payment_warning = ""
        self._touched()
        return True

    def _auto_single_leg(self):
        return self._leg0_auto and len(self._legs) == 1

    @Slot(str, result=bool)
    def setInvoiceTotal(self, text):
        text = (text or "").strip()
        if not text:
            self._invoice_total = ""
            if self._auto_single_leg():
                self._legs[0]["amount"] = ""
            return self._accepted()
        value = _decimal(text)
        if value is None or value < 0:
            return self._reject("إجمالي الفاتورة غير صحيح.")
        account = self._account_value()
        if self._auto_single_leg():
            if account > value:
                return self._reject("إجمالي الفاتورة لا يمكن أن يقل عن المبلغ على حساب العميل.")
            self._legs[0]["amount"] = self._amount_text(value - account) if value - account > 0 else ""
        elif self._legs_total() + account > value:
            # Never redistribute a manual split silently: the smaller total is refused.
            return self._reject("إجمالي الفاتورة لا يمكن أن يقل عن المبالغ الموزعة عليه (طرق الدفع وحساب العميل).")
        self._invoice_total = text
        return self._accepted()

    @Slot(int, str)
    def setLegAmount(self, index, amount):
        if not 0 <= index < len(self._legs):
            return
        amount = (amount or "").strip()
        if not self._total_mode():
            self._legs[index]["amount"] = amount
            self._touched()
            return
        value = _decimal(amount) if amount else Decimal("0")
        if value is None or value < 0:
            self._reject("المبلغ غير صحيح.")
            return
        total = self._total_value()
        if total is None:
            self._reject("اكتب إجمالي الفاتورة أولاً.")
            return
        others = self._legs_total() - (_decimal(self._legs[index]["amount"]) or Decimal("0"))
        if others + value + self._account_value() > total:
            self._reject("مجموع طرق الدفع لا يمكن أن يتجاوز المبلغ المطلوب دفعه الآن" if self._account_value() else "مجموع طرق الدفع لا يمكن أن يتجاوز إجمالي الفاتورة")
            return
        self._legs[index]["amount"] = amount
        if index == 0:
            self._leg0_auto = False
        self._accepted()

    def _legs_total(self):
        return sum((_decimal(leg["amount"]) or Decimal("0") for leg in self._legs), Decimal("0"))

    def _account_value(self):
        value = _decimal(self._account_amount) if self._account_amount else None
        return value if value is not None and value > 0 else Decimal("0")

    @staticmethod
    def _amount_text(value):
        return str(int(value)) if value == value.to_integral_value() else f"{value:.2f}"

    @Slot()
    def addPaymentLeg(self):
        """Split the money across methods (same direction), up to three legs. With a total, the new
        leg starts with exactly the current remainder (never a guessed split, never a zero leg)."""
        if self._total_mode():
            remaining = self._remaining_now()
            if remaining is None:
                self._reject("اكتب إجمالي الفاتورة أولاً.")
            elif remaining <= 0:
                self._reject("لا يوجد مبلغ متبقٍ لإضافة طريقة دفع.")
            elif len(self._legs) >= 3:
                self._reject("الحد الأقصى ثلاث طرق دفع.")
            else:
                self._legs.append({"method": "PHYSICAL_CASH", "account": "", "amount": self._amount_text(remaining)})
                self._leg0_auto = False
                self._accepted()
            return
        if len(self._legs) < 3:
            if self._account_value() and self._commercial is None:
                self._commercial = self._legs_total() + self._account_value()    # hold P + L
            self._legs.append({"method": "PHYSICAL_CASH", "account": "", "amount": ""})
            self.changed.emit()

    @Slot(int)
    def removePaymentLeg(self, index):
        if len(self._legs) > 1 and 0 <= index < len(self._legs):
            del self._legs[index]
            if len(self._legs) == 1:
                self._commercial = None
            self._touched()

    # The first leg, for the single-method layout and the keyboard flow.
    @Slot(str)
    def setPaymentMethod(self, code):
        self.setLegMethod(0, code)

    @Slot(str)
    def setBankAccount(self, bank_account_id):
        self.setLegAccount(0, bank_account_id)

    @Slot(str)
    def setPaymentAmount(self, amount):
        self.setLegAmount(0, amount)

    @Slot(str)
    def setPaymentDirection(self, direction):
        """Exchange only: who pays whom is the employee's explicit choice (never a sign)."""
        direction = direction if direction in ("IN", "OUT") else ""
        if direction != self._direction_choice:
            self._direction_choice = direction
            self._touched()

    def _money_direction(self):
        if self.directionChoosable and self._direction_choice:
            return self._direction_choice
        return self.paymentDirection

    def _derived_account_direction(self):
        """على العميل when the customer pays, للعميل when the shop pays (never the opposite here)."""
        return "CUSTOMER_OWES" if self._money_direction() == "IN" else "SHOP_OWES"

    @Slot(str)
    def setAccountDirection(self, direction):
        """The على العميل / للعميل button. With a total, choosing it puts the CURRENT remainder on the
        customer account — only when the employee asks (unpaid money is never turned into debt silently)."""
        if direction != self._derived_account_direction():
            return
        remaining = self._remaining_now()
        if remaining is not None and remaining > 0:
            self._account_amount = self._amount_text(self._account_value() + remaining)
            self._leg0_auto = False
            self._accepted()
            return
        self.changed.emit()

    @Slot(str)
    def setAccountAmount(self, amount):
        """The deferred part of the commercial amount: P + L stays the same.

        One payment leg is lowered/raised automatically (23,000 → 20,000 + 3,000 على العميل);
        with several legs the required amount is shown and the legs must add up to it."""
        amount = (amount or "").strip()
        if amount == self._account_amount:
            return
        if self._total_mode():
            new = _decimal(amount) if amount else Decimal("0")
            if new is None or new < 0:
                self._reject("مبلغ الحساب غير صحيح.")
                return
            total = self._total_value()
            if total is None:
                self._reject("اكتب إجمالي الفاتورة أولاً.")
                return
            if self._auto_single_leg():
                if new > total:
                    self._reject("مجموع طرق الدفع وحساب العميل لا يمكن أن يتجاوز إجمالي الفاتورة")
                    return
                self._legs[0]["amount"] = self._amount_text(total - new) if total - new > 0 else ""
            elif self._legs_total() + new > total:
                self._reject("مجموع طرق الدفع وحساب العميل لا يمكن أن يتجاوز إجمالي الفاتورة")
                return
            self._account_amount = amount
            self._accepted()
            return
        new = _decimal(amount) if amount else Decimal("0")
        if new is not None and new >= 0:
            commercial = (self._commercial if self._commercial is not None
                          else self._legs_total() + self._account_value())
            if len(self._legs) == 1:
                self._commercial = None
                if commercial > 0:
                    self._legs[0]["amount"] = self._amount_text(max(commercial - new, Decimal("0")))
            else:
                self._commercial = commercial if new > 0 else None
        self._account_amount = amount
        self._touched()

    def _required_now(self):
        """With several legs and an account part: what the legs must add up to (None otherwise)."""
        if self._total_mode():
            total = self._total_value()
            return None if total is None or not self._account_value() else max(total - self._account_value(),
                                                                                Decimal("0"))
        if self._commercial is None or len(self._legs) < 2 or not self._account_value():
            return None
        return max(self._commercial - self._account_value(), Decimal("0"))

    @Property(bool, notify=changed)
    def totalMode(self):
        return self._total_mode()

    @Property(str, notify=changed)
    def invoiceTotal(self):
        return self._invoice_total

    @Property(str, notify=changed)
    def remainingText(self):
        remaining = self._remaining_now()
        return "" if remaining is None or remaining <= 0 else f"المتبقي: {remaining:,.2f} ج"

    @Property(bool, notify=changed)
    def canAddPayment(self):
        if not self._total_mode():
            return len(self._legs) < 3
        remaining = self._remaining_now()
        return remaining is not None and remaining > 0 and len(self._legs) < 3

    @Property(str, notify=changed)
    def paymentWarning(self):
        return self._payment_warning

    @Property(str, notify=changed)
    def requiredNowText(self):
        required = self._required_now()
        return "" if required is None else f"المطلوب دفعه الآن: {required:,.2f} ج"

    def _required_problem(self):
        if self._total_mode():
            remaining = self._remaining_now()
            if remaining is None:
                return "اكتب إجمالي الفاتورة."
            if remaining > 0:
                return (f"المتبقي من إجمالي الفاتورة {remaining:,.2f} ج: أضف طريقة دفع "
                        "أو اضغط «على العميل» / «للعميل».")
            if remaining < 0:
                return "مجموع طرق الدفع وحساب العميل يتجاوز إجمالي الفاتورة."
            return ""
        required = self._required_now()
        if required is None or self._legs_total() == required:
            return ""
        return f"مجموع طرق الدفع ({self._legs_total():,.2f} ج) يجب أن يساوي المطلوب دفعه الآن ({required:,.2f} ج)."

    @Slot()
    def payFullAmount(self):
        """F8: fills the first leg with the full amount when one exists (old gold only);
        with sold pieces the price is the real money itself, so nothing is guessed."""
        if not self.previewReady or self._preview.get("full_amount") in (None, ""):
            return
        others = sum((_decimal(leg["amount"]) or Decimal("0") for leg in self._legs[1:]), Decimal("0"))
        amount = Decimal(str(self._preview["full_amount"])) - others
        self._legs[0]["amount"] = f"{max(amount, Decimal('0')):.2f}"
        self._touched()

    @Slot(str)
    def setSettlementChoice(self, choice):
        """Old gold only: SETTLE = تسوية فرق التقريب; ACCOUNT puts the gap on the customer account."""
        if choice == "ACCOUNT":
            gap = abs(self._residual())
            current = _decimal(self._account_amount or "0") or Decimal("0")
            self._settlement_choice = ""
            # Old gold only: the unpaid old-gold value is owed TO the customer (للعميل).
            self._account_amount = f"{current + gap:.2f}"
            self._touched()
            return
        choice = "SETTLE" if choice == "SETTLE" else ""
        if choice == self._settlement_choice:
            return
        self._settlement_choice = choice
        self._touched()

    @Slot(str)
    def setSettlementReason(self, reason):
        reason = (reason or "").strip()
        if reason == self._settlement_reason:
            return
        self._settlement_reason = reason
        self._touched()

    @Slot(str, str)
    def verifyApprover(self, username, password):
        """A manager types their own credentials; the grant stays in Python."""
        service = self._service_instance()
        self._approval_error = ""
        self._start("approver", lambda _expected_actor: service.verify_settlement_approver(
            username, password, _expected_actor=_expected_actor))

    def _on_approver(self, result, error, code, kind):
        if error:
            self._approver, self._approval_error = None, error
            return
        self._approver, self._approval_error = result, ""
        self._touched()

    @Slot()
    def clearApprover(self):
        self._approver, self._approval_error = None, ""
        self._touched()

    # ------------------------------------------------------------ بحث عن قطعة (cascade)

    @Slot(int)
    def setSearchType(self, item_type_id):
        """A higher level always resets the levels below it (no stale filters)."""
        self._search_type = max(0, item_type_id)
        self._search_karat = self._search_factory = 0
        self._reload_facets()

    @Slot(int)
    def setSearchKarat(self, karat_id):
        self._search_karat = max(0, karat_id) if self._search_type else 0
        self._search_factory = 0
        self._reload_facets()

    @Slot(int)
    def setSearchFactory(self, factory_id):
        self._search_factory = max(0, factory_id) if self._search_karat else 0
        self.changed.emit()

    @Slot()
    def resetSearch(self):
        self._search_type = self._search_karat = self._search_factory = 0
        self._search_results, self._search_error, self._search_truncated, self._search_ran = [], "", False, False
        self._reload_facets()

    def _reload_facets(self):
        service = self._service_instance()
        exclude = tuple(line["inventory_item_id"] for line in self._sales)
        type_id, karat_id = self._search_type or None, self._search_karat or None
        self.changed.emit()
        if hasattr(service, "item_search_facets"):
            self._start("facets", lambda _expected_actor: service.item_search_facets(
                item_type_id=type_id, karat_id=karat_id, exclude_ids=exclude, _expected_actor=_expected_actor))

    def _on_facets(self, result, error, code, kind):
        if not error:
            self._facets = result

    @Slot(str, str, str, str)
    def searchStock(self, text, weight, tolerance, size):
        """Results for the current الصنف / العيار / المصنع plus optional weight or size."""
        service = self._service_instance()
        exclude = tuple(line["inventory_item_id"] for line in self._sales)
        filters = dict(text=(text or "").strip(), karat_id=self._search_karat or None,
                       item_type_id=self._search_type or None, factory_id=self._search_factory or None,
                       weight=(weight or "").strip(), tolerance=(tolerance or "").strip() or "0",
                       size=(size or "").strip(), exclude_ids=exclude)
        self._search_error = ""
        self._start("item_search", lambda _expected_actor: service.search_sellable_items(
            _expected_actor=_expected_actor, **filters))

    @Slot(str, int, str, str, str)
    def searchItems(self, text, karat_id, weight, tolerance, size):
        """Stage 2 entry point (kept for compatibility): free text + karat."""
        self._search_karat = karat_id if karat_id > 0 else 0
        self.searchStock(text, weight, tolerance, size)

    def _on_item_search(self, result, error, code, kind):
        self._search_ran = True
        if error:
            self._search_results, self._search_truncated, self._search_error = [], False, error
            return
        self._search_results, self._search_truncated = list(result["items"]), bool(result["truncated"])

    @Slot(str, result=bool)
    def addSearchedItem(self, inventory_item_id):
        """The employee explicitly chose this physical piece (اختيار هذه القطعة)."""
        item = next((r for r in self._search_results if r["inventory_item_id"] == inventory_item_id), None)
        if item is None:
            return False
        self._error, self._barcode_not_found = "", ""
        added = self._add_sale_item({key: item.get(key) for key in (
            "inventory_item_id", "barcode", "item_name", "karat_id", "karat", "weight_grams", "factory_name", "size",
            "item_notes")}, "SEARCH")
        if added:
            self._search_results = [r for r in self._search_results if r["inventory_item_id"] != inventory_item_id]
            self.changed.emit()
        return added

    @Slot()
    def clearItemSearch(self):
        self._search_results, self._search_error, self._search_truncated, self._search_ran = [], "", False, False
        self.changed.emit()

    # ------------------------------------------------------------ preview / finalize

    def _draft(self):
        from services.invoice_service import (
            DescribedSaleInput, InvoiceDraft, NewCustomerInput, OldGoldLineInput, PaymentInput, SaleLineInput,
        )
        sales = tuple(SaleLineInput(line["inventory_item_id"], identified_by=line.get("identified_by", "SCAN"))
                      for line in self._sales)
        described = tuple(DescribedSaleInput(
            kind=line["kind"], karat_id=line["karat_id"], weight_grams=line["weight"],
            item_type_id=line["item_type_id"], factory_id=line["factory_id"],
            description=line["description"] or None, size=line["size"] or None, notes=line["notes"] or None,
            reason_code=line["reason_code"]) for line in self._described)
        olds = []
        for line in self._olds:
            kind, value = line["discount_type"], line["discount_value"]
            if kind == "NONE" and self._old_gold_discount and self._nonzero(self._old_gold_discount):
                kind, value = "PERCENT", self._old_gold_discount
            olds.append(OldGoldLineInput(line["karat_id"], line["weight"], None if kind == "NONE" else kind,
                                         None if kind == "NONE" else (value or None)))
        # Real payment legs; the service gives them the invoice's one direction.
        payments = tuple(PaymentInput(leg["method"], leg["amount"], bank_account_id=leg["account"] or None)
                         for leg in self._legs if leg["amount"] and self._nonzero(leg["amount"]))
        salesperson = int(self._salesperson_id) if self._salesperson_id.isdigit() else None
        new_customer = (NewCustomerInput(self._new_customer["name"], self._new_customer["mobile"] or None,
                                         self._new_customer["place"] or None)
                        if self._new_customer["name"] and not self._customer_id else None)
        return InvoiceDraft(salesperson, self._customer_id or None, sales, tuple(olds), payments,
                            described_sale_lines=described,
                            payment_direction=(self._direction_choice or None)
                            if (sales or described) and olds else None,
                            account_amount=self._account_amount
                            if self._account_amount and self._nonzero(self._account_amount) else None,
                            account_direction=self._derived_account_direction(),
                            settlement_choice=self._settlement_choice or None,
                            settlement_reason=self._settlement_reason or None,
                            settlement_approver=self._approver, new_customer=new_customer,
                            invoice_total=self._invoice_total
                            if self._total_mode() and self._total_value() is not None else None)

    @staticmethod
    def _nonzero(amount):
        try:
            return Decimal(amount) != 0
        except InvalidOperation:
            return True                               # let the calculator report the bad value

    def _start_preview(self):
        if not self._has_lines() or not self._salesperson_id:
            if not self._salesperson_id and self._has_lines():
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
        # A settled/closed choice no longer applies once there is no difference.
        if not self._residual() and self._settlement_choice:
            self._settlement_choice = ""

    def _residual(self):
        # Old gold only: the gap (R − L) the employee must settle or put on the account.
        # The last computed gap stays shown while a newer preview is running,
        # so the controls never flicker while the employee types.
        if not self._has_lines() or not self._preview:
            return Decimal("0")
        return _decimal(self._preview.get("gap_signed") or "0") or Decimal("0")

    @Slot(bool, bool)
    @Slot(bool, bool, bool)
    def finalizeInvoice(self, stale_acknowledged, dead_cash_confirmed, print_after=False):
        """Save happens first, always. Printing (when requested) is a separate step that runs
        only after the invoice is actually finalized, and a printer failure never touches it."""
        if not self.canFinalize:
            return
        draft, token = self._draft(), self._preview["price_token"]
        service = self._service_instance()
        self._error = ""
        self._pending_print, self._print_result = bool(print_after), {}
        self._start("finalize", lambda _expected_actor: service.finalize(
            draft, token, stale_acknowledged=bool(stale_acknowledged),
            dead_cash_confirmed=bool(dead_cash_confirmed), _expected_actor=_expected_actor))

    def _on_finalize(self, result, error, code, kind):
        payment_method = self._legs[0]["method"]
        if error:
            self._error = error
            if kind in ("PriceChanged", "PriceUnavailable"):
                self.refreshPrices()
            self._preview_version = -1
            self._timer.start()
            return
        notes = []
        if result.get("old_gold_discount_review"):
            notes.append("تعديل خصم الكسر للمراجعة")
        if result.get("customer_created"):
            notes.append("تم تسجيل العميل الجديد")
        if result.get("unidentified_cases"):
            notes.append("قطعة يدوية تحتاج مراجعة المدير")
        if result.get("making_review_required"):
            notes.append("السعر يحتاج مراجعة")
        success = {
            "invoiceNumber": result["invoice_number"], "finalizedAt": result["finalized_at"],
            "saleTotal": money(result["sale_total"]), "oldGoldTotal": money(result["old_gold_total"]),
            "netText": f"{NET_TEXT[result['net_direction']]} {money(result['net_amount'])}",
            "receivedText": money(result["money_received"]), "paidText": money(result["money_paid"]),
            "paymentText": PAYMENT_TEXT.get(payment_method, "") if (result["money_received"] != "0.00"
                                                                   or result["money_paid"] != "0.00") else "",
            "residualText": (f"{RESIDUAL_TEXT[result['account_residual_direction']]} "
                             f"{money(result['account_residual_amount'])}"
                             if result.get("account_residual_direction", "NONE") != "NONE" else "مسددة بالكامل"),
            "statusText": STATUS_TEXT.get(result["settlement_status"], result["settlement_status"]),
            "notes": " • ".join(notes),
        }
        # The draft is discarded; reference data, the salesperson and the success summary stay.
        salespeople, board, salesperson = self._salespeople, self._board, self._salesperson_id
        self._timer.stop()
        self._reset_state()
        self._salespeople, self._board, self._salesperson_id = salespeople, board, salesperson
        self._success = success
        self._last_invoice_id = str(result["invoice_id"])
        self.finalized.emit(result["invoice_number"])
        self.refreshPrices()
        self.refreshReference()
        if self._pending_print:
            self._pending_print = False
            self._start_print(self._last_invoice_id)

    def _start_print(self, invoice_id):
        """Printing is read-only after finalize: it can only report success or failure —
        it never rolls back or in any way changes the invoice that was already saved."""
        service = self._print_service_instance()
        self._print_result = {}
        self._start("print", lambda _expected_actor: service.print_invoice(
            invoice_id, _expected_actor=_expected_actor))

    def _on_print(self, result, error, code, kind):
        if error:
            self._print_result = {"printed": False, "message": error}
        else:
            self._print_result = {"printed": bool(result.printed), "message": result.message}
        self.changed.emit()

    @Slot(result=str)
    def billPrinterProblem(self):
        """Checked when حفظ وطباعة is chosen, BEFORE saving: '' when the Bill printer is ready.
        Save Only never asks (it needs no printer); the Label printer is never involved."""
        check = getattr(self._print_service_instance(), "bill_printer_ready", None)
        if check is None:
            return ""
        ready, message = check()
        return "" if ready else message

    @Slot()
    def openPrintingSettings(self):
        self.printingSettingsRequested.emit()

    @Slot()
    def retryPrint(self):
        """From the success banner, when the first attempt failed (offline printer, no paper, ...)."""
        if self._last_invoice_id and not self.printBusy:
            self._start_print(self._last_invoice_id)

    @Slot()
    def dismissPrintResult(self):
        self._print_result = {}
        self.changed.emit()

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
        self._success, self._print_result = {}, {}
        self.changed.emit()

    @Slot()
    def clearError(self):
        self._error = ""
        self._barcode_not_found = ""
        self.changed.emit()

    # ------------------------------------------------------------ properties

    @Property(bool, notify=changed)
    def busy(self):
        return bool(self._busy_ops - {"customers", "facets", "reference", "print"})

    @Property(bool, notify=changed)
    def finalizing(self):
        return "finalize" in self._busy_ops

    @Property(bool, notify=changed)
    def printBusy(self):
        return "print" in self._busy_ops

    @Property(bool, notify=changed)
    def printFailed(self):
        return bool(self._print_result) and not self._print_result.get("printed", True)

    @Property(bool, notify=changed)
    def printSucceeded(self):
        return bool(self._print_result.get("printed"))

    @Property(str, notify=changed)
    def printMessage(self):
        return self._print_result.get("message", "")

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
    def customerSuggestions(self):
        return [{"customerId": c["customer_id"], "name": c["name"], "mobile": c.get("mobile") or "",
                 "place": c.get("place") or "", "code": c.get("customer_code") or "",
                 "accountText": account_text(c.get("balance")), "deadCash": bool(c.get("dead_cash_warning"))}
                for c in self._customer_suggestions]

    @Property("QVariantMap", notify=changed)
    def customerInfo(self):
        """The chosen customer (existing or new) as shown beside the customer field."""
        if self._customer_id and self._customer:
            c = self._customer
            balance = self._preview.get("customer_balance_before") if self.previewReady else c.get("balance")
            return {"state": "EXISTING", "name": c["name"], "mobile": c.get("mobile") or "",
                    "place": c.get("place") or "", "code": c.get("customer_code") or "",
                    "accountText": account_text(balance if balance is not None else c.get("balance")),
                    "deadCash": bool(c.get("dead_cash_warning")) or self.deadCashWarning}
        if self._new_customer["name"]:
            same = self._preview.get("new_customer_same_mobile", []) if self.previewReady else []
            return {"state": "NEW", "name": self._new_customer["name"], "mobile": self._new_customer["mobile"],
                    "place": self._new_customer["place"], "code": "",
                    "accountText": "عميل جديد — يُسجل عند الحفظ",
                    "deadCash": False, "sameMobile": same[0]["name"] if same else ""}
        return {"state": "NONE", "name": "", "mobile": "", "place": "", "code": "", "accountText": "",
                "deadCash": False}

    @Property("QVariantList", notify=changed)
    def priceRows(self):
        return [{"karatId": int(p["karat_id"]), "karat": str(p["karat"]), "buy": price(p["shop_buy"]),
                 "sell": price(p["shop_sell"]), "buyShort": short_price(p["shop_buy"]),
                 "sellShort": short_price(p["shop_sell"])} for p in self._board.get("prices", [])]

    @Property(str, notify=changed)
    def priceStatusText(self):
        if not self._board:
            return "الأسعار غير متاحة"
        return PRICE_STATUS_TEXT.get(self._board.get("price_status"), self._board.get("price_status", ""))

    @Property(bool, notify=changed)
    def priceStale(self):
        return bool(self._board.get("requires_price_acknowledgement"))

    @Property("QVariantList", notify=changed)
    def itemTypes(self):
        return [{"id": int(r["id"]), "name": r["name"]} for r in self._reference.get("item_types", [])]

    @Property("QVariantList", notify=changed)
    def factories(self):
        return [{"id": int(r["id"]), "name": r["name"]} for r in self._reference.get("factories", [])]

    @Property("QVariantList", notify=changed)
    def manualReasons(self):
        return [{"code": code, "text": text} for code, text in REASONS]

    @Property("QVariantList", notify=changed)
    def oldGoldStock(self):
        """Available COUNTED grams per karat, for بيع من الكسر."""
        return [{"karatId": r["karat_id"], "karat": str(r["karat"]), "grams": grams(r["grams"])}
                for r in self._reference.get("old_gold_stock", [])]

    @Slot()
    def _sync_models(self):
        self._sale_model.set_rows(self._sale_rows())
        self._old_gold_model.set_rows(self._old_gold_rows())
        self._leg_model.set_rows([{**leg, "legIndex": leg["index"]} for leg in self.paymentLegs])

    @Property(QObject, constant=True)
    def saleLinesModel(self):
        return self._sale_model

    @Property(QObject, constant=True)
    def oldGoldLinesModel(self):
        return self._old_gold_model

    @Property(QObject, constant=True)
    def paymentLegsModel(self):
        return self._leg_model

    def _preview_lines(self, key):
        return self._preview.get(key, []) if self.previewReady else []

    def _sale_rows(self):
        rows, calc = [], self._preview_lines("sale_lines")
        for index, line in enumerate(self._sales):
            result = calc[index] if index < len(calc) else {}
            # Table cells carry numbers only; the column headers name the units.
            rows.append({"kind": "STOCK", "barcode": line.get("barcode") or "", "itemName": line.get("item_name") or "",
                         "karat": str(line.get("karat") or ""), "weight": _weight(line["weight_grams"]),
                         "factory": line.get("factory_name") or "", "size": str(line.get("size") or ""),
                         "notes": line.get("item_notes") or "",
                         "lineTotal": price(result.get("line_total")),
                         "identifiedBy": line.get("identified_by", "SCAN"), "tag": ""})
        described = self._preview_lines("described_lines")
        for index, line in enumerate(self._described):
            result = described[index] if index < len(described) else {}
            name = " — ".join(v for v in (line["item_type_name"], line["description"]) if v)
            rows.append({"kind": line["kind"], "barcode": "", "itemName": name, "karat": line["karat"],
                         "weight": _weight(line["weight"]), "factory": line["factory_name"],
                         "size": line["size"] or "", "notes": line["notes"],
                         "lineTotal": price(result.get("line_total")), "identifiedBy": "",
                         "tag": "تحتاج مراجعة" if line["kind"] == UNIDENTIFIED else "من الكسر"})
        return rows

    def _old_gold_rows(self):
        rows, calc = [], self._preview_lines("old_gold_lines")
        for index, line in enumerate(self._olds):
            result = calc[index] if index < len(calc) else {}
            rows.append({"karat": str(line["karat"]), "weight": line["weight"],
                         "discountType": line["discount_type"], "discountValue": line["discount_value"],
                         "goldPrice": price(result.get("gold_price_per_gram")),
                         "grossTotal": money(result.get("gross_total")),
                         "discountAmount": money(result.get("discount_amount")),
                         "lineTotal": money(result.get("line_total"))})
        return rows

    @Property(int, notify=changed)
    def saleCount(self):
        return len(self._sales) + len(self._described)

    @Property(int, notify=changed)
    def stockSaleCount(self):
        return len(self._sales)

    @Property(int, notify=changed)
    def oldGoldCount(self):
        return len(self._olds)

    @Property(int, notify=changed)
    def oldGoldKaratId(self):
        return self._old_gold_karat_id

    @Property(str, notify=changed)
    def paymentAmount(self):
        return self._legs[0]["amount"]

    @Property(str, notify=changed)
    def paymentMethod(self):
        return self._legs[0]["method"]

    @Property("QVariantList", notify=changed)
    def paymentMethods(self):
        """نقدي / محفظة / InstaPay / تحويل بنكي; a method that needs an account is usable only with a real one."""
        return [{"code": row["code"], "text": PAYMENT_TEXT[row["code"]],
                 "needsAccount": bool(row["requires_bank_account"]),
                 "available": self._unavailable_text(row["code"]) == "",
                 "unavailableText": self._unavailable_text(row["code"])}
                for row in self._methods()]

    def _unavailable_text(self, code):
        """Why a supported method cannot post yet (no active account of its type); '' when usable."""
        method = self._method(code)
        if method is None or not method.get("requires_bank_account") or self._accounts_for(code):
            return ""
        return NO_ACCOUNT_TEXT[ACCOUNT_TYPE_FOR_METHOD.get(code, "BANK")]

    @Property("QVariantList", notify=changed)
    def availablePaymentMethods(self):
        """Methods that can post to a real ledger right now (the selector still shows all of them)."""
        return [m for m in self.paymentMethods if m["available"]]

    @Property("QVariantList", notify=changed)
    def paymentLegs(self):
        return [{"index": i, "method": leg["method"], "account": leg["account"], "amount": leg["amount"],
                 "unavailableText": self._unavailable_text(leg["method"]),
                 "needsAccount": bool((self._method(leg["method"]) or {}).get("requires_bank_account"))
                 and self._unavailable_text(leg["method"]) == "",
                 "accounts": [{"id": row["id"], "name": " — ".join(v for v in (row["name"], row.get("bank_name")) if v)}
                              for row in self._accounts_for(leg["method"])]}
                for i, leg in enumerate(self._legs)]

    @Property(bool, notify=changed)
    def paymentNeedsAccount(self):
        return bool((self._method(self._legs[0]["method"]) or {}).get("requires_bank_account"))

    @Property("QVariantList", notify=changed)
    def bankAccounts(self):
        return self.paymentLegs[0]["accounts"]

    @Property(str, notify=changed)
    def bankAccountId(self):
        return self._legs[0]["account"]

    @Property(bool, notify=changed)
    def directionChoosable(self):
        """Only an exchange (sold pieces and old gold) can go either way."""
        return bool((self._sales or self._described) and self._olds)

    @Property(str, notify=changed)
    def paymentDirection(self):
        """IN / OUT: the last computed direction (stays put while a newer preview runs)."""
        if self._preview.get("payment_direction") in ("IN", "OUT"):
            return self._preview["payment_direction"]
        if self._direction_choice and self.directionChoosable:
            return self._direction_choice
        return "OUT" if self._olds and not (self._sales or self._described) else "IN"

    @Property(str, notify=changed)
    def paymentLabel(self):
        return DIRECTION_TEXT[self.paymentDirection]

    @Property(str, notify=changed)
    def accountAmount(self):
        return self._account_amount

    @Property(str, notify=changed)
    def accountDirection(self):
        return self._derived_account_direction()

    @Property(str, notify=changed)
    def accountEffectText(self):
        """سيضاف على العميل / سيضاف للعميل: the entered positive amount in words (no sign shown)."""
        amount = _decimal(self._account_amount) if self._account_amount else None
        if amount is None or amount <= 0:
            return ""
        side = "على العميل" if self._derived_account_direction() == "CUSTOMER_OWES" else "للعميل"
        return f"سيضاف {side}: {amount:,.2f} ج"

    @Property(str, notify=changed)
    def discountWarning(self):
        return self._discount_warning

    @Property(bool, notify=changed)
    def fullAmountAvailable(self):
        return bool(self.previewReady and self._preview.get("full_amount") not in (None, ""))

    @Property(str, notify=changed)
    def oldGoldDiscountPercent(self):
        return self._old_gold_discount

    @Property("QVariantMap", notify=changed)
    def weightTotals(self):
        """Grams per karat under each table (مباع / مشترى), for every sold line kind."""
        def totals(entries):
            sums = {karat: Decimal("0") for karat in WEIGHT_KARATS}
            for karat, weight in entries:
                value = _decimal(weight or "0")
                if value is not None and str(karat) in sums:
                    sums[str(karat)] += value
            return {karat: (f"{value:.3f}" if value else "—") for karat, value in sums.items()}
        sold = [(line.get("karat"), line.get("weight_grams")) for line in self._sales]
        sold += [(line["karat"], line["weight"]) for line in self._described]
        return {"sold": totals(sold), "bought": totals((line["karat"], line["weight"]) for line in self._olds)}

    # ---- search state
    @Property("QVariantMap", notify=changed)
    def searchFacets(self):
        return {"itemTypes": self._facets.get("item_types", []), "karats": self._facets.get("karats", []),
                "factories": self._facets.get("factories", [])}

    @Property(int, notify=changed)
    def searchTypeId(self):
        return self._search_type

    @Property(int, notify=changed)
    def searchKaratId(self):
        return self._search_karat

    @Property(int, notify=changed)
    def searchFactoryId(self):
        return self._search_factory

    @Property(str, notify=changed)
    def searchPath(self):
        """The active cascade in words, so no filter is ever hidden."""
        parts = []
        for key, value, fmt in (("item_types", self._search_type, "{}"), ("karats", self._search_karat, "عيار {}"),
                                ("factories", self._search_factory, "{}")):
            row = next((r for r in self._facets.get(key, []) if r["id"] == value), None) if value else None
            if row:
                parts.append(fmt.format(row["name"]))
        return " ← ".join(parts)

    @staticmethod
    def item_title(item):
        """دبلة | عيار 21 | 3.500 جم | مصنع X | مقاس 18 — enough to tell similar pieces apart.

        Parts are separated with right-to-left marks so numbers keep their place in Arabic text."""
        parts = [item.get("item_name") or "قطعة", f"عيار {item.get('karat')}", grams(item.get("weight_grams"))]
        factory = (item.get("factory_name") or "").strip()
        if factory:
            parts.append(factory if factory.startswith("مصنع") else f"مصنع {factory}")
        if item.get("size"):
            parts.append(f"مقاس {item['size']}")
        if item.get("category_name"):
            parts.append(item["category_name"])
        return "‏ | ‏".join(parts)

    @Property("QVariantList", notify=changed)
    def searchResults(self):
        # Separate fields so the row can stress type, weight and karat; barcode stays secondary.
        return [{"inventoryItemId": r["inventory_item_id"], "title": self.item_title(r),
                 "itemName": r.get("item_name") or "قطعة", "weightText": grams(r.get("weight_grams")),
                 "karatText": f"عيار {r.get('karat')}",
                 "detailText": " • ".join(v for v in (
                     (r.get("factory_name") or "").strip(),
                     f"مقاس {r['size']}" if r.get("size") else "",
                     (r.get("category_name") or "").strip(),
                     (r.get("item_notes") or "").strip()) if v),
                 "barcodeText": r["barcode"] if r.get("barcode") else "بدون باركود"}
                for r in self._search_results]

    @Property(bool, notify=changed)
    def searchBusy(self):
        return "item_search" in self._busy_ops

    @Property(str, notify=changed)
    def searchMessage(self):
        if self._search_error:
            return self._search_error
        if not self._search_ran or "item_search" in self._busy_ops:
            return ""
        if not self._search_results:
            return "لا توجد قطع متاحة بهذه البيانات — استخدم إدخال يدوي F5 إذا لم تجد القطعة."
        count = f"{len(self._search_results)} قطعة — اختر القطعة التي أمامك"
        return count + (" (تظهر أول النتائج فقط؛ ضيّق البحث)" if self._search_truncated else "")

    @Property(bool, notify=changed)
    def barcodeNotFound(self):
        return bool(self._barcode_not_found)

    # ---- money
    @Property(bool, notify=changed)
    def previewReady(self):
        return bool(self._preview) and self._preview_version == self._version

    @Property("QVariantMap", notify=changed)
    def summary(self):
        """Every money figure as text; the service computed them all (effective sale = O + P_in − P_out + L)."""
        p = self._preview if self.previewReady else {}
        empty = {"saleTotal": "", "oldGoldTotal": "", "netText": "", "netDirection": "", "receivedText": "",
                 "paidText": "", "residualText": "", "residualDirection": "", "statusText": "",
                 "dueLabel": self.paymentLabel, "dueAmount": "", "paidLabel": self.paymentLabel, "paidAmount": "",
                 "remainingLabel": "على حساب العميل", "remainingAmount": "", "goldValue": "", "averageMaking": "",
                 "effectiveSale": "", "makingNotice": "", "makingStatus": "", "oldGoldGross": "",
                 "discountChanged": False, "differenceAmount": "", "differenceSigned": "", "accountEffect": ""}
        if not p:
            return empty
        residual = p["account_residual_direction"]
        out = p.get("payment_direction") == "OUT"
        gross = sum((Decimal(str(line["gross_total"])) for line in p.get("old_gold_lines", [])), Decimal("0"))
        difference = self._residual()
        paid = Decimal(str(p["money_paid"] if out else p["money_received"]))
        account = (f"{REMAINING_TEXT[residual]} {money(p['account_residual_amount'])}" if residual != "NONE" else "")
        methods = " + ".join(dict.fromkeys(PAYMENT_TEXT[leg["method"]] for leg in self._legs
                                           if leg["amount"] and self._nonzero(leg["amount"])))
        # The dominant final figure: the real money in its direction (or the full old-gold amount).
        due = p.get("full_amount") if p.get("full_amount") not in (None, "") else paid
        return {"dueLabel": DIRECTION_TEXT["OUT" if out else "IN"],
                "dueAmount": money(due) if Decimal(str(due)) else "",
                "paidLabel": DIRECTION_TEXT["OUT" if out else "IN"] + (f" ({methods})" if methods else ""),
                "paidAmount": money(paid),
                "remainingLabel": REMAINING_TEXT[residual],
                "remainingAmount": "" if residual == "NONE" else money(p["account_residual_amount"]),
                "saleTotal": money(p["sale_total"]), "oldGoldTotal": money(p["old_gold_total"]),
                "netText": f"{NET_TEXT[p['net_direction']]} {money(p['net_amount'])}",
                "netDirection": p["net_direction"],
                "receivedText": money(p["money_received"]), "paidText": money(p["money_paid"]),
                "residualText": RESIDUAL_TEXT[residual] + ("" if residual == "NONE"
                                                          else f" {money(p['account_residual_amount'])}"),
                "residualDirection": residual,
                "statusText": STATUS_TEXT.get(p["settlement_status"], p["settlement_status"]),
                "goldValue": money(p.get("gold_value_total")),
                "effectiveSale": money(p.get("effective_sale_total")),
                "averageMaking": price(p.get("average_making_per_gram")),
                "makingStatus": p.get("making_review_status") or "",
                "makingNotice": MAKING_NOTICE.get(p.get("making_review_status"), ""),
                "oldGoldGross": money(gross) if p.get("old_gold_lines") else "",
                "discountChanged": bool(p.get("old_gold_discount_changed")),
                "differenceAmount": "" if not difference else f"{abs(difference):,.2f}",
                "differenceSigned": str(difference or ""), "accountEffect": account}

    @Property(str, notify=changed)
    def invoiceTotalText(self):
        """إجمالي الفاتورة: the commercial amount P + L (never only the money moving now).

        The service's net amount: unchanged when money moves between cash, bank, wallet
        and the customer account, and the value the making is based on."""
        if not self.previewReady or self._preview.get("net_amount") in (None, ""):
            return ""
        return money(self._preview["net_amount"])

    @Property("QVariantList", notify=changed)
    def paymentBreakdown(self):
        """How the invoice total is allocated: each used payment leg, then the account part.
        Unused methods (zero) are never listed."""
        if not self.previewReady:
            return []
        rows = []
        for leg in self._legs:
            amount = _decimal(leg["amount"]) if leg["amount"] else None
            if amount is None or amount <= 0:
                continue
            label = PAYMENT_TEXT.get(leg["method"], leg["method"])
            account = next((a for a in self._accounts_for(leg["method"]) if a["id"] == leg["account"]), None)
            if account:
                label = f"{label} — {account['name']}"
            rows.append({"label": label, "amount": money(amount), "kind": "MONEY"})
        account_amount = self._account_value()
        if account_amount:
            side = "على العميل" if self._derived_account_direction() == "CUSTOMER_OWES" else "للعميل"
            rows.append({"label": side, "amount": money(account_amount), "kind": self._derived_account_direction()})
        return rows

    @Property(bool, notify=changed)
    def customerMissing(self):
        """The service refused the draft for a missing customer name (shown before Save)."""
        return self.previewReady and any(p.get("code") == "CUSTOMER_NAME_REQUIRED"
                                         for p in self._preview.get("blocking_problems", []))

    @Property(bool, notify=changed)
    def hasDifference(self):
        return bool(self._residual())

    @Property(str, notify=changed)
    def settlementChoice(self):
        return self._settlement_choice

    @Property(str, notify=changed)
    def settlementReason(self):
        return self._settlement_reason

    @Property(bool, notify=changed)
    def settlementNeedsReason(self):
        limit = _decimal(self._reference["settings"].get("settlement_self_limit") or "50") or Decimal("50")
        return self._settlement_choice == "SETTLE" and abs(self._residual()) > limit

    @Property(bool, notify=changed)
    def settlementNeedsApproval(self):
        actor = self._session.current_actor()
        if actor is not None and "INVOICE_SETTLEMENT_APPROVE" in actor.permissions:
            return False                                  # the logged-in user may approve directly
        return self.settlementNeedsReason

    @Property(str, notify=changed)
    def approverText(self):
        if self._approver is not None:
            return f"موافقة: {self._approver.display_name}"
        return self._approval_error

    @Property(bool, notify=changed)
    def approverOk(self):
        return self._approver is not None

    @Property(bool, notify=changed)
    def makingReviewRequired(self):
        return bool(self.previewReady and self._preview.get("making_review_required"))

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
        if not self.previewReady:
            return []
        local = [self._required_problem()] if self._required_problem() else []
        return local + [p["message"] for p in self._preview.get("blocking_problems", [])]

    @Property(bool, notify=changed)
    def canFinalize(self):
        return (self.previewReady and not self.busy and not self.blockingProblems
                and bool(self._salesperson_id) and self._has_lines())

    @Property("QVariantMap", notify=changed)
    def success(self):
        return self._success

    @Property(bool, notify=changed)
    def hasSuccess(self):
        return bool(self._success)
