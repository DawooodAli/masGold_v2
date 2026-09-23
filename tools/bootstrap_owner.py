"""Explicit initial setup only. Never imported or run by application startup."""
import getpass
from pathlib import Path
import sys

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from repositories.authentication_repository import AuthenticationRepository, authentication_transaction
from services.password_service import PasswordService
from services.authentication_service import AuthenticationService


def bootstrap_owner(username, display_name, password, employee_id=None, *,
                    transaction_factory=authentication_transaction,
                    repository_factory=AuthenticationRepository, passwords=None):
    # Compatibility entry point; the service owns all creation/transaction logic.
    if username != "admin":
        raise ValueError("اسم المستخدم الأول ثابت: admin")
    service = AuthenticationService(transaction_factory, repository_factory, passwords)
    return service.create_first_owner(display_name, password, password, employee_id).user_id


def main():
    print("اسم المستخدم: admin")
    display_name = input("اسم العرض: ")
    employee = input("رقم موظف نشط للربط (اتركه فارغاً دون ربط): ").strip()
    import warnings
    password = confirmation = None
    try:
        # Refuse getpass's echoed-input fallback when a secure terminal is absent.
        with warnings.catch_warnings():
            warnings.simplefilter('error', getpass.GetPassWarning)
            password = getpass.getpass("كلمة المرور: ")
            confirmation = getpass.getpass("تأكيد كلمة المرور: ")
        AuthenticationService().create_first_owner(
            display_name, password, confirmation, int(employee) if employee else None)
    except getpass.GetPassWarning:
        print("شغّل الأداة من طرفية تدعم إدخال كلمة المرور دون إظهارها.")
        return 1
    except ValueError as exc:
        print(str(exc))
        return 1
    except Exception:
        print("تعذرت التهيئة؛ لم تُعتمد العملية. راجع إعدادات قاعدة البيانات.")
        return 1
    finally:
        password = confirmation = None
    print("تم إنشاء المستخدم الأول بنجاح.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
