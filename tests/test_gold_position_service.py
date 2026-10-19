"""موضع الذهب: the read-only consolidated gold position (fake repositories, no database)."""
from contextlib import contextmanager
from decimal import Decimal as D
import unittest

from services.gold_position_service import (GoldPositionService, REQUIRED_PERMISSIONS, build_position, eq21_karat, eq21_lot)
from services.inventory_service import InventoryService

OWNER = set(REQUIRED_PERMISSIONS)


def scrap_row(karat, physical, held="0", available=None):
    return {"karat_id": karat, "karat_value": karat, "physical_grams": D(physical), "held_grams": D(held),
            "available_grams": D(available if available is not None else D(physical) - D(held))}


def custody(gold=(), lots=()):
    return {"cash": {}, "settled": False,
            "gold": [{"karat_id": k, "karat_value": k, "designation": d, "outstanding": D(w)} for k, d, w in gold],
            "lots": [{"lot_id": str(i), "kind": kind, "weight": D(w), "outstanding": D(w), "code": f"MT000001-{i}",
                      "fineness": None if f is None else D(f)} for i, kind, w, f in lots]}


def build(**kw):
    base = dict(scrap=[], shop_lots=[], receipt_stock=[], inventory=[], returned_pending=[], trips=[], pools=[], logo_pools=[])
    base.update(kw)
    return build_position(**base)


def pool(pool_id, lot_id, party, balance, fineness, weight=None, party_id=7):
    return {"pool_id": pool_id, "lot_id": lot_id, "party_id": party_id, "party_name": party, "balance": D(balance),
            "weight_grams": D(weight or balance), "fineness_permille": None if fineness is None else D(fineness), "lot_kind": "MAIN"}


class Math21K(unittest.TestCase):
    def test_master_karat_and_assay_conversion(self):
        step = D("0.000001")
        self.assertEqual(eq21_karat("10", 24).quantize(step), (D("10") * 24 / 21).quantize(step))
        self.assertEqual(eq21_karat("21", 21), D("21"))
        self.assertEqual(eq21_lot("10", D("750.0")).quantize(step), (D("10") * D("0.75") * 24 / 21).quantize(step))
        self.assertIsNone(eq21_lot("10", None))                                   # an unknown assay is never inferred

    def test_the_reading_matches_the_inventory_service_definition(self):
        position = build(inventory=[{"karat_value": 18, "weight_grams": "4.500"}, {"karat_value": 21, "weight_grams": "5.000"},
                                    {"karat_value": 24, "weight_grams": "10.000"}])
        expected = InventoryService.equivalent_21([(18, "4.500"), (21, "5.000"), (24, "10.000")])
        self.assertEqual(expected, D("20.286"))
        self.assertEqual(position["shop"]["parts"]["inventory"]["eq21"], expected)
        self.assertEqual(position["summary"]["total_eq21"], expected)


