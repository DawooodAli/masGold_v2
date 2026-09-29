"""Reversal / Correction V1 (CorrectionService) on an in-memory store (no database).

The transaction factory snapshots the whole store and restores it on any exception (a MySQL
rollback). The fake repository enforces the 023 contracts: one full reversal per source, one
settlement chain sequence per source, reviews / cases closed only while open, lines linked once.
Money / ledger rows are the POSTED effects; the service must derive every reversal from them.
"""
from contextlib import contextmanager
import copy
import dataclasses
from datetime import datetime, timedelta
from decimal import Decimal
import unittest

from app.session import AppSession
from services.authentication_service import AuthenticationResult, _VERIFIED
from services.correction_service import CorrectionError, CorrectionService, SettlementLeg


D = Decimal
T0 = datetime(2026, 9, 20, 10, 0, 0)


def inv(invoice_id, number, lines, customer="12", direction="CUSTOMER_PAYS_SHOP", total="30000.00"):
    return {"invoice_id": invoice_id, "invoice_number": number, "record_origin": "NATIVE",
            "invoice_status": "FINALIZED", "invoice_type": "SELL", "net_direction": direction, "net_amount": D(total),
            "customer_id": customer, "customer_name_snapshot": "عميل", "finalized_at": T0, "invoice_date": T0,
            "lines": lines, "events": []}


def ln(line_id, line_type, karat_id=1, weight="10.000", item=None, barcode=None):
    return {"line_id": line_id, "line_type": line_type, "inventory_item_id": item, "karat_id": karat_id,
            "karat_value": {1: 21, 2: 18}[karat_id], "weight_grams": D(weight), "barcode_snapshot": barcode}


