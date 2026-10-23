"""تعديل رصيد الكسر: the owner / manager corrects the physical Scrap of one karat with a mandatory reason (a wrong entry, a recount). One counted ADJUSTMENT_IN / ADJUSTMENT_OUT movement is posted
(the history is never rewritten), the decrease can never take more than the AVAILABLE Scrap (held manager-review grams are excluded) so the balance never goes negative. Right OLD_GOLD_ADJUST,
rechecked in the transaction (the actor is the AppSession user); one request key makes a retry harmless."""
import uuid
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from repositories.old_gold_adjustment_repository import OldGoldAdjustmentRepository
from services.authorization_service import AuthorizationService
from services.invoice_service import invoice_transaction
from services.old_gold_availability import shortage_message

PERMISSION = "OLD_GOLD_ADJUST"
MAX_GRAMS = Decimal("100000.000")
THOUSANDTH = Decimal("0.001")


class OldGoldAdjustmentError(ValueError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def _grams(value):
    try:
        grams = Decimal(str(value).strip().replace(",", ""))
    except (InvalidOperation, ValueError, AttributeError):
        raise OldGoldAdjustmentError("GRAMS_INVALID", "اكتب الوزن بالأرقام.") from None
    if not grams.is_finite() or grams <= 0 or grams > MAX_GRAMS:
        raise OldGoldAdjustmentError("GRAMS_INVALID", "الوزن لازم يكون أكبر من صفر.")
    if grams != grams.quantize(THOUSANDTH, rounding=ROUND_HALF_UP):
        raise OldGoldAdjustmentError("GRAMS_INVALID", "الوزن بثلاث خانات عشرية على الأكثر.")
    return grams.quantize(THOUSANDTH)


class OldGoldAdjustmentService:
    def __init__(self, session, *, transaction_factory=invoice_transaction, repository=OldGoldAdjustmentRepository, authorization=None):
        self._session = session
        self._transaction = transaction_factory
        self._repository = repository
        self._authorization = authorization or AuthorizationService(session)

    def can_adjust(self):
        actor = self._session.current_actor()
        return actor is not None and PERMISSION in actor.permissions

    def balances(self, *, _expected_actor=None):
        """The active karats with their physical, held and available grams (for the adjustment form)."""
        actor = self._session.require_authenticated()
        if _expected_actor is not None and not self._session.is_current(_expected_actor):
            raise PermissionError("انتهت جلسة الدخول.")
        if PERMISSION not in actor.permissions:
            raise PermissionError("ليس لديك صلاحية تعديل الكسر.")
        with self._transaction() as connection:
            rows = self._repository(connection).balances()
        out = []
        for r in rows:
            physical, held = Decimal(str(r["physical_grams"])), Decimal(str(r["held_grams"]))
            out.append({"karat_id": str(r["karat_id"]), "name": r["karat_name"], "karat_value": r["karat_value"], "physical": str(physical.quantize(THOUSANDTH)),
                        "held": str(held.quantize(THOUSANDTH)), "available": str((physical - held).quantize(THOUSANDTH))})
        return out

    def adjust(self, karat_id, direction, grams, reason, request_key, *, _expected_actor=None):
        session_actor = self._session.require_authenticated()
        if _expected_actor is not None and not self._session.is_current(_expected_actor):
            raise PermissionError("انتهت جلسة الدخول.")
        if PERMISSION not in session_actor.permissions:
            raise PermissionError("ليس لديك صلاحية تعديل الكسر.")
        if direction not in ("IN", "OUT"):
            raise OldGoldAdjustmentError("DIRECTION_INVALID", "اختر زيادة أو نقص.")
        try:
            karat = int(str(karat_id))
        except (TypeError, ValueError):
            raise OldGoldAdjustmentError("KARAT_INVALID", "اختر العيار.") from None
        value = _grams(grams)
        reason = str(reason or "").strip()
        if not reason:
            raise OldGoldAdjustmentError("REASON_REQUIRED", "اكتب سبب التعديل.")
        if len(reason) > 500:
            raise OldGoldAdjustmentError("REASON_TOO_LONG", "السبب 500 حرف على الأكثر.")
        try:
            key = str(uuid.UUID(str(request_key)))
        except (ValueError, TypeError, AttributeError):
            raise OldGoldAdjustmentError("REQUEST_INVALID", "أعد فتح النموذج وحاول مرة أخرى.") from None
        delta = value if direction == "IN" else -value
        with self._transaction() as connection:
            actor = self._authorization.require_permission_for_write(connection, session_actor, PERMISSION)
            repository = self._repository(connection)
            row = repository.karat(karat)
            if row is None or not row["is_active"]:
                raise OldGoldAdjustmentError("KARAT_INVALID", "العيار غير موجود أو غير نشط.")
            physical, held = repository.lock_ledger(karat)
            prior = repository.by_group(key)
            if prior is not None:                                                    # an exact retry answers with the original, a different payload is refused
                if int(prior["karat_id"]) != karat or Decimal(str(prior["weight_delta_grams"])) != delta or int(prior["performed_by_user_id"]) != int(actor.user_id):
                    raise OldGoldAdjustmentError("REQUEST_CONFLICT", "طلب التعديل مستخدم لعملية مختلفة.")
                return {"movement_id": str(prior["id"]), "karat": row["karat_value"], "delta": str(delta), "physical_after": str(physical), "replayed": True}
            available = physical - held
            if direction == "OUT" and value > available:
                raise OldGoldAdjustmentError("SCRAP_SHORTAGE", shortage_message(row["karat_value"], value, available, held))
            movement_id = repository.insert_adjustment(direction=direction, karat_id=karat, delta=delta, reason=reason, group=key, user_id=actor.user_id)
            text = f"{'زيادة' if direction == 'IN' else 'نقص'} رصيد الكسر عيار {row['karat_value']} بمقدار {value:,.3f} جرام"
            repository.audit(user_id=actor.user_id, movement_id=movement_id, description=text,
                             metadata={"reason": reason, "direction": direction, "karat": row["karat_value"], "grams": str(value), "physical_before": str(physical),
                                       "physical_after": str(physical + delta)})
            if not self._session.is_current(session_actor):
                raise PermissionError("انتهت جلسة الدخول.")
        return {"movement_id": movement_id, "karat": row["karat_value"], "delta": str(delta), "physical_after": str(physical + delta), "replayed": False}
