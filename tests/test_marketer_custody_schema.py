"""Migration 055 (marketer custody account): static replay and contract of the forward and rollback files. No database is used; the live behaviour
(constraints, refusals, rollback) is NOT proven here and still needs an approved rehearsal run."""
import hashlib
from pathlib import Path
import re
import unittest

from tests import migration_sql as sql
from tests import schema_model
from tools.schema_rehearsal.chain import load_chain

ROOT = Path(__file__).resolve().parents[1]
FORWARD = "055_marketer_custody_account.sql"
ROLLBACK = "055_marketer_custody_account_rollback.sql"
FROZEN = {"050_supplier_gold_holding_settlement.sql": "623f25b8a4f9f990bc3ad9b0891046f5e7e0c4b50f09e418f65b5fdfc77b9f3d",
          "054_market_trip_gold_buy.sql": "4ba0c10b2f31f10d21f294d27ca09d6dda6c2ef026683c441693eee9a6f45129"}
EARLIER_SOURCES = ("FINANCIAL_OPERATION", "CUSTOMER_DEPOSIT", "SUPPLIER_MAKING_PAYMENT", "MARKET_TRIP_ENTRY", "SUPPLIER_GOLD_SETTLEMENT", "HOLDING_RECEIPT",
                   "SUPPLIER_PURCHASE_INVOICE")
EARLIER_SG_SOURCES = ("SHOP_SCRAP", "CUSTODY_GOLD", "CUSTODY_LOT", "HOLDING_LOT", "HOLDING_LOGO")


