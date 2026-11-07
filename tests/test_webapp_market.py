"""عهدة السوق والموردين on the iPad: the overview read and the whitelisted writes go through the EXISTING services, each needs the current password, the confirmation, the declared fields and the request key. Fakes only."""
from decimal import Decimal as D
from unittest import mock
import unittest

from tests.test_webapp import ApiError, OWNER, make_api
from webapp.market import OPERATIONS

KEY = "11111111-1111-4111-8111-111111111111"
CALLS = []


class FakeTrips:
    fail = None

    def __init__(self, session):
        pass

    def context(self, *, _expected_actor=None):
        return {"safe": {"id": "1", "name": "الخزنة", "active": True, "balance": D("50000.00")}, "banks": [{"id": "5", "name": "بنك"}], "buyers": [{"id": "8", "name": "تاجر", "active": True}],
                "karats": [{"id": 1, "karat_value": 21, "name": "21"}, {"id": 2, "karat_value": 18, "name": "18"}],
                "shop_scrap": [{"karat_id": 1, "karat_value": 21, "available": D("100.500"), "physical": D("100.500"), "held": D("0")}]}

    def trip_page(self, status, page, size, *, _expected_actor=None):
        custody = {"cash": {"outstanding": D("12000.00")}, "gold": [{"karat_id": 1, "karat_value": 21, "designation": "UNCLASSIFIED", "outstanding": D("5.250")}],
                   "bar": D("0"), "lots": [{"lot_id": "21", "lot_no": 1, "outstanding": D("40.000"), "fineness": D("875")}], "settled": False}
        return {"rows": [{"id": "7", "code": "MT000007", "notes": "رحلة", "custody": custody, "external_holdings": []}], "has_more": False}

    def shop_lots(self, *, _expected_actor=None):
        return []

    def trip_detail(self, trip_id, *, _expected_actor=None):
        return {"entries": [{"id": "70", "code": "MT000007-1", "kind": "ISSUE", "date": "2026-11-01"}]}

    def __getattr__(self, name):
        def run(*args, **kwargs):
            kwargs.pop("_expected_actor", None)
            CALLS.append((name,) + args + tuple(sorted(kwargs.items())))
            if FakeTrips.fail:
                raise FakeTrips.fail
            return {"trip_code": "MT000007", "code": "MT000007-1", "duplicate": False, "entries": [1]}
        return run


class FakeAny:
    """Records any call (the marketer and holding services)."""

    def __init__(self, session):
        pass

    def __getattr__(self, name):
        def run(*args, **kwargs):
            kwargs.pop("_expected_actor", None)
            CALLS.append((name,) + args + tuple(sorted(kwargs.items())))
            return {"code": "MH000003", "duplicate": False}
        return run


class FakeSuppliers:
    def __init__(self, session):
        pass

    def create_supplier(self, *args, **kwargs):
        kwargs.pop("_expected_actor", None)
        CALLS.append(("create_supplier",) + args + tuple(sorted(kwargs.items())))
        return {"id": "11", "code": "S000011"}

    def list_suppliers(self, search, active_only, role, *, _expected_actor=None):
        return [{"id": "3", "name": "مورد أ", "karat_id": 1, "karat_value": 21, "is_active": True, "is_supplier": True, "gold_balance": D("12.500"), "making": D("-300.00")},
                {"id": "4", "name": "مسوقجي", "karat_id": None, "karat_value": None, "is_active": True, "is_supplier": False, "gold_balance": None, "making": D("0")}]


