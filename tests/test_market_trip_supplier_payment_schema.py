"""Migration 043 (supplier making payments from market custody): static replay and contract of the forward and rollback files.
No database is used; the live behaviour (constraints, refusals, rollback) was proven by the rehearsal on masgold_rehearsal."""
import hashlib
from pathlib import Path
import re
import unittest

from tests import migration_sql as sql
from tests import schema_model
from tools.schema_rehearsal.chain import load_chain

ROOT = Path(__file__).resolve().parents[1]
FORWARD = "043_market_trip_supplier_payments.sql"
ROLLBACK = "043_market_trip_supplier_payments_rollback.sql"
FROZEN = {"041_market_trip_lot_reissue.sql": "8d3bb3f2d335b85478e4ef8d75f5afb3dcc7bd3d3270c31c217b2980c528be8e",
          "042_market_trip_heating.sql": "91c8fcc2d9a6d92607301d7325d4a39fc6dd25a381415b3e4fd9e59ab665ed8b"}


def digest(name):
    return hashlib.sha256((ROOT / "database" / "schema" / name).read_bytes().replace(b"\r\n", b"\n")).hexdigest()


class SupplierPaymentSchemaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.model = schema_model.build()
        cls.forward = sql.read(FORWARD)
        cls.rollback = sql.read(ROLLBACK, sql.ROLLBACK_DIR)
        cls.statements = [s.sql for s in sql.statements(cls.forward)]

    def test_the_chain_replays_cleanly_and_the_link_table_exists(self):
        self.assertEqual(self.model.problems, [])
        self.assertEqual(self.model.tables["market_trip_supplier_payments"]["source"], FORWARD)

    def test_it_requires_042_and_registers_043(self):
        self.assertIn("version=42", self.forward.replace(" ", ""))
        self.assertIn("MIGRATION_043_REFUSED_missing_042", self.forward)
        self.assertRegex(self.forward, r"INSERT INTO schema_migrations\(version,name\) VALUES \(43,")

    def test_earlier_migrations_are_untouched_and_the_manifest_matches(self):
        for name, expected in FROZEN.items():
            self.assertEqual(digest(name), expected, name)
        files = {f.name: f for f in load_chain(require_committed=False)}
        self.assertEqual(len(files), 52)
        self.assertEqual(files[FORWARD].sha256, digest(FORWARD))
        self.assertIn(f"{digest(FORWARD)}  {FORWARD}", (ROOT / "database" / "schema" / "SHA256SUMS").read_text(encoding="utf-8"))

    def test_only_two_checks_change_and_no_row_is_rewritten(self):
        for statement in self.statements:
            self.assertNotRegex(statement, r"^(UPDATE|DELETE|TRUNCATE|DROP TABLE)\b")
            target = re.match(r"^INSERT INTO\s+(\w+)", statement, re.I)
            if target:
                self.assertEqual(target.group(1), "schema_migrations")
        self.assertEqual({sql.table_of(s) for s in self.statements if s.upper().startswith("ALTER")}, {"market_trip_entries", "supplier_making_payments"})
        for statement in (s for s in sql.statements(self.forward) if s.sql.upper().startswith("ALTER")):
            self.assertTrue(statement.guarded, statement.sql[:80])
        self.assertIn("CHECK (entry_kind IN ('ISSUE','RETURN','MELT','LOT_RETURN','LOT_ISSUE','HEAT','SUPPLIER_PAY'))", self.forward)
        self.assertLessEqual(len("SUPPLIER_PAY"), 12)                                             # fits entry_kind VARCHAR(12)
        self.assertLessEqual(len("CUSTODY"), 10)                                                  # fits payment_method VARCHAR(10)

    def test_the_custody_method_has_no_account_and_the_older_methods_are_kept(self):
        for needle in ("(payment_method = 'CASH' AND cash_account_id IS NOT NULL AND bank_account_id IS NULL)",
                       "(payment_method = 'BANK' AND bank_account_id IS NOT NULL AND cash_account_id IS NULL)",
                       "(payment_method = 'CUSTODY' AND cash_account_id IS NULL AND bank_account_id IS NULL)"):
            self.assertIn(needle, self.forward)

    def test_the_link_is_insert_only_one_per_entry_and_one_per_payment(self):
        for needle in ("PRIMARY KEY (entry_id)", "UNIQUE KEY uq_mtsp_payment (payment_id)", "CHECK (amount > 0)", "DECIMAL(16,2)",
                       "REFERENCES market_trip_entries(id)", "REFERENCES supplier_making_payments(id)", "REFERENCES suppliers(id)"):
            self.assertIn(needle, self.forward)
        self.assertNotIn("ON DELETE", self.forward)
        code = " ".join(line for line in self.forward.splitlines() if not line.lstrip().startswith("--"))
        for forbidden in ("cash_movements", "bank_movements", "old_gold_movements", "supplier_gold", "golddb", "FLOAT", "DOUBLE", "INSERT INTO permissions"):
            self.assertNotIn(forbidden, code, forbidden)                                         # no money, gold or permission object is touched

    def test_the_rollback_refuses_on_any_custody_payment_data_or_a_later_migration_and_restores_the_042_rules(self):
        self.assertIn("ROLLBACK_043_REFUSED_custody_payment_data_or_later_migration", self.rollback)
        for needle in ("FROM market_trip_supplier_payments", "entry_kind = 'SUPPLIER_PAY'", "payment_method = 'CUSTODY'", "version>43"):
            self.assertIn(needle, self.rollback)
        self.assertLess(self.rollback.index("ROLLBACK_043_REFUSED"), self.rollback.index("DROP TABLE"))
        self.assertIn("CHECK (entry_kind IN ('ISSUE','RETURN','MELT','LOT_RETURN','LOT_ISSUE','HEAT'))", self.rollback)
        self.assertNotIn("CUSTODY'", self.rollback.split("ADD CONSTRAINT chk_smp_method")[1].split(";")[0])
        self.assertIn("DELETE FROM schema_migrations WHERE version=43", self.rollback)


if __name__ == "__main__":
    unittest.main()
