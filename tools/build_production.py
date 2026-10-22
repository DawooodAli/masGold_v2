"""تجهيز قاعدة الـ production من الصفر: a NEW empty database built from the numbered schema chain, plus a least-privilege MySQL account for the application.

usage:  python -m tools.build_production --confirm [--database masgold_prod] [--user masgold_app] [--env-file .env.production]

What it does (and nothing else):
1. refuses unless the database does NOT exist yet (it never drops, truncates or rebuilds anything) and the chain (001-LAST) matches database/schema/SHA256SUMS;
2. CREATE DATABASE (utf8mb4 / utf8mb4_unicode_ci) and applies every chain file in order, then checks the migration ledger is contiguous and at the chain's last level;
3. creates ONE account for the application with only SELECT, INSERT, UPDATE, DELETE, EXECUTE and LOCK TABLES on that database (no DDL, nothing on any other schema);
4. writes the connection settings, with a freshly generated password, to the git-ignored env file (never printed, never logged) and proves the account works and cannot see the DEV or the old database.
The administrator credentials are the ones already in the application's .env (DEV administrator); they are used for steps 1-3 only. golddb and masgold_v2 are never touched."""
import argparse
import os
import re
import secrets
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROTECTED = ("masgold_v2", "golddb", "masgold_rehearsal", "mysql", "sys", "information_schema", "performance_schema")
GRANTS = "SELECT, INSERT, UPDATE, DELETE, EXECUTE, LOCK TABLES"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--database", default="masgold_prod")
    parser.add_argument("--user", default="masgold_app")
    parser.add_argument("--env-file", default=".env.production")
    parser.add_argument("--confirm", action="store_true")
    parser.add_argument("--skip-account", action="store_true", help="build only the database (a throw-away masgold_import_* copy for rehearsing an import): no MySQL account, no env file")
    args = parser.parse_args(argv)
    if not args.confirm:
        print("refused: pass --confirm to build the production database")
        return 2
    if not re.fullmatch(r"[a-z0-9_]{1,64}", args.database) or args.database in PROTECTED:
        print("refused: invalid or protected database name")
        return 2
    if not re.fullmatch(r"[A-Za-z0-9_]{1,32}", args.user):
        print("refused: invalid account name")
        return 2
    env_path = ROOT / args.env_file
    if args.skip_account and not args.database.startswith("masgold_import_"):
        print("refused: --skip-account is only for masgold_import_* rehearsal copies")
        return 2
    if not args.skip_account and env_path.exists():
        print(f"refused: {env_path.name} already exists (it is never overwritten)")
        return 2
    sys.path.insert(0, str(ROOT))
    from config.database_config import DatabaseConfig
    from tools.schema_rehearsal import chain as chain_module
    from tools.schema_rehearsal.mysql_client import DEFAULT_MYSQL, Credentials, MysqlClient, option_file

    admin = DatabaseConfig.from_env()
    if admin.host not in ("localhost", "127.0.0.1"):
        print("refused: the database server is not this machine")
        return 2
    files = chain_module.load_chain(require_committed=False)           # verified against SHA256SUMS
    last = int(files[-1].name[:3])
    report = {}
    with option_file(Credentials("localhost", admin.port, admin.user, admin.password)) as options:
        client = MysqlClient(DEFAULT_MYSQL, options)
        exists = client.query("SELECT COUNT(*) FROM information_schema.SCHEMATA WHERE SCHEMA_NAME = '" + args.database + "';")[0][0]
        if int(exists) != 0:
            print("refused: the database already exists (nothing was changed)")
            return 2
        client.run_script("CREATE DATABASE `" + args.database + "` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;", None)
        applied = []
        for f in files:
            client.run_script(f.sql + "\nSELECT CONCAT('SENT:', DATABASE());\n", database=args.database)
            applied.append(f.name)
        versions = [int(r[0]) for r in client.query("SELECT version FROM schema_migrations ORDER BY version;", database=args.database)]
        if versions != list(range(1, last + 1)):
            print("FAILED: the migration ledger is not contiguous; the new database was left for inspection")
            return 1
        report["level"] = versions[-1]
        report["tables"] = int(client.query("SELECT COUNT(*) FROM information_schema.TABLES WHERE TABLE_SCHEMA = '" + args.database + "' AND TABLE_TYPE = 'BASE TABLE';")[0][0])
        if args.skip_account:
            print(f"OK: {args.database} built at level {report['level']} ({report['tables']} tables) without an account")
            return 0
        password = secrets.token_urlsafe(30)
        client.run_script("CREATE USER '" + args.user + "'@'localhost' IDENTIFIED BY '" + password + "';\n"
                          "GRANT " + GRANTS + " ON `" + args.database + "`.* TO '" + args.user + "'@'localhost';\nFLUSH PRIVILEGES;", None)
    lines = ["MASGOLD_DB_HOST=localhost", f"MASGOLD_DB_PORT={admin.port}", f"MASGOLD_DB_NAME={args.database}", f"MASGOLD_DB_USER={args.user}",
             f"MASGOLD_DB_PASSWORD={password}", "MASGOLD_ENV=production"]
    env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    # proof: the application account works on its database and sees nothing of the others
    with option_file(Credentials("localhost", admin.port, args.user, password)) as options:
        app = MysqlClient(DEFAULT_MYSQL, options)
        report["app_level"] = int(app.query("SELECT MAX(version) FROM schema_migrations;", database=args.database)[0][0])
        visible = sorted(r[0] for r in app.query("SELECT SCHEMA_NAME FROM information_schema.SCHEMATA;"))
        report["other_application_schemas_visible"] = [s for s in visible if s in ("masgold_v2", "golddb", "masgold_rehearsal")]
    print(f"OK: {args.database} built at level {report['level']} ({report['tables']} tables), account {args.user} created, settings written to {env_path.name}")
    print(f"the account works (level {report['app_level']}); it sees these other application schemas: {report['other_application_schemas_visible'] or 'none'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
