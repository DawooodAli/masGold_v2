"""عهدة السوق والموردين on the iPad: the overview read and the seven whitelisted writes go through the EXISTING services, each needs the current password, the confirmation and the request key. Fakes only."""
from decimal import Decimal as D
from unittest import mock
import unittest

from tests.test_webapp import ApiError, OWNER, make_api

KEY = "11111111-1111-4111-8111-111111111111"
CALLS = []


class FakeTrips:
    fail = None

    def __init__(self, session):
        pass

    def context(self, *, _expected_actor=None):
        return {"safe": {"id": "1", "name": "الخزنة", "active": True, "balance": D("50000.00")},
                "karats": [{"id": 1, "karat_value": 21, "name": "21"}, {"id": 2, "karat_value": 18, "name": "18"}],
                "shop_scrap": [{"karat_id": 1, "karat_value": 21, "available": D("100.500"), "physical": D("100.500"), "held": D("0")}]}

    def trip_page(self, status, page, size, *, _expected_actor=None):
        custody = {"cash": {"outstanding": D("12000.00")}, "gold": [{"karat_id": 1, "karat_value": 21, "designation": "UNCLASSIFIED", "outstanding": D("5.250")}],
                   "bar": D("0"), "lots": [], "settled": False}
        return {"rows": [{"id": "7", "code": "MT000007", "notes": "رحلة", "custody": custody, "external_holdings": []}], "has_more": False}

    def _run(self, name, *args):
        CALLS.append((name,) + args)
        if FakeTrips.fail:
            raise FakeTrips.fail
        return {"trip_code": "MT000007", "code": "MT000007-1", "duplicate": False, "entries": [1]}

    def create_trip(self, cash, gold, lots, date, notes, key, *, _expected_actor=None):
        return self._run("create", cash, gold, lots, notes, key)

    def return_custody(self, trip_id, cash, gold, date, notes, key, *, _expected_actor=None):
        return self._run("return", trip_id, cash, gold, key)

    def record_custody_expense(self, trip_id, amount, description, date, reference, notes, key, *, _expected_actor=None):
        return self._run("expense", trip_id, amount, description, key)

    def close(self, trip_id, *, _expected_actor=None):
        return self._run("close", trip_id)


class FakeSuppliers:
    def __init__(self, session):
        pass

    def list_suppliers(self, search, active_only, role, *, _expected_actor=None):
        return [{"id": "3", "name": "مورد أ", "karat_id": 1, "karat_value": 21, "is_active": True, "is_supplier": True, "gold_balance": D("12.500"), "making": D("-300.00")},
                {"id": "4", "name": "مسوقجي", "karat_id": None, "karat_value": None, "is_active": True, "is_supplier": False, "gold_balance": None, "making": D("0")}]


class FakePayments:
    def __init__(self, session):
        pass

    def pay(self, supplier_id, method, source_id, amount, date, reference, notes, key, *, _expected_actor=None):
        CALLS.append(("pay", supplier_id, method, source_id, amount, key))
        return {"id": "9", "code": "SM000009", "duplicate": False}


class FakeSettlements:
    def __init__(self, session):
        pass

    def settle(self, supplier_id, karat_id, weight, date, reference, notes, key, *, _expected_actor=None):
        CALLS.append(("settle", supplier_id, karat_id, weight, key))
        return {"code": "SG000004", "duplicate": False}


