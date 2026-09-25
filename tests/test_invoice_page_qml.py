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
        # Repeater / ListView delegates live only in the visual tree, not the QObject tree.
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

    def click(self, item):
        if isinstance(item, str):
            item = self.item(item)
        self.assertTrue(item.property("visible"), item.objectName())
        self.assertTrue(item.property("enabled"), item.objectName())
        point = item.mapToScene(QPointF(item.width() / 2, item.height() / 2)).toPoint()
        self.assertTrue(0 <= point.x() <= self.root.width() and 0 <= point.y() <= self.root.height(),
                        f"{item.objectName()} is outside the window")
        QTest.mouseClick(self.root, Qt.LeftButton, Qt.NoModifier, point)
        QTest.qWait(30)

    def assert_inside_window(self, item, label=""):
        # One screen, no page scrolling: every control must be fully inside the window.
        point = item.mapToScene(QPointF(0, 0))
        width, height = self.root.width(), self.root.height()
        name = f"{item.objectName()} {label} at {width}x{height}"
        self.assertGreaterEqual(point.x(), -1, name)
        self.assertGreaterEqual(point.y(), -1, name)
        self.assertLessEqual(point.x() + item.width(), width + 1, name)
        self.assertLessEqual(point.y() + item.height(), height + 1, name)

    def type_into(self, name, text):
        self.click(name)
        QTest.keyClick(self.root, Qt.Key_A, Qt.ControlModifier)
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

    def scan(self, barcode):
        """A scanner types the code and sends Enter into the focused barcode field."""
        count = self.vm.saleCount
        self.type_into("invoiceBarcodeField", barcode)
        QTest.keyClick(self.root, Qt.Key_Return)
        self.wait_until(lambda: self.vm.saleCount == count + 1, "barcode not added")
        self.wait_until(lambda: self.item("invoiceBarcodeField").property("activeFocus"), "barcode not refocused")

    def add_old_gold(self, karat, weight):
        count = self.vm.oldGoldCount
        self.click(f"karatChip_{karat}")
        self.type_into("oldGoldWeight", weight)
        QTest.keyClick(self.root, Qt.Key_Return)
        self.wait_until(lambda: self.vm.oldGoldCount == count + 1, "old gold not added")

    def settle(self):
        QTest.qWait(60)
        self.wait_until(lambda: not self.vm.busy and (self.vm.previewReady or self.vm.previewError), "no preview")

    def pay_full(self):
        self.settle()
        self.click("payFullButton")
        self.settle()

    def finalize(self, *acknowledge):
        self.wait_until(lambda: self.vm.canFinalize, "finalize not enabled")
        self.click("finalizeInvoice")
        dialog = self.item("confirmFinalizeDialog")
        self.wait_until(lambda: dialog.property("opened"), "confirmation not opened")
        QTest.qWait(250)
        for name in acknowledge:
            self.assertFalse(self.item("confirmFinalize").property("enabled"), "saved before acknowledgement")
            self.click(name)
        self.snapshot("confirm")
        self.click("confirmFinalize")
        self.wait_until(lambda: self.vm.hasSuccess, f"no success: {self.vm.errorMessage!r} {self.vm.previewError!r}")
        self.wait_until(lambda: not dialog.property("visible"), "confirmation still open")
        self.snapshot("success")
        return self.text("successInvoiceNumber")

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
        self.scan("01260925")
        self.type_into("invoiceMakingField", "150")                    # one making for the invoice
        self.settle()
        self.assertEqual(self.text("summarySale"), "67,011.00 ج")
        self.assertEqual(self.text("summaryDue"), "67,011.00 ج")
        self.assertEqual(self.item("summaryDueBox").property("title"), "المطلوب من العميل")
        self.pay_full()
        self.assertEqual(self.vm.cashAmount, "67011.00")
        self.assertEqual(self.text("summaryResidualLabel"), "لا يوجد متبقٍ")
        number = self.finalize()
        self.assertIn("26092501", number)
        header = self.store.invoices[0]
        self.assertEqual((header["invoice_type"], header["employee_id"], header["created_by_user_id"]), ("SELL", 5, 17))
        self.assertEqual(self.store.items["100"]["inventory_status"], "SOLD")
        # Ready for the next customer: empty draft, same salesperson, focus on the barcode.
        self.assertEqual((self.vm.saleCount, self.vm.salespersonId), (0, "5"))
        self.assertTrue(self.item("invoiceBarcodeField").property("activeFocus"))
        self.assertTrue(self.item("invoiceSuccessBanner").property("visible"))
        self.assertEqual(self.v1_calls, [])
        self.assertEqual(self.warnings, [])

    def test_old_gold_only(self):
        self.select_salesperson()
        self.select_customer("12")
        self.add_old_gold("21", "10")
        self.type_into("oldGoldDiscountField", "1.5")                   # one discount for the invoice
        self.type_into("walletAmountField", "50000")
        self.settle()
        self.assertEqual(self.text("summaryOldGold"), "61,138.95 ج")
        self.assertEqual(self.item("summaryDueBox").property("title"), "المستحق للعميل")
        self.assertEqual(self.text("summaryResidualLabel"), "متبقي للعميل (يُسجل لحسابه)")
        self.assertEqual(self.text("summaryResidual"), "11,138.95 ج")
        self.assertEqual(self.text("boughtWeights_21"), "21: 10.000")
        self.finalize()
        self.assertEqual(self.store.invoices[0]["invoice_type"], "BUY")
        self.assertEqual(self.store.lines[0][1]["discount_type"], "PERCENT")
        self.assertEqual([(p["direction"], p["amount"]) for p in self.store.payments], [("OUT", D("50000.00"))])
        self.assertEqual(self.store.ledger[0]["amount_delta"], D("-11138.95"))
        self.assertEqual(self.warnings, [])

    def test_mixed_exchange_with_line_override(self):
        self.select_salesperson()
        self.scan("01260925")
        self.type_into("invoiceMakingField", "150")
        self.type_into("saleMaking_0", "200")                          # this piece only
        self.add_old_gold("21", "5")
        self.settle()
        self.assertEqual(self.text("summarySale"), "67,536.00 ج")
        self.assertEqual(self.text("summaryDue"), "36,501.00 ج")
        self.assertEqual(self.text("soldWeights_21"), "21: 10.500")
        self.assertEqual(self.text("boughtWeights_21"), "21: 5.000")
        self.pay_full()
        self.finalize()
        header = self.store.invoices[0]
        self.assertEqual((header["invoice_type"], header["settlement_status"]), ("MIXED", "SETTLED"))
        self.assertEqual(self.store.lines[0][1]["making_per_gram"], D("200.00"))

    def test_partial_payment_goes_to_customer_account(self):
        self.select_salesperson()
        self.select_customer("12")
        self.scan("01260925")
        self.type_into("invoiceMakingField", "150")
        self.type_into("cashAmountField", "60000")
        self.settle()
        self.assertEqual(self.text("summaryResidualLabel"), "متبقي على العميل (يُسجل على حسابه)")
        self.assertEqual(self.text("summaryResidual"), "7,011.00 ج")
        self.assertEqual(self.text("customerBalance"), "لا يوجد رصيد")
        self.finalize()
        self.assertEqual((self.store.ledger[0]["type_code"], self.store.ledger[0]["amount_delta"]),
                         ("SALE_BALANCE_DUE", D("7011.00")))

    def test_customer_required_for_a_remainder(self):
        self.select_salesperson()
        self.scan("01260925")
        self.type_into("invoiceMakingField", "150")
        self.type_into("cashAmountField", "100")
        self.settle()
        self.assertIn("يجب اختيار عميل", self.text("invoiceProblems"))
        self.assertFalse(self.item("finalizeInvoice").property("enabled"))
        self.select_customer("12")
        self.settle()
        self.wait_until(lambda: self.vm.canFinalize)
        self.assertFalse(self.item("invoiceProblems").property("visible"))

    def test_dead_cash_is_a_tick_in_the_one_confirmation(self):
        self.store.warnings[12] = {"flag_id": "8"}
        self.select_salesperson()
        self.select_customer("12")
        self.scan("01260925")
        self.type_into("invoiceMakingField", "150")
        self.type_into("cashAmountField", "60000")
        self.settle()
        self.assertTrue(self.item("deadCashBanner").property("visible"))
        self.finalize("deadCashAcknowledge")
        self.assertTrue(self.store.events[1]["details"]["dead_cash_confirmed"])

    def test_stale_price_is_a_tick_in_the_one_confirmation(self):
        self.prices.status = "STALE"
        QTest.qWait(20)
        self.vm.refreshPrices()
        self.wait_until(lambda: self.vm.priceStale)
        self.assertEqual(self.text("invoicePriceStatus"), "السعر قديم")
        self.select_salesperson()
        self.scan("01260925")
        self.type_into("invoiceMakingField", "150")
        self.pay_full()
        self.finalize("staleAcknowledge")
        self.assertTrue(self.store.events[1]["details"]["stale_acknowledged"])

    def test_back_from_confirmation_saves_nothing(self):
        self.select_salesperson()
        self.scan("01260925")
        self.type_into("invoiceMakingField", "150")
        self.pay_full()
        self.click("finalizeInvoice")
        dialog = self.item("confirmFinalizeDialog")
        self.wait_until(lambda: dialog.property("opened"))
        QTest.qWait(250)
        self.click("confirmBack")
        self.wait_until(lambda: not dialog.property("visible"))
        self.assertEqual((self.store.invoices, self.vm.saleCount), ([], 1))

    def test_keyboard_only_invoice_at_1080(self):
        """Scan, making, karat, weight, pay in full and save without touching the mouse."""
        self.root.setWidth(1080)
        self.root.setHeight(680)
        self.select_salesperson()
        self.item("invoiceBarcodeField").forceActiveFocus()
        for key in "01260925":
            QTest.keyClick(self.root, key)
        QTest.keyClick(self.root, Qt.Key_Return)
        self.wait_until(lambda: self.vm.saleCount == 1)
        QTest.keyClick(self.root, Qt.Key_M, Qt.ControlModifier)       # making (F7 is reserved)
        self.wait_until(lambda: self.item("invoiceMakingField").property("activeFocus"))
        for key in "150":
            QTest.keyClick(self.root, key)
        QTest.keyClick(self.root, Qt.Key_2, Qt.AltModifier)            # karat 21, focus weight
        self.wait_until(lambda: self.item("oldGoldWeight").property("activeFocus"))
        QTest.keyClick(self.root, Qt.Key_5)
        QTest.keyClick(self.root, Qt.Key_Return)
        self.wait_until(lambda: self.vm.oldGoldCount == 1)
        self.settle()
        QTest.keyClick(self.root, Qt.Key_F8)                          # pay the full amount
        self.settle()
        self.assertEqual(self.vm.cashAmount, "35976.00")
        QTest.keyClick(self.root, Qt.Key_F9)                          # save -> confirmation
        dialog = self.item("confirmFinalizeDialog")
        self.wait_until(lambda: dialog.property("opened"))
        QTest.qWait(250)
        QTest.keyClick(self.root, Qt.Key_Return)                       # confirm
        self.wait_until(lambda: self.vm.hasSuccess, "keyboard save failed")
        self.assertEqual(self.store.invoices[0]["settlement_status"], "SETTLED")
        self.wait_until(lambda: not dialog.property("visible"), "confirmation still closing")
        self.assertTrue(self.item("invoiceBarcodeField").property("activeFocus"))
        QTest.keyClick(self.root, Qt.Key_F2)                          # new invoice
        self.wait_until(lambda: not self.vm.hasSuccess and self.vm.saleCount == 0,
                        f"F2: success={self.vm.hasSuccess} lines={self.vm.saleCount} "
                        f"opened={dialog.property('visible')}")
        self.assertEqual(self.warnings, [])

    def test_reserved_function_keys_do_nothing_yet(self):
        self.select_salesperson()
        self.scan("01260925")
        for key in (Qt.Key_F3, Qt.Key_F5, Qt.Key_F7):
            QTest.keyClick(self.root, key)
            QTest.qWait(30)
            self.assertTrue(self.item("invoiceBarcodeField").property("activeFocus"), f"{key} moved focus")
        self.assertFalse(self.item("invoiceMakingField").property("activeFocus"))

    def click_row(self, name, button=Qt.LeftButton):
        row = self.item(name)
        # Click the row's description cell (the right side), not a field or button.
        point = row.mapToScene(QPointF(row.width() - 30, row.height() / 2)).toPoint()
        QTest.mouseClick(self.root, button, Qt.NoModifier, point)
        QTest.qWait(60)

    def test_fast_row_deletion_with_delete_key_and_right_click_at_1080(self):
        self.root.setWidth(1080)
        self.root.setHeight(680)
        self.select_salesperson()
        self.scan("01260925")
        self.scan("02260925")
        self.add_old_gold("21", "5")
        self.add_old_gold("18", "2")
        # Sale table: select the first piece, Delete removes it.
        self.click_row("saleRow_0")
        self.assertTrue(self.item("saleRow_0").property("selected"))
        QTest.keyClick(self.root, Qt.Key_Delete)
        self.wait_until(lambda: self.vm.saleCount == 1)
        self.assertEqual(self.vm.saleLinesModel.rows()[0]["barcode"], "02260925")
        # Old-gold table: right-click → حذف.
        self.click_row("oldGoldRow_1", Qt.RightButton)
        menu = self.item("rowMenu")
        self.wait_until(lambda: menu.property("opened"), "row menu not opened")
        self.click("rowMenuDelete")
        self.wait_until(lambda: self.vm.oldGoldCount == 1)
        self.assertEqual(self.vm.oldGoldLinesModel.rows()[0]["karat"], "21")
        # Delete inside a text field edits the text, never a row.
        self.type_into("invoiceMakingField", "150")
        QTest.keyClick(self.root, Qt.Key_Home)
        QTest.keyClick(self.root, Qt.Key_Delete)
        QTest.qWait(40)
        self.assertEqual((self.vm.saleCount, self.vm.oldGoldCount, self.vm.invoiceMaking), (1, 1, "50"))
        # The visible حذف button still works.
        self.click("removeOldGold_0")
        self.wait_until(lambda: self.vm.oldGoldCount == 0)
        self.assertEqual(self.warnings, [])

    def test_old_gold_add_needs_an_explicit_karat_at_1080(self):
        self.root.setWidth(1080)
        self.root.setHeight(680)
        self.select_salesperson()
        add = self.item("addOldGold")
        self.assertEqual(self.vm.oldGoldKaratId, -1)
        self.type_into("oldGoldWeight", "5")
        self.assertFalse(add.property("enabled"))
        QTest.keyClick(self.root, Qt.Key_Return)                       # Enter without a karat adds nothing
        QTest.mouseClick(self.root, Qt.LeftButton, Qt.NoModifier,
                         add.mapToScene(QPointF(add.width() / 2, add.height() / 2)).toPoint())
        QTest.qWait(60)
        self.assertEqual(self.vm.oldGoldCount, 0)
        self.click("karatChip_21")
        self.assertTrue(self.item("karatChip_21").property("selected"))
        self.assertTrue(add.property("enabled"))
        self.click("addOldGold")
        self.wait_until(lambda: self.vm.oldGoldCount == 1)
        self.assertEqual(self.vm.oldGoldLinesModel.rows()[0]["karat"], "21")
        self.wait_until(lambda: self.vm.oldGoldKaratId == -1, "karat not cleared")
        self.vm.refreshPrices()                                        # a model reload must not preselect
        self.wait_until(lambda: not self.vm.busy)
        QTest.qWait(60)
        self.assertFalse(any(self.item(f"karatChip_{k}").property("selected") for k in ("18", "21", "24")))
        self.assertEqual(self.warnings, [])

    def test_price_bar_follows_global_price_updates_at_1080(self):
        from tests import test_invoice_service as fixtures
        self.root.setWidth(1080)
        self.root.setHeight(680)
        sell = self.item("priceSell_21")
        self.assertEqual(sell.property("text"), "بيع 6,232.00")
        with patch.dict(fixtures.PRICES, {1: (D("6250.0000"), D("6275.0000"))}):
            self.prices.token = "T2"
            gold = self.controller.gold_prices
            gold._data = {**gold._data, "price_token": "T2", "status": "FRESH"}
            gold.changed.emit()                                        # the top bar publishes a new set
            self.wait_until(lambda: self.item("priceSell_21").property("text") == "بيع 6,275.00",
                            "Billing price bar did not follow the new set")
        self.assertEqual(self.item("priceBuy_21").property("text"), "شراء 6,250.00")
        self.assertEqual(self.vm._board["price_token"], "T2")
        self.assertEqual(self.warnings, [])

    def test_one_screen_layout_colors_and_screenshots_at_all_sizes(self):
        self.select_salesperson()
        self.select_customer("12")
        self.store.warnings[12] = {"flag_id": "8"}
        self.scan("01260925")
        self.scan("02260925")
        self.type_into("invoiceMakingField", "150")
        self.add_old_gold("21", "5")
        self.add_old_gold("18", "2.5")
        self.type_into("oldGoldDiscountField", "1")
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
            QTest.qWait(150)
            for name in ("invoicePriceBar", "invoiceSalesperson", "invoiceCustomerPicker", "deadCashBanner",
                         "invoiceBarcodeField", "invoiceAddBarcode", "saleMaking_0", "saleMaking_1",
                         "removeSale_1", "karatChip_18", "karatChip_21", "karatChip_24", "oldGoldWeight",
                         "addOldGold", "removeOldGold_1", "soldWeights", "boughtWeights",
                         "invoiceSummaryBar", "invoiceMakingField", "oldGoldDiscountField", "summarySale",
                         "summaryOldGold", "summaryDue", "cashAmountField", "payFullButton",
                         "walletAmountField", "summaryResidual", "finalizeInvoice"):
                item = self.item(name)
                self.assertTrue(item.property("visible"), f"{name} hidden at {width}x{height}")
                self.assert_inside_window(item)
            sale_panel, old_panel = self.item("salePanel"), self.item("oldGoldPanel")
            # Side by side: the sale panel on the physical right.
            self.assertGreater(sale_panel.mapToScene(QPointF(0, 0)).x(), old_panel.mapToScene(QPointF(0, 0)).x())
            self.assertGreaterEqual(self.item("saleList").property("height"), 80, f"sale table at {width}x{height}")
            # The old-gold hint ("اختر العيار أولاً") has room for its full text.
            self.assertGreaterEqual(self.item("oldGoldWeight").width(), 160, f"old-gold weight at {width}")
            if target:
                self.assertTrue(self.root.grabWindow().save(str(Path(target) / f"billing-{width}x{height}.png")))
        self.assertEqual(self.warnings, [])


if __name__ == "__main__":
    unittest.main()
