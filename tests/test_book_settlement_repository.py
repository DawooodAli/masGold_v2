"""R2-S1: the repository statements of the reconciliation settlement through a RECORDING connection. No database is contacted and no statement is executed: this proves WHICH
statements are issued (tables, aliases, locking clauses, parameters), never what MySQL does with them. The locking behaviour (gap locks, the effect of FOR SHARE ... OF on a
LEFT JOIN, deadlocks) is NOT claimed: it stays a concurrent-transaction test for the MySQL verification gate."""
import re
import unittest

from repositories.market_trip_repository import MarketTripRepository
from tests.test_book_reconciliation_service import Recorder, ZeroRow

SHARE = re.compile(r"FOR\s+SHARE", re.IGNORECASE)
UPDATE = re.compile(r"FOR\s+UPDATE", re.IGNORECASE)


class ParamRecorder(Recorder):
    def __init__(self, rows=None, **kw):
        super().__init__(**kw)
        self.params, self.rows = [], rows or {}

    def cursor(self, dictionary=False):
        outer = self
        inner = super().cursor(dictionary)

        class Cursor:
            lastrowid, rowcount = 1, 1

            def execute(self, sql, params=()):
                outer.params.append(tuple(params))
                inner.execute(sql, params)

            def fetchone(self):
                return inner.fetchone()

            def fetchall(self):
                return inner.fetchall()

            def close(self):
                pass
        return Cursor()


class DecisionReadTests(unittest.TestCase):
    def test_the_five_source_sections_are_locking_reads_in_a_fixed_order(self):
        connection = ParamRecorder()
        source = MarketTripRepository(connection).reconciliation_source_current(7)
        self.assertEqual(len(connection.statements), 5)                                      # a fixed number, whatever the trip holds
        self.assertEqual(source["incomplete"], [])
        self.assertEqual(set(source), {"trip_id", "entries", "lines", "scrap", "trades", "stock", "incomplete"})
        for sql in connection.statements:
            self.assertTrue(SHARE.search(sql), sql)                                           # every section is a CURRENT (locking) read
            self.assertFalse(UPDATE.search(sql), sql)
            self.assertNotRegex(sql, r"\bLIMIT\b")
            self.assertNotRegex(sql, r"(?i)^\s*(INSERT|UPDATE|DELETE)")
        self.assertEqual(connection.params, [(7,)] * 5)
        tables = [re.search(r"FROM (\w+)", sql).group(1) for sql in connection.statements]
        self.assertEqual(tables, ["market_trip_entries", "market_trip_book_lines", "old_gold_movements", "market_trip_book_trades", "shop_assayed_stock_movements"])

    def test_the_entries_read_locks_the_reversal_rows_too_and_carries_the_date_and_the_correction_type(self):
        connection = ParamRecorder()
        MarketTripRepository(connection).reconciliation_source_current(7)
        entries = connection.statements[0]
        self.assertIn("LEFT JOIN transaction_corrections c ON c.source_type = 'MARKET_TRIP_ENTRY' AND c.source_id = e.id AND c.correction_type = 'FULL_REVERSAL'", entries)
        self.assertIn("FOR SHARE OF e, c", entries)                                          # the correction rows (and the absence of one) are part of the decision read
        for field in ("e.entry_date", "c.correction_type AS correction_type", "c.id AS correction_id", "e.trip_mode"):
            self.assertIn(field, entries)
        self.assertIn("WHERE e.trip_id = %s ORDER BY e.id", entries)

    def test_the_other_sections_select_what_the_snapshot_needs(self):
        connection = ParamRecorder()
        MarketTripRepository(connection).reconciliation_source_current(7)
        lines, legs, trades, stock = connection.statements[1:]
        self.assertIn("WHERE l.trip_id = %s", lines)                                          # the line's OWN trip, independently of the entries (orphans stay visible)
        for field in ("l.eq21_analytical", "l.posting_difference", "l.signed_eq21_posted", "l.scrap_movement_id", "l.line_price", "l.trip_mode"):
            self.assertIn(field, lines)
        self.assertIn("m.reference_type = 'MARKET_TRIP_ENTRY'", legs)
        self.assertIn("'TRIP_CUSTODY_OUT', 'TRIP_CUSTODY_RETURN_IN'", legs)
        self.assertIn("FOR SHARE OF m", legs)
        self.assertIn("FOR SHARE OF t", trades)
        self.assertIn("SELECT s.id, s.item_entry_id", stock)                                  # the stock record identity the snapshot carries (R1 ordered by s.id without selecting it)
        self.assertIn("ORDER BY s.id FOR SHARE OF s", stock)
        self.assertIn("s.movement_type IN ('RECEIVE', 'RECEIVE_REVERSAL')", stock)

    def test_a_failing_decision_read_is_not_turned_into_an_empty_section(self):
        connection = ParamRecorder(fail_on="FROM market_trip_book_trades")
        with self.assertRaises(Exception) as raised:
            MarketTripRepository(connection).reconciliation_source_current(7)
        self.assertIn("simulated read failure", str(raised.exception))                      # it propagates: the transaction rolls back (a deadlock must never be swallowed)
        self.assertEqual(len(connection.statements), 4)                                      # and nothing is read after the failure

    def test_the_display_read_of_r1_is_untouched(self):
        connection = ParamRecorder()
        MarketTripRepository(connection).reconciliation_source(7)
        self.assertEqual(len(connection.statements), 5)
        for sql in connection.statements:
            self.assertIsNone(SHARE.search(sql), sql)                                         # still plain snapshot reads
            self.assertNotIn("market_trip_reconciliations", sql)
        self.assertNotIn("entry_date", " ".join(connection.statements))                       # its shape did not change

    def test_the_finality_read_touches_only_the_entry_and_correction_tables_and_locks(self):
        connection = ParamRecorder()
        MarketTripRepository(connection).effective_reconciliation_entries(7)
        sql, = connection.statements
        self.assertIn("e.entry_kind = 'BK_RECON'", sql)
        self.assertEqual(len(SHARE.findall(sql)), 2)                                          # the outer rows and the nested reversal lookup
        self.assertNotIn("market_trip_reconciliations", sql)
        self.assertNotIn("market_trip_book_lines", sql)

    def test_the_generation_read_locks_and_counts_reversed_documents(self):
        connection = ParamRecorder()
        generation = MarketTripRepository(connection).next_reconciliation_generation(7)
        sql, = connection.statements
        self.assertEqual(generation, 0)                                                       # ZeroRow reads 0 (the repository adds nothing of its own)
        self.assertIn("COALESCE(MAX(generation), 0) + 1", sql)
        self.assertIn("FROM market_trip_reconciliations WHERE trip_id = %s FOR SHARE", sql)
        self.assertNotIn("transaction_corrections", sql)                                      # a reversed reconciliation still holds its generation

    def test_the_lookup_reads_lock_and_are_keyed_by_the_entry(self):
        connection = ParamRecorder()
        repository = MarketTripRepository(connection)
        repository.reconciliation_of_entry(9)
        repository.entry_reversal_id(9)
        for sql in connection.statements:
            self.assertTrue(SHARE.search(sql), sql)
        self.assertEqual(connection.params, [(9,), (9,)])

    def test_the_insert_writes_every_column_once_and_only_into_the_new_table(self):
        connection = ParamRecorder()
        MarketTripRepository(connection).insert_reconciliation(
            entry_id=9, trip_id=7, generation=1, effective_date="d", date_floor="f", date_floor_source="LAST_GOLD_ENTRY", a_star_units=1, a_star=2, b_star=3, loss_posted=4,
            rounding_settlement=5, delta_units=6, rho_units=7, sum_abs_units=8, entry_count=9, line_count=10, reversed_count=11, source_digest="s", snapshot_digest="n",
            snapshot_text="{}", reason_code=None, note=None, user_id=12, confirmed_at="t", algorithm="a", source_algorithm="sa", snapshot_format="sf", unit_denominator=26250000)
        sql, = connection.statements
        params, = connection.params
        self.assertTrue(sql.startswith("INSERT INTO market_trip_reconciliations"))
        columns = [c.strip() for c in re.search(r"\((entry_id.*?confirmed_at)\)", sql).group(1).split(",")]
        self.assertEqual(len(columns), len(set(columns)))
        self.assertEqual(sql.count("%s") + sql.count("'BK_RECON'") + sql.count("'BOOK'"), len(columns))
        self.assertEqual(len(params), sql.count("%s"))
        self.assertEqual(len(columns), 30)
        self.assertNotIn("created_at", columns)                                               # the default stamps it


