"""Owner 2026-11-08 (migration 101): the bar fee «أجر السبيكة» goes to the custody cash ONLY, for a supplier as for a marketer. In-memory stubs, no database."""
import unittest
from contextlib import contextmanager
from datetime import date

from services.market_trip_service import MarketTripService
from tests.auth_fakes import FakeAuthorization
from tests.test_inventory_stock import session_for

OWNER = frozenset({"APP_LOGIN", "MARKET_TRIP_MANAGE"})                   # no SUPPLIER_MAKING_PAY: the fee no longer needs it


class Trips:
    log = []

    def __init__(self, connection):
        pass

    def has_bar_fee_schema(self):
        return True

    def trip(self, trip_id, lock=False):
        return {"id": trip_id, "status": "OPEN"}

    def by_request(self, key):
        return None

    def db_now(self):
        return None

    def next_entry_no(self, trip_id):
        return 4

    def insert_entry(self, **values):
        Trips.log.append(("entry", values["kind"]))
        return 70

    def insert_bar_fee(self, **values):
        Trips.log.append(("fee", values))

    def audit(self, **values):
        Trips.log.append(("audit", values["event_type"]))


class Suppliers:
    calls = []

    def __init__(self, connection):
        pass

    def get_supplier(self, party_id, lock=False):
        return {"id": party_id, "name": "مورد", "is_supplier": True, "is_intermediary": False}

    def making_type_id(self, code):
        Suppliers.calls.append("making_type")
        return 1

    def insert_making_movement(self, **values):
        Suppliers.calls.append("making_movement")
        return 1


@contextmanager
def transaction():
    yield object()


class BarFeeTests(unittest.TestCase):
    def setUp(self):
        Trips.log, Suppliers.calls = [], []
        session = session_for(OWNER, 9)
        self.service = MarketTripService(session, transaction_factory=transaction, repository=Trips, supplier_payments=Suppliers, authorization=FakeAuthorization(session),
                                         price_reference=lambda *a, **k: None)

    def test_a_supplier_fee_enters_the_custody_cash_only(self):
        result = self.service.bar_fee("5", "11", "250", date(2026, 11, 8), "", "", "55555555-5555-4555-8555-000000000001")
        self.assertEqual(result["amount"], "250.00")
        self.assertEqual(Suppliers.calls, [])                                                    # no supplier making movement at all
        fee = next(v for k, v in Trips.log if k == "fee")
        self.assertEqual((fee["party_kind"], fee["making_movement_id"]), ("SUPPLIER", None))

    def test_a_fee_without_any_party_is_custody_cash_only(self):
        result = self.service.bar_fee("5", "", "90", date(2026, 11, 8), "", "", "55555555-5555-4555-8555-000000000003")
        self.assertEqual((result["amount"], result["party_id"]), ("90.00", ""))
        self.assertEqual(Suppliers.calls, [])
        fee = next(v for k, v in Trips.log if k == "fee")
        self.assertEqual((fee["party_id"], fee["party_kind"], fee["making_movement_id"]), (None, None, None))

    def test_it_needs_no_supplier_payment_right(self):
        self.assertNotIn("SUPPLIER_MAKING_PAY", OWNER)
        self.service.bar_fee("5", "11", "10", date(2026, 11, 8), "", "", "55555555-5555-4555-8555-000000000002")


if __name__ == "__main__":
    unittest.main()