class Aggregation(unittest.TestCase):
    def test_every_location_and_the_total(self):
        position = build(
            scrap=[scrap_row(21, "10.000", held="2.000"), scrap_row(18, "6.300")],
            shop_lots=[{"id": 1, "trip_id": 1, "entry_no": 2, "lot_no": 1, "lot_kind": "MAIN", "label": "", "stock_grams": D("8.000"),
                        "fineness_permille": D("875.0"), "original_weight": D("8.000")}],
            inventory=[{"karat_value": 21, "weight_grams": "30.000"}],
            returned_pending=[{"karat_value": 21, "weight_grams": "1.500"}],
            trips=[{"id": 2, "code": "MT000002", "status": "OPEN",
                    "custody": custody(gold=[(21, "LOGO", "5.000"), (24, "MELT", "21.000")], lots=[(9, "MAIN", "7.000", "750.0")])}],
            pools=[pool(3, 11, "حسن", "4.000", "900.0")],
            logo_pools=[{"pool_id": 5, "party_id": 7, "party_name": "حسن", "karat_value": 18, "balance": D("2.100")}])
        s = position["summary"]
        shop = D("10") + D("6.3") * 18 / 21 + D("8") * D("0.875") * 24 / 21 + D("30") + D("1.5")
        custody_eq = D("5") + D("21") * 24 / 21 + D("7") * D("0.75") * 24 / 21
        inter = D("4") * D("0.9") * 24 / 21 + D("2.1") * 18 / 21
        self.assertEqual(s["shop_eq21"], shop.quantize(D("0.001")))
        self.assertEqual(s["custody_eq21"], custody_eq.quantize(D("0.001")))
        self.assertEqual(s["intermediary_eq21"], inter.quantize(D("0.001")))
        self.assertEqual(s["total_eq21"], (shop + custody_eq + inter).quantize(D("0.001")))
        self.assertEqual(s["unknown_grams"], D("0.000"))
        self.assertEqual(position["warnings"], [])
        # held scrap is part of the physical grams (never added twice)
        self.assertEqual(position["shop"]["scrap"][0]["physical"], D("10.000"))
        self.assertEqual(position["shop"]["scrap"][0]["held"], D("2.000"))

    def test_unknown_assay_is_listed_but_never_converted(self):
        position = build(receipt_stock=[{"piece_id": 1, "receipt_id": 1, "party_name": "حسن", "stock_grams": D("3.000")}],
                         pools=[pool(1, 5, "علي", "2.000", None)],
                         trips=[{"id": 1, "code": "MT000001", "status": "OPEN", "custody": custody(lots=[(6, "RESIDUE", "1.250", None)])}])
        s = position["summary"]
        self.assertEqual(s["total_eq21"], D("0.000"))
        self.assertEqual(s["unknown_grams"], D("6.250"))
        self.assertEqual((s["unknown_in_shop"], s["unknown_in_custody"], s["unknown_with_intermediary"]),
                         (D("3.000"), D("1.250"), D("2.000")))
        self.assertEqual(s["total_grams"], D("6.250"))

    def test_a_lot_in_two_places_is_a_warning_not_a_silent_sum(self):
        position = build(
            shop_lots=[{"id": 4, "trip_id": 1, "entry_no": 1, "lot_no": 1, "lot_kind": "MAIN", "label": "", "stock_grams": D("5.000"),
                        "fineness_permille": D("750.0"), "original_weight": D("5.000")}],
            trips=[{"id": 1, "code": "MT000001", "status": "OPEN", "custody": custody(lots=[(4, "MAIN", "5.000", "750.0")])}])
        self.assertEqual([w["code"] for w in position["warnings"]], ["LOT_IN_SEVERAL_PLACES"])
        self.assertEqual(sorted(position["warnings"][0]["places"]), ["CUSTODY:MT000001", "SHOP"])

    def test_empty_and_zero_rows_are_omitted(self):
        position = build(scrap=[scrap_row(21, "0")],
                         trips=[{"id": 1, "code": "MT000001", "status": "CLOSED", "custody": custody(gold=[(21, "MELT", "0")])}],
                         pools=[pool(1, 1, "حسن", "0", "750.0")])
        self.assertEqual(position["shop"]["scrap"], [])
        self.assertEqual(position["custody"]["trips"], [])
        self.assertEqual(position["intermediary"]["parties"], [])
        self.assertEqual(position["summary"]["total_eq21"], D("0.000"))

    # ------------------------------------------------------------ the marketer account (055): 21K physical grams, counted once
    def test_a_marketer_only_party_is_in_the_intermediary_location_and_the_total(self):
        position = build(marketer_gold=[{"party_id": 20, "party_name": "مسوقجي أول", "balance": D("10.000")}])
        (party,) = position["intermediary"]["parties"]
        self.assertEqual((party["party_name"], party["lots"], party["logo"], party["marketer"]), ("مسوقجي أول", [], [], {"grams": D("10.000"), "eq21": D("10.000")}))
        self.assertEqual((position["intermediary"]["grams"], position["intermediary"]["eq21"]), (D("10.000"), D("10.000")))
        self.assertEqual((position["summary"]["total_grams"], position["summary"]["total_eq21"], position["summary"]["unknown_grams"]), (D("10.000"), D("10.000"), D("0.000")))
        self.assertEqual(position["intermediary"]["parts"]["marketer"]["grams"], D("10.000"))
        self.assertEqual(position["intermediary"]["parts"]["holdings"]["grams"], D("0.000"))

    def test_a_mixed_party_counts_each_legacy_and_marketer_source_exactly_once(self):
        position = build(
            pools=[pool(1, 11, "مسوقجي ومورد", "8.000", "875.0", party_id=21)],
            logo_pools=[{"pool_id": 5, "party_id": 21, "party_name": "مسوقجي ومورد", "karat_value": 18, "balance": D("6.300")}],
            marketer_gold=[{"party_id": 21, "party_name": "مسوقجي ومورد", "balance": D("4.000")}])
        (party,) = position["intermediary"]["parties"]                          # ONE row for the party: three sources, none merged or repeated
        self.assertEqual((len(party["lots"]), len(party["logo"]), party["marketer"]["grams"]), (1, 1, D("4.000")))
        lot_eq, logo_eq = eq21_lot("8", D("875.0")), eq21_karat("6.3", 18)
        self.assertEqual(position["intermediary"]["grams"], D("18.300"))         # 8 + 6.3 + 4: each exactly once
        self.assertEqual(position["intermediary"]["eq21"], (lot_eq + logo_eq + D("4")).quantize(D("0.001")))
        self.assertEqual(position["intermediary"]["parts"]["holdings"]["grams"], D("14.300"))
        self.assertEqual(position["intermediary"]["parts"]["marketer"]["grams"], D("4.000"))

    def test_unknown_assay_legacy_gold_stays_unknown_beside_known_marketer_gold(self):
        position = build(receipt_stock=[{"piece_id": 1, "receipt_id": 1, "party_name": "مسوقجي أول", "stock_grams": D("3.000")}],
                         pools=[pool(1, 11, "مسوقجي أول", "2.000", None, party_id=20)],
                         marketer_gold=[{"party_id": 20, "party_name": "مسوقجي أول", "balance": D("10.000")}])
        summary = position["summary"]
        self.assertEqual(summary["unknown_grams"], D("5.000"))                   # the receipt piece (shop) + the unassayed pool (intermediary): never inferred
        self.assertEqual(summary["unknown_in_shop"], D("3.000"))
        self.assertEqual(summary["unknown_with_intermediary"], D("2.000"))
        self.assertEqual(summary["total_grams"], D("15.000"))
        self.assertEqual(summary["total_eq21"], D("10.000"))                     # only the KNOWN 21K grams: the unknown ones are not folded in
        self.assertEqual(summary["intermediary_eq21"], D("10.000"))

    def test_a_zero_balance_is_omitted_and_a_negative_one_is_a_warning_never_netted(self):
        position = build(marketer_gold=[{"party_id": 20, "party_name": "صفر", "balance": D("0.000")},
                                        {"party_id": 21, "party_name": "سالب", "balance": D("-1.000")},
                                        {"party_id": 22, "party_name": "موجب", "balance": D("2.000")}])
        self.assertEqual([p["party_name"] for p in position["intermediary"]["parties"]], ["موجب"])
        self.assertEqual(position["summary"]["total_grams"], D("2.000"))
        self.assertEqual([(w["code"], w["party_name"]) for w in position["warnings"]], [("MARKETER_GOLD_NEGATIVE", "سالب")])

    def test_marketer_cash_is_never_part_of_a_gold_total(self):
        position = build(marketer_gold=[{"party_id": 20, "party_name": "مسوقجي", "balance": D("1.000"), "cash": D("999999")}])
        self.assertEqual(position["summary"]["total_grams"], D("1.000"))
        self.assertNotIn("999999", repr(position))

    def test_a_supplier_figure_never_appears_in_the_result(self):
        position = build(scrap=[scrap_row(21, "1")])
        text = repr(position).lower()
        for word in ("supplier", "payable", "making", "مورد"):
            self.assertNotIn(word, text)


