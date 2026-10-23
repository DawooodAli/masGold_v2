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
    """BarcodePrintService stand-in with the real rule: a piece without a stored barcode (بدون باركود) is
    refused; a label is printed only with the SAME stored barcode (nothing is ever allocated); can fail."""

    def __init__(self, items):
        self.items, self.fail, self.allocated, self.queued, self.printed = items, False, 0, [], []

    def get_queue(self, ids):
        self.queued.append(list(ids))
        rows = [dict(i) for i in self.items if i["inventory_item_id"] in ids]
        if any(not r.get("barcode_value") for r in rows):
            raise ValueError("لا يمكن طباعة لاصقة لقطعة بدون باركود")
        have = sum(1 for r in rows if r.get("barcode_value"))
        return {"items": rows, "item_count": len(rows), "existing_barcode_count": have,
                "missing_barcode_count": len(rows) - have, "label_count": (len(rows) + 1) // 2,
                "has_odd_item": bool(len(rows) % 2)}

    def print_items(self, ids):
        new = 0
        self.printed.append(list(ids))
        if any(not item.get("barcode_value") for item in self.items if item["inventory_item_id"] in ids):
            raise ValueError("لا يمكن طباعة لاصقة لقطعة بدون باركود")
        if self.fail:
            raise BarcodePrinterError("printer offline", assigned_count=new, batch_code="B1")
        return {"item_count": len(ids), "new_barcode_count": new, "label_count": 1, "batch_code": "B1",
                "print_job_id": 1}


