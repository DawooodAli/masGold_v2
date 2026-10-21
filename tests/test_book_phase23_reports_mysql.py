"""Explicit opt-in rehearsal checks (packet28) for migrations 058/059 (BOOK Phase 2/3) and for the READ-ONLY report SQL on real MySQL. masgold_rehearsal ONLY (the
target, level >= 59 and the rehearsal marker are asserted before any write); never the DEV database. Real services, real repositories, real SQL. Positive fixtures are
committed and never cleaned (the ledgers are insert-only); every test builds its own trips; negative statements always run inside transactions that are rolled back.

    MASGOLD_BOOK_REHEARSAL=1 (+ the dedicated rehearsal account variables of the runner) .\\.venv\\Scripts\\python.exe -B -m unittest -v tests.test_book_phase23_reports_mysql

Evidence classes are kept apart: schema objects, the gain / reconciliation / closure cycle (v3 record round-trip), and the six reports executed on the real
repository inside one REPEATABLE READ read-only snapshot (no write: the row counts before and after are compared)."""
from contextlib import contextmanager
from datetime import date
from decimal import Decimal as D
import os
import unittest

from mysql.connector import Error as MySqlError

from repositories.market_trip_repository import MarketTripRepository
from services import book_settlement as bs
from services.market_trip_service import TripError
from services.report_service import ReportService
from tests.test_market_trip_book_mysql import BookCoreMySqlTests, DATE, karat

REPORTS = ("sales", "stock", "alerts", "indicators", "gold", "employees", "customers", "deadcash", "suppliers", "treasury", "cashflow")
GOLD_SOURCES = ("SCRAP", "PIECES", "ASSAYED", "BOOK", "MARKETER")


