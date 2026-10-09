"""Safety guards of tools/schema_rehearsal. No MySQL server is used: mysql.exe
and git are replaced by in-memory fakes, and all databases here are synthetic."""
from contextlib import redirect_stderr, redirect_stdout
import io
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock

from tools.schema_rehearsal import chain, guards, rehearsal
from tools.schema_rehearsal.__main__ import main
from tools.schema_rehearsal.chain import ChainFile
from tools.schema_rehearsal.guards import RehearsalRefused
from tools.schema_rehearsal.mysql_client import Credentials, MysqlClient, MysqlError, option_file, option_file_text


TOOL_DIR = Path(chain.__file__).parent
TARGET = "masgold_rehearsal"


def refused(test, code):
    return test.assertRaisesRegex(RehearsalRefused, f"^{code}:")


class TargetTests(unittest.TestCase):
    def test_only_the_rehearsal_database_is_accepted(self):
        self.assertEqual(guards.validate_target(TARGET), TARGET)

    def test_protected_and_system_schemas_are_refused(self):
        for name in ("masgold_v2", "masgold_prod", "golddb", "masgold", "maindb", "shippingdb", "mysql", "sys",
                     "information_schema", "performance_schema", "MASGOLD_V2", "GoldDB", "masgold_prod_copy"):
            with self.subTest(name=name), refused(self, "TARGET_PROTECTED"):
                guards.validate_target(name)

    def test_anything_else_is_refused(self):
        for name in ("masgold_rehearsal2", "masgold_rehearsal ", "MASGOLD_REHEARSAL", "`masgold_rehearsal`",
                     "masgold_rehearsal;", "test", "masgold_v3"):
            with self.subTest(name=name), refused(self, "TARGET_NOT_ALLOWED"):
                guards.validate_target(name)
        for name in ("", None):
            with self.subTest(name=name), refused(self, "TARGET_REQUIRED"):
                guards.validate_target(name)


