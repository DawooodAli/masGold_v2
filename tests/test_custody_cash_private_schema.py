"""Migration 105 (market-custody cash straight to the owner's private account): static contract of the forward and rollback files. No database is used; the live behaviour (constraints,
refusals, rollback) needs an approved rehearsal run."""
import re
import unittest

from tests import migration_sql as sql
from tests import schema_model
from tools.schema_rehearsal.chain import load_chain

FORWARD = "105_custody_cash_to_private.sql"
ROLLBACK = "105_custody_cash_to_private_rollback.sql"


def kinds(text, name="chk_mte_kind"):
    return re.findall(r"'(\w+)'", re.search(rf"ADD CONSTRAINT {name} CHECK \(entry_kind IN \(([^)]*)\)\)", text).group(1))


class CustodyCashPrivateSchemaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.model = schema_model.build()
        cls.forward = sql.read(FORWARD)
        cls.rollback = sql.read(ROLLBACK, sql.ROLLBACK_DIR)
        cls.m080 = sql.read("080_market_trip_bar_fee.sql")

    def test_the_chain_replays_and_105_requires_104_and_the_private_table(self):
        self.assertEqual(self.model.problems, [])
        self.assertIn("version=104", self.forward.replace(" ", ""))
        self.assertIn("MIGRATION_105_REFUSED_missing_104", self.forward)
        self.assertIn("MIGRATION_105_REFUSED_missing_090_private_account_entries", self.forward)
        self.assertRegex(self.forward, r"INSERT INTO schema_migrations\(version,name\) VALUES \(105,")
        self.assertEqual(len({f.name: f for f in load_chain(require_committed=False)}), 109)

    def test_the_new_kind_is_legacy_only_fits_the_column_and_the_rollback_restores_080(self):
        self.assertEqual(kinds(self.forward), kinds(self.m080) + ["ST_CASH_PRIV"])
        self.assertEqual(kinds(self.rollback), kinds(self.m080))
        self.assertLessEqual(len("ST_CASH_PRIV"), 12)                                                  # entry_kind is VARCHAR(12)
        rule = re.search(r"ADD CONSTRAINT chk_mte_mode_kind CHECK \((.*?)\)\", 'DO 0'\);", self.forward, re.S).group(1)
        legacy = re.findall(r"'(\w+)'", re.search(r"trip_mode = 'LEGACY' AND entry_kind IN \(([^)]*)\)", rule).group(1))
        book = re.findall(r"'(\w+)'", re.search(r"trip_mode = 'BOOK' AND entry_kind IN \(([^)]*)\)", rule).group(1))
        self.assertIn("ST_CASH_PRIV", legacy)
        self.assertNotIn("ST_CASH_PRIV", book)

    def test_the_settlement_document_accepts_the_private_kind_without_a_bank(self):
        for needle in ("entry_kind = 'ST_CASH_PRIV' AND bank_account_id IS NULL AND bank_movement_id IS NULL",
                       "entry_kind = 'ST_CASH_BANK' AND bank_account_id IS NOT NULL AND bank_movement_id IS NOT NULL",
                       "entry_kind IN ('ST_EXPENSE','ST_CASH_OFF') AND description IS NOT NULL AND CHAR_LENGTH(TRIM(description)) > 0"):
            self.assertIn(needle, self.forward)
        self.assertNotIn("ST_CASH_PRIV", re.search(r"ADD CONSTRAINT chk_mtsc_shape CHECK \((.*?)\)\", 'DO 0'\);", self.rollback, re.S).group(1))

    def test_a_private_entry_has_exactly_one_source_tied_to_a_custody_cash_entry(self):
        for needle in ("MODIFY COLUMN operation_id BIGINT UNSIGNED DEFAULT NULL", "ADD COLUMN trip_entry_id BIGINT UNSIGNED DEFAULT NULL", "ADD COLUMN trip_entry_kind VARCHAR(12) DEFAULT NULL",
                       "ADD UNIQUE KEY uq_private_entry_trip_entry (trip_entry_id)",
                       "FOREIGN KEY (trip_entry_id, trip_entry_kind) REFERENCES market_trip_entries(id, entry_kind)",
                       "(operation_id IS NOT NULL AND trip_entry_id IS NULL AND trip_entry_kind IS NULL) OR (operation_id IS NULL AND trip_entry_id IS NOT NULL AND trip_entry_kind = 'ST_CASH_PRIV' AND direction = 'TO_PRIVATE')"):
            self.assertIn(needle, self.forward)
        self.assertNotIn("TRIGGER", self.forward.upper())
        self.assertNotIn("DELETE FROM", self.forward.upper().replace("DELETE FROM SCHEMA_MIGRATIONS", ""))   # no history is ever rewritten

    def test_the_rollback_refuses_with_history_before_any_change(self):
        for needle in ("ROLLBACK_105_REFUSED_custody_private_entries_exist", "ROLLBACK_105_REFUSED_custody_private_transfers_exist", "version > 105",
                       "MODIFY COLUMN operation_id BIGINT UNSIGNED NOT NULL", "DELETE FROM schema_migrations WHERE version = 105"):
            self.assertIn(needle, self.rollback)
        first_change = self.rollback.index("DROP CHECK chk_private_entry_source")
        for refusal in ("ROLLBACK_105_REFUSED_later_migration_exists", "ROLLBACK_105_REFUSED_custody_private_entries_exist", "ROLLBACK_105_REFUSED_custody_private_transfers_exist"):
            self.assertLess(self.rollback.index(refusal), first_change)

    def test_the_checksum_and_the_required_level_follow_the_chain(self):
        from services.schema_level import REQUIRED_SCHEMA_LEVEL
        manifest = sql.SCHEMA_DIR.joinpath("SHA256SUMS").read_text(encoding="utf-8")
        import hashlib
        digest = hashlib.sha256((sql.SCHEMA_DIR / FORWARD).read_bytes()).hexdigest()
        self.assertIn(f"{digest}  {FORWARD}", manifest)
        self.assertGreaterEqual(REQUIRED_SCHEMA_LEVEL, 105)


if __name__ == "__main__":
    unittest.main()