class Store:
    def __init__(self):
        self.now = T0 + timedelta(days=2)
        self.invoices = {
            "500": inv("500", "26092001", [ln("1", "SELL_INVENTORY", item="200", barcode="B200"),
                                           ln("2", "SELL_OLD_GOLD", weight="5.000"),
                                           ln("3", "BUY_OLD_GOLD", weight="4.000"),
                                           ln("4", "SELL_UNIDENTIFIED", weight="2.000")], total="35000.00"),
            "501": inv("501", "26092002", [ln("5", "SELL_INVENTORY", item="201", barcode="B201")]),
            "502": inv("502", "26092003", [ln("6", "SELL_INVENTORY", item="202", barcode="B202")]),
            "503": inv("503", "26092004", [ln("7", "SELL_INVENTORY", item="203", barcode="B203")]),
            "504": inv("504", "26092005", [ln("8", "SELL_OLD_GOLD", weight="10.000")]),
            "505": inv("505", "26092006", [ln("9", "SELL_INVENTORY", item="205", barcode="B205")]),
            "506": inv("506", "26092007", [ln("10", "SELL_INVENTORY", item="206", barcode="B206")], customer=None),
        }
        self.payments = [  # invoice_payments (original legs)
            {"invoice_id": "500", "method_code": "PHYSICAL_CASH", "bank_account_id": None, "direction": "IN", "amount": D("20000.00")},
            {"invoice_id": "500", "method_code": "BANK_TRANSFER", "bank_account_id": 7, "direction": "IN", "amount": D("10000.00")},
            {"invoice_id": "501", "method_code": "PHYSICAL_CASH", "bank_account_id": None, "direction": "IN", "amount": D("30000.00")},
            {"invoice_id": "506", "method_code": "PHYSICAL_CASH", "bank_account_id": None, "direction": "IN", "amount": D("30000.00")}]
        # POSTED money / ledger rows: (ledger, account, signed delta, reference_type, reference_id)
        self.money = [("CASH", 1, D("20000.00"), "INVOICE", "500"), ("BANK", 7, D("10000.00"), "INVOICE", "500"),
                      ("CASH", 1, D("30000.00"), "INVOICE", "501"), ("CASH", 1, D("30000.00"), "INVOICE", "506"),
                      ("CASH", 1, D("5000.00"), "INVOICE_LIFECYCLE", "900"),
                      ("CASH", 1, D("-12000.00"), "INVOICE_RETURN", "800"),
                      ("CASH", 1, D("-18000.00"), "INVOICE_RETURN", "801")]
        self.ledger = [("12", D("5000.00"), "INVOICE", "500"), ("12", D("1000.00"), "INVOICE_LIFECYCLE", "900"),
                       ("12", D("-500.00"), "INVOICE_RETURN", "800")]
        self.items = {i: {"inventory_item_id": i, "inventory_status": s, "weight_grams": D("10.000"),
                          "stock_classification": "NEW_STOCK"}
                      for i, s in (("200", "SOLD"), ("201", "SOLD"), ("202", "RETURNED_PENDING"), ("300", "SOLD"),
                                   ("203", "RETURNED_PENDING"), ("205", "IN_STOCK"), ("206", "SOLD"))}
        self.exchanges = {
            "900": {"lifecycle_id": "900", "source_invoice_id": "502", "customer_id": "12", "signed_amount": D("6000.00"),
                    "lines": [{"line_id": "9001", "line_role": "RETURNED", "identification_method": None,
                               "inventory_item_id": "202", "karat_id": 1, "karat_value": 21, "weight_grams": D("10.000")},
                              {"line_id": "9002", "line_role": "REPLACEMENT", "identification_method": "SCAN",
                               "inventory_item_id": "300", "karat_id": 1, "karat_value": 21, "weight_grams": D("10.000")},
                              {"line_id": "9003", "line_role": "REPLACEMENT", "identification_method": "OLD_GOLD_STOCK",
                               "inventory_item_id": None, "karat_id": 1, "karat_value": 21, "weight_grams": D("2.000")},
                              {"line_id": "9004", "line_role": "REPLACEMENT", "identification_method": "MANUAL",
                               "inventory_item_id": None, "karat_id": 1, "karat_value": 21, "weight_grams": D("1.000")}],
                    "payments": [{"method_code": "PHYSICAL_CASH", "bank_account_id": None, "direction": "IN",
                                  "amount": D("5000.00")}]},
            "901": {"lifecycle_id": "901", "source_invoice_id": "506", "customer_id": None, "signed_amount": D("0.00"),
                    "lines": [{"line_id": "9101", "line_role": "RETURNED", "identification_method": None,
                               "inventory_item_id": "206", "karat_id": 1, "karat_value": 21, "weight_grams": D("10.000")}],
                    "payments": []}}
        self.returns = {
            "800": {"return_id": "800", "source_invoice_id": "503", "customer_id": "12", "return_status": "FINALIZED",
                    "lines": [{"line_id": "8001", "line_type": "SELL_INVENTORY", "inventory_item_id": "203", "karat_id": 1,
                               "karat_value": 21, "weight_grams": D("10.000")}],
                    "payments": [{"method_code": "PHYSICAL_CASH", "bank_account_id": None, "direction": "OUT",
                                  "amount": D("12000.00")}]},
            "801": {"return_id": "801", "source_invoice_id": "504", "customer_id": "12", "return_status": "FINALIZED",
                    "lines": [{"line_id": "8011", "line_type": "SELL_OLD_GOLD", "inventory_item_id": None, "karat_id": 1,
                               "karat_value": 21, "weight_grams": D("3.000")}],
                    "payments": [{"method_code": "PHYSICAL_CASH", "bank_account_id": None, "direction": "OUT",
                                  "amount": D("18000.00")}]},
            "802": {"return_id": "802", "source_invoice_id": "505", "customer_id": "12", "return_status": "FINALIZED",
                    "lines": [{"line_id": "8021", "line_type": "SELL_INVENTORY", "inventory_item_id": "205", "karat_id": 1,
                               "karat_value": 21, "weight_grams": D("10.000")}], "payments": []}}
        self.reversed_lines = {}                              # line id → correction id
        self.reviews = [{"review_id": "81", "lifecycle_line_id": "9001", "status": "OPEN", "code": None},
                        {"review_id": "82", "return_line_id": "8001", "status": "OPEN", "code": None},
                        {"review_id": "83", "return_line_id": "8021", "status": "RESOLVED", "code": "RETURN_TO_STOCK"}]
        self.cases = [{"case_id": "70", "invoice_line_id": "4", "status": "OPEN"},
                      {"case_id": "71", "lifecycle_line_id": "9004", "status": "OPEN"}]
        self.old_gold = [(1, D("100.000"), "OPENING_BALANCE")]      # (karat id, signed grams, code)
        self.bank_accounts = {7: {"id": 7, "name": "بنك مصر", "account_type": "BANK", "is_active": 1},
                              8: {"id": 8, "name": "فودافون", "account_type": "MOBILE_WALLET", "is_active": 1}}
        self.customers = {"12": {"customer_id": "12", "is_active": 1, "record_origin": "NATIVE"}}
        self.corrections, self.legs, self.inv_movements, self.audit = [], [], [], []

    def tables(self):
        return copy.deepcopy(vars(self))

    def balance(self, ledger, account):
        return sum((m[2] for m in self.money if (m[0], m[1]) == (ledger, account)), D("0.00"))

    def old_gold_balance(self, karat_id=1):
        return sum((g for k, g, _ in self.old_gold if k == karat_id), D("0.000"))


class Connection:
    in_transaction = True

    def __init__(self, store):
        self.store = store

    def cursor(self, dictionary=False):
        return Cursor(self.store)


class Cursor:
    def __init__(self, store):
        self.store = store

    def execute(self, sql, params=()):
        assert sql.startswith("INSERT INTO audit_log"), sql[:60]
        Repo(Connection(self.store))._maybe_fail("audit")
        self.store.audit.append(params)

    def close(self):
        pass


