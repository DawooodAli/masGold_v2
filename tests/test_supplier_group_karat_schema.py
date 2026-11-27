"""Migration 112 (a supplier invoice group carries its own karat and the grams credited in the supplier's account karat): the static contract of the forward file, its rollback and the chain.
No database server and no data."""
import re
import unittest

from tests import migration_sql as sql
from tests import schema_model
from tools.schema_rehearsal.chain import load_chain

FORWARD_NAME = "112_supplier_invoice_group_karat.sql"


class MigrationContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.model = schema_model.build()
        cls.forward = sql.read(FORWARD_NAME)
        cls.rollback = sql.read("112_supplier_invoice_group_karat_rollback.sql", sql.ROLLBACK_DIR)

    def test_the_chain_replays_and_112_requires_111(self):
        self.assertEqual(self.model.problems, [])
        self.assertIn("MIGRATION_112_REFUSED_missing_111", self.forward)
        self.assertRegex(self.forward, r"INSERT INTO schema_migrations\(version,name\) VALUES \(112,")
        self.assertLessEqual(len(re.search(r"VALUES \(112,'([^']*)'", self.forward).group(1)), 150)
        self.assertEqual(len({f.name: f for f in load_chain(require_committed=False)}), 112)

    def test_the_groups_table_gets_the_two_columns_a_foreign_key_and_a_check(self):
        columns = self.model.tables["supplier_invoice_groups"]["columns"]
        self.assertEqual((columns["karat_id"]["type"], columns["karat_id"]["nullable"]), ("tinyint unsigned", False))
        self.assertEqual((columns["credited_weight_grams"]["type"], columns["credited_weight_grams"]["nullable"]), ("decimal(14,3)", False))
        self.assertEqual(self.model.tables["supplier_invoice_groups"]["fks"]["fk_sig_karat"], {"cols": ["karat_id"], "ref": "karats", "refcols": ["id"]})
        self.assertIn("chk_sig_credited", self.model.tables["supplier_invoice_groups"]["checks"])
        self.assertEqual(columns["supplier_weight_grams"]["type"], "decimal(12,3)")                  # the ACTUAL grams stay as they were

    def test_existing_groups_are_backfilled_with_their_invoice_karat_and_their_own_grams_and_every_step_is_re_run_safe(self):
        self.assertRegex(self.forward, r"UPDATE supplier_invoice_groups g JOIN supplier_purchase_invoices i ON i\.id = g\.invoice_id\s+"
                                       r"SET g\.karat_id = i\.karat_id, g\.credited_weight_grams = g\.supplier_weight_grams\s+"
                                       r"WHERE g\.karat_id IS NULL OR g\.credited_weight_grams IS NULL")
        for guard in ("NOT EXISTS(SELECT 1 FROM information_schema.COLUMNS", "IS_NULLABLE='YES'", "CONSTRAINT_NAME='fk_sig_karat'", "CONSTRAINT_NAME='chk_sig_credited'"):
            self.assertIn(guard, self.forward)
        stripped = sql.strip_comments(self.forward)
        self.assertEqual(len(re.findall(r"(?i)\bUPDATE\b\s+supplier", stripped)), 1)                 # the one backfill, nothing else rewrites data
        self.assertNotRegex(stripped, r"(?i)\b(DELETE|TRUNCATE)\b\s")
        self.assertNotIn("supplier_gold_movements", stripped)                                        # no posted ledger row is touched
        self.assertLess(stripped.index("UPDATE supplier_invoice_groups"), stripped.index("MODIFY COLUMN karat_id"))   # NOT NULL only after the backfill

    def test_the_rollback_refuses_with_a_later_migration_or_groups_that_differ_then_drops_what_112_added(self):
        for needle in ("ROLLBACK_112_REFUSED_later_migration_exists", "version > 112", "ROLLBACK_112_REFUSED_group_karats_in_use", "g.karat_id <> i.karat_id",
                       "g.credited_weight_grams <> g.supplier_weight_grams", "DROP CHECK chk_sig_credited", "DROP FOREIGN KEY fk_sig_karat",
                       "DROP COLUMN credited_weight_grams", "DROP COLUMN karat_id", "DELETE FROM schema_migrations WHERE version = 112"):
            self.assertIn(needle, self.rollback)
        self.assertLess(self.rollback.index("ROLLBACK_112_REFUSED_group_karats_in_use"), self.rollback.index("DROP COLUMN karat_id"))
        self.assertLess(self.rollback.index("DROP FOREIGN KEY fk_sig_karat"), self.rollback.index("DROP COLUMN karat_id"))


if __name__ == "__main__":
    unittest.main()
