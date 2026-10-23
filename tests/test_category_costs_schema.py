"""Migration 070 (making cost per category): static contract of the forward and rollback files. No database is used."""
import re
import unittest

from tests import migration_sql as sql
from tests import schema_model
from tools.schema_rehearsal.chain import load_chain

FORWARD = "070_category_making_costs.sql"
ROLLBACK = "070_category_making_costs_rollback.sql"


class CategoryCostSchemaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.model = schema_model.build()
        cls.forward = sql.read(FORWARD)
        cls.rollback = sql.read(ROLLBACK, sql.ROLLBACK_DIR)

    def test_the_chain_replays_and_070_requires_069(self):
        self.assertEqual(self.model.problems, [])
        self.assertIn("version=69", self.forward.replace(" ", ""))
        self.assertIn("MIGRATION_070_REFUSED_missing_069", self.forward)
        self.assertRegex(self.forward, r"INSERT INTO schema_migrations\(version,name\) VALUES \(70,")
        self.assertLessEqual(len(re.search(r"VALUES \(70,'([^']*)'", self.forward).group(1)), 150)
        self.assertEqual(len({f.name: f for f in load_chain(require_committed=False)}), 75)

    def test_one_non_negative_cost_per_category_and_no_existing_row_is_touched(self):
        for needle in ("CREATE TABLE IF NOT EXISTS item_category_costs", "PRIMARY KEY (category_id)", "FOREIGN KEY (category_id) REFERENCES item_categories(id) ON DELETE CASCADE",
                       "making_cost_per_gram DECIMAL(12,2) NOT NULL", "making_cost_per_gram >= 0"):
            self.assertIn(needle, self.forward)
        self.assertNotIn("TRIGGER", self.forward.upper())
        self.assertNotRegex(self.forward, r"(?i)\b(update|delete)\b\s+(from\s+)?(inventory_items|item_categories|invoice_lines)")

    def test_the_rollback_refuses_while_any_cost_is_set(self):
        for needle in ("ROLLBACK_070_REFUSED_costs_exist", "FROM item_category_costs", "version > 70", "DELETE FROM schema_migrations WHERE version = 70"):
            self.assertIn(needle, self.rollback)
        self.assertLess(self.rollback.index("ROLLBACK_070_REFUSED"), self.rollback.index("DROP TABLE"))


if __name__ == "__main__":
    unittest.main()
