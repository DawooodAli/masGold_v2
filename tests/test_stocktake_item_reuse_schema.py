"""Migration 094 (a piece written off by a stocktake, restored and written off again): static contract of the forward and rollback files. No database is used."""
import re
import unittest

from tests import migration_sql as sql
from tests import schema_model
from tools.schema_rehearsal.chain import load_chain

FORWARD = "094_stocktake_item_reuse.sql"
ROLLBACK = "094_stocktake_item_reuse_rollback.sql"


class StocktakeItemReuseSchemaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.model = schema_model.build()
        cls.forward = sql.read(FORWARD)
        cls.rollback = sql.read(ROLLBACK, sql.ROLLBACK_DIR)

    def test_the_chain_replays_and_094_requires_093(self):
        self.assertEqual(self.model.problems, [])
        self.assertEqual(len({f.name: f for f in load_chain(require_committed=False)}), 100)
        self.assertIn("MIGRATION_094_REFUSED_missing_093", self.forward)
        self.assertRegex(self.forward, r"INSERT INTO schema_migrations\(version,name\) VALUES \(94,")
        self.assertLessEqual(len(re.search(r"VALUES \(94,'([^']*)'", self.forward).group(1)), 150)

    def test_the_new_index_comes_before_the_unique_key_is_dropped_and_both_steps_are_guarded(self):
        add, drop = self.forward.index("ADD KEY idx_stocktake_item_piece"), self.forward.index("DROP INDEX uq_stocktake_item_once")
        self.assertLess(add, drop)                                                              # the foreign key on inventory_item_id always keeps an index
        self.assertEqual(self.forward.count("information_schema.STATISTICS"), 2)

    def test_the_rollback_refuses_while_a_repeated_write_off_exists(self):
        self.assertIn("ROLLBACK_094_REFUSED", self.rollback)
        self.assertIn("HAVING COUNT(*) > 1", self.rollback)
        self.assertIn("ADD UNIQUE KEY uq_stocktake_item_once", self.rollback)
        self.assertIn("DELETE FROM schema_migrations WHERE version = 94", self.rollback)


if __name__ == "__main__":
    unittest.main()
