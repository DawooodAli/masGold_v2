"""سجل الفواتير V1: read-only history search, detail (as finalized), timeline and reprint.

The service reads through a fake repository (it records every call, and has no write methods at
all); the ViewModel runs over a static service. A live read-only check of the repository SQL
runs only with MASGOLD_LIVE_READONLY_TESTS=1 (a rolled-back transaction on the dev database).
"""
from contextlib import contextmanager
from datetime import date, datetime
from decimal import Decimal
import inspect
import json
import os
import unittest

from PySide6.QtGui import QGuiApplication
from PySide6.QtTest import QTest

from app.session import AppSession
from app.viewmodels.invoice_history_viewmodel import InvoiceHistoryViewModel
from services.authentication_service import AuthenticationResult, _VERIFIED
from services.invoice_history_service import InvoiceHistoryService, LifecycleEvent

D = Decimal
EMPLOYEE = frozenset({"APP_LOGIN", "INVOICE_VIEW", "INVOICE_REPRINT"})
MANAGER = EMPLOYEE | {"MANAGEMENT_REVIEW"}
FINALIZED_AT = datetime(2026, 9, 27, 21, 51, 10)


def summary_row(number, when, **overrides):
    row = {"invoice_id": number[-2:], "invoice_number": number, "invoice_type": "SELL", "invoice_status": "FINALIZED",
           "record_origin": "NATIVE", "invoice_date": when, "finalized_at": when, "net_amount": D("90000.00"),
           "net_direction": "CUSTOMER_PAYS_SHOP", "settlement_status": "PARTIAL", "sell_total": D("90000.00"),
           "buy_total": D("0.00"), "legacy_bill_number": None, "customer_name_snapshot": "على عبدالرحمن",
           "mobile_snapshot": "01026018015", "employee_name": "علي", "money_received": D("89000.00"),
           "money_paid": D("0.00")}
    row.update(overrides)
    return row


