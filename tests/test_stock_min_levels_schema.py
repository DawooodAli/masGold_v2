"""Migration 072 (minimum stock levels): static contract of the forward and rollback files. No database is used."""
import re
import unittest

from tests import migration_sql as sql
from tests import schema_model
from tools.schema_rehearsal.chain import load_chain

FORWARD = "072_stock_min_levels.sql"
ROLLBACK = "072_stock_min_levels_rollback.sql"


class StockMinLevelsSchemaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.model = schema_model.build()
        cls.forward = sql.read(FORWARD)
        cls.rollback = sql.read(ROLLBACK, sql.ROLLBACK_DIR)

    def test_the_chain_replays_and_072_requires_071(self):
        self.assertEqual(self.model.problems, [])
        self.assertIn("version=71", self.forward.replace(" ", ""))
        self.assertIn("MIGRATION_072_REFUSED_missing_071", self.forward)
        self.assertRegex(self.forward, r"INSERT INTO schema_migrations\(version,name\) VALUES \(72,")
        self.assertLessEqual(len(re.search(r"VALUES \(72,'([^']*)'", self.forward).group(1)), 150)
        self.assertEqual(len({f.name: f for f in load_chain(require_committed=False)}), 74)

    def test_one_rule_per_type_and_karat_with_at_least_one_positive_minimum(self):
        for needle in ("CREATE TABLE IF NOT EXISTS stock_min_levels", "UNIQUE KEY uq_stock_min_level (item_type_id, karat_id)", "FOREIGN KEY (item_type_id) REFERENCES item_types(id) ON DELETE CASCADE",
                       "(min_pieces IS NOT NULL OR min_grams IS NOT NULL)", "min_pieces >= 1", "min_grams > 0"):
            self.assertIn(needle, self.forward)
        self.assertNotIn("TRIGGER", self.forward.upper())

    def test_the_rollback_refuses_while_any_rule_exists(self):
        for needle in ("ROLLBACK_072_REFUSED_rules_exist", "FROM stock_min_levels", "version > 72", "DELETE FROM schema_migrations WHERE version = 72"):
            self.assertIn(needle, self.rollback)
        self.assertLess(self.rollback.index("ROLLBACK_072_REFUSED"), self.rollback.index("DROP TABLE"))


if __name__ == "__main__":
    unittest.main()