def digest(name):
    return hashlib.sha256((ROOT / "database" / "schema" / name).read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def code_of(text):
    return " ".join(line for line in text.splitlines() if not line.lstrip().startswith("--"))


def table(forward, name):
    return re.search(rf"CREATE TABLE IF NOT EXISTS {name} \((.*?)\) ENGINE=InnoDB", forward, re.S).group(1)


class MarketerSchemaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.model = schema_model.build()
        cls.forward = sql.read(FORWARD)
        cls.rollback = sql.read(ROLLBACK, sql.ROLLBACK_DIR)
        cls.code = code_of(cls.forward)
        cls.f050 = sql.read("050_supplier_gold_holding_settlement.sql")

    def test_the_chain_replays_cleanly_and_every_table_exists(self):
        self.assertEqual(self.model.problems, [])
        for name in ("marketer_documents", "marketer_shop_links", "marketer_purchase_lines", "marketer_cash_movements", "marketer_gold_movements"):
            self.assertEqual(self.model.tables[name]["source"], FORWARD, name)

    def test_it_requires_054_and_registers_055(self):
        self.assertIn("version=54", self.forward.replace(" ", ""))
        self.assertIn("MIGRATION_055_REFUSED_missing_054", self.forward)
        self.assertRegex(self.forward, r"INSERT INTO schema_migrations\(version,name\) VALUES \(55,")

    def test_earlier_migrations_are_untouched_and_the_manifest_matches(self):
        for name, expected in FROZEN.items():
            self.assertEqual(digest(name), expected, name)
        files = {f.name: f for f in load_chain(require_committed=False)}
        self.assertEqual(len(files), 57)
        self.assertEqual(files[FORWARD].sha256, digest(FORWARD))
        self.assertIn(f"{digest(FORWARD)}  {FORWARD}", (ROOT / "database" / "schema" / "SHA256SUMS").read_text(encoding="utf-8"))

    def test_the_correction_source_is_added_and_every_earlier_source_is_kept(self):
        pattern = r"source_type IN \(([^)]*)\) AND invoice_id IS NULL AND correction_type='FULL_REVERSAL'"
        added = re.findall(r"'(\w+)'", re.search(pattern, self.forward).group(1))
        self.assertEqual(added, list(EARLIER_SOURCES) + ["MARKETER_DOCUMENT"])
        self.assertEqual(re.findall(r"'(\w+)'", re.search(pattern, self.rollback).group(1)), list(EARLIER_SOURCES))

    def test_the_supplier_settlement_gains_the_marketer_source_in_21k_only_and_the_old_shape_is_kept_verbatim(self):
        sources = re.findall(r"'(\w+)'", re.search(r"ADD CONSTRAINT chk_sgs_source CHECK \(source_kind IN \(([^)]*)\)\)", self.forward).group(1))
        self.assertEqual(sources, list(EARLIER_SG_SOURCES) + ["HOLDING_MKT"])
        legs = re.findall(r"'(\w+)'", re.search(r"ADD CONSTRAINT chk_sgsl_source CHECK \(source_kind IN \(([^)]*)\)\)", self.forward).group(1))
        self.assertEqual(legs, list(EARLIER_SG_SOURCES) + ["HOLDING_MKT"])
        old = re.search(r"ADD CONSTRAINT chk_sgs_shape CHECK \((.*?)\)\", 'DO 0'\);", self.f050, re.S).group(1)
        new = re.search(r"ADD CONSTRAINT chk_sgs_shape CHECK \((.*?)\)\", 'DO 0'\);", self.forward, re.S).group(1)
        self.assertTrue(new.startswith(old))                                                          # every earlier branch, unchanged
        branch = new[len(old):]
        for needle in ("source_kind='HOLDING_MKT'", "delivered_karat_value=21", "holding_party_id IS NOT NULL", "lot_pool_id IS NULL", "logo_pool_id IS NULL",
                       "physical_declaration IS NULL", "holding_weight_grams=delivered_weight_grams"):
            self.assertIn(needle, branch)
        restored = re.search(r"ADD CONSTRAINT chk_sgs_shape CHECK \((.*?)\);", self.rollback, re.S).group(1)
        self.assertEqual(restored, old)                                                               # the rollback restores the 050 shape exactly
        self.assertIn("ADD UNIQUE KEY uq_sgs_marketer (id,holding_party_id,holding_weight_grams)", self.forward)

    def test_gold_is_21k_only_and_the_documents_have_explicit_shapes_with_a_mandatory_counterparty(self):
        documents, gold = table(self.forward, "marketer_documents"), table(self.forward, "marketer_gold_movements")
        for needle in ("CONSTRAINT chk_mkd_karat CHECK (karat_value = 21)", "cash_amount DECIMAL(16,2) DEFAULT NULL", "weight_grams DECIMAL(12,3) DEFAULT NULL",
                       "request_key CHAR(36) NOT NULL", "UNIQUE KEY uq_mkd_request (request_key)", "FOREIGN KEY (party_id) REFERENCES suppliers(id)",
                       "doc_kind IN ('CASH_HANDOVER','CASH_RETURN')", "doc_kind = 'GOLD_RETURN'", "doc_kind IN ('PURCHASE','SALE')",
                       "counterparty_kind = 'REGISTERED_PARTY' AND counterparty_party_id IS NOT NULL", "counterparty_kind = 'THIRD_PARTY' AND counterparty_party_id IS NULL",
                       "CHAR_LENGTH(TRIM(counterparty_name)) > 0", "CHAR_LENGTH(TRIM(counterparty_key)) > 0",
                       "reference_total IS NULL AND deviation_percent IS NULL"):
            self.assertIn(needle, documents)
        self.assertIn("CONSTRAINT chk_mkgm_karat CHECK (karat_value = 21)", gold)
        self.assertNotRegex(self.forward, r"\b(FLOAT|DOUBLE)\b")
        self.assertNotIn("ON DELETE", self.forward)

    def test_the_ledgers_are_signed_insert_only_and_tied_to_their_documents_by_composite_keys(self):
        cash, gold, links = table(self.forward, "marketer_cash_movements"), table(self.forward, "marketer_gold_movements"), table(self.forward, "marketer_shop_links")
        self.assertIn("FOREIGN KEY (document_id, doc_kind, party_id, cash_amount) REFERENCES marketer_documents(id, doc_kind, party_id, cash_amount)", cash)
        for movement in ("CASH_HANDOVER", "CASH_RETURN", "SALE_PROCEEDS", "PURCHASE_SPEND", "CASH_HANDOVER_REVERSAL", "CASH_RETURN_REVERSAL",
                         "SALE_PROCEEDS_REVERSAL", "PURCHASE_SPEND_REVERSAL"):
            self.assertIn(f"movement_type = '{movement}'", cash)
        self.assertIn("FOREIGN KEY (document_id, doc_kind, party_id, weight_grams) REFERENCES marketer_documents(id, doc_kind, party_id, weight_grams)", gold)
        self.assertIn("FOREIGN KEY (settlement_id, party_id, weight_grams) REFERENCES supplier_gold_settlements(id, holding_party_id, holding_weight_grams)", gold)
        for movement in ("PURCHASE_IN", "SALE_OUT", "GOLD_RETURN", "SETTLEMENT", "PURCHASE_IN_REVERSAL", "SALE_OUT_REVERSAL", "GOLD_RETURN_REVERSAL",
                         "SETTLEMENT_REVERSAL"):
            self.assertIn(f"movement_type = '{movement}'", gold)
        self.assertIn("FOREIGN KEY (shop_cash_movement_id) REFERENCES cash_movements(id)", links)
        self.assertIn("FOREIGN KEY (shop_scrap_movement_id) REFERENCES old_gold_movements(id)", links)

    def test_the_marketer_world_never_references_a_supplier_ledger(self):
        created = " ".join(table(self.forward, name) for name in ("marketer_documents", "marketer_shop_links", "marketer_purchase_lines",
                                                                   "marketer_cash_movements", "marketer_gold_movements"))
        for forbidden in ("supplier_gold_movements", "supplier_making_movements", "supplier_making_payments", "market_trip", "inventory_items", "permissions"):
            self.assertNotIn(forbidden, created, forbidden)
        referenced = set(re.findall(r"REFERENCES (\w+)", created))
        self.assertEqual(referenced, {"suppliers", "users", "employees", "marketer_documents", "supplier_gold_settlements", "cash_movements",
                                      "old_gold_movements", "transaction_corrections"})             # identities, the shop-side rows and the ONE bridge (the SG document)

    def test_every_statement_is_guarded_and_the_only_data_inserted_are_movement_types_and_the_migration_row(self):
        raw = [s.strip() for s in self.code.split(";") if s.strip()]
        allowed = ("SET @masgold_sql", "PREPARE masgold_stmt", "EXECUTE masgold_stmt", "DEALLOCATE PREPARE", "CREATE TABLE IF NOT EXISTS", "INSERT INTO")
        for statement in raw:
            self.assertTrue(statement.startswith(allowed), statement[:80])
        self.assertEqual(re.findall(r"INSERT INTO (\w+)", self.code), ["cash_movement_types", "old_gold_movement_types", "schema_migrations"])
        for statement in [s.sql for s in sql.statements(self.forward)]:
            self.assertNotRegex(statement, r"^(UPDATE|DELETE|TRUNCATE|DROP TABLE)\b")
        self.assertEqual(self.forward.count("ON DUPLICATE KEY UPDATE"), 3)                           # a re-run changes nothing

    def test_the_rollback_refuses_on_any_marketer_history_or_a_later_migration_and_drops_children_first(self):
        self.assertIn("ROLLBACK_055_REFUSED_history_or_later_migration", self.rollback)
        for needle in ("FROM marketer_documents", "FROM marketer_cash_movements", "FROM marketer_gold_movements", "source_kind='HOLDING_MKT'",
                       "source_type='MARKETER_DOCUMENT'", "version>55"):
            self.assertIn(needle, self.rollback)
        order = ["ROLLBACK_055_REFUSED", "DROP TABLE IF EXISTS marketer_gold_movements", "DROP TABLE IF EXISTS marketer_cash_movements",
                 "DROP TABLE IF EXISTS marketer_purchase_lines", "DROP TABLE IF EXISTS marketer_shop_links", "DROP TABLE IF EXISTS marketer_documents",
                 "ADD CONSTRAINT chk_sgs_shape", "DROP INDEX uq_sgs_marketer", "ADD CONSTRAINT chk_correction_source", "DELETE FROM schema_migrations WHERE version=55"]
        positions = [self.rollback.index(item) for item in order]
        self.assertEqual(positions, sorted(positions))
        self.assertNotRegex(code_of(self.rollback), r"DELETE FROM (supplier|market_trip|old_gold|cash|bank|marketer)")


if __name__ == "__main__":
    unittest.main()