class SqlPreparationTests(unittest.TestCase):
    def test_real_chain_strips_exactly_one_use_from_001_to_011(self):
        for chain_file in chain.load_chain(require_committed=False):
            raw = chain_file.path.read_bytes().decode("utf-8")
            with self.subTest(file=chain_file.name):
                self.assertEqual(len(chain_file.sql), len(raw))            # offsets preserved
                self.assertNotIn("USE masgold_v2", chain_file.sql)
                if int(chain_file.name[:3]) <= 11:
                    self.assertEqual(raw.count("USE masgold_v2;"), 1)
                    self.assertEqual(chain_file.sql, raw.replace("USE masgold_v2;", " " * 15, 1))
                else:
                    self.assertEqual(chain_file.sql, raw)

    def test_use_rules(self):
        cases = [("001_x.sql", "CREATE TABLE t (id INT);", "USE_MISSING"),
                 ("001_x.sql", "use masgold_v2;\nCREATE TABLE t (id INT);", "USE_MISSING"),
                 ("001_x.sql", "USE golddb;\nCREATE TABLE t (id INT);", "USE_MISSING"),
                 ("001_x.sql", "USE masgold_v2;\nUSE masgold_v2;", "USE_STATEMENT"),
                 ("003_x.sql", "USE masgold_v2;\nCREATE TABLE t (id INT);\nUSE golddb;", "USE_STATEMENT"),
                 ("012_x.sql", "USE masgold_v2;\nCREATE TABLE t (id INT);", "USE_STATEMENT"),
                 ("014_x.sql", "SET @s := 'USE golddb'; PREPARE s FROM @s;", "USE_STATEMENT"),
                 ("014_x.sql", 'SET @s := IF(1, "USE `masgold_v2`", \'DO 0\');', "USE_STATEMENT")]
        for name, text, code in cases:
            with self.subTest(text=text), refused(self, code):
                guards.prepare_sql(name, text)

    def test_use_words_in_data_and_comments_are_not_statements(self):
        text = ("-- USE golddb;\nUSE masgold_v2;\n/* use golddb; */\n"
                "INSERT INTO permissions (code, name) VALUES ('INVOICE_CLONE', 'Use Billing');")
        self.assertIn("'Use Billing'", guards.prepare_sql("006_x.sql", text))

    def test_cross_database_references_are_refused(self):
        for text in ("SELECT * FROM golddb.cash;", "SELECT * FROM `masgold_v2`.`customers`;",
                     "INSERT INTO masgold_prod . t VALUES (1);", "SELECT * FROM mysql.user;",
                     "SELECT * FROM sys.version;", "SELECT * FROM masgold_rehearsal.t;",
                     "SELECT * FROM performance_schema.threads;",
                     'SET @s := IF(1, "ALTER TABLE golddb.cash ADD COLUMN c INT", \'DO 0\');',
                     "SET @s := 'DROP TABLE maindb.t';"):
            with self.subTest(text=text), refused(self, "CROSS_DATABASE"):
                guards.prepare_sql("013_x.sql", text)

    def test_allowed_references(self):
        text = ("-- see golddb.cash in the dump\n"
                "SELECT k.id, m.karat_id FROM karats k JOIN old_gold_movements m ON m.karat_id = k.id;\n"
                "SET @s := IF(NOT EXISTS (SELECT 1 FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = DATABASE()),"
                " \"ALTER TABLE t ADD COLUMN c INT\", 'DO 0');")
        guards.prepare_sql("013_x.sql", text)

    def test_server_level_statements_are_refused(self):
        cases = [("CREATE DATABASE x;", "DATABASE_DDL"), ("DROP SCHEMA masgold_rehearsal;", "DATABASE_DDL"),
                 ("CREATE USER u IDENTIFIED BY 'p';", "ACCOUNT_DDL"),
                 ("GRANT ALL ON *.* TO u;", "GRANT"), ("REVOKE SELECT ON t FROM u;", "REVOKE"),
                 ("SET GLOBAL sql_mode = '';", "GLOBAL_SETTING"), ("SET PERSIST x = 1;", "GLOBAL_SETTING"),
                 ("LOAD DATA INFILE 'x' INTO TABLE t;", "LOAD_DATA"),
                 ("SELECT * FROM t INTO OUTFILE 'x';", "FILE_WRITE"),
                 ("INSTALL PLUGIN p SONAME 'p.dll';", "PLUGIN"), ("SHUTDOWN;", "SERVER_CONTROL"),
                 ('SET @s := IF(1, "CREATE DATABASE other", \'DO 0\');', "DATABASE_DDL")]
        for text, code in cases:
            with self.subTest(text=text), refused(self, code):
                guards.prepare_sql("014_x.sql", text)

    def test_mysql_client_commands_are_refused(self):
        for text in ("\\! del x\n", "SELECT 1\\G", "source other.sql;", "system dir;", "connect golddb;",
                     "delimiter $$\n"):
            with self.subTest(text=text), self.assertRaisesRegex(RehearsalRefused, "^(CLIENT_COMMAND|CROSS)"):
                guards.prepare_sql("014_x.sql", text)