class InvoiceRepo:
    def __init__(self, connection):
        self.store = connection.store

    def get_invoice(self, invoice_id):
        row = self.store.invoices.get(str(invoice_id))
        return None if row is None else copy.deepcopy(row)

    lock_invoice = get_invoice

    def active_bank_accounts(self):
        return list(self.store.bank_accounts.values())

    def get_payment_method(self, code):
        ids = {"PHYSICAL_CASH": (1, 0, 1), "INSTAPAY": (3, 1, 0), "BANK_TRANSFER": (2, 1, 0), "MOBILE_WALLET": (5, 1, 0)}
        pid, bank, cash = ids[code]
        return {"payment_method_id": pid, "code": code, "requires_bank_account": bank, "affects_physical_cash": cash,
                "is_active": 1}

    def active_cash_accounts(self):
        return [{"id": 1, "code": "MAIN", "name": "Main"}]

    def get_bank_account(self, account_id, lock=False):
        return self.store.bank_accounts.get(int(account_id))

    def lock_customer(self, customer_id):
        return self.store.customers.get(str(customer_id))

    def insert_event(self, *, invoice_id, event_type, performed_by_user_id, details=None, **kwargs):
        Repo(Connection(self.store))._maybe_fail("event")
        self.store.invoices[str(invoice_id)]["events"].append({"event_type": event_type, "details_json": copy.deepcopy(details)})


REF = {"INVOICE": "INVOICE", "EXCHANGE": "INVOICE_LIFECYCLE", "RETURN": "INVOICE_RETURN"}


class Repo:
    fail_on = None

    def __init__(self, connection):
        self.store = connection.store

    def _maybe_fail(self, name):
        if Repo.fail_on == name:
            raise RuntimeError(f"forced failure in {name}")

    def corrections_for_source(self, source_type, source_id):
        return [copy.deepcopy(c) for c in self.store.corrections
                if (c["source_type"], c["source_id"]) == (source_type, str(source_id))]

    def effective_legs(self, correction_id):
        return [dict(l) for l in self.store.legs if l["correction_id"] == correction_id and l["leg_role"] == "EFFECTIVE"]

    def _refs(self, source_type, source_id, correction_ids):
        return {(REF[source_type], str(source_id))} | {("TRANSACTION_CORRECTION", str(c)) for c in correction_ids}

    def money_by_account(self, source_type, source_id, correction_ids=()):
        refs, out = self._refs(source_type, source_id, correction_ids), {}
        for ledger, account, delta, ref_type, ref_id in self.store.money:
            if (ref_type, ref_id) in refs:
                out[(ledger, account)] = out.get((ledger, account), D("0.00")) + delta
        return out

    def account_by_customer(self, source_type, source_id, correction_ids=()):
        refs, out = self._refs(source_type, source_id, correction_ids), {}
        for customer, delta, ref_type, ref_id in self.store.ledger:
            if (ref_type, ref_id) in refs:
                out[customer] = out.get(customer, D("0.00")) + delta
        return out

    def invoice_payment_legs(self, invoice_id):
        return [dict(p) for p in self.store.payments if p["invoice_id"] == str(invoice_id)]

    def _with_reversal(self, row):
        row = copy.deepcopy(row)
        for line in row["lines"]:
            line["reversed_by_correction_id"] = self.store.reversed_lines.get(line["line_id"])
        return row

    def exchange_source(self, lifecycle_id):
        row = self.store.exchanges.get(str(lifecycle_id))
        return None if row is None else self._with_reversal(row)

    def return_source(self, return_id):
        row = self.store.returns.get(str(return_id))
        return None if row is None else self._with_reversal(row)

    def lifecycle_of_invoice(self, invoice_id):
        def active(row):
            return sum(1 for l in row["lines"] if l["line_id"] not in self.store.reversed_lines)
        return ([{"lifecycle_id": x["lifecycle_id"], "active_lines": active(x)} for x in self.store.exchanges.values()
                 if x["source_invoice_id"] == str(invoice_id)],
                [{"return_id": r["return_id"], "active_lines": active(r)} for r in self.store.returns.values()
                 if r["source_invoice_id"] == str(invoice_id) and r["return_status"] == "FINALIZED"])

    def lock_items(self, ids):
        return {i: dict(self.store.items[i]) for i in ids if i in self.store.items}

    def inspection_for(self, *, lifecycle_line_id=None, return_line_id=None):
        key, value = ("lifecycle_line_id", lifecycle_line_id) if lifecycle_line_id else ("return_line_id", return_line_id)
        r = next((v for v in self.store.reviews if v.get(key) == value), None)
        return None if r is None else {"review_id": r["review_id"], "review_status": r["status"], "resolution_code": r["code"]}

    def case_for(self, *, invoice_line_id=None, lifecycle_line_id=None):
        key, value = ("invoice_line_id", invoice_line_id) if invoice_line_id else ("lifecycle_line_id", lifecycle_line_id)
        c = next((v for v in self.store.cases if v.get(key) == value), None)
        return None if c is None else {"case_id": c["case_id"], "case_status": c["status"]}

    def lock_old_gold_balance(self, karat_id):
        return self.store.old_gold_balance(karat_id)

    def db_now(self):
        return self.store.now

    def insert_correction(self, **kw):
        self._maybe_fail("header")
        key = (kw["correction_type"], kw["source_type"], str(kw["source_id"]), kw["chain_sequence"])
        for c in self.store.corrections:
            if kw["correction_type"] == "FULL_REVERSAL" and (c["correction_type"], c["source_type"], c["source_id"]) == key[:3]:
                raise RuntimeError("Duplicate entry for key uq_correction_one_reversal")
            if kw["correction_type"] == "SETTLEMENT_CORRECTION" and (
                    c["correction_type"], c["source_type"], c["source_id"], c["chain_sequence"]) == key:
                raise RuntimeError("Duplicate entry for key uq_correction_settlement_chain")
        assert kw["reason"].strip()                                           # chk_correction_reason
        correction_id = str(len(self.store.corrections) + 1)
        self.store.corrections.append({"correction_id": correction_id, "correction_type": kw["correction_type"],
                                       "source_type": kw["source_type"], "source_id": str(kw["source_id"]),
                                       "chain_sequence": kw["chain_sequence"], "reason": kw["reason"],
                                       "after_json": kw["after"], "invoice_id": kw["invoice_id"]})
        return correction_id

    def insert_leg(self, **kw):
        self._maybe_fail("leg")
        self.store.legs.append(dict(kw, correction_id=str(kw["correction_id"])))

    def add_money(self, *, ledger, account_id, delta, type_code, correction_id, **kw):
        self._maybe_fail("money")
        self.store.money.append((ledger, account_id, delta, "TRANSACTION_CORRECTION", str(correction_id)))
        return str(len(self.store.money))

    def set_item_status(self, *, inventory_item_id, from_status, to_status, updated_by_user_id):
        self._maybe_fail("inventory")
        item = self.store.items[inventory_item_id]
        assert item["inventory_status"] == from_status
        item["inventory_status"] = to_status

    def add_inventory_movement(self, *, item, movement_code, from_status, to_status, **kw):
        self.store.inv_movements.append((item["inventory_item_id"], movement_code, from_status, to_status))

    def add_old_gold(self, *, karat_id, weight_delta, movement_code, **kw):
        self._maybe_fail("old_gold")
        self.store.old_gold.append((karat_id, weight_delta, movement_code))
        assert self.store.old_gold_balance(karat_id) >= 0                         # stock never negative

    def dismiss_review(self, *, review_id, **kw):
        self._maybe_fail("review")
        r = next(v for v in self.store.reviews if v["review_id"] == review_id)
        assert r["status"] == "OPEN"
        r["status"] = "DISMISSED"

    def cancel_case(self, *, case_id, **kw):
        c = next(v for v in self.store.cases if v["case_id"] == case_id)
        assert c["status"] == "OPEN"
        c["status"] = "CANCELLED"

    def mark_lines_reversed(self, *, table, line_ids, correction_id):
        for line_id in line_ids:
            assert line_id not in self.store.reversed_lines
            self.store.reversed_lines[line_id] = str(correction_id)


