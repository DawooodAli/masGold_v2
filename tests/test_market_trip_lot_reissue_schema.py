"""Migration 041 (market-trip lot reissue): static replay and contract of the forward and rollback files.
No database is used; the live behaviour (constraints, refusals, rollback) was proven by the rehearsal on masgold_rehearsal."""
import hashlib
from pathlib import Path
import re
import unittest

from tests import migration_sql as sql
from tests import schema_model
from tools.schema_rehearsal.chain import load_chain

ROOT = Path(__file__).resolve().parents[1]
FORWARD = "041_market_trip_lot_reissue.sql"
ROLLBACK = "041_market_trip_lot_reissue_rollback.sql"
FROZEN = {"039_market_trip_melting.sql": "5fb7ac9695f1b4b671854ee44df2fe626d41354bf0d88a79a81a571ba1c9d13a",
          "040_market_trip_lot_returns.sql": "358dc2c2c79b20e08ede28b64624e1f93c785eb774795f4492dc7d8f5d737fed"}


def digest(name):
    return hashlib.sha256((ROOT / "database" / "schema" / name).read_bytes().replace(b"\r\n", b"\n")).hexdigest()


class LotReissueSchemaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.model = schema_model.build()
        cls.forward = sql.read(FORWARD)
        cls.rollback = sql.read(ROLLBACK, sql.ROLLBACK_DIR)
        cls.statements = [s.sql for s in sql.statements(cls.forward)]

    def test_the_chain_replays_cleanly_and_the_issue_table_exists(self):
        self.assertEqual(self.model.problems, [])
        self.assertEqual(self.model.tables["market_trip_lot_issues"]["source"], FORWARD)

    def test_it_requires_040_and_registers_041(self):
        self.assertIn("version=40", self.forward.replace(" ", ""))
        self.assertIn("MIGRATION_041_REFUSED_missing_040", self.forward)
        self.assertRegex(self.forward, r"INSERT INTO schema_migrations\(version,name\) VALUES \(41,")

    def test_earlier_migrations_are_untouched_and_the_manifest_matches(self):
        for name, expected in FROZEN.items():
            self.assertEqual(digest(name), expected, name)
        files = {f.name: f for f in load_chain(require_committed=False)}
        self.assertEqual(len(files), 46)
        self.assertEqual(files[FORWARD].sha256, digest(FORWARD))
        self.assertIn(f"{digest(FORWARD)}  {FORWARD}", (ROOT / "database" / "schema" / "SHA256SUMS").read_text(encoding="utf-8"))

    def test_only_checks_and_one_new_table_change_and_no_row_is_rewritten(self):
        for statement in self.statements:
            self.assertNotRegex(statement, r"^(UPDATE|DELETE|TRUNCATE|DROP TABLE)\b")
            target = re.match(r"^INSERT INTO\s+(\w+)", statement, re.I)
            if target:
                self.assertEqual(target.group(1), "schema_migrations")
        altered = {sql.table_of(s) for s in self.statements if s.upper().startswith("ALTER")}
        self.assertEqual(altered, {"market_trip_entries", "market_trip_lot_movements", "market_trip_lot_shop_movements"})
        for statement in (s for s in sql.statements(self.forward) if s.sql.upper().startswith("ALTER")):
            self.assertTrue(statement.guarded, statement.sql[:80])

    def test_the_new_values_keep_every_earlier_one_and_have_the_right_signs(self):
        self.assertIn("CHECK (entry_kind IN ('ISSUE','RETURN','MELT','LOT_RETURN','LOT_ISSUE'))", self.forward)
        self.assertIn("CHECK (movement_type IN ('CREATE','SHOP_RETURN','SHOP_ISSUE'))", self.forward)
        self.assertIn("(movement_type <> 'SHOP_ISSUE' OR weight_delta > 0)", self.forward)               # a bar entering a custody: positive
        for kept in ("(movement_type <> 'CREATE' OR weight_delta > 0)", "(movement_type <> 'SHOP_RETURN' OR weight_delta < 0)"):
            self.assertIn(kept, self.forward)
        for shape in ("movement_type = 'TRIP_RETURN' AND weight_delta > 0 AND correction_id IS NULL",
                      "movement_type = 'RETURN_REVERSAL' AND weight_delta < 0 AND correction_id IS NOT NULL",
                      "movement_type = 'TRIP_ISSUE' AND weight_delta < 0 AND correction_id IS NULL",
                      "movement_type = 'ISSUE_REVERSAL' AND weight_delta > 0 AND correction_id IS NOT NULL"):
            self.assertIn(shape, self.forward)
        self.assertLessEqual(len("LOT_ISSUE"), 12)                                                       # fits the widened entry_kind
        self.assertLessEqual(len("ISSUE_REVERSAL"), 20)                                                   # fits movement_type VARCHAR(20)

    def test_an_issue_line_links_the_entry_the_lot_and_both_movements_once(self):
        for needle in ("UNIQUE KEY uq_mtli_entry_lot (entry_id, lot_id)", "UNIQUE KEY uq_mtli_custody (custody_movement_id)",
                       "UNIQUE KEY uq_mtli_shop (shop_movement_id)", "CHECK (weight_grams > 0)",
                       "REFERENCES market_trip_lot_movements(id)", "REFERENCES market_trip_lot_shop_movements(id)",
                       "REFERENCES market_trip_lots(id)", "REFERENCES market_trip_entries(id)"):
            self.assertIn(needle, self.forward)
        self.assertNotIn("ON DELETE", self.forward)
        self.assertIn("DECIMAL(12,3)", str(self.model.tables["market_trip_lot_issues"]["columns"]["weight_grams"]).upper().replace(" ", ""))
        self.assertNotRegex(self.forward, r"\b(FLOAT|DOUBLE)\b")

    def test_no_cash_scrap_supplier_inventory_or_golddb_object_is_touched(self):
        for forbidden in ("golddb", "DROP DATABASE", "cash_movements", "old_gold_movements", "supplier_", "inventory_items", "karats"):
            self.assertNotIn(forbidden, self.forward)

    def test_the_rollback_refuses_on_any_reissue_data_or_a_later_migration_and_restores_the_040_rules(self):
        self.assertIn("ROLLBACK_041_REFUSED_lot_issue_data_or_later_migration", self.rollback)
        for needle in ("FROM market_trip_lot_issues", "movement_type IN ('TRIP_ISSUE','ISSUE_REVERSAL')", "movement_type = 'SHOP_ISSUE'",
                       "entry_kind = 'LOT_ISSUE'", "version>41"):
            self.assertIn(needle, self.rollback)
        self.assertLess(self.rollback.index("ROLLBACK_041_REFUSED"), self.rollback.index("DROP TABLE"))
        self.assertIn("CHECK (movement_type IN ('CREATE','SHOP_RETURN'))", self.rollback)
        self.assertIn("CHECK (entry_kind IN ('ISSUE','RETURN','MELT','LOT_RETURN'))", self.rollback)
        self.assertNotIn("TRIP_ISSUE", self.rollback.split("ADD CONSTRAINT chk_mtlsm_shape")[1].split(";")[0])      # the 040 shape is restored
        self.assertIn("DELETE FROM schema_migrations WHERE version=41", self.rollback)


if __name__ == "__main__":
    unittest.main()
