"""InvoiceService business flow on an in-memory store (no database).

The fake store models every table the service writes; its transaction factory
snapshots the store and restores it on any exception, like a rollback.
Real SQL, locks and constraints are covered by test_invoice_service_mysql.py.
"""
from contextlib import contextmanager
import copy
import dataclasses
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from types import MappingProxyType
import unittest

from app.session import AppSession
from services.authentication_service import AuthenticationResult, _VERIFIED
from services.gold_price_service import BillingKaratPrice, BillingPriceSet, PriceChanged, PriceUnavailable
from services.invoice_calculator import InvoiceCalculationError
from services.invoice_service import (
    InvoiceDraft, InvoiceError, InvoiceService, OldGoldLineInput, PaymentInput, SaleLineInput,
)


D = Decimal
NOW = datetime(2026, 9, 25, 14, 30, 5)
KARATS = {1: 21, 2: 18, 3: 24}
PRICES = {1: (D("6207.0000"), D("6232.0000")), 2: (D("5320.0000"), D("5341.0000")),
          3: (D("7093.0000"), D("7122.0000"))}


class Store:
    def __init__(self):
        self.employees = {5: {"id": 5, "name": "علي", "is_active": 1},
                          6: {"id": 6, "name": "تامر", "is_active": 0}}
        self.customers = {
            "12": {"customer_id": "12", "customer_code": "C000012", "name": "عميل", "mobile": None,
                   "place": None, "is_active": 1, "record_origin": "NATIVE", "customer_type_name": "Regular"},
            "13": {"customer_id": "13", "customer_code": "C000013", "name": "غير نشط", "mobile": None,
                   "place": None, "is_active": 0, "record_origin": "NATIVE", "customer_type_name": None},
            "977": {"customer_id": "977", "customer_code": "C000977", "name": "عميل قديم غير مسجل #977",
                    "mobile": None, "place": None, "is_active": 1, "record_origin": "LEGACY_PLACEHOLDER",
                    "customer_type_name": None}}
        self.items = {
            "100": {"inventory_item_id": "100", "karat_id": 1, "karat_value": 21, "weight_grams": D("10.500"),
                    "size": None, "stock_classification": "NEW_STOCK", "inventory_status": "IN_STOCK",
                    "item_type_name": "خاتم", "factory_name": None, "category_code": "A", "barcode_value": "01260925"},
            "101": {"inventory_item_id": "101", "karat_id": 2, "karat_value": 18, "weight_grams": D("3.333"),
                    "size": None, "stock_classification": "NEW_STOCK", "inventory_status": "IN_STOCK",
                    "item_type_name": "سلسلة", "factory_name": None, "category_code": "B", "barcode_value": "02260925"},
            "102": {"inventory_item_id": "102", "karat_id": 1, "karat_value": 21, "weight_grams": D("5.000"),
                    "size": None, "stock_classification": "NEW_STOCK", "inventory_status": "SOLD",
                    "item_type_name": "حلق", "factory_name": None, "category_code": "A", "barcode_value": "03260925"}}
        self.ledger, self.warnings = [], {}
        # OTHER needs no bank account: only the V2 allow-list can refuse it.
        self.methods = {"PHYSICAL_CASH": (1, 0), "MOBILE_WALLET": (5, 0), "INSTAPAY": (3, 1), "OTHER": (99, 0)}
        self.invoices, self.snapshots, self.price_snapshots, self.lines = [], [], [], []
        self.payments, self.events, self.inventory_movements, self.old_gold, self.audit = [], [], [], [], []
        self.sequence = {}
        self.sql = []

    def tables(self):
        return {k: v for k, v in vars(self).items() if k != "sql"}


class Connection:
    def __init__(self, store, clock_offset):
        self.store, self.clock_offset, self.in_transaction = store, clock_offset, True
        self.rolled_back = False

    def rollback(self):
        self.rolled_back = True

    def cursor(self, dictionary=False):
        return Cursor(self)


class Cursor:
    def __init__(self, connection):
        self.connection, self.rows = connection, []

    def execute(self, sql, params=()):
        store = self.connection.store
        store.sql.append(sql)
        if "UTC_TIMESTAMP" in sql:
            self.rows = [{"local_now": NOW, "utc_now": NOW - self.connection.clock_offset}]
        elif "FROM karats" in sql:
            self.rows = [{"id": k} for k in sorted(KARATS)]
        elif "FROM employees" in sql:
            employee = store.employees.get(params[0])
            self.rows = [dict(employee)] if employee else []
        elif sql.startswith("INSERT INTO audit_log"):
            store.audit.append(params)
        else:
            raise AssertionError(f"unexpected SQL {sql[:60]}")

    def fetchall(self):
        return self.rows

    def close(self):
        pass