class ChainAndManifestTests(unittest.TestCase):
    def copy_chain(self):
        directory = Path(tempfile.mkdtemp(prefix="masgold-chain-test-"))
        self.addCleanup(shutil.rmtree, directory, True)
        for name in chain.chain_names():
            shutil.copyfile(chain.SCHEMA_DIR / name, directory / name)
        manifest = directory / "SHA256SUMS"
        manifest.write_text(chain.manifest_text(directory), encoding="utf-8")
        return directory, manifest

    def test_committed_manifest_matches_the_chain(self):
        self.assertEqual(chain.MANIFEST.read_text(encoding="utf-8"), chain.manifest_text())
        files = chain.load_chain(require_committed=False)
        self.assertEqual([f.name for f in files], chain.chain_names())
        self.assertEqual([int(f.name[:3]) for f in files], list(range(1, 39)))

    def test_changed_file_is_refused(self):
        directory, manifest = self.copy_chain()
        path = directory / "014_billing_v2.sql"
        path.write_bytes(path.read_bytes() + b"\n-- edited\n")
        with refused(self, "SHA256_MISMATCH"):
            chain.load_chain(directory, manifest, require_committed=False)

    def test_line_endings_do_not_change_the_hash(self):
        self.assertEqual(chain.sha256_of(b"a\r\nb\r\n"), chain.sha256_of(b"a\nb\n"))

    def test_missing_or_extra_files_are_refused(self):
        directory, manifest = self.copy_chain()
        (directory / "018_extra.sql").write_text("SELECT 1;", encoding="utf-8")
        with refused(self, "CHAIN_INCOMPLETE"):
            chain.load_chain(directory, manifest, require_committed=False)
        (directory / "018_extra.sql").unlink()
        (directory / "009_audit_printing.sql").unlink()
        with refused(self, "CHAIN_INCOMPLETE"):
            chain.load_chain(directory, manifest, require_committed=False)

    def test_manifest_must_list_exactly_the_chain(self):
        directory, manifest = self.copy_chain()
        lines = manifest.read_text(encoding="utf-8").splitlines()
        manifest.write_text("\n".join(lines[:-1]) + "\n", encoding="utf-8")
        with refused(self, "MANIFEST_MISMATCH"):
            chain.load_chain(directory, manifest, require_committed=False)
        manifest.write_text("\n".join(lines) + "\nnot a manifest line\n", encoding="utf-8")
        with refused(self, "MANIFEST_INVALID"):
            chain.load_chain(directory, manifest, require_committed=False)
        manifest.unlink()
        with refused(self, "MANIFEST_MISSING"):
            chain.load_chain(directory, manifest, require_committed=False)

    def git(self, results):
        calls = []

        def runner(args, capture_output):
            calls.append(args)
            return subprocess.CompletedProcess(args, results.get(args[3], 0), b"", b"")
        return runner, calls

    def test_files_must_be_committed_and_unchanged(self):
        paths = [chain.SCHEMA_DIR / "001_foundation.sql"]
        runner, calls = self.git({})
        chain.verify_committed(paths, runner=runner)
        self.assertEqual([c[3:] for c in calls], [
            ["ls-files", "--error-unmatch", "--", "database/schema/001_foundation.sql"],
            ["diff", "--quiet", "HEAD", "--", "database/schema/001_foundation.sql"]])
        for failing in ("ls-files", "diff"):
            runner, _ = self.git({failing: 1})
            with self.subTest(failing=failing), refused(self, "NOT_COMMITTED"):
                chain.verify_committed(paths, runner=runner)

    def test_load_chain_checks_manifest_and_every_file_against_git(self):
        runner, calls = self.git({})
        chain.load_chain(runner=runner)
        checked = {c[-1] for c in calls if c[3] == "diff"}
        self.assertEqual(checked, {"database/schema/SHA256SUMS"} |
                         {f"database/schema/{n}" for n in chain.chain_names()})


class FakeRunner:
    """Records mysql.exe invocations."""

    def __init__(self, returncode=0, stdout=b"", stderr=b""):
        self.calls, self.returncode, self.stdout, self.stderr = [], returncode, stdout, stderr

    def __call__(self, args, input, capture_output):
        option_path = Path(args[1].split("=", 1)[1])
        self.calls.append({"args": args, "input": input, "options": option_path.read_text(encoding="utf-8")})
        return subprocess.CompletedProcess(args, self.returncode, self.stdout, self.stderr)


class MysqlClientTests(unittest.TestCase):
    CREDENTIALS = Credentials("127.0.0.1", 3306, "masgold_build", 'p"a\\ss wörd')

    def test_password_only_in_temporary_option_file(self):
        runner = FakeRunner(stdout=b"masgold_rehearsal\n")
        with option_file(self.CREDENTIALS) as path:
            directory = path.parent
            MysqlClient("mysql.exe", path, runner).query("SELECT DATABASE();", database=TARGET)
        call = runner.calls[0]
        self.assertTrue(call["args"][1].startswith("--defaults-file="))       # first option: only this file
        self.assertNotIn("p\"a", " ".join(call["args"]))
        self.assertFalse(any(a.startswith(("--password", "-p")) for a in call["args"]))
        self.assertIn("--database=masgold_rehearsal", call["args"])
        self.assertIn("--binary-mode", call["args"])
        self.assertIn('password="p\\"a\\\\ss wörd"', call["options"])
        self.assertNotIn(b"ss w", call["input"])
        self.assertFalse(directory.exists())                                  # deleted afterwards
        self.assertNotIn("wörd", repr(self.CREDENTIALS))

    def test_option_file_is_removed_after_failure(self):
        with self.assertRaises(RuntimeError):
            with option_file(self.CREDENTIALS) as path:
                raise RuntimeError("boom")
        self.assertFalse(path.parent.exists())

    def test_line_breaks_in_credentials_are_refused(self):
        with self.assertRaises(ValueError):
            option_file_text(Credentials("h", 1, "u", "a\nsocket=/x"))

    def test_mysql_errors_are_raised(self):
        runner = FakeRunner(returncode=1, stderr=b"ERROR 1146 (42S02) at line 3: Table x doesn't exist")
        with option_file(self.CREDENTIALS) as path, self.assertRaisesRegex(MysqlError, "ERROR 1146"):
            MysqlClient("mysql.exe", path, runner).run_script("SELECT 1;", TARGET)