class CustomerRepo:
    def __init__(self, connection):
        self.store = connection.store

    def add_transaction(self, *, customer_id, type_code, amount_delta, reference_type, reference_id, **kw):
        Repo(Connection(self.store))._maybe_fail("customer")
        assert type_code in ("REVERSAL_ACCOUNT", "SETTLEMENT_CORRECTION") and amount_delta != 0
        self.store.ledger.append((str(customer_id), amount_delta, reference_type, str(reference_id)))
        return str(1000 + len(self.store.ledger))


class Authorization:
    def __init__(self, session, permissions):
        self.session, self.permissions = session, permissions

    def require_permission(self, connection, actor, code, **kwargs):
        if not self.session.is_current(actor) or code not in self.permissions:
            raise PermissionError("لا توجد صلاحية")
        return dataclasses.replace(actor)


MANAGER = frozenset({"APP_LOGIN", "MANAGEMENT_REVIEW", "TRANSACTION_CORRECT"})
REASON = "تم تسجيل الفاتورة بالخطأ"


class CorrectionTestCase(unittest.TestCase):
    def setUp(self):
        Repo.fail_on = None
        self.store = Store()
        self.service = self.make(MANAGER)

    def make(self, permissions):
        session = AppSession()
        session._establish(AuthenticationResult(3, None, "المدير", permissions, _VERIFIED), int(session.generation))

        @contextmanager
        def transaction():
            saved = copy.deepcopy(vars(self.store))
            try:
                yield Connection(self.store)
            except BaseException:
                self.store.__dict__.clear()
                self.store.__dict__.update(saved)
                raise
        return CorrectionService(session, authorization=Authorization(session, permissions),
                                 transaction_factory=transaction, invoice_repository=InvoiceRepo,
                                 correction_repository=Repo, customer_repository=CustomerRepo)

    def assertRefused(self, code, call):
        before = self.store.tables()
        with self.assertRaises(CorrectionError) as context:
            call()
        self.assertEqual(context.exception.code, code)
        self.assertEqual(self.store.tables(), before)
        return context.exception


