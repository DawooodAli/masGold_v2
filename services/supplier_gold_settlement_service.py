"""Direct supplier GOLD settlement from available shop Scrap (045): the shop delivers Scrap of a selected master karat to a SUPPLIER against its gold
account.

A settlement (SGxxxxxx) is one immutable document that posts, atomically in ONE transaction:
  * one COUNTED Scrap OUT of the DELIVERED karat (the actual physical grams, deducted once; Scrap held for manager review is not available), and
  * one NEGATIVE supplier gold movement in the supplier's DEDICATED karat (the shop owes the supplier less).
credited grams = delivered grams x delivered karat / supplier karat (services.supplier_rules.settlement_credit): the POSTED credit is derived straight
from the high-precision calculation (0.001 g, ROUND_HALF_UP); the six-decimal analytical value and the rounding difference are stored with the
original weight, both karats and the posted credit. Supplier balances stay grams of the dedicated karat. No cash, making, valuation, sale or purchase.

The recipient needs the SUPPLIER role (an intermediary-only party is refused) and a resolved karat. An inactive supplier may receive a settlement and
the payable may cross zero into supplier credit (as with making payments); the Scrap OUT is limited to what is available.

Permissions (explicit grants, re-checked inside the transaction through AuthorizationService; the actor is the AppSession user, never a caller identity):
  SUPPLIER_GOLD_SETTLE          OWNER, MANAGER   post a settlement
  SUPPLIER_GOLD_SETTLE_REVERSE  OWNER only       reverse one (through CorrectionService, which also requires TRANSACTION_CORRECT)

Idempotent on the request key (same key + same payload + same user returns the first result; anything else is a REQUEST_CONFLICT).
Lock order: actor -> supplier row -> Scrap karat balance -> request key (a reversal takes the same order). No path takes Scrap before the supplier.
"""
from datetime import date, datetime
from decimal import Decimal
import hashlib
import json
from uuid import UUID

from mysql.connector import IntegrityError

from repositories.old_gold_conversion_repository import OldGoldConversionRepository
from repositories.market_trip_repository import entry_code as _custody_code
from repositories.supplier_gold_settlement_repository import (CUSTODY_SOURCE_KIND, GOLD_MOVEMENT, SOURCE, SOURCE_KIND,
                                                              SupplierGoldSettlementRepository, settlement_code)
from services import supplier_rules as rules
from services.authorization_service import AuthorizationService
from services.correction_rules import correction_code
from services.invoice_service import invoice_transaction
from services.number_text import normalize_digits
from services.old_gold_availability import held_grams, shortage_message
from services.supplier_service import authenticated_actor, clean_text

SETTLE, REVERSE = "SUPPLIER_GOLD_SETTLE", "SUPPLIER_GOLD_SETTLE_REVERSE"
REFERENCE_MAX, NOTES_MAX = 100, 1000


def _key(value):
    try:
        return str(UUID(str(value)))
    except (ValueError, TypeError, AttributeError):
        raise rules.SupplierRuleError("REQUEST_INVALID", "أعد فتح النموذج وحاول مرة أخرى.") from None


def _is_request_key_violation(exc):
    return getattr(exc, "errno", None) == 1062 and "uq_sgs_request" in str(exc)


def _id(value, code, message):
    try:
        number = int(str(value).strip())
    except (TypeError, ValueError):
        raise rules.SupplierRuleError(code, message) from None
    if number <= 0:
        raise rules.SupplierRuleError(code, message)
    return number


def _date(value):
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = normalize_digits(str(value or "")).strip()
    if not text:
        raise rules.SupplierRuleError("DATE_REQUIRED", "التاريخ مطلوب.")
    try:
        return date.fromisoformat(text)
    except ValueError:
        raise rules.SupplierRuleError("DATE_INVALID", "التاريخ غير صحيح.") from None