class FakeServer:
    """In-memory stand-in for mysql.exe at the query level."""

    def __init__(self, grants=None, protected_rows=None, target_state="MISSING"):
        self.grants = grants or ["GRANT USAGE ON *.* TO `masgold_build`@`localhost`",
                                 "GRANT ALL PRIVILEGES ON `masgold_rehearsal`.* TO `masgold_build`@`localhost`"]
        self.protected_rows = protected_rows or []
        self.exists = target_state != "MISSING"
        self.tables = {"EMPTY": 0, "MARKED": 5, "FOREIGN": 3}.get(target_state, 0)
        self.marker = TARGET if target_state == "MARKED" else None
        self.session_database = None
        self.log, self.applied = [], []
        self.after_script = None

    def query(self, sql, database=None):
        self.log.append(("query", database, sql))
        if sql == "SELECT CURRENT_USER();":
            return [["masgold_build@localhost"]]
        if sql == "SHOW GRANTS;":
            return [[g] for g in self.grants]
        if "information_schema_stats_expiry" in sql:
            return [list(r) for r in self.protected_rows]
        if "information_schema.SCHEMATA WHERE SCHEMA_NAME" in sql:
            return [["1" if self.exists else "0"]]
        if "TABLE_NAME = 'masgold_rehearsal_marker'" in sql:
            return [[str(self.tables), "1" if self.marker else "0", "0"]]
        if sql.startswith("SELECT target_database FROM masgold_rehearsal_marker"):
            return [[self.marker]] if self.marker else []
        if sql.startswith("SELECT version FROM schema_migrations"):
            return [[str(v)] for v in self.applied]
        if sql.startswith("SELECT TABLE_TYPE"):
            return [["BASE TABLE", str(self.tables)]]
        raise AssertionError(f"unexpected query {sql[:60]}")

    def run_script(self, sql, database):
        self.log.append(("script", database, sql))
        if sql.startswith("CREATE DATABASE"):
            self.exists, self.tables, self.marker = True, 0, None
        elif sql.startswith("DROP DATABASE"):
            self.exists, self.tables, self.marker = False, 0, None
        elif sql.startswith("CREATE TABLE masgold_rehearsal_marker"):
            self.tables, self.marker = self.tables + 1, database
        else:
            number = int(re.search(r"-- file (\d+)", sql).group(1))
            self.applied.append(number)
            self.tables += 1
            if self.after_script:
                self.after_script(number)
            return f"{rehearsal.SENTINEL}{self.session_database or database}\n"
        return ""


def synthetic_chain(count=3):
    return [ChainFile(f"{n:03d}_x.sql", Path(f"{n:03d}_x.sql"), "0" * 64, f"-- file {n}\nSELECT 1;")
            for n in range(1, count + 1)]


