"""الجرد بالباركود — one transient counting session (no database writes, ever).

The expected pieces of the chosen scope are loaded ONCE (the same current-stock predicate as
بيان المخزون). Each scan is resolved against an in-memory barcode index: a match moves exactly one
piece from المتبقي to تم الجرد and both totals change incrementally (exact Decimal). Nothing in the
database changes — no status, weight, barcode or movement. Closing / reset / a new scope discards it."""
from concurrent.futures import Future
from decimal import Decimal

from PySide6.QtCore import QObject, Property, QSettings, Signal, Slot

from webapp import stocktake_link

from app.barcode_input import canonical_barcode
from app.viewmodels.stock_balance_viewmodel import weight_text
from services.inventory_service import InventoryService

DUPLICATE = "تم جرد هذا الباركود بالفعل."
UNKNOWN = "هذا الباركود غير موجود ضمن الجرد الحالي."
ZERO = Decimal("0.000")
MAX_BATCH = 200


class StocktakeViewModel(QObject):
    changed = Signal()
    messageChanged = Signal()
    webScanRequested = Signal(object)         # (code, Future) from an iPad camera, handled on the GUI thread
    webScanned = Signal(str)                  # the result of a scan that came from the iPad (the page flashes like a local scan)

    def __init__(self, service=None, parent=None):
        super().__init__(parent)
        self._service = service or InventoryService()
        self._item_types, self._karats = [], []
        self._item_type_id, self._karat_id = 0, 0
        self._rows = {}                  # inventory_item_id -> source row (Decimal weight)
        self._by_barcode = {}            # barcode -> inventory_item_id
        self._remaining, self._scanned = [], []      # ordered ids; scanned is newest first
        self._remaining_weight = self._scanned_weight = ZERO
        self._loaded = False
        self._error = ""
        self._message, self._message_kind = "", ""
        # Batches (owner 2026-11-04): the LEFT table (تم الجرد) shows only the current batch of N scanned pieces; the Nth scan fills it, the next scan
        # starts a fresh one. The right table (المتبقي) and every total always cover the whole count. 0 = no batches (every scan stays in the table).
        self._batch_size = self._stored_batch_size()
        self._archived = 0                          # scanned pieces that belong to finished batches (the oldest ones)
        self._screen_open = False
        self.webScanRequested.connect(self._on_web_scan)

    # ------------------------------------------------------------ the iPad camera
    def _web_scan_blocking(self, code):
        """Runs on an HTTP server thread: queue the scan for the GUI thread and wait for its answer."""
        future = Future()
        self.webScanRequested.emit((code, future))
        try:
            return future.result(timeout=5)
        except Exception:
            return {"result": "UNAVAILABLE", "message": "الجرد على الكمبيوتر لا يرد الآن."}

    def _on_web_scan(self, request):
        code, future = request
        future.set_result(self._web_scan_inline(code))

    def _web_scan_inline(self, code):
        """The scan itself, always on the GUI thread."""
        if not (self._screen_open and self._loaded):
            return {"result": "CLOSED", "message": "شاشة الجرد مقفولة على الكمبيوتر."}
        result = self.scan(str(code))
        self.webScanned.emit(result)
        return {"result": result, "message": self._message, "scanned": len(self._scanned), "remaining": len(self._remaining),
                "weight": weight_text(self._scanned_weight)}

    @Slot()
    def screenOpened(self):
        self._screen_open = True
        stocktake_link.set_handler(self._web_scan_blocking)

    @Slot()
    def screenClosed(self):
        self._screen_open = False
        stocktake_link.clear_handler(self._web_scan_blocking)

    # ------------------------------------------------------------ batches
    @staticmethod
    def _real_window():
        """The saved choice is read / written only in the real application (never on the offscreen / test platforms)."""
        try:
            from PySide6.QtGui import QGuiApplication
            return QGuiApplication.instance() is not None and QGuiApplication.platformName() not in ("offscreen", "minimal", "")
        except Exception:
            return False

    @classmethod
    def _stored_batch_size(cls):
        if not cls._real_window():
            return 0
        try:
            return max(0, min(MAX_BATCH, int(QSettings("masGold", "v2").value("stocktake/batch_size", 0))))
        except Exception:
            return 0

    def _in_batch(self):
        """How many of the scanned pieces belong to the current batch (all of them when batches are off)."""
        return len(self._scanned) - self._archived

    @Slot(int)
    def setBatchSize(self, size):
        """The owner's batch size (0 = off). What is already scanned counts as finished batches; only the table changes."""
        self._batch_size = max(0, min(MAX_BATCH, int(size)))
        if self._real_window():
            try:
                QSettings("masGold", "v2").setValue("stocktake/batch_size", self._batch_size)
            except Exception:
                pass
        self._archived = len(self._scanned) if self._batch_size > 0 else 0
        self.changed.emit()

    @Slot()
    def startNextBatch(self):
        """Close the current batch early: the left table is emptied, the scanned pieces stay counted."""
        if self._batch_size > 0 and self._in_batch():
            self._archived = len(self._scanned)
            self._set_message("بدأت دفعة جديدة.", "ok")
            self.changed.emit()

    # ------------------------------------------------------------ session
    def _load_source(self):
        try:
            rows = self._service.get_stocktake_source(self._item_type_id or None, self._karat_id or None)
        except Exception as exc:
            rows, self._error = [], "تعذر تحميل الجرد: " + str(exc)
        else:
            self._error = ""
        self._rows = {str(r["inventory_item_id"]): r for r in rows}
        self._by_barcode = {r["barcode_value"]: str(r["inventory_item_id"]) for r in rows if r.get("barcode_value")}
        self._remaining = list(self._rows)
        self._scanned = []
        self._archived = 0
        self._remaining_weight = sum((r["weight_grams"] for r in rows), ZERO)
        self._scanned_weight = ZERO
        self._loaded = True
        self._set_message("", "")
        self.changed.emit()

    @Slot()
    def start(self):
        """Opening the screen begins a fresh session (filters loaded once, then the source set)."""
        if not self._item_types and not self._karats:
            try:
                filters = self._service.get_stocktake_filters()
                self._item_types = [{"id": str(r["id"]), "name": r["name"]} for r in filters["item_types"]]
                self._karats = [{"id": str(r["id"]), "name": f"عيار {r['karat_value']}"} for r in filters["karats"]]
            except Exception as exc:
                self._error = "تعذر تحميل الجرد: " + str(exc)
        self._load_source()

    @Slot(int, int, bool, result=bool)
    def applyFilters(self, item_type_id, karat_id, confirmed):
        """A new scope rebuilds the session; refused (returns False) while scans exist unless confirmed."""
        if self._scanned and not confirmed:
            return False
        self._item_type_id, self._karat_id = max(int(item_type_id), 0), max(int(karat_id), 0)
        self._load_source()
        return True

    @Slot(bool, result=bool)
    def reset(self, confirmed):
        """Every piece back to المتبقي; the current filters stay."""
        if self._scanned and not confirmed:
            return False
        self._load_source()
        return True

    @Slot(str, result=str)
    def scan(self, text):
        """OK / DUPLICATE / UNKNOWN / INVALID. Only OK changes the session."""
        barcode, problem = canonical_barcode(text)
        if problem:
            self._set_message(problem, "error")
            return "INVALID"
        if not barcode:
            return "EMPTY"
        item_id = self._by_barcode.get(barcode)
        if item_id is None:
            self._set_message(UNKNOWN, "error")
            return "UNKNOWN"
        if item_id in self._scanned:
            self._set_message(DUPLICATE, "warning")
            return "DUPLICATE"
        weight = self._rows[item_id]["weight_grams"]
        if self._batch_size > 0 and self._in_batch() >= self._batch_size:
            self._archived = len(self._scanned)             # the previous batch was full: this scan opens the next one
        self._remaining.remove(item_id)
        self._scanned.insert(0, item_id)
        self._remaining_weight -= weight
        self._scanned_weight += weight
        full = self._batch_size > 0 and self._in_batch() >= self._batch_size
        self._set_message(f"✓ {barcode} — اكتملت الدفعة {self._batches_finished()}، المسح التالي يبدأ دفعة جديدة" if full else "✓ " + barcode, "ok")
        self.changed.emit()
        return "OK"

    def _set_message(self, text, kind):
        self._message, self._message_kind = text, kind
        self.messageChanged.emit()

    # ------------------------------------------------------------ display
    def _display(self, item_id):
        row = self._rows[item_id]
        return {"id": item_id, "barcode": row.get("barcode_value") or "بدون باركود",
                "has_barcode": bool(row.get("barcode_value")), "item_type": row.get("item_type_name") or "—",
                "karat": str(row.get("karat_value") or ""), "size": row.get("size") or "—",
                "weight_text": weight_text(row["weight_grams"])}

    @Property("QVariantList", notify=changed)
    def remainingRows(self):
        return [dict(self._display(i), number=n) for n, i in enumerate(self._remaining, 1)]

    def _batches_finished(self):
        if self._batch_size <= 0:
            return 0
        return (self._archived + (self._batch_size if self._in_batch() >= self._batch_size else 0)) // self._batch_size

    @Property("QVariantList", notify=changed)
    def scannedRows(self):
        shown = self._scanned[: self._in_batch()] if self._batch_size > 0 else self._scanned
        count = len(shown)
        return [dict(self._display(i), number=count - n) for n, i in enumerate(shown)]       # numbered in scan order inside the batch

    @Property(int, notify=changed)
    def batchSize(self):
        return self._batch_size

    @Property(bool, notify=changed)
    def batchMode(self):
        return self._batch_size > 0

    @Property(int, notify=changed)
    def batchShown(self):
        return self._in_batch() if self._batch_size > 0 else len(self._scanned)

    @Property(str, notify=changed)
    def batchText(self):
        if self._batch_size <= 0:
            return ""
        done = len(self._scanned) - self._in_batch()
        batches = done // self._batch_size
        return (f"الدفعة الحالية {self._in_batch()} من {self._batch_size}"
                + (f" • دفعات منتهية {batches} ({done} قطعة)" if done else ""))

    @Property(int, notify=changed)
    def remainingCount(self):
        return len(self._remaining)

    @Property(int, notify=changed)
    def scannedCount(self):
        return len(self._scanned)

    @Property(str, notify=changed)
    def remainingWeightText(self):
        return weight_text(self._remaining_weight)

    @Property(str, notify=changed)
    def scannedWeightText(self):
        return weight_text(self._scanned_weight)

    @Property(int, notify=changed)
    def withoutBarcodeCount(self):
        return sum(1 for i in self._remaining if not self._rows[i].get("barcode_value"))

    @Property("QVariantList", notify=changed)
    def karatTotals(self):
        """Per karat: remaining / scanned count and weight (dynamic karats, exact Decimal)."""
        totals = {}
        for side, ids in (("remaining", self._remaining), ("scanned", self._scanned)):
            for item_id in ids:
                row = self._rows[item_id]
                key = (row.get("karat_sort_order") or 0, row.get("karat_value"))
                entry = totals.setdefault(key, {"remaining": [0, ZERO], "scanned": [0, ZERO]})
                entry[side][0] += 1
                entry[side][1] += row["weight_grams"]
        return [{"title": f"عيار {karat}", "remaining_count": e["remaining"][0],
                 "remaining_weight_text": weight_text(e["remaining"][1]), "scanned_count": e["scanned"][0],
                 "scanned_weight_text": weight_text(e["scanned"][1])}
                for (_, karat), e in sorted(totals.items(), key=lambda kv: (kv[0][0], -(kv[0][1] or 0)))]

    @Slot("QVariantList")
    def dropRemaining(self, ids):
        """Pieces written off one by one leave المتبقي (the scans of the session stay)."""
        gone = {str(i) for i in ids}
        for item_id in [i for i in self._remaining if i in gone]:
            self._remaining.remove(item_id)
            self._remaining_weight -= self._rows[item_id]["weight_grams"]
        self.changed.emit()

    @Slot(str, result="QVariantMap")
    def pieceInfo(self, item_id):
        """What the single write-off dialog shows about one remaining piece."""
        row = self._rows.get(str(item_id))
        return {} if row is None else self._display(str(item_id))

    @Slot(result="QVariantMap")
    def approvalPayload(self):
        """What the approval needs: the scope and the two id lists of this session (the service re-derives every figure from the locked database rows)."""
        return {"itemType": str(self._item_type_id), "karat": str(self._karat_id), "scanned": list(self._scanned), "remaining": list(self._remaining)}

    @Property(bool, notify=changed)
    def hasScanned(self):
        return bool(self._scanned)

    @Property(bool, notify=changed)
    def loaded(self):
        return self._loaded

    @Property(str, notify=changed)
    def errorMessage(self):
        return self._error

    @Property("QVariantList", notify=changed)
    def itemTypeOptions(self):
        return [{"id": "0", "name": "كل الأصناف"}] + self._item_types

    @Property("QVariantList", notify=changed)
    def karatOptions(self):
        return [{"id": "0", "name": "كل العيارات"}] + self._karats

    @Property(int, notify=changed)
    def itemTypeId(self):
        return self._item_type_id

    @Property(int, notify=changed)
    def karatId(self):
        return self._karat_id

    @Property(str, notify=messageChanged)
    def message(self):
        return self._message

    @Property(str, notify=messageChanged)
    def messageKind(self):
        return self._message_kind
