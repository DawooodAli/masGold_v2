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

    def insert_line(self, *, lifecycle_id, line_role, line_number, line):
        if line.source_line_id and any(l["source_invoice_line_id"] == line.source_line_id
                                       for l in self.store.lifecycle_lines):
            raise RuntimeError("Duplicate entry for key uq_lifecycle_source_line")
        assert (line_role == "RETURNED") == (line.source_line_id is not None)
        assert line.pure == pure_weight(line.weight, line.karat)
        self.store.lifecycle_lines.append({"lifecycle_id": lifecycle_id, "role": line_role, "number": line_number,
                                           "source_invoice_line_id": line.source_line_id,
                                           "inventory_item_id": line.inventory_item_id, "pure": line.pure,
                                           "making_per_gram": line.making_per_gram})

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