class SupplierGoldSettlementService:
    def __init__(self, session, *, transaction_factory=invoice_transaction, repository=SupplierGoldSettlementRepository,
                 scrap=OldGoldConversionRepository, authorization=None, corrections=None):
        self._session, self._transaction, self._repository, self._scrap = session, transaction_factory, repository, scrap
        self._authorization = authorization or AuthorizationService(session)
        self._corrections = corrections

    # ------------------------------------------------------------------ UI hints (never the authority)
    def can_settle(self):
        actor = self._session.current_actor()
        return actor is not None and SETTLE in actor.permissions

    def can_reverse(self):
        actor = self._session.current_actor()
        return actor is not None and REVERSE in actor.permissions

    # ------------------------------------------------------------------ reads
    def settlement_context(self, supplier_id, *, _expected_actor=None):
        """What the dialog needs: the recipient and the SHOP Scrap available per active karat (custody gold is never in it)."""
        actor = authenticated_actor(self._session, SETTLE, "ليس لديك صلاحية تسوية ذهب الموردين.", _expected_actor)
        supplier_number = _id(supplier_id, "SUPPLIER_NOT_FOUND", "المورد غير موجود.")
        with self._transaction() as connection:
            self._authorization.require_permission_read(connection, actor, SETTLE)
            repository = self._repository(connection)
            supplier = repository.get_supplier(supplier_number)
            if supplier is None:
                raise rules.SupplierRuleError("SUPPLIER_NOT_FOUND", "المورد غير موجود.")
            karats, scrap = repository.active_karats(), {int(r["karat_id"]): r for r in repository.shop_scrap()}
        return {"supplier": {"id": str(supplier["id"]), "code": rules.supplier_code(supplier["id"]), "name": supplier["name"],
                             "is_active": bool(supplier["is_active"]), "is_supplier": rules.has_supplier_role(supplier),
                             "karat_id": supplier["karat_id"],
                             "karat_value": None if supplier["karat_value"] is None else int(supplier["karat_value"])},
                "scrap": [{"karat_id": int(k["id"]), "karat_value": int(k["karat_value"]),
                           "available": Decimal(str(scrap[int(k["id"])]["available_grams"])) if int(k["id"]) in scrap else Decimal("0.000"),
                           "held": Decimal(str(scrap[int(k["id"])]["held_grams"])) if int(k["id"]) in scrap else Decimal("0.000")}
                          for k in karats]}

    def settlement_detail(self, settlement_id, *, _expected_actor=None):
        actor = authenticated_actor(self._session, SETTLE, "ليس لديك صلاحية تسوية ذهب الموردين.", _expected_actor)
        with self._transaction() as connection:
            self._authorization.require_permission_read(connection, actor, SETTLE)
            row = self._repository(connection).settlement(_id(settlement_id, "SETTLEMENT_NOT_FOUND", "التسوية غير موجودة."))
            if row is None:
                raise rules.SupplierRuleError("SETTLEMENT_NOT_FOUND", "التسوية غير موجودة.")
        return self.document(row)

    @staticmethod
    def document(row):
        return {"id": str(row["id"]), "code": settlement_code(row["id"]), "supplier_id": str(row["supplier_id"]),
                "supplier_name": row["supplier_name"], "delivered_karat": None if row["delivered_karat_value"] is None else int(row["delivered_karat_value"]),
                "target_karat": int(row["target_karat_value"]), "delivered_weight": row["delivered_weight_grams"],
                "credited_weight": row["credited_weight_grams"], "analytical": row["credited_analytical_grams"],
                "rounding_difference": row["rounding_difference_grams"], "settlement_date": row["settlement_date"],
                "reference": row["reference"] or "", "notes": row["notes"] or "", "actor": row.get("actor_name") or "",
                "occurred_at": row["occurred_at"], "source_kind": row.get("source_kind", SOURCE_KIND),
                # a settlement made from market custody (046): its custody entry, where the reversal belongs
                "custody_reference": (None if row.get("custody_entry_no") is None
                                      else _custody_code(row["custody_trip_id"], row["custody_entry_no"])),
                "custody_designation": row.get("custody_designation"),
                "lot_id": None if row.get("delivered_lot_id") is None else str(row["delivered_lot_id"]),
                "fineness": row.get("delivered_fineness_permille"), "lot_kind": row.get("lot_kind"), "lot_no": row.get("lot_no"),
                "lot_origin": None if row.get("origin_entry_no") is None else _custody_code(row["origin_trip_id"], row["origin_entry_no"]), "reversed": row.get("reversal_id") is not None,
                "reversal_code": None if row.get("reversal_id") is None else correction_code("FULL_REVERSAL", row["reversal_id"]),
                "reversal_reason": row.get("reversal_reason")}

    # ------------------------------------------------------------------ the settlement
    def settle(self, supplier_id, delivered_karat_id, delivered_weight, settlement_date, reference, notes, request_key, *,
               _expected_actor=None):
        session_actor = authenticated_actor(self._session, SETTLE, "ليس لديك صلاحية تسوية ذهب الموردين.", _expected_actor)
        supplier_number = _id(supplier_id, "SUPPLIER_NOT_FOUND", "المورد غير موجود.")
        karat_number = _id(delivered_karat_id, "KARAT_REQUIRED", "اختر عيار الذهب المُسلَّم.")
        weight = rules.parse_delivered_weight(delivered_weight)
        settled_on = _date(settlement_date)
        reference_text, notes_text = clean_text(reference, REFERENCE_MAX, "المرجع"), clean_text(notes, NOTES_MAX, "الملاحظات")
        key = _key(request_key)
        digest = hashlib.sha256(json.dumps([supplier_number, karat_number, str(weight), settled_on.isoformat(), reference_text, notes_text],
                                           ensure_ascii=False).encode()).hexdigest()
        try:
            return self._post(session_actor, supplier_number, karat_number, weight, settled_on, reference_text, notes_text, key, digest)
        except IntegrityError as exc:
            if not _is_request_key_violation(exc):
                raise                                          # any other integrity error is a real defect: never hidden
            return self._replay(session_actor, key, digest, exc)

    def _post(self, session_actor, supplier_id, karat_id, weight, settled_on, reference, notes, key, digest):
        with self._transaction() as connection:
            actor = self._authorization.require_permission_for_write(connection, session_actor, SETTLE)
            repository, scrap = self._repository(connection), self._scrap(connection)
            supplier = repository.get_supplier(supplier_id, lock=True)                        # lock order: supplier -> Scrap karat
            if supplier is None:
                raise rules.SupplierRuleError("SUPPLIER_NOT_FOUND", "المورد غير موجود.")
            delivered = repository.get_karat(karat_id)
            if delivered is None or not delivered["is_active"]:
                raise rules.SupplierRuleError("KARAT_NOT_FOUND", "عيار الذهب المُسلَّم غير موجود أو غير نشط.")
            available = sum((Decimal(str(r["weight_delta_grams"])) for r in scrap.lock_old_gold_balance(karat_id)), Decimal("0.000"))
            prior = repository.by_request(key)
            if prior is not None:
                return self._replayed(prior, digest, actor)
            if repository.request_key_used_elsewhere(key):                                   # a custody / holding operation owns this key
                raise rules.SupplierRuleError("REQUEST_CONFLICT", "طلب العملية مستخدم لعملية مختلفة.")
            rules.require_supplier_role(supplier)                                            # an intermediary-only party has no gold account
            if supplier["karat_id"] is None:
                raise rules.SupplierRuleError("SUPPLIER_KARAT_MISSING",
                                              "عيار هذا المورد غير محدد. حدّد عياره من تعديل المورد قبل تسوية الذهب له.")
            target = repository.get_karat(supplier["karat_id"])
            if weight > available:                                                           # only AVAILABLE Scrap: manager-review holds excluded
                raise rules.SupplierRuleError("SCRAP_SHORTAGE", shortage_message(int(delivered["karat_value"]), weight, available,
                                                                                 held_grams(scrap, karat_id)))
            credited, analytical, difference = rules.settlement_credit(weight, delivered["karat_value"], target["karat_value"])
            type_id = repository.gold_type_id(GOLD_MOVEMENT)
            if type_id is None:
                raise RuntimeError("supplier gold movement type SETTLEMENT is missing (migration 045)")
            now = repository.db_now()
            settlement_id = repository.insert_header(
                supplier_id=supplier_id, delivered_karat=delivered, target_karat=target, delivered_weight=weight, credited=credited,
                analytical=analytical, difference=difference, settlement_date=settled_on, reference=reference, notes=notes, key=key,
                digest=digest, actor=actor, now=now)
            code = settlement_code(settlement_id)
            label = (f"تسوية ذهب للمورد {code}: سُلِّم {weight:,.3f} جرام عيار {int(delivered['karat_value'])} "
                     f"← احتُسب {credited:,.3f} جرام عيار {int(target['karat_value'])}")
            scrap_id = repository.post_scrap_out(settlement_id=settlement_id, karat_id=karat_id, weight=weight, code=code,
                                                 notes=f"{label} — {supplier['name']}", actor=actor, now=now)
            gold_id = repository.insert_gold_movement(
                supplier_id=supplier_id, type_id=type_id, karat_id=target["id"], weight_delta=-credited,
                eq21_delta=-rules.equivalent_21(credited, target["karat_value"]), source_type=SOURCE, source_id=settlement_id,
                reference=code, description=label, now=now, user_id=actor.user_id)
            repository.insert_leg(settlement_id=settlement_id, kind="SCRAP", source_kind=SOURCE_KIND, old_gold_movement_id=scrap_id)
            repository.insert_leg(settlement_id=settlement_id, kind="GOLD", source_kind=SOURCE_KIND, supplier_gold_movement_id=gold_id)
            repository.audit(actor=actor, event_type="SUPPLIER_GOLD_SETTLEMENT", entity_id=settlement_id, entity_type=SOURCE, reference=code,
                             description=label,
                             metadata={"supplier_id": supplier_id, "delivered_karat": int(delivered["karat_value"]), "delivered_weight": str(weight),
                                       "target_karat": int(target["karat_value"]), "credited": str(credited), "analytical": str(analytical),
                                       "rounding_difference": str(difference), "settlement_date": settled_on.isoformat(), "reference": reference,
                                       "employee_id": actor.employee_id}, now=now)
            if not self._session.is_current(session_actor):
                raise PermissionError("انتهت جلسة الدخول.")
        return {"id": str(settlement_id), "code": code, "supplier_id": str(supplier_id), "delivered_karat": int(delivered["karat_value"]),
                "target_karat": int(target["karat_value"]), "delivered_weight": str(weight), "credited_weight": str(credited),
                "analytical": str(analytical), "rounding_difference": str(difference), "duplicate": False}

    @staticmethod
    def _replayed(prior, digest, actor):
        if prior["request_hash"] != digest or int(prior["performed_by_user_id"]) != int(actor.user_id):
            raise rules.SupplierRuleError("REQUEST_CONFLICT", "طلب التسوية مستخدم لعملية مختلفة.")
        return {"id": str(prior["id"]), "code": settlement_code(prior["id"]), "supplier_id": str(prior["supplier_id"]),
                "delivered_karat": int(prior["delivered_karat_value"]), "target_karat": int(prior["target_karat_value"]),
                "delivered_weight": str(prior["delivered_weight_grams"]), "credited_weight": str(prior["credited_weight_grams"]),
                "analytical": str(prior["credited_analytical_grams"]), "rounding_difference": str(prior["rounding_difference_grams"]),
                "duplicate": True}

    def _replay(self, session_actor, key, digest, original):
        """The losing side of a concurrent same-key request, in a new transaction: decided from a FRESH read, nothing is written."""
        with self._transaction() as connection:
            actor = self._authorization.require_permission_for_write(connection, session_actor, SETTLE)
            prior = self._repository(connection).by_request(key)
            if prior is None:
                raise original
            return self._replayed(prior, digest, actor)

    # ------------------------------------------------------------------ reversal (CorrectionService conventions)
    def reverse(self, settlement_id, reason, *, _expected_actor=None):
        """Full reversal (RV): the OWNER-only right is checked here as a hint and again, inside the transaction, by CorrectionService (together
        with TRANSACTION_CORRECT). The STORED delivered grams return to Scrap and the STORED credit is restored: nothing is recalculated."""
        authenticated_actor(self._session, REVERSE, "عكس تسوية ذهب المورد للمالك فقط.", _expected_actor)
        if self._corrections is None:
            from services.correction_service import CorrectionService
            self._corrections = CorrectionService(self._session)
        return self._corrections.reverse(SOURCE, str(settlement_id), reason, _expected_actor=_expected_actor)
