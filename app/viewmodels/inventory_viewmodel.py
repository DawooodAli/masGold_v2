from decimal import Decimal

from PySide6.QtCore import (
    QObject,
    Property,
    Signal,
    Slot,
)

from services.inventory_service import InventoryService
from datetime import date, datetime

STATUS_TEXT = {"IN_STOCK": "بالمخزون", "SOLD": "مباعة", "RETURNED_PENDING": "مرتجعة — قيد الفحص",
               "CONVERTED_TO_OLD_GOLD": "محولة إلى كسر", "SCRAPPED": "مستبعدة", "REMOVED": "مستبعدة"}

class InventoryViewModel(QObject):

    dataChanged = Signal()
    errorChanged = Signal()
    actionChanged = Signal()

    def __init__(self):
        super().__init__()

        self._service = InventoryService()
        self._session = None

        self._items = []
        self._summary = {}

        self._search_text = ""
        self._karat_filter, self._item_type_filter = 0, 0
        self._history_piece = {}
        self._error_message = ""
        self._action_message = ""
        self._action_success = False
        self.refresh()


    def attach_session(self, session):
        """The logged-in session: stock entry is attributed to its actor and re-authorized (INVENTORY_MANAGE)
        by the service on every write. An injected / stub service is left untouched."""
        self._session = session
        if isinstance(self._service, InventoryService):
            self._service = InventoryService(session=session)
        self.dataChanged.emit()

    @Property(bool, notify=dataChanged)
    def canManage(self):
        """UI hint only (the add-stock controls); the service re-checks the database on every write."""
        actor = self._session.current_actor() if self._session is not None else None
        return bool(actor and InventoryService.MANAGE_PERMISSION in actor.permissions)

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

    @Property(str, notify=dataChanged)
    def totalWeight21Text(self):
        """إجمالي الوزن المكافئ عيار 21 (exact Decimal from the service; never a float sum)."""
        value = self._summary.get("equivalent_21_weight_grams")
        return format(Decimal(str(value or 0)).quantize(Decimal("0.001")), ",.3f")

    @Property(int, notify=dataChanged)
    def karatFilterId(self):
        return self._karat_filter

    @Property(int, notify=dataChanged)
    def itemTypeFilterId(self):
        return self._item_type_filter

    @Slot(int, int)
    def setFilters(self, karat_id, item_type_id):
        self._karat_filter, self._item_type_filter = max(int(karat_id), 0), max(int(item_type_id), 0)
        self.refresh()

    @Property("QVariantMap", notify=dataChanged)
    def historyPiece(self):
        return self._history_piece

    @Property(bool, notify=dataChanged)
    def hasHistoryPiece(self):
        return bool(self._history_piece)

    def _find_history_piece(self):
        """An exact barcode / item number that is no longer current stock (e.g. SOLD): shown read only
        for traceability — never listed, counted, sold again or re-barcoded."""
        self._history_piece = {}
        if not self._search_text or self._items:
            return
        piece = self._service.find_piece(self._search_text) if hasattr(self._service, "find_piece") else None
        if not piece or piece.get("inventory_status") == "IN_STOCK":
            return
        if self._karat_filter and int(piece.get("karat_id") or 0) != self._karat_filter:
            return
        if self._item_type_filter and int(piece.get("item_type_id") or 0) != self._item_type_filter:
            return
        sale = piece.get("sale") or {}
        weight = Decimal(str(piece.get("weight_grams") or 0)).quantize(Decimal("0.001"))
        self._history_piece = {
            "itemNumber": f"#{piece['inventory_item_id']}", "barcode": piece.get("barcode_value") or "—",
            "itemType": piece.get("item_type_name") or "—", "karat": str(piece.get("karat_value") or ""),
            "weight": f"{weight:,.3f}", "factory": piece.get("factory_code") or piece.get("factory_name") or "—",
            "category": piece.get("category_code") or "—",
            "status": STATUS_TEXT.get(piece.get("inventory_status"), piece.get("inventory_status") or ""),
            "invoiceId": str(sale["invoice_id"]) if sale.get("invoice_id") is not None else "",
            "invoiceNumber": sale.get("invoice_number") or "",
            "invoiceDate": self._qml_safe(sale.get("invoice_date")) or "",
            "customer": sale.get("customer_name") or "—",
        }


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
                    karat_id=self._karat_filter or None,
                    item_type_id=self._item_type_filter or None,
                )
                or []
            )
            self._find_history_piece()

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
        str,
        str,
        str,
        str,
        str,
        result=bool,
    )
    def createInventoryItem(
            self,
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
                # Manual entry has no responsible employee (owner decision).
                self._service.create_inventory_item(
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

                    # No actor argument: the service attributes the write to the authenticated session user
                    # and enforces INVENTORY_MANAGE itself (the screen never supplies an identity).
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