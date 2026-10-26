"""الرئيسية (Dashboard): read-only today's activity.

Service: each figure's one definition, permissions, the empty day. ViewModel: Arabic formatting
and empty states. QML: the real Main.qml at 1440×900 / 1280×800 / 1080×680 (screenshots when
MASGOLD_UI_REVIEW_DIR is set), permissions and refresh after a finalized invoice.
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
CUSTOMERS = BILLING | {"CUSTOMERS_VIEW"}


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

    def month_sold_by_karat(self, previous=False):
        self.state["reads"].append("month_prev" if previous else "month")
        return self.state["month_prev" if previous else "month"]

    def month_start(self):
        self.state["reads"].append("month_start")
        return self.state["month_info"]

    def month_top_types(self, limit=3):
        self.state["reads"].append("top_types")
        return self.state["top_types"]

    def price_daily_21(self):
        self.state["reads"].append("price_daily")
        return self.state["price_daily"]

    def price_history_legacy_21(self):
        self.state["reads"].append("price_legacy")
        return self.state["price_legacy"]

    def below_min_levels(self):
        self.state["reads"].append("below_min")
        return self.state["below_min"]

    def customer_balances(self, limit=6):
        self.state["reads"].append("customer_balances")
        return self.state["customer_balances"]


class FakeBalanceRepo:
    def __init__(self, connection):
        self.state = connection

    def old_gold_counted(self):
        return self.state["balance"]


def price_series():
    """12 stored 21K days ending 2026-09-27, buy rising 6,000 → 6,275 (sell +25): the 30-day
    average of the buy price is 6,137.50 and the latest sits 2.24% above it."""
    return [{"day": datetime(2026, 9, 16 + i), "buy": D(6000 + 25 * i), "sell": D(6025 + 25 * i)} for i in range(12)]


def empty_state():
    return {"reads": [], "invoices": {"invoice_count": 0, "sell_total": D("0"), "buy_total": D("0"),
                                      "sale_invoice_count": 0, "buy_invoice_count": 0, "unidentified_lines": 0},
            "sold": [], "flow": [], "month": [], "month_prev": [], "month_info": {"first": "2026-09-01", "day": 27, "today": datetime(2026, 9, 27)}, "below_min": [],
            "top_types": [], "price_daily": [], "price_legacy": [], "customer_balances": [],
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
    state["month"] = [{"karat_value": 21, "grams": D("120.500"), "pieces": 30}, {"karat_value": 18, "grams": D("40.000"), "pieces": 9}]
    state["month_prev"] = [{"karat_value": 21, "grams": D("100.000"), "pieces": 25}]
    state["top_types"] = [{"item_type": "دبلة", "pieces": 5, "grams": D("45.000"), "value": D("280000.00")},
                          {"item_type": "خاتم", "pieces": 3, "grams": D("12.250"), "value": D("78000.00")}]
    state["price_daily"] = price_series()
    state["customer_balances"] = [{"customer_id": 7, "customer_code": "CU0007", "name": "سليا محمد", "balance": D("25000.00")},
                                  {"customer_id": 9, "customer_code": None, "name": "منى علي", "balance": D("-3200.00")},
                                  {"customer_id": 11, "customer_code": "CU0011", "name": "أحمد حسن", "balance": D("0.00")}]   # settled: never reaches the card
    state["balance"][0]["balance_grams"] = D("128.520")
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
        self.assertEqual((snapshot["invoice_count"], snapshot["sell_total"], snapshot["sold"]),
                         (0, "0.00", []))
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
        for key in ("invoice_count", "sell_total", "buy_total", "sold", "top_types"):
            self.assertNotIn(key, snapshot)
        self.assertEqual(state["reads"], ["flow", "price_daily", "price_legacy"])   # sales, stock and customer queries never ran
        self.assertEqual(len(snapshot["old_gold"]), 4)                              # the strip's information only

    def test_one_small_set_of_queries_per_refresh(self):
        state = busy_state()
        make_service(state)[0].today()
        self.assertEqual(sorted(state["reads"]), ["flow", "invoices", "month", "month_prev", "month_start",
                                                  "price_daily", "price_legacy", "sold", "top_types"])

    def test_no_money_received_cash_or_bank_balances_on_the_dashboard(self):
        snapshot = make_service(busy_state())[0].today()
        for key in ("money_received", "cash_balance", "bank", "wallet"):
            self.assertNotIn(key, snapshot)

    def test_the_month_so_far_is_per_karat_against_the_same_span_of_the_same_month_last_year(self):
        snapshot = make_service(busy_state())[0].today()
        self.assertEqual(snapshot["month_day"], 27)
        self.assertEqual(snapshot["month"], [{"karat": 21, "grams": "120.500", "pieces": 30, "previous": "100.000"}, {"karat": 18, "grams": "40.000", "pieces": 9, "previous": "0.000"}])
        self.assertNotIn("month_total", snapshot)                                                       # never summed across karats

    def test_the_minimum_stock_shortages_need_an_inventory_grant_and_list_what_is_missing(self):
        state = busy_state()
        state["below_min"] = [{"item_type": "دبلة", "karat_value": 21, "min_pieces": 5, "min_grams": D("20.000"), "pieces": 4, "grams": D("21.500")},       # 1 piece short
                              {"item_type": "خاتم", "karat_value": 21, "min_pieces": 2, "min_grams": D("10.000"), "pieces": 2, "grams": D("8.000")},        # 2 g short
                              {"item_type": "سلسلة", "karat_value": 18, "min_pieces": 1, "min_grams": None, "pieces": 1, "grams": D("8.000")}]               # fine
        granted = make_service(state, BILLING | {"INVENTORY_VIEW"})[0].today()
        self.assertEqual(granted["below_min"], [{"type": "دبلة", "karat": 21, "short_pieces": 1, "short_grams": "0.000"}, {"type": "خاتم", "karat": 21, "short_pieces": 0, "short_grams": "2.000"}])
        self.assertIn("below_min", state["reads"])
        plain = make_service(busy_state(), BILLING)[0].today()
        self.assertFalse(plain["can_see_stock"])
        self.assertNotIn("below_min", plain)
        self.assertEqual(make_service({**busy_state(), "below_min": None}, BILLING | {"INVENTORY_VIEW"})[0].today()["below_min"], [])      # a database below 072

    def test_requires_a_login(self):
        service, session = make_service(empty_state())
        session.logout()
        with self.assertRaises(Exception):
            service.today()

    def test_top_types_list_one_row_per_type_and_are_never_summed(self):
        snapshot = make_service(busy_state())[0].today()
        self.assertEqual(snapshot["top_types"], [{"type": "دبلة", "pieces": 5, "grams": "45.000", "value": "280000.00"},
                                                 {"type": "خاتم", "pieces": 3, "grams": "12.250", "value": "78000.00"}])
        self.assertNotIn("top_types_total", snapshot)
        self.assertEqual(make_service(empty_state())[0].today()["top_types"], [])

    def test_price_hint_is_the_21k_buy_price_against_its_30_day_average(self):
        hint = make_service(busy_state())[0].today()["price_hint"]
        self.assertEqual(hint, {"available": True, "average30": "6137.50", "vs_average30_percent": "2.24",
                                "hint_text": "البيع مناسب نسبيًا", "hint_tone": "sell"})          # the sell price sits at the top of its 90-day range
        short = make_service({**empty_state(), "price_daily": price_series()[:5]})[0].today()["price_hint"]
        self.assertEqual((short["available"], short["average30"], short["vs_average30_percent"]), (True, None, None))   # below the 10 observations the average needs
        self.assertEqual(make_service(empty_state())[0].today()["price_hint"],
                         {"available": False, "average30": None, "vs_average30_percent": None,
                          "hint_text": "بيانات غير كافية", "hint_tone": "muted"})                    # no stored price at all

    def test_customer_balances_need_a_customers_grant_and_show_the_true_ledger_balance(self):
        state = busy_state()
        granted = make_service(state, CUSTOMERS)[0].today()
        self.assertTrue(granted["can_see_balances"])
        self.assertIn("customer_balances", state["reads"])
        self.assertEqual(granted["customer_balances"],
                         [{"customer": "سليا محمد", "code": "CU0007", "balance": "25000.00", "direction": "OWED"},
                          {"customer": "منى علي", "code": "", "balance": "3200.00", "direction": "CREDIT"}])   # the settled customer disappears
        plain = make_service(busy_state(), BILLING)[0].today()
        self.assertFalse(plain["can_see_balances"])
        self.assertNotIn("customer_balances", plain)


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
        self.assertEqual((vm.invoiceCount, vm.soldByKarat, vm.topTypes), (0, [], []))
        self.assertFalse(vm.canSeeBalances)
        self.assertEqual(vm.customerBalances, [])

    def test_formatting_of_the_new_cards(self):
        vm = self.vm(busy_state(), CUSTOMERS)
        self.assertEqual(vm.topTypes[0], {"type": "دبلة", "pieces": "5 قطعة", "grams": "45.000", "value": "280,000.00 ج"})
        self.assertEqual(vm.priceHint, {"available": True, "average": "6,137.50 ج",
                                        "versus": "سعر الشراء الآن أعلى منه بنسبة 2.24%",
                                        "action": "البيع مناسب نسبيًا", "tone": "sell"})
        self.assertTrue(vm.canSeeBalances)
        self.assertEqual(vm.customerBalances, [{"name": "سليا محمد", "code": "CU0007", "balance": "25,000.00 ج",
                                                "owes": True},
                                               {"name": "منى علي", "code": "", "balance": "3,200.00 ج",
                                                "owes": False}])          # the settled customer disappears

    def test_price_hint_without_history_says_the_data_is_insufficient(self):
        self.assertEqual(self.vm(empty_state()).priceHint,
                         {"available": False, "average": "—",
                          "versus": "بيانات غير كافية (تحتاج 10 أيام أسعار على الأقل)",
                          "action": "بيانات غير كافية", "tone": "muted"})


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
        service, _ = make_service(busy_state(), CUSTOMERS)
        return service.today()

    def test_dashboard_fits_every_target_size(self):
        self.open_dashboard(self.busy_snapshot())
        target = os.getenv("MASGOLD_UI_REVIEW_DIR")
        for width, height in page_harness.SIZES:
            self.root.setWidth(width)
            self.root.setHeight(height)
            QTest.qWait(250)
            page = self.item("dashboardPage")
            for name in ("todayCard", "soldWeightCard", "priceCard", "oldGoldCard",
                         "priceHintCard", "topTypesCard", "invoiceCount", "sellTotal", "dashboardPriceSell"):
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

    def test_dashboard_cards_of_a_row_have_the_same_height_and_the_new_cards_show_their_figures(self):
        """Owner review (2026-10-22): the cards of one row are level with each other, the page text is bigger, and the month / minimum-stock cards exist and the gold price card is the first (rightmost) one."""
        self.grant("INVENTORY_VIEW")
        state = busy_state()
        state["below_min"] = [{"item_type": "دبلة", "karat_value": 21, "min_pieces": 5, "min_grams": None, "pieces": 4, "grams": D("21.500")}]
        service, _ = make_service(state, BILLING | {"INVENTORY_VIEW"})
        vm = self.open_dashboard(service.today())
        self.assertEqual([r["karat"] for r in vm.monthByKarat], ["21", "18"])
        self.assertIn("▲ 20%", vm.monthByKarat[0]["versus"])                                         # 120.5 against 100 g of the same span last year
        self.assertEqual(vm.belowMin, [{"title": "دبلة عيار 21", "short": "ناقص 1 قطعة"}])
        for width, height in page_harness.SIZES:
            self.root.setWidth(width)
            self.root.setHeight(height)
            QTest.qWait(250)
            page = self.item("dashboardPage")
            for name in ("monthCard", "belowMinCard"):
                item = self.item(name)
                self.assertTrue(item.property("visible"), f"{name} hidden at {width}x{height}")
                left = item.mapToItem(page, QPointF(0, 0)).x()
                self.assertGreaterEqual(left, -1, name)
                self.assertLessEqual(left + item.property("width"), page.property("width") + 1, name)
            wide = bool(page.property("wide"))                                                               # three columns on a wide page, two on a narrow one
            rows = ([("priceCard", "priceHintCard", "todayCard"), ("soldWeightCard", "oldGoldCard", "monthCard"),
                     ("topTypesCard", "belowMinCard")] if wide else
                    [("priceCard", "priceHintCard"), ("todayCard", "soldWeightCard"), ("oldGoldCard", "monthCard"),
                     ("topTypesCard", "belowMinCard")])
            for row in rows:
                heights = {name: round(self.item(name).property("height")) for name in row}
                self.assertEqual(len(set(heights.values())), 1, f"cards of one row differ in height at {width}x{height}: {heights}")
            if wide:
                # RTL grid: the first card is the rightmost (biggest x); each next card is further left.
                row1 = {name: self.item(name).mapToItem(page, QPointF(0, 0)).x() for name in ("priceCard", "priceHintCard", "todayCard")}
                self.assertGreater(row1["priceCard"], row1["priceHintCard"], f"the gold price card is not the rightmost at {width}x{height}")        # the price on the physical right (owner review)
                self.assertGreater(row1["priceHintCard"], row1["todayCard"])
                row2 = {name: self.item(name).mapToItem(page, QPointF(0, 0)).x() for name in ("soldWeightCard", "oldGoldCard", "monthCard")}
                self.assertGreater(row2["soldWeightCard"], row2["oldGoldCard"])
                self.assertGreater(row2["oldGoldCard"], row2["monthCard"])
            self.assertGreaterEqual(self.item("invoiceCount").property("font").pixelSize(), 26)             # the key figure is bigger than before
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
            page = self.item("dashboardPage")                                                              # the same order as the Main.qml price strip: buy on the physical right
            self.assertGreater(buy.mapToItem(page, QPointF(0, 0)).x(), sell.mapToItem(page, QPointF(0, 0)).x())
            for label in (sell, buy):
                self.assertGreaterEqual(label.property("font").pixelSize(), 30)       # a hero number
                self.assert_inside_window(label)
                self.assertLessEqual(label.property("contentWidth"), label.property("width") + 1)
            self.assertLess(self.item("dashboardPriceStatus").property("font").pixelSize(), 14)  # status stays small (the page text is two points bigger since the 2026-10-22 review)
            self.assertIn("6,232", self.item("goldTopSell").property("text"))           # label is separate
            if target:
                Path(target).mkdir(parents=True, exist_ok=True)
                self.root.grabWindow().save(str(Path(target) / f"dashboard-prices-{width}x{height}.png"))

    def test_dashboard_hidden_placeholder_pages_are_not_in_the_sidebar(self):
        for name in ("cashNavButton",):
            self.assertFalse(self.item(name).property("visible"), name)
        self.assertTrue(self.item("reviewsNavButton").property("visible"))

    def test_dashboard_empty_day_shows_clear_arabic_states(self):
        self.open_dashboard()
        self.assertEqual(self.item("invoiceCount").property("text"), "0")
        self.assertEqual(self.item("invoiceBreakdown").property("text"), "لا توجد فواتير اليوم بعد")
        self.assertTrue(self.item("soldWeightEmpty").property("visible"))
        target = os.getenv("MASGOLD_UI_REVIEW_DIR")
        if target:
            self.root.grabWindow().save(str(Path(target) / "dashboard-empty-1280x800.png"))

    def test_dashboard_hides_sales_without_billing_permission(self):
        self.open_dashboard({**self.dashboard.snapshot, "can_see_sales": False, "can_see_balances": True,
                             "customer_balances": []})
        for name in ("todayCard", "soldWeightCard", "monthCard", "topTypesCard"):
            self.assertFalse(self.item(name).property("visible"), name)
        for name in ("priceCard", "oldGoldCard", "priceHintCard", "customerBalancesCard"):
            self.assertTrue(self.item(name).property("visible"), name)                                   # the price and balance cards are for every user
        self.assertTrue(self.item("customerBalancesEmpty").property("visible"))

    def test_dashboard_new_cards_show_their_figures(self):
        """Owner request 2026-10-25: the 21K price hint, the best-selling types and the customer balances (red = على العميل, blue = للعميل, no wording)."""
        vm = self.open_dashboard(self.busy_snapshot())
        self.assertEqual((self.item("priceHintAverage").property("value"), self.item("priceHintVersus").property("value")),
                         ("6,137.50 ج", "سعر الشراء الآن أعلى منه بنسبة 2.24%"))
        self.assertEqual(self.item("priceHintAction").property("value"), "البيع مناسب نسبيًا")
        self.assertIsNotNone(self.item("topType_0"))                                                     # the first best-selling type row exists
        self.assertEqual(vm.topTypes[0]["type"], "دبلة")
        # the customer balances card: the amount only, distinguished by colour (RED owes / BLUE credit)
        self.assertTrue(self.item("customerBalancesCard").property("visible"))
        self.assertEqual(self.item("customerBalancesEmpty").property("visible"), False)
        row = self.item("customerBalance_0")
        line = next(c for c in row.findChildren(object) if c.property("value") is not None and c.property("value") != "")
        self.assertEqual(line.property("value"), "25,000.00 ج")                                         # no «على العميل» prefix, no direction wording
        self.assertEqual(line.property("valueColor").name(), self._theme_color("red"))
        credit = self.item("customerBalance_1")
        line2 = next(c for c in credit.findChildren(object) if c.property("value") is not None and c.property("value") != "")
        self.assertEqual(line2.property("valueColor").name(), self._theme_color("blue"))

    def _theme_color(self, name):
        """The Theme colour (Theme has no objectName): the only child of the page exposing that colour property."""
        theme = next(c for c in self.item("dashboardPage").findChildren(object) if c.property(name) is not None)
        value = theme.property(name)
        return value.name() if hasattr(value, "name") else str(value)

    def test_dashboard_refreshes_after_a_finalized_invoice(self):
        self.open_dashboard()
        calls = self.dashboard.calls
        self.controller.operational_viewmodels["invoiceVM"].finalized.emit("26092701")
        self.wait_until(lambda: self.dashboard.calls == calls + 1, "no refresh after finalize")


load_tests = _only_dashboard_tests


if __name__ == "__main__":
    unittest.main()
