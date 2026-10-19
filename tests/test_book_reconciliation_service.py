"""R1 reconciliation preview through the REAL MarketTripBookService over the in-memory database of tests/market_trip_fakes.py, plus the repository's read statements
through a recording connection. NOT real-MySQL tests: what fakes show says nothing about the SQL semantics, the snapshot or the locks of the real engine (those
stay for the deferred rehearsal checks). Read-only: every test asserts that nothing is written."""
import ast
import copy
from decimal import Decimal as D
from pathlib import Path
import re
import unittest
from unittest.mock import patch

from mysql.connector import errors as mysql_errors

from repositories.market_trip_repository import MarketTripRepository
from services.market_trip_service import TripError
from tests.market_trip_fakes import AccountsRepo, HoldingRepo, SupplierPayRepo, TripRepo, build
from tests.test_market_trip_book import K18, K21, PRICES, TODAY, assay, karat, key

ROOT = Path(__file__).resolve().parents[1]
LOCKING = re.compile(r"FOR\s+(SHARE|UPDATE)|LOCK\s+IN\s+SHARE\s+MODE", re.IGNORECASE)
WRITES = ("entries", "book_lines", "scrap", "cash_moves", "events", "audit_rows", "corrections", "assayed_moves", "book_trades", "cash_legs", "trips")


class PreviewCase(unittest.TestCase):
    def setUp(self):
        self.db, self.session, self.authorization, self.service, self.corrections = build(book=True)
        self.service._price_reference = lambda: dict(PRICES)
        self.n = 0
        for target, value in (("repositories.market_trip_repository.MarketTripRepository", TripRepo),
                              ("repositories.supplier_payment_repository.SupplierPaymentRepository", SupplierPayRepo),
                              ("repositories.financial_account_repository.FinancialAccountRepository", AccountsRepo),
                              ("repositories.intermediary_holding_repository.IntermediaryHoldingRepository", HoldingRepo)):
            started = patch(target, value)
            started.start()
            self.addCleanup(started.stop)

    def k(self):
        self.n += 1
        return key(self.n + 400)

    def trip(self, weight="10", back="4"):
        trip = self.service.book_issue("", [karat(K21, weight)], TODAY, "", self.k())["trip_id"]
        if back:
            self.service.book_return(trip, [karat(K21, back)], TODAY, "", self.k())
        return trip

    def state(self):
        return {name: copy.deepcopy(getattr(self.db, name)) for name in WRITES}


