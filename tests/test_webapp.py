"""The iPad reports app: sessions, the read-only API (real ReportService over the report fakes) and the HTTP surface (a real server on a free local port). No database is used."""
from contextlib import contextmanager
from decimal import Decimal as D
from http.client import HTTPConnection
import json
import threading
import unittest

from services.authentication_service import AuthenticationError, AuthenticationResult, _VERIFIED
from services.dead_cash_service import DeadCashError
from services.report_service import ReportService
from tests.auth_fakes import FakeAuthorization
from tests.test_report_service import ALL, FakeRepo, TODAY
from webapp import server
from webapp.api import ApiError, WebReports
from webapp.sessions import SessionStore

OWNER = ALL | {"MANAGEMENT_REVIEW", "MARKET_TRIP_MANAGE", "INTERMEDIARY_HOLDING_VIEW"}


class FakeAuthentication:
    def __init__(self, permissions=OWNER):
        self.permissions, self.attempts = frozenset(permissions), []

    def authenticate(self, username, password):
        self.attempts.append(username)
        if (username, password) != ("admin", "secret"):
            raise AuthenticationError("تعذر تسجيل الدخول.")
        return AuthenticationResult(7, None, "المالك", self.permissions, _VERIFIED)

    def verify_user_password(self, user_id, password):
        self.verified = getattr(self, "verified", []) + [(user_id, password)]
        if password != "secret":
            raise AuthenticationError("كلمة المرور غير صحيحة.")


def make_api(permissions=OWNER, store=None):
    @contextmanager
    def snapshot():
        yield object()

    def service(session):
        return ReportService(session, transaction_factory=snapshot, repository=FakeRepo, authorization=FakeAuthorization(session), today=lambda: TODAY)
    FakeRepo.sheets = None
    FakeRepo.tables = {"market_trip_book_lines": True, "marketer_gold_movements": True, "shop_assayed_stock_movements": True}
    return WebReports(authentication=FakeAuthentication(permissions), store=store, service_factory=service, today=lambda: TODAY)


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


class SessionStoreTests(unittest.TestCase):
    class Session:
        def __init__(self):
            self.out = False

        def logout(self):
            self.out = True

    def test_an_idle_or_old_session_expires_and_is_logged_out(self):
        clock = Clock()
        store = SessionStore(clock=clock, idle=60, maximum=300)
        a = self.Session()
        token = store.add(a, object())
        clock.now = 50
        self.assertIsNotNone(store.get(token))                     # renews the idle timer
        clock.now = 100
        self.assertIsNotNone(store.get(token))
        clock.now = 170
        self.assertIsNone(store.get(token))
        self.assertTrue(a.out)
        b = self.Session()
        token = store.add(b, object())
        for step in range(1, 8):                                  # always active, but never beyond the absolute limit
            clock.now = 170 + step * 50
            store.get(token)
        self.assertIsNone(store.get(token))
        self.assertTrue(b.out)

    def test_the_table_never_grows_past_its_capacity_and_unknown_tokens_are_refused(self):
        clock = Clock()
        store = SessionStore(clock=clock, capacity=3)
        tokens = []
        for _ in range(5):
            clock.now += 1
            tokens.append(store.add(self.Session(), object()))
        self.assertEqual(store.count(), 3)
        self.assertIsNone(store.get(tokens[0]))
        self.assertIsNone(store.get(""))
        self.assertIsNone(store.get("not-a-token"))


