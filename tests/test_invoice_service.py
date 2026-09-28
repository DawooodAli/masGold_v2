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
    DescribedSaleInput, InvoiceDraft, InvoiceError, InvoiceService, MAX_SALE_ITEMS, OldGoldLineInput, PaymentInput,
    SaleLineInput,
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
        # code -> (id, requires_bank_account, affects_physical_cash), as after migration 017.
        self.methods = {"PHYSICAL_CASH": (1, 0, 1), "MOBILE_WALLET": (5, 1, 0), "INSTAPAY": (3, 1, 0),
                        "BANK_TRANSFER": (2, 1, 0), "CARD_POS": (4, 1, 0), "OTHER": (99, 0, 0)}
        # Configured accounts (never invented by the service): a bank, a closed bank, a wallet.
        self.bank_accounts = {7: {"id": 7, "name": "حساب المحل", "account_type": "BANK", "bank_name": "CIB",
                                  "account_label": "جاري", "is_active": 1},
                              8: {"id": 8, "name": "حساب مغلق", "account_type": "BANK", "bank_name": "NBE",
                                  "account_label": "", "is_active": 0},
                              9: {"id": 9, "name": "محفظة المحل", "account_type": "MOBILE_WALLET",
                                  "bank_name": "فودافون كاش", "account_label": "", "is_active": 1}}
        self.cash_accounts = [{"id": 1, "code": "MAIN_SAFE", "name": "Main Safe"}]
        # The real money ledgers (007): signed amount per payment.
        self.cash_movements, self.bank_movements = [], []
        self.invoices, self.snapshots, self.price_snapshots, self.lines = [], [], [], []
        self.payments, self.events, self.inventory_movements, self.old_gold, self.audit = [], [], [], [], []
        self.sequence = {}
        # 016: reference data and the new workflow tables.
        self.item_types = {2: {"id": 2, "name": "خاتم", "default_category_id": 1, "is_active": 1},
                           4: {"id": 4, "name": "دبلة", "default_category_id": None, "is_active": 1}}
        self.factories = {5: {"id": 5, "name": "لوكس", "is_active": 1}}
        self.categories = {1: {"id": 1, "code": "A", "name": "A", "sale_making_min_per_gram": None,
                               "sale_making_max_per_gram": None}}
        self.settings = {"settlement_self_limit": D("50.00"), "settlement_approval_limit": D("500.00"),
                         "old_gold_discount_default_percent": D("1.50")}
        self.old_gold_opening = {1: D("100.000"), 2: D("0.000"), 3: D("0.000")}
        self.cases, self.case_events, self.adjustments, self.reviews, self.new_customers = [], [], [], [], []
        self.locked_karats = []
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

    # ---- 016 reads
    def billing_settings(self):
        return dict(self.store.settings)

    def category_ranges(self, category_ids):
        return {int(i): dict(self.store.categories[int(i)]) for i in category_ids
                if i is not None and int(i) in self.store.categories}

    def get_item_type(self, item_type_id):
        row = self.store.item_types.get(int(item_type_id))
        return dict(row) if row else None

    def get_factory(self, factory_id):
        row = self.store.factories.get(int(factory_id))
        return dict(row) if row else None

    def reference_lists(self):
        return {"item_types": list(self.store.item_types.values()), "factories": list(self.store.factories.values())}

    def _counted(self, karat_id):
        moved = sum((m["signed_weight"] for m in self.store.old_gold if m["karat_id"] == karat_id), D("0.000"))
        return self.store.old_gold_opening.get(karat_id, D("0.000")) + moved

    def old_gold_balances(self):
        return [{"karat_id": k, "karat_value": KARATS[k], "balance_grams": self._counted(k)} for k in sorted(KARATS)]

    def lock_old_gold_balance(self, karat_id):
        self.store.locked_karats.append(int(karat_id))
        return self._counted(int(karat_id))

    def sellable_facets(self, *, item_type_id=None, karat_id=None, exclude_ids=()):
        excluded = {str(int(i)) for i in exclude_ids}
        stock = [i for i in self.store.items.values()
                 if i["inventory_status"] == "IN_STOCK" and i["inventory_item_id"] not in excluded]

        def count(rows, key, name):
            out = {}
            for row in rows:
                if row.get(key) is None:
                    continue
                entry = out.setdefault(row[key], {"id": row[key], "name": row[name], "item_count": 0})
                entry["item_count"] += 1
            return sorted(out.values(), key=lambda r: str(r["name"]))
        types = count(stock, "item_type_id", "item_type_name")
        karats = (count([i for i in stock if i.get("item_type_id") == item_type_id], "karat_id", "karat_value")
                  if item_type_id else [])
        factories = (count([i for i in stock if i.get("item_type_id") == item_type_id and i["karat_id"] == karat_id],
                           "factory_id", "factory_name") if item_type_id and karat_id else [])
        return {"item_types": types, "karats": karats, "factories": factories}

    # ---- 016 writes
    def add_invoice_sale_out_movement(self, **values):
        self.store.old_gold.append({**values, "signed_weight": -values["weight_grams"], "code": "INVOICE_SALE_OUT"})
        return str(700 + len(self.store.old_gold))

    def insert_described_sale_line(self, **values):
        self._fail("insert_described_sale_line")
        self.store.lines.append((values["line_type"], values))
        return str(800 + len(self.store.lines))

    def open_unidentified_sale_case(self, **values):
        self.store.cases.append(values)
        self.store.case_events.append({"case": len(self.store.cases), "event_type": "OPENED"})
        return str(600 + len(self.store.cases))

    def insert_settlement_adjustment(self, **values):
        self.store.adjustments.append(values)

    def insert_management_review(self, **values):
        self.store.reviews.append(values)

    def search_sellable_items(self, *, text="", karat_id=None, weight_min=None, weight_max=None, size="",
                              exclude_ids=(), weight_target=None, limit=31, item_type_id=None, factory_id=None):
        """Same contract as InvoiceRepository.search_sellable_items (IN_STOCK only, no choice made)."""
        self.store.searches = getattr(self.store, "searches", []) + [dict(text=text, karat_id=karat_id)]
        excluded = {str(int(i)) for i in exclude_ids}
        rows = []
        for item in self.store.items.values():
            fields = [item.get("item_type_name"), item.get("factory_name"), item.get("category_name"),
                      item.get("category_code"), item.get("barcode_value")]
            if item["inventory_status"] != "IN_STOCK" or item["inventory_item_id"] in excluded:
                continue
            from utils.search_normalization import normalize_search_text as fold
            if text and not any(fold(text) in fold(value) or text in (value or "") for value in fields):
                continue
            if karat_id is not None and item["karat_id"] != karat_id:
                continue
            if item_type_id is not None and item.get("item_type_id") != item_type_id:
                continue
            if factory_id is not None and item.get("factory_id") != factory_id:
                continue
            if weight_min is not None and not weight_min <= item["weight_grams"] <= weight_max:
                continue
            if size and size not in (item.get("size") or ""):
                continue
            rows.append(dict(item))
        key = ((lambda r: (abs(r["weight_grams"] - weight_target), -int(r["inventory_item_id"])))
               if weight_target is not None else (lambda r: -int(r["inventory_item_id"])))
        return sorted(rows, key=key)[:limit]

    def get_payment_method(self, code):
        if code not in self.store.methods:
            return None
        method_id, bank, cash = self.store.methods[code]
        return {"payment_method_id": method_id, "code": code, "is_active": 1, "requires_bank_account": bank,
                "affects_physical_cash": cash}

    def active_cash_accounts(self):
        return [dict(a) for a in self.store.cash_accounts]

    def add_cash_movement(self, **values):
        self._fail("add_cash_movement")
        amount = values["amount"] if values["direction"] == "IN" else -values["amount"]
        self.store.cash_movements.append({**values, "amount_delta": amount})

    def add_bank_movement(self, **values):
        self._fail("add_bank_movement")
        amount = values["amount"] if values["direction"] == "IN" else -values["amount"]
        self.store.bank_movements.append({**values, "amount_delta": amount})

    def active_bank_accounts(self):
        return [dict(a) for a in self.store.bank_accounts.values() if a["is_active"]]

    def get_bank_account(self, bank_account_id, lock=False):
        row = self.store.bank_accounts.get(int(bank_account_id))
        return dict(row) if row else None

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
        self.store.old_gold.append({**values, "signed_weight": values["weight_grams"], "code": "CUSTOMER_BUY_IN"})
        return str(700 + len(self.store.old_gold))

    def insert_payment(self, **values):
        self._fail("insert_payment")
        self.store.payments.append(values)
        return str(400 + len(self.store.payments))

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

    def find_active_by_mobile(self, mobile):
        from utils.search_normalization import normalize_mobile_search as fold
        return [dict(c) for c in self.store.customers.values()
                if c["is_active"] and c.get("mobile") and fold(c["mobile"]) == fold(mobile)
                and c["record_origin"] != "LEGACY_PLACEHOLDER"]

    def create_customer(self, name, mobile, place, customer_type_id, notes, created_by_user_id=None):
        customer_id = str(500 + len(self.store.new_customers) + 1)
        self.store.customers[customer_id] = {
            "customer_id": customer_id, "customer_code": f"C{int(customer_id):06d}", "name": name,
            "mobile": mobile, "place": place, "is_active": 1, "record_origin": "NATIVE",
            "customer_type_name": None, "created_by": created_by_user_id}
        self.store.new_customers.append(customer_id)
        return int(customer_id)

    def search_customers(self, search="", is_active=None, limit=101, **kwargs):
        from utils.search_normalization import normalize_mobile_search, normalize_search_text as fold
        text, phone = fold(search), normalize_mobile_search(search)
        rows = []
        for c in self.store.customers.values():
            if c["record_origin"] == "LEGACY_PLACEHOLDER" or (is_active and not c["is_active"]):
                continue
            if text in fold(c["name"]) or (phone and c.get("mobile") and phone in normalize_mobile_search(c["mobile"])):
                rows.append({**c, "balance": self.get_balance(int(c["customer_id"])),
                             "has_active_dead_cash_warning": int(c["customer_id"]) in self.store.warnings})
        return rows[:limit]


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


