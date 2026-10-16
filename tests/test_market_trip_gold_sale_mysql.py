"""Explicit opt-in rehearsal checks for the sale of custody gold (migration 053). They connect to masgold_rehearsal ONLY (target, migration level >= 53 and
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
DATE = "2026-10-16"
NEVER_WRITTEN = ("old_gold_movements", "cash_movements", "bank_movements", "supplier_gold_movements", "supplier_making_movements",
                 "supplier_gold_settlements", "supplier_gold_settlement_legs", "supplier_making_payments", "market_trip_lot_shop_movements",
                 "intermediary_holding_movements", "inventory_items", "market_trip_supplier_payments", "market_trip_gold_settlements")
REFERENCE = {21: D("6000.0000"), 24: D("7000.0000")}


@unittest.skipUnless(os.environ.get("MASGOLD_GOLD_SALE_REHEARSAL") == "1", "explicit rehearsal-only opt-in")
class GoldSaleMySqlTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.config = replace(DatabaseConfig.from_env(), database=TARGET)
        with cls.tx() as c:
            q = c.cursor(dictionary=True)
            q.execute("SELECT DATABASE() AS db, MAX(version) AS v FROM schema_migrations")
            row = q.fetchone()
            assert row["db"] == TARGET and int(row["v"]) >= 53, row
            q.execute("SELECT target_database FROM masgold_rehearsal_marker WHERE marker_key='MASGOLD_SCHEMA_REHEARSAL'")
            assert q.fetchone()["target_database"] == TARGET
            q.execute("INSERT INTO employees(name) VALUES('053 rehearsal owner')")
            emp = q.lastrowid
            q.execute("INSERT INTO users(employee_id,username,password_hash,display_name) VALUES(%s,%s,'unusable-rehearsal-hash','053 owner')",
                      (emp, "rehearsal053-" + uuid4().hex))
            cls.user = q.lastrowid
            q.execute("INSERT INTO user_roles(user_id,role_id) SELECT %s,id FROM roles WHERE code='OWNER'", (cls.user,))
            q.execute("SELECT id,karat_value FROM karats")
            cls.karats = {r["karat_value"]: r["id"] for r in q.fetchall()}
            q.execute("INSERT INTO old_gold_movements(movement_type_id,karat_id,weight_delta_grams,created_by_user_id) "
                      "SELECT id,%s,10000,%s FROM old_gold_movement_types WHERE code='OPENING_BALANCE'", (cls.karats[21], cls.user))
            assert q.rowcount == 1
            auth = AuthenticationRepository(c)
            identity, permissions = auth.get_identity(cls.user), auth.effective_permissions(cls.user)
            cls.session = AppSession()
            cls.session._establish(AuthenticationResult(cls.user, emp, identity["display_name"], permissions, _VERIFIED), int(cls.session.generation))
            q.close()
        suppliers = SupplierService(cls.session, transaction_factory=cls.tx)
        cls.supplier = suppliers.create_supplier("053 مورد " + uuid4().hex[:10], karat_id=cls.karats[18])
        cls.intermediary = suppliers.create_supplier("053 مسوقجي " + uuid4().hex[:10], is_supplier=False, is_intermediary=True)
        cls.inactive = suppliers.create_supplier("053 مورد متوقف " + uuid4().hex[:10], karat_id=cls.karats[21])
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

    def trip(self, weight="20", designation="LOGO", cash=""):
        return self.service.issue("", cash, [{"karat_id": self.karats[21], "weight": weight, "designation": designation}], DATE, "053 rehearsal",
                                  str(uuid4()))

    def sell(self, trip, grams="10", total="60000", designation="LOGO", party="", name="تاجر 053", key=None, ack=False, mode="TOTAL"):
        return self.service.sell_gold(trip["trip_id"], "CUSTODY_GOLD", self.karats[21], designation, None, grams, mode, total, party, name, DATE,
                                      "053 ref", "053 notes", key or str(uuid4()), ack)

    def lots(self, weight="10", fineness="875", residues=()):
        trip = self.trip("20", "MELT")
        melt = self.service.melt(trip["trip_id"], [{"karat_id": self.karats[21], "weight": "20"}], DATE, "053 workshop",
                                 {"weight": weight, "fineness": fineness}, [{"weight": w, "fineness": f} for w, f in residues], "053 assay", "", "",
                                 str(uuid4()))
        rows = self.query("SELECT * FROM market_trip_lots WHERE entry_id=%s ORDER BY id", (melt["entry_id"],))
        return trip, melt, rows

    def sell_lot(self, trip, lot, total="61250", key=None, ack=False, grams=""):
        return self.service.sell_gold(trip["trip_id"], "CUSTODY_LOT", None, None, str(lot["id"]), grams, "TOTAL", total, "", "تاجر سبائك", DATE, "", "",
                                      key or str(uuid4()), ack)

    def custody(self, trip):
        return self.service.trip_detail(trip["trip_id"])["custody"]

    def logo(self, trip, designation="LOGO"):
        return next(g for g in self.custody(trip)["gold"] if g["designation"] == designation)["outstanding"]

    # ------------------------------------------------------------ functional checks
    def test_an_aggregate_sale_writes_exactly_the_entry_and_its_document_and_replays_exactly(self):
        trip = self.trip()
        before = self.counts()
        key = str(uuid4())
        done = self.sell(trip, "12.5", "75000", key=key)
        self.assertEqual((done["kind"], done["weight"], done["total_price"], done["buyer_kind"], done["duplicate"]),
                         ("GOLD_SALE", "12.500", "75000.00", "THIRD_PARTY", False))
        self.assertEqual(self.counts(), before)                                              # no money / supplier / Scrap / inventory / lot-ledger row
        row = self.query("SELECT * FROM market_trip_gold_sales WHERE entry_id=%s", (done["entry_id"],))[0]
        self.assertEqual((row["source_kind"], row["karat_id"], row["designation"], row["weight_grams"], row["total_price"], row["price_basis"],
                          row["buyer_name"], row["lot_id"], row["price_warning_acknowledged"]),
                         ("CUSTODY_GOLD", self.karats[21], "LOGO", D("12.500"), D("75000.00"), "TOTAL", "تاجر 053", None, 0))
        self.assertEqual(self.query("SELECT entry_kind FROM market_trip_entries WHERE id=%s", (done["entry_id"],))[0]["entry_kind"], "GOLD_SALE")
        custody = self.custody(trip)
        self.assertEqual((self.logo(trip), custody["cash"]["received"], custody["cash"]["outstanding"]), (D("7.500"), D("75000.00"), D("75000.00")))
        again = self.sell(trip, "12.5", "75000", key=key)
        self.assertEqual((again["duplicate"], again["entry_id"]), (True, done["entry_id"]))
        self.assertEqual(self.query("SELECT COUNT(*) AS n FROM market_trip_gold_sales WHERE entry_id=%s", (done["entry_id"],))[0]["n"], 1)
        with self.assertRaises(TripError) as conflict:
            self.sell(trip, "12.5", "75001", key=key)
        self.assertEqual(conflict.exception.code, "REQUEST_CONFLICT")
        with self.assertRaises(TripError) as over:
            self.sell(trip, "7.501", "1", ack=True)
        self.assertEqual(over.exception.code, "SALE_EXCEEDS_CUSTODY")

    def test_per_gram_pricing_rounds_once_and_the_price_warning_is_stored_as_evidence(self):
        trip = self.trip()
        done = self.sell(trip, "7.333", "6123.4567", mode="PER_GRAM")
        expected = (D("7.333") * D("6123.4567")).quantize(D("0.01"), rounding="ROUND_HALF_UP")
        row = self.query("SELECT * FROM market_trip_gold_sales WHERE entry_id=%s", (done["entry_id"],))[0]
        self.assertEqual((row["total_price"], row["price_per_gram"], row["price_basis"]), (expected, D("6123.4567"), "PER_GRAM"))
        before = self.counts(("market_trip_entries", "market_trip_gold_sales"))
        with self.assertRaises(TripError) as warning:
            self.sell(trip, "10", "40000")                                                   # reference 60,000: -33.33 %
        self.assertEqual(warning.exception.code, "PRICE_DEVIATION")
        self.assertEqual(self.counts(("market_trip_entries", "market_trip_gold_sales")), before)
        ok = self.sell(trip, "10", "40000", ack=True)
        row = self.query("SELECT * FROM market_trip_gold_sales WHERE entry_id=%s", (ok["entry_id"],))[0]
        self.assertEqual((row["price_warning_acknowledged"], row["deviation_percent"], row["reference_price_per_gram"]), (1, D("-33.33"), D("6000.0000")))

    def test_buyers_never_touch_a_supplier_ledger_and_an_inactive_party_is_refused(self):
        trip = self.trip()
        supplier_before = self.query("SELECT COUNT(*) AS n FROM supplier_gold_movements WHERE supplier_id=%s", (self.supplier,))[0]["n"]
        counts = self.counts()
        registered = self.sell(trip, "1", "6000", party=str(self.supplier))
        intermediary = self.sell(trip, "1", "6000", party=str(self.intermediary))
        self.assertEqual((registered["buyer_kind"], registered["buyer_party_id"]), ("REGISTERED_PARTY", str(self.supplier)))
        self.assertEqual((intermediary["buyer_kind"], intermediary["buyer_party_id"]), ("REGISTERED_PARTY", str(self.intermediary)))
        self.assertEqual(self.counts(), counts)
        self.assertEqual(self.query("SELECT COUNT(*) AS n FROM supplier_gold_movements WHERE supplier_id=%s", (self.supplier,))[0]["n"], supplier_before)
        before = self.counts(("market_trip_entries", "market_trip_gold_sales"))
        for party, code in ((str(self.inactive), "BUYER_INACTIVE"), ("999999999", "BUYER_NOT_FOUND")):
            with self.assertRaises(TripError) as refused:
                self.sell(trip, "1", "6000", party=party)
            self.assertEqual(refused.exception.code, code)
        self.assertEqual(self.counts(("market_trip_entries", "market_trip_gold_sales")), before)
        self.assertEqual(self.query("SELECT is_supplier, is_intermediary FROM suppliers WHERE id=%s", (self.intermediary,))[0],
                         {"is_supplier": 0, "is_intermediary": 1})                          # a sale grants no role

    def test_a_whole_lot_is_sold_through_a_sale_out_movement_and_reversal_restores_the_same_lot(self):
        trip, melt, lots = self.lots(residues=[("2", "")])
        main, unassayed = lots[0], lots[1]
        for lot, code in ((unassayed, "LOT_ASSAY_UNKNOWN"),):
            with self.assertRaises(TripError) as refused:
                self.sell_lot(trip, lot)
            self.assertEqual(refused.exception.code, code)
        with self.assertRaises(TripError) as partial:
            self.sell_lot(trip, main, grams="5")
        self.assertEqual(partial.exception.code, "LOT_PARTIAL_NOT_ALLOWED")
        counts = self.counts()
        done = self.sell_lot(trip, main)
        self.assertEqual(self.counts(), counts)
        move = self.query("SELECT * FROM market_trip_lot_movements WHERE entry_id=%s", (done["entry_id"],))[0]
        self.assertEqual((move["movement_type"], move["weight_delta"], move["lot_id"]), ("SALE_OUT", -main["weight_grams"], main["id"]))
        row = self.query("SELECT * FROM market_trip_gold_sales WHERE entry_id=%s", (done["entry_id"],))[0]
        self.assertEqual((row["source_kind"], row["lot_id"], row["custody_movement_id"], row["fineness_permille"], row["karat_id"]),
                         ("CUSTODY_LOT", main["id"], move["id"], main["fineness_permille"], None))
        self.assertNotIn(str(main["id"]), [l["lot_id"] for l in self.custody(trip)["lots"]])
        with self.assertRaises(TripError) as gone:
            self.sell_lot(trip, main)
        self.assertEqual(gone.exception.code, "LOT_NOT_IN_CUSTODY")
        with self.assertRaises(CorrectionError):                                             # a sold lot protects its melt
            self.correct.reverse("MARKET_TRIP_ENTRY", melt["entry_id"], "053 melt")
        reversal = self.correct.reverse("MARKET_TRIP_ENTRY", done["entry_id"], "053 مستوفى بالخطأ")
        self.assertTrue(reversal["correction_id"])
        self.assertIn(str(main["id"]), [l["lot_id"] for l in self.custody(trip)["lots"]])
        self.assertEqual(self.custody(trip)["cash"]["received"], D("0.00"))
        self.sell_lot(trip, main)                                                            # a fresh key may sell the restored lot
        self.assertEqual(self.query("SELECT total_price FROM market_trip_gold_sales WHERE entry_id=%s", (done["entry_id"],))[0]["total_price"],
                         D("61250.00"))                                                      # the reversed document is never rewritten

    def test_reversal_is_blocked_after_the_proceeds_were_returned_and_allowed_once_that_is_reversed(self):
        trip = self.trip()
        sold = self.sell(trip, "20", "120000")
        returned = self.service.return_custody(trip["trip_id"], "100000", [], DATE, "053 return", str(uuid4()))
        with self.assertRaises(CorrectionError) as blocked:
            self.correct.reverse("MARKET_TRIP_ENTRY", sold["entry_id"], "053")
        self.assertEqual(blocked.exception.code, "DEPENDENT_CASH_USE_EXISTS")
        self.correct.reverse("MARKET_TRIP_ENTRY", returned["entry_id"], "053 undo the return")
        self.correct.reverse("MARKET_TRIP_ENTRY", sold["entry_id"], "053 undo the sale")
        self.assertEqual((self.logo(trip), self.custody(trip)["cash"]["outstanding"]), (D("20.000"), D("0.00")))
        with self.assertRaises(CorrectionError) as again:
            self.correct.reverse("MARKET_TRIP_ENTRY", sold["entry_id"], "053")
        self.assertEqual(again.exception.code, "ALREADY_REVERSED")

    def test_two_threads_selling_the_same_line_post_exactly_one_and_two_reversals_decide_once(self):
        trip = self.trip("10")
        outcomes = []

        def attempt():
            try:
                outcomes.append(("ok", self.sell(trip, "6", "36000")["entry_id"]))
            except TripError as exc:
                outcomes.append((exc.code, None))
        threads = [threading.Thread(target=attempt) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(60)
        self.assertEqual(sorted(code for code, _ in outcomes), ["SALE_EXCEEDS_CUSTODY", "ok"])
        self.assertEqual(self.logo(trip), D("4.000"))
        sale_id = next(entry for code, entry in outcomes if code == "ok")
        results = []

        def reverse():
            try:
                self.correct.reverse("MARKET_TRIP_ENTRY", sale_id, "053 race")
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
                                    (str(sale_id),))[0]["n"], 1)

    def test_a_failed_sale_leaves_no_trace(self):
        trip = self.trip()
        before = self.counts(("market_trip_entries", "market_trip_gold_sales", "market_trip_lot_movements", "audit_log"))
        for call in (lambda: self.sell(trip, "0", "1"), lambda: self.sell(trip, "1", "0"), lambda: self.sell(trip, "1", "6000", name=""),
                     lambda: self.sell(trip, "999", "1", ack=True)):
            with self.assertRaises(TripError):
                call()
        self.assertEqual(self.counts(("market_trip_entries", "market_trip_gold_sales", "market_trip_lot_movements", "audit_log")), before)

    # ------------------------------------------------------------ database-constraint checks (raw statements, always rolled back)
    def refuses(self, statement, params=(), errnos=(3819, 1452, 1451, 1364, 4025, 1048, 1265)):
        with self.assertRaises(MySqlError) as raised:
            with self.tx() as c:
                q = c.cursor()
                q.execute(statement, params)
                q.close()
        self.assertIn(raised.exception.errno, errnos, raised.exception)

    def test_the_database_refuses_malformed_sales_and_unknown_kinds(self):
        trip = self.trip()
        sold = self.sell(trip, "1", "6000")
        issue = self.query("SELECT id FROM market_trip_entries WHERE trip_id=%s AND entry_kind='ISSUE'", (trip["trip_id"],))[0]["id"]
        base = ("INSERT INTO market_trip_gold_sales (entry_id, source_kind, karat_id, designation, weight_grams, total_price, price_basis, buyer_kind, "
                "buyer_party_id, buyer_name, sale_date) VALUES (%s, 'CUSTODY_GOLD', %s, 'LOGO', 1, %s, %s, %s, %s, 'x', '2026-10-16')")
        karat = self.karats[21]
        # the entry must be a GOLD_SALE entry (composite foreign key), the document is unique per entry
        self.refuses(base % ("%s", "%s", "%s", "'TOTAL'", "'THIRD_PARTY'", "NULL"), (issue, karat, 1))
        self.refuses(base % ("%s", "%s", "%s", "'TOTAL'", "'THIRD_PARTY'", "NULL"), (sold["entry_id"], karat, 1), errnos=(1062,))
        # shape rules: a registered buyer needs a party, a third party must not have one, the price is positive and its basis consistent
        self.refuses(base % ("%s", "%s", "%s", "'TOTAL'", "'REGISTERED_PARTY'", "NULL"), (sold["entry_id"], karat, 1))
        self.refuses(base % ("%s", "%s", "%s", "'TOTAL'", "'THIRD_PARTY'", str(self.supplier)), (sold["entry_id"], karat, 1))
        self.refuses(base % ("%s", "%s", "%s", "'TOTAL'", "'THIRD_PARTY'", "NULL"), (sold["entry_id"], karat, 0))
        self.refuses(base % ("%s", "%s", "%s", "'PER_GRAM'", "'THIRD_PARTY'", "NULL"), (sold["entry_id"], karat, 1))
        self.refuses("UPDATE market_trip_gold_sales SET buyer_kind='REGISTERED_PARTY', buyer_party_id=99999999 WHERE entry_id=%s", (sold["entry_id"],))
        # an unknown entry kind
        self.refuses("UPDATE market_trip_entries SET entry_kind='SALE' WHERE id=%s", (sold["entry_id"],))

    def test_the_migration_is_registered_and_the_objects_exist(self):
        self.assertEqual(self.query("SELECT name FROM schema_migrations WHERE version=53")[0]["name"][:21], "Sale of custody gold:")
        clause = self.query("SELECT CHECK_CLAUSE AS c FROM information_schema.CHECK_CONSTRAINTS WHERE CONSTRAINT_SCHEMA=DATABASE() AND CONSTRAINT_NAME='chk_mte_kind'")[0]["c"]
        self.assertIn("GOLD_SALE", clause)
        for name in ("chk_mtgsale_source", "chk_mtgsale_buyer", "chk_mtgsale_price", "chk_mtgsale_warning", "chk_mtgsale_kind"):
            self.assertEqual(len(self.query("SELECT 1 FROM information_schema.CHECK_CONSTRAINTS WHERE CONSTRAINT_SCHEMA=DATABASE() AND CONSTRAINT_NAME=%s",
                                            (name,))), 1, name)
        self.assertIn("SALE_OUT", self.query("SELECT CHECK_CLAUSE AS c FROM information_schema.CHECK_CONSTRAINTS WHERE CONSTRAINT_SCHEMA=DATABASE() "
                                             "AND CONSTRAINT_NAME='chk_mtlm_type'")[0]["c"])


if __name__ == "__main__":
    unittest.main()
