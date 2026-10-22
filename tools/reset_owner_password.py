"""EMERGENCY OWNER password recovery — explicit local tool, never imported or run by the application.

Run on the shop computer (no arguments; the password is NEVER taken from the command line):

    .\\.venv\\Scripts\\python.exe tools\\reset_owner_password.py

It replaces the password of an existing ACTIVE OWNER account (resolved from the database, not from what is typed)
and records OWNER_PASSWORD_RECOVERED in audit_log in the same transaction. Roles and permissions are untouched.
Nothing is written unless every answer is valid and the final confirmation is typed exactly.
"""
import getpass
from pathlib import Path
import sys

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.authentication_service import AuthenticationService, RecoveryFailed, RecoveryRefused
from services.password_policy import validate_new_password

DEFAULT_USERNAME = "admin"          # a typing convenience only; any existing active OWNER may be targeted
CANCELLED = "Cancelled. Nothing was changed."
FAILED = "Recovery failed. Nothing was changed. Check the database settings."


def _database_name():
    try:
        from config.database_config import DatabaseConfig
        return str(DatabaseConfig.from_env().database)
    except Exception:
        return "unknown"


def _never_fail_on_output():
    """A redirected stdout/stderr may not be UTF-8 (e.g. cp1252): print '?' instead of crashing mid-recovery."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except Exception:
            pass


def main(argv=None, service_factory=AuthenticationService):
    import warnings
    _never_fail_on_output()
    arguments = sys.argv[1:] if argv is None else list(argv)
    if arguments:
        # Never echo the arguments: one of them could be a password typed by mistake.
        print("Do not pass any value on the command line (especially a password). Run the tool without arguments.")
        return 2
    password = confirmation = None
    try:
        print("=" * 64)
        print("EMERGENCY recovery of the OWNER password - a sensitive action recorded in the audit log.")
        print("Target database: " + _database_name() + "   (it must be masgold_prod for the production program)")
        print("Roles and permissions are not changed. Type the password only in the hidden prompts below (use the English keyboard).")
        print("=" * 64)
        username = input(f"Owner username [{DEFAULT_USERNAME}] (press Enter): ").strip() or DEFAULT_USERNAME
        reason = input("Reason for the recovery (required, any short text): ").strip()
        if not reason:
            print("Write a reason. " + CANCELLED)
            return 1
        # Refuse getpass's echoed-input fallback when a secure terminal is absent.
        with warnings.catch_warnings():
            warnings.simplefilter("error", getpass.GetPassWarning)
            password = getpass.getpass("New password (hidden, nothing is shown while typing): ")
            confirmation = getpass.getpass("Confirm the new password (type it again): ")
        validate_new_password(password, confirmation)
        expected = f"RECOVER {username}"
        answer = input(f"To confirm, type exactly   {expected}   then press Enter (anything else cancels): ")
        if answer.strip() != expected:
            print(CANCELLED)
            return 1
        service_factory().recover_owner_password(username, password, confirmation, reason)
    except getpass.GetPassWarning:
        print("Run the tool from a terminal that supports hidden password input.")
        return 1
    except (EOFError, KeyboardInterrupt):
        print("\n" + CANCELLED)
        return 1
    except (RecoveryRefused, ValueError) as exc:
        print("Refused: check that the username is an existing ACTIVE owner account, the reason is not empty, and the new password is 6 to 1024 characters and typed identically twice.")   # fixed text, never a secret
        return 1
    except RecoveryFailed:
        print(FAILED)
        return 1
    except Exception:
        print(FAILED)
        return 1
    finally:
        password = confirmation = None
    print("Owner password recovered. Use the new password for the manager reviews lock and the sensitive confirmations.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
