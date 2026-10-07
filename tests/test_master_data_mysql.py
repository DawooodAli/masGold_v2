"""Opt-in DEV validation of Master Data permanent delete on the real schema (masgold_v2 only).

    set MASGOLD_MASTER_DATA_DEV=1

Creates clearly tagged temporary records (and one tagged SOLD inventory fixture), proves the service
deletes unused ones, refuses used ones without touching stock, and serializes against a concurrent
stock insert. Only these temporary fixtures are removed afterwards; existing data is never touched."""
import os
import threading
import time
import unittest
from uuid import uuid4

from config.database_config import DatabaseConfig
from database.connection import DatabaseConnection
from services.master_data_service import MasterDataInUse, MasterDataService

TAG = "MASTER DATA DEV TEST FIXTURE"


@unittest.skipUnless(os.environ.get("MASGOLD_MASTER_DATA_DEV") == "1", "opt-in DEV database test")
class MasterDataMySqlTests(unittest.TestCase):
    def setUp(self):
        config = DatabaseConfig.from_env()
        self.assertEqual(config.database, "masgold_v2")
        self.connection = DatabaseConnection(config).connect()
        self.connection.autocommit = True
        self.cursor = self.connection.cursor(dictionary=True)
        self.cursor.execute("SELECT DATABASE() AS db, MAX(version) AS v FROM schema_migrations")
        row = self.cursor.fetchone()
        self.assertEqual((row["db"], int(row["v"])), ("masgold_v2", 34))
        self.suffix = uuid4().hex[:6].upper()
        self.service = MasterDataService(self.owner_session())    # real AuthorizationService, real grants
        self.inventory_ids, self.masters = [], []
        self.addCleanup(self.cleanup)

    def owner_session(self):
        from app.session import AppSession
        from repositories.authentication_repository import AuthenticationRepository
        from services.authentication_service import AuthenticationResult, _VERIFIED
        row = self.one("SELECT u.id FROM users u JOIN user_roles ur ON ur.user_id=u.id JOIN roles r ON r.id=ur.role_id "
                       "WHERE r.code='OWNER' AND u.is_active=1 ORDER BY u.id LIMIT 1")
        auth = AuthenticationRepository(self.connection)
        identity = auth.get_identity(row["id"])
        session = AppSession()
        session._establish(AuthenticationResult(row["id"], identity["employee_id"], identity["display_name"],
                                                auth.effective_permissions(row["id"]), _VERIFIED),
                           int(session.generation))
        return session

    def one(self, sql, params=()):
        self.cursor.execute(sql, params)
        return self.cursor.fetchone()

    def cleanup(self):
        for item_id in self.inventory_ids:                   # only this test's tagged fixture rows
            self.cursor.execute("DELETE FROM inventory_items WHERE id = %s AND notes = %s", (item_id, TAG))
        for table, row_id in reversed(self.masters):
            self.cursor.execute(f"DELETE FROM {table} WHERE id = %s", (row_id,))
        self.connection.close()

    def temp(self):
        factory = self.service.create_factory("DELETE TEST FACTORY " + self.suffix, "DTF" + self.suffix, TAG)
        item_type = self.service.create_item_type("DELETE TEST TYPE " + self.suffix, "DTT" + self.suffix, None, TAG)
        category = self.service.create_category("DT_" + self.suffix, "DELETE_TEST", notes=TAG)
        self.masters += [("factories", factory), ("item_types", item_type), ("item_categories", category)]
        return factory, item_type, category

    def stock(self, factory, item_type, category, status="SOLD", connection=None):
        cursor = (connection or self.connection).cursor()
        cursor.execute(
            "INSERT INTO inventory_items (item_type_id, karat_id, category_id, factory_id, weight_grams, "
            "inventory_status, acquisition_source, notes) "
            "VALUES (%s, (SELECT id FROM karats WHERE karat_value = 21), %s, %s, 1.000, %s, 'MANUAL_ENTRY', %s)",
            (item_type, category, factory, status, TAG))
        self.inventory_ids.append(cursor.lastrowid)
        return cursor.lastrowid

    def test_unused_records_are_deleted(self):
        factory, item_type, category = self.temp()
        self.service.delete_factory(factory)
        self.service.delete_item_type(item_type)
        self.service.delete_category(category)
        for table, row_id in (("factories", factory), ("item_types", item_type), ("item_categories", category)):
            self.assertIsNone(self.one(f"SELECT id FROM {table} WHERE id = %s", (row_id,)), table)
        self.masters.clear()

    def test_sold_stock_blocks_every_delete_and_stays_intact(self):
        factory, item_type, category = self.temp()
        item = self.stock(factory, item_type, category, "SOLD")
        before = self.one("SELECT * FROM inventory_items WHERE id = %s", (item,))
        for delete in (self.service.delete_factory, self.service.delete_item_type, self.service.delete_category):
            with self.subTest(delete.__name__), self.assertRaises(MasterDataInUse):
                delete({"delete_factory": factory, "delete_item_type": item_type,
                        "delete_category": category}[delete.__name__])
        self.assertEqual(self.one("SELECT * FROM inventory_items WHERE id = %s", (item,)), before)  # not nulled
        for table, row_id in (("factories", factory), ("item_types", item_type), ("item_categories", category)):
            self.assertIsNotNone(self.one(f"SELECT id FROM {table} WHERE id = %s", (row_id,)), table)

    def test_concurrent_stock_insert_is_seen_by_the_delete(self):
        factory, item_type, category = self.temp()
        other = DatabaseConnection(DatabaseConfig.from_env()).connect()
        self.addCleanup(other.close)
        other.start_transaction()
        self.stock(factory, item_type, category, "IN_STOCK", connection=other)   # uncommitted
        outcome = {}

        def delete():
            try:
                self.service.delete_factory(factory)
                outcome["result"] = "deleted"
            except MasterDataInUse:
                outcome["result"] = "refused"
            except Exception as exc:                          # noqa: BLE001 - reported below
                outcome["result"] = repr(exc)

        worker = threading.Thread(target=delete)
        worker.start()
        time.sleep(1.0)
        self.assertTrue(worker.is_alive())                    # waits on the row lock the insert holds
        other.commit()
        worker.join(30)
        self.assertEqual(outcome.get("result"), "refused")
        self.assertIsNotNone(self.one("SELECT id FROM factories WHERE id = %s", (factory,)))

    def test_direct_sql_delete_of_a_used_record_is_refused_by_mysql(self):
        """No service: the database itself refuses (029 RESTRICT) and the stock row keeps its references."""
        from mysql.connector import IntegrityError
        factory, item_type, category = self.temp()
        item = self.stock(factory, item_type, category, "SOLD")
        for table, row_id in (("factories", factory), ("item_categories", category), ("item_types", item_type)):
            with self.subTest(table), self.assertRaises(IntegrityError) as caught:
                self.cursor.execute(f"DELETE FROM {table} WHERE id = %s", (row_id,))
            self.assertEqual(caught.exception.errno, 1451)
        row = self.one("SELECT factory_id, item_type_id, category_id FROM inventory_items WHERE id = %s", (item,))
        self.assertEqual((row["factory_id"], row["item_type_id"], row["category_id"]), (factory, item_type, category))

    def test_direct_sql_delete_is_refused_for_every_restrictive_reference(self):
        """An item type's default category and an old-gold movement's factory (029: narrowed + indexed +
        RESTRICT). The old-gold rows exist only inside a transaction that is rolled back."""
        from mysql.connector import IntegrityError
        factory, item_type, category = self.temp()
        self.cursor.execute("UPDATE item_types SET default_category_id = %s WHERE id = %s", (category, item_type))
        with self.assertRaises(IntegrityError) as caught:
            self.cursor.execute("DELETE FROM item_categories WHERE id = %s", (category,))
        self.assertEqual(caught.exception.errno, 1451)
        self.assertEqual(self.one("SELECT default_category_id AS c FROM item_types WHERE id = %s", (item_type,))["c"],
                         category)                                   # not silently cleared
        self.cursor.execute("UPDATE item_types SET default_category_id = NULL WHERE id = %s", (item_type,))
        other = DatabaseConnection(DatabaseConfig.from_env()).connect()
        self.addCleanup(other.close)
        cursor = other.cursor()
        other.start_transaction()
        try:
            movement = ("INSERT INTO old_gold_movements (movement_type_id, karat_id, factory_id, weight_delta_grams) "
                        "VALUES ((SELECT id FROM old_gold_movement_types WHERE code = 'CUSTOMER_BUY_IN'), "
                        "(SELECT id FROM karats WHERE karat_value = 21), %s, 1.000)")
            cursor.execute(movement, (factory,))
            with self.assertRaises(IntegrityError) as caught:
                cursor.execute("DELETE FROM factories WHERE id = %s", (factory,))
            self.assertEqual(caught.exception.errno, 1451)
            with self.assertRaises(IntegrityError) as caught:        # an unknown factory cannot be stored either
                cursor.execute(movement, (65000,))
            self.assertEqual(caught.exception.errno, 1452)
        finally:
            other.rollback()
        self.assertIsNone(self.one("SELECT id FROM old_gold_movements WHERE factory_id = %s", (factory,)))
    def test_delete_first_then_a_stock_insert_cannot_create_a_broken_reference(self):
        """The delete holds the unused factory; a concurrent stock insert waits and then fails its FK."""
        from mysql.connector import IntegrityError
        factory, item_type, category = self.temp()
        deleter = DatabaseConnection(DatabaseConfig.from_env()).connect()
        self.addCleanup(deleter.close)
        deleter.start_transaction()
        cursor = deleter.cursor()
        cursor.execute("SELECT id FROM factories WHERE id = %s FOR UPDATE", (factory,))
        cursor.fetchall()
        cursor.execute("DELETE FROM factories WHERE id = %s", (factory,))
        outcome = {}

        def insert():
            inserter = DatabaseConnection(DatabaseConfig.from_env()).connect()
            try:
                self.stock(factory, item_type, category, "IN_STOCK", connection=inserter)
                inserter.commit()
                outcome["result"] = "inserted"
            except IntegrityError as exc:
                outcome["result"] = exc.errno
            finally:
                inserter.close()

        worker = threading.Thread(target=insert)
        worker.start()
        time.sleep(1.0)
        self.assertTrue(worker.is_alive())                          # the FK check waits on the locked parent
        deleter.commit()
        worker.join(30)
        self.assertEqual(outcome.get("result"), 1452)               # no stock row points at a missing factory
        self.masters.remove(("factories", factory))
        self.assertIsNone(self.one("SELECT id FROM inventory_items WHERE factory_id = %s", (factory,)))


if __name__ == "__main__":
    unittest.main()