class ApiTests(unittest.TestCase):
    def test_login_checks_credentials_and_the_report_list_follows_the_rights(self):
        api = make_api()
        with self.assertRaises(ApiError) as bad:
            api.login("admin", "wrong")
        self.assertEqual(bad.exception.status, 401)
        token = api.login("admin", "secret")
        overview = api.overview(token)
        self.assertEqual(overview["user"], "المالك")
        keys = [r["key"] for g in overview["groups"] for r in g["reports"]]
        self.assertIn("goldflow", keys)
        limited = make_api(ALL)                                    # no MANAGEMENT_REVIEW: the owner-only reports are not offered
        keys = [r["key"] for g in limited.overview(limited.login("admin", "secret"))["groups"] for r in g["reports"]]
        self.assertNotIn("goldflow", keys)
        self.assertIn("sales", keys)

    def test_the_home_page_has_one_card_per_module_the_user_may_use(self):
        api = make_api()
        modules = api.overview(api.login("admin", "secret"))["modules"]
        self.assertEqual([m["key"] for m in modules], ["reports", "customers", "treasury", "daily", "suppliers", "goldprofit", "staff", "cashflow", "bankflow", "inventory", "market", "reviews", "currency", "stocktake", "calc"])
        by_key = {m["key"]: m for m in modules}
        reports, customers, treasury, daily, inventory, reviews = (by_key[k] for k in ("reports", "customers", "treasury", "daily", "inventory", "reviews"))
        self.assertEqual([r["key"] for g in inventory["groups"] for r in g["reports"]], ["stock", "alerts", "stockweights"])
        self.assertEqual((reviews["kind"], reviews["groups"], customers["kind"]), ("reviews", [], "reports"))
        transfers = make_api(OWNER | {"FINANCIAL_ACCOUNT_OPERATE"})
        self.assertIn("transfers", [m["key"] for m in transfers.overview(transfers.login("admin", "secret"))["modules"]])
        self.assertNotIn("transfers", [m["key"] for m in modules])                  # no operating right: no transfers card
        self.assertEqual([r["key"] for g in treasury["groups"] for r in g["reports"]], ["treasury", "cashflow"])
        self.assertEqual([r["key"] for g in daily["groups"] for r in g["reports"]], ["sales"])
        self.assertEqual((treasury["period"], daily["period"], customers["period"]), ("today", "today", ""))
        self.assertEqual(api.overview(api.login("admin", "secret"))["today"], "2026-10-20")
        self.assertEqual([r["key"] for g in customers["groups"] for r in g["reports"]], ["customers", "deadcash"])
        shown = [r["key"] for g in reports["groups"] for r in g["reports"]]
        self.assertNotIn("customers", shown)                                       # a report belongs to ONE card
        self.assertNotIn("deadcash", shown)
        self.assertFalse({"sales", "treasury", "cashflow", "stock", "alerts", "stockweights"} & set(shown))              # claimed by their own cards
        self.assertNotIn("goldflow", shown)                                        # now on the «ربح الذهب» card
        self.assertEqual([r["key"] for g in by_key["goldprofit"]["groups"] for r in g["reports"]], ["profit", "goldflow"])
        self.assertEqual(customers["count"], 2)
        self.assertTrue(all(m["title"] and m["icon"] and m["subtitle"] for m in modules))
        no_customers = make_api(OWNER - {"CUSTOMERS_VIEW"})
        self.assertNotIn("customers", [m["key"] for m in no_customers.overview(no_customers.login("admin", "secret"))["modules"]])
        only_customers = make_api({"APP_LOGIN", "REPORTS_VIEW", "CUSTOMERS_VIEW"})
        keys = [m["key"] for m in only_customers.overview(only_customers.login("admin", "secret"))["modules"]]
        self.assertIn("customers", keys)

    def test_a_user_without_any_report_right_cannot_log_in(self):
        api = make_api({"APP_LOGIN"})
        with self.assertRaises(ApiError) as refused:
            api.login("admin", "secret")
        self.assertEqual(refused.exception.status, 403)

    def test_a_report_answer_is_the_desktop_display_form_and_filters_are_whitelisted(self):
        api = make_api()
        token = api.login("admin", "secret")
        view = api.report(token, "goldflow", {"from": "2026-10-01", "to": "2026-10-31", "evil": "x", "page": "abc"})
        self.assertEqual(view["report"], "goldflow")
        self.assertEqual(view["filters"]["from"], "2026-10-01")
        self.assertNotIn("evil", view["filters"])
        self.assertTrue(view["lines"] and view["lines"][0]["series"])
        self.assertTrue(all(isinstance(c, str) for t in view["tables"] for r in t["rows"] for c in r["cells"]))
        json.dumps(view, default=str)                              # the answer is serialisable
        self.assertTrue(view["periodUsed"])
        stock = api.report(token, "stock", {"from": "2020-01-01"})
        self.assertFalse(stock["periodUsed"])

    def test_a_missing_or_expired_session_and_an_unknown_report_are_refused(self):
        api = make_api()
        with self.assertRaises(ApiError) as nobody:
            api.report("", "sales", {})
        self.assertEqual(nobody.exception.status, 401)
        token = api.login("admin", "secret")
        with self.assertRaises(ApiError) as unknown:
            api.report(token, "nope", {})
        self.assertEqual(unknown.exception.status, 404)
        api.logout(token)
        with self.assertRaises(ApiError):
            api.overview(token)


