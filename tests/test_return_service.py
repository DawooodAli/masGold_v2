"""Return V1 (ReturnService) on an in-memory store (no database).

The transaction factory snapshots the whole store and restores it on any exception, like a
MySQL rollback. The fake repository enforces the 022 contracts (whole identified piece once,
F7 grams never above the sold weight, OUT money only, one inspection per Return line).
Current SHOP_BUY differs from anything on the original invoice, so a historical or SHOP_SELL
price used by mistake changes the expected amounts.
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
from services.exchange_service import ALREADY_RETURNED_TEXT, ExchangeService
from services.gold_price_service import BillingKaratPrice, BillingPriceSet, PriceChanged
from services.invoice_service import PaymentInput
from services.return_service import (
    EXCHANGED_TEXT, RETURNED_TEXT, ReturnDraft, ReturnError, ReturnSelection, ReturnService, eligibility,
    return_summary,
)


D = Decimal
T0 = datetime(2026, 9, 1, 10, 0, 0)                       # original invoice finalized (local)
KARATS = {1: 21, 2: 18, 3: 24}
PRICES = {1: (D("6250.0000"), D("6400.0000")), 2: (D("5000.0000"), D("5150.0000")), 3: (D("7100.0000"), D("7300.0000"))}


def line(line_id, number, line_type, karat_id, weight, item_id=None, making=None, exchanged=None, name="خاتم"):
    return {"line_id": line_id, "line_number": number, "line_type": line_type, "inventory_item_id": item_id,
            "karat_id": karat_id, "karat_value": KARATS[karat_id], "weight_grams": D(weight),
            "item_name_snapshot": name, "barcode_snapshot": None if item_id is None else f"B{item_id}",
            "making_total": None if making is None else D(making), "line_total": D("1.00"),
            "consumed_by_exchange": exchanged}


class Store:
    def __init__(self):
        self.now = T0 + timedelta(hours=2)
        self.invoices = {"500": {"invoice_id": "500", "invoice_number": "26090101", "invoice_type": "MIXED",
                                 "invoice_status": "FINALIZED", "record_origin": "NATIVE", "customer_id": "12",
                                 "customer_name_snapshot": "عميل", "finalized_at": T0, "invoice_date": T0},
                         "501": {"invoice_id": "501", "invoice_number": "26090102", "invoice_type": "SELL",
                                 "invoice_status": "FINALIZED", "record_origin": "NATIVE", "customer_id": None,
                                 "customer_name_snapshot": None, "finalized_at": T0, "invoice_date": T0}}
        self.lines = {"500": [line("1", 1, "SELL_INVENTORY", 1, "10.000", "200", "1500.00"),
                              line("2", 2, "SELL_INVENTORY", 2, "4.000", "201", "400.00"),
                              line("3", 3, "SELL_OLD_GOLD", 1, "10.000", None, "1000.00", name="دبلة من الكسر"),
                              line("4", 4, "BUY_OLD_GOLD", 1, "5.000", name="كسر العميل"),
                              line("5", 5, "SELL_UNIDENTIFIED", 1, "2.000", name="يدوي"),
                              line("6", 6, "SELL_INVENTORY", 1, "5.000", "202", None),
                              line("7", 7, "SELL_INVENTORY", 1, "6.000", "203", "600.00", exchanged="9")],
                      "501": [line("8", 1, "SELL_INVENTORY", 1, "3.000", "204", "300.00")]}
        self.items = {i: {"inventory_item_id": i, "inventory_status": s, "weight_grams": D(w),
                          "stock_classification": "NEW_STOCK"}
                      for i, s, w in (("200", "SOLD", "10.000"), ("201", "SOLD", "4.000"), ("202", "SOLD", "5.000"),
                                      ("203", "RETURNED_PENDING", "6.000"), ("204", "SOLD", "3.000"))}
        self.customers = {"12": {"customer_id": "12", "is_active": 1, "record_origin": "NATIVE"}}
        self.bank_accounts = {7: {"id": 7, "name": "بنك المحل", "account_type": "BANK", "is_active": 1},
                              8: {"id": 8, "name": "محفظة المحل", "account_type": "MOBILE_WALLET", "is_active": 1}}
        self.cash_balance = D("2000.00")
        # The customer's old gold bought on the same invoice: must stay untouched.
        self.old_gold_movements = [{"code": "CUSTOMER_BUY_IN", "karat_id": 1, "delta": D("5.000"), "ref": "500"}]
        self.returns, self.return_lines, self.return_payments = [], [], []
        self.cash_movements, self.bank_movements, self.inventory_movements = [], [], []
        self.reviews, self.ledger, self.events, self.audit = [], [], [], []

    def tables(self):
        return copy.deepcopy(vars(self))

    def old_gold_balance(self, karat_id=1):
        return sum((m["delta"] for m in self.old_gold_movements if m["karat_id"] == karat_id), D("0.000"))


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
            self.rows = [{"local_now": self.store.now, "utc_now": self.store.now - timedelta(hours=3)}]
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
        return None if row is None else dict(row)

    lock_invoice = get_invoice

    def get_payment_method(self, code):
        ids = {"PHYSICAL_CASH": (1, 0, 1), "INSTAPAY": (3, 1, 0), "BANK_TRANSFER": (2, 1, 0), "MOBILE_WALLET": (5, 1, 0)}
        if code not in ids:
            return None
        pid, bank, cash = ids[code]
        return {"payment_method_id": pid, "code": code, "requires_bank_account": bank, "affects_physical_cash": cash,
                "is_active": 1}

    def active_cash_accounts(self):
        return [{"id": 1, "code": "MAIN", "name": "Main"}]

    def active_bank_accounts(self):
        return list(self.store.bank_accounts.values())

    def get_bank_account(self, account_id, lock=False):
        return self.store.bank_accounts.get(int(account_id))

    def lock_customer(self, customer_id):
        return self.store.customers.get(str(customer_id))

    def insert_event(self, **kwargs):
        self.store.events.append(kwargs)


class ReturnRepo:
    fail_on = None

    def __init__(self, connection):
        self.store = connection.store

    def _maybe_fail(self, name):
        if ReturnRepo.fail_on == name:
            raise RuntimeError(f"forced failure in {name}")

    def source_lines(self, invoice_id):
        rows = []
        for l in self.store.lines.get(str(invoice_id), []):
            returned = sum((r["weight"] for r in self.store.return_lines if r["source_line_id"] == l["line_id"]),
                           D("0.000"))
            item = self.store.items.get(l["inventory_item_id"] or "")
            rows.append(dict(l, returned_grams=returned, item_status=None if item is None else item["inventory_status"],
                             stored_barcode=l["barcode_snapshot"]))
        return rows

    def lock_items(self, ids):
        return {i: dict(self.store.items[i]) for i in ids if i in self.store.items}

    def _header(self, return_id):
        return next((r for r in self.store.returns if r["return_id"] == str(return_id)), None)

    def get_return(self, return_id, lock=False):
        row = self._header(return_id)
        return None if row is None else copy.deepcopy(row)

    def open_for_invoice(self, invoice_id):
        return [copy.deepcopy(r) for r in self.store.returns
                if r["source_invoice_id"] == str(invoice_id) and r["return_status"] in
                ("DRAFT", "PENDING_APPROVAL", "APPROVED", "REJECTED")]

    def for_invoice(self, invoice_id):
        out = []
        for r in self.store.returns:
            if r["source_invoice_id"] != str(invoice_id):
                continue
            row = copy.deepcopy(r)
            row["lines"] = [dict(l, inspection_status=next((v["status"] for v in self.store.reviews
                                                            if v.get("return_line_id") == l["line_id"]), None))
                            for l in self.store.return_lines if l["return_id"] == r["return_id"]]
            row["payments"] = [p for p in self.store.return_payments if p["return_id"] == r["return_id"]]
            row["approval"] = self.latest_approval(r["return_id"])
            out.append(row)
        return out

    @staticmethod
    def _check(row):
        # The 022 header CHECKs, enforced as MySQL would.
        assert row["return_status"] in ("DRAFT", "PENDING_APPROVAL", "APPROVED", "REJECTED", "FINALIZED", "CANCELED")
        assert row["requires_approval"] == (not row["making_window_open"])
        assert row["making_refund"] == 0 or row["making_window_open"]
        assert row["return_status"] not in ("PENDING_APPROVAL", "APPROVED", "REJECTED") or row["requires_approval"]
        assert row["total_amount"] == row["gold_amount"] + row["making_refund"]
        if row["return_status"] == "FINALIZED":
            assert row["total_amount"] > 0 and row["money_paid"] + row["account_amount"] == row["total_amount"]
        else:
            assert row["money_paid"] == 0 and row["account_amount"] == 0

    def insert_header(self, *, status, source_invoice_id, source_finalized_at, customer_id, selection, selection_hash,
                      making_window_open, requires_approval, deadline_at, gold_amount, making_refund, notes,
                      created_by_user_id, created_by_employee_id, created_at, details=None):
        row = {"return_id": str(len(self.store.returns) + 1), "return_status": status,
               "source_invoice_id": str(source_invoice_id), "source_invoice_number": "x",
               "source_finalized_at": source_finalized_at, "customer_id": customer_id,
               "selection_json": copy.deepcopy(selection), "selection_hash": selection_hash,
               "making_window_open": making_window_open, "requires_approval": requires_approval,
               "return_deadline_at": deadline_at, "gold_amount": gold_amount, "making_refund": making_refund,
               "total_amount": gold_amount + making_refund, "money_paid": D("0.00"), "account_amount": D("0.00"),
               "price_snapshot_json": None, "notes": notes, "created_by_name": "موظف", "created_at": created_at,
               "finalized_at": None, "finalized_by_name": None, "canceled_at": None, "canceled_by_name": None}
        self._check(row)
        self.store.returns.append(row)
        return row["return_id"]

    def update_selection(self, *, return_id, status, selection, selection_hash, making_window_open, gold_amount,
                         making_refund, notes):
        row = self._header(return_id)
        assert row["return_status"] in ("DRAFT", "PENDING_APPROVAL", "APPROVED", "REJECTED")
        row.update(return_status=status, selection_json=copy.deepcopy(selection), selection_hash=selection_hash,
                   making_window_open=making_window_open, requires_approval=not making_window_open,
                   gold_amount=gold_amount, making_refund=making_refund, total_amount=gold_amount + making_refund,
                   notes=notes)
        self._check(row)

    def set_status(self, *, return_id, from_statuses, to_status):
        row = self._header(return_id)
        if row["return_status"] not in from_statuses:
            return False
        row["return_status"] = to_status
        self._check(row)
        return True

    def cancel(self, *, return_id, canceled_by_user_id, canceled_at):
        row = self._header(return_id)
        if row["return_status"] not in ("DRAFT", "PENDING_APPROVAL", "APPROVED", "REJECTED"):
            return False
        row.update(return_status="CANCELED", canceled_at=canceled_at, canceled_by_name="موظف")
        return True

    def finalize_header(self, *, return_id, making_window_open, gold_amount, making_refund, money_paid, account_amount,
                        price_snapshot, details, notes, finalized_by_user_id, finalized_at):
        row = self._header(return_id)
        assert row["return_status"] in ("DRAFT", "APPROVED")
        row.update(return_status="FINALIZED", making_window_open=making_window_open,
                   requires_approval=not making_window_open, gold_amount=gold_amount, making_refund=making_refund,
                   total_amount=gold_amount + making_refund, money_paid=money_paid, account_amount=account_amount,
                   price_snapshot_json=price_snapshot, notes=notes, finalized_at=finalized_at,
                   finalized_by_name="موظف")
        self._check(row)

    def insert_line(self, *, return_id, line_number, line, inventory_item_id, old_gold_movement_id):
        # 022: an identified piece is whole and once; F7 has its own movement; grams never above the sold weight.
        if inventory_item_id:
            assert line.line_type == "SELL_INVENTORY" and old_gold_movement_id is None and line.weight == line.source_weight
            if any(r["source_line_id"] == line.source_line_id for r in self.store.return_lines):
                raise RuntimeError("Duplicate entry for key uq_return_identified_source_line")
        else:
            assert line.line_type == "SELL_OLD_GOLD" and old_gold_movement_id is not None
        assert D("0") < line.weight <= line.source_weight
        assert line.making_refund == 0 or line.historical_making is not None
        taken = sum((r["weight"] for r in self.store.return_lines if r["source_line_id"] == line.source_line_id), D("0"))
        assert taken + line.weight <= line.source_weight
        row = {"line_id": str(100 + len(self.store.return_lines) + 1), "return_id": str(return_id),
               "line_number": line_number, "source_line_id": line.source_line_id, "line_type": line.line_type,
               "inventory_item_id": inventory_item_id, "old_gold_movement_id": old_gold_movement_id,
               "karat_value": line.karat, "weight": line.weight, "weight_grams": line.weight,
               "source_weight_grams": line.source_weight, "shop_buy_price_per_gram": line.price_per_gram,
               "price_provider_timestamp": line.provider_timestamp, "gold_amount": line.gold_amount,
               "making_refund": line.making_refund, "item_name_snapshot": line.item_name,
               "barcode_snapshot": line.barcode, "stored_barcode": line.barcode, "quote_id": line.quote_id}
        self.store.return_lines.append(row)
        return row["line_id"]

    def insert_payment(self, **kwargs):
        self._maybe_fail("insert_payment")
        assert kwargs["amount"] > 0 and (kwargs["cash_movement_id"] is None) != (kwargs["bank_movement_id"] is None)
        self.store.return_payments.append(dict(kwargs, payment_method_code={1: "PHYSICAL_CASH", 2: "BANK_TRANSFER",
                                                                            3: "INSTAPAY", 5: "MOBILE_WALLET"}
                                               [kwargs["payment_method_id"]], bank_account_name=None,
                                               return_id=str(kwargs["return_id"])))

    def add_cash_out(self, *, cash_account_id, amount, return_id, **kwargs):
        self._maybe_fail("add_cash_out")
        self.store.cash_balance -= amount                         # may go negative: allowed
        self.store.cash_movements.append({"type": "RETURN_PAYMENT_OUT", "delta": -amount, "ref": return_id})
        return str(len(self.store.cash_movements))

    def add_bank_out(self, *, bank_account_id, amount, return_id, payment_reference=None, **kwargs):
        self._maybe_fail("add_bank_out")
        self.store.bank_movements.append({"account": bank_account_id, "delta": -amount, "ref": return_id})
        return str(len(self.store.bank_movements))

    def mark_item_returned_pending(self, *, inventory_item_id, updated_by_user_id):
        self._maybe_fail("mark_item_returned_pending")
        row = self.store.items[str(inventory_item_id)]
        if row["inventory_status"] != "SOLD":
            raise RuntimeError("no longer sold")
        row["inventory_status"] = "RETURNED_PENDING"

    def add_inventory_return_movement(self, *, item, return_id, **kwargs):
        self._maybe_fail("add_inventory_return_movement")
        self.store.inventory_movements.append((item["inventory_item_id"], "INVOICE_RETURN_IN", "SOLD", "RETURNED_PENDING"))

    def add_old_gold_in(self, *, karat_id, weight_grams, return_id, **kwargs):
        self._maybe_fail("add_old_gold_in")
        assert weight_grams > 0
        self.store.old_gold_movements.append({"code": "RETURN_OLD_GOLD_IN", "karat_id": karat_id, "delta": weight_grams,
                                              "ref": return_id})
        return str(len(self.store.old_gold_movements))

    def open_inspection(self, *, return_line_id, invoice_id, summary, details, created_by_user_id):
        self._maybe_fail("open_inspection")
        if any(r.get("return_line_id") == str(return_line_id) for r in self.store.reviews):
            raise RuntimeError("Duplicate entry for key uq_management_review_return_line")
        self.store.reviews.append({"review_id": str(900 + len(self.store.reviews) + 1), "type": "RETURNED_ITEM_INSPECTION",
                                   "status": "OPEN", "return_line_id": str(return_line_id), "details": details,
                                   "summary": summary})
        return self.store.reviews[-1]["review_id"]

    def open_approval(self, *, return_id, invoice_id, summary, details, created_by_user_id):
        self.store.reviews.append({"review_id": str(900 + len(self.store.reviews) + 1), "type": "RETURN_APPROVAL",
                                   "status": "OPEN", "code": None, "return_id": str(return_id),
                                   "details": copy.deepcopy(details), "summary": summary})
        return self.store.reviews[-1]["review_id"]

    def _approvals(self, return_id):
        return [r for r in self.store.reviews if r["type"] == "RETURN_APPROVAL" and r["return_id"] == str(return_id)]

    def latest_approval(self, return_id, lock=False):
        rows = self._approvals(return_id)
        if not rows:
            return None
        r = rows[-1]
        return {"review_id": r["review_id"], "review_status": r["status"], "resolution_code": r["code"],
                "details_json": copy.deepcopy(r["details"]), "resolved_by_name": "المدير" if r["code"] else None}

    def approval_by_id(self, review_id):
        r = next((v for v in self.store.reviews if v["review_id"] == str(review_id) and v["type"] == "RETURN_APPROVAL"),
                 None)
        return None if r is None else {"review_id": r["review_id"], "return_id": r["return_id"], "review_status": r["status"]}

    def resolve_approval(self, *, review_id, resolution_code, resolved_by_user_id, resolved_at):
        r = next(v for v in self.store.reviews if v["review_id"] == str(review_id))
        if r["status"] != "OPEN":
            return False
        r.update(status="RESOLVED", code=resolution_code)
        return True

    def dismiss_open_approvals(self, *, return_id, user_id, at, notes):
        for r in self._approvals(return_id):
            if r["status"] == "OPEN":
                r["status"] = "DISMISSED"


class CustomerRepo:
    def __init__(self, connection):
        self.store = connection.store

    def add_transaction(self, *, customer_id, type_code, amount_delta, **kwargs):
        ReturnRepo(Connection(self.store))._maybe_fail("customer_ledger")
        assert type_code == "RETURN_CREDIT" and amount_delta < 0           # DECREASE: the shop owes the customer
        self.store.ledger.append((customer_id, type_code, amount_delta, kwargs["reference_type"]))


class Prices:
    def __init__(self, token="T1", prices=PRICES):
        self.token, self.prices = token, prices

    def _set(self, karat_ids):
        prices = {k: BillingKaratPrice(k, KARATS[k], self.prices[k][0], self.prices[k][1], 50 + k, 60 + k,
                                       datetime(2026, 9, 1, 9, 0, k, tzinfo=timezone.utc))
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


EMPLOYEE = frozenset({"APP_LOGIN", "INVOICE_VIEW", "INVOICE_RETURN", "PAYMENT_RECORD"})
MANAGER = frozenset({"APP_LOGIN", "MANAGEMENT_REVIEW"})


def select(*pairs):
    return tuple(ReturnSelection(line_id, weight) for line_id, weight in pairs)


def cash(amount, method="PHYSICAL_CASH", account=None):
    return PaymentInput(method, amount, "OUT", None, account)


class ReturnTestCase(unittest.TestCase):
    def setUp(self):
        ReturnRepo.fail_on = None
        self.store = Store()
        self.prices = Prices()
        self.service = self.make_service(EMPLOYEE)
        self.manager = self.make_service(MANAGER, user=3)

    def make_service(self, permissions, user=17):
        session = AppSession()
        session._establish(AuthenticationResult(user, 7, "مستخدم", permissions, _VERIFIED), int(session.generation))

        @contextmanager
        def transaction():
            saved = copy.deepcopy(vars(self.store))
            try:
                yield Connection(self.store)
            except BaseException:
                self.store.__dict__.clear()
                self.store.__dict__.update(saved)
                raise
        return ReturnService(session, gold_prices=self.prices, authorization=Authorization(session, permissions),
                             transaction_factory=transaction, invoice_repository=InvoiceRepo,
                             return_repository=ReturnRepo, customer_repository=CustomerRepo)

    def at(self, **delta):
        self.store.now = T0 + timedelta(**delta)

    def draft(self, lines=(("1", None),), payments=None, account=None, invoice="500", return_id=None, notes=None):
        return ReturnDraft(invoice, select(*lines), tuple(payments or ()), account, notes, return_id)

    def finalize(self, draft):
        return self.service.finalize(draft, "T1")

    def total(self, draft):
        return self.service.preview(draft)["total_amount"]

    def full_cash(self, lines=(("1", None),), **kw):
        d = self.draft(lines, **kw)
        return self.draft(lines, payments=(cash(self.total(d)),), **kw)

    def assertRefused(self, code, call):
        before = self.store.tables()
        with self.assertRaises(ReturnError) as context:
            call()
        self.assertEqual(context.exception.code, code)
        self.assertEqual(self.store.tables(), before)


# ----------------------------------------------------------------------------- 56. time boundaries
class TimeBoundaryTests(ReturnTestCase):
    def test_making_window_boundaries(self):
        for delta, window in ((dict(hours=71, minutes=59, seconds=59), True), (dict(hours=72), True),
                              (dict(hours=72, seconds=1), False)):
            with self.subTest(delta=delta):
                self.at(**delta)
                preview = self.service.preview(self.draft())
                self.assertEqual(preview["making_window_open"], window)
                self.assertEqual(preview["requires_approval"], not window)
                self.assertEqual(preview["making_refund"], "1500.00" if window else "0.00")
                self.assertEqual("APPROVAL_REQUIRED" in [p["code"] for p in preview["blocking_problems"]], not window)

    def test_exactly_72_hours_finalizes_without_approval(self):
        self.at(hours=72)
        result = self.finalize(self.full_cash())
        self.assertEqual((result["making_refund"], result["total_amount"]), ("1500.00", "64000.00"))

    def test_deadline_is_the_end_of_the_15th_calendar_day(self):
        window = eligibility(T0, T0.date(), datetime(2026, 9, 15, 23, 59, 59))
        self.assertFalse(window["expired"])
        self.assertEqual(window["last_allowed_at"], datetime(2026, 9, 15, 23, 59, 59))
        self.assertTrue(eligibility(T0, T0.date(), datetime(2026, 9, 16, 0, 0, 0))["expired"])
        # Not 15 × 24 hours: 15 days after 10:00 is still refused past midnight of day 15.
        self.assertTrue(eligibility(T0, T0.date(), T0 + timedelta(days=14, hours=14))["expired"])

    def test_day_15_allowed_with_approval_and_day_16_refused_even_approved(self):
        self.store.now = datetime(2026, 9, 15, 23, 0, 0)
        d = self.draft((("1", None),))
        request = self.service.request_approval(d)
        review = self.store.reviews[-1]["review_id"]
        self.manager.decide_approval(review, True)
        self.store.now = datetime(2026, 9, 15, 23, 59, 59)
        d = self.draft((("1", None),), payments=(cash("62500.00"),), return_id=request["return_id"])
        self.assertEqual(self.finalize(d)["total_amount"], "62500.00")

    def test_approval_never_overrides_the_deadline(self):
        self.store.now = datetime(2026, 9, 15, 20, 0, 0)
        request = self.service.request_approval(self.draft())
        self.manager.decide_approval(self.store.reviews[-1]["review_id"], True)
        self.store.now = datetime(2026, 9, 16, 0, 0, 0)
        self.assertRefused("RETURN_EXPIRED", lambda: self.finalize(
            self.draft(payments=(cash("62500.00"),), return_id=request["return_id"])))
        # A manager cannot approve an expired Return either.
        self.store.now = datetime(2026, 9, 15, 20, 0, 0)
        other = self.service.request_approval(self.draft((("2", None),)))
        self.store.now = datetime(2026, 9, 16, 0, 0, 0)
        self.assertRefused("RETURN_EXPIRED", lambda: self.manager.decide_approval(self.store.reviews[-1]["review_id"], True))
        self.assertTrue(other["return_id"])
        self.assertRefused("RETURN_EXPIRED", lambda: self.service.request_approval(self.draft((("6", None),))))


# ----------------------------------------------------------------------------- 57. valuation
class ValuationTests(ReturnTestCase):
    def test_identified_piece_current_shop_buy_times_exact_weight_plus_making(self):
        p = self.service.preview(self.draft())
        self.assertEqual((p["gold_amount"], p["making_refund"], p["total_amount"]), ("62500.00", "1500.00", "64000.00"))
        self.assertEqual(p["lines"][0]["shop_buy_price_per_gram"], "6250.0000")   # never SHOP_SELL 6400

    def test_multi_karat_each_line_its_own_quote(self):
        p = self.service.preview(self.draft((("1", None), ("2", None))))
        self.assertEqual([l["gold_amount"] for l in p["lines"]], ["62500.00", "20000.00"])      # 21K 6250, 18K 5000
        self.assertEqual(p["total_amount"], str(D("62500.00") + D("20000.00") + D("1500.00") + D("400.00")))

    def test_partial_f7_gold_and_proportional_making(self):
        p = self.service.preview(self.draft((("3", "3.5"),)))
        self.assertEqual((p["gold_amount"], p["making_refund"]), ("21875.00", "350.00"))

    def test_after_72_hours_making_is_zero(self):
        self.at(hours=80)
        p = self.service.preview(self.draft((("3", "3.5"),)))
        self.assertEqual((p["gold_amount"], p["making_refund"], p["total_amount"]), ("21875.00", "0.00", "21875.00"))

    def test_missing_historical_making_inside_72_hours_is_refused(self):
        with self.assertRaises(ReturnError) as refused:
            self.service.preview(self.draft((("6", None),)))
        self.assertEqual(refused.exception.code, "HISTORICAL_MAKING_UNAVAILABLE")
        self.at(hours=100)                                       # after the window it is gold only
        self.assertEqual(self.service.preview(self.draft((("6", None),)))["total_amount"], "31250.00")

    def test_quote_snapshot_is_persisted_per_line(self):
        self.finalize(self.full_cash((("1", None), ("2", None))))
        header, lines = self.store.returns[0], self.store.return_lines
        self.assertEqual([(l["karat_value"], l["shop_buy_price_per_gram"], l["quote_id"]) for l in lines],
                         [(21, D("6250.0000"), 51), (18, D("5000.0000"), 52)])
        self.assertTrue(all(l["price_provider_timestamp"] is not None for l in lines))
        self.assertEqual({p["karat"]: p["shop_buy"] for p in header["price_snapshot_json"]["prices"]},
                         {21: D("6250.0000"), 18: D("5000.0000")})
        self.assertEqual(header["price_snapshot_json"]["basis"], "SHOP_BUY")

    def test_partial_making_parts_add_up_exactly(self):
        self.store.lines["500"][2]["making_total"] = D("100.00")
        for weight in ("3.333", "3.333", "3.334"):
            self.finalize(self.full_cash((("3", weight),)))
        self.assertEqual(sum((l["making_refund"] for l in self.store.return_lines), D("0")), D("100.00"))


# ----------------------------------------------------------------------------- 58. lifecycle
class LifecycleTests(ReturnTestCase):
    def test_identified_return_moves_to_returned_pending_with_one_inspection(self):
        result = self.finalize(self.full_cash())
        self.assertEqual(self.store.items["200"]["inventory_status"], "RETURNED_PENDING")
        self.assertEqual(self.store.inventory_movements, [("200", "INVOICE_RETURN_IN", "SOLD", "RETURNED_PENDING")])
        inspections = [r for r in self.store.reviews if r["type"] == "RETURNED_ITEM_INSPECTION"]
        self.assertEqual(len(inspections), 1)
        self.assertEqual((inspections[0]["details"]["source"], inspections[0]["details"]["stored_barcode"],
                          inspections[0]["details"]["return_code"]), ("RETURN", "B200", "RT000001"))
        self.assertIn("مرتجع RT000001", inspections[0]["summary"])
        self.assertEqual((result["code"], result["inspection_reviews"]), ("RT000001", 1))
        self.assertEqual(self.store.events[0]["event_type"], "RETURNED")

    def test_same_piece_cannot_return_twice(self):
        self.finalize(self.full_cash())
        self.assertRefused("ALREADY_RETURNED", lambda: self.finalize(self.draft(payments=(cash("64000.00"),))))

    def test_piece_used_by_an_exchange_cannot_return(self):
        with self.assertRaises(ReturnError) as refused:
            self.service.preview(self.draft((("7", None),)))
        self.assertEqual((refused.exception.code, str(refused.exception)), ("ALREADY_EXCHANGED", EXCHANGED_TEXT))

    def test_returned_piece_cannot_be_exchanged(self):
        self.assertEqual(ExchangeService._line_status(
            {"line_type": "SELL_INVENTORY", "inventory_item_id": "200", "consumed_by": None, "returned_by": "1"}),
            ALREADY_RETURNED_TEXT)

    def test_f7_partial_returns_decrement_the_remaining_weight_exactly(self):
        before = self.store.old_gold_balance()
        self.finalize(self.full_cash((("3", "3.500"),)))
        context = self.service.context("500")
        f7 = next(l for l in context["lines"] if l["line_id"] == "3")
        self.assertEqual((f7["consumed_grams"], f7["remaining_grams"], f7["returnable"]), ("3.500", "6.500", True))
        self.assertEqual(self.store.old_gold_balance() - before, D("3.500"))
        self.assertRefused("WEIGHT_EXCEEDS_REMAINING", lambda: self.finalize(self.draft((("3", "6.501"),),
                                                                                         payments=(cash("1"),))))
        self.finalize(self.full_cash((("3", "6.500"),)))
        f7 = next(l for l in self.service.context("500")["lines"] if l["line_id"] == "3")
        self.assertEqual((f7["remaining_grams"], f7["returnable"], f7["reason"]), ("0.000", False, "لا يوجد وزن متاح للمرتجع"))
        self.assertEqual(self.store.old_gold_balance() - before, D("10.000"))       # never above the sold 10.000
        # F7 never enters RETURNED_PENDING and never creates an inventory item.
        self.assertEqual(self.store.inventory_movements, [])
        self.assertEqual(sorted(self.store.items), ["200", "201", "202", "203", "204"])

    def test_several_returns_on_different_items_of_one_invoice(self):
        self.finalize(self.full_cash((("1", None),)))
        self.finalize(self.full_cash((("2", None),)))
        self.assertEqual([r["return_status"] for r in self.store.returns], ["FINALIZED", "FINALIZED"])
        context = {l["line_id"]: l for l in self.service.context("500")["lines"]}
        self.assertEqual((context["1"]["reason"], context["2"]["reason"]), (RETURNED_TEXT, RETURNED_TEXT))

    def test_unidentified_f5_and_customer_old_gold_are_not_returnable(self):
        for line_id, code in (("5", "LINE_NOT_RETURNABLE"), ("4", "LINE_NOT_RETURNABLE")):
            with self.assertRaises(ReturnError) as refused:
                self.service.preview(self.draft(((line_id, None),)))
            self.assertEqual(refused.exception.code, code)
        self.assertNotIn("4", [l["line_id"] for l in self.service.context("500")["lines"]])

    def test_identified_piece_is_always_whole(self):
        with self.assertRaises(ReturnError) as refused:
            self.service.preview(self.draft((("1", "5.000"),)))
        self.assertEqual(refused.exception.code, "WEIGHT_NOT_PARTIAL")


# ----------------------------------------------------------------------------- 59. mixed invoice
class MixedInvoiceTests(ReturnTestCase):
    def test_customer_old_gold_purchase_is_untouched(self):
        before = copy.deepcopy(self.store.old_gold_movements)
        invoice, lines = copy.deepcopy(self.store.invoices), copy.deepcopy(self.store.lines)
        result = self.finalize(self.full_cash())
        self.assertEqual(result["total_amount"], "64000.00")
        self.assertEqual(self.store.old_gold_movements, before)                  # no reversal, no recalculation
        self.assertEqual((self.store.invoices, self.store.lines), (invoice, lines))   # original invoice immutable


# ----------------------------------------------------------------------------- 60. payments
class PaymentTests(ReturnTestCase):
    def test_cash_bank_wallet_and_split(self):
        d = self.draft(payments=(cash("30000.00"), cash("20000.00", "BANK_TRANSFER", "7"),
                                 cash("10000.00", "MOBILE_WALLET", "8"), cash("2000.00", "INSTAPAY", "7")),
                       account="2000.00")
        result = self.finalize(d)
        self.assertEqual((result["money_paid"], result["account_amount"]), ("62000.00", "2000.00"))
        self.assertEqual([m["delta"] for m in self.store.cash_movements], [D("-30000.00")])
        self.assertEqual([(m["account"], m["delta"]) for m in self.store.bank_movements],
                         [(7, D("-20000.00")), (8, D("-10000.00")), (7, D("-2000.00"))])
        self.assertEqual(len(self.store.return_payments), 4)
        self.assertEqual(self.store.ledger, [(12, "RETURN_CREDIT", D("-2000.00"), "INVOICE_RETURN")])

    def test_full_customer_account(self):
        result = self.finalize(self.draft(account="64000.00"))
        self.assertEqual((result["money_paid"], self.store.return_payments, self.store.cash_movements),
                         ("0.00", [], []))
        self.assertEqual(self.store.ledger[0][2], D("-64000.00"))

    def test_account_needs_a_registered_customer(self):
        self.assertRefused("ACCOUNT_NEEDS_CUSTOMER", lambda: self.finalize(
            self.draft((("8", None),), invoice="501", account="19050.00")))

    def test_under_and_over_settlement_refused_without_fallback(self):
        self.assertRefused("SETTLEMENT_INCOMPLETE", lambda: self.finalize(self.draft(payments=(cash("63999.99"),))))
        self.assertRefused("AMOUNT_MISMATCH", lambda: self.finalize(self.draft(payments=(cash("64000.01"),))))
        self.assertRefused("SETTLEMENT_INCOMPLETE", lambda: self.finalize(self.draft()))
        self.assertEqual(self.store.ledger, [])                      # never an automatic customer-account remainder

    def test_cash_may_go_negative(self):
        self.store.cash_balance = D("2000.00")
        self.finalize(self.draft(payments=(cash("64000.00"),)))
        self.assertEqual(self.store.cash_balance, D("-62000.00"))

    def test_account_must_match_its_method(self):
        with self.assertRaises(ReturnError):
            self.finalize(self.draft(payments=(cash("64000.00", "MOBILE_WALLET", "7"),)))
        with self.assertRaises(ReturnError):
            self.finalize(self.draft(payments=(cash("64000.00", "INSTAPAY"),)))


# ----------------------------------------------------------------------------- 61. approval
class ApprovalTests(ReturnTestCase):
    def setUp(self):
        super().setUp()
        self.at(days=3, hours=4)                       # > 72 h, within day 15

    def request(self, lines=(("1", None),), notes=None):
        result = self.service.request_approval(self.draft(lines, notes=notes))
        return result["return_id"], self.store.reviews[-1]["review_id"]

    def test_within_72_hours_needs_no_approval(self):
        self.at(hours=5)
        self.assertEqual(self.finalize(self.full_cash())["making_refund"], "1500.00")
        self.assertRefused("APPROVAL_NOT_NEEDED", lambda: self.service.request_approval(self.draft((("2", None),))))

    def test_employee_blocked_without_approval(self):
        self.assertRefused("APPROVAL_REQUIRED", lambda: self.finalize(self.draft(payments=(cash("62500.00"),))))
        return_id, _ = self.request()
        self.assertRefused("APPROVAL_PENDING", lambda: self.finalize(
            self.draft(payments=(cash("62500.00"),), return_id=return_id)))
        with self.assertRaises(PermissionError):                 # an employee cannot approve
            self.service.decide_approval(self.store.reviews[-1]["review_id"], True)

    def test_manager_approval_unlocks_exactly_that_return_with_empty_reason(self):
        return_id, review = self.request(notes=None)
        self.assertEqual(self.store.returns[0]["return_status"], "PENDING_APPROVAL")
        self.assertEqual(self.manager.decide_approval(review, True)["status"], "APPROVED")
        result = self.finalize(self.draft(payments=(cash("62500.00"),), return_id=return_id))
        self.assertEqual((result["gold_amount"], result["making_refund"]), ("62500.00", "0.00"))
        # The same approval cannot authorize another Return.
        self.assertRefused("APPROVAL_REQUIRED", lambda: self.finalize(self.draft((("2", None),), payments=(cash("20000.00"),))))

    def test_changed_weight_invalidates_approval_payment_change_does_not(self):
        return_id, review = self.request((("3", "2.000"),))
        self.manager.decide_approval(review, True)
        self.assertRefused("APPROVAL_REQUIRED", lambda: self.finalize(
            self.draft((("3", "2.500"),), payments=(cash("15625.00"),), return_id=return_id)))
        # Payment method / split changes need no new approval.
        result = self.finalize(self.draft((("3", "2.000"),), payments=(cash("5000.00"), cash("7500.00", "INSTAPAY", "7")),
                                          return_id=return_id))
        self.assertEqual(result["total_amount"], "12500.00")

    def test_changing_the_request_dismisses_the_old_review(self):
        return_id, review = self.request((("3", "2.000"),))
        again = self.service.request_approval(self.draft((("3", "2.000"),), return_id=return_id))
        self.assertFalse(again["changed"])                                       # same selection: idempotent
        self.service.request_approval(self.draft((("3", "3.000"),), return_id=return_id))
        statuses = [r["status"] for r in self.store.reviews if r["type"] == "RETURN_APPROVAL"]
        self.assertEqual(statuses, ["DISMISSED", "OPEN"])
        self.assertRefused("REVIEW_ALREADY_DECIDED", lambda: self.manager.decide_approval(review, True))

    def test_rejected_return_cannot_finalize(self):
        return_id, review = self.request()
        self.assertEqual(self.manager.decide_approval(review, False)["status"], "REJECTED")
        self.assertRefused("RETURN_REJECTED", lambda: self.finalize(
            self.draft(payments=(cash("62500.00"),), return_id=return_id)))

    def test_second_decision_refused(self):
        _, review = self.request()
        self.manager.decide_approval(review, True)
        self.assertRefused("REVIEW_ALREADY_DECIDED", lambda: self.manager.decide_approval(review, False))

    def test_stale_review_after_selection_change_is_refused(self):
        return_id, review = self.request((("3", "2.000"),))
        header = self.store.returns[0]
        header["selection_hash"] = "0" * 64                          # the Return changed under the open review
        self.assertRefused("REVIEW_STALE", lambda: self.manager.decide_approval(review, True))


# ----------------------------------------------------------------------------- 62. cancellation
class CancellationTests(ReturnTestCase):
    def test_draft_pending_and_approved_can_cancel_without_effects(self):
        draft = self.service.save_draft(self.draft())
        effects = (copy.deepcopy(self.store.items), list(self.store.cash_movements), list(self.store.old_gold_movements))
        self.assertEqual(self.service.cancel(draft["return_id"])["status"], "CANCELED")
        self.at(days=4)
        pending = self.service.request_approval(self.draft((("2", None),)))
        self.service.cancel(pending["return_id"])
        self.assertEqual(self.store.reviews[-1]["status"], "DISMISSED")
        approved = self.service.request_approval(self.draft((("3", "1.000"),)))
        self.manager.decide_approval(self.store.reviews[-1]["review_id"], True)
        self.service.cancel(approved["return_id"])
        self.assertEqual(self.store.reviews[-1]["status"], "RESOLVED")          # decided approval stays as history
        self.assertEqual([r["return_status"] for r in self.store.returns], ["CANCELED"] * 3)
        self.assertEqual((self.store.items, self.store.cash_movements, self.store.old_gold_movements), effects)
        self.assertEqual(len(self.store.returns), 3)                             # never deleted

    def test_finalized_identified_and_f7_returns_cannot_cancel(self):
        identified = self.finalize(self.full_cash())
        f7 = self.finalize(self.full_cash((("3", "1.000"),)))
        for result in (identified, f7):
            self.assertRefused("RETURN_FINALIZED_IMMUTABLE", lambda r=result: self.service.cancel(r["return_id"]))

    def test_canceled_return_cannot_finalize(self):
        draft = self.service.save_draft(self.draft())
        self.service.cancel(draft["return_id"])
        self.assertRefused("RETURN_CANCELED", lambda: self.finalize(
            self.draft(payments=(cash("64000.00"),), return_id=draft["return_id"])))


# ----------------------------------------------------------------------------- 43. rollback
class RollbackTests(ReturnTestCase):
    def test_every_injected_failure_rolls_everything_back(self):
        identified = self.draft(payments=(cash("30000.00"), cash("20000.00", "INSTAPAY", "7")), account="14000.00")
        f7 = self.draft((("3", "2.000"),), payments=(cash("12700.00"),))            # 2 × 6250 + 1000 × 2/10
        for point, d in (("insert_payment", identified), ("add_cash_out", identified), ("add_bank_out", identified),
                         ("customer_ledger", identified), ("add_inventory_return_movement", identified),
                         ("mark_item_returned_pending", identified), ("open_inspection", identified),
                         ("add_old_gold_in", f7)):
            with self.subTest(point=point):
                ReturnRepo.fail_on = point
                before = self.store.tables()
                with self.assertRaises(RuntimeError):
                    self.finalize(d)
                self.assertEqual(self.store.tables(), before)
        ReturnRepo.fail_on = None
        self.assertEqual(self.finalize(identified)["total_amount"], "64000.00")


# ----------------------------------------------------------------------------- 45. permissions / 42. concurrency
class PermissionAndOverlapTests(ReturnTestCase):
    def test_return_permission_required(self):
        viewer = self.make_service(frozenset({"APP_LOGIN", "INVOICE_VIEW"}))
        for call in (lambda: viewer.context("500"), lambda: viewer.preview(self.draft()),
                     lambda: viewer.finalize(self.full_cash(), "T1")):
            with self.assertRaises(PermissionError):
                call()

    def test_ordinary_employee_permission_matrix(self):
        """SALESPERSON / CASHIER: INVOICE_RETURN (+ PAYMENT_RECORD), never MANAGEMENT_REVIEW."""
        employee = self.make_service(frozenset({"APP_LOGIN", "INVOICE_RETURN", "PAYMENT_RECORD"}), user=21)
        # 1. <= 72 h: the employee finalizes directly.
        self.assertEqual(employee.finalize(self.full_cash(), "T1")["making_refund"], "1500.00")
        # 3. > 72 h: blocked until a manager approves; the employee may request it.
        self.at(days=4)
        self.assertRefused("APPROVAL_REQUIRED", lambda: employee.finalize(
            self.draft((("2", None),), payments=(cash("20000.00"),)), "T1"))
        request = employee.request_approval(self.draft((("2", None),)))
        review = self.store.reviews[-1]["review_id"]
        # 2. The same employee cannot approve / reject.
        for decision in (True, False):
            with self.assertRaises(PermissionError):
                employee.decide_approval(review, decision)
        self.assertEqual(self.store.reviews[-1]["status"], "OPEN")
        # 4. After the manager's approval, the employee finalizes it.
        self.manager.decide_approval(review, True)
        result = employee.finalize(self.draft((("2", None),), payments=(cash("20000.00"),),
                                              return_id=request["return_id"]), "T1")
        self.assertEqual((result["total_amount"], result["making_refund"]), ("20000.00", "0.00"))
        # 5. A role without INVOICE_RETURN stays blocked everywhere (even with the approval right).
        for permissions in (frozenset({"APP_LOGIN", "INVOICE_VIEW", "INVOICE_CREATE", "INVOICE_FINALIZE"}), MANAGER):
            other = self.make_service(permissions, user=22)
            for call in (lambda: other.context("500"), lambda: other.save_draft(self.draft((("3", "1.000"),))),
                         lambda: other.request_approval(self.draft((("3", "1.000"),))),
                         lambda: other.finalize(self.draft((("3", "1.000"),), payments=(cash("6450.00"),)), "T1")):
                with self.assertRaises(PermissionError):
                    call()

    def test_manager_approval_permission_is_management_review(self):
        self.at(days=4)
        self.service.request_approval(self.draft())
        reconciler = self.make_service(frozenset({"APP_LOGIN", "INVENTORY_RECONCILE"}))
        with self.assertRaises(PermissionError):
            reconciler.decide_approval(self.store.reviews[-1]["review_id"], True)

    def test_sequential_overlap_one_winner(self):
        d = self.full_cash()
        self.finalize(d)
        self.assertRefused("ALREADY_RETURNED", lambda: self.finalize(d))

    def test_price_changed_is_refused(self):
        d = self.full_cash()
        self.prices.token = "T2"
        before = self.store.tables()
        with self.assertRaises(PriceChanged):
            self.service.finalize(d, "T1")
        self.assertEqual(self.store.tables(), before)


# ----------------------------------------------------------------------------- 39. History
class HistoryTests(ReturnTestCase):
    def test_return_summary_and_history_headlines(self):
        from app.viewmodels.invoice_history_viewmodel import InvoiceHistoryViewModel
        self.finalize(self.full_cash((("1", None),)))
        self.finalize(self.full_cash((("3", "3.000"),)))
        rows = [return_summary(r) for r in ReturnRepo(Connection(self.store)).for_invoice("500")]
        self.assertEqual([r["code"] for r in rows], ["RT000001", "RT000002"])
        self.assertEqual(rows[0]["lines"][0]["disposition"]["text"], "القطعة المرتجعة بانتظار مراجعة المدير")
        self.assertEqual(rows[1]["lines"][0]["disposition"]["text"], "عاد الوزن إلى رصيد الكسر")
        vm = InvoiceHistoryViewModel.__new__(InvoiceHistoryViewModel)
        vm._detail = {"returns": rows}
        headlines = [r["headline"] for r in InvoiceHistoryViewModel.returns.fget(vm)]
        self.assertEqual(headlines, ["مرتجع — تم رد الذهب والمصنعية"] * 2)
        rows[0]["making_window_open"], rows[0]["approval_status"], rows[0]["age_days"] = False, "APPROVED", 3
        self.assertEqual(InvoiceHistoryViewModel.returns.fget(vm)[0]["headline"][:31],
                         "مرتجع بعد 3 أيام — تمت موافقة المدير"[:31])
        rows[0]["approval_status"] = None
        self.assertEqual(InvoiceHistoryViewModel.returns.fget(vm)[0]["headline"], "مرتجع — تم رد قيمة الذهب فقط")


class ReturnViewModelTests(ReturnTestCase):
    def test_typing_f7_grams_never_rebuilds_the_rows(self):
        """Regression: every keystroke re-emitted `lines`; the Repeater rebuilt the row and the weight
        field lost focus after the first digit (3.5 was saved as 3.000)."""
        from PySide6.QtCore import QCoreApplication
        from app.viewmodels.return_viewmodel import ReturnViewModel
        QCoreApplication.instance() or QCoreApplication([])
        vm = ReturnViewModel(self.service._session, self.service)
        vm._on_context(self.service.context("500"), "")
        emitted = []
        vm.linesChanged.connect(lambda: emitted.append(1))
        vm.toggleLine("3")
        self.assertEqual(len(emitted), 1)
        for text in ("3", "3.", "3.5"):
            vm.setWeight("3", text)
        self.assertEqual(len(emitted), 1)
        self.assertEqual(vm._draft().lines, (ReturnSelection("3", "3.5"),))
        vm.shutdown()


if __name__ == "__main__":
    unittest.main()
