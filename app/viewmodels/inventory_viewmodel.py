from decimal import Decimal

from PySide6.QtCore import (
    QObject,
    Property,
    Signal,
    Slot,
)

from services.inventory_service import InventoryService
from datetime import date, datetime

class InventoryViewModel(QObject):

    dataChanged = Signal()
    errorChanged = Signal()
    actionChanged = Signal()

    def __init__(self):
        super().__init__()

        self._service = InventoryService()

        self._items = []
        self._summary = {}

        self._search_text = ""
        self._error_message = ""
        self._action_message = ""
        self._action_success = False
        self.refresh()


    # ========================================================
    # QML SAFE VALUES
    # ========================================================

    @staticmethod
    def _qml_safe(value):

        """
        Convert Python/MySQL values to QML-safe types.
        """

        if isinstance(value, Decimal):
            return float(value)

        if isinstance(value, datetime):
            return value.strftime("%Y-%m-%d %H:%M:%S")

        if isinstance(value, date):
            return value.strftime("%Y-%m-%d")

        if isinstance(value, dict):
            return {
                key: InventoryViewModel._qml_safe(item)
                for key, item in value.items()
            }

        if isinstance(value, (list, tuple)):
            return [
                InventoryViewModel._qml_safe(item)
                for item in value
            ]

        return value
    @staticmethod
    def _translate_create_error(exc):

        message = str(exc)

        translations = {
            "Employee is required.":
                "يجب اختيار الموظف.",

            "Employee is not active or does not exist.":
                "الموظف غير موجود أو غير نشط.",

            "Item type is required.":
                "يجب اختيار الصنف.",

            "Item type is not active or does not exist.":
                "الصنف غير موجود أو غير نشط.",

            "Karat is required.":
                "يجب اختيار العيار.",

            "Karat is not active or does not exist.":
                "العيار غير موجود أو غير نشط.",

            "Category is not active or does not exist.":
                "الفئة غير موجودة أو غير نشطة.",

            "Factory is not active or does not exist.":
                "المصنع غير موجود أو غير نشط.",

            "Weight is required.":
                "الوزن مطلوب.",

            "Weight must be a valid number.":
                "الوزن غير صحيح.",

            "Weight must be greater than zero.":
                "الوزن يجب أن يكون أكبر من صفر.",

            "Invalid stock classification.":
                "نوع البضاعة غير صحيح.",

            "Barcode already exists.":
                "هذا الباركود مستخدم بالفعل.",

            "Barcode is too long.":
                "الباركود أطول من الحد المسموح.",

            "Size is too long.":
                "المقاس أطول من الحد المسموح.",

            "Notes are too long.":
                "الملاحظات أطول من الحد المسموح.",

            "ITEM_CREATED movement type is not configured.":
                "نوع حركة إدخال المخزون غير مهيأ في قاعدة البيانات.",
        }

        return translations.get(
            message,
            "تعذر إدخال القطعة: " + message,
        )

    # ========================================================
    # PROPERTIES
    # ========================================================
    @Property(
        str,
        notify=actionChanged,
    )
    def actionMessage(self):
        return self._action_message

    @Property(
        bool,
        notify=actionChanged,
    )
    def actionSuccess(self):
        return self._action_success
    @Property(
        "QVariantList",
        notify=dataChanged,
    )
    def items(self):
        return self._items


    @Property(
        "QVariantMap",
        notify=dataChanged,
    )
    def summary(self):
        return self._summary


    @Property(
        int,
        notify=dataChanged,
    )
    def itemCount(self):
        return int(
            self._summary.get(
                "total_items",
                0,
            )
            or 0
        )


    @Property(
        int,
        notify=dataChanged,
    )
    def newStockCount(self):
        return int(
            self._summary.get(
                "new_stock_items",
                0,
            )
            or 0
        )


    @Property(
        int,
        notify=dataChanged,
    )
    def oldStockCount(self):
        return int(
            self._summary.get(
                "old_stock_items",
                0,
            )
            or 0
        )


    @Property(
        int,
        notify=dataChanged,
    )
    def withoutBarcodeCount(self):
        return int(
            self._summary.get(
                "without_barcode_items",
                0,
            )
            or 0
        )


    @Property(
        float,
        notify=dataChanged,
    )
    def totalWeight(self):
        return float(
            self._summary.get(
                "total_weight_grams",
                0,
            )
            or 0
        )


    @Property(
        str,
        notify=errorChanged,
    )
    def errorMessage(self):
        return self._error_message


    # ========================================================
    # ACTIONS
    # ========================================================

    @Slot(str)
    def search(self, text):

        self._search_text = (
            text or ""
        ).strip()

        self.refresh()


    @Slot()
    def clearSearch(self):

        self._search_text = ""

        self.refresh()


    @Slot()
    def refresh(self):

        try:

            self._items = self._qml_safe(
                self._service.get_stock_items(
                    search=self._search_text,
                )
                or []
            )

            self._summary = self._qml_safe(
                self._service.get_stock_summary()
                or {}
            )

            self._error_message = ""

            self.dataChanged.emit()
            self.errorChanged.emit()


        except Exception as exc:

            self._items = []
            self._summary = {}

            self._error_message = (
                "تعذر تحميل المخزون: "
                + str(exc)
            )

            self.dataChanged.emit()
            self.errorChanged.emit()

    @Slot(
        int,
        int,
        int,
        int,
        int,
        str,
        str,
        str,
        str,
        str,
        result=bool,
    )
    def createInventoryItem(
            self,
            employee_id,
            item_type_id,
            karat_id,
            category_id,
            factory_id,
            weight,
            size,
            stock_classification,
            barcode,
            notes,
    ):

        try:

            inventory_item_id = (
                self._service.create_inventory_item(
                    employee_id=employee_id,
                    item_type_id=item_type_id,
                    karat_id=karat_id,
                    category_id=category_id,
                    factory_id=factory_id,
                    weight_grams=weight,
                    size=size,
                    stock_classification=(
                        stock_classification
                    ),
                    barcode=barcode,
                    notes=notes,

                    # Login/session comes later.
                    user_id=None,
                )
            )

            self._action_message = (
                    "تم إدخال القطعة بنجاح — رقم القطعة #"
                    + str(inventory_item_id)
            )

            self._action_success = True

            self.refresh()

            self.actionChanged.emit()

            return True


        except Exception as exc:

            self._action_message = (
                self._translate_create_error(
                    exc
                )
            )

            self._action_success = False

            self.actionChanged.emit()

            return False

    @Slot()
    def clearActionMessage(self):

        self._action_message = ""
        self._action_success = False

        self.actionChanged.emit()