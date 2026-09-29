"""Exchange V1 (ExchangeService) on an in-memory store (no database).

The transaction factory snapshots the whole store and restores it on any exception,
like a MySQL rollback. The quotes are deliberately NOT proportional between karats
(21K 6207/6232, 18K 5000/5100, 24K 7500/7600; a 21K-derived 24K would be 7093.71/7122.29),
so any price silently derived from another karat changes the expected amounts.
"""
from contextlib import contextmanager
import copy
import dataclasses
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import MappingProxyType
import unittest

from app.session import AppSession
from services.authentication_service import AuthenticationResult, _VERIFIED
from services.exchange_service import (
    ALREADY_EXCHANGED_TEXT, HISTORICAL_MAKING_MISSING_TEXT, LINE_REFUSALS, MAKING_REFUND_WINDOW_HOURS,
    REFUND_EXPIRED_TEXT, REFUND_TEXT, ExchangeDraft, ExchangeError, ExchangeService, ReplacementInput, pure_weight,
)
from services.gold_price_service import BillingKaratPrice, BillingPriceSet, PriceChanged
from services.invoice_history_service import InvoiceHistoryService
from services.invoice_service import PaymentInput


D = Decimal
NOW = datetime(2026, 9, 28, 12, 0, 0)
KARATS = {1: 21, 2: 18, 3: 24}
PRICES = {1: (D("6207.0000"), D("6232.0000")), 2: (D("5000.0000"), D("5100.0000")),
          3: (D("7500.0000"), D("7600.0000"))}


def item(item_id, karat_id, weight, status="IN_STOCK", category_id=None, name="خاتم"):
    return {"inventory_item_id": item_id, "karat_id": karat_id, "karat_value": KARATS[karat_id],
            "weight_grams": D(weight), "size": None, "stock_classification": "NEW_STOCK",
            "inventory_status": status, "item_type_name": name, "factory_name": None, "category_code": None,
            "barcode_value": f"B{item_id}", "item_type_id": 1, "factory_id": None, "category_id": category_id,
            "item_notes": None}


def line(line_id, number, line_type, karat_id, weight, item_id=None, making=None):
    return {"line_id": line_id, "line_number": number, "line_type": line_type, "inventory_item_id": item_id,
            "karat_id": karat_id, "karat_value": KARATS[karat_id], "weight_grams": D(weight),
            "item_name_snapshot": "خاتم", "barcode_snapshot": None if item_id is None else f"B{item_id}",
            "making_per_gram": None, "making_total": None if making is None else D(making),
            "gross_total": D("1.00"), "line_total": D("1.00")}


class Store:
    def __init__(self):
        self.invoices = {"500": {"invoice_id": "500", "invoice_number": "26092701", "invoice_type": "SELL",
                                 "invoice_status": "FINALIZED", "record_origin": "NATIVE", "customer_id": "12",
                                 "customer_name_snapshot": "عميل", "finalized_at": NOW - timedelta(days=1),
                                 "invoice_date": NOW - timedelta(days=1), "net_amount": D("90000.00"),
                                 "net_direction": "CUSTOMER_PAYS_SHOP", "settlement_status": "SETTLED"},
                         "501": {"invoice_id": "501", "invoice_number": "26092702", "invoice_type": "SELL",
                                 "invoice_status": "FINALIZED", "record_origin": "NATIVE", "customer_id": None,
                                 "customer_name_snapshot": None, "finalized_at": NOW - timedelta(hours=1),
                                 "invoice_date": NOW - timedelta(hours=1), "net_amount": D("1.00"),
                                 "net_direction": "CUSTOMER_PAYS_SHOP", "settlement_status": "SETTLED"}}
        self.lines = {"500": [line("1", 1, "SELL_INVENTORY", 1, "10.000", "200", "1500.00"),
                              line("2", 2, "SELL_INVENTORY", 2, "4.000", "201", "400.00"),
                              line("3", 3, "SELL_UNIDENTIFIED", 1, "2.000"),
                              line("4", 4, "SELL_OLD_GOLD", 1, "3.000"),
                              line("5", 5, "BUY_OLD_GOLD", 1, "5.000"),
                              line("6", 6, "SELL_INVENTORY", 1, "5.000", "202"),
                              line("8", 7, "SELL_INVENTORY", 3, "5.000", "204", "500.00")],
                      "501": [line("7", 1, "SELL_INVENTORY", 1, "10.000", "203", "1000.00")]}
        self.items = {i["inventory_item_id"]: i for i in (
            item("200", 1, "10.000", "SOLD"), item("201", 2, "4.000", "SOLD"), item("202", 1, "5.000", "SOLD"),
            item("203", 1, "10.000", "SOLD"), item("204", 3, "5.000", "SOLD"),
            item("309", 2, "5.000"), item("310", 2, "3.000"), item("311", 3, "6.000"), item("312", 3, "4.000"),
            item("300", 1, "10.000"), item("301", 1, "12.000", category_id=1), item("302", 1, "8.000"),
            item("303", 2, "4.000"), item("304", 1, "9.000", "SOLD"), item("305", 3, "5.000"),
            item("306", 1, "3.429"))}
        self.categories = {1: {"id": 1, "code": "A", "name": "خواتم", "sale_making_min_per_gram": D("150"),
                               "sale_making_max_per_gram": D("250")}}
        self.customers = {"12": {"customer_id": "12", "is_active": 1, "record_origin": "NATIVE"}}
        self.bank_accounts = {7: {"id": 7, "name": "حساب المحل", "account_type": "BANK", "is_active": 1}}
        self.lifecycle, self.lifecycle_lines, self.lifecycle_payments = [], [], []
        self.cash_movements, self.bank_movements, self.inventory_movements = [], [], []
        self.ledger, self.events, self.reviews, self.audit = [], [], [], []
        # Billing reference data and the COUNTED old-gold ledger (grams per karat id), cases (F5 review).
        self.item_types = {2: {"id": 2, "name": "خاتم", "default_category_id": 1, "is_active": 1},
                           4: {"id": 4, "name": "دبلة", "default_category_id": None, "is_active": 1}}
        self.factories = {5: {"id": 5, "name": "لوكس", "is_active": 1}}
        self.old_gold = {1: D("20.000"), 2: D("0.000"), 3: D("0.000")}
        self.old_gold_movements, self.cases = [], []
        self.inspections = []                  # RETURNED_ITEM_INSPECTION management reviews (021)

    def tables(self):
        return copy.deepcopy(vars(self))


class Connection:
    in_transaction = True

    def __init__(self, store):
        self.store = store

    def cursor(self, dictionary=False):
        return Cursor(self.store)


class Cursor:
    def __init__(self, store):
        self.store, self.rows = store, []

    def execute(self, sql, params=()):
        if "UTC_TIMESTAMP" in sql:
            self.rows = [{"local_now": NOW, "utc_now": NOW - timedelta(hours=3)}]
        elif "FROM karats" in sql:
            self.rows = [{"id": k} for k in sorted(KARATS)]
        elif sql.startswith("INSERT INTO audit_log"):
            self.store.audit.append(params)
        else:
            raise AssertionError(sql[:60])

    def fetchall(self):
        return self.rows

    def close(self):
        pass


class InvoiceRepo:
    def __init__(self, connection):
        self.store = connection.store

    def get_invoice(self, invoice_id):
        row = self.store.invoices.get(str(invoice_id))
        return None if row is None else dict(row, lines=copy.deepcopy(self.store.lines.get(str(invoice_id), [])))

    def lock_invoice(self, invoice_id):
        return self.store.invoices.get(str(invoice_id))

    def read_sale_items(self, ids):
        return [dict(self.store.items[str(i)]) for i in ids if str(i) in self.store.items]

    lock_sale_items = read_sale_items

    def search_sellable_items(self, *, text="", karat_id=None, weight_min=None, weight_max=None, exclude_ids=(),
                              limit=31, **kwargs):
        rows = [dict(i) for i in self.store.items.values() if i["inventory_status"] == "IN_STOCK"
                and (karat_id is None or i["karat_id"] == karat_id)
                and (weight_min is None or weight_min <= i["weight_grams"] <= weight_max)
                and i["inventory_item_id"] not in exclude_ids]
        return rows[:limit]

    def find_item_id_by_barcode(self, barcode):
        return next((k for k, v in self.store.items.items() if v["barcode_value"] == barcode), None)

    def category_ranges(self, ids):
        return {i: self.store.categories[i] for i in ids if i in self.store.categories}

    def mark_item_sold(self, *, inventory_item_id, updated_by_user_id):
        row = self.store.items[str(inventory_item_id)]
        assert row["inventory_status"] == "IN_STOCK"
        row["inventory_status"] = "SOLD"

    def get_payment_method(self, code):
        ids = {"PHYSICAL_CASH": (1, 0, 1), "INSTAPAY": (3, 1, 0), "BANK_TRANSFER": (2, 1, 0),
               "MOBILE_WALLET": (5, 1, 0)}
        if code not in ids:
            return None
        pid, bank, cash = ids[code]
        return {"payment_method_id": pid, "code": code, "requires_bank_account": bank,
                "affects_physical_cash": cash, "is_active": 1}

    def active_cash_accounts(self):
        return [{"id": 1, "code": "MAIN", "name": "Main"}]

    def active_bank_accounts(self):
        return list(self.store.bank_accounts.values())

    def get_bank_account(self, account_id, lock=False):
        return self.store.bank_accounts.get(int(account_id))

    def lock_customer(self, customer_id):
        return self.store.customers.get(str(customer_id))

    def insert_management_review(self, **kwargs):
        self.store.reviews.append(kwargs)

    def get_item_type(self, item_type_id):
        return self.store.item_types.get(int(item_type_id))

    def get_factory(self, factory_id):
        return self.store.factories.get(int(factory_id))

    def reference_lists(self):
        return {"item_types": list(self.store.item_types.values()), "factories": list(self.store.factories.values())}

    def old_gold_balances(self):
        return [{"karat_id": k, "karat_value": KARATS[k], "balance_grams": v} for k, v in self.store.old_gold.items()]

    def lock_old_gold_balance(self, karat_id):
        return self.store.old_gold[int(karat_id)]

    def insert_event(self, **kwargs):
        self.store.events.append(kwargs)


