"""نسخة احتياطية تلقائية لقاعدة البيانات: a compressed logical backup (mysqldump) written to another drive, with a verified result and a simple retention.

usage:  python -m tools.backup_database [--database NAME] [--dest E:\\masGold_backups] [--keep-days 30] [--keep-min 7]

* READ ONLY on the database: it only dumps (--single-transaction, routines, triggers). Nothing is written to any database.
* The credentials are the application's own (.env through DatabaseConfig); the password goes to mysqldump through a temporary option file and is never printed or logged.
* The file is written under a temporary name and renamed only after the dump finished and was checked (size and the "Dump completed" trailer), so a half-written file is never mistaken for a backup.
* Retention: files of this database older than --keep-days are deleted, but the newest --keep-min files are always kept. Only files that match this tool's own name pattern are ever deleted.
* Exit code 0 = a verified backup exists now; non-zero = nothing new was verified (the Windows task shows the failure). Every run appends one line to <dest>\\backup.log (no secrets)."""
import argparse
import gzip
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

DEFAULT_DEST = Path(r"E:\masGold_backups")
DEFAULT_MYSQLDUMP = Path(r"C:\Program Files\MySQL\MySQL Server 8.0\bin\mysqldump.exe")
TRAILER = b"-- Dump completed"
NAME = re.compile(r"^(?P<db>[a-z0-9_]{1,64})_(?P<stamp>\d{8}_\d{6})\.sql\.gz$")
MIN_BYTES = 2000


class BackupError(RuntimeError):
    pass


def backup_name(database, now):
    return f"{database}_{now:%Y%m%d_%H%M%S}.sql.gz"


def files_for(dest, database):
    """(path, timestamp) of this database's backups, oldest first; foreign files are ignored."""
    found = []
    for path in Path(dest).glob(f"{database}_*.sql.gz"):
        match = NAME.match(path.name)
        if match and match.group("db") == database:
            found.append((path, datetime.strptime(match.group("stamp"), "%Y%m%d_%H%M%S")))
    return sorted(found, key=lambda item: item[1])


def to_delete(files, now, keep_days, keep_min):
    """Files older than keep_days, never touching the newest keep_min."""
    protected = {path for path, _ in files[-keep_min:]} if keep_min > 0 else set()
    limit = now - timedelta(days=keep_days)
    return [path for path, stamp in files if stamp < limit and path not in protected]


def verify(path):
    """The compressed file is readable to its end, is not tiny and carries mysqldump's completion trailer."""
    size = Path(path).stat().st_size
    if size < MIN_BYTES // 4:
        raise BackupError("the backup file is too small")
    tail = b""
    with gzip.open(path, "rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            tail = (tail + chunk)[-4096:]
    if TRAILER not in tail:
        raise BackupError("the backup does not end with the dump trailer")
    return size


def run_dump(database, target, mysqldump):
    from config.database_config import DatabaseConfig
    from tools.schema_rehearsal.mysql_client import Credentials, option_file
    cfg = DatabaseConfig.from_env()
    if cfg.host not in ("localhost", "127.0.0.1"):
        raise BackupError("the database host is not this machine")
    if not Path(mysqldump).exists():
        raise BackupError(f"mysqldump was not found at {mysqldump}")
    with option_file(Credentials("localhost", cfg.port, cfg.user, cfg.password)) as options:
        command = [str(mysqldump), f"--defaults-extra-file={options}", "--single-transaction", "--routines", "--triggers", "--events",
                   "--default-character-set=utf8mb4", "--no-tablespaces", database]
        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        with gzip.open(target, "wb", compresslevel=6) as out:
            shutil.copyfileobj(process.stdout, out, 1024 * 1024)
        error = process.stderr.read().decode("utf-8", "replace")[:300]
        if process.wait() != 0:
            raise BackupError("mysqldump failed: " + error.replace(cfg.password, "***"))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--database", default=None)
    parser.add_argument("--dest", default=str(DEFAULT_DEST))
    parser.add_argument("--keep-days", type=int, default=30)
    parser.add_argument("--keep-min", type=int, default=7)
    parser.add_argument("--mysqldump", default=str(DEFAULT_MYSQLDUMP))
    args = parser.parse_args(argv)
    from config.database_config import DatabaseConfig
    database = args.database or DatabaseConfig.from_env().database
    if not re.fullmatch(r"[a-z0-9_]{1,64}", database):
        print("invalid database name")
        return 2
    dest = Path(args.dest)
    now = datetime.now()
    status, detail = "FAILED", ""
    temp = None
    try:
        dest.mkdir(parents=True, exist_ok=True)
        final = dest / backup_name(database, now)
        temp = dest / (final.name + ".partial")
        run_dump(database, temp, args.mysqldump)
        size = verify(temp)
        os.replace(temp, final)
        temp = None
        removed = to_delete(files_for(dest, database), now, args.keep_days, args.keep_min)
        for path in removed:
            path.unlink()
        status, detail = "OK", f"{final.name} {size} bytes, {len(removed)} old file(s) removed"
    except Exception as exc:                                   # the reason goes to the log and the exit code; no secret is ever part of it
        detail = f"{type(exc).__name__}: {exc}"[:300]
    finally:
        if temp is not None and Path(temp).exists():
            Path(temp).unlink()
    try:
        with open(dest / "backup.log", "a", encoding="utf-8") as log:
            log.write(f"{now:%Y-%m-%d %H:%M:%S}  {database}  {status}  {detail}\n")
    except OSError:
        pass
    print(f"{status}: {database} {detail}")
    return 0 if status == "OK" else 1


if __name__ == "__main__":
    sys.exit(main())
