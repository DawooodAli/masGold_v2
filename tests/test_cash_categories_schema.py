"""Migration 069 (manageable income / expense categories): static contract of the forward and rollback files. No database is used."""
import re
import unittest

from services.cash_category_service import SEED
from tests import migration_sql as sql
from tests import schema_model
from tools.schema_rehearsal.chain import load_chain

FORWARD = "069_cash_categories.sql"
ROLLBACK = "069_cash_categories_rollback.sql"


class CashCategoriesSchemaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.model = schema_model.build()
        cls.forward = sql.read(FORWARD)
        cls.rollback = sql.read(ROLLBACK, sql.ROLLBACK_DIR)

    def test_the_chain_replays_and_069_requires_068(self):
        self.assertEqual(self.model.problems, [])
        self.assertIn("version=68", self.forward.replace(" ", ""))
        self.assertIn("MIGRATION_069_REFUSED_missing_068", self.forward)
        self.assertRegex(self.forward, r"INSERT INTO schema_migrations\(version,name\) VALUES \(69,")
        self.assertLessEqual(len(re.search(r"VALUES \(69,'([^']*)'", self.forward).group(1)), 150)
        self.assertEqual(len({f.name: f for f in load_chain(require_committed=False)}), 75)

    def test_the_table_seeds_exactly_the_code_categories_and_binds_operations_by_foreign_key(self):
        for code, name, direction in SEED:
            self.assertIn(f"('{code}', '{name}', '{direction}', 1, 1,", self.forward)               # same code, Arabic name and direction as the fallback in code
        for needle in ("CREATE TABLE IF NOT EXISTS cash_categories", "UNIQUE KEY uq_cash_category_code (code)", "UNIQUE KEY uq_cash_category_name (name)", "direction IN ('IN', 'OUT', 'BOTH')",
                       "FOREIGN KEY (category) REFERENCES cash_categories(code) ON UPDATE RESTRICT ON DELETE RESTRICT", "category IS NULL OR operation_type IN ('MANUAL_DEPOSIT', 'MANUAL_WITHDRAWAL')"):
            self.assertIn(needle, self.forward)
        self.assertLess(self.forward.index("INSERT INTO cash_categories"), self.forward.index("ADD CONSTRAINT fk_fo_category"))        # seeded before the foreign key exists
        self.assertNotIn("TRIGGER", self.forward.upper())
        self.assertNotRegex(self.forward, r"(?i)\b(update|delete)\b\s+(from\s+)?financial_operations")

    def test_the_rollback_refuses_while_the_owner_has_his_own_categories(self):
        for needle in ("ROLLBACK_069_REFUSED_custom_categories_exist", "WHERE is_system = 0", "version > 69", "DROP FOREIGN KEY fk_fo_category", "DELETE FROM schema_migrations WHERE version = 69"):
            self.assertIn(needle, self.rollback)
        self.assertLess(self.rollback.index("ROLLBACK_069_REFUSED"), self.rollback.index("DROP FOREIGN KEY"))
        self.assertLess(self.rollback.index("ADD CONSTRAINT chk_fo_category"), self.rollback.index("DROP TABLE"))


if __name__ == "__main__":
    unittest.main()