class LifecycleRepo:
    fail_on = None

    def __init__(self, connection):
        self.store = connection.store

    def _maybe_fail(self, name):
        if LifecycleRepo.fail_on == name:
            raise RuntimeError(f"forced failure in {name}")

    def source_lines(self, invoice_id):
        consumed = {l["source_invoice_line_id"]: l["lifecycle_id"] for l in self.store.lifecycle_lines
                    if l["source_invoice_line_id"]}
        return [dict(l, consumed_by=consumed.get(l["line_id"])) for l in self.store.lines.get(str(invoice_id), [])]

    def insert_transaction(self, **kwargs):
        lifecycle_id = str(len(self.store.lifecycle) + 1)
        c = kwargs["calculation"]
        # The 019 CHECKs, enforced here as MySQL would.
        assert c.pure_delta == c.replacement_pure - c.returned_pure
        assert kwargs["money_received"] - kwargs["money_paid"] + kwargs["account_signed"] == c.signed_amount
        assert c.making_charge == 0 or c.pure_delta > 0
        assert c.making_refund == 0 or (c.pure_delta < 0 and c.refund_eligible)
        self.store.lifecycle.append(dict(kwargs, lifecycle_id=lifecycle_id, calculation=dataclasses.asdict(c)))
        return lifecycle_id

    def insert_line(self, *, lifecycle_id, line_role, line_number, line, old_gold_movement_id=None):
        if line.source_line_id and any(l["source_invoice_line_id"] == line.source_line_id
                                       for l in self.store.lifecycle_lines):
            raise RuntimeError("Duplicate entry for key uq_lifecycle_source_line")
        assert (line_role == "RETURNED") == (line.source_line_id is not None)
        assert line.pure == pure_weight(line.weight, line.karat)
        # The 020 source-identity CHECK, enforced here as MySQL would.
        source = line.identified_by
        if line_role == "REPLACEMENT":
            assert source in ("SCAN", "SEARCH", "MANUAL", "OLD_GOLD_STOCK")
            if source in ("SCAN", "SEARCH"):
                assert line.inventory_item_id and old_gold_movement_id is None
            elif source == "MANUAL":
                assert line.inventory_item_id is None and old_gold_movement_id is None and line.reason_code
            else:
                assert line.inventory_item_id is None and old_gold_movement_id is not None
        self.store.lifecycle_lines.append({"lifecycle_id": lifecycle_id, "role": line_role, "number": line_number,
                                           "source_invoice_line_id": line.source_line_id,
                                           "inventory_item_id": line.inventory_item_id, "pure": line.pure,
                                           "making_per_gram": line.making_per_gram, "source": source,
                                           "old_gold_movement_id": old_gold_movement_id,
                                           "reason_code": line.reason_code, "item_name": line.item_name,
                                           "weight": line.weight})
        return str(len(self.store.lifecycle_lines))

    def add_old_gold_replacement_out(self, *, karat_id, weight_grams, lifecycle_id, **kwargs):
        self.store.old_gold[int(karat_id)] -= weight_grams
        self.store.old_gold_movements.append({"code": "EXCHANGE_REPLACEMENT_OUT", "karat_id": karat_id,
                                              "delta": -weight_grams, "reference": ("INVOICE_LIFECYCLE", lifecycle_id)})
        return str(len(self.store.old_gold_movements))

    def open_unidentified_replacement_case(self, **kwargs):
        self.store.cases.append(kwargs)
        return str(len(self.store.cases))

    def open_returned_item_inspection(self, *, lifecycle_line_id, invoice_id, summary, details, created_by_user_id):
        self._maybe_fail("open_returned_item_inspection")
        # UNIQUE uq_management_review_lifecycle_line, as MySQL would.
        if any(r["lifecycle_line_id"] == str(lifecycle_line_id) for r in self.store.inspections):
            raise RuntimeError("Duplicate entry for key uq_management_review_lifecycle_line")
        line = self.store.lifecycle_lines[int(lifecycle_line_id) - 1]
        assert line["role"] == "RETURNED"
        review_id = str(900 + len(self.store.inspections) + 1)
        self.store.inspections.append({"review_id": review_id, "lifecycle_line_id": str(lifecycle_line_id),
                                       "invoice_id": invoice_id, "summary": summary, "details": details,
                                       "status": "OPEN", "resolution": None, "resolved_by": None,
                                       "resolved_at": None, "created_by": created_by_user_id})
        return review_id

    def insert_payment(self, **kwargs):
        assert kwargs["amount"] > 0 and kwargs["direction"] in ("IN", "OUT")
        assert (kwargs["cash_movement_id"] is None) != (kwargs["bank_movement_id"] is None)
        self.store.lifecycle_payments.append(kwargs)

    def add_cash_movement(self, *, cash_account_id, direction, amount, lifecycle_id, **kwargs):
        self._maybe_fail("add_cash_movement")
        self.store.cash_movements.append({"account": cash_account_id, "type": f"EXCHANGE_PAYMENT_{direction}",
                                          "delta": amount if direction == "IN" else -amount,
                                          "reference": ("INVOICE_LIFECYCLE", lifecycle_id)})
        return str(len(self.store.cash_movements))

    def add_bank_movement(self, *, bank_account_id, direction, amount, lifecycle_id, payment_reference=None, **kw):
        self.store.bank_movements.append({"account": bank_account_id, "type": f"EXCHANGE_PAYMENT_{direction}",
                                          "delta": amount if direction == "IN" else -amount})
        return str(len(self.store.bank_movements))

    def mark_item_returned_pending(self, *, inventory_item_id, updated_by_user_id):
        row = self.store.items[str(inventory_item_id)]
        assert row["inventory_status"] == "SOLD"
        row["inventory_status"] = "RETURNED_PENDING"

    def add_inventory_movement(self, *, item, movement_code, from_status, to_status, lifecycle_id, **kwargs):
        self._maybe_fail("add_inventory_movement")
        self.store.inventory_movements.append((item["inventory_item_id"], movement_code, from_status, to_status))


class CustomerRepo:
    TYPES = {"EXCHANGE_BALANCE_DUE": "INCREASE", "EXCHANGE_CREDIT": "DECREASE"}

    def __init__(self, connection):
        self.store = connection.store

    def add_transaction(self, *, customer_id, type_code, amount_delta, **kwargs):
        direction = self.TYPES[type_code]
        assert (amount_delta > 0) == (direction == "INCREASE")
        self.store.ledger.append((customer_id, type_code, amount_delta, kwargs["reference_type"]))


class Prices:
    def __init__(self, token="T1", prices=PRICES):
        self.token, self.prices = token, prices

    def _set(self, karat_ids):
        prices = {k: BillingKaratPrice(k, KARATS[k], self.prices[k][0], self.prices[k][1], 50 + k, 60 + k,
                                       datetime(2026, 9, 28, 9, 0, k, tzinfo=timezone.utc))
                  for k in karat_ids if k in self.prices}
        return BillingPriceSet(self.token, "AUTO", "FRESH", (), False, MappingProxyType(prices),
                               MappingProxyType({"source_code": "GOLDTIMO"}))

    def get_billing_price_set(self, karat_ids):
        return self._set(karat_ids)

    def lock_and_validate_for_finalization(self, connection, token, karat_ids, *, acknowledged=False):
        if token != self.token:
            raise PriceChanged("تغيرت أسعار الذهب")
        return self._set(karat_ids)


class Authorization:
    def __init__(self, session, permissions):
        self.session, self.permissions = session, permissions

    def require_permission(self, connection, actor, code, **kwargs):
        if not self.session.is_current(actor) or code not in self.permissions:
            raise PermissionError("لا توجد صلاحية")
        return dataclasses.replace(actor)


def draft(returned=("1",), replacements=(("300", None),), payments=(), account=None, invoice="500"):
    return ExchangeDraft(invoice, tuple(returned),
                         tuple(ReplacementInput(i, m) for i, m in replacements), tuple(payments), account)


def cash(amount, direction=None, method="PHYSICAL_CASH", account=None):
    return PaymentInput(method, amount, direction, None, account)


class ExchangeTestCase(unittest.TestCase):
    permissions = frozenset({"APP_LOGIN", "INVOICE_VIEW", "INVOICE_EXCHANGE", "PAYMENT_RECORD"})

    def setUp(self):
        LifecycleRepo.fail_on = None
        self.store = Store()
        self.session = AppSession()
        self.session._establish(AuthenticationResult(17, 7, "موظف", self.permissions, _VERIFIED),
                                int(self.session.generation))
        self.prices = Prices()
        self.service = self.make_service(self.prices)

    def make_service(self, prices):
        @contextmanager
        def transaction():
            saved = copy.deepcopy(vars(self.store))
            try:
                yield Connection(self.store)
            except BaseException:
                self.store.__dict__.clear()
                self.store.__dict__.update(saved)
                raise
        return ExchangeService(self.session, gold_prices=prices,
                               authorization=Authorization(self.session, self.permissions),
                               transaction_factory=transaction, invoice_repository=InvoiceRepo,
                               lifecycle_repository=LifecycleRepo, customer_repository=CustomerRepo)

    def finalize(self, d, token="T1"):
        return self.service.finalize(d, token)

    def assertRefused(self, code, d, message=None):
        before = self.store.tables()
        with self.assertRaises(ExchangeError) as context:
            self.finalize(d)
        self.assertEqual(context.exception.code, code)
        if message:
            self.assertEqual(str(context.exception), message)
        self.assertEqual(self.store.tables(), before)


