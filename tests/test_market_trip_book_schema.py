"""Migration 056 (market-trip BOOK core): static replay and contract of the forward and rollback files. No database is used; the live behaviour (constraints, refusals, the
rollback guard, races) is NOT proven here and still needs the prepared, not yet executed, rehearsal tests (tests/test_market_trip_book_mysql.py)."""
import hashlib
from pathlib import Path
import re
import unittest

from services import book_rules
from tests import migration_sql as sql
from tests import schema_model
from tools.schema_rehearsal.chain import load_chain

ROOT = Path(__file__).resolve().parents[1]
FORWARD = "056_market_trip_book_core.sql"
ROLLBACK = "056_market_trip_book_core_rollback.sql"
FROZEN = {"055_marketer_custody_account.sql": "68db63f9eb101c961c1ecded1786d4b56b2d1f5c914ecd9674349e0ee7c8f0d2",
          "054_market_trip_gold_buy.sql": "4ba0c10b2f31f10d21f294d27ca09d6dda6c2ef026683c441693eee9a6f45129"}
EARLIER_KINDS = ("ISSUE", "RETURN", "MELT", "LOT_RETURN", "LOT_ISSUE", "HEAT", "SUPPLIER_PAY", "GOLD_SETTLE", "LOT_SETTLE", "LOT_HOLD", "HOLD_RETURN",
                 "LOGO_HOLD", "HOLD_BACK", "GOLD_SALE", "GOLD_BUY")


