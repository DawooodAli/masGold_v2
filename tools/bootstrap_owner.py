"""Explicit initial setup only. Never imported or run by application startup."""
import getpass
from pathlib import Path
import sys

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from repositories.authentication_repository import AuthenticationRepository, authentication_transaction
from services.password_service import PasswordService


def bootstrap_owner(username, display_name, password, employee_id=None, *,
                    transaction_factory=authentication_transaction,
                    repository_factory=AuthenticationRepository, passwords=None):
    if not isinstance(username, str) or not username.strip() or len(username) > 100:
        raise ValueError("اسم المستخدم مطلوب وبحد أقصى ١٠٠ حرف.")
    if not isinstance(display_name, str) or not display_name.strip() or len(display_name) > 100:
        raise ValueError("اسم العرض مطلوب وبحد أقصى ١٠٠ حرف.")
    if employee_id is not None and (type(employee_id) is not int or not 0 < employee_id <= 4294967295):
        raise ValueError("رقم الموظف غير صحيح.")
    if not isinstance(password, str) or not 12 <= len(password) <= 1024:
        raise ValueError("استخدم كلمة مرور من ١٢ إلى ١٠٢٤ حرفاً.")
    encoded = (passwords or PasswordService()).hash(password)
    with transaction_factory() as connection:
        repository = repository_factory(connection)
        repository.acquire_bootstrap_lock()
        if repository.has_users():
            raise ValueError("يوجد مستخدم بالفعل؛ تهيئة المستخدم الأول مرفوضة.")
        role_id = repository.bootstrap_owner_role()
        user_id = repository.create_first_user(username, display_name, encoded, employee_id, role_id)
        if "APP_LOGIN" not in repository.effective_permissions(user_id, lock=True):
            raise ValueError("المستخدم الجديد لا يملك صلاحية APP_LOGIN النشطة.")
    return user_id


def main():
    username = input("اسم المستخدم: ")
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
        passwords = PasswordService()
        if not passwords.verify(passwords.hash(password), confirmation):
            print("تأكيد كلمة المرور غير مطابق.")
            return 1
        bootstrap_owner(username, display_name, password, int(employee) if employee else None,
                        passwords=passwords)
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