class CaseTests(ExchangeTestCase):
    def test_a_same_pure_gold_is_zero_and_moves_no_money(self):
        preview = self.service.preview(draft())
        self.assertEqual((preview["pure_delta_grams"], preview["signed_amount"], preview["direction_text"]),
                         ("0.000", "0.00", "لا يوجد فرق"))
        self.assertIsNone(preview["price_basis"])
        self.assertEqual(preview["blocking_problems"], [])
        result = self.finalize(draft())
        self.assertEqual((result["signed_amount"], result["direction"]), ("0.00", "NONE"))
        self.assertEqual((self.store.lifecycle_payments, self.store.cash_movements, self.store.ledger), ([], [], []))

    def test_b_price_change_is_ignored_when_pure_gold_is_equal(self):
        service = self.make_service(Prices(prices={1: (D("9000"), D("9100")), 2: PRICES[2], 3: PRICES[3]}))
        self.assertEqual(service.preview(draft())["signed_amount"], "0.00")

    def test_c_more_gold_customer_pays_shop_sell_plus_making_on_delta(self):
        d = draft(replacements=(("301", "200"),))
        preview = self.service.preview(d)
        # Same karat (21K): +2.000 g × the actual 21K SHOP_SELL 6232 = 12,464.00.
        # making 200 × 12 = 2,400 / 10.500 pure = 228.5714 per pure gram × 1.750 pure = 400.00.
        self.assertEqual((preview["valuation_basis"], preview["valued_delta_grams"], preview["pure_delta_grams"],
                          preview["price_basis"], preview["price_karat"], preview["price_per_gram"],
                          preview["gold_amount"], preview["making_rate_per_pure_gram"], preview["making_charge"],
                          preview["signed_amount"], preview["direction_text"]),
                         ("SAME_KARAT", "2.000", "1.750", "SHOP_SELL", 21, "6232.0000", "12464.00", "228.5714",
                          "400.00", "12864.00", "على العميل"))
        self.assertFalse(preview["making_refund_applies"])
        d = draft(replacements=(("301", "200"),), payments=(cash("12864.00"),))
        self.assertEqual(self.finalize(d)["signed_amount"], "12864.00")
        self.assertEqual(self.store.cash_movements[0]["delta"], D("12864.00"))
        self.assertEqual(self.store.cash_movements[0]["type"], "EXCHANGE_PAYMENT_IN")

    def test_d_less_gold_within_window_credits_shop_buy_and_refunds_making(self):
        d = draft(replacements=(("302", None),))
        preview = self.service.preview(d)
        # Same karat: −2.000 g × the actual 21K SHOP_BUY 6207 = 12,414.00;
        # refund 1500 / 8.750 pure = 171.4286 × 1.750 pure = 300.00.
        self.assertEqual((preview["price_karat"], preview["price_per_gram"]), (21, "6207.0000"))
        self.assertEqual((preview["price_basis"], preview["gold_amount"], preview["making_refund"],
                          preview["signed_amount"], preview["direction_text"], preview["making_refund_text"]),
                         ("SHOP_BUY", "12414.00", "300.00", "-12714.00", "للعميل", REFUND_TEXT))
        self.assertEqual(preview["making_charge"], "0.00")
        self.finalize(draft(replacements=(("302", None),), payments=(cash("12714.00"),)))
        self.assertEqual(self.store.cash_movements[0]["delta"], D("-12714.00"))
        self.assertEqual(self.store.lifecycle_payments[0]["direction"], "OUT")

    def test_e_less_gold_after_window_has_no_refund(self):
        self.store.invoices["500"]["finalized_at"] = NOW - timedelta(hours=MAKING_REFUND_WINDOW_HOURS, seconds=1)
        preview = self.service.preview(draft(replacements=(("302", None),)))
        self.assertEqual((preview["making_refund"], preview["signed_amount"]), ("0.00", "-12414.00"))
        self.assertTrue(preview["making_refund_expired"])
        self.assertEqual(preview["making_refund_expired_text"], REFUND_EXPIRED_TEXT)

    def test_e_window_boundary_is_inclusive(self):
        self.store.invoices["500"]["finalized_at"] = NOW - timedelta(hours=MAKING_REFUND_WINDOW_HOURS)
        self.assertEqual(self.service.preview(draft(replacements=(("302", None),)))["making_refund"], "300.00")

    def test_f_different_karats_compare_on_pure_gold(self):
        # 18K 4.000 g = 3.000 pure ↔ 24K 3.000 g = 3.000 pure: no difference.
        self.store.items["313"] = item("313", 3, "3.000")
        preview = self.service.preview(draft(returned=("2",), replacements=(("313", None),)))
        self.assertEqual((preview["returned_pure_grams"], preview["replacement_pure_grams"],
                          preview["signed_amount"]), ("3.000", "3.000", "0.00"))

    def test_f_pure_weights_are_exact(self):
        # 18K 4.000 = 3.000 pure; 21K 3.500 = 3.0625 pure (never rounded to 3.063): +0.0625 × 24K 7600 = 475.00.
        self.store.items["314"] = item("314", 1, "3.500")
        preview = self.service.preview(draft(returned=("2",), replacements=(("314", "0"),)))
        self.assertEqual((preview["replacement_pure_grams"], preview["pure_delta_grams"], preview["gold_amount"]),
                         ("3.0625", "0.0625", "475.00"))
        # 21K 3.429 g = 3.000375 pure: a real (tiny) difference, not hidden by rounding.
        self.assertEqual(self.service.preview(draft(returned=("2",), replacements=(("306", "0"),)))
                         ["pure_delta_grams"], "0.000375")

    def test_g_many_to_many_uses_pure_weighted_making(self):
        d = draft(returned=("1", "2"), replacements=(("301", "200"), ("305", "100")))
        preview = self.service.preview(d)
        # Mixed karats: returned 8.750 + 3.000; given 10.500 + 5.000 → +3.750 pure g × actual 24K SELL 7600
        # = 28,500.00; making (2,400 + 500) / 15.500 = 187.0968 × 3.750 = 701.61.
        self.assertEqual((preview["valuation_basis"], preview["pure_delta_grams"], preview["price_karat"],
                          preview["gold_amount"], preview["making_rate_per_pure_gram"],
                          preview["making_charge"], preview["signed_amount"]),
                         ("PURE_24K", "3.750", 24, "28500.00", "187.0968", "701.61", "29201.61"))
        self.finalize(draft(returned=("1", "2"), replacements=(("301", "200"), ("305", "100")),
                            payments=(cash("29201.61"),)))
        roles = [(l["role"], l["number"]) for l in self.store.lifecycle_lines]
        self.assertEqual(roles, [("RETURNED", 1), ("RETURNED", 2), ("REPLACEMENT", 3), ("REPLACEMENT", 4)])

    def test_h_same_original_item_is_exchanged_once(self):
        self.finalize(draft())
        self.assertRefused("ALREADY_EXCHANGED", draft(replacements=(("302", None),), payments=(cash("12714.00"),)),
                           ALREADY_EXCHANGED_TEXT)
        context = self.service.context("500")
        first = next(l for l in context["lines"] if l["line_id"] == "1")
        self.assertEqual((first["exchangeable"], first["reason"], first["exchanged_by"]),
                         (False, ALREADY_EXCHANGED_TEXT, "1"))

    def test_i_old_gold_manual_and_f7_lines_are_refused(self):
        for line_id, kind in (("3", "SELL_UNIDENTIFIED"), ("4", "SELL_OLD_GOLD"), ("5", "BUY_OLD_GOLD")):
            with self.subTest(kind=kind):
                self.assertRefused("LINE_NOT_EXCHANGEABLE", draft(returned=(line_id,)), LINE_REFUSALS[kind])
        context = self.service.context("500")
        self.assertNotIn("5", [l["line_id"] for l in context["lines"]])      # bought old gold is not a sale line
        manual = next(l for l in context["lines"] if l["line_id"] == "3")
        self.assertEqual((manual["exchangeable"], manual["reason"]), (False, LINE_REFUSALS["SELL_UNIDENTIFIED"]))

    def test_j_missing_historical_making_inside_window_is_refused(self):
        # Line 6: 21K 5 g (4.375 pure) without a stored making; replacement 21K 4.000 g = 3.500 pure.
        self.store.items["307"] = item("307", 1, "4.000")
        with self.assertRaises(ExchangeError) as context:
            self.service.preview(draft(returned=("6",), replacements=(("307", None),)))
        self.assertEqual(str(context.exception), HISTORICAL_MAKING_MISSING_TEXT)
        self.assertRefused("HISTORICAL_MAKING_MISSING", draft(returned=("6",), replacements=(("307", None),)),
                           HISTORICAL_MAKING_MISSING_TEXT)

    def test_j_missing_historical_making_after_window_is_not_needed(self):
        self.store.invoices["500"]["finalized_at"] = NOW - timedelta(days=5)
        self.store.items["307"] = item("307", 1, "4.000")
        preview = self.service.preview(draft(returned=("6",), replacements=(("307", None),)))
        self.assertEqual(preview["making_refund"], "0.00")
        # Only the (unentered) money is missing; the historical making is not needed any more.
        self.assertEqual([p["code"] for p in preview["blocking_problems"]], ["AMOUNT_MISMATCH"])


