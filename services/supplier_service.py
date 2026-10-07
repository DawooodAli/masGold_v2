"""Supplier master and supplier balances / ledgers (Purchasing & Suppliers V1).

Permissions (explicit grants, never an implicit OWNER bypass; re-checked inside the transaction):
  SUPPLIER_VIEW    read suppliers, balances, ledgers (non-locking read path)
  SUPPLIER_MANAGE  create / edit / activate / deactivate / delete-unused (strict locking write path)
The actor is ALWAYS the authenticated AppSession user; nothing here accepts an identity from the caller or QML.

Balances are SUM(delta) of the immutable ledgers: delta > 0 = the shop owes the supplier (علينا للمورد),
delta < 0 = the supplier owes the shop (لنا عند المورد). Gold and making are reported independently.
"""
from decimal import Decimal

from mysql.connector import IntegrityError

from repositories.supplier_repository import SupplierRepository
from services import supplier_rules as rules
from services.authorization_service import AuthorizationService
from services.invoice_service import invoice_transaction
from services.number_text import normalize_digits

VIEW = "SUPPLIER_VIEW"
MANAGE = "SUPPLIER_MANAGE"

NAME_MAX, PHONE_MAX, NOTES_MAX = 150, 50, 1000


def authenticated_actor(session, permission, message, expected_actor=None):
    """The session actor, refused early when the session is stale or lacks the permission (UI hint layer; the
    transaction re-verifies against the database)."""
    actor = session.require_authenticated()
    if expected_actor is not None and not session.is_current(expected_actor):
        raise PermissionError("انتهت جلسة الدخول.")
    if permission not in actor.permissions:
        raise PermissionError(message)
    return actor


def clean_text(value, limit, label, required=False):
    text = normalize_digits(str(value or "")).strip() if label == "رقم الهاتف" else str(value or "").strip()
    if required and not text:
        raise rules.SupplierRuleError("TEXT_REQUIRED", f"{label} مطلوب.")
    if len(text) > limit:
        raise rules.SupplierRuleError("TEXT_LONG", f"{label} أطول من الحد المسموح.")
    return text or None


def balance_row(gold_rows, making_balance):
    """The two independent positions of one supplier (Decimals, with the explicit owes-direction)."""
    karats = [{"karat_id": r["karat_id"], "karat_value": int(r["karat_value"]),
               "weight_balance": Decimal(str(r["weight_balance"])).quantize(rules.WEIGHT_STEP),
               "eq21_balance": Decimal(str(r["eq21_balance"])),
               "direction": rules.direction(r["weight_balance"])} for r in gold_rows]
    eq21 = sum((k["eq21_balance"] for k in karats), Decimal("0"))
    making = Decimal(str(making_balance or 0)).quantize(rules.MONEY_STEP)
    return {"gold_by_karat": karats, "gold_eq21": eq21.quantize(rules.WEIGHT_STEP),
            "gold_direction": rules.direction(eq21.quantize(rules.WEIGHT_STEP)),
            "making": making, "making_direction": rules.direction(making)}


