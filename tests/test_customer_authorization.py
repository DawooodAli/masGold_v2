"""Customers page (CUSTOMERS_VIEW) and the Billing customer picker (invoice permissions) are two separate,
database-checked, non-locking read contexts. No application database."""
from unittest.mock import MagicMock, patch
import unittest

from services.customer_service import CustomerService
from tests.auth_fakes import customers_page_service
from tests.test_invoice_service import InvoiceServiceTestCase

BILLING_FIELDS = {"customer_id", "customer_code", "name", "mobile", "place", "balance", "dead_cash_warning"}


class CustomersPageAuthorizationTests(unittest.TestCase):
    def setUp(self):
        self.connection = MagicMock()
        self.connection.cursor.return_value.fetchall.return_value = []
        self.connection.cursor.return_value.fetchone.return_value = None
        transaction = MagicMock()
        transaction.connection = self.connection
        transaction.__enter__.return_value = transaction
        transaction.__exit__.return_value = False
        self.enterContext(patch("services.customer_service.DatabaseTransaction", return_value=transaction))
        self.service = customers_page_service()
        self.authorization = self.service._authorization

    def reads(self):
        service = self.service
        return {"search": lambda: service.search_customers(search="أحمد"),
                "details": lambda: service.get_customer_details("12"),
                "picker selection": lambda: service.get_customer_for_selection("12"),
                "statement": lambda: service.get_customer_statement("12"),
                "profile": lambda: service.get_customer_profile("12"),
                "dead cash": lambda: service.get_dead_cash_history("12")}

    def test_customers_view_opens_the_page_with_a_non_locking_check(self):
        result = self.service.search_customers(search="أحمد")
        self.assertEqual(result["items"], [])
        self.assertEqual(self.authorization.checked, [("READ", "CUSTOMERS_VIEW")])     # one check, read path
        executed = " ".join(str(call.args[0]) for call in self.connection.cursor.return_value.execute.call_args_list)
        self.assertNotIn("FOR UPDATE", executed)                                       # no lock of any kind

    def test_revocation_blocks_the_next_read_and_restoring_reopens_it(self):
        self.service.search_customers()
        self.authorization.permissions = frozenset({"APP_LOGIN"})          # revoked in the database only
        self.assertIn("CUSTOMERS_VIEW", self.service._session.current_actor().permissions)   # session is stale
        for name, read in self.reads().items():
            with self.subTest(read=name), self.assertRaises(PermissionError):
                read()
        self.authorization.permissions = frozenset({"APP_LOGIN", "CUSTOMERS_VIEW"})
        self.service.search_customers()                                     # no re-login needed
        self.assertEqual({kind for kind, _ in self.authorization.checked}, {"READ"})

    def test_billing_permissions_do_not_open_the_customers_page(self):
        billing_only = customers_page_service(frozenset({"APP_LOGIN", "INVOICE_CREATE", "INVOICE_FINALIZE"}))
        for read in (billing_only.search_customers, lambda: billing_only.get_customer_profile("12"),
                     lambda: billing_only.get_customer_statement("12")):
            with self.assertRaises(PermissionError):
                read()

    def test_a_service_without_a_session_is_refused(self):
        with self.assertRaises(PermissionError):
            CustomerService().search_customers()
        self.assertFalse(self.connection.cursor.return_value.execute.called)   # nothing was read


class BillingPickerAuthorizationTests(InvoiceServiceTestCase):
    permissions = frozenset({"APP_LOGIN", "INVOICE_CREATE", "INVOICE_FINALIZE", "PAYMENT_RECORD"})   # no CUSTOMERS_VIEW

    def test_billing_user_without_customers_view_can_search_and_select(self):
        self.store.customers["12"]["mobile"] = "01001234567"
        by_name = self.service.search_customers("عميل")
        by_phone = self.service.search_customers("0100 123")
        self.assertEqual([r["customer_id"] for r in by_name], ["12"])
        self.assertEqual([r["customer_id"] for r in by_phone], ["12"])
        self.assertNotIn("CUSTOMERS_VIEW", self.permissions)
        self.assertEqual(self.authorization.read_checks[-1], ("INVOICE_CREATE", "INVOICE_FINALIZE"))
        self.assertEqual(self.authorization.checked, [])                   # no locking (write) check for a read

    def test_picker_returns_only_the_fields_billing_needs(self):
        rows = self.service.search_customers("عميل")
        self.assertTrue(rows)
        for row in rows:
            self.assertEqual(set(row), BILLING_FIELDS)                     # no statement / history / profile data
        self.assertLessEqual(len(rows), 8)
        for name in ("statement", "profile", "dead_cash_history", "transactions"):
            self.assertFalse(any(name in attr for attr in dir(self.service) if "customer" in attr), name)

    def test_revoked_invoice_permissions_block_the_picker(self):
        self.service.search_customers("عميل")
        self.authorization.permissions = frozenset({"APP_LOGIN", "CUSTOMERS_VIEW"})    # invoice rights revoked
        with self.assertRaises(PermissionError):
            self.service.search_customers("عميل")                          # CUSTOMERS_VIEW is not a way in
        self.authorization.permissions = frozenset({"APP_LOGIN", "INVOICE_FINALIZE"})  # one invoice right back
        self.assertTrue(self.service.search_customers("عميل"))


class NeitherRouteTests(InvoiceServiceTestCase):
    permissions = frozenset({"APP_LOGIN", "INVOICE_RETURN"})               # e.g. a role with neither right

    def test_user_with_neither_permission_can_use_neither_route(self):
        with self.assertRaises(PermissionError):
            self.service.search_customers("عميل")                          # Billing picker
        page = customers_page_service(self.permissions)
        transaction = MagicMock()
        transaction.__enter__.return_value = transaction
        transaction.__exit__.return_value = False
        with patch("services.customer_service.DatabaseTransaction", return_value=transaction):
            with self.assertRaises(PermissionError):
                page.search_customers()                                    # Customers page
        self.assertFalse(transaction.connection.cursor.return_value.execute.called)


if __name__ == "__main__":
    unittest.main()