# ----------------------------------------------------------------------------- 60. invoice reversal
class InvoiceReversalTests(CorrectionTestCase):
    def test_simple_identified_sale_back_in_stock_with_exact_money(self):
        original = copy.deepcopy(self.store.invoices["501"]["lines"])
        result = self.service.reverse("INVOICE", "501", REASON)
        self.assertEqual(result["code"], "RV000001")
        self.assertEqual(self.store.items["201"]["inventory_status"], "IN_STOCK")          # same item, same barcode
        self.assertEqual(self.store.inv_movements, [("201", "REVERSAL_SALE_RESTOCK", "SOLD", "IN_STOCK")])
        self.assertEqual(self.store.money[-1], ("CASH", 1, D("-30000.00"), "TRANSACTION_CORRECTION", "1"))
        self.assertEqual(self.store.invoices["501"]["lines"], original)                    # original unchanged
        self.assertEqual(self.store.payments[2]["amount"], D("30000.00"))
        self.assertEqual(self.store.invoices["501"]["events"][-1]["event_type"], "REVERSED")
        self.assertEqual(self.store.corrections[0]["reason"], REASON)                      # persisted exactly

    def test_mixed_invoice_reverses_both_sides_exactly(self):
        stock_before = self.store.old_gold_balance()
        self.service.reverse("INVOICE", "500", REASON)
        posted = [m for m in self.store.money if m[3] == "TRANSACTION_CORRECTION"]
        self.assertEqual(sorted((m[0], m[1], m[2]) for m in posted),
                         [("BANK", 7, D("-10000.00")), ("CASH", 1, D("-20000.00"))])       # never re-derived
        self.assertEqual(self.store.ledger[-1][:2], ("12", D("-5000.00")))
        self.assertEqual(self.store.items["200"]["inventory_status"], "IN_STOCK")
        codes = {c: g for _, g, c in self.store.old_gold[1:]}
        self.assertEqual(codes, {"REVERSAL_OLD_GOLD_SALE_IN": D("5.000"), "REVERSAL_OLD_GOLD_PURCHASE_OUT": D("-4.000")})
        self.assertEqual(self.store.old_gold_balance() - stock_before, D("1.000"))
        self.assertEqual(self.store.cases[0]["status"], "CANCELLED")                       # F5 no longer actionable

    def test_customer_old_gold_already_consumed_blocks(self):
        self.store.old_gold = [(1, D("3.000"), "OPENING_BALANCE")]                        # the 4.000 bought are gone
        error = self.assertRefused("OLD_GOLD_DEPENDENCY_EXISTS", lambda: self.service.reverse("INVOICE", "500", REASON))
        self.assertIn("تم استهلاكه لاحقاً", str(error))

    def test_downstream_exchange_or_return_blocks_the_invoice(self):
        for invoice_id, text in (("506", "الاستبدال EX000901"), ("503", "المرتجع RT000800")):
            with self.subTest(invoice=invoice_id):
                error = self.assertRefused("DOWNSTREAM_DEPENDENCY_EXISTS",
                                           lambda i=invoice_id: self.service.reverse("INVOICE", i, REASON))
                self.assertIn(text.split()[-1], str(error))
        preview = self.service.preview_reversal("INVOICE", "506")
        self.assertFalse(preview["can_reverse"])
        self.assertIn("EX000901", preview["blockers"][0]["message"])

    def test_only_one_full_reversal(self):
        self.service.reverse("INVOICE", "501", REASON)
        self.assertRefused("ALREADY_REVERSED", lambda: self.service.reverse("INVOICE", "501", REASON))


# ----------------------------------------------------------------------------- 61. Return reversal
class ReturnReversalTests(CorrectionTestCase):
    def test_identified_return_with_open_inspection(self):
        self.service.reverse("RETURN", "800", "تم تنفيذ المرتجع بالخطأ")
        self.assertEqual(self.store.items["203"]["inventory_status"], "SOLD")
        self.assertEqual(self.store.inv_movements, [("203", "REVERSAL_RETURN_TO_SOLD", "RETURNED_PENDING", "SOLD")])
        self.assertEqual(self.store.reviews[1]["status"], "DISMISSED")
        self.assertEqual(self.store.money[-1][:3], ("CASH", 1, D("12000.00")))              # the refund comes back IN
        self.assertEqual(self.store.ledger[-1][:2], ("12", D("500.00")))
        self.assertEqual(self.store.reversed_lines, {"8001": "1"})                          # eligibility restored
        self.assertEqual(self.store.returns["800"]["return_status"], "FINALIZED")           # never edited

    def test_disposed_piece_blocks(self):
        error = self.assertRefused("RETURN_ITEM_ALREADY_DISPOSED",
                                   lambda: self.service.reverse("RETURN", "802", "خطأ"))
        self.assertIn("المخزون", str(error))

    def test_f7_return_removes_the_exact_grams_again(self):
        before = self.store.old_gold_balance()
        self.service.reverse("RETURN", "801", "خطأ")
        self.assertEqual(self.store.old_gold[-1], (1, D("-3.000"), "REVERSAL_RETURN_OLD_GOLD_OUT"))
        self.assertEqual(self.store.old_gold_balance(), before - D("3.000"))
        self.assertEqual(self.store.reversed_lines, {"8011": "1"})

    def test_f7_grams_consumed_downstream_blocks(self):
        self.store.old_gold = [(1, D("2.500"), "OPENING_BALANCE")]
        self.assertRefused("OLD_GOLD_DEPENDENCY_EXISTS", lambda: self.service.reverse("RETURN", "801", "خطأ"))


