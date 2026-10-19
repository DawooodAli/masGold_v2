"""Migration 044 (market-party roles on the supplier master): static replay and contract of the forward and rollback files.
No database is used; the live behaviour (constraints, refusals, rollback) was proven by the rehearsal on masgold_rehearsal."""
import hashlib
from pathlib import Path
import re
import unittest

from tests import migration_sql as sql
from tests import schema_model
from tools.schema_rehearsal.chain import load_chain

ROOT = Path(__file__).resolve().parents[1]
FORWARD = "044_market_party_roles.sql"
ROLLBACK = "044_market_party_roles_rollback.sql"
FROZEN = {"042_market_trip_heating.sql": "91c8fcc2d9a6d92607301d7325d4a39fc6dd25a381415b3e4fd9e59ab665ed8b",
          "043_market_trip_supplier_payments.sql": "e83e409db27ed618a3a15793c2015615fb6c7d482d661af767d7a1d08c058e04"}


def digest(name):
    return hashlib.sha256((ROOT / "database" / "schema" / name).read_bytes().replace(b"\r\n", b"\n")).hexdigest()


class PartyRoleSchemaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.model = schema_model.build()
        cls.forward = sql.read(FORWARD)
        cls.rollback = sql.read(ROLLBACK, sql.ROLLBACK_DIR)
        cls.statements = [s.sql for s in sql.statements(cls.forward)]

    def test_the_chain_replays_cleanly(self):
        self.assertEqual(self.model.problems, [])

    def test_it_requires_043_and_registers_044(self):
        self.assertIn("version=43", self.forward.replace(" ", ""))
        self.assertIn("MIGRATION_044_REFUSED_missing_043", self.forward)
        self.assertRegex(self.forward, r"INSERT INTO schema_migrations\(version,name\) VALUES \(44,")

    def test_earlier_migrations_are_untouched_and_the_manifest_matches(self):
        for name, expected in FROZEN.items():
            self.assertEqual(digest(name), expected, name)
        files = {f.name: f for f in load_chain(require_committed=False)}
        self.assertEqual(len(files), 59)
        self.assertEqual(files[FORWARD].sha256, digest(FORWARD))
        self.assertIn(f"{digest(FORWARD)}  {FORWARD}", (ROOT / "database" / "schema" / "SHA256SUMS").read_text(encoding="utf-8"))

    def test_only_two_guarded_columns_and_two_checks_on_suppliers_and_no_row_is_rewritten(self):
        for statement in self.statements:
            self.assertNotRegex(statement, r"^(UPDATE|DELETE|TRUNCATE|DROP)\b")
            target = re.match(r"^INSERT INTO\s+(\w+)", statement, re.I)
            if target:
                self.assertEqual(target.group(1), "schema_migrations")
        alters = [s for s in sql.statements(self.forward) if s.sql.upper().startswith("ALTER")]
        self.assertEqual({sql.table_of(s.sql) for s in alters}, {"suppliers"})
        self.assertEqual(len(alters), 4)
        for statement in alters:
            self.assertTrue(statement.guarded, statement.sql[:80])

    def test_existing_rows_become_supplier_only_and_one_role_is_required(self):
        self.assertIn("is_supplier BOOLEAN NOT NULL DEFAULT TRUE", self.forward)
        self.assertIn("is_intermediary BOOLEAN NOT NULL DEFAULT FALSE", self.forward)
        self.assertIn("CHECK (is_supplier IN (0,1) AND is_intermediary IN (0,1) AND (is_supplier = 1 OR is_intermediary = 1))", self.forward)
        self.assertIn("CHECK (is_supplier = 1 OR karat_id IS NULL)", self.forward)            # an intermediary-only party has no supplier-account karat
        code = " ".join(line for line in self.forward.splitlines() if not line.lstrip().startswith("--"))
        for forbidden in ("karat_id =", "supplier_gold", "supplier_making", "cash_movements", "golddb", "backfill"):
            self.assertNotIn(forbidden, code.replace("karat_id IS NULL", ""), forbidden)      # no karat is guessed, no ledger is touched

    def test_the_rollback_refuses_when_an_intermediary_role_would_lose_its_meaning(self):
        self.assertIn("ROLLBACK_044_REFUSED_intermediary_roles_or_later_migration", self.rollback)
        for needle in ("is_intermediary = 1 OR is_supplier = 0", "version>44"):
            self.assertIn(needle, self.rollback)
        self.assertLess(self.rollback.index("ROLLBACK_044_REFUSED"), self.rollback.index("DROP CHECK"))
        self.assertLess(self.rollback.index("DROP CHECK chk_suppliers_intermediary_karat"), self.rollback.index("DROP COLUMN is_intermediary"))
        self.assertIn("DELETE FROM schema_migrations WHERE version=44", self.rollback)


if __name__ == "__main__":
    unittest.main()