class Fake:
    connection = object()

    def __init__(self, **tables):
        self.__dict__.update(tables)
        self.calls, self.cash_reads, self.connections = [], 0, []
        self.trip_rows = tables.get("trip_rows", [{"id": 2, "status": "OPEN"}, {"id": 3, "status": "CLOSED"}])
        self.gold_trips = tables.get("gold_trips", {2: "5"})
        self.marketer_rows = tables.get("marketer_rows", [])

    def trips(self, limit=100):
        self.calls.append("trips")                                       # the paged list: a position must never be derived from it
        return self.trip_rows[-limit:]

    def all_trip_ids(self):
        self.calls.append("all_trip_ids")
        return list(self.trip_rows)

    def party_gold_balances(self, party_id=None):
        self.calls.append("party_gold_balances")
        return list(self.marketer_rows)

    def custody_cash(self, trip_id, exclude_entry_id=None, *, current=False):
        self.cash_reads += 1
        return (D("0"), D("0"), D("0"))

    def custody_gold(self, trip_id, exclude_entry_id=None, *, current=False):
        self.calls.append("custody_gold")                                # the LOCKING per-trip decision read: a position must never use it
        raise AssertionError("the gold position called the per-trip locking read custody_gold")

    def custody_lots(self, trip_id, exclude_entry_id=None, *, lot_id=None, current=True):
        self.calls.append("custody_lots")
        raise AssertionError("the gold position called the per-trip locking read custody_lots")

    def custody_gold_by_trip(self):
        self.calls.append("custody_gold_by_trip")
        return {trip: [{"karat_id": 21, "karat_value": 21, "designation": "LOGO", "issued": D(grams), "returned": D("0")}] for trip, grams in self.gold_trips.items()}

    def custody_lots_by_trip(self):
        self.calls.append("custody_lots_by_trip")
        return {}

    def book_groups_by_trip(self):                                       # 056: BOOK balances (aggregated, no locks)
        self.calls.append("book_groups_by_trip")
        return {}

    def effective_reconciliations_display(self):                         # 057: effective BOOK reconciliations (display, no locks)
        self.calls.append("effective_reconciliations_display")
        return []

    def assayed_stock_items(self):
        self.calls.append("assayed_stock_items")
        return []

    def shop_scrap(self):
        return [scrap_row(21, "10")]

    def shop_lots(self):
        return []

    def receipt_stock(self):
        return []

    def open_pools(self, **_):
        return []

    def logo_open_pools(self, **_):
        return []

    def get_stock_weight_by_karat(self):
        return [{"karat_value": 21, "weight_grams": D("4")}]

    def get_returned_pending_weight_by_karat(self):
        return []


