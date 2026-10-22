import sys
from pathlib import Path

from PySide6.QtCore import QObject, QUrl, Qt, Slot, Property, Signal, QTimer
from PySide6.QtGui import QGuiApplication, QFont
from PySide6.QtQml import QQmlApplicationEngine, qmlRegisterType, qmlTypeId
from PySide6.QtQuickControls2 import QQuickStyle

from app.session import AppSession
from app.viewmodels.authentication_viewmodel import AuthenticationViewModel
from app.viewmodels.gold_price_viewmodel import GoldPriceViewModel
from app.viewmodels.gold_price_settings_viewmodel import GoldPriceSettingsViewModel


def create_operational_viewmodels():
    """Import and construct operational objects only after successful login."""
    from app.viewmodels.master_data_viewmodel import MasterDataViewModel
    from app.viewmodels.inventory_viewmodel import InventoryViewModel
    from app.viewmodels.barcode_viewmodel import BarcodeViewModel
    from app.viewmodels.old_gold_conversion_viewmodel import OldGoldConversionViewModel
    from app.viewmodels.customer_viewmodel import CustomerViewModel
    from app.viewmodels.customer_picker_viewmodel import CustomerPickerViewModel
    from app.viewmodels.stock_balance_viewmodel import StockBalanceViewModel
    from app.viewmodels.stocktake_viewmodel import StocktakeViewModel

    if qmlTypeId("MasGold.Customers", 1, 0, "CustomerPickerViewModel") < 0:
        qmlRegisterType(CustomerPickerViewModel, "MasGold.Customers", 1, 0,
                        "CustomerPickerViewModel")
    return {
        "masterDataVM": MasterDataViewModel(),
        "inventoryVM": InventoryViewModel(),
        "barcodeVM": BarcodeViewModel(),
        "oldGoldConversionVM": OldGoldConversionViewModel(),
        # The frozen Billing V1 view model (float money, no session authorization, writes billing_* history
        # tables) is deliberately NOT registered: Billing V2 (invoiceVM) is the only invoice path.
        "customerVM": CustomerViewModel(),
        "stockBalanceVM": StockBalanceViewModel(),       # بيان المخزون (loads when opened)
        "stocktakeVM": StocktakeViewModel(),             # الجرد بالباركود (transient session)
    }


def create_invoice_viewmodel(session):
    """Billing V2 view-model for one login; its draft is discarded on logout."""
    from app.viewmodels.invoice_viewmodel import InvoiceViewModel
    return InvoiceViewModel(session)


def create_printer_settings_viewmodel(session):
    """Settings → الطباعة: this workstation's Bill and Label printers (local, not shop data)."""
    from app.viewmodels.printer_settings_viewmodel import PrinterSettingsViewModel
    return PrinterSettingsViewModel(session)


def create_review_viewmodel(session):
    """مراجعات المدير indicator and page for one login."""
    from app.viewmodels.management_review_viewmodel import ManagementReviewViewModel
    return ManagementReviewViewModel(session)


def create_balance_viewmodel(session):
    """MainWindow balance strip (cash, counted old gold 21/18/24) for one login."""
    from app.viewmodels.operational_balance_viewmodel import OperationalBalanceViewModel
    return OperationalBalanceViewModel(session)


def create_dashboard_viewmodel(session):
    """الرئيسية: today's activity (read-only) for one login."""
    from app.viewmodels.dashboard_viewmodel import DashboardViewModel
    return DashboardViewModel(session)


def create_invoice_history_viewmodel(session):
    """Read-only invoice history and detail for one login."""
    from app.viewmodels.invoice_history_viewmodel import InvoiceHistoryViewModel
    return InvoiceHistoryViewModel(session)


def create_exchange_viewmodel(session):
    """استبدال (Exchange V1) workspace, opened from an invoice in History."""
    from app.viewmodels.exchange_viewmodel import ExchangeViewModel
    return ExchangeViewModel(session)


def create_return_viewmodel(session):
    """مرتجع (Return V1) workspace, opened from an invoice in History."""
    from app.viewmodels.return_viewmodel import ReturnViewModel
    return ReturnViewModel(session)


def create_correction_viewmodel(session):
    """تصحيح / عكس (Reversal / Correction V1), managers only, opened from an invoice in History."""
    from app.viewmodels.correction_viewmodel import CorrectionViewModel
    return CorrectionViewModel(session)


