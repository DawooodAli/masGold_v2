"""Migration 075 (Scrap adjustment right for the manager): static contract of the forward and rollback files. No database is used."""
import re
import unittest

from tests import migration_sql as sql
from tests import schema_model
from tools.schema_rehearsal.chain import load_chain

FORWARD = "075_old_gold_adjust_manager.sql"
ROLLBACK = "075_old_gold_adjust_manager_rollback.sql"


class OldGoldAdjustManagerSchemaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.model = schema_model.build()
        cls.forward = sql.read(FORWARD)
        cls.rollback = sql.read(ROLLBACK, sql.ROLLBACK_DIR)

    def test_the_chain_replays_and_075_requires_074(self):
        self.assertEqual(self.model.problems, [])
        self.assertIn("version=74", self.forward.replace(" ", ""))
        self.assertIn("MIGRATION_075_REFUSED_missing_074", self.forward)
        self.assertRegex(self.forward, r"INSERT INTO schema_migrations\(version,name\) VALUES \(75,")
        self.assertLessEqual(len(re.search(r"VALUES \(75,'([^']*)'", self.forward).group(1)), 150)
        self.assertEqual(len({f.name: f for f in load_chain(require_committed=False)}), 75)

    def test_only_the_existing_permission_is_granted_to_the_manager_and_the_owner_idempotently(self):
        self.assertIn("p.code = 'OLD_GOLD_ADJUST'", self.forward)
        self.assertIn("r.code IN ('OWNER', 'MANAGER')", self.forward)
        self.assertIn("NOT EXISTS", self.forward)
        self.assertNotRegex(self.forward, r"(?i)CREATE TABLE|ALTER TABLE|DROP ")

    def test_the_rollback_refuses_after_an_adjustment_and_only_takes_back_the_manager_grant(self):
        for needle in ("ROLLBACK_075_REFUSED_adjustments_exist", "reference_type = 'MANUAL_ADJUSTMENT'", "version > 75", "r.code = 'MANAGER'", "DELETE FROM schema_migrations WHERE version = 75"):
            self.assertIn(needle, self.rollback)
        self.assertLess(self.rollback.index("ROLLBACK_075_REFUSED"), self.rollback.index("DELETE rp"))


if __name__ == "__main__":
    unittest.main()
