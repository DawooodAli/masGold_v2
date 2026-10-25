"""The REAL MarketTripRepository (its Python, not its SQL semantics) against a recording connection: the mode guard of the ONE entry-insert path, what the INSERT statements
carry for a BOOK and for a LEGACY trip, and the locking / reversal shape of the new reads. The statements are NOT executed on any database: the behaviour of the SQL is the
subject of the prepared rehearsal tests (tests/test_market_trip_book_mysql.py)."""
from decimal import Decimal as D
import re
import unittest

from repositories.market_trip_repository import MarketTripRepository
from services import book_rules as br
from services.trip_errors import TripError


class Actor:
    user_id, employee_id = 7, None


class Recorder:
    """A connection whose cursor records every statement and answers the few reads the repository makes before an insert."""

    def __init__(self, trip=None):
        self.trip, self.statements = trip, []

    def cursor(self, dictionary=False):
        outer = self

        class Cursor:
            lastrowid, rowcount = 99, 1

            def __init__(self):
                self.rows, self.generic = [], False

            def execute(self, sql, params=()):
                outer.statements.append((" ".join(sql.split()), tuple(params or ())))
                text = sql.lower()
                if "information_schema.tables" in text:
                    self.rows = [{"n": 1}]
                elif "marketer_documents" in text:
                    self.rows = [{"n": 0}]
                elif "market_trip_bar_fees" in text and " as fee" in text:
                    self.rows = [{"fee": 0}]
                elif "from market_trips" in text and "where id" in text:
                    self.rows = [] if outer.trip is None else [dict(outer.trip)]
                elif text.lstrip().startswith("select"):
                    self.rows, self.generic = [{"issued": 0, "returned": 0, "paid": 0, "proceeds": 0, "spent": 0, "grams": 0, "posted": 0, "n": 0}], True
                else:
                    self.rows = []

            def fetchone(self):
                return self.rows[0] if self.rows else None

            def fetchall(self):
                return [] if self.generic else list(self.rows)

            def close(self):
                pass
        return Cursor()

    def inserts(self, table):
        return [s for s in self.statements if s[0].upper().startswith(f"INSERT INTO {table.upper()} ")]


def entry(repo, kind):
    return repo.insert_entry(trip_id=5, entry_no=1, kind=kind, entry_date="2026-10-17", notes=None, key="k", digest="d", actor=Actor(), now="2026-10-17 10:00:00")


class ModeGuardTests(unittest.TestCase):
    def test_a_book_trip_refuses_every_legacy_only_kind_before_any_insert(self):
        connection = Recorder({"id": 5, "accounting_mode": "BOOK"})
        repo = MarketTripRepository(connection)
        for kind in br.LEGACY_ONLY_KINDS:
            with self.assertRaises(TripError) as refused:
                entry(repo, kind)
            self.assertEqual(refused.exception.code, "TRIP_MODE_MISMATCH", kind)
        self.assertEqual(connection.inserts("market_trip_entries"), [])

    def test_a_legacy_trip_refuses_every_book_kind_and_a_row_without_the_column_is_legacy(self):
        for trip in ({"id": 5, "accounting_mode": "LEGACY"}, {"id": 5}):
            connection = Recorder(trip)
            repo = MarketTripRepository(connection)
            for kind in br.BOOK_KINDS:
                with self.assertRaises(TripError) as refused:
                    entry(repo, kind)
                self.assertEqual(refused.exception.code, "TRIP_MODE_MISMATCH", kind)
            self.assertEqual(connection.inserts("market_trip_entries"), [])
            entry(repo, "ISSUE")                                                                      # a legacy entry is accepted
            (sql, params), = connection.inserts("market_trip_entries")
            self.assertNotIn("trip_mode", sql)                                                         # the statement of every normal entry is the one it always was

    def test_a_book_entry_carries_its_mode_and_the_shared_cash_kinds_work_in_both_modes(self):
        connection = Recorder({"id": 5, "accounting_mode": "BOOK"})
        repo = MarketTripRepository(connection)
        for kind in (*br.BOOK_KINDS, *br.SHARED_CASH_KINDS):
            entry(repo, kind)
        inserts = connection.inserts("market_trip_entries")
        self.assertEqual(len(inserts), len(br.BOOK_KINDS) + len(br.SHARED_CASH_KINDS))
        for sql, params in inserts:
            self.assertIn("trip_mode", sql)
            self.assertEqual(params[-1], "BOOK")

    def test_the_guard_runs_before_the_request_key_is_even_written(self):
        connection = Recorder({"id": 5, "accounting_mode": "BOOK"})
        with self.assertRaises(TripError):
            entry(MarketTripRepository(connection), "MELT")
        self.assertFalse([s for s in connection.statements if s[0].upper().startswith("INSERT")])


