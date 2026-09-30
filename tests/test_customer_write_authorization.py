"""Customer create / edit / deactivate / reactivate require CUSTOMERS_MANAGE through the strict (locking)
write check inside the mutation's own transaction. No application database."""
from unittest.mock import MagicMock, patch
import unittest

from services.authorization_service import AuthorizationService
from services.customer_service import CustomerService
from tests.auth_fakes import customers_page_service
from tests.test_invoice_service import InvoiceServiceTestCase

MANAGER = frozenset({"APP_LOGIN", "CUSTOMERS_VIEW", "CUSTOMERS_MANAGE"})
VIEWER = frozenset({"APP_LOGIN", "CUSTOMERS_VIEW"})
EXISTING = {"id": 143, "customer_code": "C000143", "name": "عميل", "mobile": None, "place": None,
            "customer_type_id": None, "notes": None, "is_active": 1}
SNAPSHOT = {k: EXISTING[k] for k in ("name", "mobile", "place", "customer_type_id", "notes", "is_active")}


class CustomerWriteCase(unittest.TestCase):
    def setUp(self):
        self.connection = MagicMock()
        self.cursor = self.connection.cursor.return_value
        self.cursor.fetchone.return_value = dict(EXISTING)
        self.cursor.fetchall.return_value = []
        self.cursor.lastrowid = 144
        self.cursor.rowcount = 1
        self.transaction = MagicMock()
        self.transaction.connection = self.connection
        self.transaction.__enter__.return_value = self.transaction
        self.transaction.__exit__.return_value = False
        self.enterContext(patch("services.customer_service.DatabaseTransaction", return_value=self.transaction))

    def statements(self):
        return [str(call.args[0]) for call in self.cursor.execute.call_args_list]

    def writes(self):
        return [s for s in self.statements() if s.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE"))]

    def mutations(self, service):
        return {"create": lambda: service.create_customer(name="عميل جديد"),
                "edit": lambda: service.update_customer("143", name="اسم معدل", mobile=None, place=None,
                                                        customer_type_id=None, notes=None,
                                                        original_values=dict(SNAPSHOT)),
                "deactivate": lambda: service.deactivate_customer("143"),
                "reactivate": lambda: service.reactivate_customer("143")}


class AuthorizedCustomerWrites(CustomerWriteCase):
    def test_every_mutation_succeeds_with_customers_manage_through_the_write_path(self):
        for name in ("create", "edit", "deactivate", "reactivate"):
            with self.subTest(mutation=name):
                self.cursor.execute.reset_mock()
                self.cursor.fetchone.return_value = dict(EXISTING, is_active=0 if name == "reactivate" else 1)
                service = customers_page_service(MANAGER)
                self.mutations(service)[name]()
                self.assertEqual(service._authorization.checked, [("WRITE", "CUSTOMERS_MANAGE")])   # strict path
                self.assertEqual(len(self.writes()), 1 if name != "create" else 2)   # create also sets its code
                self.assertIn("customers", self.writes()[0])

    def test_writes_are_attributed_to_the_verified_actor_not_the_caller(self):
        service = customers_page_service(MANAGER)
        service.create_customer(name="عميل جديد", user_id=999)               # a caller-supplied id is ignored
        insert = next(call for call in self.cursor.execute.call_args_list if "INSERT INTO customers" in str(call.args[0]))
        self.assertEqual(insert.args[1][-1], 1)                              # the session's verified user


class UnauthorizedCustomerWrites(CustomerWriteCase):
    def refused_without_any_write(self, service):
        for name, mutation in self.mutations(service).items():
            with self.subTest(mutation=name):
                self.cursor.execute.reset_mock()
                with self.assertRaises(PermissionError):
                    mutation()
                self.assertEqual(self.statements(), [])                      # nothing read or written: no partial write

    def test_customers_view_alone_cannot_mutate(self):
        self.refused_without_any_write(customers_page_service(VIEWER))

    def test_revocation_is_honored_immediately_despite_a_stale_session(self):
        service = customers_page_service(MANAGER)
        service.create_customer(name="عميل جديد")                            # allowed
        service._authorization.permissions = VIEWER                          # revoked in the database only
        self.assertIn("CUSTOMERS_MANAGE", service._session.current_actor().permissions)      # the session is stale
        self.refused_without_any_write(service)
        service._authorization.permissions = MANAGER                         # restored: works again, no re-login
        service.create_customer(name="عميل جديد")

    def test_billing_and_other_permissions_cannot_mutate(self):
        for permissions in (frozenset({"APP_LOGIN", "INVOICE_CREATE", "INVOICE_FINALIZE", "PAYMENT_RECORD"}),
                            frozenset({"APP_LOGIN", "CUSTOMER_ACCOUNT_SETTLE"}), frozenset({"APP_LOGIN"})):
            self.refused_without_any_write(customers_page_service(permissions))

    def test_a_service_without_a_session_or_with_a_logged_out_session_is_refused(self):
        self.refused_without_any_write(CustomerService())
        service = customers_page_service(MANAGER)
        service._session.logout()
        self.refused_without_any_write(service)

    def test_the_transaction_is_rolled_back_on_refusal(self):
        service = customers_page_service(VIEWER)
        with self.assertRaises(PermissionError):
            service.create_customer(name="عميل جديد")
        exit_args = self.transaction.__exit__.call_args.args
        self.assertIs(exit_args[0], PermissionError)                         # the context manager saw the refusal


class RealAuthorizationServiceLocks(CustomerWriteCase):
    """With the real AuthorizationService the write check locks the identity and grants; reads never do."""

    class Repository:
        locks, grants = [], set()

        def __init__(self, connection):
            pass

        def get_identity(self, user_id, *, lock=False):
            type(self).locks.append(("identity", lock))
            return {"id": 1, "employee_id": None, "display_name": "موظف", "is_active": True,
                    "must_change_password": False}

        def effective_permissions(self, user_id, *, lock=False):
            type(self).locks.append(("permissions", lock))
            return frozenset(type(self).grants)

    def test_mutation_locks_and_read_does_not(self):
        service = customers_page_service(MANAGER)
        service._authorization = AuthorizationService(service._session, repository_factory=self.Repository)
        self.Repository.locks, self.Repository.grants = [], set(MANAGER)
        service.deactivate_customer("143")
        self.assertEqual(self.Repository.locks, [("identity", True), ("permissions", True)])
        self.Repository.locks = []
        service.search_customers()
        self.assertEqual(self.Repository.locks, [("identity", False), ("permissions", False)])
        self.Repository.grants = set(VIEWER)                                 # revoked in the database
        self.cursor.execute.reset_mock()
        with self.assertRaises(PermissionError):
            service.deactivate_customer("143")
        self.assertEqual(self.writes(), [])


class BillingIsolationTests(InvoiceServiceTestCase):
    permissions = frozenset({"APP_LOGIN", "INVOICE_CREATE", "INVOICE_FINALIZE", "PAYMENT_RECORD"})   # no CUSTOMERS_MANAGE

    def test_billing_user_selects_existing_customers_but_the_picker_cannot_mutate(self):
        before = {key: dict(value) for key, value in self.store.customers.items()}
        rows = self.service.search_customers("عميل")
        self.assertEqual([r["customer_id"] for r in rows], ["12"])
        self.assertEqual(set(rows[0]), {"customer_id", "customer_code", "name", "mobile", "place", "balance",
                                        "dead_cash_warning"})                # the approved field set, unchanged
        self.assertEqual(self.store.customers, before)                       # a search changes nothing
        self.assertEqual(self.authorization.checked, [])                     # and takes no write check
        # The Billing service exposes no customer edit / deactivate / reactivate operation at all.
        for name in dir(self.service):
            if "customer" in name.lower() and not name.startswith("_"):
                self.assertEqual(name, "search_customers")


if __name__ == "__main__":
    unittest.main()
