"""نسخة احتياطية تلقائية لقاعدة البيانات: a compressed logical backup (mysqldump) written to another drive, with a verified result and a simple retention.

usage:  python -m tools.backup_database [--database NAME] [--dest E:\\masGold_backups] [--keep-days 30] [--keep-min 7]

* READ ONLY on the database: it only dumps (--single-transaction, routines, triggers). Nothing is written to any database.
* The credentials are the application's own (.env through DatabaseConfig); the password goes to mysqldump through a temporary option file and is never printed or logged.
* The file is written under a temporary name and renamed only after the dump finished and was checked (size and the "Dump completed" trailer), so a half-written file is never mistaken for a backup.
* Retention: files of this database older than --keep-days are deleted, but the newest --keep-min files are always kept. Only files that match this tool's own name pattern are ever deleted.
* --offsite FOLDER (owner request 2026-10-31): after the local backup is verified, an ENCRYPTED copy (AES-256-GCM, key from the owner's passphrase with Argon2id, see
  services/backup_crypto.py) is written to FOLDER (a synced Google Drive / OneDrive folder, a USB drive, a network share) as <name>.sql.gz.enc, checked by decrypting it again, and the same
  retention applies there. The passphrase is stored once by `python -m tools.set_backup_passphrase` (Windows Credential Manager); without it the offsite step FAILS loudly (exit 3) and the local
  backup is untouched. Restore: `python -m tools.decrypt_backup FILE.sql.gz.enc OUT.sql.gz`. A lost passphrase cannot be recovered.
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

DEFAULT_DEST = Path(r"E:\masGold_backups")                          # the fallback; the folder the owner chose in Settings (config/backup_settings.py) wins when --dest is not given
DEFAULT_MYSQLDUMP = Path(r"C:\Program Files\MySQL\MySQL Server 8.0\bin\mysqldump.exe")
TRAILER = b"-- Dump completed"
NAME = re.compile(r"^(?P<db>[a-z0-9_]{1,64})_(?P<stamp>\d{8}_\d{6})\.sql\.gz$")
ENCRYPTED_NAME = re.compile(r"^(?P<db>[a-z0-9_]{1,64})_(?P<stamp>\d{8}_\d{6})\.sql\.gz\.enc$")
PASSPHRASE_SECRET = "backup_passphrase"
MIN_BYTES = 2000


class BackupError(RuntimeError):
    pass


def backup_name(database, now):
    return f"{database}_{now:%Y%m%d_%H%M%S}.sql.gz"


def files_for(dest, database, *, encrypted=False):
    """(path, timestamp) of this database's backups, oldest first; foreign files are ignored."""
    found = []
    pattern, suffix = (ENCRYPTED_NAME, ".sql.gz.enc") if encrypted else (NAME, ".sql.gz")
    for path in Path(dest).glob(f"{database}_*{suffix}"):
        match = pattern.match(path.name)
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


def offsite_copy(local, folder, database, now, keep_days, keep_min, passphrase=None):
    """Encrypt the verified local backup into `folder`; returns a short description. The copy is accepted only after it was decrypted again end to end."""
    from services.backup_crypto import BackupCryptoError, encrypt_file, verify_file
    if passphrase is None:
        from services.secret_store import SecretStore
        passphrase = SecretStore().get_secret(PASSPHRASE_SECRET)
    if not passphrase:
        raise BackupError("no backup passphrase is stored; run: python -m tools.set_backup_passphrase")
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    final = folder / (Path(local).name + ".enc")
    temp = folder / (final.name + ".partial")
    try:
        encrypt_file(local, temp, passphrase)
        try:
            verify_file(temp, passphrase)
        except BackupCryptoError as exc:
            raise BackupError(f"the encrypted copy failed its own check: {exc}") from None
        os.replace(temp, final)
        temp = None
    finally:
        if temp is not None and Path(temp).exists():
            Path(temp).unlink()
    removed = to_delete(files_for(folder, database, encrypted=True), now, keep_days, keep_min)
    for path in removed:
        path.unlink()
    return f"{final.name} {final.stat().st_size} bytes, {len(removed)} old file(s) removed"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--database", default=None)
    parser.add_argument("--dest", default=None)
    parser.add_argument("--keep-days", type=int, default=30)
    parser.add_argument("--keep-min", type=int, default=7)
    parser.add_argument("--mysqldump", default=str(DEFAULT_MYSQLDUMP))
    parser.add_argument("--offsite", default=None)
    args = parser.parse_args(argv)
    from config.database_config import DatabaseConfig
    database = args.database or DatabaseConfig.from_env().database
    if not re.fullmatch(r"[a-z0-9_]{1,64}", database):
        print("invalid database name")
        return 2
    from config import backup_settings
    dest = Path(args.dest) if args.dest else backup_settings.destination()
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
    code = 0 if status == "OK" else 1
    if status == "OK" and args.offsite:
        offsite_status, offsite_detail = "OFFSITE_FAILED", ""
        try:
            offsite_detail = offsite_copy(final, args.offsite, database, now, args.keep_days, args.keep_min)
            offsite_status = "OFFSITE_OK"
        except Exception as exc:                               # only our own messages; the passphrase is never part of an error
            offsite_detail = f"{type(exc).__name__}: {exc}"[:300]
            code = 3
        try:
            with open(dest / "backup.log", "a", encoding="utf-8") as log:
                log.write(f"{datetime.now():%Y-%m-%d %H:%M:%S}  {database}  {offsite_status}  {offsite_detail}\n")
        except OSError:
            pass
        print(f"{offsite_status}: {database} {offsite_detail}")
    print(f"{status}: {database} {detail}")
    return code


if __name__ == "__main__":
    sys.exit(main())