class InvoiceRepo:
    fail_on = None

    def __init__(self, connection):
        self.store = connection.store

    def _fail(self, name):
        if InvoiceRepo.fail_on == name:
            raise RuntimeError(f"injected failure in {name}")

    def lock_customer(self, customer_id):
        row = self.store.customers.get(str(customer_id))
        return dict(row) if row else None

    def _items(self, ids):
        return [dict(self.store.items[str(i)]) for i in sorted(int(x) for x in ids) if str(i) in self.store.items]

    lock_sale_items = read_sale_items = _items

    def get_payment_method(self, code):
        if code not in self.store.methods:
            return None
        method_id, bank = self.store.methods[code]
        return {"payment_method_id": method_id, "code": code, "is_active": 1, "requires_bank_account": bank}

    def next_invoice_number(self, business_date):
        seq = self.store.sequence.get(business_date, 0) + 1
        self.store.sequence[business_date] = seq
        return business_date.strftime("%y%m%d") + f"{seq:02d}"

    def insert_invoice(self, **values):
        self.store.invoices.append(values)
        return str(len(self.store.invoices) + 900)

    def insert_customer_snapshot(self, **values):
        self.store.snapshots.append(values)

    def insert_price_snapshots(self, **values):
        self.store.price_snapshots.extend(values["prices"])

    def insert_sale_line(self, **values):
        self._fail("insert_sale_line")
        self.store.lines.append(("SALE", values))

    def insert_old_gold_line(self, **values):
        self.store.lines.append(("OLD", values))

    def mark_item_sold(self, *, inventory_item_id, updated_by_user_id):
        item = self.store.items[str(inventory_item_id)]
        assert item["inventory_status"] == "IN_STOCK"
        item["inventory_status"], item["updated_by"] = "SOLD", updated_by_user_id

    def add_inventory_sold_movement(self, **values):
        self.store.inventory_movements.append(values)

    def add_customer_buy_in_movement(self, **values):
        self.store.old_gold.append(values)
        return str(700 + len(self.store.old_gold))

    def insert_payment(self, **values):
        self._fail("insert_payment")
        self.store.payments.append(values)

    def insert_event(self, **values):
        self.store.events.append(values)


class CustomerRepo:
    def __init__(self, connection):
        self.store = connection.store

    def get_customer_by_id(self, customer_id):
        return dict(self.store.customers[str(customer_id)]) if str(customer_id) in self.store.customers else None

    def get_balance(self, customer_id):
        return sum((r["amount_delta"] for r in self.store.ledger if r["customer_id"] == int(customer_id)), D("0.00"))

    def add_transaction(self, **values):
        self.store.ledger.append(values)


class DeadCashRepo:
    def __init__(self, connection):
        self.store = connection.store

    def get_active_warning(self, customer_id):
        return self.store.warnings.get(int(customer_id))


class Prices:
    def __init__(self, status="FRESH", token="T1"):
        self.status, self.token, self.locked = status, token, []

    def _set(self, karat_ids):
        prices = {k: BillingKaratPrice(k, KARATS[k], PRICES[k][0], PRICES[k][1], 50 + k, 60 + k,
                                       datetime(2026, 9, 25, 11, 29, 55 + k, tzinfo=timezone.utc))
                  for k in karat_ids}
        return BillingPriceSet(self.token, "AUTO", self.status, ("قديم",) if self.status != "FRESH" else (),
                               self.status != "FRESH", MappingProxyType(prices),
                               MappingProxyType({"source_code": "GOLDTIMO"}))

    def get_billing_price_set(self, karat_ids):
        return self._set(karat_ids)

    def lock_and_validate_for_finalization(self, connection, token, karat_ids, *, acknowledged=False):
        assert connection.in_transaction
        self.locked.append(tuple(karat_ids))
        result = self._set(karat_ids)
        if token != self.token:
            raise PriceChanged("تغيرت أسعار الذهب")
        if result.requires_acknowledgement and acknowledged is not True:
            raise PriceUnavailable("يلزم الإقرار")
        return result


