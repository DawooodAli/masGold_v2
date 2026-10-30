"""Store the passphrase that encrypts the offsite backups (Windows Credential Manager, service «masGold», name «backup_passphrase»).

usage:  python -m tools.set_backup_passphrase

The passphrase is typed twice without echo and is never printed or logged. WRITE IT DOWN AND KEEP IT SOMEWHERE SAFE OUTSIDE THIS COMPUTER: an encrypted backup cannot be opened without it
and it cannot be recovered. Changing it later only affects the NEXT copies; older copies still need the passphrase they were made with."""
import getpass
import sys

from services.backup_crypto import MIN_PASSPHRASE
from services.secret_store import SecretStore, SecretStoreError
from tools.backup_database import PASSPHRASE_SECRET


def main():
    store = SecretStore()
    try:
        existing = store.get_secret(PASSPHRASE_SECRET)
    except SecretStoreError as exc:
        print(str(exc))
        return 2
    if existing and input("A passphrase is already stored. Replace it? Older copies keep needing the old one (yes/no): ").strip().lower() != "yes":
        print("unchanged")
        return 0
    first = getpass.getpass("New backup passphrase: ")
    if len(first) < MIN_PASSPHRASE:
        print(f"too short: use at least {MIN_PASSPHRASE} characters")
        return 2
    if getpass.getpass("Type it again: ") != first:
        print("the two entries differ")
        return 2
    try:
        store.set_secret(PASSPHRASE_SECRET, first)
        if store.get_secret(PASSPHRASE_SECRET) != first:
            raise SecretStoreError("could not verify the stored passphrase")
    except SecretStoreError as exc:
        print(str(exc))
        return 2
    print("stored. Keep a written copy in a safe place: it cannot be recovered.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