def invoice(**overrides):
    """26092701 as finalized: 90,000 total = 80,000 cash + 9,000 wallet + 1,000 on the account."""
    details = {"L": "1000.00", "P": "89000.00", "N": "90000.00", "effective_sale_total": "90000.00",
               "gold_value_total": "83722.62", "making_total": "6277.38", "average_making_per_gram": "467.41",
               "making_status": "WITHIN_RANGE"}
    row = {
        "invoice_id": "21", "invoice_number": "26092701", "invoice_type": "SELL", "invoice_status": "FINALIZED",
        "record_origin": "NATIVE", "customer_id": "12", "employee_id": "5", "employee_name": "علي",
        "invoice_date": FINALIZED_AT, "finalized_at": FINALIZED_AT, "sell_total": D("90000.00"),
        "buy_total": D("0.00"), "net_amount": D("90000.00"), "net_direction": "CUSTOMER_PAYS_SHOP",
        "settlement_status": "PARTIAL", "notes": None, "finalized_by_name": "المالك",
        "customer_code_snapshot": "C000012", "customer_name_snapshot": "على عبدالرحمن",
        "mobile_snapshot": "01026018015", "place_snapshot": "القناطر",
        "balance_before": D("0.00"), "balance_after": D("1000.00"),
        "lines": [
            {"line_id": "51", "line_number": 1, "line_type": "SELL_INVENTORY", "identification_method": "SCAN",
             "inventory_item_id": "100", "karat_value": 21, "weight_grams": D("10.000"), "quantity": 1,
             "item_name_snapshot": "خاتم", "category_snapshot": "خواتم", "factory_snapshot": "لوكس",
             "size_snapshot": "18", "barcode_snapshot": "01260925", "gold_price_per_gram": D("6234.0000"),
             "making_total": D("4000.00"), "line_total": D("66340.00"), "notes": None},
            {"line_id": "52", "line_number": 2, "line_type": "SELL_UNIDENTIFIED", "identification_method": "MANUAL",
             "inventory_item_id": None, "karat_value": 21, "weight_grams": D("2.430"), "quantity": 1,
             "item_name_snapshot": "حلق", "category_snapshot": None, "factory_snapshot": None,
             "size_snapshot": None, "barcode_snapshot": None, "gold_price_per_gram": D("6234.0000"),
             "making_total": D("1277.38"), "line_total": D("16425.00"), "notes": "الباركود مفقود"},
            {"line_id": "53", "line_number": 3, "line_type": "SELL_OLD_GOLD", "identification_method": None,
             "inventory_item_id": None, "karat_value": 21, "weight_grams": D("1.000"), "quantity": 1,
             "item_name_snapshot": "دبلة", "category_snapshot": None, "factory_snapshot": None,
             "size_snapshot": None, "barcode_snapshot": None, "gold_price_per_gram": D("6234.0000"),
             "making_total": D("1000.00"), "line_total": D("7235.00"), "notes": None},
            {"line_id": "54", "line_number": 4, "line_type": "BUY_OLD_GOLD", "karat_value": 18,
             "weight_grams": D("2.000"), "quantity": 1, "gold_price_per_gram": D("5320.0000"),
             "gross_total": D("10640.00"), "discount_type": "PERCENT", "discount_value": D("1.50"),
             "discount_amount": D("159.60"), "line_total": D("10480.40"), "notes": None}],
        "payments": [
            {"payment_id": "71", "payment_method_code": "PHYSICAL_CASH", "payment_method_name": "نقدي",
             "direction": "IN", "amount": D("80000.00"), "occurred_at": FINALIZED_AT},
            {"payment_id": "72", "payment_method_code": "MOBILE_WALLET", "payment_method_name": "محفظة",
             "direction": "IN", "amount": D("9000.00"), "occurred_at": FINALIZED_AT}],
        "price_snapshots": [{"karat_value": 21, "shop_buy_price_per_gram": D("6207.0000"),
                             "shop_sell_price_per_gram": D("6234.0000"), "source_code_snapshot": "GOLDTIMO",
                             "provider_timestamp": FINALIZED_AT}],
        "events": [
            {"event_id": "302", "event_type": "FINALIZED", "occurred_at": FINALIZED_AT, "performed_by_name": "المالك",
             "related_invoice_id": None, "details_json": json.dumps(details)},
            {"event_id": "301", "event_type": "CREATED", "occurred_at": FINALIZED_AT, "performed_by_name": "المالك",
             "related_invoice_id": None, "details_json": None}],
    }
    row.update(overrides)
    return row


class FakeRepo:
    """Read-only by construction: it has no write methods; every call is recorded."""

    def __init__(self, state):
        self.state = state

    def search_invoice_history(self, **kwargs):
        self.state["calls"].append(("search", kwargs))
        return self.state["rows"][kwargs["offset"]:kwargs["offset"] + kwargs["limit"]]

    def get_invoice(self, invoice_id):
        self.state["calls"].append(("get_invoice", invoice_id))
        row = self.state["invoices"].get(str(invoice_id))
        return None if row is None else json.loads(json.dumps(row, default=str), parse_float=D) and dict(row)

    def get_payment_accounts(self, invoice_id):
        return [{"payment_id": "72", "bank_account_id": "9", "bank_account_name": "محفظة المحل"}]

    def invoice_account_allocation(self, invoice_id, customer_id):
        return self.state["ledger"].get(str(invoice_id), [])


class FakePrint:
    def __init__(self, printed=True):
        self.calls, self.printed = [], printed

    def print_invoice(self, invoice_id, *, _expected_actor=None):
        self.calls.append(invoice_id)
        return type("Outcome", (), {"printed": self.printed,
                                    "message": "ok" if self.printed else "الطابعة غير متصلة."})()


def make(permissions=EMPLOYEE, rows=None, page_size=50, printed=True):
    session = AppSession()
    session._establish(AuthenticationResult(1, 5, "موظف", permissions, _VERIFIED), int(session.generation))
    state = {"calls": [], "rows": rows or [], "invoices": {"21": invoice()},
             "ledger": {"21": [{"transaction_id": "6", "amount_delta": D("1000.00"), "type_code": "SALE_BALANCE_DUE"}]}}

    @contextmanager
    def transaction():
        yield state
    printer = FakePrint(printed)
    service = InvoiceHistoryService(session, transaction_factory=transaction, invoice_repository=FakeRepo,
                                    page_size=page_size, print_service=printer)
    return service, state, printer