class SourceHygieneTests(unittest.TestCase):
    def test_only_the_reconciliation_methods_name_the_new_table_in_the_repository(self):
        from pathlib import Path
        text = (Path(__file__).resolve().parents[1] / "repositories" / "market_trip_repository.py").read_text(encoding="utf-8")
        owners = set()
        for match in re.finditer(r"\n    def (\w+)\(", text):
            end = text.find("\n    def ", match.end())
            if "market_trip_reconciliations" in text[match.start():end if end > 0 else len(text)]:
                owners.add(match.group(1))
        self.assertEqual(owners, {"next_reconciliation_generation", "insert_reconciliation", "reconciliation_of_entry"})

    def test_only_the_reconcile_transaction_calls_them_in_the_services(self):
        from pathlib import Path
        root = Path(__file__).resolve().parents[1]
        callers = {}
        for folder in ("services", "app"):
            for file in (root / folder).rglob("*.py"):
                text = file.read_text(encoding="utf-8")
                for name in ("next_reconciliation_generation", "insert_reconciliation", "reconciliation_of_entry", "reconciliation_source_current"):
                    if re.search(rf"\.{name}\(", text):
                        callers.setdefault(name, set()).add(file.relative_to(root).as_posix())
        self.assertEqual({k: sorted(v) for k, v in callers.items()}, {name: ["services/market_trip_book_service.py"] for name in
                         ("next_reconciliation_generation", "insert_reconciliation", "reconciliation_of_entry", "reconciliation_source_current")})


if __name__ == "__main__":
    unittest.main()
