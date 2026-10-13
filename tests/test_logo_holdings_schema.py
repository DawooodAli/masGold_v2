"""Migration 049 (master-karat LOGO holdings): static replay and contract of the forward and rollback files. No database is used; the live behaviour
(constraints, composite keys, refusals, rollback) was proven by the rehearsal on masgold_rehearsal."""
import hashlib
from pathlib import Path
import re
import unittest

from tests import migration_sql as sql
from tests import schema_model
from tools.schema_rehearsal.chain import load_chain

ROOT = Path(__file__).resolve().parents[1]
FORWARD = "049_intermediary_logo_holdings.sql"
ROLLBACK = "049_intermediary_logo_holdings_rollback.sql"
FROZEN = {"047_supplier_gold_whole_lot_settlement.sql": "b68de49b8f908cb9941173d1a2674478a331cfdfded9193d8139b9b002354165",
          "048_intermediary_lot_holdings.sql": "921ed1a96da324a1a97133d01c052ba3dca0edf0e5d7c99f053d8156d325c46f"}
EARLIER_KINDS = ("ISSUE", "RETURN", "MELT", "LOT_RETURN", "LOT_ISSUE", "HEAT", "SUPPLIER_PAY", "GOLD_SETTLE", "LOT_SETTLE", "LOT_HOLD", "HOLD_RETURN")
TABLES = ("logo_holding_pools", "logo_holding_documents", "logo_holding_movements", "market_trip_logo_hold_links")


