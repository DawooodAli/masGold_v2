"""Static safety checks on 013/014/015 and their rollbacks; no database is used."""
import re
import unittest

from tests import migration_sql as sql
from tests.billing_v2_schema_fixture import BillingSchemaDatabase


CONSOLIDATION = "013_schema_consolidation.sql"
BILLING = "014_billing_v2.sql"
PROVENANCE = "015_legacy_provenance.sql"
WORKFLOW = "016_billing_workflow.sql"
ACCOUNTS = "017_payment_accounts.sql"
OPENING = "018_opening_balances.sql"
LIFECYCLE = "019_invoice_lifecycle_exchange.sql"
SOURCES = "020_exchange_replacement_sources.sql"
FORWARD = (CONSOLIDATION, BILLING, PROVENANCE, WORKFLOW, ACCOUNTS, OPENING, LIFECYCLE, SOURCES)
# 013 is baseline schema already used by the application: no rollback script.
ROLLBACK = {BILLING: "014_billing_v2_rollback.sql",
            PROVENANCE: "015_legacy_provenance_rollback.sql",
            WORKFLOW: "016_billing_workflow_rollback.sql",
            ACCOUNTS: "017_payment_accounts_rollback.sql",
            OPENING: "018_opening_balances_rollback.sql",
            LIFECYCLE: "019_invoice_lifecycle_exchange_rollback.sql",
            SOURCES: "020_exchange_replacement_sources_rollback.sql"}
CHANGE = re.compile(r"^(ALTER|CREATE|INSERT|DELETE|DROP|UPDATE|TRUNCATE|RENAME|REPLACE)\b", re.I)
ACQUISITION_SOURCES = {"MARKET_PURCHASE", "OLD_GOLD_MANUFACTURE", "OLD_STOCK_CONVERSION",
                       "LEGACY_MIGRATION", "MANUAL_ENTRY"}

# Live FK columns with ON DELETE SET NULL; MySQL forbids them in CHECKs (error 3823).
SET_NULL_FK_COLUMNS = {
    "invoices": {"customer_id", "employee_id", "source_invoice_id", "created_by_user_id",
                 "finalized_by_user_id", "voided_by_user_id"},
    "invoice_lines": {"inventory_item_id"},
    "invoice_customer_snapshots": {"customer_id"},
    "customers": {"customer_type_id", "created_by_user_id", "updated_by_user_id"},
    "dead_cash_writeoffs": {"created_by_user_id", "reversed_by_user_id", "reversal_transaction_id"},
    "old_gold_movements": {"related_inventory_item_id", "price_quote_id", "created_by_user_id"},
    "dead_cash_recoveries": {"created_by_user_id"},
    "inventory_items": {"acquisition_quote_id", "category_id", "factory_id", "created_by_user_id",
                        "updated_by_user_id"},
    "inventory_reconciliation_cases": {"observed_karat_id", "observed_item_type_id", "observed_category_id",
                                       "observed_factory_id", "matched_inventory_item_id",
                                       "created_by_user_id", "resolved_by_user_id"},
}


def forward_text(name):
    return sql.read(name)


def rollback_text(name):
    return sql.read(ROLLBACK[name], sql.ROLLBACK_DIR)


def all_texts():
    for name in FORWARD:
        yield name, forward_text(name)
        if name in ROLLBACK:
            yield ROLLBACK[name], rollback_text(name)


