"""Migration 110 (customer ledger type INVOICE_CUSTOMER_CHANGE for changing the customer of a finalized invoice): the static contract of the forward file, its rollback and the chain. No database."""
import re
import unittest

from tests import migration_sql as sql
from tests import schema_model
from tools.schema_rehearsal.chain import load_chain


class MigrationContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.model = schema_model.build()
        cls.forward = sql.read("110_invoice_customer_change.sql")
        cls.rollback = sql.read("110_invoice_customer_change_rollback.sql", sql.ROLLBACK_DIR)

    def test_the_chain_replays_and_110_requires_109(self):
        self.assertEqual(self.model.problems, [])
        self.assertIn("MIGRATION_110_REFUSED_missing_109", self.forward)
        self.assertRegex(self.forward, r"INSERT INTO schema_migrations\(version,name\) VALUES \(110,")
        self.assertLessEqual(len(re.search(r"VALUES \(110,'([^']*)'", self.forward).group(1)), 150)
        self.assertEqual(len({f.name: f for f in load_chain(require_committed=False)}), 110)

    def test_only_the_ledger_type_is_added_and_nothing_is_rewritten(self):
        self.assertIn("'INVOICE_CUSTOMER_CHANGE'", self.forward)
        self.assertIn("'EITHER'", self.forward)                                                  # the sign follows the moved amount
        stripped = self.forward.replace("ON DUPLICATE KEY UPDATE", "")
        self.assertNotRegex(stripped, r"(?i)\b(UPDATE|DELETE|ALTER|DROP)\b\s")

    def test_the_rollback_refuses_with_a_later_migration_or_used_rows_then_removes_only_the_type(self):
        for needle in ("ROLLBACK_110_REFUSED_later_migration_exists", "version > 110", "ROLLBACK_110_REFUSED_customer_changes_exist",
                       "DELETE FROM customer_transaction_types WHERE code = 'INVOICE_CUSTOMER_CHANGE'", "DELETE FROM schema_migrations WHERE version = 110"):
            self.assertIn(needle, self.rollback)
        self.assertLess(self.rollback.index("ROLLBACK_110_REFUSED_customer_changes_exist"), self.rollback.index("DELETE FROM customer_transaction_types"))


if __name__ == "__main__":
    unittest.main()
