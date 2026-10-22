"""Migration 071 (the old shop's sales history): static contract of the forward and rollback files. No database is used."""
import re
import unittest

from tests import migration_sql as sql
from tests import schema_model
from tools.schema_rehearsal.chain import load_chain

FORWARD = "071_sales_history_legacy.sql"
ROLLBACK = "071_sales_history_legacy_rollback.sql"


class SalesHistorySchemaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.model = schema_model.build()
        cls.forward = sql.read(FORWARD)
        cls.rollback = sql.read(ROLLBACK, sql.ROLLBACK_DIR)

    def test_the_chain_replays_and_071_requires_070(self):
        self.assertEqual(self.model.problems, [])
        self.assertIn("version=70", self.forward.replace(" ", ""))
        self.assertIn("MIGRATION_071_REFUSED_missing_070", self.forward)
        self.assertRegex(self.forward, r"INSERT INTO schema_migrations\(version,name\) VALUES \(71,")
        self.assertLessEqual(len(re.search(r"VALUES \(71,'([^']*)'", self.forward).group(1)), 150)
        self.assertEqual(len({f.name: f for f in load_chain(require_committed=False)}), 71)

    def test_the_history_is_a_daily_insert_only_record_with_honest_weights(self):
        for needle in ("CREATE TABLE IF NOT EXISTS sales_history_legacy", "PRIMARY KEY (sale_date)", "bills >= 1 AND bills_with_weight <= bills", "bills_with_weight > 0 OR grams = 0", "source = 'GOLDDB'"):
            self.assertIn(needle, self.forward)
        self.assertNotIn("TRIGGER", self.forward.upper())
        self.assertNotRegex(self.forward, r"(?i)\bgolddb\.")                                       # the old database is never referenced as a schema

    def test_the_rollback_refuses_when_the_history_holds_rows(self):
        for needle in ("ROLLBACK_071_REFUSED_sales_history_exists", "FROM sales_history_legacy", "version > 71", "DELETE FROM schema_migrations WHERE version = 71"):
            self.assertIn(needle, self.rollback)
        self.assertLess(self.rollback.index("ROLLBACK_071_REFUSED"), self.rollback.index("DROP TABLE"))


if __name__ == "__main__":
    unittest.main()