def sale(item="100"):
    """A scanned stock piece; its price comes from the real money (no making is typed)."""
    return SaleLineInput(item)


def disc(percent="1.50"):
    return {"discount_type": "PERCENT", "discount_value": percent}


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
    # Item 100: 21K 10.500 g × SHOP_SELL 6232 = 65,436.00 gold; item 101: 18K 3.333 g × 5341 = 17,801.55.
    def test_sale_only_fully_paid_in_cash(self):
        result = self.finalize(InvoiceDraft(5, "12", (sale(),), (), (cash("67011"),)))
        self.assertEqual((result["invoice_number"], result["invoice_type"], result["settlement_status"]),
                         ("26092501", "SELL", "SETTLED"))
        # The cash received is the sale price: 67,011 − 65,436 gold = 1,575 making (150.00/g).
        self.assertEqual((result["sale_total"], result["effective_sale_total"], result["average_making_per_gram"]),
                         ("67011.00", "67011.00", "150.00"))
        header = self.store.invoices[0]
        self.assertEqual((header["sell_total"], header["buy_total"]), (D("67011.00"), D("0.00")))
        self.assertEqual(self.store.items["100"]["inventory_status"], "SOLD")
        self.assertEqual(len(self.store.inventory_movements), 1)
        self.assertEqual([(p["direction"], p["amount"]) for p in self.store.payments], [("IN", D("67011.00"))])
        self.assertEqual([m["amount_delta"] for m in self.store.cash_movements], [D("67011.00")])
        self.assertEqual(self.store.ledger, [])                                    # nothing owed
        self.assertEqual([s["invoice_id"] for s in self.store.snapshots], ["901"])  # the named customer
        self.assertEqual([e["event_type"] for e in self.store.events], ["CREATED", "FINALIZED"])
        self.assertEqual(self.commits, 1)

    def test_old_gold_purchase_only_partially_paid_leaves_shop_owing(self):
        # 10 g × SHOP_BUY 6207 = 62,070.00 − 1.5% (931.05) = 61,138.95; wallet OUT 50,000; 11,138.95 kept for him.
        result = self.finalize(InvoiceDraft(5, "12", (), (old(1, "10", **disc()),),
                                            (PaymentInput("MOBILE_WALLET", "50000", bank_account_id="9"),),
                                            account_amount="11138.95", account_direction="SHOP_OWES"))
        self.assertEqual((result["invoice_type"], result["net_direction"], result["settlement_status"]),
                         ("BUY", "SHOP_PAYS_CUSTOMER", "PARTIAL"))
        self.assertIsNone(result["average_making_per_gram"])                  # nothing sold: no making
        self.assertEqual(self.store.payments[0]["direction"], "OUT")
        self.assertEqual((self.store.ledger[0]["type_code"], self.store.ledger[0]["amount_delta"]),
                         ("BUY_BALANCE_DUE", D("-11138.95")))
        self.assertEqual([(m["bank_account_id"], m["amount_delta"]) for m in self.store.bank_movements],
                         [(9, D("-50000.00"))])
        movement = self.store.old_gold[0]
        self.assertEqual((movement["gold_price_per_gram"], movement["cash_value"], movement["price_quote_id"]),
                         (D("6207.0000"), D("61138.95"), 51))
        self.assertEqual(self.store.snapshots[0]["balance_after"], D("-11138.95"))

    def test_exchange_with_a_separate_customer_account_entry(self):
        # O = 5 g × 6207 = 31,035.00 − 1.5% (465.53) = 30,569.47; the customer owes 35,976:
        # 30,000 paid in cash now + 5,976 deferred on the account (P + L).
        result = self.finalize(InvoiceDraft(5, "12", (sale(),), (old(1, "5", **disc()),), (cash("30000"),),
                                            account_amount="5976", account_direction="CUSTOMER_OWES"))
        # effective = O + P + L = 30,569.47 + 30,000 + 5,976 = 66,545.47.
        self.assertEqual((result["invoice_type"], result["effective_sale_total"], result["net_amount"],
                          result["account_residual_amount"]), ("MIXED", "66545.47", "35976.00", "5976.00"))
        self.assertEqual([(kind, v["line_number"]) for kind, v in self.store.lines], [("SALE", 1), ("OLD", 2)])
        self.assertEqual(self.store.lines[1][1]["old_gold_movement_id"], "701")
        self.assertEqual((self.store.ledger[0]["type_code"], self.store.ledger[0]["amount_delta"]),
                         ("SALE_BALANCE_DUE", D("5976.00")))
        self.assertEqual(self.store.ledger[0]["reference_code"], result["invoice_number"])
        self.assertEqual([m["amount_delta"] for m in self.store.cash_movements], [D("30000.00")])   # real money only

    def test_exact_exchange_needs_no_payment_or_customer(self):
        # O = 11 g × 6207 = 68,277.00 − 1.5% (1,024.16) = 67,252.84 = the sale price; no money moves.
        result = self.finalize(InvoiceDraft(5, "12", (sale(),), (old(1, "11", **disc()),)))
        self.assertEqual((result["net_direction"], result["net_amount"], result["settlement_status"],
                          result["effective_sale_total"]), ("SETTLED", "0.00", "SETTLED", "67252.84"))
        self.assertEqual((self.store.payments, self.store.ledger, self.store.cash_movements), ([], [], []))
        self.assertEqual(self.store.invoices[0]["customer_id"], "12")               # every invoice names its customer

    def test_numbers_follow_the_server_business_date(self):
        first = self.finalize(InvoiceDraft(5, "12", (sale("100"),), (), (cash("67011"),)))
        second = self.finalize(InvoiceDraft(5, "12", (sale("101"),), (), (cash("17801.55"),)))
        self.assertEqual((first["invoice_number"], second["invoice_number"]), ("26092501", "26092502"))
        self.assertEqual(self.store.invoices[0]["invoice_date"], NOW)