class TripInsertTests(unittest.TestCase):
    def test_a_normal_trip_statement_does_not_mention_the_mode_and_a_book_trip_states_it(self):
        legacy, book = Recorder(), Recorder()
        MarketTripRepository(legacy).insert_trip(notes=None, actor=Actor(), now="2026-10-17 10:00:00")
        MarketTripRepository(book).insert_trip(notes=None, actor=Actor(), now="2026-10-17 10:00:00", mode="BOOK")
        self.assertNotIn("accounting_mode", legacy.inserts("market_trips")[0][0])
        self.assertIn("'BOOK'", book.inserts("market_trips")[0][0])

    def test_the_lists_read_the_mode(self):
        connection = Recorder()
        repo = MarketTripRepository(connection)
        repo.trips()
        repo.trip_page("OPEN", 1, 50)
        for sql, _ in connection.statements:
            self.assertIn("t.accounting_mode", sql)


class ReadShapeTests(unittest.TestCase):
    def test_the_decision_read_of_the_book_lines_locks_and_looks_up_reversals_currently(self):
        connection = Recorder()
        MarketTripRepository(connection).book_lines(5, current=True)
        sql, params = connection.statements[-1]
        self.assertIn("FOR SHARE OF e, l", sql)
        self.assertIn("transaction_corrections", sql)
        self.assertIn("FOR SHARE)", sql)                                                              # the nested reversal lookup is a CURRENT read under the trip lock
        self.assertEqual(len(re.findall(r"FOR SHARE", sql)), 2)                                       # exactly: the outer rows and the nested lookup

    def test_the_display_read_of_the_book_lines_is_a_snapshot_read_throughout(self):
        connection = Recorder()
        MarketTripRepository(connection).book_lines(5)
        MarketTripRepository(connection).book_lines(5, exclude_entry_id=9, current=False)
        for sql, _ in connection.statements:
            self.assertNotRegex(sql, r"FOR (SHARE|UPDATE)|LOCK IN SHARE MODE")                        # no locking clause anywhere, nested lookup included
            self.assertIn("transaction_corrections", sql)                                             # the reversal lookup is still there, as a plain snapshot read
        decision, display = (MarketTripRepository(Recorder()), MarketTripRepository(Recorder()))
        decision.connection.statements.clear()
        decision.book_lines(5, current=True)
        display.book_lines(5)
        stripped = re.sub(r" FOR SHARE( OF e, l)?", "", decision.connection.statements[0][0])
        self.assertEqual(stripped, display.connection.statements[0][0])                               # the two forms differ ONLY by their locks

    def test_every_display_read_of_a_book_screen_takes_no_lock(self):
        """What `_book_summary` / `_book_view` / the position call: book lines (display form), stock items, groups, entry details."""
        connection = Recorder()
        repo = MarketTripRepository(connection)
        repo.book_lines(5)
        repo.assayed_stock_items()
        repo.book_groups_by_trip()
        repo.entry_book_lines(7)
        repo.entry_book_trade(7)
        repo.entry_stock_items(7)
        for sql, _ in connection.statements:
            self.assertNotRegex(sql, r"FOR (SHARE|UPDATE)", sql)

    def test_the_display_aggregations_take_no_locks_and_group_by_trip(self):
        connection = Recorder()
        repo = MarketTripRepository(connection)
        repo.book_groups_by_trip()
        repo.assayed_stock_items()
        for sql, _ in connection.statements:
            self.assertNotIn("FOR SHARE", sql)
            self.assertNotIn("FOR UPDATE", sql)
        self.assertIn("GROUP BY e.trip_id, l.direction, l.basis, l.karat_value, l.fineness_permille", connection.statements[0][0])

    def test_the_custody_cash_includes_the_book_trades_in_the_two_existing_terms(self):
        connection = Recorder()
        repository = MarketTripRepository(connection)
        repository._cash_settle = False                                                      # 064: the capability probe is not the subject of this test
        repository.custody_cash(5, current=True)
        trades = [s for s in connection.statements if "market_trip_book_trades" in s[0]]
        self.assertEqual(len(trades), 1)
        self.assertIn("IF(t.entry_kind = 'BK_SELL', t.total_price, 0)", trades[0][0])
        self.assertIn("IF(t.entry_kind = 'BK_BUY', t.total_price, 0)", trades[0][0])

    def test_the_stock_item_lock_is_taken_first_and_only_on_request(self):
        connection = Recorder()
        repo = MarketTripRepository(connection)
        repo.stock_item(1, 1)
        repo.stock_item(1, 1, lock=True)
        self.assertNotIn("FOR UPDATE", connection.statements[0][0])
        self.assertIn("FOR UPDATE OF l", connection.statements[1][0])
        repo.stock_balance(1, 1)
        self.assertIn("FOR SHARE", connection.statements[-1][0])

    def test_stock_movements_are_insert_only_in_the_repository(self):
        import inspect
        import repositories.market_trip_repository as module
        source = inspect.getsource(module)
        self.assertNotIn("UPDATE shop_assayed_stock_movements", source)
        self.assertNotIn("DELETE FROM shop_assayed_stock_movements", source)
        self.assertNotIn("UPDATE market_trip_book_lines", source)
        self.assertNotIn("DELETE FROM market_trip_book_lines", source)
        self.assertEqual(D("1"), D("1"))


if __name__ == "__main__":
    unittest.main()
