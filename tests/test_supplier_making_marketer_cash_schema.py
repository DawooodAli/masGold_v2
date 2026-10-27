"""Migration 078's marketer-cash supplier payment schema contract; no database is used."""
import re
import unittest
from pathlib import Path

from tests import migration_sql as sql
from tests import schema_model
from tools.schema_rehearsal.chain import load_chain

ROOT = Path(__file__).resolve().parents[1]
FORWARD = "078_supplier_making_marketer_cash.sql"
ROLLBACK = "078_supplier_making_marketer_cash_rollback.sql"


def table(text, name):
    match = re.search(rf"CREATE TABLE IF NOT EXISTS {name} \((.*?)\) ENGINE=InnoDB", text, re.S)
    if not match:
        raise AssertionError(f"missing table {name}")
    return match.group(1)


class SupplierMakingMarketerCashSchemaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.forward = sql.read(FORWARD)
        cls.rollback = sql.read(ROLLBACK, sql.ROLLBACK_DIR)
        cls.model = schema_model.build()

    def test_chain_is_contiguous_and_has_no_static_foreign_key_defect(self):
        self.assertEqual(self.model.problems, [])
        self.assertEqual(len(load_chain(require_committed=False)), 81)

    def test_payment_link_ties_one_payment_to_one_supplier_pay_document_and_marketer(self):
        link = table(self.forward, "supplier_making_marketer_payments")
        normalized = " ".join(link.split())
        self.assertIn("PRIMARY KEY (payment_id)", link)
        self.assertIn("UNIQUE KEY uq_smmp_document (marketer_document_id)", link)
        self.assertIn("FOREIGN KEY (payment_id, marketer_party_id, amount) REFERENCES supplier_making_payments(id, marketer_party_id, amount)",
                      normalized)
        self.assertIn("FOREIGN KEY (marketer_document_id, doc_kind, marketer_party_id, amount) "
                      "REFERENCES marketer_documents(id, doc_kind, party_id, cash_amount)", normalized)
        self.assertIn("CHECK (doc_kind = 'SUPPLIER_PAY' AND amount > 0)", link)
        self.assertIn("ADD UNIQUE KEY uq_smp_marketer_link (id, marketer_party_id, amount)", self.forward)

    def test_payment_method_requires_exactly_one_marketer_source_and_no_main_safe(self):
        self.assertIn("payment_method = 'MARKETER' AND cash_account_id IS NULL AND bank_account_id IS NULL AND marketer_party_id IS NOT NULL",
                      self.forward)
        self.assertIn("payment_method = 'CUSTODY' AND cash_account_id IS NULL AND bank_account_id IS NULL AND marketer_party_id IS NULL",
                      self.forward)

    def test_marketer_cash_document_and_ledger_are_insert_only_shapes(self):
        self.assertIn("doc_kind = 'SUPPLIER_PAY' AND cash_amount IS NOT NULL AND cash_amount > 0", self.forward)
        self.assertIn("movement_type = 'SUPPLIER_PAY' AND doc_kind = 'SUPPLIER_PAY' AND amount_delta = -cash_amount AND correction_id IS NULL",
                      self.forward)
        self.assertIn("movement_type = 'SUPPLIER_PAY_REVERSAL' AND doc_kind = 'SUPPLIER_PAY' AND amount_delta = cash_amount AND correction_id IS NOT NULL",
                      self.forward)

    def test_rollback_refuses_when_new_documents_exist(self):
        self.assertIn("supplier_making_payments WHERE payment_method = 'MARKETER'", self.rollback)
        self.assertIn("marketer_documents WHERE doc_kind = 'SUPPLIER_PAY'", self.rollback)
        self.assertIn("schema_migrations WHERE version > 78", self.rollback)
