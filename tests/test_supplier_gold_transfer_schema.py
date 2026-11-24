"""Migration 108 (supplier gold transfers): static contract of the forward and rollback files. No database is used."""
import re
import unittest

from tests import migration_sql as sql
from tests import schema_model
from tools.schema_rehearsal.chain import load_chain

FORWARD = "108_supplier_gold_transfers.sql"
ROLLBACK = "108_supplier_gold_transfers_rollback.sql"
TYPES = ("ACCOUNT_TRANSFER_OUT", "ACCOUNT_TRANSFER_IN", "ACCOUNT_TRANSFER_OUT_REVERSAL", "ACCOUNT_TRANSFER_IN_REVERSAL")


class SupplierGoldTransferSchemaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.model = schema_model.build()
        cls.forward = sql.read(FORWARD)
        cls.rollback = sql.read(ROLLBACK, sql.ROLLBACK_DIR)

    def test_the_chain_replays_and_108_requires_107(self):
        self.assertEqual(self.model.problems, [])
        self.assertIn("version=107", self.forward.replace(" ", ""))
        self.assertIn("MIGRATION_108_REFUSED_missing_107", self.forward)
        self.assertRegex(self.forward, r"INSERT INTO schema_migrations\(version,name\) VALUES \(108,")
        self.assertLessEqual(len(re.search(r"VALUES \(108,'([^']*)'", self.forward).group(1)), 150)
        self.assertEqual(len({f.name: f for f in load_chain(require_committed=False)}), 109)

    def test_the_document_is_insert_only_with_one_reversal_per_original_and_a_mandatory_reason(self):
        for needle in ("CREATE TABLE IF NOT EXISTS supplier_gold_transfers", "UNIQUE KEY uq_sgt_request (request_key)", "UNIQUE KEY uq_sgt_reverses (reverses_id)",
                       "CONSTRAINT fk_sgt_reverses FOREIGN KEY (reverses_id) REFERENCES supplier_gold_transfers(id)", "from_supplier_id <> to_supplier_id",
                       "from_weight_grams > 0 AND to_weight_grams > 0 AND to_analytical_grams > 0", "rounding_difference_grams = to_weight_grams - to_analytical_grams",
                       "reverses_id IS NOT NULL AND reason IS NOT NULL AND CHAR_LENGTH(TRIM(reason)) > 0"):
            self.assertIn(needle, self.forward)
        self.assertNotIn("TRIGGER", self.forward.upper())
        self.assertNotRegex(self.forward.replace("ON DUPLICATE KEY UPDATE", ""), r"(?i)\b(UPDATE|DELETE)\b\s")      # no existing row is rewritten (only the type seed upserts)

    def test_the_four_ledger_movement_types_are_seeded_idempotently(self):
        for code in TYPES:
            self.assertIn(f"('{code}'", self.forward)
        self.assertIn("ON DUPLICATE KEY UPDATE", self.forward)

    def test_the_rollback_refuses_when_a_transfer_or_its_movements_exist(self):
        for needle in ("ROLLBACK_108_REFUSED_transfers_exist", "ROLLBACK_108_REFUSED_transfer_movements_exist", "version > 108", "DELETE FROM schema_migrations WHERE version = 108"):
            self.assertIn(needle, self.rollback)
        for code in TYPES:
            self.assertIn(f"'{code}'", self.rollback)
        self.assertLess(self.rollback.index("ROLLBACK_108_REFUSED_transfer_movements_exist"), self.rollback.index("DROP TABLE"))


if __name__ == "__main__":
    unittest.main()