# ----------------------------------------------------------------------------- 62. Exchange reversal
class ExchangeReversalTests(CorrectionTestCase):
    def test_every_exchange_effect_is_reversed(self):
        before = self.store.old_gold_balance()
        self.service.reverse("EXCHANGE", "900", "تم تنفيذ الاستبدال بالقطعة الخطأ")
        self.assertEqual(self.store.items["300"]["inventory_status"], "IN_STOCK")           # replacement restocked
        self.assertEqual(self.store.items["202"]["inventory_status"], "SOLD")               # original sold again
        self.assertEqual(sorted(self.store.inv_movements), sorted([
            ("202", "REVERSAL_EXCHANGE_RETURN_TO_SOLD", "RETURNED_PENDING", "SOLD"),
            ("300", "REVERSAL_EXCHANGE_REPLACEMENT_RESTOCK", "SOLD", "IN_STOCK")]))
        self.assertEqual(self.store.reviews[0]["status"], "DISMISSED")
        self.assertEqual(self.store.cases[1]["status"], "CANCELLED")
        self.assertEqual(self.store.old_gold_balance() - before, D("2.000"))                # F7 grams back
        self.assertEqual(self.store.money[-1][:3], ("CASH", 1, D("-5000.00")))
        self.assertEqual(self.store.ledger[-1][:2], ("12", D("-1000.00")))
        self.assertEqual(sorted(self.store.reversed_lines), ["9001", "9002", "9003", "9004"])

    def test_disposed_returned_piece_blocks(self):
        self.store.reviews[0].update(status="RESOLVED", code="CONVERT_TO_OLD_GOLD")
        self.store.items["202"]["inventory_status"] = "CONVERTED_TO_OLD_GOLD"
        self.assertRefused("RETURNED_ITEM_ALREADY_DISPOSED", lambda: self.service.reverse("EXCHANGE", "900", "خطأ"))

    def test_replacement_changed_later_blocks(self):
        self.store.items["300"]["inventory_status"] = "RETURNED_PENDING"
        self.assertRefused("DOWNSTREAM_DEPENDENCY_EXISTS", lambda: self.service.reverse("EXCHANGE", "900", "خطأ"))

    def test_after_exchange_reversal_the_invoice_can_be_reversed(self):
        self.store.items["206"]["inventory_status"] = "RETURNED_PENDING"
        self.service.reverse("EXCHANGE", "901", "خطأ")                                        # newest first
        self.assertEqual(self.store.items["206"]["inventory_status"], "SOLD")
        self.service.reverse("INVOICE", "506", REASON)
        self.assertEqual(self.store.items["206"]["inventory_status"], "IN_STOCK")


