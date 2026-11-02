"""Migration 095 (a sale below the gold value stores a negative total making): static contract of the forward and rollback files. No database is used."""
import re
import unittest

from tests import migration_sql as sql
from tests import schema_model
from tools.schema_rehearsal.chain import load_chain

FORWARD = "095_sale_below_gold_making.sql"
ROLLBACK = "095_sale_below_gold_making_rollback.sql"


class SaleBelowGoldSchemaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.model = schema_model.build()
        cls.forward = sql.read(FORWARD)
        cls.rollback = sql.read(ROLLBACK, sql.ROLLBACK_DIR)

    def test_the_chain_replays_and_095_requires_094(self):
        self.assertEqual(self.model.problems, [])
        self.assertEqual(len({f.name: f for f in load_chain(require_committed=False)}), 95)
        self.assertIn("MIGRATION_095_REFUSED_missing_094", self.forward)
        self.assertRegex(self.forward, r"INSERT INTO schema_migrations\(version,name\) VALUES \(95,")
        self.assertLessEqual(len(re.search(r"VALUES \(95,'([^']*)'", self.forward).group(1)), 150)

    def test_only_the_total_making_sign_check_is_dropped_and_it_is_guarded(self):
        self.assertEqual(self.forward.count("DROP CHECK"), 1)
        self.assertIn("DROP CHECK chk_invoice_line_making_total", self.forward)
        self.assertNotIn("chk_invoice_line_making ", self.forward.replace("chk_invoice_line_making_total", ""))   # the per-gram check stays
        self.assertIn("information_schema.TABLE_CONSTRAINTS", self.forward)

    def test_the_rollback_refuses_while_a_negative_making_exists(self):
        self.assertIn("ROLLBACK_095_REFUSED", self.rollback)
        self.assertIn("making_total < 0", self.rollback)
        self.assertIn("ADD CONSTRAINT chk_invoice_line_making_total CHECK (making_total IS NULL OR making_total >= 0)", self.rollback)
        self.assertIn("DELETE FROM schema_migrations WHERE version = 95", self.rollback)


if __name__ == "__main__":
    unittest.main()