class RehearsalOrchestrationTests(unittest.TestCase):
    def rehearse(self, server, **kwargs):
        return rehearsal.Rehearsal(server, TARGET, synthetic_chain(), log=lambda _: None).run(**kwargs)

    def scripts(self, server):
        return [(db, sql.split("\n")[0]) for kind, db, sql in server.log if kind == "script"]

    def test_fresh_build_creates_marks_and_applies_in_order(self):
        server = FakeServer()
        result = self.rehearse(server, create=True)
        self.assertEqual(result["applied"], ["001_x.sql", "002_x.sql", "003_x.sql"])
        self.assertEqual(result["schema_migrations"], [1, 2, 3])
        self.assertEqual(self.scripts(server), [
            (None, "CREATE DATABASE `masgold_rehearsal` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;"),
            (TARGET, "CREATE TABLE masgold_rehearsal_marker ("),
            (TARGET, "-- file 1"), (TARGET, "-- file 2"), (TARGET, "-- file 3")])

    def test_missing_target_needs_create(self):
        server = FakeServer()
        with refused(self, "TARGET_MISSING"):
            self.rehearse(server)
        self.assertEqual(self.scripts(server), [])

    def test_empty_target_is_marked_then_built(self):
        server = FakeServer(target_state="EMPTY")
        self.assertEqual(self.rehearse(server)["initial_state"], "EMPTY")
        self.assertNotIn("DROP", " ".join(sql for _, sql in self.scripts(server)))

    def test_unmarked_non_empty_target_is_never_touched(self):
        for flags in ({}, {"create": True}, {"rebuild": True}):
            server = FakeServer(target_state="FOREIGN")
            with self.subTest(flags=flags), refused(self, "TARGET_NOT_EMPTY"):
                self.rehearse(server, **flags)
            self.assertEqual(self.scripts(server), [])

    def test_marker_for_another_database_is_not_accepted(self):
        server = FakeServer(target_state="MARKED")
        server.marker = "masgold_v2"
        with refused(self, "TARGET_NOT_EMPTY"):
            self.rehearse(server, rebuild=True)
        self.assertEqual(self.scripts(server), [])

    def test_previous_rehearsal_needs_rebuild(self):
        server = FakeServer(target_state="MARKED")
        with refused(self, "TARGET_ALREADY_BUILT"):
            self.rehearse(server)
        self.assertEqual(self.scripts(server), [])
        server = FakeServer(target_state="MARKED")
        self.rehearse(server, rebuild=True)
        self.assertEqual(self.scripts(server)[:2], [
            (None, "DROP DATABASE `masgold_rehearsal`;"),
            (None, "CREATE DATABASE `masgold_rehearsal` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;")])

    def test_database_switch_inside_a_file_stops_the_run(self):
        server = FakeServer()
        server.after_script = lambda n: setattr(server, "session_database", "masgold_v2" if n == 2 else None)
        with refused(self, "DATABASE_SWITCHED"):
            self.rehearse(server, create=True)
        self.assertEqual(server.applied, [1, 2])

    def test_protected_schema_change_stops_the_run(self):
        server = FakeServer(grants=["GRANT ALL PRIVILEGES ON *.* TO `root`@`localhost` WITH GRANT OPTION"],
                            protected_rows=[("golddb", "a" * 64), ("masgold_v2", "b" * 64)])
        server.after_script = lambda n: server.protected_rows.append(("masgold_v2", "c" * 64)) if n == 1 else None
        with self.assertRaisesRegex(RehearsalRefused, "^PROTECTED_CHANGED: .*masgold_v2"):
            self.rehearse(server, create=True, allow_privileged=True)
        self.assertEqual(server.applied, [1])

    def test_broad_accounts_are_refused_unless_explicitly_allowed(self):
        cases = [["GRANT ALL PRIVILEGES ON *.* TO `root`@`localhost` WITH GRANT OPTION"],
                 ["GRANT USAGE ON *.* TO `u`@`localhost`", "GRANT SELECT ON `golddb`.* TO `u`@`localhost`"],
                 ["GRANT USAGE ON *.* TO `u`@`localhost`",
                  "GRANT ALL PRIVILEGES ON `masgold_rehearsal`.* TO `u`@`localhost` WITH GRANT OPTION"]]
        for grants in cases:
            server = FakeServer(grants=grants)
            with self.subTest(grants=grants), refused(self, "ACCOUNT_TOO_BROAD"):
                self.rehearse(server, create=True)
            self.assertEqual(self.scripts(server), [])
        server = FakeServer(grants=["GRANT USAGE ON *.* TO `u`@`localhost`"],
                            protected_rows=[("golddb", "a" * 64)])
        with refused(self, "ACCOUNT_TOO_BROAD"):              # visibility alone counts
            self.rehearse(server, create=True)

    def test_restricted_account_sees_no_protected_schema(self):
        result = self.rehearse(FakeServer(), create=True)
        self.assertEqual((result["account_warnings"], result["protected_schemas_visible"]), ([], []))

    def test_incomplete_migration_table_fails(self):
        server = FakeServer()
        original = server.query
        server.query = lambda sql, database=None: [["1"]] if sql.startswith("SELECT version") else original(sql, database)
        with refused(self, "MIGRATIONS_INCOMPLETE"):
            self.rehearse(server, create=True)

    def test_constructor_refuses_protected_targets(self):
        with refused(self, "TARGET_PROTECTED"):
            rehearsal.Rehearsal(FakeServer(), "masgold_v2", synthetic_chain())

    def test_fingerprint_query_covers_protected_schemas(self):
        sql = rehearsal.fingerprint_sql()
        for name in guards.PROTECTED_APP_SCHEMAS + ("mysql", "sys"):
            self.assertIn(f"'{name}'", sql)
        self.assertIn("information_schema_stats_expiry = 0", sql)
        self.assertIn("UPDATE_TIME", sql)
        guards.check_sql_safety("fingerprint", sql.replace("information_schema_stats_expiry", "x"))