# ----------------------------------------------------------------------------- 63. settlement correction
class SettlementCorrectionTests(CorrectionTestCase):
    def correct(self, source_type, source_id, legs, account=None, reason="تم اختيار حساب بنكي خاطئ", **kw):
        return self.service.correct_settlement(source_type, source_id, [SettlementLeg(*l) for l in legs], account, reason, **kw)

    def test_cash_to_bank_moves_only_money(self):
        items, gold, lines = copy.deepcopy(self.store.items), list(self.store.old_gold), copy.deepcopy(self.store.invoices)
        result = self.correct("INVOICE", "501", [("BANK_TRANSFER", "30000.00", "7")])
        self.assertEqual(result["code"], "FC000001")
        posted = [m[:3] for m in self.store.money if m[3] == "TRANSACTION_CORRECTION"]
        self.assertEqual(sorted(posted), [("BANK", 7, D("30000.00")), ("CASH", 1, D("-30000.00"))])
        self.assertEqual((self.store.items, self.store.old_gold), (items, gold))
        self.assertEqual(self.store.payments[2]["method_code"], "PHYSICAL_CASH")          # original row unchanged
        self.assertEqual(self.store.invoices["501"]["lines"], lines["501"]["lines"])

    def test_split_and_then_a_second_correction_on_the_effective_state(self):
        self.correct("INVOICE", "501", [("PHYSICAL_CASH", "10000.00"), ("BANK_TRANSFER", "20000.00", "7")])
        state = self.service.current_settlement("INVOICE", "501")
        self.assertEqual([(l["method_code"], l["amount"]) for l in state["legs"]],
                         [("PHYSICAL_CASH", "10000.00"), ("BANK_TRANSFER", "20000.00")])
        self.assertEqual(state["next_sequence"], 2)
        self.correct("INVOICE", "501", [("BANK_TRANSFER", "30000.00", "7")], reason="التحويل كان كاملاً", expected_sequence=2)
        self.assertEqual(self.store.balance("CASH", 1) - D("30000.00") - D("20000.00") - D("30000.00") + D("30000.00"),
                         self.store.balance("CASH", 1) - D("50000.00"))
        effect_cash = sum((m[2] for m in self.store.money if (m[0], m[4]) == ("CASH", "501") or
                           (m[0] == "CASH" and m[3] == "TRANSACTION_CORRECTION")), D("0"))
        self.assertEqual(effect_cash, D("0.00"))                                           # cash net 0
        self.assertEqual(sum((m[2] for m in self.store.money if m[0] == "BANK" and m[3] == "TRANSACTION_CORRECTION"),
                             D("0")), D("30000.00"))
        self.assertEqual([c["chain_sequence"] for c in self.store.corrections], [1, 2])

    def test_bank_to_cash_and_wallet_eligibility(self):
        self.correct("INVOICE", "500", [("PHYSICAL_CASH", "30000.00")], account="5000.00")
        self.assertRefused("BANK_ACCOUNT_TYPE", lambda: self.correct("INVOICE", "500", [("MOBILE_WALLET", "30000.00", "7")],
                                                                     account="5000.00"))
        self.correct("INVOICE", "500", [("MOBILE_WALLET", "20000.00", "8"), ("INSTAPAY", "10000.00", "7")],
                     account="5000.00")

    def test_customer_account_reallocation(self):
        self.correct("INVOICE", "500", [("PHYSICAL_CASH", "25000.00"), ("BANK_TRANSFER", "10000.00", "7")], account="0")
        self.assertEqual(self.store.ledger[-1][:2], ("12", D("-5000.00")))                  # the account part released
        self.assertEqual(self.service.current_settlement("INVOICE", "500")["account"], "0.00")

    def test_total_preserved_and_zero_effect_refused(self):
        self.assertRefused("TOTAL_MISMATCH", lambda: self.correct("INVOICE", "501", [("BANK_TRANSFER", "29999.99", "7")]))
        self.assertRefused("NO_EFFECT_CORRECTION", lambda: self.correct("INVOICE", "501", [("PHYSICAL_CASH", "30000.00")]))
        self.assertRefused("ACCOUNT_NEEDS_CUSTOMER", lambda: self.correct("INVOICE", "506", [("PHYSICAL_CASH", "20000.00")],
                                                                          account="10000.00"))

    def test_direction_preserved_for_exchange_and_return(self):
        self.correct("RETURN", "800", [("BANK_TRANSFER", "12000.00", "7")], account="500.00")
        self.assertEqual(sorted(m[:3] for m in self.store.money if m[3] == "TRANSACTION_CORRECTION"),
                         [("BANK", 7, D("-12000.00")), ("CASH", 1, D("12000.00"))])        # still money OUT
        self.correct("EXCHANGE", "900", [("INSTAPAY", "5000.00", "7")], account="1000.00")
        self.assertEqual(sorted(m[:3] for m in self.store.money[-2:]),
                         [("BANK", 7, D("5000.00")), ("CASH", 1, D("-5000.00"))])        # still money IN

    def test_stale_chain_and_reversed_source_refused(self):
        self.correct("INVOICE", "501", [("BANK_TRANSFER", "30000.00", "7")])
        self.assertRefused("STALE_CORRECTION", lambda: self.correct("INVOICE", "501", [("PHYSICAL_CASH", "30000.00")],
                                                                    expected_sequence=1))
        self.service.reverse("INVOICE", "501", REASON)
        self.assertEqual(self.store.balance("BANK", 7), D("0.00") + D("10000.00"))        # reversal used the effective route
        self.assertRefused("ALREADY_REVERSED", lambda: self.correct("INVOICE", "501", [("PHYSICAL_CASH", "30000.00")]))


