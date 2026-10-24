"""Migration 079 records the source of a market-custody weight adjustment."""
import unittest

from tests import migration_sql as sql
from tools.schema_rehearsal.chain import LAST, load_chain


FORWARD = "079_market_trip_adjustment_source.sql"
ROLLBACK = "079_market_trip_adjustment_source_rollback.sql"


class MarketTripAdjustmentSourceSchemaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.forward = sql.read(FORWARD)
        cls.rollback = sql.read(ROLLBACK, sql.ROLLBACK_DIR)

    def test_migration_is_the_current_chain_tip_and_is_checksummed(self):
        files = load_chain(require_committed=False)
        self.assertEqual((len(files), LAST, files[78].name), (80, 80, FORWARD))
        self.assertIn(f"  {FORWARD}", (sql.SCHEMA_DIR / "SHA256SUMS").read_text(encoding="utf-8"))

    def test_old_adjustments_default_to_scrap_and_new_source_is_increase_only(self):
        self.assertIn("version = 78", self.forward)
        self.assertIn("COLUMN_NAME='source_kind'", self.forward)
        self.assertIn("source_kind VARCHAR(12) NOT NULL DEFAULT 'SHOP_SCRAP'", self.forward)
        self.assertIn("(direction = 'INCREASE' AND source_kind IN ('SHOP_SCRAP','UNSOURCED'))", self.forward)
        self.assertIn("(direction = 'DECREASE' AND source_kind = 'SHOP_SCRAP')", self.forward)
        self.assertIn("VALUES (79,'Market-custody issue-weight correction source')", self.forward)

    def test_rollback_refuses_unsourced_history_or_a_later_migration(self):
        self.assertIn("source_kind <> 'SHOP_SCRAP'", self.rollback)
        self.assertIn("schema_migrations WHERE version > 79", self.rollback)
        self.assertIn("DROP COLUMN source_kind", self.rollback)


if __name__ == "__main__":
    unittest.main()
