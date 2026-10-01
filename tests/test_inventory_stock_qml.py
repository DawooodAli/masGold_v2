"""Inventory close pass on the real Main.qml: entry without a responsible employee, item details,
مراجعة وطباعة + selection after printing, بيان المخزون and الجرد بالباركود — real clicks and real key
events at 1440×900 / 1280×800 / 1080×680. The database is blocked; services are in-memory doubles."""
from decimal import Decimal as D

from PySide6.QtCore import Q_ARG, QCoreApplication, QEvent, QMetaObject, QObject, Qt
from PySide6.QtGui import QKeyEvent
from PySide6.QtTest import QTest

from services.barcode_print_service import BarcodePrinterError
from tests import test_invoice_page_qml as harness
from tests.test_inventory_stock import K18, K21, K24, MIXED, Store, piece, service_for

INVENTORY, BALANCE, STOCKTAKE = 2, 11, 12


class InventoryList:
    """InventoryService stand-in for the Inventory page list / summary."""

    def __init__(self, items):
        self.items, self.refreshes = items, 0

    def get_stock_items(self, **kwargs):
        self.refreshes += 1
        return [dict(i) for i in self.items]

    def get_stock_summary(self):
        return {"total_items": len(self.items), "new_stock_items": len(self.items), "old_stock_items": 0,
                "without_barcode_items": sum(1 for i in self.items if not i.get("barcode_value")),
                "total_weight_grams": sum((D(i["weight_grams"]) for i in self.items), D("0"))}


class Printing:
    """BarcodePrintService stand-in: allocates once per piece without a barcode; can fail."""

    def __init__(self, items):
        self.items, self.fail, self.allocated = items, False, 0

    def get_queue(self, ids):
        rows = [dict(i) for i in self.items if i["inventory_item_id"] in ids]
        have = sum(1 for r in rows if r.get("barcode_value"))
        return {"items": rows, "item_count": len(rows), "existing_barcode_count": have,
                "missing_barcode_count": len(rows) - have, "label_count": (len(rows) + 1) // 2,
                "has_odd_item": bool(len(rows) % 2)}

    def print_items(self, ids):
        new = 0
        for item in self.items:
            if item["inventory_item_id"] in ids and not item.get("barcode_value"):
                self.allocated += 1
                new += 1
                item["barcode_value"] = f"{self.allocated:02d}261001"
        if self.fail:
            raise BarcodePrinterError("printer offline", assigned_count=new, batch_code="B1")
        return {"item_count": len(ids), "new_barcode_count": new, "label_count": 1, "batch_code": "B1",
                "print_job_id": 1}