class Authorization:
    """Behaves like AuthorizationService: the actor must be the session's current
    object, and a fresh ActorContext copy is returned (never the same object)."""

    def __init__(self, session, permissions):
        self.session, self.permissions, self.checked = session, permissions, []

    def require_permission(self, connection, actor, code, **kwargs):
        self.checked.append(code)
        if not self.session.is_current(actor):
            raise PermissionError("انتهت جلسة الدخول.")
        if code not in self.permissions:
            raise PermissionError("لا توجد صلاحية")
        return dataclasses.replace(actor)


def sale(item="100", making="150"):
    return SaleLineInput(item, making_per_gram=making)


def old(karat=1, weight="5", **kwargs):
    return OldGoldLineInput(karat, weight, **kwargs)


def cash(amount, method="PHYSICAL_CASH", direction=None):
    return PaymentInput(method, amount, direction)


class InvoiceServiceTestCase(unittest.TestCase):
    permissions = frozenset({"APP_LOGIN", "INVOICE_CREATE", "INVOICE_FINALIZE", "PAYMENT_RECORD"})

    def setUp(self):
        InvoiceRepo.fail_on = None
        self.store = Store()
        self.clock_offset = timedelta(hours=3)
        self.commits = 0
        self.session = AppSession()
        # The logged-in user is linked to employee 7; the invoice's salesperson is chosen (5).
        self.session._establish(AuthenticationResult(17, 7, "موظف", self.permissions, _VERIFIED),
                                int(self.session.generation))
        self.prices = Prices()
        self.authorization = Authorization(self.session, self.permissions)

        self.connections = []

        @contextmanager
        def transaction():
            saved = copy.deepcopy(self.store.tables())
            try:
                connection = Connection(self.store, self.clock_offset)
                self.connections.append(connection)
                yield connection
                self.commits += 1
            except BaseException:
                for key, value in saved.items():
                    setattr(self.store, key, value)
                raise

        self.service = InvoiceService(self.session, gold_prices=self.prices, authorization=self.authorization,
                                      transaction_factory=transaction, invoice_repository=InvoiceRepo,
                                      customer_repository=CustomerRepo, dead_cash_repository=DeadCashRepo)

    def finalize(self, draft, token="T1", **kwargs):
        return self.service.finalize(draft, token, **kwargs)

    def assertNothingWritten(self, before):
        self.assertEqual(self.store.tables(), before)

    def assertRefused(self, code, draft, **kwargs):
        before = copy.deepcopy(self.store.tables())
        with self.assertRaises(InvoiceError) as context:
            self.finalize(draft, **kwargs)
        self.assertEqual(context.exception.code, code)
        self.assertNothingWritten(before)