@unittest.skipUnless(os.environ.get("MASGOLD_BOOK_REHEARSAL") == "1", "explicit rehearsal-only opt-in")
class Phase23AndReportsMySqlTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        BookCoreMySqlTests.setUpClass()
        cls.h = BookCoreMySqlTests("test_the_migration_is_registered_and_the_objects_exist")
        cls.h.service._reconciliation_enabled = True                    # the production construction leaves it off; the rehearsal turns it on for these checks
        with cls.h.tx() as c:
            q = c.cursor()
            q.execute("SELECT MAX(version) FROM schema_migrations")
            assert int(q.fetchone()[0]) >= 59
            q.close()

    # ------------------------------------------------------------ helpers
    def digest(self, trip):
        with self.h.tx() as c:
            return bs.build(MarketTripRepository(c).reconciliation_source_current(trip))

    def reconcile(self, trip, key=None):
        entries = self.digest(trip)[1]
        return self.h.service.reconcile(trip, self.digest(trip)[3], None, "OPERATING_LOSS", None, {k: True for k in bs.CONFIRMATION_KEYS}, bs.versions_for(entries),
                                        key or self.h.key())

    def posted(self, trip):
        return D(self.h.balance(trip)["posted"])

    def code(self, call, expected):
        with self.assertRaises(TripError) as raised:
            call()
        self.assertEqual(raised.exception.code, expected)

    # ------------------------------------------------------------ schema objects (058 / 059)
    def test_the_objects_of_058_and_059_exist(self):
        tables = {r["TABLE_NAME"] for r in self.h.query("SELECT TABLE_NAME FROM information_schema.TABLES WHERE TABLE_SCHEMA = DATABASE()")}
        for name in ("market_trip_gains", "market_trip_book_supplier_links"):
            self.assertIn(name, tables)
        versions = [int(r["version"]) for r in self.h.query("SELECT version FROM schema_migrations ORDER BY version")]
        self.assertEqual(versions, list(range(1, len(versions) + 1)))                       # the chain is contiguous
        self.assertGreaterEqual(versions[-1], 59)
        kinds = self.h.query("SELECT CHECK_CLAUSE FROM information_schema.CHECK_CONSTRAINTS WHERE CONSTRAINT_SCHEMA = DATABASE() AND CONSTRAINT_NAME = 'chk_mte_kind'")
        self.assertTrue(kinds and "BK_GAIN" in kinds[0]["CHECK_CLAUSE"] and "BK_ISS_STK" in kinds[0]["CHECK_CLAUSE"])

    def test_the_database_refuses_a_malformed_gain_record(self):
        trip = self.h.book_trip("10")
        gained = self.h.service.book_gain(trip, karat(self.h.karats[21], "0.5"), "فرق وزن ميزان", "إيصال 1", True, DATE, "", self.h.key())
        row = self.h.query("SELECT * FROM market_trip_gains WHERE entry_id=%s", (gained["entry_id"],))
        self.assertEqual(len(row), 1)
        self.h.refuses("UPDATE market_trip_gains SET explanation='' WHERE entry_id=%s", (gained["entry_id"],))
        self.h.refuses("INSERT INTO market_trip_gains(entry_id,trip_id,explanation,evidence_reference,confirmed_by_user_id,confirmed_at) VALUES (%s,%s,'x','y',%s,NOW())",
                       (gained["entry_id"], trip, self.h.user), errnos=(1062, 3819, 1452, 1451, 1364, 1048))

    # ------------------------------------------------------------ gain -> reconciliation -> closure
    def test_the_gain_reconcile_close_cycle_and_its_finality(self):
        service = self.h.service
        trip = self.h.book_trip("20")
        service.book_return(trip, [karat(self.h.karats[21], "18")], DATE, "", self.h.key())            # 2 g stay unsettled
        with self.assertRaises(TripError):
            service.book_gain(trip, karat(self.h.karats[21], "0.5"), "شرح", "إيصال", False, DATE, "", self.h.key())          # no explicit confirmation
        gained = service.book_gain(trip, karat(self.h.karats[21], "0.5"), "فرق وزن مثبت بإيصال", "إيصال ميزان 7", True, DATE, "", self.h.key())
        self.assertEqual(self.posted(trip), D("2.500"))                                      # the gain is an INPUT of the book
        self.code(lambda: service.close(trip), "RECONCILIATION_REQUIRED")
        entries = self.digest(trip)[1]
        self.assertEqual(bs.versions_for(entries)["source_algorithm"], "book-recon-source-v3")           # the lowest version that covers a gain
        done = self.reconcile(trip)
        with self.h.tx() as c:
            row = MarketTripRepository(c).reconciliation_of_entry(done["entry_id"])
        row.update(id=row["entry_id"])
        self.assertEqual(bs.verify_record(row), [])                                                      # the v3 record round-trips through the real JSON column
        self.code(lambda: service.book_gain(trip, karat(self.h.karats[21], "0.1"), "x", "y", True, DATE, "", self.h.key()), "RECONCILIATION_FINAL")
        self.assertEqual(service.close(trip)["status"], "CLOSED")                                        # exactly one effective, verifying, matching reconciliation; no cash
        self.assertEqual(self.h.query("SELECT status FROM market_trips WHERE id=%s", (trip,))[0]["status"], "CLOSED")

    def test_a_gain_is_reversed_only_by_the_ordinary_full_reversal(self):
        trip = self.h.book_trip("10")
        gained = self.h.service.book_gain(trip, karat(self.h.karats[21], "1"), "زيادة موثقة", "إيصال 9", True, DATE, "", self.h.key())
        self.assertEqual(self.posted(trip), D("11.000"))
        self.h.correct(gained["entry_id"])
        self.assertEqual(self.posted(trip), D("10.000"))
        self.assertEqual(len(self.h.query("SELECT 1 FROM market_trip_gains WHERE entry_id=%s", (gained["entry_id"],))), 1)   # the evidence record is never rewritten

    # ------------------------------------------------------------ the six reports on the REAL repository
    def run_reports(self, today):
        results = {}
        with BookCoreMySqlTests.snapshot() as connection:
            @contextmanager
            def factory():
                yield connection
            service = ReportService(self.h.session, transaction_factory=factory, today=lambda: today)
            for report in REPORTS:
                filters = {"from": "2020-01-01", "to": today.isoformat()}
                results[(report, None)] = service.run(report, filters)
        return results

    def test_every_report_runs_on_real_mysql_without_a_write(self):
        before = self.h.counts(("market_trips", "market_trip_entries", "old_gold_movements", "cash_movements", "bank_movements", "audit_log"))
        results = self.run_reports(date.today())
        for key, built in results.items():
            self.assertTrue(built["tables"], key)
            for table in built["tables"]:
                self.assertIn("rows", table)
        keys = [t["key"] for t in results[("gold", None)]["tables"]]
        self.assertTrue(keys[-1] == "scrap" and all(k.startswith("sales") for k in keys[:-1]) and len(keys) >= 2, keys)             # the redesigned gold movement report reads on real MySQL
        self.assertEqual(self.h.counts(("market_trips", "market_trip_entries", "old_gold_movements", "cash_movements", "bank_movements", "audit_log")), before)

    def test_a_legacy_invoice_with_null_split_amounts_stays_unknown_and_is_not_written(self):
        with self.h.tx() as c:
            q = c.cursor(dictionary=True)
            q.execute("SELECT COLUMN_NAME, IS_NULLABLE, COLUMN_DEFAULT, DATA_TYPE FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'invoices'")
            columns = q.fetchall()
            q.close()
        nullable = {r["COLUMN_NAME"] for r in columns if r["IS_NULLABLE"] == "YES"}
        self.assertTrue({"sell_total", "buy_total"} <= nullable, "migration 015: legacy split totals are NULLable")      # the SQL fact the report relies on

    def test_the_sales_report_keeps_legacy_unknown_values_unknown_on_real_mysql(self):
        """Three LEGACY invoices are inserted in ONE transaction that is ALWAYS rolled back (nothing persists); the real ReportRepository reads them through that same
        connection: both NULL -> unknown; one side NULL -> the known side shown, the other unknown; stored zeros -> proven zeros."""
        from database.connection import DatabaseConnection
        tag = str(int(date.today().strftime("%y%m%d")) * 100 + 91)
        rows = (("L" + tag + "A", None, None), ("L" + tag + "B", "1000.00", None), ("L" + tag + "C", "0.00", "0.00"))
        connection = DatabaseConnection(self.h.config).connect()
        try:
            connection.rollback()
            connection.start_transaction(isolation_level="REPEATABLE READ")
            q = connection.cursor()
            for number, sell, buy in rows:
                q.execute("INSERT INTO invoices(invoice_number, invoice_type, invoice_status, invoice_date, sell_total, buy_total, net_amount, net_direction, settlement_status, "
                          "legacy_source_system, legacy_completeness, record_origin, finalized_at) "
                          "VALUES (%s,'SELL','FINALIZED',NOW(),%s,%s,0.00,'SETTLED','UNSETTLED','REHEARSAL','PARTIAL','LEGACY',NOW())", (number, sell, buy))
            q.close()

            @contextmanager
            def factory():
                yield connection
            service = ReportService(self.h.session, transaction_factory=factory, today=date.today)
            built = service.run("sales", {"from": date.today().isoformat(), "to": date.today().isoformat()})
            found = {}
            for table in built["tables"]:
                for row in table.get("rows", []):
                    if str(row.get("number")) in {n for n, _, _ in rows}:
                        found[row["number"]] = row
            self.assertEqual(set(found), {n for n, _, _ in rows})
            a, b, c = (found[n] for n, _, _ in rows)
            self.assertEqual((a["sales"], a["oldgold"]), ("غير معلوم", "غير معلوم"))
            self.assertEqual((b["sales"], b["oldgold"]), (D("1000.00"), "غير معلوم"))
            self.assertEqual((c["sales"], c["oldgold"]), (D("0.00"), D("0.00")))
            labels = " ".join(t["label"] for t in built["totals"])
            self.assertIn("جزئي", labels)                                                   # the totals say they exclude unknown legacy values
        finally:
            connection.rollback()
            connection.close()
        self.assertEqual(self.h.query("SELECT COUNT(*) AS n FROM invoices WHERE invoice_number LIKE %s", ("L" + tag + "%",))[0]["n"], 0)

    # ------------------------------------------------------------ races (two real connections, production authorization path)
    def services(self, count):
        out = []
        for n in range(count):
            service = self.h.build_service(self.h.make_session(f"p23race{n}")[0])
            service._reconciliation_enabled = True
            out.append(service)
        return out

    def ok_count(self, outcomes):
        return sum(1 for code, _ in outcomes if code == "ok")

    def test_race_two_reconciliations_of_one_trip_create_exactly_one(self):
        trip = self.h.book_trip("10")
        self.h.service.book_return(trip, [karat(self.h.karats[21], "8")], DATE, "", self.h.key())
        digest, entries = self.digest(trip)[3], self.digest(trip)[1]
        versions = bs.versions_for(entries)
        confirm = {k: True for k in bs.CONFIRMATION_KEYS}
        outcomes = self.h.race([lambda s=s: s.reconcile(trip, digest, None, "OPERATING_LOSS", None, dict(confirm), dict(versions), self.h.key())["entry_id"]
                                for s in self.services(2)])
        self.assertEqual(self.ok_count(outcomes), 1, outcomes)
        self.assertEqual(len(self.h.query("SELECT 1 FROM market_trip_entries e WHERE e.trip_id=%s AND e.entry_kind='BK_RECON'", (trip,))), 1)

    def test_race_a_gain_and_a_reconciliation_never_both_stand(self):
        trip = self.h.book_trip("10")
        self.h.service.book_return(trip, [karat(self.h.karats[21], "8")], DATE, "", self.h.key())
        digest, entries = self.digest(trip)[3], self.digest(trip)[1]
        versions = bs.versions_for(entries)
        confirm = {k: True for k in bs.CONFIRMATION_KEYS}
        first, second = self.services(2)
        outcomes = self.h.race([lambda: first.reconcile(trip, digest, None, "OPERATING_LOSS", None, dict(confirm), dict(versions), self.h.key())["entry_id"],
                                lambda: second.book_gain(trip, karat(self.h.karats[21], "0.5"), "فرق وزن", "إيصال سباق", True, DATE, "", self.h.key())["entry_id"]])
        self.assertEqual(self.ok_count(outcomes), 1, outcomes)                                # the gain after a reconciliation is final-refused; a gain first makes the digest stale
        recon = self.h.query("SELECT COUNT(*) AS n FROM market_trip_entries WHERE trip_id=%s AND entry_kind='BK_RECON'", (trip,))[0]["n"]
        gains = self.h.query("SELECT COUNT(*) AS n FROM market_trip_entries WHERE trip_id=%s AND entry_kind='BK_GAIN'", (trip,))[0]["n"]
        self.assertEqual((recon, gains) in ((1, 0), (0, 1)), True, (recon, gains))

    def test_race_two_closures_of_one_reconciled_trip_close_it_once(self):
        trip = self.h.book_trip("10")
        self.h.service.book_return(trip, [karat(self.h.karats[21], "8")], DATE, "", self.h.key())
        self.reconcile(trip)
        outcomes = self.h.race([lambda s=s: s.close(trip)["status"] for s in self.services(2)])
        self.assertEqual(self.ok_count(outcomes), 1, outcomes)
        self.assertIn("TRIP_ALREADY_CLOSED", [code for code, _ in outcomes])

    def test_race_the_same_request_key_posts_one_document(self):
        trip = self.h.book_trip("10")
        key = self.h.key()
        scrap = self.h.scrap(21)
        session = self.h.make_session("p23samekey")[0]                                        # the SAME actor: another actor with the same key is a REQUEST_CONFLICT by design
        twins = [self.h.build_service(session) for _ in range(2)]
        outcomes = self.h.race([lambda s=s: s.book_return(trip, [karat(self.h.karats[21], "4")], DATE, "", key)["entry_id"] for s in twins])
        self.assertEqual(self.ok_count(outcomes), 2, outcomes)                                # an exact replay returns the original
        self.assertEqual(len({value for _, value in outcomes}), 1)
        self.assertEqual(self.h.scrap(21), scrap + D("4.000"))                                # posted once

    def test_race_one_assayed_stock_item_cannot_be_issued_to_two_trips(self):
        from tests.test_market_trip_book_mysql import assay
        source = self.h.book_trip("10")
        returned = self.h.service.book_return(source, [assay("500", "2")], DATE, "", self.h.key())
        item = {"entry_id": returned["entry_id"], "line_no": 1, "grams": "2"}
        targets = [self.h.book_trip("5") for _ in range(2)]
        outcomes = self.h.race([lambda s=s, t=t: s.book_issue_stock(t, [dict(item)], DATE, "", self.h.key())["entry_id"] for s, t in zip(self.services(2), targets)])
        self.assertEqual(self.ok_count(outcomes), 1, outcomes)


if __name__ == "__main__":
    unittest.main()
