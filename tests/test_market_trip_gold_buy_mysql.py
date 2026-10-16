"""Explicit opt-in rehearsal checks for the cash gold purchase (migration 054). They connect to masgold_rehearsal ONLY (target, migration level >= 54 and
marker are asserted) and never to the DEV database. Real services, real repositories, real SQL, REPEATABLE READ connections. Fixtures are committed and never
cleaned (immutable ledgers); every test builds its own custody. Negative database checks run inside transactions that are rolled back.

Evidence classes, kept apart: functional checks (real services over MySQL), database-constraint checks (raw statements the services would never issue,
rolled back) and a two-thread race on the production authorization path (which serializes protected writers first, so it proves the decision made after the
wait, not lock-order freedom). None of this is a stress test."""
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
from services.authentication_service import AuthenticationResult, _VERIFIED
from services.correction_service import CorrectionError, CorrectionService
from services.market_trip_service import MarketTripService, TripError
from services.supplier_service import SupplierService

TARGET = "masgold_rehearsal"
DATE = "2026-10-17"
NEVER_WRITTEN = ("old_gold_movements", "cash_movements", "bank_movements", "supplier_gold_movements", "supplier_making_movements",
                 "supplier_gold_settlements", "supplier_gold_settlement_legs", "supplier_making_payments", "market_trip_lot_shop_movements",
                 "market_trip_lot_movements", "intermediary_holding_movements", "inventory_items", "market_trip_supplier_payments",
                 "market_trip_gold_settlements", "market_trip_gold_sales")
REFERENCE = {21: D("6000.0000"), 18: D("5142.8571"), 24: D("7000.0000")}


