"""The customers page list of written-off balances: totals per customer, DEAD_CASH_VIEW enforced by the service (no database)."""
from contextlib import contextmanager
from decimal import Decimal as D
import unittest

from app.session import AppSession
from services.authentication_service import AuthenticationResult, _VERIFIED
from services.dead_cash_service import DeadCashService
from tests.auth_fakes import FakeAuthorization

VIEWER = frozenset({"APP_LOGIN", "CUSTOMERS_VIEW", "DEAD_CASH_VIEW"})
NO_VIEW = frozenset({"APP_LOGIN", "CUSTOMERS_VIEW"})
ROWS = [{"customer_id": 12, "name": "أحمد", "customer_code": "C000012", "writeoffs": 2, "written_off": D("8000.00"), "recovered": D("1500.00")},
        {"customer_id": 40, "name": "سعيد", "customer_code": "C000040", "writeoffs": 1, "written_off": D("500.50"), "recovered": D("0")}]


class Repo:
    def __init__(self, connection):
        pass

    def writeoff_totals_by_customer(self):
        return [dict(row) for row in ROWS]


class WriteoffListTests(unittest.TestCase):
    def service(self, permissions, database=None):
        session = AppSession()
        session._establish(AuthenticationResult(3, None, "المالك", permissions, _VERIFIED), int(session.generation))

        @contextmanager
        def transaction():
            yield object()
        return DeadCashService(session, transaction_factory=transaction, repository=Repo,
                               authorization=FakeAuthorization(session, permissions if database is None else database))

    def test_totals_are_the_exact_sums_with_remaining_after_recoveries(self):
        data = self.service(VIEWER).writeoff_summary()
        self.assertEqual(data["written_off"], "8500.50")
        self.assertEqual(data["recovered"], "1500.00")
        self.assertEqual(data["remaining"], "7000.50")
        self.assertEqual([(i["customer_id"], i["remaining"]) for i in data["items"]], [("12", "6500.00"), ("40", "500.50")])

    def test_without_the_view_right_it_is_refused(self):
        with self.assertRaises(PermissionError):
            self.service(NO_VIEW).writeoff_summary()

    def test_the_database_right_is_rechecked(self):
        with self.assertRaises(PermissionError):
            self.service(VIEWER, database=NO_VIEW).writeoff_summary()


if __name__ == "__main__":
    unittest.main()
