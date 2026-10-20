"""The display-only custody aggregation of the Gold Position (`MarketTripRepository.custody_gold_by_trip` / `custody_lots_by_trip`) against the locking per-trip decision reads
(`custody_gold` / `custody_lots`), through the REAL repository code over a RECORDING connection with canned rows.

What this proves: how many statements are issued (a fixed number, not one per trip), that the display statements contain no locking read, that the decision statements
still do, which tables are touched (no cash, no document history), and that the shared Python assembly produces the same custody rows as the per-trip read for the
same data. What it does NOT prove: that the SQL is valid for MySQL, that GROUP BY / the reversal lookup return the right rows, performance, snapshot consistency or any
concurrency. Those need the small real-MySQL test described in REVIEW-STABILIZATION.md."""
from decimal import Decimal as D
import re
import unittest

from repositories.market_trip_repository import MarketTripRepository
from services import custody_rules
from services.market_trip_service import custody_view

D0 = D("0.000")
KARAT = {18: 2, 21: 1, 24: 3}

# canned aggregate rows (what each table's GROUP BY would return); every row names its trip
TABLES = {
    "market_trip_entry_gold": [
        {"trip_id": 1, "karat_id": 1, "karat_value": 21, "designation": "LOGO", "issued": D("10.000"), "returned": D("2.000")},
        {"trip_id": 2, "karat_id": 2, "karat_value": 18, "designation": "MELT", "issued": D("20.000"), "returned": D0},
        {"trip_id": 2, "karat_id": 3, "karat_value": 24, "designation": "LOGO", "issued": D("5.000"), "returned": D0},
        {"trip_id": 3, "karat_id": 1, "karat_value": 21, "designation": "UNCLASSIFIED", "issued": D("4.000"), "returned": D("4.000")}],
    "market_trip_melt_inputs": [{"trip_id": 2, "karat_id": 2, "karat_value": 18, "melted": D("20.000")}],
    "market_trip_heat_lines": [{"trip_id": 1, "karat_id": 1, "karat_value": 21, "heat_delta": D("-0.500")}],
    "market_trip_gold_settlements": [{"trip_id": 1, "karat_id": 1, "karat_value": 21, "designation": "LOGO", "supplier_settled": D("1.000")}],
    "market_trip_logo_hold_links": [{"trip_id": 1, "karat_id": 1, "karat_value": 21, "designation": "LOGO", "held_out": D("2.000"), "held_back": D("1.000")}],
    "market_trip_gold_sales": [{"trip_id": 1, "karat_id": 1, "karat_value": 21, "designation": "LOGO", "sold": D("1.000")}],
    "market_trip_gold_buy_lines": [{"trip_id": 1, "karat_id": 1, "karat_value": 21, "designation": "LOGO", "purchased": D("3.000")},
                                   {"trip_id": 3, "karat_id": 3, "karat_value": 24, "designation": "MELT", "purchased": D("1.500")}],
}
LOTS = [{"custody_trip_id": 2, "id": 7, "entry_id": 11, "lot_no": 1, "lot_kind": "MAIN", "label": "", "weight_grams": D("10.000"), "fineness_permille": D("875.0"),
         "pure_grams": D("8.750000"), "entry_no": 2, "origin_trip_id": 2, "outstanding_grams": D("10.000")},
        {"custody_trip_id": 3, "id": 9, "entry_id": 12, "lot_no": 2, "lot_kind": "RESIDUE", "label": "", "weight_grams": D("1.000"), "fineness_permille": None,
         "pure_grams": None, "entry_no": 1, "origin_trip_id": 2, "outstanding_grams": D("1.000")}]
GOLD_TABLE_ORDER = list(TABLES)


def table_of(sql):
    for name in (*TABLES, "market_trip_lot_movements", "market_trip_entry_cash", "market_trip_supplier_payments", "market_trip_gold_buys"):
        if re.search(rf"\b{name}\b", sql):
            return name
    return None


class Cursor:
    def __init__(self, connection):
        self.connection, self._rows = connection, []

    def execute(self, sql, params=()):
        sql = " ".join(sql.split())
        self.connection.statements.append(sql)
        table = table_of(sql)
        if table == "market_trip_lot_movements":
            rows = LOTS if "GROUP BY me.trip_id" in sql else [l for l in LOTS if l["custody_trip_id"] == int(params[0])]
            self._rows = [{**r, "id": r["id"]} for r in rows]
        elif table in TABLES:
            rows = TABLES[table]
            if params:                                                                    # the per-trip form: WHERE e.trip_id = %s
                rows = [r for r in rows if r["trip_id"] == int(params[0])]
            self._rows = [dict(r) for r in rows]
        else:
            self._rows = []

    def fetchall(self):
        return self._rows

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def close(self):
        pass


