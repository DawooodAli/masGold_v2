"""Opt-in DEV validation of Purchasing & Suppliers V1 on the real schema (masgold_v2 at migration 035, after the OWNER approves it).

    set MASGOLD_SUPPLIER_DEV=1

Requires the OWNER-approved application of migration 034 to the DEV database. Everything runs inside ONE
transaction that is rolled back at the end, so nothing persists (ledger rows are immutable by design and are never
cleaned up with DELETE). It proves on real MySQL: Decimal precision (3 / 6 / 2 places), the unique ledger source
keys, the sign / amount CHECKs, idempotent posting, the canonical inventory + barcode path with the group's
original making rate, the composite group foreign key and informational weight variance."""
from contextlib import contextmanager
from decimal import Decimal as D
import os
import unittest
from uuid import uuid4

from config.database_config import DatabaseConfig
from database.connection import DatabaseConnection

KEYS = "aaaaaaaa-aaaa-4aaa-8aaa-%012d"
PERMISSIONS = {"SUPPLIER_VIEW", "SUPPLIER_MANAGE", "SUPPLIER_INVOICE_EDIT", "SUPPLIER_INVOICE_POST", "SUPPLIER_INTAKE"}


@unittest.skipUnless(os.environ.get("MASGOLD_SUPPLIER_DEV") == "1", "opt-in DEV database test")
class SupplierMySqlTests(unittest.TestCase):
    def setUp(self):
        config = DatabaseConfig.from_env()
        self.assertEqual(config.database, "masgold_v2")
        self.connection = DatabaseConnection(config).connect()
        self.cursor = self.connection.cursor(dictionary=True)
        self.cursor.execute("SELECT DATABASE() AS db, MAX(version) AS v FROM schema_migrations")
        row = self.cursor.fetchone()
        self.assertEqual((row["db"], int(row["v"])), ("masgold_v2", 41))
        self.assertFalse(self.connection.autocommit)           # every statement below belongs to the ONE test transaction
        self.connection.rollback()                              # end the read above; nothing is ever committed
        self.addCleanup(self.cleanup)
        self.counter = 0
        from app.session import AppSession
        from repositories.authentication_repository import AuthenticationRepository
        from services.authentication_service import AuthenticationResult, _VERIFIED
        from services.supplier_purchase_service import SupplierPurchaseService
        from services.supplier_service import SupplierService
        owner = self.one("SELECT u.id FROM users u JOIN user_roles ur ON ur.user_id=u.id JOIN roles r ON r.id=ur.role_id "
                         "WHERE r.code='OWNER' AND u.is_active=1 AND u.must_change_password=0 ORDER BY u.id LIMIT 1")
        auth = AuthenticationRepository(self.connection)
        identity = auth.get_identity(owner["id"])
        self.permissions = auth.effective_permissions(owner["id"])
        session = AppSession()
        session._establish(AuthenticationResult(owner["id"], identity["employee_id"], identity["display_name"],
                                                self.permissions, _VERIFIED), int(session.generation))
        self.owner_id = owner["id"]
        self.suppliers = SupplierService(session, transaction_factory=self.shared)
        self.purchases = SupplierPurchaseService(session, transaction_factory=self.shared)

    def cleanup(self):
        self.connection.rollback()
        self.connection.close()

    @contextmanager
    def shared(self):
        """One service call = one savepoint of the test transaction (a failed call rolls back only itself)."""
        cursor = self.connection.cursor()
        cursor.execute("SAVEPOINT supplier_call")
        try:
            yield self.connection
        except BaseException:
            cursor.execute("ROLLBACK TO SAVEPOINT supplier_call")
            raise
        finally:
            cursor.close()

    def one(self, sql, params=()):
        self.cursor.execute(sql, params)
        return self.cursor.fetchone()

    def all(self, sql, params=()):
        self.cursor.execute(sql, params)
        return self.cursor.fetchall()

    def key(self):
        self.counter += 1
        return KEYS % (uuid4().int % 10 ** 11 * 10 + self.counter)

    def invoice(self, karat_id, groups, supplier_id):
        draft = self.purchases.create_draft(supplier_id, karat_id, "DEV-" + uuid4().hex[:6], "2026-10-08", groups, "",
                                            self.key())
        return draft, self.purchases.post_invoice(draft["id"], self.key())

    def karat(self, value):
        return self.one("SELECT id FROM karats WHERE karat_value=%s", (value,))["id"]

    def test_the_owner_holds_the_five_explicit_supplier_rights(self):
        self.assertTrue(PERMISSIONS <= set(self.permissions))

    def test_posting_persists_exact_decimals_and_is_idempotent(self):
        supplier = self.suppliers.create_supplier("DEV SUPPLIER " + uuid4().hex[:6], karat_id=self.karat(21))
        groups = [{"weight": "40", "making_per_gram": "120"}, {"weight": "35", "making_per_gram": "180"},
                  {"weight": "25", "making_per_gram": "150"}]
        draft, posted = self.invoice(self.karat(21), groups, supplier)
        gold = self.one("SELECT * FROM supplier_gold_movements WHERE source_id=%s", (draft["id"],))
        making = self.one("SELECT * FROM supplier_making_movements WHERE source_id=%s", (draft["id"],))
        self.assertEqual((gold["weight_delta_grams"], gold["equivalent_21_delta_grams"], making["amount_delta"]),
                         (D("100.000"), D("100.000000"), D("14850.00")))
        post_key = self.one("SELECT post_request_key AS k FROM supplier_purchase_invoices WHERE id=%s",
                            (draft["id"],))["k"]
        self.assertTrue(self.purchases.post_invoice(draft["id"], post_key)["duplicate"])      # a retry creates nothing
        self.assertEqual(self.one("SELECT COUNT(*) AS n FROM supplier_gold_movements WHERE source_id=%s",
                                  (draft["id"],))["n"], 1)
        supplier18 = self.suppliers.create_supplier("DEV SUPPLIER " + uuid4().hex[:6], karat_id=self.karat(18))
        eighteen, _ = self.invoice(self.karat(18), [{"weight": "100", "making_per_gram": "10"}], supplier18)
        row = self.one("SELECT weight_delta_grams, equivalent_21_delta_grams FROM supplier_gold_movements "
                       "WHERE source_id=%s", (eighteen["id"],))
        self.assertEqual((row["weight_delta_grams"], row["equivalent_21_delta_grams"]), (D("100.000"), D("85.714286")))

    def test_database_constraints_protect_the_ledgers(self):
        supplier = self.suppliers.create_supplier("DEV SUPPLIER " + uuid4().hex[:6], karat_id=self.karat(21))
        draft, _ = self.invoice(self.karat(21), [{"weight": "10", "making_per_gram": "5"}], supplier)
        type_id = self.one("SELECT id FROM supplier_gold_movement_types WHERE code='PURCHASE_INVOICE'")["id"]
        cursor = self.connection.cursor()
        for sql, errno in (
                ("INSERT INTO supplier_gold_movements (supplier_id, movement_type_id, karat_id, weight_delta_grams, "
                 "equivalent_21_delta_grams, source_type, source_id, occurred_at, created_by_user_id) "
                 "VALUES (%s, %s, %s, 1, 1, 'SUPPLIER_PURCHASE_INVOICE', %s, NOW(), %s)", 1062),     # unique source key
                ("INSERT INTO supplier_gold_movements (supplier_id, movement_type_id, karat_id, weight_delta_grams, "
                 "equivalent_21_delta_grams, source_type, source_id, occurred_at, created_by_user_id) "
                 "VALUES (%s, %s, %s, 1, -1, 'X', %s, NOW(), %s)", 3819)):                           # sign CHECK
            cursor.execute("SAVEPOINT constraint_probe")
            with self.assertRaises(Exception) as refused:
                cursor.execute(sql, (supplier, type_id, self.karat(21), draft["id"], self.owner_id))
            self.assertEqual(getattr(refused.exception, "errno", None), errno)
            cursor.execute("ROLLBACK TO SAVEPOINT constraint_probe")
        cursor.close()

    def test_intake_uses_the_canonical_inventory_and_barcode_path_and_variance_is_informational(self):
        supplier = self.suppliers.create_supplier("DEV SUPPLIER " + uuid4().hex[:6], karat_id=self.karat(21))
        draft, _ = self.invoice(self.karat(21), [{"weight": "50", "making_per_gram": "120"},
                                                 {"weight": "50", "making_per_gram": "180"}], supplier)
        group_ids = [str(g["id"]) for g in self.all("SELECT id FROM supplier_invoice_groups WHERE invoice_id=%s "
                                                   "ORDER BY group_no", (draft["id"],))]
        item_type = self.one("SELECT id FROM item_types WHERE is_active=1 ORDER BY id LIMIT 1")["id"]
        balances_before = self.suppliers.supplier_detail(supplier)["balances"]
        piece = self.purchases.add_piece(draft["id"], group_ids[0], item_type, "49.920", self.key())
        item = self.one("SELECT * FROM inventory_items WHERE id=%s", (piece["inventory_item_id"],))
        self.assertEqual((item["acquisition_source"], item["stock_classification"], item["inventory_status"],
                          item["acquisition_making_per_gram"], item["weight_grams"], item["karat_id"]),
                         ("SUPPLIER_PURCHASE", "NEW_STOCK", "IN_STOCK", D("120.00"), D("49.920"), self.karat(21)))
        from repositories.inventory_repository import InventoryRepository
        listed = InventoryRepository(self.connection).get_stock_items(search=piece["barcode"], limit=5, karat_id=None,
                                                                      item_type_id=None)          # Inventory reads it back
        self.assertEqual([(row["inventory_item_id"], row["acquisition_source"]) for row in listed],
                         [(item["id"], "SUPPLIER_PURCHASE")])
        barcode = self.one("SELECT * FROM inventory_barcodes WHERE inventory_item_id=%s", (item["id"],))
        self.assertEqual((barcode["barcode_status"], barcode["barcode_value"]), ("ACTIVE", piece["barcode"]))
        self.assertTrue(self.one("SELECT 1 AS ok FROM inventory_movements m JOIN inventory_movement_types t "
                                 "ON t.id=m.movement_type_id WHERE m.inventory_item_id=%s AND t.code='ITEM_CREATED'",
                                 (item["id"],)))
        detail = self.purchases.invoice_detail(draft["id"])
        self.assertEqual((detail["groups"][0]["variance"], detail["intake"]["variance"]), (D("-0.080"), D("-50.080")))
        self.assertEqual(self.suppliers.supplier_detail(supplier)["balances"], balances_before)   # variance is information
        origin = self.purchases.piece_origin(piece["inventory_item_id"])
        self.assertEqual((origin["invoice_id"], origin["group_no"], origin["making_per_gram"]),
                         (draft["id"], 1, D("120.00")))
        # The composite foreign key refuses a group that belongs to another invoice.
        other, _ = self.invoice(self.karat(21), [{"weight": "5", "making_per_gram": "1"}], supplier)
        other_group = self.one("SELECT id FROM supplier_invoice_groups WHERE invoice_id=%s", (other["id"],))["id"]
        unlinked = self.one("SELECT id FROM inventory_items WHERE id <> %s ORDER BY id LIMIT 1", (item["id"],))["id"]  # read only
        cursor = self.connection.cursor()
        cursor.execute("SAVEPOINT composite_fk")
        with self.assertRaises(Exception) as refused:
            cursor.execute("INSERT INTO supplier_invoice_pieces (inventory_item_id, invoice_id, group_id, making_per_gram,"
                           " request_key, created_by_user_id) VALUES (%s, %s, %s, 1, %s, %s)",
                           (unlinked, draft["id"], other_group, self.key(), self.owner_id))
        self.assertEqual(getattr(refused.exception, "errno", None), 1452)
        cursor.execute("ROLLBACK TO SAVEPOINT composite_fk")
        cursor.close()


if __name__ == "__main__":
    unittest.main()
