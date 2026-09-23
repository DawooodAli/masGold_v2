import sys
from pathlib import Path

from PySide6.QtCore import QObject, QUrl, Qt, Slot
from PySide6.QtGui import QGuiApplication, QFont
from PySide6.QtQml import QQmlApplicationEngine, qmlRegisterType, qmlTypeId
from PySide6.QtQuickControls2 import QQuickStyle

from app.session import AppSession
from app.viewmodels.authentication_viewmodel import AuthenticationViewModel


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


class ApplicationController(QObject):
    def __init__(self, app, *, authentication_service=None,
                 operational_factory=create_operational_viewmodels, qml_directory=None):
        super().__init__(app)
        self.app = app
        self.qml_directory = qml_directory or Path(__file__).resolve().parent / "qml"
        self.session = AppSession(self)
        self.authentication = AuthenticationViewModel(self.session, authentication_service, self)
        self._operational_factory = operational_factory
        self.operational_engine = None
        self.operational_viewmodels = {}
        self.login_engine = self._engine()
        self.login_engine.load(QUrl.fromLocalFile(str(self.qml_directory / "Login.qml")))
        if not self.login_engine.rootObjects():
            raise RuntimeError("Could not load Login.qml")
        self.authentication.authenticated.connect(self._open_operational)
        self.session.changed.connect(self._session_changed)
        app.aboutToQuit.connect(self.authentication.shutdown)

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
            for name, viewmodel in self.operational_viewmodels.items():
                viewmodel.setParent(engine)
                engine.rootContext().setContextProperty(name, viewmodel)
            engine.load(QUrl.fromLocalFile(str(self.qml_directory / "Main.qml")))
            if not engine.rootObjects():
                raise RuntimeError("Could not load Main.qml")
            self.login_engine.rootObjects()[0].hide()
        except Exception:
            self.authentication.logout()
            self.authentication.show_application_error()

    @Slot()
    def _session_changed(self):
        if self.session.authenticated:
            return
        for root in self.login_engine.rootObjects():
            root.show()
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
