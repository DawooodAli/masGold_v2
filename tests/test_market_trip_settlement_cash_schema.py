"""Migration 064 (settlement step 2e: the custody cash settled after the trip): static contract of the forward and rollback files. No database is used; the live behaviour
(constraints, refusals, rollback) needs an approved rehearsal run."""
import re
import unittest

from tests import migration_sql as sql
from tests import schema_model
from tools.schema_rehearsal.chain import load_chain

FORWARD = "064_market_trip_settlement_cash.sql"
ROLLBACK = "064_market_trip_settlement_cash_rollback.sql"
NEW_KINDS = ("ST_EXPENSE", "ST_CASH_BANK", "ST_CASH_OFF")


def kinds(text):
    return re.findall(r"'(\w+)'", re.search(r"ADD CONSTRAINT chk_mte_kind CHECK \(entry_kind IN \(([^)]*)\)\)", text).group(1))


class CashSettleSchemaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.model = schema_model.build()
        cls.forward = sql.read(FORWARD)
        cls.rollback = sql.read(ROLLBACK, sql.ROLLBACK_DIR)
        cls.m062 = sql.read("062_market_trip_settlement_marketer.sql")

    def test_the_chain_replays_and_064_requires_063(self):
        self.assertEqual(self.model.problems, [])
        self.assertIn("version=63", self.forward.replace(" ", ""))
        self.assertIn("MIGRATION_064_REFUSED_missing_063", self.forward)
        self.assertRegex(self.forward, r"INSERT INTO schema_migrations\(version,name\) VALUES \(64,")
        self.assertLessEqual(len(re.search(r"VALUES \(64,'([^']*)'", self.forward).group(1)), 150)
        self.assertEqual(len({f.name: f for f in load_chain(require_committed=False)}), 67)

    def test_the_three_kinds_are_legacy_only_and_fit_the_column(self):
        self.assertEqual(kinds(self.forward), kinds(self.m062) + list(NEW_KINDS))
        self.assertEqual(kinds(self.rollback), kinds(self.m062))
        self.assertLessEqual(max(len(k) for k in NEW_KINDS), 12)                                    # entry_kind is VARCHAR(12)
        rule = re.search(r"ADD CONSTRAINT chk_mte_mode_kind CHECK \((.*?)\)\", 'DO 0'\);", self.forward, re.S).group(1)
        legacy = re.findall(r"'(\w+)'", re.search(r"trip_mode = 'LEGACY' AND entry_kind IN \(([^)]*)\)", rule).group(1))
        book = re.findall(r"'(\w+)'", re.search(r"trip_mode = 'BOOK' AND entry_kind IN \(([^)]*)\)", rule).group(1))
        for kind in NEW_KINDS:
            self.assertIn(kind, legacy)
            self.assertNotIn(kind, book)

    def test_the_document_shape(self):
        for needle in ("CREATE TABLE IF NOT EXISTS market_trip_settle_cash", "chk_mtsc_amount CHECK (amount > 0)", "uq_mtsc_movement",
                       "entry_kind IN ('ST_EXPENSE','ST_CASH_OFF') AND description IS NOT NULL AND CHAR_LENGTH(TRIM(description)) > 0 AND bank_account_id IS NULL AND bank_movement_id IS NULL",
                       "entry_kind = 'ST_CASH_BANK' AND bank_account_id IS NOT NULL AND bank_movement_id IS NOT NULL",
                       "FOREIGN KEY (entry_id, entry_kind) REFERENCES market_trip_entries(id, entry_kind)", "TRIP_CUSTODY_BANK_IN"):
            self.assertIn(needle, self.forward)
        self.assertNotIn("TRIGGER", self.forward.upper())

    def test_the_purchase_line_accepts_no_destination_and_the_rollback_restores_054(self):
        self.assertIn("designation IN ('MELT','LOGO','UNCLASSIFIED')", self.forward)
        self.assertIn("designation IN ('MELT','LOGO')", self.rollback)

    def test_the_rollback_refuses_with_history_before_any_change(self):
        for needle in ("ROLLBACK_064_REFUSED_cash_settlement_history_exists", "entry_kind IN ('ST_EXPENSE','ST_CASH_BANK','ST_CASH_OFF')", "FROM market_trip_settle_cash",
                       "TRIP_CUSTODY_BANK_IN", "designation = 'UNCLASSIFIED'", "version > 64", "DELETE FROM schema_migrations WHERE version = 64"):
            self.assertIn(needle, self.rollback)
        self.assertLess(self.rollback.index("ROLLBACK_064_REFUSED"), self.rollback.index("DROP TABLE"))


if __name__ == "__main__":
    unittest.main()