class RuleTests(InvoiceServiceTestCase):
    def test_an_eleventh_sale_item_is_refused_even_if_the_ui_is_bypassed(self):
        """One printed A5 bill holds 10 sale rows: scanned, F5 and F7 lines all count."""
        self.assertEqual(MAX_SALE_ITEMS, 10)
        eleven = tuple(sale(str(100 + i)) for i in range(11))
        self.assertRefused("TOO_MANY_SALE_ITEMS", InvoiceDraft(5, "12", eleven, (), (cash("67011"),)))
        mixed = InvoiceDraft(5, "12", tuple(sale(str(100 + i)) for i in range(6)), (), (cash("67011"),),
                             described_sale_lines=tuple(DescribedSaleInput("OLD_GOLD_STOCK", 1, "2")
                                                        for _ in range(5)))
        self.assertRefused("TOO_MANY_SALE_ITEMS", mixed)
        with self.assertRaises(InvoiceError) as context:
            self.finalize(InvoiceDraft(5, "12", eleven, (), (cash("67011"),)))
        self.assertEqual(str(context.exception), "الحد الأقصى للفاتورة 10 أصناف بيع")

    def test_customer_old_gold_does_not_count_toward_the_ten(self):
        draft = InvoiceDraft(5, "12", tuple(sale(str(100 + i)) for i in range(10)),
                             tuple(old(1, "1") for _ in range(5)), (cash("67011"),))
        try:
            self.finalize(draft)
        except InvoiceError as exc:                  # other rules may refuse this synthetic draft...
            self.assertNotEqual(exc.code, "TOO_MANY_SALE_ITEMS")   # ...but never the 10-item rule

    def test_account_amount_needs_a_customer_and_old_gold_gap_needs_a_decision(self):
        self.assertRefused("CUSTOMER_NAME_REQUIRED", InvoiceDraft(5, None, (sale(),), (), (cash("67011"),),
                                                                  account_amount="7011",
                                                                  account_direction="CUSTOMER_OWES"))
        self.assertRefused("ACCOUNT_DIRECTION_REQUIRED", InvoiceDraft(5, "12", (sale(),), (), (cash("67011"),),
                                                                      account_amount="7011"))
        # Old gold only: 5 g = 31,035.00 owed; nothing paid and nothing decided → refused.
        self.assertRefused("SETTLEMENT_CHOICE_REQUIRED", InvoiceDraft(5, "12", (), (old(),), ()))
        self.assertRefused("CUSTOMER_NAME_REQUIRED", InvoiceDraft(5, None, (), (old(),), (),
                                                                  account_amount="31035",
                                                                  account_direction="SHOP_OWES"))

    def test_money_below_the_gold_value_or_missing_is_refused(self):
        self.assertRefused("PAYMENT_REQUIRED", InvoiceDraft(5, "12", (sale(),), (), ()))
        self.assertRefused("SALE_BELOW_GOLD", InvoiceDraft(5, "12", (sale(),), (), (cash("65000"),)))

    def test_inactive_placeholder_and_missing_customers_are_rejected(self):
        for customer, code in (("13", "CUSTOMER_INACTIVE"), ("977", "CUSTOMER_PLACEHOLDER"),
                               ("55", "CUSTOMER_NOT_FOUND")):
            with self.subTest(customer=customer):
                self.assertRefused(code, InvoiceDraft(5, customer, (sale(),), (), (cash("67011"),)))

    def test_flagged_customer_needs_confirmation_for_new_debt_only(self):
        self.store.warnings[12] = {"flag_id": "8"}
        draft = InvoiceDraft(5, "12", (sale(),), (), (cash("67011"),), account_amount="7011",
                             account_direction="CUSTOMER_OWES")
        self.assertRefused("DEAD_CASH_CONFIRMATION_REQUIRED", draft)
        result = self.finalize(draft, dead_cash_confirmed=True)
        self.assertEqual(result["account_residual_amount"], "7011.00")
        self.assertTrue(self.store.snapshots[0]["dead_cash_warning_active"])
        self.assertTrue(self.store.events[1]["details"]["dead_cash_confirmed"])
        # Fully paid: no new debt, no confirmation needed.
        self.finalize(InvoiceDraft(5, "12", (sale("101"),), (), (cash("17801.55"),)))

    def test_unavailable_missing_and_duplicate_items(self):
        self.assertRefused("ITEM_UNAVAILABLE", InvoiceDraft(5, "12", (sale("102"),), (), (cash("1"),)))
        self.assertRefused("ITEM_NOT_FOUND", InvoiceDraft(5, "12", (sale("555"),), (), (cash("1"),)))

    def test_price_token_and_staleness(self):
        draft = InvoiceDraft(5, "12", (sale(),), (), (cash("67011"),))
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

    def test_only_supported_payment_methods(self):
        for method in ("CARD_POS", "OTHER"):
            draft = InvoiceDraft(5, "12", (sale(),), (), (cash("67011", method),))
            with self.subTest(method=method):
                self.assertRefused("PAYMENT_METHOD_NOT_ALLOWED", draft)
                with self.assertRaises(InvoiceError) as context:           # preview refuses too
                    self.service.preview(draft)
                self.assertEqual(context.exception.code, "PAYMENT_METHOD_NOT_ALLOWED")
        self.finalize(InvoiceDraft(5, "12", (sale(),), (),
                                   (PaymentInput("MOBILE_WALLET", "67011", bank_account_id="9"),)))

    def test_payment_legs_share_one_direction(self):
        draft = InvoiceDraft(5, "12", (sale(),), (), (cash("40000"), PaymentInput("INSTAPAY", "27011", "OUT", None, "7")))
        self.assertRefused("MIXED_PAYMENT_DIRECTIONS", draft)
        split = InvoiceDraft(5, "12", (sale(),), (), (cash("40000"), PaymentInput("INSTAPAY", "27011", bank_account_id="7")))
        result = self.finalize(split)
        self.assertEqual(result["effective_sale_total"], "67011.00")
        self.assertEqual(([m["amount_delta"] for m in self.store.cash_movements],
                          [m["amount_delta"] for m in self.store.bank_movements]),
                         ([D("40000.00")], [D("27011.00")]))

    def test_active_salesperson_is_required(self):
        for employee, code in ((None, "SALESPERSON_REQUIRED"), (6, "SALESPERSON_INACTIVE"),
                               (99, "SALESPERSON_INACTIVE")):
            with self.subTest(employee=employee):
                self.assertRefused(code, InvoiceDraft(employee, None, (sale(),), (), (cash("67011"),)))

    def test_calculation_errors_write_nothing(self):
        before = copy.deepcopy(self.store.tables())
        for draft in (InvoiceDraft(5, "12", (sale(),), (), (cash("67011.001"),)),        # precision
                      InvoiceDraft(5, "12", (), (old(1, "1.0005"),), ())):                # weight precision
            with self.subTest(draft=draft), self.assertRaises(InvoiceCalculationError):
                self.finalize(draft)
        self.assertNothingWritten(before)
        # Old gold only: paying more than the old gold is worth is refused.
        self.assertRefused("OVERPAYMENT", InvoiceDraft(5, "12", (), (old(),), (cash("40000"),)))

    def test_empty_invoice_and_missing_price(self):
        with self.assertRaises(InvoiceError):
            self.finalize(InvoiceDraft(5, "12", (), (), ()))
        self.assertRefused("PRICE_MISSING", InvoiceDraft(5, "12", (), (old(karat=9),), ()))

    def test_server_clock_must_be_egypt_time(self):
        self.clock_offset = timedelta(0)
        self.assertRefused("CLOCK_NOT_EGYPT", InvoiceDraft(5, "12", (sale(),), (), (cash("67011"),)))
        self.clock_offset = timedelta(hours=2)                              # winter time is fine
        self.finalize(InvoiceDraft(5, "12", (sale(),), (), (cash("67011"),)))