class Session:
    def __init__(self, permissions):
        self.actor = type("Actor", (), {"permissions": set(permissions), "generation": 1})()

    def require_authenticated(self):
        return self.actor

    def current_actor(self):
        return self.actor

    def is_current(self, actor):
        return actor is self.actor


class Authorization:
    def __init__(self):
        self.checked = []

    def require_permission_read(self, connection, actor, code):
        self.checked.append(code)
        if code not in actor.permissions:
            raise PermissionError(code)


class ServiceTests(unittest.TestCase):
    def service(self, permissions=OWNER, marketer=(), trips=None, gold_trips=None):
        self.opened, self.rolled_back = 0, 0
        outer = self

        @contextmanager
        def transaction():
            outer.opened += 1
            try:
                yield Fake.connection
            finally:
                outer.rolled_back += 1
        extra = {}
        if trips is not None:
            extra["trip_rows"] = trips
        if gold_trips is not None:
            extra["gold_trips"] = gold_trips
        self.authorization, self.fake = Authorization(), Fake(marketer_rows=list(marketer), **extra)

        def repository(connection):
            self.fake.connections.append(connection)
            return self.fake
        return GoldPositionService(Session(permissions), transaction_factory=transaction, authorization=self.authorization,
                                   trip_repository=repository, holding_repository=repository, inventory_repository=repository,
                                   marketer_repository=repository)

    def test_one_transaction_all_rights_checked_in_it_and_the_totals(self):
        service = self.service()
        position = service.position()
        self.assertEqual((self.opened, self.rolled_back), (1, 1))
        self.assertEqual(self.authorization.checked, list(REQUIRED_PERMISSIONS))
        self.assertEqual(position["summary"]["shop_eq21"], D("14.000"))           # 10 scrap + 4 inventory
        self.assertEqual(position["summary"]["custody_eq21"], D("5.000"))
        self.assertEqual(position["summary"]["total_eq21"], D("19.000"))
        self.assertEqual([t["code"] for t in position["custody"]["trips"]], ["MT000002"])

    def test_the_marketer_ledger_is_read_in_the_same_transaction_as_every_other_source(self):
        service = self.service(marketer=[{"party_id": 20, "party_name": "مسوقجي أول", "balance": D("10.000")}])
        position = service.position()
        self.assertEqual(self.opened, 1)                                          # no separately opened service transaction
        self.assertEqual(self.fake.connections, [self.fake.connection] * len(self.fake.connections))   # every repository got the SAME connection
        self.assertEqual(position["summary"]["intermediary_eq21"], D("10.000"))
        self.assertEqual(position["summary"]["total_eq21"], D("29.000"))          # 10 scrap + 4 inventory + 5 custody + 10 marketer

    def test_an_older_trip_beyond_the_former_list_limit_is_still_in_the_total(self):
        service = self.service(trips=[{"id": i, "status": "CLOSED"} for i in range(1, 6001)] + [{"id": 6001, "status": "OPEN"}], gold_trips={1: "7"})
        position = service.position()
        self.assertEqual(position["custody"]["grams"], D("7.000"))                # trip 1 (the oldest of 6001) is NOT dropped
        self.assertEqual([t["code"] for t in position["custody"]["trips"]], ["MT000001"])
        self.assertNotIn("trips", self.fake.calls)                                 # the paged list of the custody screens is never the source of a total
        self.assertEqual((self.fake.calls.count("all_trip_ids"), self.fake.calls.count("custody_gold_by_trip"), self.fake.calls.count("custody_lots_by_trip")), (1, 1, 1))
        self.assertNotIn("custody_gold", self.fake.calls)                          # no per-trip query loop and no locking decision read
        self.assertNotIn("custody_lots", self.fake.calls)
        self.assertEqual(self.fake.cash_reads, 0)                                  # no cash and no document history is loaded to derive a gold total

    def test_a_missing_right_refuses_the_whole_read(self):
        for missing in REQUIRED_PERMISSIONS:
            service = self.service(OWNER - {missing})
            with self.assertRaises(PermissionError):
                service.position()
            self.assertEqual(self.opened, 0)                                   # refused before any read
            self.assertFalse(service.can_view())
        self.assertTrue(self.service().can_view())

    def test_a_right_revoked_in_the_database_is_caught_inside_the_transaction(self):
        service = self.service()
        self.authorization.require_permission_read = lambda c, a, code: (_ for _ in ()).throw(PermissionError(code))
        with self.assertRaises(PermissionError):
            service.position()
        self.assertEqual((self.opened, self.rolled_back), (1, 1))
        self.assertNotIn("all_trip_ids", self.fake.calls)                         # no source was read


if __name__ == "__main__":
    unittest.main()
