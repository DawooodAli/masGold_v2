"""Explicit opt-in rehearsal checks for the LEGACY custody read paths changed by the display-read repair (no migration). They connect to masgold_rehearsal ONLY (the
target, a migration level of at least 56 and the rehearsal marker are asserted before any write) and never to the DEV database. Real services, real repositories, real SQL,
REPEATABLE READ connections; the shared helpers (sessions, transactions, snapshots) are those of tests/test_market_trip_book_mysql.py.

    MASGOLD_BOOK_REHEARSAL=1 .\\.venv\\Scripts\\python.exe -B -m unittest tests.test_custody_display_mysql

Fixtures are committed and never cleaned. Every test builds its own trips. Evidence classes are kept apart: a lock inventory of an open display snapshot, snapshot
consistency across a committed legacy change, and the decisions (overspending, dependent-use reversal) that must keep reading CURRENT data."""
from decimal import Decimal as D
import os
import unittest

from repositories.market_trip_repository import MarketTripRepository
from services.correction_service import CorrectionError
from services.market_trip_book_service import MarketTripBookService
from services.market_trip_service import TripError
import tests.test_market_trip_book_mysql as shared_module        # a MODULE alias: importing the TestCase class by name would make unittest collect its 19 tests here too
from tests.test_market_trip_book_mysql import DATE, TARGET


@unittest.skipUnless(os.environ.get("MASGOLD_BOOK_REHEARSAL") == "1", "explicit rehearsal-only opt-in")
class LegacyCustodyReadsMySqlTests(unittest.TestCase):
    tx = classmethod(shared_module.BookCoreMySqlTests.tx.__func__)
    snapshot = classmethod(shared_module.BookCoreMySqlTests.snapshot.__func__)
    make_session = classmethod(shared_module.BookCoreMySqlTests.make_session.__func__)
    build_service = classmethod(shared_module.BookCoreMySqlTests.build_service.__func__)
    query = shared_module.BookCoreMySqlTests.query
    key = shared_module.BookCoreMySqlTests.key
    scrap = shared_module.BookCoreMySqlTests.scrap
    correct = shared_module.BookCoreMySqlTests.correct

    @classmethod
    def setUpClass(cls):
        shared_module.BookCoreMySqlTests.setUpClass.__func__(cls)                                                   # target, level >= 56 and marker asserted there; Scrap opening balances

    def legacy_trip(self, cash="1000", grams="10"):
        return self.service.issue("", cash, [{"karat_id": self.karats[21], "weight": grams, "designation": "MELT"}], DATE, "custody rehearsal", self.key())["trip_id"]

    def display(self, connection, trip):
        def factory():
            from contextlib import contextmanager

            @contextmanager
            def yielded():
                yield connection
            return yielded()
        return MarketTripBookService(self.session, transaction_factory=factory).trip_detail(trip)

    def record_locks(self, connection_id):
        rows = self.query("SELECT t.OBJECT_NAME AS name FROM performance_schema.data_locks t JOIN performance_schema.threads th ON th.THREAD_ID=t.THREAD_ID "
                          "WHERE th.PROCESSLIST_ID=%s AND t.OBJECT_SCHEMA=%s AND t.LOCK_TYPE='RECORD'", (connection_id, TARGET))
        return [r["name"] for r in rows]

    # ------------------------------------------------------------ display
    def test_a_legacy_display_snapshot_holds_no_record_lock_and_stays_consistent_across_a_committed_change(self):
        trip = self.legacy_trip("1000", "10")
        with self.snapshot() as old:
            q = old.cursor()
            q.execute("SELECT CONNECTION_ID()")
            connection_id = q.fetchone()[0]
            q.close()
            first = self.display(old, trip)
            repository = MarketTripRepository(old)
            repository.trip_page("OPEN", 1, 50)
            repository.custody_lots(int(trip), current=False)
            self.assertEqual(self.record_locks(connection_id), [])                         # the whole legacy screen read takes NO record lock
            self.assertEqual(first["custody"]["cash"]["outstanding"], D("1000.00"))
            self.service.return_custody(trip, "400", [], DATE, "", self.key())              # committed by ANOTHER connection: must not block on the open display
            during = self.display(old, trip)
            self.assertEqual(during["custody"], first["custody"])                           # the old snapshot keeps the OLD state throughout
            self.assertEqual([e["id"] for e in during["entries"]], [e["id"] for e in first["entries"]])
            self.assertEqual(self.record_locks(connection_id), [])
        with self.snapshot() as fresh:
            now = self.display(fresh, trip)
            self.assertEqual(now["custody"]["cash"]["outstanding"], D("600.00"))             # a fresh snapshot sees the committed return
            self.assertEqual(len(now["entries"]), len(first["entries"]) + 1)

    # ------------------------------------------------------------ decisions
    def test_legacy_decisions_still_reject_overspending_on_current_data_even_with_a_stale_display_open(self):
        trip = self.legacy_trip("1000", "10")
        with self.snapshot() as stale:
            self.assertEqual(self.display(stale, trip)["custody"]["cash"]["outstanding"], D("1000.00"))
            self.service.return_custody(trip, "800", [], DATE, "", self.key())
            self.assertEqual(self.display(stale, trip)["custody"]["cash"]["outstanding"], D("1000.00"))      # the display is stale ...
            with self.assertRaises(TripError) as refused:                                  # ... but the decision reads current data, not the stale snapshot
                self.service.return_custody(trip, "300", [], DATE, "", self.key())
            self.assertEqual(refused.exception.code, "RETURN_EXCEEDS_CUSTODY")
        with self.assertRaises(TripError) as gold:
            self.service.return_custody(trip, "0", [{"karat_id": self.karats[21], "weight": "10.001", "designation": "MELT"}], DATE, "", self.key())
        self.assertEqual(gold.exception.code, "RETURN_EXCEEDS_CUSTODY")
        with self.assertRaises(TripError) as sale:
            self.service.sell_gold(trip, "CUSTODY_GOLD", self.karats[21], "MELT", None, "10.001", "TOTAL", "60006", "", "buyer", DATE, "", "", self.key(), True)
        self.assertEqual(sale.exception.code, "SALE_EXCEEDS_CUSTODY")
        with self.assertRaises(TripError) as spend:
            self.service.buy_gold(trip, [{"karat_id": self.karats[21], "designation": "MELT", "weight": "1", "price": "200.01"}], "", "vendor", DATE, "", "", self.key(), True)
        self.assertEqual(spend.exception.code, "PURCHASE_EXCEEDS_CASH")                     # only 200.00 EGP of custody cash is left

    def test_a_dependent_use_still_blocks_the_reversal_of_its_issue_and_a_valid_reversal_releases_it(self):
        trip = self.legacy_trip("1000", "10")
        issue = self.query("SELECT id FROM market_trip_entries WHERE trip_id=%s AND entry_kind='ISSUE'", (trip,))[0]["id"]
        back = self.service.return_custody(trip, "600", [], DATE, "", self.key())["entry_id"]
        with self.snapshot() as stale:
            self.display(stale, trip)
            with self.assertRaises(CorrectionError) as blocked:
                self.correct(issue)
            self.assertEqual(blocked.exception.code, "DEPENDENT_RETURN_EXISTS")             # the guard reads CURRENT custody, whatever an open display shows
            self.correct(back)                                                              # newest first: undo the return ...
        self.correct(issue)                                                                 # ... then the issue
        with self.snapshot() as fresh:
            self.assertEqual(self.display(fresh, trip)["custody"]["cash"]["outstanding"], D("0.00"))


if __name__ == "__main__":
    unittest.main()