class MarketTests(unittest.TestCase):
    def setUp(self):
        CALLS.clear()
        FakeTrips.fail = None
        for target, fake in (("services.market_trip_service.MarketTripService", FakeTrips), ("services.supplier_service.SupplierService", FakeSuppliers),
                             ("services.supplier_payment_service.SupplierPaymentService", FakePayments),
                             ("services.supplier_gold_settlement_service.SupplierGoldSettlementService", FakeSettlements)):
            patcher = mock.patch(target, fake)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.api = make_api(OWNER)
        self.token = self.api.login("admin", "secret")
        self.market = self.api.market

    def body(self, **values):
        base = {"op": "trip_create", "password": "secret", "confirmed": True, "request_key": KEY}
        base.update(values)
        return base

    def test_the_overview_shows_the_safe_scrap_open_custody_and_only_active_supplier_roles(self):
        data = self.market.overview(self.token)
        self.assertEqual((data["safe"]["text"], data["scrap"][0]["text"]), ("50,000.00", "100.500"))
        trip = data["trips"][0]
        self.assertEqual((trip["code"], trip["cashText"], trip["gold"][0]["text"]), ("MT000007", "12,000.00", "5.250"))
        self.assertEqual([s["name"] for s in data["suppliers"]], ["مورد أ"])                         # an intermediary-only party is not a supplier row
        self.assertEqual((data["suppliers"][0]["gold"], data["suppliers"][0]["makingSide"]), ("12.500 علينا للمورد", "300.00 لنا عند المورد"))

    def test_the_card_is_offered_only_with_the_trip_right(self):
        self.assertIn("market", [m["key"] for m in self.api.overview(self.token)["modules"]])
        plain = make_api(OWNER - {"MARKET_TRIP_MANAGE"})
        self.assertNotIn("market", [m["key"] for m in plain.overview(plain.login("admin", "secret"))["modules"]])

    def test_a_write_needs_the_password_the_confirmation_and_a_known_operation(self):
        with self.assertRaises(ApiError) as caught:
            self.market.op(self.token, self.body(password="wrong"))
        self.assertEqual(caught.exception.status, 400)
        with self.assertRaises(ApiError):
            self.market.op(self.token, self.body(confirmed=False))
        with self.assertRaises(ApiError):
            self.market.op(self.token, self.body(op="melt"))
        self.assertEqual(CALLS, [])

    def test_each_operation_reaches_its_service_with_the_client_key(self):
        self.market.op(self.token, self.body(cash="5000", gold=[{"karat_id": "1", "weight": "10.5"}, {"karat_id": "2", "weight": ""}], notes="n"))
        self.market.op(self.token, self.body(op="trip_return", trip_id="7", cash="100", gold=[{"karat_id": "1", "weight": "1", "designation": "UNCLASSIFIED"}]))
        self.market.op(self.token, self.body(op="trip_pay_supplier", trip_id="7", supplier_id="3", amount="900"))
        self.market.op(self.token, self.body(op="trip_expense", trip_id="7", amount="50", description="بوفيه"))
        self.market.op(self.token, self.body(op="trip_close", trip_id="7"))
        self.market.op(self.token, self.body(op="sup_pay_cash", supplier_id="3", amount="700"))
        self.market.op(self.token, self.body(op="sup_gold_settle", supplier_id="3", karat_id="1", weight="2.5"))
        self.assertEqual(CALLS, [
            ("create", "5000", [{"karat_id": "1", "weight": "10.5"}], [], "n", KEY),
            ("return", "7", "100", [{"karat_id": "1", "weight": "1", "designation": "UNCLASSIFIED"}], KEY),
            ("pay", "3", "CUSTODY", "7", "900", KEY), ("expense", "7", "50", "بوفيه", KEY), ("close", "7"),
            ("pay", "3", "CASH", "1", "700", KEY), ("settle", "3", "1", "2.5", KEY)])

    def test_a_service_refusal_is_shown_and_a_permission_error_is_403(self):
        FakeTrips.fail = ValueError("العهدة مغلقة")
        with self.assertRaises(ApiError) as caught:
            self.market.op(self.token, self.body(op="trip_close", trip_id="7"))
        self.assertEqual((caught.exception.status, str(caught.exception)), (400, "العهدة مغلقة"))
        FakeTrips.fail = PermissionError("عهدة السوق للمالك فقط.")
        with self.assertRaises(ApiError) as caught:
            self.market.op(self.token, self.body())
        self.assertEqual(caught.exception.status, 403)

    def test_too_many_or_malformed_gold_lines_are_refused(self):
        with self.assertRaises(ApiError):
            self.market.op(self.token, self.body(gold=[{"karat_id": "1", "weight": "1"}] * 9))
        with self.assertRaises(ApiError):
            self.market.op(self.token, self.body(gold="x"))


if __name__ == "__main__":
    unittest.main()
