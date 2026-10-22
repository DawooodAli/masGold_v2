"""Migration 057 (BOOK reconciliation document): STATIC checks of the SQL text, the manifest, the rollback and the schema level. No database is contacted and nothing is executed.

`SqlCheckEvaluator` evaluates the TEXT of the numeric CHECK constraint (`chk_mtr_balance`) with Python `Decimal` arithmetic, so the expression as WRITTEN is proved to accept the
figures the service produces and to reject every perturbation of an identity. That is a check of the mathematics of the SQL text; it is NOT MySQL's executor (operator
precedence, DECIMAL promotion, collations, REGEXP_LIKE and JSON_TYPE inside a CHECK stay for the MySQL verification gate)."""
import ast
from decimal import Decimal as D
import hashlib
from pathlib import Path
import random
import re
import unittest

from services import book_rules as br
from services import book_settlement as bs
from services.schema_level import REQUIRED_SCHEMA_LEVEL
from tests.test_book_settlement_rules import DatedSrc
from tests.test_book_reconciliation_rules import K
from tools.schema_rehearsal.chain import LAST, SCHEMA_DIR, load_chain, sha256_of

ROOT = Path(__file__).resolve().parents[1]
FORWARD = (SCHEMA_DIR / "057_market_trip_reconciliation.sql").read_text(encoding="utf-8")
ROLLBACK = (SCHEMA_DIR / "rollback" / "057_market_trip_reconciliation_rollback.sql").read_text(encoding="utf-8")
BOOK_CORE = (SCHEMA_DIR / "056_market_trip_book_core.sql").read_text(encoding="utf-8")
BOOK_CORE_ROLLBACK = (SCHEMA_DIR / "rollback" / "056_market_trip_book_core_rollback.sql").read_text(encoding="utf-8")
TABLE = FORWARD[FORWARD.index("CREATE TABLE IF NOT EXISTS market_trip_reconciliations"):FORWARD.index("INSERT INTO schema_migrations")]
KINDS_056 = re.search(r"CHECK \(entry_kind IN \(([^)]*'BK_RETURN')\)\)", BOOK_CORE).group(1)


def columns(sql=TABLE):
    out = {}
    for line in sql.splitlines():
        match = re.match(r" ([a-z_0-9]+) ((?:BIGINT|SMALLINT|INT|VARCHAR|CHAR|DECIMAL|DATE|DATETIME|JSON).*?),?\s*$", line)
        if match and match.group(1) not in ("PRIMARY",):
            out[match.group(1)] = match.group(2)
    return out


class SqlCheckEvaluator:
    """Evaluate one CHECK body written with AND, ABS(), comparison operators, + - * and decimal literals, with Decimal arithmetic."""

    def __init__(self, body):
        text = " ".join(body.split())
        text = re.sub(r"\bAND\b", " and ", text)
        text = text.replace("ABS(", "abs(")
        text = re.sub(r"(?<![<>!=])=(?!=)", "==", text)
        tree = ast.parse(text, mode="eval")

        class Literals(ast.NodeTransformer):
            def visit_Constant(self, node):
                if isinstance(node.value, float):
                    raise AssertionError("a float literal reached the evaluator")
                return node

        # decimal literals are rewritten from their SOURCE text so no float is ever created
        def literal(match):
            return f"D('{match.group(0)}')" if "." in match.group(0) else match.group(0)
        tree = ast.parse(re.sub(r"(?<![A-Za-z_'])\d+\.\d+", literal, text), mode="eval")
        self.code = compile(Literals().visit(tree), "<check>", "eval")

    def __call__(self, row):
        return bool(eval(self.code, {"abs": abs, "D": D, "__builtins__": {}}, dict(row)))


def check_body(name):
    return re.search(rf"CONSTRAINT {name} CHECK \((.*?)\),?\n(?: CONSTRAINT|\) ENGINE)", TABLE, re.DOTALL).group(1)