def fit_to_work_area(window, platform=None):
    """Open the main window inside the Windows WORK AREA (screen minus taskbar), never under it.

    Main.qml asks for 1280 x 800 with a 1080 x 680 minimum. On a laptop at 125-150 % display
    scaling, or with the taskbar visible, the usable height can be below 800 (or even 680)
    logical pixels, so the bottom of Billing ended up under the taskbar. The minimum is clamped
    to the available geometry and the window is maximized, which Windows sizes to the work area.
    Offscreen/test platforms keep the size the tests set."""
    screen = window.screen()
    if screen is None:
        return
    area = screen.availableGeometry()
    window.setMinimumWidth(min(window.minimumWidth(), area.width()))
    window.setMinimumHeight(min(window.minimumHeight(), area.height()))
    window.setWidth(min(window.width(), area.width()))
    window.setHeight(min(window.height(), area.height()))
    window.setX(area.x() + (area.width() - window.width()) // 2)
    window.setY(area.y() + (area.height() - window.height()) // 2)
    if (platform or QGuiApplication.platformName()) not in ("offscreen", "minimal"):
        window.showMaximized()


class ApplicationController(QObject):
    stateChanged = Signal()

    @Property(str, notify=stateChanged)
    def state(self):
        return self.authentication.stage

    def __init__(self, app, *, authentication_service=None,
                 operational_factory=create_operational_viewmodels, qml_directory=None,
                 gold_price_service=None, invoice_factory=create_invoice_viewmodel,
                 history_factory=create_invoice_history_viewmodel, review_factory=create_review_viewmodel,
                 printer_settings_factory=create_printer_settings_viewmodel,
                 balance_factory=create_balance_viewmodel, dashboard_factory=create_dashboard_viewmodel,
                 exchange_factory=create_exchange_viewmodel, return_factory=create_return_viewmodel,
                 correction_factory=create_correction_viewmodel, snapshot_factory=None, category_factory=None, cost_factory=None):
        super().__init__(app)
        self.app = app
        # English digits everywhere: typed Arabic-Indic / Persian digits become 0-9 before any field.
        from app.digit_filter import DigitKeyFilter
        self._digit_filter = DigitKeyFilter(self)
        app.installEventFilter(self._digit_filter)
        self.qml_directory = qml_directory or Path(__file__).resolve().parent / "qml"
        self.session = AppSession(self)
        self.authentication = AuthenticationViewModel(self.session, authentication_service, self)
        # Workers outlive operational QML engines during logout. Their session generation
        # is checked before publishing any result into the next user's window.
        self.gold_prices = GoldPriceViewModel(self.session, gold_price_service, self)
        self.gold_settings = GoldPriceSettingsViewModel(self.session, self)
        self._operational_factory = operational_factory
        self._invoice_factory = invoice_factory
        self._history_factory = history_factory
        self._review_factory = review_factory
        self._printer_settings_factory = printer_settings_factory
        self._balance_factory = balance_factory
        self._dashboard_factory = dashboard_factory
        self._exchange_factory = exchange_factory
        self._return_factory = return_factory
        self._correction_factory = correction_factory
        self._snapshot_factory = snapshot_factory
        self._category_factory = category_factory
        self._cost_factory = cost_factory
        self.operational_engine = None
        self.operational_viewmodels = {}
        self.login_engine = self._engine()
        self.login_engine.load(QUrl.fromLocalFile(str(self.qml_directory / "Login.qml")))
        if not self.login_engine.rootObjects():
            raise RuntimeError("Could not load Login.qml")
        self.setup_engine = self._engine()
        self.setup_engine.load(QUrl.fromLocalFile(str(self.qml_directory / "FirstRunOwnerSetup.qml")))
        if not self.setup_engine.rootObjects():
            raise RuntimeError("Could not load FirstRunOwnerSetup.qml")
        self.authentication.changed.connect(self.stateChanged)
        self.authentication.authenticated.connect(self._open_operational)
        self.session.changed.connect(self._session_changed)
        app.aboutToQuit.connect(self.authentication.shutdown)
        app.aboutToQuit.connect(self.gold_prices.shutdown)
        app.aboutToQuit.connect(self.gold_settings.shutdown)
        app.aboutToQuit.connect(self._shutdown_operational)
        QTimer.singleShot(0, self.authentication.checkStartup)

    def _engine(self):
        engine = QQmlApplicationEngine(self)
        engine.rootContext().setContextProperty("appSession", self.session)
        engine.rootContext().setContextProperty("authenticationVM", self.authentication)
        engine.quit.connect(self.app.quit)
        return engine

    def _attach_position_snapshots(self):
        """The owner's initial balance card (Settings) and the daily stocktaking sheet. The sheet is requested only in the real application (never offscreen / test platforms, so a test
        never writes to a database); a failure is logged by the worker and never blocks the application."""
        from app.viewmodels.position_snapshot_viewmodel import create_position_snapshot_viewmodel
        viewmodel = self.operational_viewmodels.get("positionSnapshotVM")
        if viewmodel is None:
            viewmodel = (self._snapshot_factory or create_position_snapshot_viewmodel)(self.session)
            self.operational_viewmodels["positionSnapshotVM"] = viewmodel
        if self.operational_viewmodels.get("categoryCostVM") is None:
            from app.viewmodels.category_cost_viewmodel import create_category_cost_viewmodel
            self.operational_viewmodels["categoryCostVM"] = (self._cost_factory or create_category_cost_viewmodel)(self.session)
        categories = self.operational_viewmodels.get("cashCategoryVM")
        if categories is None:
            from app.viewmodels.cash_category_viewmodel import create_cash_category_viewmodel
            self.operational_viewmodels["cashCategoryVM"] = (self._category_factory or create_cash_category_viewmodel)(self.session)
        if QGuiApplication.platformName() not in ("offscreen", "minimal"):
            viewmodel.ensureDaily()

    @Slot()
    def _open_operational(self):
        if not self.session.authenticated or self.operational_engine is not None:
            return
        engine = self._engine()
        self.operational_engine = engine
        try:
            self.operational_viewmodels = self._operational_factory()
            self.operational_viewmodels.setdefault("invoiceVM", self._invoice_factory(self.session))
            self.operational_viewmodels.setdefault("invoiceHistoryVM", self._history_factory(self.session))
            self.operational_viewmodels.setdefault("reviewsVM", self._review_factory(self.session))
            self.operational_viewmodels.setdefault("printerSettingsVM", self._printer_settings_factory(self.session))
            self.operational_viewmodels.setdefault("balancesVM", self._balance_factory(self.session))
            self.operational_viewmodels.setdefault("dashboardVM", self._dashboard_factory(self.session))
            self._attach_position_snapshots()
            customer_page = self.operational_viewmodels.get("customerVM")
            if hasattr(customer_page, "attach_session"):
                customer_page.attach_session(self.session)       # Customers page reads need CUSTOMERS_VIEW
            master_data = self.operational_viewmodels.get("masterDataVM")
            if hasattr(master_data, "attach_session"):
                master_data.attach_session(self.session)         # mutations need MASTER_DATA_MANAGE
            conversion = self.operational_viewmodels.get("oldGoldConversionVM")
            if hasattr(conversion, "attach_session"):
                conversion.attach_session(self.session)          # OLD_GOLD_MANAGE + the session actor
            inventory = self.operational_viewmodels.get("inventoryVM")
            if hasattr(inventory, "attach_session"):
                inventory.attach_session(self.session)           # INVENTORY_MANAGE + the session actor
            invoice_vm = self.operational_viewmodels["invoiceVM"]
            history = self.operational_viewmodels.get("invoiceHistoryVM")
            if hasattr(invoice_vm, "finalized") and hasattr(history, "refreshInvoice"):
                invoice_vm.finalized.connect(history.refreshInvoice)
            balances = self.operational_viewmodels["balancesVM"]
            if hasattr(invoice_vm, "finalized") and hasattr(balances, "refresh"):
                invoice_vm.finalized.connect(balances.refresh)       # the ledgers just changed
            dashboard = self.operational_viewmodels["dashboardVM"]
            if hasattr(invoice_vm, "finalized") and hasattr(dashboard, "refresh"):
                invoice_vm.finalized.connect(dashboard.refresh)      # today's figures just changed
            reviews = self.operational_viewmodels["reviewsVM"]
            if hasattr(invoice_vm, "finalized") and hasattr(reviews, "reloadAccounts"):
                invoice_vm.finalized.connect(reviews.reloadAccounts)   # wallet / bank balances too
            self.operational_viewmodels.setdefault("exchangeVM", self._exchange_factory(self.session))
            exchange = self.operational_viewmodels["exchangeVM"]
            if hasattr(exchange, "finalized"):
                # Real money and stock changed (never a sale: the dashboard is not refreshed).
                if hasattr(balances, "refresh"):
                    exchange.finalized.connect(balances.refresh)
                if hasattr(reviews, "reloadAccounts"):
                    exchange.finalized.connect(reviews.reloadAccounts)
                history = self.operational_viewmodels.get("invoiceHistoryVM")
                if hasattr(history, "refreshInvoice"):
                    exchange.finalized.connect(history.refreshInvoice)
            self.operational_viewmodels.setdefault("returnVM", self._return_factory(self.session))
            returned = self.operational_viewmodels["returnVM"]
            if hasattr(returned, "finalized"):
                # Real money, stock and old gold changed; a Return is never a sale (no dashboard refresh).
                if hasattr(balances, "refresh"):
                    returned.finalized.connect(balances.refresh)
                if hasattr(reviews, "reloadAccounts"):
                    returned.finalized.connect(reviews.reloadAccounts)
                if hasattr(reviews, "refresh"):
                    returned.finalized.connect(reviews.refresh)         # a new inspection review
                history = self.operational_viewmodels.get("invoiceHistoryVM")
                if hasattr(history, "refreshInvoice"):
                    returned.finalized.connect(history.refreshInvoice)
            self.operational_viewmodels.setdefault("correctionVM", self._correction_factory(self.session))
            from app.viewmodels.customer_account_viewmodel import CustomerAccountViewModel
            account = CustomerAccountViewModel(self.session)
            self.operational_viewmodels["customerAccountVM"] = account
            if hasattr(history, "refresh"):
                account.finalized.connect(history.refresh)  # the open detail's later customer-account activity
            if hasattr(customer_page, "deadCashFinalized"):
                # A write-off / reversal changed the customer ledger (never a money ledger).
                customer_page.deadCashFinalized.connect(account.refresh)
            for target in (balances, reviews, self.operational_viewmodels.get("customerVM")):
                if hasattr(target, "refresh"):
                    account.finalized.connect(target.refresh)
            corrected = self.operational_viewmodels["correctionVM"]
            if hasattr(corrected, "finalized"):
                corrected.finalized.connect(account.refresh)
            for source in (invoice_vm, exchange, returned):
                if hasattr(source, "finalized"):
                    source.finalized.connect(account.refresh)
            customers = self.operational_viewmodels.get("customerVM")
            if hasattr(customers, "selectionChanged"):
                customers.selectionChanged.connect(lambda: account.followSelection(customers.selectedCustomerId))
            if hasattr(customers, "refresh"):
                for source in (invoice_vm, exchange, returned, corrected):
                    if hasattr(source, "finalized"):
                        source.finalized.connect(customers.refresh)
            if hasattr(reviews, "inspectionResolved"):
                if hasattr(history, "refresh"):
                    reviews.inspectionResolved.connect(history.refresh)
                reviews.inspectionResolved.connect(account.refresh)
                if hasattr(customers, "refresh"):
                    reviews.inspectionResolved.connect(customers.refresh)
            # Scrap changed (032): an Exchange put a piece into Scrap (held), or a manager returned it from
            # Scrap to stock / committed it. Every loaded Scrap or stock view reloads from the database at
            # once — the top-bar physical balance, the F7 / conversion available grams and the stock lists.
            scrap_views = [(balances, "refresh"), (invoice_vm, "refreshReference"),
                           (self.operational_viewmodels.get("oldGoldConversionVM"), "refreshBalances"),
                           (self.operational_viewmodels.get("inventoryVM"), "refresh")]
            stock_balance = self.operational_viewmodels.get("stockBalanceVM")
            # A Return (033) also puts its identified pieces into Scrap (held).
            finalized_signals = [getattr(exchange, "finalized", None), getattr(returned, "finalized", None)]
            finalized_signals = [getattr(exchange, "finalized", None), getattr(returned, "finalized", None)]
            for source in (getattr(reviews, "inspectionResolved", None), *finalized_signals):
                if source is None:
                    continue
                for target, slot in scrap_views:
                    if target is not None and hasattr(target, slot) and not (
                            source in finalized_signals and target is balances):
                        source.connect(getattr(target, slot))         # finalize → balances is wired above
                if hasattr(stock_balance, "refresh"):
                    # بيان المخزون loads when opened; once loaded it follows every change.
                    source.connect(lambda *_: stock_balance.loaded and stock_balance.refresh())
            if hasattr(corrected, "finalized"):
                # Money, customer ledger, stock and old gold may all have changed.
                for target in (balances, reviews):
                    for slot in ("refresh", "reloadAccounts"):
                        if hasattr(target, slot):
                            corrected.finalized.connect(getattr(target, slot))
                history = self.operational_viewmodels.get("invoiceHistoryVM")
                if hasattr(history, "refreshInvoice"):
                    corrected.finalized.connect(history.refreshInvoice)
            from app.viewmodels.financial_accounts_viewmodel import FinancialAccountsViewModel
            accounts = self.operational_viewmodels.setdefault("financialAccountsVM",
                                                              FinancialAccountsViewModel(self.session))
            # Every finalize that moves real money refreshes الحسابات المالية (off-page: marked stale).
            for source in (invoice_vm, exchange, returned, account, corrected):
                if hasattr(source, "finalized") and hasattr(accounts, "refresh"):
                    source.finalized.connect(accounts.refresh)
            if hasattr(accounts, "finalized"):
                for target, slot in ((balances, "refresh"), (reviews, "reloadAccounts")):
                    if hasattr(target, slot):
                        accounts.finalized.connect(getattr(target, slot))
            # Settings → المستخدمون والصلاحيات (USERS_MANAGE, re-checked by the service on every call).
            from app.viewmodels.user_admin_viewmodel import UserAdminViewModel
            self.operational_viewmodels.setdefault("userAdminVM", UserAdminViewModel(self.session))
            # Purchasing & Suppliers V1: each right (view / manage / edit / post / intake) is re-checked by its
            # service inside the transaction; the view models only format and expose UI hints.
            from app.viewmodels.supplier_viewmodel import SupplierViewModel
            from app.viewmodels.supplier_purchase_viewmodel import SupplierPurchaseViewModel
            suppliers = self.operational_viewmodels.setdefault("supplierVM", SupplierViewModel(self.session))
            purchases = self.operational_viewmodels.setdefault("supplierPurchaseVM",
                                                              SupplierPurchaseViewModel(self.session))
            if hasattr(purchases, "invoicePosted") and hasattr(suppliers, "refresh"):
                purchases.invoicePosted.connect(suppliers.refresh)      # the supplier ledgers just changed
            # A supplier making payment / its reversal moved REAL money (Main Safe or a bank account): the top-bar
            # balances, the wallet / bank balances and الحسابات المالية reload from the database at once. The signals are
            # emitted only after the transaction committed; every target keeps its latest request, so a refresh asked
            # while another load is running still ends with a fresh read. (No local arithmetic: balances are re-read.)
            for money_changed in (getattr(suppliers, "paymentPosted", None), getattr(suppliers, "reversalDone", None)):
                if money_changed is None:
                    continue
                for target, slot in ((balances, "refresh"), (reviews, "reloadAccounts"), (accounts, "refresh")):
                    if hasattr(target, slot):
                        money_changed.connect(getattr(target, slot))
            # A supplier gold settlement / its reversal moved REAL Scrap (no cash): the Scrap consumers reload from the database at once.
            for scrap_changed in (getattr(suppliers, "settlementPosted", None), getattr(suppliers, "settlementReversed", None)):
                if scrap_changed is None:
                    continue
                for target, slot in ((balances, "refresh"), (invoice_vm, "refreshReference"),
                                     (self.operational_viewmodels.get("oldGoldConversionVM"), "refreshBalances")):
                    if target is not None and hasattr(target, slot):
                        scrap_changed.connect(getattr(target, slot))
            if hasattr(purchases, "stockChanged"):
                # A supplier-intake piece is a real new inventory item (+ barcode): stock views reload.
                inventory_vm = self.operational_viewmodels.get("inventoryVM")
                if hasattr(inventory_vm, "refresh"):
                    purchases.stockChanged.connect(inventory_vm.refresh)
                if hasattr(stock_balance, "refresh"):
                    purchases.stockChanged.connect(lambda *_: stock_balance.loaded and stock_balance.refresh())
            # Market-trip custody (037, OWNER only; the service re-checks the right in every transaction). Cash left or came back
            # to MAIN_SAFE and Scrap left or came back to the counted balance: the top-bar balances, the wallet / bank balances,
            # الحسابات المالية and every Scrap consumer reload from the database once the transaction has committed.
            from app.viewmodels.market_trip_viewmodel import MarketTripViewModel
            trips = self.operational_viewmodels.setdefault("marketTripVM", MarketTripViewModel(self.session))
            if hasattr(trips, "supplierPaymentChanged") and hasattr(suppliers, "refresh"):
                trips.supplierPaymentChanged.connect(suppliers.refresh)         # the supplier ledger / payment history just changed
            if hasattr(suppliers, "holdingChanged") and hasattr(trips, "refresh"):
                suppliers.holdingChanged.connect(trips.refresh)                 # a whole lot came back from an intermediary: the custody views reload
            if hasattr(trips, "custodyChanged"):
                for target, slot in ((balances, "refresh"), (reviews, "reloadAccounts"), (accounts, "refresh"),
                                     (invoice_vm, "refreshReference"),
                                     (self.operational_viewmodels.get("oldGoldConversionVM"), "refreshBalances")):
                    if target is not None and hasattr(target, slot):
                        trips.custodyChanged.connect(getattr(target, slot))
            # Marketer custody account (055): the signals are emitted only after the transaction committed, and every target keeps its latest
            # request, so a refresh asked while another read is running still ends with a read that includes the committed write.
            marketer = getattr(suppliers, "marketer", None)
            if marketer is not None:
                # cash moved through Main Safe / Scrap moved: the top-bar balances, wallet / bank balances, الحسابات المالية and the Scrap consumers
                for target, slot in ((balances, "refresh"), (reviews, "reloadAccounts"), (accounts, "refresh"), (invoice_vm, "refreshReference"),
                                     (self.operational_viewmodels.get("oldGoldConversionVM"), "refreshBalances")):
                    if target is not None and hasattr(target, slot):
                        marketer.balancesChanged.connect(getattr(target, slot))
                if hasattr(reviews, "loadGoldPosition"):
                    marketer.positionChanged.connect(reviews.loadGoldPosition)      # shop-owned gold moved / changed place
            if hasattr(invoice_vm, "attach_price_source"):
                invoice_vm.attach_price_source(self.gold_prices)
            # Barcode fields map physical scan codes (independent of the Windows keyboard layout).
            from app.barcode_input import BarcodeInput
            self.barcode_input = BarcodeInput(engine)
            engine.rootContext().setContextProperty("barcodeInput", self.barcode_input)
            engine.rootContext().setContextProperty("goldPriceVM", self.gold_prices)
            engine.rootContext().setContextProperty("goldSettingsVM", self.gold_settings)
            for name, viewmodel in self.operational_viewmodels.items():
                viewmodel.setParent(engine)
                engine.rootContext().setContextProperty(name, viewmodel)
            engine.load(QUrl.fromLocalFile(str(self.qml_directory / "Main.qml")))
            if not engine.rootObjects():
                raise RuntimeError("Could not load Main.qml")
            fit_to_work_area(engine.rootObjects()[0])
            reviews = self.operational_viewmodels.get("reviewsVM")
            if hasattr(reviews, "activate"):
                reviews.activate()                  # مراجعات المدير • N stays current
            if hasattr(balances, "activate"):
                balances.activate()
            if hasattr(dashboard, "activate"):
                dashboard.activate()
        except Exception:
            self.authentication.logout()
            self.authentication.show_application_error()

    @Slot()
    def _shutdown_operational(self):
        # Wait for background work before its view-model and engine are destroyed.
        for viewmodel in self.operational_viewmodels.values():
            if hasattr(viewmodel, "shutdown"):
                viewmodel.shutdown()

    @Slot()
    def _session_changed(self):
        if self.session.authenticated:
            return
        self._shutdown_operational()
        if self.operational_engine is not None:
            for root in self.operational_engine.rootObjects():
                root.hide()
            # Discard all operational drafts, selections, dialogs and VM caches.
            self.operational_engine.deleteLater()
            self.operational_engine = None
            self.operational_viewmodels = {}


def main():
    QQuickStyle.setStyle("Material")
    app = QGuiApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    app.setApplicationName("مجوهرات ماس")
    app.setFont(QFont("Cairo", 11))
    app.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
    controller = ApplicationController(app)
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
