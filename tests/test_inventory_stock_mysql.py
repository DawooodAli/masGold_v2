"""Opt-in DEV proof of the بيان المخزون / الجرد بالباركود SQL on the real schema (masgold_v2 only).

    set MASGOLD_INVENTORY_DEV=1

Adds clearly tagged temporary pieces in several statuses next to the owner's real stock, measures
the DIFFERENCE they make (the owner's own items are never touched), then removes only those rows."""
from decimal import Decimal as D
import os
import unittest
from uuid import uuid4

from config.database_config import DatabaseConfig
from database.connection import DatabaseConnection
from services.inventory_service import InventoryService

TAG = "INVENTORY STOCK DEV TEST FIXTURE"


@unittest.skipUnless(os.environ.get("MASGOLD_INVENTORY_DEV") == "1", "opt-in DEV database test")
class InventoryStockMySqlTests(unittest.TestCase):
    def setUp(self):
        config = DatabaseConfig.from_env()
        self.assertEqual(config.database, "masgold_v2")
        self.connection = DatabaseConnection(config).connect()
        self.connection.autocommit = True
        self.cursor = self.connection.cursor(dictionary=True)
        self.cursor.execute("SELECT DATABASE() AS db, MAX(version) AS v FROM schema_migrations")
        row = self.cursor.fetchone()
        self.assertEqual((row["db"], int(row["v"])), ("masgold_v2", 36))
        self.suffix = uuid4().hex[:6].upper()
        self.ids = []
        self.addCleanup(self.cleanup)
        self.service = InventoryService()
        self.cursor.execute("SELECT id FROM karats WHERE karat_value = 21")
        self.k21 = self.cursor.fetchone()["id"]
        self.cursor.execute("SELECT id FROM karats WHERE karat_value = 24")
        self.k24 = self.cursor.fetchone()["id"]
        self.cursor.execute("INSERT INTO item_types (code, name, notes) VALUES (%s, %s, %s)",
                            ("ST" + self.suffix, "صنف اختبار " + self.suffix, TAG))
        self.item_type = self.cursor.lastrowid

    def cleanup(self):
        for item_id in self.ids:
            self.cursor.execute("DELETE FROM inventory_barcodes WHERE inventory_item_id = %s", (item_id,))
            self.cursor.execute("DELETE FROM inventory_items WHERE id = %s AND notes = %s", (item_id, TAG))
        self.cursor.execute("DELETE FROM item_types WHERE id = %s AND notes = %s", (self.item_type, TAG))
        self.connection.close()

    def add(self, karat, weight, status="IN_STOCK", stock_class="NEW_STOCK", barcode=True):
        self.cursor.execute(
            "INSERT INTO inventory_items (item_type_id, karat_id, weight_grams, stock_classification, "
            "inventory_status, acquisition_source, notes) VALUES (%s, %s, %s, %s, %s, 'MANUAL_ENTRY', %s)",
            (self.item_type, karat, weight, stock_class, status, TAG))
        item_id = self.cursor.lastrowid
        self.ids.append(item_id)
        if barcode:
            value = f"T{self.suffix}{len(self.ids):02d}"
            self.cursor.execute("INSERT INTO inventory_barcodes (inventory_item_id, barcode_value, barcode_status) "
                                "VALUES (%s, %s, 'ACTIVE')", (item_id, value))
            return value
        return None

    def mine(self, balance):
        rows = [r for k in balance["karats"] for r in k["item_types"] if r["item_type_id"] == self.item_type]
        return {(next(k["karat_value"] for k in balance["karats"] if r in k["item_types"])):
                (r["piece_count"], r["weight_grams"]) for r in rows}

    def test_balance_counts_only_current_stock_with_exact_decimal(self):
        before = self.service.get_stock_balance()
        self.add(self.k21, "3.333")
        self.add(self.k21, "1.001", stock_class="OLD_STOCK")
        self.add(self.k24, "10.010")
        for status in ("SOLD", "RETURNED_PENDING", "CONVERTED_TO_OLD_GOLD", "REMOVED", "SCRAPPED"):
            self.add(self.k21, "99.999", status=status)
        after = self.service.get_stock_balance()
        self.assertEqual(self.mine(after), {21: (2, D("4.334")), 24: (1, D("10.010"))})
        self.assertEqual(after["piece_count"] - before["piece_count"], 3)
        self.assertEqual(after["weight_grams"] - before["weight_grams"], D("14.344"))
        self.assertEqual(self.mine(self.service.get_stock_balance("OLD_STOCK")), {21: (1, D("1.001"))})
        self.assertEqual(self.mine(self.service.get_stock_balance("NEW_STOCK")),
                         {21: (1, D("3.333")), 24: (1, D("10.010"))})

    def test_stocktake_source_uses_the_same_predicate_and_filters(self):
        current = self.add(self.k21, "2.500")
        self.add(self.k21, "1.000", barcode=False)
        sold = self.add(self.k21, "5.000", status="SOLD")
        rows = self.service.get_stocktake_source(self.item_type, None)
        self.assertEqual(sorted((r["barcode_value"] or "", r["weight_grams"]) for r in rows),
                         [("", D("1.000")), (current, D("2.500"))])
        self.assertNotIn(sold, [r["barcode_value"] for r in rows])
        self.assertEqual(self.service.get_stocktake_source(self.item_type, self.k24), [])
        everything = self.service.get_stocktake_source()
        self.assertIn("01261001", [r["barcode_value"] for r in everything])     # the owner's real piece


if __name__ == "__main__":
    unittest.main()
