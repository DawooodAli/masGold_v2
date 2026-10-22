"""Migration 061 (settlement step 2b: supplier delivery of the melted bar and of unmelted gold): static replay and contract of the forward and rollback files. No database is
used; the live behaviour (constraints, refusals, rollback) is NOT proven here and still needs an approved rehearsal run."""
import hashlib
from pathlib import Path
import re
import unittest

from tests import migration_sql as sql
from tests import schema_model
from tools.schema_rehearsal.chain import load_chain

ROOT = Path(__file__).resolve().parents[1]
FORWARD = "061_market_trip_settlement_distribution.sql"
ROLLBACK = "061_market_trip_settlement_distribution_rollback.sql"
FROZEN = {"060_market_trip_settlement_melt.sql": "271f0b9cf61f932665c088f111bca014537d6e5d46d01ac7737e0d131b9b15d0",
          "059_market_trip_book_gain.sql": "1c6de6505090cb7362e3834eececb51d69a5da510c37f079ee61c22c44f0ad2b",
          "058_market_trip_book_cycle.sql": "c49f1cd8545f8e01fe402ec67d4483e2092ecb2b5975d560a9df78cfeab840ef"}
SOURCES_058 = ("SHOP_SCRAP", "CUSTODY_GOLD", "CUSTODY_LOT", "HOLDING_LOT", "HOLDING_LOGO", "HOLDING_MKT", "TRIP_BOOK")


