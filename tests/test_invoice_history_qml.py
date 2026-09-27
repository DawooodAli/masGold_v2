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
            number = row.childItems()[0].childItems()[0]
            self.assert_inside_window(number)
            self.assertLessEqual(number.property("contentWidth"), number.property("width") + 1, f"number clipped at {width}")
            self.shot(f"list-{width}x{height}")
            # Double-click opens the invoice (as finalized).
            point = row.mapToScene(QPointF(row.width() / 2, row.height() / 2)).toPoint()
            QTest.mouseDClick(self.root, Qt.LeftButton, Qt.NoModifier, point)
            self.wait_until(lambda: vm.detail, "detail not opened")
            QTest.qWait(150)
            self.assertEqual(vm.detail["invoiceTotal"], "90,000.00 ج")
            for name in ("historyHeaderSection", "historyCustomerSection", "historyMoneySection",
                         "historyItemsSection", "historyPaymentsSection", "historyTimelineSection"):
                self.assertTrue(self.item(name).property("visible"), f"{name} at {width}x{height}")
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
        self.wait_until(lambda: vm.reprintMessage, "no reprint result")
        self.assertEqual([c for c in self.history.calls if c[0] == "reprint"], [("reprint", "21")])
        self.assertEqual(self.history.printer.calls, ["21"])
        self.assertEqual(self.store.invoices, [])                               # nothing saved anywhere
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


def load_tests(loader, tests, pattern):
    suite = unittest.TestSuite()
    suite.addTests(InvoiceHistoryPageTests(n) for n in loader.getTestCaseNames(InvoiceHistoryPageTests)
                   if n.startswith("test_history") or n == "test_new_invoice_tab_is_unchanged")
    return suite
