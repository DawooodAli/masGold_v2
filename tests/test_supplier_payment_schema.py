"""Migration 036 (supplier making payments): static replay and contract of the forward and rollback files.
No database is used; the live behaviour was proven by the rehearsal on masgold_rehearsal (see the task report)."""
import hashlib
from pathlib import Path
import re
import unittest

from tests import migration_sql as sql
from tests import schema_model
from tools.schema_rehearsal.chain import load_chain

ROOT = Path(__file__).resolve().parents[1]
FORWARD = "036_supplier_making_payments.sql"
ROLLBACK = "036_supplier_making_payments_rollback.sql"
TABLES = ["supplier_making_payments", "supplier_making_payment_legs"]
FROZEN = {"034_suppliers_purchasing.sql": "3f35b7d149cfd51f40fe82377532e43fc93e4d6af7e9dd6551c2efcf51db0de2",
          "035_supplier_karat.sql": "30220bd68e41f599b31ebd325f75add294f90fc8bd5f636072f6b99feb688512"}


def digest(name):
    raw = (ROOT / "database" / "schema" / name).read_bytes()
    return hashlib.sha256(raw.replace(b"\r\n", b"\n")).hexdigest()


class SupplierPaymentSchemaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.model = schema_model.build()
        cls.forward = sql.read(FORWARD)
        cls.rollback = sql.read(ROLLBACK, sql.ROLLBACK_DIR)
        cls.statements = [s.sql for s in sql.statements(cls.forward)]

    def test_the_chain_replays_cleanly_and_the_new_tables_exist(self):
        self.assertEqual(self.model.problems, [])
        for table in TABLES:
            self.assertIn(table, self.model.tables)
            self.assertEqual(self.model.tables[table]["source"], FORWARD)

    def test_it_requires_035_and_registers_036(self):
        self.assertIn("version=35", self.forward.replace(" ", ""))
        self.assertIn("MIGRATION_036_REFUSED_missing_035", self.forward)
        self.assertRegex(self.forward, r"INSERT INTO schema_migrations\(version,name\) VALUES \(36,")

    def test_earlier_migrations_are_untouched_and_the_manifest_matches(self):
        for name, expected in FROZEN.items():
            self.assertEqual(digest(name), expected, name)
        files = {f.name: f for f in load_chain(require_committed=False)}
        self.assertEqual(len(files), 44)
        self.assertEqual(files[FORWARD].sha256, digest(FORWARD))
        self.assertIn(f"{digest(FORWARD)}  {FORWARD}", (ROOT / "database" / "schema" / "SHA256SUMS").read_text(encoding="utf-8"))

    def test_only_reference_data_permissions_and_the_correction_rule_are_written(self):
        allowed = {"cash_movement_types", "bank_movement_types", "supplier_making_movement_types", "permissions",
                   "role_permissions", "schema_migrations"}
        for statement in self.statements:
            target = re.match(r"^INSERT INTO\s+(\w+)", statement, re.I)
            if target:
                self.assertIn(target.group(1), allowed)
            self.assertNotRegex(statement, r"^(DROP TABLE|UPDATE|DELETE|TRUNCATE)\b")
        altered = {sql.table_of(s) for s in self.statements if s.upper().startswith("ALTER")}
        self.assertEqual(altered, {"transaction_corrections"})
        for statement in (s for s in self.statements if s.upper().startswith("ALTER")):
            self.assertRegex(statement, r"chk_correction_source")
        for forbidden in ("golddb", "DROP DATABASE", "financial_operations"):
            self.assertNotIn(forbidden, self.forward)

    def test_money_is_exact_decimal_never_float(self):
        tables = self.model.tables
        self.assertEqual(tables["supplier_making_payments"]["columns"]["amount"]["type"], "decimal(16,2)")
        for column in ("balance_before", "balance_after"):
            self.assertEqual(tables["supplier_making_payments"]["columns"][column]["type"], "decimal(16,2)")
        for table in TABLES:
            for name, column in tables[table]["columns"].items():
                self.assertNotRegex(column["type"], r"float|double", (table, name))

    def test_the_document_carries_supplier_method_account_date_actor_reference_and_idempotency(self):
        columns = self.model.tables["supplier_making_payments"]["columns"]
        for name in ("supplier_id", "payment_method", "cash_account_id", "bank_account_id", "amount", "payment_date", "reference",
                     "notes", "request_key", "request_hash", "performed_by_user_id", "performed_by_employee_id", "occurred_at"):
            self.assertIn(name, columns)
        self.assertEqual(columns["payment_date"]["type"], "date")
        self.assertFalse(columns["payment_date"]["nullable"])
        self.assertEqual((columns["cash_account_id"]["nullable"], columns["bank_account_id"]["nullable"]), (True, True))
        self.assertNotIn("updated_at", columns)                                        # immutable: no edit timestamp
        self.assertTrue(self.model.tables["supplier_making_payments"]["indexes"]["uq_smp_request"]["unique"])

    def test_database_checks_guard_the_payment(self):
        checks = sql.checks(self.forward)
        self.assertEqual(checks["chk_smp_amount"][1].replace(" ", ""), "amount>0")
        method = checks["chk_smp_method"][1]
        for part in ("payment_method = 'CASH' AND cash_account_id IS NOT NULL AND bank_account_id IS NULL",
                     "payment_method = 'BANK' AND bank_account_id IS NOT NULL AND cash_account_id IS NULL"):
            self.assertIn(part, method)
        self.assertEqual(checks["chk_smp_balance"][1].replace(" ", ""), "balance_after=balance_before-amount")
        leg = checks["chk_smpl_leg"][1]
        self.assertIn("leg_kind = 'MONEY'", leg)
        self.assertIn("leg_kind = 'MAKING'", leg)

    def test_every_movement_is_linked_once_and_each_payment_has_one_leg_of_each_kind(self):
        indexes = self.model.tables["supplier_making_payment_legs"]["indexes"]
        self.assertEqual(indexes["uq_smpl_kind"]["cols"], ["payment_id", "leg_kind"])
        for name in ("uq_smpl_cash_movement", "uq_smpl_bank_movement", "uq_smpl_making_movement"):
            self.assertTrue(indexes[name]["unique"], name)
        fks = self.model.tables["supplier_making_payment_legs"]["fks"]
        self.assertEqual({fks[n]["ref"] for n in fks},
                         {"supplier_making_payments", "cash_movements", "bank_movements", "supplier_making_movements"})
        payment_fks = self.model.tables["supplier_making_payments"]["fks"]
        self.assertEqual({payment_fks[n]["ref"] for n in payment_fks},
                         {"suppliers", "cash_accounts", "bank_accounts", "users", "employees"})

    def test_the_correction_source_is_a_standalone_full_reversal_and_keeps_every_earlier_source(self):
        rule = self.forward.split("ADD CONSTRAINT chk_correction_source CHECK (")[1].split(";")[0]
        for part in ("source_type IN ('INVOICE','EXCHANGE','RETURN') AND invoice_id IS NOT NULL",
                     "source_type='CUSTOMER_ACCOUNT_SETTLEMENT' AND invoice_id IS NULL",
                     "'FINANCIAL_OPERATION','CUSTOMER_DEPOSIT','SUPPLIER_MAKING_PAYMENT'",
                     "invoice_id IS NULL", "correction_type='FULL_REVERSAL'"):
            self.assertIn(part, rule)
        self.assertEqual(self.forward.count("DROP CHECK chk_correction_source"), 1)
        self.assertRegex(self.forward, r"IF\(EXISTS\(SELECT 1 FROM information_schema\.TABLE_CONSTRAINTS\s+WHERE CONSTRAINT_SCHEMA=DATABASE\(\) "
                                       r"AND TABLE_NAME='transaction_corrections' AND CONSTRAINT_NAME='chk_correction_source'\),\s+"
                                       r"'ALTER TABLE transaction_corrections DROP CHECK chk_correction_source'")   # guarded: a re-run is safe

    def test_new_movement_types_are_reference_data_with_a_stable_code(self):
        for table in ("cash_movement_types", "bank_movement_types"):
            self.assertRegex(self.forward, rf"INSERT INTO {table}\(code,name,expected_direction,description\) VALUES\s*\n\s*\('SUPPLIER_MAKING_PAYMENT'")
        self.assertIn("'PAYMENT','Supplier Making Payment'", self.forward)
        self.assertIn("'PAYMENT_REVERSAL','Supplier Making Payment Reversal'", self.forward)
        self.assertNotIn("SHOP_PAYMENT_OUT", self.forward)                            # the customer payment type is not reused

    def test_the_owner_alone_reverses_and_the_cashier_gets_nothing(self):
        self.assertEqual(len(re.findall(r"'SUPPLIER_MAKING_PAY'", self.forward)), 2)           # the right and its grant
        self.assertIn("p.code='SUPPLIER_MAKING_PAY'\n WHERE r.code IN ('OWNER','MANAGER')", self.forward)
        self.assertIn("p.code='SUPPLIER_MAKING_PAY_REVERSE'\n WHERE r.code = 'OWNER'", self.forward)
        for role in ("CASHIER", "SALESPERSON"):
            self.assertNotIn(role, self.forward)

    # ------------------------------------------------------------------ rollback
    def test_rollback_refuses_once_any_payment_movement_or_correction_exists_or_a_later_migration_exists(self):
        guard = self.rollback.split("PREPARE")[0]
        for part in ("EXISTS(SELECT 1 FROM supplier_making_payments)", "EXISTS(SELECT 1 FROM supplier_making_payment_legs)",
                     "source_type='SUPPLIER_MAKING_PAYMENT'", "t.code IN ('PAYMENT','PAYMENT_REVERSAL')",
                     "t.code='SUPPLIER_MAKING_PAYMENT'", "version>36", "ROLLBACK_036_REFUSED_supplier_payments_or_later_migration"):
            self.assertIn(part, guard)

    def test_rollback_restores_the_027_correction_rule_drops_children_first_and_removes_only_its_own_rows(self):
        restored = self.rollback.split("ADD CONSTRAINT chk_correction_source CHECK (")[1].split(";")[0]
        self.assertNotIn("SUPPLIER_MAKING_PAYMENT", restored)
        self.assertIn("'FINANCIAL_OPERATION','CUSTOMER_DEPOSIT'", restored)
        drops = re.findall(r"^DROP TABLE (\w+);", self.rollback, re.M)
        self.assertEqual(drops, ["supplier_making_payment_legs", "supplier_making_payments"])
        self.assertGreater(self.rollback.index("DROP CHECK chk_correction_source"), self.rollback.index("PREPARE"))
        self.assertIn("DELETE FROM schema_migrations WHERE version=36;", self.rollback)
        for code in ("SUPPLIER_MAKING_PAY", "SUPPLIER_MAKING_PAY_REVERSE"):
            self.assertIn(f"'{code}'", self.rollback)
        for forbidden in ("golddb", "DROP DATABASE", "TRUNCATE", "DELETE FROM supplier_making_movements",
                          "DELETE FROM cash_movements"):
            self.assertNotIn(forbidden, self.rollback)


if __name__ == "__main__":
    unittest.main()
