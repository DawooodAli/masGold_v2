"""Migration 038 (22K support): static replay and contract of the forward and rollback files.
No database is used; the live behaviour was proven by the rehearsal on masgold_rehearsal (see the task report)."""
import hashlib
from pathlib import Path
import re
import unittest

from tests import migration_sql as sql
from tests import schema_model
from tools.schema_rehearsal.chain import load_chain

ROOT = Path(__file__).resolve().parents[1]
FORWARD = "038_karat_22.sql"
ROLLBACK = "038_karat_22_rollback.sql"
FROZEN = {"035_supplier_karat.sql": "30220bd68e41f599b31ebd325f75add294f90fc8bd5f636072f6b99feb688512",
          "036_supplier_making_payments.sql": "f715fbed3968c3a7db72ee3b4c131286a973f6e86cea2197658a24d1f80d918c"}


def digest(name):
    return hashlib.sha256((ROOT / "database" / "schema" / name).read_bytes().replace(b"\r\n", b"\n")).hexdigest()


class Karat22SchemaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.model = schema_model.build()
        cls.forward = sql.read(FORWARD)
        cls.rollback = sql.read(ROLLBACK, sql.ROLLBACK_DIR)
        cls.statements = [s.sql for s in sql.statements(cls.forward)]

    def test_the_chain_replays_cleanly_and_the_provenance_column_exists(self):
        self.assertEqual(self.model.problems, [])
        self.assertIn("derived_from_quote_id", self.model.tables["gold_price_quotes"]["columns"])

    def test_it_requires_037_and_registers_038(self):
        self.assertIn("version=37", self.forward.replace(" ", ""))
        self.assertIn("MIGRATION_038_REFUSED_missing_037", self.forward)
        self.assertRegex(self.forward, r"INSERT INTO schema_migrations\(version,name\) VALUES \(38,")

    def test_earlier_migrations_are_untouched_and_the_manifest_matches(self):
        for name, expected in FROZEN.items():
            self.assertEqual(digest(name), expected, name)
        files = {f.name: f for f in load_chain(require_committed=False)}
        self.assertEqual(len(files), 42)
        self.assertEqual(files[FORWARD].sha256, digest(FORWARD))
        self.assertIn(f"{digest(FORWARD)}  {FORWARD}", (ROOT / "database" / "schema" / "SHA256SUMS").read_text(encoding="utf-8"))

    def test_the_master_row_is_inserted_only_when_absent_and_never_overwritten(self):
        inserts = [s for s in self.statements if re.match(r"^INSERT INTO\s+karats\b", s, re.I)]
        self.assertEqual(len(inserts), 1)
        self.assertIn("WHERE NOT EXISTS (SELECT 1 FROM karats WHERE karat_value = 22)", inserts[0])
        updates = [s for s in self.statements if re.match(r"^UPDATE\b", s, re.I)]
        self.assertEqual(len(updates), 1)
        # only the activation flag may change on an existing row; identity, purity and name are never rewritten
        self.assertRegex(updates[0], r"^UPDATE karats SET is_active = TRUE WHERE karat_value = 22 AND is_active = FALSE$")
        for statement in self.statements:
            self.assertNotRegex(statement, r"^(DROP TABLE|DELETE|TRUNCATE)\b")

    def test_the_outcome_and_inconsistencies_are_recorded_once(self):
        for token in ("INSERTED", "ALREADY_ACTIVE", "REACTIVATED", "EXISTING_ROW_DIFFERS_FROM_EXPECTED_PURITY_OR_NAME",
                      "KARAT_22_SUPPORT", "@k22_first"):
            self.assertIn(token, self.forward)

    def test_provenance_is_a_nullable_fk_to_a_quote_and_only_for_derived_quotes(self):
        self.assertIn("derived_from_quote_id BIGINT UNSIGNED DEFAULT NULL", self.forward)
        self.assertIn("FOREIGN KEY (derived_from_quote_id) REFERENCES gold_price_quotes(id)", self.forward)
        self.assertIn("CHECK (derived_from_quote_id IS NULL OR quote_method = 'DERIVED')", self.forward)
        altered = {sql.table_of(s) for s in self.statements if s.upper().startswith("ALTER")}
        self.assertEqual(altered, {"gold_price_quotes"})

    def test_every_alteration_is_guarded_for_a_safe_rerun(self):
        guarded = [s for s in sql.statements(self.forward) if s.sql.upper().startswith("ALTER")]
        self.assertTrue(guarded)
        for statement in guarded:
            self.assertTrue(statement.guarded, statement.sql[:80])

    def test_no_other_database_or_supplier_table_is_touched(self):
        for forbidden in ("golddb", "DROP DATABASE", "supplier_", "inventory_items", "old_gold_movements"):
            self.assertNotIn(forbidden, self.forward)

    def test_the_rollback_refuses_on_any_22k_data_or_later_migration_and_keeps_the_master_row(self):
        self.assertIn("ROLLBACK_038_REFUSED_22k_data_provenance_or_later_migration", self.rollback)
        for needle in ("derived_from_quote_id IS NOT NULL", "version>38", "karat_value = 22"):
            self.assertIn(needle, self.rollback)
        for table in ("suppliers", "supplier_purchase_invoices", "inventory_items", "old_gold_movements",
                      "invoice_price_snapshots", "market_trip_entry_gold", "gold_price_quotes"):
            self.assertIn(f"JOIN {table} ", self.rollback)
        self.assertIn("DELETE FROM schema_migrations WHERE version=38", self.rollback)
        self.assertNotRegex(self.rollback, r"(DELETE FROM|UPDATE)\s+karats\b")
        self.assertLess(self.rollback.index("ROLLBACK_038_REFUSED"), self.rollback.index("DROP COLUMN"))
        self.assertLess(self.rollback.index("DROP CHECK"), self.rollback.index("DROP FOREIGN KEY"))
        self.assertLess(self.rollback.index("DROP FOREIGN KEY"), self.rollback.index("DROP COLUMN"))


if __name__ == "__main__":
    unittest.main()
