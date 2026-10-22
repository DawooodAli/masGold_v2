"""The making COST per gram of each item category («الحروف» A B C D L T), set by the owner in Settings.

One number per category, for every karat. It is the CURRENT cost: stock entry freezes it into the piece (`InventoryService`), so changing it never rewrites a piece or a past sale. The
making-profit report compares the making charged on a sale with that stored cost. Right: SETTINGS_MANAGE, rechecked inside every write transaction (the actor is the AppSession user);
every change is audited. A cost is a non-negative amount with at most two decimals; clearing it returns the category to «غير محدد»."""
from decimal import Decimal, InvalidOperation

from repositories.category_cost_repository import CategoryCostRepository
from services.authorization_service import AuthorizationService
from services.invoice_service import invoice_transaction

MANAGE = "SETTINGS_MANAGE"
LIMIT = Decimal("100000")


class CostError(ValueError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def parse_cost(value):
    text = str(value if value is not None else "").strip().replace(",", "")
    if not text:
        raise CostError("COST_REQUIRED", "اكتب تكلفة المصنعية للجرام.")
    try:
        amount = Decimal(text)
    except InvalidOperation:
        raise CostError("COST_INVALID", "اكتب التكلفة بالأرقام.") from None
    if not amount.is_finite() or amount < 0 or amount >= LIMIT or amount != amount.quantize(Decimal("0.01")):
        raise CostError("COST_INVALID", "التكلفة رقم من صفر بخانتين عشريتين على الأكثر.")
    return amount.quantize(Decimal("0.01"))


def view(row):
    return {"id": str(row["id"]), "code": row["code"], "name": row["name"], "cost": None if row["cost"] is None else Decimal(str(row["cost"])),
            "range_min": None if row.get("range_min") is None else Decimal(str(row["range_min"])), "range_max": None if row.get("range_max") is None else Decimal(str(row["range_max"]))}


class CategoryCostService:
    def __init__(self, session, *, transaction_factory=invoice_transaction, repository=CategoryCostRepository, authorization=None):
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

    def costs(self, *, _expected_actor=None):
        actor = self._actor(_expected_actor)
        if MANAGE not in actor.permissions:
            raise PermissionError("تكلفة المصنعية للمالك فقط.")
        with self._transaction() as connection:
            self._authorization.require_permission_read(connection, actor, MANAGE)
            return [view(r) for r in self._repository(connection).categories()]

    def _write(self, expected_actor, work):
        session_actor = self._actor(expected_actor)
        with self._transaction() as connection:
            actor = self._authorization.require_permission_for_write(connection, session_actor, MANAGE)
            result = work(self._repository(connection), actor)
            if not self._session.is_current(session_actor):
                raise PermissionError("انتهت جلسة الدخول.")
        return result

    def set_cost(self, category_id, amount, *, _expected_actor=None):
        value = parse_cost(amount)

        def work(repository, actor):
            category = repository.category(int(category_id), lock=True)
            if category is None:
                raise CostError("NOT_FOUND", "التصنيف غير موجود أو غير نشط.")
            before = repository.cost(category["id"])
            repository.set_cost(category["id"], value, actor.user_id)
            repository.audit(user_id=actor.user_id, event_type="CATEGORY_MAKING_COST_SET", category_id=category["id"], code=category["code"],
                             description=f"تكلفة مصنعية الحرف {category['code']}: {value:,.2f} ج/جرام", metadata={"before": None if before is None else str(before), "after": str(value)},
                             occurred_at=repository.now())
            return {"id": str(category["id"]), "cost": value}
        return self._write(_expected_actor, work)

    def clear_cost(self, category_id, *, _expected_actor=None):
        def work(repository, actor):
            category = repository.category(int(category_id), lock=True)
            if category is None:
                raise CostError("NOT_FOUND", "التصنيف غير موجود أو غير نشط.")
            before = repository.cost(category["id"])
            repository.clear_cost(category["id"])
            repository.audit(user_id=actor.user_id, event_type="CATEGORY_MAKING_COST_CLEARED", category_id=category["id"], code=category["code"],
                             description=f"مسح تكلفة مصنعية الحرف {category['code']}", metadata={"before": None if before is None else str(before)}, occurred_at=repository.now())
            return {"id": str(category["id"]), "cost": None}
        return self._write(_expected_actor, work)
