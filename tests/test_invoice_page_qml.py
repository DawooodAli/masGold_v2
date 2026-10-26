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
from PySide6.QtGui import QFont, QFontDatabase, QGuiApplication, QKeyEvent
from PySide6.QtQuick import QQuickItem
from PySide6.QtQuickControls2 import QQuickStyle
from PySide6.QtTest import QTest

from app.viewmodels.dashboard_viewmodel import DashboardViewModel
from app.viewmodels.invoice_history_viewmodel import InvoiceHistoryViewModel
from app.viewmodels.invoice_viewmodel import InvoiceViewModel
from app.viewmodels.management_review_viewmodel import ManagementReviewViewModel
from app.viewmodels.operational_balance_viewmodel import OperationalBalanceViewModel
from main import ApplicationController, create_operational_viewmodels
from services.authentication_service import AuthenticationError, AuthenticationResult, GENERIC_ERROR, _VERIFIED
from services.invoice_service import InvoiceService
from tests import click_diagnostics
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


class Approvers:
    """The manager account, as the approval check reads it inside the transaction."""

    def __init__(self, connection):
        pass

    def get_identity(self, user_id, lock=False):
        return {"id": user_id, "is_active": True, "must_change_password": False, "employee_id": None}

    def effective_permissions(self, user_id, lock=False):
        return frozenset({"APP_LOGIN", "INVOICE_SETTLEMENT_APPROVE"})


class UiProviderSettings:
    """Provider settings beside the printer controls, with no DB, HTTP or keyring.

    These page tests exercise printing and navigation. Provider configuration
    operations have their own service/worker tests and are not faked as successful
    writes here: this fixture deliberately implements only the load they need.
    """

    def __init__(self):
        self.load_calls = 0

    def load(self, actor):
        self.load_calls += 1
        return {"base_url": "https://goldtimo.com/api/v1", "timeout": "10",
                "refresh": "60", "stale": "300", "stored": False, "configured": False}


class UiPrinter:
    """InvoicePrintService stand-in: records each print job (never a real printer)."""

    def __init__(self):
        self.calls, self.outcome = [], (True, "تم إرسال الفاتورة للطابعة.")
        self.ready = (True, "")                              # the Bill printer check before حفظ وطباعة
        self.gate = None                                     # a threading.Event keeps the job in flight

    def bill_printer_ready(self):
        return self.ready

    def print_invoice(self, invoice_id, *, _expected_actor=None):
        from services.invoice_print_service import PrintOutcome
        self.calls.append(invoice_id)
        if self.gate is not None:
            self.gate.wait(10)
        printed, message = self.outcome
        return PrintOutcome(invoice_number="", printed=printed, message=message)


class Credentials:
    def verify_credentials(self, username, password):
        if (username, password) != ("manager", "manager-pass"):
            raise AuthenticationError(GENERIC_ERROR)
        return AuthenticationResult(3, None, "المدير", frozenset({"APP_LOGIN", "INVOICE_SETTLEMENT_APPROVE"}))


class Reviews:
    """ManagementReviewService stand-in over the same in-memory store."""

    def __init__(self, store):
        self.store = store

    def counts(self, **kwargs):
        return {"INVENTORY_MATCH": len(self.store.cases),
                "MAKING_PRICE": sum(r["review_type"] == "MAKING_PRICE" for r in self.store.reviews),
                "CASH_SETTLEMENT": sum(r["review_type"] == "CASH_SETTLEMENT" for r in self.store.reviews),
                "OLD_GOLD_DISCOUNT": sum(r["review_type"] == "OLD_GOLD_DISCOUNT" for r in self.store.reviews)}

    def open_reviews(self, **kwargs):
        return {"INVENTORY_MATCH": [], "MAKING_PRICE": [], "CASH_SETTLEMENT": [], "OLD_GOLD_DISCOUNT": []}


class Balances:
    """OperationalBalanceService stand-in: the same in-memory ledgers the invoice writes."""

    def __init__(self, store):
        self.store = store

    def strip(self, **kwargs):
        cash = sum((m["amount_delta"] for m in self.store.cash_movements), D("0.00"))
        repo = InvoiceRepo(type("C", (), {"store": self.store})())
        grams = {row["karat_value"]: row["balance_grams"] for row in repo.old_gold_balances()}
        return {"cash_balance": str(cash),
                "old_gold": [{"karat": k, "grams": str(grams.get(k, D("0.000")))} for k in (21, 18, 24)]}

    def financial_accounts(self, **kwargs):
        cash = sum((m["amount_delta"] for m in self.store.cash_movements), D("0.00"))
        return [{"ledger": "CASH", "account_id": "1", "name": "Main Safe", "account_type": "CASH", "is_active": 1,
                 "balance": cash, "money_in": max(cash, D("0")), "money_out": max(-cash, D("0")),
                 "movement_count": len(self.store.cash_movements), "recent": []}]


class UiDashboard:
    """DashboardService stand-in (the live database stays blocked): today is empty unless a test
    fills `snapshot`."""

    def __init__(self):
        self.calls = 0
        self.snapshot = {"can_see_sales": True, "invoice_count": 0, "sale_invoice_count": 0,
                         "buy_invoice_count": 0, "unidentified_lines": 0, "sell_total": "0.00",
                         "buy_total": "0.00", "sold": [],
                         "old_gold": [{"karat": k, "balance": "0.000", "in": "0.000", "out": "0.000"}
                                      for k in (21, 18, 24)]}

    def today(self, *, _expected_actor=None):
        self.calls += 1
        return copy.deepcopy(self.snapshot)


class InvoicePageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Material, as in the real app, even when another test module created the application first.
        QQuickStyle.setStyle("Material")
        cls.app = QGuiApplication.instance() or QGuiApplication([])
        cls.app.setQuitOnLastWindowClosed(False)
        cls.app.setLayoutDirection(Qt.RightToLeft)
        fonts = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"
        # The real Cairo when installed (per user or system): widths then match the real app.
        for cairo in (Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft/Windows/Fonts/Cairo.ttf", fonts / "Cairo.ttf"):
            if "Cairo" not in QFontDatabase.families() and cairo.is_file():
                QFontDatabase.addApplicationFont(str(cairo))
        if "Cairo" not in QFontDatabase.families() and (fonts / "arial.ttf").is_file():
            QFontDatabase.addApplicationFont(str(fonts / "arial.ttf"))
            QFont.insertSubstitution("Cairo", "Arial")

    def setUp(self):
        block = patch("database.connection.DatabaseConnection.connect",
                      side_effect=AssertionError("Live database forbidden"))
        block.start()
        self.addCleanup(block.stop)
        self.store, self.prices, self.commits = Store(), Prices(), 0
        self.stock()
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

        self.printer = UiPrinter()
        self.dashboard = UiDashboard()
        from tests.test_invoice_history import StaticHistory
        self.history = StaticHistory()          # سجل الفواتير over fixtures (never the database)

        def invoice_factory(session):
            service = InvoiceService(session, gold_prices=self.prices,
                                     authorization=Authorization(session, PERMISSIONS),
                                     transaction_factory=transaction, invoice_repository=UiInvoiceRepo,
                                     customer_repository=CustomerRepo, dead_cash_repository=DeadCashRepo,
                                     approver_repository=Approvers, authentication=Credentials())
            return InvoiceViewModel(session, service=service, master_data=master, print_service=self.printer,
                                    preview_delay_ms=20)

        self.controller = ApplicationController(
            self.app, authentication_service=authentication_fixture.FakeAuthentication(),
            operational_factory=factory, gold_price_service=authentication_fixture.EmptyGoldPrices(),
            invoice_factory=invoice_factory,
            printer_settings_factory=self.printer_settings,
            review_factory=lambda session: ManagementReviewViewModel(session, service=Reviews(self.store),
                                                                     balance_service=Balances(self.store)),
            balance_factory=lambda session: OperationalBalanceViewModel(session, service=Balances(self.store)),
            dashboard_factory=lambda session: DashboardViewModel(session, service=self.dashboard),
            history_factory=lambda session: InvoiceHistoryViewModel(session, service=self.history, debounce_ms=20))
        # Inject before opening operational QML can start a provider load worker.
        self.provider_settings = UiProviderSettings()
        self.controller.gold_settings.service = self.provider_settings
        self.wait_until(lambda: self.controller.state == "LOGIN")
        session = self.controller.session
        session._establish(AuthenticationResult(17, 7, "موظف", PERMISSIONS, _VERIFIED), int(session.generation))
        self.controller._open_operational()
        self.root = self.controller.operational_engine.rootObjects()[0]
        self.warnings = []
        self.controller.operational_engine.warnings.connect(
            lambda errors: self.warnings.extend(e.toString() for e in errors))
        self.vm = self.controller.operational_viewmodels["invoiceVM"]
        gate = self.controller.operational_viewmodels["managerGateVM"]          # the manager lock is open for every page test; its own tests lock it
        gate._unlocked = True
        gate.changed.emit()
        self.root.setWidth(1280)
        self.root.setHeight(800)
        self.root.setProperty("currentPage", 1)
        self.wait_until(lambda: len(self.vm.priceRows) == 3 and len(self.vm.salespeople) == 1
                        and len(self.vm.itemTypes) == 3 and not self.vm.busy)

    def assert_provider_settings_loaded(self):
        settings = self.controller.gold_settings
        self.wait_until(lambda: not settings.busy and bool(settings.data), "offline provider settings not loaded")
        self.assertIs(settings.service, self.provider_settings)
        self.assertGreater(self.provider_settings.load_calls, 0)
        self.assertFalse(settings.isError, settings.message)
        self.assertEqual((settings.data["stored"], settings.data["configured"]), (False, False))

    def grant(self, *codes, revoke=()):
        """Change the signed-in actor's permission set (UI gating reads it) and notify the gated view models."""
        actor = self.controller.session.current_actor()
        object.__setattr__(actor, "permissions", frozenset((set(actor.permissions) | set(codes)) - set(revoke)))
        vms = self.controller.operational_viewmodels
        vms["inventoryVM"].dataChanged.emit()
        vms["oldGoldConversionVM"].busyChanged.emit()

    def stock(self):
        """In-stock pieces with the physical details the counter needs."""
        items = self.store.items
        items["100"].update(item_type_id=2, factory_id=5, factory_name="لوكس", item_notes="فص أزرق")
        items["101"].update(item_type_id=3, factory_id=None)
        items["102"].update(item_type_id=4, factory_id=5, factory_name="لوكس")          # SOLD
        base = {"size": None, "stock_classification": "NEW_STOCK", "inventory_status": "IN_STOCK",
                "category_code": "A", "category_name": None, "item_type_name": "دبلة", "item_type_id": 4,
                "karat_id": 1, "karat_value": 21}
        items.update({
            "103": {**base, "inventory_item_id": "103", "weight_grams": D("3.500"), "factory_id": 5,
                    "factory_name": "لوكس", "size": "18", "barcode_value": None, "item_notes": "محفورة"},
            "104": {**base, "inventory_item_id": "104", "weight_grams": D("3.520"), "factory_id": 5,
                    "factory_name": "لوكس", "size": "17", "barcode_value": "04260925"},
            "105": {**base, "inventory_item_id": "105", "weight_grams": D("3.500"), "factory_id": 6,
                    "factory_name": "النور", "size": "18", "barcode_value": None}})
        self.store.item_types[3] = {"id": 3, "name": "سلسلة", "default_category_id": None, "is_active": 1}
        self.store.factories[6] = {"id": 6, "name": "النور", "is_active": 1}

    def tearDown(self):
        self.vm.shutdown()
        self.controller.operational_viewmodels["reviewsVM"].shutdown()
        self.controller.operational_viewmodels["balancesVM"].shutdown()
        self.controller.operational_viewmodels["dashboardVM"].shutdown()
        self.controller.operational_viewmodels["invoiceHistoryVM"].shutdown()
        for engine in (self.controller.login_engine, self.controller.setup_engine, self.controller.operational_engine):
            for root in (engine.rootObjects() if engine is not None else []):
                root.hide()
        self.root = None
        root = None
        self.controller.authentication.shutdown()
        self.controller.gold_prices.shutdown()
        self.controller.gold_settings.shutdown()          # a Settings visit starts its load worker
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

    @staticmethod
    def child_items(parent):
        """parent.childItems(), refusing anything that is not a QQuickItem.

        The tree walkers twice met a PySide QMetaObject wrapper where a child item was expected (an AttributeError on objectName in
        two different full runs, never reproducible in isolation). The cause is NOT established; the failure stays a failure, but it now
        reports Python type, identity and tree position without asking the wrapper to format itself."""
        children = list(parent.childItems())
        for index, child in enumerate(children):
            if not isinstance(child, QQuickItem):
                ancestors, node = [], parent
                while node is not None and len(ancestors) < 25:
                    ancestors.append(f"{type(node).__name__}:{node.objectName() or '-'}")
                    node = node.parentItem()
                child_type = type(child)
                raise AssertionError(
                    f"UI tree walk met non-QQuickItem {child_type.__module__}.{child_type.__qualname__} "
                    f"id=0x{id(child):x} at child {index} of {len(children)} of "
                    f"{type(parent).__name__} id=0x{id(parent):x} {parent.objectName()!r}; "
                    f"ancestors (nearest first): {' < '.join(ancestors)}")
            yield child

    def visual_item(self, name, parent=None):
        parent = parent or self.root.contentItem()
        if parent.objectName() == name:
            return parent
        for child in self.child_items(parent):
            found = self.visual_item(name, child)
            if found is not None:
                return found
        return None

    def visible_item(self, name, parent=None):
        """The VISIBLE item with this objectName (repeated rows can share one name)."""
        parent = parent or self.root.contentItem()
        if parent.objectName() == name and parent.property("visible"):
            return parent
        for child in self.child_items(parent):
            found = self.visible_item(name, child)
            if found is not None:
                return found
        return None

    def item(self, name):
        for _ in range(100):
            found = self.root.findChild(QObject, name) or self.visual_item(name)
            if found is not None:
                return found
            QTest.qWait(20)
        self.fail(f"{name} not found")

    def reveal(self, item):
        """The supplier party page has ONE scroll (`partyScroll`): scroll it as the employee does until the item is inside its viewport."""
        flick = item.parentItem()
        while flick is not None and flick.objectName() not in ("partyScroll", "tripDetailScroll", "reportsScroller", "goldSettleFormScroll"):      # the market-trip detail scrolls the same way
            flick = flick.parentItem()
        if flick is None:
            return
        pos = item.mapToItem(flick.property("contentItem"), QPointF(0, 0))
        top, view = flick.property("contentY"), flick.height()
        if pos.y() < top + 4 or pos.y() + item.height() > top + view - 4:
            limit = max(0.0, flick.property("contentHeight") - view)
            flick.setProperty("contentY", float(max(0.0, min(pos.y() - 8, limit))))
            QTest.qWait(40)

    def click(self, item, button=Qt.LeftButton):
        if isinstance(item, str):
            item = self.item(item)
        self.assertTrue(item.property("visible"), item.objectName())
        self.assertTrue(item.property("enabled"), item.objectName())
        self.reveal(item)
        for _ in range(25):                                  # a row that just appeared may still be laid out
            point = item.mapToScene(QPointF(item.width() / 2, item.height() / 2)).toPoint()
            if 0 <= point.x() <= self.root.width() and 0 <= point.y() <= self.root.height():
                break
            QTest.qWait(20)
        self.assertTrue(0 <= point.x() <= self.root.width() and 0 <= point.y() <= self.root.height(),
                        f"{item.objectName()} is outside the window")
        record = click_diagnostics.begin(self, item, point)
        QTest.mouseClick(self.root, button, Qt.NoModifier, point)
        QTest.qWait(30)
        click_diagnostics.finish(self, record)

    def run(self, result=None):
        """Unchanged behaviour; a FAILED test additionally prints the click diagnostics (lost clicks, overlay and scroll state) so an intermittent failure carries evidence."""
        before = len(getattr(result, "failures", ())) + len(getattr(result, "errors", ()))
        outcome = super().run(result)
        outcome = result if result is not None else outcome
        if len(outcome.failures) + len(outcome.errors) > before:
            click_diagnostics.report(self)
        return outcome

    def assert_inside_window(self, item):
        self.reveal(item)
        point = item.mapToScene(QPointF(0, 0))
        width, height = self.root.width(), self.root.height()
        name = f"{item.objectName()} at {width}x{height}"
        self.assertGreaterEqual(point.x(), -1, name)
        self.assertGreaterEqual(point.y(), -1, name)
        self.assertLessEqual(point.x() + item.width(), width + 1, name)
        self.assertLessEqual(point.y() + item.height(), height + 1, name)

    def type_text(self, text):
        """Real key events carrying Unicode text (QTest.keyClick only covers Latin-1, not Arabic)."""
        for char in text:
            for kind in (QEvent.KeyPress, QEvent.KeyRelease):
                QCoreApplication.sendEvent(self.root, QKeyEvent(kind, 0, Qt.NoModifier, char))
        QTest.qWait(20)

    def type_into(self, name, text):
        self.click(name)
        QTest.keyClick(self.root, Qt.Key_A, Qt.ControlModifier)
        self.type_text(text)
        if name.startswith("legAmount_") or name == "accountAmountField":
            # Money amounts are validated when committed: leave the field as the employee does.
            QTest.keyClick(self.root, Qt.Key_Tab)
            QTest.qWait(40)

    def choose(self, combo_name, target):
        combo = self.item(combo_name)
        self.click(combo)
        self.wait_until(lambda: combo.property("down"), f"{combo_name} popup")
        for _ in range(10):
            highlighted = combo.property("highlightedIndex")
            if highlighted == target:
                break
            QTest.keyClick(self.root, Qt.Key_Down if highlighted < target else Qt.Key_Up)
        QTest.keyClick(self.root, Qt.Key_Return)
        self.wait_until(lambda: not combo.property("down"), f"{combo_name} closed")

    def select_salesperson(self):
        self.choose("invoiceSalesperson", 0)
        self.wait_until(lambda: self.vm.salespersonId == "5")

    def scan(self, barcode):
        count = self.vm.saleCount
        self.type_into("invoiceBarcodeField", barcode)
        QTest.keyClick(self.root, Qt.Key_Return)
        self.wait_until(lambda: self.vm.saleCount == count + 1, "barcode not added")
        self.wait_until(lambda: self.item("invoiceBarcodeField").property("activeFocus"), "barcode not refocused")

    def receive(self, amount, leg=0):
        """With sold pieces and no split yet, the amount typed is إجمالي الفاتورة (the one total; the
        single cash leg follows it). Otherwise (old gold only, or a split) it is one leg's real money."""
        if leg == 0 and self.vm.totalMode and self.vm._auto_single_leg():
            self.type_into("invoiceTotalField", amount)
            return
        self.type_into(f"legAmount_{leg}", amount)

    def total(self, amount):
        """إجمالي الفاتورة (with sold pieces)."""
        self.type_into("invoiceTotalField", amount)

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

    def printer_settings(self, session):
        """Settings → الطباعة over a temporary ini file and a fixed printer list (no real printer)."""
        import tempfile
        from PySide6.QtCore import QSettings
        from app.viewmodels.printer_settings_viewmodel import PrinterSettingsViewModel
        from config.printer_settings import PrinterSettings
        handle, path = tempfile.mkstemp(suffix=".ini")
        os.close(handle)
        self.addCleanup(lambda: os.path.exists(path) and os.remove(path))
        self.printing_jobs = []
        settings = PrinterSettings(QSettings(path, QSettings.IniFormat))
        bill = type("Bill", (), {"print_test_page": lambda s: (self.printing_jobs.append(
            ("bill", settings.bill_printer())), type("O", (), {"printed": True, "message": ""})())[1]})()
        label = type("Label", (), {"print_test_label": lambda s: self.printing_jobs.append(
            ("label", settings.label_printer()))})()
        return PrinterSettingsViewModel(session, settings=settings,
                                        printers=lambda: ["Brother HL-L2360D series", "ZDesigner GK420t"],
                                        bill_printing=bill, label_printing=label)

    def scroll_settings_to(self, name):
        """Settings scrolls; bring an item into view as the owner would."""
        flick = self.item("settingsScroll").property("contentItem")
        target = self.item(name)
        top = target.mapToItem(flick.property("contentItem"), QPointF(0, 0)).y()
        limit = max(0, flick.property("contentHeight") - flick.height())
        flick.setProperty("contentY", max(0, min(limit, top - 120)))
        QTest.qWait(150)

    def open_settings_dialog(self, page, button, dialog):
        """Open one of the settings windows the way the owner does: go to its page and click its button."""
        self.root.setProperty("currentPage", page)
        self.wait_until(lambda: self.visual_item(button) is not None and self.item(button).property("visible"), "the button is shown")
        QTest.qWait(600)                                                                         # the page and its layout settle before the click
        self.click(button)
        self.wait_until(lambda: self.item(dialog).property("opened"), "the window is open")

    def scroll_dialog_to(self, dialog, name):
        """A settings window scrolls; bring an item into view as the owner would."""
        flick = self.item(dialog).property("contentItem").property("contentItem")
        target = self.item(name)
        top = target.mapToItem(flick.property("contentItem"), QPointF(0, 0)).y()
        limit = max(0, flick.property("contentHeight") - flick.height())
        flick.setProperty("contentY", max(0, min(limit, top - 120)))
        QTest.qWait(150)

    def name_customer(self):
        """Every invoice needs a customer name: tests that don't choose one type a new walk-in name."""
        if self.vm.customerId or self.vm._new_customer["name"]:
            return
        self.type_into("customerNameField", "زبون تجريبي")
        popup = self.item("customerSuggestions")
        QTest.qWait(80)
        if popup.property("opened"):
            QTest.keyClick(self.root, Qt.Key_Escape)
        self.settle()

    def finalize(self, *acknowledge):
        self.name_customer()
        self.wait_until(lambda: self.vm.canFinalize, f"finalize not enabled: {self.vm.blockingProblems} "
                                                     f"{self.vm.previewError!r}")
        self.click("finalizeInvoice")
        dialog = self.item("confirmFinalizeDialog")
        self.wait_until(lambda: dialog.property("opened"), "confirmation not opened")
        QTest.qWait(250)
        for name in acknowledge:
            self.click(name)
        self.snapshot("confirm")
        self.click("confirmFinalize")
        self.wait_until(lambda: self.vm.hasSuccess, f"no success: {self.vm.errorMessage!r}")
        self.wait_until(lambda: not dialog.property("visible"), "confirmation still open")
        self.snapshot("success")

    def snapshot(self, label):
        target = os.getenv("MASGOLD_UI_REVIEW_DIR")
        if target:
            Path(target).mkdir(parents=True, exist_ok=True)
            self.root.grabWindow().save(str(Path(target) / f"billing-{self._testMethodName}-{label}.png"))

    def text(self, name):
        return self.item(name).property("text")

    def rows(self):
        return [r["inventoryItemId"] for r in self.vm.searchResults]

    def facet(self, name, check):
        """Click a cascade chip once its level is shown, then wait until the choice registered."""
        self.wait_until(lambda: self.visual_item(name) is not None and self.visual_item(name).property("visible"),
                        f"{name} not shown")
        QTest.qWait(60)
        self.click(name)
        self.wait_until(check, f"{name} not applied")
        self.wait_until(lambda: not self.vm.searchBusy)

    def combo_index(self, rows, name):
        return next(i for i, row in enumerate(rows) if row["name"] == name)

    def open_search(self):
        QTest.keyClick(self.root, Qt.Key_F3)
        panel = self.item("itemSearchPanel")
        self.wait_until(lambda: panel.property("visible") and not self.vm.searchBusy
                        and len(self.vm.searchFacets["itemTypes"]) > 0)
        return panel

    # ------------------------------------------------------------ 1: the real money is the sale price

    def test_barcode_sale_price_comes_from_the_money_received(self):
        self.select_salesperson()
        self.scan("01260925")
        row = self.vm.saleLinesModel.rows()[0]
        self.assertEqual((row["itemName"], row["barcode"], row["factory"], row["notes"]),
                         ("خاتم", "01260925", "لوكس", "فص أزرق"))
        self.assertIsNone(self.root.findChild(QObject, "agreedTotalField"))   # no price is ever typed
        self.assertIsNone(self.visual_item("agreedTotalField"))
        QTest.keyClick(self.root, Qt.Key_Return)                      # empty barcode → إجمالي الفاتورة
        self.wait_until(lambda: self.item("invoiceTotalField").property("activeFocus"))
        self.type_text("67010")
        self.settle()
        self.assertEqual((self.text("summaryGoldValue"), self.text("summaryEffectiveSale"),
                          self.text("summaryAverageMaking")), ("65,436.00 ج", "67,010.00 ج", "149.90"))
        self.assertIsNone(self.visual_item("summaryMaking"))                  # no total making for the salesman
        self.assertEqual((self.text("paymentDirectionLabel"), self.text("summaryDueLabel"), self.text("summaryDue")),
                         ("المستلم من العميل", "المستلم من العميل", "67,010.00 ج"))
        # A different amount is a different price: the average follows at once.
        self.receive("67115")
        self.settle()
        self.wait_until(lambda: self.text("summaryAverageMaking") == "159.90")
        self.receive("67010")
        self.settle()
        self.finalize()
        self.assertEqual(self.store.items["100"]["inventory_status"], "SOLD")
        line = self.store.lines[0][1]
        self.assertEqual((line["making_total"], line["making_per_gram"], line["identification_method"]),
                         (D("1574.00"), None, "SCAN"))
        self.assertEqual(self.store.ledger, [])
        self.assertEqual([m["amount_delta"] for m in self.store.cash_movements], [D("67010.00")])
        balances = self.controller.operational_viewmodels["balancesVM"]
        self.wait_until(lambda: self.text("balanceCash") == "67,010.00 ج", "cash strip not refreshed")
        self.assertEqual(self.warnings, [])

    # ------------------------------------------------------------ 2-5: cascading search

    def test_cascade_type_karat_factory_weight_only_in_stock(self):
        self.select_salesperson()
        self.open_search()
        types = {t["name"]: t["count"] for t in self.vm.searchFacets["itemTypes"]}
        self.assertEqual(types, {"خاتم": 1, "سلسلة": 1, "دبلة": 3})           # the SOLD دبلة is not counted
        self.facet("searchType_4", lambda: [k["name"] for k in self.vm.searchFacets["karats"]] == ["21"])
        self.facet("searchKarat_21", lambda: {f["name"] for f in self.vm.searchFacets["factories"]} == {"لوكس", "النور"})
        self.facet("searchFactory_5", lambda: self.vm.searchFactoryId == 5)
        self.type_into("itemSearchWeight", "3.5")
        self.type_into("itemSearchTolerance", "0")
        QTest.keyClick(self.root, Qt.Key_Return)
        self.wait_until(lambda: self.rows() == ["103"] and not self.vm.searchBusy)
        self.assertEqual(self.text("itemSearchPath"), "دبلة ← عيار 21 ← لوكس")
        self.assertFalse(self.item("itemSearchConfirm").property("enabled"))   # one match is still not a choice
        self.snapshot("search")
        self.click("itemSearchRow_0")
        QTest.keyClick(self.root, Qt.Key_Return)
        self.wait_until(lambda: self.vm.saleCount == 1 and not self.item("itemSearchPanel").property("visible"))
        self.wait_until(lambda: self.item("invoiceBarcodeField").property("activeFocus"))
        self.assertEqual(self.vm.saleLinesModel.rows()[0]["notes"], "محفورة")
        self.assertEqual(self.warnings, [])

    def test_cascade_with_size_instead_of_weight(self):
        self.open_search()
        self.facet("searchType_4", lambda: self.vm.searchTypeId == 4 and self.vm.searchFacets["karats"])
        self.facet("searchKarat_21", lambda: self.vm.searchKaratId == 1)
        self.type_into("itemSearchSize", "17")
        QTest.keyClick(self.root, Qt.Key_Return)
        self.wait_until(lambda: self.rows() == ["104"] and not self.vm.searchBusy)

    def test_changing_a_level_resets_the_levels_below_and_reset_clears_all(self):
        self.open_search()
        self.facet("searchType_4", lambda: self.vm.searchTypeId == 4 and self.vm.searchFacets["karats"])
        self.facet("searchKarat_21", lambda: self.vm.searchKaratId == 1 and self.vm.searchFacets["factories"])
        self.facet("searchFactory_6", lambda: self.vm.searchFactoryId == 6)
        self.wait_until(lambda: self.rows() == ["105"] and not self.vm.searchBusy)
        self.facet("searchType_2", lambda: self.vm.searchTypeId == 2)     # change الصنف
        self.wait_until(lambda: self.vm.searchKaratId == 0 and self.vm.searchFactoryId == 0)
        self.wait_until(lambda: self.rows() == ["100"] and not self.vm.searchBusy)
        self.assertEqual(self.text("itemSearchPath"), "خاتم")
        self.click("itemSearchReset")
        self.wait_until(lambda: self.vm.searchTypeId == 0 and set(self.rows()) == {"100", "101", "103", "104", "105"})
        self.assertNotIn("102", self.rows())                                  # SOLD never offered
        QTest.keyClick(self.root, Qt.Key_Escape)
        self.wait_until(lambda: not self.item("itemSearchPanel").property("visible"))

    def test_arabic_text_search_is_normalized(self):
        self.store.items["104"]["item_type_name"] = "دبلة"
        self.open_search()
        self.type_into("itemSearchText", "دبله")                               # ة typed as ه
        QTest.keyClick(self.root, Qt.Key_Return)
        self.wait_until(lambda: set(self.rows()) == {"103", "104", "105"} and not self.vm.searchBusy)

    # ------------------------------------------------------------ 6: manual unidentified sale

    def test_manual_sale_opens_a_review_case_without_inventory_identity(self):
        self.select_salesperson()
        QTest.keyClick(self.root, Qt.Key_F5)
        self.wait_until(lambda: self.item("manualPanel").property("visible"))
        self.choose("manualType", self.combo_index(self.vm.itemTypes, "دبلة"))
        self.click("manualKarat_21")
        self.type_into("manualWeight", "4")
        self.choose("manualFactory", self.combo_index(self.vm.factories, "لوكس"))
        self.click("manualReason_BARCODE_LOST")
        self.type_into("manualNotes", "نقش يدوي")
        self.snapshot("manual")
        self.click("manualAdd")
        self.wait_until(lambda: self.vm.saleCount == 1 and not self.item("manualPanel").property("visible"))
        self.assertEqual(self.vm.saleLinesModel.rows()[0]["tag"], "تحتاج مراجعة")
        self.receive("25500")                                                 # 4 × 6232 = 24928 gold
        self.settle()
        self.finalize()
        kind, line = self.store.lines[0]
        self.assertEqual((kind, line["item_name"], line["factory_name"], line["making_total"]),
                         ("SELL_UNIDENTIFIED", "دبلة", "لوكس", D("572.00")))
        self.assertEqual((self.store.cases[0]["reason_code"], self.store.cases[0]["notes"]), ("BARCODE_LOST", "نقش يدوي"))
        self.assertEqual(self.store.inventory_movements, [])
        reviews = self.controller.operational_viewmodels["reviewsVM"]
        self.wait_until(lambda: reviews.totalCount == 1)
        self.assertIn("مراجعات المدير  •  1", self.text("reviewsNavButton"))

    # ------------------------------------------------------------ 7-8: sale from old-gold stock

    def test_sale_from_old_gold_stock_deducts_grams(self):
        self.select_salesperson()
        QTest.keyClick(self.root, Qt.Key_F7)
        self.wait_until(lambda: self.item("stockSalePanel").property("visible"))
        self.type_into("stockDescription", "دبلة سادة")
        self.click("stockKarat_21")
        self.wait_until(lambda: self.text("stockAvailable") == "المتاح: 100.000")
        self.type_into("stockWeight", "3.2")
        self.snapshot("from-stock")
        QTest.keyClick(self.root, Qt.Key_Return)
        self.wait_until(lambda: self.vm.saleCount == 1)
        self.assertEqual(self.vm.saleLinesModel.rows()[0]["tag"], "من الكسر")
        self.receive("20500")
        self.settle()
        self.finalize()
        movement = self.store.old_gold[0]
        self.assertEqual((movement["code"], movement["signed_weight"]), ("INVOICE_SALE_OUT", D("-3.200")))
        self.assertEqual(self.store.lines[0][0], "SELL_OLD_GOLD")

    def test_insufficient_old_gold_stock_blocks_and_shows_available_grams(self):
        self.select_salesperson()
        QTest.keyClick(self.root, Qt.Key_F7)
        self.wait_until(lambda: self.item("stockSalePanel").property("visible"))
        self.type_into("stockDescription", "غويشة")
        self.click("stockKarat_21")
        self.type_into("stockWeight", "150")
        self.click("stockAdd")
        self.wait_until(lambda: self.vm.saleCount == 1)
        self.receive("1000000")
        self.settle()
        self.wait_until(lambda: "المتاح 100.000 جرام" in self.text("invoiceProblems"))
        self.assertFalse(self.item("finalizeInvoice").property("enabled"))

    # ------------------------------------------------------------ 9, 15: weights and old-gold discount

    def test_weight_totals_and_default_discount(self):
        self.scan("01260925")
        self.scan("02260925")
        self.add_old_gold("21", "5")
        self.add_old_gold("18", "2.5")
        self.assertEqual((self.text("soldWeights_21"), self.text("soldWeights_18"), self.text("soldWeights_24")),
                         ("مباع 21K: 10.500 جرام", "مباع 18K: 3.333 جرام", "مباع 24K: —"))
        self.assertEqual(self.text("boughtWeights_21"), "مشترى 21K: 5.000 جرام")
        self.assertEqual(self.text("oldGoldDiscountField"), "1.5")
        self.click("removeOldGold_1")
        self.wait_until(lambda: self.text("boughtWeights_18") == "مشترى 18K: —")
        self.type_into("oldGoldDiscountField", "2")
        QTest.keyClick(self.root, Qt.Key_Return)                               # committed on Enter
        self.wait_until(lambda: self.vm._draft().old_gold_lines[0].discount_value == "2")

    # ------------------------------------------------------------ 10-12: customers

    def test_existing_customer_by_name_and_by_phone(self):
        self.store.customers["12"].update(name="أحمد علي", mobile="01001234567", place="طنطا")
        self.store.ledger.append({"customer_id": 12, "amount_delta": D("3250.00")})
        self.type_into("customerNameField", "احمد")                        # normalized أ/ا
        popup = self.item("customerSuggestions")
        self.wait_until(lambda: popup.property("opened"))
        self.click("customerSuggestion_0")
        self.wait_until(lambda: self.vm.customerId == "12")
        self.assertEqual((self.text("customerMobileField"), self.text("customerPlaceField")), ("01001234567", "طنطا"))
        self.assertEqual(self.text("customerAccount"), "عليه 3,250.00 ج")
        self.click("customerClear")
        self.wait_until(lambda: self.vm.customerId == "")
        self.type_into("customerMobileField", "0100 123")
        self.wait_until(lambda: popup.property("opened"))
        self.click("customerSuggestion_0")
        self.wait_until(lambda: self.vm.customerId == "12")

    def test_new_customer_is_created_only_on_finalize(self):
        self.select_salesperson()
        self.type_into("customerNameField", "عميل جديد")
        self.type_into("customerMobileField", "01112223333")
        self.type_into("customerPlaceField", "بنها")
        self.wait_until(lambda: self.vm.customerInfo["state"] == "NEW")
        self.assertEqual(self.text("customerAccount"), "عميل جديد — يُسجل عند الحفظ")
        self.scan("01260925")
        self.receive("67010")
        self.type_into("legAmount_0", "60000")                              # 7,010 → على العميل automatically
        self.wait_until(lambda: self.text("accountAmountField") == "7010")
        self.settle()
        self.assertEqual(self.store.new_customers, [])                      # nothing created while typing
        self.finalize()
        created = self.store.new_customers[0]
        self.assertEqual((self.store.customers[created]["name"], self.store.customers[created]["place"]),
                         ("عميل جديد", "بنها"))
        self.assertEqual(self.store.invoices[0]["customer_id"], created)
        self.assertEqual(self.store.ledger[0]["amount_delta"], D("7010.00"))

    # ------------------------------------------------------------ 14: making review

    def test_making_outside_the_category_range_flags_review_only(self):
        self.store.items["100"]["category_id"] = 1
        self.store.categories[1]["sale_making_min_per_gram"] = D("200.00")
        self.select_salesperson()
        self.scan("01260925")
        self.receive("66500")
        self.settle()
        self.wait_until(lambda: self.item("makingReviewFlag").property("visible"))
        self.assertEqual(self.text("makingReviewFlag"), "السعر يحتاج مراجعة")
        self.finalize()
        self.assertEqual(self.store.invoices[0]["sell_total"], D("66500.00"))   # the customer price is unchanged
        self.assertEqual(self.store.reviews[0]["review_type"], "MAKING_PRICE")

    # ------------------------------------------------------------ 16-19: settlement

    def old_gold_payout(self, paid):
        # 10 g × 6207 = 62070 − 1.5% (931.05) = 61138.95 owed to the customer
        self.select_salesperson()
        self.add_old_gold("21", "10")
        self.wait_until(lambda: self.text("paymentDirectionLabel") == "المدفوع للعميل")
        self.receive(paid)
        self.settle()

    def test_small_difference_is_settled_explicitly_without_ledger(self):
        self.old_gold_payout("61130")
        self.wait_until(lambda: self.item("settlementDifference").property("visible"))
        # Shown against the FINAL amount (61,140 − 61,130); the explicit settlement keeps the exact 8.95.
        self.assertEqual(self.text("settlementDifference"), "10.00")
        self.assertFalse(self.item("finalizeInvoice").property("enabled"))   # never automatic
        self.click("settleDifference")
        self.settle()
        self.snapshot("settle")
        self.finalize()
        self.assertEqual(self.store.ledger, [])
        self.assertEqual(self.store.adjustments[0]["amount_signed"], D("-8.95"))
        self.assertEqual(self.store.invoices[0]["settlement_status"], "SETTLED")
        self.assertEqual([p["amount"] for p in self.store.payments], [D("61130.00")])
        self.assertEqual([m["amount_delta"] for m in self.store.cash_movements], [D("-61130.00")])

    def test_difference_deliberately_added_to_the_customer_account(self):
        self.store.customers["12"]["mobile"] = "01001234567"
        self.old_gold_payout("61130")
        self.click("differenceToAccount")                              # fills the account amount
        self.settle()
        self.wait_until(lambda: self.text("accountAmountField") == "10.00")   # 61,140 final − 61,130 paid
        self.assertIn("اسم العميل مطلوب لإتمام الفاتورة", self.text("invoiceProblems"))
        self.type_into("customerNameField", "عميل")
        popup = self.item("customerSuggestions")
        self.wait_until(lambda: popup.property("opened"))
        self.click("customerSuggestion_0")
        self.settle()
        self.finalize()
        self.assertEqual((self.store.ledger[0]["type_code"], self.store.ledger[0]["amount_delta"]),
                         ("BUY_BALANCE_DUE", D("-10.00")))
        self.assertEqual(self.store.adjustments, [])

    def test_above_fifty_needs_reason_and_manager_approval(self):
        self.old_gold_payout("61080")                                          # 58.95
        self.click("settleDifference")
        self.settle()
        self.wait_until(lambda: self.item("requestApproval").property("visible"))
        self.assertFalse(self.item("finalizeInvoice").property("enabled"))
        self.type_into("settlementReason", "فكة")
        self.click("requestApproval")
        popup = self.item("approvalPopup")
        self.wait_until(lambda: popup.property("opened"))
        self.type_into("approverUser", "manager")
        self.type_into("approverPassword", "manager-pass")
        self.click("approverConfirm")
        self.wait_until(lambda: self.vm.approverOk)
        self.wait_until(lambda: not popup.property("visible"), "approval popup still closing")
        self.assertEqual(self.text("approverText"), "موافقة: المدير")
        self.settle()
        self.finalize()
        adjustment = self.store.adjustments[0]
        self.assertEqual((adjustment["approved_by_user_id"], adjustment["reason"]), (3, "فكة"))
        self.assertEqual(self.store.ledger, [])

    # ------------------------------------------------------------ 20: cancel / failure

    # ------------------------------------------------------------ real money, direction, account (real clicks)

    def test_exchange_where_the_shop_pays_and_the_direction_is_explicit(self):
        self.select_salesperson()
        self.scan("01260925")
        self.add_old_gold("21", "20")                  # 124,140 − 1.5% = O 122,277.90 > gold 65,436 → OUT
        self.settle()
        self.wait_until(lambda: self.item("directionOut").property("visible"))
        self.assertTrue(self.item("directionOut").property("checkedLook"))
        self.assertFalse(self.item("paymentDirectionLabel").property("visible"))
        self.receive("55000")
        self.settle()
        self.wait_until(lambda: self.text("summaryEffectiveSale") == "67,277.90 ج")
        self.assertEqual((self.text("summaryDueLabel"), self.text("summaryDue"), self.text("summaryAverageMaking")),
                         ("المدفوع للعميل", "55,000.00 ج", "175.42"))
        # F8 never guesses a price when something is sold.
        self.assertFalse(self.item("payFullButton").property("visible"))          # no dead button
        QTest.keyClick(self.root, Qt.Key_F8)                                   # and F8 does nothing
        self.assertEqual(self.text("legAmount_0"), "55000")
        self.snapshot("shop-pays")
        self.finalize()
        self.assertEqual([(p["direction"], p["amount"]) for p in self.store.payments], [("OUT", D("55000.00"))])
        self.assertEqual([m["amount_delta"] for m in self.store.cash_movements], [D("-55000.00")])
        self.assertEqual(self.store.invoices[0]["sell_total"], D("67277.90"))

    def test_exchange_where_the_customer_pays_the_shop(self):
        self.select_salesperson()
        self.scan("01260925")
        self.add_old_gold("21", "5")                   # O 30,569.47 < gold → IN
        self.settle()
        self.wait_until(lambda: self.item("directionIn").property("checkedLook"))
        self.receive("36440")
        self.settle()
        self.wait_until(lambda: self.text("summaryEffectiveSale") == "67,009.47 ج")
        self.assertEqual((self.text("summaryDueLabel"), self.text("summaryAverageMaking")),
                         ("المستلم من العميل", "149.85"))
        self.snapshot("customer-pays")
        self.finalize()
        self.assertEqual([m["amount_delta"] for m in self.store.cash_movements], [D("36440.00")])

    def test_f8_fills_the_full_amount_for_old_gold_only(self):
        self.select_salesperson()
        self.add_old_gold("21", "10")                  # 62,070 − 1.5% = 61,138.95 owed to the customer
        self.settle()
        self.wait_until(lambda: self.item("payFullButton").property("visible"))
        QTest.keyClick(self.root, Qt.Key_F8)
        self.wait_until(lambda: self.text("legAmount_0") == "61140.00", "F8 did not fill")
        self.settle()
        self.assertEqual((self.text("paymentDirectionLabel"), self.text("summaryDue")),
                         ("المدفوع للعميل", "61,140.00 ج"))
        self.finalize()
        self.assertEqual([m["amount_delta"] for m in self.store.cash_movements], [D("-61140.00")])

    def test_split_payment_and_account_amount_make_the_price(self):
        # Commercial 67,011 = 40,000 cash + 27,011 InstaPay. Moving 7,011 to على العميل leaves
        # 60,000 required now; with two legs the employee must adjust them to match — never guessed.
        self.store.customers["12"]["mobile"] = "01001234567"
        self.select_salesperson()
        self.type_into("customerNameField", "عميل")
        popup = self.item("customerSuggestions")
        self.wait_until(lambda: popup.property("opened"))
        self.click("customerSuggestion_0")
        self.scan("01260925")
        self.total("67010")                                                    # إجمالي الفاتورة
        self.wait_until(lambda: self.text("legAmount_0") == "67010")           # one cash leg follows it
        self.type_into("legAmount_0", "40000")
        self.wait_until(lambda: self.text("accountAmountField") == "27010")    # unpaid → على العميل at once
        self.click("addPaymentLeg")
        self.wait_until(lambda: self.visual_item("legMethod_1") is not None)
        self.assertEqual(self.text("legAmount_1"), "27010")                    # starts with exactly the remainder
        self.assertEqual(self.text("accountAmountField"), "")
        self.choose("legMethod_1", 2)                                          # InstaPay
        self.wait_until(lambda: self.item("legAccount_1").property("visible"))
        self.choose("legAccount_1", 0)
        self.settle()
        self.wait_until(lambda: self.text("summaryEffectiveSale") == "67,010.00 ج")
        self.type_into("legAmount_1", "30000")                                 # 40,000 + 30,000 > 67,010
        self.wait_until(lambda: "لا يمكن أن يتجاوز" in self.text("paymentWarning"), "over-allocation accepted")
        self.assertEqual(self.vm.paymentLegs[1]["amount"], "27010")            # refused: last valid value
        self.type_into("legAmount_1", "20010")                                 # 7,000 left → على العميل
        self.wait_until(lambda: self.text("accountAmountField") == "7000")
        self.settle()
        self.assertEqual(self.text("invoiceProblems"), "")
        self.assertTrue(self.item("finalizeInvoice").property("enabled"))
        self.assertEqual(self.text("summaryEffectiveSale"), "67,010.00 ج")     # commercial amount unchanged
        self.finalize()
        self.assertEqual([m["amount_delta"] for m in self.store.cash_movements], [D("40000.00")])
        self.assertEqual([(m["bank_account_id"], m["amount_delta"]) for m in self.store.bank_movements],
                         [(7, D("20010.00"))])
        self.assertEqual(self.store.ledger[0]["amount_delta"], D("7000.00"))  # a balance, never a movement
        self.assertEqual(self.store.invoices[0]["sell_total"], D("67010.00"))

    def test_exchange_discount_outside_1_25_to_5_blocks_save(self):
        self.select_salesperson()
        self.scan("01260925")
        self.add_old_gold("21", "5")
        self.receive("36440")
        self.settle()
        self.assertFalse(self.item("discountChangedFlag").property("visible"))
        average, old_net = self.text("summaryAverageMaking"), self.text("summaryOldGold")
        # 5.5% is rejected on commit: the field returns to the last valid 1.5 and nothing is recalculated.
        self.type_into("oldGoldDiscountField", "5.5")
        QTest.keyClick(self.root, Qt.Key_Return)
        self.wait_until(lambda: self.text("oldGoldDiscountField") == "1.5", "rejected value stayed in the field")
        self.assertEqual(self.text("discountWarning"), "النسبة المسموح بها من \u20661.25%\u2069 إلى \u20665%\u2069")
        self.settle()
        self.assertEqual((self.text("summaryAverageMaking"), self.text("summaryOldGold")), (average, old_net))
        self.assertEqual(self.vm._draft().old_gold_lines[0].discount_value, "1.5")
        # Typed but not committed, then Save: committed (and rejected) first, never saved.
        self.name_customer()
        self.type_into("oldGoldDiscountField", "1")
        QTest.keyClick(self.root, Qt.Key_F9)
        self.wait_until(lambda: self.text("oldGoldDiscountField") == "1.5")
        # The restored 1.5 is valid, so F9 opens the confirmation with the 1.5 figures; go back.
        dialog = self.item("confirmFinalizeDialog")
        self.wait_until(lambda: dialog.property("opened"))
        QTest.qWait(250)
        QTest.keyClick(self.root, Qt.Key_Escape)
        self.wait_until(lambda: not dialog.property("visible"))
        self.assertEqual(self.vm._draft().old_gold_lines[0].discount_value, "1.5")
        self.type_into("oldGoldDiscountField", "2")
        QTest.keyClick(self.root, Qt.Key_Return)
        self.settle()
        self.wait_until(lambda: self.item("discountChangedFlag").property("visible"))
        self.finalize()
        self.assertEqual([r["review_type"] for r in self.store.reviews], ["OLD_GOLD_DISCOUNT"])
        self.assertNotEqual(average, "")

    def test_live_regression_customer_account_never_moves_the_average(self):
        # 21K 3.600 g sold (22,460.40), old gold net 36,724.73, 12,800 paid to the customer → 406.76/g.
        from tests import test_invoice_service as fixture
        prices = patch.dict(fixture.PRICES, {1: (D("6213.9983"), D("6239.0000"))})
        prices.start()
        self.addCleanup(prices.stop)
        self.vm.refreshPrices()
        self.store.items["106"] = {**self.store.items["100"], "inventory_item_id": "106", "weight_grams": D("3.600"),
                                   "barcode_value": "06260925"}
        self.store.customers["12"]["mobile"] = "01001234567"
        self.select_salesperson()
        self.type_into("customerNameField", "عميل")
        popup = self.item("customerSuggestions")
        self.wait_until(lambda: popup.property("opened"))
        self.click("customerSuggestion_0")
        self.scan("06260925")
        self.add_old_gold("21", "6")
        self.receive("12800")
        self.settle()
        self.wait_until(lambda: self.text("summaryAverageMaking") == "406.76", "regression average")
        expected = (self.text("summaryGoldValue"), self.text("summaryOldGold"), self.text("summaryEffectiveSale"),
                    self.text("summaryAverageMaking"), self.text("summaryDueLabel"), self.text("summaryDue"))
        self.assertEqual(expected, ("22,460.40 ج", "36,724.73 ج", "23,924.73 ج", "406.76", "المدفوع للعميل",
                                    "12,800.00 ج"))
        # Only للعميل is offered on this OUT invoice; 1,000 deferred to the account leaves 11,800 paid now,
        # but the commercial amount, making and average stay exactly the same (23,924.73 / 406.76).
        self.assertFalse(self.item("accountCustomerOwes").property("enabled"))
        self.assertTrue(self.item("accountShopOwes").property("enabled"))
        self.type_into("accountAmountField", "1000")
        self.settle()
        self.wait_until(lambda: self.text("accountEffect") == "سيضاف للعميل: 1,000.00 ج")
        self.assertEqual(self.text("legAmount_0"), "11800")
        self.assertEqual((self.text("summaryGoldValue"), self.text("summaryOldGold"), self.text("summaryEffectiveSale"),
                          self.text("summaryAverageMaking")),
                         expected[:4])
        self.snapshot("account-shop-owes")
        self.finalize()
        self.assertEqual(self.store.invoices[0]["sell_total"], D("23924.73"))
        self.assertEqual([m["amount_delta"] for m in self.store.cash_movements], [D("-11800.00")])
        self.assertEqual([(r["type_code"], r["amount_delta"]) for r in self.store.ledger],
                         [("BUY_BALANCE_DUE", D("-1000.00"))])

    def test_header_places_user_left_and_balances_in_the_centre(self):
        self.header_layout_checks("")
        # Realistic large figures must still fit on one line without pushing the price card away.
        balances = self.controller.operational_viewmodels["balancesVM"]
        balances._strip = {"cash_balance": "1250300.50", "old_gold": [
            {"karat": 21, "grams": "1234.567"}, {"karat": 18, "grams": "456.789"}, {"karat": 24, "grams": "78.901"}]}
        balances.changed.emit()
        self.assertEqual(self.text("balanceCash"), "1,250,300.50 ج")
        self.header_layout_checks("-large")

    def header_layout_checks(self, suffix):
        for width, height in SIZES:
            self.root.setWidth(width)
            self.root.setHeight(height)
            QTest.qWait(150)
            with self.subTest(size=(width, height)):
                x = {name: self.item(name).mapToScene(QPointF(0, 0)).x()
                     for name in ("topUserName", "logoutButton", "balanceStrip", "goldPriceArea")}
                for name in ("topUserName", "logoutButton", "balanceStrip", "balanceCash", "balanceKarat_21",
                             "balanceKarat_18", "balanceKarat_24", "goldPriceArea", "connectionDot"):
                    self.assert_inside_window(self.item(name))
                    item = self.item(name)
                    if item.property("contentWidth") is not None:                    # never clipped
                        self.assertLessEqual(item.property("contentWidth"), item.width() + 1, f"{name} clipped")
                # Physical left → right: user, logout, balances, Gold Price card.
                self.assertLess(x["topUserName"], x["logoutButton"])
                self.assertLess(x["logoutButton"], x["balanceStrip"])
                self.assertLess(x["balanceStrip"], x["goldPriceArea"])
                strip, card = self.item("balanceStrip"), self.item("goldPriceArea")
                self.assertLessEqual(x["balanceStrip"] + strip.width(), x["goldPriceArea"])   # never under the card
                self.assertGreaterEqual(card.width(), 250)
                for label in card.findChildren(QObject):                          # price card text never clipped
                    if label.property("contentWidth") is not None and label.property("visible"):
                        self.assertLessEqual(label.property("contentWidth"), label.property("width") + 1,
                                             f"price card text clipped: {label.property('text')}")
                # ONE row of boxes: النقدية | 21 | 18 | 24, right to left, inside the top bar.
                names = ("balanceBoxCash", "balanceBox_21", "balanceBox_18", "balanceBox_24")
                centres = [self.item(n).mapToScene(QPointF(0, self.item(n).height() / 2)) for n in names]
                self.assertLess(max(c.y() for c in centres) - min(c.y() for c in centres), 1.5, "strip wrapped")
                xs = [c.x() for c in centres]
                self.assertEqual(xs, sorted(xs, reverse=True))
                self.assertLessEqual(strip.height(), 48)
                # Nothing is pushed past the top bar (under the sidebar): the price card and status stay whole.
                bar = self.item("topBar")
                bar_right = bar.mapToScene(QPointF(bar.width(), 0)).x()
                status = self.item("connectionStatus") if width >= 1200 else self.item("connectionDot")
                self.assertTrue(status.property("visible"))
                self.assertLessEqual(status.mapToScene(QPointF(status.width(), 0)).x(),
                                     bar_right - (10 if width < 1200 else 16) + 1, "header overflows its margin")
                self.assertLessEqual(x["goldPriceArea"] + card.width(), status.mapToScene(QPointF(0, 0)).x())
                self.snapshot(f"header-{width}x{height}{suffix}")

    def test_all_methods_stay_visible_and_unavailable_ones_cannot_save(self):
        self.store.bank_accounts.clear()                                       # no wallet, no bank: never faked
        self.vm.refreshReference()
        self.wait_until(lambda: self.vm._reference.get("bank_accounts") == [])
        self.select_salesperson()
        self.scan("01260925")
        combo = self.item("legMethod_0")
        self.assertEqual(combo.property("count"), 4)
        self.assertEqual([m["text"] for m in self.vm.paymentMethods], ["نقدي", "محفظة", "InstaPay", "تحويل بنكي"])
        self.receive("67010")
        for index, text in ((1, "لا توجد محفظة مفعلة"), (2, "لا يوجد حساب بنكي مفعّل"), (3, "لا يوجد حساب بنكي مفعّل")):
            with self.subTest(method=index):
                self.choose("legMethod_0", index)
                self.wait_until(lambda: self.text("legUnavailable_0") == text)
                self.assertTrue(self.item("legUnavailable_0").property("visible"))
                self.assertFalse(self.item("legAccount_0").property("visible"))
                self.settle()
                self.wait_until(lambda: text in self.text("invoiceProblems"))
                self.assertFalse(self.item("finalizeInvoice").property("enabled"))
                if index == 1:
                    self.snapshot("payment-no-wallet")
        QTest.keyClick(self.root, Qt.Key_F9)                                   # F9 cannot save it either
        QTest.qWait(200)
        self.assertFalse(self.item("confirmFinalizeDialog").property("opened"))
        self.assertEqual((self.store.payments, self.store.bank_movements, self.store.cash_movements), ([], [], []))
        self.choose("legMethod_0", 0)                                          # cash still works
        self.wait_until(lambda: not self.item("legUnavailable_0").property("visible"))
        self.settle()
        self.finalize()
        self.assertEqual([m["amount_delta"] for m in self.store.cash_movements], [D("67010.00")])

    def test_real_accounts_enable_wallet_instapay_and_bank_transfer(self):
        # Fixture: one bank account (7) and one wallet (9): each is preselected for its methods.
        self.select_salesperson()
        self.scan("01260925")
        for index, account in ((1, "9"), (2, "7"), (3, "7")):
            with self.subTest(method=index):
                self.choose("legMethod_0", index)
                self.wait_until(lambda: self.item("legAccount_0").property("visible"))
                self.assertFalse(self.item("legUnavailable_0").property("visible"))
                self.assertEqual(self.vm.paymentLegs[0]["account"], account)
        self.snapshot("payment-bank-transfer-account")

    def test_customer_account_directions_look_different(self):
        # Incoming invoice (customer pays): only على العميل is offered, in the receivable colour.
        # Item 100: 10.5 g × 6232 = 65,436 gold value.
        self.select_salesperson()
        self.scan("01260925")
        self.receive("68435")
        self.settle()
        making = self.text("summaryAverageMaking")
        owes, owed, effect = self.item("accountCustomerOwes"), self.item("accountShopOwes"), self.item("accountEffect")
        self.assertTrue(owes.property("enabled"))
        self.assertFalse(owed.property("enabled"))                              # not offered on this direction
        self.type_into("legAmount_0", "65435")                                  # 3,000 unpaid → على العميل
        self.settle()
        self.wait_until(lambda: self.text("accountEffect") == "سيضاف على العميل: 3,000.00 ج")
        self.assertTrue(self.item("accountAmountField").property("readOnly"))   # automatic, never typed
        receivable = owes.property("background").property("color")
        self.assertEqual(effect.property("color"), receivable)                 # the same semantic family
        self.assertLess(receivable.hueF() - 0.5, 0.1)                           # cool (blue / cyan)
        self.assertEqual(self.text("summaryAverageMaking"), making)            # never moves the making
        self.snapshot("account-customer-owes-3000")

        # Outgoing invoice (shop pays): only للعميل is offered, in the payable colour.
        QTest.keyClick(self.root, Qt.Key_F2)
        self.wait_until(lambda: self.vm.saleCount == 0 and self.vm.oldGoldCount == 0)
        self.select_salesperson()
        self.scan("01260925")
        self.add_old_gold("21", "30")                                          # O (30 g) = 183,637.65
        self.click("directionOut")
        self.receive("120000")
        self.settle()
        self.assertFalse(owes.property("enabled"))
        self.assertTrue(owed.property("enabled"))
        self.type_into("accountAmountField", "3000")
        self.settle()
        self.wait_until(lambda: self.text("accountEffect") == "سيضاف للعميل: 3,000.00 ج")
        self.assertEqual(self.text("legAmount_0"), "117000")
        payable = owed.property("background").property("color")
        self.assertEqual(effect.property("color"), payable)
        self.assertNotEqual(receivable, payable)                               # never the same highlight
        self.assertLess(payable.hueF(), 0.12)                                  # warm (orange)
        self.snapshot("account-shop-owes-3000")

    def test_manual_entry_dialog_explains_the_reasons(self):
        self.select_salesperson()
        QTest.keyClick(self.root, Qt.Key_F5)
        self.wait_until(lambda: self.item("manualPanel").property("visible"))
        self.assertEqual(self.text("manualHint"),
                         "إذا أمكن تحديد القطعة من البحث F3، استخدم القطعة المسجلة بدل الإدخال اليدوي.")
        self.assertEqual([self.text("manualReason_" + c) for c in ("NO_BARCODE", "BARCODE_LOST", "NOT_FOUND", "OTHER")],
                         ["لم يُنشأ لها باركود", "الباركود مفقود أو تالف", "لم أجد القطعة في البحث", "سبب آخر"])
        for code in ("NO_BARCODE", "BARCODE_LOST", "NOT_FOUND", "OTHER"):
            self.assert_inside_window(self.item("manualReason_" + code))
        self.snapshot("manual-entry-reasons")

    # ---- save confirmation: حفظ فقط / حفظ وطباعة / إلغاء; printing only after the save
    def open_confirmation(self):
        self.select_salesperson()
        self.scan("01260925")
        self.receive("67010")
        self.settle()
        self.name_customer()
        self.wait_until(lambda: self.vm.canFinalize)
        QTest.keyClick(self.root, Qt.Key_F9)
        dialog = self.item("confirmFinalizeDialog")
        self.wait_until(lambda: dialog.property("opened"))
        QTest.qWait(250)
        return dialog

    def test_confirmation_offers_save_only_save_and_print_and_cancel(self):
        dialog = self.open_confirmation()
        self.assertEqual([self.text(n) for n in ("confirmFinalize", "confirmSaveAndPrint", "confirmBack")],
                         ["حفظ فقط (Enter)", "حفظ وطباعة", "إلغاء (Esc)"])
        for name in ("confirmFinalize", "confirmSaveAndPrint", "confirmBack"):
            self.assert_inside_window(self.item(name))
        self.snapshot("save-confirmation")
        self.click("confirmBack")                                              # إلغاء: nothing saved
        self.wait_until(lambda: not dialog.property("visible"))
        self.assertEqual((self.store.invoices, self.printer.calls), ([], []))
        self.assertEqual(self.vm.saleCount, 1)                                 # the draft is still there

    def test_save_only_finalizes_without_a_print_job(self):
        self.open_confirmation()
        self.click("confirmFinalize")
        self.wait_until(lambda: self.vm.hasSuccess)
        self.wait_until(lambda: not self.item("confirmFinalizeDialog").property("visible"))
        self.assertEqual(len(self.store.invoices), 1)
        self.assertEqual(self.printer.calls, [])
        self.assertFalse(self.item("printStatusBanner").property("visible"))

    def test_save_and_print_finalizes_once_then_prints_the_committed_invoice(self):
        self.open_confirmation()
        self.click("confirmSaveAndPrint")
        self.wait_until(lambda: self.vm.hasSuccess and self.vm.printSucceeded)
        self.wait_until(lambda: not self.item("confirmFinalizeDialog").property("visible"))
        self.assertEqual(len(self.store.invoices), 1)                            # one invoice
        self.assertEqual(len(self.store.payments), 1)                            # one payment
        self.assertEqual(len(self.store.cash_movements), 1)                      # one movement
        self.assertEqual(self.printer.calls, ["901"])                            # the committed invoice id
        self.assertEqual(self.text("printStatusText"), "تم إرسال الفاتورة للطابعة.")
        self.snapshot("saved-and-printed")

    def test_save_and_print_shows_busy_and_ignores_repeated_retry(self):
        import threading
        self.printer.gate = gate = threading.Event()
        self.addCleanup(gate.set)
        self.open_confirmation()
        self.click("confirmSaveAndPrint")
        self.wait_until(lambda: self.vm.hasSuccess and self.vm.printBusy, "print never started")
        self.wait_until(lambda: self.item("printStatusBanner").property("visible"))
        self.assertEqual(self.vm.printStatusText, "جاري إرسال أمر الطباعة...")
        self.assertTrue(self.item("printBusyIndicator").property("visible"))
        self.assertEqual(self.text("printStatusText"), "جاري إرسال أمر الطباعة...")
        self.assert_inside_window(self.item("printStatusBanner"))
        for _ in range(4):                                       # repeated retry signals while the job is in flight
            self.vm.retryPrint()
        QTest.qWait(80)
        self.assertEqual(self.printer.calls, ["901"])            # ONE job
        gate.set()
        self.wait_until(lambda: self.vm.printSucceeded and not self.vm.printBusy)
        self.assertEqual(self.text("printStatusText"), "تم إرسال الفاتورة للطابعة.")
        self.assertFalse(self.item("printBusyIndicator").property("visible"))
        self.assertEqual(self.printer.calls, ["901"])
        self.assertEqual((len(self.store.invoices), len(self.store.payments)), (1, 1))

    def test_printer_failure_keeps_the_invoice_and_offers_retry(self):
        self.printer.outcome = (False, "الطابعة 'Brother HL-L2360D series' غير متاحة أو غير متصلة.")
        self.open_confirmation()
        self.click("confirmSaveAndPrint")
        self.wait_until(lambda: self.vm.hasSuccess and self.vm.printFailed)
        self.wait_until(lambda: not self.item("confirmFinalizeDialog").property("visible"))
        self.assertEqual(len(self.store.invoices), 1)                            # still finalized
        self.assertEqual(self.store.items["100"]["inventory_status"], "SOLD")
        self.assertEqual([m["amount_delta"] for m in self.store.cash_movements], [D("67010.00")])
        self.assertIn("غير متاحة", self.text("printStatusText"))
        self.assertTrue(self.text("printStatusText").startswith("تعذر إرسال الفاتورة للطابعة."))
        self.assertTrue(self.item("printRetry").property("visible"))
        self.snapshot("print-failed")
        self.printer.outcome = (True, "تم إرسال الفاتورة للطابعة.")
        retry, banner = self.item("printRetry"), self.item("printStatusBanner")
        self.assertLessEqual(retry.height(), banner.height())                  # never spills over the banner
        self.click("printRetry")
        self.wait_until(lambda: self.vm.printSucceeded, f"retry: {self.printer.calls} {self.vm.printMessage!r}")
        self.assertEqual(len(self.printer.calls), 2)
        self.assertEqual((len(self.store.invoices), len(self.store.payments), len(self.store.cash_movements)),
                         (1, 1, 1))                                              # retry never reposts anything

    def test_balance_strip_colours_are_distinct_and_zero_is_muted(self):
        self.select_salesperson()
        balances = self.controller.operational_viewmodels["balancesVM"]
        self.wait_until(lambda: balances.ready)
        names = ("balanceCash", "balanceKarat_21", "balanceKarat_18", "balanceKarat_24")
        boxes = ("balanceBoxCash", "balanceBox_21", "balanceBox_18", "balanceBox_24")
        colours = [self.item(n).property("accent").name() for n in boxes]
        self.assertEqual(len(set(colours)), 4)                                   # one stable colour each
        # Fixture: cash 0, 21K 100.000, 18K 0.000, 24K 0.000 → zero figures muted, never hidden.
        self.assertEqual([self.item(n).property("opacity") < 1 for n in names], [True, False, True, True])
        self.assertTrue(all(self.item(n).property("visible") for n in names))
        self.assertTrue(self.item("balanceCash").property("font").bold())        # cash is the most prominent
        self.snapshot("balance-strip-colours")

    def piece_for_23000(self):
        # 3.600 g × 6232 = 22,435.20 gold, so 23,000 is a real commercial amount.
        self.store.items["106"] = {**self.store.items["100"], "inventory_item_id": "106", "weight_grams": D("3.600"),
                                   "barcode_value": "06260925"}
        self.select_salesperson()
        self.scan("06260925")

    def test_invoice_total_stays_while_money_moves_to_the_account(self):
        self.piece_for_23000()
        self.receive("23000")
        self.settle()
        self.wait_until(lambda: self.text("summaryInvoiceTotal") == "23,000.00 ج")
        average = self.text("summaryAverageMaking")
        self.type_into("legAmount_0", "20000")                                 # 3,000 → على العميل automatically
        self.settle()
        self.wait_until(lambda: self.text("summaryDue") == "20,000.00 ج")
        self.assertEqual((self.text("summaryInvoiceTotal"), self.text("summaryDueLabel"), self.text("summaryDue")),
                         ("23,000.00 ج", "المستلم من العميل", "20,000.00 ج"))
        self.assertEqual([r["label"] + ": " + r["amount"] for r in self.vm.paymentBreakdown],
                         ["نقدي: 20,000.00 ج", "على العميل: 3,000.00 ج"])
        self.assertEqual(self.text("breakdownRow_1"), "‏على العميل: 3,000.00 ج")
        self.assertEqual(self.text("summaryAverageMaking"), average)             # making from 23,000
        self.snapshot("invoice-total-23000-cash-20000-account-3000")

    def test_split_breakdown_lists_only_the_methods_used(self):
        self.piece_for_23000()
        self.total("23000")
        self.type_into("legAmount_0", "5000")
        self.click("addPaymentLeg")
        self.wait_until(lambda: self.visual_item("legMethod_1") is not None)
        self.assertEqual(self.text("legAmount_1"), "18000")                    # the remainder
        self.choose("legMethod_1", 2)                                          # InstaPay
        self.wait_until(lambda: self.item("legAccount_1").property("visible"))
        self.choose("legAccount_1", 0)
        self.receive("15000", leg=1)
        self.type_into("accountAmountField", "3000")
        self.settle()
        self.wait_until(lambda: self.text("summaryDue") == "20,000.00 ج")
        self.assertEqual(self.text("summaryInvoiceTotal"), "23,000.00 ج")
        labels = [r["label"] + ": " + r["amount"] for r in self.vm.paymentBreakdown]
        self.assertEqual(labels[0], "نقدي: 5,000.00 ج")
        self.assertTrue(labels[1].startswith("InstaPay — ") and labels[1].endswith(": 15,000.00 ج"), labels)
        self.assertEqual(labels[2], "على العميل: 3,000.00 ج")
        self.assertEqual(len(labels), 3)                                        # no zero rows
        self.snapshot("invoice-total-split")

    def test_save_without_a_customer_name_is_refused_and_focuses_the_name(self):
        self.select_salesperson()
        self.scan("01260925")
        self.receive("67010")
        self.settle()
        self.wait_until(lambda: "اسم العميل مطلوب لإتمام الفاتورة" in self.text("invoiceProblems"))
        self.assertFalse(self.item("finalizeInvoice").property("enabled"))
        QTest.keyClick(self.root, Qt.Key_F9)
        QTest.qWait(200)
        self.assertFalse(self.item("confirmFinalizeDialog").property("opened"))
        self.assertTrue(self.item("customerNameField").property("activeFocus"))
        self.type_into("customerNameField", "   ")                              # whitespace is not a name
        QTest.qWait(80)
        self.settle()
        self.assertFalse(self.vm.canFinalize)
        self.snapshot("save-without-customer")
        self.assertEqual(self.store.invoices, [])

    def test_save_and_print_without_a_bill_printer_saves_nothing_and_offers_settings(self):
        self.printer.ready = (False, "لم يتم تحديد طابعة الفواتير. اخترها من الإعدادات.")
        dialog = self.open_confirmation()
        self.click("confirmSaveAndPrint")
        self.wait_until(lambda: self.text("billPrinterMissing") == "لم يتم تحديد طابعة الفواتير. اخترها من الإعدادات.")
        self.assertTrue(dialog.property("opened"))                              # nothing saved, dialog stays
        self.assertEqual((self.store.invoices, self.printer.calls), ([], []))
        self.snapshot("save-and-print-without-bill-printer")
        self.click("openPrintingSettings")
        self.wait_until(lambda: self.root.property("currentPage") == 6)
        self.wait_until(lambda: not dialog.property("visible"))
        self.assert_provider_settings_loaded()
        self.assertEqual(self.store.invoices, [])
        self.root.setProperty("currentPage", 1)
        self.wait_until(lambda: self.item("finalizeInvoice").property("visible"))
        self.click("finalizeInvoice")                                          # حفظ فقط still works
        self.wait_until(lambda: dialog.property("opened"))
        QTest.qWait(250)
        self.click("confirmFinalize")
        self.wait_until(lambda: self.vm.hasSuccess)
        self.assertEqual((len(self.store.invoices), self.printer.calls), (1, []))

    def test_settings_printing_has_two_independent_printers(self):
        printing = self.controller.operational_viewmodels["printerSettingsVM"]
        self.root.setProperty("currentPage", 6)
        self.wait_until(lambda: self.visual_item("billPrinterCombo") is not None)
        self.assert_provider_settings_loaded()
        self.scroll_settings_to("billPrinterCombo")
        self.choose("billPrinterCombo", 0)
        self.wait_until(lambda: printing.billPrinter["name"] == "Brother HL-L2360D series")
        self.assertEqual(printing.labelPrinter["name"], "")                    # untouched
        self.scroll_settings_to("labelPrinterCombo")
        self.choose("labelPrinterCombo", 1)
        self.wait_until(lambda: printing.labelPrinter["name"] == "ZDesigner GK420t")
        self.assertEqual(printing.billPrinter["name"], "Brother HL-L2360D series")
        self.click("testBillPrint")
        # The bill test print runs on a background thread (the UI never freezes): wait for its one job.
        self.wait_until(lambda: len(self.printing_jobs) == 1 and not printing.billTestBusy, "bill test print not done")
        self.scroll_settings_to("testLabelPrint")
        self.click("testLabelPrint")
        self.assertEqual(self.printing_jobs, [("bill", "Brother HL-L2360D series"), ("label", "ZDesigner GK420t")])
        self.assertEqual(self.store.invoices, [])
        for width, height in SIZES:
            self.root.setWidth(width)
            self.root.setHeight(height)
            QTest.qWait(150)
            self.scroll_settings_to("settingsPrinting")
            self.assertIsNotNone(self.visual_item("settingsPrinting"))
            self.snapshot(f"settings-printing-{width}x{height}")

    def test_f7_description_is_the_last_field_before_add(self):
        self.select_salesperson()
        QTest.keyClick(self.root, Qt.Key_F7)
        self.wait_until(lambda: self.item("stockSalePanel").property("visible"))
        self.wait_until(lambda: self.item("stockType").property("activeFocus"))   # the first field
        y = {n: self.item(n).mapToScene(QPointF(0, self.item(n).height() / 2)).y()          # vertical centres
             for n in ("stockType", "stockKarat_21", "stockWeight", "stockFactory", "stockSize", "stockNotes",
                       "stockDescription", "stockAdd")}
        self.assertLess(y["stockType"], y["stockKarat_21"])
        self.assertLess(y["stockKarat_21"], y["stockWeight"])
        self.assertLess(y["stockWeight"], y["stockNotes"])
        self.assertLess(y["stockNotes"], y["stockDescription"])                # الوصف is the last input
        self.assertAlmostEqual(y["stockDescription"], y["stockAdd"], delta=6)    # beside إضافة
        self.item("stockNotes").forceActiveFocus()
        QTest.keyClick(self.root, Qt.Key_Tab)
        self.wait_until(lambda: self.item("stockDescription").property("activeFocus"), "Tab from notes")
        self.snapshot("f7-description-last")

    def test_sales_table_has_no_line_total_column(self):
        self.select_salesperson()
        self.scan("01260925")
        self.settle()
        self.assertIsNone(self.visual_item("saleLineTotal_0"))
        self.assertEqual(self.text("saleSize_0"), "—")
        self.assertEqual(self.text("saleNotes_0"), "فص أزرق")
        self.snapshot("sales-table")

    def test_instapay_offers_only_real_accounts_and_posts_to_them(self):
        # Two real bank accounts: the employee must choose (a single one is preselected).
        self.store.bank_accounts[10] = {"id": 10, "name": "حساب ثانٍ", "account_type": "BANK", "bank_name": "QNB",
                                        "account_label": "", "is_active": 1}
        self.vm.refreshReference()
        self.wait_until(lambda: len(self.vm._reference.get("bank_accounts", [])) == 3)
        self.select_salesperson()
        self.scan("01260925")
        self.choose("legMethod_0", 2)                                          # InstaPay
        self.wait_until(lambda: self.item("legAccount_0").property("visible"))
        self.receive("67010")
        self.settle()
        self.wait_until(lambda: "اختر الحساب" in self.text("invoiceProblems"))
        self.assertFalse(self.item("finalizeInvoice").property("enabled"))
        self.choose("legAccount_0", 0)
        self.settle()
        self.finalize()
        payment = self.store.payments[0]
        self.assertEqual((payment["payment_method_id"], payment["bank_account_id"]), (3, 7))
        self.assertEqual([(m["bank_account_id"], m["amount_delta"]) for m in self.store.bank_movements],
                         [(7, D("67010.00"))])
        self.assertEqual(self.store.cash_movements, [])

    def test_balance_strip_shows_only_cash_and_counted_old_gold(self):
        self.select_salesperson()
        balances = self.controller.operational_viewmodels["balancesVM"]
        self.wait_until(lambda: balances.ready)
        self.assertEqual([self.text(n) for n in ("balanceCash", "balanceKarat_21", "balanceKarat_18",
                                                 "balanceKarat_24")],
                         ["0.00 ج", "100.000", "0.000", "0.000"])
        self.assertEqual([(self.item(b).property("caption"), self.item(b).property("karat"))
                          for b in ("balanceBoxCash", "balanceBox_21", "balanceBox_18", "balanceBox_24")],
                         [("النقدية", ""), ("كسر", "21"), ("كسر", "18"), ("كسر", "24")])
        strip = self.item("balanceStrip")
        texts = [child.property("text") or "" for child in strip.findChildren(QObject)]
        self.assertFalse([t for t in texts if "محفظة" in t or "InstaPay" in t or "بنك" in t])
        self.add_old_gold("21", "10")
        self.settle()
        QTest.keyClick(self.root, Qt.Key_F8)
        self.settle()
        reviews = self.controller.operational_viewmodels["reviewsVM"]
        reloads = reviews._latest.get("accounts", 0)
        self.finalize()
        self.wait_until(lambda: self.text("balanceCash") == "⁦−61,140.00⁩ ج", "cash not refreshed")
        self.assertGreater(reviews._latest.get("accounts", 0), reloads)          # wallet / bank balances too
        self.assertEqual(self.text("balanceKarat_21"), "110.000")
        self.snapshot("balance-strip")

    def test_cancel_and_failure_write_nothing(self):
        self.select_salesperson()
        self.type_into("customerNameField", "عميل جديد")
        self.scan("01260925")
        self.receive("67010")
        self.settle()
        self.click("finalizeInvoice")
        dialog = self.item("confirmFinalizeDialog")
        self.wait_until(lambda: dialog.property("opened"))
        QTest.qWait(250)
        self.click("confirmBack")
        self.wait_until(lambda: not dialog.property("visible"))
        before = copy.deepcopy(self.store.tables())
        InvoiceRepo.fail_on = "insert_sale_line"
        self.addCleanup(setattr, InvoiceRepo, "fail_on", None)
        self.click("finalizeInvoice")
        self.wait_until(lambda: dialog.property("opened"))
        QTest.qWait(250)
        self.click("confirmFinalize")
        self.wait_until(lambda: self.vm.errorMessage != "")
        self.assertEqual(self.store.tables(), before)                         # no invoice, no customer, no stock change
        self.assertEqual(self.store.new_customers, [])

    # ------------------------------------------------------------ keyboard and layout

    def test_keyboard_only_invoice_at_1080(self):
        self.root.setWidth(1080)
        self.root.setHeight(680)
        self.select_salesperson()
        self.item("invoiceBarcodeField").forceActiveFocus()
        for key in "01260925":
            QTest.keyClick(self.root, key)
        QTest.keyClick(self.root, Qt.Key_Return)
        self.wait_until(lambda: self.vm.saleCount == 1)
        QTest.keyClick(self.root, Qt.Key_2, Qt.AltModifier)
        self.wait_until(lambda: self.item("oldGoldWeight").property("activeFocus"))
        QTest.keyClick(self.root, Qt.Key_5)
        QTest.keyClick(self.root, Qt.Key_Return)
        self.wait_until(lambda: self.vm.oldGoldCount == 1)
        QTest.keyClick(self.root, Qt.Key_M, Qt.ControlModifier)                # straight to إجمالي الفاتورة
        self.wait_until(lambda: self.item("invoiceTotalField").property("activeFocus"))
        for key in "36440":
            QTest.keyClick(self.root, key)
        QTest.keyClick(self.root, Qt.Key_F4)                                   # the customer name, by keyboard
        for key in "زبون":
            QTest.keyClick(self.root, key)
        self.settle()
        QTest.keyClick(self.root, Qt.Key_F9)
        dialog = self.item("confirmFinalizeDialog")
        self.wait_until(lambda: dialog.property("opened"))
        QTest.qWait(250)
        QTest.keyClick(self.root, Qt.Key_Return)
        self.wait_until(lambda: self.vm.hasSuccess, f"keyboard save failed: {self.vm.errorMessage}")
        self.wait_until(lambda: not dialog.property("visible"))
        QTest.keyClick(self.root, Qt.Key_F2)
        self.wait_until(lambda: not self.vm.hasSuccess and self.vm.saleCount == 0)
        self.assertEqual(self.warnings, [])

    def test_one_screen_layout_and_screenshots_at_all_sizes(self):
        self.select_salesperson()
        self.store.customers["12"].update(mobile="01001234567")
        self.store.warnings[12] = {"flag_id": "8"}
        self.type_into("customerNameField", "عميل")
        popup = self.item("customerSuggestions")
        self.wait_until(lambda: popup.property("opened"))
        self.click("customerSuggestion_0")
        self.scan("01260925")
        self.scan("02260925")
        self.add_old_gold("21", "5")
        self.add_old_gold("18", "2.5")
        # The widest money area: direction choice, InstaPay with its account, a second leg, an account amount.
        self.choose("legMethod_0", 2)
        self.total("41000")
        self.type_into("legAmount_0", "30000")
        self.click("addPaymentLeg")
        self.wait_until(lambda: self.visual_item("legAmount_1") is not None)
        self.receive("10000", leg=1)
        self.type_into("accountAmountField", "1000")     # direction follows the money automatically
        self.settle()
        target = os.getenv("MASGOLD_UI_REVIEW_DIR")
        for width, height in SIZES:
            self.root.setWidth(width)
            self.root.setHeight(height)
            QTest.qWait(150)
            for name in ("invoiceSalesperson", "customerNameField", "customerMobileField", "customerPlaceField",
                         "customerAccount", "invoiceBarcodeField", "openItemSearch", "openManualEntry",
                         "openStockSale", "karatChip_21", "oldGoldWeight", "addOldGold", "soldWeights",
                         "boughtWeights", "summaryGoldValue", "summaryEffectiveSale", "summaryAverageMaking",
                         "oldGoldDiscountField", "summaryOldGold", "directionIn", "directionOut",
                         "legMethod_0", "legAccount_0", "legAmount_0", "legMethod_1", "legAmount_1", "removeLeg_1",
                         "accountAmountField", "summaryDueLabel", "summaryDue", "finalizeInvoice",
                         "balanceCash", "balanceKarat_21", "balanceKarat_18", "balanceKarat_24"):
                item = self.item(name)
                self.assertTrue(item.property("visible"), f"{name} hidden at {width}x{height}")
                self.assert_inside_window(item)
            # The final amount and Save keep a full-size panel (never squeezed by the money rows).
            self.assertGreaterEqual(self.item("finalizeInvoice").property("width"), 140, f"save at {width}x{height}")
            due = self.item("summaryDue")
            self.assertLessEqual(due.property("contentWidth"), due.property("width") + 1, f"due at {width}x{height}")
            sale_panel, old_panel = self.item("salePanel"), self.item("oldGoldPanel")
            self.assertGreater(sale_panel.mapToScene(QPointF(0, 0)).x(), old_panel.mapToScene(QPointF(0, 0)).x())
            self.assertGreaterEqual(self.item("saleList").property("height"), 70, f"sale table at {width}x{height}")
            if target:
                Path(target).mkdir(parents=True, exist_ok=True)
                self.root.grabWindow().save(str(Path(target) / f"billing-{width}x{height}.png"))
        self.assertEqual(self.warnings, [])


    def test_invoice_total_allocation_by_real_clicks(self):
        """إجمالي الفاتورة is typed once; cash follows it; + إضافة starts at the remainder; an
        over-allocation is refused; the account button takes the remainder. Screenshots per step."""
        self.select_salesperson()
        self.scan("02260925")                                   # 18K 3.333 g: 17,801.55 gold value
        self.name_customer()
        self.total("20000")
        self.wait_until(lambda: self.text("legAmount_0") == "20000", "cash did not follow the total")
        self.settle()
        self.assertEqual(self.text("summaryEffectiveSale"), "20,000.00 ج")
        making = self.text("summaryAverageMaking")
        self.snapshot("total-20000-full-cash")
        self.type_into("legAmount_0", "12000")
        self.wait_until(lambda: self.text("accountAmountField") == "8000")     # على العميل, automatically
        self.click("addPaymentLeg")
        self.wait_until(lambda: self.visual_item("legAmount_1") is not None)
        self.assertEqual(self.text("legAmount_1"), "8000")
        self.choose("legMethod_1", 1)                           # محفظة
        self.settle()
        self.assertEqual(self.text("summaryAverageMaking"), making)            # the split never moves making
        self.snapshot("total-20000-cash-12000-wallet-8000")
        self.type_into("legAmount_1", "9000")                   # 12,000 + 9,000 > 20,000
        self.wait_until(lambda: "لا يمكن أن يتجاوز" in self.text("paymentWarning"))
        self.snapshot("total-20000-rejected-overpayment")
        self.click("invoiceTotalField")                         # leaving the field restores the last valid value
        self.wait_until(lambda: self.text("legAmount_1") == "8000", "last valid value not restored")
        self.type_into("legAmount_1", "5000")
        self.wait_until(lambda: self.vm.canAddPayment, "nothing remains to add")
        # Every leg row has the button; only the last row shows it.
        self.click(self.visible_item("addPaymentLeg"))
        self.wait_until(lambda: self.visual_item("legAmount_2") is not None)
        self.assertEqual(self.text("legAmount_2"), "3000")
        self.choose("legMethod_2", 3)                           # تحويل بنكي
        self.wait_until(lambda: self.item("legAccount_2").property("visible"))
        self.choose("legAccount_2", 0)
        self.settle()
        self.snapshot("total-20000-three-methods")
        self.type_into("legAmount_2", "1000")                   # 2,000 unpaid → على العميل automatically
        self.wait_until(lambda: self.text("accountAmountField") == "2000")
        self.settle()
        self.assertEqual(self.text("summaryAverageMaking"), making)
        self.assertEqual(self.text("summaryEffectiveSale"), "20,000.00 ج")
        self.snapshot("total-20000-payments-plus-account-2000")
        self.wait_until(lambda: self.item("finalizeInvoice").property("enabled"))
        self.finalize()
        self.assertEqual(sorted(p["amount"] for p in self.store.payments), [D("1000.00"), D("5000.00"), D("12000.00")])
        self.assertEqual(len(self.store.ledger), 1)
        self.assertEqual(self.warnings, [])


    def test_live_making_follows_only_the_invoice_total(self):
        """The real ViewModel over the real service: payment rows and the account never move the
        making or its average; only إجمالي الفاتورة does."""
        from tests.test_invoice_service import PRICES
        self.store.items["100"]["weight_grams"] = D("13.430")
        patcher = patch.dict(PRICES, {1: (D("6207.0000"), D("6234.0000"))})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.select_salesperson()
        self.scan("01260925")
        self.name_customer()
        vm = self.vm

        def shown():
            self.settle()
            return vm.summary["goldValue"], vm.summary["effectiveSale"], vm.summary["averageMaking"]

        vm.setInvoiceTotal("85000")
        expected = ("83,722.62 ج", "85,000.00 ج", "95.11")
        self.assertEqual(shown(), expected)                                    # A: cash 85,000
        vm.setLegAmount(0, "84000")                                            # B: 1,000 → على العميل
        self.assertEqual((vm.accountAmount, shown()), ("1000", expected))
        vm.setLegAmount(0, "80000")
        vm.addPaymentLeg()
        vm.setLegMethod(1, "MOBILE_WALLET")                                    # C: 80,000 + 5,000 (remainder)
        self.assertEqual((vm.accountAmount, shown()), ("", expected))
        vm.setLegAmount(0, "40000")
        vm.setLegAmount(1, "20000")
        vm.addPaymentLeg()
        vm.setLegMethod(2, "INSTAPAY")
        vm.setLegAccount(2, "7")
        vm.setLegAmount(2, "24000")                                            # D: 40,000 + 20,000 + 24,000 + 1,000
        self.assertEqual([leg["amount"] for leg in vm.paymentLegs], ["40000", "20000", "24000"])
        self.assertEqual((vm.accountAmount, shown()), ("1000", expected))     # the account never moves it
        vm.setInvoiceTotal("90000")                                            # only the total changes it
        self.assertEqual(shown(), ("83,722.62 ج", "90,000.00 ج", "467.41"))


if __name__ == "__main__":
    unittest.main()