class Connection:
    def __init__(self):
        self.statements = []

    def cursor(self, dictionary=False):
        return Cursor(self)


def normalise(rows):
    keys = ("karat_id", "karat_value", "designation", "issued", "returned", "melted", "heat_delta", "supplier_settled", "held_out", "held_back", "sold", "purchased")
    return [{k: D(str(r.get(k, 0))) if k not in ("karat_id", "karat_value", "designation") else r.get(k) for k in keys} for r in rows]


class AggregationShapeTests(unittest.TestCase):
    def aggregate(self, connection=None):
        connection = connection or Connection()
        repo = MarketTripRepository(connection)
        repo._settlement = False                                                   # 060: the settlement reads have their own tests below
        repo._cash_settle = False                                                      # 064: the capability probe is not the subject of this test
        return connection, repo.custody_gold_by_trip(), repo.custody_lots_by_trip()

    def test_a_fixed_number_of_statements_whatever_the_number_of_trips(self):
        small, _, _ = self.aggregate()
        self.assertEqual(len(small.statements), 8)                               # seven gold terms + the lots: no per-trip loop
        many = Connection()
        saved = {k: list(v) for k, v in TABLES.items()}
        try:
            for table, rows in TABLES.items():                                    # 3000 trips' worth of rows: the number of statements must not move
                template = rows[0]
                rows.extend({**template, "trip_id": 100 + i} for i in range(3000))
            _, gold, _ = self.aggregate(many)
        finally:
            for table in TABLES:
                TABLES[table][:] = saved[table]
        self.assertEqual(len(many.statements), 8)
        self.assertGreater(len(gold), 3000)

    def test_the_display_statements_contain_no_locking_read_and_touch_no_cash_or_history(self):
        connection, _, _ = self.aggregate()
        for sql in connection.statements:
            self.assertNotIn("FOR SHARE", sql)
            self.assertNotIn("FOR UPDATE", sql)
            self.assertNotIn("LOCK IN SHARE MODE", sql)
            self.assertNotIn("WHERE e.trip_id = %s", sql)                          # no per-trip filter
            self.assertIn("transaction_corrections", sql)                           # a reversed entry is still excluded (the SNAPSHOT form of the lookup)
            self.assertIn("FULL_REVERSAL", sql)
            self.assertNotIn("market_trip_entry_cash", sql)
            self.assertNotIn("market_trip_supplier_payments", sql)
            self.assertNotIn("market_trip_gold_buys ", sql)                        # the purchase TOTAL (cash); only its gold lines are read
        tables = {table_of(sql) for sql in connection.statements}
        self.assertEqual(tables, set(TABLES) | {"market_trip_lot_movements"})

    def test_the_decision_reads_keep_their_locks(self):
        connection = Connection()
        repo = MarketTripRepository(connection)
        repo._settlement = False                                                   # 060: the settlement reads have their own tests below
        repo._cash_settle = False                                                      # 064: the capability probe is not the subject of this test
        repo.custody_gold(1, current=True)
        repo.custody_lots(2)
        self.assertEqual(len(connection.statements), 8)                           # custody_gold: 7 term queries (one trip) + custody_lots: 1
        gold = [s for s in connection.statements if table_of(s) in TABLES]
        self.assertEqual(len(gold), 7)
        for sql in connection.statements:
            self.assertIn("FOR SHARE", sql)                                       # every decision statement is a current / locking read
        self.assertTrue(all("FOR SHARE)" in s for s in gold))                     # ... including the nested reversal lookup when current=True