class FakeDeadCash:
    def __init__(self):
        self.calls, self.fail = [], None

    def context(self, customer, *, _expected_actor=None):
        return {"customer_id": str(customer), "name": "عميل", "balance": "1500.00", "eligible": True}

    def write_off(self, customer, amount, reason, password, expected, key, *, silent=True, _expected_actor=None):
        self.calls.append((customer, amount, reason, password, expected, key, silent))
        if self.fail:
            raise self.fail
        return {"id": "5", "code": "DC000005", "amount": amount, "balance_before": expected, "balance_after": "0.00", "duplicate": False}


class CustomersAndWriteOffTests(unittest.TestCase):
    def setUp(self):
        self.fake = FakeDeadCash()
        self.api = make_api(OWNER | {"DEAD_CASH_MANAGE"})
        self.api._deadcash_factory = lambda session: self.fake
        self.token = self.api.login("admin", "secret")

    def test_the_customers_report_lists_who_owes_and_whom_we_owe_with_the_exact_balance(self):
        view = self.api.report(self.token, "customers", {})
        balances = view["balances"]
        self.assertTrue(balances["rows"])
        self.assertEqual(balances["total"], len(balances["rows"]))
        for row in balances["rows"]:
            owes, credit = bool(row["owes"]), bool(row["credit"])
            self.assertNotEqual(owes, credit)                                      # exactly one side per customer
            self.assertEqual(owes, float(row["balance"]) > 0)
        ordered = [abs(float(r["balance"])) for r in balances["rows"]]
        self.assertEqual(ordered, sorted(ordered, reverse=True))                   # the biggest balances first
        only_credit = self.api.report(self.token, "customers", {"balance": "CREDIT"})["balances"]["rows"]
        self.assertTrue(all(r["credit"] and not r["owes"] for r in only_credit))
        self.assertNotIn("balances", self.api.report(self.token, "sales", {}))

    def test_only_a_user_with_the_write_off_right_is_offered_it(self):
        self.assertTrue(self.api.overview(self.token)["canWriteOff"])
        limited = make_api(OWNER)
        self.assertFalse(limited.overview(limited.login("admin", "secret"))["canWriteOff"])

    def test_a_write_off_goes_through_the_desktop_service_with_the_typed_values_and_never_trusts_the_page_for_identity(self):
        result = self.api.write_off(self.token, {"customer": "17", "amount": "500", "reason": "عميل مسافر", "password": "pw", "expected_balance": "1500.00",
                                                 "request_key": "11111111-1111-4111-8111-111111111111", "user_id": "999", "silent": False})
        self.assertEqual(result["code"], "DC000005")
        self.assertEqual(self.fake.calls, [("17", "500", "عميل مسافر", "pw", "1500.00", "11111111-1111-4111-8111-111111111111", True)])   # silent stays the owner default; no user id exists
        context = self.api.deadcash_context(self.token, {"customer": "17"})
        self.assertEqual(context["balance"], "1500.00")

    def test_refusals_of_the_service_become_clear_http_errors_and_a_missing_session_is_refused(self):
        cases = [(DeadCashError("PASSWORD_INVALID", "كلمة المرور غير صحيحة"), 400), (DeadCashError("BALANCE_CHANGED", "الرصيد تغير"), 409),
                 (DeadCashError("EXCEEDS_BALANCE", "أكبر من الرصيد"), 400), (PermissionError("لا توجد صلاحية"), 403)]
        for error, status in cases:
            self.fake.fail = error
            with self.assertRaises(ApiError) as caught:
                self.api.write_off(self.token, {"customer": "1", "amount": "1", "reason": "x", "password": "p", "expected_balance": "1", "request_key": "k"})
            self.assertEqual((caught.exception.status, caught.exception.message), (status, str(error)))
        with self.assertRaises(ApiError) as nobody:
            self.api.write_off("", {})
        self.assertEqual(nobody.exception.status, 401)
        self.fake.fail = RuntimeError("secret internal detail")
        with self.assertRaises(ApiError) as hidden:
            self.api.write_off(self.token, {"customer": "1", "amount": "1", "reason": "x", "password": "p", "expected_balance": "1", "request_key": "k"})
        self.assertEqual(hidden.exception.status, 500)
        self.assertNotIn("secret", hidden.exception.message)