class ScenarioTests(InvoiceServiceTestCase):
    def test_sale_only_fully_paid_in_cash(self):
        result = self.finalize(InvoiceDraft(5, None, (sale(),), (), (cash("67011"),)))
        self.assertEqual((result["invoice_number"], result["invoice_type"], result["settlement_status"]),
                         ("26092501", "SELL", "SETTLED"))
        self.assertEqual((result["sale_total"], result["net_amount"]), ("67011.00", "67011.00"))
        header = self.store.invoices[0]
        # DB weight 10.500 x SHOP_SELL 6232 + 10.500 x 150 making.
        self.assertEqual((header["sell_total"], header["buy_total"]), (D("67011.00"), D("0.00")))
        self.assertEqual(self.store.items["100"]["inventory_status"], "SOLD")
        self.assertEqual(len(self.store.inventory_movements), 1)
        self.assertEqual([(p["direction"], p["amount"]) for p in self.store.payments], [("IN", D("67011.00"))])
        self.assertEqual((self.store.ledger, self.store.snapshots), ([], []))       # walk-in, nothing owed
        self.assertEqual([e["event_type"] for e in self.store.events], ["CREATED", "FINALIZED"])
        self.assertEqual(self.commits, 1)

    def test_old_gold_purchase_only_partially_paid_leaves_shop_owing(self):
        result = self.finalize(InvoiceDraft(5, "12", (), (old(1, "10", discount_type="PERCENT",
                                                                discount_value="1.5"),),
                                            (cash("50000", "MOBILE_WALLET"),)))
        self.assertEqual((result["invoice_type"], result["net_direction"], result["settlement_status"]),
                         ("BUY", "SHOP_PAYS_CUSTOMER", "PARTIAL"))
        # 10 g x SHOP_BUY 6207 = 62070.00 - 1.5% (931.05) = 61138.95; paid 50000 OUT.
        self.assertEqual(self.store.payments[0]["direction"], "OUT")
        self.assertEqual(self.store.ledger[0]["type_code"], "BUY_BALANCE_DUE")
        self.assertEqual(self.store.ledger[0]["amount_delta"], D("-11138.95"))
        movement = self.store.old_gold[0]
        self.assertEqual((movement["gold_price_per_gram"], movement["cash_value"], movement["price_quote_id"]),
                         (D("6207.0000"), D("61138.95"), 51))
        self.assertEqual(self.store.snapshots[0]["balance_after"], D("-11138.95"))

    def test_mixed_exchange_with_partial_payment(self):
        result = self.finalize(InvoiceDraft(5, "12", (sale(),), (old(1, "5"),), (cash("30000"),)))
        # S 67011.00, B 5 x 6207 = 31035.00, N 35976.00, P 30000, R 5976.00.
        self.assertEqual((result["invoice_type"], result["net_amount"], result["residual_amount"]),
                         ("MIXED", "35976.00", "5976.00"))
        self.assertEqual([(kind, v["line_number"]) for kind, v in self.store.lines], [("SALE", 1), ("OLD", 2)])
        self.assertEqual(self.store.lines[1][1]["old_gold_movement_id"], "701")
        self.assertEqual((self.store.ledger[0]["type_code"], self.store.ledger[0]["amount_delta"]),
                         ("SALE_BALANCE_DUE", D("5976.00")))
        self.assertEqual(self.store.ledger[0]["reference_code"], result["invoice_number"])

    def test_exact_exchange_needs_no_payment_or_customer(self):
        # Sale 10.5 x 6232 + making 2841.00 = 68277.00; old gold 11 x 6207 = 68277.00.
        draft = InvoiceDraft(5, None, (SaleLineInput("100", making_total="2841"),), (old(1, "11"),))
        result = self.finalize(draft)
        self.assertEqual((result["net_direction"], result["net_amount"], result["settlement_status"]),
                         ("SETTLED", "0.00", "SETTLED"))
        self.assertEqual((self.store.payments, self.store.ledger), ([], []))
        self.assertEqual(self.store.invoices[0]["customer_id"], None)

    def test_numbers_follow_the_server_business_date(self):
        first = self.finalize(InvoiceDraft(5, None, (sale("100"),), (), (cash("67011"),)))
        second = self.finalize(InvoiceDraft(5, None, (sale("101", "0"),), (), (cash("17801.55"),)))
        self.assertEqual((first["invoice_number"], second["invoice_number"]), ("26092501", "26092502"))
        self.assertEqual(self.store.invoices[0]["invoice_date"], NOW)


