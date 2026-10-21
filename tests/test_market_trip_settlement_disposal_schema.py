"""Migration 065 (settlement step 2f: return to the shop, loss, issue-weight correction): static contract of the forward and rollback files. No database is used; the live behaviour
(constraints, refusals, rollback) needs an approved rehearsal run."""
import re
import unittest

from tests import migration_sql as sql
from tests import schema_model
from tools.schema_rehearsal.chain import load_chain

FORWARD = "065_market_trip_settlement_disposal.sql"
ROLLBACK = "065_market_trip_settlement_disposal_rollback.sql"
NEW_KINDS = ("ST_RET_MKT", "ST_LOSS", "ST_ADJUST")


def kinds(text):
    return re.findall(r"'(\w+)'", re.search(r"ADD CONSTRAINT chk_mte_kind CHECK \(entry_kind IN \(([^)]*)\)\)", text).group(1))


class DisposalSchemaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.model = schema_model.build()
        cls.forward = sql.read(FORWARD)
        cls.rollback = sql.read(ROLLBACK, sql.ROLLBACK_DIR)
        cls.m064 = sql.read("064_market_trip_settlement_cash.sql")

    def test_the_chain_replays_and_065_requires_064(self):
        self.assertEqual(self.model.problems, [])
        self.assertIn("version=64", self.forward.replace(" ", ""))
        self.assertIn("MIGRATION_065_REFUSED_missing_064", self.forward)
        self.assertRegex(self.forward, r"INSERT INTO schema_migrations\(version,name\) VALUES \(65,")
        self.assertLessEqual(len(re.search(r"VALUES \(65,'([^']*)'", self.forward).group(1)), 150)
        self.assertEqual(len({f.name: f for f in load_chain(require_committed=False)}), 67)

    def test_the_three_kinds_are_legacy_only_and_fit_the_column(self):
        self.assertEqual(kinds(self.forward), kinds(self.m064) + list(NEW_KINDS))
        self.assertEqual(kinds(self.rollback), kinds(self.m064))
        self.assertLessEqual(max(len(k) for k in NEW_KINDS), 12)                                    # entry_kind is VARCHAR(12)
        rule = re.search(r"ADD CONSTRAINT chk_mte_mode_kind CHECK \((.*?)\)\", 'DO 0'\);", self.forward, re.S).group(1)
        legacy = re.findall(r"'(\w+)'", re.search(r"trip_mode = 'LEGACY' AND entry_kind IN \(([^)]*)\)", rule).group(1))
        book = re.findall(r"'(\w+)'", re.search(r"trip_mode = 'BOOK' AND entry_kind IN \(([^)]*)\)", rule).group(1))
        for kind in NEW_KINDS:
            self.assertIn(kind, legacy)
            self.assertNotIn(kind, book)

    def test_the_pool_ledger_accepts_the_kinds_and_the_rollback_restores_the_previous_text(self):
        self.assertIn("entry_kind IN ('ST_MELT','ST_BAR_SUP','ST_BAR_MKT','ST_LOGO','GOLD_SALE','ST_RET_MKT','ST_LOSS','ST_ADJUST')", self.forward)
        self.assertIn("entry_kind IN ('ST_MELT','ST_BAR_SUP','ST_BAR_MKT','ST_LOGO','GOLD_SALE')", self.rollback)

    def test_the_document_shapes(self):
        for needle in ("CREATE TABLE IF NOT EXISTS market_trip_settle_lines", "chk_mtsl_weight CHECK (weight_grams > 0)",
                       "(source IN ('RAW','LOGO') AND karat_id IS NOT NULL) OR (source = 'BAR' AND karat_id IS NULL)",
                       "(entry_kind = 'ST_RET_MKT' AND classification IS NULL) OR (entry_kind = 'ST_LOSS' AND classification IN ('CRUCIBLE','MARKET'))",
                       "UNIQUE KEY uq_mtsl_line (entry_id, line_no)", "FOREIGN KEY (entry_id, entry_kind) REFERENCES market_trip_entries(id, entry_kind)",
                       "CREATE TABLE IF NOT EXISTS market_trip_settle_adjust", "chk_mtsa_direction CHECK (direction IN ('INCREASE','DECREASE'))",
                       "chk_mtsa_reason CHECK (CHAR_LENGTH(TRIM(reason)) > 0)"):
            self.assertIn(needle, self.forward)
        self.assertNotIn("TRIGGER", self.forward.upper())

    def test_the_rollback_refuses_with_history_before_any_change(self):
        for needle in ("ROLLBACK_065_REFUSED_disposal_history_exists", "entry_kind IN ('ST_RET_MKT','ST_LOSS','ST_ADJUST')", "FROM market_trip_settle_lines",
                       "FROM market_trip_settle_adjust", "version > 65", "DELETE FROM schema_migrations WHERE version = 65"):
            self.assertIn(needle, self.rollback)
        self.assertLess(self.rollback.index("ROLLBACK_065_REFUSED"), self.rollback.index("DROP TABLE"))


if __name__ == "__main__":
    unittest.main()
