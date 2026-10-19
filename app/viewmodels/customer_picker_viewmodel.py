"""Independent, read-only customer selection state for reusable QML pickers."""

from PySide6.QtCore import QObject, Property, Signal, Slot

from app.viewmodels.customer_presentation import customer_row, error_text
from services.customer_service import CustomerService
import app.viewmodels.reports_viewmodel  # noqa: F401  (registers MasGold.Reports for QML: the entry point is not edited)


class CustomerPickerViewModel(QObject):
    resultsChanged = Signal()
    searchChanged = Signal()
    configurationChanged = Signal()
    selectionChanged = Signal()
    busyChanged = Signal()
    errorChanged = Signal()
    selectionInvalidated = Signal(str, str)

    PAGE_SIZE = 20

    def __init__(self, parent=None, service=None):
        super().__init__(parent)
        self._default_service = service is None
        self._session = None
        self._service = service if service is not None else CustomerService()
        self._results = []
        self._search_text = ""
        self._allow_inactive = False
        self._page = 1
        self._has_more = False
        self._selected_customer = {}
        self._selected_customer_id = ""
        self._busy = False
        self._error_message = ""
        self._has_searched = False
        # QML can create/configure a closed picker without touching MySQL.

    def _get_session(self):
        return self._session

    def _set_session(self, session):
        """Only the real AppSession object is accepted; it carries no settable identity."""
        from app.session import AppSession
        if isinstance(session, AppSession) and session is not self._session:
            self._session = session
            if self._default_service:
                self._service = CustomerService(session)

    session = Property(QObject, _get_session, _set_session)

    @Property("QVariantList", notify=resultsChanged)
    def results(self):
        return self._results

    @Property(str, notify=searchChanged)
    def searchText(self):
        return self._search_text

    @Property(int, notify=resultsChanged)
    def page(self):
        return self._page

    @Property(int, constant=True)
    def pageSize(self):
        return self.PAGE_SIZE

    @Property(bool, notify=resultsChanged)
    def hasMore(self):
        return self._has_more

    @Property("QVariantMap", notify=selectionChanged)
    def selectedCustomer(self):
        return self._selected_customer

    @Property(str, notify=selectionChanged)
    def selectedCustomerId(self):
        return self._selected_customer_id

    @Property(bool, notify=selectionChanged)
    def hasSelection(self):
        return bool(self._selected_customer_id)

    @Property(bool, notify=busyChanged)
    def busy(self):
        return self._busy

    @Property(str, notify=errorChanged)
    def errorMessage(self):
        return self._error_message

    def _set_busy(self, value):
        if self._busy != value:
            self._busy = value
            self.busyChanged.emit()

    def _set_error(self, message):
        if self._error_message != message:
            self._error_message = message
            self.errorChanged.emit()

    def _publish_selection(self, raw):
        selected = customer_row(raw)
        # The presentation helper preserves BIGINT identities as strings.
        self._selected_customer_id = selected["customer_id"]
        self._selected_customer = selected
        self.selectionChanged.emit()

    def _invalidate_inactive_selection(self):
        customer_id = self._selected_customer_id
        reason = "تم إلغاء اختيار العميل لأنه غير نشط. اختر عميلاً نشطاً."
        self.clearSelection()
        self._set_error(reason)
        self.selectionInvalidated.emit(customer_id, reason)

    def _revalidate_selection(self):
        if not self._selected_customer_id:
            return
        if not self._allow_inactive and not self._selected_customer.get("is_active"):
            self._invalidate_inactive_selection()
            return
        try:
            # Inspect fresh status to distinguish a confirmed inactive record
            # from a transient database error, without fetching any histories.
            raw = self._service.get_customer_for_selection(
                self._selected_customer_id, allow_inactive=True,
            )
            if not self._allow_inactive and not raw["is_active"]:
                self._invalidate_inactive_selection()
            else:
                self._publish_selection(raw)
        except Exception as exc:
            # A failed read cannot prove that the confirmed choice is invalid.
            self._set_error(error_text(exc, "تعذر التحقق من بيانات العميل المختار. حاول التحديث مرة أخرى."))

    def _load_results(self, page):
        self._has_searched = True
        try:
            result = self._service.search_customers(
                search=self._search_text,
                status="ALL" if self._allow_inactive else "ACTIVE",
                page=page,
                page_size=self.PAGE_SIZE,
            )
            self._results = [customer_row(row) for row in result["items"]]
            self._page = result["page"]
            self._has_more = result["has_more"]
        except Exception as exc:
            self._results = []
            self._has_more = False
            self._set_error(error_text(exc, "تعذر البحث عن العملاء. حاول مرة أخرى."))
        finally:
            self.resultsChanged.emit()

    def _load(self, page, revalidate=False):
        if self._busy:
            return
        self._set_busy(True)
        self._set_error("")
        try:
            self._load_results(page)
            if revalidate:
                self._revalidate_selection()
        finally:
            self._set_busy(False)

    @Slot(str)
    def search(self, text):
        if self._busy:
            return
        # Normalization and matching stay in the existing service/repository.
        if self._search_text != text:
            self._search_text = text
            self.searchChanged.emit()
        self._load(1)

    @Slot()
    def refresh(self):
        self._load(self._page, revalidate=True)

    @Slot(int)
    def loadPage(self, page):
        self._load(page)

    @Slot(bool)
    def setAllowInactive(self, allow_inactive):
        allow_inactive = bool(allow_inactive)
        if self._busy or self._allow_inactive == allow_inactive:
            return
        self._allow_inactive = allow_inactive
        self.configurationChanged.emit()
        self._set_busy(True)
        self._set_error("")
        try:
            self._revalidate_selection()
            if self._has_searched:
                self._load_results(1)
        finally:
            self._set_busy(False)

    @Property(bool, notify=configurationChanged)
    def allowInactive(self):
        return self._allow_inactive

    @allowInactive.setter
    def allowInactive(self, value):
        self.setAllowInactive(value)

    @Slot(str, result=bool)
    def selectCustomer(self, customer_id):
        if self._busy:
            return False
        self._set_busy(True)
        self._set_error("")
        try:
            raw = self._service.get_customer_for_selection(
                customer_id, allow_inactive=self._allow_inactive,
            )
            self._publish_selection(raw)
            return True
        except Exception as exc:
            # Failed replacement leaves the previously confirmed choice intact.
            self._set_error(error_text(exc, "تعذر اختيار العميل. حاول مرة أخرى."))
            return False
        finally:
            self._set_busy(False)

    @Slot()
    def clearSelection(self):
        if self._selected_customer_id or self._selected_customer:
            self._selected_customer_id = ""
            self._selected_customer = {}
            self.selectionChanged.emit()
