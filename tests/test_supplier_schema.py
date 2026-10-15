"""Migration 034 (Purchasing & Suppliers V1): static replay and contract of the forward and rollback files.
No database is used; the live behaviour is proven by the schema rehearsal / opt-in MySQL run."""
import re
import unittest

from tests import migration_sql as sql
from tests import schema_model
from tools.schema_rehearsal.chain import load_chain

FORWARD = "034_suppliers_purchasing.sql"
ROLLBACK = "034_suppliers_purchasing_rollback.sql"
TABLES = ["suppliers", "supplier_purchase_invoices", "supplier_invoice_groups", "supplier_gold_movement_types",
          "supplier_making_movement_types", "supplier_gold_movements", "supplier_making_movements",
          "supplier_invoice_pieces"]
PERMISSIONS = ["SUPPLIER_VIEW", "SUPPLIER_MANAGE", "SUPPLIER_INVOICE_EDIT", "SUPPLIER_INVOICE_POST", "SUPPLIER_INTAKE"]


class SupplierSchemaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.model = schema_model.build()
        cls.forward = sql.read(FORWARD)
        cls.rollback = sql.read(ROLLBACK, sql.ROLLBACK_DIR)
        cls.statements = [s.sql for s in sql.statements(cls.forward)]

    def test_the_chain_replays_cleanly_and_the_new_tables_exist(self):
        self.assertEqual(self.model.problems, [])
        for table in TABLES:
            self.assertIn(table, self.model.tables)
            self.assertEqual(self.model.tables[table]["source"], FORWARD)

    def test_it_requires_033_and_registers_034(self):
        self.assertIn("version=33", self.forward.replace(" ", ""))
        self.assertIn("MIGRATION_034_REFUSED_missing_033", self.forward)
        self.assertRegex(self.forward, r"INSERT INTO schema_migrations\(version,name\) VALUES \(34,")

    def test_only_new_tables_reference_data_and_permissions_are_written_no_existing_table_is_altered(self):
        allowed_inserts = {"supplier_gold_movement_types", "supplier_making_movement_types", "permissions",
                           "role_permissions", "schema_migrations"}
        for statement in self.statements:
            target = re.match(r"^INSERT INTO\s+(\w+)", statement, re.I)
            if target:
                self.assertIn(target.group(1), allowed_inserts)
            self.assertNotRegex(statement, r"^(DROP|UPDATE|DELETE|TRUNCATE)\b")
        # The only existing table touched is inventory_items.acquisition_source (the guarded section below).
        altered = {sql.table_of(s) for s in self.statements if s.upper().startswith("ALTER")}
        self.assertEqual(altered, {"inventory_items"})
        for statement in (s for s in self.statements if s.upper().startswith("ALTER")):
            self.assertRegex(statement, r"acquisition_source|chk_inventory_acquisition_source")

    def test_one_karat_per_invoice_the_groups_carry_none(self):
        invoice = self.model.tables["supplier_purchase_invoices"]["columns"]
        groups = self.model.tables["supplier_invoice_groups"]["columns"]
        self.assertEqual(invoice["karat_id"], {"type": "tinyint unsigned", "nullable": False})
        self.assertNotIn("karat_id", groups)
        self.assertEqual(groups["making_per_gram"]["type"], "decimal(12,2)")       # EGP per gram, never per piece
        self.assertNotIn("making_per_piece", groups)

    def test_decimal_precision_keeps_gold_exact(self):
        tables = self.model.tables
        self.assertEqual(tables["supplier_purchase_invoices"]["columns"]["supplier_weight_grams"]["type"], "decimal(12,3)")
        self.assertEqual(tables["supplier_gold_movements"]["columns"]["weight_delta_grams"]["type"], "decimal(14,3)")
        self.assertEqual(tables["supplier_gold_movements"]["columns"]["equivalent_21_delta_grams"]["type"], "decimal(18,6)")
        self.assertEqual(tables["supplier_making_movements"]["columns"]["amount_delta"]["type"], "decimal(16,2)")
        for table in TABLES:
            for name, column in tables[table]["columns"].items():
                self.assertNotRegex(column["type"], r"float|double", (table, name))

    def test_ledgers_are_append_only_by_design_with_unique_source_keys(self):
        for table, key in (("supplier_gold_movements", "uq_sgm_source"), ("supplier_making_movements", "uq_smm_source")):
            index = self.model.tables[table]["indexes"][key]
            self.assertTrue(index["unique"])
            self.assertEqual(index["cols"], ["source_type", "source_id", "movement_type_id"])
        # No updated_at column and no mutable balance anywhere: balances are SUM(delta) of the ledger.
        for table in ("supplier_gold_movements", "supplier_making_movements"):
            columns = self.model.tables[table]["columns"]
            self.assertNotIn("updated_at", columns)
            self.assertFalse([c for c in columns if "balance" in c])
        self.assertNotIn("balance", " ".join(self.model.tables["suppliers"]["columns"]))

    def test_database_checks_guard_the_accounting_truth(self):
        checks = sql.checks(self.forward)
        self.assertIn("equivalent_21_delta_grams > 0", checks["chk_sgm_sign"][1])
        self.assertIn("equivalent_21_delta_grams < 0", checks["chk_sgm_sign"][1])
        self.assertEqual(checks["chk_smm_amount"][1].replace(" ", ""), "amount_delta<>0")
        self.assertEqual(checks["chk_spi_status"][1].replace(" ", ""),
                         "statusIN('DRAFT','POSTED','CANCELLED')".replace(" ", ""))
        for part in ("status = 'POSTED'", "posted_at IS NOT NULL", "post_request_key IS NOT NULL"):
            self.assertIn(part, checks["chk_spi_state"][1])
        self.assertIn("supplier_weight_grams > 0", checks["chk_spi_weight"][1])

    def test_idempotency_keys_are_unique(self):
        invoice = self.model.tables["supplier_purchase_invoices"]["indexes"]
        self.assertTrue(invoice["uq_spi_draft_request"]["unique"])
        self.assertTrue(invoice["uq_spi_post_request"]["unique"])
        pieces = self.model.tables["supplier_invoice_pieces"]["indexes"]
        self.assertTrue(pieces["uq_sip_request"]["unique"])
        self.assertTrue(pieces["uq_sip_item"]["unique"])                           # one piece belongs to one group

    def test_a_piece_links_to_a_group_of_the_same_invoice(self):
        fk = self.model.tables["supplier_invoice_pieces"]["fks"]["fk_sip_group"]
        self.assertEqual((fk["cols"], fk["ref"], fk["refcols"]),
                         (["invoice_id", "group_id"], "supplier_invoice_groups", ["invoice_id", "id"]))
        self.assertIn("fk_sip_item", self.model.tables["supplier_invoice_pieces"]["fks"])

    def test_the_five_rights_are_granted_to_owner_and_manager_only(self):
        for code in PERMISSIONS:
            self.assertEqual(len(re.findall(rf"'{code}'", self.forward)), 2)       # the right + its grant
        self.assertIn("WHERE r.code IN ('OWNER','MANAGER')", self.forward)

    def test_the_manifest_hash_matches_the_committed_file(self):
        files = {f.name: f for f in load_chain(require_committed=False)}
        self.assertEqual(len(files), 52)
        self.assertIn(FORWARD, files)

    # ------------------------------------------------------------------ acquisition source
    def test_the_supplier_source_is_appended_and_guarded(self):
        alters = [s for s in sql.statements(self.forward) if s.sql.upper().startswith("ALTER TABLE INVENTORY_ITEMS")]
        self.assertEqual(len(alters), 3)
        modify, drop, add = alters
        self.assertIn("MODIFY COLUMN acquisition_source ENUM('MARKET_PURCHASE','OLD_GOLD_MANUFACTURE',"
                      "'OLD_STOCK_CONVERSION','LEGACY_MIGRATION','MANUAL_ENTRY','SUPPLIER_PURCHASE') NOT NULL", modify.sql)
        self.assertEqual(drop.sql, "ALTER TABLE inventory_items DROP CHECK chk_inventory_acquisition_source")
        self.assertIn("ADD CONSTRAINT chk_inventory_acquisition_source CHECK", add.sql)
        self.assertIn("'SUPPLIER_PURCHASE'", add.sql)
        for statement in alters:                                  # every step is idempotent: guarded by the live schema
            self.assertTrue(statement.guard, statement.sql[:60])
        self.assertIn("COLUMN_TYPE LIKE '%SUPPLIER_PURCHASE%'", modify.guard)
        self.assertIn("CHECK_CLAUSE NOT LIKE '%SUPPLIER_PURCHASE%'", drop.guard)
        self.assertIn("CONSTRAINT_NAME = 'chk_inventory_acquisition_source'", add.guard)

    def test_the_final_model_accepts_both_sources_with_distinct_meanings(self):
        column = self.model.tables["inventory_items"]["columns"]["acquisition_source"]
        self.assertIn("'MARKET_PURCHASE'", column["type"])
        self.assertIn("'SUPPLIER_PURCHASE'", column["type"])
        self.assertNotIn("SUPPLIER_PURCHASE", sql.read("013_schema_consolidation.sql"))      # 013 is not edited
        self.assertEqual(self.model.problems, [])

    # ------------------------------------------------------------------ rollback
    def test_rollback_refuses_once_any_supplier_data_or_a_later_migration_exists(self):
        guard = self.rollback.split("PREPARE")[0]
        for table in ("suppliers", "supplier_purchase_invoices", "supplier_invoice_groups", "supplier_gold_movements",
                      "supplier_making_movements", "supplier_invoice_pieces"):
            self.assertIn(f"EXISTS(SELECT 1 FROM {table})", guard)
        self.assertIn("version>34", guard)
        self.assertIn("ROLLBACK_034_REFUSED_supplier_data_or_later_migration", guard)

    def test_rollback_drops_in_foreign_key_order_and_removes_only_its_own_rows(self):
        drops = re.findall(r"^DROP TABLE (\w+);", self.rollback, re.M)
        self.assertEqual(sorted(drops), sorted(TABLES))
        order = {name: i for i, name in enumerate(drops)}
        for child, parent in (("supplier_invoice_pieces", "supplier_invoice_groups"),
                              ("supplier_invoice_groups", "supplier_purchase_invoices"),
                              ("supplier_purchase_invoices", "suppliers"),
                              ("supplier_gold_movements", "supplier_gold_movement_types"),
                              ("supplier_making_movements", "supplier_making_movement_types"),
                              ("supplier_gold_movements", "suppliers"), ("supplier_making_movements", "suppliers")):
            self.assertLess(order[child], order[parent], (child, parent))
        self.assertIn("DELETE FROM schema_migrations WHERE version=34;", self.rollback)
        for code in PERMISSIONS:
            self.assertIn(f"'{code}'", self.rollback)
        for forbidden in ("golddb", "DROP DATABASE", "TRUNCATE", "DELETE FROM inventory_items"):
            self.assertNotIn(forbidden, self.rollback)

    def test_rollback_restores_the_five_value_source_and_refuses_when_a_supplier_piece_exists(self):
        guard = self.rollback.split("PREPARE")[0]
        self.assertIn("EXISTS(SELECT 1 FROM inventory_items WHERE acquisition_source='SUPPLIER_PURCHASE')", guard)
        self.assertIn("ALTER TABLE inventory_items MODIFY COLUMN acquisition_source ENUM('MARKET_PURCHASE',"
                      "'OLD_GOLD_MANUFACTURE','OLD_STOCK_CONVERSION','LEGACY_MIGRATION','MANUAL_ENTRY') NOT NULL;",
                      self.rollback)
        restored = self.rollback.split("ADD CONSTRAINT chk_inventory_acquisition_source CHECK (")[1].split(";")[0]
        self.assertNotIn("SUPPLIER_PURCHASE", restored)
        self.assertEqual(len(re.findall(r"'(\w+)'", restored)), 5)
        # The inventory constraint is restored AFTER the guard and BEFORE the supplier tables are dropped.
        self.assertLess(self.rollback.index("ALTER TABLE inventory_items DROP CHECK"),
                        self.rollback.index("DROP TABLE supplier_invoice_pieces"))
        self.assertGreater(self.rollback.index("ALTER TABLE inventory_items DROP CHECK"), self.rollback.index("PREPARE"))


if __name__ == "__main__":
    unittest.main()
