"""Billing V2 page: real clicks and key presses on the real Main.qml/InvoicePage.qml.

The live database is blocked. The page runs the real InvoiceViewModel and the
real InvoiceService (calculation, rules, finalization flow) over the in-memory
store used by test_invoice_service.py. Screenshots are written when
MASGOLD_UI_REVIEW_DIR is set.
"""
from contextlib import contextmanager
import copy
from decimal import Decimal
import os
from pathlib import Path
import unittest
from unittest.mock import patch

from PySide6.QtCore import QCoreApplication, QEvent, QObject, QPointF, Qt
from PySide6.QtGui import QFont, QFontDatabase, QGuiApplication
from PySide6.QtQuickControls2 import QQuickStyle
from PySide6.QtTest import QTest

from app.viewmodels.invoice_viewmodel import InvoiceViewModel
from main import ApplicationController, create_operational_viewmodels
from services.authentication_service import AuthenticationResult, _VERIFIED
from services.invoice_service import InvoiceService
from tests import test_authentication_qml as authentication_fixture
from tests.test_invoice_service import (
    Authorization, Connection, CustomerRepo, DeadCashRepo, InvoiceRepo, Prices, Store,
)


D = Decimal
SIZES = ((1440, 900), (1280, 800), (1080, 680))
PERMISSIONS = frozenset({"APP_LOGIN", "INVOICE_CREATE", "INVOICE_FINALIZE", "PAYMENT_RECORD", "SETTINGS_MANAGE"})


class UiInvoiceRepo(InvoiceRepo):
    def find_item_id_by_barcode(self, barcode):
        return next((item_id for item_id, item in self.store.items.items()
                     if item["barcode_value"] == barcode), None)


class PickerService:
    """CustomerService stand-in for the real CustomerPicker."""

    def __init__(self, store):
        self.store = store

    def _row(self, customer):
        return {"customer_id": int(customer["customer_id"]), "customer_code": customer["customer_code"],
                "name": customer["name"], "mobile": None, "place": None, "is_active": customer["is_active"],
                "balance": D("0.00"), "balance_status": "SETTLED", "active_warning_count": 0,
                "has_active_dead_cash_warning": int(customer["customer_id"]) in self.store.warnings,
                "has_dead_cash_history": False, "unreversed_dead_cash_count": 0,
                "customer_type_code": "REGULAR", "last_transaction_at": None}

    def search_customers(self, search="", status="ACTIVE", page=1, page_size=25, **kwargs):
        rows = [self._row(c) for c in self.store.customers.values()
                if c["record_origin"] != "LEGACY_PLACEHOLDER" and c["is_active"]]
        return {"items": rows, "page": 1, "has_more": False}

    def get_customer_for_selection(self, customer_id, allow_inactive=False):
        return self._row(self.store.customers[str(customer_id)])


class InvoicePageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if QGuiApplication.instance() is None:
            QQuickStyle.setStyle("Material")
        cls.app = QGuiApplication.instance() or QGuiApplication([])
        cls.app.setQuitOnLastWindowClosed(False)
        cls.app.setLayoutDirection(Qt.RightToLeft)
        fonts = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"
        if "Cairo" not in QFontDatabase.families() and (fonts / "arial.ttf").is_file():
            QFontDatabase.addApplicationFont(str(fonts / "arial.ttf"))
            QFont.insertSubstitution("Cairo", "Arial")

    def setUp(self):
        block = patch("database.connection.DatabaseConnection.connect",
                      side_effect=AssertionError("Live database forbidden"))
        block.start()
        self.addCleanup(block.stop)
        picker = patch("app.viewmodels.customer_picker_viewmodel.CustomerService",
                       side_effect=lambda *a, **k: PickerService(self.store))
        picker.start()
        self.addCleanup(picker.stop)
        self.store, self.prices, self.commits = Store(), Prices(), 0
        self.v1_calls = []

        def factory():
            empty = authentication_fixture.EmptyService
            v1 = type("V1", (), {"__getattr__": lambda s, n: lambda *a, **k: self.v1_calls.append(n) or []})
            with patch("app.viewmodels.master_data_viewmodel.MasterDataService", empty), \
                    patch("app.viewmodels.inventory_viewmodel.InventoryService", empty), \
                    patch("app.viewmodels.old_gold_conversion_viewmodel.OldGoldConversionService", empty), \
                    patch("app.viewmodels.barcode_viewmodel.BarcodePrintService", empty), \
                    patch("app.viewmodels.billing_viewmodel.BillingService", v1):
                return create_operational_viewmodels()

        @contextmanager
        def transaction():
            saved = copy.deepcopy(self.store.tables())
            try:
                yield Connection(self.store, self.clock_offset)
                self.commits += 1
            except BaseException:
                for key, value in saved.items():
                    setattr(self.store, key, value)
                raise

        from datetime import timedelta
        self.clock_offset = timedelta(hours=3)
        master = type("Master", (), {"get_employees": lambda s: [
            {"id": 5, "employee_code": "E5", "name": "علي", "notes": None}]})()

        def invoice_factory(session):
            service = InvoiceService(session, gold_prices=self.prices,
                                     authorization=Authorization(session, PERMISSIONS),
                                     transaction_factory=transaction, invoice_repository=UiInvoiceRepo,
                                     customer_repository=CustomerRepo, dead_cash_repository=DeadCashRepo)
            return InvoiceViewModel(session, service=service, master_data=master, preview_delay_ms=20)

        self.controller = ApplicationController(self.app, authentication_service=authentication_fixture.FakeAuthentication(),
                                                operational_factory=factory,
                                                gold_price_service=authentication_fixture.EmptyGoldPrices(),
                                                invoice_factory=invoice_factory)
        self.wait_until(lambda: self.controller.state == "LOGIN")
        session = self.controller.session
        session._establish(AuthenticationResult(17, 7, "موظف", PERMISSIONS, _VERIFIED), int(session.generation))
        self.controller._open_operational()
        self.root = self.controller.operational_engine.rootObjects()[0]
        self.warnings = []
        self.controller.operational_engine.warnings.connect(
            lambda errors: self.warnings.extend(e.toString() for e in errors))
        self.vm = self.controller.operational_viewmodels["invoiceVM"]
        self.root.setWidth(1280)
        self.root.setHeight(800)
        self.root.setProperty("currentPage", 1)
        self.wait_until(lambda: len(self.vm.priceRows) == 3 and len(self.vm.salespeople) == 1 and not self.vm.busy)

    def tearDown(self):
        self.vm.shutdown()
        for engine in (self.controller.login_engine, self.controller.setup_engine, self.controller.operational_engine):
            for root in (engine.rootObjects() if engine is not None else []):
                root.hide()
        self.controller.authentication.shutdown()
        self.controller.gold_prices.shutdown()
        QTest.qWait(30)
        self.controller.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)

    # ------------------------------------------------------------ helpers

    def wait_until(self, predicate, message="timed out"):
        for _ in range(250):
            if predicate():
                return
            QTest.qWait(20)
        self.fail(message)

    def visual_item(self, name, parent=None):
        # Repeater delegates live only in the visual tree, not the QObject tree.
        parent = parent or self.root.contentItem()
        if parent.objectName() == name:
            return parent
        for child in parent.childItems():
            found = self.visual_item(name, child)
            if found is not None:
                return found
        return None

    def item(self, name):
        # Delegates can also appear a frame after the view-model changes.
        for _ in range(100):
            found = self.root.findChild(QObject, name) or self.visual_item(name)
            if found is not None:
                return found
            QTest.qWait(20)
        self.fail(f"{name} not found")

    def scroll_into_view(self, item):
        flick = self.item("invoiceScroll").property("contentItem")
        viewport_top = flick.mapToScene(QPointF(0, 0)).y()
        top = item.mapToScene(QPointF(0, 0)).y()
        height = flick.property("height")
        if top < viewport_top or top + item.height() > viewport_top + height:
            content_y = flick.property("contentY") + (top - viewport_top) - 20
            flick.setProperty("contentY", max(0, content_y))
            QTest.qWait(30)

    def click(self, item):
        if isinstance(item, str):
            item = self.item(item)
        self.scroll_into_view(item) if self._inside_scroll(item) else None
        self.assertTrue(item.property("visible"), item.objectName())
        self.assertTrue(item.property("enabled"), item.objectName())
        point = item.mapToScene(QPointF(item.width() / 2, item.height() / 2)).toPoint()
        QTest.mouseClick(self.root, Qt.LeftButton, Qt.NoModifier, point)
        QTest.qWait(30)

    def _inside_scroll(self, item):
        scroll, parent = self.root.findChild(QObject, "invoiceScroll"), item.parentItem()
        while parent is not None:
            if parent == scroll.property("contentItem"):
                return True
            parent = parent.parentItem()
        return False

    def type_into(self, name, text):
        self.click(name)
        for char in text:
            QTest.keyClick(self.root, char)
        QTest.qWait(20)

    def choose(self, combo_name, target):
        """Open the combo by click, move the highlight to row `target` with the keyboard, confirm."""
        combo = self.item(combo_name)
        self.click(combo)
        self.wait_until(lambda: combo.property("down"), f"{combo_name} popup")
        for _ in range(10):
            highlighted = combo.property("highlightedIndex")
            if highlighted == target:
                break
            QTest.keyClick(self.root, Qt.Key_Down if highlighted < target else Qt.Key_Up)
        self.assertEqual(combo.property("highlightedIndex"), target, combo_name)
        QTest.keyClick(self.root, Qt.Key_Return)
        self.wait_until(lambda: not combo.property("down"), f"{combo_name} closed")

    def select_salesperson(self):
        self.choose("invoiceSalesperson", 0)
        self.wait_until(lambda: self.vm.salespersonId == "5")

    def select_customer(self, customer_id="12"):
        picker = self.item("invoiceCustomerPicker")
        self.click(picker.findChild(QObject, "customerPickerOpenButton"))
        popup = picker.findChild(QObject, "customerPickerPopup")
        results = picker.findChild(QObject, "customerPickerResults")
        self.wait_until(lambda: results.property("count") > 0 and popup.property("opened"))
        # Result rows in search order; the fake lists active, non-placeholder customers.
        order = [c["customer_id"] for c in self.store.customers.values()
                 if c["is_active"] and c["record_origin"] != "LEGACY_PLACEHOLDER"]
        rows = sorted((c for c in results.property("contentItem").childItems() if c.property("visible")),
                      key=lambda c: c.property("y"))
        self.click(rows[order.index(customer_id)])
        self.wait_until(lambda: self.vm.customerId == customer_id, "customer not selected")
        self.wait_until(lambda: not popup.property("visible"), "picker popup still open")

    def add_barcode(self, barcode):
        count = self.vm.saleCount
        self.type_into("invoiceBarcodeField", barcode)
        self.click("invoiceAddBarcode")
        self.wait_until(lambda: self.vm.saleCount == count + 1, "barcode not added")

    def add_old_gold(self, karat_row, weight):
        count = self.vm.oldGoldCount
        self.choose("oldGoldKarat", karat_row)
        self.type_into("oldGoldWeight", weight)
        self.click("addOldGold")
        self.wait_until(lambda: self.vm.oldGoldCount == count + 1, "old gold not added")

    def settle(self):
        QTest.qWait(60)
        self.wait_until(lambda: not self.vm.busy and (self.vm.previewReady or self.vm.previewError), "no preview")

    def finalize(self, *confirm_first):
        self.wait_until(lambda: self.vm.canFinalize, "finalize not enabled")
        self.click("finalizeInvoice")
        for dialog, button in confirm_first:
            self.wait_until(lambda: self.item(dialog).property("opened"), f"{dialog} not opened")
            self.click(button)
        self.wait_until(lambda: self.item("confirmFinalizeDialog").property("opened"), "confirm not opened")
        self.snapshot("confirm")
        self.click("confirmFinalize")
        self.wait_until(lambda: self.item("invoiceSuccessDialog").property("opened"), "no success")
        QTest.qWait(250)                                               # let the dialog finish opening
        self.snapshot("success")
        return self.item("successInvoiceNumber").property("text")

    def snapshot(self, label):
        target = os.getenv("MASGOLD_UI_REVIEW_DIR")
        if target:
            Path(target).mkdir(parents=True, exist_ok=True)
            self.root.grabWindow().save(str(Path(target) / f"billing-{self._testMethodName}-{label}.png"))

    def text(self, name):
        return self.item(name).property("text")

    # ------------------------------------------------------------ flows

    def test_sale_only(self):
        self.select_salesperson()
        self.add_barcode("01260925")
        self.type_into("saleMaking_0", "150")
        self.type_into("cashAmountField", "67011")
        self.settle()
        self.assertEqual(self.text("summarySale"), "67,011.00 ج")
        self.assertEqual(self.text("summaryResidual"), "مسددة بالكامل")
        number = self.finalize()
        self.assertIn("26092501", number)
        header = self.store.invoices[0]
        self.assertEqual((header["invoice_type"], header["employee_id"], header["created_by_user_id"]), ("SELL", 5, 17))
        self.assertEqual(self.store.items["100"]["inventory_status"], "SOLD")
        self.click("successNewInvoice")
        self.wait_until(lambda: self.vm.saleCount == 0 and not self.vm.hasSuccess)
        self.assertEqual(self.v1_calls, [])
        self.assertEqual(self.warnings, [])

    def test_old_gold_only(self):
        self.select_salesperson()
        self.select_customer("12")
        self.add_old_gold(1, "10")                                     # rows 18, 21, 24 -> 21K
        self.choose("oldGoldDiscountType_0", 1)                         # خصم %
        self.type_into("oldGoldDiscountValue_0", "1.5")
        self.type_into("walletAmountField", "50000")
        self.settle()
        self.assertEqual(self.text("summaryOldGold"), "61,138.95 ج")
        self.assertIn("متبقي للعميل", self.text("summaryResidual"))
        self.finalize()
        self.assertEqual(self.store.invoices[0]["invoice_type"], "BUY")
        self.assertEqual([(p["direction"], p["amount"]) for p in self.store.payments], [("OUT", D("50000.00"))])
        self.assertEqual(self.store.ledger[0]["amount_delta"], D("-11138.95"))
        self.assertEqual(self.warnings, [])

    def test_mixed_exchange(self):
        self.select_salesperson()
        self.add_barcode("01260925")
        self.type_into("saleMaking_0", "150")
        self.add_old_gold(1, "5")
        self.choose("oldGoldDiscountType_0", 2)                         # خصم مبلغ
        self.type_into("oldGoldDiscountValue_0", "35")
        self.type_into("cashAmountField", "36011")
        self.settle()
        self.assertEqual(self.text("summaryNet"), "على العميل 36,011.00 ج")
        self.finalize()
        self.assertEqual((self.store.invoices[0]["invoice_type"], self.store.invoices[0]["settlement_status"]),
                         ("MIXED", "SETTLED"))
        self.assertEqual(self.store.lines[1][1]["discount_amount"], D("35.00"))

    def test_partial_payment_with_residual(self):
        self.select_salesperson()
        self.select_customer("12")
        self.add_barcode("01260925")
        self.type_into("saleMaking_0", "150")
        self.type_into("cashAmountField", "60000")
        self.settle()
        self.assertEqual(self.text("summaryResidual"), "متبقي على العميل 7,011.00 ج")
        self.assertEqual(self.text("customerBalance"), "لا يوجد رصيد")
        self.assertEqual(self.text("customerBalanceAfter"), "على العميل 7,011.00 ج")
        self.finalize()
        self.assertEqual((self.store.ledger[0]["type_code"], self.store.ledger[0]["amount_delta"]),
                         ("SALE_BALANCE_DUE", D("7011.00")))

    def test_customer_required(self):
        self.select_salesperson()
        self.add_barcode("01260925")
        self.type_into("saleMaking_0", "150")
        self.type_into("cashAmountField", "100")
        self.settle()
        self.assertIn("يجب اختيار عميل", self.text("invoiceProblems"))
        self.assertFalse(self.item("finalizeInvoice").property("enabled"))
        self.select_customer("12")
        self.settle()
        self.wait_until(lambda: self.vm.canFinalize)
        self.assertFalse(self.item("invoiceProblems").property("visible"))

    def test_dead_cash_confirmation(self):
        self.store.warnings[12] = {"flag_id": "8"}
        self.select_salesperson()
        self.select_customer("12")
        self.add_barcode("01260925")
        self.type_into("saleMaking_0", "150")
        self.type_into("cashAmountField", "60000")
        self.settle()
        self.assertTrue(self.item("deadCashBanner").property("visible"))
        self.finalize(("deadCashDialog", "deadCashConfirm"))
        self.assertTrue(self.store.events[1]["details"]["dead_cash_confirmed"])

    def test_stale_price_confirmation(self):
        self.prices.status = "STALE"
        QTest.qWait(20)
        self.vm.refreshPrices()
        self.wait_until(lambda: self.vm.priceStale)
        self.assertEqual(self.text("invoicePriceStatus"), "السعر قديم")
        self.select_salesperson()
        self.add_barcode("01260925")
        self.type_into("saleMaking_0", "150")
        self.type_into("cashAmountField", "67011")
        self.settle()
        self.finalize(("stalePriceDialog", "stalePriceConfirm"))
        self.assertTrue(self.store.events[1]["details"]["stale_acknowledged"])

    def test_layout_colors_and_screenshots_at_all_sizes(self):
        self.select_salesperson()
        self.select_customer("12")
        self.store.warnings[12] = {"flag_id": "8"}
        self.add_barcode("01260925")
        self.type_into("saleMaking_0", "150")
        self.add_old_gold(1, "5")
        self.type_into("cashAmountField", "20000")
        self.settle()
        buy, sell = self.item("priceBuy_21"), self.item("priceSell_21")
        self.assertEqual(buy.property("color").name(), "#60c9af")            # shop BUY teal
        self.assertEqual(sell.property("color").name(), "#e2bc63")           # shop SELL gold
        target = os.getenv("MASGOLD_UI_REVIEW_DIR")
        if target:
            Path(target).mkdir(parents=True, exist_ok=True)
        for width, height in SIZES:
            self.root.setWidth(width)
            self.root.setHeight(height)
            QTest.qWait(120)
            for name in ("invoicePriceBar", "invoiceSummaryBar", "finalizeInvoice", "summaryResidual"):
                item = self.item(name)
                point = item.mapToScene(QPointF(0, 0))
                self.assertTrue(item.property("visible"), name)
                self.assertGreaterEqual(point.x(), -1, f"{name} at {width}")
                self.assertLessEqual(point.x() + item.width(), width + 1, f"{name} at {width}")
                self.assertLessEqual(point.y() + item.height(), height + 1, f"{name} at {width}x{height}")
            for name in ("invoiceBarcodeField", "invoiceAddBarcode", "saleMaking_0", "oldGoldWeight",
                         "addOldGold", "oldGoldDiscountType_0", "cashAmountField", "walletAmountField",
                         "invoiceSalesperson", "invoiceCustomerPicker"):
                item = self.item(name)
                self.scroll_into_view(item)
                point = item.mapToScene(QPointF(0, 0))
                self.assertGreaterEqual(point.x(), -1, f"{name} at {width}")
                self.assertLessEqual(point.x() + item.width(), width + 1, f"{name} clipped at {width}")
            self.item("invoiceScroll").property("contentItem").setProperty("contentY", 0)
            QTest.qWait(60)
            if target:
                self.assertTrue(self.root.grabWindow().save(str(Path(target) / f"billing-{width}x{height}.png")))
        self.assertEqual(self.warnings, [])


if __name__ == "__main__":
    unittest.main()