class AtomicityAndAttributionTests(InvoiceServiceTestCase):
    def exchange(self):
        return InvoiceDraft(5, "12", (sale(),), (old(1, "5", **disc()),),
                            (cash("25976"), PaymentInput("BANK_TRANSFER", "10000", bank_account_id="7")),
                            account_amount="5976", account_direction="CUSTOMER_OWES")

    def test_injected_failure_rolls_back_every_table(self):
        # A money ledger that cannot be written leaves no finalized invoice behind.
        for failing in ("insert_sale_line", "insert_payment", "add_cash_movement", "add_bank_movement"):
            InvoiceRepo.fail_on = failing
            before = copy.deepcopy(self.store.tables())
            with self.subTest(failing=failing), self.assertRaisesRegex(RuntimeError, "injected"):
                self.finalize(self.exchange())
            self.assertNothingWritten(before)
            self.assertEqual(self.store.items["100"]["inventory_status"], "IN_STOCK")
        self.assertEqual(self.commits, 0)

    def test_actor_and_salesperson_are_recorded_separately(self):
        self.finalize(self.exchange())
        header = self.store.invoices[0]
        self.assertEqual((header["created_by_user_id"], header["employee_id"]), (17, 5))
        self.assertEqual(self.store.inventory_movements[0]["performed_by_user_id"], 17)
        self.assertEqual(self.store.inventory_movements[0]["performed_by_employee_id"], 5)
        self.assertEqual((self.store.old_gold[0]["created_by_user_id"],
                          self.store.old_gold[0]["received_by_employee_id"]), (17, 5))
        self.assertEqual(self.store.items["100"]["updated_by"], 17)
        self.assertEqual({p["created_by_user_id"] for p in self.store.payments}, {17})
        self.assertEqual({m["created_by_user_id"] for m in self.store.cash_movements + self.store.bank_movements}, {17})
        self.assertEqual(self.store.ledger[0]["created_by_user_id"], 17)
        self.assertEqual({e["performed_by_user_id"] for e in self.store.events}, {17})
        self.assertEqual(self.store.audit[0][:3], (17, "INVOICE_FINALIZED", "INVOICE"))
        details = self.store.events[1]["details"]
        self.assertEqual((details["salesperson_employee_id"], details["actor_employee_id"]), (5, 7))
        self.assertEqual(self.authorization.checked, ["INVOICE_FINALIZE", "PAYMENT_RECORD"])

    def test_permission_and_session_are_enforced(self):
        self.authorization.permissions = frozenset({"APP_LOGIN"})
        with self.assertRaises(PermissionError):
            self.finalize(InvoiceDraft(5, "12", (sale(),), (), (cash("67011"),)))
        self.session.logout()
        with self.assertRaises(PermissionError):
            self.finalize(InvoiceDraft(5, "12", (sale(),), (), (cash("67011"),)))
        self.assertEqual(self.store.invoices, [])

    def test_each_payment_is_posted_once_to_its_own_ledger(self):
        self.finalize(self.exchange())
        payments = self.store.payments
        self.assertEqual([(p["payment_method_id"], p["bank_account_id"], p["amount"]) for p in payments],
                         [(1, None, D("25976.00")), (2, 7, D("10000.00"))])
        # Exactly one ledger row per payment, linked by its id: no double posting, no unposted money.
        self.assertEqual([(m["invoice_payment_id"], m["cash_account_id"], m["amount_delta"])
                          for m in self.store.cash_movements], [("401", 1, D("25976.00"))])
        self.assertEqual([(m["invoice_payment_id"], m["bank_account_id"], m["amount_delta"])
                          for m in self.store.bank_movements], [("402", 7, D("10000.00"))])
        # The part on the account is a customer balance, never a money movement.
        self.assertEqual(self.store.ledger[0]["amount_delta"], D("5976.00"))

    def test_price_snapshots_keep_each_karat_provider_time_in_utc(self):
        self.finalize(InvoiceDraft(5, "12", (sale(),), (), (cash("67011"),)))
        times = {p["karat_id"]: p["provider_timestamp"] for p in self.store.price_snapshots}
        self.assertEqual(times, {1: datetime(2026, 9, 25, 11, 29, 56), 2: datetime(2026, 9, 25, 11, 29, 57),
                                 3: datetime(2026, 9, 25, 11, 29, 58)})
        self.assertEqual({p["quote_id"] for p in self.store.price_snapshots}, {51, 52, 53})


