"""Migration 076 (borrowed cash): static contract of the forward and rollback files. No database is used."""
import re
import unittest

from tests import migration_sql as sql
from tests import schema_model
from tools.schema_rehearsal.chain import load_chain

FORWARD = "076_borrowed_cash.sql"
ROLLBACK = "076_borrowed_cash_rollback.sql"


class BorrowedCashSchemaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.model = schema_model.build()
        cls.forward = sql.read(FORWARD)
        cls.rollback = sql.read(ROLLBACK, sql.ROLLBACK_DIR)

    def test_the_chain_replays_and_076_requires_075(self):
        self.assertEqual(self.model.problems, [])
        self.assertIn("version=75", self.forward.replace(" ", ""))
        self.assertIn("MIGRATION_076_REFUSED_missing_075", self.forward)
        self.assertRegex(self.forward, r"INSERT INTO schema_migrations\(version,name\) VALUES \(76,")
        self.assertLessEqual(len(re.search(r"VALUES \(76,'([^']*)'", self.forward).group(1)), 150)
        self.assertEqual(len({f.name: f for f in load_chain(require_committed=False)}), 80)

    def test_existing_advances_stay_lend_and_the_shapes_are_checked(self):
        for needle in ("direction VARCHAR(6) NOT NULL DEFAULT 'LEND'", "lender_name VARCHAR(100) DEFAULT NULL", "kind = 'THIRD_PARTY' AND employee_id IS NULL AND customer_id IS NULL AND lender_name IS NOT NULL",
                       "direction = 'LEND' AND kind IN ('EMPLOYEE', 'CUSTOMER')", "('BORROWED_CASH', 'سلفة مستلفة', 'IN', 1, 1", "('BORROW_REPAY', 'سداد سلفة مستلفة', 'OUT', 1, 1"):
            self.assertIn(needle, self.forward)
        self.assertNotRegex(self.forward, r"(?i)DROP TABLE|TRIGGER")

    def test_the_rollback_refuses_while_a_borrowed_advance_exists(self):
        for needle in ("ROLLBACK_076_REFUSED_borrowed_advances_exist", "direction = 'BORROW'", "version > 76", "DELETE FROM schema_migrations WHERE version = 76"):
            self.assertIn(needle, self.rollback)
        self.assertLess(self.rollback.index("ROLLBACK_076_REFUSED"), self.rollback.index("ALTER TABLE"))


if __name__ == "__main__":
    unittest.main()