def row_of(settlement):
    return {"a_star_units": settlement.a_star_units, "a_star": bs.a_star_evidence(settlement), "b_star": bs.milli_decimal(settlement.b_star_milli),
            "loss_posted": bs.milli_decimal(settlement.loss_milli), "rounding_settlement": bs.milli_decimal(settlement.rounding_milli), "delta_units": settlement.delta_units,
            "rho_units": settlement.rho_units, "sum_abs_units": settlement.sum_abs_units}


class ForwardMigrationTests(unittest.TestCase):
    def test_it_is_the_next_file_of_the_chain_and_the_manifest_matches(self):
        files = load_chain(require_committed=False)
        self.assertEqual((len(files), LAST, files[56].name), (73, 73, "057_market_trip_reconciliation.sql"))      # 058 and 059 follow it (Phase 2)
        self.assertEqual(files[56].sha256, sha256_of((SCHEMA_DIR / files[56].name).read_bytes()))
        manifest = (SCHEMA_DIR / "SHA256SUMS").read_text(encoding="utf-8").splitlines()
        self.assertEqual(manifest[56], f"{hashlib.sha256(FORWARD.replace(chr(13) + chr(10), chr(10)).encode()).hexdigest()}  057_market_trip_reconciliation.sql")
        self.assertEqual(REQUIRED_SCHEMA_LEVEL, 73)

    def test_it_refuses_without_056_and_every_step_is_guarded(self):
        self.assertIn("version=56", FORWARD)
        self.assertIn("MIGRATION_057_REFUSED_missing_056", FORWARD)
        for statement in re.findall(r"SET @masgold_sql := (IF\(.*?'DO 0'\));", FORWARD, re.DOTALL):
            self.assertRegex(statement, r"IF\((NOT )?EXISTS")
        self.assertIn("CREATE TABLE IF NOT EXISTS market_trip_reconciliations", FORWARD)
        self.assertRegex(FORWARD, r"VALUES \(57,'[^']+'\)\s+ON DUPLICATE KEY UPDATE name=VALUES\(name\);")
        statements = "\n".join(line for line in FORWARD.splitlines() if not line.lstrip().startswith("--"))
        self.assertNotRegex(statements, r"(?im)^\s*(DELETE|UPDATE|TRUNCATE)\b")                  # no statement rewrites or removes history
        for forbidden in ("DROP TABLE", "DROP COLUMN", "CREATE TRIGGER", "DELIMITER", "CREATE VIEW"):
            self.assertNotIn(forbidden, statements.upper())

    def test_the_entry_kind_checks_gain_exactly_bk_recon_in_the_book_branch(self):
        self.assertIn(f"CHECK (entry_kind IN ('ISSUE'", FORWARD)
        new_kinds = KINDS_056 + ",'BK_RECON'"
        self.assertIn(f"CHECK (entry_kind IN ({new_kinds}))", FORWARD)
        legacy_branch = re.search(r"trip_mode = 'LEGACY' AND entry_kind IN \(([^)]*)\)", FORWARD).group(1)
        book_branch = re.search(r"trip_mode = 'BOOK' AND entry_kind IN \(([^)]*)\)", FORWARD).group(1)
        old_legacy = re.search(r"trip_mode = 'LEGACY' AND entry_kind IN \(([^)]*)\)", BOOK_CORE).group(1)
        old_book = re.search(r"trip_mode = 'BOOK' AND entry_kind IN \(([^)]*)\)", BOOK_CORE).group(1)
        self.assertEqual(legacy_branch, old_legacy)                                          # LEGACY trips gain nothing
        self.assertEqual(book_branch, old_book + ",'BK_RECON'")
        self.assertNotIn("BK_RECON", legacy_branch)

    def test_the_guards_detect_the_old_definitions_by_the_missing_kind(self):
        self.assertEqual(len(re.findall(r"CHECK_CLAUSE NOT LIKE '%BK_RECON%'", FORWARD)), 2)

    def test_the_book_kinds_set_of_the_rules_stays_the_four_gold_kinds(self):
        self.assertEqual(br.PHASE1_KINDS, (br.BK_ISSUE, br.BK_BUY, br.BK_SELL, br.BK_RETURN))      # BK_RECON is no gold kind: no line, leg, trade or stock row
        self.assertNotIn(br.BK_RECON, br.BOOK_KINDS)
        self.assertTrue(br.kind_allowed_in_mode(br.BK_RECON, br.BOOK))
        self.assertFalse(br.kind_allowed_in_mode(br.BK_RECON, br.LEGACY))
        with self.assertRaises(br.BookError):
            br.require_kind_in_mode(br.BK_RECON, br.LEGACY)

    def test_a_reconciliation_has_no_book_line_no_new_line_table_and_no_other_new_table(self):
        self.assertEqual(re.findall(r"CREATE TABLE IF NOT EXISTS (\w+)", FORWARD), ["market_trip_reconciliations"])
        self.assertNotRegex(FORWARD, r"ALTER TABLE market_trip_book_lines")                          # nothing here touches the line table: BK_RECON has no physical book line