class ItemSearchTests(InvoiceServiceTestCase):
    """بحث عن قطعة: candidates only; the piece is sold by its exact inventory id."""

    def setUp(self):
        super().setUp()
        base = {"size": None, "stock_classification": "NEW_STOCK", "inventory_status": "IN_STOCK",
                "category_code": "A", "category_name": None}
        self.store.items.update({
            "103": {**base, "inventory_item_id": "103", "karat_id": 1, "karat_value": 21,
                    "weight_grams": D("3.500"), "item_type_name": "دبلة", "factory_name": "مصنع النور",
                    "size": "18", "barcode_value": None},
            "104": {**base, "inventory_item_id": "104", "karat_id": 1, "karat_value": 21,
                    "weight_grams": D("3.520"), "item_type_name": "دبلة", "factory_name": "مصنع النور",
                    "size": "17", "barcode_value": "04260925"},
            "105": {**base, "inventory_item_id": "105", "karat_id": 1, "karat_value": 21, "inventory_status": "SOLD",
                    "weight_grams": D("3.500"), "item_type_name": "دبلة", "factory_name": None,
                    "barcode_value": None}})

    def ids(self, result):
        return [item["inventory_item_id"] for item in result["items"]]

    def test_only_in_stock_candidates_closest_weight_first(self):
        result = self.service.search_sellable_items(text="دبلة", karat_id=1, weight="3.5", tolerance="0.05")
        self.assertEqual(self.ids(result), ["103", "104"])            # both similar pieces are shown
        self.assertNotIn("105", self.ids(self.service.search_sellable_items(text="دبلة")))
        first = result["items"][0]
        self.assertEqual((first["barcode"], first["factory_name"], first["size"], first["weight_grams"]),
                         (None, "مصنع النور", "18", "3.500"))
        self.assertFalse(result["truncated"])

    def test_filters_and_draft_exclusion(self):
        self.assertEqual(self.ids(self.service.search_sellable_items(text="دبلة", exclude_ids=("103",))), ["104"])
        self.assertEqual(self.ids(self.service.search_sellable_items(text="دبلة", karat_id=2)), [])
        self.assertEqual(self.ids(self.service.search_sellable_items(text="دبلة", size="17")), ["104"])
        self.assertEqual(self.ids(self.service.search_sellable_items(text="النور", weight="3.5", tolerance="0")),
                         ["103"])
        self.assertEqual(self.ids(self.service.search_sellable_items(text="0426")), ["104"])

    def test_invalid_weight_and_permission(self):
        for weight, tolerance in (("abc", "0.05"), ("-1", "0.05"), ("3.5", "6"), ("NaN", "0")):
            with self.assertRaises(InvoiceError) as refused:
                self.service.search_sellable_items(weight=weight, tolerance=tolerance)
            self.assertEqual(refused.exception.code, "SEARCH_WEIGHT_INVALID")
        self.session.logout()
        with self.assertRaises(PermissionError):
            self.service.search_sellable_items(text="دبلة")

    def test_searched_piece_is_sold_by_its_exact_id_and_recorded(self):
        before_movements = len(self.store.inventory_movements)
        self.finalize(InvoiceDraft(5, "12", (SaleLineInput("103", identified_by="SEARCH"),),
                                   (), (cash("22162"),)))
        self.assertEqual(self.store.items["103"]["inventory_status"], "SOLD")
        self.assertEqual(self.store.items["104"]["inventory_status"], "IN_STOCK")   # the similar piece is untouched
        self.assertEqual(len(self.store.inventory_movements), before_movements + 1)
        finalized = [e for e in self.store.events if e["event_type"] == "FINALIZED"][0]
        self.assertEqual(finalized["details"]["search_identified_item_ids"], ["103"])

    def test_scanned_pieces_record_no_search_detail_and_bad_method_is_refused(self):
        self.finalize(InvoiceDraft(5, "12", (sale(),), (), (cash("67011"),)))
        finalized = [e for e in self.store.events if e["event_type"] == "FINALIZED"][0]
        self.assertNotIn("search_identified_item_ids", finalized["details"])
        self.assertRefused("IDENTIFICATION_INVALID",
                           InvoiceDraft(5, "12", (SaleLineInput("101", identified_by="GUESS"),),
                                        (), (cash("1"),)))


