"""الرئيسية (Dashboard): read-only today's activity.

Service: each figure's one definition, permissions, the empty day. ViewModel: Arabic formatting
and empty states. QML: the real Main.qml at 1440×900 / 1280×800 / 1080×680 (screenshots when
MASGOLD_UI_REVIEW_DIR is set), permissions, shortcuts and refresh after a finalized invoice.
"""
from contextlib import contextmanager
from datetime import datetime
from decimal import Decimal
import os
from pathlib import Path
import unittest

from PySide6.QtCore import QPointF
from PySide6.QtGui import QGuiApplication
from PySide6.QtTest import QTest

from app.session import AppSession
from app.viewmodels.dashboard_viewmodel import DashboardViewModel
from services.authentication_service import AuthenticationResult, _VERIFIED
from services.dashboard_service import DashboardService
from tests import test_invoice_page_qml as page_harness

D = Decimal
BILLING = frozenset({"APP_LOGIN", "INVOICE_CREATE", "INVOICE_FINALIZE"})
VIEW_ONLY = frozenset({"APP_LOGIN"})


class FakeDashboardRepo:
    def __init__(self, connection):
        self.state = connection

    def today_invoices(self):
        self.state["reads"].append("invoices")
        return self.state["invoices"]

    def today_sold_by_karat(self):
        self.state["reads"].append("sold")
        return self.state["sold"]

    def today_old_gold_flow(self):
        self.state["reads"].append("flow")
        return self.state["flow"]

    def recent_invoices(self, limit=5):
        self.state["reads"].append(f"recent:{limit}")
        return self.state["recent"]


class FakeBalanceRepo:
    def __init__(self, connection):
        self.state = connection

    def old_gold_counted(self):
        return self.state["balance"]


def empty_state():
    return {"reads": [], "invoices": {"invoice_count": 0, "sell_total": D("0"), "buy_total": D("0"),
                                      "sale_invoice_count": 0, "buy_invoice_count": 0, "unidentified_lines": 0},
            "sold": [], "flow": [], "recent": [],
            "balance": [{"karat_id": 1, "karat_value": 21, "balance_grams": D("0")},
                        {"karat_id": 2, "karat_value": 18, "balance_grams": D("0")},
                        {"karat_id": 3, "karat_value": 24, "balance_grams": D("0")}]}


def busy_state():
    state = empty_state()
    state["invoices"] = {"invoice_count": 3, "sell_total": D("48250.50"), "buy_total": D("31035.00"),
                         "sale_invoice_count": 2, "buy_invoice_count": 2, "unidentified_lines": 1}
    state["sold"] = [{"karat_value": 18, "grams": D("4.250"), "pieces": 2, "from_old_gold_grams": D("0")},
                     {"karat_value": 21, "grams": D("7.100"), "pieces": 3, "from_old_gold_grams": D("2.000")}]
    state["flow"] = [{"karat_value": 21, "grams_in": D("5.000"), "grams_out": D("2.000")}]
    state["balance"][0]["balance_grams"] = D("128.520")
    state["recent"] = [{"invoice_number": "26092618", "finalized_at": datetime(2026, 9, 27, 13, 5),
                        "net_amount": D("23000.00"), "net_direction": "CUSTOMER_PAYS_SHOP",
                        "customer_name_snapshot": "سليا محمد", "employee_name": "علي"}]
    return state


def make_service(state, permissions=BILLING):
    session = AppSession()
    session._establish(AuthenticationResult(1, 5, "موظف", permissions, _VERIFIED), int(session.generation))

    @contextmanager
    def transaction():
        yield state
    from tests.auth_fakes import FakeAuthorization
    return DashboardService(session, transaction_factory=transaction, repository=FakeDashboardRepo,
                            balance_repository=FakeBalanceRepo, authorization=FakeAuthorization(session)), session