class TableShapeTests(unittest.TestCase):
    def test_every_column_is_not_null_except_the_reason_and_the_note(self):
        found = columns()
        self.assertEqual(set(found), {"entry_id", "entry_kind", "trip_id", "trip_mode", "generation", "algorithm", "source_algorithm", "snapshot_format", "effective_date",
                                      "date_floor", "date_floor_source", "unit_denominator", "a_star_units", "a_star", "b_star", "loss_posted", "rounding_settlement",
                                      "delta_units", "rho_units", "sum_abs_units", "entry_count", "line_count", "reversed_count", "source_digest", "snapshot_digest",
                                      "snapshot", "reason_code", "note", "confirmed_by_user_id", "confirmed_at", "created_at"})
        nullable = {name for name, definition in found.items() if "NOT NULL" not in definition}
        self.assertEqual(nullable, {"reason_code", "note"})
        for name in ("reason_code", "note"):
            self.assertIn("DEFAULT NULL", found[name])
        self.assertEqual(sorted(name for name, definition in found.items() if "DEFAULT" in definition and "NOT NULL" in definition),
                         ["created_at", "entry_kind", "trip_mode"])                          # nothing else relies on a DEFAULT to satisfy NOT NULL

    def test_types_and_capacities(self):
        found = columns()
        expected = {"generation": "SMALLINT UNSIGNED", "algorithm": "VARCHAR(24)", "source_algorithm": "VARCHAR(24)", "snapshot_format": "VARCHAR(24)", "a_star_units": "DECIMAL(30,0)",
                    "a_star": "DECIMAL(21,6)", "b_star": "DECIMAL(18,3)", "loss_posted": "DECIMAL(18,3)", "rounding_settlement": "DECIMAL(18,3)", "delta_units": "DECIMAL(30,0)",
                    "rho_units": "DECIMAL(30,0)", "sum_abs_units": "DECIMAL(30,0)", "entry_count": "INT UNSIGNED", "line_count": "INT UNSIGNED", "reversed_count": "INT UNSIGNED",
                    "unit_denominator": "INT UNSIGNED", "note": "VARCHAR(1000)", "reason_code": "VARCHAR(24)", "snapshot": "JSON"}
        for name, type_ in expected.items():
            self.assertTrue(found[name].startswith(type_), (name, found[name]))
        self.assertEqual(max(len(v) for v in (bs.RECON_ALGORITHM, bs.SOURCE_ALGORITHM, bs.SNAPSHOT_FORMAT)), 22)       # fits VARCHAR(24)
        self.assertEqual(max(len(code) for code in bs.REASONS), 17)                                                     # fits VARCHAR(24)
        self.assertEqual(bs.GENERATION_LIMIT, 2 ** 16 - 1)
        self.assertEqual(bs.COUNTER_LIMIT, 2 ** 32 - 1)
        self.assertLess(bs.GRAM_LIMIT * bs.UNITS_PER_GRAM, 10 ** 30)

    def test_the_digest_columns_are_binary_ascii_and_the_check_is_case_sensitive(self):
        found = columns()
        for name in ("source_digest", "snapshot_digest"):
            self.assertIn("CHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL", found[name])
        body = check_body("chk_mtr_digests")
        self.assertEqual(len(re.findall(r"REGEXP_LIKE\(\w+, '\^\[0-9a-f\]\{64\}\$', 'c'\)", body)), 2)
        self.assertNotIn("LOWER(", TABLE)                                                  # `col = LOWER(col)` proves nothing under a case-insensitive collation
        self.assertNotRegex(TABLE, r"REGEXP '")

    def test_keys(self):
        self.assertIn("PRIMARY KEY (entry_id)", TABLE)
        self.assertIn("UNIQUE KEY uq_mtr_trip_generation (trip_id, generation)", TABLE)
        self.assertEqual(len(re.findall(r"UNIQUE KEY", TABLE)), 1)                         # no UNIQUE(trip_id): a reversed reconciliation must not block a new generation
        self.assertNotRegex(TABLE, r"UNIQUE KEY \w+ \(trip_id\)")
        self.assertIn("FOREIGN KEY (entry_id, entry_kind, trip_id) REFERENCES market_trip_entries(id, entry_kind, trip_id)", TABLE)
        self.assertIn("FOREIGN KEY (entry_id, trip_mode) REFERENCES market_trip_entries(id, trip_mode) ON UPDATE RESTRICT ON DELETE RESTRICT", TABLE)
        self.assertIn("FOREIGN KEY (confirmed_by_user_id) REFERENCES users(id)", TABLE)
        for name in ("chk_mtr_head", "chk_mtr_digests", "chk_mtr_date", "chk_mtr_counts", "chk_mtr_balance", "chk_mtr_reason"):
            self.assertIn(f"CONSTRAINT {name} CHECK", TABLE)

    def test_the_head_check_pins_kind_mode_versions_and_denominator(self):
        body = check_body("chk_mtr_head")
        for text in ("entry_kind = 'BK_RECON'", "trip_mode = 'BOOK'", "generation >= 1", "algorithm = 'book-recon-v1'", "source_algorithm = 'book-recon-source-v1'",
                     "snapshot_format = 'book-recon-snapshot-v1'", "unit_denominator = 26250000", "JSON_TYPE(snapshot) = 'OBJECT'"):
            self.assertIn(text, body)
        self.assertEqual((bs.RECON_ALGORITHM, bs.SOURCE_ALGORITHM, bs.SNAPSHOT_FORMAT, bs.UNITS_PER_GRAM), ("book-recon-v1", "book-recon-source-v1", "book-recon-snapshot-v1", 26250000))

    def test_the_date_check_ties_the_window_to_the_confirmation_date(self):
        body = check_body("chk_mtr_date")
        for text in ("date_floor_source IN ('LAST_GOLD_ENTRY','TRIP_CREATED')", "date_floor <= effective_date", "effective_date <= DATE(confirmed_at)"):
            self.assertIn(text, body)
        self.assertEqual((bs.FLOOR_LAST_GOLD_ENTRY, bs.FLOOR_TRIP_CREATED), ("LAST_GOLD_ENTRY", "TRIP_CREATED"))

    def test_the_reason_check_matches_the_policy(self):
        body = check_body("chk_mtr_reason")
        self.assertIn("reason_code IS NULL AND loss_posted = 0", body)                     # a reason may be missing ONLY while the posted loss is zero
        self.assertIn("reason_code IN ('OPERATING_LOSS','PURITY_DIFFERENCE','TRADING_LOSS')", body)
        self.assertIn("reason_code IN ('OTHER','UNEXPLAINED') AND note IS NOT NULL AND CHAR_LENGTH(TRIM(note)) > 0", body)
        self.assertEqual(set(re.findall(r"'([A-Z_]+)'", body)), set(bs.REASONS))
        self.assertEqual(bs.NOTE_REQUIRED_REASONS, ("OTHER", "UNEXPLAINED"))

    def test_the_balance_check_uses_no_division_and_no_tolerance_except_the_stated_bounds(self):
        body = check_body("chk_mtr_balance")
        self.assertNotIn("/", body)
        self.assertNotRegex(body, r"\bROUND\(|\bFLOOR\(|\bCEIL")
        self.assertEqual(sorted(set(re.findall(r"\d+\.\d+", body))), ["13.125"])           # the ONLY non-integer literal: the strict evidence bound of the six-decimal column


