"""PREPARED, NOT YET EXECUTED. Explicit opt-in rehearsal checks for the market-trip BOOK core (migration 056). They connect to masgold_rehearsal ONLY (the target, a migration
level of at least 56 and the rehearsal marker are asserted before any write) and never to the DEV database. Real services, real repositories, real SQL, REPEATABLE READ
connections. Run them only after the concrete patch is reviewed and migration 056 has been applied to the rehearsal database by an approved step:

    MASGOLD_BOOK_REHEARSAL=1 .\\.venv\\Scripts\\python.exe -B -m unittest -v <named tests of this module>

Fixtures are committed and never cleaned (the ledgers are insert-only); every test builds its own trips. Negative database checks are raw statements the services would
never issue and always run inside transactions that are rolled back. Evidence classes are kept apart: functional checks, database-constraint checks, a replay /
cross-flow-key check, two-thread races (on the production authorization path, which serializes protected writers first, and on a test-only unlocked authorization that
proves the TRIP row lock serializes the balance), the aggregation parity of the display read and the snapshot consistency of the Gold Position. None of this is a stress test."""
from contextlib import contextmanager
from dataclasses import replace
from decimal import Decimal as D
import os
import threading
import unittest
from uuid import uuid4

from mysql.connector import Error as MySqlError

from app.session import AppSession
from config.database_config import DatabaseConfig
from database.connection import DatabaseConnection
from repositories.authentication_repository import AuthenticationRepository
from repositories.market_trip_repository import MarketTripRepository
from services import book_rules
from services.authentication_service import AuthenticationResult, _VERIFIED
from services.correction_service import CorrectionError, CorrectionService
from services.gold_position_service import GoldPositionService
from services.market_trip_book_service import MarketTripBookService
from services.market_trip_service import TripError
from tests.test_market_trip_hardening_mysql import UnlockedAuthorization

TARGET = "masgold_rehearsal"
DATE = "2026-10-17"
REFERENCE = {21: D("6000.0000"), 18: D("5142.8571"), 24: D("7000.0000")}
NEVER_WRITTEN = ("bank_movements", "supplier_gold_movements", "supplier_making_movements", "supplier_gold_settlements", "supplier_making_payments", "inventory_items",
                 "market_trip_lot_movements", "market_trip_lot_shop_movements", "intermediary_holding_movements", "market_trip_gold_buys", "market_trip_gold_sales")


def karat(karat_id, weight, price=None):
    row = {"basis": "MASTER_KARAT", "karat_id": karat_id, "weight": weight}
    if price is not None:
        row["price"] = price
    return row


def assay(permille, weight, price=None):
    row = {"basis": "FINENESS", "fineness": permille, "weight": weight}
    if price is not None:
        row["price"] = price
    return row