class MigrationFileSafetyTests(unittest.TestCase):
    def test_files_never_name_or_switch_a_database(self):
        for name, text in all_texts():
            with self.subTest(file=name):
                self.assertFalse([s for s in sql.raw_statements(text) if re.match(r"^USE\b", s, re.I)])
                self.assertIsNone(re.search(r"masgold_(v2|prod|rehearsal)|golddb\.|Dump2026",
                                            sql.strip_comments(text), re.I))

    def test_014_and_015_are_additive(self):
        for name in (BILLING, PROVENANCE):
            for statement in sql.statements(forward_text(name)):
                with self.subTest(file=name, sql=statement.sql[:80]):
                    self.assertIsNone(re.match(r"^(DROP|TRUNCATE|DELETE|UPDATE|RENAME|REPLACE)\b",
                                               statement.sql, re.I))
                    self.assertIsNone(re.search(r"\b(DROP|RENAME|TRUNCATE)\b", statement.sql, re.I))
                    modify = re.search(r"MODIFY COLUMN\s+(\w+)\s+(.*)$", statement.sql, re.I)
                    if modify:
                        # Only 015 relaxes NOT NULL; nothing else is modified.
                        self.assertEqual(name, PROVENANCE)
                        self.assertNotIn("NOT NULL", modify.group(2).upper())
                        self.assertIn(" NULL", modify.group(2).upper())

    def test_013_changes_are_limited_to_the_approved_corrections(self):
        for statement in sql.statements(forward_text(CONSOLIDATION)):
            with self.subTest(sql=statement.sql[:80]):
                self.assertIsNone(re.match(r"^(DROP|TRUNCATE|DELETE|UPDATE|RENAME|REPLACE)\b",
                                           statement.sql, re.I))
                drops = re.findall(r"\bDROP (\w+(?: KEY)?)\s+(\w+)", statement.sql, re.I)
                if drops:
                    # Only the atomic swap of the acquisition-source CHECK.
                    self.assertEqual(drops, [("CHECK", "chk_inventory_source")])
                    self.assertIn("ADD CONSTRAINT chk_inventory_acquisition_source CHECK", statement.sql)
                modify = re.search(r"MODIFY COLUMN\s+(\w+)\s+ENUM\((.*)\) NOT NULL$", statement.sql, re.I)
                if "MODIFY" in statement.sql.upper():
                    self.assertIsNotNone(modify)
                    self.assertEqual(modify.group(1), "acquisition_source")
                    self.assertEqual(set(re.findall(r"'(\w+)'", modify.group(2))), ACQUISITION_SOURCES)

    def test_every_change_to_an_existing_table_is_guarded_by_its_own_object(self):
        for name, text in all_texts():
            for statement in sql.statements(text):
                if not re.match(r"^(ALTER TABLE|DROP)\b", statement.sql, re.I):
                    continue
                with self.subTest(file=name, sql=statement.sql[:80]):
                    self.assertTrue(statement.guarded)
                    table = sql.table_of(statement.sql) or re.search(
                        r"DROP TABLE\s+(\w+)", statement.sql, re.I).group(1)
                    self.assertIn(f"'{table}'", statement.guard)
                    for obj in re.findall(r"(?:ADD|DROP|MODIFY) (?:COLUMN|CONSTRAINT|UNIQUE KEY|KEY|"
                                          r"CHECK|INDEX|FOREIGN KEY)\s+(\w+)", statement.sql, re.I):
                        self.assertIn(f"'{obj}'", statement.guard)
                    if "MODIFY COLUMN" in statement.sql.upper():
                        self.assertRegex(statement.guard, "IS_NULLABLE|COLUMN_TYPE")

    def test_direct_statements_are_idempotent(self):
        for name in FORWARD:
            for statement in sql.statements(forward_text(name)):
                if statement.guarded:
                    continue
                with self.subTest(file=name, sql=statement.sql[:80]):
                    upper = statement.sql.upper()
                    if upper.startswith("CREATE TABLE"):
                        self.assertTrue(upper.startswith("CREATE TABLE IF NOT EXISTS"))
                    elif upper.startswith("INSERT"):
                        self.assertTrue("ON DUPLICATE KEY UPDATE" in upper or "WHERE NOT EXISTS" in upper)
                    elif upper.startswith("CREATE"):
                        self.assertTrue(upper.startswith("CREATE OR REPLACE VIEW"))

    def test_session_plumbing_never_hides_a_change(self):
        guarded = re.compile(r'^SET\s+@masgold_sql\s*:=\s*IF\(.*"\s*,\s*\'DO 0\'\s*\)$', re.S)
        for name, text in all_texts():
            for raw in sql.raw_statements(text):
                if raw.startswith("SET ") and not guarded.match(raw):
                    with self.subTest(file=name, sql=raw[:80]):
                        self.assertIsNone(re.search(r"\b(ALTER|DROP|CREATE|INSERT|DELETE|UPDATE)\b",
                                                    raw.replace("ON UPDATE", ""), re.I))

    def test_all_refusal_guards_run_before_the_first_change(self):
        for name, text in all_texts():
            with self.subTest(file=name):
                order = []
                for raw in sql.raw_statements(text):
                    if "_REFUSED_" in raw:
                        order.append("guard")
                    else:
                        effective = sql.statements(raw)
                        if effective and CHANGE.match(effective[0].sql):
                            order.append("change")
                self.assertIn("guard", order)
                self.assertLess(max(i for i, k in enumerate(order) if k == "guard"),
                                order.index("change"))
                prefix = "MIGRATION" if name in FORWARD else "ROLLBACK"
                number = name[:3]
                for kind, found in re.findall(r"(\w+)_(\d{3})_REFUSED_", text):
                    self.assertEqual((kind, found), (prefix, number))

    def test_sql_mode_is_pinned_and_restored(self):
        for name, text in all_texts():
            with self.subTest(file=name):
                raw = sql.raw_statements(text)
                self.assertTrue(raw[0].startswith("SET @masgold_saved_sql_mode"))
                self.assertNotIn("ANSI_QUOTES", raw[1])
                self.assertEqual(raw[-1], "SET SESSION sql_mode = @masgold_saved_sql_mode")

    def test_each_migration_requires_its_predecessor(self):
        self.assertIn("WHERE version = 11", forward_text(CONSOLIDATION))
        self.assertIn("MIGRATION_013_REFUSED_migration_012_not_applied", forward_text(CONSOLIDATION))
        self.assertIn("MIGRATION_014_REFUSED_migration_013_missing", forward_text(BILLING))
        self.assertIn("MIGRATION_015_REFUSED_migration_014_missing", forward_text(PROVENANCE))
        self.assertIn("MIGRATION_017_REFUSED_migration_016_missing", forward_text(ACCOUNTS))
        self.assertIn("MIGRATION_018_REFUSED_migration_017_missing", forward_text(OPENING))
        self.assertIn("MIGRATION_019_REFUSED_migration_018_missing", forward_text(LIFECYCLE))
        self.assertIn("MIGRATION_020_REFUSED_migration_019_missing", forward_text(SOURCES))

    def test_020_adds_only_the_replacement_source_objects(self):
        text = forward_text(SOURCES)
        checks = sql.checks(text)
        self.assertIn("'MANUAL', 'OLD_GOLD_STOCK'", checks["chk_lifecycle_line_identification"][1])
        identity = checks["chk_lifecycle_line_source_identity"][1]
        for rule in ("identification_method = 'MANUAL'", "inventory_item_id IS NULL",
                     "identification_method = 'OLD_GOLD_STOCK'", "old_gold_movement_id IS NOT NULL"):
            self.assertIn(rule, identity)
        self.assertFalse(sql.create_table_columns(text))                     # no new table
        self.assertIn("'EXCHANGE_REPLACEMENT_OUT'", text)
        rollback = rollback_text(SOURCES)
        for reason in ("new_replacement_sources_used", "exchange_cases_exist", "new_codes_in_use"):
            self.assertIn(f"ROLLBACK_020_REFUSED_{reason}", rollback)
        self.assertIn("MODIFY COLUMN inventory_item_id BIGINT UNSIGNED NOT NULL", rollback)

    def test_019_adds_the_exchange_objects_only(self):
        text = forward_text(LIFECYCLE)
        checks = sql.checks(text)
        for name in ("chk_inventory_status", "chk_inventory_move_from_status", "chk_inventory_move_to_status"):
            self.assertIn("'RETURNED_PENDING'", checks[name][1])
            self.assertIn("'IN_STOCK', 'SOLD', 'SCRAPPED', 'REMOVED'", checks[name][1])
        self.assertIn("'EXCHANGED'", checks["chk_invoice_event_type"][1])
        self.assertEqual(set(sql.create_table_columns(text)),
                         {"invoice_lifecycle_transactions", "invoice_lifecycle_lines", "invoice_lifecycle_payments"})
        # One original sale line can be consumed once, whatever the lifecycle type.
        self.assertIn("UNIQUE KEY uq_lifecycle_source_line (source_invoice_line_id)", text)
        self.assertIn("r.code IN ('OWNER')", text)
        rollback = rollback_text(LIFECYCLE)
        for reason in ("lifecycle_rows_exist", "returned_pending_in_use", "exchange_events_exist",
                       "new_codes_in_use"):
            self.assertIn(f"ROLLBACK_019_REFUSED_{reason}", rollback)

    def test_018_adds_only_the_opening_balance_codes(self):
        text = forward_text(OPENING)
        for table in ("cash_movement_types", "bank_movement_types"):
            inserts = [s.sql for s in sql.statements(text) if s.sql.upper().startswith(f"INSERT INTO {table.upper()} ")]
            self.assertEqual(len(inserts), 1)
            self.assertEqual(re.findall(r"\(\s*'(\w+)'\s*,\s*'[^']*'\s*,\s*'(IN|OUT|EITHER)'", inserts[0]),
                             [("OPENING_BALANCE", "IN")])
        self.assertFalse([s for s in sql.statements(text) if re.match(r"^(ALTER|CREATE|DROP|UPDATE|DELETE)", s.sql)])

    def test_017_changes_only_the_account_type_and_the_wallet_flag(self):
        text = forward_text(ACCOUNTS)
        self.assertEqual(sql.checks(text)["chk_bank_account_type"][1].split(),
                         "account_type IN ('BANK', 'MOBILE_WALLET')".split())
        updates = [s.sql for s in sql.statements(text) if s.sql.upper().startswith("UPDATE")]
        self.assertEqual(len(updates), 1)
        self.assertIn("WHERE code = 'MOBILE_WALLET'", " ".join(updates[0].split()))
        rollback = rollback_text(ACCOUNTS)
        for reason in ("wallet_accounts_exist", "wallet_payments_recorded"):
            self.assertIn(f"ROLLBACK_017_REFUSED_{reason}", rollback)


