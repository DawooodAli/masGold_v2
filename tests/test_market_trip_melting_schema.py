"""Migration 039 (market-trip melting): static replay and contract of the forward and rollback files.
No database is used; the live behaviour (constraints, defaults, refusals, rollback) was proven by the rehearsal on masgold_rehearsal."""
import hashlib
from pathlib import Path
import re
import unittest

from tests import migration_sql as sql
from tests import schema_model
from tools.schema_rehearsal.chain import load_chain

ROOT = Path(__file__).resolve().parents[1]
FORWARD = "039_market_trip_melting.sql"
ROLLBACK = "039_market_trip_melting_rollback.sql"
TABLES = ["market_trip_melts", "market_trip_melt_inputs", "market_trip_lots", "market_trip_lot_movements"]
FROZEN = {"037_market_trip_custody.sql": "9fdcfaf0abc2b3126df55edadd5e89ceeb26c43d30cbb020df704235e91f9fdb",
          "038_karat_22.sql": "3c51cff1500c79ebe0ffb5f16b929ba2e06693505297740a5aa20e3a2631a7a1"}


def digest(name):
    return hashlib.sha256((ROOT / "database" / "schema" / name).read_bytes().replace(b"\r\n", b"\n")).hexdigest()


class MeltingSchemaTests(unittest.TestCase):
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
        self.assertIn("designation", self.model.tables["market_trip_entry_gold"]["columns"])

    def test_it_requires_038_and_registers_039(self):
        self.assertIn("version=38", self.forward.replace(" ", ""))
        self.assertIn("MIGRATION_039_REFUSED_missing_038", self.forward)
        self.assertRegex(self.forward, r"INSERT INTO schema_migrations\(version,name\) VALUES \(39,")

    def test_earlier_migrations_are_untouched_and_the_manifest_matches(self):
        for name, expected in FROZEN.items():
            self.assertEqual(digest(name), expected, name)
        files = {f.name: f for f in load_chain(require_committed=False)}
        self.assertEqual(len(files), 46)
        self.assertEqual(files[FORWARD].sha256, digest(FORWARD))
        self.assertIn(f"{digest(FORWARD)}  {FORWARD}", (ROOT / "database" / "schema" / "SHA256SUMS").read_text(encoding="utf-8"))

    def test_the_designation_defaults_to_unclassified_and_no_historical_row_is_rewritten(self):
        self.assertIn("designation VARCHAR(12) NOT NULL DEFAULT 'UNCLASSIFIED'", self.forward)
        self.assertIn("CHECK (designation IN ('MELT','LOGO','UNCLASSIFIED'))", self.forward)
        for statement in self.statements:
            self.assertNotRegex(statement, r"^(UPDATE|DELETE|TRUNCATE|DROP TABLE)\b")
            target = re.match(r"^INSERT INTO\s+(\w+)", statement, re.I)
            if target:
                self.assertEqual(target.group(1), "schema_migrations")
        altered = {sql.table_of(s) for s in self.statements if s.upper().startswith("ALTER")}
        self.assertEqual(altered, {"market_trip_entry_gold", "market_trip_entries"})

    def test_one_leg_per_entry_karat_and_designation_with_the_new_key_added_before_the_old_is_dropped(self):
        self.assertIn("ADD UNIQUE KEY uq_mteg_entry_karat_des (entry_id, karat_id, designation)", self.forward)
        self.assertIn("DROP INDEX uq_mteg_entry_karat", self.forward)
        self.assertLess(self.forward.index("ADD UNIQUE KEY uq_mteg_entry_karat_des"), self.forward.index("DROP INDEX uq_mteg_entry_karat"))

    def test_the_melt_entry_kind_is_added_to_the_entry_check(self):
        self.assertIn("CHECK (entry_kind IN ('ISSUE','RETURN','MELT'))", self.forward)

    def test_every_alteration_is_guarded_for_a_safe_rerun(self):
        alters = [s for s in sql.statements(self.forward) if s.sql.upper().startswith("ALTER")]
        self.assertTrue(alters)
        for statement in alters:
            self.assertTrue(statement.guarded, statement.sql[:80])
        self.assertEqual(len(re.findall(r"CREATE TABLE IF NOT EXISTS", self.forward)), 4)

    def test_quantities_are_exact_decimal_with_the_existing_precision(self):
        columns = {t: self.model.tables[t]["columns"] for t in TABLES}
        for column in ("input_weight_grams", "output_weight_grams", "weight_difference_grams"):
            self.assertIn("DECIMAL(12,3)", str(columns["market_trip_melts"][column]).upper().replace(" ", ""))
        for column in ("nominal_pure_grams", "known_output_pure_grams", "pure_difference_grams", "nominal_eq21_grams",
                       "known_output_eq21_grams", "eq21_difference_grams"):
            self.assertIn("DECIMAL(18,6)", str(columns["market_trip_melts"][column]).upper().replace(" ", ""))        # the analytical 6 dp
        self.assertIn("DECIMAL(5,1)", str(columns["market_trip_lots"]["fineness_permille"]).upper().replace(" ", ""))   # parts per thousand, 0.1
        self.assertNotRegex(self.forward, r"\b(FLOAT|DOUBLE)\b")

    def test_checks_guard_weights_fineness_the_analysis_and_the_lot_kind(self):
        for needle in ("chk_mtm_workshop", "chk_mtm_weights", "chk_mtm_gain", "chk_mtm_complete", "chk_mtmi_weight", "chk_mtl_kind",
                       "chk_mtl_weight", "chk_mtl_fineness", "chk_mtlm_type", "chk_mtlm_delta"):
            self.assertIn(needle, self.forward)
        self.assertIn("fineness_permille > 0 AND fineness_permille <= 1000", self.forward)
        self.assertIn("lot_kind <> 'MAIN' OR fineness_permille IS NOT NULL", self.forward)               # the main output is always assayed
        self.assertIn("(lot_kind = 'MAIN') = (lot_no = 1)", self.forward)                                  # exactly one MAIN lot, lot 1
        self.assertIn("weight_difference_grams = output_weight_grams - input_weight_grams", self.forward)
        self.assertIn("output_weight_grams <= input_weight_grams", self.forward)                           # a gain needs its explanation
        self.assertIn("(pure_complete = 1) = (pure_difference_grams IS NOT NULL)", self.forward)           # incomplete assays: no pure difference

    def test_movements_and_lots_are_insert_only_documents_bound_to_entries(self):
        for needle in ("CONSTRAINT fk_mtm_entry FOREIGN KEY (entry_id) REFERENCES market_trip_entries(id)",
                       "CONSTRAINT fk_mtmi_melt FOREIGN KEY (entry_id) REFERENCES market_trip_melts(entry_id)",
                       "CONSTRAINT fk_mtl_melt FOREIGN KEY (entry_id) REFERENCES market_trip_melts(entry_id)",
                       "CONSTRAINT fk_mtlm_lot FOREIGN KEY (lot_id) REFERENCES market_trip_lots(id)",
                       "CONSTRAINT fk_mtlm_entry FOREIGN KEY (entry_id) REFERENCES market_trip_entries(id)",
                       "UNIQUE KEY uq_mtmi_entry_karat (entry_id, karat_id)", "UNIQUE KEY uq_mtl_entry_no (entry_id, lot_no)",
                       "UNIQUE KEY uq_mtlm_lot_entry_type (lot_id, entry_id, movement_type)"):
            self.assertIn(needle, self.forward)
        self.assertNotIn("ON DELETE", self.forward)

    def test_no_cash_scrap_supplier_or_golddb_object_is_touched(self):
        for forbidden in ("golddb", "DROP DATABASE", "cash_movements", "old_gold_movements", "supplier_", "inventory_items"):
            self.assertNotIn(forbidden, self.forward)

    def test_the_rollback_refuses_on_any_melting_data_or_a_later_migration_and_drops_children_first(self):
        self.assertIn("ROLLBACK_039_REFUSED_melting_data_or_later_migration", self.rollback)
        for needle in ("FROM market_trip_melts", "FROM market_trip_lots", "FROM market_trip_lot_movements", "FROM market_trip_melt_inputs",
                       "entry_kind = 'MELT'", "designation <> 'UNCLASSIFIED'", "version>39"):
            self.assertIn(needle, self.rollback)
        order = [self.rollback.index(f"DROP TABLE {t};") for t in ("market_trip_lot_movements", "market_trip_lots", "market_trip_melt_inputs",
                                                                  "market_trip_melts")]
        self.assertEqual(order, sorted(order))
        self.assertLess(self.rollback.index("ROLLBACK_039_REFUSED"), self.rollback.index("DROP TABLE"))
        self.assertLess(self.rollback.index("ADD UNIQUE KEY uq_mteg_entry_karat "), self.rollback.index("DROP INDEX uq_mteg_entry_karat_des"))
        self.assertLess(self.rollback.index("DROP INDEX uq_mteg_entry_karat_des"), self.rollback.index("DROP COLUMN designation"))
        self.assertIn("CHECK (entry_kind IN ('ISSUE','RETURN'))", self.rollback)
        self.assertIn("DELETE FROM schema_migrations WHERE version=39", self.rollback)


if __name__ == "__main__":
    unittest.main()