class PreviewTests(InvoiceServiceTestCase):
    def test_preview_recalculates_and_writes_nothing(self):
        self.store.warnings[12] = {"flag_id": "8"}
        before = copy.deepcopy(self.store.tables())
        preview = self.service.preview(InvoiceDraft(5, "12", (sale(),), (old(1, "5", **disc()),), (cash("30000"),),
                                                    account_amount="5976", account_direction="CUSTOMER_OWES"))
        self.assertNothingWritten(before)
        self.assertEqual(self.commits, 0)                                     # rolled back, never committed
        self.assertEqual((preview["price_token"], preview["sale_total"], preview["old_gold_total"]),
                         ("T1", "66545.47", "30569.47"))
        self.assertEqual(preview["sale_lines"][0]["weight_grams"], "10.500")        # from the database
        self.assertEqual(preview["sale_lines"][0]["gold_price_per_gram"], "6232.0000")
        self.assertEqual(preview["old_gold_lines"][0]["gold_price_per_gram"], "6207.0000")
        self.assertEqual((preview["customer_balance_before"], preview["customer_balance_after"]),
                         ("0.00", "5976.00"))
        self.assertTrue(preview["requires_dead_cash_confirmation"])
        self.assertEqual(preview["blocking_problems"], [])

    def test_preview_reports_missing_money_and_customer(self):
        preview = self.service.preview(InvoiceDraft(5, "12", (sale(),), (), ()))
        self.assertEqual([p["code"] for p in preview["blocking_problems"]], ["PAYMENT_REQUIRED"])
        preview = self.service.preview(InvoiceDraft(5, None, (sale(),), (), (cash("67011"),)))
        self.assertEqual([p["code"] for p in preview["blocking_problems"]], ["CUSTOMER_NAME_REQUIRED"])

    def test_draft_cannot_carry_prices_or_totals(self):
        # No agreed total and no per-line making: the price only comes from the real money.
        for field in ("sale_total", "agreed_sale_total"):
            with self.subTest(field=field), self.assertRaises(TypeError):
                InvoiceDraft(5, "12", (), (), (), **{field: "1"})
        for field in ("making_total", "making_per_gram", "weight_grams"):
            with self.subTest(field=field), self.assertRaises(TypeError):
                SaleLineInput("100", **{field: "0"})