class InvoiceApiTests(unittest.TestCase):
    """The invoice page and the customer's invoice list: the real history service over its own fakes, read only."""

    def setUp(self):
        from unittest import mock
        from tests import test_invoice_history as history
        state = {"calls": [], "rows": [], "invoices": {"21": history.invoice()},
                 "ledger": {"21": [{"transaction_id": "6", "amount_delta": history.D("1000.00"), "type_code": "SALE_BALANCE_DUE"}]}}

        @contextmanager
        def transaction():
            yield state
        self.patch = mock.patch("webapp.api.InvoiceHistoryService", lambda session: history.InvoiceHistoryService(
            session, transaction_factory=transaction, invoice_repository=history.FakeRepo, authorization=FakeAuthorization(session)))
        self.patch.start()
        self.addCleanup(self.patch.stop)
        self.api = make_api()
        self.token = self.api.login("admin", "secret")

    def test_one_invoice_is_shown_with_the_desktop_display_rules(self):
        view = self.api.invoice(self.token, {"id": "21"})
        self.assertTrue(view["detail"]["number"])
        self.assertTrue(view["saleLines"])
        self.assertTrue(all(isinstance(v, str) for v in view["saleLines"][0].values() if not isinstance(v, bool)))
        self.assertIn("timeline", view)
        json.dumps(view)

    def test_a_missing_invoice_a_bad_id_and_a_missing_session_are_refused(self):
        with self.assertRaises(ApiError) as missing:
            self.api.invoice(self.token, {"id": "999"})
        self.assertEqual(missing.exception.status, 404)
        with self.assertRaises(ApiError) as nobody:
            self.api.invoice("", {"id": "21"})
        self.assertEqual(nobody.exception.status, 401)
        limited = make_api({"APP_LOGIN", "REPORTS_VIEW"})
        with self.assertRaises(ApiError) as denied:
            limited.invoice(limited.login("admin", "secret"), {"id": "21"})
        self.assertEqual(denied.exception.status, 403)

    def test_the_invoices_of_a_customer_are_listed_from_the_customer_account_service(self):
        from unittest import mock
        seen = []

        class Fake:
            def __init__(self, session):
                pass

            def customer_invoices(self, customer, start="", end="", kind="", search="", page=1, *, _expected_actor=None):
                seen.append((customer, start, end, search, page))
                return {"items": [{"invoice_id": 21, "invoice_number": "26092700", "finalized_at": "2026-09-27 20:00:00", "transaction_kind": "SALE", "invoice_total": D("1500.00"),
                                   "invoice_status": "FINALIZED", "settlement_status": "SETTLED"}], "page": page, "has_more": True}
        with mock.patch("webapp.api.CustomerAccountService", Fake):
            data = self.api.customer_invoices(self.token, {"customer": "17", "page": "2", "text": "2609"})
        self.assertEqual(seen, [("17", "", "", "2609", 2)])
        self.assertEqual((data["page"], data["hasMore"], data["rows"][0]["id"], data["rows"][0]["number"]), (2, True, "21", "26092700"))
        self.assertIn("1,500", data["rows"][0]["total"])


