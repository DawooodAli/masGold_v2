"""Customer profile editing and read-only account presentation for QML."""

from copy import deepcopy
from datetime import date, datetime
from decimal import Decimal
import logging

from PySide6.QtCore import QObject, Property, Signal, Slot

from services.customer_service import CustomerService


logger = logging.getLogger(__name__)


class CustomerViewModel(QObject):
    dataChanged = Signal()
    typesChanged = Signal()
    filtersChanged = Signal()
    selectionChanged = Signal()
    historyChanged = Signal()
    editChanged = Signal()
    busyChanged = Signal()
    errorChanged = Signal()
    actionChanged = Signal()

    PAGE_SIZE = 100
    EDITABLE_FIELDS = (
        "name", "mobile", "place", "customer_type_id", "notes",
        "is_active",
    )
    CUSTOMER_TYPE_LABELS = {"REGULAR": "عادي", "VIP": "مميز"}
    BALANCE_LABELS = {
        "CUSTOMER_OWES_SHOP": "على العميل للمحل",
        "SHOP_OWES_CUSTOMER": "للعميل عند المحل",
        "SETTLED": "الحساب متزن",
    }
    TRANSACTION_LABELS = {
        "SALE_BALANCE_DUE": "متبقي بيع",
        "BUY_BALANCE_DUE": "متبقي شراء من العميل",
        "CUSTOMER_PAYMENT": "سداد من العميل",
        "SHOP_PAYMENT": "سداد للعميل",
        "CUSTOMER_ADVANCE": "دفعة مقدمة من العميل",
        "ADVANCE_APPLIED": "استخدام رصيد العميل",
        "DEAD_CASH_WRITE_OFF": "شطب دين معدوم",
        "DEAD_CASH_REVERSAL": "عكس شطب دين معدوم",
        "MANUAL_ADJUSTMENT": "تسوية يدوية",
        "LEGACY_ADJUSTMENT": "تسوية تاريخية مرحّلة",
    }
    SEVERITY_LABELS = {
        "INFO": "معلومة", "WARNING": "تحذير", "CRITICAL": "تحذير مهم",
    }
    BIGINT_FIELDS = {
        "id", "customer_id", "reference_id", "related_dead_cash_id",
        "writeoff_transaction_id", "reversal_transaction_id", "legacy_source_id",
        "transaction_id", "flag_id", "dead_cash_id",
    }

    def __init__(self, parent=None, service=None):
        super().__init__(parent)
        self._service = service if service is not None else CustomerService()
        self._customers = []
        self._customer_types = []
        self._page = 1
        self._has_more_customers = False
        self._search_text = ""
        self._status_filter = "ALL"
        self._customer_type_filter_id = 0
        self._balance_state_filter = "ALL"
        self._selected_customer = {}
        self._selected_customer_id = ""
        self._transactions = []
        self._active_flags = []
        self._dead_cash_history = []
        self._transactions_page = 1
        self._dead_cash_page = 1
        self._has_more_transactions = False
        self._has_more_dead_cash = False
        self._edit_customer = {}
        self._edit_customer_id = ""
        self._edit_original_values = None
        self._busy = False
        self._error_message = ""
        self._profile_error_message = ""
        self._read_errors = {}
        self._profile_errors = {}
        self._action_message = ""
        self._action_success = False
        # Loading starts when the Customers page is opened, never at import/init.

    @Property("QVariantList", notify=dataChanged)
    def customers(self):
        return self._customers

    @Property(int, notify=dataChanged)
    def page(self):
        return self._page

    @Property(int, notify=dataChanged)
    def customerCount(self):
        return len(self._customers)

    @Property(bool, notify=dataChanged)
    def hasMoreCustomers(self):
        return self._has_more_customers

    @Property(int, constant=True)
    def pageSize(self):
        return self.PAGE_SIZE

    @Property("QVariantList", notify=typesChanged)
    def customerTypes(self):
        return self._customer_types

    @Property(str, notify=filtersChanged)
    def searchText(self):
        return self._search_text

    @Property(str, notify=filtersChanged)
    def statusFilter(self):
        return self._status_filter

    @Property(int, notify=filtersChanged)
    def customerTypeFilterId(self):
        return self._customer_type_filter_id

    @Property(str, notify=filtersChanged)
    def balanceStateFilter(self):
        return self._balance_state_filter

    @Property("QVariantMap", notify=selectionChanged)
    def selectedCustomer(self):
        return self._selected_customer

    @Property(str, notify=selectionChanged)
    def selectedCustomerId(self):
        return self._selected_customer_id

    @Property(bool, notify=selectionChanged)
    def hasSelection(self):
        return bool(self._selected_customer)

    @Property("QVariantList", notify=historyChanged)
    def transactions(self):
        return self._transactions

    @Property("QVariantList", notify=historyChanged)
    def activeFlags(self):
        return self._active_flags

    @Property("QVariantList", notify=historyChanged)
    def deadCashHistory(self):
        return self._dead_cash_history

    @Property(int, notify=historyChanged)
    def transactionsPage(self):
        return self._transactions_page

    @Property(int, notify=historyChanged)
    def deadCashPage(self):
        return self._dead_cash_page

    @Property(bool, notify=historyChanged)
    def hasMoreTransactions(self):
        return self._has_more_transactions

    @Property(bool, notify=historyChanged)
    def hasMoreDeadCash(self):
        return self._has_more_dead_cash

    @Property("QVariantMap", notify=editChanged)
    def editCustomer(self):
        return self._edit_customer

    @Property(bool, notify=busyChanged)
    def busy(self):
        return self._busy

    @Property(str, notify=errorChanged)
    def errorMessage(self):
        return self._error_message

    @Property(str, notify=errorChanged)
    def profileErrorMessage(self):
        return self._profile_error_message

    @Property(str, notify=actionChanged)
    def actionMessage(self):
        return self._action_message

    @Property(bool, notify=actionChanged)
    def actionSuccess(self):
        return self._action_success

    @classmethod
    def _qml_safe(cls, value, key=None):
        if value is None:
            return None
        if key in cls.BIGINT_FIELDS:
            return str(value)
        if isinstance(value, Decimal):
            return format(value, "f")
        if isinstance(value, datetime):
            return value.strftime("%Y-%m-%d %H:%M:%S")
        if isinstance(value, date):
            return value.strftime("%Y-%m-%d")
        if isinstance(value, dict):
            return {k: cls._qml_safe(v, k) for k, v in value.items()}
        if isinstance(value, (list, tuple)):
            return [cls._qml_safe(v) for v in value]
        return value

    @staticmethod
    def _money_text(value, signed=False):
        if value is None:
            return "غير متاح"
        amount = value if isinstance(value, Decimal) else Decimal(str(value))
        if signed and amount != 0:
            return format(amount, "+,.2f")
        return format(amount, ",.2f")

    @classmethod
    def _customer_row(cls, raw):
        row = cls._qml_safe(raw)
        row["id"] = str(raw["customer_id"])
        row["is_active"] = bool(raw.get("is_active"))
        row["status_label"] = "نشط" if row["is_active"] else "غير نشط"
        type_code = raw.get("customer_type_code")
        row["type_label"] = cls.CUSTOMER_TYPE_LABELS.get(
            type_code, "نوع آخر" if type_code else "غير محدد",
        )
        row["balance_text"] = cls._money_text(raw.get("balance"), signed=True)
        row["balance_meaning"] = cls.BALANCE_LABELS.get(
            raw.get("balance_status"), "الرصيد غير متاح",
        )
        row["balance_nonzero"] = (
            raw.get("balance") is not None
            and Decimal(str(raw["balance"])) != 0
        )
        row["last_transaction_text"] = row.get("last_transaction_at") or "—"
        return row

    @classmethod
    def _type_row(cls, raw):
        row = cls._qml_safe(raw)
        row["id"] = int(raw["id"])
        row["is_active"] = bool(raw.get("is_active"))
        row["label"] = cls.CUSTOMER_TYPE_LABELS.get(raw.get("code"), "نوع آخر")
        row["type_label"] = row["label"]
        return row

    @classmethod
    def _transaction_row(cls, raw):
        row = cls._qml_safe(raw)
        row["id"] = str(raw["transaction_id"])
        row["amount_text"] = cls._money_text(raw.get("amount_delta"), signed=True)
        row["type_label"] = cls.TRANSACTION_LABELS.get(
            raw.get("transaction_type_code"), "حركة أخرى",
        )
        return row

    @classmethod
    def _flag_row(cls, raw):
        row = cls._qml_safe(raw)
        row["id"] = str(raw["flag_id"])
        row["severity_label"] = cls.SEVERITY_LABELS.get(raw.get("severity"), "تنبيه")
        row["type_label"] = (
            "تحذير ديون معدومة"
            if raw.get("flag_type") == "DEAD_CASH_WARNING" else "تنبيه للعميل"
        )
        return row

    @classmethod
    def _dead_cash_row(cls, raw):
        row = cls._qml_safe(raw)
        row["id"] = str(raw["dead_cash_id"])
        row["amount_text"] = cls._money_text(raw.get("amount"))
        row["balance_before_text"] = cls._money_text(raw.get("balance_before"), signed=True)
        row["balance_after_text"] = cls._money_text(raw.get("balance_after"), signed=True)
        row["is_reversed"] = bool(raw.get("is_reversed"))
        row["status_label"] = "تم عكس الشطب" if row["is_reversed"] else "رصيد مشطوب"
        return row

    def _set_busy(self, busy):
        if self._busy != busy:
            self._busy = busy
            self.busyChanged.emit()

    def _set_action(self, message, success):
        self._action_message = message
        self._action_success = success
        self.actionChanged.emit()

    @staticmethod
    def _error_text(exc, fallback):
        # Validation errors are intentionally translated by CustomerService.
        if isinstance(exc, ValueError) and any("\u0600" <= char <= "\u06ff" for char in str(exc)):
            return str(exc)
        logger.error("Customer operation failed", exc_info=True)
        return fallback

    def _publish_errors(self):
        self._error_message = "\n".join(self._read_errors.values())
        self._profile_error_message = "\n".join(self._profile_errors.values())
        self.errorChanged.emit()

    def _load_types(self):
        try:
            rows = self._service.get_customer_types()
            self._customer_types = [self._type_row(row) for row in rows]
            self._read_errors.pop("types", None)
            self.typesChanged.emit()
            return True
        except Exception as exc:
            self._read_errors["types"] = self._error_text(
                exc, "تعذر تحميل أنواع العملاء. حاول التحديث مرة أخرى.",
            )
            return False
        finally:
            self._publish_errors()

    def _load_customers(self, page):
        try:
            result = self._service.search_customers(
                search=self._search_text,
                status=self._status_filter,
                customer_type_id=self._customer_type_filter_id or None,
                balance_status=self._balance_state_filter,
                page=page,
            )
            # A last-page row may disappear from an active/inactive filter.
            if not result["items"] and page > 1:
                result = self._service.search_customers(
                    search=self._search_text,
                    status=self._status_filter,
                    customer_type_id=self._customer_type_filter_id or None,
                    balance_status=self._balance_state_filter,
                    page=1,
                )
            rows = [self._customer_row(row) for row in result["items"]]
            self._customers = rows
            self._page = result["page"]
            self._has_more_customers = result["has_more"]
            self._read_errors.pop("list", None)
            return True
        except Exception as exc:
            self._customers = []
            self._has_more_customers = False
            self._read_errors["list"] = self._error_text(
                exc, "تعذر تحميل العملاء. حاول التحديث مرة أخرى.",
            )
            return False
        finally:
            self.dataChanged.emit()
            self._publish_errors()

    def _clear_profile(self, customer_id=""):
        self._selected_customer_id = customer_id
        self._selected_customer = {}
        self._transactions = []
        self._active_flags = []
        self._dead_cash_history = []
        self._transactions_page = 1
        self._dead_cash_page = 1
        self._has_more_transactions = False
        self._has_more_dead_cash = False
        self._profile_errors.clear()
        self.selectionChanged.emit()
        self.historyChanged.emit()
        self._publish_errors()

    def _load_profile(self, customer_id):
        try:
            result = self._service.get_customer_profile(customer_id)
            customer = self._customer_row(result["customer"])
            flags = [self._flag_row(row) for row in result["active_flags"]]
            transaction_page = result["transactions_page"]
            transactions = [self._transaction_row(row) for row in transaction_page["items"]]
            dead_cash_page = result["dead_cash_page"]
            dead_cash = [self._dead_cash_row(row) for row in dead_cash_page["items"]]
            # Publish all sections together: never mix two customers' data.
            self._selected_customer = customer
            self._selected_customer_id = customer["customer_id"]
            self._active_flags = flags
            self._transactions = transactions
            self._dead_cash_history = dead_cash
            self._transactions_page = transaction_page["page"]
            self._dead_cash_page = dead_cash_page["page"]
            self._has_more_transactions = transaction_page["has_more"]
            self._has_more_dead_cash = dead_cash_page["has_more"]
            self._profile_errors.clear()
            return True
        except Exception as exc:
            self._clear_profile(str(customer_id))
            self._profile_errors["profile"] = self._error_text(
                exc, "تعذر تحميل ملف العميل وحركاته. حاول التحديث مرة أخرى.",
            )
            return False
        finally:
            self.selectionChanged.emit()
            self.historyChanged.emit()
            self._publish_errors()

    @Slot()
    def refresh(self):
        if self._busy:
            return
        self._set_busy(True)
        try:
            self._load_types()
            self._load_customers(self._page)
            if self._selected_customer_id:
                self._load_profile(self._selected_customer_id)
        finally:
            self._set_busy(False)

    @Slot(str)
    def search(self, text):
        if self._busy:
            return
        self._search_text = (text or "").strip()
        self.filtersChanged.emit()
        self.loadPage(1)

    @Slot()
    def clearSearch(self):
        self.search("")

    @Slot(str, int, str)
    def setFilters(self, status, customer_type_id, balance_status):
        if self._busy:
            return
        self._status_filter = status
        self._customer_type_filter_id = customer_type_id
        self._balance_state_filter = balance_status
        self.filtersChanged.emit()
        self.loadPage(1)

    @Slot()
    def resetFilters(self):
        if self._busy:
            return
        self._search_text = ""
        self.setFilters("ALL", 0, "ALL")

    @Slot(int)
    def loadPage(self, page):
        if self._busy:
            return
        self._set_busy(True)
        try:
            self._clear_profile()
            self._load_customers(page)
        finally:
            self._set_busy(False)

    @Slot(str, result=bool)
    def selectCustomer(self, customer_id):
        if self._busy:
            return False
        self._set_busy(True)
        try:
            return self._load_profile(customer_id)
        finally:
            self._set_busy(False)

    @Slot()
    def clearSelection(self):
        if not self._busy:
            self._clear_profile()

    def _load_history_page(self, page, dead_cash=False):
        if self._busy or not self.hasSelection:
            return
        self._set_busy(True)
        error_key = "dead_cash" if dead_cash else "transactions"
        try:
            if dead_cash:
                result = self._service.get_dead_cash_history(self._selected_customer_id, page=page)
                rows = [self._dead_cash_row(row) for row in result["items"]]
                self._dead_cash_history = rows
                self._dead_cash_page = result["page"]
                self._has_more_dead_cash = result["has_more"]
            else:
                result = self._service.get_customer_transactions(self._selected_customer_id, page=page)
                rows = [self._transaction_row(row) for row in result["items"]]
                self._transactions = rows
                self._transactions_page = result["page"]
                self._has_more_transactions = result["has_more"]
            self._profile_errors.pop(error_key, None)
        except Exception as exc:
            if dead_cash:
                self._dead_cash_history = []
                self._has_more_dead_cash = False
            else:
                self._transactions = []
                self._has_more_transactions = False
            self._profile_errors[error_key] = self._error_text(
                exc,
                "تعذر تحميل سجل الديون المعدومة." if dead_cash else "تعذر تحميل حركات العميل.",
            )
        finally:
            self.historyChanged.emit()
            self._publish_errors()
            self._set_busy(False)

    @Slot(int)
    def loadTransactionsPage(self, page):
        self._load_history_page(page)

    @Slot(int)
    def loadDeadCashPage(self, page):
        self._load_history_page(page, dead_cash=True)

    @Slot(str, result=bool)
    def beginEdit(self, customer_id):
        if self._busy:
            return False
        self._set_busy(True)
        self._edit_customer = {}
        self._edit_customer_id = ""
        self._edit_original_values = None
        try:
            raw = self._service.get_customer_details(customer_id)
            self._edit_original_values = deepcopy({key: raw[key] for key in self.EDITABLE_FIELDS})
            self._edit_customer = self._customer_row(raw)
            self._edit_customer_id = self._edit_customer["customer_id"]
            self.clearActionMessage()
            return True
        except Exception as exc:
            self._set_action(self._error_text(exc, "تعذر تحميل بيانات العميل للتعديل."), False)
            return False
        finally:
            self.editChanged.emit()
            self._set_busy(False)

    def _after_commit(self, customer_id, message):
        # A refresh error must not turn a committed write into a failed action.
        self._set_action(message, True)
        self._load_customers(self._page)
        self._load_profile(str(customer_id))

    @Slot(str, str, str, int, str, result=bool)
    def createCustomer(self, name, mobile, place, customer_type_id, notes):
        if self._busy:
            return False
        self._set_busy(True)
        try:
            try:
                customer_id = self._service.create_customer(
                    name=name, mobile=mobile, place=place,
                    customer_type_id=customer_type_id or None, notes=notes, user_id=None,
                )
            except Exception as exc:
                self._set_action(self._error_text(exc, "تعذر حفظ العميل. حاول مرة أخرى."), False)
                return False
            self._page = 1
            self._after_commit(customer_id, "تم إضافة العميل بنجاح — رقم " + str(customer_id))
            return True
        finally:
            self._set_busy(False)

    @Slot(str, str, str, str, int, str, result=bool)
    def updateCustomer(self, customer_id, name, mobile, place, customer_type_id, notes):
        if self._busy:
            return False
        if customer_id != self._edit_customer_id or self._edit_original_values is None:
            self._set_action("أعد فتح نافذة تعديل العميل لتحميل بياناته الحالية.", False)
            return False
        self._set_busy(True)
        try:
            try:
                self._service.update_customer(
                    customer_id=customer_id, name=name,
                    mobile=mobile, place=place, customer_type_id=customer_type_id or None,
                    notes=notes, original_values=deepcopy(self._edit_original_values), user_id=None,
                )
            except Exception as exc:
                self._set_action(self._error_text(exc, "تعذر تعديل بيانات العميل."), False)
                return False
            self._edit_customer = {}
            self._edit_customer_id = ""
            self._edit_original_values = None
            self.editChanged.emit()
            self._after_commit(customer_id, "تم تعديل بيانات العميل بنجاح.")
            return True
        finally:
            self._set_busy(False)

    def _change_active(self, customer_id, active):
        if self._busy:
            return False
        self._set_busy(True)
        try:
            try:
                if active:
                    self._service.reactivate_customer(customer_id, user_id=None)
                else:
                    self._service.deactivate_customer(customer_id, user_id=None)
            except Exception as exc:
                self._set_action(self._error_text(exc, "تعذر تغيير حالة العميل."), False)
                return False
            self._after_commit(
                customer_id, "تم إعادة تفعيل العميل." if active else "تم تعطيل العميل.",
            )
            return True
        finally:
            self._set_busy(False)

    @Slot(str, result=bool)
    def deactivateCustomer(self, customer_id):
        return self._change_active(customer_id, False)

    @Slot(str, result=bool)
    def reactivateCustomer(self, customer_id):
        return self._change_active(customer_id, True)

    @Slot()
    def clearActionMessage(self):
        self._set_action("", False)