class DashboardServiceTests(unittest.TestCase):
    def test_empty_day(self):
        snapshot = make_service(empty_state())[0].today()
        self.assertEqual((snapshot["invoice_count"], snapshot["sell_total"], snapshot["sold"], snapshot["recent"]),
                         (0, "0.00", [], []))
        self.assertEqual([r["balance"] for r in snapshot["old_gold"]], ["0.000", "0.000", "0.000", "0.000"])

    def test_today_counts_values_and_weights_each_have_one_meaning(self):
        snapshot = make_service(busy_state())[0].today()
        self.assertEqual((snapshot["invoice_count"], snapshot["sale_invoice_count"], snapshot["buy_invoice_count"],
                          snapshot["unidentified_lines"]), (3, 2, 2, 1))
        self.assertEqual((snapshot["sell_total"], snapshot["buy_total"]), ("48250.50", "31035.00"))
        # Sold grams per karat, in the shop's 21 / 18 / 24 order, never summed across karats.
        self.assertEqual([(r["karat"], r["grams"], r["pieces"], r["from_old_gold"]) for r in snapshot["sold"]],
                         [(21, "7.100", 3, "2.000"), (18, "4.250", 2, "0.000")])
        self.assertNotIn("sold_grams_total", snapshot)

    def test_old_gold_balance_plus_todays_flow_per_karat(self):
        old = make_service(busy_state())[0].today()["old_gold"]
        self.assertEqual(old[0], {"karat": 21, "balance": "128.520", "in": "5.000", "out": "2.000"})
        self.assertEqual(old[1], {"karat": 18, "balance": "0.000", "in": "0.000", "out": "0.000"})

    def test_without_a_billing_permission_no_sales_data_is_read_or_returned(self):
        state = busy_state()
        snapshot = make_service(state, VIEW_ONLY)[0].today()
        self.assertFalse(snapshot["can_see_sales"])
        for key in ("invoice_count", "sell_total", "buy_total", "sold", "recent"):
            self.assertNotIn(key, snapshot)
        self.assertEqual(state["reads"], ["flow"])                          # sales queries never ran
        self.assertEqual(len(snapshot["old_gold"]), 4)                       # the strip's information only

    def test_one_small_set_of_queries_per_refresh(self):
        state = busy_state()
        make_service(state)[0].today()
        self.assertEqual(sorted(state["reads"]), ["flow", "invoices", "recent:5", "sold"])

    def test_no_money_received_customer_or_bank_balances_on_the_dashboard(self):
        snapshot = make_service(busy_state())[0].today()
        for key in ("money_received", "cash_balance", "bank", "wallet", "customer_balance"):
            self.assertNotIn(key, snapshot)

    def test_requires_a_login(self):
        service, session = make_service(empty_state())
        session.logout()
        with self.assertRaises(Exception):
            service.today()


class StaticService:
    def __init__(self, snapshot):
        self.snapshot, self.calls = snapshot, 0

    def today(self, *, _expected_actor=None):
        self.calls += 1
        return self.snapshot


class DashboardViewModelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        cls.app = QGuiApplication.instance() or QGuiApplication([])

    def vm(self, state, permissions=BILLING):
        service, session = make_service(state, permissions)
        vm = DashboardViewModel(session, service=StaticService(service.today()),
                                clock=lambda: datetime(2026, 9, 27, 14, 0))
        self.addCleanup(vm.shutdown)
        vm.refresh()
        for _ in range(200):
            if vm.ready:
                break
            QTest.qWait(10)
        self.assertTrue(vm.ready)
        return vm

    def test_formatting_of_a_busy_day(self):
        vm = self.vm(busy_state())
        self.assertEqual((vm.invoiceCount, vm.invoiceBreakdown), (3, "بيع 2 • شراء كسر 2"))
        self.assertEqual((vm.sellTotalText, vm.buyTotalText), ("48,250.50 ج", "31,035.00 ج"))
        self.assertEqual(vm.soldByKarat[0], {"karat": "21", "grams": "7.100", "pieces": "3 قطعة",
                                             "fromOldGold": "منها من الكسر 2.000"})
        self.assertEqual(vm.oldGold[0]["balance"], "128.520")
        self.assertEqual((vm.oldGold[0]["inText"], vm.oldGold[0]["outText"]), ("⁦+5.000⁩", "⁦−2.000⁩"))
        self.assertEqual(vm.recentInvoices[0], {"number": "26092618", "time": "13:05", "customer": "سليا محمد",
                                                "salesperson": "علي", "amount": "23,000.00 ج",
                                                "direction": "يدفع العميل"})
        self.assertEqual(vm.dateText, "الأحد ⁦27-09-2026⁩")

    def test_empty_states(self):
        vm = self.vm(empty_state())
        self.assertEqual(vm.invoiceBreakdown, "لا توجد فواتير اليوم بعد")
        self.assertEqual(vm.soldByKarat, [])
        self.assertEqual([r["todayText"] for r in vm.oldGold], ["لا حركة اليوم"] * 4)
        self.assertTrue(all(r["zero"] for r in vm.oldGold))

    def test_view_only_user_sees_no_sales(self):
        vm = self.vm(busy_state(), VIEW_ONLY)
        self.assertFalse(vm.canSeeSales)
        self.assertEqual((vm.invoiceCount, vm.recentInvoices, vm.soldByKarat), (0, [], []))


