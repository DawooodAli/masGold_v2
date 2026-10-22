"""Minimum stock levels (Settings → الحد الأدنى للمخزون): the owner says how many pieces and / or grams of an item TYPE of a KARAT he wants to keep; the inventory alerts report lists every one
that is below. A rule only alerts - nothing is ordered or changed. Right: SETTINGS_MANAGE, rechecked inside every write transaction (the actor is the AppSession user); every change is audited.
One rule per (type, karat): saving again replaces it. At least one of pieces (a whole number from 1) or grams (positive, three decimals at most) is required."""
from decimal import Decimal, InvalidOperation

from repositories.stock_min_level_repository import StockMinLevelRepository
from services.authorization_service import AuthorizationService
from services.invoice_service import invoice_transaction

MANAGE = "SETTINGS_MANAGE"
MAX_PIECES = 1_000_000
MAX_GRAMS = Decimal("100000000")


class LevelError(ValueError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def parse_pieces(value):
    text = str(value if value is not None else "").strip().replace(",", "")
    if not text:
        return None
    if not text.isdigit() or not 1 <= int(text) <= MAX_PIECES:
        raise LevelError("PIECES_INVALID", "عدد القطع رقم صحيح من 1 فأكثر.")
    return int(text)


def parse_grams(value):
    text = str(value if value is not None else "").strip().replace(",", "")
    if not text:
        return None
    try:
        grams = Decimal(text)
    except InvalidOperation:
        raise LevelError("GRAMS_INVALID", "الوزن رقم بثلاث خانات عشرية على الأكثر.") from None
    if not grams.is_finite() or grams <= 0 or grams >= MAX_GRAMS or grams != grams.quantize(Decimal("0.001")):
        raise LevelError("GRAMS_INVALID", "الوزن رقم موجب بثلاث خانات عشرية على الأكثر.")
    return grams.quantize(Decimal("0.001"))


class StockMinLevelService:
    def __init__(self, session, *, transaction_factory=invoice_transaction, repository=StockMinLevelRepository, authorization=None):
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

    def overview(self, *, _expected_actor=None):
        """The choices for the form (active types and karats) and every rule with what is in stock now."""
        actor = self._actor(_expected_actor)
        if MANAGE not in actor.permissions:
            raise PermissionError("الحد الأدنى للمخزون للمالك فقط.")
        with self._transaction() as connection:
            self._authorization.require_permission_read(connection, actor, MANAGE)
            repository = self._repository(connection)
            return {"types": [{"id": str(t["id"]), "name": t["name"]} for t in repository.item_types()],
                    "karats": [{"id": str(k["id"]), "value": int(k["karat_value"])} for k in repository.karats()],
                    "levels": [{"id": str(r["id"]), "type": r["item_type"], "karat": int(r["karat_value"]), "min_pieces": r["min_pieces"],
                                "min_grams": None if r["min_grams"] is None else Decimal(str(r["min_grams"])), "pieces": int(r["pieces"]), "grams": Decimal(str(r["grams"]))}
                               for r in repository.levels()]}

    def _write(self, expected_actor, work):
        session_actor = self._actor(expected_actor)
        with self._transaction() as connection:
            actor = self._authorization.require_permission_for_write(connection, session_actor, MANAGE)
            result = work(self._repository(connection), actor)
            if not self._session.is_current(session_actor):
                raise PermissionError("انتهت جلسة الدخول.")
        return result

    def save(self, item_type_id, karat_id, min_pieces, min_grams, *, _expected_actor=None):
        pieces, grams = parse_pieces(min_pieces), parse_grams(min_grams)
        if pieces is None and grams is None:
            raise LevelError("AMOUNT_REQUIRED", "اكتب الحد الأدنى بالقطع أو بالجرامات (أو الاثنين).")

        def work(repository, actor):
            item_type, karat = repository.type_exists(int(item_type_id)), repository.karat_exists(int(karat_id))
            if item_type is None:
                raise LevelError("TYPE_NOT_FOUND", "اختر صنفًا صحيحًا.")
            if karat is None:
                raise LevelError("KARAT_NOT_FOUND", "اختر عيارًا صحيحًا.")
            level_id = repository.upsert(item_type_id=item_type["id"], karat_id=karat["id"], min_pieces=pieces, min_grams=grams, user_id=actor.user_id)
            repository.audit(user_id=actor.user_id, event_type="STOCK_MIN_LEVEL_SET", level_id=level_id, description=f"حد أدنى للمخزون: {item_type['name']} عيار {karat['karat_value']}",
                             metadata={"pieces": pieces, "grams": None if grams is None else str(grams)}, occurred_at=repository.now())
            return {"id": str(level_id)}
        return self._write(_expected_actor, work)

    def remove(self, level_id, *, _expected_actor=None):
        def work(repository, actor):
            row = repository.level(int(level_id), lock=True)
            if row is None:
                raise LevelError("NOT_FOUND", "الحد غير موجود.")
            repository.delete(row["id"])
            repository.audit(user_id=actor.user_id, event_type="STOCK_MIN_LEVEL_REMOVED", level_id=row["id"], description="حذف حد أدنى للمخزون",
                             metadata={"item_type_id": row["item_type_id"], "karat_id": row["karat_id"]}, occurred_at=repository.now())
            return {"id": str(row["id"]), "deleted": True}
        return self._write(_expected_actor, work)
