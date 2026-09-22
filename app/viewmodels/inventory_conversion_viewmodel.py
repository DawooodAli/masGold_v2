from datetime import date, datetime
from decimal import Decimal

from PySide6.QtCore import (
    QObject,
    Property,
    Signal,
    Slot,
)

from services.inventory_conversion_service import (
    InventoryConversionService,
)


class InventoryConversionViewModel(QObject):

    dataChanged = Signal()
    actionChanged = Signal()
    busyChanged = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)

        self._service = (
            InventoryConversionService()
        )

        self._old_stock_items = []
        self._action_message = ""
        self._action_success = True
        self._busy = False

        self.refresh()

    @staticmethod
    def _qml_safe(value):
        if isinstance(value, Decimal):
            return float(value)

        if isinstance(value, datetime):
            return value.strftime(
                "%Y-%m-%d %H:%M:%S"
            )

        if isinstance(value, date):
            return value.strftime(
                "%Y-%m-%d"
            )

        if isinstance(value, dict):
            return {
                key: InventoryConversionViewModel._qml_safe(
                    item
                )
                for key, item in value.items()
            }

        if isinstance(value, (list, tuple)):
            return [
                InventoryConversionViewModel._qml_safe(
                    item
                )
                for item in value
            ]

        return value

    def _set_busy(self, value):
        value = bool(value)

        if self._busy == value:
            return

        self._busy = value
        self.busyChanged.emit()

    def _set_action(
        self,
        message,
        success,
    ):
        self._action_message = str(
            message or ""
        )
        self._action_success = bool(
            success
        )
        self.actionChanged.emit()

    @Property(
        "QVariantList",
        notify=dataChanged,
    )
    def oldStockItems(self):
        return self._old_stock_items

    @Property(
        int,
        notify=dataChanged,
    )
    def oldStockCount(self):
        return len(
            self._old_stock_items
        )

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
        bool,
        notify=busyChanged,
    )
    def busy(self):
        return self._busy

    @Slot()
    def refresh(self):
        self._set_busy(True)

        try:
            rows = (
                self._service
                .get_available_old_stock()
            )

            self._old_stock_items = (
                self._qml_safe(
                    rows
                )
            )

            self.dataChanged.emit()

        except Exception as exc:
            self._old_stock_items = []
            self.dataChanged.emit()

            self._set_action(
                f"تعذر تحميل البضاعة القديمة: {exc}",
                False,
            )

        finally:
            self._set_busy(False)

    @Slot()
    def clearActionMessage(self):
        self._set_action(
            "",
            True,
        )

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
        "QVariantList",
        result=bool,
    )
    def convertOldStockToNew(
        self,
        employee_id,
        item_type_id,
        karat_id,
        category_id,
        factory_id,
        weight_grams,
        size,
        barcode_value,
        notes,
        source_item_ids,
    ):
        self._set_busy(True)

        try:
            result = (
                self._service
                .convert_old_stock_to_new(
                    employee_id=(
                        employee_id
                    ),
                    item_type_id=(
                        item_type_id
                    ),
                    karat_id=karat_id,
                    category_id=(
                        category_id
                    ),
                    factory_id=(
                        factory_id
                    ),
                    weight_grams=(
                        weight_grams
                    ),
                    size=size,
                    barcode_value=(
                        barcode_value
                    ),
                    notes=notes,
                    source_item_ids=(
                        source_item_ids
                    ),
                )
            )

            self._set_action(
                (
                    "تم تحويل "
                    f"{result['source_item_count']} "
                    "قطعة من البضاعة القديمة إلى قطعة جديدة "
                    f"#{result['result_inventory_item_id']}. "
                    "وزن المصدر: "
                    f"{float(result['source_total_weight_grams']):.3f} جم، "
                    "وزن الناتج: "
                    f"{float(result['result_weight_grams']):.3f} جم، "
                    "الفرق: "
                    f"{float(result['weight_difference_grams']):+.3f} جم."
                ),
                True,
            )

            self.refresh()
            return True

        except ValueError as exc:
            message = str(exc)

            translations = {
                "Employee is required.":
                    "يجب اختيار الموظف المسؤول.",
                "Item type is required.":
                    "يجب اختيار الصنف.",
                "Karat is required.":
                    "يجب اختيار العيار.",
                "At least one old-stock source item is required.":
                    "اختر قطعة واحدة على الأقل من البضاعة القديمة.",
                "Weight must be a valid number.":
                    "أدخل وزناً صحيحاً.",
                "Weight must be greater than zero.":
                    "الوزن يجب أن يكون أكبر من صفر.",
                "Barcode already exists.":
                    "هذا الباركود مستخدم بالفعل.",
                "All selected old-stock pieces must have the same karat as the resulting new item.":
                    "كل القطع القديمة المحددة يجب أن تكون من نفس عيار القطعة الجديدة.",
                "One or more selected pieces are no longer available as old stock.":
                    "إحدى القطع القديمة المحددة لم تعد متاحة.",
            }

            self._set_action(
                translations.get(
                    message,
                    message,
                ),
                False,
            )

            return False

        except Exception as exc:
            self._set_action(
                f"تعذر تنفيذ التحويل: {exc}",
                False,
            )

            return False

        finally:
            self._set_busy(False)