@unittest.skipUnless(os.environ.get("MASGOLD_GOLD_BUY_REHEARSAL") == "1", "explicit rehearsal-only opt-in")
class GoldBuyMySqlTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.config = replace(DatabaseConfig.from_env(), database=TARGET)
        with cls.tx() as c:
            q = c.cursor(dictionary=True)
            q.execute("SELECT DATABASE() AS db, MAX(version) AS v FROM schema_migrations")
            row = q.fetchone()
            assert row["db"] == TARGET and int(row["v"]) >= 54, row
            q.execute("SELECT target_database FROM masgold_rehearsal_marker WHERE marker_key='MASGOLD_SCHEMA_REHEARSAL'")
            assert q.fetchone()["target_database"] == TARGET
            q.execute("INSERT INTO employees(name) VALUES('054 rehearsal owner')")
            emp = q.lastrowid
            q.execute("INSERT INTO users(employee_id,username,password_hash,display_name) VALUES(%s,%s,'unusable-rehearsal-hash','054 owner')",
                      (emp, "rehearsal054-" + uuid4().hex))
            cls.user = q.lastrowid
            q.execute("INSERT INTO user_roles(user_id,role_id) SELECT %s,id FROM roles WHERE code='OWNER'", (cls.user,))
            q.execute("SELECT id,karat_value FROM karats")
            cls.karats = {r["karat_value"]: r["id"] for r in q.fetchall()}
            auth = AuthenticationRepository(c)
            identity, permissions = auth.get_identity(cls.user), auth.effective_permissions(cls.user)
            cls.session = AppSession()
            cls.session._establish(AuthenticationResult(cls.user, emp, identity["display_name"], permissions, _VERIFIED), int(cls.session.generation))
            q.close()
        cls.suppliers = SupplierService(cls.session, transaction_factory=cls.tx)
        cls.supplier = cls.suppliers.create_supplier("054 مورد " + uuid4().hex[:10], karat_id=cls.karats[18])
        cls.intermediary = cls.suppliers.create_supplier("054 مسوقجي " + uuid4().hex[:10], is_supplier=False, is_intermediary=True)
        cls.inactive = cls.suppliers.create_supplier("054 مورد متوقف " + uuid4().hex[:10], karat_id=cls.karats[21])
        with cls.tx() as c:
            q = c.cursor()
            q.execute("UPDATE suppliers SET is_active=0 WHERE id=%s", (cls.inactive,))
            q.close()

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

    def setUp(self):
        self.service = MarketTripService(self.session, transaction_factory=self.tx, price_reference=lambda: dict(REFERENCE))
        self.correct = CorrectionService(self.session, transaction_factory=self.tx)
        self.service._corrections = self.correct

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

    def trip(self, cash="100000"):
        return self.service.issue("", cash, [], DATE, "054 rehearsal", str(uuid4()))

    @staticmethod
    def line(karat, weight="5", price="30000", designation="LOGO"):
        return {"karat_id": karat, "designation": designation, "weight": weight, "price": price}

    def buy(self, trip, lines=None, party="", name="تاجر 054", key=None, ack=False):
        lines = lines if lines is not None else [self.line(self.karats[21])]
        return self.service.buy_gold(trip["trip_id"], lines, party, name, DATE, "054 ref", "054 notes", key or str(uuid4()), ack)

    def custody(self, trip):
        return self.service.trip_detail(trip["trip_id"])["custody"]

    def gold(self, trip, karat=21, designation="LOGO"):
        return next((g["outstanding"] for g in self.custody(trip)["gold"] if g["karat_value"] == karat and g["designation"] == designation), D("0.000"))

    # ------------------------------------------------------------ functional checks
    def test_a_purchase_writes_exactly_the_entry_document_and_lines_and_replays_exactly(self):
        trip = self.trip("100000")
        before = self.counts()
        key = str(uuid4())
        done = self.buy(trip, [self.line(self.karats[21], "10.250", "61500.50"), self.line(self.karats[18], "4.000", "20000", "MELT")], key=key)
        self.assertEqual((done["kind"], done["vendor_kind"], done["total_price"], done["duplicate"]), ("GOLD_BUY", "THIRD_PARTY", "81500.50", False))
        self.assertEqual(self.counts(), before)                                              # no money / supplier / Scrap / inventory / lot-ledger row
        head = self.query("SELECT * FROM market_trip_gold_buys WHERE entry_id=%s", (done["entry_id"],))[0]
        self.assertEqual((head["total_price"], head["vendor_kind"], head["vendor_party_id"], head["vendor_name"], head["price_warning_acknowledged"]),
                         (D("81500.50"), "THIRD_PARTY", None, "تاجر 054", 0))
        lines = self.query("SELECT * FROM market_trip_gold_buy_lines WHERE entry_id=%s ORDER BY line_no", (done["entry_id"],))
        self.assertEqual([(l["line_no"], l["karat_id"], l["designation"], l["weight_grams"], l["line_price"]) for l in lines],
                         [(1, self.karats[21], "LOGO", D("10.250"), D("61500.50")), (2, self.karats[18], "MELT", D("4.000"), D("20000.00"))])
        self.assertEqual(sum((l["line_price"] for l in lines), D("0")), head["total_price"])
        self.assertEqual(self.query("SELECT entry_kind FROM market_trip_entries WHERE id=%s", (done["entry_id"],))[0]["entry_kind"], "GOLD_BUY")
        custody = self.custody(trip)
        self.assertEqual((self.gold(trip, 21, "LOGO"), self.gold(trip, 18, "MELT"), custody["cash"]["spent"], custody["cash"]["outstanding"]),
                         (D("10.250"), D("4.000"), D("81500.50"), D("18499.50")))
        again = self.buy(trip, [self.line(self.karats[21], "10.250", "61500.50"), self.line(self.karats[18], "4.000", "20000", "MELT")], key=key)
        self.assertEqual((again["duplicate"], again["entry_id"]), (True, done["entry_id"]))
        self.assertEqual(self.query("SELECT COUNT(*) AS n FROM market_trip_gold_buys WHERE entry_id=%s", (done["entry_id"],))[0]["n"], 1)
        with self.assertRaises(TripError) as conflict:
            self.buy(trip, [self.line(self.karats[21], "10.250", "61500.51")], key=key)
        self.assertEqual(conflict.exception.code, "REQUEST_CONFLICT")

    def test_the_vendor_is_mandatory_and_no_supplier_ledger_is_ever_touched(self):
        trip = self.trip()
        supplier_rows = self.query("SELECT COUNT(*) AS n FROM supplier_gold_movements WHERE supplier_id=%s", (self.supplier,))[0]["n"]
        counts = self.counts()
        before = self.counts(("market_trip_entries", "market_trip_gold_buys", "market_trip_gold_buy_lines"))
        for name in ("", "   "):
            with self.assertRaises(TripError) as refused:
                self.buy(trip, name=name)
            self.assertEqual(refused.exception.code, "VENDOR_REQUIRED")
        for party, code in ((str(self.inactive), "VENDOR_INACTIVE"), ("999999999", "VENDOR_NOT_FOUND")):
            with self.assertRaises(TripError) as refused:
                self.buy(trip, party=party)
            self.assertEqual(refused.exception.code, code)
        self.assertEqual(self.counts(("market_trip_entries", "market_trip_gold_buys", "market_trip_gold_buy_lines")), before)
        registered = self.buy(trip, [self.line(self.karats[21], "1", "6000")], party=str(self.supplier))
        intermediary = self.buy(trip, [self.line(self.karats[21], "1", "6000")], party=str(self.intermediary))
        typed = self.buy(trip, [self.line(self.karats[21], "1", "6000")], name="  أحمَد   الصّاغة ")
        self.assertEqual((registered["vendor_kind"], registered["vendor_party_id"]), ("REGISTERED_PARTY", str(self.supplier)))
        self.assertEqual((intermediary["vendor_kind"], intermediary["vendor_party_id"]), ("REGISTERED_PARTY", str(self.intermediary)))
        key = self.query("SELECT vendor_name, vendor_name_key FROM market_trip_gold_buys WHERE entry_id=%s", (typed["entry_id"],))[0]
        self.assertEqual(key["vendor_name_key"], MarketTripService.vendor_name_key("احمد الصاغه"))
        self.assertEqual(key["vendor_name"], "أحمَد   الصّاغة")
        self.assertEqual(self.counts(), counts)
        self.assertEqual(self.query("SELECT COUNT(*) AS n FROM supplier_gold_movements WHERE supplier_id=%s", (self.supplier,))[0]["n"], supplier_rows)
        self.assertEqual(self.query("SELECT is_supplier, is_intermediary FROM suppliers WHERE id=%s", (self.intermediary,))[0],
                         {"is_supplier": 0, "is_intermediary": 1})                          # a purchase grants no role

    def test_the_cash_cap_the_invalid_lines_and_the_price_warning_evidence(self):
        trip = self.trip("100000")
        before = self.counts(("market_trip_entries", "market_trip_gold_buys", "market_trip_gold_buy_lines"))
        for lines, code in (([self.line(self.karats[21], "10", "100000.01")], "PURCHASE_EXCEEDS_CASH"),
                            ([self.line(self.karats[21], "10", "60000")] * 2, "PURCHASE_EXCEEDS_CASH"),
                            ([], "LINES_REQUIRED"), ([self.line(self.karats[21], "0")], "WEIGHT_REQUIRED"),
                            ([self.line(self.karats[21], "1", "0")], "PRICE_INVALID"),
                            ([self.line(self.karats[21], "1", "6000", "UNCLASSIFIED")], "DESIGNATION_REQUIRED"),
                            ([self.line(self.karats[21], "10", "40000")], "PRICE_DEVIATION")):
            with self.assertRaises(TripError) as refused:
                self.buy(trip, lines)
            self.assertEqual(refused.exception.code, code, lines)
        self.assertEqual(self.counts(("market_trip_entries", "market_trip_gold_buys", "market_trip_gold_buy_lines")), before)
        done = self.buy(trip, [self.line(self.karats[21], "10", "40000")], ack=True)         # reference 60,000: -33.33 %
        head = self.query("SELECT * FROM market_trip_gold_buys WHERE entry_id=%s", (done["entry_id"],))[0]
        self.assertEqual((head["price_warning_acknowledged"], head["deviation_percent"], head["reference_total"]), (1, D("-33.33"), D("60000.00")))
        inside = self.buy(trip, [self.line(self.karats[21], "5", "31500"), self.line(self.karats[24], "0.5", "3500")])
        head = self.query("SELECT * FROM market_trip_gold_buys WHERE entry_id=%s", (inside["entry_id"],))[0]
        self.assertEqual((head["price_warning_acknowledged"], head["deviation_percent"], head["reference_total"]), (0, D("4.48"), D("33500.00")))
        self.assertEqual(self.custody(trip)["cash"]["outstanding"], D("100000") - D("40000") - D("35000"))

    def test_purchased_gold_returns_to_the_shop_and_the_reversal_is_blocked_until_that_is_undone(self):
        trip = self.trip("100000")
        bought = self.buy(trip, [self.line(self.karats[21], "10", "60000")])
        scrap = self.query("SELECT COALESCE(SUM(weight_delta_grams),0) AS g FROM old_gold_movements WHERE karat_id=%s", (self.karats[21],))[0]["g"]
        returned = self.service.return_custody(trip["trip_id"], "", [{"karat_id": self.karats[21], "weight": "4", "designation": "LOGO"}], DATE,
                                               "054 return", str(uuid4()))
        self.assertEqual(self.gold(trip), D("6.000"))
        self.assertEqual(self.query("SELECT COALESCE(SUM(weight_delta_grams),0) AS g FROM old_gold_movements WHERE karat_id=%s", (self.karats[21],))[0]["g"],
                         scrap + D("4.000"))                                                  # the purchased grams reach the shop Scrap on the return
        with self.assertRaises(CorrectionError) as blocked:
            self.correct.reverse("MARKET_TRIP_ENTRY", bought["entry_id"], "054 early")
        self.assertEqual(blocked.exception.code, "DEPENDENT_GOLD_USE_EXISTS")
        self.correct.reverse("MARKET_TRIP_ENTRY", returned["entry_id"], "054 undo the return")
        reversal = self.correct.reverse("MARKET_TRIP_ENTRY", bought["entry_id"], "054 undo the purchase")
        self.assertTrue(reversal["correction_id"])
        self.assertEqual((self.gold(trip), self.custody(trip)["cash"]["outstanding"], self.custody(trip)["cash"]["spent"]),
                         (D("0.000"), D("100000.00"), D("0.00")))
        self.assertEqual(self.query("SELECT total_price FROM market_trip_gold_buys WHERE entry_id=%s", (bought["entry_id"],))[0]["total_price"],
                         D("60000.00"))                                                      # the reversed document is never rewritten
        with self.assertRaises(CorrectionError) as again:
            self.correct.reverse("MARKET_TRIP_ENTRY", bought["entry_id"], "054")
        self.assertEqual(again.exception.code, "ALREADY_REVERSED")

    def test_registering_a_typed_vendor_creates_an_active_supplier_party_that_can_then_sell(self):
        trip = self.trip()
        name = "054 بائع جديد " + uuid4().hex[:8]
        created = self.service.register_vendor(name, str(self.karats[21]))
        row = self.query("SELECT name, is_active, is_supplier, is_intermediary, karat_id FROM suppliers WHERE id=%s", (created["id"],))[0]
        self.assertEqual((row["name"], row["is_active"], row["is_supplier"], row["is_intermediary"], row["karat_id"]),
                         (name, 1, 1, 0, self.karats[21]))
        with self.assertRaises(Exception):                                                   # the supplier master keeps names unique
            self.service.register_vendor(name, str(self.karats[21]))
        with self.assertRaises(TripError) as refused:
            self.service.register_vendor("   ", str(self.karats[21]))
        self.assertEqual(refused.exception.code, "VENDOR_REQUIRED")
        done = self.buy(trip, [self.line(self.karats[21], "1", "6000")], party=created["id"])
        head = self.query("SELECT * FROM market_trip_gold_buys WHERE entry_id=%s", (done["entry_id"],))[0]
        self.assertEqual((head["vendor_kind"], head["vendor_party_id"], head["vendor_name"]), ("REGISTERED_PARTY", int(created["id"]), name))

    def test_two_threads_spending_the_same_cash_post_exactly_one_and_two_reversals_decide_once(self):
        trip = self.trip("50000")
        outcomes = []

        def attempt():
            try:
                outcomes.append(("ok", self.buy(trip, [self.line(self.karats[21], "5", "30000")])["entry_id"]))
            except TripError as exc:
                outcomes.append((exc.code, None))
        threads = [threading.Thread(target=attempt) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(60)
        self.assertEqual(sorted(code for code, _ in outcomes), ["PURCHASE_EXCEEDS_CASH", "ok"])
        self.assertEqual(self.custody(trip)["cash"]["outstanding"], D("20000.00"))
        purchase = next(entry for code, entry in outcomes if code == "ok")
        results = []

        def reverse():
            try:
                self.correct.reverse("MARKET_TRIP_ENTRY", purchase, "054 race")
                results.append("reversed")
            except CorrectionError as exc:
                results.append(exc.code)
        threads = [threading.Thread(target=reverse) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(60)
        self.assertEqual(sorted(results), ["ALREADY_REVERSED", "reversed"])
        self.assertEqual(self.query("SELECT COUNT(*) AS n FROM transaction_corrections WHERE source_type='MARKET_TRIP_ENTRY' AND source_id=%s",
                                    (str(purchase),))[0]["n"], 1)

    def test_a_failed_purchase_leaves_no_trace(self):
        trip = self.trip()
        tables = ("market_trip_entries", "market_trip_gold_buys", "market_trip_gold_buy_lines", "audit_log")
        before = self.counts(tables)
        for call in (lambda: self.buy(trip, [self.line(self.karats[21], "0")]), lambda: self.buy(trip, [self.line(self.karats[21], "1", "0")]),
                     lambda: self.buy(trip, name=""), lambda: self.buy(trip, [self.line(self.karats[21], "1", "999999999")], ack=True)):
            with self.assertRaises(TripError):
                call()
        self.assertEqual(self.counts(tables), before)

    # ------------------------------------------------------------ database-constraint checks (raw statements, always rolled back)
    def refuses(self, statement, params=(), errnos=(3819, 1452, 1451, 1364, 4025, 1048, 1265)):
        with self.assertRaises(MySqlError) as raised:
            with self.tx() as c:
                q = c.cursor()
                q.execute(statement, params)
                q.close()
        self.assertIn(raised.exception.errno, errnos, raised.exception)

    def test_the_database_refuses_malformed_purchases_blank_vendors_and_unknown_kinds(self):
        trip = self.trip()
        bought = self.buy(trip)
        entry = bought["entry_id"]
        issue = self.query("SELECT id FROM market_trip_entries WHERE trip_id=%s AND entry_kind='ISSUE'", (trip["trip_id"],))[0]["id"]
        head = ("INSERT INTO market_trip_gold_buys (entry_id, vendor_kind, vendor_party_id, vendor_name, vendor_name_key, total_price, buy_date) "
                "VALUES (%s, %s, %s, %s, %s, %s, '2026-10-17')")
        # the entry must be a GOLD_BUY entry (composite foreign key); the document is unique per entry
        self.refuses(head, (issue, "THIRD_PARTY", None, "x", "x", 1))
        self.refuses(head, (entry, "THIRD_PARTY", None, "x", "x", 1), errnos=(1062,))
        # the vendor is MANDATORY: a blank name or key, a registered vendor without a party, a typed vendor with one, a missing party, a non-positive total
        self.refuses("UPDATE market_trip_gold_buys SET vendor_name='   ' WHERE entry_id=%s", (entry,))
        self.refuses("UPDATE market_trip_gold_buys SET vendor_name_key='' WHERE entry_id=%s", (entry,))
        self.refuses("UPDATE market_trip_gold_buys SET vendor_name=NULL WHERE entry_id=%s", (entry,))
        self.refuses("UPDATE market_trip_gold_buys SET vendor_kind='REGISTERED_PARTY' WHERE entry_id=%s", (entry,))
        self.refuses("UPDATE market_trip_gold_buys SET vendor_party_id=%s WHERE entry_id=%s", (self.supplier, entry))
        self.refuses("UPDATE market_trip_gold_buys SET vendor_kind='REGISTERED_PARTY', vendor_party_id=99999999 WHERE entry_id=%s", (entry,))
        self.refuses("UPDATE market_trip_gold_buys SET total_price=0 WHERE entry_id=%s", (entry,))
        self.refuses("UPDATE market_trip_gold_buys SET deviation_percent=NULL WHERE entry_id=%s", (entry,))             # evidence comes in pairs
        # lines: new gold is MELT or LOGO, positive weight and price, an existing document and karat
        line = ("INSERT INTO market_trip_gold_buy_lines (entry_id, line_no, karat_id, designation, weight_grams, line_price) VALUES (%s, %s, %s, %s, %s, %s)")
        karat = self.karats[21]
        self.refuses(line, (entry, 2, karat, "UNCLASSIFIED", 1, 1))
        self.refuses(line, (entry, 2, karat, "LOGO", 0, 1))
        self.refuses(line, (entry, 2, karat, "LOGO", 1, 0))
        self.refuses(line, (entry, 1, karat, "LOGO", 1, 1), errnos=(1062,))
        self.refuses(line, (entry, 2, 99, "LOGO", 1, 1))
        self.refuses(line, (issue, 2, karat, "LOGO", 1, 1))
        # an unknown entry kind
        self.refuses("UPDATE market_trip_entries SET entry_kind='BUY' WHERE id=%s", (entry,))

    def test_the_migration_is_registered_and_the_objects_exist(self):
        self.assertEqual(self.query("SELECT name FROM schema_migrations WHERE version=54")[0]["name"][:24], "Cash gold purchase for a")
        self.assertIn("GOLD_BUY", self.query("SELECT CHECK_CLAUSE AS c FROM information_schema.CHECK_CONSTRAINTS WHERE CONSTRAINT_SCHEMA=DATABASE() "
                                             "AND CONSTRAINT_NAME='chk_mte_kind'")[0]["c"])
        for name in ("chk_mtgb_vendor", "chk_mtgb_total", "chk_mtgb_warning", "chk_mtgb_kind", "chk_mtgbl_designation", "chk_mtgbl_weight", "chk_mtgbl_price"):
            self.assertEqual(len(self.query("SELECT 1 FROM information_schema.CHECK_CONSTRAINTS WHERE CONSTRAINT_SCHEMA=DATABASE() AND CONSTRAINT_NAME=%s",
                                            (name,))), 1, name)
        self.assertEqual(self.query("SELECT IS_NULLABLE AS n FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='market_trip_gold_buys' "
                                    "AND COLUMN_NAME='vendor_name'")[0]["n"], "NO")


if __name__ == "__main__":
    unittest.main()
