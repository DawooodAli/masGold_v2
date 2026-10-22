"""Migration 062 (settlement step 2c: the melted bar handed to a marketer and the unmelted remainder classified as LOGO): static replay and contract of the forward and rollback
files. No database is used; the live behaviour (constraints, refusals, rollback) is NOT proven here and still needs an approved rehearsal run."""
import hashlib
from pathlib import Path
import re
import unittest

from tests import migration_sql as sql
from tests import schema_model
from tools.schema_rehearsal.chain import load_chain

ROOT = Path(__file__).resolve().parents[1]
FORWARD = "062_market_trip_settlement_marketer.sql"
ROLLBACK = "062_market_trip_settlement_marketer_rollback.sql"
FROZEN = {"061_market_trip_settlement_distribution.sql": "9e034cf654f0e0ad33c3f1b34dba798e12b330057826a4d081fd5411528d95df",
          "060_market_trip_settlement_melt.sql": "271f0b9cf61f932665c088f111bca014537d6e5d46d01ac7737e0d131b9b15d0",
          "058_market_trip_book_cycle.sql": "c49f1cd8545f8e01fe402ec67d4483e2092ecb2b5975d560a9df78cfeab840ef"}


def digest(name):
    return hashlib.sha256((ROOT / "database" / "schema" / name).read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def code_of(text):
    return " ".join(line for line in text.splitlines() if not line.lstrip().startswith("--"))


def kinds(text):
    return re.findall(r"'(\w+)'", re.search(r"ADD CONSTRAINT chk_mte_kind CHECK \(entry_kind IN \(([^)]*)\)\)", text).group(1))


class MarketerLogoSchemaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.model = schema_model.build()
        cls.forward = sql.read(FORWARD)
        cls.rollback = sql.read(ROLLBACK, sql.ROLLBACK_DIR)
        cls.m058 = sql.read("058_market_trip_book_cycle.sql")
        cls.m061 = sql.read("061_market_trip_settlement_distribution.sql")

    def test_the_chain_replays_cleanly(self):
        self.assertEqual(self.model.problems, [])

    def test_it_requires_061_and_registers_062(self):
        self.assertIn("version=61", self.forward.replace(" ", ""))
        self.assertIn("MIGRATION_062_REFUSED_missing_061", self.forward)
        self.assertRegex(self.forward, r"INSERT INTO schema_migrations\(version,name\) VALUES \(62,")
        self.assertLessEqual(len(re.search(r"VALUES \(62,'([^']*)'", self.forward).group(1)), 150)         # schema_migrations.name is VARCHAR(150)

    def test_earlier_migrations_are_untouched_and_the_manifest_matches(self):
        for name, expected in FROZEN.items():
            self.assertEqual(digest(name), expected, name)
        files = {f.name: f for f in load_chain(require_committed=False)}
        self.assertEqual(len(files), 70)
        self.assertEqual(files[FORWARD].sha256, digest(FORWARD))
        self.assertIn(f"{digest(FORWARD)}  {FORWARD}", (ROOT / "database" / "schema" / "SHA256SUMS").read_text(encoding="utf-8"))

    def test_the_kind_rules_add_the_two_kinds_to_legacy_only_and_the_rollback_restores_061(self):
        self.assertEqual(kinds(self.forward), kinds(self.m061) + ["ST_BAR_MKT", "ST_LOGO"])
        self.assertEqual(kinds(self.rollback), kinds(self.m061))
        self.assertLessEqual(max(len("ST_BAR_MKT"), len("ST_LOGO")), 12)                                # entry_kind is VARCHAR(12)
        rule = re.search(r"ADD CONSTRAINT chk_mte_mode_kind CHECK \((.*?)\)\", 'DO 0'\);", self.forward, re.S).group(1)
        legacy = re.findall(r"'(\w+)'", re.search(r"trip_mode = 'LEGACY' AND entry_kind IN \(([^)]*)\)", rule).group(1))
        book = re.findall(r"'(\w+)'", re.search(r"trip_mode = 'BOOK' AND entry_kind IN \(([^)]*)\)", rule).group(1))
        for kind in ("ST_BAR_MKT", "ST_LOGO"):
            self.assertIn(kind, legacy)
            self.assertNotIn(kind, book)

    def test_the_pool_ledger_gains_the_logo_pool_and_the_two_kinds(self):
        self.assertIn("entry_kind IN ('ST_MELT','ST_BAR_SUP','ST_BAR_MKT','ST_LOGO')", self.forward)
        self.assertIn("(pool = 'RAW' AND karat_id IS NOT NULL) OR (pool = 'LOGO' AND karat_id IS NOT NULL) OR (pool = 'BAR' AND karat_id IS NULL)", self.forward)
        self.assertIn("ADD CONSTRAINT chk_mtsm_pool CHECK ((pool = 'RAW' AND karat_id IS NOT NULL) OR (pool = 'BAR' AND karat_id IS NULL))", self.rollback)
        self.assertIn("ADD CONSTRAINT chk_mtsm_kind CHECK (entry_kind IN ('ST_MELT','ST_BAR_SUP'))", self.rollback)

    def test_the_marketer_shape_is_the_058_shape_with_the_new_kind_in_the_in_leg_only(self):
        old = re.search(r'ADD CONSTRAINT chk_mkgm_shape CHECK \((.*)\)", \'DO 0\'\);', self.m058).group(1)
        new = re.search(r'ADD CONSTRAINT chk_mkgm_shape CHECK \((.*)\)", \'DO 0\'\);', self.forward).group(1)
        self.assertEqual(new.count("trip_entry_kind IN ('BK_DLV_MKT','ST_BAR_MKT')"), 2)                      # TRIP_BOOK_IN and its reversal
        self.assertEqual(new.replace("trip_entry_kind IN ('BK_DLV_MKT','ST_BAR_MKT')", "trip_entry_kind = 'BK_DLV_MKT'"), old)       # nothing else changed
        self.assertNotIn("ST_BAR_MKT", re.search(r"movement_type = 'TRIP_BOOK_OUT'.*?\)", new).group(0))      # the receipt (OUT) leg stays BOOK only
        restored = re.search(r'ADD CONSTRAINT chk_mkgm_shape CHECK \((.*)\)", \'DO 0\'\);', self.rollback).group(1)
        self.assertEqual(restored, old)

    def test_no_trigger_and_the_rollback_refuses_before_any_change(self):
        self.assertNotIn("TRIGGER", code_of(self.forward).upper())
        code = code_of(self.rollback)
        for needle in ("ROLLBACK_062_REFUSED_marketer_logo_history_exists", "entry_kind = 'ST_BAR_MKT'", "entry_kind = 'ST_LOGO'", "trip_entry_kind = 'ST_BAR_MKT'",
                       "version > 62", "DELETE FROM schema_migrations WHERE version = 62"):
            self.assertIn(needle, code)
        self.assertLess(code.index("ROLLBACK_062_REFUSED"), code.index("DROP CHECK"))


if __name__ == "__main__":
    unittest.main()