def digest(name):
    return hashlib.sha256((ROOT / "database" / "schema" / name).read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def code_of(text):
    return " ".join(line for line in text.splitlines() if not line.lstrip().startswith("--"))


def table(forward, name):
    return re.search(r"CREATE TABLE IF NOT EXISTS " + name + r" \((.*?)\) ENGINE=InnoDB", forward, re.S).group(1)


class BookCoreSchemaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.model = schema_model.build()
        cls.forward = sql.read(FORWARD)
        cls.rollback = sql.read(ROLLBACK, sql.ROLLBACK_DIR)
        cls.code = code_of(cls.forward)
        cls.lines = table(cls.forward, "market_trip_book_lines")
        cls.trades = table(cls.forward, "market_trip_book_trades")
        cls.stock = table(cls.forward, "shop_assayed_stock_movements")

    # ---------------------------------------------------------------- chain
    def test_the_chain_replays_cleanly_and_the_new_objects_exist(self):
        self.assertEqual(self.model.problems, [])
        for name in ("market_trip_book_lines", "market_trip_book_trades", "shop_assayed_stock_movements"):
            self.assertEqual(self.model.tables[name]["source"], FORWARD)
        self.assertIn("accounting_mode", self.model.tables["market_trips"]["columns"])
        self.assertIn("trip_mode", self.model.tables["market_trip_entries"]["columns"])
        self.assertIn("trip_mode", self.model.tables["market_trip_entry_gold"]["columns"])

    def test_it_requires_055_and_registers_056(self):
        self.assertIn("version=55", self.forward.replace(" ", ""))
        self.assertIn("MIGRATION_056_REFUSED_missing_055", self.forward)
        self.assertRegex(self.forward, r"INSERT INTO schema_migrations\(version,name\) VALUES \(56,")

    def test_earlier_migrations_are_untouched_and_the_manifest_matches(self):
        for name, expected in FROZEN.items():
            self.assertEqual(digest(name), expected, name)
        files = {f.name: f for f in load_chain(require_committed=False)}
        self.assertEqual(len(files), 67)
        self.assertEqual(files[FORWARD].sha256, digest(FORWARD))
        self.assertIn(f"{digest(FORWARD)}  {FORWARD}", (ROOT / "database" / "schema" / "SHA256SUMS").read_text(encoding="utf-8"))

    def test_the_schema_level_follows_the_chain(self):
        from services.schema_level import REQUIRED_SCHEMA_LEVEL
        self.assertEqual(REQUIRED_SCHEMA_LEVEL, 67)

    def test_every_alter_is_guarded_so_a_rerun_changes_nothing(self):
        for statement in re.findall(r"\"(ALTER TABLE [^\"]*)\"", self.forward):
            self.assertRegex(self.forward, r"IF\((?:NOT )?EXISTS[^;]*" + re.escape(statement[:60]), statement[:80])
        self.assertEqual(len(re.findall(r"CREATE TABLE IF NOT EXISTS", self.forward)), 3)
        for forbidden in ("DROP TABLE", "TRUNCATE", "DELETE FROM", "UPDATE market_trip", "INSERT INTO market_trip", "INSERT INTO cash_movement_types", "INSERT INTO old_gold_movement_types"):
            self.assertNotIn(forbidden, self.code, forbidden)                                             # no row of any business table is written or removed

    # ---------------------------------------------------------------- mode and isolation
    def test_existing_rows_acquire_legacy_through_the_default_and_the_mode_is_immutable_once_an_entry_exists(self):
        self.assertIn("ADD COLUMN accounting_mode VARCHAR(8) NOT NULL DEFAULT 'LEGACY'", self.forward)
        self.assertIn("ADD COLUMN trip_mode VARCHAR(8) NOT NULL DEFAULT 'LEGACY'", self.forward)
        self.assertIn("accounting_mode IN ('LEGACY','BOOK')", self.forward)
        self.assertIn("ADD UNIQUE KEY uq_mt_id_mode (id, accounting_mode)", self.forward)
        self.assertIn("ADD UNIQUE KEY uq_mte_id_mode (id, trip_mode)", self.forward)
        self.assertIn("FOREIGN KEY (trip_id, trip_mode) REFERENCES market_trips(id, accounting_mode) ON UPDATE RESTRICT ON DELETE RESTRICT", self.forward)
        fk = self.model.tables["market_trip_entries"]["fks"]["fk_mte_trip_mode"]
        self.assertEqual((fk["cols"], fk["ref"], fk["refcols"]), (["trip_id", "trip_mode"], "market_trips", ["id", "accounting_mode"]))
        self.assertEqual(self.model.tables["market_trips"]["indexes"]["uq_mt_id_mode"]["cols"], ["id", "accounting_mode"])
        self.assertEqual(self.model.tables["market_trip_entries"]["indexes"]["uq_mte_id_mode"]["cols"], ["id", "trip_mode"])
        self.assertNotIn("ON UPDATE CASCADE", self.code)
        self.assertNotIn("TRIGGER", self.code.upper())                                                    # no triggers: the rule is keys + row-local CHECKs

    def test_the_kind_rule_adds_the_four_book_kinds_and_keeps_every_earlier_kind(self):
        vocabulary = re.search(r"ADD CONSTRAINT chk_mte_kind CHECK \(entry_kind IN \(([^)]*)\)\)", self.forward).group(1)
        self.assertEqual(re.findall(r"'(\w+)'", vocabulary), list(EARLIER_KINDS) + list(book_rules.PHASE1_KINDS))     # 056 added the four Phase 1 kinds (058 adds Phase 2)
        rule = re.search(r"ADD CONSTRAINT chk_mte_mode_kind CHECK \((.*?)\)\", 'DO 0'\)", self.forward, re.S).group(1)
        legacy, book = re.search(r"trip_mode = 'LEGACY' AND entry_kind IN \(([^)]*)\)", rule).group(1), re.search(r"trip_mode = 'BOOK' AND entry_kind IN \(([^)]*)\)", rule).group(1)
        self.assertEqual(re.findall(r"'(\w+)'", legacy), list(EARLIER_KINDS))
        self.assertEqual(re.findall(r"'(\w+)'", book), list(book_rules.SHARED_CASH_KINDS) + list(book_rules.PHASE1_KINDS))
        for kind in book_rules.LEGACY_ONLY_KINDS:                                                         # the pure rule and the database rule say the same thing (060 adds ST_MELT later)
            self.assertIn(kind, EARLIER_KINDS + ("ST_MELT",))
            self.assertNotIn(f"'{kind}'", book)
        self.assertEqual(set(EARLIER_KINDS) | {"ST_MELT"}, set(book_rules.LEGACY_ONLY_KINDS) | set(book_rules.SHARED_CASH_KINDS))

    def test_the_legacy_gold_legs_exist_only_for_legacy_entries(self):
        for needle in ("ALTER TABLE market_trip_entry_gold ADD COLUMN trip_mode VARCHAR(8) NOT NULL DEFAULT 'LEGACY'", "CHECK (trip_mode = 'LEGACY')",
                       "FOREIGN KEY (entry_id, trip_mode) REFERENCES market_trip_entries(id, trip_mode) ON UPDATE RESTRICT ON DELETE RESTRICT"):
            self.assertIn(needle, self.forward)

    # ---------------------------------------------------------------- the new tables
    def test_the_book_lines_keep_facts_evidence_and_the_posted_amount_with_every_cross_check(self):
        for needle in ("PRIMARY KEY (entry_id, line_no)", "weight_grams DECIMAL(14,3) NOT NULL", "basis VARCHAR(12) NOT NULL", "karat_id TINYINT UNSIGNED DEFAULT NULL",
                       "fineness_permille DECIMAL(5,1) DEFAULT NULL", "eq21_analytical DECIMAL(18,6) NOT NULL", "eq21_posted DECIMAL(14,3) NOT NULL",
                       "posting_difference DECIMAL(12,6) NOT NULL", "signed_eq21_posted DECIMAL(14,3) NOT NULL", "line_price DECIMAL(16,2) DEFAULT NULL",
                       "FOREIGN KEY (entry_id, entry_kind, trip_id) REFERENCES market_trip_entries(id, entry_kind, trip_id)",
                       "FOREIGN KEY (entry_id, trip_mode) REFERENCES market_trip_entries(id, trip_mode) ON UPDATE RESTRICT ON DELETE RESTRICT",
                       "FOREIGN KEY (karat_id) REFERENCES karats(id)", "FOREIGN KEY (scrap_movement_id) REFERENCES old_gold_movements(id)",
                       "UNIQUE KEY uq_mtbl_scrap (scrap_movement_id)", "UNIQUE KEY uq_mtbl_item (entry_id, line_no, entry_kind, basis)", "CHECK (trip_mode = 'BOOK')",
                       "entry_kind IN ('BK_ISSUE','BK_BUY') AND direction = 'IN'", "entry_kind IN ('BK_SELL','BK_RETURN') AND direction = 'OUT'", "weight_grams > 0",
                       "basis = 'MASTER_KARAT' AND karat_id IS NOT NULL AND karat_value IS NOT NULL AND karat_value > 0 AND fineness_permille IS NULL",
                       "basis = 'FINENESS' AND karat_id IS NULL AND karat_value IS NULL AND fineness_permille IS NOT NULL AND fineness_permille > 0 AND fineness_permille <= 1000",
                       "ABS(posting_difference) <= 0.0005", "ABS((eq21_posted - eq21_analytical) - posting_difference) <= 0.000001",
                       "direction = 'IN' AND signed_eq21_posted = eq21_posted", "direction = 'OUT' AND signed_eq21_posted = -eq21_posted",
                       "ABS(eq21_analytical * 21 - weight_grams * karat_value) <= 0.0000105", "ABS(eq21_analytical * 875 - weight_grams * fineness_permille) <= 0.0004375"):
            self.assertIn(needle, self.lines, needle)

    def test_the_evidence_tolerances_are_exactly_the_six_decimal_rounding_scaled_by_the_factor(self):
        self.assertEqual(0.0000005 * 21, 0.0000105)                                                       # |round6(a) - a| <= 5e-7, a = weight x karat / 21
        self.assertAlmostEqual(0.0000005 * 875, 0.0004375, places=12)                                     # a = weight x permille / 875

    def test_each_line_kind_has_exactly_its_leg_scrap_stock_or_price(self):
        leg = re.search(r"CONSTRAINT chk_mtbl_leg CHECK \((.*)$", self.lines, re.S).group(1)
        for needle in ("entry_kind = 'BK_ISSUE' AND basis = 'MASTER_KARAT' AND scrap_movement_id IS NOT NULL AND line_price IS NULL",
                       "entry_kind = 'BK_RETURN' AND basis = 'MASTER_KARAT' AND scrap_movement_id IS NOT NULL AND line_price IS NULL",
                       "entry_kind = 'BK_RETURN' AND basis = 'FINENESS' AND scrap_movement_id IS NULL AND line_price IS NULL",
                       "entry_kind IN ('BK_BUY','BK_SELL') AND scrap_movement_id IS NULL AND line_price IS NOT NULL AND line_price > 0"):
            self.assertIn(needle, leg)

    def test_the_trade_document_keeps_the_counterparty_mandatory_and_the_cash_in_custody(self):
        for needle in ("PRIMARY KEY (entry_id)", "counterparty_name VARCHAR(150) NOT NULL", "counterparty_name_key VARCHAR(150) NOT NULL", "party_id INT UNSIGNED DEFAULT NULL",
                       "total_price DECIMAL(16,2) NOT NULL", "FOREIGN KEY (entry_id, entry_kind) REFERENCES market_trip_entries(id, entry_kind)",
                       "FOREIGN KEY (party_id) REFERENCES suppliers(id)", "entry_kind IN ('BK_BUY','BK_SELL')",
                       "counterparty_kind = 'REGISTERED_PARTY' AND party_id IS NOT NULL", "counterparty_kind = 'THIRD_PARTY' AND party_id IS NULL",
                       "CHAR_LENGTH(TRIM(counterparty_name)) > 0", "CHAR_LENGTH(TRIM(counterparty_name_key)) > 0", "total_price > 0",
                       "reference_total IS NULL AND deviation_percent IS NULL"):
            self.assertIn(needle, self.trades, needle)
        self.assertNotIn("cash_movements", self.code)                                                      # no real cash row is ever written for a trade
        self.assertNotIn("bank_movements", self.code)

    def test_the_stock_ledger_is_one_final_shape_insert_only_with_grams_and_posted_together(self):
        for needle in ("grams_delta DECIMAL(14,3) NOT NULL", "eq21_posted_delta DECIMAL(14,3) NOT NULL", "eq21_analytical DECIMAL(18,6) NOT NULL",
                       "posting_difference DECIMAL(12,6) NOT NULL", "eq21_independent DECIMAL(14,3) DEFAULT NULL", "posting_basis VARCHAR(11) NOT NULL DEFAULT 'INDEPENDENT'",
                       "movement_type VARCHAR(24) NOT NULL", "source_entry_id BIGINT UNSIGNED NOT NULL", "correction_id BIGINT UNSIGNED DEFAULT NULL",
                       "FOREIGN KEY (item_entry_id, item_line_no, item_entry_kind, item_basis) REFERENCES market_trip_book_lines(entry_id, line_no, entry_kind, basis)",
                       "FOREIGN KEY (correction_id) REFERENCES transaction_corrections(id)", "FOREIGN KEY (source_entry_id) REFERENCES market_trip_entries(id)",
                       "item_entry_kind = 'BK_RETURN' AND item_basis = 'FINENESS'", "movement_type IN ('RECEIVE','RECEIVE_REVERSAL','ISSUE','ISSUE_REVERSAL')",
                       "posting_basis IN ('INDEPENDENT','ALLOCATED')",
                       "movement_type = 'RECEIVE' AND grams_delta > 0 AND eq21_posted_delta >= 0 AND correction_id IS NULL AND source_entry_id = item_entry_id",
                       "movement_type = 'RECEIVE_REVERSAL' AND grams_delta < 0 AND eq21_posted_delta <= 0 AND correction_id IS NOT NULL AND source_entry_id = item_entry_id",
                       "movement_type = 'ISSUE' AND grams_delta < 0 AND eq21_posted_delta <= 0 AND correction_id IS NULL AND source_entry_id <> item_entry_id",
                       "movement_type = 'ISSUE_REVERSAL' AND grams_delta > 0 AND eq21_posted_delta >= 0 AND correction_id IS NOT NULL AND source_entry_id <> item_entry_id",
                       "UNIQUE KEY uq_sasm_item_type_source (item_entry_id, item_line_no, movement_type, source_entry_id)",
                       "UNIQUE KEY uq_sasm_correction_item (correction_id, item_entry_id, item_line_no)", "KEY idx_sasm_item (item_entry_id, item_line_no, id)"):
            self.assertIn(needle, self.stock, needle)
        self.assertNotIn("UPDATE shop_assayed_stock_movements", self.code)

    # ---------------------------------------------------------------- field widths (the HOLDING_MARKETER overflow of 055 must never repeat)
    def test_every_constant_fits_the_column_it_is_stored_in(self):
        widths = {"entry_kind": 12, "item_entry_kind": 12, "trip_mode": 8, "accounting_mode": 8, "direction": 3, "basis": 12, "item_basis": 12, "counterparty_kind": 16,
                  "movement_type": 24, "posting_basis": 11}
        for column, width in widths.items():
            self.assertRegex(self.forward, rf"{column} VARCHAR\({width}\)", column)
        values = {"entry_kind": list(EARLIER_KINDS) + list(book_rules.BOOK_KINDS), "trip_mode": [book_rules.LEGACY, book_rules.BOOK], "accounting_mode": [book_rules.LEGACY, book_rules.BOOK],
                  "direction": [book_rules.IN, book_rules.OUT], "basis": list(book_rules.BASES), "counterparty_kind": ["REGISTERED_PARTY", "THIRD_PARTY"],
                  "movement_type": [book_rules.RECEIVE, book_rules.RECEIVE_REVERSAL, book_rules.ISSUE_STOCK, book_rules.ISSUE_REVERSAL],
                  "posting_basis": [book_rules.INDEPENDENT, "ALLOCATED"]}
        for column, constants in values.items():
            for value in constants:
                self.assertLessEqual(len(value), widths[column], f"{value!r} does not fit {column} VARCHAR({widths[column]})")
        self.assertEqual(max(len(k) for k in book_rules.PHASE1_KINDS), 9)                                     # BK_RETURN
        self.assertEqual(max(len(k) for k in book_rules.BOOK_KINDS), 10)                                      # 058: BK_ISS_STK ... still fit VARCHAR(12)

    def test_the_columns_agree_with_the_tables_they_reference(self):
        karats = self.model.tables["karats"]["columns"]["id"]["type"]
        self.assertEqual(self.model.tables["market_trip_book_lines"]["columns"]["karat_id"]["type"], karats)
        self.assertEqual(self.model.tables["market_trip_book_trades"]["columns"]["party_id"]["type"], self.model.tables["suppliers"]["columns"]["id"]["type"])
        self.assertEqual(self.model.tables["market_trip_book_lines"]["columns"]["scrap_movement_id"]["type"], self.model.tables["old_gold_movements"]["columns"]["id"]["type"])
        self.assertEqual(self.model.tables["shop_assayed_stock_movements"]["columns"]["correction_id"]["type"],
                         self.model.tables["transaction_corrections"]["columns"]["id"]["type"])

    def test_the_posted_figures_are_wide_enough_for_the_largest_weight(self):
        self.assertGreaterEqual(10 ** (14 - 3), 10 ** 9)                                                      # weight_grams DECIMAL(14,3) holds the service maximum (1e9 g)
        self.assertGreaterEqual(10 ** (18 - 6), 10 ** 9 * 24 // 21)                                           # eq21_analytical DECIMAL(18,6) holds a 24K equivalent of that weight

    # ---------------------------------------------------------------- rollback
    def test_the_rollback_refuses_when_any_book_history_exists_and_never_deletes_business_rows(self):
        guard = re.search(r"SET @masgold_sql=IF\((.*?)\n 'SELECT \* FROM ROLLBACK_056_REFUSED_book_history_or_later_migration'", self.rollback, re.S).group(1)
        for needle in ("accounting_mode<>'LEGACY'", "trip_mode<>'LEGACY' OR entry_kind IN ('BK_ISSUE','BK_BUY','BK_SELL','BK_RETURN')", "FROM market_trip_book_lines",
                       "FROM market_trip_book_trades", "FROM shop_assayed_stock_movements", "version>56"):
            self.assertIn(needle, guard)
        self.assertLess(self.rollback.index("ROLLBACK_056_REFUSED"), self.rollback.index("DROP TABLE"))      # the refusal comes before anything is dropped
        self.assertEqual(re.findall(r"DELETE FROM (\w+)", self.rollback), ["schema_migrations"])
        for statement in ("DROP TABLE IF EXISTS shop_assayed_stock_movements", "DROP TABLE IF EXISTS market_trip_book_trades", "DROP TABLE IF EXISTS market_trip_book_lines",
                          "DROP COLUMN accounting_mode", "DROP COLUMN trip_mode", "DROP FOREIGN KEY fk_mte_trip_mode", "DROP FOREIGN KEY fk_mteg_mode"):
            self.assertIn(statement, self.rollback)
        kinds = re.search(r"ADD CONSTRAINT chk_mte_kind CHECK \(entry_kind IN \(([^)]*)\)\)", self.rollback).group(1)
        self.assertEqual(re.findall(r"'(\w+)'", kinds), list(EARLIER_KINDS))                              # the 055 vocabulary is restored exactly


if __name__ == "__main__":
    unittest.main()
