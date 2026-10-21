"""Migration 053 (sale of custody gold): static replay and contract of the forward and rollback files. No database is used; the live
behaviour (constraints, refusals, rollback) is NOT proven here and still needs an approved rehearsal run."""
import hashlib
from pathlib import Path
import re
import unittest

from tests import migration_sql as sql
from tests import schema_model
from tools.schema_rehearsal.chain import load_chain

ROOT = Path(__file__).resolve().parents[1]
FORWARD = "053_market_trip_gold_sale.sql"
ROLLBACK = "053_market_trip_gold_sale_rollback.sql"
FROZEN = {"047_supplier_gold_whole_lot_settlement.sql": "b68de49b8f908cb9941173d1a2674478a331cfdfded9193d8139b9b002354165",
          "049_intermediary_logo_holdings.sql": "2ec47b5e3850ce8f4227d620bf23d6b5f10351478cfc85143b08b657e82540f5",
          "052_supplier_invoice_reversal.sql": "6500b2c6bb1d5a444c115c952e86405c0c932f24a84752d51579925c946388cd"}
EARLIER_KINDS = ("ISSUE", "RETURN", "MELT", "LOT_RETURN", "LOT_ISSUE", "HEAT", "SUPPLIER_PAY", "GOLD_SETTLE", "LOT_SETTLE", "LOT_HOLD", "HOLD_RETURN",
                 "LOGO_HOLD", "HOLD_BACK")


