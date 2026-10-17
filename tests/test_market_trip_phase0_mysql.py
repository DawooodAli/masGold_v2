"""Explicit opt-in rehearsal checks for Phase 0 of the market-trip screen (active / archive pages, newest-first history, close / reopen).
They connect to masgold_rehearsal ONLY (target database, schema level 55 and the rehearsal marker are asserted before any write) and never to DEV.
Real MarketTripRepository / MarketTripService, real SQL, REPEATABLE READ connections. Opt-in: MASGOLD_PHASE0_REHEARSAL=1.
Optional: MASGOLD_PHASE0_EVIDENCE=<directory> receives the EXPLAIN / measurement evidence as JSON (no credentials are ever written).

Fixtures: committed and never cleaned (the ledgers are insert-only); every test builds its own trips. The rehearsal database already holds thousands of
trips: the comparisons therefore use the WHOLE population of a status inside ONE consistent snapshot (an independent full ordered query against the pages
the production repository returns) instead of an isolated population. The extra look-ahead row of `trip_page` is a sentinel: only the first `page_size`
rows are displayed / concatenated. Nothing here changes production SQL, filtering or the schema."""
from contextlib import contextmanager
from dataclasses import replace
from decimal import Decimal as D
import json
import math
import os
import statistics
import time
import unittest
from uuid import uuid4

from app.session import AppSession
from config.database_config import DatabaseConfig
from database.connection import DatabaseConnection
from repositories.authentication_repository import AuthenticationRepository
from repositories.market_trip_repository import MarketTripRepository
from services.authentication_service import AuthenticationResult, _VERIFIED
from services.market_trip_service import MarketTripService, TRIP_PAGE_SIZE

TARGET = "masgold_rehearsal"
DATE = "2026-10-17"
ENABLED = os.environ.get("MASGOLD_PHASE0_REHEARSAL") == "1"
EVIDENCE_DIR = os.environ.get("MASGOLD_PHASE0_EVIDENCE", "")
ACCOUNTING_TABLES = ("cash_movements", "bank_movements", "old_gold_movements", "supplier_gold_movements", "supplier_making_movements",
                     "market_trip_entries", "market_trip_lot_movements", "market_trip_lot_shop_movements", "intermediary_holding_movements",
                     "inventory_items", "transaction_corrections")


