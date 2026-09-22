import sys
from pathlib import Path

from PySide6.QtCore import QUrl, Qt
from PySide6.QtGui import QGuiApplication, QFont
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuickControls2 import QQuickStyle

from app.viewmodels.master_data_viewmodel import (
    MasterDataViewModel,
)
from app.viewmodels.inventory_viewmodel import (
    InventoryViewModel,
)
from app.viewmodels.barcode_viewmodel import (
    BarcodeViewModel,
)
from app.viewmodels.old_gold_conversion_viewmodel import (
    OldGoldConversionViewModel,
)
from app.viewmodels.billing_viewmodel import (
    BillingViewModel,
)


def main():
    QQuickStyle.setStyle("Material")

    app = QGuiApplication(sys.argv)

    app.setApplicationName(
        "مجوهرات ماس"
    )

    app.setFont(
        QFont(
            "Cairo",
            11,
        )
    )

    app.setLayoutDirection(
        Qt.LayoutDirection.RightToLeft
    )

    engine = QQmlApplicationEngine()

    master_data_vm = (
        MasterDataViewModel()
    )

    inventory_vm = (
        InventoryViewModel()
    )

    barcode_vm = (
        BarcodeViewModel()
    )

    old_gold_conversion_vm = (
        OldGoldConversionViewModel()
    )

    billing_vm = (
        BillingViewModel()
    )

    engine.rootContext().setContextProperty(
        "masterDataVM",
        master_data_vm,
    )

    engine.rootContext().setContextProperty(
        "inventoryVM",
        inventory_vm,
    )

    engine.rootContext().setContextProperty(
        "barcodeVM",
        barcode_vm,
    )

    engine.rootContext().setContextProperty(
        "oldGoldConversionVM",
        old_gold_conversion_vm,
    )

    engine.rootContext().setContextProperty(
        "billingVM",
        billing_vm,
    )

    project_root = (
        Path(__file__).resolve().parent
    )

    qml_file = (
        project_root
        / "qml"
        / "Main.qml"
    )

    engine.load(
        QUrl.fromLocalFile(
            str(qml_file)
        )
    )

    if not engine.rootObjects():
        raise RuntimeError(
            "Could not load Main.qml"
        )

    sys.exit(
        app.exec()
    )


if __name__ == "__main__":
    main()