class SearchApiTests(unittest.TestCase):
    """The customers card: find a customer (name / mobile / code) or an invoice by its exact number."""

    def setUp(self):
        from unittest import mock
        self.seen, self.invoices = [], {"26092700": 21}

        class FakeCustomers:
            def __init__(inner, session):
                pass

            def search_customers(inner, search="", status="ALL", customer_type_id=None, balance_status="ALL", page=1, page_size=100):
                self.seen.append((search, status, page, page_size))
                rows = [{"customer_id": 17, "customer_code": "C000017", "name": "أحمد", "mobile": "0100", "place": "طنطا", "is_active": 1, "balance": D("1500.00")},
                        {"customer_id": 18, "customer_code": "C000018", "name": "أحمد علي", "mobile": "", "place": "", "is_active": 0, "balance": D("-200.00")}]
                return {"items": rows, "page": page, "page_size": page_size, "has_more": page < 2}

        class FakeHistory:
            def __init__(inner, session):
                pass

            def find_by_number(inner, number, *, _expected_actor=None):
                return self.invoices.get(number)
        for target, fake in (("webapp.api.CustomerService", FakeCustomers), ("webapp.api.InvoiceHistoryService", FakeHistory)):
            patcher = mock.patch(target, fake)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.api = make_api()
        self.token = self.api.login("admin", "secret")

    def test_a_name_finds_customers_with_their_balance_side(self):
        data = self.api.customer_search(self.token, {"text": "أحمد"})
        self.assertIsNone(data["invoice"])
        first, second = data["customers"]
        self.assertEqual((first["id"], first["owes"] != "", first["credit"]), ("17", True, ""))
        self.assertEqual((second["credit"] != "", second["owes"], second["active"]), (True, "", False))        # a customer we owe, and an inactive one is still found
        self.assertEqual(self.seen, [("أحمد", "ALL", 1, 30)])
        self.assertTrue(data["hasMore"])
        self.api.customer_search(self.token, {"text": "أحمد", "page": "2"})
        self.assertEqual(self.seen[-1][2], 2)

    def test_an_exact_invoice_number_is_found_and_a_wrong_one_is_not(self):
        data = self.api.customer_search(self.token, {"text": "26092700"})
        self.assertEqual(data["invoice"], {"id": "21", "number": "26092700"})
        self.assertIsNone(self.api.customer_search(self.token, {"text": "26099999"})["invoice"])
        self.assertIsNone(self.api.customer_search(self.token, {"text": "أحمد"})["invoice"])                  # a name is never looked up as an invoice number

    def test_an_empty_search_returns_nothing_and_a_missing_session_is_refused(self):
        self.assertEqual(self.api.customer_search(self.token, {"text": "  "}), {"invoice": None, "customers": [], "page": 1, "hasMore": False})
        self.assertEqual(self.seen, [])
        with self.assertRaises(ApiError) as nobody:
            self.api.customer_search("", {"text": "x"})
        self.assertEqual(nobody.exception.status, 401)

    def test_the_reports_that_honour_the_text_filter_are_marked_searchable(self):
        self.assertTrue(self.api.report(self.token, "sales", {})["searchable"])
        self.assertTrue(self.api.report(self.token, "customers", {})["searchable"])
        self.assertFalse(self.api.report(self.token, "goldflow", {})["searchable"])


