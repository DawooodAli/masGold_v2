"""تحديث قاعدة الإنتاج: applies the pending schema migrations (database/schema, in order) to `masgold_prod` only.

usage:
    python -m tools.migrate_production check
    python -m tools.migrate_production apply

* `check` is READ ONLY: it prints the database's level and the files that are pending.
* `apply` first takes a verified backup (`tools.backup_database`, into E:\\masGold_backups), then applies ONLY the pending files in order, each verified against SHA256SUMS. Forward only: never DROP,
  never rebuild. It stops at the first failure and says which file failed. Nothing but `masgold_prod` is ever touched; the live session must really be `masgold_prod`.
* Uses the administrator credentials of the application's .env (the same ones as the other tools); the password is never printed."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGET = "masgold_prod"


def main(argv):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass
    mode = argv[0] if argv else ""
    if mode not in ("check", "apply") or len(argv) != 1:
        print(__doc__)
        return 2
    sys.path.insert(0, str(ROOT))
    from config.database_config import DatabaseConfig
    from tools.schema_rehearsal import chain as chain_module
    from tools.schema_rehearsal.mysql_client import DEFAULT_MYSQL, Credentials, MysqlClient, MysqlError, option_file
    cfg = DatabaseConfig.from_env()
    if cfg.host not in ("localhost", "127.0.0.1"):
        print("refused: the database server is not this machine")
        return 2
    files = chain_module.load_chain(require_committed=False)
    with option_file(Credentials("localhost", cfg.port, cfg.user, cfg.password)) as options:
        client = MysqlClient(DEFAULT_MYSQL, options)
        live = client.query("SELECT DATABASE();", database=TARGET)[0][0]
        if live != TARGET:
            print("refused: the live session is not", TARGET)
            return 2
        versions = [int(r[0]) for r in client.query("SELECT version FROM schema_migrations ORDER BY version;", database=TARGET)]
        if not versions or versions != list(range(1, len(versions) + 1)):
            print("refused: the migration ledger is not contiguous", versions[-5:])
            return 2
        level = versions[-1]
        pending = [f for f in files if int(f.name[:3]) > level]
        print(f"{TARGET}: level {level}; pending: {[f.name for f in pending] or 'none'}")
        if mode == "check" or not pending:
            return 0
        if [int(f.name[:3]) for f in pending] != list(range(level + 1, int(files[-1].name[:3]) + 1)):
            print("refused: the pending files are not the contiguous continuation of the chain")
            return 2
        from tools import backup_database
        if backup_database.main(["--database", TARGET]) != 0:
            print("refused: the backup was not verified, nothing was changed")
            return 1
        for f in pending:
            try:
                result = client.run_script(f.sql + "\nSELECT CONCAT('SENT:', DATABASE());\n", database=TARGET)
                if "SENT:" + TARGET not in result:
                    raise MysqlError("the script did not finish on " + TARGET)
                print("applied", f.name)
            except MysqlError as exc:
                print("FAILED", f.name, str(exc)[:600])
                return 3
        after = int(client.query("SELECT MAX(version) FROM schema_migrations;", database=TARGET)[0][0])
        print(f"OK: level {after}")
        return 0 if after == int(files[-1].name[:3]) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
