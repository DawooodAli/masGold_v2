"""Migration 054 (cash gold purchase): static replay and contract of the forward and rollback files. No database is used; the live behaviour
(constraints, refusals, rollback) is NOT proven here and still needs an approved rehearsal run."""
import hashlib
from pathlib import Path
import re
import unittest

from tests import migration_sql as sql
from tests import schema_model
from tools.schema_rehearsal.chain import load_chain

ROOT = Path(__file__).resolve().parents[1]
FORWARD = "054_market_trip_gold_buy.sql"
ROLLBACK = "054_market_trip_gold_buy_rollback.sql"
FROZEN = {"053_market_trip_gold_sale.sql": "e2e9a8348e612012a7e0fda94975e87c6615799d5dbfbd40736fde8398f66cb8",
          "052_supplier_invoice_reversal.sql": "6500b2c6bb1d5a444c115c952e86405c0c932f24a84752d51579925c946388cd"}
EARLIER_KINDS = ("ISSUE", "RETURN", "MELT", "LOT_RETURN", "LOT_ISSUE", "HEAT", "SUPPLIER_PAY", "GOLD_SETTLE", "LOT_SETTLE", "LOT_HOLD", "HOLD_RETURN",
                 "LOGO_HOLD", "HOLD_BACK", "GOLD_SALE")


