"""عهدة السوق والموردين on the iPad: the overview read and the whitelisted writes go through the EXISTING services, each needs the current password, the confirmation, the declared fields and the request key. Fakes only."""
from decimal import Decimal as D
from unittest import mock
import unittest

from tests.test_webapp import ApiError, OWNER, make_api
from webapp.market import OPERATIONS, SETTINGS_OPS

KEY = "11111111-1111-4111-8111-111111111111"
CALLS = []


class FakeTrips:
    fail = None

    def __init__(self, session):
        pass

    def context(self, *, _expected_actor=None):
        return {"safe": {"id": "1", "name": "الخزنة", "active": True, "balance": D("50000.00")}, "banks": [{"id": "5", "name": "بنك"}], "intermediaries": [{"id": "8", "name": "مسوقجي", "active": True}], "buyers": [{"id": "8", "name": "تاجر", "active": True}],
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

    review = {"token": "tok", "display": ["SG000004 • تحتاج إلى عكس"], "requires_physical_declaration": False}

    def party_holdings(self, party_id, *, _expected_actor=None):
        return {"pools": [{"pool_id": "40", "document": "IH000001", "balance": D("40.000"), "return_review_events": ["SG000004 • تحتاج إلى عكس"]}],
                "logo_pools": [{"pool_id": "50", "document": "IG000002", "karat_value": 21, "balance": D("8.000")}]}

    def return_review(self, pool_id, *, _expected_actor=None):
        return dict(FakeAny.review)

    def __getattr__(self, name):
        def run(*args, **kwargs):
            kwargs.pop("_expected_actor", None)
            CALLS.append((name,) + args + tuple(sorted(kwargs.items())))
            return {"code": "MH000003", "duplicate": False}
        return run


class FakePurchases:
    def __init__(self, session):
        pass

    def list_invoices(self, supplier_id=None, status=None, *, _expected_actor=None):
        base = {"supplier_name": "مورد أ", "invoice_date": "2026-11-01", "supplier_weight": D("100.000"), "gross_making": D("5000.00"), "piece_count": 0}
        return [{**base, "id": "31", "code": "SI000031", "status": "DRAFT", "reversed": False}, {**base, "id": "30", "code": "SI000030", "status": "POSTED", "reversed": False},
                {**base, "id": "29", "code": "SI000029", "status": "POSTED", "reversed": True}]

    def open_groups(self, *, _expected_actor=None):
        return [{"invoice_id": "30", "group_id": "300", "invoice_code": "SI000030", "supplier_name": "مورد أ", "group_no": 1, "remaining": D("60.000")}]

    def invoice_detail(self, invoice_id, *, _expected_actor=None):
        return {"groups": [{"id": "300", "group_no": 1, "effective_weight": D("100.000")}]}

    def __getattr__(self, name):
        def run(*args, **kwargs):
            kwargs.pop("_expected_actor", None)
            CALLS.append((name,) + args + tuple(sorted(kwargs.items())))
            return {"code": "SI000031", "duplicate": False}
        return run


class FakeInventory:
    def get_stocktake_filters(self):
        return {"item_types": [{"id": 4, "name": "خاتم"}]}


class FakeSettingsService:
    """One fake for the four settings services: reads return fixed rows, every write is recorded."""

    def __init__(self, session):
        pass

    def costs(self, *, _expected_actor=None):
        return [{"id": "1", "code": "A", "name": "ألف", "cost": D("300.00"), "range_min": D("100"), "range_max": D("200")}]

    def overview(self, *, _expected_actor=None):
        return {"types": [{"id": "4", "name": "خاتم"}], "karats": [{"id": "1", "value": 21}], "levels": [{"id": "9", "type": "خاتم", "karat": 21, "min_pieces": 5, "min_grams": None, "pieces": 2, "grams": D("10")}],
                "settings": [{"key": "price_warning_percent", "value": "10", "type": "DECIMAL", "type_text": "عدد عشري", "description": "د", "public": False}]}

    def categories(self, *, _expected_actor=None):
        return [{"code": "RENT", "name": "إيجار", "direction": "OUT", "direction_text": "مصروف", "active": True, "system": True, "used": True}]

    def __getattr__(self, name):
        def run(*args, **kwargs):
            kwargs.pop("_expected_actor", None)
            CALLS.append((name,) + args + tuple(sorted(kwargs.items())))
            return {"ok": True}
        return run


class FakeSuppliers:
    def __init__(self, session):
        pass

    def update_supplier(self, *args, **kwargs):
        kwargs.pop('_expected_actor', None)
        CALLS.append(('update_supplier',) + args + tuple(sorted(kwargs.items())))
        return {'id': '3'}

    def set_active(self, *args, **kwargs):
        kwargs.pop('_expected_actor', None)
        CALLS.append(('set_active',) + args)
        return {'id': '3'}

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
                             ("services.supplier_purchase_service.SupplierPurchaseService", FakePurchases), ("services.inventory_service.InventoryService", FakeInventory),
                             ("services.category_cost_service.CategoryCostService", FakeSettingsService), ("services.stock_min_level_service.StockMinLevelService", FakeSettingsService),
                             ("services.cash_category_service.CashCategoryService", FakeSettingsService), ("services.system_settings_service.SystemSettingsService", FakeSettingsService),
                             ("services.holding_settlement_service.HoldingSettlementService", FakeAny), ("services.marketer_custody_service.MarketerCustodyService", FakeAny), ("services.intermediary_holding_service.IntermediaryHoldingService", FakeAny)):
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
        self.assertEqual({o["key"] for o in data["ops"]}, set(OPERATIONS) - set(SETTINGS_OPS))             # the settings forms live on their own card
        known = set(data["options"]) | {"entries", "groups"}
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
        for bad in (self.body("trip_close", {"trip_id": "7"}, confirmed=False), self.body("melt"), self.body("trip_close", {}), self.body("trip_expense", {"trip_id": "7", "amount": "5"})):
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

    def test_supplier_invoices_are_listed_and_every_invoice_operation_reaches_its_service(self):
        data = self.market.overview(self.token)
        self.assertEqual([o["v"] for o in data["options"]["draft_invoices"]], ["31"])
        self.assertEqual([o["v"] for o in data["options"]["posted_invoices"]], ["30"])                 # a reversed invoice is not offered again
        self.assertEqual(data["options"]["intake_groups"][0]["v"], "30:300")
        self.assertEqual(data["options"]["item_types"], [{"v": "4", "t": "خاتم"}])
        self.assertEqual(self.market.groups(self.token, {"invoice": "30"})["groups"][0]["v"], "300")
        groups = [{"description": "دبل", "weight": "100", "making_per_gram": "50"}]
        self.run_op("inv_create", {"supplier_id": "3", "reference": "A1", "invoice_date": "2026-11-01"}, groups)
        self.run_op("inv_update", {"invoice_id": "31", "supplier_id": "3", "invoice_date": "2026-11-02"}, groups)
        self.run_op("inv_post", {"invoice_id": "31"})
        self.run_op("inv_cancel", {"invoice_id": "31"})
        self.run_op("inv_intake", {"group": "30:300", "item_type_id": "4", "weight": "10.1", "barcode_mode": "AUTO"})
        self.run_op("inv_close_group", {"group": "30:300"})
        self.run_op("inv_adjust_group", {"invoice_id": "30", "group_id": "300", "direction": "ADD", "weight": "1", "reason": "ميزان"})
        self.run_op("inv_reverse", {"invoice_id": "30", "reason": "خطأ"})
        self.run_op("sup_adjust_account", {"supplier_id": "3", "kind": "MAKING", "direction": "DEDUCT", "amount": "50", "reason": "فرق"})
        self.assertEqual([c[0] for c in CALLS], ["create_draft", "update_draft", "post_invoice", "cancel_draft", "add_piece", "close_group", "adjust_group", "reverse_invoice", "adjust_account"])
        self.assertEqual(CALLS[0][1:3], ("3", None))
        self.assertEqual(CALLS[4][1:3], ("30", "300"))                                                   # the combined picker value is split into invoice and group
        self.assertEqual(CALLS[5][1], "300")

    def test_marketer_holdings_returns_settlements_and_party_edits(self):
        data = self.market.overview(self.token)
        self.assertEqual([o["v"] for o in data["options"]["lot_pools"]], ["40"])
        self.assertIn("SG000004", data["options"]["lot_pools"][0]["t"])                                  # the recorded events are shown beside the lot
        self.assertEqual([o["v"] for o in data["options"]["logo_pools"]], ["50"])
        self.run_op("hold_return_lot", {"pool_id": "40", "intact": "1"})
        self.run_op("hold_return_logo", {"pool_id": "50", "weight": "3"})
        self.run_op("hold_settle_lot", {"pool_id": "40", "supplier_id": "3", "weight": "5", "declaration": "GRAM_ENTITLEMENT"})
        self.run_op("hold_settle_logo", {"pool_id": "50", "supplier_id": "3", "weight": "2"})
        self.run_op("sup_update", {"supplier_id": "3", "name": "مورد", "role": "SUPPLIER"})
        self.run_op("sup_active", {"supplier_id": "3", "state": "INACTIVE"})
        self.run_op("trip_create", {"lot_id": "99"})
        names = [c[0] for c in CALLS]
        self.assertEqual(names, ["return_holding", "return_logo", "settle", "settle", "update_supplier", "set_active", "create_trip"])
        self.assertEqual(CALLS[0][1:4], ("40", True, "tok"))                                              # the token comes from the service's own review
        self.assertEqual(CALLS[2][1:3], ("LOT", "40"))
        self.assertEqual(CALLS[3][1:3], ("LOGO", "50"))
        self.assertEqual(CALLS[6][3], [{"lot_id": "99"}])

    def test_a_lot_return_with_physical_events_needs_the_second_declaration(self):
        FakeAny.review = {"token": "tok", "display": ["HR000001 / RP000001 • معكوس"], "requires_physical_declaration": True}
        self.addCleanup(lambda: setattr(FakeAny, "review", {"token": "tok", "display": ["x"], "requires_physical_declaration": False}))
        with self.assertRaises(ApiError) as caught:
            self.run_op("hold_return_lot", {"pool_id": "40", "intact": "1"})
        self.assertIn("HR000001", str(caught.exception))
        self.assertEqual(CALLS, [])
        self.run_op("hold_return_lot", {"pool_id": "40", "intact": "1", "physical": "1"})
        self.assertEqual(CALLS[0][1:5], ("40", True, "tok", True))

    def test_the_settings_card_lists_the_business_settings_and_changes_them_through_their_services(self):
        data = self.market.settings_overview(self.token)
        self.assertEqual({t["title"] for t in data["tables"]}, {"تكلفة المصنعية للحروف (ج/جرام)", "الحد الأدنى للمخزون", "تصنيفات المصروفات والإيرادات", "إعدادات النظام"})
        self.assertEqual({o["key"] for o in data["ops"]}, set(SETTINGS_OPS))
        self.assertEqual(data["options"]["sys_keys"][0]["v"], "price_warning_percent")
        self.assertNotIn("market", [o["key"] for o in data["ops"]])
        self.run_op("cat_cost_set", {"category_id": "1", "amount": "350"})
        self.run_op("min_save", {"type_id": "4", "karat_id": "1", "min_pieces": "5"})
        self.run_op("cashcat_add", {"name": "نقل", "direction": "OUT"})
        self.run_op("cashcat_active", {"code": "RENT", "state": "INACTIVE"})
        self.run_op("sys_set", {"key": "price_warning_percent", "value": "12"})
        self.assertEqual([c[0] for c in CALLS], ["set_cost", "save", "add", "set_active", "set_value"])
        self.assertEqual(CALLS[1][1:5], ("4", "1", "5", None))
        self.assertEqual(CALLS[3][1:3], ("RENT", False))
        self.assertEqual(CALLS[4][1:6], ("price_warning_percent", "12", "DECIMAL", "د", False))
        self.assertIn(("password", "secret"), CALLS[4])                                                    # the service re-checks the user's own password itself

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