class PricingRuleTests(ExchangeTestCase):
    """Each karat's own actual quote; mixed karats: pure delta at the actual 24K quote."""

    def price(self, d):
        p = self.service.preview(d)
        return (p["valuation_basis"], p["price_basis"], p["price_karat"], p["price_per_gram"],
                p["valued_delta_grams"], p["gold_amount"], p["signed_amount"])

    def test_1_same_karat_18k_uses_the_actual_18k_quote(self):
        # 18K 4.000 → 5.000: +1.000 g × 5100 = 5,100.00 (+ making 100×5 = 500 / 3.750 pure × 0.750 = 100.00).
        self.assertEqual(self.price(draft(returned=("2",), replacements=(("309", "100"),))),
                         ("SAME_KARAT", "SHOP_SELL", 18, "5100.0000", "1.000", "5100.00", "5200.00"))
        # 18K 4.000 → 3.000: −1.000 g × 5000 = 5,000.00 (+ refund 400 / 3.000 pure × 0.750 = 100.00).
        self.assertEqual(self.price(draft(returned=("2",), replacements=(("310", None),))),
                         ("SAME_KARAT", "SHOP_BUY", 18, "5000.0000", "-1.000", "5000.00", "-5100.00"))

    def test_2_same_karat_21k_uses_the_actual_21k_quote(self):
        self.assertEqual(self.price(draft(replacements=(("301", "0"),))),
                         ("SAME_KARAT", "SHOP_SELL", 21, "6232.0000", "2.000", "12464.00", "12464.00"))
        self.assertEqual(self.price(draft(replacements=(("302", None),)))[:6],
                         ("SAME_KARAT", "SHOP_BUY", 21, "6207.0000", "-2.000", "12414.00"))

    def test_3_same_karat_24k_uses_the_actual_24k_quote(self):
        self.assertEqual(self.price(draft(returned=("8",), replacements=(("311", "0"),))),
                         ("SAME_KARAT", "SHOP_SELL", 24, "7600.0000", "1.000", "7600.00", "7600.00"))
        # −1.000 g × 7500 = 7,500.00 + refund 500 / 5.000 × 1.000 = 100.00.
        self.assertEqual(self.price(draft(returned=("8",), replacements=(("312", None),))),
                         ("SAME_KARAT", "SHOP_BUY", 24, "7500.0000", "-1.000", "7500.00", "-7600.00"))

    def test_4_different_karats_positive_delta_uses_actual_24k_shop_sell(self):
        # 18K 4.000 (3.000 pure) → 24K 5.000 (5.000 pure): +2.000 × 7600 = 15,200.00.
        self.assertEqual(self.price(draft(returned=("2",), replacements=(("305", "0"),))),
                         ("PURE_24K", "SHOP_SELL", 24, "7600.0000", "2.000", "15200.00", "15200.00"))

    def test_5_different_karats_negative_delta_uses_actual_24k_shop_buy(self):
        # 21K 10.000 (8.750 pure) → 18K 4.000 (3.000 pure): −5.750 × 7500 = 43,125.00;
        # refund 1500 / 8.750 = 171.4286 × 5.750 = 985.71.
        self.assertEqual(self.price(draft(replacements=(("303", None),))),
                         ("PURE_24K", "SHOP_BUY", 24, "7500.0000", "-5.750", "43125.00", "-44110.71"))

    def test_6_no_karat_is_derived_from_another(self):
        # A 21K-derived 24K price would be 6207 × 24/21 = 7093.71 (→ 40,788.86 here); the real quote is used.
        gold = self.service.preview(draft(replacements=(("303", None),)))["gold_amount"]
        self.assertNotEqual(gold, "40788.86")
        self.assertEqual(gold, "43125.00")
        # Without an authoritative 24K quote a mixed exchange is refused, never derived from 21K.
        service = self.make_service(Prices(prices={1: PRICES[1], 2: PRICES[2]}))
        with self.assertRaises(ExchangeError) as context:
            service.preview(draft(replacements=(("303", None),)))
        self.assertEqual(context.exception.code, "PRICE_MISSING")
        # Same-karat 21K needs only the 21K quote.
        self.assertEqual(service.preview(draft(replacements=(("301", "0"),)))["gold_amount"], "12464.00")


class InventoryAndAccountingTests(ExchangeTestCase):
    def test_inventory_moves_returned_to_pending_and_replacement_out(self):
        self.finalize(draft())
        self.assertEqual(self.store.items["200"]["inventory_status"], "RETURNED_PENDING")
        self.assertEqual(self.store.items["300"]["inventory_status"], "SOLD")
        self.assertEqual(self.store.inventory_movements,
                         [("200", "EXCHANGE_RETURN_IN", "SOLD", "RETURNED_PENDING"),
                          ("300", "EXCHANGE_REPLACEMENT_OUT", "IN_STOCK", "SOLD")])

    def test_original_invoice_is_not_changed_and_gets_one_event(self):
        before = copy.deepcopy((self.store.invoices, self.store.lines))
        result = self.finalize(draft(replacements=(("301", "200"),), payments=(cash("12864.00"),)))
        self.assertEqual((self.store.invoices, self.store.lines), before)
        self.assertEqual(len(self.store.invoices), 2)                       # no new invoice: never a sale
        (event,) = self.store.events
        self.assertEqual((event["invoice_id"], event["event_type"]), ("500", "EXCHANGED"))
        self.assertEqual(event["details"]["signed_amount"], D("12864.00"))
        self.assertEqual(event["details"]["lifecycle_id"], result["lifecycle_id"])
        self.assertEqual(self.store.audit[0][1], "INVOICE_EXCHANGED")

    def test_price_snapshot_is_stored(self):
        self.finalize(draft(replacements=(("301", "200"),), payments=(cash("12864.00"),)))
        snapshot = self.store.lifecycle[0]["price_snapshot"]
        self.assertEqual((snapshot["price_token"], snapshot["source_code"], snapshot["valuation_basis"],
                          snapshot["price_karat"], snapshot["price_basis"], snapshot["price_per_gram"]),
                         ("T1", "GOLDTIMO", "SAME_KARAT", 21, "SHOP_SELL", D("6232.0000")))
        self.assertEqual([p["karat"] for p in snapshot["prices"]], [18, 21, 24])

    def test_customer_account_only_when_chosen(self):
        d = draft(replacements=(("301", "200"),), payments=(cash("10000.00"),), account="2864.00")
        self.finalize(d)
        self.assertEqual(self.store.ledger, [(12, "EXCHANGE_BALANCE_DUE", D("2864.00"), "INVOICE_LIFECYCLE")])
        self.assertEqual(self.store.lifecycle[0]["account_signed"], D("2864.00"))

    def test_shop_owes_can_go_to_the_customer_account(self):
        self.finalize(draft(replacements=(("302", None),), account="12714.00"))
        self.assertEqual(self.store.ledger, [(12, "EXCHANGE_CREDIT", D("-12714.00"), "INVOICE_LIFECYCLE")])
        self.assertEqual(self.store.cash_movements, [])

    def test_account_needs_a_customer(self):
        self.store.items["308"] = item("308", 1, "12.000")
        self.assertRefused("ACCOUNT_NEEDS_CUSTOMER",
                           draft(returned=("7",), replacements=(("308", "0"),), account="12464.00", invoice="501"))

    def test_bank_leg_posts_to_bank_ledger(self):
        d = draft(replacements=(("301", "200"),), payments=(cash("12864.00", method="INSTAPAY", account="7"),))
        self.finalize(d)
        self.assertEqual((self.store.bank_movements[0]["account"], self.store.bank_movements[0]["delta"]),
                         (7, D("12864.00")))
        self.assertEqual(self.store.cash_movements, [])

    def test_amount_must_match_exactly(self):
        self.assertRefused("AMOUNT_MISMATCH", draft(replacements=(("301", "200"),), payments=(cash("12000.00"),)))
        preview = self.service.preview(draft(replacements=(("301", "200"),), payments=(cash("12000.00"),)))
        self.assertEqual(preview["blocking_problems"][0]["code"], "AMOUNT_MISMATCH")

    def test_zero_difference_takes_no_money(self):
        self.assertRefused("NO_DIFFERENCE_NO_MONEY", draft(payments=(cash("10.00"),)))

    def test_wrong_direction_is_refused(self):
        self.assertRefused("PAYMENT_DIRECTION",
                           draft(replacements=(("301", "200"),), payments=(cash("12864.00", direction="OUT"),)))

    def test_replacement_making_required_only_for_more_gold(self):
        preview = self.service.preview(draft(replacements=(("301", None),)))
        self.assertTrue(preview["making_missing"])
        self.assertEqual(preview["blocking_problems"][0]["code"], "REPLACEMENT_MAKING_REQUIRED")
        self.assertEqual(preview["gold_amount"], "12464.00")                # the gold part is still shown
        self.assertRefused("REPLACEMENT_MAKING_REQUIRED", draft(replacements=(("301", None),),
                                                                payments=(cash("12464.00"),)))
        self.assertFalse(self.service.preview(draft(replacements=(("302", None),)))["making_required"])

    def test_making_out_of_range_opens_a_review_but_does_not_block(self):
        d = draft(replacements=(("301", "300"),))
        preview = self.service.preview(d)
        self.assertTrue(preview["making_review_required"])
        signed = preview["signed_amount"]
        self.finalize(draft(replacements=(("301", "300"),), payments=(cash(signed),)))
        (review,) = self.store.reviews
        self.assertEqual((review["review_type"], review["invoice_id"]), ("MAKING_PRICE", "500"))


class ValidationTests(ExchangeTestCase):
    def test_history_view_alone_cannot_exchange(self):
        self.session._establish(AuthenticationResult(18, None, "مشاهد", frozenset({"APP_LOGIN", "INVOICE_VIEW"}),
                                                     _VERIFIED), int(self.session.generation))
        with self.assertRaises(PermissionError):
            self.service.preview(draft())
        with self.assertRaises(PermissionError):
            self.finalize(draft())
        self.assertFalse(self.service.can_exchange())

    def test_replacement_must_be_in_stock_and_distinct(self):
        self.assertRefused("ITEM_UNAVAILABLE", draft(replacements=(("304", None),)))
        self.assertRefused("DUPLICATE_REPLACEMENT", draft(replacements=(("300", None), ("300", None))))
        self.assertRefused("ITEM_NOT_FOUND", draft(replacements=(("٣٠٠", None),)))

    def test_returned_item_must_still_be_sold(self):
        self.store.items["200"]["inventory_status"] = "IN_STOCK"
        self.assertRefused("RETURNED_NOT_SOLD", draft())

    def test_line_must_belong_to_the_invoice(self):
        self.assertRefused("LINE_NOT_IN_INVOICE", draft(returned=("7",)))

    def test_invoice_must_be_a_native_finalized_sale(self):
        for field_name, value, code in (("record_origin", "LEGACY", "INVOICE_LEGACY"),
                                        ("invoice_status", "VOIDED", "INVOICE_NOT_FINALIZED"),
                                        ("invoice_type", "BUY", "INVOICE_NOT_SALE")):
            with self.subTest(field=field_name):
                saved = self.store.invoices["500"][field_name]
                self.store.invoices["500"][field_name] = value
                self.assertRefused(code, draft())
                self.store.invoices["500"][field_name] = saved

    def test_stale_price_token_is_refused_before_writing(self):
        before = self.store.tables()
        with self.assertRaises(PriceChanged):
            self.finalize(draft(), token="OLD")
        self.assertEqual(self.store.tables(), before)

    def test_empty_sides_are_refused(self):
        self.assertRefused("RETURNED_REQUIRED", draft(returned=()))
        self.assertRefused("REPLACEMENT_REQUIRED", draft(replacements=()))