class ServiceTests(PreviewCase):
    def test_the_preview_values_and_the_untouched_closure(self):
        trip = self.trip()
        view = self.service.reconciliation_preview(trip)
        self.assertEqual((view["a"], view["aState"], view["b"], view["proposedLoss"], view["roundingSettlement"]), ("6.000000", "positive", "6.000", "6.000", "0.000"))
        self.assertEqual(view["effect"], {"position": "-6.000", "loss_part": "-6.000", "rounding_part": "0.000"})
        self.assertTrue(view["complete"] and view["trustworthy"])
        self.assertEqual((view["entryCount"], view["lineCount"], view["status"]), (2, 2, "OPEN"))
        self.assertEqual(view["cashOutstanding"], "0.00")
        with self.assertRaises(TripError) as closing:                                         # a good preview never closes: closure needs the approved reconciliation (059 era)
            self.service.close(trip)
        self.assertEqual(closing.exception.code, "RECONCILIATION_REQUIRED")

    def test_nothing_is_written_and_no_lock_is_recorded(self):
        trip = self.trip()
        before, locks = self.state(), list(self.db.lock_log)
        for _ in range(3):
            self.service.reconciliation_preview(trip)
        self.assertEqual(self.state(), before)
        self.assertEqual(self.db.lock_log, locks)

    def test_the_preview_path_calls_no_posting_audit_or_correction(self):
        trip = self.trip()
        boom = AssertionError("a write path was called by the preview")
        with patch.object(TripRepo, "insert_entry", side_effect=boom), patch.object(TripRepo, "insert_book_line", side_effect=boom), \
                patch.object(TripRepo, "audit", side_effect=boom), patch.object(TripRepo, "insert_event", side_effect=boom), \
                patch.object(TripRepo, "post_scrap", side_effect=boom), patch.object(TripRepo, "set_status", side_effect=boom):
            self.assertTrue(self.service.reconciliation_preview(trip)["complete"])

    def test_every_read_comes_from_one_call_of_the_snapshot_read_and_has_no_list_limit(self):
        trip = self.service.book_issue("", [karat(K21, "90")], TODAY, "", self.k())["trip_id"]
        for _ in range(60):                                                                  # far more documents than any list on the screen shows
            self.service.book_return(trip, [karat(K21, "0.100")], TODAY, "", self.k())
        self.db.preview_reads = 0
        view = self.service.reconciliation_preview(trip)
        self.assertEqual(self.db.preview_reads, 1)
        self.assertEqual((view["entryCount"], view["lineCount"]), (61, 61))
        self.assertEqual(view["a"], "84.000000")

    def test_a_reversed_document_stops_counting_and_is_counted_as_reversed(self):
        trip = self.trip()
        entry = next(e for e in self.db.entries.values() if e["entry_kind"] == "BK_RETURN")
        self.service.reverse(entry["id"], "خطأ إدخال")
        view = self.service.reconciliation_preview(trip)
        self.assertEqual((view["a"], view["b"], view["reversedCount"], view["lineCount"]), ("10.000000", "10.000", 1, 1))

    def test_cash_entries_change_the_cash_figure_but_not_the_source_token(self):
        trip = self.trip()
        first = self.service.reconciliation_preview(trip)
        self.service.issue(trip, "1000", [], TODAY, "", self.k())
        second = self.service.reconciliation_preview(trip)
        self.assertEqual(second["sourceToken"], first["sourceToken"])
        self.assertEqual((first["cashOutstanding"], second["cashOutstanding"]), ("0.00", "1000.00"))
        self.service.book_return(trip, [karat(K21, "1")], TODAY, "", self.k())              # a gold document does change it
        self.assertNotEqual(self.service.reconciliation_preview(trip)["sourceToken"], first["sourceToken"])

    def test_authorization_and_session(self):
        trip = self.trip()
        _, _, _, no_rights, _ = build(permissions=())
        with self.assertRaises(PermissionError):
            no_rights.reconciliation_preview(trip)
        with self.assertRaises(PermissionError):
            self.service.reconciliation_preview(trip, _expected_actor=object())             # not the current actor
        with patch.object(self.session, "is_current", return_value=False):                  # the session ended while the answer was being built
            with self.assertRaises(PermissionError):
                self.service.reconciliation_preview(trip)

    def test_a_legacy_trip_and_a_missing_trip_are_refused(self):
        legacy = self.service.issue("", "500", [], TODAY, "", self.k())["trip_id"]
        with self.assertRaises(TripError) as refused:
            self.service.reconciliation_preview(legacy)
        self.assertEqual(refused.exception.code, "TRIP_MODE_MISMATCH")
        with self.assertRaises(TripError) as missing:
            self.service.reconciliation_preview("999")
        self.assertEqual(missing.exception.code, "TRIP_NOT_FOUND")

    def test_an_unreadable_part_gives_an_incomplete_preview_without_a_proposal(self):
        trip = self.trip()
        self.db.unreadable = {"lines"}
        view = self.service.reconciliation_preview(trip)
        self.assertFalse(view["complete"])
        self.assertFalse(view["trustworthy"])
        self.assertIsNone(view["proposedLoss"])
        self.assertIsNone(view["effect"])
        self.assertIn("SOURCE_LINES_UNREADABLE", [d["code"] for d in view["diagnostics"]])
        self.assertEqual(view["lineCount"], 0)                                               # the missing part is reported, not silently read as zero

    def test_corrupt_stored_data_is_diagnosed_not_raised(self):
        trip = self.trip()
        self.db.book_lines[0]["eq21_posted"] = D("9.999")
        view = self.service.reconciliation_preview(trip)
        self.assertIn("LINE_POSTED_MISMATCH", [d["code"] for d in view["diagnostics"]])
        self.assertFalse(view["trustworthy"])
        self.assertIsNone(view["proposedLoss"])

    def test_a_deleted_assayed_stock_record_and_an_unsupported_entry_are_seen(self):
        trip = self.service.book_issue("", [karat(K21, "10")], TODAY, "", self.k())["trip_id"]
        self.service.book_return(trip, [assay("866.7", "1.8")], TODAY, "", self.k())
        self.assertTrue(self.service.reconciliation_preview(trip)["trustworthy"])
        self.db.assayed_moves.clear()
        self.assertIn("STOCK_RECORD_MISSING", [d["code"] for d in self.service.reconciliation_preview(trip)["diagnostics"]])
        entry = next(e for e in self.db.entries.values())
        self.db.entries[999] = {**entry, "id": 999, "entry_no": 99, "entry_kind": "BK_NEXT_KIND", "request_key": "x"}
        self.assertIn("UNSUPPORTED_KIND", [d["code"] for d in self.service.reconciliation_preview(trip)["diagnostics"]])


