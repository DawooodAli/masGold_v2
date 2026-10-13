"""Supplier gold settlement from shop-owned gold held by a market intermediary / مسوقجي (050, «تسوية ذهب لمورد من رصيد المسوقجي»).

ONE holding source (an assayed-lot pool of 048 or a master-karat LOGO pool of 049), ONE supplier and ONE positive quantity (at most three decimal
places) per SG document. Partial and repeated allocations are allowed: the same source may settle several suppliers one after another, and no source
is merged with another. Facts stay distinct: the source identity, its original karat or assay, the ORIGINAL full-lot weight, and the delivered grams.

One transaction posts the immutable SG header (source HOLDING_LOT / HOLDING_LOGO), ONE negative supplier gold movement in the supplier's dedicated karat,
exactly ONE GOLD leg and ONE explicit signed holding movement (SETTLEMENT, -q). Nothing moves in custody, Scrap, cash, making, inventory or the shop lot
ledger, and the originating custody is neither locked nor reopened (it may be CLOSED).

Credit (precision-50 Decimal, the posted figure derived once, straight from the raw result, at 0.001 g ROUND_HALF_UP; the six-decimal analytical value and the
posted-minus-analytical difference are stored separately):
  LOGO   q x source karat / supplier karat           (services.supplier_rules.settlement_credit)
  LOT    q x fineness / 1000 x 24 / supplier karat   (services.supplier_rules.lot_settlement_credit)
Unknown assay cannot occur for a lot pool (048 refuses it); excess quantity, excess precision and a credit that rounds to zero are refused atomically.

Physical condition (lot pools only; the declaration is stored immutably on the SG header). WHOLE_ORIGINAL_BAR needs the full original quantity, a full
balance AND no allocation history of the pool (any GRAM_ENTITLEMENT / PHYSICALLY_DIVIDED settlement ever, reversed ones included); a full quantity alone
never proves a whole-bar delivery, so GRAM_ENTITLEMENT and PHYSICALLY_DIVIDED are always offered. Nothing is preselected and the service validates.

Rights (explicit grants, re-checked inside the transaction; the actor is the session user): INTERMEDIARY_HOLDING_MANAGE + SUPPLIER_GOLD_SETTLE.
Reversal is ONE canonical correction on the SG (CorrectionService, source SUPPLIER_GOLD_SETTLEMENT): TRANSACTION_CORRECT + SUPPLIER_GOLD_SETTLE_REVERSE +
INTERMEDIARY_HOLDING_REVERSE, from the holder page or the supplier page alike.

Lock order (every writer): the lot row (lot pools) -> the holder and the recipient in ASCENDING party id (once when they are the same party) -> the
holding source pool; every decision is a CURRENT locking read. The holder may be inactive (what it holds can still be settled); the recipient may be
inactive and the payable may cross zero into supplier credit. A holder that is also the recipient (a dual-role party) needs the explicit confirmation,
enforced here and part of the request digest; its holdings and its supplier payable stay separate."""
import hashlib
import json
from uuid import UUID

from mysql.connector import IntegrityError

from repositories.intermediary_holding_repository import IntermediaryHoldingRepository, MOVE_SETTLEMENT
from repositories.market_trip_repository import MarketTripRepository
from repositories.supplier_gold_settlement_repository import (GOLD_MOVEMENT, HOLDING_KINDS, HOLDING_LOGO_KIND, HOLDING_LOT_KIND, SOURCE,
                                                              SupplierGoldSettlementRepository, settlement_code)
from services import supplier_rules as rules
from services.authorization_service import AuthorizationService
from services.intermediary_holding_rules import (ALLOCATING, MANAGE_PERMISSION, POOL_LOGO, POOL_LOT, REVERSE_PERMISSION,
                                                 SETTLE_CONFIRMATION_TEXT, SETTLE_PERMISSION, SETTLE_REVERSE_PERMISSION, WHOLE,
                                                 declaration_options)
from services.invoice_service import invoice_transaction
from services.market_trip_service import NOTES_MAX, PAYMENT_REFERENCE_MAX, TripError, _entry_date, _text
from decimal import Decimal

KEY_CONSTRAINTS = ("uq_sgs_request", "uq_mte_request", "uq_ihd_request", "uq_lhd_request", "uq_hr_request")
ZERO = Decimal("0.000")
KINDS = {POOL_LOT: HOLDING_LOT_KIND, POOL_LOGO: HOLDING_LOGO_KIND}


