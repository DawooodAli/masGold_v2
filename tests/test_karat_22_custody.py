"""22K in market-trip custody and the inventory/Scrap summaries: custody is per karat in physical grams (22K is its own line, never
merged into another karat or an Eq21 total), the entry dialog offers 22K only while its master row is active, and the existing
Eq21 helper treats 22K like any other karat. No melting, designation, allocation or market sale is involved."""
from decimal import Decimal as D
import unittest

from services.inventory_service import InventoryService
from tests.test_inventory_stock import K18, K21, K24, Store, piece, service_for
from tests.test_market_trips import K18 as M18, K21 as M21, TripCase

K22 = "4"                                        # the 22K master row of the fake (inactive by default)


class Karat22CustodyTests(TripCase):
    def activate_22k(self, scrap="30.000"):
        self.db.karats[4]["is_active"] = 1
        self.db.scrap.append({"id": 90, "karat_id": 4, "weight_delta": D(scrap), "reference_type": "OPENING", "reference_id": "0",
                              "type_code": "OPENING"})

    def test_the_entry_dialog_offers_22k_only_when_its_master_row_is_active(self):
        self.assertNotIn(22, [k["karat_value"] for k in self.service.context()["karats"]])
        self.activate_22k()
        context = self.service.context()
        self.assertIn(22, [k["karat_value"] for k in context["karats"]])
        self.assertIn(22, [r["karat_value"] for r in context["shop_scrap"]])
        self.assertEqual({r["karat_value"]: r["physical"] for r in context["shop_scrap"]}[22], D("30.000"))

    def test_an_inactive_22k_cannot_be_issued(self):
        self.db.scrap.append({"id": 90, "karat_id": 4, "weight_delta": D("30.000"), "reference_type": "OPENING", "reference_id": "0",
                              "type_code": "OPENING"})
        before = self.state()
        with self.assertRaises(Exception) as refused:
            self.issue("", [(K22, "5.000")])
        self.assertEqual(getattr(refused.exception, "code", None), "KARAT_NOT_FOUND")
        self.assertEqual(self.state(), before)

    def test_22k_issue_and_return_move_only_22k_counted_grams_and_stay_a_separate_custody_line(self):
        self.activate_22k()
        trip = self.issue("", [(K22, "12.500"), (M21, "10.000")])["trip_id"]
        outs = {m["karat_id"]: m for m in self.db.scrap if m["reference_type"] == "MARKET_TRIP_ENTRY"}
        self.assertEqual((outs[4]["weight_delta"], outs[4]["type_code"]), (D("-12.500"), "TRIP_CUSTODY_OUT"))
        self.assertEqual((self.db.physical(4), self.db.physical(1)), (D("17.500"), D("90.000")))
        lines = {g["karat_value"]: g["outstanding"] for g in self.custody()["gold"]}
        self.assertEqual(lines, {22: D("12.500"), 21: D("10.000")})            # per karat; no merged Eq21 total
        self.ret(trip, "", [(K22, "2.500")])
        self.assertEqual({g["karat_value"]: g["outstanding"] for g in self.custody()["gold"]}, {22: D("10.000"), 21: D("10.000")})
        self.assertEqual(self.db.physical(4), D("20.000"))
        self.refused("RETURN_EXCEEDS_CUSTODY", lambda: self.ret(trip, "", [(K22, "10.001")]))
        self.refused("RETURN_EXCEEDS_CUSTODY", lambda: self.ret(trip, "", [(M18, "0.001")]))      # another karat is not substitutable

    def test_22k_issue_is_limited_by_the_available_22k_scrap_only(self):
        self.activate_22k("5.000")
        self.refused("SCRAP_SHORTAGE", lambda: self.issue("", [(K22, "5.001")]))
        self.issue("", [(K22, "5.000")])
        self.assertEqual(self.db.available(4), D("0.000"))


K22_STOCK = (5, 22, 4)                           # (karat_id, value, sort_order): migration 038 appends 22K after 24K (sort_order 4)


class Karat22SummaryTests(unittest.TestCase):
    def test_the_stock_balance_lists_22k_as_its_own_karat_with_its_own_weight_and_the_equivalent(self):
        store = Store([piece(1, K18, "دبلة", "4.500"), piece(2, K21, "خاتم", "5.000"), piece(3, K22_STOCK, "خاتم", "10.000"),
                       piece(4, K24, "سبيكة", "10.000"), piece(5, K22_STOCK, "سلسلة", "2.250", status="SOLD")])
        balance = service_for(store).get_stock_balance()
        self.assertEqual([(k["karat_value"], k["weight_grams"], k["piece_count"]) for k in balance["karats"]],
                         [(21, D("5.000"), 1), (18, D("4.500"), 1), (24, D("10.000"), 1), (22, D("10.000"), 1)])   # master sort order; SOLD is not stock
        self.assertEqual((balance["weight_grams"], balance["equivalent_21_weight_grams"]), (D("29.500"), D("30.762")))


    def test_the_inventory_21k_equivalent_treats_22k_like_any_other_karat(self):
        self.assertEqual(InventoryService.equivalent_21([(22, "10.000")]), D("10.476"))          # 10 * 22 / 21
        self.assertEqual(InventoryService.equivalent_21([(18, "4.500"), (21, "5.000"), (22, "10.000"), (24, "10.000")]),
                         D("30.762"))                                                              # 3.857143 + 5 + 10.476190 + 11.428571
        self.assertEqual(InventoryService.equivalent_21([(21, "5.000")]), D("5.000"))              # unchanged for the old karats


if __name__ == "__main__":
    unittest.main()
