"""The numbered chain 001-015 alone must build the application schema.

Static replay of the SQL files (tests/schema_model.py); no database is used.
"""
import re
import unittest

from tests import migration_sql as sql
from tests import schema_model


PROTOTYPE_DIR = sql.SCHEMA_DIR.parent / "migrations"
PROTOTYPE_TABLES = {"barcode_print_jobs", "barcode_print_job_items", "barcode_print_job_events",
                    "inventory_conversion_batches", "inventory_conversion_sources",
                    "old_gold_conversion_batches", "billing_invoice_sequences", "billing_invoices",
                    "billing_sale_lines", "billing_old_gold_lines"}
PROTOTYPE_OLD_GOLD_COLUMNS = {"movement_code": "varchar(40)", "customer_id": "bigint unsigned",
                              "invoice_id": "bigint unsigned", "factory_id": "int unsigned",
                              "performed_by_user_id": "int unsigned"}
ACQUISITION_ENUM = ("enum('MARKET_PURCHASE','OLD_GOLD_MANUFACTURE','OLD_STOCK_CONVERSION',"
                    "'LEGACY_MIGRATION','MANUAL_ENTRY')")
# 034 appends exactly one value (supplier merchandise); nothing is renamed or removed.
ACQUISITION_ENUM_034 = ACQUISITION_ENUM[:-1] + ",'SUPPLIER_PURCHASE')"


def fk_problems(model):
    problems = []
    for table, definition in model.tables.items():
        for name, fk in definition["fks"].items():
            referenced = model.tables.get(fk["ref"])
            if referenced is None:
                problems.append(f"{table}.{name}: missing table {fk['ref']}")
                continue
            for column, ref_column in zip(fk["cols"], fk["refcols"]):
                mine = definition["columns"][column]["type"]
                theirs = referenced["columns"][ref_column]["type"]
                if mine != theirs:
                    problems.append(f"{table}.{name}: {column} {mine} -> {fk['ref']}.{ref_column} {theirs}")
            if not any(ix["cols"][:len(fk["refcols"])] == fk["refcols"] for ix in referenced["indexes"].values()):
                problems.append(f"{table}.{name}: {fk['ref']}{fk['refcols']} is not indexed")
    return problems


class SchemaChainTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.model = schema_model.build()

    def test_chain_is_contiguous_001_to_030(self):
        files = schema_model.chain_files()
        self.assertEqual([int(name[:3]) for name in files], list(range(1, 39)))
        self.assertEqual(files[12:], ["013_schema_consolidation.sql", "014_billing_v2.sql",
                                      "015_legacy_provenance.sql", "016_billing_workflow.sql",
                                      "017_payment_accounts.sql", "018_opening_balances.sql",
                                      "019_invoice_lifecycle_exchange.sql", "020_exchange_replacement_sources.sql",
                                      "021_returned_item_inspection.sql", "022_invoice_returns.sql",
                                      "023_transaction_corrections.sql", "024_customer_account.sql",
                                      "025_financial_operations.sql", "026_cash_movement_categories.sql", "027_customer_deposits.sql", "028_dead_cash_v1.sql", "029_master_data_integrity.sql", "030_conversion_actor.sql", "031_exchange_making_model.sql", "032_exchange_returned_scrap_hold.sql", "033_return_v2_scrap_hold.sql", "034_suppliers_purchasing.sql", "035_supplier_karat.sql", "036_supplier_making_payments.sql", "037_market_trip_custody.sql", "038_karat_22.sql"])

    def test_prototype_files_are_kept_but_not_part_of_the_chain(self):
        prototypes = sorted(p.name for p in PROTOTYPE_DIR.glob("20260921_*.sql"))
        self.assertEqual(len(prototypes), 4)
        self.assertFalse(set(prototypes) & set(schema_model.chain_files()))
        for name in schema_model.chain_files():
            self.assertNotIn("20260921_", sql.strip_comments(sql.read(name)))

    def test_chain_replays_cleanly(self):
        self.assertEqual(self.model.problems, [])

    def test_every_foreign_key_matches_its_referenced_column(self):
        self.assertEqual(fk_problems(self.model), [])

    def test_fk_check_detects_the_d1_prototype_defect(self):
        model = schema_model.build(schema_model.chain_files()[:12])
        prototype = (PROTOTYPE_DIR / "20260921_old_gold_grams_conversion.sql").read_text(encoding="utf-8")
        create = next(s for s in sql.split_statements(prototype)
                      if "CREATE TABLE IF NOT EXISTS old_gold_conversion_batches" in s)
        model.apply(" ".join(create.split()), "prototype")
        self.assertIn("old_gold_conversion_batches.fk_old_gold_conversion_karat: karat_id int unsigned "
                      "-> karats.id tinyint unsigned", fk_problems(model))

    def test_after_clause_must_name_an_existing_column(self):
        model = schema_model.build(schema_model.chain_files()[:11])
        model.apply("ALTER TABLE karats ADD COLUMN extra INT NULL AFTER no_such_column", "synthetic")
        model.apply("ALTER TABLE karats ADD COLUMN extra2 INT NULL AFTER extra", "synthetic")
        self.assertEqual(model.problems, ["synthetic: ADD COLUMN karats AFTER missing column no_such_column"])

    def test_012_places_movement_employee_after_an_existing_column(self):
        """Regression: 012 once used AFTER created_by_user_id, which inventory_movements
        (003) does not have; MySQL failed the first fresh rehearsal with error 1054."""
        model = schema_model.build(schema_model.chain_files()[:11])
        self.assertNotIn("created_by_user_id", model.tables["inventory_movements"]["columns"])
        self.assertIn("performed_by_user_id", model.tables["inventory_movements"]["columns"])
        model.apply("ALTER TABLE inventory_movements ADD COLUMN performed_by_employee_id INT UNSIGNED NULL "
                    "AFTER created_by_user_id", "012 as first committed")
        self.assertEqual(model.problems, ["012 as first committed: ADD COLUMN inventory_movements "
                                          "AFTER missing column created_by_user_id"])
        fixed = [s.sql for s in sql.statements(sql.read("012_actor_attribution.sql"))
                 if s.sql.startswith("ALTER TABLE inventory_movements ADD COLUMN")]
        self.assertEqual(fixed, ["ALTER TABLE inventory_movements ADD COLUMN performed_by_employee_id "
                                 "INT UNSIGNED NULL AFTER performed_by_user_id"])
        self.assertEqual(schema_model.build().problems, [])

    def test_prototype_era_objects_are_reproduced(self):
        self.assertLessEqual(PROTOTYPE_TABLES, set(self.model.tables))
        for table in PROTOTYPE_TABLES:
            self.assertEqual(self.model.tables[table]["source"], "013_schema_consolidation.sql")
        # Reproduced as the prototype defined them (through 028) ...
        columns = schema_model.build(schema_model.chain_files()[:28]).tables["old_gold_movements"]["columns"]
        for column, column_type in PROTOTYPE_OLD_GOLD_COLUMNS.items():
            with self.subTest(column=column):
                self.assertEqual(columns[column], {"type": column_type, "nullable": True})
        # ... then 029 narrows factory_id to factories.id so it can carry a RESTRICT foreign key.
        self.assertEqual(self.model.tables["old_gold_movements"]["columns"]["factory_id"],
                         {"type": "smallint unsigned", "nullable": True})
        self.assertEqual(self.model.tables["old_gold_conversion_batches"]["columns"]["karat_id"]["type"],
                         "tinyint unsigned")

    def test_013_tables_keep_the_prototype_columns(self):
        """Same tables and columns as the historical files; only karat_id's type is corrected."""
        consolidated = sql.create_table_columns(sql.read("013_schema_consolidation.sql"))
        for path in sorted(PROTOTYPE_DIR.glob("20260921_*.sql")):
            for statement in sql.split_statements(path.read_text(encoding="utf-8")):
                statement = " ".join(statement.split())
                match = re.match(r"CREATE TABLE IF NOT EXISTS (\w+)", statement)
                if not match or match.group(1) == "old_gold_movements":
                    continue
                with self.subTest(table=match.group(1)):
                    self.assertEqual(sql.create_table_columns(statement)[match.group(1)],
                                     consolidated[match.group(1)])

    def test_acquisition_source_allows_old_stock_conversion(self):
        # Through 033 the column holds the five values 013 defined; 034 appends SUPPLIER_PURCHASE (see below).
        items = schema_model.build(schema_model.chain_files()[:33]).tables["inventory_items"]
        self.assertEqual(items["columns"]["acquisition_source"], {"type": ACQUISITION_ENUM, "nullable": False})
        self.assertIn("chk_inventory_acquisition_source", items["checks"])
        self.assertNotIn("chk_inventory_source", items["checks"])
        _, expression = sql.checks(sql.read("013_schema_consolidation.sql"))["chk_inventory_acquisition_source"]
        self.assertEqual(set(re.findall(r"'(\w+)'", expression)),
                         set(re.findall(r"'(\w+)'", ACQUISITION_ENUM)))

    def test_034_appends_the_supplier_source_and_keeps_every_existing_value(self):
        final = self.model.tables["inventory_items"]
        self.assertEqual(final["columns"]["acquisition_source"], {"type": ACQUISITION_ENUM_034, "nullable": False})
        self.assertIn("chk_inventory_acquisition_source", final["checks"])
        self.assertNotIn("chk_inventory_source", final["checks"])
        values = re.findall(r"'(\w+)'", ACQUISITION_ENUM_034)
        self.assertEqual(values[:5], re.findall(r"'(\w+)'", ACQUISITION_ENUM))           # existing values keep order
        self.assertEqual(values[5:], ["SUPPLIER_PURCHASE"])
        self.assertIn("MARKET_PURCHASE", values)                                         # not redefined or removed

    def test_old_gold_karat_stays_not_null(self):
        self.assertEqual(self.model.tables["old_gold_movements"]["columns"]["karat_id"],
                         {"type": "tinyint unsigned", "nullable": False})

    def test_accepted_index_names_are_not_reproduced_redundantly(self):
        for table in ("inventory_movements", "old_gold_movements"):
            indexes = self.model.tables[table]["indexes"]
            for name, index in indexes.items():
                with self.subTest(table=table, index=name):
                    duplicates = [n for n, other in indexes.items() if n != name and other["cols"] == index["cols"]]
                    self.assertEqual(duplicates, [])


if __name__ == "__main__":
    unittest.main()
