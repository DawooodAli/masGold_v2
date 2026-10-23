"""Migration 074 (temporary cash advances): static contract of the forward and rollback files. No database is used."""
import re
import unittest

from tests import migration_sql as sql
from tests import schema_model
from tools.schema_rehearsal.chain import load_chain

FORWARD = "074_cash_advances.sql"
ROLLBACK = "074_cash_advances_rollback.sql"


class CashAdvancesSchemaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.model = schema_model.build()
        cls.forward = sql.read(FORWARD)
        cls.rollback = sql.read(ROLLBACK, sql.ROLLBACK_DIR)

    def test_the_chain_replays_and_074_requires_073(self):
        self.assertEqual(self.model.problems, [])
        self.assertIn("version=73", self.forward.replace(" ", ""))
        self.assertIn("MIGRATION_074_REFUSED_missing_073", self.forward)
        self.assertRegex(self.forward, r"INSERT INTO schema_migrations\(version,name\) VALUES \(74,")
        self.assertLessEqual(len(re.search(r"VALUES \(74,'([^']*)'", self.forward).group(1)), 150)
        self.assertEqual(len({f.name: f for f in load_chain(require_committed=False)}), 75)

    def test_an_advance_has_exactly_one_party_one_withdrawal_and_a_positive_amount(self):
        for needle in ("CREATE TABLE IF NOT EXISTS cash_advances", "UNIQUE KEY uq_cash_advance_operation (operation_id)", "FOREIGN KEY (operation_id) REFERENCES financial_operations(id)",
                       "FOREIGN KEY (employee_id) REFERENCES employees(id)", "FOREIGN KEY (customer_id) REFERENCES customers(id)", "amount > 0",
                       "kind = 'EMPLOYEE' AND employee_id IS NOT NULL AND customer_id IS NULL", "kind = 'CUSTOMER' AND customer_id IS NOT NULL AND employee_id IS NULL"):
            self.assertIn(needle, self.forward)
        self.assertNotIn("TRIGGER", self.forward.upper())

    def test_a_repayment_is_tied_to_its_advance_and_its_own_deposit(self):
        for needle in ("CREATE TABLE IF NOT EXISTS cash_advance_repayments", "FOREIGN KEY (advance_id) REFERENCES cash_advances(id)",
                       "UNIQUE KEY uq_cash_advance_repay_operation (operation_id)", "chk_cash_advance_repay_amount CHECK (amount > 0)"):
            self.assertIn(needle, self.forward)

    def test_the_two_system_categories_are_seeded_and_never_touch_the_customer_ledger(self):
        self.assertIn("('CUSTOMER_CUSTODY', 'عهدة مؤقتة لعميل', 'OUT', 1, 1", self.forward)
        self.assertIn("('ADVANCE_REPAY', 'سداد سلفة / عهدة', 'IN', 1, 1", self.forward)
        self.assertNotRegex(self.forward, r"(INSERT INTO|UPDATE|ALTER TABLE)\s+customer_transactions")

    def test_the_rollback_refuses_while_any_advance_exists(self):
        for needle in ("ROLLBACK_074_REFUSED_advances_exist", "FROM cash_advances", "version > 74", "DELETE FROM schema_migrations WHERE version = 74"):
            self.assertIn(needle, self.rollback)
        self.assertLess(self.rollback.index("ROLLBACK_074_REFUSED"), self.rollback.index("DROP TABLE"))
        self.assertLess(self.rollback.index("DROP TABLE IF EXISTS cash_advance_repayments"), self.rollback.index("DROP TABLE IF EXISTS cash_advances"))


if __name__ == "__main__":
    unittest.main()