class InvoiceTotalTests(InvoiceServiceTestCase):
    """إجمالي الفاتورة is authoritative: the price and making come from it; payment legs and the
    customer account only allocate it (never above it, and exactly equal to it at finalize)."""
    # Item 100: 21K 10.500 g × 6232 = 65,436.00 gold value.

    def draft(self, payments, total="67011", **kwargs):
        return InvoiceDraft(5, "12", (sale(),), (), tuple(payments), invoice_total=total, **kwargs)

    def test_making_is_the_same_for_every_split_of_the_total(self):
        splits = [
            [cash("67011")],
            [cash("40000"), PaymentInput("MOBILE_WALLET", "27011", bank_account_id="9")],
            [cash("40000"), PaymentInput("INSTAPAY", "20000", bank_account_id="7")],     # + 7,011 on the account
        ]
        results = []
        for payments in splits:
            kwargs = {"account_amount": "7011", "account_direction": "CUSTOMER_OWES"} if len(payments) == 2                 and payments[1].method_code == "INSTAPAY" else {}
            preview = self.service.preview(self.draft(payments, **kwargs))
            self.assertEqual(preview["blocking_problems"], [], payments)
            results.append((preview["effective_sale_total"], preview["making_total"], preview["average_making_per_gram"]))
        self.assertEqual(set(results), {("67011.00", "1575.00", "150.00")})

    def test_payments_above_the_total_are_refused_even_without_the_screen(self):
        self.assertRefused("PAYMENT_ABOVE_TOTAL", self.draft([cash("60000"), cash("10000")]))
        self.assertRefused("PAYMENT_ABOVE_TOTAL", self.draft([cash("67011")], account_amount="1000",
                                                              account_direction="CUSTOMER_OWES"))

    def test_an_incomplete_allocation_is_refused_and_never_becomes_debt(self):
        before = copy.deepcopy(self.store.tables())
        with self.assertRaises(InvoiceError) as context:
            self.finalize(self.draft([cash("60000")]))
        self.assertEqual(context.exception.code, "PAYMENT_ALLOCATION_INCOMPLETE")
        self.assertIn("7,011.00", str(context.exception))
        self.assertNothingWritten(before)

    def test_cash_plus_account_equal_to_the_total_finalizes_and_posts_only_real_money(self):
        result = self.finalize(self.draft([cash("60000")], account_amount="7011", account_direction="CUSTOMER_OWES"))
        self.assertEqual((result["effective_sale_total"], result["average_making_per_gram"]), ("67011.00", "150.00"))
        header = self.store.invoices[0]
        self.assertEqual((header["sell_total"], header["net_amount"]), (D("67011.00"), D("67011.00")))   # printed total
        self.assertEqual([m["amount_delta"] for m in self.store.cash_movements], [D("60000.00")])    # real money only
        self.assertEqual([p["amount"] for p in self.store.payments], [D("60000.00")])
        self.assertEqual(len(self.store.ledger), 1)                                                  # the 7,011

    def test_three_methods_equal_to_the_total_finalize(self):
        result = self.finalize(self.draft([cash("40000"), PaymentInput("MOBILE_WALLET", "20000", bank_account_id="9"),
                                           PaymentInput("BANK_TRANSFER", "7011", bank_account_id="7")]))
        self.assertEqual(result["effective_sale_total"], "67011.00")
        self.assertEqual(sorted(p["amount"] for p in self.store.payments), [D("7011.00"), D("20000.00"), D("40000.00")])
        self.assertEqual(self.store.ledger, [])

    def test_shop_pays_customer_direction_is_symmetric(self):
        # Exchange: 30 g old 21K is worth more than the piece, so the shop pays; the total is what the shop owes.
        draft = InvoiceDraft(5, "12", (sale(),), (old(1, "30", **disc()),), (cash("20000"),),
                             payment_direction="OUT", invoice_total="23000",
                             account_amount="3000", account_direction="SHOP_OWES")
        preview = self.service.preview(draft)
        codes = [p["code"] for p in preview["blocking_problems"]]
        self.assertNotIn("PAYMENT_ABOVE_TOTAL", codes)
        self.assertNotIn("PAYMENT_ALLOCATION_INCOMPLETE", codes)
        over = InvoiceDraft(5, "12", (sale(),), (old(1, "30", **disc()),), (cash("21000"),),
                            payment_direction="OUT", invoice_total="23000",
                            account_amount="3000", account_direction="SHOP_OWES")
        self.assertIn("PAYMENT_ABOVE_TOTAL", [p["code"] for p in self.service.preview(over)["blocking_problems"]])

    def test_without_a_total_the_old_contract_is_unchanged(self):
        result = self.finalize(InvoiceDraft(5, "12", (sale(),), (), (cash("67011"),)))
        self.assertEqual(result["effective_sale_total"], "67011.00")



