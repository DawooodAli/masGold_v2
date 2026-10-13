"""Migration 040 (market-trip lot returns): static replay and contract of the forward and rollback files.
No database is used; the live behaviour (constraints, refusals, rollback) was proven by the rehearsal on masgold_rehearsal."""
import hashlib
from pathlib import Path
import re
import unittest

from tests import migration_sql as sql
from tests import schema_model
from tools.schema_rehearsal.chain import load_chain

ROOT = Path(__file__).resolve().parents[1]
FORWARD = "040_market_trip_lot_returns.sql"
ROLLBACK = "040_market_trip_lot_returns_rollback.sql"
TABLES = ["market_trip_lot_shop_movements", "market_trip_lot_returns"]
FROZEN = {"038_karat_22.sql": "3c51cff1500c79ebe0ffb5f16b929ba2e06693505297740a5aa20e3a2631a7a1",
          "039_market_trip_melting.sql": "5fb7ac9695f1b4b671854ee44df2fe626d41354bf0d88a79a81a571ba1c9d13a"}


def digest(name):
    return hashlib.sha256((ROOT / "database" / "schema" / name).read_bytes().replace(b"\r\n", b"\n")).hexdigest()


class LotReturnSchemaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.model = schema_model.build()
        cls.forward = sql.read(FORWARD)
        cls.rollback = sql.read(ROLLBACK, sql.ROLLBACK_DIR)
        cls.statements = [s.sql for s in sql.statements(cls.forward)]

    def test_the_chain_replays_cleanly_and_the_new_tables_exist(self):
        self.assertEqual(self.model.problems, [])
        for table in TABLES:
            self.assertEqual(self.model.tables[table]["source"], FORWARD)

    def test_it_requires_039_and_registers_040(self):
        self.assertIn("version=39", self.forward.replace(" ", ""))
        self.assertIn("MIGRATION_040_REFUSED_missing_039", self.forward)
        self.assertRegex(self.forward, r"INSERT INTO schema_migrations\(version,name\) VALUES \(40,")

    def test_earlier_migrations_are_untouched_and_the_manifest_matches(self):
        for name, expected in FROZEN.items():
            self.assertEqual(digest(name), expected, name)
        files = {f.name: f for f in load_chain(require_committed=False)}
        self.assertEqual(len(files), 50)
        self.assertEqual(files[FORWARD].sha256, digest(FORWARD))
        self.assertIn(f"{digest(FORWARD)}  {FORWARD}", (ROOT / "database" / "schema" / "SHA256SUMS").read_text(encoding="utf-8"))

    def test_only_checks_and_new_tables_change_and_no_row_is_rewritten(self):
        for statement in self.statements:
            self.assertNotRegex(statement, r"^(UPDATE|DELETE|TRUNCATE|DROP TABLE)\b")
            target = re.match(r"^INSERT INTO\s+(\w+)", statement, re.I)
            if target:
                self.assertEqual(target.group(1), "schema_migrations")
        altered = {sql.table_of(s) for s in self.statements if s.upper().startswith("ALTER")}
        self.assertEqual(altered, {"market_trip_entries", "market_trip_lot_movements"})
        for statement in (s for s in sql.statements(self.forward) if s.sql.upper().startswith("ALTER")):
            self.assertTrue(statement.guarded, statement.sql[:80])

    def test_the_entry_kind_column_is_widened_for_the_new_value_without_touching_any_row(self):
        self.assertIn("MODIFY COLUMN entry_kind VARCHAR(12) NOT NULL", self.forward)
        self.assertIn("CHARACTER_MAXIMUM_LENGTH < 12", self.forward)                                    # guarded: a re-run changes nothing
        self.assertLess(self.forward.index("MODIFY COLUMN entry_kind"), self.forward.index("'ISSUE','RETURN','MELT','LOT_RETURN'"))
        self.assertGreaterEqual(len("LOT_RETURN"), 7)                                                   # the 037 width (6) could not hold it
        self.assertIn("MODIFY COLUMN entry_kind VARCHAR(6) NOT NULL", self.rollback)

    def test_the_entry_kind_and_the_custody_movement_type_gain_exactly_their_new_values(self):
        self.assertIn("CHECK (entry_kind IN ('ISSUE','RETURN','MELT','LOT_RETURN'))", self.forward)
        self.assertIn("CHECK (movement_type IN ('CREATE','SHOP_RETURN'))", self.forward)
        self.assertIn("(movement_type <> 'SHOP_RETURN' OR weight_delta < 0)", self.forward)             # custody leaves: negative grams
        self.assertIn("(movement_type <> 'CREATE' OR weight_delta > 0)", self.forward)                  # the melt's birth keeps its rule

    def test_the_shop_ledger_is_insert_only_signed_and_traceable(self):
        for needle in ("movement_type IN", "movement_type = 'TRIP_RETURN' AND weight_delta > 0 AND correction_id IS NULL",
                       "movement_type = 'RETURN_REVERSAL' AND weight_delta < 0 AND correction_id IS NOT NULL",
                       "UNIQUE KEY uq_mtlsm_lot_entry_type (lot_id, entry_id, movement_type)",
                       "FOREIGN KEY (lot_id) REFERENCES market_trip_lots(id)", "FOREIGN KEY (entry_id) REFERENCES market_trip_entries(id)",
                       "FOREIGN KEY (correction_id) REFERENCES transaction_corrections(id)"):
            self.assertIn(needle, self.forward)
        columns = self.model.tables["market_trip_lot_shop_movements"]["columns"]
        self.assertIn("DECIMAL(12,3)", str(columns["weight_delta"]).upper().replace(" ", ""))
        self.assertNotIn("updated_at", columns)
        self.assertNotRegex(self.forward, r"\b(FLOAT|DOUBLE)\b")

    def test_a_return_line_links_the_entry_the_lot_and_both_movements_once(self):
        for needle in ("UNIQUE KEY uq_mtlr_entry_lot (entry_id, lot_id)", "UNIQUE KEY uq_mtlr_custody (custody_movement_id)",
                       "UNIQUE KEY uq_mtlr_shop (shop_movement_id)", "CHECK (weight_grams > 0)",
                       "REFERENCES market_trip_lot_movements(id)", "REFERENCES market_trip_lot_shop_movements(id)",
                       "REFERENCES market_trip_lots(id)", "REFERENCES market_trip_entries(id)"):
            self.assertIn(needle, self.forward)
        self.assertNotIn("ON DELETE", self.forward)

    def test_no_cash_scrap_supplier_inventory_or_golddb_object_is_touched(self):
        for forbidden in ("golddb", "DROP DATABASE", "cash_movements", "old_gold_movements", "supplier_", "inventory_items", "karats"):
            self.assertNotIn(forbidden, self.forward)

    def test_the_rollback_refuses_on_any_lot_return_data_or_a_later_migration_and_drops_children_first(self):
        self.assertIn("ROLLBACK_040_REFUSED_lot_return_data_or_later_migration", self.rollback)
        for needle in ("FROM market_trip_lot_returns", "FROM market_trip_lot_shop_movements", "movement_type <> 'CREATE'",
                       "entry_kind = 'LOT_RETURN'", "version>40"):
            self.assertIn(needle, self.rollback)
        self.assertLess(self.rollback.index("ROLLBACK_040_REFUSED"), self.rollback.index("DROP TABLE"))
        self.assertLess(self.rollback.index("DROP TABLE market_trip_lot_returns;"), self.rollback.index("DROP TABLE market_trip_lot_shop_movements;"))
        self.assertIn("CHECK (movement_type IN ('CREATE'))", self.rollback)
        self.assertIn("CHECK (entry_kind IN ('ISSUE','RETURN','MELT'))", self.rollback)
        self.assertIn("DELETE FROM schema_migrations WHERE version=40", self.rollback)


if __name__ == "__main__":
    unittest.main()