class CommandLineTests(unittest.TestCase):
    def run_cli(self, argv, **env):
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.dict(os.environ, env), redirect_stdout(out), redirect_stderr(err):
            code = main(argv)
        return code, out.getvalue(), err.getvalue()

    def test_target_is_required(self):
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            main(["run"])

    def test_there_is_no_password_or_production_option(self):
        for extra in (["--password", "x"], ["--mode", "production-cutover"], ["-p", "x"]):
            with self.subTest(extra=extra), redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                main(["run", "--target", TARGET, *extra])

    def test_protected_target_is_refused_before_files_or_password(self):
        with mock.patch("getpass.getpass", side_effect=AssertionError("password asked")), \
                mock.patch.object(chain, "load_chain", side_effect=AssertionError("files read")):
            for target in ("masgold_v2", "masgold_prod", "golddb"):
                code, _, err = self.run_cli(["run", "--target", target], MASGOLD_DB_NAME=TARGET)
                self.assertEqual(code, 2)
                self.assertIn("TARGET_PROTECTED", err)

    def test_database_name_is_never_taken_from_the_environment(self):
        source = "\n".join(p.read_text(encoding="utf-8") for p in TOOL_DIR.glob("*.py"))
        for forbidden in ("dotenv", "config.database_config", "DatabaseConfig", "MASGOLD_DB_", "getenv("):
            self.assertFalse(forbidden in source, forbidden)
        # The only environment variable read is the rehearsal password.
        self.assertEqual(re.findall(r"os\.environ\S*", source), ["os.environ.get(PASSWORD_ENV)"])

    def test_uncommitted_chain_is_refused(self):
        with mock.patch.object(chain, "verify_committed",
                               side_effect=RehearsalRefused("NOT_COMMITTED", "x differs from HEAD.")), \
                mock.patch("getpass.getpass", side_effect=AssertionError("password asked")):
            code, _, err = self.run_cli(["run", "--target", TARGET, "--create"])
        self.assertEqual((code, "NOT_COMMITTED" in err), (2, True))

    def test_verify_is_offline(self):
        with mock.patch.object(chain, "verify_committed"), \
                mock.patch("subprocess.run", side_effect=AssertionError("no process expected")):
            code, out, _ = self.run_cli(["verify"])
        self.assertEqual(code, 0)
        self.assertIn("OK: 38 files verified", out)

    def test_end_to_end_with_fake_mysql(self):
        runner = FakeRunner(stdout=b"")
        with mock.patch.object(chain, "verify_committed"), \
                mock.patch.object(rehearsal.Rehearsal, "run", return_value={"ok": True}) as run, \
                mock.patch("pathlib.Path.is_file", return_value=True):
            code, out, _ = self.run_cli(["run", "--target", TARGET, "--create"],
                                        MASGOLD_REHEARSAL_PASSWORD="secret-pass")
        self.assertEqual(code, 0)
        self.assertIn("REHEARSAL OK", out)
        self.assertNotIn("secret-pass", out)
        run.assert_called_once_with(create=True, rebuild=False, allow_privileged=False)


if __name__ == "__main__":
    unittest.main()