class FakePayments:
    def __init__(self, session):
        pass

    def pay(self, supplier_id, method, source_id, amount, date, reference, notes, key, *, _expected_actor=None):
        CALLS.append(("pay", supplier_id, method, source_id, amount, key))
        return {"id": "9", "code": "SM000009", "duplicate": False}

    def reverse(self, payment_id, reason, *, _expected_actor=None):
        CALLS.append(("sm_reverse", payment_id, reason))
        return {"code": "RV000001"}


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
                             ("services.supplier_gold_settlement_service.SupplierGoldSettlementService", FakeSettlements),
                             ("services.marketer_custody_service.MarketerCustodyService", FakeAny), ("services.intermediary_holding_service.IntermediaryHoldingService", FakeAny)):
            patcher = mock.patch(target, fake)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.api = make_api(OWNER)
        self.token = self.api.login("admin", "secret")
        self.market = self.api.market

    def body(self, op="trip_create", v=None, lines=None, **extra):
        base = {"op": op, "v": v or {}, "lines": lines or [], "password": "secret", "confirmed": True, "request_key": KEY}
        base.update(extra)
        return base

    def run_op(self, op, v=None, lines=None):
        return self.market.op(self.token, self.body(op, v, lines))

    def test_the_overview_shows_the_safe_scrap_open_custody_options_and_the_operation_specs(self):
        data = self.market.overview(self.token)
        self.assertEqual((data["safe"]["text"], data["scrap"][0]["text"]), ("50,000.00", "100.500"))
        self.assertEqual((data["trips"][0]["code"], data["trips"][0]["cashText"]), ("MT000007", "12,000.00"))
        self.assertEqual([s["name"] for s in data["suppliers"]], ["مورد أ"])                         # an intermediary-only party is not a supplier row
        self.assertEqual((data["suppliers"][0]["gold"], data["suppliers"][0]["makingSide"]), ("12.500 علينا للمورد", "300.00 لنا عند المورد"))
        self.assertEqual(data["options"]["lots"][0]["trip"], "7")
        self.assertEqual({o["key"] for o in data["ops"]}, set(OPERATIONS))
        known = set(data["options"]) | {"entries"}
        for op in data["ops"]:
            for field in op["fields"]:
                for f in (field["cols"] if field["type"] == "lines" else [field]):
                    if f["type"] == "select":
                        self.assertIn(f["options"], known, (op["key"], f["k"]))                         # every dropdown has a source the page can fill

    def test_entries_of_a_trip_are_listed_for_the_reversal_picker(self):
        self.assertEqual(self.market.entries(self.token, {"trip": "7"})["entries"][0]["v"], "70")

    def test_the_card_is_offered_only_with_the_trip_right(self):
        self.assertIn("market", [m["key"] for m in self.api.overview(self.token)["modules"]])
        plain = make_api(OWNER - {"MARKET_TRIP_MANAGE"})
        self.assertNotIn("market", [m["key"] for m in plain.overview(plain.login("admin", "secret"))["modules"]])

    def test_a_write_needs_the_password_the_confirmation_the_fields_and_a_known_operation(self):
        with self.assertRaises(ApiError) as caught:
            self.market.op(self.token, self.body("trip_close", {"trip_id": "7"}, password="wrong"))
        self.assertEqual(caught.exception.status, 400)
        for bad in (self.body("trip_close", {"trip_id": "7"}, confirmed=False), self.body("melt"), self.body("trip_close", {}), self.body("trip_create", {"cash": "5"})):
            with self.assertRaises(ApiError):
                self.market.op(self.token, bad)
        self.assertEqual(CALLS, [])

    def test_each_operation_reaches_its_service_with_the_client_key(self):
        self.run_op("trip_create", {"cash": "5000", "notes": "n"}, [{"karat_id": "1", "weight": "10.5"}, {}])
        self.run_op("trip_return", {"trip_id": "7", "cash": "100"}, [{"karat_id": "1", "weight": "1", "designation": "UNCLASSIFIED"}])
        self.run_op("trip_pay_supplier", {"trip_id": "7", "supplier_id": "3", "amount": "900"})
        self.run_op("trip_expense", {"trip_id": "7", "amount": "50", "description": "بوفيه"})
        self.run_op("trip_close", {"trip_id": "7"})
        self.run_op("sup_pay_cash", {"supplier_id": "3", "amount": "700"})
        self.run_op("sup_pay_bank", {"supplier_id": "3", "bank_id": "5", "amount": "10"})
        self.run_op("sup_gold_settle", {"supplier_id": "3", "karat_id": "1", "weight": "2.5"})
        self.run_op("trip_reverse", {"trip_id": "7", "entry_id": "70", "reason": "خطأ"})
        self.run_op("trip_heat", {"trip_id": "7", "workshop": "ورشة"}, [{"karat_id": "1", "input_weight": "5", "output_weight": "4.9"}])
        self.run_op("trip_sell", {"trip_id": "7", "source_kind": "CUSTODY_GOLD", "karat_id": "1", "designation": "MELT", "weight": "2", "price_mode": "TOTAL", "price": "9000", "buyer_name": "س"})
        self.run_op("trip_buy", {"trip_id": "7", "vendor_name": "ص", "ack": "1"}, [{"karat_id": "1", "weight": "3", "price": "1000"}])
        self.run_op("trip_lot_return", {"trip_id": "7", "lot_id": "21"})
        self.assertEqual([c[0] for c in CALLS], ["create_trip", "return_custody", "pay", "record_custody_expense", "close", "pay", "pay", "settle", "reverse", "heat", "sell_gold", "buy_gold", "return_lots"])
        self.assertEqual(CALLS[0][1:4], ("5000", [{"karat_id": "1", "weight": "10.5"}], []))
        self.assertEqual(CALLS[5][1:4], ("3", "CASH", "1"))
        self.assertEqual(CALLS[6][1:4], ("3", "BANK", "5"))
        self.assertEqual(CALLS[11][-2:], (("bank_account_id", None), ("payment_method", "CUSTODY")))
        self.assertTrue(all(KEY in c for c in CALLS if c[0] in ("pay", "settle")))

    def test_the_marketer_supplier_and_document_operations_reach_their_services(self):
        self.run_op("mk_cash_handover", {"party_id": "8", "amount": "1000"})
        self.run_op("mk_sale", {"party_id": "8", "weight": "2", "price_mode": "TOTAL", "price": "9000", "buyer_name": "س"})
        self.run_op("mk_reverse", {"doc": "MH000003", "reason": "خطأ"})
        self.run_op("sup_reverse_payment", {"doc": "SM000009", "reason": "خطأ"})
        self.run_op("hold_logo", {"trip_id": "7", "party_id": "8", "karat_id": "1", "weight": "3"})
        self.run_op("sup_create", {"name": "مورد جديد", "role": "BOTH", "karat_id": "1"})
        self.assertEqual([c[0] for c in CALLS], ["cash_handover", "sale", "reverse", "sm_reverse", "hand_over_logo", "create_supplier"])
        self.assertIn(("reverse", "3", "خطأ"), CALLS)                                        # MH000003 -> document 3
        self.assertIn(("sm_reverse", "9", "خطأ"), CALLS)
        self.assertTrue(any(c[0] == "create_supplier" and ("is_supplier", True) in c and ("is_intermediary", True) in c for c in CALLS))
        with self.assertRaises(ApiError):
            self.run_op("mk_reverse", {"doc": "MH", "reason": "x"})

    def test_a_service_refusal_is_shown_and_a_permission_error_is_403(self):
        FakeTrips.fail = ValueError("العهدة مغلقة")
        with self.assertRaises(ApiError) as caught:
            self.run_op("trip_close", {"trip_id": "7"})
        self.assertEqual((caught.exception.status, str(caught.exception)), (400, "العهدة مغلقة"))
        FakeTrips.fail = PermissionError("عهدة السوق للمالك فقط.")
        with self.assertRaises(ApiError) as caught:
            self.run_op("trip_close", {"trip_id": "7"})
        self.assertEqual(caught.exception.status, 403)

    def test_too_many_lines_and_a_bad_date_are_refused(self):
        with self.assertRaises(ApiError):
            self.run_op("trip_create", {}, [{"karat_id": "1", "weight": "1"}] * 13)
        with self.assertRaises(ApiError):
            self.run_op("trip_logo", {"trip_id": "7", "date": "x"})


if __name__ == "__main__":
    unittest.main()
