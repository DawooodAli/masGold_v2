"""Migration 060 (market-custody settlement step 2a: ST_MELT): static replay and contract of the forward and rollback files. No database is used; the live behaviour
(constraints, refusals, rollback) is NOT proven here and still needs an approved rehearsal run."""
import hashlib
from pathlib import Path
import re
import unittest

from tests import migration_sql as sql
from tests import schema_model
from tools.schema_rehearsal.chain import load_chain

ROOT = Path(__file__).resolve().parents[1]
FORWARD = "060_market_trip_settlement_melt.sql"
ROLLBACK = "060_market_trip_settlement_melt_rollback.sql"
FROZEN = {"059_market_trip_book_gain.sql": "1c6de6505090cb7362e3834eececb51d69a5da510c37f079ee61c22c44f0ad2b",
          "058_market_trip_book_cycle.sql": "c49f1cd8545f8e01fe402ec67d4483e2092ecb2b5975d560a9df78cfeab840ef"}
EARLIER_LEGACY = ("ISSUE", "RETURN", "MELT", "LOT_RETURN", "LOT_ISSUE", "HEAT", "SUPPLIER_PAY", "GOLD_SETTLE", "LOT_SETTLE", "LOT_HOLD", "HOLD_RETURN", "LOGO_HOLD",
                  "HOLD_BACK", "GOLD_SALE", "GOLD_BUY")
BOOK_KINDS = ("BK_ISSUE", "BK_BUY", "BK_SELL", "BK_RETURN", "BK_RECON", "BK_ISS_STK", "BK_DLV_SUP", "BK_DLV_MKT", "BK_RECEIVE", "BK_GAIN")


