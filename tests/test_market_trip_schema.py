"""Migration 037 (market-trip custody): static replay and contract of the forward and rollback files.
No database is used; the live behaviour was proven by the rehearsal on masgold_rehearsal (see the task report)."""
import hashlib
from pathlib import Path
import re
import unittest

from tests import migration_sql as sql
from tests import schema_model
from tools.schema_rehearsal.chain import load_chain

ROOT = Path(__file__).resolve().parents[1]
FORWARD = "037_market_trip_custody.sql"
ROLLBACK = "037_market_trip_custody_rollback.sql"
TABLES = ["market_trips", "market_trip_status_events", "market_trip_entries", "market_trip_entry_cash", "market_trip_entry_gold"]
FROZEN = {"034_suppliers_purchasing.sql": "3f35b7d149cfd51f40fe82377532e43fc93e4d6af7e9dd6551c2efcf51db0de2",
          "035_supplier_karat.sql": "30220bd68e41f599b31ebd325f75add294f90fc8bd5f636072f6b99feb688512",
          "036_supplier_making_payments.sql": "f715fbed3968c3a7db72ee3b4c131286a973f6e86cea2197658a24d1f80d918c"}


def digest(name):
    return hashlib.sha256((ROOT / "database" / "schema" / name).read_bytes().replace(b"\r\n", b"\n")).hexdigest()


