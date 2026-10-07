"""بيان المخزون — current physical stock by karat → item type (read only).

Display-ready values only: counts and weights arrive as exact Decimal from InventoryService and are
formatted here; QML renders, it never sums. Nothing is loaded until the page opens."""
from decimal import Decimal

from PySide6.QtCore import QObject, Property, Signal, Slot

from services.inventory_service import InventoryService

FILTERS = ("ALL", "NEW_STOCK", "OLD_STOCK")


def weight_text(value) -> str:
    return format(Decimal(value or 0).quantize(Decimal("0.001")), ",.3f")


def karat_title(row) -> str:
    return f"عيار {row.get('karat_value')}"


class StockBalanceViewModel(QObject):
    changed = Signal()

    def __init__(self, service=None, parent=None):
        super().__init__(parent)
        self._service = service or InventoryService()
        self._filter = "ALL"
        self._karats = []
        self._count = 0
        self._weight = Decimal("0.000")
        self._equivalent = Decimal("0.000")
        self._loaded = False
        self._error = ""

    @Property(str, notify=changed)
    def filter(self):
        return self._filter

    @Property("QVariantList", notify=changed)
    def karats(self):
        return self._karats

    @Property(int, notify=changed)
    def totalCount(self):
        return self._count

    @Property(str, notify=changed)
    def totalWeightText(self):
        return weight_text(self._weight)

    @Property(str, notify=changed)
    def totalEquivalentText(self):
        """إجمالي الوزن المكافئ عيار 21 — the operational total (per-karat panels stay physical)."""
        return weight_text(self._equivalent)

    @Property(bool, notify=changed)
    def loaded(self):
        return self._loaded

    @Property(bool, notify=changed)
    def isEmpty(self):
        return self._loaded and not self._error and self._count == 0

    @Property(str, notify=changed)
    def errorMessage(self):
        return self._error

    @Slot()
    def refresh(self):
        try:
            balance = self._service.get_stock_balance(self._filter)
        except Exception as exc:                                     # shown on the page, never raised
            self._karats, self._count, self._weight = [], 0, Decimal("0.000")
            self._equivalent = Decimal("0.000")
            self._error = "تعذر تحميل بيان المخزون: " + str(exc)
        else:
            self._error = ""
            self._count = int(balance["piece_count"])
            self._weight = Decimal(balance["weight_grams"])
            self._equivalent = Decimal(balance.get("equivalent_21_weight_grams") or 0)
            self._karats = [{
                "karat_id": str(section["karat_id"]),
                "title": karat_title(section),
                "count": int(section["piece_count"]),
                "weight_text": weight_text(section["weight_grams"]),
                "rows": [{"name": row["item_type_name"] or "—", "count": int(row["piece_count"]),
                          "weight_text": weight_text(row["weight_grams"])}
                         for row in section["item_types"]],
            } for section in balance["karats"]]
        self._loaded = True
        self.changed.emit()

    @Slot(str)
    def setFilter(self, value):
        value = value if value in FILTERS else "ALL"
        if value != self._filter:
            self._filter = value
        self.refresh()
