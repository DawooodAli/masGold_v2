from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from PySide6.QtCore import (
    QObject,
    Property,
    Signal,
    Slot,
)

from services.old_gold_conversion_service import (
    OldGoldConversionService,
)


class OldGoldConversionViewModel(QObject):

    dataChanged = Signal()
    actionChanged = Signal()
    busyChanged = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)

        self._service = OldGoldConversionService()
        self._session = None

        self._balances = []
        self._action_message = ""
        self._action_success = True
        self._busy = False

        self.refreshBalances()

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
                key: OldGoldConversionViewModel._qml_safe(
                    item
                )
                for key, item in value.items()
            }

        if isinstance(value, (list, tuple)):
            return [
                OldGoldConversionViewModel._qml_safe(
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

    def _set_action(self, message, success):
        self._action_message = str(message or "")
        self._action_success = bool(success)
        self.actionChanged.emit()

    @Property(
        "QVariantList",
        notify=dataChanged,
    )
    def balances(self):
        return self._balances

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
    def refreshBalances(self):
        self._set_busy(True)

        try:
            rows = self._service.get_balances()

            self._balances = self._qml_safe(
                rows
            )

            self.dataChanged.emit()

        except Exception as exc:
            self._balances = []
            self.dataChanged.emit()

            self._set_action(
                f"تعذر تحميل رصيد الكسر: {exc}",
                False,
            )

        finally:
            self._set_busy(False)

    @Slot(int, result=float)
    def balanceForKarat(self, karat_id):
        try:
            karat_id = int(karat_id)
        except (TypeError, ValueError):
            return 0.0

        for row in self._balances:
            try:
                if int(
                    row.get("karat_id")
                ) == karat_id:
                    return float(
                        row.get("balance_grams")
                        or 0
                    )
            except (
                TypeError,
                ValueError,
            ):
                continue

        return 0.0

    @Slot(int, result=str)
    def scrapBreakdownForKarat(self, karat_id):
        """Physical / held / available of one karat while something is HELD for a manager decision (032)."""
        from services.old_gold_availability import breakdown_text
        for row in self._balances:
            try:
                if int(row.get("karat_id")) != int(karat_id):
                    continue
                held = Decimal(str(row.get("held_grams") or 0))
            except (TypeError, ValueError, InvalidOperation):
                continue
            if held <= 0:
                return ""
            return breakdown_text(row.get("physical_grams") or 0, held, row.get("balance_grams") or 0)
        return ""

    @Slot()
    def clearActionMessage(self):
        self._set_action("", True)

    def attach_session(self, session):
        """The logged-in session: the service attributes the conversion to its authenticated actor and
        re-authorizes it (OLD_GOLD_MANAGE) on every write. An injected / stub service is left untouched."""
        self._session = session
        if isinstance(self._service, OldGoldConversionService):
            self._service = OldGoldConversionService(session=session)
        self.busyChanged.emit()

    @Property(bool, notify=busyChanged)
    def canManage(self):
        """UI hint only (the «محولة من الكسر» option); the service re-checks the database on every write."""
        actor = self._session.current_actor() if self._session is not None else None
        return bool(actor and OldGoldConversionService.MANAGE_PERMISSION in actor.permissions)

    @Slot(
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
    def convertToNewStock(
        self,
        item_type_id,
        karat_id,
        category_id,
        factory_id,
        result_weight_grams,
        consumed_old_gold_grams,
        size,
        barcode_value,
        notes,
    ):
        self._set_busy(True)

        try:
            result = self._service.convert_to_new_stock(
                item_type_id=item_type_id,
                karat_id=karat_id,
                category_id=category_id,
                factory_id=factory_id,
                result_weight_grams=result_weight_grams,
                consumed_old_gold_grams=consumed_old_gold_grams,
                size=size,
                barcode_value=barcode_value,
                notes=notes,
            )

            self._set_action(
                (
                    "تم إنشاء القطعة الجديدة "
                    f"#{result['result_inventory_item_id']} "
                    "وخصم "
                    f"{float(result['consumed_weight_grams']):.3f} جرام "
                    "من رصيد الكسر. "
                    "الرصيد المتبقي: "
                    f"{float(result['remaining_balance_grams']):.3f} جرام. "
                    "فرق الوزن: "
                    f"{float(result['weight_difference_grams']):+.3f} جرام."
                ),
                True,
            )

            self.refreshBalances()
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
                "Result weight must be a valid number.":
                    "أدخل وزن القطعة الجديدة بشكل صحيح.",
                "Result weight must be greater than zero.":
                    "وزن القطعة الجديدة يجب أن يكون أكبر من صفر.",
                "Consumed old-gold weight must be a valid number.":
                    "أدخل وزن الكسر المسحوب بشكل صحيح.",
                "Consumed old-gold weight must be greater than zero.":
                    "وزن الكسر المسحوب يجب أن يكون أكبر من صفر.",
                "Not enough old-gold balance.":
                    "رصيد الكسر غير كافٍ لهذا التحويل.",
                "Barcode already exists.":
                    "هذا الباركود مستخدم بالفعل.",
                "Employee does not exist or is inactive.":
                    "الموظف غير موجود أو غير نشط.",
                "Item type does not exist or is inactive.":
                    "الصنف غير موجود أو غير نشط.",
                "Karat does not exist or is inactive.":
                    "العيار غير موجود أو غير نشط.",
                "Category does not exist or is inactive.":
                    "الفئة غير موجودة أو غير نشطة.",
                "Factory does not exist or is inactive.":
                    "المصنع غير موجود أو غير نشط.",
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
                f"تعذر التحويل من الكسر: {exc}",
                False,
            )

            return False

        finally:
            self._set_busy(False)