class RuleTests(InvoiceServiceTestCase):
    def test_residual_without_customer_is_rejected(self):
        self.assertRefused("CUSTOMER_REQUIRED", InvoiceDraft(5, None, (sale(),), (), (cash("100"),)))
        self.assertRefused("CUSTOMER_REQUIRED", InvoiceDraft(5, None, (), (old(),), ()))

    def test_inactive_placeholder_and_missing_customers_are_rejected(self):
        for customer, code in (("13", "CUSTOMER_INACTIVE"), ("977", "CUSTOMER_PLACEHOLDER"),
                               ("55", "CUSTOMER_NOT_FOUND")):
            with self.subTest(customer=customer):
                self.assertRefused(code, InvoiceDraft(5, customer, (sale(),), (), (cash("67011"),)))

    def test_flagged_customer_needs_confirmation_for_new_debt_only(self):
        self.store.warnings[12] = {"flag_id": "8"}
        draft = InvoiceDraft(5, "12", (sale(),), (), (cash("60000"),))
        self.assertRefused("DEAD_CASH_CONFIRMATION_REQUIRED", draft)
        result = self.finalize(draft, dead_cash_confirmed=True)
        self.assertEqual(result["residual_amount"], "7011.00")
        self.assertTrue(self.store.snapshots[0]["dead_cash_warning_active"])
        self.assertTrue(self.store.events[1]["details"]["dead_cash_confirmed"])
        # Fully paid: no new debt, no confirmation needed.
        self.finalize(InvoiceDraft(5, "12", (sale("101", "0"),), (), (cash("17801.55"),)))

    def test_unavailable_missing_and_duplicate_items(self):
        self.assertRefused("ITEM_UNAVAILABLE", InvoiceDraft(5, None, (sale("102"),), (), (cash("1"),)))
        self.assertRefused("ITEM_NOT_FOUND", InvoiceDraft(5, None, (sale("555"),), (), (cash("1"),)))

    def test_price_token_and_staleness(self):
        draft = InvoiceDraft(5, None, (sale(),), (), (cash("67011"),))
        before = copy.deepcopy(self.store.tables())
        with self.assertRaises(PriceChanged):
            self.finalize(draft, token="OLD")
        self.prices.status = "STALE"
        with self.assertRaises(PriceUnavailable):
            self.finalize(draft)
        self.assertNothingWritten(before)
        self.finalize(draft, stale_acknowledged=True)
        self.assertTrue(self.store.events[1]["details"]["stale_acknowledged"])
        self.assertEqual(self.prices.locked[-1], (1, 2, 3))                # every karat snapshotted

    def test_only_cash_and_mobile_wallet(self):
        for method in ("INSTAPAY", "BANK_TRANSFER", "CARD_POS", "OTHER"):
            draft = InvoiceDraft(5, None, (sale(),), (), (cash("67011", method),))
            with self.subTest(method=method):
                self.assertRefused("PAYMENT_METHOD_NOT_ALLOWED", draft)
                with self.assertRaises(InvoiceError) as context:           # preview refuses too
                    self.service.preview(draft)
                self.assertEqual(context.exception.code, "PAYMENT_METHOD_NOT_ALLOWED")
        self.finalize(InvoiceDraft(5, None, (sale(),), (), (cash("67011", "MOBILE_WALLET"),)))

    def test_active_salesperson_is_required(self):
        for employee, code in ((None, "SALESPERSON_REQUIRED"), (6, "SALESPERSON_INACTIVE"),
                               (99, "SALESPERSON_INACTIVE")):
            with self.subTest(employee=employee):
                self.assertRefused(code, InvoiceDraft(employee, None, (sale(),), (), (cash("67011"),)))

    def test_calculation_errors_write_nothing(self):
        before = copy.deepcopy(self.store.tables())
        for draft in (InvoiceDraft(5, None, (sale(),), (), (cash("67011.001"),)),        # precision
                      InvoiceDraft(5, None, (SaleLineInput("100"),), (), ()),             # making required
                      InvoiceDraft(5, None, (sale(),), (), (cash("70000"),)),             # overpayment
                      InvoiceDraft(5, "12", (), (old(1, "1.0005"),), ())):                # weight precision
            with self.subTest(draft=draft), self.assertRaises(InvoiceCalculationError):
                self.finalize(draft)
        self.assertNothingWritten(before)

    def test_empty_invoice_and_missing_price(self):
        with self.assertRaises(InvoiceError):
            self.finalize(InvoiceDraft(5, None, (), (), ()))
        self.assertRefused("PRICE_MISSING", InvoiceDraft(5, "12", (), (old(karat=9),), ()))

    def test_server_clock_must_be_egypt_time(self):
        self.clock_offset = timedelta(0)
        self.assertRefused("CLOCK_NOT_EGYPT", InvoiceDraft(5, None, (sale(),), (), (cash("67011"),)))
        self.clock_offset = timedelta(hours=2)                              # winter time is fine
        self.finalize(InvoiceDraft(5, None, (sale(),), (), (cash("67011"),)))