class ChartApiTests(unittest.TestCase):
    def setUp(self):
        self.api = make_api()
        self.token = self.api.login("admin", "secret")

    def test_the_price_chart_has_buy_sell_and_average_lines_for_the_chosen_days(self):
        data = self.api.chart(self.token, {"kind": "PRICE_CHART", "id": "21", "days": "30"})
        self.assertEqual((data["kind"], data["karat"]), ("PRICE_CHART", "21"))
        group = data["lines"][0]
        self.assertEqual([s["tone"] for s in group["series"]], ["buy", "sell", "avg"])
        self.assertEqual(len(group["labels"]), len(group["series"][0]["values"]))
        self.assertGreaterEqual(group["high"], group["low"])
        json.dumps(data, default=str)

    def test_the_monthly_sales_of_a_type_is_a_bar_chart_with_the_report_filters(self):
        data = self.api.chart(self.token, {"kind": "CHART", "id": "دبلة", "extra": "21", "from": "2026-10-01", "to": "2026-10-31"})
        self.assertEqual(data["kind"], "CHART")
        rows = data["bars"][0]["rows"]
        self.assertTrue(rows and all("قطعة" in r["text"] for r in rows))
        self.assertIn("جرام", data["weight"])

    def test_the_pieces_behind_a_row_are_listed_and_a_missing_right_or_an_unknown_kind_is_refused(self):
        data = self.api.chart(self.token, {"kind": "PIECES", "id": "خاتم", "extra": "21|1", "report": "stockweights"})
        self.assertEqual(data["pieces"]["columns"][0], "الباركود")
        self.assertEqual(int(data["pieces"]["count"]), len(data["pieces"]["rows"]))
        with self.assertRaises(ApiError) as unknown:
            self.api.chart(self.token, {"kind": "NOPE"})
        self.assertEqual(unknown.exception.status, 404)
        with self.assertRaises(ApiError) as bad:
            self.api.chart(self.token, {"kind": "PRICE_CHART", "id": "99"})
        self.assertEqual(bad.exception.status, 400)
        limited = make_api({"APP_LOGIN", "REPORTS_VIEW"})
        other = limited.login("admin", "secret")
        with self.assertRaises(ApiError) as denied:
            limited.chart(other, {"kind": "CHART", "id": "دبلة", "extra": "21"})
        self.assertEqual(denied.exception.status, 403)
        with self.assertRaises(ApiError) as nobody:
            self.api.chart("", {"kind": "PRICE_CHART", "id": "21"})
        self.assertEqual(nobody.exception.status, 401)

    def test_the_demand_chart_answers_with_its_yearly_lines_or_an_honest_empty_state(self):
        data = self.api.chart(self.token, {"kind": "DEMAND_CHART", "id": "M"})
        self.assertEqual((data["kind"], data["granularity"]), ("DEMAND_CHART", "M"))
        self.assertEqual(bool(data["lines"]), data["hasData"])
        json.dumps(data, default=str)


class HttpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.api = make_api()
        cls.server = server.serve("127.0.0.1", 0, cls.api)
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def request(self, method, path, body=None, headers=None):
        connection = HTTPConnection("127.0.0.1", self.port, timeout=10)
        connection.request(method, path, body=body, headers=headers or {})
        response = connection.getresponse()
        data = response.read()
        result = (response.status, dict(response.getheaders()), data)
        connection.close()
        return result

    def login(self):
        status, headers, _ = self.request("POST", "/api/login", json.dumps({"username": "admin", "password": "secret"}),
                                          {"X-Requested-With": "masgold", "Content-Type": "application/json", "Host": f"127.0.0.1:{self.port}"})
        self.assertEqual(status, 200)
        return headers["Set-Cookie"], headers["Set-Cookie"].split(";")[0]

    def test_the_page_and_assets_are_served_with_security_headers(self):
        for path in ("/", "/app.js", "/style.css", "/manifest.webmanifest", "/icon.svg"):
            status, headers, body = self.request("GET", path)
            self.assertEqual(status, 200, path)
            self.assertEqual(headers["Cache-Control"], "no-store")
            self.assertEqual(headers["X-Content-Type-Options"], "nosniff")
            self.assertIn("frame-ancestors 'none'", headers["Content-Security-Policy"])
        self.assertNotIn(b".innerHTML", self.request("GET", "/app.js")[2])      # report text is only ever inserted as text
        script = self.request("GET", "/app.js")[2]
        self.assertIn(b'k === "style"', script)                                   # styles go through the CSSOM: the CSP refuses inline style attributes (found in a real browser run)
        self.assertNotIn(b"style-src 'self' 'unsafe-inline'", self.request("GET", "/")[1]["Content-Security-Policy"].encode())

    def test_nothing_outside_the_whitelist_exists_and_only_reads_and_login_are_allowed(self):
        for path in ("/../server.py", "/static/app.js", "/api/exports", "/webapp/api.py", "/%2e%2e/api.py"):
            self.assertEqual(self.request("GET", path)[0], 404, path)
        for method in ("PUT", "DELETE", "PATCH"):
            self.assertEqual(self.request(method, "/api/report")[0], 405, method)
        self.assertEqual(self.request("POST", "/api/report", "{}", {"X-Requested-With": "masgold"})[0], 403)

    def test_login_needs_the_custom_header_and_a_same_origin_and_sets_a_strict_cookie(self):
        status, _, _ = self.request("POST", "/api/login", json.dumps({"username": "admin", "password": "secret"}))
        self.assertEqual(status, 403)                                              # no custom header
        status, _, _ = self.request("POST", "/api/login", json.dumps({"username": "admin", "password": "secret"}),
                                    {"X-Requested-With": "masgold", "Origin": "http://evil.example"})
        self.assertEqual(status, 403)                                              # another origin
        status, _, body = self.request("POST", "/api/login", json.dumps({"username": "admin", "password": "bad"}), {"X-Requested-With": "masgold"})
        self.assertEqual(status, 401)
        self.assertNotIn(b"bad", body)
        cookie, _ = self.login()
        for flag in ("HttpOnly", "SameSite=Strict", "Path=/"):
            self.assertIn(flag, cookie)
        self.assertNotIn("Secure", cookie)                                         # plain HTTP locally; behind https the proxy header adds it

    def test_the_api_needs_the_cookie_and_a_logout_ends_the_session(self):
        self.assertEqual(self.request("GET", "/api/overview")[0], 401)
        self.assertEqual(self.request("GET", "/api/report?key=sales")[0], 401)
        _, cookie = self.login()
        status, _, body = self.request("GET", "/api/overview", headers={"Cookie": cookie})
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["user"], "المالك")
        status, _, body = self.request("GET", "/api/report?key=goldflow&from=2026-10-01&to=2026-10-31", headers={"Cookie": cookie})
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["report"], "goldflow")
        status, headers, _ = self.request("POST", "/api/logout", "{}", {"X-Requested-With": "masgold", "Cookie": cookie})
        self.assertEqual(status, 200)
        self.assertIn("Max-Age=0", headers["Set-Cookie"])
        self.assertEqual(self.request("GET", "/api/overview", headers={"Cookie": cookie})[0], 401)

    def test_the_write_off_route_needs_the_header_a_session_and_the_right(self):
        body = json.dumps({"customer": "1", "amount": "1", "reason": "x", "password": "p", "expected_balance": "1", "request_key": "k"})
        self.assertEqual(self.request("POST", "/api/deadcash/writeoff", body)[0], 403)                                  # no custom header
        self.assertEqual(self.request("POST", "/api/deadcash/writeoff", body, {"X-Requested-With": "masgold"})[0], 401)  # no session
        self.assertEqual(self.request("GET", "/api/deadcash/context?customer=1")[0], 401)
        _, cookie = self.login()
        status, _, answer = self.request("POST", "/api/deadcash/writeoff", body, {"X-Requested-With": "masgold", "Cookie": cookie})
        self.assertEqual(status, 403)                                                                                    # the real service: this user lacks DEAD_CASH_MANAGE
        self.assertIn("صلاحية", json.loads(answer)["error"])

    def test_an_oversized_body_is_refused_and_the_secure_flag_follows_the_https_header(self):
        status, _, _ = self.request("POST", "/api/login", "x" * 5000, {"X-Requested-With": "masgold", "Content-Length": "5000"})
        self.assertEqual(status, 413)
        status, headers, _ = self.request("POST", "/api/login", json.dumps({"username": "admin", "password": "secret"}),
                                          {"X-Requested-With": "masgold", "X-Forwarded-Proto": "https"})
        self.assertEqual(status, 200)
        self.assertIn("Secure", headers["Set-Cookie"])


if __name__ == "__main__":
    unittest.main()