class HistorySearchTests(unittest.TestCase):
    def rows(self, count=3):
        return [summary_row(f"260927{i:02d}", datetime(2026, 9, 27, 20 - i, 0)) for i in range(count)]

    def test_newest_first_order_is_kept_and_summary_has_the_three_money_meanings(self):
        service, state, _ = make(rows=self.rows())
        result = service.search()
        self.assertEqual([r["invoice_number"] for r in result["items"]], ["26092700", "26092701", "26092702"])
        item = result["items"][0]
        self.assertEqual((item["invoice_total"], item["money_moved"]), (D("90000.00"), D("89000.00")))
        self.assertEqual(state["calls"][0][1]["offset"], 0)

    def test_filters_reach_the_repository_and_date_to_is_inclusive(self):
        service, state, _ = make()
        service.search(text="2609", date_from=date(2026, 9, 1), date_to=date(2026, 9, 27), invoice_type="MIXED",
                       settlement_status="PARTIAL")
        kwargs = state["calls"][0][1]
        self.assertEqual((kwargs["text"], kwargs["date_from"], kwargs["date_to_exclusive"], kwargs["invoice_type"],
                          kwargs["settlement_status"]),
                         ("2609", date(2026, 9, 1), date(2026, 9, 28), "MIXED", "PARTIAL"))

    def test_pagination_composes_with_filters(self):
        service, state, _ = make(rows=self.rows(5), page_size=2)
        first = service.search(text="على")
        second = service.search(text="على", page=2)
        last = service.search(text="على", page=3)
        self.assertEqual((len(first["items"]), first["has_more"], second["has_more"], len(last["items"]),
                          last["has_more"]), (2, True, True, 1, False))
        self.assertEqual([c[1]["offset"] for c in state["calls"]], [0, 2, 4])
        self.assertTrue(all(c[1]["text"] == "على" for c in state["calls"]))

    def test_no_results_and_invalid_filters(self):
        service, _, _ = make(rows=[])
        self.assertEqual(service.search(text="999")["items"], [])
        with self.assertRaises(ValueError):
            service.search(date_from=date(2026, 9, 28), date_to=date(2026, 9, 1))
        with self.assertRaises(ValueError):
            service.search(invoice_type="EXCHANGE")

    def test_history_needs_the_invoice_view_permission(self):
        service, _, _ = make(permissions=frozenset({"APP_LOGIN"}))
        with self.assertRaises(PermissionError):
            service.search()