class RollbackTests(ExchangeTestCase):
    def test_failure_after_writes_rolls_everything_back(self):
        for step in ("add_inventory_movement", "add_cash_movement"):
            with self.subTest(step=step):
                LifecycleRepo.fail_on = step
                before = self.store.tables()
                with self.assertRaises(RuntimeError):
                    self.finalize(draft(replacements=(("301", "200"),), payments=(cash("12864.00"),)))
                self.assertEqual(self.store.tables(), before)
                self.assertEqual(self.store.items["200"]["inventory_status"], "SOLD")
                self.assertEqual(self.store.items["301"]["inventory_status"], "IN_STOCK")
        LifecycleRepo.fail_on = None
        self.finalize(draft(replacements=(("301", "200"),), payments=(cash("12864.00"),)))
        self.assertEqual(len(self.store.lifecycle), 1)

    def test_preview_writes_nothing(self):
        before = self.store.tables()
        self.service.preview(draft(replacements=(("301", "200"),), payments=(cash("12864.00"),)))
        self.assertEqual(self.store.tables(), before)


class ReplacementSearchTests(ExchangeTestCase):
    def test_search_offers_in_stock_pieces_only(self):
        found = self.service.search_replacements(karat_id=1, weight="12", tolerance="0")["items"]
        self.assertEqual([i["inventory_item_id"] for i in found], ["301"])
        ids = [i["inventory_item_id"] for i in self.service.search_replacements()["items"]]
        self.assertNotIn("304", ids)                                   # SOLD
        self.assertNotIn("200", ids)                                   # the sold original piece
        excluded = self.service.search_replacements(karat_id=1, exclude_ids=("300",))["items"]
        self.assertNotIn("300", [i["inventory_item_id"] for i in excluded])

    def test_search_needs_the_exchange_permission_and_a_valid_weight(self):
        with self.assertRaises(ExchangeError):
            self.service.search_replacements(weight="abc")
        self.session._establish(AuthenticationResult(18, None, "مشاهد", frozenset({"APP_LOGIN", "INVOICE_VIEW"}),
                                                     _VERIFIED), int(self.session.generation))
        with self.assertRaises(PermissionError):
            self.service.search_replacements()

    def test_a_searched_piece_is_recorded_as_search(self):
        d = ExchangeDraft("500", ("1",), (ReplacementInput("300", None, "SEARCH"),), (), None)
        self.finalize(d)
        self.assertEqual(self.store.lifecycle_lines[-1]["inventory_item_id"], "300")


def manual(weight="12.000", making="0", reason="NO_BARCODE", notes=None, item_type=2, karat=1):
    return ReplacementInput(None, making, "MANUAL", karat_id=karat, weight_grams=weight, item_type_id=item_type,
                            reason_code=reason, notes=notes)


def from_old_gold(weight="12.000", making="0", karat=1, description="دبلة سادة"):
    return ReplacementInput(None, making, "OLD_GOLD_STOCK", karat_id=karat, weight_grams=weight,
                            description=description)


class ReplacementSourceTests(ExchangeTestCase):
    """Exchange replacements are added the same four ways Billing adds a sold piece."""

    def run_exchange(self, replacement, payments=None):
        d = ExchangeDraft("500", ("1",), (replacement,), payments or (), None)
        preview = self.service.preview(d)
        if payments is None and preview["direction"] != "NONE":
            d = ExchangeDraft("500", ("1",), (replacement,), (cash(preview["amount"]),), None)
        return preview, self.finalize(d)

    def test_barcode_and_search_replacements_are_identified_stock(self):
        for source in ("SCAN", "SEARCH"):
            with self.subTest(source=source):
                self.setUp()
                self.run_exchange(ReplacementInput("300", None, source))
                line = self.store.lifecycle_lines[-1]
                self.assertEqual((line["source"], line["inventory_item_id"]), (source, "300"))
                self.assertEqual(self.store.items["300"]["inventory_status"], "SOLD")

    def test_f5_manual_replacement_has_no_inventory_identity_and_opens_billing_review(self):
        from services.invoice_service import REASONS
        preview, result = self.run_exchange(manual())
        self.assertEqual((preview["valuation_basis"], preview["valued_delta_grams"]), ("SAME_KARAT", "2.000"))
        line = self.store.lifecycle_lines[-1]
        self.assertEqual((line["source"], line["inventory_item_id"], line["old_gold_movement_id"]),
                         ("MANUAL", None, None))                          # never a fake inventory id
        self.assertEqual(line["item_name"], "خاتم")                         # Billing's own item naming
        # The same review as Billing F5: an UNIDENTIFIED_SALE reconciliation case with Billing's reason.
        (case,) = self.store.cases
        self.assertEqual((case["reason_code"], case["reason"], case["item_type_id"], case["category_id"]),
                         ("NO_BARCODE", REASONS["NO_BARCODE"], 2, 1))
        self.assertEqual(case["lifecycle_line_id"], "2")
        self.assertEqual(result["unidentified_cases"], 1)
        self.assertEqual([m for m in self.store.inventory_movements if m[1] == "EXCHANGE_REPLACEMENT_OUT"], [])

    def test_f5_reasons_follow_billing_rules(self):
        from services.invoice_service import REASONS
        self.assertRefused("REASON_REQUIRED", ExchangeDraft("500", ("1",), (manual(reason=None),)))
        self.assertRefused("REASON_NOTES_REQUIRED", ExchangeDraft("500", ("1",), (manual(reason="OTHER"),)))
        self.assertRefused("ITEM_TYPE_REQUIRED", ExchangeDraft("500", ("1",), (manual(item_type=None),)))
        context = self.service.context("500")
        self.assertEqual([r["code"] for r in context["reasons"]], list(REASONS))       # the authoritative list
        self.run_exchange(manual(reason="OTHER", notes="القطعة بدون ملصق"))
        self.assertEqual(self.store.cases[-1]["notes"], "القطعة بدون ملصق")

    def test_manual_making_outside_range_raises_the_same_making_review_as_billing(self):
        # Category 1 (from item type 2) allows 150–250 ج/جرام; 300 → MAKING_PRICE review, never blocked.
        preview, _ = self.run_exchange(manual(making="300"))
        self.assertTrue(preview["making_review_required"])
        (review,) = self.store.reviews
        self.assertEqual((review["review_type"], review["invoice_id"]), ("MAKING_PRICE", "500"))

    def test_f7_replacement_takes_exact_weight_from_the_old_gold_stock(self):
        self.run_exchange(from_old_gold("12.000"))
        self.assertEqual(self.store.old_gold[1], D("8.000"))               # 20.000 − 12.000 exactly
        (movement,) = self.store.old_gold_movements
        self.assertEqual((movement["code"], movement["delta"]), ("EXCHANGE_REPLACEMENT_OUT", D("-12.000")))
        line = self.store.lifecycle_lines[-1]
        self.assertEqual((line["source"], line["inventory_item_id"], line["old_gold_movement_id"]),
                         ("OLD_GOLD_STOCK", None, "1"))
        self.assertEqual(self.store.cases, [])                                 # F7 is not an unidentified piece
        self.assertEqual(self.store.lifecycle[0]["details"]["replacement_sources"], ["OLD_GOLD_STOCK"])

    def test_f7_cannot_exceed_the_stock_before_the_exchange(self):
        d = ExchangeDraft("500", ("1",), (from_old_gold("25.000"),))
        problems = [p["code"] for p in self.service.preview(d)["blocking_problems"]]
        self.assertEqual(problems[0], "OLD_GOLD_STOCK_INSUFFICIENT")
        self.assertRefused("OLD_GOLD_STOCK_INSUFFICIENT", d)
        # The returned piece becomes RETURNED_PENDING inventory — never old-gold stock for the same exchange.
        self.run_exchange(from_old_gold("20.000"))
        self.assertEqual((self.store.old_gold[1], self.store.items["200"]["inventory_status"]),
                         (D("0.000"), "RETURNED_PENDING"))

    def test_f7_needs_a_description_like_billing(self):
        self.assertRefused("DESCRIPTION_REQUIRED", ExchangeDraft("500", ("1",), (from_old_gold(description=""),)))

    def test_duplicates_apply_to_identified_pieces_only(self):
        self.assertRefused("DUPLICATE_REPLACEMENT", ExchangeDraft("500", ("1",), (
            ReplacementInput("300"), ReplacementInput("300", None, "SEARCH"))))
        # Two manual pieces with the same description are two pieces (no identity to compare).
        d = ExchangeDraft("500", ("1",), (manual("5.000"), manual("5.000")))
        preview = self.service.preview(d)
        self.assertEqual(preview["replacement_pure_grams"], "8.750")
        self.finalize(ExchangeDraft("500", ("1",), (manual("5.000"), manual("5.000"))))
        self.assertEqual(len(self.store.cases), 2)

    def test_every_source_feeds_the_same_calculation(self):
        results = []
        for replacement in (ReplacementInput("301", "0"), manual("12.000"), from_old_gold("12.000")):
            d = ExchangeDraft("500", ("1",), (replacement,))
            p = self.service.preview(d)
            results.append((p["valued_delta_grams"], p["gold_amount"], p["price_per_gram"]))
        self.assertEqual(len(set(results)), 1)                                  # identical result for 12 g 21K