class MarketTripSchemaTests(unittest.TestCase):
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

    def test_it_requires_036_and_registers_037(self):
        self.assertIn("version=36", self.forward.replace(" ", ""))
        self.assertIn("MIGRATION_037_REFUSED_missing_036", self.forward)
        self.assertRegex(self.forward, r"INSERT INTO schema_migrations\(version,name\) VALUES \(37,")

    def test_earlier_migrations_are_untouched_and_the_manifest_matches(self):
        for name, expected in FROZEN.items():
            self.assertEqual(digest(name), expected, name)
        files = {f.name: f for f in load_chain(require_committed=False)}
        self.assertEqual(len(files), 72)
        self.assertEqual(files[FORWARD].sha256, digest(FORWARD))
        self.assertIn(f"{digest(FORWARD)}  {FORWARD}", (ROOT / "database" / "schema" / "SHA256SUMS").read_text(encoding="utf-8"))

    def test_only_reference_data_permissions_and_the_correction_rule_are_written(self):
        allowed = {"old_gold_movement_types", "cash_movement_types", "permissions", "role_permissions", "schema_migrations"}
        for statement in self.statements:
            target = re.match(r"^INSERT INTO\s+(\w+)", statement, re.I)
            if target:
                self.assertIn(target.group(1), allowed)
            self.assertNotRegex(statement, r"^(DROP TABLE|UPDATE|DELETE|TRUNCATE)\b")
        self.assertEqual({sql.table_of(s) for s in self.statements if s.upper().startswith("ALTER")}, {"transaction_corrections"})
        for forbidden in ("golddb", "DROP DATABASE", "supplier_"):
            self.assertNotIn(forbidden, self.forward)

    def test_quantities_are_exact_decimal_with_the_existing_precision(self):
        tables = self.model.tables
        self.assertEqual(tables["market_trip_entry_cash"]["columns"]["amount"]["type"], "decimal(16,2)")
        self.assertEqual(tables["market_trip_entry_gold"]["columns"]["weight_grams"]["type"], "decimal(12,3)")
        for table in TABLES:
            for name, column in tables[table]["columns"].items():
                self.assertNotRegex(column["type"], r"float|double", (table, name))
        gold = tables["market_trip_entry_gold"]["columns"]
        self.assertEqual(gold["karat_id"]["type"], "tinyint unsigned")                       # an existing master karat, nothing derived
        for forbidden in ("equivalent_21", "eq21", "purity"):
            self.assertFalse([c for c in gold if forbidden in c])

    def test_checks_guard_status_kind_quantities_and_the_reopen_reason(self):
        checks = sql.checks(self.forward)
        self.assertIn("'OPEN','CLOSED'", checks["chk_mt_status"][1].replace(" ", ""))
        self.assertIn("'ISSUE','RETURN'", checks["chk_mte_kind"][1].replace(" ", ""))
        self.assertEqual(checks["chk_mtec_amount"][1].replace(" ", ""), "amount>0")
        self.assertEqual(checks["chk_mteg_weight"][1].replace(" ", ""), "weight_grams>0")
        self.assertIn("'OPENED','CLOSED','REOPENED'", checks["chk_mtse_type"][1].replace(" ", ""))
        self.assertIn("event_type <> 'REOPENED'", checks["chk_mtse_reason"][1])

    def test_entries_are_immutable_documents_with_idempotency_and_one_leg_per_movement(self):
        entries = self.model.tables["market_trip_entries"]
        self.assertTrue(entries["indexes"]["uq_mte_request"]["unique"])
        self.assertEqual(entries["indexes"]["uq_mte_no"]["cols"], ["trip_id", "entry_no"])
        self.assertNotIn("updated_at", entries["columns"])
        self.assertEqual({entries["fks"][n]["ref"] for n in entries["fks"]}, {"market_trips", "users", "employees"})
        cash = self.model.tables["market_trip_entry_cash"]["indexes"]
        gold = self.model.tables["market_trip_entry_gold"]["indexes"]
        self.assertTrue(cash["uq_mtec_entry"]["unique"] and cash["uq_mtec_movement"]["unique"])
        self.assertNotIn("uq_mteg_entry_karat", gold)                                     # 039 replaced it: one leg per (entry, karat, designation)
        self.assertEqual(gold["uq_mteg_entry_karat_des"]["cols"], ["entry_id", "karat_id", "designation"])
        self.assertTrue(gold["uq_mteg_movement"]["unique"])
        self.assertEqual({self.model.tables["market_trip_entry_cash"]["fks"][n]["ref"] for n in self.model.tables["market_trip_entry_cash"]["fks"]},
                         {"market_trip_entries", "cash_accounts", "cash_movements"})
        self.assertEqual({self.model.tables["market_trip_entry_gold"]["fks"][n]["ref"] for n in self.model.tables["market_trip_entry_gold"]["fks"]},
                         {"market_trip_entries", "karats", "old_gold_movements"})
        trips = self.model.tables["market_trips"]["columns"]
        # accounting_mode (056) is creation-time metadata, not a mutable field: the status columns stay the only mutable ones. The database protects it only through the
        # composite FK (trip_id, trip_mode) -> (id, accounting_mode) ON UPDATE RESTRICT once a trip has entries (no trigger; see test_market_trip_book_schema).
        self.assertEqual(set(trips) - {"id", "notes", "created_by_user_id", "created_at", "accounting_mode"}, {"status", "status_changed_at"})
        self.assertEqual(trips["accounting_mode"], {"type": "varchar(8)", "nullable": False})

    def test_the_correction_source_is_a_standalone_full_reversal_and_keeps_every_earlier_source(self):
        rule = self.forward.split("ADD CONSTRAINT chk_correction_source CHECK (")[1].split(";")[0]
        for part in ("source_type IN ('INVOICE','EXCHANGE','RETURN') AND invoice_id IS NOT NULL",
                     "source_type='CUSTOMER_ACCOUNT_SETTLEMENT' AND invoice_id IS NULL",
                     "'FINANCIAL_OPERATION','CUSTOMER_DEPOSIT','SUPPLIER_MAKING_PAYMENT','MARKET_TRIP_ENTRY'",
                     "invoice_id IS NULL", "correction_type='FULL_REVERSAL'"):
            self.assertIn(part, rule)
        self.assertEqual(self.forward.count("DROP CHECK chk_correction_source"), 1)
        self.assertRegex(self.forward, r"IF\(EXISTS\(SELECT 1 FROM information_schema\.TABLE_CONSTRAINTS\s+WHERE CONSTRAINT_SCHEMA=DATABASE\(\) "
                                       r"AND TABLE_NAME='transaction_corrections' AND CONSTRAINT_NAME='chk_correction_source'\),\s+"
                                       r"'ALTER TABLE transaction_corrections DROP CHECK chk_correction_source'")

    def test_new_movement_types_have_stable_codes_and_the_right_directions(self):
        for code, direction in (("TRIP_CUSTODY_OUT", "OUT"), ("TRIP_CUSTODY_RETURN_IN", "IN"), ("REVERSAL_TRIP_ISSUE_IN", "IN"),
                                ("REVERSAL_TRIP_RETURN_OUT", "OUT")):
            self.assertRegex(self.forward, rf"\('{code}','[^']+','[^']+','{direction}'\)")
        for code, direction in (("TRIP_CASH_ISSUE", "OUT"), ("TRIP_CASH_RETURN", "IN")):
            self.assertRegex(self.forward, rf"\('{code}','[^']+','{direction}','")
        self.assertNotIn("SHOP_PAYMENT_OUT", self.forward)

    def test_the_owner_alone_holds_the_right(self):
        self.assertEqual(len(re.findall(r"'MARKET_TRIP_MANAGE'", self.forward)), 2)       # the right and its grant
        self.assertIn("p.code='MARKET_TRIP_MANAGE'\n WHERE r.code = 'OWNER'", self.forward)
        for role in ("MANAGER", "CASHIER", "SALESPERSON"):
            self.assertNotIn(role, self.forward)

    # ------------------------------------------------------------------ rollback
    def test_rollback_refuses_once_any_trip_movement_or_correction_exists_or_a_later_migration_exists(self):
        guard = self.rollback.split("PREPARE")[0]
        for part in ("EXISTS(SELECT 1 FROM market_trips)", "EXISTS(SELECT 1 FROM market_trip_entries)",
                     "source_type='MARKET_TRIP_ENTRY'", "'TRIP_CUSTODY_OUT','TRIP_CUSTODY_RETURN_IN','REVERSAL_TRIP_ISSUE_IN','REVERSAL_TRIP_RETURN_OUT'",
                     "'TRIP_CASH_ISSUE','TRIP_CASH_RETURN'", "version>37", "ROLLBACK_037_REFUSED_trip_data_or_later_migration"):
            self.assertIn(part, guard)

    def test_rollback_restores_the_036_rule_drops_children_first_and_removes_only_its_own_rows(self):
        restored = self.rollback.split("ADD CONSTRAINT chk_correction_source CHECK (")[1].split(";")[0]
        self.assertNotIn("MARKET_TRIP_ENTRY", restored)
        self.assertIn("'FINANCIAL_OPERATION','CUSTOMER_DEPOSIT','SUPPLIER_MAKING_PAYMENT'", restored)
        drops = re.findall(r"^DROP TABLE (\w+);", self.rollback, re.M)
        self.assertEqual(drops, ["market_trip_entry_gold", "market_trip_entry_cash", "market_trip_entries", "market_trip_status_events", "market_trips"])
        self.assertGreater(self.rollback.index("DROP CHECK chk_correction_source"), self.rollback.index("PREPARE"))
        self.assertIn("DELETE FROM schema_migrations WHERE version=37;", self.rollback)
        for forbidden in ("golddb", "DROP DATABASE", "TRUNCATE", "DELETE FROM old_gold_movements", "DELETE FROM cash_movements"):
            self.assertNotIn(forbidden, self.rollback)


if __name__ == "__main__":
    unittest.main()
