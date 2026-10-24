"""Migration 046 (supplier gold settlement from market custody): static replay and contract of the forward and rollback files.
No database is used; the live behaviour (constraints, refusals, rollback) was proven by the rehearsal on masgold_rehearsal."""
import hashlib
from pathlib import Path
import re
import unittest

from tests import migration_sql as sql
from tests import schema_model
from tools.schema_rehearsal.chain import load_chain

ROOT = Path(__file__).resolve().parents[1]
FORWARD = "046_supplier_gold_custody_settlement.sql"
ROLLBACK = "046_supplier_gold_custody_settlement_rollback.sql"
FROZEN = {"043_market_trip_supplier_payments.sql": "e83e409db27ed618a3a15793c2015615fb6c7d482d661af767d7a1d08c058e04",
          "044_market_party_roles.sql": "60e68565b75d93875d2f860b77971bf4dc0b1e7795811d9baef35a4f245f86b2",
          "045_supplier_gold_settlement.sql": "2e72a5de82f8cbf3582975122bfbf11618d4d02ffd5a3a67eb69468dddeb574a"}
EARLIER_KINDS = ("ISSUE", "RETURN", "MELT", "LOT_RETURN", "LOT_ISSUE", "HEAT", "SUPPLIER_PAY")


def digest(name):
    return hashlib.sha256((ROOT / "database" / "schema" / name).read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def code_of(text):
    return " ".join(line for line in text.splitlines() if not line.lstrip().startswith("--"))


class CustodyGoldSettlementSchemaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.model = schema_model.build()
        cls.forward = sql.read(FORWARD)
        cls.rollback = sql.read(ROLLBACK, sql.ROLLBACK_DIR)
        cls.code = code_of(cls.forward)

    def test_the_chain_replays_cleanly_and_the_link_table_and_the_leg_source_exist(self):
        self.assertEqual(self.model.problems, [])
        self.assertEqual(self.model.tables["market_trip_gold_settlements"]["source"], FORWARD)
        self.assertIn("source_kind", self.model.tables["supplier_gold_settlement_legs"]["columns"])
        self.assertEqual(self.model.tables["supplier_gold_settlement_legs"]["source"], "045_supplier_gold_settlement.sql")   # altered, not recreated

    def test_it_requires_045_and_registers_046(self):
        self.assertIn("version=45", self.forward.replace(" ", ""))
        self.assertIn("MIGRATION_046_REFUSED_missing_045", self.forward)
        self.assertRegex(self.forward, r"INSERT INTO schema_migrations\(version,name\) VALUES \(46,")

    def test_earlier_migrations_are_untouched_and_the_manifest_matches(self):
        for name, expected in FROZEN.items():
            self.assertEqual(digest(name), expected, name)
        files = {f.name: f for f in load_chain(require_committed=False)}
        self.assertEqual(len(files), 80)
        self.assertEqual(files[FORWARD].sha256, digest(FORWARD))
        self.assertIn(f"{digest(FORWARD)}  {FORWARD}", (ROOT / "database" / "schema" / "SHA256SUMS").read_text(encoding="utf-8"))

    def test_the_entry_kind_rule_adds_gold_settle_and_keeps_every_earlier_kind(self):
        clause = re.search(r"ADD CONSTRAINT chk_mte_kind CHECK \(entry_kind IN \(([^)]*)\)\)", self.forward).group(1)
        kinds = re.findall(r"'(\w+)'", clause)
        self.assertEqual(kinds, list(EARLIER_KINDS) + ["GOLD_SETTLE"])
        self.assertLessEqual(len("GOLD_SETTLE"), 12)                                                  # entry_kind is VARCHAR(12)
        restored = re.findall(r"'(\w+)'", re.search(r"ADD CONSTRAINT chk_mte_kind CHECK \(entry_kind IN \(([^)]*)\)\)", self.rollback).group(1))
        self.assertEqual(restored, list(EARLIER_KINDS))                                              # the 043 rule, exactly

    def test_the_document_accepts_the_custody_source_and_exposes_the_key_the_link_references(self):
        self.assertIn("CHECK (source_kind IN ('SHOP_SCRAP','CUSTODY_GOLD'))", self.forward)
        self.assertIn("ADD UNIQUE KEY uq_sgs_link (id, source_kind, supplier_id, delivered_karat_id, delivered_weight_grams)", self.forward)

    def test_the_link_is_tied_to_the_document_by_one_composite_foreign_key_in_the_same_column_order(self):
        link = re.search(r"CREATE TABLE IF NOT EXISTS market_trip_gold_settlements \((.*?)\) ENGINE=InnoDB", self.forward, re.S).group(1)
        fk = re.search(r"FOREIGN KEY \(([^)]*)\)\s+REFERENCES supplier_gold_settlements\(([^)]*)\)", link).groups()
        child, parent = [c.strip() for c in fk[0].split(",")], [c.strip() for c in fk[1].split(",")]
        self.assertEqual(child, ["settlement_id", "source_kind", "supplier_id", "karat_id", "weight_grams"])
        self.assertEqual(parent, ["id", "source_kind", "supplier_id", "delivered_karat_id", "delivered_weight_grams"])
        key = re.search(r"ADD UNIQUE KEY uq_sgs_link \(([^)]*)\)", self.forward).group(1)
        self.assertEqual([c.strip() for c in key.split(",")], parent)                                # the referenced key is exactly that list
        for needle in ("entry_id BIGINT UNSIGNED NOT NULL", "settlement_id BIGINT UNSIGNED NOT NULL", "source_kind VARCHAR(12) NOT NULL DEFAULT 'CUSTODY_GOLD'",
                       "supplier_id INT UNSIGNED NOT NULL", "karat_id TINYINT UNSIGNED NOT NULL", "weight_grams DECIMAL(14,3) NOT NULL",
                       "PRIMARY KEY (entry_id)", "UNIQUE KEY uq_mtgs_settlement (settlement_id)",
                       "CONSTRAINT fk_mtgs_entry FOREIGN KEY (entry_id) REFERENCES market_trip_entries(id)",
                       "CHECK (source_kind = 'CUSTODY_GOLD')", "CHECK (designation IN ('LOGO'))", "CHECK (weight_grams > 0)"):
            self.assertIn(needle, link)
        self.assertNotIn("ON DELETE", self.forward)
        self.assertNotRegex(self.forward, r"\b(FLOAT|DOUBLE)\b")
        # the types of the referenced 045 columns are the types the link repeats (a foreign key needs identical types)
        header = sql.read("045_supplier_gold_settlement.sql")
        for needle in ("supplier_id INT UNSIGNED NOT NULL", "delivered_karat_id TINYINT UNSIGNED NOT NULL", "delivered_weight_grams DECIMAL(14,3) NOT NULL"):
            self.assertIn(needle, header)

    def test_every_leg_states_its_source_and_a_scrap_leg_exists_on_shop_scrap_only(self):
        for needle in ("ADD COLUMN source_kind VARCHAR(12) NOT NULL DEFAULT 'SHOP_SCRAP' AFTER settlement_id", "ALTER COLUMN source_kind DROP DEFAULT",
                       "CHECK (source_kind IN ('SHOP_SCRAP','CUSTODY_GOLD'))", "CHECK (leg_kind <> 'SCRAP' OR source_kind = 'SHOP_SCRAP')",
                       "ADD KEY idx_sgsl_source (settlement_id, source_kind)",
                       "ADD CONSTRAINT fk_sgsl_source FOREIGN KEY (settlement_id, source_kind) REFERENCES supplier_gold_settlements(id, source_kind)"):
            self.assertIn(needle, self.forward)
        self.assertLess(self.forward.index("ADD COLUMN source_kind"), self.forward.index("DROP DEFAULT"))      # existing legs are backfilled first
        self.assertLess(self.forward.index("DROP DEFAULT"), self.forward.index("ADD CONSTRAINT fk_sgsl_source"))
        self.assertLess(self.forward.index("ADD UNIQUE KEY uq_sgs_link"), self.forward.index("ADD CONSTRAINT fk_sgsl_source"))   # the key exists first

    def test_every_statement_is_guarded_and_nothing_outside_the_two_settlement_tables_and_the_entry_rule_changes(self):
        for statement in [s.sql for s in sql.statements(self.forward)]:
            self.assertNotRegex(statement, r"^(UPDATE|DELETE|TRUNCATE|DROP TABLE)\b")
        # the RAW top-level statements: guarded SET / PREPARE / EXECUTE / DEALLOCATE, CREATE ... IF NOT EXISTS and the migration row - never a bare ALTER
        raw = [s.strip() for s in self.code.split(";") if s.strip()]
        allowed = ("SET @masgold_sql", "PREPARE masgold_stmt", "EXECUTE masgold_stmt", "DEALLOCATE PREPARE", "CREATE TABLE IF NOT EXISTS",
                   "INSERT INTO schema_migrations")
        for statement in raw:
            self.assertTrue(statement.startswith(allowed) or statement.startswith(("PREPARE", "EXECUTE", "DEALLOCATE")), statement[:80])
        self.assertGreaterEqual(sum(1 for s in raw if s.startswith("SET @masgold_sql")), 12)         # every ALTER sits inside one of these guards
        for table in ("cash_movements", "bank_movements", "supplier_making", "old_gold_movement", "market_trip_lot", "market_trip_melt", "market_trip_heat",
                      "inventory_items", "permissions", "role_permissions", "golddb", "transaction_corrections"):
            self.assertNotIn(table, self.code, table)
        self.assertEqual(re.findall(r"INSERT INTO (\w+)", self.code), ["schema_migrations"])          # no reference data, no permission, no movement type

    def test_the_rollback_refuses_on_any_custody_settlement_data_or_a_later_migration_and_drops_in_dependency_order(self):
        self.assertIn("ROLLBACK_046_REFUSED_custody_settlement_data_or_later_migration", self.rollback)
        for needle in ("FROM market_trip_gold_settlements", "entry_kind = 'GOLD_SETTLE'", "FROM supplier_gold_settlements WHERE source_kind <> 'SHOP_SCRAP'",
                       "FROM supplier_gold_settlement_legs WHERE source_kind <> 'SHOP_SCRAP'", "version>46"):
            self.assertIn(needle, self.rollback)
        order = ["ROLLBACK_046_REFUSED", "DROP TABLE market_trip_gold_settlements", "DROP FOREIGN KEY fk_sgsl_source", "DROP CHECK chk_sgsl_scrap_only",
                 "DROP CHECK chk_sgsl_source", "DROP INDEX idx_sgsl_source", "DROP COLUMN source_kind", "DROP INDEX uq_sgs_link",
                 "ADD CONSTRAINT chk_sgs_source CHECK (source_kind = 'SHOP_SCRAP')", "ADD CONSTRAINT chk_mte_kind", "DELETE FROM schema_migrations WHERE version=46"]
        positions = [self.rollback.index(item) for item in order]
        self.assertEqual(positions, sorted(positions))                                               # the guard first; checks before the column; FK before its key
        self.assertNotRegex(code_of(self.rollback), r"DELETE FROM (supplier_gold|market_trip|old_gold|cash|bank)")


if __name__ == "__main__":
    unittest.main()
