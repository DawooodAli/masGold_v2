"""Manageable income / expense categories (Settings → تصنيفات المصروفات والإيرادات).

The treasury's «حركة نقدية جديدة» offers these categories. The owner can ADD one (a name and a direction: دخل / خرج / الاتجاهين), RENAME it, DISABLE / ENABLE it and DELETE it while no operation
has ever used it; a used category can only be disabled, so history keeps its name. The ten categories that existed before this feature are seeded as system categories (they can be renamed,
disabled and, if unused, deleted like any other, but their codes never change). The direction of a category never changes after it is created.

Right: SETTINGS_MANAGE, rechecked inside every write transaction (the actor is the AppSession user); the list itself is readable by anyone who may open the treasury or Settings.
Every change is audited. Codes of the owner's categories are generated here (`C` + 12 hex digits), never typed."""
import secrets

from repositories.cash_category_repository import CashCategoryRepository
from services.authorization_service import AuthorizationService
from services.invoice_service import invoice_transaction

MANAGE = "SETTINGS_MANAGE"
DIRECTIONS = {"IN": "دخل", "OUT": "خرج", "BOTH": "الاتجاهين"}
NAME_MAX = 60

# The categories of the code before this feature (also the migration's seed): used when the table does not exist yet and by the tests to prove the seed.
SEED = (("EXPENSE", "مصروفات", "OUT"), ("SALARY", "مرتبات", "OUT"), ("RENT", "إيجار", "OUT"), ("UTILITIES", "كهرباء ومياه", "OUT"), ("TRANSPORT", "نقل", "OUT"),
        ("MAINTENANCE", "صيانة", "OUT"), ("HOSPITALITY", "بوفيه وضيافة", "OUT"), ("STAFF_ADVANCE", "سلف موظفين", "OUT"), ("REVENUE", "إيراد", "IN"), ("REPAIR", "مرمة", "BOTH"))


class CategoryError(ValueError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def clean_name(value):
    name = " ".join(str(value or "").split())
    if not name:
        raise CategoryError("NAME_REQUIRED", "اكتب اسم التصنيف.")
    if len(name) > NAME_MAX:
        raise CategoryError("NAME_LONG", f"اسم التصنيف حتى {NAME_MAX} حرفًا.")
    return name


def view(row, used=None):
    return {"code": row["code"], "name": row["name"], "direction": row["direction"], "direction_text": DIRECTIONS[row["direction"]], "active": bool(row["is_active"]),
            "system": bool(row["is_system"]), "used": None if used is None else bool(used)}


class CashCategoryService:
    def __init__(self, session, *, transaction_factory=invoice_transaction, repository=CashCategoryRepository, authorization=None):
        self._session = session
        self._transaction = transaction_factory
        self._repository = repository
        self._authorization = authorization or AuthorizationService(session)

    def _actor(self, expected_actor):
        actor = self._session.require_authenticated()
        if expected_actor is not None and not self._session.is_current(expected_actor):
            raise PermissionError("انتهت جلسة الدخول.")
        return actor

    def can_manage(self):
        actor = self._session.current_actor()
        return actor is not None and MANAGE in actor.permissions

    def categories(self, *, _expected_actor=None):
        """Every category with whether any operation used it (the delete button is offered only for an unused one)."""
        actor = self._actor(_expected_actor)
        if MANAGE not in actor.permissions:
            raise PermissionError("إدارة التصنيفات للمالك فقط.")
        with self._transaction() as connection:
            self._authorization.require_permission_read(connection, actor, MANAGE)
            repository = self._repository(connection)
            return [view(r, repository.usage(r["code"])) for r in repository.categories()]

    def _write(self, expected_actor, work):
        session_actor = self._actor(expected_actor)
        with self._transaction() as connection:
            actor = self._authorization.require_permission_for_write(connection, session_actor, MANAGE)
            result = work(self._repository(connection), actor)
            if not self._session.is_current(session_actor):
                raise PermissionError("انتهت جلسة الدخول.")
        return result

    def add(self, name, direction, *, _expected_actor=None):
        name = clean_name(name)
        if direction not in DIRECTIONS:
            raise CategoryError("DIRECTION_INVALID", "اختر اتجاه التصنيف: دخل أو خرج أو الاتجاهين.")

        def work(repository, actor):
            if repository.name_taken(name):
                raise CategoryError("NAME_TAKEN", "يوجد تصنيف بنفس الاسم.")
            code = "C" + secrets.token_hex(6).upper()
            repository.insert(code=code, name=name, direction=direction, sort_order=repository.next_sort_order(), user_id=actor.user_id)
            row = repository.category(code)
            repository.audit(user_id=actor.user_id, event_type="CASH_CATEGORY_ADDED", entity_id=row["id"], code=code, description=f"إضافة تصنيف: {name}",
                             metadata={"name": name, "direction": direction}, occurred_at=repository.now())
            return view(row, 0)
        return self._write(_expected_actor, work)

    def rename(self, code, name, *, _expected_actor=None):
        name = clean_name(name)

        def work(repository, actor):
            row = repository.category(str(code), lock=True)
            if row is None:
                raise CategoryError("NOT_FOUND", "التصنيف غير موجود.")
            if repository.name_taken(name, row["code"]):
                raise CategoryError("NAME_TAKEN", "يوجد تصنيف بنفس الاسم.")
            repository.rename(row["code"], name)
            repository.audit(user_id=actor.user_id, event_type="CASH_CATEGORY_RENAMED", entity_id=row["id"], code=row["code"], description=f"تغيير اسم تصنيف: {row['name']} إلى {name}",
                             metadata={"before": row["name"], "after": name}, occurred_at=repository.now())
            return view({**row, "name": name}, repository.usage(row["code"]))
        return self._write(_expected_actor, work)

    def set_active(self, code, active, *, _expected_actor=None):
        def work(repository, actor):
            row = repository.category(str(code), lock=True)
            if row is None:
                raise CategoryError("NOT_FOUND", "التصنيف غير موجود.")
            repository.set_active(row["code"], bool(active))
            repository.audit(user_id=actor.user_id, event_type="CASH_CATEGORY_ENABLED" if active else "CASH_CATEGORY_DISABLED", entity_id=row["id"], code=row["code"],
                             description=f"{'تفعيل' if active else 'تعطيل'} تصنيف: {row['name']}", metadata={"name": row["name"]}, occurred_at=repository.now())
            return view({**row, "is_active": 1 if active else 0}, repository.usage(row["code"]))
        return self._write(_expected_actor, work)

    def delete(self, code, *, _expected_actor=None):
        """Only a category no operation ever used; otherwise the owner is told to disable it (history keeps its name)."""
        def work(repository, actor):
            row = repository.category(str(code), lock=True)
            if row is None:
                raise CategoryError("NOT_FOUND", "التصنيف غير موجود.")
            if repository.usage(row["code"]):
                raise CategoryError("CATEGORY_USED", "التصنيف مستخدم في عمليات سابقة: عطّله بدل الحذف.")
            repository.delete(row["code"])
            repository.audit(user_id=actor.user_id, event_type="CASH_CATEGORY_DELETED", entity_id=row["id"], code=row["code"], description=f"حذف تصنيف: {row['name']}",
                             metadata={"name": row["name"], "direction": row["direction"]}, occurred_at=repository.now())
            return {"code": row["code"], "deleted": True}
        return self._write(_expected_actor, work)
