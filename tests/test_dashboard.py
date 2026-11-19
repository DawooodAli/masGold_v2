"""الرئيسية (Dashboard): read-only today's activity.

Service: each figure's one definition, permissions, the empty day. ViewModel: Arabic formatting
and empty states. QML: the real Main.qml at 1440×900 / 1280×800 / 1080×680 (screenshots when
MASGOLD_UI_REVIEW_DIR is set), permissions and refresh after a finalized invoice.
"""
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from decimal import Decimal
import os
from pathlib import Path
import unittest

from PySide6.QtCore import QPoint, QPointF
from PySide6.QtGui import QGuiApplication
from PySide6.QtTest import QTest

from app.formatting import ltr
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

    def sales_by_day(self, days=14):
        return []

    def scrap_sold_by_day(self, days=14):
        self.state["reads"].append("scrap_sold")
        return self.state.get("scrap_sold", [])

    def customer_balance_totals(self):
        self.state["reads"].append("balance_totals")
        return self.state.get("balance_totals")

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


    def today_scrap_purchases(self):
        self.state["reads"].append("scrap")
        return self.state["scrap"]

    def today_making_groups(self):
        self.state["reads"].append("making")
        return self.state.get("making_groups", [])

    def today_scrap_discount(self):
        self.state["reads"].append("scrap_discount")
        return self.state.get("scrap_discount", {"gross": 0, "discount": 0})

    def latest_shop_buy_prices(self):
        self.state["reads"].append("scrap_prices")
        return self.state["scrap_prices"]

    def db_today(self):
        return self.state["today"]

    def dead_stock_by_karat(self, days):
        self.state["reads"].append(("dead", days))
        return self.state["dead_stock"]


class FakeReportRepo:
    def __init__(self, connection):
        self.state = connection

    def customer_open_ledger(self):
        self.state["reads"].append("debt_ledger")
        return self.state["debt_ledger"]


class FakeBalanceRepo:
    def __init__(self, connection):
        self.state = connection

    def old_gold_counted(self):
        return self.state["balance"]


def price_series():
    """12 stored 21K days ending 2026-09-27, buy rising 6,000 → 6,275 (sell +25): the 30-day
    average of the buy price is 6,137.50 and the latest sits 2.24% above it."""
    return [{"day": datetime(2026, 9, 16 + i), "buy": D(6000 + 25 * i), "sell": D(6025 + 25 * i)} for i in range(12)]


def long_price_state(rows):
    """`rows` stored 21K days ending 2026-09-27, buy 5,000 + 10 per day (index 0 = the oldest day), sell +25: the 90-day average of the buy price ending at index j >= 89 is 5,000 + 10 * (j - 44.5)."""
    state = empty_state()
    state["price_daily"] = [{"day": datetime(2026, 9, 27) - timedelta(days=rows - 1 - i), "buy": D(5000 + 10 * i), "sell": D(5025 + 10 * i)} for i in range(rows)]
    return state