class SettlementBarTests(ExchangeTestCase):
    """The settlement shown with the result (ViewModel display over the service preview)."""

    def model(self, replacement, amount="", account="", method="PHYSICAL_CASH"):
        from app.viewmodels.exchange_viewmodel import ExchangeViewModel
        vm = ExchangeViewModel(self.session, self.service)
        vm._context = self.service.context("500")
        vm._selected = ["1"]
        vm._replacements = [dict(replacement, key="r1")]
        vm._method, vm._amount, vm._account = method, amount, account
        vm._preview = self.service.preview(vm._draft())
        return vm

    def identified(self, item_id, weight, pure, making="0"):
        return {"inventory_item_id": item_id, "identified_by": "SCAN", "making": making, "item_name": "خاتم",
                "karat": 21, "weight_grams": weight, "pure_grams": pure}

    def test_customer_owes_exposes_money_in(self):
        s = self.model(self.identified("301", "12.000", "10.500")).settlement
        self.assertEqual((s["direction"], s["resultLabel"], s["due"], s["needsMoney"], s["moneyLabel"]),
                         ("CUSTOMER_OWES", "على العميل", "12,464.00 ج", True, "يستلم المحل"))
        self.assertEqual((s["accountLabel"], s["remainingText"], s["complete"]),
                         ("على حساب العميل", "المتبقي 12,464.00 ج", False))

    def test_shop_owes_exposes_money_out(self):
        s = self.model(self.identified("302", "8.000", "7.000")).settlement
        self.assertEqual((s["resultLabel"], s["due"], s["moneyLabel"], s["accountLabel"]),
                         ("للعميل", "12,714.00 ج", "يدفع المحل للعميل", "للعميل على حسابه"))

    def test_zero_shows_no_payment(self):
        vm = self.model(self.identified("300", "10.000", "8.750"))
        self.assertEqual(vm.settlement, {"direction": "NONE", "resultLabel": "لا يوجد فرق مالي", "due": "",
                                         "needsMoney": False, "remainingText": "", "complete": True})
        self.assertTrue(vm.canFinalize)

    def test_account_selector_follows_the_method(self):
        vm = self.model(self.identified("301", "12.000", "10.500"))
        self.assertFalse(vm.methodNeedsAccount)
        vm._method = "INSTAPAY"
        self.assertTrue(vm.methodNeedsAccount)
        self.assertEqual([a["name"] for a in vm.bankAccounts], ["حساب المحل"])   # only eligible (BANK) accounts

    def test_customer_account_is_explicit_and_must_complete_exactly(self):
        part = self.model(self.identified("301", "12.000", "10.500"), amount="10000")
        self.assertEqual((part.settlement["remainingText"], part.canFinalize), ("المتبقي 2,464.00 ج", False))
        full = self.model(self.identified("301", "12.000", "10.500"), amount="10000", account="2464")
        self.assertEqual((full.settlement["remainingText"], full.canFinalize), ("التسوية مكتملة", True))
        over = self.model(self.identified("301", "12.000", "10.500"), amount="12465")
        self.assertEqual((over.settlement["complete"], over.canFinalize), (False, False))

    def test_f5_and_f7_use_the_same_settlement(self):
        bars = []
        for row in ({"inventory_item_id": None, "identified_by": "MANUAL", "making": "0", "item_name": "خاتم", "karat": 21,
                     "karat_id": 1, "weight_grams": "12.000", "item_type_id": 2, "reason_code": "NO_BARCODE"},
                    {"inventory_item_id": None, "identified_by": "OLD_GOLD_STOCK", "making": "0", "item_name": "دبلة",
                     "karat": 21, "karat_id": 1, "weight_grams": "12.000", "description": "دبلة"},
                    self.identified("301", "12.000", "10.500")):
            s = self.model(row).settlement
            bars.append((s["resultLabel"], s["due"], s["moneyLabel"]))
        self.assertEqual(len(set(bars)), 1)

    def test_settlement_bar_is_fixed_above_save_not_in_the_scroll(self):
        from pathlib import Path
        qml = (Path(__file__).resolve().parents[1] / "qml" / "ExchangeWorkspace.qml").read_text(encoding="utf-8")
        scroll = qml[qml.index('objectName: "exchangeEditScroll"'):qml.index('objectName: "exchangeSettlementBar"')]
        bar = qml[qml.index('objectName: "exchangeSettlementBar"'):]
        for name in ("exchangeMethod", "exchangeBankAccount", "exchangeAmount", "exchangeAccountAmount", "exchangeSave"):
            self.assertNotIn(f'"{name}"', scroll)                         # never below the fold
            self.assertIn(f'"{name}"', bar)
        self.assertLess(bar.index('"exchangeMoneySection"'), bar.index('"exchangeSave"'))    # settle, then save
        self.assertIn("anchors.bottom: actionBar.top", qml)                                   # scroll ends above it