def digest(name):
    return hashlib.sha256((ROOT / "database" / "schema" / name).read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def code_of(text):
    return " ".join(line for line in text.splitlines() if not line.lstrip().startswith("--"))


def table(forward, name):
    return re.search(rf"CREATE TABLE IF NOT EXISTS {name} \((.*?)\) ENGINE=InnoDB", forward, re.S).group(1)


class SettlementMeltSchemaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.model = schema_model.build()
        cls.forward = sql.read(FORWARD)
        cls.rollback = sql.read(ROLLBACK, sql.ROLLBACK_DIR)
        cls.code = code_of(cls.forward)

    def test_the_chain_replays_cleanly_and_the_three_tables_exist(self):
        self.assertEqual(self.model.problems, [])
        for name in ("market_trip_settle_melts", "market_trip_settle_melt_inputs", "market_trip_settle_moves"):
            self.assertEqual(self.model.tables[name]["source"], FORWARD)

    def test_it_requires_059_and_registers_060(self):
        self.assertIn("version=59", self.forward.replace(" ", ""))
        self.assertIn("MIGRATION_060_REFUSED_missing_059", self.forward)
        self.assertRegex(self.forward, r"INSERT INTO schema_migrations\(version,name\) VALUES \(60,")
        self.assertLessEqual(len(re.search(r"VALUES \(60,'([^']*)'", self.forward).group(1)), 150)         # schema_migrations.name is VARCHAR(150)

    def test_earlier_migrations_are_untouched_and_the_manifest_matches(self):
        for name, expected in FROZEN.items():
            self.assertEqual(digest(name), expected, name)
        files = {f.name: f for f in load_chain(require_committed=False)}
        self.assertEqual(len(files), 74)
        self.assertEqual(files[FORWARD].sha256, digest(FORWARD))
        self.assertIn(f"{digest(FORWARD)}  {FORWARD}", (ROOT / "database" / "schema" / "SHA256SUMS").read_text(encoding="utf-8"))

    def test_the_kind_rules_add_st_melt_to_legacy_only_and_keep_everything_else(self):
        kind = re.findall(r"'(\w+)'", re.search(r"ADD CONSTRAINT chk_mte_kind CHECK \(entry_kind IN \(([^)]*)\)\)", self.forward).group(1))
        self.assertEqual(kind, list(EARLIER_LEGACY) + list(BOOK_KINDS) + ["ST_MELT"])
        self.assertLessEqual(len("ST_MELT"), 12)                                                    # entry_kind is VARCHAR(12)
        rule = re.search(r"ADD CONSTRAINT chk_mte_mode_kind CHECK \((.*?)\)\", 'DO 0'\)", self.forward, re.S).group(1)
        legacy = re.findall(r"'(\w+)'", re.search(r"trip_mode = 'LEGACY' AND entry_kind IN \(([^)]*)\)", rule).group(1))
        book = re.findall(r"'(\w+)'", re.search(r"trip_mode = 'BOOK' AND entry_kind IN \(([^)]*)\)", rule).group(1))
        self.assertEqual(legacy, list(EARLIER_LEGACY) + ["ST_MELT"])                                  # legacy trips only
        self.assertNotIn("ST_MELT", book)
        back = re.findall(r"'(\w+)'", re.search(r"ADD CONSTRAINT chk_mte_kind CHECK \(entry_kind IN \(([^)]*)\)\)", self.rollback).group(1))
        self.assertEqual(back, list(EARLIER_LEGACY) + list(BOOK_KINDS))                              # the rollback restores the 059 vocabulary

    def test_the_melt_document_is_tied_to_its_entry_and_proves_its_own_arithmetic(self):
        head = table(self.forward, "market_trip_settle_melts")
        for needle in ("PRIMARY KEY (entry_id)", "entry_kind VARCHAR(12) NOT NULL DEFAULT 'ST_MELT'", "workshop VARCHAR(120) NOT NULL", "fineness_permille SMALLINT UNSIGNED NOT NULL",
                       "FOREIGN KEY (entry_id, entry_kind) REFERENCES market_trip_entries(id, entry_kind)", "entry_kind = 'ST_MELT'", "CHAR_LENGTH(TRIM(workshop)) > 0",
                       "input_weight_grams > 0 AND output_weight_grams > 0", "fineness_permille BETWEEN 1 AND 1000", "theoretical_eq21 >= 0 AND actual_eq21 >= 0",
                       "loss_eq21 = theoretical_eq21 - actual_eq21 AND weight_loss_eq21 + fineness_loss_eq21 = loss_eq21"):
            self.assertIn(needle, head)
        self.assertNotRegex(head, r"fineness_permille\s+DECIMAL")                                      # an INTEGER per mille, never a decimal

    def test_the_inputs_and_the_pool_ledger(self):
        inputs = table(self.forward, "market_trip_settle_melt_inputs")
        for needle in ("PRIMARY KEY (entry_id, karat_id)", "FOREIGN KEY (entry_id) REFERENCES market_trip_settle_melts(entry_id)",
                       "FOREIGN KEY (karat_id) REFERENCES karats(id)", "weight_grams > 0"):
            self.assertIn(needle, inputs)
        moves = table(self.forward, "market_trip_settle_moves")
        for needle in ("id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT", "pool VARCHAR(4) NOT NULL", "karat_id TINYINT UNSIGNED DEFAULT NULL", "delta DECIMAL(14,3) NOT NULL",
                       "FOREIGN KEY (entry_id, entry_kind, trip_id) REFERENCES market_trip_entries(id, entry_kind, trip_id)", "FOREIGN KEY (karat_id) REFERENCES karats(id)",
                       "pool = 'RAW' AND karat_id IS NOT NULL", "pool = 'BAR' AND karat_id IS NULL", "delta <> 0"):
            self.assertIn(needle, moves)
        self.assertIn("ADD UNIQUE KEY uq_mte_id_kind_trip (id,entry_kind,trip_id)", sql.read("048_intermediary_lot_holdings.sql"))     # the composite key the FK references exists

    def test_no_trigger_no_update_no_delete_of_history(self):
        self.assertNotIn("TRIGGER", self.code.upper())
        self.assertNotRegex(self.code.upper(), r"\bUPDATE\s+MARKET_TRIP|\bDELETE\s+FROM\s+MARKET_TRIP")

    def test_the_rollback_refuses_while_any_settlement_history_exists(self):
        code = code_of(self.rollback)
        for needle in ("ROLLBACK_060_REFUSED_settlement_history_exists", "entry_kind = 'ST_MELT'", "FROM market_trip_settle_melts", "FROM market_trip_settle_moves",
                       "version > 60", "DELETE FROM schema_migrations WHERE version = 60"):
            self.assertIn(needle, code)
        self.assertLess(code.index("ROLLBACK_060_REFUSED"), code.index("DROP TABLE IF EXISTS market_trip_settle_moves"))     # the refusal comes BEFORE any drop


if __name__ == "__main__":
    unittest.main()