class SettlementReadsTests(unittest.TestCase):
    """060: the RAW pool (grams that left UNCLASSIFIED custody) and the BAR pool are read with the same discipline as every other custody term: display = ONE aggregate query
    each with NO lock and the snapshot reversal lookup; decision = locking reads; before 060 (no table) nothing is read at all."""

    def repo(self, present=True):
        connection = Connection()
        repo = MarketTripRepository(connection)
        repo._settlement = present
        return connection, repo

    def test_display_form_adds_one_unlocked_aggregate_per_pool(self):
        connection, repo = self.repo()
        repo.custody_gold_by_trip()
        repo.settle_bar_by_trip()
        settle = [s for s in connection.statements if "market_trip_settle_moves" in s]
        self.assertEqual(len(settle), 3)                                           # the RAW and the LOGO terms of every trip + the BAR pools of every trip, whatever the number of trips
        for sql in settle:
            self.assertNotIn("FOR SHARE", sql)
            self.assertNotIn("WHERE e.trip_id = %s", sql)
            self.assertIn("FULL_REVERSAL", sql)                                    # a reversed melt stops counting (the snapshot form of the lookup)
        self.assertEqual(len(connection.statements), 7 + 3)

    def test_decision_form_locks_the_rows_and_the_nested_lookup(self):
        connection, repo = self.repo()
        repo.custody_gold(1, current=True)
        repo.settle_bar_balance(1, current=True)
        settle = [s for s in connection.statements if "market_trip_settle_moves" in s]
        self.assertEqual(len(settle), 3)                                           # RAW, LOGO (the custody gold read) and the BAR balance
        for sql in settle:
            self.assertEqual(sql.count("FOR SHARE"), 2, sql)                       # the outer rows AND the nested reversal lookup

    def test_a_database_before_060_reads_nothing_of_the_settlement(self):
        connection, repo = self.repo(present=False)
        repo.custody_gold_by_trip()
        repo.custody_gold(1, current=True)
        self.assertEqual(repo.settle_bar_balance(1), 0)
        self.assertEqual(repo.settle_bar_by_trip(), {})
        self.assertFalse([s for s in connection.statements if "market_trip_settle_moves" in s])


class ArithmeticParityTests(unittest.TestCase):
    def setUp(self):
        connection = Connection()
        self.repo = MarketTripRepository(connection)
        self.aggregated = self.repo.custody_gold_by_trip()
        self.lots = self.repo.custody_lots_by_trip()

    def per_trip(self, trip):
        return MarketTripRepository(Connection()).custody_gold(trip)

    def test_every_trip_matches_the_per_trip_read(self):
        self.assertEqual(sorted(self.aggregated), [1, 2, 3])
        for trip in (1, 2, 3):
            self.assertEqual(normalise(self.aggregated[trip]), normalise(self.per_trip(trip)), f"trip {trip}")

    def test_the_custody_arithmetic_is_the_shared_definition(self):
        outstanding = {trip: {(g["karat_value"], g["designation"]): custody_rules.gold_outstanding(g) for g in rows} for trip, rows in self.aggregated.items()}
        # trip 1 LOGO 21K: issued 10 - returned 2 + heat(-0.5) - settled 1 - held out 2 + held back 1 - sold 1 + purchased 3
        self.assertEqual(outstanding[1][(21, "LOGO")], D("7.500"))
        self.assertEqual(outstanding[2][(18, "MELT")], D("0.000"))                 # melted in full
        self.assertEqual(outstanding[2][(24, "LOGO")], D("5.000"))                 # another karat of the same trip stays separate
        self.assertEqual(outstanding[3][(21, "UNCLASSIFIED")], D("0.000"))
        self.assertEqual(outstanding[3][(24, "MELT")], D("1.500"))                 # a purchase-only line (no issue row) is created by the assembly

    def test_the_view_the_position_builds_equals_the_one_of_a_custody_screen(self):
        for trip in (1, 2, 3):
            screen = custody_view(("0.00", "0.00"), self.per_trip(trip), MarketTripRepository(Connection()).custody_lots(trip), trip_id=trip)
            position = custody_view(("0.00", "0.00"), self.aggregated.get(trip, []), self.lots.get(trip, []), trip_id=trip)
            self.assertEqual([(g["karat_value"], g["designation"], g["outstanding"]) for g in position["gold"]],
                             [(g["karat_value"], g["designation"], g["outstanding"]) for g in screen["gold"]])
            self.assertEqual([(l["lot_id"], l["outstanding"], l["fineness"]) for l in position["lots"]],
                             [(l["lot_id"], l["outstanding"], l["fineness"]) for l in screen["lots"]])

    def test_lots_are_grouped_by_the_custody_that_holds_them_and_an_unassayed_residue_stays_unknown(self):
        self.assertEqual({trip: [l["id"] for l in rows] for trip, rows in self.lots.items()}, {2: [7], 3: [9]})
        residue = custody_view(("0", "0"), [], self.lots[3], trip_id=3)["lots"][0]
        self.assertIsNone(residue["fineness"])                                    # never inferred
        self.assertEqual(residue["origin_trip_id"], 2)                           # a reissued bar is listed in ITS custody, not its origin's


if __name__ == "__main__":
    unittest.main()
