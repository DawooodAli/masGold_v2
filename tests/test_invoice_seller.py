import unittest
from contextlib import contextmanager
from types import SimpleNamespace

from services.invoice_seller_service import InvoiceSellerError, InvoiceSellerService


class Store:
    def __init__(self):
        self.invoice = {"id": 5, "invoice_number": "26103005", "invoice_status": "FINALIZED", "employee_id": 1, "employee_name": "علي"}
        self.employees = {1: {"id": 1, "name": "علي", "is_active": 1}, 2: {"id": 2, "name": "محمود", "is_active": 1}, 3: {"id": 3, "name": "قديم", "is_active": 0}}
        self.audits, self.updates = [], []


class Repo:
    store = None

    def __init__(self, connection):
        pass

    def lock_invoice(self, invoice_id):
        return dict(self.store.invoice) if int(invoice_id) == 5 else None

    def employee(self, employee_id):
        return self.store.employees.get(int(employee_id))

    def active_employees(self):
        return [{"id": e["id"], "name": e["name"]} for e in self.store.employees.values() if e["is_active"]]

    def set_seller(self, invoice_id, employee_id):
        self.store.updates.append((invoice_id, employee_id))
        self.store.invoice["employee_id"] = employee_id
        return 1

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
    return InvoiceSellerService(Session(permissions), transaction_factory=transaction, repository=repo, authorization=Authorization()), store


class InvoiceSellerTests(unittest.TestCase):
    def test_a_manager_changes_only_the_seller_with_a_reason_and_one_audit(self):
        service, store = build()
        self.assertEqual(service.change("5", "2", "اتكتب بالغلط")["seller"], "محمود")
        self.assertEqual(store.updates, [(5, 2)])
        meta = store.audits[0]["metadata"]
        self.assertEqual((meta["old_employee_name"], meta["new_employee_name"], meta["reason"]), ("علي", "محمود", "اتكتب بالغلط"))

    def test_refusals(self):
        service, store = build()
        for args, code in ((("5", "2", " "), "REASON_REQUIRED"), (("5", "", "x"), "EMPLOYEE_REQUIRED"), (("5", "3", "x"), "EMPLOYEE_INVALID"),
                           (("5", "1", "x"), "NO_CHANGE"), (("99", "2", "x"), "INVOICE_NOT_FOUND")):
            with self.assertRaises(InvoiceSellerError) as caught:
                service.change(*args)
            self.assertEqual(caught.exception.code, code)
        store.invoice["invoice_status"] = "VOIDED"
        with self.assertRaises(InvoiceSellerError):
            service.change("5", "2", "x")
        self.assertEqual(store.updates, [])

    def test_the_right_is_required_and_the_list_has_active_employees_only(self):
        service, _ = build(("APP_LOGIN",))
        with self.assertRaises(PermissionError):
            service.change("5", "2", "x")
        with self.assertRaises(PermissionError):
            service.employees()
        manager, _ = build()
        self.assertEqual([e["name"] for e in manager.employees()], ["علي", "محمود"])


if __name__ == "__main__":
    unittest.main()