def record(name, payload):
    if EVIDENCE_DIR:
        with open(os.path.join(EVIDENCE_DIR, name + ".json"), "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, default=str)


class Recorder:
    """Connection proxy that remembers every statement the production repository sends (to EXPLAIN the exact text)."""

    def __init__(self, connection):
        self._connection, self.statements = connection, []

    def cursor(self, *args, **kwargs):
        outer = self

        class Cursor:
            def __init__(self, cursor):
                self._cursor = cursor

            def execute(self, query, params=()):
                outer.statements.append((query, params))
                return self._cursor.execute(query, params)

            def __getattr__(self, name):
                return getattr(self._cursor, name)
        return Cursor(self._connection.cursor(*args, **kwargs))

    def __getattr__(self, name):
        return getattr(self._connection, name)


@unittest.skipUnless(ENABLED, "explicit rehearsal-only opt-in: MASGOLD_PHASE0_REHEARSAL=1")
class Phase0MySqlTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.config = replace(DatabaseConfig.from_env(), database=TARGET)
        with cls.tx() as c:
            q = c.cursor(dictionary=True)
            q.execute("SELECT DATABASE() AS db, CURRENT_USER() AS cu, MAX(version) AS v FROM schema_migrations")
            row = q.fetchone()
            assert row["db"] == TARGET and int(row["v"]) == 55, (row["db"], row["v"])
            q.execute("SELECT target_database FROM masgold_rehearsal_marker WHERE marker_key='MASGOLD_SCHEMA_REHEARSAL'")
            assert q.fetchone()["target_database"] == TARGET
            q.execute("SELECT id,karat_value FROM karats")
            cls.karats = {r["karat_value"]: r["id"] for r in q.fetchall()}
            q.execute("INSERT INTO employees(name) VALUES('phase0 rehearsal owner')")
            emp = q.lastrowid
            q.execute("INSERT INTO users(employee_id,username,password_hash,display_name) VALUES(%s,%s,'unusable-rehearsal-hash','phase0 owner')",
                      (emp, "rehearsal-phase0-" + uuid4().hex[:12]))
            cls.user = q.lastrowid
            q.execute("INSERT INTO user_roles(user_id,role_id) SELECT %s,id FROM roles WHERE code='OWNER'", (cls.user,))
            # the established harness gives the history fixtures their Scrap the same way (counted opening balance, test-owned)
            q.execute("INSERT INTO old_gold_movements(movement_type_id,karat_id,weight_delta_grams,created_by_user_id) "
                      "SELECT id,%s,2000,%s FROM old_gold_movement_types WHERE code='OPENING_BALANCE'", (cls.karats[21], cls.user))
            assert q.rowcount == 1
            auth = AuthenticationRepository(c)
            identity, permissions = auth.get_identity(cls.user), auth.effective_permissions(cls.user)
            cls.session = AppSession()
            cls.session._establish(AuthenticationResult(cls.user, emp, identity["display_name"], permissions, _VERIFIED), int(cls.session.generation))
            q.close()
        cls.service = MarketTripService(cls.session, transaction_factory=cls.tx, price_reference=lambda: {21: D("6000.0000")})

    @classmethod
    @contextmanager
    def tx(cls):
        assert cls.config.database == TARGET
        c = DatabaseConnection(cls.config).connect()
        try:
            q = c.cursor()
            q.execute("SELECT DATABASE()")
            assert q.fetchone() == (TARGET,)
            q.close()
            c.rollback()
            c.start_transaction(isolation_level="REPEATABLE READ")
            yield c
            c.commit()
        except BaseException:
            c.rollback()
            raise
        finally:
            c.close()

    @classmethod
    @contextmanager
    def snapshot(cls):
        """ONE consistent REPEATABLE READ snapshot (never committed): everything read inside it sees the same database state.
        Yields (connection, a real MarketTripService whose every transaction IS this connection)."""
        assert cls.config.database == TARGET
        c = DatabaseConnection(cls.config).connect()
        try:
            q = c.cursor()
            q.execute("SELECT DATABASE()")
            assert q.fetchone() == (TARGET,)
            q.close()
            c.rollback()
            c.start_transaction(consistent_snapshot=True, isolation_level="REPEATABLE READ")

            @contextmanager
            def shared():
                yield c
            yield c, MarketTripService(cls.session, transaction_factory=shared, price_reference=lambda: {21: D("6000.0000")})
        finally:
            c.rollback()
            c.close()

    # ------------------------------------------------------------ helpers
    def query(self, sql, params=()):
        with self.tx() as c:
            q = c.cursor(dictionary=True)
            q.execute(sql, params)
            rows = q.fetchall()
            q.close()
            return rows

    @staticmethod
    def rows(connection, sql, params=()):
        q = connection.cursor(dictionary=True)
        q.execute(sql, params)
        rows = q.fetchall()
        q.close()
        return rows

    def open_trip(self, cash="100"):
        return int(self.service.issue("", cash, [], DATE, "phase0 rehearsal", str(uuid4()))["trip_id"])

    def closed_trip(self):
        tid = self.open_trip("100")
        self.service.return_custody(str(tid), "100", [], DATE, "", str(uuid4()))
        self.service.close(str(tid))
        return tid

    def count(self, status):
        return self.query("SELECT COUNT(*) AS n FROM market_trips WHERE status=%s", (status,))[0]["n"]

    @staticmethod
    def order_column(status):
        return "status_changed_at" if status == "CLOSED" else "created_at"

    def walk(self, connection, status, page_size):
        """Every page through the production repository inside the snapshot: (displayed ids per page, raw row counts, expected has_more per page)."""
        repository = MarketTripRepository(connection)
        pages, raw, page = [], [], 1
        while True:
            rows = repository.trip_page(status, page, page_size)
            raw.append(len(rows))
            pages.append([int(r["id"]) for r in rows[:page_size]])               # the 51st row is a look-ahead sentinel, never displayed
            if len(rows) > page_size:
                nxt = repository.trip_page(status, page + 1, page_size)
                self.assertEqual(int(rows[page_size]["id"]), int(nxt[0]["id"]), "the sentinel is the first row of the next page")
            if len(rows) <= page_size:
                return pages, raw
            page += 1

    def independent(self, connection, status):
        """The full population of the status, ordered in PYTHON from raw (id, timestamp) rows (not by the SQL under test) and by a plain SQL ORDER BY."""
        column = self.order_column(status)
        raw = self.rows(connection, f"SELECT id, {column} AS ts FROM market_trips WHERE status=%s", (status,))
        by_python = [int(r["id"]) for r in sorted(raw, key=lambda r: (r["ts"], r["id"]), reverse=True)]
        by_sql = [int(r["id"]) for r in self.rows(connection, f"SELECT id FROM market_trips WHERE status=%s ORDER BY {column} DESC, id DESC", (status,))]
        self.assertEqual(by_python, by_sql)
        ties = len(raw) - len({r["ts"] for r in raw})
        return by_python, ties

    def check_status(self, status, page_size):
        with self.snapshot() as (connection, service):
            expected, ties = self.independent(connection, status)
            pages, raw = self.walk(connection, status, page_size)
            flat = [i for page in pages for i in page]
            self.assertEqual(len(flat), len(set(flat)), "a trip appears on two pages")
            self.assertEqual(flat, expected, "concatenated displayed pages differ from the independent ordered query")
            population = {int(r["id"]): r["status"] for r in self.rows(connection, "SELECT id, status FROM market_trips")}
            self.assertTrue(all(population[i] == status for i in flat), "another status leaked into the pages")
            total, size = len(expected), page_size
            self.assertEqual(len(pages), max(1, math.ceil(total / size)) if total else 1)
            self.assertTrue(all(len(p) == size for p in pages[:-1]))
            self.assertEqual(len(pages[-1]), total - size * (len(pages) - 1) if total else 0)
            # has_more through the SERVICE (same snapshot) must agree with the arithmetic on every checked page
            for number in sorted({1, 2, len(pages), len(pages) + 1}):
                answer = service.trip_page(status, number, size)
                self.assertEqual((answer["status"], answer["page"]), (status, number))
                shown = [int(r["id"]) for r in answer["rows"]]
                self.assertEqual(shown, pages[number - 1] if number <= len(pages) else [])
                self.assertEqual(answer["has_more"], number < len(pages), f"has_more wrong on page {number}")
                self.assertLessEqual(len(answer["rows"]), size)
            return {"status": status, "page_size": size, "population": total, "pages": len(pages), "raw_rows_per_page_tail": raw[-3:],
                    "last_page_rows": len(pages[-1]), "equal_timestamp_groups_extra_rows": ties}

    # ------------------------------------------------------------ 1. pagination and filtering
    def test_open_pages_match_the_independent_ordered_query_in_one_snapshot(self):
        for size in (50, 100):
            result = self.check_status("OPEN", size)
            self.assertGreater(result["population"], 100, "the rehearsal OPEN population must exceed 100 for this check to prove reachability")
            self.assertGreater(result["equal_timestamp_groups_extra_rows"], 0, "no equal created_at exists: the tie-break was not exercised")
            record(f"pages-open-{size}", result)

    def test_closed_pages_match_the_independent_ordered_query_in_one_snapshot(self):
        self.closed_trip()
        self.closed_trip()
        for size in (50, 100):
            result = self.check_status("CLOSED", size)
            record(f"pages-closed-{size}", result)

    def test_status_filter_is_applied_before_the_limit(self):
        """Test-owned CLOSED trips are the NEWEST rows by created_at; they must not occupy a page of OPEN, and OPEN page 1 must still be full."""
        for _ in range(3):
            self.closed_trip()
        newest_open = self.open_trip("100")
        with self.snapshot() as (connection, service):
            newest_rows = self.rows(connection, "SELECT id, status FROM market_trips ORDER BY created_at DESC, id DESC LIMIT 10")
            self.assertIn("CLOSED", [r["status"] for r in newest_rows], "no CLOSED trip among the newest rows: the check proves nothing")
            first = service.trip_page("OPEN", 1, 50)
            self.assertEqual(len(first["rows"]), 50)
            self.assertTrue(first["has_more"])
            self.assertTrue(all(r["status"] == "OPEN" for r in first["rows"]))
            self.assertEqual(int(first["rows"][0]["id"]), newest_open)
            closed = service.trip_page("CLOSED", 1, 50)
            self.assertTrue(all(r["status"] == "CLOSED" for r in closed["rows"]))
            record("filter-before-limit", {"newest_ten_statuses": [r["status"] for r in newest_rows], "open_page_rows": len(first["rows"])})

    def test_boundary_populations_50_51_100_101_on_the_closed_status(self):
        """Test-owned CLOSED trips raise the CLOSED population to exactly 50, 51, 100 and 101 (when the current population allows it: fixtures are never
        deleted); each population is walked with page sizes 50 and 100 inside one snapshot."""
        start = self.count("CLOSED")
        if start > 101:
            self.skipTest(f"the CLOSED population is already {start}: exact 50/51/100/101 cannot be built without deleting rows "
                          "(see test_every_exact_multiple_boundary_of_the_existing_populations for the no-write boundary check)")
        done, skipped = [], []
        for target in (50, 51, 100, 101):
            current = self.count("CLOSED")
            if current > target:
                skipped.append({"target": target, "population": current})
                continue
            for _ in range(target - current):
                self.closed_trip()
            self.assertEqual(self.count("CLOSED"), target)
            for size in (50, 100):
                result = self.check_status("CLOSED", size)
                total = result["population"]
                expected_pages = math.ceil(total / size)
                self.assertEqual((total, result["pages"]), (target, expected_pages))
                with self.snapshot() as (connection, service):
                    last = service.trip_page("CLOSED", expected_pages, size)
                    self.assertFalse(last["has_more"])
                    self.assertEqual(len(last["rows"]), total - size * (expected_pages - 1))
                    if total % size == 0:                                              # exactly full last page: NO phantom extra page
                        self.assertEqual(service.trip_page("CLOSED", expected_pages + 1, size)["rows"], [])
                        self.assertEqual(len(MarketTripRepository(connection).trip_page("CLOSED", expected_pages, size)), size)
                    elif expected_pages > 1:                                           # a penultimate page exists: it carries the sentinel
                        self.assertEqual(len(MarketTripRepository(connection).trip_page("CLOSED", expected_pages - 1, size)), size + 1)
                done.append({"target": target, "page_size": size, "pages": result["pages"], "last_page_rows": result["last_page_rows"]})
        record("boundaries-closed", {"exercised": done, "not_reachable": skipped})
        self.assertTrue(done)

    def test_every_exact_multiple_boundary_of_the_existing_populations(self):
        """No writes. For each status, every page size 1..100 that divides the population exactly (last page full) must report has_more False on that page
        and an empty next page; a size that leaves a remainder must report has_more True on the penultimate page."""
        out = {}
        for status in ("OPEN", "CLOSED"):
            with self.snapshot() as (connection, service):
                total = self.rows(connection, "SELECT COUNT(*) AS n FROM market_trips WHERE status=%s", (status,))[0]["n"]
                repository = MarketTripRepository(connection)
                exact, remainder = [], []
                for size in range(1, 101):
                    pages = math.ceil(total / size)
                    last = repository.trip_page(status, pages, size) if total else []
                    if total and total % size == 0:
                        exact.append(size)
                        self.assertEqual(len(last), size, f"{status} size {size}: a full last page must carry NO sentinel")
                        self.assertEqual(repository.trip_page(status, pages + 1, size), [])
                    elif total and pages > 1:
                        remainder.append(size)
                        self.assertEqual(len(repository.trip_page(status, pages - 1, size)), size + 1)
                        self.assertEqual(len(last), total % size)
                answer = service.trip_page(status, max(1, math.ceil(total / 100)), 100) if total else {"has_more": False}
                self.assertFalse(answer["has_more"])
                out[status] = {"population": total, "exact_multiple_sizes_checked": exact, "remainder_sizes_checked": len(remainder)}
        record("exact-multiples", out)
        self.assertTrue(out["OPEN"]["exact_multiple_sizes_checked"] or out["CLOSED"]["exact_multiple_sizes_checked"],
                        "no exact multiple exists in the current populations")

    # ------------------------------------------------------------ 2. history ordering
    def ensure_tie(self, name, build_one, tie_of, limit=25):
        """Build rows until two of them share the same second (natural equal timestamps); never edits a stored timestamp."""
        for _ in range(limit):
            ties = tie_of()
            if ties:
                return ties
            build_one()
        ties = tie_of()
        self.assertTrue(ties, f"no equal-timestamp {name} produced after {limit} rows")
        return ties

    def test_history_reads_are_newest_first_with_a_descending_id_tie_break_and_unchanged_references(self):
        service = self.service
        tid = str(int(service.issue("", "1000", [{"karat_id": self.karats[21], "weight": "300", "designation": "MELT"},
                                                  {"karat_id": self.karats[21], "weight": "100", "designation": "LOGO"}], DATE, "phase0 history",
                                    str(uuid4()))["trip_id"]))
        for _ in range(2):
            service.issue(tid, "10", [], DATE, "", str(uuid4()))

        def heat():
            service.heat(tid, [{"karat_id": self.karats[21], "input_weight": "2", "output_weight": "1.9"}], DATE, "ws", "", "", "", str(uuid4()))

        def melt():
            service.melt(tid, [{"karat_id": self.karats[21], "weight": "5"}], DATE, "ws", {"weight": "4.9", "fineness": "875"}, [], "assay", "", "", str(uuid4()))

        for _ in range(3):
            heat()
        for _ in range(3):
            melt()
        service.return_custody(tid, "5", [], DATE, "", str(uuid4()))

        def tie_groups(sql):
            def inner():
                return [g for g in self.query(sql, (tid,)) if g["n"] > 1]
            return inner

        entries_tie = tie_groups("SELECT occurred_at, COUNT(*) AS n FROM market_trip_entries WHERE trip_id=%s GROUP BY occurred_at")
        melts_tie = tie_groups("SELECT e.occurred_at, COUNT(*) AS n FROM market_trip_melts m JOIN market_trip_entries e ON e.id=m.entry_id "
                               "WHERE e.trip_id=%s GROUP BY e.occurred_at")
        heats_tie = tie_groups("SELECT e.occurred_at, COUNT(*) AS n FROM market_trip_heats h JOIN market_trip_entries e ON e.id=h.entry_id "
                               "WHERE e.trip_id=%s GROUP BY e.occurred_at")
        self.ensure_tie("entries", lambda: service.issue(tid, "1", [], DATE, "", str(uuid4())), entries_tie)
        self.ensure_tie("melts", melt, melts_tie)
        self.ensure_tie("heats", heat, heats_tie)
        # status events need a closable trip: its own cycles until two events share a second
        event_trip = self.open_trip("100")
        service.return_custody(str(event_trip), "100", [], DATE, "", str(uuid4()))

        def cycle():
            service.close(str(event_trip))
            service.reopen(str(event_trip), "phase0 rehearsal cycle")

        tid_events = str(event_trip)

        def events_tie_inner():
            return [g for g in self.query("SELECT occurred_at, COUNT(*) AS n FROM market_trip_status_events WHERE trip_id=%s GROUP BY occurred_at",
                                          (tid_events,)) if g["n"] > 1]
        self.ensure_tie("status events", cycle, events_tie_inner)

        with self.snapshot() as (connection, snap_service):
            repository = MarketTripRepository(connection)
            trip_id = int(tid)
            # ---- documents (entries)
            default = repository.entries(trip_id)
            newest = repository.entries(trip_id, newest_first=True)
            expected = [int(r["id"]) for r in self.rows(connection, "SELECT id FROM market_trip_entries WHERE trip_id=%s ORDER BY occurred_at DESC, id DESC", (trip_id,))]
            self.assertGreaterEqual(len(newest), 10)
            self.assertEqual([int(r["id"]) for r in newest], expected)
            self.assertEqual([int(r["entry_no"]) for r in default], sorted(int(r["entry_no"]) for r in default))          # the chronological default is unchanged
            self.assertEqual([int(r["id"]) for r in default], sorted(int(r["id"]) for r in default))
            self.assertEqual({int(r["id"]): int(r["entry_no"]) for r in default}, {int(r["id"]): int(r["entry_no"]) for r in newest})
            self.assertEqual(sorted(int(r["entry_no"]) for r in newest), list(range(1, len(newest) + 1)))               # references untouched, contiguous
            self.assertTrue(any(a["occurred_at"] == b["occurred_at"] for a, b in zip(newest, newest[1:])))
            for a, b in zip(newest, newest[1:]):
                self.assertTrue((a["occurred_at"], int(a["id"])) > (b["occurred_at"], int(b["id"])), "not strictly descending by (occurred_at, id)")
            # ---- melts and heats
            for name, table in (("melts", "market_trip_melts"), ("heats", "market_trip_heats")):
                default = getattr(repository, name)(trip_id)
                newest = getattr(repository, name)(trip_id, newest_first=True)
                expected = [int(r["id"]) for r in self.rows(
                    connection, f"SELECT e.id FROM {table} x JOIN market_trip_entries e ON e.id = x.entry_id WHERE e.trip_id=%s "
                                "ORDER BY e.occurred_at DESC, e.id DESC", (trip_id,))]
                self.assertGreaterEqual(len(newest), 3, name)
                self.assertEqual([int(r["entry_id"]) for r in newest], expected, name)
                self.assertEqual([int(r["entry_no"]) for r in default], sorted(int(r["entry_no"]) for r in default), name)
                self.assertEqual({int(r["entry_id"]): int(r["entry_no"]) for r in default}, {int(r["entry_id"]): int(r["entry_no"]) for r in newest}, name)
                self.assertTrue(any(a["occurred_at"] == b["occurred_at"] for a, b in zip(newest, newest[1:])), name)
            # ---- status events of the cycled trip
            default = repository.events(event_trip)
            newest = repository.events(event_trip, newest_first=True)
            expected = [int(r["id"]) for r in self.rows(connection, "SELECT id FROM market_trip_status_events WHERE trip_id=%s ORDER BY occurred_at DESC, id DESC", (event_trip,))]
            self.assertGreaterEqual(len(newest), 3)
            self.assertEqual([int(r["id"]) for r in newest], expected)
            self.assertEqual([int(r["id"]) for r in default], sorted(int(r["id"]) for r in default))                  # chronological default
            self.assertEqual([r["event_type"] for r in default][:2], ["OPENED", "CLOSED"])
            self.assertTrue(any(a["occurred_at"] == b["occurred_at"] for a, b in zip(newest, newest[1:])))
            # ---- the service detail uses the newest-first reads and keeps the references
            detail = snap_service.trip_detail(tid)
            self.assertEqual([int(e["id"]) for e in detail["entries"]], [int(r["id"]) for r in repository.entries(trip_id, newest_first=True)])
            self.assertEqual({e["code"] for e in detail["entries"]}, {f"MT{trip_id:06d}-{n}" for n in range(1, len(detail["entries"]) + 1)})
            self.assertEqual([int(m["entry_id"]) for m in detail["melts"]], [int(r["entry_id"]) for r in repository.melts(trip_id, newest_first=True)])
            self.assertEqual([int(h["entry_id"]) for h in detail["heats"]], [int(r["entry_id"]) for r in repository.heats(trip_id, newest_first=True)])
            record("history-order", {"trip": trip_id, "entries": len(detail["entries"]), "melts": len(detail["melts"]), "heats": len(detail["heats"]),
                                     "event_trip": event_trip, "events": len(newest)})

    # ------------------------------------------------------------ 3. close / reopen
    def tables(self, connection):
        return {t: self.rows(connection, f"SELECT COUNT(*) AS n FROM {t}")[0]["n"] for t in ACCOUNTING_TABLES}

    def balances(self, connection):
        cash = self.rows(connection, "SELECT COALESCE(SUM(amount_delta),0) AS s FROM cash_movements")[0]["s"]
        bank = self.rows(connection, "SELECT COALESCE(SUM(amount_delta),0) AS s FROM bank_movements")[0]["s"]
        scrap = self.rows(connection, "SELECT COALESCE(SUM(weight_delta_grams),0) AS s FROM old_gold_movements")[0]["s"]
        return {"cash": cash, "bank": bank, "scrap": scrap}

    def test_close_and_reopen_move_the_trip_between_the_lists_and_change_no_accounting(self):
        service = self.service
        trip = self.open_trip("100")
        service.issue(str(trip), "", [{"karat_id": self.karats[21], "weight": "5", "designation": "LOGO"}], DATE, "", str(uuid4()))
        service.return_custody(str(trip), "100", [{"karat_id": self.karats[21], "weight": "5", "designation": "LOGO"}], DATE, "", str(uuid4()))
        later = self.open_trip("100")                                                     # created AFTER `trip`: must stay above it in Active
        with self.snapshot() as (connection, snap):
            before_tables, before_money = self.tables(connection), self.balances(connection)
            before_custody = snap.trip_detail(str(trip))["custody"]
            active = [int(r["id"]) for r in snap.trip_page("OPEN", 1, 50)["rows"]]
            self.assertEqual(active[:2], [later, trip])
        started = self.query("SELECT NOW() AS now")[0]["now"].replace(microsecond=0)
        service.close(str(trip))
        closed_row = self.query("SELECT * FROM market_trips WHERE id=%s", (trip,))[0]
        self.assertEqual(closed_row["status"], "CLOSED")
        self.assertGreaterEqual(closed_row["status_changed_at"], started)
        events = self.query("SELECT event_type, occurred_at FROM market_trip_status_events WHERE trip_id=%s ORDER BY id", (trip,))
        self.assertEqual([e["event_type"] for e in events], ["OPENED", "CLOSED"])
        self.assertEqual(events[-1]["occurred_at"], closed_row["status_changed_at"])
        with self.snapshot() as (connection, snap):
            open_all = {int(r["id"]) for r in self.rows(connection, "SELECT id FROM market_trips WHERE status='OPEN'")}
            self.assertNotIn(trip, open_all)
            archive = snap.trip_page("CLOSED", 1, 50)
            self.assertEqual(int(archive["rows"][0]["id"]), trip)                         # newest closing time first
            self.assertEqual(archive["rows"][0]["status"], "CLOSED")
            self.assertEqual(archive["rows"][0]["status_changed_at"], closed_row["status_changed_at"])
            self.assertEqual(snap.trip_detail(str(trip))["trip"]["status"], "CLOSED")     # archived details stay readable
        service.reopen(str(trip), "phase0 rehearsal reopen")
        reopened = self.query("SELECT * FROM market_trips WHERE id=%s", (trip,))[0]
        self.assertEqual(reopened["status"], "OPEN")
        self.assertGreaterEqual(reopened["status_changed_at"], closed_row["status_changed_at"])
        self.assertEqual(reopened["created_at"], closed_row["created_at"])
        events = self.query("SELECT event_type, reason FROM market_trip_status_events WHERE trip_id=%s ORDER BY id", (trip,))
        self.assertEqual([e["event_type"] for e in events], ["OPENED", "CLOSED", "REOPENED"])
        self.assertEqual(events[-1]["reason"], "phase0 rehearsal reopen")
        with self.snapshot() as (connection, snap):
            archive_ids = {int(r["id"]) for r in self.rows(connection, "SELECT id FROM market_trips WHERE status='CLOSED'")}
            self.assertNotIn(trip, archive_ids)
            expected, _ = self.independent(connection, "OPEN")
            active = [int(r["id"]) for r in snap.trip_page("OPEN", 1, 50)["rows"]]
            self.assertEqual(active, expected[:50])
            self.assertEqual(active[:2], [later, trip], "a reopened trip keeps its created_at position, not its reopen time")
            after_tables, after_money = self.tables(connection), self.balances(connection)
            after_custody = snap.trip_detail(str(trip))["custody"]
        delta = {t: after_tables[t] - before_tables[t] for t in ACCOUNTING_TABLES}
        self.assertEqual(delta, {t: 0 for t in ACCOUNTING_TABLES}, "close / reopen wrote an accounting or custody row")
        self.assertEqual(before_money, after_money)
        self.assertEqual(before_custody, after_custody)
        record("close-reopen", {"trip": trip, "table_deltas": delta, "balances_unchanged": after_money == before_money,
                                "events": [e["event_type"] for e in events]})

    # ------------------------------------------------------------ 4. query plans
    def test_explain_of_the_open_and_closed_page_queries(self):
        out = {}
        with self.snapshot() as (connection, _):
            recorder = Recorder(connection)
            repository = MarketTripRepository(recorder)
            for status in ("OPEN", "CLOSED"):
                total = self.rows(connection, "SELECT COUNT(*) AS n FROM market_trips WHERE status=%s", (status,))[0]["n"]
                last = max(1, math.ceil(total / 50))
                entry = {"population": total, "table_rows": self.rows(connection, "SELECT COUNT(*) AS n FROM market_trips")[0]["n"], "pages": {}}
                for label, page in (("first", 1), ("last", last)):
                    recorder.statements.clear()
                    shown = repository.trip_page(status, page, 50)
                    sql, params = recorder.statements[-1]
                    explain = self.rows(connection, "EXPLAIN " + sql, params)
                    tree = self.rows(connection, "EXPLAIN FORMAT=TREE " + sql, params)
                    q = connection.cursor()
                    q.execute("EXPLAIN ANALYZE " + sql, params)
                    analyze = "\n".join(str(r[0]) for r in q.fetchall())
                    q.close()
                    times = []
                    for _ in range(5):
                        started = time.perf_counter()
                        repository.trip_page(status, page, 50)
                        times.append((time.perf_counter() - started) * 1000)
                    entry["pages"][label] = {"page": page, "rows_returned": len(shown), "params": list(params),
                                             "explain": explain, "tree": [list(t.values())[0] for t in tree], "analyze": analyze,
                                             "wall_ms_median_of_5": round(statistics.median(times), 2), "wall_ms_all": [round(t, 2) for t in times]}
                    self.assertTrue(explain)
                out[status] = entry
            indexes = self.rows(connection, "SHOW INDEX FROM market_trips")
            out["market_trips_indexes"] = [(r["Key_name"], r["Seq_in_index"], r["Column_name"]) for r in indexes]
            out["sql_text"] = recorder.statements[-1][0]
        record("explain", out)


if __name__ == "__main__":
    unittest.main()