def _only_dashboard_tests(loader, tests, pattern):
    suite = unittest.TestSuite()
    for case in (DashboardServiceTests, DashboardViewModelTests, DashboardPageTests):
        names = [n for n in loader.getTestCaseNames(case)
                 if case is not DashboardPageTests or n.startswith("test_dashboard")]
        suite.addTests(case(n) for n in names)
    return suite


class DashboardPageTests(page_harness.InvoicePageTests):
    """The real Main.qml. Inherits the Billing harness (blocked database, fake login) but runs
    only the test_dashboard_* methods below (see load_tests)."""

    def open_dashboard(self, snapshot=None):
        if snapshot is not None:
            self.dashboard.snapshot = snapshot
        self.controller.operational_viewmodels["dashboardVM"].refresh()
        self.root.setProperty("currentPage", 0)
        vm = self.controller.operational_viewmodels["dashboardVM"]
        self.wait_until(lambda: vm.ready and not vm.busy, "dashboard not loaded")
        QTest.qWait(80)
        return vm

    def busy_snapshot(self):
        service, _ = make_service(busy_state())
        return service.today()

    def test_dashboard_fits_every_target_size(self):
        self.open_dashboard(self.busy_snapshot())
        target = os.getenv("MASGOLD_UI_REVIEW_DIR")
        for width, height in page_harness.SIZES:
            self.root.setWidth(width)
            self.root.setHeight(height)
            QTest.qWait(250)
            page = self.item("dashboardPage")
            for name in ("todayCard", "soldWeightCard", "priceCard", "oldGoldCard", "attentionCard",
                         "actionsCard", "invoiceCount", "sellTotal", "dashboardPriceSell"):
                item = self.item(name)
                self.assertTrue(item.property("visible"), f"{name} hidden at {width}x{height}")
                left = item.mapToItem(page, QPointF(0, 0)).x()
                self.assertGreaterEqual(left, -1, f"{name} clipped left at {width}x{height}")
                self.assertLessEqual(left + item.property("width"), page.property("width") + 1,
                                     f"{name} overflows at {width}x{height}")
            # The key figures of the first card row are on screen without scrolling.
            for name in ("invoiceCount", "dashboardPriceSell"):
                self.assert_inside_window(self.item(name))
            if target:
                Path(target).mkdir(parents=True, exist_ok=True)
                self.root.grabWindow().save(str(Path(target) / f"dashboard-{width}x{height}.png"))
        self.assertEqual(self.warnings, [])

    def test_dashboard_header_stays_readable_with_large_balances(self):
        """Worst realistic header: 7-digit cash and 4-digit grams, at every target size."""
        balances = self.controller.operational_viewmodels["balancesVM"]
        balances._strip = {"cash_balance": "1234567.89",
                           "old_gold": [{"karat": k, "grams": "1234.567"} for k in (21, 18, 22, 24)]}
        balances.changed.emit()
        self.open_dashboard(self.busy_snapshot())
        target = os.getenv("MASGOLD_UI_REVIEW_DIR")
        for width, height in page_harness.SIZES:
            self.root.setWidth(width)
            self.root.setHeight(height)
            QTest.qWait(250)
            self.assertEqual(self.item("balanceCash").property("text"), "1,234,567.89 ج")
            boxes = []
            for name in ("balanceCash", "balanceKarat_21", "balanceKarat_18", "balanceKarat_22", "balanceKarat_24",
                         "goldPriceArea", "connectionDot", "logoutButton"):
                item = self.item(name)
                self.assertTrue(item.property("visible"), f"{name} hidden at {width}x{height}")
                self.assert_inside_window(item)
                if hasattr(item, "property") and item.property("truncated"):
                    self.fail(f"{name} elided at {width}x{height}")
                origin = item.mapToScene(QPointF(0, 0))
                boxes.append((name, origin.x(), origin.x() + item.property("width")))
            strip = [b for b in boxes if b[0].startswith("balance")]
            price = next(b for b in boxes if b[0] == "goldPriceArea")
            self.assertLessEqual(max(b[2] for b in strip), price[1] + 0.5, f"strip overlaps price at {width}")
            if target:
                Path(target).mkdir(parents=True, exist_ok=True)
                self.root.grabWindow().copy(0, 0, width, 70).save(
                    str(Path(target) / f"header-{width}x{height}.png"))

    def test_dashboard_gold_prices_are_large_and_fit_every_size(self):
        """Readable from a distance: the two 21K numbers dominate the card, at every size,
        including a 1920×1080 screen minus the Windows taskbar (1920×1040 work area)."""
        prices = self.controller.gold_prices
        prices._data = {"status": "FRESH", "mode": "AUTO", "source": "GOLDTIMO", "age_seconds": 30,
                        "prices": [{"karat": 21, "shop_buy_price_per_gram": "6207.00",
                                    "shop_sell_price_per_gram": "6232.00"}]}
        prices.changed.emit()
        self.open_dashboard(self.busy_snapshot())
        target = os.getenv("MASGOLD_UI_REVIEW_DIR")
        for width, height in ((1920, 1040),) + page_harness.SIZES:
            self.root.setWidth(width)
            self.root.setHeight(height)
            QTest.qWait(250)
            sell, buy = self.item("dashboardPriceSell"), self.item("dashboardPriceBuy")
            self.assertEqual((sell.property("text"), buy.property("text")), ("6,232", "6,207"))
            for label in (sell, buy):
                self.assertGreaterEqual(label.property("font").pixelSize(), 30)       # a hero number
                self.assert_inside_window(label)
                self.assertLessEqual(label.property("contentWidth"), label.property("width") + 1)
            self.assertLess(self.item("dashboardPriceStatus").property("font").pixelSize(), 12)  # status stays small
            self.assertIn("6,232", self.item("goldTopSell").property("text"))           # label is separate
            if target:
                Path(target).mkdir(parents=True, exist_ok=True)
                self.root.grabWindow().save(str(Path(target) / f"dashboard-prices-{width}x{height}.png"))

    def test_dashboard_hidden_placeholder_pages_are_not_in_the_sidebar(self):
        for name in ("cashNavButton", "reportsNavButton"):
            self.assertFalse(self.item(name).property("visible"), name)
        self.assertTrue(self.item("reviewsNavButton").property("visible"))

    def test_dashboard_empty_day_shows_clear_arabic_states(self):
        self.open_dashboard()
        self.assertEqual(self.item("invoiceCount").property("text"), "0")
        self.assertEqual(self.item("invoiceBreakdown").property("text"), "لا توجد فواتير اليوم بعد")
        self.assertTrue(self.item("soldWeightEmpty").property("visible"))
        self.assertTrue(self.item("attentionEmpty").property("visible"))
        target = os.getenv("MASGOLD_UI_REVIEW_DIR")
        if target:
            self.root.grabWindow().save(str(Path(target) / "dashboard-empty-1280x800.png"))

    def test_dashboard_hides_sales_without_billing_permission(self):
        self.open_dashboard({**self.dashboard.snapshot, "can_see_sales": False})
        for name in ("todayCard", "soldWeightCard", "recentCard", "dashboardNewInvoice"):
            self.assertFalse(self.item(name).property("visible"), name)
        for name in ("priceCard", "oldGoldCard", "attentionCard"):
            self.assertTrue(self.item(name).property("visible"), name)

    def test_dashboard_shortcut_opens_the_existing_billing_page(self):
        self.open_dashboard()
        self.click("dashboardNewInvoice")
        self.wait_until(lambda: self.root.property("currentPage") == 1, "new invoice did not open Billing")

    def test_dashboard_refreshes_after_a_finalized_invoice(self):
        self.open_dashboard()
        calls = self.dashboard.calls
        self.controller.operational_viewmodels["invoiceVM"].finalized.emit("26092701")
        self.wait_until(lambda: self.dashboard.calls == calls + 1, "no refresh after finalize")


load_tests = _only_dashboard_tests


if __name__ == "__main__":
    unittest.main()