class BalanceCheckTextTests(unittest.TestCase):
    """The mathematics of the written CHECK against the figures the service computes (a Python evaluation of the SQL TEXT, not MySQL)."""
    check = SqlCheckEvaluator(check_body("chk_mtr_balance"))

    def sources(self):
        rng = random.Random(570)
        out = []
        for _ in range(300):
            s = DatedSrc()
            s.add(br.BK_ISSUE, [(f"{rng.randint(1, 300)}.{rng.randint(0, 999):03d}", K, rng.choice((18, 21, 22, 24))) for _ in range(rng.randint(1, 3))])
            for _ in range(rng.randint(0, 3)):
                s.add(br.BK_BUY, [(f"{rng.randint(0, 30)}.{rng.randint(1, 999):03d}", "F", str(D(rng.randint(10, 10_000)).scaleb(-1)))], prices=["10.00"])
            for _ in range(rng.randint(0, 3)):
                kind = rng.choice((br.BK_RETURN, br.BK_SELL))
                row = (f"{rng.randint(0, 2)}.{rng.randint(1, 999):03d}", K, rng.choice((18, 21, 22, 24)))
                s.add(kind, [row], prices=None if kind == br.BK_RETURN else ["10.00"])
            preview = s.preview()
            if preview.trustworthy and preview.a_state in ("zero", "positive_below_posting_precision", "positive"):
                out.append(s.built()[2])
        return out

    def test_every_computed_settlement_satisfies_the_written_check(self):
        settlements = self.sources()
        self.assertGreater(len(settlements), 100)
        for st in settlements:
            self.assertTrue(self.check(row_of(st)), st)

    def test_the_boundary_cases_satisfy_it(self):
        for units in (0, 3000, 13124, 13125, 22500, 23625, 26249, 26250, 39374, 39375):
            for b_milli in (0, 1, 5):
                st = bs.Settlement(units + 50_000, 50_000, b_milli, 0, 10 ** 9, 1, 1, 0)
                if st.a_star_units == units:
                    self.assertTrue(self.check(row_of(st)), (units, b_milli))

    def test_every_perturbation_of_an_identity_is_rejected(self):
        for st in self.sources()[:60]:
            good = row_of(st)
            self.assertTrue(self.check(good))
            perturbed = {"a_star_units +1": {"a_star_units": good["a_star_units"] + 1}, "delta +1": {"delta_units": good["delta_units"] + 1},
                         "rho +1": {"rho_units": good["rho_units"] + 1}, "loss +0.001": {"loss_posted": good["loss_posted"] + D("0.001")},
                         "rounding +0.001": {"rounding_settlement": good["rounding_settlement"] + D("0.001")}, "b +0.001": {"b_star": good["b_star"] + D("0.001")},
                         "negative A": {"a_star_units": -1}, "a_star evidence off": {"a_star": good["a_star"] + D("0.000001") * 14},
                         "bound too small": {"sum_abs_units": max(0, abs(int(good["rounding_settlement"] * 1000)) * 26250 - 13125 - 1)}}
            for name, change in perturbed.items():
                row = {**good, **change}
                if name == "bound too small" and abs(int(good["rounding_settlement"] * 1000)) * 26250 <= 13125:
                    continue                                                               # the bound only bites when the rounding settlement is large
                self.assertFalse(self.check(row), (name, good))

    def test_the_loss_is_round_half_up_on_the_lattice(self):
        for units in range(0, 80_000, 7):
            loss = D(bs.loss_milli_of(units)).scaleb(-3)
            st = bs.Settlement(units, 0, int(loss * 1000), 0, 0, 1, 1, 0)
            good = row_of(bs.Settlement(units, 0, int(loss * 1000), 0, 10 ** 9, 1, 1, 0))
            self.assertTrue(self.check(good), units)
            for wrong in (loss + D("0.001"), loss - D("0.001")):
                if wrong >= 0:
                    row = {**good, "loss_posted": wrong, "rounding_settlement": good["b_star"] - wrong, "delta_units": int(wrong * 1000) * 26250 - units}
                    self.assertFalse(self.check(row), (units, wrong))


