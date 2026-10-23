"""Migration 067 (position snapshots for the gold profit report): static contract of the forward and rollback files. No database is used."""
import re
import unittest

from tests import migration_sql as sql
from tests import schema_model
from tools.schema_rehearsal.chain import load_chain

FORWARD = "067_position_snapshots.sql"
ROLLBACK = "067_position_snapshots_rollback.sql"


class PositionSnapshotSchemaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.model = schema_model.build()
        cls.forward = sql.read(FORWARD)
        cls.rollback = sql.read(ROLLBACK, sql.ROLLBACK_DIR)

    def test_the_chain_replays_and_067_requires_066(self):
        self.assertEqual(self.model.problems, [])
        self.assertIn("version=66", self.forward.replace(" ", ""))
        self.assertIn("MIGRATION_067_REFUSED_missing_066", self.forward)
        self.assertRegex(self.forward, r"INSERT INTO schema_migrations\(version,name\) VALUES \(67,")
        self.assertLessEqual(len(re.search(r"VALUES \(67,'([^']*)'", self.forward).group(1)), 150)
        self.assertEqual(len({f.name: f for f in load_chain(require_committed=False)}), 77)

    def test_the_snapshot_table_has_one_opening_and_a_consistent_total(self):
        for needle in ("CREATE TABLE IF NOT EXISTS position_snapshots", "snapshot_kind IN ('OPENING', 'DAILY', 'MANUAL')", "opening_flag TINYINT GENERATED ALWAYS AS (IF(snapshot_kind = 'OPENING', 1, NULL)) STORED",
                       "UNIQUE KEY uq_position_snapshot_one_opening (opening_flag)",
                       "gold_total_eq21 = gold_shop_eq21 + gold_custody_eq21 + gold_intermediary_eq21 + gold_book_eq21 + gold_suppliers_eq21", "unknown_grams >= 0"):
            self.assertIn(needle, self.forward)
        self.assertNotIn("TRIGGER", self.forward.upper())

    def test_the_rollback_refuses_when_a_snapshot_exists(self):
        for needle in ("ROLLBACK_067_REFUSED_snapshots_exist", "FROM position_snapshots", "version > 67", "DELETE FROM schema_migrations WHERE version = 67"):
            self.assertIn(needle, self.rollback)
        self.assertLess(self.rollback.index("ROLLBACK_067_REFUSED"), self.rollback.index("DROP TABLE"))


if __name__ == "__main__":
    unittest.main()