class ZeroRow(dict):
    """A row whose every numeric column reads 0 (the custody sums), so the repository code after the statement runs."""

    def __missing__(self, key):
        return 0


class Recorder:
    """A connection whose cursor records every statement; `fail_on` makes the statement containing that text raise a database error."""

    def __init__(self, fail_on=None):
        self.statements, self.fail_on = [], fail_on

    def cursor(self, dictionary=False):
        outer = self

        class Cursor:
            lastrowid, rowcount = 1, 0

            def execute(self, sql, params=()):
                text = " ".join(sql.split())
                outer.statements.append(text)
                if outer.fail_on and outer.fail_on in text:
                    raise mysql_errors.OperationalError("simulated read failure")

            def fetchone(self):
                return ZeroRow()

            def fetchall(self):
                return []

            def close(self):
                pass
        return Cursor()


class QueryTests(unittest.TestCase):
    def test_the_preview_read_is_five_plain_snapshot_statements(self):
        connection = Recorder()
        source = MarketTripRepository(connection).reconciliation_source(5)
        self.assertEqual(len(connection.statements), 6)                                      # a fixed number (the 058 capability check + five), whatever the trip holds
        for sql in connection.statements:
            self.assertIsNone(LOCKING.search(sql), sql)                                      # outer queries, subqueries and the reversal lookup alike
        joined = " ".join(connection.statements)
        for table in ("market_trip_entries", "market_trip_book_lines", "old_gold_movements", "market_trip_book_trades", "shop_assayed_stock_movements", "transaction_corrections"):
            self.assertIn(table, joined)
        self.assertNotRegex(joined, r"\bLIMIT\b")                                            # no list limit can cut the data
        self.assertEqual(source["incomplete"], [])
        lines_sql = next(s for s in connection.statements if "market_trip_book_lines" in s and "FROM market_trip_book_lines" in s)
        self.assertIn("WHERE l.trip_id = %s", lines_sql)                                      # selected by the line's OWN trip, independently of the entries

    def test_a_failing_section_is_reported_not_read_as_empty(self):
        connection = Recorder(fail_on="FROM market_trip_book_trades")
        source = MarketTripRepository(connection).reconciliation_source(5)
        self.assertEqual([code for code, _ in source["incomplete"]], ["SOURCE_TRADES_UNREADABLE"])
        self.assertEqual(len(connection.statements), 6)                                      # the capability check and the other sections still ran

    def test_the_financial_decision_reads_still_lock(self):
        """The R1 work added a lock-free read; it did not turn the decision methods into non-locking ones."""
        for call in (lambda r: r.book_lines(5, current=True), lambda r: r.custody_cash(5), lambda r: r.custody_gold(5, current=True), lambda r: r.custody_lots(5)):
            connection = Recorder()
            call(MarketTripRepository(connection))
            self.assertTrue(connection.statements)
            for sql in connection.statements:
                self.assertEqual(len(re.findall(r"FOR SHARE", sql)), 2, sql)                 # outer rows + the nested current reversal lookup

    def test_the_service_preview_uses_only_reads(self):
        tree = ast.parse((ROOT / "services" / "market_trip_book_service.py").read_text(encoding="utf-8"))
        function = next(f for f in ast.walk(tree) if isinstance(f, ast.FunctionDef) and f.name == "reconciliation_preview")
        calls = [(n.func.attr, {k.arg: ast.unparse(k.value) for k in n.keywords}) for n in ast.walk(function) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)]
        names = {name for name, _ in calls}
        self.assertEqual(names & {"insert_entry", "insert_book_line", "audit", "insert_event", "set_status", "post_scrap", "lock_old_gold_balance", "require_permission_for_write",
                                  "require_permission"}, set())
        self.assertIn(("require_permission_read", {}), calls)
        self.assertIn(("custody_cash", {"current": "False"}), calls)                          # the DISPLAY form of the cash read
        self.assertEqual(sum(1 for name, _ in calls if name == "trip"), 1)
        self.assertFalse([k for name, k in calls if name == "trip" and k])                    # trip(..) without lock=True


if __name__ == "__main__":
    unittest.main()
