from pathlib import Path
import unittest
from tests.schema_model import build
from tools.schema_rehearsal.chain import load_chain


class AccountSchemaTests(unittest.TestCase):
    def test_chain_and_cs_foreign_keys(self):
        self.assertEqual(len(load_chain(require_committed=False)),51)
        model=build()
        self.assertEqual(model.problems,[])
        from tests.test_schema_chain import fk_problems
        self.assertEqual(fk_problems(model),[])
        self.assertIn('customer_account_settlements',model.tables)
        self.assertIn('customer_account_settlement_payments',model.tables)

    def test_rollback_refuses_financial_history(self):
        sql=Path('database/schema/rollback/024_customer_account_rollback.sql').read_text()
        guard=sql[:sql.index('ALTER TABLE')]
        for name in ('customer_account_settlements','transaction_corrections','customer_transactions','cash_movements','bank_movements'):
            self.assertIn(name,guard)
        self.assertIn('version>24',guard)

    def test_source_shape_and_permissions(self):
        sql=Path('database/schema/024_customer_account.sql').read_text()
        self.assertIn("source_type='CUSTOMER_ACCOUNT_SETTLEMENT' AND invoice_id IS NULL",sql)
        self.assertIn("source_type IN ('INVOICE','EXCHANGE','RETURN') AND invoice_id IS NOT NULL",sql)
        self.assertIn("r.code IN ('OWNER','MANAGER','CASHIER')",sql)
        self.assertNotIn('SALESPERSON',sql)
        self.assertIn('UNIQUE KEY uq_cs_request',sql)
        self.assertIn('amount <= ABS(balance_before)',sql)
