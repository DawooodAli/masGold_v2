"""تغيير العميل: a manager moves a finalized invoice to the right customer (owner 2026-11-25). The rules, the refusals, what moves and what does not, the audit and the lock order. No database."""
from contextlib import contextmanager
from decimal import Decimal as D
from types import SimpleNamespace
import unittest

from services.invoice_customer_service import InvoiceCustomerError, InvoiceCustomerService


class Store:
    def __init__(self):
        self.invoice = {"id": "5", "invoice_number": "26112520", "invoice_status": "FINALIZED", "customer_id": "1", "customer_code_snapshot": "C000001", "customer_name_snapshot": "اسلم بدران"}
        self.customers = {1: {"id": "1", "customer_code": "C000001", "name": "اسلم بدران", "mobile": "010", "place": "الحارث", "is_active": 1, "merged_into_customer_id": None, "customer_type_name": "عادي"},
                          2: {"id": "2", "customer_code": "C000002", "name": "أسلم بدران", "mobile": "011", "place": "الحارث", "is_active": 1, "merged_into_customer_id": None, "customer_type_name": "عادي"},
                          3: {"id": "3", "customer_code": "C000003", "name": "قديم", "mobile": None, "place": None, "is_active": 0, "merged_into_customer_id": None, "customer_type_name": None},
                          4: {"id": "4", "customer_code": "C000004", "name": "مدموج", "mobile": None, "place": None, "is_active": 1, "merged_into_customer_id": 2, "customer_type_name": None}}
        self.balances = {1: D("29400.00"), 2: D("-500.00")}
        self.ledger = [{"id": "50", "customer_id": "1", "amount_delta": D("29400.00")}]
        self.dependents = {"exchange": 0, "return": 0, "correction": 0}
        self.moves, self.audits, self.order = [], [], []


class Repo:
    store = None

    def __init__(self, connection):
        pass

    def invoice(self, invoice_id, lock=False):
        if lock:
            self.store.order.append(("invoice", int(invoice_id)))
        return dict(self.store.invoice) if int(invoice_id) == 5 else None

    def customer(self, customer_id, lock=False):
        if lock:
            self.store.order.append(("customer", int(customer_id)))
        row = self.store.customers.get(int(customer_id))
        return dict(row) if row else None

    def balance(self, customer_id):
        return self.store.balances.get(int(customer_id), D("0.00"))

    def ledger_rows(self, invoice_id, lock=False):
        return [dict(r) for r in self.store.ledger]

    def dependents(self, invoice_id):
        return dict(self.store.dependents)

    def move(self, invoice_id, old_customer_id, customer):
        self.store.moves.append((int(invoice_id), old_customer_id, int(customer["id"])))
        return len(self.store.ledger), 0

    def audit(self, **values):
        self.store.audits.append(values)


class Session:
    def __init__(self, permissions):
        self.actor = SimpleNamespace(user_id=9, permissions=frozenset(permissions))

    def require_authenticated(self):
        return self.actor

    def current_actor(self):
        return self.actor

    def is_current(self, actor):
        return True


class Authorization:
    def require_permission_for_write(self, connection, actor, permission):
        if permission not in actor.permissions:
            raise PermissionError("denied")
        return actor


def build(permissions=("TRANSACTION_CORRECT",)):
    store = Store()
    repo = type("R", (Repo,), {"store": store})

    @contextmanager
    def transaction():
        yield object()
    return InvoiceCustomerService(Session(permissions), transaction_factory=transaction, repository=repo, authorization=Authorization()), store