class SupplierService:
    def __init__(self, session, *, transaction_factory=invoice_transaction, repository=SupplierRepository,
                 authorization=None):
        self._session, self._transaction, self._repository = session, transaction_factory, repository
        self._authorization = authorization or AuthorizationService(session)

    # ------------------------------------------------------------------ UI hints (never the authority)
    def can_view(self):
        actor = self._session.current_actor()
        return actor is not None and VIEW in actor.permissions

    def can_manage(self):
        actor = self._session.current_actor()
        return actor is not None and MANAGE in actor.permissions

    # ------------------------------------------------------------------ authorization paths
    def _read_verified(self, connection, actor):
        """Non-locking database re-check for the read-only supplier screens."""
        return self._authorization.require_permission_read(connection, actor, VIEW)

    def _write_verified(self, connection, actor):
        """Strict (locking) write path: a revoked grant refuses the write even with a stale session."""
        return self._authorization.require_permission_for_write(connection, actor, MANAGE)

    # ------------------------------------------------------------------ reads
    def list_suppliers(self, search="", active_only=False, *, _expected_actor=None):
        actor = authenticated_actor(self._session, VIEW, "لا توجد صلاحية لعرض الموردين.", _expected_actor)
        with self._transaction() as connection:
            self._read_verified(connection, actor)
            rows = self._repository(connection).list_suppliers(
                str(search or "").strip() or None, True if active_only else None)
        result = []
        for row in rows:
            eq21 = Decimal(str(row["gold_eq21_balance"])).quantize(rules.WEIGHT_STEP)
            making = Decimal(str(row["making_balance"])).quantize(rules.MONEY_STEP)
            result.append({"id": str(row["id"]), "code": rules.supplier_code(row["id"]), "name": row["name"],
                           "phone": row["phone"] or "", "notes": row["notes"] or "",
                           "is_active": bool(row["is_active"]), "gold_eq21": eq21,
                           "gold_direction": rules.direction(eq21), "making": making,
                           "making_direction": rules.direction(making),
                           "posted_invoice_count": int(row["posted_invoice_count"])})
        return result

    def supplier_detail(self, supplier_id, *, _expected_actor=None):
        """Balances (per karat, Eq21 and making) plus both ledgers with their running balances."""
        actor = authenticated_actor(self._session, VIEW, "لا توجد صلاحية لعرض الموردين.", _expected_actor)
        supplier_id = _supplier_id(supplier_id)
        with self._transaction() as connection:
            self._read_verified(connection, actor)
            repository = self._repository(connection)
            supplier = repository.get_supplier(supplier_id)
            if supplier is None:
                raise rules.SupplierRuleError("SUPPLIER_NOT_FOUND", "المورد غير موجود.")
            balances = balance_row(repository.gold_balances(supplier_id), repository.making_balance(supplier_id))
            gold = repository.gold_ledger(supplier_id)
            making = repository.making_ledger(supplier_id)
        return {"supplier": {"id": str(supplier["id"]), "code": rules.supplier_code(supplier["id"]),
                             "name": supplier["name"], "phone": supplier["phone"] or "",
                             "notes": supplier["notes"] or "", "is_active": bool(supplier["is_active"])},
                "balances": balances,
                "gold_ledger": [{"id": str(r["id"]), "occurred_at": r["occurred_at"], "movement": r["movement_name"],
                                 "karat_value": int(r["karat_value"]),
                                 "weight_delta": Decimal(str(r["weight_delta_grams"])),
                                 "eq21_delta": Decimal(str(r["equivalent_21_delta_grams"])),
                                 "eq21_balance_after": Decimal(str(r["eq21_balance_after"])),
                                 "source_type": r["source_type"], "source_id": str(r["source_id"]),
                                 "reference": r["source_reference"] or "", "actor": r["actor_name"] or ""}
                                for r in gold],
                "making_ledger": [{"id": str(r["id"]), "occurred_at": r["occurred_at"], "movement": r["movement_name"],
                                   "amount_delta": Decimal(str(r["amount_delta"])),
                                   "balance_after": Decimal(str(r["balance_after"])),
                                   "source_type": r["source_type"], "source_id": str(r["source_id"]),
                                   "reference": r["source_reference"] or "", "actor": r["actor_name"] or ""}
                                  for r in making]}

    # ------------------------------------------------------------------ master writes
    def create_supplier(self, name, phone=None, notes=None, *, _expected_actor=None):
        actor = authenticated_actor(self._session, MANAGE, "لا توجد صلاحية لإدارة الموردين.", _expected_actor)
        name = clean_text(name, NAME_MAX, "اسم المورد", required=True)
        phone, notes = clean_text(phone, PHONE_MAX, "رقم الهاتف"), clean_text(notes, NOTES_MAX, "الملاحظات")
        try:
            with self._transaction() as connection:
                actor = self._write_verified(connection, actor)
                repository = self._repository(connection)
                supplier_id = repository.create_supplier(name=name, phone=phone, notes=notes, user_id=actor.user_id)
                repository.audit(actor=actor, event_type="SUPPLIER_CREATED", entity_id=supplier_id,
                                 reference=rules.supplier_code(supplier_id), description=f"إنشاء مورد {name}",
                                 metadata={"name": name}, now=repository.db_now())
        except IntegrityError as exc:
            raise _duplicate_name(exc) from exc
        return str(supplier_id)

    def update_supplier(self, supplier_id, name, phone=None, notes=None, *, _expected_actor=None):
        actor = authenticated_actor(self._session, MANAGE, "لا توجد صلاحية لإدارة الموردين.", _expected_actor)
        supplier_id = _supplier_id(supplier_id)
        name = clean_text(name, NAME_MAX, "اسم المورد", required=True)
        phone, notes = clean_text(phone, PHONE_MAX, "رقم الهاتف"), clean_text(notes, NOTES_MAX, "الملاحظات")
        try:
            with self._transaction() as connection:
                actor = self._write_verified(connection, actor)
                repository = self._repository(connection)
                if repository.get_supplier(supplier_id, lock=True) is None:
                    raise rules.SupplierRuleError("SUPPLIER_NOT_FOUND", "المورد غير موجود.")
                repository.update_supplier(supplier_id=supplier_id, name=name, phone=phone, notes=notes,
                                           user_id=actor.user_id)
                repository.audit(actor=actor, event_type="SUPPLIER_UPDATED", entity_id=supplier_id,
                                 reference=rules.supplier_code(supplier_id), description=f"تعديل بيانات المورد {name}",
                                 metadata={"name": name}, now=repository.db_now())
        except IntegrityError as exc:
            raise _duplicate_name(exc) from exc

    def set_active(self, supplier_id, active, *, _expected_actor=None):
        """Deactivate / reactivate. History and balances are untouched; a deactivated supplier takes no new invoice."""
        actor = authenticated_actor(self._session, MANAGE, "لا توجد صلاحية لإدارة الموردين.", _expected_actor)
        supplier_id = _supplier_id(supplier_id)
        with self._transaction() as connection:
            actor = self._write_verified(connection, actor)
            repository = self._repository(connection)
            if repository.get_supplier(supplier_id, lock=True) is None:
                raise rules.SupplierRuleError("SUPPLIER_NOT_FOUND", "المورد غير موجود.")
            repository.set_active(supplier_id=supplier_id, active=bool(active), user_id=actor.user_id)
            repository.audit(actor=actor, event_type="SUPPLIER_ACTIVATED" if active else "SUPPLIER_DEACTIVATED",
                             entity_id=supplier_id, reference=rules.supplier_code(supplier_id),
                             description="تفعيل المورد" if active else "إيقاف المورد", metadata={"active": bool(active)},
                             now=repository.db_now())

    def delete_unused_supplier(self, supplier_id, *, _expected_actor=None):
        """Permanent delete of a supplier that nothing references (Master Data semantics). A supplier with any
        invoice or ledger row is refused: deactivate it instead."""
        actor = authenticated_actor(self._session, MANAGE, "لا توجد صلاحية لإدارة الموردين.", _expected_actor)
        supplier_id = _supplier_id(supplier_id)
        with self._transaction() as connection:
            actor = self._write_verified(connection, actor)
            repository = self._repository(connection)
            supplier = repository.get_supplier(supplier_id, lock=True)
            if supplier is None:
                raise rules.SupplierRuleError("SUPPLIER_NOT_FOUND", "المورد غير موجود.")
            if repository.reference_count(supplier_id):
                raise rules.SupplierRuleError("SUPPLIER_IN_USE", "المورد مرتبط بفواتير أو حركات ولا يمكن حذفه. "
                                              "أوقفه بدلاً من ذلك.")
            repository.delete_supplier(supplier_id)
            repository.audit(actor=actor, event_type="SUPPLIER_DELETED", entity_id=supplier_id,
                             reference=rules.supplier_code(supplier_id), description=f"حذف مورد غير مستخدم {supplier['name']}",
                             metadata={"name": supplier["name"]}, now=repository.db_now())


def _supplier_id(value):
    try:
        number = int(str(value).strip())
    except (TypeError, ValueError):
        raise rules.SupplierRuleError("SUPPLIER_NOT_FOUND", "المورد غير موجود.") from None
    if number <= 0:
        raise rules.SupplierRuleError("SUPPLIER_NOT_FOUND", "المورد غير موجود.")
    return number


def _duplicate_name(exc):
    if getattr(exc, "errno", None) == 1062:
        return rules.SupplierRuleError("SUPPLIER_EXISTS", "يوجد مورد بنفس الاسم.")
    return exc
