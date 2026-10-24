"""Migration 068 (more expense categories): static contract of the forward and rollback files. No database is used."""
import re
import unittest

from tests import migration_sql as sql
from tests import schema_model
from tools.schema_rehearsal.chain import load_chain

FORWARD = "068_expense_categories.sql"
ROLLBACK = "068_expense_categories_rollback.sql"
NEW = ("UTILITIES", "TRANSPORT", "MAINTENANCE", "HOSPITALITY", "STAFF_ADVANCE")


class ExpenseCategoriesSchemaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.model = schema_model.build()
        cls.forward = sql.read(FORWARD)
        cls.rollback = sql.read(ROLLBACK, sql.ROLLBACK_DIR)

    def test_the_chain_replays_and_068_requires_067(self):
        self.assertEqual(self.model.problems, [])
        self.assertIn("version=67", self.forward.replace(" ", ""))
        self.assertIn("MIGRATION_068_REFUSED_missing_067", self.forward)
        self.assertRegex(self.forward, r"INSERT INTO schema_migrations\(version,name\) VALUES \(68,")
        self.assertLessEqual(len(re.search(r"VALUES \(68,'([^']*)'", self.forward).group(1)), 150)
        self.assertEqual(len({f.name: f for f in load_chain(require_committed=False)}), 80)

    def test_only_the_outgoing_categories_are_widened_and_the_old_ones_stay(self):
        add = self.forward[self.forward.index("ADD CONSTRAINT chk_fo_category"):]
        for category in ("EXPENSE", "SALARY", "RENT", "REPAIR", "REVENUE", *NEW):
            self.assertIn(f"'{category}'", add)
        self.assertRegex(add, r"operation_type='MANUAL_DEPOSIT' AND category IN \('REVENUE','REPAIR'\)")                # no new income category
        self.assertNotIn("TRIGGER", self.forward.upper())
        self.assertNotRegex(self.forward, r"(?i)\b(update|delete)\b\s+(from\s+)?financial_operations")                    # no row is rewritten

    def test_the_rollback_refuses_while_a_new_category_is_in_use(self):
        for needle in ("ROLLBACK_068_REFUSED_new_categories_in_use", "FROM financial_operations WHERE category IN", "version > 68", "DELETE FROM schema_migrations WHERE version = 68"):
            self.assertIn(needle, self.rollback)
        for category in NEW:
            self.assertIn(f"'{category}'", self.rollback)
        self.assertLess(self.rollback.index("ROLLBACK_068_REFUSED"), self.rollback.index("DROP CHECK"))


if __name__ == "__main__":
    unittest.main()