class ChangeTests(unittest.TestCase):
    def test_a_manager_moves_the_invoice_with_a_reason_and_one_audit_of_both_customers_and_balances(self):
        service, store = build()
        result = service.change("5", "2", "اسم العميل اتكتب بالغلط", True)
        self.assertEqual((result["customer"], result["ledger_rows_moved"]), ("أسلم بدران", 1))
        self.assertEqual(store.moves, [(5, 1, 2)])
        meta = store.audits[0]["metadata"]
        self.assertEqual((meta["old_customer_id"], meta["new_customer_id"], meta["reason"]), (1, 2, "اسم العميل اتكتب بالغلط"))
        self.assertEqual((meta["account_effect"], meta["old_balance"], meta["old_balance_after"], meta["new_balance"], meta["new_balance_after"]),
                         ("29400.00", "29400.00", "0.00", "-500.00", "28900.00"))
        self.assertEqual(len(store.audits), 1)

    def test_the_customers_are_locked_in_ascending_id_order_then_the_invoice(self):
        service, store = build()
        service.change("5", "2", "x", True)
        self.assertEqual(store.order, [("customer", 1), ("customer", 2), ("invoice", 5)])
        service, store = build()
        store.invoice["customer_id"] = "2"
        store.ledger = []
        service.change("5", "1", "x", True)
        self.assertEqual([o for o in store.order if o[0] == "customer"], [("customer", 1), ("customer", 2)])

    def test_an_invoice_without_an_account_entry_moves_no_balance(self):
        service, store = build()
        store.ledger = []
        preview = service.preview("5", "2")
        self.assertEqual(D(preview["account_effect"]), 0)
        self.assertEqual((preview["old_balance"], preview["old_balance_after"]), ("29400.00", "29400.00"))
        service.change("5", "2", "x", True)
        self.assertEqual(store.audits[0]["metadata"]["account_effect"], "0.00")

    def test_a_credit_entry_moves_the_credit_to_the_new_customer(self):
        service, store = build()
        store.ledger = [{"id": "50", "customer_id": "1", "amount_delta": D("-1000.00")}]
        preview = service.preview("5", "2")
        self.assertEqual((preview["old_balance_after"], preview["new_balance_after"]), ("30400.00", "-1500.00"))

    def test_a_walk_in_invoice_can_be_given_a_customer(self):
        service, store = build()
        store.invoice["customer_id"] = None
        store.ledger = []
        preview = service.preview("5", "2")
        self.assertEqual((preview["old"]["id"], preview["old_balance"]), (None, None))
        service.change("5", "2", "x", True)
        self.assertEqual(store.moves, [(5, None, 2)])

    def test_the_preview_writes_nothing(self):
        service, store = build()
        service.preview("5", "2")
        self.assertEqual((store.moves, store.audits, store.order), ([], [], []))


class RefusalTests(unittest.TestCase):
    def refused(self, code, args):
        service, store = build()
        with self.assertRaises(InvoiceCustomerError) as caught:
            service.change(*args)
        self.assertEqual(caught.exception.code, code)
        self.assertEqual((store.moves, store.audits), ([], []))

    def test_input_refusals(self):
        for args, code in ((("5", "2", " ", True), "REASON_REQUIRED"), (("5", "2", "x" * 301, True), "REASON_TOO_LONG"), (("5", "2", "x", False), "CONFIRMATION_REQUIRED"),
                           (("5", "", "x", True), "CUSTOMER_REQUIRED"), (("0", "2", "x", True), "INVOICE_NOT_FOUND"), (("9", "2", "x", True), "INVOICE_NOT_FOUND"),
                           (("5", "99", "x", True), "CUSTOMER_NOT_FOUND"), (("5", "1", "x", True), "NO_CHANGE"), (("5", "3", "x", True), "CUSTOMER_INACTIVE"),
                           (("5", "4", "x", True), "CUSTOMER_INACTIVE")):
            with self.subTest(code=code):
                self.refused(code, args)

    def test_an_invoice_that_is_not_finalized_is_refused(self):
        service, store = build()
        store.invoice["invoice_status"] = "VOIDED"
        with self.assertRaises(InvoiceCustomerError) as caught:
            service.change("5", "2", "x", True)
        self.assertEqual(caught.exception.code, "INVOICE_NOT_FINALIZED")

    def test_an_exchange_a_return_or_a_correction_on_the_invoice_blocks_the_change(self):
        for name, word in (("exchange", "استبدال"), ("return", "مرتجع"), ("correction", "تصحيح")):
            with self.subTest(name=name):
                service, store = build()
                store.dependents[name] = 1
                with self.assertRaises(InvoiceCustomerError) as caught:
                    service.change("5", "2", "x", True)
                self.assertEqual(caught.exception.code, "HAS_DEPENDENTS")
                self.assertIn(word, str(caught.exception))
                self.assertEqual(store.moves, [])

    def test_a_ledger_entry_that_does_not_belong_to_the_invoice_customer_blocks_the_change(self):
        service, store = build()
        store.ledger = [{"id": "50", "customer_id": "7", "amount_delta": D("10.00")}]
        with self.assertRaises(InvoiceCustomerError) as caught:
            service.change("5", "2", "x", True)
        self.assertEqual(caught.exception.code, "LEDGER_MISMATCH")

    def test_the_right_is_required_for_the_preview_and_the_change(self):
        service, store = build(permissions=("INVOICE_VIEW",))
        self.assertFalse(service.can_change())
        with self.assertRaises(PermissionError):
            service.preview("5", "2")
        with self.assertRaises(PermissionError):
            service.change("5", "2", "x", True)
        self.assertEqual(store.moves, [])


if __name__ == "__main__":
    unittest.main()