class MigrationContentTests(unittest.TestCase):
    def inserts(self, text, table):
        return [s.sql for s in sql.statements(text) if s.sql.upper().startswith(f"INSERT INTO {table.upper()} ")]

    def customer_codes(self, text):
        return dict(re.findall(r"\(\s*'(\w+)'\s*,\s*'[^']*'\s*,\s*'(INCREASE|DECREASE|EITHER)'",
                               " ".join(self.inserts(text, "customer_transaction_types"))))

    def old_gold_codes(self, text):
        return dict(re.findall(r"\(\s*'(\w+)'\s*,\s*'[^']*'\s*,\s*'[^']*'\s*,\s*'(IN|OUT|EITHER)'\s*\)",
                               " ".join(self.inserts(text, "old_gold_movement_types"))))

    def test_014_codes(self):
        text = forward_text(BILLING)
        self.assertEqual(self.customer_codes(text), {
            "SALE_BALANCE_DUE_REVERSAL": "DECREASE", "BUY_BALANCE_DUE_REVERSAL": "INCREASE",
            "DEAD_CASH_RECOVERY": "INCREASE"})
        self.assertEqual(self.old_gold_codes(text), {"CUSTOMER_BUY_IN_REVERSAL": "OUT"})
        wallet = " ".join(self.inserts(text, "payment_methods"))
        self.assertEqual(re.findall(r"\(\s*'(\w+)'", wallet), ["MOBILE_WALLET"])
        # Confirmed: no physical cash and no bank account required.
        self.assertRegex(wallet, r"'MOBILE_WALLET'\s*,\s*'Mobile Wallet'\s*,\s*FALSE\s*,\s*FALSE")

    def test_015_codes(self):
        text = forward_text(PROVENANCE)
        self.assertEqual(self.customer_codes(text), {
            "LEGACY_REMAINDER": "INCREASE", "LEGACY_PAYMENT": "DECREASE",
            "LEGACY_DEPOSIT": "DECREASE", "LEGACY_DEPOSIT_REFUND": "INCREASE"})
        self.assertEqual(self.old_gold_codes(text), {"OPENING_BALANCE": "IN"})
        self.assertFalse(self.inserts(text, "payment_methods"))

    def test_migrations_contain_no_business_or_legacy_data(self):
        allowed = {CONSOLIDATION: {"schema_migrations"},
                   BILLING: {"schema_migrations", "customer_transaction_types", "old_gold_movement_types",
                             "payment_methods", "invoice_number_sequences", "barcode_number_sequences"},
                   PROVENANCE: {"schema_migrations", "customer_transaction_types", "old_gold_movement_types"},
                   # Reference codes, permissions and the settings row only; no business data.
                   WORKFLOW: {"schema_migrations", "old_gold_movement_types", "billing_settings",
                              "permissions", "role_permissions"},
                   ACCOUNTS: {"schema_migrations"},
                   OPENING: {"schema_migrations", "cash_movement_types", "bank_movement_types"},
                   LIFECYCLE: {"schema_migrations", "inventory_movement_types", "cash_movement_types",
                               "bank_movement_types", "customer_transaction_types", "permissions",
                               "role_permissions"},
                   SOURCES: {"schema_migrations", "old_gold_movement_types"}}
        for name in FORWARD:
            for statement in sql.statements(forward_text(name)):
                target = re.match(r"^INSERT INTO\s+(\w+)", statement.sql, re.I)
                if not target:
                    continue
                with self.subTest(file=name, table=target.group(1)):
                    self.assertIn(target.group(1), allowed[name])
                    if target.group(1).endswith("_sequences"):
                        # Seeded only from rows already in this database.
                        self.assertNotIn("VALUES (", statement.sql.upper())
                        self.assertRegex(statement.sql, r"FROM (invoices|inventory_barcodes) ")

    def test_sequence_limits(self):
        checks = sql.checks(forward_text(BILLING))
        self.assertEqual(checks["chk_invoice_number_seq_range"][1], "last_seq BETWEEN 0 AND 99")
        self.assertEqual(checks["chk_barcode_number_seq_range"][1], "last_seq BETWEEN 0 AND 999")
        # The barcode seed reads every numeric 8-9 digit barcode (NN or NNN + YYMMDD), any status.
        seed = self.inserts(forward_text(BILLING), "barcode_number_sequences")[0]
        self.assertIn("REGEXP '^[0-9]{8,9}$'", seed)
        self.assertNotIn("barcode_status", seed)
        self.assertIn("'^[0-9]{2}(0[1-9]|1[0-2])(0[1-9]|[12][0-9]|3[01])(0[1-9]|[1-9][0-9])$'",
                      sql.checks(forward_text(BILLING))["chk_invoice_native_number"][1])

    def test_registrations(self):
        registrations13 = self.inserts(forward_text(CONSOLIDATION), "schema_migrations")
        self.assertTrue(any(re.search(r"SELECT 12, .* WHERE NOT EXISTS", s) for s in registrations13))
        self.assertTrue(any("VALUES ( 13," in s for s in registrations13))
        self.assertEqual([s for s in self.inserts(forward_text(BILLING), "schema_migrations")
                          if "VALUES ( 14," not in s], [])
        self.assertTrue(any("VALUES ( 15," in s for s in self.inserts(forward_text(PROVENANCE), "schema_migrations")))
        self.assertIn("DELETE FROM schema_migrations WHERE version = 14",
                      [s.sql for s in sql.statements(rollback_text(BILLING))])
        self.assertNotRegex(rollback_text(BILLING), r"version\s*=\s*1[23]\b")
        self.assertIn("DELETE FROM schema_migrations WHERE version = 15",
                      [s.sql for s in sql.statements(rollback_text(PROVENANCE))])

    def test_015_requires_014_native_checks_for_every_relaxed_column(self):
        checks14 = sql.checks(forward_text(BILLING))
        relaxed = {(sql.table_of(s.sql), re.search(r"MODIFY COLUMN\s+(\w+)", s.sql).group(1))
                   for s in sql.statements(forward_text(PROVENANCE)) if "MODIFY COLUMN" in s.sql}
        self.assertEqual(relaxed, {("invoices", "sell_total"), ("invoices", "buy_total"),
                                   ("invoice_lines", "gold_price_per_gram"),
                                   ("invoice_lines", "line_total")})
        for table, column in relaxed:
            with self.subTest(column=column):
                self.assertTrue(any(t == table and expr.startswith("record_origin <> 'NATIVE'")
                                    and f"{column} IS NOT NULL" in expr
                                    for t, expr in checks14.values()))
        self.assertIn("chk_invoice_native_totals", forward_text(PROVENANCE))

    def test_checks_are_valid_mysql_check_expressions(self):
        for name in FORWARD:
            for constraint, (table, expression) in sql.checks(forward_text(name)).items():
                with self.subTest(check=constraint):
                    self.assertIsNone(re.search(r"\bSELECT\b|@|NOW\(|CURRENT_|RAND\(|UUID\(", expression, re.I))
                    for column in SET_NULL_FK_COLUMNS.get(table, ()):
                        self.assertIsNone(re.search(rf"\b{column}\b", expression))

    def test_new_foreign_keys_used_in_checks_have_no_referential_actions(self):
        for name in FORWARD:
            text = forward_text(name)
            checked = " ".join(expr for _, expr in sql.checks(text).values())
            for statement in sql.statements(text):
                for fk in re.finditer(r"FOREIGN KEY \(([^)]*)\) REFERENCES \w+\([^)]*\)(\s+ON (DELETE|UPDATE) [A-Z ]+)?",
                                      statement.sql):
                    if fk.group(2) and "RESTRICT" not in fk.group(2):
                        for column in fk.group(1).split(","):
                            with self.subTest(fk=fk.group(0)):
                                self.assertIsNone(re.search(rf"\b{column.strip()}\b", checked))

    def test_015_view_is_004_view_restricted_to_counted_rows(self):
        original = sql.view(sql.read("004_old_gold.sql"), "vw_old_gold_balances")
        counted = sql.view(forward_text(PROVENANCE), "vw_old_gold_balances")
        self.assertEqual(counted.replace(" AND m.balance_effect = 'COUNTED'", ""), original)
        self.assertEqual(sql.view(rollback_text(PROVENANCE), "vw_old_gold_balances"), original)