class AtomicityAndAttributionTests(InvoiceServiceTestCase):
    def test_injected_failure_rolls_back_every_table(self):
        for failing in ("insert_sale_line", "insert_payment"):
            InvoiceRepo.fail_on = failing
            before = copy.deepcopy(self.store.tables())
            with self.subTest(failing=failing), self.assertRaisesRegex(RuntimeError, "injected"):
                self.finalize(InvoiceDraft(5, "12", (sale(),), (old(),), (cash("30000"),)))
            self.assertNothingWritten(before)
            self.assertEqual(self.store.items["100"]["inventory_status"], "IN_STOCK")
        self.assertEqual(self.commits, 0)

    def test_actor_and_salesperson_are_recorded_separately(self):
        self.finalize(InvoiceDraft(5, "12", (sale(),), (old(),), (cash("30000"),)))
        header = self.store.invoices[0]
        self.assertEqual((header["created_by_user_id"], header["employee_id"]), (17, 5))
        self.assertEqual(self.store.inventory_movements[0]["performed_by_user_id"], 17)
        self.assertEqual(self.store.inventory_movements[0]["performed_by_employee_id"], 5)
        self.assertEqual((self.store.old_gold[0]["created_by_user_id"],
                          self.store.old_gold[0]["received_by_employee_id"]), (17, 5))
        self.assertEqual(self.store.items["100"]["updated_by"], 17)
        self.assertEqual({p["created_by_user_id"] for p in self.store.payments}, {17})
        self.assertEqual(self.store.ledger[0]["created_by_user_id"], 17)
        self.assertEqual({e["performed_by_user_id"] for e in self.store.events}, {17})
        self.assertEqual(self.store.audit[0][:3], (17, "INVOICE_FINALIZED", "INVOICE"))
        details = self.store.events[1]["details"]
        self.assertEqual((details["salesperson_employee_id"], details["actor_employee_id"]), (5, 7))
        self.assertEqual(self.authorization.checked, ["INVOICE_FINALIZE", "PAYMENT_RECORD"])

    def test_permission_and_session_are_enforced(self):
        self.authorization.permissions = frozenset({"APP_LOGIN"})
        with self.assertRaises(PermissionError):
            self.finalize(InvoiceDraft(5, None, (sale(),), (), (cash("67011"),)))
        self.session.logout()
        with self.assertRaises(PermissionError):
            self.finalize(InvoiceDraft(5, None, (sale(),), (), (cash("67011"),)))
        self.assertEqual(self.store.invoices, [])

    def test_no_cash_or_bank_movements_are_written(self):
        self.finalize(InvoiceDraft(5, "12", (sale(),), (old(),), (cash("30000"),)))
        self.assertFalse([sql for sql in self.store.sql if "cash_movements" in sql or "bank_movements" in sql])
        source = Path("services/invoice_service.py").read_text(encoding="utf-8")
        self.assertNotRegex(source, r"INSERT INTO (cash|bank)_movements")

    def test_price_snapshots_keep_each_karat_provider_time_in_utc(self):
        self.finalize(InvoiceDraft(5, None, (sale(),), (), (cash("67011"),)))
        times = {p["karat_id"]: p["provider_timestamp"] for p in self.store.price_snapshots}
        self.assertEqual(times, {1: datetime(2026, 9, 25, 11, 29, 56), 2: datetime(2026, 9, 25, 11, 29, 57),
                                 3: datetime(2026, 9, 25, 11, 29, 58)})
        self.assertEqual({p["quote_id"] for p in self.store.price_snapshots}, {51, 52, 53})


class PreviewTests(InvoiceServiceTestCase):
    def test_preview_recalculates_and_writes_nothing(self):
        self.store.warnings[12] = {"flag_id": "8"}
        before = copy.deepcopy(self.store.tables())
        preview = self.service.preview(InvoiceDraft(5, "12", (sale(),), (old(1, "5"),), (cash("30000"),)))
        self.assertNothingWritten(before)
        self.assertEqual(self.commits, 0)                                     # rolled back, never committed
        self.assertEqual((preview["price_token"], preview["sale_total"], preview["old_gold_total"]),
                         ("T1", "67011.00", "31035.00"))
        self.assertEqual(preview["sale_lines"][0]["weight_grams"], "10.500")        # from the database
        self.assertEqual(preview["sale_lines"][0]["gold_price_per_gram"], "6232.0000")
        self.assertEqual(preview["old_gold_lines"][0]["gold_price_per_gram"], "6207.0000")
        self.assertEqual((preview["customer_balance_before"], preview["customer_balance_after"]),
                         ("0.00", "5976.00"))
        self.assertTrue(preview["requires_dead_cash_confirmation"])
        self.assertEqual(preview["blocking_problems"], [])

    def test_preview_reports_customer_requirement(self):
        preview = self.service.preview(InvoiceDraft(5, None, (sale(),), (), ()))
        self.assertEqual([p["code"] for p in preview["blocking_problems"]], ["CUSTOMER_REQUIRED"])

    def test_draft_cannot_carry_totals(self):
        with self.assertRaises(TypeError):
            InvoiceDraft(5, None, (), (), (), sale_total="1")
        with self.assertRaises(TypeError):
            SaleLineInput("100", making_total="0", weight_grams="99")


if __name__ == "__main__":
    unittest.main()