def digest(name):
    return hashlib.sha256((ROOT / "database" / "schema" / name).read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def code_of(text):
    return " ".join(line for line in text.splitlines() if not line.lstrip().startswith("--"))


class GoldBuySchemaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.model = schema_model.build()
        cls.forward = sql.read(FORWARD)
        cls.rollback = sql.read(ROLLBACK, sql.ROLLBACK_DIR)
        cls.code = code_of(cls.forward)
        cls.head = re.search(r"CREATE TABLE IF NOT EXISTS market_trip_gold_buys \((.*?)\) ENGINE=InnoDB", cls.forward, re.S).group(1)
        cls.lines = re.search(r"CREATE TABLE IF NOT EXISTS market_trip_gold_buy_lines \((.*?)\) ENGINE=InnoDB", cls.forward, re.S).group(1)

    def test_the_chain_replays_cleanly_and_both_tables_exist(self):
        self.assertEqual(self.model.problems, [])
        self.assertEqual(self.model.tables["market_trip_gold_buys"]["source"], FORWARD)
        self.assertEqual(self.model.tables["market_trip_gold_buy_lines"]["source"], FORWARD)

    def test_it_requires_053_and_registers_054(self):
        self.assertIn("version=53", self.forward.replace(" ", ""))
        self.assertIn("MIGRATION_054_REFUSED_missing_053", self.forward)
        self.assertRegex(self.forward, r"INSERT INTO schema_migrations\(version,name\) VALUES \(54,")

    def test_earlier_migrations_are_untouched_and_the_manifest_matches(self):
        for name, expected in FROZEN.items():
            self.assertEqual(digest(name), expected, name)
        files = {f.name: f for f in load_chain(require_committed=False)}
        self.assertEqual(len(files), 67)
        self.assertEqual(files[FORWARD].sha256, digest(FORWARD))
        self.assertIn(f"{digest(FORWARD)}  {FORWARD}", (ROOT / "database" / "schema" / "SHA256SUMS").read_text(encoding="utf-8"))

    def test_the_entry_kind_rule_adds_gold_buy_and_keeps_every_earlier_kind(self):
        pattern = r"ADD CONSTRAINT chk_mte_kind CHECK \(entry_kind IN \(([^)]*)\)\)"
        kinds = re.findall(r"'(\w+)'", re.search(pattern, self.forward).group(1))
        self.assertEqual(kinds, list(EARLIER_KINDS) + ["GOLD_BUY"])
        self.assertLessEqual(len("GOLD_BUY"), 12)                                                    # entry_kind is VARCHAR(12)
        self.assertEqual(re.findall(r"'(\w+)'", re.search(pattern, self.rollback).group(1)), list(EARLIER_KINDS))

    def test_the_vendor_identity_is_mandatory_and_the_document_is_tied_to_its_entry(self):
        for needle in ("PRIMARY KEY (entry_id)", "entry_kind VARCHAR(12) NOT NULL DEFAULT 'GOLD_BUY'", "vendor_name VARCHAR(150) NOT NULL",
                       "vendor_name_key VARCHAR(150) NOT NULL", "vendor_party_id INT UNSIGNED DEFAULT NULL", "total_price DECIMAL(16,2) NOT NULL",
                       "FOREIGN KEY (entry_id, entry_kind) REFERENCES market_trip_entries(id, entry_kind)",
                       "FOREIGN KEY (vendor_party_id) REFERENCES suppliers(id)",
                       "vendor_kind = 'REGISTERED_PARTY' AND vendor_party_id IS NOT NULL", "vendor_kind = 'THIRD_PARTY' AND vendor_party_id IS NULL",
                       "CHAR_LENGTH(TRIM(vendor_name)) > 0", "CHAR_LENGTH(TRIM(vendor_name_key)) > 0", "total_price > 0",
                       "reference_total IS NULL AND deviation_percent IS NULL"):
            self.assertIn(needle, self.head)
        self.assertIn("ADD UNIQUE KEY uq_mte_id_kind (id,entry_kind)", sql.read("047_supplier_gold_whole_lot_settlement.sql"))

    def test_the_lines_are_new_scrap_only_melt_or_logo_positive_and_tied_to_the_document(self):
        for needle in ("PRIMARY KEY (entry_id, line_no)", "karat_id TINYINT UNSIGNED NOT NULL", "weight_grams DECIMAL(14,3) NOT NULL",
                       "line_price DECIMAL(16,2) NOT NULL", "FOREIGN KEY (entry_id) REFERENCES market_trip_gold_buys(entry_id)",
                       "FOREIGN KEY (karat_id) REFERENCES karats(id)", "designation IN ('MELT','LOGO')", "weight_grams > 0", "line_price > 0"):
            self.assertIn(needle, self.lines)
        self.assertNotIn("UNCLASSIFIED", self.lines)                                                  # new gold is never unclassified
        self.assertNotIn("ON DELETE", self.forward)
        self.assertNotRegex(self.forward, r"\b(FLOAT|DOUBLE)\b")

    def test_a_purchase_never_references_a_supplier_ledger_or_a_money_table(self):
        for table in ("supplier_gold_movements", "supplier_making_movements", "supplier_gold_settlements", "supplier_making_payments",
                      "cash_movements", "bank_movements", "old_gold_movements", "inventory_items", "market_trip_lot", "permissions", "role_permissions", "golddb"):
            self.assertNotIn(table, self.code, table)
        self.assertEqual(re.findall(r"REFERENCES (\w+)", self.head + self.lines),
                         ["market_trip_entries", "suppliers", "market_trip_gold_buys", "karats"])

    def test_every_statement_is_guarded_and_idempotent(self):
        raw = [s.strip() for s in self.code.split(";") if s.strip()]
        allowed = ("SET @masgold_sql", "PREPARE masgold_stmt", "EXECUTE masgold_stmt", "DEALLOCATE PREPARE", "CREATE TABLE IF NOT EXISTS",
                   "INSERT INTO schema_migrations")
        for statement in raw:
            self.assertTrue(statement.startswith(allowed), statement[:80])
        self.assertEqual(re.findall(r"INSERT INTO (\w+)", self.code), ["schema_migrations"])
        for statement in [s.sql for s in sql.statements(self.forward)]:
            self.assertNotRegex(statement, r"^(UPDATE|DELETE|TRUNCATE|DROP TABLE)\b")

    def test_the_rollback_refuses_on_any_purchase_history_or_a_later_migration_and_drops_children_first(self):
        self.assertIn("ROLLBACK_054_REFUSED_history_or_later_migration", self.rollback)
        for needle in ("FROM market_trip_gold_buys", "FROM market_trip_gold_buy_lines", "entry_kind='GOLD_BUY'", "version>54"):
            self.assertIn(needle, self.rollback)
        order = ["ROLLBACK_054_REFUSED", "DROP TABLE IF EXISTS market_trip_gold_buy_lines", "DROP TABLE IF EXISTS market_trip_gold_buys",
                 "ADD CONSTRAINT chk_mte_kind", "DELETE FROM schema_migrations WHERE version=54"]
        positions = [self.rollback.index(item) for item in order]
        self.assertEqual(positions, sorted(positions))
        self.assertNotRegex(code_of(self.rollback), r"DELETE FROM (supplier|market_trip|old_gold|cash|bank)")


if __name__ == "__main__":
    unittest.main()