def digest(name):
    return hashlib.sha256((ROOT / "database" / "schema" / name).read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def code_of(text):
    return " ".join(line for line in text.splitlines() if not line.lstrip().startswith("--"))


class DistributionSchemaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.model = schema_model.build()
        cls.forward = sql.read(FORWARD)
        cls.rollback = sql.read(ROLLBACK, sql.ROLLBACK_DIR)
        cls.m058 = sql.read("058_market_trip_book_cycle.sql")
        cls.m060 = sql.read("060_market_trip_settlement_melt.sql")

    def test_the_chain_replays_cleanly_and_the_link_table_exists(self):
        self.assertEqual(self.model.problems, [])
        self.assertEqual(self.model.tables["market_trip_bar_supplier_links"]["source"], FORWARD)

    def test_it_requires_060_and_registers_061(self):
        self.assertIn("version=60", self.forward.replace(" ", ""))
        self.assertIn("MIGRATION_061_REFUSED_missing_060", self.forward)
        self.assertRegex(self.forward, r"INSERT INTO schema_migrations\(version,name\) VALUES \(61,")
        self.assertLessEqual(len(re.search(r"VALUES \(61,'([^']*)'", self.forward).group(1)), 150)         # schema_migrations.name is VARCHAR(150)

    def test_earlier_migrations_are_untouched_and_the_manifest_matches(self):
        for name, expected in FROZEN.items():
            self.assertEqual(digest(name), expected, name)
        files = {f.name: f for f in load_chain(require_committed=False)}
        self.assertEqual(len(files), 72)
        self.assertEqual(files[FORWARD].sha256, digest(FORWARD))
        self.assertIn(f"{digest(FORWARD)}  {FORWARD}", (ROOT / "database" / "schema" / "SHA256SUMS").read_text(encoding="utf-8"))

    def test_the_source_vocabulary_adds_trip_bar_to_the_document_and_the_leg(self):
        for constraint, rollback_text in (("chk_sgs_source", self.rollback), ("chk_sgsl_source", self.rollback)):
            added = re.search(rf"ADD CONSTRAINT {constraint} CHECK \(source_kind IN \(([^)]*)\)\)", self.forward).group(1)
            self.assertEqual(re.findall(r"'(\w+)'", added), list(SOURCES_058) + ["TRIP_BAR"], constraint)
            restored = re.search(rf"ADD CONSTRAINT {constraint} CHECK \(source_kind IN \(([^)]*)\)\)", rollback_text).group(1)
            self.assertEqual(re.findall(r"'(\w+)'", restored), list(SOURCES_058), constraint)             # the rollback restores the 058 vocabulary

    def test_the_document_shape_is_the_058_shape_with_trip_bar_in_the_custody_gold_branch_only(self):
        old = re.search(r'ADD CONSTRAINT chk_sgs_shape CHECK \((.*)\)", \'DO 0\'\);', self.m058).group(1)
        new = re.search(r'ADD CONSTRAINT chk_sgs_shape CHECK \((.*)\)", \'DO 0\'\);', self.forward).group(1)
        self.assertEqual(new.replace("'SHOP_SCRAP','CUSTODY_GOLD','TRIP_BAR'", "'SHOP_SCRAP','CUSTODY_GOLD'"), old)         # nothing else changed
        restored = re.search(r'ADD CONSTRAINT chk_sgs_shape CHECK \((.*)\)", \'DO 0\'\);', self.rollback).group(1)
        self.assertEqual(restored, old)

    def test_the_kind_rules_add_st_bar_sup_to_legacy_only_and_the_pool_ledger(self):
        old_kinds = re.findall(r"'(\w+)'", re.search(r"ADD CONSTRAINT chk_mte_kind CHECK \(entry_kind IN \(([^)]*)\)\)", self.m060).group(1))
        kinds = re.findall(r"'(\w+)'", re.search(r"ADD CONSTRAINT chk_mte_kind CHECK \(entry_kind IN \(([^)]*)\)\)", self.forward).group(1))
        self.assertEqual(kinds, old_kinds + ["ST_BAR_SUP"])
        self.assertLessEqual(len("ST_BAR_SUP"), 12)                                                  # entry_kind is VARCHAR(12)
        rule = re.search(r"ADD CONSTRAINT chk_mte_mode_kind CHECK \((.*?)\)\", 'DO 0'\);", self.forward, re.S).group(1)
        legacy = re.findall(r"'(\w+)'", re.search(r"trip_mode = 'LEGACY' AND entry_kind IN \(([^)]*)\)", rule).group(1))
        book = re.findall(r"'(\w+)'", re.search(r"trip_mode = 'BOOK' AND entry_kind IN \(([^)]*)\)", rule).group(1))
        self.assertIn("ST_BAR_SUP", legacy)
        self.assertNotIn("ST_BAR_SUP", book)
        self.assertIn("entry_kind IN ('ST_MELT','ST_BAR_SUP')", self.forward)                           # the pool ledger accepts the bar delivery move
        back = re.findall(r"'(\w+)'", re.search(r"ADD CONSTRAINT chk_mte_kind CHECK \(entry_kind IN \(([^)]*)\)\)", self.rollback).group(1))
        self.assertEqual(back, old_kinds)

    def test_unmelted_gold_may_be_delivered_and_the_rollback_restores_logo_only(self):
        self.assertIn("ADD CONSTRAINT chk_mtgs_designation CHECK (designation IN ('LOGO','UNCLASSIFIED'))", self.forward)
        self.assertIn("ADD CONSTRAINT chk_mtgs_designation CHECK (designation IN ('LOGO'))", self.rollback)

    def test_the_link_table_ties_the_entry_to_the_document(self):
        match = re.search(r"CREATE TABLE IF NOT EXISTS market_trip_bar_supplier_links \((.*?)\) ENGINE=InnoDB", self.forward, re.S).group(1)
        for needle in ("PRIMARY KEY (entry_id)", "entry_kind VARCHAR(12) NOT NULL DEFAULT 'ST_BAR_SUP'", "source_kind VARCHAR(12) NOT NULL DEFAULT 'TRIP_BAR'",
                       "UNIQUE KEY uq_mtbarl_settlement (settlement_id)", "FOREIGN KEY (entry_id, entry_kind, trip_id) REFERENCES market_trip_entries(id, entry_kind, trip_id)",
                       "FOREIGN KEY (settlement_id, source_kind, supplier_id, weight_grams) REFERENCES supplier_gold_settlements(id, source_kind, supplier_id, delivered_weight_grams)",
                       "entry_kind = 'ST_BAR_SUP' AND source_kind = 'TRIP_BAR' AND weight_grams > 0"):
            self.assertIn(needle, match)
        self.assertIn("uq_sgs_book_link (id, source_kind, supplier_id, delivered_weight_grams)", self.m058)     # the key the document FK references exists

    def test_no_trigger_and_the_rollback_refuses_before_any_drop(self):
        self.assertNotIn("TRIGGER", code_of(self.forward).upper())
        code = code_of(self.rollback)
        for needle in ("ROLLBACK_061_REFUSED_settlement_distribution_history_exists", "entry_kind = 'ST_BAR_SUP'", "FROM market_trip_bar_supplier_links",
                       "designation = 'UNCLASSIFIED'", "source_kind = 'TRIP_BAR'", "version > 61", "DELETE FROM schema_migrations WHERE version = 61"):
            self.assertIn(needle, code)
        self.assertLess(code.index("ROLLBACK_061_REFUSED"), code.index("DROP TABLE IF EXISTS market_trip_bar_supplier_links"))


if __name__ == "__main__":
    unittest.main()
