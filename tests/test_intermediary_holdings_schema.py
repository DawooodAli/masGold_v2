"""Migration 048 (whole assayed lot holdings at an intermediary): static replay and contract of the forward and rollback files.
No database is used; the live behaviour (constraints, refusals, composite keys, rollback) was proven by the rehearsal on masgold_rehearsal."""
import hashlib
from pathlib import Path
import re
import unittest

from tests import migration_sql as sql
from tests import schema_model
from tools.schema_rehearsal.chain import load_chain

ROOT = Path(__file__).resolve().parents[1]
FORWARD = "048_intermediary_lot_holdings.sql"
ROLLBACK = "048_intermediary_lot_holdings_rollback.sql"
FROZEN = {"045_supplier_gold_settlement.sql": "2e72a5de82f8cbf3582975122bfbf11618d4d02ffd5a3a67eb69468dddeb574a",
          "046_supplier_gold_custody_settlement.sql": "604aec4e9ac1f94ea01867ca0e04b972cf9c855f19ecf0e80f3b5c1375a7a8e9",
          "047_supplier_gold_whole_lot_settlement.sql": "b68de49b8f908cb9941173d1a2674478a331cfdfded9193d8139b9b002354165"}
EARLIER_KINDS = ("ISSUE", "RETURN", "MELT", "LOT_RETURN", "LOT_ISSUE", "HEAT", "SUPPLIER_PAY", "GOLD_SETTLE", "LOT_SETTLE")
EARLIER_MOVEMENTS = ("CREATE", "SHOP_RETURN", "SHOP_ISSUE", "SUPPLIER_SETTLE")
TABLES = ("intermediary_holding_pools", "intermediary_holding_documents", "intermediary_holding_movements", "market_trip_holding_links")