class RollbackTests(unittest.TestCase):
    def test_it_refuses_on_any_reconciliation_history_and_on_a_later_migration_before_anything_changes(self):
        guard = ROLLBACK[:ROLLBACK.index("PREPARE masgold_stmt")]
        self.assertIn("EXISTS(SELECT 1 FROM market_trip_reconciliations)", guard)
        self.assertIn("EXISTS(SELECT 1 FROM market_trip_entries WHERE entry_kind='BK_RECON')", guard)            # a reversed reconciliation is still a BK_RECON entry
        self.assertIn("EXISTS(SELECT 1 FROM schema_migrations WHERE version>57)", guard)
        self.assertIn("ROLLBACK_057_REFUSED_reconciliation_history_or_later_migration", guard)
        self.assertEqual(ROLLBACK.index("DROP TABLE"), ROLLBACK.index("DROP TABLE IF EXISTS market_trip_reconciliations"))
        self.assertGreater(ROLLBACK.index("DROP TABLE"), ROLLBACK.index("EXECUTE masgold_stmt"))                 # nothing is dropped before the guard ran

    def test_it_never_deletes_history_to_pass_and_removes_only_the_marker(self):
        deletes = re.findall(r"DELETE FROM (\w+)", ROLLBACK)
        self.assertEqual(deletes, ["schema_migrations"])
        self.assertIn("DELETE FROM schema_migrations WHERE version=57;", ROLLBACK)
        for forbidden in ("TRUNCATE", "UPDATE market", "DELETE FROM market_trip", "DELETE FROM transaction_corrections"):
            self.assertNotIn(forbidden, ROLLBACK)

    def test_it_restores_the_056_definitions_exactly(self):
        self.assertIn(f"ADD CONSTRAINT chk_mte_kind CHECK (entry_kind IN ({KINDS_056}));", ROLLBACK)
        legacy = re.search(r"trip_mode = 'LEGACY' AND entry_kind IN \(([^)]*)\)", BOOK_CORE).group(1)
        book = re.search(r"trip_mode = 'BOOK' AND entry_kind IN \(([^)]*)\)", BOOK_CORE).group(1)
        self.assertIn(f"CHECK ((trip_mode = 'LEGACY' AND entry_kind IN ({legacy})) OR (trip_mode = 'BOOK' AND entry_kind IN ({book})));", ROLLBACK)
        self.assertNotIn("BK_RECON", ROLLBACK.split("DROP TABLE")[1].replace("ROLLBACK_057", ""))
        self.assertIn("DROP CHECK chk_mte_mode_kind", ROLLBACK)

    def test_the_056_rollback_is_unchanged_in_meaning_and_still_covers_its_own_history(self):
        self.assertIn("version>56", BOOK_CORE_ROLLBACK)                                       # 056 rolls back only while no later migration exists (057 must go first)


if __name__ == "__main__":
    unittest.main()
