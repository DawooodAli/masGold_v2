"""Migration 042 (Logo heating): static replay and contract of the forward and rollback files.
No database is used; the live behaviour (constraints, refusals, rollback) was proven by the rehearsal on masgold_rehearsal."""
import hashlib
from pathlib import Path
import re
import unittest

from tests import migration_sql as sql
from tests import schema_model
from tools.schema_rehearsal.chain import load_chain

ROOT = Path(__file__).resolve().parents[1]
FORWARD = "042_market_trip_heating.sql"
ROLLBACK = "042_market_trip_heating_rollback.sql"
FROZEN = {"040_market_trip_lot_returns.sql": "358dc2c2c79b20e08ede28b64624e1f93c785eb774795f4492dc7d8f5d737fed",
          "041_market_trip_lot_reissue.sql": "8d3bb3f2d335b85478e4ef8d75f5afb3dcc7bd3d3270c31c217b2980c528be8e"}


def digest(name):
    return hashlib.sha256((ROOT / "database" / "schema" / name).read_bytes().replace(b"\r\n", b"\n")).hexdigest()


class HeatingSchemaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.model = schema_model.build()
        cls.forward = sql.read(FORWARD)
        cls.rollback = sql.read(ROLLBACK, sql.ROLLBACK_DIR)
        cls.statements = [s.sql for s in sql.statements(cls.forward)]

    def test_the_chain_replays_cleanly_and_both_tables_exist(self):
        self.assertEqual(self.model.problems, [])
        for table in ("market_trip_heats", "market_trip_heat_lines"):
            self.assertEqual(self.model.tables[table]["source"], FORWARD)

    def test_it_requires_041_and_registers_042(self):
        self.assertIn("version=41", self.forward.replace(" ", ""))
        self.assertIn("MIGRATION_042_REFUSED_missing_041", self.forward)
        self.assertRegex(self.forward, r"INSERT INTO schema_migrations\(version,name\) VALUES \(42,")

    def test_earlier_migrations_are_untouched_and_the_manifest_matches(self):
        for name, expected in FROZEN.items():
            self.assertEqual(digest(name), expected, name)
        files = {f.name: f for f in load_chain(require_committed=False)}
        self.assertEqual(len(files), 54)
        self.assertEqual(files[FORWARD].sha256, digest(FORWARD))
        self.assertIn(f"{digest(FORWARD)}  {FORWARD}", (ROOT / "database" / "schema" / "SHA256SUMS").read_text(encoding="utf-8"))

    def test_only_the_kind_check_changes_and_no_row_is_rewritten(self):
        for statement in self.statements:
            self.assertNotRegex(statement, r"^(UPDATE|DELETE|TRUNCATE|DROP TABLE)\b")
            target = re.match(r"^INSERT INTO\s+(\w+)", statement, re.I)
            if target:
                self.assertEqual(target.group(1), "schema_migrations")
        self.assertEqual({sql.table_of(s) for s in self.statements if s.upper().startswith("ALTER")}, {"market_trip_entries"})
        for statement in (s for s in sql.statements(self.forward) if s.sql.upper().startswith("ALTER")):
            self.assertTrue(statement.guarded, statement.sql[:80])
        self.assertIn("CHECK (entry_kind IN ('ISSUE','RETURN','MELT','LOT_RETURN','LOT_ISSUE','HEAT'))", self.forward)
        self.assertLessEqual(len("HEAT"), 12)

    def test_the_header_and_lines_keep_only_physical_weights(self):
        for needle in ("workshop VARCHAR(120) NOT NULL", "CHAR_LENGTH(TRIM(workshop)) > 0", "total_difference_grams = total_output_grams - total_input_grams",
                       "total_input_grams > 0 AND total_output_grams > 0", "difference_grams = output_weight_grams - input_weight_grams",
                       "input_weight_grams > 0 AND output_weight_grams > 0", "CHECK (designation = 'LOGO')",
                       "UNIQUE KEY uq_mthl_entry_karat (entry_id, karat_id)", "REFERENCES market_trip_heats(entry_id)",
                       "REFERENCES market_trip_entries(id)", "REFERENCES karats(id)"):
            self.assertIn(needle, self.forward)
        columns = str(self.model.tables["market_trip_heat_lines"]["columns"]).upper().replace(" ", "")
        self.assertEqual(columns.count("DECIMAL(12,3)"), 3)
        code = " ".join(line for line in self.forward.splitlines() if not line.lstrip().startswith("--"))      # comments may name what is NOT stored
        for forbidden in ("pure", "eq21", "fineness", "purity", "FLOAT", "DOUBLE", "ON DELETE", "golddb", "cash_movements", "old_gold_movements",
                          "supplier_", "inventory_items", "market_trip_lots", "market_trip_melts"):
            self.assertNotIn(forbidden.lower(), code.lower(), forbidden)

    def test_the_rollback_refuses_on_any_heating_data_or_a_later_migration_and_restores_the_041_rule(self):
        self.assertIn("ROLLBACK_042_REFUSED_heating_data_or_later_migration", self.rollback)
        for needle in ("FROM market_trip_heat_lines", "FROM market_trip_heats", "entry_kind = 'HEAT'", "version>42"):
            self.assertIn(needle, self.rollback)
        self.assertLess(self.rollback.index("ROLLBACK_042_REFUSED"), self.rollback.index("DROP TABLE"))
        self.assertLess(self.rollback.index("DROP TABLE market_trip_heat_lines"), self.rollback.index("DROP TABLE market_trip_heats"))
        self.assertIn("CHECK (entry_kind IN ('ISSUE','RETURN','MELT','LOT_RETURN','LOT_ISSUE'))", self.rollback)
        self.assertIn("DELETE FROM schema_migrations WHERE version=42", self.rollback)


if __name__ == "__main__":
    unittest.main()