class HistoryDetailTests(unittest.TestCase):
    def test_identity_time_customer_and_salesperson_as_finalized(self):
        d = make()[0].detail("21")
        self.assertEqual((d["invoice_number"], d["finalized_at"], d["salesperson"], d["read_only"]),
                         ("26092701", FINALIZED_AT, "علي", True))
        self.assertEqual(d["customer"], {"customer_id": "12", "code": "C000012", "name": "على عبدالرحمن",
                                         "mobile": "01026018015", "place": "القناطر"})

    def test_all_three_sale_kinds_are_listed_with_the_ids_a_future_exchange_needs(self):
        lines = make()[0].detail("21")["sale_lines"]
        self.assertEqual([(l["line_id"], l["kind"], l["inventory_item_id"]) for l in lines],
                         [("51", "INVENTORY", "100"), ("52", "MANUAL", None), ("53", "OLD_GOLD_STOCK", None)])
        first = lines[0]
        self.assertEqual((first["category"], first["factory"], first["karat"], first["weight"], first["quantity"],
                          first["gold_price"], first["gold_value"], first["making_total"]),
                         ("خواتم", "لوكس", 21, D("10.000"), 1, D("6234.0000"), D("62340.00"), D("4000.00")))
        self.assertEqual(lines[1]["notes"], "الباركود مفقود")

    def test_customer_old_gold_is_a_separate_internal_section(self):
        d = make()[0].detail("21")
        self.assertEqual([(l["karat"], l["weight"], l["discount_value"], l["line_total"]) for l in d["old_gold_lines"]],
                         [(18, D("2.000"), D("1.50"), D("10480.40"))])
        self.assertNotIn("54", [l["line_id"] for l in d["sale_lines"]])

    def test_the_three_money_meanings_are_never_collapsed(self):
        d = make()[0].detail("21")
        self.assertEqual((d["invoice_total"], d["money_received"], d["account_signed"]),
                         (D("90000.00"), D("89000.00"), D("1000.00")))
        self.assertEqual([(p["method_code"], p["amount"], p["account_name"]) for p in d["payments"]],
                         [("PHYSICAL_CASH", D("80000.00"), None), ("MOBILE_WALLET", D("9000.00"), "محفظة المحل")])

    def test_making_is_the_saved_value_never_recalculated_from_payments(self):
        """Different stored payment rows for the same finalized invoice: the saved making stays."""
        service, state, _ = make(permissions=MANAGER)
        base = service.detail("21")["economics"]
        state["invoices"]["21"]["payments"] = [{"payment_id": "71", "payment_method_code": "PHYSICAL_CASH",
                                                 "payment_method_name": "نقدي", "direction": "IN",
                                                 "amount": D("40000.00"), "occurred_at": FINALIZED_AT}]
        again = service.detail("21")["economics"]
        self.assertEqual(base, again)
        self.assertEqual((again["gold_value"], again["effective_sale"], again["making_total"], again["average_making"]),
                         (D("83722.62"), D("90000.00"), D("6277.38"), D("467.41")))

    def test_total_making_is_manager_only_average_is_for_everyone_with_history(self):
        employee = make(permissions=EMPLOYEE)[0].detail("21")
        manager = make(permissions=MANAGER)[0].detail("21")
        self.assertIsNone(employee["economics"]["making_total"])
        self.assertEqual(employee["economics"]["average_making"], D("467.41"))
        self.assertEqual(manager["economics"]["making_total"], D("6277.38"))
        self.assertEqual((employee["can_see_manager_details"], manager["can_see_manager_details"]), (False, True))

    def test_historical_price_snapshot_and_missing_economics_stay_unknown(self):
        service, state, _ = make()
        d = service.detail("21")
        self.assertEqual([(p["karat"], p["sell"], p["buy"]) for p in d["price_snapshots"]],
                         [(21, D("6234.0000"), D("6207.0000"))])
        state["invoices"]["21"] = invoice(events=[], price_snapshots=[])        # an older invoice: nothing saved
        old = service.detail("21")
        self.assertEqual((old["economics"]["gold_value"], old["economics"]["average_making"], old["price_snapshots"]),
                         (None, None, []))

    def test_account_part_falls_back_to_the_saved_L_without_a_ledger_row(self):
        service, state, _ = make()
        state["ledger"] = {}
        self.assertEqual(service.detail("21")["account_signed"], D("1000.00"))

    def test_timeline_is_structured_and_deterministic(self):
        timeline = make()[0].detail("21")["timeline"]
        self.assertTrue(all(isinstance(e, LifecycleEvent) for e in timeline))
        self.assertEqual([(e.event_type, e.actor, e.occurred_at) for e in timeline],
                         [("CREATED", "المالك", FINALIZED_AT), ("FINALIZED", "المالك", FINALIZED_AT)])

    def test_missing_invoice(self):
        with self.assertRaises(ValueError):
            make()[0].detail("999")