def empty_state():
    return {"reads": [], "invoices": {"invoice_count": 0, "sell_total": D("0"), "buy_total": D("0"),
                                      "sale_invoice_count": 0, "buy_invoice_count": 0, "unidentified_lines": 0},
            "sold": [], "flow": [], "month": [], "month_prev": [], "month_info": {"first": "2026-09-01", "day": 27, "today": datetime(2026, 9, 27)}, "below_min": [],
            "top_types": [], "price_daily": [], "price_legacy": [], "customer_balances": [],
            "scrap": [], "scrap_prices": [], "today": date(2026, 9, 27), "dead_stock": [], "debt_ledger": [],
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
    state["scrap"] = [{"karat_value": 21, "grams": D("10.000"), "paid": D("61000.00")}, {"karat_value": 18, "grams": D("4.000"), "paid": D("20000.00")}]
    state["scrap_prices"] = [{"karat_value": 21, "buy": D("6207.0000")}]
    state["dead_stock"] = [{"karat_value": 21, "pieces": 4, "grams": D("20.500"), "oldest_days": 400}, {"karat_value": 18, "pieces": 1, "grams": D("3.000"), "oldest_days": 200}]
    state["debt_ledger"] = [
        {"customer_id": 7, "name": "سليا محمد", "customer_code": "CU0007", "is_active": 1, "occurred_at": datetime(2026, 7, 1), "amount_delta": D("1000.00")},
        {"customer_id": 7, "name": "سليا محمد", "customer_code": "CU0007", "is_active": 1, "occurred_at": datetime(2026, 9, 20), "amount_delta": D("300.00")},
        {"customer_id": 9, "name": "منى علي", "customer_code": None, "is_active": 1, "occurred_at": datetime(2026, 9, 25), "amount_delta": D("700.00")},        # recent: not overdue
        {"customer_id": 11, "name": "عميل موقوف", "customer_code": None, "is_active": 0, "occurred_at": datetime(2026, 1, 1), "amount_delta": D("50.00")}]
    state["balance"][0]["balance_grams"] = D("128.520")
    # today (2026-09-27): 21K 10 g + 18K 4 g bought, 21K 7.1 g + 18K 4.25 g sold; two days ago 2.1 g of 24K sold
    state["scrap_sold"] = [{"day": date(2026, 9, 25), "karat_value": 24, "bought": D("0.000"), "sold": D("2.100")},
                           {"day": date(2026, 9, 27), "karat_value": 18, "bought": D("4.000"), "sold": D("4.250")},
                           {"day": date(2026, 9, 27), "karat_value": 21, "bought": D("10.000"), "sold": D("7.100")}]
    state["balance_totals"] = {"owed": D("25000.00"), "owed_count": 1, "credit": D("3200.00"), "credit_count": 1}
    return state


CURRENCY_RATES = {"enabled": True, "available": True, "updated": "12:00", "error": "",
                  "rows": [{"code": "USD", "name": "دولار أمريكي", "price": "48.52"}, {"code": "SAR", "name": "ريال سعودي", "price": "12.94"}]}


def make_service(state, permissions=BILLING):
    session = AppSession()
    session._establish(AuthenticationResult(1, 5, "موظف", permissions, _VERIFIED), int(session.generation))

    @contextmanager
    def transaction():
        yield state
    from tests.auth_fakes import FakeAuthorization
    return DashboardService(session, transaction_factory=transaction, repository=FakeDashboardRepo,
                            balance_repository=FakeBalanceRepo, report_repository=FakeReportRepo, authorization=FakeAuthorization(session),
                            currency_rates=lambda: CURRENCY_RATES), session


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
        self.assertEqual(sorted(state["reads"]), ["flow", "invoices", "making", "month", "month_prev", "month_start",
                                                  "price_daily", "price_legacy", "scrap", "scrap_discount", "scrap_prices", "scrap_sold", "sold", "top_types"])

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


class DashboardNewCardsServiceTests(unittest.TestCase):
    def test_overdue_debts_are_the_old_open_owed_items_of_active_customers(self):
        snapshot = make_service(busy_state(), CUSTOMERS)[0].today()
        overdue = snapshot["overdue"]
        self.assertEqual((overdue["days"], overdue["total"], overdue["customers"]), (30, "1000.00", 1))     # 300 (7 days) and 700 (2 days) are recent; the inactive customer is left out
        self.assertEqual(overdue["rows"], [{"customer": "سليا محمد", "code": "CU0007", "amount": "1000.00", "age": 88}])
        state = busy_state()
        self.assertNotIn("overdue", make_service(state, BILLING)[0].today())
        self.assertNotIn("debt_ledger", state["reads"])

    def test_a_payment_consumes_the_oldest_debt_first(self):
        state = busy_state()
        state["debt_ledger"].insert(1, {"customer_id": 7, "name": "سليا محمد", "customer_code": "CU0007", "is_active": 1,
                                        "occurred_at": datetime(2026, 7, 5), "amount_delta": D("-1000.00")})
        overdue = make_service(state, CUSTOMERS)[0].today()["overdue"]
        self.assertEqual((overdue["total"], overdue["customers"], overdue["rows"]), ("0.00", 0, []))

    def test_the_average_scrap_purchase_discount_is_a_weighted_percent_and_never_zero_without_a_purchase(self):
        state = busy_state()
        state["scrap_discount"] = {"gross": D("10000.00"), "discount": D("155.00")}                 # 1.55 % of the gross value
        self.assertEqual(make_service(state, BILLING)[0].today()["scrap_discount_pct"], "1.55")
        state["scrap_discount"] = {"gross": D("0"), "discount": D("0")}                               # nothing bought today
        self.assertIsNone(make_service(state, BILLING)[0].today()["scrap_discount_pct"])
        self.assertEqual(DashboardService.scrap_discount({"gross": D("3"), "discount": D("0.125")}), "4.17")   # ROUND_HALF_UP to two decimals

    def test_making_averages_are_weighted_per_group_and_never_invent_zero(self):
        state = busy_state()
        state["making_groups"] = [{"grp": "PIECES", "grams": D("10"), "known_grams": D("8"), "making": D("400")},
                                  {"grp": "SCRAP", "grams": D("5"), "known_grams": D("0"), "making": D("0")}]
        got = make_service(state, BILLING)[0].today()["making_averages"]
        self.assertEqual(got["PIECES"]["average"], "50.00")
        self.assertEqual((got["BULLION"]["average"], got["BULLION"]["unknown"]), (None, False))      # no sale today
        self.assertEqual((got["SCRAP"]["average"], got["SCRAP"]["unknown"]), (None, True))          # making unknown

    def test_scrap_today_is_per_karat_with_the_price_difference(self):
        rows = make_service(busy_state(), BILLING)[0].today()["scrap_today"]
        self.assertEqual(rows[0], {"karat": 21, "grams": "10.000", "average": "6100.00", "price": "6207.00", "diff": "-107.00", "discount": "1.72"})
        self.assertEqual(rows[1], {"karat": 18, "grams": "4.000", "average": "5000.00", "price": None, "diff": None, "discount": None})       # no stored price: never invented
        none = make_service(empty_state(), BILLING)[0].today()
        self.assertEqual(none["scrap_today"], [])
        state = busy_state()
        self.assertNotIn("scrap_today", make_service(state, VIEW_ONLY)[0].today())
        self.assertNotIn("scrap", state["reads"])

    def test_dead_stock_follows_the_inventory_grant_and_is_per_karat(self):
        state = busy_state()
        snapshot = make_service(state, BILLING | {"INVENTORY_VIEW"})[0].today()
        self.assertEqual(snapshot["dead_stock"], {"days": 180, "rows": [{"karat": 21, "pieces": 4, "grams": "20.500", "oldest": 400},
                                                                          {"karat": 18, "pieces": 1, "grams": "3.000", "oldest": 200}]})
        self.assertIn(("dead", 180), state["reads"])
        state = busy_state()
        self.assertNotIn("dead_stock", make_service(state, BILLING)[0].today())
        self.assertNotIn(("dead", 180), state["reads"])


class DashboardChartsServiceTests(unittest.TestCase):
    """The scrap-bought-against-gold-sold chart and the customer balance bar (owner 2026-11-18)."""

    def test_scrap_against_sold_is_one_entry_per_day_in_21k_equivalent_grams(self):
        days = make_service(busy_state())[0].today()["scrap_sold_days"]
        self.assertEqual(len(days), 14)
        self.assertEqual((days[0]["day"], days[-1]["day"]), ("2026-09-14", "2026-09-27"))                  # oldest first, today last
        # today: 10 + 4 x 18/21 = 13.428571... g bought, 7.1 + 4.25 x 18/21 = 10.742857... g sold: converted to 21K FIRST, rounded once to 0.001 g
        self.assertEqual(days[-1], {"day": "2026-09-27", "bought": "13.429", "sold": "10.743"})
        self.assertEqual(days[-3], {"day": "2026-09-25", "bought": "0.000", "sold": "2.400"})              # 2.1 g of 24K = 2.4 g of 21K
        self.assertEqual(days[-2], {"day": "2026-09-26", "bought": "0.000", "sold": "0.000"})              # a day without a line is a zero, never absent

    def test_scrap_against_sold_never_adds_raw_grams_across_karats(self):
        rows = [{"day": date(2026, 9, 27), "karat_value": 18, "bought": D("14.000"), "sold": D("0")},
                {"day": date(2026, 9, 27), "karat_value": 24, "bought": D("7.000"), "sold": D("0")}]
        today = DashboardService.scrap_sold_days(rows, date(2026, 9, 27))[-1]
        self.assertEqual(today["bought"], "20.000")                                                       # 14 x 18/21 + 7 x 24/21 = 12 + 8, not 21 raw grams
        # one rounding per figure: three lines of 0.0004 g of 21K each stay one 0.0012 g exact sum (0.001), not three roundings to 0
        tiny = [{"day": date(2026, 9, 27), "karat_value": 21, "bought": D("0.0004"), "sold": D("0")}] * 3
        self.assertEqual(DashboardService.scrap_sold_days(tiny, date(2026, 9, 27))[-1]["bought"], "0.001")

    def test_scrap_against_sold_needs_the_billing_permission_and_an_empty_window_is_all_zero(self):
        state = busy_state()
        plain = make_service(state, VIEW_ONLY)[0].today()
        self.assertNotIn("scrap_sold_days", plain)
        self.assertNotIn("scrap_sold", state["reads"])
        empty = make_service(empty_state())[0].today()["scrap_sold_days"]
        self.assertEqual((len(empty), {r["bought"] for r in empty}, {r["sold"] for r in empty}), (14, {"0.000"}, {"0.000"}))

    def test_balance_totals_need_a_customers_grant(self):
        state = busy_state()
        granted = make_service(state, CUSTOMERS)[0].today()
        self.assertEqual(granted["balance_totals"], {"owed": "25000.00", "credit": "3200.00", "owed_count": 1, "credit_count": 1})
        self.assertIn("balance_totals", state["reads"])
        state = busy_state()
        plain = make_service(state, BILLING)[0].today()
        self.assertNotIn("balance_totals", plain)
        self.assertNotIn("balance_totals", state["reads"])
        none = make_service({**empty_state(), "balance_totals": None}, CUSTOMERS)[0].today()["balance_totals"]
        self.assertEqual(none, {"owed": "0.00", "credit": "0.00", "owed_count": 0, "credit_count": 0})


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
        self.assertEqual((vm.invoiceCount, vm.invoiceBreakdown), (3, "بيع 1 • شراء كسر 1 • مختلطة 1"))      # 2 sale + 2 scrap invoices overlap in one mixed invoice: 1 + 1 + 1 = 3
        self.assertEqual((vm.sellTotalText, vm.buyTotalText), ("48,250 ج", "31,035 ج"))
        self.assertEqual(vm.soldByKarat[0], {"karat": "21", "grams": "7.10", "pieces": "3 قطعة",
                                             "fromOldGold": "منها من الكسر 2.00"})
        self.assertEqual(vm.oldGold[0]["balance"], "128.52")
        self.assertEqual((vm.oldGold[0]["inText"], vm.oldGold[0]["outText"]), ("⁦+5.00⁩", "⁦−2.00⁩"))
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
        self.assertEqual(vm.topTypes[0], {"type": "دبلة", "pieces": "5 قطعة", "grams": "45.00", "value": "280,000 ج"})
        self.assertEqual(vm.priceHint, {"available": True, "average": "6,137.50 ج",
                                        "versus": "سعر الشراء الآن أعلى منه بنسبة 2.24%",
                                        "action": "البيع مناسب نسبيًا", "tone": "sell"})
        self.assertTrue(vm.canSeeBalances)
        self.assertEqual(vm.customerBalances, [{"name": "سليا محمد", "code": "CU0007", "balance": "25,000 ج",
                                                "owes": True},
                                               {"name": "منى علي", "code": "", "balance": "3,200 ج",
                                                "owes": False}])          # the settled customer disappears

    def test_the_third_average_is_the_scrap_purchase_discount_in_percent(self):
        state = busy_state()
        state["scrap_discount"] = {"gross": D("10000.00"), "discount": D("150.00")}
        rows = {r["key"]: r for r in self.vm(state).makingAverages}
        self.assertEqual(rows["SCRAP"]["label"], "متوسط شراء الكسر في المية")
        self.assertIn("1.50", rows["SCRAP"]["text"])
        self.assertIn("%", rows["SCRAP"]["text"])
        state["scrap_discount"] = {"gross": D("0"), "discount": D("0")}
        self.assertEqual({r["key"]: r for r in self.vm(state).makingAverages}["SCRAP"]["text"], "—")

    def test_scrap_against_sold_chart_rows_and_texts(self):
        chart = self.vm(busy_state()).scrapSoldChart
        self.assertTrue(chart["available"])
        self.assertEqual(len(chart["days"]), 14)
        today = chart["days"][-1]
        self.assertEqual((today["label"], today["boughtText"], today["soldText"]), ("27/09", "13.43 جرام", "10.74 جرام"))
        self.assertEqual((today["boughtRatio"], today["boughtBest"], today["soldBest"]), (1.0, True, False))   # the tallest column of the window carries the label
        self.assertAlmostEqual(today["soldRatio"], 10.743 / 13.429, places=3)
        self.assertEqual(chart["days"][0]["boughtText"], "—")
        self.assertEqual((chart["boughtTotal"], chart["soldTotal"]), ("13.43 جرام", "13.14 جرام"))
        self.assertIn("21", chart["caption"])
        empty = self.vm(empty_state()).scrapSoldChart
        self.assertEqual((empty["available"], len(empty["days"]), empty["days"][-1]["boughtRatio"]), (False, 14, 0.0))
        self.assertEqual(self.vm(busy_state(), VIEW_ONLY).scrapSoldChart["days"], [])

    def test_price_chart_carries_the_90_day_average_floats_texts_and_a_range_that_includes_it(self):
        chart = self.vm(long_price_state(100)).priceChart                                # 30 shown days, the first 19 without a 90-day average
        self.assertEqual((len(chart["ma90"]), len(chart["ma90Texts"])), (30, 30))
        self.assertEqual(chart["ma90"][:19], [None] * 19)
        self.assertEqual(chart["ma90Texts"][:19], [""] * 19)
        self.assertEqual((chart["ma90"][19], chart["ma90Texts"][19]), (5445.0, "5,445 ج"))
        self.assertEqual(chart["ma90Texts"][-1], "5,545 ج")
        self.assertLessEqual(chart["low"], min(v for v in chart["ma90"] if v is not None))
        self.assertGreaterEqual(chart["high"], max(chart["buy"] + chart["sell"]))
        # the y range includes a 90-day value that lies outside the buy / sell values: in a falling market the long average sits above every drawn price
        state = long_price_state(100)
        state["price_daily"] = [{**r, "buy": D(20000 - 100 * i), "sell": D(20025 - 100 * i)} for i, r in enumerate(state["price_daily"])]
        falling = self.vm(state).priceChart
        long_values = [v for v in falling["ma90"] if v is not None]
        self.assertEqual(falling["high"], max(long_values))
        self.assertGreater(falling["high"], max(falling["buy"] + falling["sell"]))
        empty = self.vm(empty_state()).priceChart
        self.assertEqual((empty["available"], empty["ma90"], empty["ma90Texts"]), (False, [], []))

    def test_balance_split_ready_text_and_shares(self):
        split = self.vm(busy_state(), CUSTOMERS).balanceSplit
        self.assertEqual((split["available"], split["owedText"], split["creditText"]), (True, "25,000 ج", "3,200 ج"))
        self.assertEqual((split["owedCount"], split["creditCount"], split["owedPercent"], split["creditPercent"]), ("1 عميل", "1 عميل", ltr("89%"), ltr("11%")))
        self.assertAlmostEqual(split["owedRatio"] + split["creditRatio"], 1.0, places=9)
        self.assertAlmostEqual(split["owedRatio"], 25000 / 28200, places=6)
        for state, permissions in ((empty_state(), CUSTOMERS), (busy_state(), BILLING)):
            nothing = self.vm(state, permissions).balanceSplit
            self.assertEqual((nothing["available"], nothing["owedRatio"], nothing["creditRatio"]), (False, 0.0, 0.0))

    def test_the_price_series_carries_a_30_day_moving_average_of_the_buy_price(self):
        series = make_service(busy_state())[0].today()["price_series"]
        self.assertEqual([r["ma"] for r in series[:9]], [None] * 9)                      # fewer than 10 stored days: no average yet, a gap
        self.assertEqual((series[9]["ma"], series[11]["ma"]), ("6112.50", "6137.50"))    # trailing average of the shop BUY price ending that day

    def test_the_price_series_carries_a_90_day_moving_average_computed_over_the_stored_days_before_the_shown_ones(self):
        series = make_service(long_price_state(130))[0].today()["price_series"]
        self.assertEqual(len(series), 30)                                                # the 30 shown days; the 100 days before them feed the 90-day average
        self.assertEqual((series[0]["ma90"], series[-1]["ma90"]), ("5555.00", "5845.00"))   # index 100: mean of buys 11..100; index 129: mean of buys 40..129 (hand-checked)
        self.assertEqual(series[-1]["ma"], "6145.00")                                    # the 30-day average is untouched
        self.assertTrue(all(isinstance(r["ma90"], str) for r in series))
        self.assertIsInstance(series[0]["ma90"], str)                                    # exact Decimal text, no float in the service

    def test_the_90_day_average_is_a_gap_while_fewer_than_90_days_are_stored_behind_a_day(self):
        series = make_service(long_price_state(100))[0].today()["price_series"]          # shown = stored indices 70..99
        self.assertEqual([r["ma90"] for r in series[:19]], [None] * 19)                 # indices 70..88: under 90 stored days behind them, never invented
        self.assertEqual((series[19]["ma90"], series[-1]["ma90"]), ("5445.00", "5545.00"))   # index 89 is the first day with 90 stored days (mean of buys 0..89)
        self.assertEqual(series[0]["ma"], "5555.00")                                     # the 30-day average already exists there
        short = make_service(busy_state())[0].today()["price_series"]
        self.assertEqual([r["ma90"] for r in short], [None] * len(short))                # the 12-day fixture has no 90-day average at all

    def test_price_hint_without_history_says_the_data_is_insufficient(self):
        self.assertEqual(self.vm(empty_state()).priceHint,
                         {"available": False, "average": "—",
                          "versus": "بيانات غير كافية (تحتاج 10 أيام أسعار على الأقل)",
                          "action": "بيانات غير كافية", "tone": "muted"})


class TodayScrapValueSqlTests(unittest.TestCase):
    def test_a_scrap_only_invoice_counts_at_what_was_actually_settled(self):
        """Owner 2026-11-19: the scrap purchase card showed the table value 11,460 while 11,400 was paid (a 60 settlement adjustment). The query adds the adjustment's signed amount
        for scrap-only invoices (one adjustment row per invoice, so the join cannot duplicate a row)."""
        from repositories.dashboard_repository import DashboardRepository
        seen = []

        class Cursor:
            def execute(self, query, params=None): seen.append(query)
            def fetchone(self): return {}
            def fetchall(self): return []
            def close(self): pass

        class Connection:
            def cursor(self, **kwargs): return Cursor()

        DashboardRepository(Connection()).today_invoices()
        sql = " ".join(seen[0].split())
        self.assertIn("LEFT JOIN invoice_settlement_adjustments a ON a.invoice_id = i.id", sql)
        self.assertIn("i.sell_total = 0 AND i.buy_total > 0 THEN COALESCE(a.amount_signed, 0)", sql)


def _only_dashboard_tests(loader, tests, pattern):
    suite = unittest.TestSuite()
    for case in (DashboardServiceTests, DashboardNewCardsServiceTests, DashboardChartsServiceTests, DashboardViewModelTests, DashboardPageTests, TodayScrapValueSqlTests):
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
        if getattr(self, "show_retired", True):                                         # the five retired cards are hidden by default (owner 2026-11-10); the old tests look at them
            self.item("dashboardPage").setProperty("showRetired", True)
        vm = self.controller.operational_viewmodels["dashboardVM"]
        self.wait_until(lambda: vm.ready and not vm.busy, "dashboard not loaded")
        QTest.qWait(80)
        return vm

    def test_dashboard_gold_movement_button_sits_between_customers_and_management_and_opens_the_two_gold_charts(self):
        """Owner 2026-11-18: the live gold chart and the 21K price chart live on their own page, reached by «السعر العالمي» under العملاء and above الإدارة."""
        self.open_dashboard(self.busy_snapshot())
        for width, height in page_harness.SIZES:
            self.root.setWidth(width)
            self.root.setHeight(height)
            QTest.qWait(200)
            with self.subTest(size=(width, height)):
                ys = [self.item(n).mapToScene(QPointF(0, 0)).y() for n in ("customersNavButton", "goldMovementNavButton", "reviewsNavButton")]
                self.assertEqual(ys, sorted(ys))
                self.assertLess(ys[0], ys[1])
                self.assertLess(ys[1], ys[2])
                for name in ("goldMovementNavButton", "reviewsNavButton", "logoutButton", "topUserName"):
                    self.assert_inside_window(self.item(name))
        self.root.setProperty("currentPage", 0)
        QTest.qWait(100)
        self.click(self.item("goldMovementNavButton"))
        QTest.qWait(250)
        self.assertEqual(self.root.property("currentPage"), 16)
        self.assertTrue(self.visible_item("priceChartCard") is not None and self.visible_item("liveGoldChartCard") is not None)
        self.assertIsNone(self.visible_item("todayCard"))
        self.assertEqual(self.warnings, [])

    def test_five_older_cards_are_hidden_by_default(self):
        self.show_retired = False
        self.open_dashboard(self.busy_snapshot())
        for name in ("priceCard", "priceHintCard", "oldGoldCard", "soldWeightCard", "scrapTodayCard"):
            self.assertFalse(self.item(name).property("visible"), name)
        self.assertTrue(self.item("todayCard").property("visible"))

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
                         "priceHintCard", "invoiceCount", "sellTotal", "dashboardPriceSell"):
                item = self.item(name)
                self.assertTrue(item.property("visible"), f"{name} hidden at {width}x{height}")
                left = item.mapToItem(page, QPointF(0, 0)).x()
                self.assertGreaterEqual(left, -1, f"{name} clipped left at {width}x{height}")
                self.assertLessEqual(left + item.property("width"), page.property("width") + 1,
                                     f"{name} overflows at {width}x{height}")
            # Owner 2026-11-18: today's figures are the first card, fully on screen. The two gold charts moved to «السعر العالمي» (page 16) and are not on the home page.
            self.assert_inside_window(self.item("todayCard"))
            self.assertFalse(self.item("priceChartCard").property("visible"))
            self.assertFalse(self.item("liveGoldChartCard").property("visible"))
            self.root.setProperty("currentPage", 16)
            QTest.qWait(200)
            gold_page = self.item("goldMovementPage")
            for name in ("priceChartCard", "liveGoldChartCard"):
                item = self.visible_item(name)
                self.assertIsNotNone(item, f"{name} missing on the gold movement page at {width}x{height}")
                left = item.mapToItem(gold_page, QPointF(0, 0)).x()
                self.assertGreaterEqual(left, -1, f"{name} clipped left at {width}x{height}")
                self.assertLessEqual(left + item.property("width"), gold_page.property("width") + 1, f"{name} overflows at {width}x{height}")
            self.assertIsNone(self.visible_item("todayCard"))
            self.root.setProperty("currentPage", 0)
            QTest.qWait(100)
            if target:
                Path(target).mkdir(parents=True, exist_ok=True)
                self.root.grabWindow().save(str(Path(target) / f"dashboard-{width}x{height}.png"))
        self.assertEqual(self.warnings, [])

    def test_dashboard_price_chart_legend_names_the_90_day_average_and_nothing_is_clipped(self):
        snapshot = self.busy_snapshot()
        snapshot["price_series"] = make_service(long_price_state(130), CUSTOMERS)[0].today()["price_series"]
        vm = self.open_dashboard(snapshot)
        self.assertEqual(len(vm.priceChart["ma90"]), 30)
        names = ("شراء المحل", "بيع المحل", "متوسط 30 يوم (شراء)", "متوسط 90 يوم (شراء)")
        target = os.getenv("MASGOLD_UI_REVIEW_DIR")
        for width, height in page_harness.SIZES:
            self.root.setWidth(width)
            self.root.setHeight(height)
            self.root.setProperty("currentPage", 16)                                       # the price chart lives on «السعر العالمي»
            QTest.qWait(250)
            card, legend, page = self.visible_item("priceChartCard"), self.visible_item("priceChartLegend"), self.item("goldMovementPage")
            self.assertTrue(legend.property("visible"), f"legend hidden at {width}x{height}")
            left = card.mapToItem(page, QPointF(0, 0)).x()
            self.assertGreaterEqual(left, -1, f"card clipped left at {width}x{height}")
            self.assertLessEqual(left + card.property("width"), page.property("width") + 1, f"card overflows at {width}x{height}")
            for name in names:
                entry = self.visible_item("priceChartLegend_" + name)
                self.assertIsNotNone(entry, f"{name} hidden at {width}x{height}")
                origin = entry.mapToItem(card, QPointF(0, 0))
                self.assertGreaterEqual(origin.x(), -1, f"{name} clipped left at {width}x{height}")
                self.assertLessEqual(origin.x() + entry.property("width"), card.property("width") + 1, f"{name} clipped right at {width}x{height}")
                self.assertLessEqual(origin.y() + entry.property("height"), card.property("height") + 1, f"{name} clipped at the bottom at {width}x{height}")
            self.assertGreaterEqual(legend.property("height"), self.visible_item("priceChartLegend_" + names[0]).property("height"))   # the Flow grows to hold a wrapped row
            if target:
                Path(target).mkdir(parents=True, exist_ok=True)
                self.root.grabWindow().save(str(Path(target) / f"dashboard-ma90-{width}x{height}.png"))
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
            for name in ("ratesCard",):
                item = self.item(name)
                self.assertTrue(item.property("visible"), f"{name} hidden at {width}x{height}")
                left = item.mapToItem(page, QPointF(0, 0)).x()
                self.assertGreaterEqual(left, -1, name)
                self.assertLessEqual(left + item.property("width"), page.property("width") + 1, name)
            wide = bool(page.property("wide"))                                                               # three columns on a wide page, two on a narrow one
            # Owner 2026-11-18: a card is as tall as its content; only the two charts of the first row are level with each other, and today's figures come first across the whole width.
            self.root.setProperty("currentPage", 16)
            QTest.qWait(200)
            price_card, live_card = self.visible_item("priceChartCard"), self.visible_item("liveGoldChartCard")      # «السعر العالمي»: the two charts stand under each other (owner 2026-11-18)
            self.assertLess(price_card.mapToScene(QPointF(0, 0)).y() + price_card.property("height") - 1, live_card.mapToScene(QPointF(0, 0)).y(), f"the charts are not stacked at {width}x{height}")
            self.assertLess(abs(price_card.mapToScene(QPointF(0, 0)).x() - live_card.mapToScene(QPointF(0, 0)).x()), 1.5)
            self.root.setProperty("currentPage", 0)
            QTest.qWait(100)
            today = self.item("todayCard")
            self.assertGreaterEqual(today.property("width"), page.property("width") - 40)
            if wide:
                # RTL grid: the first card is the rightmost (biggest x); each next card is further left.
                row1 = {name: self.item(name).mapToItem(page, QPointF(0, 0)).x() for name in ("priceCard", "priceHintCard", "soldWeightCard")}
                self.assertGreater(row1["priceCard"], row1["priceHintCard"], f"the gold price card is not the rightmost at {width}x{height}")        # the price on the physical right (owner review)
                self.assertGreater(row1["priceHintCard"], row1["soldWeightCard"])
                row2 = {name: self.item(name).mapToItem(page, QPointF(0, 0)).x() for name in ("oldGoldCard", "ratesCard")}
                self.assertGreater(row2["oldGoldCard"], row2["ratesCard"])
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
            self.assertEqual(self.item("balanceCash").property("text"), "1,234,570 ج")
            boxes = []
            for name in ("balanceCash", "balanceKarat_21", "balanceKarat_18", "balanceKarat_24",
                         "goldPriceArea", "connectionDot"):
                item = self.item(name)
                self.assertTrue(item.property("visible"), f"{name} hidden at {width}x{height}")
                self.assert_inside_window(item)
                if hasattr(item, "property") and item.property("truncated"):
                    self.fail(f"{name} elided at {width}x{height}")
                origin = item.mapToScene(QPointF(0, 0))
                boxes.append((name, origin.x(), origin.x() + item.property("width")))
            self.assertFalse(self.item("balanceKarat_22").property("visible"), "the 22K box was removed from the sidebar")
            cash_y = self.item("balanceBoxCash").mapToScene(QPointF(0, 0)).y()
            price_y = self.item("goldPriceArea").mapToScene(QPointF(0, 0)).y()
            self.assertGreater(price_y, cash_y + self.item("balanceBoxCash").property("height"), f"the price card is not under the balances at {width}")     # both live in the sidebar (owner 2026-11-18)
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
                left = label.mapToItem(page, QPointF(0, 0)).x()                       # (the card sits below today's figures since 2026-11-18, so only the horizontal fit is checked)
                self.assertGreaterEqual(left, -1, f"price clipped left at {width}x{height}")
                self.assertLessEqual(left + label.property("width"), page.property("width") + 1, f"price overflows at {width}x{height}")
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
        for name in ("todayCard", "soldWeightCard"):
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
        self.assertEqual(vm.topTypes[0]["type"], "دبلة")
        # the customer balances card: the amount only, distinguished by colour (RED owes / BLUE credit)
        self.assertTrue(self.item("customerBalancesCard").property("visible"))
        self.assertEqual(self.item("customerBalancesEmpty").property("visible"), False)
        row = self.item("customerBalance_0")
        line = next(c for c in row.findChildren(object) if c.property("value") is not None and c.property("value") != "")
        self.assertEqual(line.property("value"), "25,000 ج")                                         # no «على العميل» prefix, no direction wording
        self.assertEqual(line.property("valueColor").name(), self._theme_color("red"))
        credit = self.item("customerBalance_1")
        line2 = next(c for c in credit.findChildren(object) if c.property("value") is not None and c.property("value") != "")
        self.assertEqual(line2.property("valueColor").name(), self._theme_color("blue"))

    def test_overdue_scrap_and_dead_stock_cards(self):
        """Owner request 2026-10-26: the overdue debts and today's scrap price (the dead-stock card was removed 2026-11-01), readable at every target size."""
        service, _ = make_service(busy_state(), CUSTOMERS | {"INVENTORY_VIEW"})
        self.open_dashboard(service.today())
        for width, height in page_harness.SIZES:
            self.root.setWidth(width)
            self.root.setHeight(height)
            QTest.qWait(250)
            page = self.item("dashboardPage")
            for name in ("overdueCard", "scrapTodayCard"):
                item = self.item(name)
                self.assertTrue(item.property("visible"), f"{name} hidden at {width}x{height}")
                left = item.mapToItem(page, QPointF(0, 0)).x()
                self.assertGreaterEqual(left, -1, name)
                self.assertLessEqual(left + item.property("width"), page.property("width") + 1, name)
        self.assertEqual(self.item("overdueRow_0") is not None, True)
        self.assertEqual(self.item("overdueEmpty").property("visible"), False)
        self.assertIsNotNone(self.item("scrapToday_0"))
        self.assertEqual(self.warnings, [])

    def _inside(self, item, card, name, size):
        left = item.mapToItem(card, QPointF(0, 0)).x()
        top = item.mapToItem(card, QPointF(0, 0)).y()
        self.assertGreaterEqual(left, -0.5, f"{name} clipped left at {size}")
        self.assertLessEqual(left + item.property("width"), card.property("width") + 0.5, f"{name} overflows the card at {size}")
        self.assertGreaterEqual(top, -0.5, f"{name} above the card at {size}")
        self.assertLessEqual(top + item.property("height"), card.property("height") + 0.5, f"{name} below the card at {size}")

    def test_dashboard_scrap_and_balance_charts_fit_every_size(self):
        """The scrap-bought-against-gold-sold columns and the customer balance bar (owner 2026-11-18): drawn from the view model, inside their cards at every target size."""
        self.open_dashboard(self.busy_snapshot())
        target = os.getenv("MASGOLD_UI_REVIEW_DIR")
        for width, height in page_harness.SIZES:
            self.root.setWidth(width)
            self.root.setHeight(height)
            QTest.qWait(250)
            size = f"{width}x{height}"
            page = self.item("dashboardPage")
            for card_name in ("scrapSoldCard", "balanceSplitCard"):
                card = self.item(card_name)
                self.assertTrue(card.property("visible"), f"{card_name} hidden at {size}")
                left = card.mapToItem(page, QPointF(0, 0)).x()
                self.assertGreaterEqual(left, -1, f"{card_name} clipped left at {size}")
                self.assertLessEqual(left + card.property("width"), page.property("width") + 1, f"{card_name} overflows at {size}")
            scrap = self.item("scrapSoldCard")
            self.assertFalse(self.item("scrapSoldEmpty").property("visible"))
            for i in range(14):
                self.assertIsNotNone(self.item(f"scrapSoldDay_{i}"), i)
                for key in ("bought", "sold"):
                    bar = self.item(f"scrapSold_{key}_{i}")
                    self.assertLessEqual(bar.property("width"), 24, f"a column is wider than 24 px at {size}")
                    self._inside(bar, scrap, f"scrapSold_{key}_{i}", size)
            today_bought, today_sold = self.item("scrapSold_bought_13"), self.item("scrapSold_sold_13")
            self.assertGreater(today_bought.property("height"), today_sold.property("height"))                 # 13.43 g against 10.74 g
            self.assertEqual(self.item("scrapSold_bought_0").property("height"), 0)                             # a day without a line has no column
            self.assertGreater(today_bought.mapToItem(scrap, QPointF(0, 0)).x(), today_sold.mapToItem(scrap, QPointF(0, 0)).x())   # the first series (scrap bought) on the right of the pair, as in the legend
            for name in ("scrapSoldTotals", "scrapSoldCaption"):
                label = self.item(name)
                self._inside(label, scrap, name, size)
            self.assertIn("13.43", self.item("scrapSoldTotals").property("value"))
            self.assertIn("21", self.item("scrapSoldCaption").property("value"))
            split = self.item("balanceSplitCard")
            self.assertFalse(self.item("balanceSplitEmpty").property("visible"))
            self.assertEqual((self.item("balanceSplit_owed").property("value"), self.item("balanceSplit_credit").property("value")), ("25,000 ج", "3,200 ج"))
            owed, credit, bar = self.item("balanceSplitOwedSegment"), self.item("balanceSplitCreditSegment"), self.item("balanceSplitBar")
            self.assertGreater(owed.property("width"), credit.property("width") * 5)                           # 89% against 11%
            self.assertLessEqual(owed.property("width") + credit.property("width"), bar.property("width") + 0.5)
            self.assertGreater(owed.mapToItem(split, QPointF(0, 0)).x(), credit.mapToItem(split, QPointF(0, 0)).x())     # على العميل on the physical right
            for name in ("balanceSplitBar", "balanceSplit_owed", "balanceSplit_credit", "balanceSplitNote_owed", "balanceSplitNote_credit", "balanceSplitCaption"):
                self._inside(self.item(name), split, name, size)
                if name.startswith("balanceSplit_"):
                    self.assertFalse(self.item(name).property("truncated"), f"{name} elided at {size}")
            self.assertEqual(self._theme_color("accountReceivable"), owed.property("color").name())
            self.assertEqual(self._theme_color("accountPayable"), credit.property("color").name())
            if target:
                Path(target).mkdir(parents=True, exist_ok=True)
                self.item("scrapSoldCard").setProperty("visible", True)
                self.root.grabWindow().save(str(Path(target) / f"dashboard-charts-{size}.png"))
        self.assertEqual(self.warnings, [])

    def test_dashboard_news_ticker_scrolls_along_the_bottom_and_the_news_card_is_gone(self):
        snapshot = self.busy_snapshot()
        snapshot["gold_news"] = {"available": True, "updated": "21:27", "error": "", "items": [
            {"title": "الذهب ينهار لقاع الجلسة", "time": "2026-11-18 19:12", "link": "https://example.invalid/1"},
            {"title": "UBS تجيب عن سؤال ارتفاع الذهب", "time": "2026-11-18 15:24", "link": "https://example.invalid/2"},
            {"title": "بنك أوف أمريكا يصيب", "time": "2026-11-18 13:03", "link": "https://example.invalid/3"}]}
        self.open_dashboard(snapshot)
        for width, height in page_harness.SIZES:
            self.root.setWidth(width)
            self.root.setHeight(height)
            QTest.qWait(250)
            ticker = self.item("newsTicker")
            self.assertTrue(ticker.property("visible"), f"no ticker at {width}x{height}")
            self.assert_inside_window(ticker)
            window = self.root.property("height")
            bottom = ticker.mapToScene(QPointF(0, ticker.property("height"))).y()
            self.assertGreaterEqual(bottom, window - 90, "the ticker is not along the bottom of the window")           # the main window has its own chrome under the page
        self.assertIsNone(self.visible_item("goldNewsCard"))                                                           # the news card was replaced by the ticker
        strip = self.item("newsTickerStrip")
        first = strip.property("x")
        QTest.qWait(400)
        self.assertGreater(strip.property("x"), first, "the ticker does not move")                                      # it slides to the right (the reading direction of Arabic)
        self.assertEqual(self.warnings, [])

    def test_dashboard_without_news_has_no_ticker(self):
        self.open_dashboard(self.busy_snapshot())
        self.assertFalse(self.item("newsTicker").property("visible"))

    def test_dashboard_scrap_chart_tooltip_shows_both_series_of_the_hovered_day(self):
        self.open_dashboard(self.busy_snapshot())
        self.root.setWidth(1440)
        self.root.setHeight(2600)                                                                           # the whole page is on screen, so the mouse can reach the chart
        QTest.qWait(300)
        tip = self.item("scrapSoldTip")
        self.assertFalse(tip.property("visible"))
        cell = self.item("scrapSoldDay_13")
        centre = cell.mapToScene(QPointF(cell.property("width") / 2, cell.property("height") / 2)).toPoint()
        QTest.mouseMove(self.root, centre)
        QTest.mouseMove(self.root, centre + QPoint(1, 0))
        self.wait_until(lambda: tip.property("visible"), "no tooltip on hover")
        self.assertEqual(tip.property("title"), "27/09")
        self.assertEqual([r["value"] for r in tip.property("rows").toVariant()], ["13.43 جرام", "10.74 جرام"])
        QTest.mouseMove(self.root, QPoint(2, 2))
        self.wait_until(lambda: not tip.property("visible"), "tooltip stayed after the mouse left")

    def test_dashboard_scrap_and_balance_charts_say_so_when_there_is_no_data(self):
        service, _ = make_service(empty_state(), CUSTOMERS)
        self.open_dashboard(service.today())
        for width, height in page_harness.SIZES:
            self.root.setWidth(width)
            self.root.setHeight(height)
            QTest.qWait(200)
            size = f"{width}x{height}"
            for name, card in (("scrapSoldEmpty", "scrapSoldCard"), ("balanceSplitEmpty", "balanceSplitCard")):
                empty = self.item(name)
                self.assertTrue(empty.property("visible"), f"{name} hidden at {size}")
                self._inside(empty, self.item(card), name, size)
            self.assertFalse(self.item("balanceSplitBar").property("visible"))
        self.assertEqual(self.item("scrapSoldEmpty").property("value"), "لا يوجد شراء كسر أو بيع ذهب في آخر 14 يوم")

    def test_new_cards_follow_the_grants(self):
        service, _ = make_service(busy_state(), BILLING)
        self.open_dashboard(service.today())
        self.assertFalse(self.item("overdueCard").property("visible"))
        self.assertTrue(self.item("scrapTodayCard").property("visible"))

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




class SalesDaysTests(unittest.TestCase):
    def test_every_day_of_the_window_has_a_bar_and_a_missing_day_is_zero(self):
        from datetime import date
        from services.dashboard_service import SALES_DAYS, DashboardService
        rows = DashboardService.sales_days([{"day": date(2026, 11, 1), "total": "1500.50"}], date(2026, 11, 1))
        self.assertEqual(len(rows), SALES_DAYS)
        self.assertEqual((rows[-1]["day"], rows[-1]["total"], rows[0]["total"]), ("2026-11-01", "1500.50", "0.00"))


if __name__ == "__main__":
    unittest.main()
