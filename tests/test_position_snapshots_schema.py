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
        self.assertEqual(len({f.name: f for f in load_chain(require_committed=False)}), 109)

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

class PositionSnapshotDebtsSchemaTests(unittest.TestCase):
    """Migration 107: three nullable debt columns on the sheets (NULL = taken before it), guarded and re-run safe, rollback refuses once any sheet records debts."""
    @classmethod
    def setUpClass(cls):
        cls.forward = sql.read("107_position_snapshot_debts.sql")
        cls.rollback = sql.read("107_position_snapshot_debts_rollback.sql", sql.ROLLBACK_DIR)

    def test_the_three_columns_are_nullable_and_checked_together(self):
        self.assertIn("MIGRATION_107_REFUSED_missing_106", self.forward)
        for column in ("owed_customers_egp", "owed_suppliers_egp", "owed_marketer_egp"):
            self.assertRegex(self.forward, rf"ADD COLUMN {column} DECIMAL\(16,2\) DEFAULT NULL")
        self.assertIn("chk_pos_snapshot_debts", self.forward)
        self.assertIn("owed_customers_egp >= 0 AND owed_suppliers_egp >= 0 AND owed_marketer_egp >= 0", self.forward)
        self.assertRegex(self.forward, r"INSERT INTO schema_migrations\(version,name\) VALUES \(107,")
        self.assertNotRegex(self.forward, r"(?i)(UPDATE|DELETE)\s")                       # no existing sheet is rewritten

    def test_the_rollback_refuses_when_debts_are_recorded(self):
        for needle in ("ROLLBACK_107_REFUSED_snapshot_debts_exist", "owed_customers_egp IS NOT NULL", "version > 107", "DELETE FROM schema_migrations WHERE version = 107"):
            self.assertIn(needle, self.rollback)
        self.assertLess(self.rollback.index("ROLLBACK_107_REFUSED"), self.rollback.index("DROP COLUMN"))


if __name__ == "__main__":
    unittest.main()
