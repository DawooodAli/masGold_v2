"""Migration 045 (supplier gold settlement from shop Scrap): static replay and contract of the forward and rollback files.
No database is used; the live behaviour (constraints, refusals, rollback) was proven by the rehearsal on masgold_rehearsal."""
import hashlib
from pathlib import Path
import re
import unittest

from tests import migration_sql as sql
from tests import schema_model
from tools.schema_rehearsal.chain import load_chain

ROOT = Path(__file__).resolve().parents[1]
FORWARD = "045_supplier_gold_settlement.sql"
ROLLBACK = "045_supplier_gold_settlement_rollback.sql"
FROZEN = {"043_market_trip_supplier_payments.sql": "e83e409db27ed618a3a15793c2015615fb6c7d482d661af767d7a1d08c058e04",
          "044_market_party_roles.sql": "60e68565b75d93875d2f860b77971bf4dc0b1e7795811d9baef35a4f245f86b2"}
EARLIER_SOURCES = ("INVOICE", "EXCHANGE", "RETURN", "CUSTOMER_ACCOUNT_SETTLEMENT", "FINANCIAL_OPERATION", "CUSTOMER_DEPOSIT",
                   "SUPPLIER_MAKING_PAYMENT", "MARKET_TRIP_ENTRY")


def digest(name):
    return hashlib.sha256((ROOT / "database" / "schema" / name).read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def clause_of(text):
    clause = text[text.index("ADD CONSTRAINT chk_correction_source"):]
    return clause[:clause.index(";")]


class GoldSettlementSchemaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.model = schema_model.build()
        cls.forward = sql.read(FORWARD)
        cls.rollback = sql.read(ROLLBACK, sql.ROLLBACK_DIR)
        cls.statements = [s.sql for s in sql.statements(cls.forward)]

    def test_the_chain_replays_cleanly_and_both_tables_exist(self):
        self.assertEqual(self.model.problems, [])
        for table in ("supplier_gold_settlements", "supplier_gold_settlement_legs"):
            self.assertEqual(self.model.tables[table]["source"], FORWARD)

    def test_it_requires_044_and_registers_045(self):
        self.assertIn("version=44", self.forward.replace(" ", ""))
        self.assertIn("MIGRATION_045_REFUSED_missing_044", self.forward)
        self.assertRegex(self.forward, r"INSERT INTO schema_migrations\(version,name\) VALUES \(45,")

    def test_earlier_migrations_are_untouched_and_the_manifest_matches(self):
        for name, expected in FROZEN.items():
            self.assertEqual(digest(name), expected, name)
        files = {f.name: f for f in load_chain(require_committed=False)}
        self.assertEqual(len(files), 74)
        self.assertEqual(files[FORWARD].sha256, digest(FORWARD))
        self.assertIn(f"{digest(FORWARD)}  {FORWARD}", (ROOT / "database" / "schema" / "SHA256SUMS").read_text(encoding="utf-8"))

    def test_the_document_stores_the_original_the_derived_and_the_difference(self):
        for needle in ("delivered_karat_value TINYINT UNSIGNED NOT NULL", "target_karat_value TINYINT UNSIGNED NOT NULL",
                       "delivered_weight_grams DECIMAL(14,3) NOT NULL", "credited_weight_grams DECIMAL(14,3) NOT NULL",
                       "credited_analytical_grams DECIMAL(18,6) NOT NULL", "rounding_difference_grams DECIMAL(18,6) NOT NULL",
                       "CHECK (source_kind = 'SHOP_SCRAP')", "delivered_weight_grams > 0 AND credited_weight_grams > 0 AND credited_analytical_grams > 0",
                       "rounding_difference_grams = credited_weight_grams - credited_analytical_grams", "UNIQUE KEY uq_sgs_request (request_key)",
                       "REFERENCES suppliers(id)", "REFERENCES karats(id)"):
            self.assertIn(needle, self.forward)
        self.assertNotRegex(self.forward, r"\b(FLOAT|DOUBLE)\b")
        self.assertNotIn("ON DELETE", self.forward)

    def test_each_leg_links_exactly_one_movement_once(self):
        for needle in ("UNIQUE KEY uq_sgsl_kind (settlement_id, leg_kind)", "UNIQUE KEY uq_sgsl_scrap_movement (old_gold_movement_id)",
                       "UNIQUE KEY uq_sgsl_gold_movement (supplier_gold_movement_id)", "REFERENCES old_gold_movements(id)",
                       "REFERENCES supplier_gold_movements(id)",
                       "(leg_kind = 'SCRAP' AND old_gold_movement_id IS NOT NULL AND supplier_gold_movement_id IS NULL)",
                       "(leg_kind = 'GOLD' AND supplier_gold_movement_id IS NOT NULL AND old_gold_movement_id IS NULL)"):
            self.assertIn(needle, self.forward)

    def test_the_correction_source_rule_keeps_every_earlier_source_and_restriction(self):
        clause = clause_of(self.forward)
        self.assertIn("(source_type IN ('INVOICE','EXCHANGE','RETURN') AND invoice_id IS NOT NULL)", clause)
        self.assertIn("(source_type='CUSTOMER_ACCOUNT_SETTLEMENT' AND invoice_id IS NULL)", clause)
        for source in EARLIER_SOURCES + ("SUPPLIER_GOLD_SETTLEMENT",):
            self.assertIn(f"'{source}'", clause)
        self.assertIn("AND invoice_id IS NULL AND correction_type='FULL_REVERSAL'", clause)
        # the previous definition (037) is exactly the new one minus the new source
        self.assertEqual(clause.replace(",'SUPPLIER_GOLD_SETTLEMENT'", ""), clause_of(sql.read("037_market_trip_custody.sql")))

    def test_only_inserts_and_the_two_tables_and_no_cash_or_making_object_is_touched(self):
        for statement in self.statements:
            self.assertNotRegex(statement, r"^(UPDATE|DELETE|TRUNCATE|DROP TABLE)\b")
        code = " ".join(line for line in self.forward.splitlines() if not line.lstrip().startswith("--"))
        for forbidden in ("cash_movements", "bank_movements", "supplier_making", "golddb", "inventory_items", "market_trip"):
            self.assertNotIn(forbidden, code, forbidden)
        for needle in ("SUPPLIER_GOLD_SETTLEMENT_OUT", "REVERSAL_SUPPLIER_GOLD_SETTLEMENT_IN", "'SETTLEMENT'", "'SETTLEMENT_REVERSAL'",
                       "'SUPPLIER_GOLD_SETTLE'", "'SUPPLIER_GOLD_SETTLE_REVERSE'"):
            self.assertIn(needle, self.forward)
        self.assertIn("WHERE r.code IN ('OWNER','MANAGER')", self.forward)           # the settle right: OWNER and MANAGER
        self.assertIn("WHERE r.code = 'OWNER'", self.forward)                         # the reverse right: OWNER only

    def test_the_rollback_refuses_on_any_settlement_data_or_a_later_migration_and_restores_the_037_rule(self):
        self.assertIn("ROLLBACK_045_REFUSED_settlement_data_or_later_migration", self.rollback)
        for needle in ("FROM supplier_gold_settlements", "FROM supplier_gold_settlement_legs", "'SETTLEMENT','SETTLEMENT_REVERSAL'",
                       "'SUPPLIER_GOLD_SETTLEMENT_OUT','REVERSAL_SUPPLIER_GOLD_SETTLEMENT_IN'", "source_type = 'SUPPLIER_GOLD_SETTLEMENT'", "version>45"):
            self.assertIn(needle, self.rollback)
        self.assertLess(self.rollback.index("ROLLBACK_045_REFUSED"), self.rollback.index("DROP TABLE"))
        self.assertLess(self.rollback.index("DROP TABLE supplier_gold_settlement_legs"), self.rollback.index("DROP TABLE supplier_gold_settlements"))
        restored = clause_of(self.rollback)
        self.assertNotIn("SUPPLIER_GOLD_SETTLEMENT", restored)
        self.assertEqual(restored, clause_of(sql.read("037_market_trip_custody.sql")))
        self.assertIn("DELETE FROM schema_migrations WHERE version=45", self.rollback)


if __name__ == "__main__":
    unittest.main()
