"""Static checks of migration 025 (Financial Accounts V1) and its guarded rollback; no database is used."""
from pathlib import Path
import re
import unittest

from tests.schema_model import build
from tests.test_schema_chain import fk_problems
from tools.schema_rehearsal.chain import load_chain

MIGRATION = Path("database/schema/025_financial_operations.sql")
ROLLBACK = Path("database/schema/rollback/025_financial_operations_rollback.sql")


class FinancialAccountsSchemaTests(unittest.TestCase):
    def test_chain_tables_and_foreign_keys(self):
        self.assertEqual(len(load_chain(require_committed=False)), 71)
        model = build()
        self.assertEqual(model.problems, [])
        self.assertEqual(fk_problems(model), [])
        for table in ("financial_operations", "financial_operation_legs"):
            self.assertIn(table, model.tables)

    def test_shape_idempotency_and_leg_constraints(self):
        sql = MIGRATION.read_text(encoding="utf-8")
        self.assertIn("UNIQUE KEY uq_fo_request (request_key)", sql)
        self.assertIn("UNIQUE KEY uq_fol_role (operation_id, leg_role)", sql)
        self.assertIn("UNIQUE KEY uq_fol_cash_movement (cash_movement_id)", sql)
        self.assertIn("UNIQUE KEY uq_fol_bank_movement (bank_movement_id)", sql)
        self.assertIn("CHECK (amount > 0)", sql)
        self.assertIn("CHAR_LENGTH(TRIM(reason))>0", sql)
        self.assertIn("AND NOT (COALESCE(from_cash_account_id,0)=COALESCE(to_cash_account_id,0)", sql)
        self.assertNotIn("balance DECIMAL", sql)                                   # no stored balance
        self.assertIsNone(re.search(r"^\s*UPDATE\s", sql, re.M))                   # no row rewrite

    def test_correction_source_is_full_reversal_only_and_keeps_existing_sources(self):
        sql = MIGRATION.read_text(encoding="utf-8")
        self.assertIn("source_type IN ('INVOICE','EXCHANGE','RETURN') AND invoice_id IS NOT NULL", sql)
        self.assertIn("source_type='CUSTOMER_ACCOUNT_SETTLEMENT' AND invoice_id IS NULL", sql)
        self.assertIn("source_type='FINANCIAL_OPERATION' AND invoice_id IS NULL AND correction_type='FULL_REVERSAL'", sql)

    def test_movement_types_and_permission_grants(self):
        sql = MIGRATION.read_text(encoding="utf-8")
        for code in ("MANUAL_DEPOSIT", "MANUAL_WITHDRAWAL", "ACCOUNT_TRANSFER_IN", "ACCOUNT_TRANSFER_OUT"):
            self.assertEqual(sql.count(f"('{code}',"), 2, code)                  # cash and bank ledgers
        self.assertIn("r.code IN ('OWNER','MANAGER','CASHIER')", sql)
        self.assertNotIn("SALESPERSON", sql)
        self.assertNotIn("TRANSACTION_CORRECT'", sql)                             # never over-granted

    def test_rollback_refuses_history_and_restores_024_check(self):
        sql = ROLLBACK.read_text(encoding="utf-8")
        guard = sql[:sql.index("ALTER TABLE")]
        for name in ("financial_operations", "financial_operation_legs", "transaction_corrections",
                     "cash_movements", "bank_movements", "version>25"):
            self.assertIn(name, guard)
        restored = sql[sql.index("ADD CONSTRAINT chk_correction_source"):]
        self.assertIn("source_type='CUSTOMER_ACCOUNT_SETTLEMENT' AND invoice_id IS NULL", restored)
        self.assertNotIn("FINANCIAL_OPERATION", restored.split(";")[0])


class CashCategoriesSchemaTests(unittest.TestCase):
    MIGRATION = Path("database/schema/026_cash_movement_categories.sql")
    ROLLBACK = Path("database/schema/rollback/026_cash_movement_categories_rollback.sql")

    def test_category_column_and_constraints(self):
        model = build()
        self.assertIn("category", model.tables["financial_operations"]["columns"])
        sql = self.MIGRATION.read_text(encoding="utf-8")
        self.assertIn("(category IS NOT NULL OR (reason IS NOT NULL AND CHAR_LENGTH(TRIM(reason))>0))", sql)
        self.assertIn("operation_type='MANUAL_DEPOSIT' AND category IN ('REVENUE','REPAIR')", sql)
        self.assertIn("operation_type='MANUAL_WITHDRAWAL' AND category IN ('EXPENSE','SALARY','RENT','REPAIR')", sql)
        self.assertIsNone(re.search(r"^\s*(UPDATE|DELETE)\s", sql, re.M))           # no history rewritten
        self.assertNotIn("customer", sql.lower())                                    # no customer money path

    def test_rollback_refuses_categorised_history(self):
        sql = self.ROLLBACK.read_text(encoding="utf-8")
        guard = sql[:sql.index("ALTER TABLE")]
        self.assertIn("category IS NOT NULL", guard)
        self.assertIn("version>26", guard)


if __name__ == "__main__":
    unittest.main()
