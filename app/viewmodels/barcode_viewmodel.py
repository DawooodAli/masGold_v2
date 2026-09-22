from datetime import date, datetime
from decimal import Decimal

from PySide6.QtCore import (
    QObject,
    Property,
    Signal,
    Slot,
)

from services.barcode_print_service import (
    BarcodePrintService,
    BarcodePrinterError,
)


class BarcodeViewModel(QObject):

    queueChanged = Signal()
    historyChanged = Signal()
    actionChanged = Signal()
    busyChanged = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)

        self._service = BarcodePrintService()

        self._queue_items = []
        self._queue_item_ids = []

        self._existing_barcode_count = 0
        self._missing_barcode_count = 0
        self._label_count = 0
        self._has_odd_item = False

        self._print_history = []
        self._history_date = ""

        self._action_message = ""
        self._action_success = True
        self._busy = False

    @staticmethod
    def _qml_safe(value):
        if isinstance(value, Decimal):
            return float(value)

        if isinstance(value, datetime):
            return value.isoformat(
                sep=" ",
                timespec="seconds",
            )

        if isinstance(value, date):
            return value.isoformat()

        if isinstance(value, dict):
            return {
                key: BarcodeViewModel._qml_safe(item)
                for key, item in value.items()
            }

        if isinstance(value, (list, tuple)):
            return [
                BarcodeViewModel._qml_safe(item)
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

    def _apply_queue_result(
        self,
        result,
    ):
        self._queue_items = (
            self._qml_safe(
                result["items"]
            )
        )

        self._queue_item_ids = [
            int(
                item[
                    "inventory_item_id"
                ]
            )
            for item in result["items"]
        ]

        self._existing_barcode_count = int(
            result[
                "existing_barcode_count"
            ]
        )

        self._missing_barcode_count = int(
            result[
                "missing_barcode_count"
            ]
        )

        self._label_count = int(
            result["label_count"]
        )

        self._has_odd_item = bool(
            result["has_odd_item"]
        )

        self.queueChanged.emit()

    @Property(
        "QVariantList",
        notify=queueChanged,
    )
    def queueItems(self):
        return self._queue_items

    @Property(
        int,
        notify=queueChanged,
    )
    def queueCount(self):
        return len(
            self._queue_items
        )

    @Property(
        int,
        notify=queueChanged,
    )
    def existingBarcodeCount(self):
        return self._existing_barcode_count

    @Property(
        int,
        notify=queueChanged,
    )
    def missingBarcodeCount(self):
        return self._missing_barcode_count

    @Property(
        int,
        notify=queueChanged,
    )
    def physicalLabelCount(self):
        return self._label_count

    @Property(
        bool,
        notify=queueChanged,
    )
    def hasOddItem(self):
        return self._has_odd_item

    @Property(
        "QVariantList",
        notify=historyChanged,
    )
    def printHistory(self):
        return self._print_history

    @Property(
        int,
        notify=historyChanged,
    )
    def printHistoryCount(self):
        return len(
            self._print_history
        )

    @Property(
        str,
        notify=historyChanged,
    )
    def historyDate(self):
        return self._history_date

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

    @Slot(
        "QVariantList",
        result=bool,
    )
    def prepareQueue(
        self,
        item_ids,
    ):
        self._set_busy(True)

        try:
            result = self._service.get_queue(
                item_ids
            )

            self._apply_queue_result(
                result
            )

            self._set_action(
                "",
                True,
            )

            return True

        except Exception as exc:
            self.clearQueue()

            self._set_action(
                f"تعذر تجهيز قائمة الطباعة: {exc}",
                False,
            )

            return False

        finally:
            self._set_busy(False)

    @Slot(result=bool)
    def printQueue(self):
        if not self._queue_item_ids:
            self._set_action(
                "لا توجد قطع في قائمة الطباعة.",
                False,
            )
            return False

        self._set_busy(True)

        try:
            selected_ids = list(
                reversed(
                    self._queue_item_ids
                )
            )

            result = self._service.print_items(
                selected_ids
            )

            self._set_action(
                (
                    f"تم إرسال {result['item_count']} قطعة "
                    f"على {result['label_count']} لاصقة للطابعة. "
                    f"رقم الدفعة: {result['batch_code']}."
                    + (
                        f" تم إنشاء {result['new_barcode_count']} "
                        "باركود جديد."
                        if result[
                            "new_barcode_count"
                        ] > 0
                        else ""
                    )
                ),
                True,
            )

            refreshed = (
                self._service.get_queue(
                    selected_ids
                )
            )

            self._apply_queue_result(
                refreshed
            )

            return True

        except BarcodePrinterError as exc:
            if exc.assigned_count > 0:
                message = (
                    f"تم حفظ {exc.assigned_count} باركود جديد "
                    "بنجاح، لكن الطباعة فشلت. "
                    "لن يتم إنشاء باركودات جديدة عند إعادة المحاولة. "
                )
            else:
                message = (
                    "فشلت الطباعة. "
                )

            if exc.batch_code:
                message += (
                    f"دفعة المحاولة: {exc.batch_code}. "
                )

            message += (
                f"السبب: {exc}"
            )

            try:
                refreshed = (
                    self._service.get_queue(
                        selected_ids
                    )
                )

                self._apply_queue_result(
                    refreshed
                )

            except Exception:
                pass

            self._set_action(
                message,
                False,
            )

            return False

        except Exception as exc:
            self._set_action(
                f"تعذر طباعة الباركود: {exc}",
                False,
            )

            return False

        finally:
            self._set_busy(False)

    # =========================================================
    # PRINT HISTORY
    # =========================================================

    @Slot(
        str,
        result=bool,
    )
    def loadPrintHistory(
        self,
        print_date,
    ):
        self._set_busy(True)

        try:
            print_date = str(
                print_date or ""
            ).strip()

            rows = (
                self._service
                .get_print_history(
                    print_date
                )
            )

            self._print_history = (
                self._qml_safe(
                    rows
                )
            )

            self._history_date = (
                print_date
            )

            self.historyChanged.emit()

            self._set_action(
                "",
                True,
            )

            return True

        except Exception as exc:
            self._print_history = []
            self._history_date = ""

            self.historyChanged.emit()

            self._set_action(
                f"تعذر تحميل سجل الطباعة: {exc}",
                False,
            )

            return False

        finally:
            self._set_busy(False)

    @Slot(
        int,
        result=bool,
    )
    def reprintJob(
        self,
        print_job_id,
    ):
        self._set_busy(True)

        try:
            result = (
                self._service
                .reprint_job(
                    print_job_id
                )
            )

            self._set_action(
                (
                    "تمت إعادة طباعة الدفعة بنجاح. "
                    f"الدفعة الجديدة: {result['batch_code']}. "
                    f"إعادة طباعة من: "
                    f"{result['reprint_of_batch_code']}."
                ),
                True,
            )

            try:
                rows = (
                    self._service
                    .get_print_history(
                        self._history_date
                    )
                )

                self._print_history = (
                    self._qml_safe(
                        rows
                    )
                )

                self.historyChanged.emit()

            except Exception:
                pass

            return True

        except BarcodePrinterError as exc:
            message = (
                "فشلت إعادة الطباعة. "
            )

            if exc.batch_code:
                message += (
                    f"دفعة المحاولة: {exc.batch_code}. "
                )

            message += (
                f"السبب: {exc}"
            )

            self._set_action(
                message,
                False,
            )

            return False

        except Exception as exc:
            self._set_action(
                f"تعذر إعادة طباعة الدفعة: {exc}",
                False,
            )

            return False

        finally:
            self._set_busy(False)

    @Slot()
    def clearQueue(self):
        self._queue_items = []
        self._queue_item_ids = []

        self._existing_barcode_count = 0
        self._missing_barcode_count = 0
        self._label_count = 0
        self._has_odd_item = False

        self.queueChanged.emit()

    @Slot()
    def clearHistory(self):
        self._print_history = []
        self._history_date = ""

        self.historyChanged.emit()