class HistoryReprintAndImmutabilityTests(unittest.TestCase):
    def test_reprint_uses_the_frozen_print_service_with_the_original_invoice(self):
        service, state, printer = make()
        outcome = service.reprint("21")
        self.assertTrue(outcome.printed)
        self.assertEqual(printer.calls, ["21"])
        self.assertFalse(any(name == "search" for name, _ in state["calls"]))

    def test_reprint_needs_its_permission(self):
        service, _, printer = make(permissions=frozenset({"APP_LOGIN", "INVOICE_VIEW"}))
        with self.assertRaises(PermissionError):
            service.reprint("21")
        self.assertEqual(printer.calls, [])

    def test_a_printer_failure_changes_nothing(self):
        service, state, _ = make(printed=False)
        before = json.dumps(state["invoices"], default=str, sort_keys=True)
        self.assertFalse(service.reprint("21").printed)
        self.assertEqual(json.dumps(state["invoices"], default=str, sort_keys=True), before)

    def test_history_has_no_mutation_path(self):
        public = {name for name, _ in inspect.getmembers(InvoiceHistoryService, inspect.isfunction)
                  if not name.startswith("_")}
        self.assertEqual(public, {"search", "detail", "reprint"})
        self.assertFalse([name for name in dir(FakeRepo) if name.startswith(("update", "insert", "delete", "set"))])


class StaticHistory:
    def __init__(self):
        self.calls = []
        service, _, self.printer = make(permissions=MANAGER, rows=[summary_row("26092701", FINALIZED_AT,
                                                                              invoice_id="21")])
        self.real = service

    def search(self, **kwargs):
        self.calls.append(("search", {k: v for k, v in kwargs.items() if k != "_expected_actor"}))
        return self.real.search(**{k: v for k, v in kwargs.items() if k != "_expected_actor"})

    def detail(self, invoice_id, **kwargs):
        self.calls.append(("detail", invoice_id))
        return self.real.detail(invoice_id)

    def reprint(self, invoice_id, **kwargs):
        self.calls.append(("reprint", invoice_id))
        return self.real.reprint(invoice_id)


class HistoryViewModelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        cls.app = QGuiApplication.instance() or QGuiApplication([])

    def setUp(self):
        session = AppSession()
        session._establish(AuthenticationResult(1, 5, "مدير", MANAGER, _VERIFIED), int(session.generation))
        self.service = StaticHistory()
        self.vm = InvoiceHistoryViewModel(session, service=self.service, debounce_ms=10,
                                          clock=lambda: datetime(2026, 9, 27, 22, 0))
        self.addCleanup(self.vm.shutdown)

    def wait(self, predicate):
        for _ in range(300):
            if predicate():
                return
            QTest.qWait(10)
        self.fail("timed out")

    def test_list_rows_are_formatted_with_total_and_real_money_apart(self):
        self.vm.activate()
        self.wait(lambda: self.vm.results)
        row = self.vm.results[0]
        self.assertEqual((row["number"], row["date"], row["time"], row["total"], row["moved"], row["statusText"],
                          row["typeText"]),
                         ("26092701", "⁦2026-09-27⁩", "21:51", "90,000.00 ج", "مستلم 89,000.00 ج",
                          "مسددة جزئياً", "بيع"))

    def test_typing_is_debounced_into_one_search(self):
        for text in ("2", "26", "2609", "26092701"):
            self.vm.setSearchText(text)
        self.wait(lambda: self.service.calls)
        QTest.qWait(60)
        self.assertEqual([c[1]["text"] for c in self.service.calls if c[0] == "search"], ["26092701"])

    def test_today_and_filters(self):
        self.vm.setToday()
        self.wait(lambda: self.service.calls)
        self.assertEqual((self.service.calls[-1][1]["date_from"], self.service.calls[-1][1]["date_to"]),
                         (date(2026, 9, 27), date(2026, 9, 27)))
        self.vm.setTypeFilter("MIXED")
        self.vm.setStatusFilter("PARTIAL")
        # Two searches run on worker threads; their calls may be recorded in either order.
        self.wait(lambda: any(c[1].get("settlement_status") == "PARTIAL" and c[1].get("invoice_type") == "MIXED"
                              for c in self.service.calls if c[0] == "search"))
        self.vm.setDateRange("27-09-2026", "")
        self.assertIn("2026-09-27", self.vm.errorMessage)

    def test_empty_states(self):
        self.service.real = make(rows=[])[0]
        self.vm.activate()
        self.wait(lambda: self.service.calls and not self.vm.busy)
        self.assertEqual(self.vm.emptyText, "لا توجد فواتير")
        self.vm.setTypeFilter("BUY")
        self.wait(lambda: not self.vm.busy and self.vm.filtered)
        self.assertEqual(self.vm.emptyText, "لا توجد نتائج مطابقة")

    def test_detail_formatting_sections_and_timeline(self):
        self.vm.openInvoice("21")
        self.wait(lambda: self.vm.detail)
        d = self.vm.detail
        self.assertEqual((d["number"], d["finalizedAt"], d["invoiceTotal"], d["movedLabel"], d["movedAmount"],
                          d["accountText"], d["goldValue"], d["averageMaking"], d["makingTotal"]),
                         ("26092701", "⁦2026-09-27 21:51⁩", "90,000.00 ج", "المستلم من العميل",
                          "89,000.00 ج", "على العميل: 1,000.00 ج", "83,722.62 ج", "467.41 ج/جم", "6,277.38 ج"))
        self.assertEqual([l["kindText"] for l in self.vm.saleLines],
                         ["من المخزون — بالباركود", "بيع يدوي (غير محدد في المخزون)", "من رصيد الكسر"])
        self.assertEqual([p["method"] for p in self.vm.payments], ["نقدي", "محفظة — محفظة المحل"])
        self.assertEqual([e["text"] for e in self.vm.timeline], ["تم إنشاء الفاتورة", "تم حفظ الفاتورة نهائياً"])
        self.assertEqual(self.vm.priceSnapshots[0]["sell"], "6,234.00")
        self.vm.closeDetail()
        self.assertFalse(self.vm.detailOpen)

    def test_reprint_reports_success_and_failure_and_is_read_only(self):
        self.vm.openInvoice("21")
        self.wait(lambda: self.vm.detail)
        self.vm.reprint()
        self.wait(lambda: self.vm.reprintMessage)
        self.assertEqual((self.vm.reprintMessage, self.vm.reprintFailed), ("تم إرسال الفاتورة إلى الطابعة.", False))
        self.service.real._print_service.printed = False
        self.vm.reprint()
        self.wait(lambda: self.vm.reprintFailed)
        self.assertEqual(self.vm.reprintMessage, "الطابعة غير متصلة.")
        self.assertEqual([c for c in self.service.calls if c[0] == "reprint"], [("reprint", "21"), ("reprint", "21")])


