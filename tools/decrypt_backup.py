"""Decrypt an offsite backup made by `tools.backup_database --offsite`.

usage:  python -m tools.decrypt_backup FILE.sql.gz.enc OUT.sql.gz

The passphrase is asked for without echo (or taken from the stored one with --stored). The output is a normal compressed mysqldump; restoring it into a database is a separate, deliberate
step (`gunzip` it and feed it to `mysql`). An existing OUT file is never overwritten, and a failed decryption leaves no output file."""
import argparse
import getpass
import sys
from pathlib import Path

from services.backup_crypto import BackupCryptoError, decrypt_file


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("source")
    parser.add_argument("target")
    parser.add_argument("--stored", action="store_true", help="use the passphrase stored in Windows Credential Manager")
    args = parser.parse_args(argv)
    source, target = Path(args.source), Path(args.target)
    if not source.is_file():
        print("the encrypted file was not found")
        return 2
    if target.exists():
        print("the output file already exists; choose another name")
        return 2
    if args.stored:
        from services.secret_store import SecretStore
        from tools.backup_database import PASSPHRASE_SECRET
        passphrase = SecretStore().get_secret(PASSPHRASE_SECRET) or ""
    else:
        passphrase = getpass.getpass("Backup passphrase: ")
    try:
        size = decrypt_file(source, target, passphrase)
    except BackupCryptoError as exc:
        if target.exists():
            target.unlink()
        print(str(exc))
        return 1
    print(f"decrypted: {target} ({size} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
