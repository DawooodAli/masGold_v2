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
    from app.viewmodels.billing_viewmodel import BillingViewModel
    from app.viewmodels.customer_viewmodel import CustomerViewModel
    from app.viewmodels.customer_picker_viewmodel import CustomerPickerViewModel

    if qmlTypeId("MasGold.Customers", 1, 0, "CustomerPickerViewModel") < 0:
        qmlRegisterType(CustomerPickerViewModel, "MasGold.Customers", 1, 0,
                        "CustomerPickerViewModel")
    return {
        "masterDataVM": MasterDataViewModel(),
        "inventoryVM": InventoryViewModel(),
        "barcodeVM": BarcodeViewModel(),
        "oldGoldConversionVM": OldGoldConversionViewModel(),
        "billingVM": BillingViewModel(),
        "customerVM": CustomerViewModel(),
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
                 correction_factory=create_correction_viewmodel):
        super().__init__(app)
        self.app = app
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
            invoice_vm = self.operational_viewmodels["invoiceVM"]
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
                reviews.inspectionResolved.connect(account.refresh)
                if hasattr(customers, "refresh"):
                    reviews.inspectionResolved.connect(customers.refresh)
            if hasattr(corrected, "finalized"):
                # Money, customer ledger, stock and old gold may all have changed.
                for target in (balances, reviews):
                    for slot in ("refresh", "reloadAccounts"):
                        if hasattr(target, slot):
                            corrected.finalized.connect(getattr(target, slot))
                history = self.operational_viewmodels.get("invoiceHistoryVM")
                if hasattr(history, "refreshInvoice"):
                    corrected.finalized.connect(history.refreshInvoice)
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