@unittest.skipUnless(os.getenv("MASGOLD_LIVE_READONLY_TESTS") == "1", "live read-only check (set MASGOLD_LIVE_READONLY_TESTS=1)")
class LiveRepositoryReadOnlyTests(unittest.TestCase):
    """The real SQL on the dev database, inside a transaction that is always rolled back."""

    def test_search_and_detail_queries(self):
        from dotenv import load_dotenv
        load_dotenv(".env")
        from repositories.invoice_repository import InvoiceRepository
        from services.invoice_service import invoice_transaction
        with invoice_transaction() as connection:
            try:
                repo = InvoiceRepository(connection)
                rows = repo.search_invoice_history(limit=51)
                dates = [r["invoice_date"] for r in rows]
                self.assertEqual(dates, sorted(dates, reverse=True))                     # newest first
                if rows:
                    number = rows[0]["invoice_number"]
                    self.assertEqual([r["invoice_number"] for r in repo.search_invoice_history(text=number)], [number])
                    self.assertTrue(all(r["invoice_number"].startswith(number[:6])
                                        or (r["mobile_snapshot"] or "").startswith(number[:6])
                                        for r in repo.search_invoice_history(text=number[:6])))
                    self.assertEqual(repo.search_invoice_history(text="لا يوجد عميل بهذا الاسم أبداً"), [])
                    page1 = repo.search_invoice_history(limit=2, offset=0)
                    page2 = repo.search_invoice_history(limit=2, offset=2)
                    self.assertFalse({r["invoice_id"] for r in page1} & {r["invoice_id"] for r in page2})
                    self.assertEqual({r["invoice_type"] for r in repo.search_invoice_history(invoice_type="SELL")}
                                     - {"SELL"}, set())
            finally:
                connection.rollback()


if __name__ == "__main__":
    unittest.main()