class HistoryPresentationTests(unittest.TestCase):
    """Presentation only: shop-sold weights and the customer's old gold stay separate."""

    def vm(self, detail):
        from app.viewmodels.invoice_history_viewmodel import InvoiceHistoryViewModel
        model = InvoiceHistoryViewModel(AppSession())
        model._detail = detail
        return model

    def test_sold_and_old_gold_totals_are_separate(self):
        model = self.vm({"sale_lines": [{"karat": 21, "weight": D("3.500")}, {"karat": 18, "weight": D("2.250")},
                                        {"karat": 21, "weight": D("1.000")}],
                         "old_gold_lines": [{"karat": 21, "weight": D("5.000"), "line_total": D("30000.00")}]})
        sold = model.soldSummary
        self.assertEqual((sold["count"], sold["total"]), ("3", "6.750"))
        self.assertEqual(sold["karats"], [{"karat": "21", "weight": "4.500"}, {"karat": "18", "weight": "2.250"}])
        old = model.oldGoldSummary
        self.assertEqual((old["count"], old["total"], old["value"]), ("1", "5.000", "30,000.00 ج"))
        self.assertEqual(self.vm({"sale_lines": [], "old_gold_lines": []}).oldGoldSummary, {})

    def test_exchange_is_shown_inline_with_its_own_facts_only(self):
        from services.exchange_service import exchange_summary
        row = {"lifecycle_id": "2", "source_invoice_id": "23", "source_invoice_number": "26092802",
               "occurred_at": datetime(2026, 9, 28, 17, 4), "performed_by_name": "admin",
               "lines": [{"line_role": "RETURNED", "inventory_item_id": "25", "item_name_snapshot": "خاتم",
                          "barcode_snapshot": None, "karat_value": 21, "weight_grams": D("3.500"),
                          "pure_weight_grams": D("3.062500"), "making_per_gram": None},
                         {"line_role": "REPLACEMENT", "inventory_item_id": "26", "item_name_snapshot": "خاتم",
                          "barcode_snapshot": None, "karat_value": 21, "weight_grams": D("4.000"),
                          "pure_weight_grams": D("3.500000"), "making_per_gram": D("250.00")}],
               "payments": [{"payment_method_code": "PHYSICAL_CASH", "direction": "IN", "amount": D("3242.00"),
                             "bank_account_name": None}],
               "returned_pure_grams": D("3.062500"), "replacement_pure_grams": D("3.500000"),
               "pure_delta_grams": D("0.437500"), "valuation_basis": "SAME_KARAT", "valued_delta_grams": D("0.500000"),
               "price_basis": "SHOP_SELL", "price_karat_value": 21, "price_per_gram": D("6234.0000"),
               "gold_amount": D("3117.00"), "making_charge": D("125.00"), "making_refund": D("0.00"),
               "making_refund_eligible": 0, "signed_amount": D("3242.00"), "money_received": D("3242.00"),
               "money_paid": D("0.00"), "account_signed": D("0.00"), "notes": None}
        (x,) = self.vm({"exchanges": [exchange_summary(row)]}).exchanges
        self.assertEqual((x["code"], x["directionText"], x["amount"]), ("EX000002", "على العميل", "3,242.00 ج"))
        self.assertEqual(x["deltaText"], "فرق الوزن ⁦+0.500⁩")
        self.assertEqual([p["weight"] for p in x["returned"] + x["replacements"]], ["3.500", "4.000"])
        self.assertIn("مصنعية الذهب الزائد 125.00 ج", x["makingText"])
        self.assertEqual((x["moneyTitle"], x["money"], x["accountText"]), ("المستلم من العميل", "نقدي: 3,242.00 ج", ""))

    # ---- directional money wording (never the ambiguous «الدفع الفعلي»)
    def direction(self, received, paid):
        from app.viewmodels.invoice_history_viewmodel import InvoiceHistoryViewModel
        return InvoiceHistoryViewModel.money_direction({"money_received": D(received), "money_paid": D(paid)})

    def test_sale_shows_received_from_customer(self):
        self.assertEqual(self.direction("22519.00", "0.00"), ("المستلم من العميل", "22,519.00 ج"))

    def test_old_gold_purchase_shows_paid_to_customer(self):
        self.assertEqual(self.direction("0.00", "20800.00"), ("المدفوع للعميل", "20,800.00 ج"))

    def test_mixed_invoice_follows_the_saved_money_direction(self):
        # Sale + customer old gold: the saved legs decide, not the presence of old gold.
        self.assertEqual(self.direction("5000.00", "0.00")[0], "المستلم من العميل")
        self.assertEqual(self.direction("0.00", "3000.00")[0], "المدفوع للعميل")

    def test_no_money_is_never_shown_as_a_direction(self):
        self.assertEqual(self.direction("0.00", "0.00"), ("لا يوجد دفع فعلي", ""))

    def exchange_row(self, signed, received, paid, account="0.00", payments=()):
        from services.exchange_service import exchange_summary
        return exchange_summary({
            "lifecycle_id": "9", "source_invoice_id": "1", "source_invoice_number": "26092801",
            "occurred_at": datetime(2026, 9, 28, 12, 0), "performed_by_name": "admin", "lines": [],
            "payments": [{"payment_method_code": "PHYSICAL_CASH", "direction": d, "amount": D(a),
                          "bank_account_name": None} for d, a in payments],
            "returned_pure_grams": D("3.5"), "replacement_pure_grams": D("3.5"), "pure_delta_grams": D("0"),
            "valuation_basis": "SAME_KARAT", "valued_delta_grams": D("0.000000"), "price_basis": None,
            "price_karat_value": None, "price_per_gram": None, "gold_amount": D("0.00"), "making_charge": D("0.00"),
            "making_refund": D("0.00"), "making_refund_eligible": 0, "signed_amount": D(signed),
            "money_received": D(received), "money_paid": D(paid), "account_signed": D(account), "notes": None})

    def test_exchange_money_titles(self):
        owes = self.vm({"exchanges": [self.exchange_row("100.00", "100.00", "0.00", payments=[("IN", "100.00")])]})
        self.assertEqual(owes.exchanges[0]["moneyTitle"], "المستلم من العميل")
        credit = self.vm({"exchanges": [self.exchange_row("-80.00", "0.00", "80.00", payments=[("OUT", "80.00")])]})
        self.assertEqual((credit.exchanges[0]["moneyTitle"], credit.exchanges[0]["money"]),
                         ("المدفوع للعميل", "نقدي: 80.00 ج"))
        zero = self.vm({"exchanges": [self.exchange_row("0.00", "0.00", "0.00")]}).exchanges[0]
        self.assertEqual((zero["moneyTitle"], zero["money"], zero["amount"]), ("لا يوجد دفع فعلي", "", ""))
        # All on the customer account: no real money, and the account stays a separate line.
        on_account = self.vm({"exchanges": [self.exchange_row("-80.00", "0.00", "0.00", account="-80.00")]}).exchanges[0]
        self.assertEqual((on_account["moneyTitle"], on_account["accountText"]),
                         ("لا يوجد دفع فعلي", "حساب العميل: للعميل 80.00 ج"))

    def test_history_page_uses_only_directional_wording_and_the_old_gold_rules(self):
        from pathlib import Path
        qml = (Path(__file__).resolve().parents[1] / "qml" / "InvoiceHistoryPage.qml").read_text(encoding="utf-8")
        self.assertNotIn("الدفع الفعلي", qml)
        self.assertIn('objectName: "historyCustomerBand"', qml)
        for rule in ('visible: !detailScroll.d.isOldGoldOnly',                     # gold & making card
                     'visible: !!historyPage.vm && historyPage.vm.saleLines.length > 0',    # empty sold section
                     'visible: !!detailRoot.oldGold.count'):                                # empty old-gold section
            self.assertIn(rule, qml)
        self.assertNotIn("لا توجد أصناف مباعة", qml)

    def test_history_page_follows_the_owner_order_and_drops_duplicates(self):
        from pathlib import Path
        qml = (Path(__file__).resolve().parents[1] / "qml" / "InvoiceHistoryPage.qml").read_text(encoding="utf-8")
        detail = qml[qml.index("// ============================================================ DETAIL"):]
        order = [detail.index(f'objectName: "{name}"') for name in (
            "historyTopRow", "historyCustomerSection", "historyMoneySection", "historyPricesSection",
            "historyItemsRow", "historyItemsSection", "historyOldGoldSection", "historyExchangesSection",
            "historyHeaderSection")]
        self.assertEqual(order, sorted(order))                              # the exact owner order
        for gone in ("سجل العمليات", "historyTimelineSection", "historyPaymentsSection", "إجمالي المصنعية"):
            self.assertNotIn(gone, detail)
        strip = detail[detail.index('objectName: "historySummaryStrip"'):]
        for field in ('"historyInvoiceTotal"', "detailScroll.d.movedLabel", '"historyAccount"', '"historyFinalizedAt"',
                      '"historyOperation"'):
            self.assertIn(field, strip)
        # The historical price is readable at the page's value level (never a caption, never dashboard size).
        prices = detail[detail.index('objectName: "historyPricesSection"'):detail.index('objectName: "historyItemsRow"')]
        self.assertIn("theme.fontSection", prices)
        self.assertNotIn("fontCaption", prices)
        self.assertNotIn("fontNumber", prices)

    def test_sold_items_and_old_gold_share_one_row_only_when_both_exist(self):
        from pathlib import Path
        qml = (Path(__file__).resolve().parents[1] / "qml" / "InvoiceHistoryPage.qml").read_text(encoding="utf-8")
        row = qml[qml.index('objectName: "historyItemsRow"'):qml.index('objectName: "historyExchangesSection"')]
        # Both sections are children of the same two-column row; the row pairs them only for MIXED content.
        self.assertIn('objectName: "historyItemsSection"', row)
        self.assertIn('objectName: "historyOldGoldSection"', row)
        self.assertLess(row.index('"historyItemsSection"'), row.index('"historyOldGoldSection"'))   # sold on the right
        self.assertIn("columns: detailRoot.itemsPaired ? 2 : 1", row)
        paired = qml[qml.index("readonly property bool itemsPaired"):]
        paired = paired[:paired.index("\n\n")]
        self.assertIn("saleLines.length > 0", paired)
        self.assertIn("oldGold.count", paired)
        self.assertNotIn("invoice_type", paired)
        self.assertEqual(row.count("Layout.preferredWidth: 1; Layout.fillHeight: true"), 2)          # equal weight

    def test_sold_table_keeps_factory_size_and_notes_in_every_mode(self):
        from pathlib import Path
        qml = (Path(__file__).resolve().parents[1] / "qml" / "InvoiceHistoryPage.qml").read_text(encoding="utf-8")
        sold = qml[qml.index('objectName: "historyItemsSection"'):qml.index('objectName: "historySoldTotals"')]
        header = sold[sold.index('objectName: "historySoldHeader"'):sold.index("Repeater {")]
        for column in ("المصنع", "المقاس", "ملاحظات", "الصنف", "العيار", "الوزن", "النوع", "الباركود"):
            line = next(l for l in header.splitlines() if f'"{column}"' in l)
            self.assertNotIn("visible", line, column)                  # never conditionally hidden
        self.assertNotIn("visible: detailRoot.wide", sold)
        self.assertNotIn("!detailRoot.itemsPaired", sold)
        import re
        for cell in ('"historySoldFactory_"', '"historySoldSize_"', '"historySoldNotes_"'):
            block = sold[sold.index(cell):]
            # No own `visible:` condition (the tooltip's `ToolTip.visible` is fine).
            self.assertIsNone(re.search(r"(?<!ToolTip\.)\bvisible:", block[:block.index("}")]), cell)
        self.assertIn('soldRow.modelData.factory || "—"', sold)                 # neutral placeholder
        self.assertIn('soldRow.modelData.size || "—"', sold)
        self.assertEqual(sold.count("ToolTip.text: value"), 4)                  # factory, type, barcode, notes

    def test_paired_tables_pad_the_shorter_one_with_visual_rows_only(self):
        from pathlib import Path
        qml = (Path(__file__).resolve().parents[1] / "qml" / "InvoiceHistoryPage.qml").read_text(encoding="utf-8")
        # target_rows = max(sold, old gold); the shorter paired table gets (target - its rows) empty Items.
        self.assertIn("Math.max(historyPage.vm.saleLines.length, historyPage.vm.oldGoldLines.length)", qml)
        for filler, lines in (("historySoldFillers", "saleLines"), ("historyOldGoldFillers", "oldGoldLines")):
            block = qml[qml.index(f'objectName: "{filler}"'):]
            block = block[:block.index("}\n                    }") if "}\n                    }" in block else 400]
            self.assertIn("detailRoot.itemsPaired && historyPage.vm", block)        # paired mode only
            self.assertIn(f"detailRoot.targetRows - historyPage.vm.{lines}.length", block)
            self.assertIn("delegate: Item {", block)                               # visual only, no data
        # Both tables use one fixed row height, so padded bodies end on the same line.
        self.assertEqual(qml.count("Layout.preferredHeight: detailRoot.rowHeight"), 4)
        # The totals come from the saved lines only: fillers never enter counts or weights.
        model = self.vm({"sale_lines": [{"karat": 21, "weight": D("3.500")}] * 3,
                         "old_gold_lines": [{"karat": 21, "weight": D("3.500"), "line_total": D("20915.98")}]})
        self.assertEqual((model.soldSummary["count"], model.soldSummary["total"]), ("3", "10.500"))
        self.assertEqual((model.oldGoldSummary["count"], model.oldGoldSummary["total"]), ("1", "3.500"))

    def test_weights_show_no_unit_but_money_keeps_its_currency(self):
        from app.formatting import grams, money
        self.assertEqual(grams(D("3.500")), "3.500")
        self.assertEqual(grams(D("7")), "7.000")
        self.assertEqual(money(D("24000")), "24,000.00 ج")                     # currency stays
        model = self.vm({"sale_lines": [{"karat": 21, "weight": D("4.500")}, {"karat": 18, "weight": D("2.250")}],
                         "old_gold_lines": []})
        self.assertEqual(model.soldSummary["karats"], [{"karat": "21", "weight": "4.500"},
                                                       {"karat": "18", "weight": "2.250"}])
        from pathlib import Path
        root = Path(__file__).resolve().parents[1] / "qml"
        history = (root / "InvoiceHistoryPage.qml").read_text(encoding="utf-8")
        billing = (root / "InvoicePage.qml").read_text(encoding="utf-8")
        self.assertNotIn(" جم", history)
        self.assertNotIn("/جم", history)
        self.assertIn('HeadCell { text: "الوزن"', billing)
        self.assertNotIn('model.weight + " جم"', billing)

    def test_weight_unit_is_written_jaram_never_jm(self):
        """Weight unit = جرام (only where a unit is needed); currency = ج; labelled weights stay unitless."""
        import re
        from pathlib import Path
        from app.formatting import grams, money
        from services.exchange_service import MAKING_REQUIRED_TEXT
        root = Path(__file__).resolve().parents[1]
        standalone = re.compile(r"(?<![ء-ي])جم(?![ء-ي])")
        for folder in ("app", "services", "repositories", "qml"):
            for path in (root / folder).rglob("*"):
                if path.suffix in (".py", ".qml"):
                    self.assertIsNone(standalone.search(path.read_text(encoding="utf-8")), str(path))
        self.assertIn("ج/جرام", MAKING_REQUIRED_TEXT)                              # rate wording
        qml = (root / "qml" / "ExchangeWorkspace.qml").read_text(encoding="utf-8")
        self.assertIn('"مصنعية ج/جرام"', qml)
        main = (root / "qml" / "Main.qml").read_text(encoding="utf-8")
        for label in ("سعر بيع 21 / جرام", "سعر شراء 21 / جرام", '"ذهب/جرام: "', '"شراء/جرام: "', '" ج/جرام"'):
            self.assertIn(label, main)
        self.assertEqual(grams(D("3.500")), "3.500")                                # labelled: still unitless
        self.assertEqual(money(D("24000")), "24,000.00 ج")                          # currency unchanged

    def test_top_row_cards_share_one_height_and_prices_are_readable(self):
        from pathlib import Path
        qml = (Path(__file__).resolve().parents[1] / "qml" / "InvoiceHistoryPage.qml").read_text(encoding="utf-8")
        top = qml[qml.index('objectName: "historyTopRow"'):qml.index('objectName: "historyItemsRow"')]
        self.assertEqual(top.count("Layout.fillHeight: true"), 3)
        self.assertNotIn("Qt.AlignTop", top)
        prices = top[top.index('"historyPricesSection"'):]
        self.assertEqual(prices.count("font.pixelSize: theme.fontSection"), 3)  # karat + sell + buy
        self.assertNotIn("fontCaption", prices)
        self.assertNotIn("fontNumber", prices)                                   # not dashboard-size

    def test_historical_prices_show_only_the_used_karats(self):
        snapshots = [{"karat": k, "sell": D(s), "buy": D(b), "source": "GOLDTIMO"}
                     for k, s, b in ((18, "5344", "5322"), (21, "6234", "6209"), (24, "7125", "7096"))]
        only21 = self.vm({"sale_lines": [{"karat": 21, "weight": D("3.500")}], "old_gold_lines": [],
                          "price_snapshots": snapshots})
        self.assertEqual([(p["karat"], p["sell"], p["buy"]) for p in only21.priceSnapshots],
                         [("21", "6,234.00 ج", "6,209.00 ج")])
        # Sold 21K + customer old gold 18K: exactly those two karats.
        mixed = self.vm({"sale_lines": [{"karat": 21, "weight": D("1")}],
                         "old_gold_lines": [{"karat": 18, "weight": D("2"), "line_total": D("1")}],
                         "price_snapshots": snapshots})
        self.assertEqual([p["karat"] for p in mixed.priceSnapshots], ["18", "21"])

    # ---- invoice kind from CONTENT, independent of the money direction
    def invoice_detail(self, *, sold, old_gold, received="0.00", paid="0.00", stored_type="MIXED"):
        from services.invoice_history_service import transaction_kind
        sale_lines = [{"karat": 21, "weight": D("3.500"), "kind": "INVENTORY", "inventory_item_id": str(40 + i)}
                      for i in range(sold)]
        old_lines = [{"karat": 21, "weight": D("15.000"), "line_total": D("91811.85")} for _ in range(old_gold)]
        return {"economics": {"gold_value": None, "average_making": None, "making_total": None, "making_status": None},
                "customer": {}, "net_direction": "SHOP_PAYS_CUSTOMER" if D(paid) else "CUSTOMER_PAYS_SHOP",
                "account_signed": D("0.00"), "is_legacy": False, "sale_lines": sale_lines, "old_gold_lines": old_lines,
                "transaction_kind": transaction_kind(sale_lines, old_lines),
                "invoice_id": "30", "invoice_number": "26092808", "finalized_at": datetime(2026, 9, 28, 18, 0),
                # The stored type is deliberately NOT trusted for the presentation.
                "invoice_type": stored_type, "invoice_status": "FINALIZED", "settlement_status": "SETTLED",
                "legacy_label": "", "invoice_total": D("22000.00"), "money_received": D(received),
                "money_paid": D(paid), "payments": [], "can_see_manager_details": False, "buy_total": D("91811.85")}

    def shown(self, **kwargs):
        model = self.vm({})
        model._detail = self.invoice_detail(**kwargs)
        return model.detail

    def test_kind_sold_only_is_sale(self):
        d = self.shown(sold=1, old_gold=0, received="22000.00", stored_type="SELL")
        self.assertEqual((d["transactionKind"], d["typeText"], d["isOldGoldOnly"]), ("SALE", "بيع", False))

    def test_kind_old_gold_only_is_old_gold_purchase(self):
        d = self.shown(sold=0, old_gold=1, paid="20800.00", stored_type="BUY")
        self.assertEqual((d["transactionKind"], d["typeText"], d["isOldGoldOnly"]),
                         ("OLD_GOLD_PURCHASE", "شراء كسر", True))

    def test_kind_sold_and_old_gold_is_mixed_whatever_the_money_direction(self):
        for received, paid in (("24000.00", "0.00"), ("0.00", "22000.00")):
            with self.subTest(received=received, paid=paid):
                d = self.shown(sold=2, old_gold=1, received=received, paid=paid)
                self.assertEqual((d["transactionKind"], d["typeText"], d["isOldGoldOnly"]),
                                 ("MIXED", "بيع مع كسر", False))
                self.assertEqual(d["movedLabel"], "المستلم من العميل" if D(received) else "المدفوع للعميل")

    def test_regression_mixed_content_with_a_buy_stored_type_is_still_mixed(self):
        # The failing shape: sold piece + customer old gold, money OUT, stored type BUY.
        d = self.shown(sold=1, old_gold=1, paid="22000.00", stored_type="BUY")
        self.assertEqual((d["typeText"], d["settlementText"], d["isOldGoldOnly"], d["movedLabel"]),
                         ("بيع مع كسر", "مسددة", False, "المدفوع للعميل"))

    def test_mixed_page_shows_both_item_sections_and_pure_old_gold_hides_the_sold_one(self):
        from pathlib import Path
        qml = (Path(__file__).resolve().parents[1] / "qml" / "InvoiceHistoryPage.qml").read_text(encoding="utf-8")
        # The sold section depends on the sold lines only; the old-gold section on the old-gold lines only;
        # the gold / making card is hidden ONLY for a pure old-gold purchase.
        self.assertIn('visible: !!historyPage.vm && historyPage.vm.saleLines.length > 0', qml)
        self.assertIn('visible: !!detailRoot.oldGold.count', qml)
        self.assertIn('visible: !detailScroll.d.isOldGoldOnly', qml)
        self.assertNotIn("invoice_type", qml)
        mixed = self.vm({})
        mixed._detail = self.invoice_detail(sold=1, old_gold=1, paid="22000.00", stored_type="BUY")
        self.assertEqual((len(mixed._detail["sale_lines"]), mixed.oldGoldSummary["count"], mixed.soldSummary["count"]), (1, "1", "1"))
        pure = self.vm({})
        pure._detail = self.invoice_detail(sold=0, old_gold=1, paid="20800.00", stored_type="BUY")
        self.assertEqual((pure.soldSummary["count"], pure.detail["isOldGoldOnly"]), ("0", True))

    def test_final_strip_money_fields_stay_separate(self):
        from app.viewmodels.invoice_history_viewmodel import InvoiceHistoryViewModel
        model = self.vm({})
        model._detail = {
            "economics": {"gold_value": None, "average_making": None, "making_total": None, "making_status": None},
            "customer": {}, "net_direction": "SHOP_PAYS_CUSTOMER", "account_signed": D("-80.00"),
            "is_legacy": False, "sale_lines": [], "old_gold_lines": [{"karat": 21, "weight": D("3.5"), "line_total": D("1")}],
            "invoice_id": "7", "invoice_number": "26092807", "finalized_at": datetime(2026, 9, 28, 17, 54),
            "invoice_type": "BUY", "invoice_status": "FINALIZED", "settlement_status": "SETTLED",
            "legacy_label": "", "invoice_total": D("20880.00"), "money_received": D("0.00"),
            "money_paid": D("20800.00"), "payments": [{"method_code": "PHYSICAL_CASH", "direction": "OUT",
                                                       "amount": D("20800.00")}],
            "can_see_manager_details": False, "buy_total": D("20880.00")}
        d = model.detail
        self.assertIsInstance(model, InvoiceHistoryViewModel)
        self.assertEqual((d["movedLabel"], d["movedAmount"], d["paymentMethods"]),
                         ("المدفوع للعميل", "20,800.00 ج", "نقدي"))
        self.assertEqual(d["accountText"], "للعميل: 80.00 ج")                     # never merged into the money
        self.assertEqual((d["typeText"], d["settlementText"]), ("شراء كسر", "مسددة"))
        self.assertTrue(d["isOldGoldOnly"])
        self.assertEqual(d["finalizedAt"], "⁦2026-09-28 17:54⁩")