class RollbackTests(unittest.TestCase):
    def test_013_has_no_rollback_script(self):
        self.assertFalse(list(sql.ROLLBACK_DIR.glob("013_*")))

    def test_rollback_removes_only_objects_its_migration_created(self):
        for name in ROLLBACK:
            created = sql.created_objects(forward_text(name))
            for statement in sql.statements(rollback_text(name)):
                for obj in re.findall(r"DROP (?:TABLE|COLUMN|CHECK|INDEX|FOREIGN KEY)\s+(\w+)", statement.sql):
                    with self.subTest(rollback=name, object=obj):
                        self.assertTrue(statement.guarded)
                        self.assertIn(obj, created)

    def test_rollback_drops_everything_its_migration_created(self):
        for name in ROLLBACK:
            dropped = set()
            for statement in sql.statements(rollback_text(name)):
                dropped.update(re.findall(r"DROP (?:TABLE|COLUMN|CHECK|INDEX|FOREIGN KEY)\s+(\w+)",
                                          statement.sql))
            new_tables = set(sql.create_table_columns(forward_text(name)))
            inner = {obj for s in sql.statements(forward_text(name)) if s.sql.startswith("CREATE TABLE")
                     for obj in sql.created_objects(s.sql)} - new_tables
            with self.subTest(rollback=name):
                self.assertEqual(sql.created_objects(forward_text(name)) - inner, dropped)

    def test_rollback_deletes_only_its_own_unused_codes(self):
        for name in ROLLBACK:
            forward, rollback = forward_text(name), rollback_text(name)
            for statement in sql.statements(rollback):
                if not statement.sql.startswith("DELETE"):
                    continue
                with self.subTest(sql=statement.sql[:80]):
                    self.assertFalse(statement.guarded)
                    table = re.match(r"DELETE FROM (\w+)", statement.sql).group(1)
                    self.assertIn(table, {"customer_transaction_types", "old_gold_movement_types",
                                          "inventory_movement_types",
                                          "cash_movement_types", "bank_movement_types",
                                          "payment_methods", "schema_migrations", "permissions",
                                          "role_permissions"})
                    for code in re.findall(r"'(\w+)'", statement.sql):
                        self.assertIn(f"'{code}'", forward)
                        self.assertRegex(rollback, rf"'{code}'[^;]*\)\s*,\s*'SELECT 1 FROM ROLLBACK_\d+_REFUSED_new_codes_in_use'")

    def test_rollback_015_restores_original_column_definitions(self):
        restores = {re.search(r"MODIFY COLUMN (\w+) (.*)$", s.sql).group(1): s.sql
                    for s in sql.statements(rollback_text(PROVENANCE)) if "MODIFY COLUMN" in s.sql}
        self.assertEqual(set(restores), {"sell_total", "buy_total", "gold_price_per_gram", "line_total"})
        for column in ("sell_total", "buy_total"):
            self.assertTrue(restores[column].endswith("DECIMAL(16,2) NOT NULL DEFAULT 0.00"))
        self.assertTrue(restores["gold_price_per_gram"].endswith("DECIMAL(14,4) NOT NULL"))
        self.assertTrue(restores["line_total"].endswith("DECIMAL(16,2) NOT NULL"))

    def test_rollback_refusals(self):
        text = rollback_text(BILLING)
        for reason in ("rollback_015_first", "invoice_family_not_empty", "dead_cash_recoveries_not_empty",
                       "writeoff_direction_in_use", "legacy_customers_exist", "new_codes_in_use",
                       "barcode_numbers_allocated"):
            self.assertIn(f"ROLLBACK_014_REFUSED_{reason}", text)
        text = rollback_text(PROVENANCE)
        for reason in ("provenance_not_empty", "legacy_invoices_exist", "provenance_json_in_use",
                       "reference_only_old_gold_exists", "new_codes_in_use"):
            self.assertIn(f"ROLLBACK_015_REFUSED_{reason}", text)


class FixtureMirrorTests(unittest.TestCase):
    """The SQLite fixture must carry every object 014/015 add."""

    def setUp(self):
        self.db = BillingSchemaDatabase()
        self.addCleanup(self.db.close)

    def test_added_columns_exist_in_fixture(self):
        for name in (BILLING, PROVENANCE):
            for table, column in sql.added_columns(forward_text(name)):
                with self.subTest(table=table, column=column):
                    self.assertIn(column, self.db.columns(table))

    def test_new_tables_match_fixture_columns(self):
        for name in (BILLING, PROVENANCE):
            for table, columns in sql.create_table_columns(forward_text(name)).items():
                with self.subTest(table=table):
                    self.assertEqual(set(columns), self.db.columns(table))

    def test_every_new_check_is_enforced_by_fixture(self):
        applied = {name for checks in self.db.checks.values() for name in checks}
        for name in (BILLING, PROVENANCE):
            for constraint in sql.checks(forward_text(name)):
                with self.subTest(check=constraint):
                    self.assertIn(constraint, applied)


if __name__ == "__main__":
    unittest.main()
