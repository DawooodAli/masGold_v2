"""رصيد الرفايع: the rafayea (رفايع, loose parts) is a loose part with no barcode, so it is kept as WEIGHT ONLY per karat (migration 098, insert-only signed ledger, balance = SUM). The employee
enters the weight («إضافة وزن») or corrects it («زيادة» / «نقص» with a mandatory reason). A removal can never take the balance below zero. Right CLASP_STOCK_MANAGE is rechecked in
the transaction (the actor is the AppSession user); one request key makes a retry harmless. The invoice sale of a clasp is a separate slice."""
import uuid
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from repositories.clasp_stock_repository import ClaspStockRepository
from services.authorization_service import AuthorizationService
from services.invoice_service import invoice_transaction

PERMISSION = "CLASP_STOCK_MANAGE"
MAX_GRAMS = Decimal("100000.000")
THOUSANDTH = Decimal("0.001")
SIGNS = {"ENTRY": 1, "ADJUST_IN": 1, "ADJUST_OUT": -1}
LABELS = {"ENTRY": "إضافة وزن", "ADJUST_IN": "زيادة", "ADJUST_OUT": "نقص", "SALE_OUT": "بيع", "SALE_RETURN": "مرتجع", "SALE_REVERSAL": "عكس بيع"}


class ClaspStockError(ValueError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def _grams(value):
    try:
        grams = Decimal(str(value).strip().replace(",", ""))
    except (InvalidOperation, ValueError, AttributeError):
        raise ClaspStockError("GRAMS_INVALID", "اكتب الوزن بالأرقام.") from None
    if not grams.is_finite() or grams <= 0 or grams > MAX_GRAMS:
        raise ClaspStockError("GRAMS_INVALID", "الوزن لازم يكون أكبر من صفر.")
    if grams != grams.quantize(THOUSANDTH, rounding=ROUND_HALF_UP):
        raise ClaspStockError("GRAMS_INVALID", "الوزن بثلاث خانات عشرية على الأكثر.")
    return grams.quantize(THOUSANDTH)


class ClaspStockService:
    def __init__(self, session, *, transaction_factory=invoice_transaction, repository=ClaspStockRepository, authorization=None):
        self._session = session
        self._transaction = transaction_factory
        self._repository = repository
        self._authorization = authorization or AuthorizationService(session)

    def can_manage(self):
        actor = self._session.current_actor()
        return actor is not None and PERMISSION in actor.permissions

    def overview(self, *, _expected_actor=None):
        """The grams per karat and the latest movements (anyone who may see the inventory may read; only the manage right writes)."""
        actor = self._session.require_authenticated()
        if _expected_actor is not None and not self._session.is_current(_expected_actor):
            raise PermissionError("انتهت جلسة الدخول.")
        if PERMISSION not in actor.permissions and "INVENTORY_VIEW" not in actor.permissions:
            raise PermissionError("ليس لديك صلاحية عرض رصيد الرفايع.")
        with self._transaction() as connection:
            repository = self._repository(connection)
            if not repository.has_schema():
                raise ClaspStockError("SCHEMA_OUTDATED", "قاعدة البيانات تحتاج تحديث 098 قبل استخدام رصيد الرفايع.")
            balances = repository.balances()
            recent = repository.recent()
        return {"balances": [{"karat_id": str(r["karat_id"]), "name": r["karat_name"], "karat_value": r["karat_value"], "grams": str(Decimal(str(r["grams"])).quantize(THOUSANDTH))} for r in balances],
                "recent": [{"id": str(r["id"]), "karat": r["karat_value"], "kind": LABELS.get(r["movement_code"], r["movement_code"]), "delta": str(Decimal(str(r["delta_grams"])).quantize(THOUSANDTH)),
                            "reason": r["reason"], "at": str(r["created_at"])[:16], "user": r["username"]} for r in recent]}

    def receive(self, karat_id, grams, reason, request_key, *, _expected_actor=None):
        """Weight entered into the clasp stock."""
        return self._post("ENTRY", karat_id, grams, reason, request_key, _expected_actor)

    def adjust(self, karat_id, direction, grams, reason, request_key, *, _expected_actor=None):
        if direction not in ("IN", "OUT"):
            raise ClaspStockError("DIRECTION_INVALID", "اختر زيادة أو نقص.")
        return self._post("ADJUST_IN" if direction == "IN" else "ADJUST_OUT", karat_id, grams, reason, request_key, _expected_actor)

    def _post(self, code, karat_id, grams, reason, request_key, expected_actor):
        session_actor = self._session.require_authenticated()
        if expected_actor is not None and not self._session.is_current(expected_actor):
            raise PermissionError("انتهت جلسة الدخول.")
        if PERMISSION not in session_actor.permissions:
            raise PermissionError("ليس لديك صلاحية تعديل رصيد الرفايع.")
        try:
            karat = int(str(karat_id))
        except (TypeError, ValueError):
            raise ClaspStockError("KARAT_INVALID", "اختر العيار.") from None
        value = _grams(grams)
        reason = str(reason or "").strip()
        if not reason:
            raise ClaspStockError("REASON_REQUIRED", "اكتب سبب الحركة." if code == "ENTRY" else "اكتب سبب التعديل.")
        if len(reason) > 500:
            raise ClaspStockError("REASON_TOO_LONG", "السبب 500 حرف على الأكثر.")
        try:
            key = str(uuid.UUID(str(request_key)))
        except (ValueError, TypeError, AttributeError):
            raise ClaspStockError("REQUEST_INVALID", "أعد فتح النموذج وحاول مرة أخرى.") from None
        delta = value * SIGNS[code]
        with self._transaction() as connection:
            actor = self._authorization.require_permission_for_write(connection, session_actor, PERMISSION)
            repository = self._repository(connection)
            if not repository.has_schema():
                raise ClaspStockError("SCHEMA_OUTDATED", "قاعدة البيانات تحتاج تحديث 098 قبل استخدام رصيد الرفايع.")
            row = repository.karat(karat)
            if row is None or not row["is_active"]:
                raise ClaspStockError("KARAT_INVALID", "العيار غير موجود أو غير نشط.")
            before = repository.lock_balance(karat)
            prior = repository.by_key(key)
            if prior is not None:                                                    # an exact retry answers with the original, a different payload is refused
                if int(prior["karat_id"]) != karat or Decimal(str(prior["delta_grams"])) != delta or prior["movement_code"] != code or int(prior["created_by_user_id"]) != int(actor.user_id):
                    raise ClaspStockError("REQUEST_CONFLICT", "طلب الرفايع مستخدم لعملية مختلفة.")
                return {"movement_id": str(prior["id"]), "karat": row["karat_value"], "delta": str(delta), "balance_after": str(before), "replayed": True}
            if delta < 0 and value > before:
                raise ClaspStockError("CLASP_SHORTAGE", f"رصيد الرفايع عيار {row['karat_value']} {before:,.3f} جرام فقط، لا يكفي لنقص {value:,.3f}.")
            movement_id = repository.insert(karat_id=karat, code=code, delta=delta, reason=reason, request_key=key, user_id=actor.user_id)
            repository.audit(user_id=actor.user_id, movement_id=movement_id, description=f"{LABELS[code]} رفايع عيار {row['karat_value']} بمقدار {value:,.3f} جرام",
                             metadata={"code": code, "karat": row["karat_value"], "grams": str(value), "reason": reason, "balance_before": str(before), "balance_after": str(before + delta)})
            if not self._session.is_current(session_actor):
                raise PermissionError("انتهت جلسة الدخول.")
        return {"movement_id": movement_id, "karat": row["karat_value"], "delta": str(delta), "balance_after": str(before + delta), "replayed": False}
