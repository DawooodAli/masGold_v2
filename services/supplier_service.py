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

from repositories.intermediary_holding_repository import IntermediaryHoldingRepository
from repositories.market_trip_repository import entry_code as _custody_code
from repositories.supplier_gold_settlement_repository import settlement_code as _settlement_code
from repositories.supplier_payment_repository import payment_code as _payment_code
from repositories.supplier_repository import SupplierRepository
from services import supplier_rules as rules
from services.authorization_service import AuthorizationService
from services.correction_rules import correction_code
from services.intermediary_holding_rules import (MANAGE_PERMISSION as MANAGE_HOLDINGS, TRIP_PERMISSION as TRIP_MANAGE, VIEW_PERMISSION as VIEW_HOLDINGS,
                                                 document_row, holding_settlement_row, logo_document_row, logo_pool_row, pool_row, receipt_row)
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


def _given(value):
    return value not in (None, "", 0, "0")


def balance_row(supplier, gold_rows, making_balance):
    """The two independent positions of one supplier. GOLD is in PHYSICAL grams of the supplier's dedicated karat (no
    Eq21); making is EGP. A supplier without a karat, or whose ledger holds another karat, is UNRESOLVED: its per-karat
    history stays readable and different karats are never added into one balance."""
    lines = [{"karat_id": r["karat_id"], "karat_value": int(r["karat_value"]),
              "weight_balance": Decimal(str(r["weight_balance"])).quantize(rules.WEIGHT_STEP),
              "direction": rules.direction(r["weight_balance"])} for r in gold_rows]
    karat_id = supplier.get("karat_id")
    resolved = karat_id is not None and all(line["karat_id"] == karat_id for line in lines)
    own = sum((line["weight_balance"] for line in lines if line["karat_id"] == karat_id), Decimal("0")) if resolved else None
    making = Decimal(str(making_balance or 0)).quantize(rules.MONEY_STEP)
    return {"gold_status": "RESOLVED" if resolved else "UNRESOLVED", "gold_by_karat": lines,
            "gold_karat_id": karat_id, "gold_karat_value": supplier.get("karat_value"),
            "gold_balance": None if own is None else own.quantize(rules.WEIGHT_STEP),
            "gold_direction": None if own is None else rules.direction(own.quantize(rules.WEIGHT_STEP)),
            "making": making, "making_direction": rules.direction(making)}


