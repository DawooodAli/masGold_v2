"""Migration 066 (the old shop's gold price history): static contract of the forward and rollback files. No database is used."""
import re
import unittest

from tests import migration_sql as sql
from tests import schema_model
from tools.schema_rehearsal.chain import load_chain

FORWARD = "066_gold_price_history_legacy.sql"
ROLLBACK = "066_gold_price_history_legacy_rollback.sql"


class PriceHistorySchemaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.model = schema_model.build()
        cls.forward = sql.read(FORWARD)
        cls.rollback = sql.read(ROLLBACK, sql.ROLLBACK_DIR)

    def test_the_chain_replays_and_066_requires_065(self):
        self.assertEqual(self.model.problems, [])
        self.assertIn("version=65", self.forward.replace(" ", ""))
        self.assertIn("MIGRATION_066_REFUSED_missing_065", self.forward)
        self.assertRegex(self.forward, r"INSERT INTO schema_migrations\(version,name\) VALUES \(66,")
        self.assertLessEqual(len(re.search(r"VALUES \(66,'([^']*)'", self.forward).group(1)), 150)
        self.assertEqual(len({f.name: f for f in load_chain(require_committed=False)}), 70)

    def test_the_history_table_is_a_daily_per_karat_insert_only_record(self):
        for needle in ("CREATE TABLE IF NOT EXISTS gold_price_history_legacy", "PRIMARY KEY (price_date, karat_value)", "karat_value IN (18, 21, 22, 24)",
                       "shop_buy > 0 AND shop_sell >= shop_buy", "source = 'GOLDDB'", "entries_in_day >= 1"):
            self.assertIn(needle, self.forward)
        self.assertNotIn("TRIGGER", self.forward.upper())
        self.assertNotRegex(self.forward, r"(?i)\bgolddb\.")                                    # the old database is never referenced as a schema

    def test_the_rollback_refuses_when_the_history_holds_rows(self):
        for needle in ("ROLLBACK_066_REFUSED_price_history_exists", "FROM gold_price_history_legacy", "version > 66", "DELETE FROM schema_migrations WHERE version = 66"):
            self.assertIn(needle, self.rollback)
        self.assertLess(self.rollback.index("ROLLBACK_066_REFUSED"), self.rollback.index("DROP TABLE"))


if __name__ == "__main__":
    unittest.main()