def digest(name):
    return hashlib.sha256((ROOT / "database" / "schema" / name).read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def code_of(text):
    return " ".join(line for line in text.splitlines() if not line.lstrip().startswith("--"))


def table(text, name):
    return re.search(rf"CREATE TABLE IF NOT EXISTS {name} \((.*?)\) ENGINE=InnoDB", text, re.S).group(1)


def fk(text, name):
    child, parent_table, parent = re.search(rf"CONSTRAINT {name} FOREIGN KEY \(([^)]*)\)\s+REFERENCES (\w+)\(([^)]*)\)", text).groups()
    return [c.strip() for c in child.split(",")], parent_table, [c.strip() for c in parent.split(",")]


class LogoHoldingSchemaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.model = schema_model.build()
        cls.forward = sql.read(FORWARD)
        cls.rollback = sql.read(ROLLBACK, sql.ROLLBACK_DIR)
        cls.code = code_of(cls.forward)

    def test_the_chain_replays_cleanly_the_tables_exist_and_048_is_untouched(self):
        self.assertEqual(self.model.problems, [])
        for name in TABLES:
            self.assertEqual(self.model.tables[name]["source"], FORWARD, name)
        for name, expected in FROZEN.items():
            self.assertEqual(digest(name), expected, name)
        files = {f.name: f for f in load_chain(require_committed=False)}
        self.assertEqual(len(files), 50)
        self.assertEqual(files[FORWARD].sha256, digest(FORWARD))
        self.assertIn(f"{digest(FORWARD)}  {FORWARD}", (ROOT / "database" / "schema" / "SHA256SUMS").read_text(encoding="utf-8"))

    def test_it_requires_048_registers_049_and_adds_no_permission_type_or_lot_movement(self):
        self.assertIn("version=48", self.forward.replace(" ", ""))
        self.assertIn("MIGRATION_049_REFUSED_missing_048", self.forward)
        self.assertRegex(self.forward, r"INSERT INTO schema_migrations\(version,name\) VALUES \(49,")
        self.assertEqual(re.findall(r"INSERT INTO (\w+)", self.code), ["schema_migrations"])
        for forbidden in ("permissions", "market_trip_lot_movements", "chk_mtlm", "cash_movements", "supplier_gold", "supplier_making", "old_gold_movements"):
            self.assertNotIn(forbidden, self.code, forbidden)

    def test_the_entry_rule_keeps_every_earlier_kind_and_the_rollback_restores_the_048_rule_exactly(self):
        kinds = re.findall(r"'(\w+)'", re.search(r"ADD CONSTRAINT chk_mte_kind CHECK \(entry_kind IN \(([^)]*)\)\)", self.forward).group(1))
        self.assertEqual(kinds, list(EARLIER_KINDS) + ["LOGO_HOLD", "HOLD_BACK"])
        self.assertLessEqual(max(len("LOGO_HOLD"), len("HOLD_BACK")), 12)
        restored = re.findall(r"'(\w+)'", re.search(r"ADD CONSTRAINT chk_mte_kind CHECK\(entry_kind IN \(([^)]*)\)\)", self.rollback).group(1))
        self.assertEqual(restored, list(EARLIER_KINDS))                                           # the 048 rule, HOLD_RETURN included

    def test_composite_keys_enforce_pool_party_karat_original_trip_document_kind_and_quantity(self):
        pools, documents = table(self.forward, "logo_holding_pools"), table(self.forward, "logo_holding_documents")
        movements, links = table(self.forward, "logo_holding_movements"), table(self.forward, "market_trip_logo_hold_links")
        child, parent_table, parent = fk(documents, "fk_lhd_pool")
        self.assertEqual((child, parent_table), (["pool_id", "party_id", "karat_id", "trip_id"], "logo_holding_pools"))
        self.assertIn(f"UNIQUE KEY uq_lhp_facts ({','.join(parent)})", pools)
        self.assertEqual(fk(documents, "fk_lhd_handover"), (["handover_pool_id", "handover_weight"], "logo_holding_pools", ["id", "weight_grams"]))
        self.assertIn("UNIQUE KEY uq_lhp_weight (id,weight_grams)", pools)
        self.assertIn("UNIQUE KEY uq_lhd_one_handover (handover_pool_id)", documents)             # ONE original handover per pool
        child, parent_table, parent = fk(movements, "fk_lhm_document")
        self.assertEqual((child, parent_table), (["document_id", "doc_kind", "pool_id", "weight_grams"], "logo_holding_documents"))
        self.assertIn(f"UNIQUE KEY uq_lhd_movement ({','.join(parent)})", documents)
        child, parent_table, parent = fk(links, "fk_mtlhl_document")
        self.assertEqual((child, parent_table), (["document_id", "doc_kind", "pool_id", "karat_id", "weight_grams", "trip_id"], "logo_holding_documents"))
        self.assertIn(f"UNIQUE KEY uq_lhd_link ({','.join(parent)})", documents)
        self.assertEqual(fk(links, "fk_mtlhl_entry"), (["entry_id", "entry_kind", "trip_id"], "market_trip_entries", ["id", "entry_kind", "trip_id"]))
        self.assertIn("uq_mte_id_kind_trip (id,entry_kind,trip_id)", sql.read("048_intermediary_lot_holdings.sql"))
        self.assertNotIn("ON DELETE", self.forward)
        self.assertNotRegex(self.forward, r"\b(FLOAT|DOUBLE)\b")

    def test_explicit_null_rules_and_shape_checks(self):
        for needle in ("(doc_kind='HANDOVER' AND handover_pool_id IS NOT NULL AND handover_pool_id=pool_id AND handover_weight IS NOT NULL AND handover_weight=weight_grams)",
                       "(doc_kind='RETURN' AND handover_pool_id IS NULL AND handover_weight IS NULL)",
                       "designation='LOGO' AND weight_grams>0",
                       "(movement_type='HANDOVER_REVERSAL' AND doc_kind='HANDOVER' AND weight_delta=-weight_grams AND correction_id IS NOT NULL)",
                       "(movement_type='RETURN_REVERSAL' AND doc_kind='RETURN' AND weight_delta=weight_grams AND correction_id IS NOT NULL)",
                       "(movement_type='RETURN' AND doc_kind='RETURN' AND weight_delta=-weight_grams AND correction_id IS NULL)",
                       "(entry_kind='LOGO_HOLD' AND doc_kind='HANDOVER') OR (entry_kind='HOLD_BACK' AND doc_kind='RETURN')",
                       "UNIQUE KEY uq_lhm_document_type (document_id,movement_type)", "UNIQUE KEY uq_lhm_correction (correction_id)",
                       "UNIQUE KEY uq_mtlhl_document (document_id)", "UNIQUE KEY uq_lhd_request (request_key)"):
            self.assertIn(needle, self.forward)
        self.assertNotIn("fineness", self.code)                                                     # no invented assay or pure-gold figure
        self.assertNotIn("intact", self.code.lower())

    def test_every_alter_is_guarded_and_the_rollback_guards_all_049_history_not_048_entries(self):
        raw = [s.strip() for s in self.code.split(";") if s.strip()]
        allowed = ("SET @masgold_sql", "PREPARE masgold_stmt", "EXECUTE masgold_stmt", "DEALLOCATE PREPARE", "CREATE TABLE IF NOT EXISTS", "INSERT INTO schema_migrations")
        for statement in raw:
            self.assertTrue(statement.startswith(allowed), statement[:80])
        self.assertIn("ROLLBACK_049_REFUSED_history_or_later_migration", self.rollback)
        for needle in ("FROM logo_holding_pools", "FROM logo_holding_documents", "FROM logo_holding_movements", "FROM market_trip_logo_hold_links",
                       "entry_kind IN ('LOGO_HOLD','HOLD_BACK')", "version>49"):
            self.assertIn(needle, self.rollback)
        self.assertNotIn("'HOLD_RETURN')\n OR", self.rollback.split("SET @masgold_sql")[1].split("'SELECT")[0])      # the guard names LOGO_HOLD/HOLD_BACK only
        guard = self.rollback.split("PREPARE")[0]
        self.assertNotIn("HOLD_RETURN", guard)
        order = ["ROLLBACK_049_REFUSED", "DROP TABLE market_trip_logo_hold_links", "DROP TABLE logo_holding_movements", "DROP TABLE logo_holding_documents",
                 "DROP TABLE logo_holding_pools", "ADD CONSTRAINT chk_mte_kind", "DELETE FROM schema_migrations WHERE version=49"]
        positions = [self.rollback.index(item) for item in order]
        self.assertEqual(positions, sorted(positions))


if __name__ == "__main__":
    unittest.main()