class MakingFromInvoiceTotalOnlyTests(InvoiceServiceTestCase):
    """The owner's real screen: 13.430 g 21K at 6,234 = 83,722.62 gold; إجمالي الفاتورة 85,000
    → making 1,277.38, average 95.11/g — whatever the settlement (split, methods, account)."""

    def setUp(self):
        super().setUp()
        self.store.items["100"]["weight_grams"] = D("13.430")
        patcher = unittest.mock.patch.dict(PRICES, {1: (D("6207.0000"), D("6234.0000"))})
        patcher.start()
        self.addCleanup(patcher.stop)

    def making(self, payments, total="85000", **account):
        preview = self.service.preview(InvoiceDraft(5, "12", (sale(),), (), tuple(payments),
                                                    invoice_total=total, **account))
        self.assertEqual(preview["blocking_problems"], [], payments)
        return (preview["gold_value_total"], preview["effective_sale_total"], preview["making_total"],
                preview["average_making_per_gram"])

    def test_every_settlement_gives_the_same_making(self):
        on_account = {"account_amount": "1000", "account_direction": "CUSTOMER_OWES"}
        cases = {
            "A cash 85,000": ([cash("85000")], {}),
            "B cash 84,000 + account 1,000": ([cash("84000")], on_account),
            "C cash 80,000 + wallet 4,000 + account 1,000":
                ([cash("80000"), PaymentInput("MOBILE_WALLET", "4000", bank_account_id="9")], on_account),
            "D cash 40,000 + wallet 20,000 + InstaPay 24,000 + account 1,000":
                ([cash("40000"), PaymentInput("MOBILE_WALLET", "20000", bank_account_id="9"),
                  PaymentInput("INSTAPAY", "24000", bank_account_id="7")], on_account),
        }
        results = {name: self.making(payments, **account) for name, (payments, account) in cases.items()}
        self.assertEqual(set(results.values()), {("83722.62", "85000.00", "1277.38", "95.11")}, results)

    def test_only_the_invoice_total_changes_making(self):
        self.assertEqual(self.making([cash("90000")], total="90000")[2:], ("6277.38", "467.41"))
        self.assertEqual(self.making([cash("85000")])[2:], ("1277.38", "95.11"))

    def test_the_saved_invoice_uses_the_same_making(self):
        result = self.finalize(InvoiceDraft(5, "12", (sale(),), (), (cash("84000"),), invoice_total="85000",
                                            account_amount="1000", account_direction="CUSTOMER_OWES"))
        self.assertEqual((result["effective_sale_total"], result["average_making_per_gram"]), ("85000.00", "95.11"))
        self.assertEqual(self.store.lines[0][1]["making_total"], D("1277.38"))
        self.assertEqual([m["amount_delta"] for m in self.store.cash_movements], [D("84000.00")])   # real money only



class DuplicateSaleItemTests(InvoiceServiceTestCase):
    """One physical piece is sold once per invoice, whatever path added it (service-side guard)."""
    # Item 100: 21K 10.500 g × 6232 = 65,436.00 gold value.

    def test_the_same_inventory_item_twice_is_refused_and_nothing_is_written(self):
        draft = InvoiceDraft(5, "12", (sale("100"), sale("100")), (), (cash("140000"),), invoice_total="140000")
        self.assertRefused("DUPLICATE_SALE_ITEM", draft)                          # no invoice, stock, money, ledger
        self.assertEqual((self.store.invoices, self.store.payments, self.store.ledger), ([], [], []))
        self.assertEqual(self.store.items["100"]["inventory_status"], "IN_STOCK")
        with self.assertRaises(InvoiceError) as context:
            self.service.preview(draft)
        self.assertEqual((context.exception.code, str(context.exception)),
                         ("DUPLICATE_SALE_ITEM", "لا يمكن بيع نفس القطعة مرتين في الفاتورة نفسها"))

    def test_two_different_pieces_with_identical_attributes_both_sell(self):
        self.store.items["145"] = {**self.store.items["100"], "inventory_item_id": "145", "barcode_value": "ABC1"}
        self.store.items["146"] = {**self.store.items["100"], "inventory_item_id": "146", "barcode_value": "ABC2"}
        result = self.finalize(InvoiceDraft(5, "12", (sale("145"), sale("146")), (), (cash("140000"),),
                                            invoice_total="140000"))
        self.assertEqual(result["effective_sale_total"], "140000.00")
        self.assertEqual((self.store.items["145"]["inventory_status"], self.store.items["146"]["inventory_status"]),
                         ("SOLD", "SOLD"))


if __name__ == "__main__":
    unittest.main()