class InventoryStockPageTests(harness.InvoicePageTests):
    def children(self, item):
        from PySide6.QtQuick import QQuickItem
        return [c for c in item.childItems() if isinstance(c, QQuickItem)]

    def visible_item(self, name, parent=None):
        parent = parent or self.root.contentItem()
        if parent.objectName() == name and parent.property("visible"):
            return parent
        for child in self.children(parent):
            found = self.visible_item(name, child)
            if found is not None:
                return found
        return None

    def labels(self, parent):
        out = []

        def walk(item):
            if item.property("visible") is False:
                return
            text = item.property("text")
            if isinstance(text, str) and text:
                out.append(text)
            for child in self.children(item):
                walk(child)
        walk(parent)
        return out

    def page(self, index):
        self.root.setProperty("currentPage", index)
        QTest.qWait(150)

    def press(self, key, text=""):
        for kind in (QEvent.KeyPress, QEvent.KeyRelease):
            QCoreApplication.sendEvent(self.root, QKeyEvent(kind, key, Qt.NoModifier, text))
        QTest.qWait(30)

    def scan(self, barcode):
        """A keyboard-wedge scan: the characters, then Enter — no mouse."""
        self.type_text(barcode)
        self.press(Qt.Key_Return, "\r")

    # ------------------------------------------------------------ inventory entry / details
    def test_inventory_header_actions_fit(self):
        self.page(INVENTORY)
        for width, height in harness.SIZES:
            with self.subTest(size=(width, height)):
                self.root.setWidth(width)
                self.root.setHeight(height)
                QTest.qWait(120)
                for name in ("openStockBalanceButton", "openStocktakeButton", "barcodeReviewPrintButton"):
                    self.assert_inside_window(self.item(name))
        self.assertEqual(self.warnings, [])

    def test_new_item_dialog_has_no_responsible_employee(self):
        self.page(INVENTORY)
        dialog = self.item("newInventoryItemDialog")
        QTest.qWait(50)
        for width, height in harness.SIZES:
            with self.subTest(size=(width, height)):
                self.root.setWidth(width)
                self.root.setHeight(height)
                QMetaObject.invokeMethod(dialog, "openForNewItem")
                self.wait_until(lambda: dialog.property("opened"), "entry dialog")
                texts = self.labels(dialog.property("contentItem"))
                self.assertNotIn("الموظف المسؤول *", texts)
                self.assertIn("طريقة الإدخال *", texts)
                self.assertIsNone(self.visible_item("inventoryEmployeeCombo"))
                save = self.item("saveInventoryItemButton")
                self.assertTrue(save.property("enabled"))           # no employee needed (none exist here)
                self.assert_inside_window(save)
                dialog.close()
                self.wait_until(lambda: not dialog.property("visible"), "closed")
        self.assertEqual(self.warnings, [])

    def test_details_show_entry_method_and_merchandise_type_without_employee(self):
        self.page(INVENTORY)
        self.root.setProperty("selectedInventoryItem", {
            "inventory_item_id": 7, "item_type_name": "خاتم", "karat_value": 21, "weight_grams": 3.6, "size": "55",
            "factory_name": "النور للتصنيع", "category_code": "A", "barcode_value": "01261001",
            "stock_classification": "NEW_STOCK", "inventory_status": "IN_STOCK", "acquisition_source": "MANUAL_ENTRY",
            "acquired_at": "2026-10-01 10:00:00", "entered_by_employee_id": 1, "notes": ""})
        dialog = self.item("inventoryItemDetailsDialog")
        for width, height in harness.SIZES:
            with self.subTest(size=(width, height)):
                self.root.setWidth(width)
                self.root.setHeight(height)
                dialog.open()
                self.wait_until(lambda: dialog.property("opened"), "details")
                texts = self.labels(dialog.property("contentItem"))
                self.assertNotIn("رقم الموظف", texts)
                self.assertIn("طريقة الإدخال", texts)
                self.assertIn("إدخال يدوي", texts)
                self.assertEqual(self.item("inventoryDetailsStockClass").property("text"), "بضاعة جديدة")
                dialog.close()
                self.wait_until(lambda: not dialog.property("visible"), "closed")
        self.assertEqual(self.warnings, [])

    # ------------------------------------------------------------ barcode print + selection
    def print_setup(self):
        items = [{"inventory_item_id": 7, "barcode_value": "01261001", "item_type_name": "خاتم", "weight_grams": 3.6,
                  "karat_value": 21, "stock_classification": "NEW_STOCK", "acquired_at": "2026-10-01 10:00:00"},
                 {"inventory_item_id": 8, "barcode_value": None, "item_type_name": "دبله", "weight_grams": 4.5,
                  "karat_value": 18, "stock_classification": "NEW_STOCK", "acquired_at": "2026-10-01 11:00:00"}]
        inventory = self.controller.operational_viewmodels["inventoryVM"]
        inventory._service = InventoryList(items)
        inventory.refresh()
        barcode = self.controller.operational_viewmodels["barcodeVM"]
        barcode._service = Printing(items)
        self.page(INVENTORY)
        return self.item("inventoryPage"), barcode, items, inventory

    def test_successful_print_stores_barcode_and_clears_selection(self):
        page, barcode, items, inventory = self.print_setup()
        self.assertEqual(inventory.withoutBarcodeCount, 1)
        page.setProperty("selectedItemIds", [8])
        QTest.qWait(50)
        review = self.item("barcodeReviewPrintButton")
        self.assertEqual(review.property("text"), "🖨  مراجعة وطباعة")
        self.click(review)
        dialog = self.item("barcodePrintQueueDialog")
        self.wait_until(lambda: dialog.property("opened"), "print queue")
        self.assertEqual(barcode.missingBarcodeCount, 1)                     # detected as needing a barcode
        self.click("printBarcodeNowButton")
        self.wait_until(lambda: not dialog.property("visible"), "queue closed")
        self.assertEqual(page.property("selectedItemIds").toVariant() if hasattr(page.property("selectedItemIds"),
                         "toVariant") else page.property("selectedItemIds"), [])
        self.assertEqual((items[1]["barcode_value"], barcode._service.allocated), ("01261001", 1))
        self.assertEqual(inventory.withoutBarcodeCount, 0)                   # counter refreshed
        self.assertEqual(items[0]["barcode_value"], "01261001")             # existing barcode untouched
        self.assertEqual(self.warnings, [])

    def test_failed_print_keeps_selection_and_retry_allocates_nothing_new(self):
        page, barcode, items, inventory = self.print_setup()
        barcode._service.fail = True
        page.setProperty("selectedItemIds", [8])
        QTest.qWait(50)
        self.click("barcodeReviewPrintButton")
        dialog = self.item("barcodePrintQueueDialog")
        self.wait_until(lambda: dialog.property("opened"), "print queue")
        self.click("printBarcodeNowButton")
        QTest.qWait(80)
        self.assertTrue(dialog.property("visible"))                          # stays open with the error
        selected = page.property("selectedItemIds")
        selected = selected.toVariant() if hasattr(selected, "toVariant") else selected
        self.assertEqual([int(x) for x in selected], [8])
        self.assertFalse(barcode.actionSuccess)
        self.assertEqual(items[1]["barcode_value"], "01261001")             # persisted before the failure
        barcode._service.fail = False
        self.click("printBarcodeNowButton")                                  # retry
        self.wait_until(lambda: not dialog.property("visible"), "queue closed")
        self.assertEqual((barcode._service.allocated, items[1]["barcode_value"]), (1, "01261001"))
        self.assertEqual(self.warnings, [])

    # ------------------------------------------------------------ بيان المخزون
    def test_stock_balance_page(self):
        long_name = "سلسلة مخرّمة بتصميم إيطالي طويل جداً للاختبار"
        pieces = MIXED + [piece(20, K21, long_name, "1234.567", barcode="20261001")]
        vm = self.controller.operational_viewmodels["stockBalanceVM"]
        vm._service = service_for(Store(pieces))
        self.page(INVENTORY)
        self.click("openStockBalanceButton")
        self.wait_until(lambda: self.root.property("currentPage") == BALANCE and vm.loaded, "balance opened")
        for width, height in harness.SIZES:
            with self.subTest(size=(width, height)):
                self.root.setWidth(width)
                self.root.setHeight(height)
                QTest.qWait(120)
                self.assertEqual(self.item("stockBalanceTotalCount").property("text"), "إجمالي القطع: 7")
                self.assertEqual(self.item("stockBalanceTotalWeight").property("text"), "إجمالي الوزن: 1,290.901 جرام")
                for code in ("ALL", "NEW_STOCK", "OLD_STOCK"):
                    self.assert_inside_window(self.item("stockBalanceFilter_" + code))
                self.assert_inside_window(self.item("stockBalanceBack"))
                panels = [self.visible_item(f"stockBalanceKarat_{k[0]}") for k in (K21, K18, K24)]
                self.assertTrue(all(panels))
                for panel in panels:
                    self.assertLessEqual(panel.mapToScene(panel.boundingRect().topLeft()).x() + panel.width(),
                                         width + 1)
                self.assertIn(long_name, self.labels(panels[0]))
        self.click("stockBalanceFilter_OLD_STOCK")
        self.wait_until(lambda: vm.filter == "OLD_STOCK", "filter")
        self.assertEqual(self.item("stockBalanceTotalCount").property("text"), "إجمالي القطع: 1")
        self.assertIsNone(self.visible_item("stockBalanceKarat_2"))          # 18K has no old stock: omitted
        vm._service = service_for(Store())
        self.click("stockBalanceFilter_ALL")
        self.wait_until(lambda: vm.isEmpty, "empty")
        self.assertIsNotNone(self.visible_item("stockBalanceEmpty"))
        self.click("stockBalanceBack")
        self.assertEqual(self.root.property("currentPage"), INVENTORY)
        self.assertEqual(self.warnings, [])

    # ------------------------------------------------------------ الجرد بالباركود
    def test_stocktake_scanner_flow_at_all_sizes(self):
        vm = self.controller.operational_viewmodels["stocktakeVM"]
        store = Store(MIXED)
        vm._service = service_for(store)
        self.page(INVENTORY)
        self.click("openStocktakeButton")
        self.wait_until(lambda: self.root.property("currentPage") == STOCKTAKE and vm.loaded, "stocktake opened")
        field = self.item("stocktakeScanField")
        self.wait_until(lambda: field.property("activeFocus"), "autofocus")
        reads = store.reads
        self.scan("02261001")
        self.assertEqual((vm.remainingCount, vm.scannedCount, vm.remainingWeightText, vm.scannedWeightText),
                         (5, 1, "52.209", "4.125"))
        self.assertEqual((field.property("text"), field.property("activeFocus")), ("", True))   # cleared, refocused
        self.scan("02261001")
        self.assertEqual(self.item("stocktakeMessage").property("text"), "تم جرد هذا الباركود بالفعل.")
        self.scan("06261001")                                                 # SOLD
        self.assertEqual(self.item("stocktakeMessage").property("text"), "هذا الباركود غير موجود ضمن الجرد الحالي.")
        self.assertEqual((vm.remainingCount, vm.scannedCount, store.reads), (5, 1, reads))
        self.assertTrue(field.property("activeFocus"))
        for width, height in harness.SIZES:
            with self.subTest(size=(width, height)):
                self.root.setWidth(width)
                self.root.setHeight(height)
                QTest.qWait(150)
                for name in ("stocktakeScanField", "stocktakeReset", "stocktakeBack", "stocktakeRemainingPanel",
                             "stocktakeScannedPanel", "stocktakeItemTypeFilter", "stocktakeKaratFilter"):
                    self.assert_inside_window(self.item(name))
                right, left = self.item("stocktakeRemainingPanel"), self.item("stocktakeScannedPanel")
                if width >= 1080 and right.mapToScene(right.boundingRect().topLeft()).y() == \
                        left.mapToScene(left.boundingRect().topLeft()).y():
                    self.assertGreater(right.mapToScene(right.boundingRect().topLeft()).x(),
                                       left.mapToScene(left.boundingRect().topLeft()).x())   # المتبقي on the right
        # reset with confirmation
        self.click("stocktakeReset")
        confirm = self.item("stocktakeConfirmDialog")
        self.wait_until(lambda: confirm.property("opened"), "confirm")
        self.click("stocktakeConfirmOk")
        self.wait_until(lambda: not confirm.property("visible"), "confirmed")
        self.assertEqual((vm.remainingCount, vm.scannedCount), (6, 0))
        self.assertTrue(field.property("activeFocus"))
        # filters: karat 18 (no scans yet → no confirmation)
        combo = self.item("stocktakeKaratFilter")
        options = [o["name"] for o in vm.karatOptions]
        combo.setProperty("currentIndex", options.index("عيار 18"))
        QMetaObject.invokeMethod(combo, "activated", Q_ARG(int, options.index("عيار 18")))
        self.wait_until(lambda: vm.remainingCount == 2, "karat filter")
        self.scan("04261001")
        self.assertEqual(vm.scannedCount, 1)
        combo.setProperty("currentIndex", 0)
        QMetaObject.invokeMethod(combo, "activated", Q_ARG(int, 0))
        self.wait_until(lambda: confirm.property("opened"), "scope confirm")
        self.click("stocktakeConfirmCancel")                                  # keep the session
        self.wait_until(lambda: not confirm.property("visible"), "cancelled")
        self.assertEqual((vm.scannedCount, combo.property("currentIndex")), (1, options.index("عيار 18")))
        self.assertEqual(store.created + store.movements, [])                 # never a stock write
        self.assertEqual(self.warnings, [])

    def test_stocktake_empty_inventory(self):
        vm = self.controller.operational_viewmodels["stocktakeVM"]
        vm._service = service_for(Store())
        self.page(STOCKTAKE)
        self.wait_until(lambda: vm.loaded, "loaded")
        self.assertIsNotNone(self.visible_item("stocktakeEmpty"))
        self.assertEqual(self.item("stocktakeRemainingCount").property("text"), "0 قطعة")
        self.assertEqual(self.warnings, [])


# unittest otherwise inherits every Billing harness test, inflating the suite count.
for _name in dir(harness.InvoicePageTests):
    if _name.startswith("test_"):
        setattr(InventoryStockPageTests, _name, None)