def digest(name):
    return hashlib.sha256((ROOT / "database" / "schema" / name).read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def code_of(text):
    return " ".join(line for line in text.splitlines() if not line.lstrip().startswith("--"))


class GoldSaleSchemaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.model = schema_model.build()
        cls.forward = sql.read(FORWARD)
        cls.rollback = sql.read(ROLLBACK, sql.ROLLBACK_DIR)
        cls.code = code_of(cls.forward)
        cls.table = re.search(r"CREATE TABLE IF NOT EXISTS market_trip_gold_sales \((.*?)\) ENGINE=InnoDB", cls.forward, re.S).group(1)

    def test_the_chain_replays_cleanly_and_the_sale_table_exists(self):
        self.assertEqual(self.model.problems, [])
        self.assertEqual(self.model.tables["market_trip_gold_sales"]["source"], FORWARD)

    def test_it_requires_052_and_registers_053(self):
        self.assertIn("version=52", self.forward.replace(" ", ""))
        self.assertIn("MIGRATION_053_REFUSED_missing_052", self.forward)
        self.assertRegex(self.forward, r"INSERT INTO schema_migrations\(version,name\) VALUES \(53,")

    def test_earlier_migrations_are_untouched_and_the_manifest_matches(self):
        for name, expected in FROZEN.items():
            self.assertEqual(digest(name), expected, name)
        files = {f.name: f for f in load_chain(require_committed=False)}
        self.assertEqual(len(files), 67)
        self.assertEqual(files[FORWARD].sha256, digest(FORWARD))
        self.assertIn(f"{digest(FORWARD)}  {FORWARD}", (ROOT / "database" / "schema" / "SHA256SUMS").read_text(encoding="utf-8"))

    def test_the_entry_kind_rule_adds_gold_sale_and_keeps_every_earlier_kind(self):
        pattern = r"ADD CONSTRAINT chk_mte_kind CHECK \(entry_kind IN \(([^)]*)\)\)"
        kinds = re.findall(r"'(\w+)'", re.search(pattern, self.forward).group(1))
        self.assertEqual(kinds, list(EARLIER_KINDS) + ["GOLD_SALE"])
        self.assertLessEqual(len("GOLD_SALE"), 12)                                                   # entry_kind is VARCHAR(12)
        self.assertEqual(re.findall(r"'(\w+)'", re.search(pattern, self.rollback).group(1)), list(EARLIER_KINDS))

    def test_the_lot_ledger_accepts_a_negative_sale_out_and_keeps_every_earlier_rule(self):
        types = re.findall(r"'(\w+)'", re.search(r"chk_mtlm_type CHECK \(movement_type IN \(([^)]*)\)\)", self.forward).group(1))
        self.assertEqual(types, ["CREATE", "SHOP_RETURN", "SHOP_ISSUE", "SUPPLIER_SETTLE", "HOLD_OUT", "HOLD_IN", "SALE_OUT"])
        self.assertIn("movement_type NOT IN ('SHOP_RETURN','SUPPLIER_SETTLE','HOLD_OUT','SALE_OUT') OR weight_delta<0", self.forward)
        self.assertIn("movement_type NOT IN ('CREATE','SHOP_ISSUE','HOLD_IN') OR weight_delta>0", self.forward)
        restored = re.findall(r"'(\w+)'", re.search(r"chk_mtlm_type CHECK \(movement_type IN \(([^)]*)\)\)", self.rollback).group(1))
        self.assertEqual(restored, ["CREATE", "SHOP_RETURN", "SHOP_ISSUE", "SUPPLIER_SETTLE", "HOLD_OUT", "HOLD_IN"])

    def test_the_sale_document_shape_buyer_price_and_warning_rules(self):
        for needle in ("PRIMARY KEY (entry_id)", "entry_kind VARCHAR(12) NOT NULL DEFAULT 'GOLD_SALE'", "weight_grams DECIMAL(14,3) NOT NULL",
                       "total_price DECIMAL(16,2) NOT NULL", "price_per_gram DECIMAL(16,4) DEFAULT NULL", "buyer_name VARCHAR(150) NOT NULL",
                       "buyer_party_id INT UNSIGNED DEFAULT NULL", "FOREIGN KEY (entry_id, entry_kind) REFERENCES market_trip_entries(id, entry_kind)",
                       "FOREIGN KEY (buyer_party_id) REFERENCES suppliers(id)", "FOREIGN KEY (karat_id) REFERENCES karats(id)",
                       "FOREIGN KEY (lot_id) REFERENCES market_trip_lots(id)",
                       "REFERENCES market_trip_lot_movements(id, lot_id, entry_id, movement_type, weight_delta)",
                       "buyer_kind = 'REGISTERED_PARTY' AND buyer_party_id IS NOT NULL", "buyer_kind = 'THIRD_PARTY' AND buyer_party_id IS NULL",
                       "total_price > 0", "price_basis = 'TOTAL' AND price_per_gram IS NULL", "price_basis = 'PER_GRAM' AND price_per_gram IS NOT NULL",
                       "source_kind = 'CUSTODY_GOLD'", "source_kind = 'CUSTODY_LOT'", "movement_type = 'SALE_OUT' AND weight_delta = -weight_grams",
                       "reference_price_per_gram IS NULL AND deviation_percent IS NULL"):
            self.assertIn(needle, self.table)
        self.assertNotIn("ON DELETE", self.forward)
        self.assertNotRegex(self.forward, r"\b(FLOAT|DOUBLE)\b")
        # the composite parent key of the lot ledger exists (047) with the same column order
        self.assertIn("ADD UNIQUE KEY uq_mtlm_settle_link (id,lot_id,entry_id,movement_type,weight_delta)", sql.read("047_supplier_gold_whole_lot_settlement.sql"))
        self.assertIn("ADD UNIQUE KEY uq_mte_id_kind (id,entry_kind)", sql.read("047_supplier_gold_whole_lot_settlement.sql"))

    def test_a_sale_never_references_a_supplier_ledger_or_a_money_table(self):
        for table in ("supplier_gold_movements", "supplier_making_movements", "supplier_gold_settlements", "supplier_making_payments",
                      "cash_movements", "bank_movements", "old_gold_movements", "inventory_items", "permissions", "role_permissions", "golddb"):
            self.assertNotIn(table, self.code, table)
        self.assertEqual(re.findall(r"REFERENCES (\w+)", self.table), ["market_trip_entries", "karats", "market_trip_lots", "suppliers",
                                                                      "market_trip_lot_movements"])

    def test_every_statement_is_guarded_and_idempotent(self):
        raw = [s.strip() for s in self.code.split(";") if s.strip()]
        allowed = ("SET @masgold_sql", "PREPARE masgold_stmt", "EXECUTE masgold_stmt", "DEALLOCATE PREPARE", "CREATE TABLE IF NOT EXISTS",
                   "INSERT INTO schema_migrations")
        for statement in raw:
            self.assertTrue(statement.startswith(allowed), statement[:80])
        self.assertEqual(re.findall(r"INSERT INTO (\w+)", self.code), ["schema_migrations"])
        for statement in [s.sql for s in sql.statements(self.forward)]:
            self.assertNotRegex(statement, r"^(UPDATE|DELETE|TRUNCATE|DROP TABLE)\b")

    def test_the_rollback_refuses_on_any_sale_history_or_a_later_migration_and_restores_the_earlier_rules(self):
        self.assertIn("ROLLBACK_053_REFUSED_history_or_later_migration", self.rollback)
        for needle in ("FROM market_trip_gold_sales", "entry_kind='GOLD_SALE'", "movement_type='SALE_OUT'", "version>53"):
            self.assertIn(needle, self.rollback)
        order = ["ROLLBACK_053_REFUSED", "DROP TABLE IF EXISTS market_trip_gold_sales", "ADD CONSTRAINT chk_mte_kind", "ADD CONSTRAINT chk_mtlm_type",
                 "ADD CONSTRAINT chk_mtlm_delta", "DELETE FROM schema_migrations WHERE version=53"]
        positions = [self.rollback.index(item) for item in order]
        self.assertEqual(positions, sorted(positions))
        self.assertNotRegex(code_of(self.rollback), r"DELETE FROM (supplier|market_trip|old_gold|cash|bank)")


if __name__ == "__main__":
    unittest.main()