class InventoryStockPageTests(harness.InvoicePageTests):
    def setUp(self):
        super().setUp()
        self.grant("INVENTORY_MANAGE", "OLD_GOLD_MANAGE")        # the add-stock controls are permission-gated

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
    def test_review_fixes_card_filters_history_and_digits(self):
        from PySide6.QtCore import QEvent
        from PySide6.QtGui import QKeyEvent
        calls = []

        class Service:
            def get_stock_items(self, **kwargs):
                calls.append(kwargs)
                return []

            def get_stock_summary(self):
                return {"total_items": 0, "equivalent_21_weight_grams": D("20.286")}

            def find_piece(self, text):
                return {"inventory_item_id": "7", "inventory_status": "SOLD", "weight_grams": D("3.600"),
                        "karat_id": 1, "item_type_id": 1, "item_type_name": "خاتم", "karat_value": 21,
                        "factory_code": "NR", "category_code": "A", "barcode_value": "01261001",
                        "sale": {"invoice_id": "15", "invoice_number": "26100203",
                                 "invoice_date": "2026-10-01 19:51:00", "customer_name": "علي"}}
        inventory = self.controller.operational_viewmodels["inventoryVM"]
        inventory._service = Service()
        inventory.refresh()
        self.page(INVENTORY)
        self.assertEqual(self.item("inventoryTotalWeight21").property("text"), "20.286 جرام")
        self.assertIn("إجمالي الوزن المكافئ عيار 21", self.labels(self.item("inventoryPage")))
        # SOLD barcode: a read-only card with its invoice, never part of the current list
        inventory.search("01261001")
        self.wait_until(lambda: inventory.hasHistoryPiece, "history card")
        card = self.visible_item("inventoryHistoryCard")
        self.assertIsNotNone(card)
        self.assertIn("مباعة", self.item("inventoryHistoryTitle").property("text"))
        self.assertIn("26100203", self.item("inventoryHistorySale").property("text"))
        opened = []
        history = self.controller.operational_viewmodels["invoiceHistoryVM"]
        history.openInvoice = lambda invoice_id: opened.append(invoice_id)
        self.click("inventoryHistoryOpenInvoice")
        self.assertEqual((self.root.property("currentPage"), opened), (1, ["15"]))
        # filters reach the service and combine with the search
        self.page(INVENTORY)
        inventory.setFilters(1, 2)
        self.assertEqual(calls[-1], {"search": "01261001", "karat_id": 1, "item_type_id": 2})
        for width, height in harness.SIZES:
            self.root.setWidth(width)
            self.root.setHeight(height)
            QTest.qWait(80)
            for name in ("inventoryKaratFilter", "inventoryItemTypeFilter"):
                self.assert_inside_window(self.item(name))
        # English digits: typed Arabic-Indic digits arrive as 0-9 in any field
        inventory.setFilters(0, 0)
        self.click(self.item("openStocktakeButton"))
        stocktake = self.item("stocktakeScanField")
        self.wait_until(lambda: stocktake.property("activeFocus"))
        for char in "٠١٢٦١٠٠١":
            for kind in (QEvent.KeyPress, QEvent.KeyRelease):
                QCoreApplication.sendEvent(self.root, QKeyEvent(kind, 0, Qt.NoModifier, char))
        QTest.qWait(30)
        self.assertEqual(stocktake.property("text"), "01261001")
        self.assertEqual(self.warnings, [])

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

    def test_a_piece_in_stock_can_have_its_type_and_karat_corrected_with_real_clicks(self):
        calls = []

        class Service:
            def get_stock_items(self, **kwargs):
                return []

            def get_stock_summary(self):
                return {}

            def correct_item(self, item_id, item_type_id, karat_id, reason):
                calls.append((item_id, item_type_id, karat_id, reason))
                return {"inventory_item_id": item_id}
        inventory = self.controller.operational_viewmodels["inventoryVM"]
        inventory._service = Service()
        self.page(INVENTORY)
        master = self.controller.operational_viewmodels["masterDataVM"]
        master._item_types = [{"id": 1, "name": "خاتم"}, {"id": 2, "name": "سلسلة"}]
        master._karats = [{"id": 1, "name": "عيار 21"}, {"id": 2, "name": "عيار 18"}]
        master.dataChanged.emit()
        item = {"inventory_item_id": 7, "item_type_id": master.itemTypes[0]["id"], "karat_id": master.karats[0]["id"], "item_type_name": "خاتم", "karat_value": 21,
                "weight_grams": 3.6, "size": "55", "factory_name": "النور", "category_code": "A", "barcode_value": "01261001", "stock_classification": "NEW_STOCK",
                "inventory_status": "IN_STOCK", "acquisition_source": "MANUAL_ENTRY", "acquired_at": "2026-10-01 10:00:00", "notes": ""}
        self.root.setProperty("selectedInventoryItem", item)
        details = self.item("inventoryItemDetailsDialog")
        for width, height in harness.SIZES:
            self.root.setWidth(width)
            self.root.setHeight(height)
            details.open()
            self.wait_until(lambda: details.property("opened"), "details")
            button = self.item("inventoryItemCorrectOpen")
            self.assertTrue(button.property("visible"))
            self.assert_inside_window(button)
            self.click(button)
            dialog = self.item("inventoryItemCorrectDialog")
            self.wait_until(lambda: dialog.property("opened"), "correction dialog")
            self.assertFalse(self.item("inventoryCorrectSave").property("enabled"))                  # the reason is mandatory
            self.choose("inventoryCorrectKarat", len(master.karats) - 1)
            self.type_into("inventoryCorrectReason", "اتدخلت بعيار غلط")
            self.wait_until(lambda: self.item("inventoryCorrectSave").property("enabled"), "can save")
            self.assert_inside_window(self.item("inventoryCorrectSave"))
            before = len(calls)
            self.click("inventoryCorrectSave")
            self.wait_until(lambda: len(calls) == before + 1, "correction sent")
            self.wait_until(lambda: not dialog.property("visible") and not details.property("visible"), "both dialogs close")
        self.assertEqual(calls[-1][0], 7)
        self.assertEqual(calls[-1][2], master.karats[-1]["id"])
        self.assertEqual(calls[-1][3], "اتدخلت بعيار غلط")
        sold = dict(item, inventory_status="SOLD")
        self.root.setProperty("selectedInventoryItem", sold)
        details.open()
        self.wait_until(lambda: details.property("opened"), "details")
        self.assertFalse(self.item("inventoryItemCorrectOpen").property("visible"))                  # a sold piece is never corrected
        details.close()
        self.assertEqual(self.warnings, [])

    def test_a_spoiled_piece_in_stock_is_converted_to_scrap_with_real_clicks(self):
        calls = []

        class Service:
            def get_stock_items(self, **kwargs):
                return []

            def get_stock_summary(self):
                return {}

            def convert_item_to_scrap(self, item_id, reason):
                calls.append((item_id, reason))
                return {"inventory_item_id": item_id}
        inventory = self.controller.operational_viewmodels["inventoryVM"]
        inventory._service = Service()
        self.page(INVENTORY)
        item = {"inventory_item_id": 397, "item_type_id": 1, "karat_id": 1, "item_type_name": "دبلة", "karat_value": 21, "weight_grams": 3.63, "size": "50",
                "factory_name": "لوكس", "category_code": "", "barcode_value": "67250122", "stock_classification": "NEW_STOCK", "inventory_status": "IN_STOCK",
                "acquisition_source": "LEGACY_MIGRATION", "acquired_at": "2025-02-05 00:00:00", "notes": ""}
        self.root.setProperty("selectedInventoryItem", item)
        details = self.item("inventoryItemDetailsDialog")
        for width, height in harness.SIZES:
            self.root.setWidth(width)
            self.root.setHeight(height)
            details.open()
            self.wait_until(lambda: details.property("opened"), "details")
            button = self.item("inventoryItemScrapOpen")
            self.assertTrue(button.property("visible"))
            self.assert_inside_window(button)
            self.click(button)
            dialog = self.item("inventoryItemScrapDialog")
            self.wait_until(lambda: dialog.property("opened"), "scrap dialog")
            self.assertFalse(self.item("inventoryScrapSave").property("enabled"))                       # the reason is mandatory
            self.type_into("inventoryScrapReason", "القطعة بايظة")
            self.wait_until(lambda: self.item("inventoryScrapSave").property("enabled"), "can save")
            self.assert_inside_window(self.item("inventoryScrapSave"))
            before = len(calls)
            self.click("inventoryScrapSave")
            self.wait_until(lambda: len(calls) == before + 1, "conversion sent")
            self.wait_until(lambda: not dialog.property("visible") and not details.property("visible"), "both dialogs close")
        self.assertEqual(calls[-1], (397, "القطعة بايظة"))
        self.root.setProperty("selectedInventoryItem", dict(item, inventory_status="SOLD"))
        details.open()
        self.wait_until(lambda: details.property("opened"), "details")
        self.assertFalse(self.item("inventoryItemScrapOpen").property("visible"))                       # only a piece in stock
        details.close()
        self.assertEqual(self.warnings, [])

    def test_supplier_items_are_labelled_tawrid_mawrid_never_market_purchase(self):
        self.page(INVENTORY)
        dialog = self.item("inventoryItemDetailsDialog")
        base = {"inventory_item_id": 7, "item_type_name": "خاتم", "karat_value": 21, "weight_grams": 3.6, "size": "55",
                "factory_name": "النور للتصنيع", "category_code": "A", "barcode_value": "01261001",
                "stock_classification": "NEW_STOCK", "inventory_status": "IN_STOCK",
                "acquired_at": "2026-10-01 10:00:00", "notes": ""}
        expected = {"SUPPLIER_PURCHASE": "توريد مورد", "MARKET_PURCHASE": "شراء من السوق",
                    "MANUAL_ENTRY": "إدخال يدوي", "OLD_GOLD_MANUFACTURE": "محولة من الكسر"}
        for source, label in expected.items():
            with self.subTest(source=source):
                self.root.setProperty("selectedInventoryItem", {**base, "acquisition_source": source})
                dialog.open()
                self.wait_until(lambda: dialog.property("opened"), "details")
                texts = self.labels(dialog.property("contentItem"))
                self.assertIn(label, texts)
                if source == "SUPPLIER_PURCHASE":
                    self.assertNotIn("شراء من السوق", texts)
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

    def all_visual(self, name, parent=None, found=None):
        found = [] if found is None else found
        parent = parent or self.root.contentItem()
        if parent.objectName() == name:
            found.append(parent)
        for child in self.children(parent):
            self.all_visual(name, child, found)
        return found

    def selection(self, page):
        selected = page.property("selectedItemIds")
        selected = selected.toVariant() if hasattr(selected, "toVariant") else selected
        return [int(x) for x in selected]

    def test_successful_print_keeps_the_same_barcode_and_clears_selection(self):
        page, barcode, items, inventory = self.print_setup()
        page.setProperty("selectedItemIds", [7])
        QTest.qWait(50)
        review = self.item("barcodeReviewPrintButton")
        self.assertEqual(review.property("text"), "🖨  مراجعة وطباعة")
        self.click(review)
        dialog = self.item("barcodePrintQueueDialog")
        self.wait_until(lambda: dialog.property("opened"), "print queue")
        self.assertEqual((barcode.missingBarcodeCount, barcode.existingBarcodeCount), (0, 1))
        self.click("printBarcodeNowButton")
        self.wait_until(lambda: not dialog.property("visible"), "queue closed")
        self.assertEqual(self.selection(page), [])
        self.assertEqual((items[0]["barcode_value"], barcode._service.allocated), ("01261001", 0))   # SAME barcode
        self.assertIsNone(items[1]["barcode_value"])                         # بدون باركود stays as it is
        self.assertEqual(inventory.withoutBarcodeCount, 1)
        self.assertEqual(self.warnings, [])

    def test_failed_print_keeps_selection_and_retry_reuses_the_same_barcode(self):
        page, barcode, items, inventory = self.print_setup()
        barcode._service.fail = True
        page.setProperty("selectedItemIds", [7])
        QTest.qWait(50)
        self.click("barcodeReviewPrintButton")
        dialog = self.item("barcodePrintQueueDialog")
        self.wait_until(lambda: dialog.property("opened"), "print queue")
        self.click("printBarcodeNowButton")
        QTest.qWait(80)
        self.assertTrue(dialog.property("visible"))                          # stays open with the error
        self.assertEqual(self.selection(page), [7])
        self.assertFalse(barcode.actionSuccess)
        barcode._service.fail = False
        self.click("printBarcodeNowButton")                                  # retry
        self.wait_until(lambda: not dialog.property("visible"), "queue closed")
        self.assertEqual((barcode._service.allocated, items[0]["barcode_value"]), (0, "01261001"))
        self.assertEqual(self.warnings, [])

    def test_a_piece_without_barcode_cannot_be_selected_or_printed(self):
        page, barcode, items, inventory = self.print_setup()
        QTest.qWait(150)
        # No checkbox at all for the barcode-less piece (#8); the barcoded one (#7) keeps its checkbox.
        boxes = [b for b in self.all_visual("inventorySelectBox") if b.property("visible")]
        self.assertEqual(len(boxes), 1)
        # Select All skips it.
        page.setProperty("barcodeFilterMode", 0)
        page.selectAllVisible()
        self.assertEqual(self.selection(page), [7])
        # Even a programmatic selection of it is refused.
        page.setSelected(8, True)
        self.assertEqual(self.selection(page), [7])
        page.clearSelection()
        page.setSelected(8, True)
        self.assertEqual(self.selection(page), [])
        self.assertFalse(self.item("barcodeReviewPrintButton").property("enabled"))   # nothing selected: no print
        # The old «تحديد بدون باركود» shortcut no longer exists.
        self.assertNotIn("تحديد بدون باركود", self.labels(self.item("inventoryPage")))
        self.assertEqual(barcode._service.queued, [])                         # nothing reached Review & Print
        # The service layer refuses it too (a label is never consumed).
        with self.assertRaises(ValueError):
            barcode._service.get_queue([8])
        self.assertEqual(self.warnings, [])

    def test_inventory_rows_are_compact_one_line_rows(self):
        page, barcode, items, inventory = self.print_setup()
        QTest.qWait(150)
        for width, height in harness.SIZES:
            with self.subTest(size=(width, height)):
                self.root.setWidth(width)
                self.root.setHeight(height)
                QTest.qWait(120)
                numbers = [n for n in self.all_visual("inventoryItemNumber") if n.property("visible")]
                self.assertEqual(len(numbers), 2)
                row = numbers[0].parentItem().parentItem()
                self.assertLessEqual(row.height(), 46)                       # was 116
                for text in self.all_visual("inventoryKaratWeight"):
                    self.assertIn(" جرام", text.property("text"))              # the unit sits beside the weight
                    self.assertLessEqual(text.property("contentWidth"), text.width() + 1)
                self.assertEqual(sorted(t.property("text") for t in self.all_visual("inventoryStatusText")
                                        if t.property("visible")), ["—", "—"])      # stand-in rows carry no status
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
                self.assertEqual(self.item("stockBalanceTotalWeight").property("text"), "إجمالي الوزن المكافئ عيار 21: 1,294.558 جرام")
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

    def test_approving_the_stocktake_writes_off_the_remaining_with_real_clicks(self):
        vm = self.controller.operational_viewmodels["stocktakeVM"]
        adjust = self.controller.operational_viewmodels["stocktakeAdjustVM"]
        calls = []

        class FakeAdjust:
            def can_adjust(self):
                return True

            def approve(self, *args, **kwargs):
                calls.append(args)
                return {"code": "SK000001", "id": "1", "written_off_count": len(args[3]), "written_off_grams": D("10.000"), "replayed": False}
        adjust._service = FakeAdjust()
        adjust.changed.emit()
        vm._service = service_for(Store(MIXED))
        self.page(INVENTORY)
        self.click("openStocktakeButton")
        self.wait_until(lambda: self.root.property("currentPage") == STOCKTAKE and vm.loaded, "stocktake opened")
        approve = self.item("stocktakeApprove")
        self.assertFalse(approve.property("enabled"))                          # nothing scanned yet
        self.scan("02261001")
        QTest.qWait(200)
        self.assertGreater(vm.withoutBarcodeCount, 0)
        self.assertFalse(approve.property("enabled"))                          # pieces without a barcode can never be scanned: the whole count is not approvable
        vm._service = service_for(Store([p for p in MIXED if p.get("barcode_value")]))
        vm.start()
        self.wait_until(lambda: vm.loaded and vm.withoutBarcodeCount == 0, "only barcoded pieces")
        self.scan("02261001")
        self.wait_until(lambda: approve.property("enabled"), "approve enabled")
        remaining_before = vm.remainingCount
        for width, height in harness.SIZES:
            self.root.setWidth(width)
            self.root.setHeight(height)
            QTest.qWait(150)
            self.assert_inside_window(approve)
        self.click("stocktakeApprove")
        dialog = self.item("stocktakeApproveDialog")
        self.wait_until(lambda: dialog.property("opened"), "dialog")
        self.assertFalse(self.item("stocktakeApproveOk").property("enabled"))  # the confirmation is required
        self.click("stocktakeConfirmCheck")
        self.wait_until(lambda: self.item("stocktakeApproveOk").property("enabled"), "confirmed")
        self.click("stocktakeApproveOk")
        self.wait_until(lambda: len(calls) == 1 and not dialog.property("visible"), "approved")
        args = calls[0]
        self.assertEqual((len(args[2]), len(args[3]), args[4]), (1, remaining_before, "SOLD_OUTSIDE"))
        self.assertIn("SK000001", self.item("stocktakeDone").property("text"))
        self.assertEqual(self.warnings, [])

    def test_a_remaining_row_can_be_deleted_from_the_stock_with_real_clicks(self):
        vm = self.controller.operational_viewmodels["stocktakeVM"]
        adjust = self.controller.operational_viewmodels["stocktakeAdjustVM"]
        calls = []

        class FakeAdjust:
            def can_adjust(self):
                return True

            def write_off_pieces(self, ids, reason, note, confirmed, key, **kwargs):
                calls.append((list(ids), reason, confirmed))
                return {"code": "SK000002", "id": "2", "written_off_count": len(ids), "written_off_grams": D("1.000"), "replayed": False}
        adjust._service = FakeAdjust()
        adjust.changed.emit()
        vm._service = service_for(Store(MIXED))
        self.page(INVENTORY)
        self.click("openStocktakeButton")
        self.wait_until(lambda: self.root.property("currentPage") == STOCKTAKE and vm.loaded, "stocktake opened")
        first = vm.remainingRows[0]["id"]
        before = vm.remainingCount
        self.wait_until(lambda: self.visual_item("stocktakeRemainingDelete_" + first) is not None, "the delete button")
        self.click("stocktakeRemainingDelete_" + first)
        dialog = self.item("stocktakeApproveDialog")
        self.wait_until(lambda: dialog.property("opened"), "dialog")
        self.assertNotEqual(self.item("stocktakePieceInfo").property("text"), "")
        self.click("stocktakeConfirmCheck")
        self.wait_until(lambda: self.item("stocktakeApproveOk").property("enabled"), "confirmed")
        self.click("stocktakeApproveOk")
        self.wait_until(lambda: len(calls) == 1 and not dialog.property("visible"), "written off")
        self.assertEqual(calls[0], ([first], "SOLD_OUTSIDE", True))
        self.assertEqual((vm.remainingCount, first in [r["id"] for r in vm.remainingRows]), (before - 1, False))
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