class HistoryExchangeActionTests(unittest.TestCase):
    def test_exchange_action_hides_when_every_stock_piece_was_exchanged(self):
        lines = [{"kind": "INVENTORY", "inventory_item_id": "25"}, {"kind": "MANUAL", "inventory_item_id": None}]
        taken = [{"returned": [{"inventory_item_id": "25"}]}]
        self.assertTrue(InvoiceHistoryService._has_unexchanged_piece(lines, []))
        self.assertFalse(InvoiceHistoryService._has_unexchanged_piece(lines, taken))
        self.assertTrue(InvoiceHistoryService._has_unexchanged_piece(
            lines + [{"kind": "INVENTORY", "inventory_item_id": "26"}], taken))


class HistoryTimelineTests(unittest.TestCase):
    def test_exchanged_event_carries_amount_and_link(self):
        events = [{"event_id": "1", "event_type": "FINALIZED", "occurred_at": NOW, "performed_by_name": "أ"},
                  {"event_id": "2", "event_type": "EXCHANGED", "occurred_at": NOW + timedelta(hours=1),
                   "performed_by_name": "ب", "details_json": '{"lifecycle_id": "9", "signed_amount": "-120.50"}'}]
        finalized, exchanged = InvoiceHistoryService._timeline(events)
        self.assertIsNone(finalized.amount)
        self.assertEqual((exchanged.event_type, exchanged.amount, exchanged.linked_record_id, exchanged.actor),
                         ("EXCHANGED", D("-120.50"), "9", "ب"))


if __name__ == "__main__":
    unittest.main()