def digest(name):
    return hashlib.sha256((ROOT / "database" / "schema" / name).read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def code_of(text):
    return " ".join(line for line in text.splitlines() if not line.lstrip().startswith("--"))


def fk(text, name):
    match = re.search(rf"CONSTRAINT {name} FOREIGN KEY \(([^)]*)\)\s+REFERENCES (\w+)\(([^)]*)\)", text)
    child, table, parent = match.groups()
    return [c.strip() for c in child.split(",")], table, [c.strip() for c in parent.split(",")]


class HoldingSchemaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.model = schema_model.build()
        cls.forward = sql.read(FORWARD)
        cls.rollback = sql.read(ROLLBACK, sql.ROLLBACK_DIR)
        cls.code = code_of(cls.forward)

    def test_the_chain_replays_cleanly_and_the_four_tables_exist(self):
        self.assertEqual(self.model.problems, [])
        for table in TABLES:
            self.assertEqual(self.model.tables[table]["source"], FORWARD, table)

    def test_it_requires_047_and_registers_048(self):
        self.assertIn("version=47", self.forward.replace(" ", ""))
        self.assertIn("MIGRATION_048_REFUSED_missing_047", self.forward)
        self.assertRegex(self.forward, r"INSERT INTO schema_migrations\(version,name\) VALUES \(48,")

    def test_earlier_migrations_are_untouched_and_the_manifest_matches(self):
        for name, expected in FROZEN.items():
            self.assertEqual(digest(name), expected, name)
        files = {f.name: f for f in load_chain(require_committed=False)}
        self.assertEqual(len(files), 75)
        self.assertEqual(files[FORWARD].sha256, digest(FORWARD))
        self.assertIn(f"{digest(FORWARD)}  {FORWARD}", (ROOT / "database" / "schema" / "SHA256SUMS").read_text(encoding="utf-8"))

    def test_the_entry_and_movement_rules_add_the_new_values_and_keep_every_earlier_one(self):
        kinds = re.findall(r"'(\w+)'", re.search(r"ADD CONSTRAINT chk_mte_kind CHECK \(entry_kind IN \(([^)]*)\)\)", self.forward).group(1))
        self.assertEqual(kinds, list(EARLIER_KINDS) + ["LOT_HOLD", "HOLD_RETURN"])
        self.assertLessEqual(max(len(k) for k in ("LOT_HOLD", "HOLD_RETURN")), 12)                   # entry_kind is VARCHAR(12)
        restored = re.findall(r"'(\w+)'", re.search(r"ADD CONSTRAINT chk_mte_kind CHECK\(entry_kind IN \(([^)]*)\)\)", self.rollback).group(1))
        self.assertEqual(restored, list(EARLIER_KINDS))
        moves = re.findall(r"'(\w+)'", re.search(r"ADD CONSTRAINT chk_mtlm_type CHECK \(movement_type IN \(([^)]*)\)\)", self.forward).group(1))
        self.assertEqual(moves, list(EARLIER_MOVEMENTS) + ["HOLD_OUT", "HOLD_IN"])
        self.assertIn("movement_type NOT IN ('CREATE','SHOP_ISSUE','HOLD_IN') OR weight_delta>0", self.forward)
        self.assertIn("movement_type NOT IN ('SHOP_RETURN','SUPPLIER_SETTLE','HOLD_OUT') OR weight_delta<0", self.forward)

    def test_every_composite_foreign_key_names_the_same_columns_in_the_same_order_as_a_unique_parent_key(self):
        pools = re.search(r"CREATE TABLE IF NOT EXISTS intermediary_holding_pools \((.*?)\) ENGINE=InnoDB", self.forward, re.S).group(1)
        documents = re.search(r"CREATE TABLE IF NOT EXISTS intermediary_holding_documents \((.*?)\) ENGINE=InnoDB", self.forward, re.S).group(1)
        movements = re.search(r"CREATE TABLE IF NOT EXISTS intermediary_holding_movements \((.*?)\) ENGINE=InnoDB", self.forward, re.S).group(1)
        links = re.search(r"CREATE TABLE IF NOT EXISTS market_trip_holding_links \((.*?)\) ENGINE=InnoDB", self.forward, re.S).group(1)
        self.assertEqual(fk(pools, "fk_ihp_lot"), (["lot_id", "origin_melt_entry_id", "weight_grams", "fineness_permille"], "market_trip_lots",
                                                    ["id", "entry_id", "weight_grams", "fineness_permille"]))                      # 047's own unique key
        self.assertIn("UNIQUE KEY uq_mtl_settle_facts (id,entry_id,weight_grams,fineness_permille)", sql.read("047_supplier_gold_whole_lot_settlement.sql"))
        child, table, parent = fk(documents, "fk_ihd_pool")
        self.assertEqual((child, table), (["pool_id", "party_id", "lot_id", "weight_grams", "fineness_permille"], "intermediary_holding_pools"))
        self.assertIn(f"UNIQUE KEY uq_ihp_facts ({','.join(parent)})", pools)
        child, table, parent = fk(movements, "fk_ihm_document")
        self.assertEqual((child, table, parent), (["document_id", "doc_kind", "pool_id", "weight_grams"], "intermediary_holding_documents",
                                                  ["id", "doc_kind", "pool_id", "weight_grams"]))
        self.assertIn(f"UNIQUE KEY uq_ihd_movement ({','.join(parent)})", documents)
        child, table, parent = fk(links, "fk_mthl_document")
        self.assertEqual((child, table), (["document_id", "doc_kind", "pool_id", "lot_id", "weight_grams", "trip_id"], "intermediary_holding_documents"))
        self.assertIn(f"UNIQUE KEY uq_ihd_link ({','.join(parent)})", documents)
        child, table, parent = fk(links, "fk_mthl_entry")
        self.assertEqual((child, table, parent), (["entry_id", "entry_kind", "trip_id"], "market_trip_entries", ["id", "entry_kind", "trip_id"]))
        self.assertIn("ADD UNIQUE KEY uq_mte_id_kind_trip (id,entry_kind,trip_id)", self.forward)
        child, table, parent = fk(links, "fk_mthl_movement")
        self.assertEqual((child, table, parent), (["custody_movement_id", "lot_id", "entry_id", "movement_type", "weight_delta"], "market_trip_lot_movements",
                                                  ["id", "lot_id", "entry_id", "movement_type", "weight_delta"]))
        self.assertIn("UNIQUE KEY uq_mtlm_settle_link (id,lot_id,entry_id,movement_type,weight_delta)", sql.read("047_supplier_gold_whole_lot_settlement.sql"))

    def test_nothing_is_unique_per_lot_across_history_and_every_document_and_movement_is_unique_where_it_must_be(self):
        pools = re.search(r"CREATE TABLE IF NOT EXISTS intermediary_holding_pools \((.*?)\) ENGINE=InnoDB", self.forward, re.S).group(1)
        self.assertNotRegex(pools, r"UNIQUE KEY \w+ \((lot_id|party_id,lot_id)\)")                      # a lot may be handed over many times
        for needle in ("UNIQUE KEY uq_ihd_request (request_key)", "UNIQUE KEY uq_ihm_document_type (document_id,movement_type)",
                       "UNIQUE KEY uq_ihm_correction (correction_id)", "UNIQUE KEY uq_mthl_document (document_id)",
                       "UNIQUE KEY uq_mthl_movement (custody_movement_id)", "PRIMARY KEY (entry_id)"):
            self.assertIn(needle, self.forward)
        self.assertNotIn("ON DELETE", self.forward)
        self.assertNotRegex(self.forward, r"\b(FLOAT|DOUBLE)\b")

    def test_the_shape_checks_agree_documents_movements_and_links(self):
        for needle in ("(movement_type='HANDOVER' AND doc_kind='HANDOVER' AND weight_delta=weight_grams AND correction_id IS NULL)",
                       "(movement_type='RETURN' AND doc_kind='RETURN' AND weight_delta=-weight_grams AND correction_id IS NULL)",
                       "(movement_type='HANDOVER_REVERSAL' AND doc_kind='HANDOVER' AND weight_delta=-weight_grams AND correction_id IS NOT NULL)",
                       "(movement_type='RETURN_REVERSAL' AND doc_kind='RETURN' AND weight_delta=weight_grams AND correction_id IS NOT NULL)",
                       "(entry_kind='LOT_HOLD' AND doc_kind='HANDOVER' AND movement_type='HOLD_OUT' AND weight_delta=-weight_grams)",
                       "(entry_kind='HOLD_RETURN' AND doc_kind='RETURN' AND movement_type='HOLD_IN' AND weight_delta=weight_grams)",
                       "doc_kind='RETURN' AND intact_confirmation_code='SAME_BAR_WHOLE_UNDIVIDED' AND confirmed_by_user_id IS NOT NULL AND confirmed_at IS NOT NULL",
                       "weight_grams>0 AND fineness_permille>0 AND fineness_permille<=1000"):
            self.assertIn(needle, self.forward)

    def test_the_rights_are_owner_only_and_the_only_data_written_is_the_migration_row_and_the_three_rights(self):
        for code in ("INTERMEDIARY_HOLDING_VIEW", "INTERMEDIARY_HOLDING_MANAGE", "INTERMEDIARY_HOLDING_REVERSE"):
            self.assertIn(f"('{code}'", self.forward)
        self.assertIn("WHERE r.code='OWNER'", self.forward)
        self.assertNotIn("MANAGER", self.forward)
        self.assertEqual(re.findall(r"INSERT INTO (\w+)", self.code), ["permissions", "role_permissions", "schema_migrations"])
        for statement in [s.sql for s in sql.statements(self.forward)]:
            self.assertNotRegex(statement, r"^(UPDATE|DELETE|TRUNCATE|DROP TABLE)\b")
        for table in ("cash_movements", "bank_movements", "supplier_making", "supplier_gold", "old_gold_movements", "inventory_items", "golddb"):
            self.assertNotIn(table, self.code, table)

    def test_every_alter_is_guarded_and_nothing_outside_the_new_objects_and_two_rules_changes(self):
        raw = [s.strip() for s in self.code.split(";") if s.strip()]
        allowed = ("SET @masgold_sql", "PREPARE masgold_stmt", "EXECUTE masgold_stmt", "DEALLOCATE PREPARE", "CREATE TABLE IF NOT EXISTS",
                   "INSERT INTO permissions", "INSERT INTO role_permissions", "INSERT INTO schema_migrations")
        for statement in raw:
            self.assertTrue(statement.startswith(allowed) or statement.startswith(("PREPARE", "EXECUTE", "DEALLOCATE")), statement[:80])
        self.assertGreaterEqual(sum(1 for s in raw if s.startswith("SET @masgold_sql")), 8)

    def test_the_rollback_refuses_on_any_holding_history_or_a_later_migration_and_drops_in_dependency_order(self):
        self.assertIn("ROLLBACK_048_REFUSED_history_or_later_migration", self.rollback)
        for needle in ("FROM intermediary_holding_pools", "FROM intermediary_holding_documents", "FROM intermediary_holding_movements",
                       "FROM market_trip_holding_links", "entry_kind IN ('LOT_HOLD','HOLD_RETURN')", "movement_type IN ('HOLD_OUT','HOLD_IN')", "version>48"):
            self.assertIn(needle, self.rollback)
        order = ["ROLLBACK_048_REFUSED", "DROP TABLE market_trip_holding_links", "DROP TABLE intermediary_holding_movements",
                 "DROP TABLE intermediary_holding_documents", "DROP TABLE intermediary_holding_pools", "DROP INDEX uq_mte_id_kind_trip",
                 "ADD CONSTRAINT chk_mte_kind", "ADD CONSTRAINT chk_mtlm_type", "ADD CONSTRAINT chk_mtlm_delta",
                 "DELETE rp FROM role_permissions", "DELETE FROM permissions", "DELETE FROM schema_migrations WHERE version=48"]
        positions = [self.rollback.index(item) for item in order]
        self.assertEqual(positions, sorted(positions))
        self.assertNotRegex(code_of(self.rollback), r"DELETE FROM (intermediary|market_trip|supplier|old_gold|cash|bank)")


if __name__ == "__main__":
    unittest.main()