def _number(value, code, message):
    try:
        number = int(str(value).strip())
    except (TypeError, ValueError):
        raise TripError(code, message) from None
    if number <= 0:
        raise TripError(code, message)
    return number


def _key(value):
    try:
        return str(UUID(str(value)))
    except (ValueError, TypeError, AttributeError):
        raise TripError("REQUEST_INVALID", "أعد فتح النموذج وحاول مرة أخرى.") from None


def _kind(value):
    if value not in KINDS:
        raise TripError("HOLDING_NOT_FOUND", "مصدر الذهب لدى المسوقجي غير موجود.")
    return value


def _bool(value):
    return value is True or str(value).strip().lower() == "true"


class HoldingSettlementService:
    def __init__(self, session, *, transaction_factory=invoice_transaction, repository=MarketTripRepository, holdings=IntermediaryHoldingRepository,
                 parties=SupplierGoldSettlementRepository, authorization=None, corrections=None):
        self._session, self._transaction = session, transaction_factory
        self._repository, self._holdings, self._parties = repository, holdings, parties
        self._authorization = authorization or AuthorizationService(session)
        self._corrections = corrections

    # ------------------------------------------------------------------ rights (UI hints; the transaction re-checks every one)
    def can_settle(self):
        actor = self._session.current_actor()
        return actor is not None and {MANAGE_PERMISSION, SETTLE_PERMISSION} <= set(actor.permissions)

    def can_reverse(self):
        actor = self._session.current_actor()
        return actor is not None and {SETTLE_REVERSE_PERMISSION, REVERSE_PERMISSION} <= set(actor.permissions)

    def _writer(self, expected_actor):
        actor = self._session.require_authenticated()
        if expected_actor is not None and not self._session.is_current(expected_actor):
            raise PermissionError("انتهت جلسة الدخول.")
        if not {MANAGE_PERMISSION, SETTLE_PERMISSION} <= set(actor.permissions):
            raise PermissionError("تسوية ذهب المسوقجي للموردين للمالك فقط.")
        return actor

    def _verified(self, connection, session_actor):
        """Strict (locking) write check of BOTH rights inside the transaction; the actor of the posting is the verified one."""
        self._authorization.require_permission_for_write(connection, session_actor, SETTLE_PERMISSION)
        return self._authorization.require_permission_for_write(connection, session_actor, MANAGE_PERMISSION)

    # ------------------------------------------------------------------ pool facts
    @staticmethod
    def _pool_facts(kind, pool, karat_value=None):
        if kind == POOL_LOT:
            return {"kind": kind, "weight": Decimal(str(pool["weight_grams"])), "fineness": Decimal(str(pool["fineness_permille"]))}
        return {"kind": kind, "weight": Decimal(str(pool["weight_grams"])), "karat_value": karat_value}

    def _pool_row(self, holdings, kind, pool_id):
        return holdings.pool(pool_id) if kind == POOL_LOT else holdings.logo_pool(pool_id)

    def _balance(self, holdings, kind, pool_id):
        return Decimal(str(holdings.pool_balance(pool_id) if kind == POOL_LOT else holdings.logo_pool_balance(pool_id)))

    @staticmethod
    def _credit(kind, quantity, pool, karat_value, target_value):
        if kind == POOL_LOT:
            return rules.lot_settlement_credit(quantity, pool["fineness_permille"], target_value)
        return rules.settlement_credit(quantity, karat_value, target_value)

    # ------------------------------------------------------------------ reads (display only: no decision locks; the figures of a preview are
    # computed by services.intermediary_holding_rules.holding_preview with the SAME functions the posting uses)
    def context(self, pool_kind, pool_id, *, _expected_actor=None):
        """What the dialog shows for ONE source: its facts, the available grams, the recipients and the settlement history the declaration
        choices depend on (the service re-derives everything under the locks at posting)."""
        self._writer(_expected_actor)
        pool_kind, pool_number = _kind(pool_kind), _number(pool_id, "HOLDING_NOT_FOUND", "مصدر الذهب لدى المسوقجي غير موجود.")
        actor = self._session.require_authenticated()
        with self._transaction() as connection:
            self._authorization.require_permission_read(connection, actor, SETTLE_PERMISSION)
            self._authorization.require_permission_read(connection, actor, MANAGE_PERMISSION)
            holdings, parties = self._holdings(connection), self._parties(connection)
            pool = self._pool_row(holdings, pool_kind, pool_number)
            if pool is None:
                raise TripError("HOLDING_NOT_FOUND", "مصدر الذهب لدى المسوقجي غير موجود.")
            holder = parties.get_supplier(pool["party_id"])
            balance = Decimal(str(holdings.display_balance(logo=pool_kind == POOL_LOGO, pool_id=pool_number)))        # a plain read: the dialog is no decision
            karat_value = None
            if pool_kind == POOL_LOGO:
                karat = parties.get_karat(pool["karat_id"])
                karat_value = int(karat["karat_value"])
            history = [r["physical_declaration"] for r in holdings.display_pool_history(pool_number)] if pool_kind == POOL_LOT else []
            recipients = [{"id": str(s["id"]), "code": rules.supplier_code(s["id"]), "name": s["name"], "active": bool(s["is_active"]),
                           "karat_value": int(s["karat_value"]), "self": int(s["id"]) == int(pool["party_id"])}
                          for s in parties.list_suppliers(role="SUPPLIER", limit=500) if s["karat_id"] is not None]
        facts = self._pool_facts(pool_kind, pool, karat_value)
        return {"pool_kind": pool_kind, "pool_id": str(pool_number), "holder": {"id": str(holder["id"]), "name": holder["name"], "active": bool(holder["is_active"])},
                "facts": facts, "balance": balance, "recipients": recipients, "history": history, "allocated": any(d in ALLOCATING for d in history),
                "whole_confirmation_text": SETTLE_CONFIRMATION_TEXT}

    # ------------------------------------------------------------------ results and replay
    def _result(self, holdings, header, kind, duplicate, remaining=None):
        lot = header["source_kind"] == HOLDING_LOT_KIND
        pool_id = header["lot_pool_id"] if lot else header["logo_pool_id"]
        return {"id": str(header["id"]), "code": settlement_code(header["id"]), "source_kind": header["source_kind"],
                "pool_kind": kind, "pool_id": str(pool_id), "holder_id": str(header["holding_party_id"]), "supplier_id": str(header["supplier_id"]),
                "delivered_weight": str(header["delivered_weight_grams"]), "credited_weight": str(header["credited_weight_grams"]),
                "analytical": str(header["credited_analytical_grams"]), "rounding_difference": str(header["rounding_difference_grams"]),
                "target_karat": int(header["target_karat_value"]), "declaration": header["physical_declaration"],
                "remaining": str(self._balance(holdings, kind, pool_id) if remaining is None else remaining), "duplicate": duplicate}

    def _prior(self, holdings, parties, key, digest, actor, kind):
        """The original linked records of an already-recorded key, decided BEFORE any availability is derived. Any other use of the key (another
        operation, another payload, another actor, another source) is a conflict."""
        header = parties.by_request(key)
        if header is None:
            if parties.request_key_used_elsewhere(key):
                raise TripError("REQUEST_CONFLICT", "طلب العملية مستخدم لعملية مختلفة.")
            return None
        if (header["source_kind"] not in HOLDING_KINDS or header["source_kind"] != KINDS[kind] or header["request_hash"] != digest
                or int(header["performed_by_user_id"]) != int(actor.user_id)):
            raise TripError("REQUEST_CONFLICT", "طلب العملية مستخدم لعملية مختلفة.")
        return self._result(holdings, header, kind, True)

    def _replay(self, session_actor, kind, key, digest, original):
        with self._transaction() as connection:
            actor = self._verified(connection, session_actor)
            result = self._prior(self._holdings(connection), self._parties(connection), key, digest, actor, kind)
            if result is None:
                raise original
            return result

    # ------------------------------------------------------------------ the settlement
    def settle(self, pool_kind, pool_id, supplier_id, quantity, declaration, self_confirmed, settlement_date, reference, notes, request_key, *,
               _expected_actor=None):
        session_actor = self._writer(_expected_actor)
        pool_kind = _kind(pool_kind)
        pool_number = _number(pool_id, "HOLDING_NOT_FOUND", "مصدر الذهب لدى المسوقجي غير موجود.")
        supplier_number = _number(supplier_id, "SUPPLIER_NOT_FOUND", "اختر المورد المستلم.")
        grams = rules.parse_delivered_weight(quantity)
        settled_on = _entry_date(settlement_date)
        reference, notes, key = _text(reference, PAYMENT_REFERENCE_MAX, "المرجع"), _text(notes, NOTES_MAX, "الملاحظات"), _key(request_key)
        declaration = (str(declaration).strip() if declaration not in (None, "") else None)
        confirmed = _bool(self_confirmed)
        digest = hashlib.sha256(json.dumps([KINDS[pool_kind], pool_number, supplier_number, str(grams), settled_on.isoformat(), reference, notes,
                                           declaration, confirmed], ensure_ascii=False).encode()).hexdigest()
        try:
            return self._post(session_actor, pool_kind, pool_number, supplier_number, grams, declaration, confirmed, settled_on, reference, notes,
                              key, digest)
        except IntegrityError as exc:
            if getattr(exc, "errno", None) != 1062 or not any(k in str(exc) for k in KEY_CONSTRAINTS):
                raise                                          # any other integrity error is a real defect: never hidden
            return self._replay(session_actor, pool_kind, key, digest, exc)

    def _post(self, session_actor, kind, pool_id, supplier_id, grams, declaration, confirmed, settled_on, reference, notes, key, digest):
        with self._transaction() as connection:
            actor = self._verified(connection, session_actor)
            market, holdings, parties = self._repository(connection), self._holdings(connection), self._parties(connection)
            pool = self._pool_row(holdings, kind, pool_id)                          # immutable facts: a plain read only finds what to lock
            if pool is None:
                raise TripError("HOLDING_NOT_FOUND", "مصدر الذهب لدى المسوقجي غير موجود.")
            holder_id = int(pool["party_id"])
            if kind == POOL_LOT and not market.lock_lots([int(pool["lot_id"])]):      # lot -> parties (ascending) -> pool; no trip is locked
                raise TripError("LOT_NOT_FOUND", "الشحنة غير موجودة.")
            locked = {party: parties.get_supplier(party, lock=True) for party in sorted({holder_id, supplier_id})}
            pool = holdings.lock_pool(pool_id) if kind == POOL_LOT else holdings.lock_logo_pool(pool_id)
            replay = self._prior(holdings, parties, key, digest, actor, kind)
            if replay is not None:
                return replay                                                       # the original records, before any availability is derived
            supplier = locked[supplier_id]
            if supplier is None:
                raise TripError("SUPPLIER_NOT_FOUND", "المورد غير موجود.")
            rules.require_supplier_role(supplier)                                   # an intermediary-only party has no gold account
            if supplier["karat_id"] is None:
                raise rules.SupplierRuleError("SUPPLIER_KARAT_MISSING", "عيار هذا المورد غير محدد: حدّده من تعديل المورد أولًا.")
            self_settlement = supplier_id == holder_id
            if self_settlement and not confirmed:
                raise TripError("SELF_SETTLEMENT_CONFIRMATION_REQUIRED",
                                "المسوقجي هو نفسه المورد المستلم: أكّد صراحةً أن ذهب المحل لديه يبقى منفصلًا عن حساب المورد قبل التسوية.")
            balance = self._balance(holdings, kind, pool_id)                        # CURRENT, compensations included
            if balance <= 0:
                raise TripError("HOLDING_NOT_AVAILABLE", "لا يوجد رصيد من هذا المصدر لدى المسوقجي.")
            if grams > balance:
                raise TripError("HOLDING_EXCEEDED", f"المتبقي من هذا المصدر لدى المسوقجي {balance:,.3f} جرام فقط.")
            if kind == POOL_LOT:
                history = [r["physical_declaration"] for r in holdings.pool_settlement_history(pool_id)]       # CURRENT, reversed ones included
                options = declaration_options(pool["weight_grams"], balance, grams, history)
                if declaration is None:
                    raise TripError("DECLARATION_REQUIRED", "اختر حالة السبكة الفعلية قبل التسوية: لا اختيار افتراضي.")
                if declaration not in options:
                    if declaration == WHOLE:
                        raise TripError("WHOLE_NOT_ELIGIBLE",
                                        "لا يُقبل «تسليم السبكة الأصلية كاملة» إلا بكامل الكمية الأصلية ورصيد كامل ودون تخصيص جرامات سابق لهذه السبكة.")
                    raise TripError("DECLARATION_INVALID", "حالة السبكة المختارة غير صحيحة.")
                karat_value = None
            else:
                if declaration is not None:
                    raise TripError("DECLARATION_NOT_APPLICABLE", "لا تنطبق حالة السبكة على لوجو بالجرام.")
                karat = parties.get_karat(pool["karat_id"])
                karat_value = int(karat["karat_value"])
            target = parties.get_karat(supplier["karat_id"])
            credited, analytical, difference = self._credit(kind, grams, pool, karat_value, int(target["karat_value"]))     # zero credit refuses here
            type_id = parties.gold_type_id(GOLD_MOVEMENT)
            if type_id is None:
                raise RuntimeError("supplier gold movement type SETTLEMENT is missing (migration 045)")
            header_pool = pool if kind == POOL_LOT else {**pool, "karat_value": karat_value}
            now = market.db_now()
            settlement_id = parties.insert_holding_header(
                kind=KINDS[kind], supplier_id=supplier_id, pool=header_pool, target_karat=target, quantity=grams, credited=credited, analytical=analytical,
                difference=difference, declaration=declaration, self_confirmed=self_settlement, settlement_date=settled_on, reference=reference,
                notes=notes, key=key, digest=digest, actor=actor, now=now)
            code = settlement_code(settlement_id)
            source_text = (f"سبكة عيار فعلي {pool['fineness_permille']} بالألف (وزنها الأصلي {Decimal(str(pool['weight_grams'])):,.3f} جرام)" if kind == POOL_LOT
                           else f"لوجو عيار {karat_value}")
            label = (f"تسوية ذهب للمورد {code} من رصيد المسوقجي {locked[holder_id]['name']}: سُلِّم {grams:,.3f} جرام من {source_text} "
                     f"← احتُسب {credited:,.3f} جرام عيار {int(target['karat_value'])}")
            gold_id = parties.insert_gold_movement(
                supplier_id=supplier_id, type_id=type_id, karat_id=target["id"], weight_delta=-credited,
                eq21_delta=-rules.equivalent_21(credited, target["karat_value"]), source_type=SOURCE, source_id=settlement_id,
                reference=code, description=label, now=now, user_id=actor.user_id)
            parties.insert_leg(settlement_id=settlement_id, kind="GOLD", source_kind=KINDS[kind], supplier_gold_movement_id=gold_id)
            holdings.insert_settlement_movement(logo=kind == POOL_LOGO, pool_id=pool_id, settlement_id=settlement_id, movement_type=MOVE_SETTLEMENT,
                                                weight=grams, delta=-grams)
            metadata = {"settlement": code, "source_kind": KINDS[kind], "pool_id": pool_id, "holder_id": holder_id, "supplier_id": supplier_id,
                        "delivered": str(grams), "target_karat": int(target["karat_value"]), "credited": str(credited), "analytical": str(analytical),
                        "rounding_difference": str(difference), "remaining": str(balance - grams), "declaration": declaration,
                        "self_settlement": self_settlement, "date": settled_on.isoformat(), "reference": reference, "employee_id": actor.employee_id}
            if kind == POOL_LOT:
                metadata.update({"lot_id": str(pool["lot_id"]), "origin_melt_entry_id": str(pool["origin_melt_entry_id"]),
                                 "original_lot_weight": str(pool["weight_grams"]), "fineness": str(pool["fineness_permille"])})
            else:
                metadata.update({"source_karat": karat_value})
            parties.audit(actor=actor, event_type="SUPPLIER_GOLD_SETTLEMENT", entity_id=settlement_id, entity_type=SOURCE, reference=code,
                          description=label, metadata=metadata, now=now)
            holdings.audit(actor=actor, event_type="INTERMEDIARY_HOLDING_SETTLEMENT", entity_id=settlement_id, reference=code, description=label,
                           metadata=metadata, now=now)
            result = self._result(holdings, parties.settlement(settlement_id), kind, False, remaining=balance - grams)
            if not self._session.is_current(session_actor):
                raise PermissionError("انتهت جلسة الدخول.")
        return result

    # ------------------------------------------------------------------ reversal: the ONE canonical correction on the SG
    def reverse(self, settlement_id, reason, *, _expected_actor=None):
        """Full reversal (RV) of a settlement from a holding. The rights are checked here as a hint and again, inside the transaction, by
        CorrectionService (TRANSACTION_CORRECT + SUPPLIER_GOLD_SETTLE_REVERSE + INTERMEDIARY_HOLDING_REVERSE). The STORED supplier credit and
        the STORED delivered grams are restored: nothing is recalculated."""
        actor = self._session.require_authenticated()
        if _expected_actor is not None and not self._session.is_current(_expected_actor):
            raise PermissionError("انتهت جلسة الدخول.")
        if not {SETTLE_REVERSE_PERMISSION, REVERSE_PERMISSION} <= set(actor.permissions):
            raise PermissionError("عكس تسوية ذهب المسوقجي للمالك فقط.")
        if self._corrections is None:
            from services.correction_service import CorrectionService
            self._corrections = CorrectionService(self._session)
        return self._corrections.reverse(SOURCE, str(settlement_id), reason, _expected_actor=_expected_actor)
