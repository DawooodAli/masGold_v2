"""سجل الفواتير on the real Main.qml (Billing workspace tab): real clicks, keyboard, sizes.

Runs on the Billing harness (blocked database, fake login) with the history fixtures of
test_invoice_history; screenshots are written when MASGOLD_UI_REVIEW_DIR is set."""
import os
from pathlib import Path
import unittest

from PySide6.QtCore import QPointF, Qt
from PySide6.QtTest import QTest

from tests import test_invoice_page_qml as page_harness


class InvoiceHistoryPageTests(page_harness.InvoicePageTests):
    def open_history(self):
        self.root.setProperty("currentPage", 1)
        self.click("billingTabHistory")
        self.wait_until(lambda: self.item("invoiceHistoryPage").property("visible"), "history tab not shown")
        vm = self.controller.operational_viewmodels["invoiceHistoryVM"]
        self.wait_until(lambda: vm.results, "history not loaded")
        QTest.qWait(80)
        return vm

    def shot(self, name):
        target = os.getenv("MASGOLD_UI_REVIEW_DIR")
        if target:
            Path(target).mkdir(parents=True, exist_ok=True)
            self.root.grabWindow().save(str(Path(target) / f"history-{name}.png"))

    def test_history_list_opens_detail_and_reprints_at_every_size(self):
        vm = self.open_history()
        for width, height in ((1440, 900), (1280, 800), (1080, 680)):
            self.root.setWidth(width)
            self.root.setHeight(height)
            QTest.qWait(250)
            row = self.visual_item("historyRow_0")
            self.assertIsNotNone(row)
            self.assert_inside_window(row)
            for name in ("historySearch", "historyToday", "historyNext"):
                self.assert_inside_window(self.item(name))
            # Every visible cell of the first row, including the invoice number, is inside the window.
            number = self.visual_item("historyNumber_0")       # the first line-row of the invoice group
            self.assert_inside_window(number)
            self.assertLessEqual(number.property("contentWidth"), number.property("width") + 1, f"number clipped at {width}")
            self.shot(f"list-{width}x{height}")
            # Double-click opens the invoice (as finalized).
            point = row.mapToScene(QPointF(row.width() / 2, row.height() / 2)).toPoint()
            QTest.mouseDClick(self.root, Qt.LeftButton, Qt.NoModifier, point)
            self.wait_until(lambda: vm.detail, "detail not opened")
            QTest.qWait(150)
            self.assertEqual(vm.detail["invoiceTotal"], "90,000 ج")
            for name in ("historyHeaderSection", "historyCustomerSection", "historyMoneySection",
                         "historyItemsSection", "historyPricesSection"):
                self.assertTrue(self.item(name).property("visible"), f"{name} at {width}x{height}")
            # Owner layout: no separate payment card and no visible operations log.
            for name in ("historyPaymentsSection", "historyTimelineSection"):
                self.assertIsNone(self.visual_item(name), f"{name} still rendered at {width}x{height}")
            # The final strip is the last element, below the sold items.
            strip, items = self.item("historyHeaderSection"), self.item("historyItemsSection")
            self.assertGreater(strip.mapToScene(QPointF(0, 0)).y(), items.mapToScene(QPointF(0, 0)).y())
            reprint = self.item("historyReprint")
            self.assert_inside_window(reprint)                                  # never clipped
            self.shot(f"detail-{width}x{height}")
            QTest.keyClick(self.root, Qt.Key_Escape)                            # back to the list
            self.wait_until(lambda: not vm.detailOpen, "Esc did not close the detail")
        # Reprint: one call to the frozen print path (a fake here), never a save.
        QTest.mouseDClick(self.root, Qt.LeftButton, Qt.NoModifier,
                          self.visual_item("historyRow_0").mapToScene(QPointF(40, 12)).toPoint())
        self.wait_until(lambda: vm.detail)
        self.click("historyReprint")
        self.wait_until(lambda: vm.reprintMessage and not vm.reprintBusy, "no reprint result")
        self.assertEqual([c for c in self.history.calls if c[0] == "reprint"], [("reprint", "21")])
        self.assertEqual(self.history.printer.calls, ["21"])
        self.assertEqual(self.store.invoices, [])                               # nothing saved anywhere
        self.assertEqual(self.warnings, [])

    def test_reprint_shows_busy_ignores_repeats_and_recovers_at_every_size(self):
        import threading
        vm = self.open_history()
        gate = self.history.printer.gate = threading.Event()
        self.addCleanup(gate.set)
        QTest.mouseDClick(self.root, Qt.LeftButton, Qt.NoModifier,
                          self.visual_item("historyRow_0").mapToScene(QPointF(40, 12)).toPoint())
        self.wait_until(lambda: vm.detail)
        calls_before = len(self.history.printer.calls)
        for width, height in ((1440, 900), (1280, 800), (1080, 680)):
            with self.subTest(size=(width, height)):
                self.root.setWidth(width)
                self.root.setHeight(height)
                QTest.qWait(200)
                button = self.item("historyReprint")
                self.assertEqual(button.property("text"), "إعادة طباعة")
                self.assertTrue(button.property("enabled"))
                gate.clear()
                self.click(button)
                self.assertTrue(vm.reprintBusy)
                QTest.qWait(60)
                # busy: button disabled with the in-progress label, spinner + message visible and inside the window
                self.assertFalse(button.property("enabled"))
                self.assertEqual(button.property("text"), "جاري الطباعة...")
                spinner, message = self.item("historyReprintSpinner"), self.item("historyReprintMessage")
                self.assertTrue(spinner.property("visible"))
                self.assertTrue(message.property("visible"))
                self.assertEqual(message.property("text"), "جاري إرسال أمر الطباعة...")
                for item in (button, spinner, message):
                    self.assert_inside_window(item)
                # a second real click and an Enter/Space press on the focused button do nothing
                point = button.mapToScene(QPointF(button.width() / 2, button.height() / 2)).toPoint()
                QTest.mouseClick(self.root, Qt.LeftButton, Qt.NoModifier, point)
                QTest.keyClick(self.root, Qt.Key_Space)
                QTest.keyClick(self.root, Qt.Key_Return)
                vm.reprint()                                           # even a duplicate slot emission
                QTest.qWait(80)
                self.assertEqual(len(self.history.printer.calls), calls_before + 1)
                gate.set()
                self.wait_until(lambda: not vm.reprintBusy, "reprint never finished")
                QTest.qWait(60)
                calls_before += 1
                self.assertEqual(message.property("text"), "تم إرسال الفاتورة للطابعة.")
                self.assertTrue(button.property("enabled"))
                self.assertEqual(button.property("text"), "إعادة طباعة")
                self.assertFalse(spinner.property("visible"))
                self.assert_inside_window(message)
        self.assertEqual(self.store.invoices, [])
        self.assertEqual(self.warnings, [])

    def test_reprint_failure_shows_arabic_message_and_unlocks(self):
        import threading
        vm = self.open_history()
        gate = self.history.printer.gate = threading.Event()
        gate.set()
        self.addCleanup(gate.set)
        self.history.printer.printed = False
        QTest.mouseDClick(self.root, Qt.LeftButton, Qt.NoModifier,
                          self.visual_item("historyRow_0").mapToScene(QPointF(40, 12)).toPoint())
        self.wait_until(lambda: vm.detail)
        self.click("historyReprint")
        self.wait_until(lambda: vm.reprintFailed and not vm.reprintBusy, "no failure shown")
        QTest.qWait(60)
        self.assertEqual(self.item("historyReprintMessage").property("text"),
                         "تعذر إرسال الفاتورة للطابعة. الطابعة غير متصلة.")
        button = self.item("historyReprint")
        self.assertTrue(button.property("enabled"))
        self.history.printer.printed = True
        self.click(button)                                              # a second attempt is allowed
        self.wait_until(lambda: len(self.history.printer.calls) == 2 and not vm.reprintBusy)
        self.assertFalse(vm.reprintFailed)
        self.assertEqual(self.warnings, [])

    def test_history_piece_barcode_correction_button_and_dialog_at_every_size(self):
        vm = self.open_history()
        vm.openInvoice(vm.results[0]["invoiceId"])
        self.wait_until(lambda: vm.detail, "detail not opened")
        vm._detail["can_correct"] = True
        vm._detail["sale_lines"][0].update(kind="INVENTORY", inventory_item_id="7", line_id="9", barcode="37260106", notes="")
        vm.changed.emit()
        calls = []
        vm.correctPiece = lambda *args: calls.append(args)             # the service is covered by tests/test_invoice_piece_correction.py
        for width, height in ((1440, 900), (1280, 800), (1080, 680)):
            self.root.setWidth(width)
            self.root.setHeight(height)
            QTest.qWait(250)
            button = self.visual_item("historyPieceCorrect_0")
            self.assertIsNotNone(button, f"no correction button at {width}x{height}")
            self.assert_inside_window(button)
            self.click(button)
            QTest.qWait(120)
            dialog = self.item("historyPieceDialog")
            self.wait_until(lambda: dialog.property("opened"), f"dialog not opened at {width}x{height}")
            save = self.item("historyPieceSave")
            for name in ("historyPieceBarcode", "historyPieceReason", "historyPieceConfirm", "historyPieceSave", "historyPieceCancel"):
                self.assert_inside_window(self.item(name))
            self.assertFalse(save.property("enabled"))
            self.item("historyPieceBarcode").setProperty("text", "40260107")
            self.item("historyPieceReason").setProperty("text", "سبب")
            self.assertFalse(save.property("enabled"))                  # the confirmation is still missing
            self.click("historyPieceConfirm")
            QTest.qWait(60)
            self.assertTrue(save.property("enabled"))
            self.click("historyPieceCancel")
            self.wait_until(lambda: not dialog.property("visible"), f"dialog not closed at {width}x{height}")
        self.assertEqual(calls, [])
        self.assertEqual(self.warnings, [])

    def test_a_mixed_invoice_prints_at_its_full_sale_total_by_a_real_click_at_every_size(self):
        from datetime import datetime
        from decimal import Decimal
        vm = self.open_history()
        QTest.mouseDClick(self.root, Qt.LeftButton, Qt.NoModifier,
                          self.visual_item("historyRow_0").mapToScene(QPointF(40, 12)).toPoint())
        self.wait_until(lambda: vm.detail, "detail not opened")
        for width, height in ((1440, 900), (1280, 800), (1080, 680)):
            self.root.setWidth(width)
            self.root.setHeight(height)
            QTest.qWait(250)
            button = self.item("historyReprintSaleTotal")
            self.assertTrue(button.property("visible"))                         # the fixture sells pieces and buys old gold
            self.assertEqual(button.property("text"), "طباعة بإجمالي البيع")
            for name in ("historyReprintSaleTotal", "historyReprint", "historyBack"):
                self.assert_inside_window(self.item(name))
            before = len(self.history.printer.calls)
            self.click(button)
            self.wait_until(lambda: len(self.history.printer.calls) == before + 1 and not vm.reprintBusy, f"no paper at {width}")
            self.assertEqual(self.history.printer.modes[-1], {"sale_total": True, "reprint": True})
            self.wait_until(lambda: vm.reprintMessage == "تم إرسال الفاتورة للطابعة.")
        # The latest full-total paper is shown in the summary strip (amount, when, who); the detail reload after the paper finishes first.
        self.wait_until(lambda: not vm.detailBusy, "detail reload never finished")
        vm._detail = dict(vm._detail, sale_total_prints=[
            {"occurred_at": datetime(2026, 11, 12, 14, 3), "actor": "admin", "total": Decimal("69280.00")}])
        vm.changed.emit()
        QTest.qWait(150)
        figure = self.visual_item("historySaleTotalPrint")
        self.assertIsNotNone(figure)
        self.assertTrue(figure.property("visible"))
        flick = self.item("historyDetailScroll").property("contentItem")      # the strip is the last element: scrolled to, as the employee does
        flick.setProperty("contentY", float(max(0.0, flick.property("contentHeight") - flick.height())))
        QTest.qWait(120)
        self.assert_inside_window(figure)
        self.shot("sale-total-1080x680")
        self.assertEqual(self.store.invoices, [])                               # nothing saved anywhere
        self.assertEqual(self.warnings, [])

    def test_type_and_karat_filters_by_real_clicks_at_every_size_and_clear_resets_them(self):
        vm = self.open_history()
        self.wait_until(lambda: len(vm.itemTypeChoices) > 1 and len(vm.karatChoices) > 1, "choices not loaded")
        for width, height in ((1440, 900), (1280, 800), (1080, 680)):
            self.root.setWidth(width)
            self.root.setHeight(height)
            QTest.qWait(250)
            for name in ("historyItemType", "historyKarat", "historyClear"):
                self.assert_inside_window(self.item(name))
            dabla = next(i for i, c in enumerate(vm.itemTypeChoices) if c["code"] == "دبلة")
            k18 = next(i for i, c in enumerate(vm.karatChoices) if c["code"] == "18")
            self.choose("historyItemType", dabla)
            self.choose("historyKarat", k18)
            self.wait_until(lambda: any(c[1].get("item_type") == "دبلة" and c[1].get("karat") == 18
                                        for c in self.history.calls if c[0] == "search"), f"no filtered search at {width}")
            self.wait_until(lambda: not vm.busy)
            QTest.qWait(80)
            # The choice stays shown after the search came back (the list is not rebuilt by every search).
            self.assertEqual(self.item("historyItemType").property("currentText"), "دبلة")
            self.assertEqual(self.item("historyKarat").property("currentText"), "عيار 18")
            self.shot(f"filters-{width}x{height}")
            self.click("historyClear")
            self.wait_until(lambda: vm.itemTypeFilter == "" and vm.karatFilter == "", f"clear did not reset at {width}")
            self.assertEqual(self.item("historyItemType").property("currentText"), "كل الأصناف")
            self.assertEqual(self.item("historyKarat").property("currentText"), "كل العيارات")
        self.assertEqual(self.warnings, [])

    def test_history_keyboard_enter_opens_the_selected_invoice(self):
        vm = self.open_history()
        self.click("historyRow_0")
        QTest.keyClick(self.root, Qt.Key_Return)
        self.wait_until(lambda: vm.detail, "Enter did not open the invoice")
        self.assertEqual(vm.detail["customerName"], "على عبدالرحمن")

    def test_new_invoice_tab_is_unchanged(self):
        self.root.setProperty("currentPage", 1)
        self.click("billingTabNew")
        self.wait_until(lambda: self.item("invoiceBarcodeField").property("visible"))


    # ------------------------------------------------------------ printed invoice barcode → exact invoice
    def scan_into_search(self, number, layout):
        from PySide6.QtCore import QEvent
        from PySide6.QtGui import QGuiApplication, QKeyEvent
        from tests.test_barcode_input import strokes_for
        field = self.item("historySearch")
        self.wait_until(lambda: field.property("activeFocus"), "search box not focused")
        field.setProperty("text", "")
        for code, shift, text in strokes_for(number, layout):
            for kind in (QEvent.KeyPress, QEvent.KeyRelease):
                QGuiApplication.sendEvent(self.root, QKeyEvent(kind, 0, Qt.NoModifier, code, 0, 0, text))
        for kind in (QEvent.KeyPress, QEvent.KeyRelease):
            QGuiApplication.sendEvent(self.root, QKeyEvent(kind, Qt.Key_Return, Qt.NoModifier, 0x1C, 0, 0, "\r"))

    def test_history_scan_or_typed_invoice_number_opens_it(self):
        vm = self.open_history()
        sales_before = self.vm.saleCount
        for layout in ("en", "ar"):                                    # the same printed barcode, both layouts
            self.scan_into_search("26092701", layout)
            self.wait_until(lambda: vm.detail and vm.detail.get("number") == "26092701", f"{layout} scan did not open")
            QTest.keyClick(self.root, Qt.Key_Escape)
            self.wait_until(lambda: not vm.detailOpen)
        self.wait_until(lambda: self.item("historySearch").property("activeFocus"), "focus not back on search")
        self.type_into("historySearch", "26092701")                     # typed by hand + Enter
        QTest.keyClick(self.root, Qt.Key_Return)
        self.wait_until(lambda: vm.detail and vm.detail.get("number") == "26092701", "typed number did not open")
        self.assertEqual(self.vm.saleCount, sales_before)               # never an inventory / Billing lookup
        self.assertFalse([c for c in self.history.calls if c[0] not in ("search", "exact", "detail", "options")])
        self.assertEqual(self.warnings, [])

    def test_history_exact_invoice_number_field_opens_the_invoice_whatever_the_range(self):
        vm = self.open_history()
        self.root.setWidth(1080)
        self.root.setHeight(680)
        QTest.qWait(200)
        for name in ("historyNumberField", "historyNumberOpen"):
            self.assert_inside_window(self.item(name))                         # visible at the smallest size
        vm.setDateRange("2026-01-01", "2026-01-02")                           # a range that excludes the invoice
        QTest.qWait(150)
        searches = len([c for c in self.history.calls if c[0] == "search"])
        before = (vm.dateFrom, vm.dateTo, vm.searchText)
        # Real typing + Enter: opens the existing detail directly.
        self.click("historyNumberField")
        self.type_text("26092701")
        QTest.keyClick(self.root, Qt.Key_Return)
        self.wait_until(lambda: vm.detail and vm.detail.get("number") == "26092701", "exact number did not open")
        self.assertEqual((vm.dateFrom, vm.dateTo, vm.searchText), before)      # the range was never touched
        self.assertEqual(len([c for c in self.history.calls if c[0] == "search"]), searches)
        QTest.qWait(200)
        # The historical price card shows the invoice's own time.
        stamp = self.item("historyPricesTimestamp")
        self.assertEqual(stamp.property("text"), vm.priceSnapshotTime)
        self.assertIn("2026-09-27", stamp.property("text"))
        QTest.keyClick(self.root, Qt.Key_Escape)
        self.wait_until(lambda: not vm.detailOpen)
        # A missing number is a clear message and changes nothing.
        field = self.item("historyNumberField")
        field.setProperty("text", "")
        self.click(field)
        self.type_text("99999999")
        self.click("historyNumberOpen")
        self.wait_until(lambda: vm.lookupMessage, "no message")
        self.assertEqual(self.item("historyNumberMessage").property("text"), "لم يتم العثور على فاتورة برقم 99999999.")
        self.assertFalse(vm.detailOpen)
        self.assertEqual(self.warnings, [])


def load_tests(loader, tests, pattern):
    suite = unittest.TestSuite()
    suite.addTests(InvoiceHistoryPageTests(n) for n in loader.getTestCaseNames(InvoiceHistoryPageTests)
                   if n.startswith("test_history") or n == "test_new_invoice_tab_is_unchanged")
    return suite
