"""Shared opt-in harness of the real-MySQL market-trip tests: it connects to masgold_rehearsal ONLY (target, level and rehearsal marker are asserted before any
write), funds MAIN_SAFE and Scrap once per run and builds sessions / services for the other *_mysql modules. (The retired BOOK mode had its own tests here.)"""
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
from services.authentication_service import AuthenticationResult, _VERIFIED
from services.correction_service import CorrectionError, CorrectionService
from services.gold_position_service import GoldPositionService
from services.market_trip_service import MarketTripService, TripError
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
        service = MarketTripService(session, transaction_factory=cls.tx, price_reference=lambda: dict(REFERENCE),
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




    # ------------------------------------------------------------ database-constraint checks (raw statements, rolled back)





    # ------------------------------------------------------------ races




    # ------------------------------------------------------------ aggregation and snapshot









    # ------------------------------------------------------------ corrections


    # ------------------------------------------------------------ the migration
    def test_the_migration_is_registered_and_the_objects_exist(self):
        self.assertEqual(self.query("SELECT COUNT(*) AS n FROM schema_migrations WHERE version=56")[0]["n"], 1)
        names = {r["TABLE_NAME"] for r in self.query("SELECT TABLE_NAME FROM information_schema.TABLES WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME IN "
                                                      "('market_trip_book_lines','market_trip_book_trades','shop_assayed_stock_movements')")}
        self.assertEqual(names, {"market_trip_book_lines", "market_trip_book_trades", "shop_assayed_stock_movements"})
        self.assertEqual(self.query("SELECT COLUMN_DEFAULT AS d, IS_NULLABLE AS n FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=DATABASE() "
                                    "AND TABLE_NAME='market_trips' AND COLUMN_NAME='accounting_mode'")[0], {"d": "LEGACY", "n": "NO"})
        self.assertEqual(self.query("SELECT COUNT(*) AS n FROM market_trips WHERE accounting_mode NOT IN ('LEGACY','BOOK')")[0]["n"], 0)
        self.assertEqual(self.query("SELECT COUNT(*) AS n FROM market_trip_entries e JOIN market_trips t ON t.id=e.trip_id WHERE e.trip_mode<>t.accounting_mode")[0]["n"], 0)


if __name__ == "__main__":
    unittest.main()
