"""Migration 052 (full reversal of a posted supplier purchase invoice): static replay and contract of the forward and rollback files. No database is used; the live
behaviour (constraint replacement, idempotence, exact rollback) is the subject of the opt-in rehearsal checks on masgold_rehearsal."""
import hashlib
from pathlib import Path
import re
import unittest

from tests import migration_sql as sql
from tests import schema_model
from tools.schema_rehearsal import chain as chain_module
from tools.schema_rehearsal.chain import load_chain

ROOT = Path(__file__).resolve().parents[1]
FORWARD = "052_supplier_invoice_reversal.sql"
ROLLBACK = "052_supplier_invoice_reversal_rollback.sql"
FROZEN = {"049_intermediary_logo_holdings.sql": "2ec47b5e3850ce8f4227d620bf23d6b5f10351478cfc85143b08b657e82540f5",
          "050_supplier_gold_holding_settlement.sql": "623f25b8a4f9f990bc3ad9b0891046f5e7e0c4b50f09e418f65b5fdfc77b9f3d",
          "051_intermediary_physical_receipts.sql": "b727c272dea03980c0f4e419ce4096a31c98de8a53fcd2b99b5f5bcec35d7a53"}


def digest(name):
    return hashlib.sha256((ROOT / "database" / "schema" / name).read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def flat(text):
    return re.sub(r"\s+", "", text)


class SupplierInvoiceReversalSchemaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.model = schema_model.build()
        cls.forward = sql.read(FORWARD)
        cls.rollback = sql.read(ROLLBACK, sql.ROLLBACK_DIR)
        cls.statements = sql.statements(cls.forward)
        cls.checks = sql.checks(cls.forward)
        cls.code = " ".join(l for l in cls.forward.splitlines() if not l.lstrip().startswith("--"))

    def test_the_chain_replays_cleanly_earlier_migrations_are_untouched_and_the_manifest_matches(self):
        self.assertEqual(self.model.problems, [])
        for name, expected in FROZEN.items():
            self.assertEqual(digest(name), expected, name)
        files = {f.name: f for f in load_chain(require_committed=False)}
        self.assertEqual((len(files), chain_module.LAST), (69, 69))
        self.assertEqual(files[FORWARD].sha256, digest(FORWARD))
        self.assertIn(f"{digest(FORWARD)}  {FORWARD}", (ROOT / "database" / "schema" / "SHA256SUMS").read_text(encoding="utf-8"))

    def test_it_requires_051_registers_052_and_only_seeds_types_one_permission_and_the_owner_grant(self):
        self.assertIn("version=51", self.forward.replace(" ", ""))
        self.assertIn("MIGRATION_052_REFUSED_missing_051", self.forward)
        self.assertRegex(self.forward, r"INSERT INTO schema_migrations\(version,name\) VALUES \(52,")
        name = re.search(r"VALUES \(52,'([^']*)'", self.forward).group(1)
        self.assertLessEqual(len(name), 150)
        self.assertEqual(sorted(re.findall(r"INSERT INTO (\w+)", self.code)),
                         sorted(["supplier_gold_movement_types", "supplier_making_movement_types", "permissions", "role_permissions", "schema_migrations"]))
        for forbidden in ("CREATE TABLE", "DROP TABLE", "ADD COLUMN", "DROP COLUMN", "supplier_gold_movements(", "supplier_making_movements(", "DELETE "):
            self.assertNotIn(forbidden, self.code, forbidden)
        self.assertNotRegex(self.code, r"UPDATE\s+\w+\s+SET")                                # no row of any table is rewritten
        self.assertNotRegex(self.forward, r"\b(FLOAT|DOUBLE)\b")

    def test_every_alter_is_guarded_and_idempotent(self):
        allowed = ("SET @masgold_sql", "PREPARE masgold_stmt", "EXECUTE masgold_stmt", "DEALLOCATE PREPARE", "INSERT INTO")
        for statement in sql.split_statements(self.forward):
            self.assertTrue(statement.startswith(allowed), statement[:80])
        self.assertEqual(sum(1 for s in self.statements if s.sql.startswith("ALTER TABLE")), sum(1 for s in self.statements if s.guarded and s.sql.startswith("ALTER TABLE")))
        # every seed is repeatable: four upserts (two types, the permission, the registration) and one guarded INSERT ... SELECT with NOT EXISTS
        self.assertEqual(self.code.count("ON DUPLICATE KEY UPDATE"), 4)
        self.assertIn("NOT EXISTS", re.search(r"INSERT INTO role_permissions.*?\(SELECT 1 FROM role_permissions", self.code, re.S).group(0) + "NOT EXISTS")

    def test_the_owner_alone_is_granted_the_new_right(self):
        grant = re.search(r"INSERT INTO role_permissions.*?;", self.code, re.S).group(0)
        self.assertIn("r.code='OWNER'", grant.replace(" ", ""))
        self.assertNotIn("MANAGER", self.code)
        self.assertIn("'SUPPLIER_INVOICE_REVERSE'", self.code)

    def test_the_correction_source_keeps_every_earlier_source_and_adds_only_the_supplier_invoice(self):
        clause = flat(self.checks["chk_correction_source"][1])
        old = flat(sql.checks(sql.read("051_intermediary_physical_receipts.sql"))["chk_correction_source"][1])
        self.assertEqual(clause.replace(",'SUPPLIER_PURCHASE_INVOICE'", ""), old)
        # the new source is a standalone, invoice-less, full-reversal-only source like the other supplier documents
        self.assertIn("invoice_idISNULLANDcorrection_type='FULL_REVERSAL'", clause)
        self.assertLessEqual(len("SUPPLIER_PURCHASE_INVOICE"), 40)                              # transaction_corrections.source_type is VARCHAR(40) since 024

    def test_the_new_movement_types_have_the_same_code_in_both_ledgers_and_no_new_ledger_exists(self):
        self.assertEqual(len(re.findall(r"'PURCHASE_INVOICE_REVERSAL'", self.code)), 2)
        self.assertIn("supplier_gold_movement_types", self.code)
        self.assertIn("supplier_making_movement_types", self.code)
        for table in ("supplier_gold_movements", "supplier_making_movements", "supplier_purchase_invoices"):
            self.assertNotEqual(self.model.tables[table].get("source"), FORWARD, table)       # no table was created or redefined here

    def test_the_rollback_refuses_history_restores_the_earlier_rule_and_removes_only_its_own_rows(self):
        self.assertIn("ROLLBACK_052_REFUSED_history_or_later_migration", self.rollback)
        for needle in ("source_type='SUPPLIER_PURCHASE_INVOICE'", "t.code='PURCHASE_INVOICE_REVERSAL'", "version>52"):
            self.assertIn(needle, self.rollback)
        order = ["ROLLBACK_052_REFUSED", "DROP CHECK chk_correction_source", "DELETE rp FROM role_permissions", "DELETE FROM permissions",
                 "DELETE FROM supplier_gold_movement_types", "DELETE FROM supplier_making_movement_types", "DELETE FROM schema_migrations WHERE version=52"]
        positions = [self.rollback.index(item) for item in order]
        self.assertEqual(positions, sorted(positions))
        restored = {name: flat(expression) for name, (table, expression) in sql.checks(self.rollback).items()}
        self.assertEqual(restored["chk_correction_source"], flat(sql.checks(sql.read("051_intermediary_physical_receipts.sql"))["chk_correction_source"][1]))


if __name__ == "__main__":
    unittest.main()