class ComposedChainTests(CorrectionTestCase):
    def test_settlement_correction_then_full_reversal_nets_everything_to_zero(self):
        """FC (Cash → Bank) then RV: the reversal negates the CURRENT effective settlement."""
        payments = copy.deepcopy(self.store.payments)
        cash0, bank0 = self.store.balance("CASH", 1) - D("30000.00"), self.store.balance("BANK", 7)   # before invoice 501
        self.service.correct_settlement("INVOICE", "501", [SettlementLeg("BANK_TRANSFER", "30000.00", "7")], None,
                                        "تم اختيار الخزنة بالخطأ")
        self.assertEqual((self.store.balance("CASH", 1) - cash0, self.store.balance("BANK", 7) - bank0),
                         (D("0.00"), D("30000.00")))                                          # effective: bank +30,000
        self.service.reverse("INVOICE", "501", REASON)
        self.assertEqual((self.store.balance("CASH", 1) - cash0, self.store.balance("BANK", 7) - bank0),
                         (D("0.00"), D("0.00")))                                              # whole chain nets to 0
        rv_rows = [m for m in self.store.money if m[3:] == ("TRANSACTION_CORRECTION", "2")]
        self.assertEqual(rv_rows, [("BANK", 7, D("-30000.00"), "TRANSACTION_CORRECTION", "2")])  # bank, not cash
        self.assertEqual(self.store.payments, payments)                                          # originals unchanged
        self.assertEqual([(c["correction_type"], c["source_id"]) for c in self.store.corrections],
                         [("SETTLEMENT_CORRECTION", "501"), ("FULL_REVERSAL", "501")])           # FC and RV kept
        self.assertEqual([m for m in self.store.inv_movements if m[0] == "201"],
                         [("201", "REVERSAL_SALE_RESTOCK", "SOLD", "IN_STOCK")])                 # restocked once
        # After RV: no further FC and no second RV.
        self.assertRefused("ALREADY_REVERSED", lambda: self.service.correct_settlement(
            "INVOICE", "501", [SettlementLeg("PHYSICAL_CASH", "30000.00")], None, "x"))
        self.assertRefused("ALREADY_REVERSED", lambda: self.service.reverse("INVOICE", "501", REASON))

    def test_customer_account_nets_to_zero_across_fc_and_rv(self):
        self.service.correct_settlement("INVOICE", "500", [SettlementLeg("PHYSICAL_CASH", "35000.00")], "0", "x")
        self.service.reverse("INVOICE", "500", REASON)
        self.assertEqual(sum((d for c, d, _, _ in self.store.ledger if c == "12" and _ in ("INVOICE",)
                              or (c == "12" and _ == "TRANSACTION_CORRECTION")), D("0")), D("0.00"))
        cash_chain = sum((m[2] for m in self.store.money if m[0] == "CASH" and
                          (m[3:] == ("INVOICE", "500") or m[3] == "TRANSACTION_CORRECTION")), D("0"))
        bank_chain = sum((m[2] for m in self.store.money if m[0] == "BANK" and
                          (m[3:] == ("INVOICE", "500") or m[3] == "TRANSACTION_CORRECTION")), D("0"))
        self.assertEqual((cash_chain, bank_chain), (D("0.00"), D("0.00")))


# ----------------------------------------------------------------------------- 64/65. permissions and reason
class PermissionAndReasonTests(CorrectionTestCase):
    def test_only_transaction_correct_may_correct(self):
        for permissions in (frozenset({"APP_LOGIN", "INVOICE_RETURN", "PAYMENT_RECORD"}),   # SALESPERSON / CASHIER
                            frozenset({"APP_LOGIN", "PAYMENT_RECORD"}),
                            frozenset({"APP_LOGIN", "MANAGEMENT_REVIEW"})):
            other = self.make(permissions)
            for call in (lambda: other.reverse("INVOICE", "501", REASON),
                         lambda: other.preview_reversal("INVOICE", "501"),
                         lambda: other.correct_settlement("INVOICE", "501", [SettlementLeg("BANK_TRANSFER", "30000.00", "7")],
                                                          None, REASON)):
                with self.subTest(permissions=sorted(permissions)):
                    with self.assertRaises(PermissionError):
                        call()
        owner = self.make(frozenset({"APP_LOGIN", "TRANSACTION_CORRECT"}))
        self.assertEqual(owner.reverse("INVOICE", "501", REASON)["code"], "RV000001")

    def test_blank_reason_refused_and_arabic_reason_persisted(self):
        for reason in ("", "   "):
            self.assertRefused("REASON_REQUIRED", lambda r=reason: self.service.reverse("INVOICE", "501", r))
            self.assertRefused("REASON_REQUIRED", lambda r=reason: self.service.correct_settlement(
                "INVOICE", "501", [SettlementLeg("BANK_TRANSFER", "30000.00", "7")], None, r))
        self.service.reverse("INVOICE", "501", "  تم تسجيل الفاتورة بالخطأ  ")
        self.assertEqual(self.store.corrections[0]["reason"], "تم تسجيل الفاتورة بالخطأ")


# ----------------------------------------------------------------------------- 52. rollback
class RollbackTests(CorrectionTestCase):
    def test_every_injected_failure_rolls_everything_back(self):
        for point, call in (("header", lambda: self.service.reverse("INVOICE", "500", REASON)),
                            ("money", lambda: self.service.reverse("INVOICE", "500", REASON)),
                            ("leg", lambda: self.service.reverse("INVOICE", "500", REASON)),
                            ("customer", lambda: self.service.reverse("INVOICE", "500", REASON)),
                            ("inventory", lambda: self.service.reverse("INVOICE", "500", REASON)),
                            ("old_gold", lambda: self.service.reverse("INVOICE", "500", REASON)),
                            ("review", lambda: self.service.reverse("RETURN", "800", REASON)),
                            ("event", lambda: self.service.reverse("EXCHANGE", "900", REASON)),
                            ("audit", lambda: self.service.reverse("EXCHANGE", "900", REASON)),
                            ("money", lambda: self.service.correct_settlement(
                                "INVOICE", "501", [SettlementLeg("BANK_TRANSFER", "30000.00", "7")], None, REASON))):
            with self.subTest(point=point):
                Repo.fail_on = point
                before = self.store.tables()
                with self.assertRaises(RuntimeError):
                    call()
                self.assertEqual(self.store.tables(), before)
        Repo.fail_on = None
        self.assertEqual(self.service.reverse("INVOICE", "500", REASON)["code"], "RV000001")


if __name__ == "__main__":
    unittest.main()