class SupplierService:
    def __init__(self, session, *, transaction_factory=invoice_transaction, repository=SupplierRepository,
                 authorization=None, holdings=IntermediaryHoldingRepository):
        self._session, self._transaction, self._repository = session, transaction_factory, repository
        self._holdings = holdings
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
    def list_suppliers(self, search="", active_only=False, role=None, *, _expected_actor=None):
        actor = authenticated_actor(self._session, VIEW, "لا توجد صلاحية لعرض الموردين.", _expected_actor)
        role = role or None
        if role not in rules.ROLE_FILTERS:
            raise rules.SupplierRuleError("ROLE_FILTER_INVALID", "تصفية الأدوار غير صحيحة.")
        with self._transaction() as connection:
            self._read_verified(connection, actor)
            rows = self._repository(connection).list_suppliers(
                str(search or "").strip() or None, True if active_only else None, role=role)
        result = []
        for row in rows:
            is_supplier, is_intermediary = rules.has_supplier_role(row), bool(row.get("is_intermediary", False))
            resolved = is_supplier and row["karat_id"] is not None and not int(row["gold_foreign_rows"])
            grams = Decimal(str(row["gold_grams_balance"])).quantize(rules.WEIGHT_STEP)
            making = Decimal(str(row["making_balance"])).quantize(rules.MONEY_STEP)
            result.append({"id": str(row["id"]), "code": rules.supplier_code(row["id"]), "name": row["name"],
                           "phone": row["phone"] or "", "notes": row["notes"] or "",
                           "karat_id": row["karat_id"], "karat_value": None if row["karat_value"] is None else int(row["karat_value"]),
                           "is_active": bool(row["is_active"]), "is_supplier": is_supplier, "is_intermediary": is_intermediary,
                           "role_text": rules.role_text(is_supplier, is_intermediary),
                           "gold_status": "RESOLVED" if resolved else ("UNRESOLVED" if is_supplier else "NOT_APPLICABLE"),
                           "gold_balance": grams if resolved else None,
                           "gold_direction": rules.direction(grams) if resolved else None, "making": making,
                           "making_direction": rules.direction(making),
                           "posted_invoice_count": int(row["posted_invoice_count"])})
        return result

    def supplier_detail(self, supplier_id, *, _expected_actor=None):
        """Balances (gold in physical grams of the supplier's karat, and making) plus both ledgers with running balances."""
        actor = authenticated_actor(self._session, VIEW, "لا توجد صلاحية لعرض الموردين.", _expected_actor)
        supplier_id = _supplier_id(supplier_id)
        with self._transaction() as connection:
            self._read_verified(connection, actor)
            repository = self._repository(connection)
            supplier = repository.get_supplier(supplier_id)
            if supplier is None:
                raise rules.SupplierRuleError("SUPPLIER_NOT_FOUND", "المورد غير موجود.")
            balances = balance_row(supplier, repository.gold_balances(supplier_id), repository.making_balance(supplier_id))
            if not rules.has_supplier_role(supplier):
                balances = {**balances, "gold_status": "NOT_APPLICABLE"}                 # an intermediary-only party has no supplier account
            history = repository.history_karats(supplier_id)
            gold = repository.gold_ledger(supplier_id)
            making = repository.making_ledger(supplier_id)
            holdings = self._holdings_view(connection, actor, supplier_id)
        return {"holdings": holdings, "supplier": {"id": str(supplier["id"]), "code": rules.supplier_code(supplier["id"]),
                             "name": supplier["name"], "phone": supplier["phone"] or "",
                             "notes": supplier["notes"] or "", "is_active": bool(supplier["is_active"]),
                             "karat_id": supplier["karat_id"],
                             "is_supplier": rules.has_supplier_role(supplier), "is_intermediary": bool(supplier.get("is_intermediary", False)),
                             "role_text": rules.role_text(rules.has_supplier_role(supplier), bool(supplier.get("is_intermediary", False))),
                             "karat_value": None if supplier["karat_value"] is None else int(supplier["karat_value"]),
                             "karat_locked": bool(history),                       # an invoice exists: the karat can no longer change
                             "history_karats": [h["karat_value"] for h in history]},
                "balances": balances,
                "gold_ledger": [{"id": str(r["id"]), "occurred_at": r["occurred_at"], "movement": rules.gold_movement_text(r.get("movement_code"), r["movement_name"]),
                                 "karat_value": int(r["karat_value"]),
                                 "weight_delta": Decimal(str(r["weight_delta_grams"])),
                                 "weight_balance_after": Decimal(str(r["weight_balance_after"])),
                                 "source_type": r["source_type"], "source_id": str(r["source_id"]),
                                 "reference": r["source_reference"] or "", "actor": r["actor_name"] or "",
                                 # a gold settlement (045): the delivered grams / karat beside the credited ones, and its reversed state
                                 "settlement_id": None if r.get("settlement_id") is None else str(r["settlement_id"]),
                                 "delivered_weight": None if r.get("delivered_weight_grams") is None else Decimal(str(r["delivered_weight_grams"])),
                                 "delivered_karat": None if r.get("delivered_karat_value") is None else int(r["delivered_karat_value"]),
                                 "reversed_by": (None if r.get("settlement_reversal_id") is None
                                                 else correction_code("FULL_REVERSAL", r["settlement_reversal_id"])),
                                 # a settlement made from market custody (046) names its custody entry and is reversed from there
                                 "custody_reference": (None if r.get("custody_entry_no") is None
                                                       else _custody_code(r["custody_trip_id"], r["custody_entry_no"])),
                                 "custody_designation": r.get("custody_designation"),
                                 # a settlement made from shop gold held by an intermediary (050)
                                 "holding_party": r.get("holding_party_name"), "declaration": r.get("physical_declaration"),
                                 "lot_weight": None if r.get("delivered_lot_weight_grams") is None else Decimal(str(r["delivered_lot_weight_grams"])),
                                 "lot_id": None if r.get("delivered_lot_id") is None else str(r["delivered_lot_id"]),
                                 "lot_fineness": r.get("delivered_fineness_permille"), "lot_kind": r.get("lot_kind"), "lot_no": r.get("lot_no"),
                                 "lot_origin": None if r.get("origin_entry_no") is None else _custody_code(r["origin_trip_id"], r["origin_entry_no"]),
                                 "reverses_reference": (None if r.get("reversed_settlement_id") is None
                                                        else _settlement_code(r["reversed_settlement_id"])
                                                        + ("" if r.get("reversed_custody_entry_no") is None else
                                                           f" • {_custody_code(r['reversed_custody_trip_id'], r['reversed_custody_entry_no'])}"))}
                                for r in gold],
                "making_ledger": [self._making_row(r) for r in making]}

    def _holdings_view(self, connection, actor, supplier_id):
        """«ذهب المحل لدى المسوقجي»: shop-owned whole lots at this party, for an actor with INTERMEDIARY_HOLDING_VIEW (re-checked against the
        database). Independent of the supplier account: a dual-role party keeps both, never combined. None when the actor may not see it."""
        if VIEW_HOLDINGS not in actor.permissions:
            return None
        try:
            self._authorization.require_permission_read(connection, actor, VIEW_HOLDINGS)
        except PermissionError:
            return None
        ledger = self._holdings(connection)
        pools = [pool_row(r) for r in ledger.open_pools(party_id=supplier_id)]
        logo_pools = [logo_pool_row(r) for r in ledger.logo_open_pools(party_id=supplier_id)]
        return {"settlements": [holding_settlement_row(r) for r in ledger.holding_settlements(supplier_id)],
                "receipts": [receipt_row(r) for r in ledger.party_receipts(supplier_id)],
                "can_receive": MANAGE_HOLDINGS in actor.permissions,
                "can_reverse_receipt": {"TRANSACTION_CORRECT", "INTERMEDIARY_HOLDING_REVERSE"} <= set(actor.permissions),
                "pools": pools, "history": [document_row(r) for r in ledger.party_documents(supplier_id)],
                "logo_pools": logo_pools, "logo_history": [logo_document_row(r) for r in ledger.logo_party_documents(supplier_id)],
                "total": sum((p["balance"] for p in pools), Decimal("0.000")),
                "pure": sum((p["pure"] for p in pools if p["pure"] is not None), Decimal("0")),
                "can_manage": {MANAGE_HOLDINGS, TRIP_MANAGE} <= set(actor.permissions),
                "can_settle": {MANAGE_HOLDINGS, "SUPPLIER_GOLD_SETTLE"} <= set(actor.permissions),
                "can_reverse": {"SUPPLIER_GOLD_SETTLE_REVERSE", "INTERMEDIARY_HOLDING_REVERSE"} <= set(actor.permissions)}

    @staticmethod
    def _making_row(r):
        """One making-ledger row. A PAYMENT row also names its method / account and (when reversed) its RV; a reversal row
        names the payment it restores. The keys are absent only for rows read from a database older than migration 036."""
        payment_id = r.get("payment_id")
        method = r.get("payment_method")
        reversed_payment = r.get("reversed_payment_id")
        custody = (None if r.get("custody_entry_no") is None
                   else _custody_code(r["custody_trip_id"], r["custody_entry_no"]))        # a payment made from market custody (043)
        reverses = r.get("reversed_reference") if reversed_payment is not None else None
        if reversed_payment is not None and r.get("reversed_custody_entry_no") is not None:
            reverses = (f"{_payment_code(reversed_payment)} • "
                        f"{_custody_code(r['reversed_custody_trip_id'], r['reversed_custody_entry_no'])}")
        return {"id": str(r["id"]), "occurred_at": r["occurred_at"], "movement": r["movement_name"],
                "movement_code": r.get("movement_code"),
                "amount_delta": Decimal(str(r["amount_delta"])),
                "balance_after": Decimal(str(r["balance_after"])),
                "source_type": r["source_type"], "source_id": str(r["source_id"]),
                "reference": r["source_reference"] or "", "actor": r["actor_name"] or "",
                "payment_id": None if payment_id is None else str(payment_id),
                "payment_method": method,
                "payment_account": (custody if method == "CUSTODY"
                                    else r.get("bank_account_name") if method == "BANK" else r.get("cash_account_name")),
                "custody_reference": custody,
                "reversed_by": (None if r.get("reversal_id") is None
                                else correction_code("FULL_REVERSAL", r["reversal_id"])),
                "reverses_reference": reverses}

    # ------------------------------------------------------------------ master writes
    @staticmethod
    def _supported_karat(repository, karat_id):
        """The karat master row of a chosen supplier karat: it must exist, be active and be 18 / 21 / 22 / 24."""
        try:
            wanted = int(str(karat_id).strip())
        except (TypeError, ValueError):
            wanted = 0
        if wanted <= 0:
            raise rules.SupplierRuleError("KARAT_REQUIRED", "اختر عيار المورد (18 أو 21 أو 22 أو 24).")
        karat = repository.get_karat(wanted)
        if karat is None or not karat["is_active"] or int(karat["karat_value"]) not in rules.SUPPORTED_KARATS:
            raise rules.SupplierRuleError("KARAT_NOT_SUPPORTED", "عيار المورد يجب أن يكون 18 أو 21 أو 22 أو 24 ومفعّلًا.")
        return karat

    def create_supplier(self, name, phone=None, notes=None, karat_id=None, *, is_supplier=True, is_intermediary=False, _expected_actor=None):
        actor = authenticated_actor(self._session, MANAGE, "لا توجد صلاحية لإدارة الموردين.", _expected_actor)
        is_supplier, is_intermediary = rules.validate_roles(is_supplier, is_intermediary)
        if not is_supplier and _given(karat_id):
            raise rules.SupplierRuleError("KARAT_NOT_ALLOWED", "المسوقجي بلا عيار حساب: لا يُحدَّد عيار إلا للمورد.")
        name = clean_text(name, NAME_MAX, "اسم المورد", required=True)
        phone, notes = clean_text(phone, PHONE_MAX, "رقم الهاتف"), clean_text(notes, NOTES_MAX, "الملاحظات")
        try:
            with self._transaction() as connection:
                actor = self._write_verified(connection, actor)
                repository = self._repository(connection)
                karat = self._supported_karat(repository, karat_id) if is_supplier else None      # a supplier needs its karat; an intermediary has none
                supplier_id = repository.create_supplier(name=name, phone=phone, notes=notes, karat_id=None if karat is None else karat["id"],
                                                         user_id=actor.user_id, is_supplier=is_supplier, is_intermediary=is_intermediary)
                repository.audit(actor=actor, event_type="SUPPLIER_CREATED", entity_id=supplier_id,
                                 reference=rules.supplier_code(supplier_id), description=f"إنشاء {rules.role_text(is_supplier, is_intermediary)} {name}",
                                 metadata={"name": name, "karat_value": None if karat is None else int(karat["karat_value"]),
                                           "is_supplier": is_supplier, "is_intermediary": is_intermediary}, now=repository.db_now())
        except IntegrityError as exc:
            raise _duplicate_name(exc) from exc
        return str(supplier_id)

    def update_supplier(self, supplier_id, name, phone=None, notes=None, karat_id=None, *, is_supplier=None, is_intermediary=None,
                        _expected_actor=None):
        actor = authenticated_actor(self._session, MANAGE, "لا توجد صلاحية لإدارة الموردين.", _expected_actor)
        supplier_id = _supplier_id(supplier_id)
        name = clean_text(name, NAME_MAX, "اسم المورد", required=True)
        phone, notes = clean_text(phone, PHONE_MAX, "رقم الهاتف"), clean_text(notes, NOTES_MAX, "الملاحظات")
        try:
            with self._transaction() as connection:
                actor = self._write_verified(connection, actor)
                repository = self._repository(connection)
                supplier = repository.get_supplier(supplier_id, lock=True)
                if supplier is None:
                    raise rules.SupplierRuleError("SUPPLIER_NOT_FOUND", "المورد غير موجود.")
                was_supplier, was_intermediary = rules.has_supplier_role(supplier), bool(supplier.get("is_intermediary", False))
                now_supplier = was_supplier if is_supplier is None else bool(is_supplier)
                now_intermediary = was_intermediary if is_intermediary is None else bool(is_intermediary)
                rules.validate_roles(now_supplier, now_intermediary)
                changed_karat = None
                if was_intermediary and not now_intermediary:
                    # Under the party row lock, a CURRENT read: any holding pool or document (reversed ones too) keeps the intermediary role.
                    if self._holdings(connection).history_count(supplier_id):
                        raise rules.SupplierRuleError("ROLE_HISTORY_EXISTS", "لا يمكن إزالة دور المسوقجي: له حركات ذهب لدى المسوقجي سابقة "
                                                      "(حتى المعكوسة). أوقفه بدلًا من ذلك.")
                if was_supplier and not now_supplier:
                    # Under the supplier row lock: any invoice (cancelled included), payment or ledger row keeps the supplier role.
                    if repository.role_history_count(supplier_id):
                        raise rules.SupplierRuleError("ROLE_HISTORY_EXISTS", "لا يمكن إزالة دور المورد: له فواتير أو دفعات أو حركات سابقة "
                                                      "(حتى الملغاة أو المعكوسة). أوقف المورد بدلًا من ذلك.")
                    if supplier["karat_id"] is not None:
                        repository.clear_karat(supplier_id=supplier_id, user_id=actor.user_id)
                if not now_supplier and _given(karat_id):
                    raise rules.SupplierRuleError("KARAT_NOT_ALLOWED", "المسوقجي بلا عيار حساب: لا يُحدَّد عيار إلا للمورد.")
                if now_supplier and not was_supplier and not _given(karat_id):
                    raise rules.SupplierRuleError("KARAT_REQUIRED", "اختر عيار المورد (18 أو 21 أو 22 أو 24) عند إضافة دور المورد.")
                if (now_supplier, now_intermediary) != (was_supplier, was_intermediary):
                    repository.set_roles(supplier_id=supplier_id, is_supplier=now_supplier, is_intermediary=now_intermediary,
                                         user_id=actor.user_id)
                # The roles are written BEFORE a karat is set: the 044 check allows a karat only on a party that is a supplier.
                if now_supplier and _given(karat_id):
                    karat = self._supported_karat(repository, karat_id)
                    if supplier["karat_id"] is None or int(supplier["karat_id"]) != int(karat["id"]):
                        # under the supplier row lock; the history read is a locking current read (no race with a new invoice)
                        rules.check_supplier_karat_change(supplier["karat_id"], karat["id"],
                                                          repository.history_karats(supplier_id, lock=True))
                        repository.set_karat(supplier_id=supplier_id, karat_id=karat["id"], user_id=actor.user_id)
                        changed_karat = int(karat["karat_value"])
                repository.update_supplier(supplier_id=supplier_id, name=name, phone=phone, notes=notes,
                                           user_id=actor.user_id)
                repository.audit(actor=actor, event_type="SUPPLIER_UPDATED", entity_id=supplier_id,
                                 reference=rules.supplier_code(supplier_id), description=f"تعديل بيانات المورد {name}",
                                 metadata={"name": name, "karat_value": changed_karat, "is_supplier": now_supplier,
                                           "is_intermediary": now_intermediary}, now=repository.db_now())
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