@unittest.skipUnless(os.environ.get("MASGOLD_BOOK_REHEARSAL") == "1", "explicit rehearsal-only opt-in")
class BookCoreMySqlTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.config = replace(DatabaseConfig.from_env(), database=TARGET)
        with cls.tx() as c:
            q = c.cursor(dictionary=True)
            q.execute("SELECT DATABASE() AS db, MAX(version) AS v FROM schema_migrations")
            row = q.fetchone()
            assert row["db"] == TARGET and int(row["v"]) >= 56, row
            q.execute("SELECT target_database FROM masgold_rehearsal_marker WHERE marker_key='MASGOLD_SCHEMA_REHEARSAL'")
            assert q.fetchone()["target_database"] == TARGET
            q.execute("SELECT id,karat_value FROM karats")
            cls.karats = {r["karat_value"]: r["id"] for r in q.fetchall()}
            q.close()
        cls.session, cls.user = cls.make_session("main")
        with cls.tx() as c:
            q = c.cursor()
            q.execute("INSERT INTO cash_movements(cash_account_id, movement_type_id, amount_delta, description, occurred_at, created_by_user_id) "      # the safe never goes
                      "SELECT a.id, t.id, 1000000000, 'rehearsal funding', NOW(), %s FROM cash_accounts a JOIN cash_movement_types t ON t.code='OPENING_BALANCE' "     # negative (stage 1):
                      "WHERE a.code='MAIN_SAFE'", (cls.user,))                                                                                                    # fund it once per run
            assert q.rowcount == 1
            for value in (21, 18, 24):                                  # test-owned counted Scrap, the way the established harness provides it
                q.execute("INSERT INTO old_gold_movements(movement_type_id,karat_id,weight_delta_grams,created_by_user_id) "
                          "SELECT id,%s,5000,%s FROM old_gold_movement_types WHERE code='OPENING_BALANCE'", (cls.karats[value], cls.user))
                assert q.rowcount == 1
            q.close()
        cls.corrector = CorrectionService(cls.session, transaction_factory=cls.tx)
        cls.service = cls.build_service(cls.session, cls.corrector)

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
        """A consistent REPEATABLE READ read-only snapshot, opened NOW and rolled back at the end."""
        c = DatabaseConnection(cls.config).connect()
        try:
            q = c.cursor()
            q.execute("SELECT DATABASE()")
            assert q.fetchone() == (TARGET,)
            q.close()
            c.rollback()
            c.start_transaction(consistent_snapshot=True, isolation_level="REPEATABLE READ", readonly=True)
            yield c
        finally:
            c.rollback()
            c.close()

    @classmethod
    def make_session(cls, label):
        with cls.tx() as c:
            q = c.cursor()
            q.execute("INSERT INTO employees(name) VALUES(%s)", ("056 rehearsal " + label,))
            emp = q.lastrowid
            q.execute("INSERT INTO users(employee_id,username,password_hash,display_name) VALUES(%s,%s,'unusable-rehearsal-hash',%s)",
                      (emp, f"rehearsal056-{label}-" + uuid4().hex[:12], "056 " + label))
            user = q.lastrowid
            q.execute("INSERT INTO user_roles(user_id,role_id) SELECT %s,id FROM roles WHERE code='OWNER'", (user,))
            q.close()
            auth = AuthenticationRepository(c)
            identity, permissions = auth.get_identity(user), auth.effective_permissions(user)
        session = AppSession()
        session._establish(AuthenticationResult(user, emp, identity["display_name"], permissions, _VERIFIED), int(session.generation))
        return session, user

    @classmethod
    def build_service(cls, session, corrector=None, authorization=None):
        service = MarketTripBookService(session, transaction_factory=cls.tx, price_reference=lambda: dict(REFERENCE), book_creation_enabled=True,
                                        **({} if authorization is None else {"authorization": authorization}))
        service._corrections = corrector or CorrectionService(session, transaction_factory=cls.tx)
        return service

    # ------------------------------------------------------------ helpers
    def query(self, sql, params=()):
        with self.tx() as c:
            q = c.cursor(dictionary=True)
            q.execute(sql, params)
            rows = q.fetchall()
            q.close()
            return rows

    def counts(self, tables=NEVER_WRITTEN):
        return {t: self.query(f"SELECT COUNT(*) AS n FROM {t}")[0]["n"] for t in tables}

    def scrap(self, karat_value):
        return self.query("SELECT COALESCE(SUM(weight_delta_grams),0) AS g FROM old_gold_movements WHERE karat_id=%s", (self.karats[karat_value],))[0]["g"]

    def key(self):
        return str(uuid4())

    def book_trip(self, weight="20", cash=None):
        trip = self.service.book_issue("", [karat(self.karats[21], weight)], DATE, "056 rehearsal", self.key())["trip_id"]
        if cash:
            self.service.issue(trip, cash, [], DATE, "056 cash", self.key())
        return trip

    def balance(self, trip, service=None):
        return (service or self.service).trip_detail(trip)["book"]["balance"]

    def correct(self, entry_id, reason="056 rehearsal", corrector=None):
        return (corrector or self.corrector).reverse("MARKET_TRIP_ENTRY", entry_id, reason)

    def refuses(self, statement, params=(), errnos=(3819, 1452, 1451, 1364, 1048, 1265, 1406)):
        with self.assertRaises(MySqlError) as raised:
            with self.tx() as c:
                q = c.cursor()
                q.execute(statement, params)
                q.close()
        self.assertIn(raised.exception.errno, errnos, raised.exception)

    # ------------------------------------------------------------ functional checks
    def test_the_four_documents_write_exactly_their_rows_through_the_existing_ledgers(self):
        scrap21, scrap18 = self.scrap(21), self.scrap(18)
        before = self.counts()
        cash_rows = self.query("SELECT COUNT(*) AS n FROM cash_movements")[0]["n"]
        issued = self.service.book_issue("", [karat(self.karats[21], "20"), karat(self.karats[18], "10")], DATE, "056", self.key())
        trip = issued["trip_id"]
        self.assertEqual(self.query("SELECT accounting_mode FROM market_trips WHERE id=%s", (trip,))[0]["accounting_mode"], "BOOK")
        self.assertEqual(self.query("SELECT trip_mode FROM market_trip_entries WHERE id=%s", (issued["entry_id"],))[0]["trip_mode"], "BOOK")
        self.assertEqual((self.scrap(21), self.scrap(18)), (scrap21 - D("20"), scrap18 - D("10")))
        lines = self.query("SELECT * FROM market_trip_book_lines WHERE entry_id=%s ORDER BY line_no", (issued["entry_id"],))
        self.assertEqual([(l["weight_grams"], l["basis"], l["karat_value"], l["eq21_posted"], l["eq21_analytical"], l["posting_difference"], l["signed_eq21_posted"], l["direction"])
                          for l in lines], [(D("20.000"), "MASTER_KARAT", 21, D("20.000"), D("20.000000"), D("0.000000"), D("20.000"), "IN"),
                                            (D("10.000"), "MASTER_KARAT", 18, D("8.571"), D("8.571429"), D("-0.000429"), D("8.571"), "IN")])
        self.assertEqual(len(self.query("SELECT 1 FROM old_gold_movements WHERE reference_type='MARKET_TRIP_ENTRY' AND reference_id=%s AND movement_code='TRIP_CUSTODY_OUT'",
                                        (issued["entry_id"],))), 2)
        self.service.issue(trip, "100000", [], DATE, "056 cash", self.key())                          # the shared cash path
        bought = self.service.book_buy(trip, [assay("916.7", "5", "32000"), karat(self.karats[24], "2", "14000")], "", "تاجر 056", DATE, "056", "", self.key(), False)
        trade = self.query("SELECT * FROM market_trip_book_trades WHERE entry_id=%s", (bought["entry_id"],))[0]
        self.assertEqual((trade["total_price"], trade["counterparty_kind"], trade["entry_kind"]), (D("46000.00"), "THIRD_PARTY", "BK_BUY"))
        sold = self.service.book_sell(trip, [karat(self.karats[21], "6", "36000")], "", "مشتري 056", DATE, "", "", self.key(), False)
        returned = self.service.book_return(trip, [karat(self.karats[21], "9.5"), assay("866.7", "1.8")], DATE, "", self.key())
        stock = self.query("SELECT * FROM shop_assayed_stock_movements WHERE item_entry_id=%s", (returned["entry_id"],))
        self.assertEqual([(m["movement_type"], m["grams_delta"], m["eq21_posted_delta"], m["eq21_analytical"], m["posting_difference"], m["source_entry_id"], m["correction_id"])
                          for m in stock], [("RECEIVE", D("1.800"), D("1.783"), D("1.782926"), D("0.000074"), int(returned["entry_id"]), None)])
        self.assertEqual(self.scrap(21), scrap21 - D("20") + D("9.5"))                                # Scrap conservation of the returned master karat
        custody = self.service.trip_detail(trip)["custody"]["cash"]
        self.assertEqual((custody["issued"], custody["spent"], custody["received"], custody["outstanding"]), (D("100000.00"), D("46000.00"), D("36000.00"), D("90000.00")))
        after = self.counts()
        self.assertEqual({k: after[k] - before[k] for k in NEVER_WRITTEN}, {k: 0 for k in NEVER_WRITTEN})          # no supplier / bank / inventory / lot / legacy-trade row
        self.assertEqual(self.query("SELECT COUNT(*) AS n FROM cash_movements")[0]["n"], cash_rows + 1)  # only the explicit cash issue touched Main Safe
        posted = sum((l["signed_eq21_posted"] for l in self.query("SELECT signed_eq21_posted FROM market_trip_book_lines WHERE trip_id=%s", (trip,))), D("0"))
        self.assertEqual(self.balance(trip)["posted"], posted)
        self.assertEqual(sold["kind"], "BK_SELL")

    def test_exact_replay_changed_payload_and_a_key_owned_by_another_flow(self):
        trip = self.book_trip()
        key = self.key()
        done = self.service.book_return(trip, [karat(self.karats[21], "2")], DATE, "", key)
        rows = self.query("SELECT COUNT(*) AS n FROM market_trip_book_lines WHERE trip_id=%s", (trip,))[0]["n"]
        again = self.service.book_return(trip, [karat(self.karats[21], "2")], DATE, "", key)
        self.assertEqual((again["duplicate"], again["entry_id"]), (True, done["entry_id"]))
        self.assertEqual(self.query("SELECT COUNT(*) AS n FROM market_trip_book_lines WHERE trip_id=%s", (trip,))[0]["n"], rows)
        for call in (lambda: self.service.book_return(trip, [karat(self.karats[21], "3")], DATE, "", key),
                     lambda: self.service.book_sell(trip, [karat(self.karats[21], "2", "12000")], "", "x", DATE, "", "", key, False)):
            with self.assertRaises(TripError) as conflict:
                call()
            self.assertEqual(conflict.exception.code, "REQUEST_CONFLICT")
        other, _ = self.make_session("other")
        stranger = self.build_service(other)
        with self.assertRaises(TripError) as conflict:
            stranger.book_return(trip, [karat(self.karats[21], "2")], DATE, "", key)
        self.assertEqual(conflict.exception.code, "REQUEST_CONFLICT")
        legacy_key = self.key()
        self.service.issue("", "10", [], DATE, "056 legacy", legacy_key)
        with self.assertRaises(TripError) as conflict:
            self.service.book_return(trip, [karat(self.karats[21], "1")], DATE, "", legacy_key)
        self.assertEqual(conflict.exception.code, "REQUEST_CONFLICT")

    def test_a_refused_or_failed_document_leaves_no_trace(self):
        trip = self.book_trip("5")
        tables = ("market_trip_entries", "market_trip_book_lines", "market_trip_book_trades", "shop_assayed_stock_movements", "old_gold_movements", "audit_log")
        before = self.counts(tables)
        for call, code in ((lambda: self.service.book_return(trip, [karat(self.karats[21], "5.001")], DATE, "", self.key()), "BOOK_EXCEEDS_BALANCE"),
                           (lambda: self.service.book_sell(trip, [karat(self.karats[21], "1", "600000")], "", "x", DATE, "", "", self.key(), False), "PRICE_DEVIATION"),
                           (lambda: self.service.book_buy(trip, [assay("916.7", "1", "5000")], "", "", DATE, "", "", self.key(), False), "VENDOR_REQUIRED"),
                           (lambda: self.service.book_issue(trip, [assay("875", "1")], DATE, "", self.key()), "SCRAP_BASIS"),
                           (lambda: self.service.close(trip), "RECONCILIATION_REQUIRED")):                 # Phase 3: closure exists and needs an effective reconciliation
            with self.assertRaises(TripError) as refused:
                call()
            self.assertEqual(refused.exception.code, code)
        self.assertEqual(self.counts(tables), before)

    def test_legacy_operations_refuse_a_book_trip_and_book_operations_refuse_a_legacy_trip(self):
        book = self.book_trip()
        legacy = self.service.issue("", "", [{"karat_id": self.karats[21], "weight": "10", "designation": "MELT"}], DATE, "056 legacy", self.key())["trip_id"]
        for call in (lambda: self.service.melt(book, [{"karat_id": self.karats[21], "weight": "1"}], DATE, "w", {"weight": "1", "fineness": "875"}, [], "a", "", "", self.key()),
                     lambda: self.service.issue(book, "", [{"karat_id": self.karats[21], "weight": "1", "designation": "MELT"}], DATE, "", self.key())):
            with self.assertRaises(TripError) as refused:
                call()
            self.assertIn(refused.exception.code, ("TRIP_MODE_MISMATCH", "BOOK_GOLD_PATH"))
        with self.assertRaises(TripError) as refused:
            self.service.book_return(legacy, [karat(self.karats[21], "1")], DATE, "", self.key())
        self.assertEqual(refused.exception.code, "TRIP_MODE_MISMATCH")

    # ------------------------------------------------------------ database-constraint checks (raw statements, rolled back)
    def entry_row(self, trip, kind, trip_mode):
        return ("INSERT INTO market_trip_entries (trip_id, trip_mode, entry_no, entry_kind, entry_date, request_key, request_hash, performed_by_user_id, occurred_at) "
                "VALUES (%s, %s, 900, %s, '2026-10-17', %s, %s, %s, NOW())", (trip, trip_mode, kind, str(uuid4()), "0" * 64, self.user))

    def test_the_database_keeps_each_entry_kind_in_its_own_mode(self):
        book, legacy = self.book_trip(), self.service.issue("", "10", [], DATE, "056", self.key())["trip_id"]
        self.refuses(*self.entry_row(book, "MELT", "BOOK"))                                            # a legacy-only kind in a BOOK trip: chk_mte_mode_kind
        self.refuses(*self.entry_row(legacy, "BK_ISSUE", "LEGACY"))                                    # a book kind in a LEGACY trip
        self.refuses(*self.entry_row(legacy, "BK_ISSUE", "BOOK"))                                      # the entry mode must equal the trip's: composite foreign key
        self.refuses(*self.entry_row(book, "ISSUE", "LEGACY"))
        self.refuses(*self.entry_row(book, "NOT_A_KIND", "BOOK"))
        entry = self.query("SELECT id FROM market_trip_entries WHERE trip_id=%s AND entry_kind='BK_ISSUE'", (book,))[0]["id"]
        for mode in ("LEGACY", "BOOK"):                                                               # a legacy gold leg never attaches to a BOOK entry
            self.refuses("INSERT INTO market_trip_entry_gold (entry_id, trip_mode, karat_id, designation, weight_grams, scrap_movement_id) VALUES (%s, %s, %s, 'MELT', 1, 1)",
                         (entry, mode, self.karats[21]))

    def test_the_mode_of_a_trip_with_entries_cannot_change_and_a_new_trip_defaults_to_legacy(self):
        book = self.book_trip()
        legacy = self.service.issue("", "10", [], DATE, "056", self.key())["trip_id"]
        self.refuses("UPDATE market_trips SET accounting_mode='LEGACY' WHERE id=%s", (book,), errnos=(1451,))
        self.refuses("UPDATE market_trips SET accounting_mode='BOOK' WHERE id=%s", (legacy,), errnos=(1451,))
        self.refuses("UPDATE market_trips SET accounting_mode='OTHER' WHERE id=%s", (legacy,))
        self.assertEqual(self.query("SELECT accounting_mode FROM market_trips WHERE id=%s", (legacy,))[0]["accounting_mode"], "LEGACY")
        with self.tx() as c:                                                                           # the DEFAULT: a trip inserted the old way is LEGACY
            q = c.cursor()
            q.execute("INSERT INTO market_trips (status, created_by_user_id) VALUES ('OPEN', %s)", (self.user,))
            q.execute("SELECT accounting_mode FROM market_trips WHERE id=%s", (q.lastrowid,))
            self.assertEqual(q.fetchone(), ("LEGACY",))
            c.rollback()

    def test_the_database_refuses_malformed_book_lines(self):
        trip = self.book_trip()
        entry = self.query("SELECT id FROM market_trip_entries WHERE trip_id=%s AND entry_kind='BK_ISSUE'", (trip,))[0]["id"]
        sell = self.service.book_sell(trip, [karat(self.karats[21], "1", "6000")], "", "x", DATE, "", "", self.key(), False)["entry_id"]
        base = ("INSERT INTO market_trip_book_lines (entry_id, line_no, trip_id, trip_mode, entry_kind, direction, weight_grams, basis, karat_id, karat_value, fineness_permille, "
                "eq21_analytical, eq21_posted, posting_difference, signed_eq21_posted, line_price, scrap_movement_id) VALUES (%s,%s,%s,'BOOK',%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)")
        k21 = self.karats[21]
        ok = (sell, 2, trip, "BK_SELL", "OUT", "1.000", "MASTER_KARAT", k21, 21, None, "1.000000", "1.000", "0.000000", "-1.000", "6000", None)
        self.refuses(base, (sell, 2, trip, "BK_SELL", "IN", "1.000", "MASTER_KARAT", k21, 21, None, "1.000000", "1.000", "0.000000", "1.000", "6000", None))     # direction vs kind
        self.refuses(base, (sell, 2, trip, "BK_SELL", "OUT", "0", "MASTER_KARAT", k21, 21, None, "0", "0", "0", "0", "6000", None))                               # weight > 0
        self.refuses(base, (sell, 2, trip, "BK_SELL", "OUT", "1.000", "MASTER_KARAT", k21, 21, "875.0", "1.000000", "1.000", "0.000000", "-1.000", "6000", None))  # basis shape
        self.refuses(base, (sell, 2, trip, "BK_SELL", "OUT", "1.000", "FINENESS", k21, None, "875.0", "1.000000", "1.000", "0.000000", "-1.000", "6000", None))     # karat on an assay line
        self.refuses(base, (sell, 2, trip, "BK_SELL", "OUT", "1.000", "FINENESS", None, None, "1000.1", "1.000000", "1.000", "0.000000", "-1.000", "6000", None))   # assay range
        self.refuses(base, (sell, 2, trip, "BK_SELL", "OUT", "1.000", "MASTER_KARAT", k21, 21, None, "1.000000", "1.001", "0.001000", "-1.001", "6000", None))       # |difference| > 0.0005
        self.refuses(base, (sell, 2, trip, "BK_SELL", "OUT", "1.000", "MASTER_KARAT", k21, 21, None, "1.000000", "1.000", "0.000100", "-1.000", "6000", None))       # evidence inconsistent
        self.refuses(base, (sell, 2, trip, "BK_SELL", "OUT", "1.000", "MASTER_KARAT", k21, 21, None, "2.000000", "2.000", "0.000000", "-2.000", "6000", None))       # analytical vs facts
        self.refuses(base, (sell, 2, trip, "BK_SELL", "OUT", "1.000", "MASTER_KARAT", k21, 21, None, "1.000000", "1.000", "0.000000", "1.000", "6000", None))        # sign of the signed amount
        self.refuses(base, (sell, 2, trip, "BK_SELL", "OUT", "1.000", "MASTER_KARAT", k21, 21, None, "1.000000", "1.000", "0.000000", "-1.000", None, None))         # a trade line needs a price
        self.refuses(base, (sell, 2, trip, "BK_SELL", "OUT", "1.000", "MASTER_KARAT", k21, 21, None, "1.000000", "1.000", "0.000000", "-1.000", "6000", 1))          # ... and has no Scrap leg
        self.refuses(base, (entry, 9, trip, "BK_ISSUE", "IN", "1.000", "MASTER_KARAT", k21, 21, None, "1.000000", "1.000", "0.000000", "1.000", None, None))         # an issue needs its Scrap leg
        self.refuses(base, (entry, 9, str(int(trip) + 1), "BK_ISSUE", "IN", "1.000", "MASTER_KARAT", k21, 21, None, "1.000000", "1.000", "0.000000", "1.000", None, 1))         # composite key: wrong trip
        self.refuses(base, ok[:3] + ("BK_BUY",) + ok[4:], errnos=(1452, 3819))                              # composite key: the entry is not a BK_BUY
        self.refuses(base, (sell, 1, trip, "BK_SELL", "OUT", "1.000", "MASTER_KARAT", k21, 21, None, "1.000000", "1.000", "0.000000", "-1.000", "6000", None), errnos=(1062,))   # primary key

    def test_the_database_refuses_malformed_trade_documents_and_stock_rows(self):
        trip = self.book_trip(cash="100000")
        bought = self.service.book_buy(trip, [assay("916.7", "5", "32000")], "", "تاجر", DATE, "", "", self.key(), False)["entry_id"]
        issue = self.query("SELECT id FROM market_trip_entries WHERE trip_id=%s AND entry_kind='BK_ISSUE'", (trip,))[0]["id"]
        returned = self.service.book_return(trip, [assay("866.7", "1.8")], DATE, "", self.key())["entry_id"]
        self.refuses("UPDATE market_trip_book_trades SET counterparty_name='   ' WHERE entry_id=%s", (bought,))
        self.refuses("UPDATE market_trip_book_trades SET counterparty_name_key='' WHERE entry_id=%s", (bought,))
        self.refuses("UPDATE market_trip_book_trades SET counterparty_kind='REGISTERED_PARTY' WHERE entry_id=%s", (bought,))
        self.refuses("UPDATE market_trip_book_trades SET party_id=1 WHERE entry_id=%s", (bought,))
        self.refuses("UPDATE market_trip_book_trades SET total_price=0 WHERE entry_id=%s", (bought,))
        self.refuses("UPDATE market_trip_book_trades SET deviation_percent=NULL WHERE entry_id=%s", (bought,))     # the pair (reference, deviation) is both set or both NULL
        self.refuses("INSERT INTO market_trip_book_trades (entry_id, entry_kind, counterparty_kind, counterparty_name, counterparty_name_key, total_price, trade_date) "
                     "VALUES (%s, 'BK_BUY', 'THIRD_PARTY', 'x', 'x', 1, '2026-10-17')", (issue,))                                  # the entry is not a BK_BUY
        stock = ("INSERT INTO shop_assayed_stock_movements (item_entry_id, item_line_no, movement_type, grams_delta, eq21_posted_delta, eq21_analytical, posting_difference, "
                 "eq21_independent, posting_basis, source_entry_id, correction_id) VALUES (%s, 1, %s, %s, %s, %s, %s, %s, 'INDEPENDENT', %s, %s)")
        self.refuses(stock, (returned, "RECEIVE", "1.000", "1.000", "1.000000", "0.000000", "1.000", returned, None), errnos=(1062,))              # one receive per item
        self.refuses(stock, (returned, "RECEIVE", "-1.000", "-1.000", "1.000000", "0.000000", "-1.000", returned, None))                           # sign of a receive
        self.refuses(stock, (returned, "RECEIVE_REVERSAL", "-1.800", "-1.783", "1.783000", "0.000000", "1.783", returned, None))                  # a reversal needs its correction
        self.refuses(stock, (returned, "ISSUE", "-0.9", "-0.891", "0.891463", "-0.000463", "0.891", returned, None))                             # an issue comes from ANOTHER entry
        self.refuses(stock, (returned, "TRANSFER", "-0.9", "-0.891", "0.891463", "-0.000463", "0.891", issue, None))
        self.refuses(stock, (issue, "RECEIVE", "1.000", "1.000", "1.000000", "0.000000", "1.000", issue, None))                                  # the item is not a BK_RETURN FINENESS line

    # ------------------------------------------------------------ races
    def race(self, jobs):
        outcomes = []
        gate = threading.Barrier(len(jobs))

        def run(job):
            gate.wait(30)
            try:
                outcomes.append(("ok", job()))
            except (TripError, CorrectionError) as exc:
                outcomes.append((exc.code, None))
        threads = [threading.Thread(target=run, args=(job,)) for job in jobs]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(120)
        return outcomes

    def test_two_writers_cannot_spend_the_same_book_balance_on_the_production_authorization_path(self):
        trip = self.book_trip("10")
        services = [self.build_service(self.make_session(f"race{n}")[0]) for n in range(2)]
        scrap = self.scrap(21)
        outcomes = self.race([lambda s=s: s.book_return(trip, [karat(self.karats[21], "6")], DATE, "", self.key())["entry_id"] for s in services])
        self.assertEqual(sorted(code for code, _ in outcomes), ["BOOK_EXCEEDS_BALANCE", "ok"])
        self.assertEqual(self.balance(trip)["posted"], D("4.000"))
        self.assertEqual(self.scrap(21), scrap + D("6.000"))

    def test_the_trip_row_lock_alone_serializes_the_balance_without_the_authorization_locks(self):
        trip = self.book_trip("10")
        pairs = [self.make_session(f"free{n}") for n in range(2)]
        services = [self.build_service(session, CorrectionService(session, transaction_factory=self.tx, authorization=UnlockedAuthorization(session)),
                                       UnlockedAuthorization(session)) for session, _ in pairs]
        scrap = self.scrap(21)
        outcomes = self.race([lambda s=s: s.book_return(trip, [karat(self.karats[21], "6")], DATE, "", self.key())["entry_id"] for s in services])
        self.assertEqual(sorted(code for code, _ in outcomes), ["BOOK_EXCEEDS_BALANCE", "ok"])
        self.assertEqual(self.scrap(21), scrap + D("6.000"))

    def test_a_reversal_and_a_use_of_the_same_gold_never_both_succeed(self):
        trip = self.book_trip("10")
        issue = self.query("SELECT id FROM market_trip_entries WHERE trip_id=%s AND entry_kind='BK_ISSUE'", (trip,))[0]["id"]
        sessions = [self.make_session(f"rev{n}")[0] for n in range(2)]
        free = [UnlockedAuthorization(s) for s in sessions]
        seller = self.build_service(sessions[0], CorrectionService(sessions[0], transaction_factory=self.tx, authorization=free[0]), free[0])
        corrector = CorrectionService(sessions[1], transaction_factory=self.tx, authorization=free[1])
        outcomes = self.race([lambda: seller.book_sell(trip, [karat(self.karats[21], "10", "60000")], "", "x", DATE, "", "", self.key(), False)["entry_id"],
                              lambda: self.correct(issue, "056 race", corrector)["correction_id"]])
        codes = sorted(code for code, _ in outcomes)
        self.assertNotEqual(codes, ["ok", "ok"])                                                      # the gold cannot be used AND taken away
        self.assertIn(codes, (["BOOK_EXCEEDS_BALANCE", "ok"], ["DEPENDENT_BOOK_USE_EXISTS", "ok"]))

    # ------------------------------------------------------------ aggregation and snapshot
    def test_the_display_aggregation_equals_the_locking_decision_read(self):
        trip = self.book_trip("20", cash="100000")
        self.service.book_buy(trip, [assay("916.7", "5", "32000"), karat(self.karats[18], "4", "20000")], "", "تاجر", DATE, "", "", self.key(), False)
        self.service.book_sell(trip, [karat(self.karats[21], "3.333", "20000")], "", "مشتري", DATE, "", "", self.key(), False)
        self.service.book_return(trip, [karat(self.karats[21], "3.334"), assay("500", "0.003")], DATE, "", self.key())
        with self.tx() as c:
            repository = MarketTripRepository(c)
            exact = book_rules.balance_of_rows(repository.book_lines(int(trip), current=True))
            grouped = book_rules.balance_of_groups(repository.book_groups_by_trip()[int(trip)])
            self.assertEqual((grouped.analytical_open, grouped.posted), (exact.analytical_open, exact.posted))
            self.assertEqual(exact.posted, sum((D(str(r["signed_eq21_posted"])) for r in repository.book_lines(int(trip))), D("0")))

    def positions(self, connection):
        @contextmanager
        def factory():
            yield connection
        return GoldPositionService(self.session, transaction_factory=factory)

    def display_service(self, connection):
        """The REAL service whose every transaction IS the given (snapshot) connection: what the screens read."""
        @contextmanager
        def factory():
            yield connection
        return MarketTripBookService(self.session, transaction_factory=factory)

    def displayed(self, connection, trip):
        """Everything one BOOK screen shows from ONE connection: the balance, the entry history with its book lines and reversal marks, the stock items, and the
        list summary of the trip (`_book_summary`, what `trip_page` shows)."""
        service = self.display_service(connection)
        detail = service.trip_detail(trip)
        summary = MarketTripBookService._book_summary(MarketTripRepository(connection), MarketTripRepository(connection).trip(trip))
        return detail, summary

    @staticmethod
    def history_balance(detail):
        """The posted balance implied by the DISPLAYED history: the lines of the entries that are not marked reversed."""
        total = D("0.000")
        for entry in detail["entries"]:
            if entry["reversed"]:
                continue
            for line in entry["book_lines"]:
                total += line["posted"] if line["direction"] == "IN" else -line["posted"]
        return total

    def test_the_display_reads_of_a_book_trip_are_one_snapshot_across_a_committed_posting_and_a_committed_reversal(self):
        trip = self.book_trip("20")
        back = self.service.book_return(trip, [karat(self.karats[21], "2"), assay("866.7", "1.8")], DATE, "", self.key())
        with self.snapshot() as old:
            first, first_summary = self.displayed(old, trip)
            self.assertEqual(first["book"]["balance"]["posted"], self.history_balance(first))                      # the three reads agree with each other
            self.assertEqual(first_summary["posted"], first["book"]["balance"]["posted"])
            self.assertEqual([(i["entry_id"], i["grams"]) for i in first["book"]["stock_items"]], [(str(back["entry_id"]), D("1.800"))])
            sale = self.service.book_sell(trip, [karat(self.karats[21], "3", "18000")], "", "مشتري", DATE, "", "", self.key(), False)   # committed by ANOTHER connection
            during, during_summary = self.displayed(old, trip)
            self.assertEqual(during["book"]["balance"], first["book"]["balance"])                                  # the old snapshot keeps the OLD state ...
            self.assertEqual([e["id"] for e in during["entries"]], [e["id"] for e in first["entries"]])             # ... its history ...
            self.assertEqual(during["book"]["stock_items"], first["book"]["stock_items"])                          # ... its stock items ...
            self.assertEqual(during_summary, first_summary)                                                       # ... and the list summary
            self.correct(back["entry_id"])                                                                        # a committed REVERSAL, also invisible to the old snapshot
            after, after_summary = self.displayed(old, trip)
            self.assertEqual((after["book"], after_summary, [(e["id"], e["reversed"]) for e in after["entries"]]),
                             (first["book"], first_summary, [(e["id"], e["reversed"]) for e in first["entries"]]))
            self.assertEqual(after["book"]["balance"]["posted"], self.history_balance(after))
        with self.snapshot() as fresh:
            now, now_summary = self.displayed(fresh, trip)
            self.assertIn(sale["entry_id"], [e["id"] for e in now["entries"]])                                    # a fresh snapshot sees both committed changes
            self.assertEqual({e["id"]: e["reversed"] for e in now["entries"]}[str(back["entry_id"])], True)
            self.assertEqual(now["book"]["stock_items"], [])
            self.assertEqual(now["book"]["balance"]["posted"], D("20.000") - D("3.000"))
            self.assertEqual((now["book"]["balance"]["posted"], now_summary["posted"]), (self.history_balance(now), self.history_balance(now)))

    def test_a_display_read_takes_no_lock_that_could_block_a_posting(self):
        """A snapshot used for display holds no locking read on the entry / line rows (a shared lock would block any UPDATE / DELETE of them): a posting commits while it is open."""
        trip = self.book_trip("20")
        with self.snapshot() as old:
            self.displayed(old, trip)
            self.service.book_return(trip, [karat(self.karats[21], "1")], DATE, "", self.key())                   # commits while the display snapshot is open
            self.displayed(old, trip)

    def test_the_gold_position_is_one_snapshot_and_counts_the_gram_once(self):
        trip = self.book_trip("20")
        with self.snapshot() as old:
            first = self.positions(old).position()
            self.service.book_return(trip, [karat(self.karats[21], "5")], DATE, "", self.key())               # committed AFTER the snapshot began
            again = self.positions(old).position()
            self.assertEqual(first["summary"], again["summary"])                                              # the old snapshot is internally consistent
            row = next(t for t in first["book"]["trips"] if t["trip_id"] == str(trip))
            self.assertEqual(row["eq21"], D("20.000"))
        with self.snapshot() as fresh:
            now = self.positions(fresh).position()
            row = next(t for t in now["book"]["trips"] if t["trip_id"] == str(trip))
            self.assertEqual(row["eq21"], D("15.000"))
            self.assertEqual(now["summary"]["total_eq21"], first["summary"]["total_eq21"])                    # 21K Scrap + book is conserved by the move

    def test_assayed_stock_appears_once_in_the_position_and_leaves_with_its_reversal(self):
        trip = self.book_trip("20")
        back = self.service.book_return(trip, [assay("866.7", "1.8")], DATE, "", self.key())
        with self.snapshot() as c:
            item = next(i for i in self.positions(c).position()["shop"]["assayed"] if i["item"] == f"{back['entry_id']}:1")
            self.assertEqual((item["grams"], item["eq21"], item["fineness"]), (D("1.800"), D("1.783"), D("866.7")))
        self.correct(back["entry_id"])
        with self.snapshot() as c:
            self.assertEqual([i for i in self.positions(c).position()["shop"]["assayed"] if i["item"] == f"{back['entry_id']}:1"], [])

    # ------------------------------------------------------------ corrections
    def test_reversals_use_the_stored_figures_and_are_decided_by_arithmetic(self):
        trip = self.book_trip("20", cash="100000")
        issue = self.query("SELECT id FROM market_trip_entries WHERE trip_id=%s AND entry_kind='BK_ISSUE'", (trip,))[0]["id"]
        sale = self.service.book_sell(trip, [karat(self.karats[21], "6", "36000")], "", "مشتري", DATE, "", "", self.key(), False)
        with self.assertRaises(CorrectionError) as blocked:
            self.correct(issue, "056 early")
        self.assertEqual(blocked.exception.code, "DEPENDENT_BOOK_USE_EXISTS")
        self.correct(sale["entry_id"])
        self.assertEqual(self.balance(trip)["posted"], D("20.000"))
        back = self.service.book_return(trip, [karat(self.karats[21], "5"), assay("866.7", "1.8")], DATE, "", self.key())
        scrap = self.scrap(21)
        self.correct(back["entry_id"])
        self.assertEqual(self.scrap(21), scrap - D("5.000"))                                              # the exact stored Scrap movement, negated
        compensation = self.query("SELECT * FROM shop_assayed_stock_movements WHERE item_entry_id=%s AND movement_type='RECEIVE_REVERSAL'", (back["entry_id"],))
        self.assertEqual([(m["grams_delta"], m["eq21_posted_delta"], m["correction_id"] is not None) for m in compensation], [(D("-1.800"), D("-1.783"), True)])
        self.assertEqual(self.query("SELECT COUNT(*) AS n FROM shop_assayed_stock_movements WHERE item_entry_id=%s", (back["entry_id"],))[0]["n"], 2)       # the original stays counted
        with self.assertRaises(CorrectionError) as twice:
            self.correct(back["entry_id"])
        self.assertEqual(twice.exception.code, "ALREADY_REVERSED")
        self.correct(issue)                                                                                # now nothing depends on it
        self.assertEqual(self.balance(trip)["posted"], D("0.000"))

    def test_an_assayed_return_cannot_be_reversed_after_part_of_the_stock_was_used(self):
        trip = self.book_trip("20")
        back = self.service.book_return(trip, [assay("866.7", "1.8")], DATE, "", self.key())
        later = self.service.book_return(trip, [karat(self.karats[21], "1")], DATE, "", self.key())
        with self.tx() as c:                                                                               # the Phase 2 consumer, simulated by one raw ISSUE row
            q = c.cursor()
            q.execute("INSERT INTO shop_assayed_stock_movements (item_entry_id, item_line_no, movement_type, grams_delta, eq21_posted_delta, eq21_analytical, posting_difference, "
                      "eq21_independent, posting_basis, source_entry_id) VALUES (%s, 1, 'ISSUE', -0.900, -0.891, 0.891463, -0.000463, 0.891, 'INDEPENDENT', %s)",
                      (back["entry_id"], later["entry_id"]))
            q.close()
        with self.assertRaises(CorrectionError) as blocked:
            self.correct(back["entry_id"])
        self.assertEqual(blocked.exception.code, "DEPENDENT_STOCK_USE_EXISTS")

    # ------------------------------------------------------------ the migration
    def test_the_migration_is_registered_and_the_objects_exist(self):
        self.assertEqual(self.query("SELECT COUNT(*) AS n FROM schema_migrations WHERE version=56")[0]["n"], 1)
        names = {r["TABLE_NAME"] for r in self.query("SELECT TABLE_NAME FROM information_schema.TABLES WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME IN "
                                                      "('market_trip_book_lines','market_trip_book_trades','shop_assayed_stock_movements')")}
        self.assertEqual(names, {"market_trip_book_lines", "market_trip_book_trades", "shop_assayed_stock_movements"})
        clause = self.query("SELECT CHECK_CLAUSE AS c FROM information_schema.CHECK_CONSTRAINTS WHERE CONSTRAINT_SCHEMA=DATABASE() AND CONSTRAINT_NAME='chk_mte_mode_kind'")[0]["c"]
        for kind in book_rules.BOOK_KINDS:
            self.assertIn(kind, clause)
        self.assertEqual(self.query("SELECT COLUMN_DEFAULT AS d, IS_NULLABLE AS n FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=DATABASE() "
                                    "AND TABLE_NAME='market_trips' AND COLUMN_NAME='accounting_mode'")[0], {"d": "LEGACY", "n": "NO"})
        self.assertEqual(self.query("SELECT COUNT(*) AS n FROM market_trips WHERE accounting_mode NOT IN ('LEGACY','BOOK')")[0]["n"], 0)
        self.assertEqual(self.query("SELECT COUNT(*) AS n FROM market_trip_entries e JOIN market_trips t ON t.id=e.trip_id WHERE e.trip_mode<>t.accounting_mode")[0]["n"], 0)


if __name__ == "__main__":
    unittest.main()
