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
CANCELLED = "تم الإلغاء؛ لم يتغير شيء."
FAILED = "تعذرت الاستعادة؛ لم تُعتمد أي تغييرات. راجع إعدادات قاعدة البيانات."


def _database_name():
    try:
        from config.database_config import DatabaseConfig
        return str(DatabaseConfig.from_env().database)
    except Exception:
        return "غير معروفة"


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
        print("لا تمرر أي قيمة في سطر الأوامر (خاصة كلمة المرور). شغّل الأداة بدون معاملات.")
        return 2
    password = confirmation = None
    try:
        print("=" * 64)
        print("استعادة طارئة لكلمة مرور المالك (OWNER) — إجراء حساس يُسجَّل في سجل التدقيق.")
        print("قاعدة البيانات المستهدفة: " + _database_name())
        print("لن تتغير الأدوار أو الصلاحيات. لا تكتب كلمة المرور في أي مكان غير الحقل المخفي.")
        print("=" * 64)
        username = input(f"اسم مستخدم المالك [{DEFAULT_USERNAME}]: ").strip() or DEFAULT_USERNAME
        reason = input("سبب الاستعادة (إلزامي): ").strip()
        if not reason:
            print("اكتب سبب الاستعادة. " + CANCELLED)
            return 1
        # Refuse getpass's echoed-input fallback when a secure terminal is absent.
        with warnings.catch_warnings():
            warnings.simplefilter("error", getpass.GetPassWarning)
            password = getpass.getpass("كلمة المرور الجديدة: ")
            confirmation = getpass.getpass("تأكيد كلمة المرور الجديدة: ")
        validate_new_password(password, confirmation)
        expected = f"RECOVER {username}"
        answer = input(f"للتأكيد اكتب بالضبط  {expected}  ثم Enter (أي شيء آخر يلغي العملية): ")
        if answer.strip() != expected:
            print(CANCELLED)
            return 1
        service_factory().recover_owner_password(username, password, confirmation, reason)
    except getpass.GetPassWarning:
        print("شغّل الأداة من طرفية تدعم إدخال كلمة المرور دون إظهارها.")
        return 1
    except (EOFError, KeyboardInterrupt):
        print("\n" + CANCELLED)
        return 1
    except (RecoveryRefused, ValueError) as exc:
        print(str(exc))                       # safe, fixed Arabic messages that never contain a secret
        return 1
    except RecoveryFailed:
        print(FAILED)
        return 1
    except Exception:
        print(FAILED)
        return 1
    finally:
        password = confirmation = None
    print("تمت استعادة كلمة مرور المالك. سجّل الدخول أو أكّد العمليات الحساسة بكلمة المرور الجديدة.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
