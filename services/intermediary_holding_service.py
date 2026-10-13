"""Whole assayed lot holdings at a market intermediary (048, «تسليم ذهب لمسوقجي» / «ذهب المحل لدى المسوقجي»).

HANDOVER takes ONE whole known-assay lot out of an OPEN custody and credits a separate shop-owned holding at ONE active intermediary-role party;
RETURN takes the same whole lot back into the ORIGINAL (still OPEN) custody after the OWNER confirms «تم استلام نفس السبكة كاملة دون تجزئة».
Nothing here moves cash, Scrap, making, a supplier payable, an inventory piece or a shop lot, and a holding is neither a sale nor a supplier
settlement. Facts of the lot (identity, melt origin, kind, weight, assay) are never changed; the posted grams are always the lot's full weight,
derived under the locks. A reversal is posted by CorrectionService (one canonical path: the custody entry), never from here.

Lock order (every writer, this one and CorrectionService): the affected trip -> the lot rows (ascending) -> the party row -> the holding source
pool. Every consumption decision is a CURRENT locking read, including the nested reversal filters inside custody_lots."""
import hashlib
import json

from mysql.connector import IntegrityError

from repositories.intermediary_holding_repository import (CONFIRMATION_CODE, HANDOVER, HOLD_BACK, HOLD_IN, HOLD_OUT, HOLD_RETURN, LOGO_HOLD, LOT_HOLD,
                                                          MOVE_HANDOVER, MOVE_RETURN, RETURN, IntermediaryHoldingRepository, holding_code,
                                                          logo_holding_code)
from repositories.market_trip_repository import OPEN, MarketTripRepository, entry_code, trip_code
from repositories.supplier_gold_settlement_repository import SupplierGoldSettlementRepository
from services.authorization_service import AuthorizationService
from services.intermediary_holding_rules import (ALLOCATING, CONFIRMATION_TEXT, MANAGE_PERMISSION, TRIP_PERMISSION, VIEW_PERMISSION,
                                                 allocation_note, document_row, holding_settlement_row, logo_document_row, logo_pool_row,
                                                 pool_row, pure_grams)
from services.invoice_service import invoice_transaction
from services.market_trip_service import (NOTES_MAX, PAYMENT_REFERENCE_MAX, TripError, ZERO_WEIGHT, _entry_date, _key, _text, _trip_id,
                                          custody_view, parse_weight)
from decimal import Decimal

KEY_CONSTRAINTS = ("uq_mte_request", "uq_ihd_request", "uq_lhd_request", "uq_sgs_request")


def _pool_id(value):
    try:
        number = int(str(value).strip())
    except (TypeError, ValueError):
        raise TripError("HOLDING_NOT_FOUND", "مصدر الذهب لدى المسوقجي غير موجود.") from None
    if number <= 0:
        raise TripError("HOLDING_NOT_FOUND", "مصدر الذهب لدى المسوقجي غير موجود.")
    return number


class IntermediaryHoldingService:
    def __init__(self, session, *, transaction_factory=invoice_transaction, repository=MarketTripRepository,
                 holdings=IntermediaryHoldingRepository, parties=SupplierGoldSettlementRepository, authorization=None):
        self._session, self._transaction = session, transaction_factory
        self._repository, self._holdings, self._parties = repository, holdings, parties
        self._authorization = authorization or AuthorizationService(session)

    # ------------------------------------------------------------------ rights
    def can_view(self):
        actor = self._session.current_actor()
        return actor is not None and VIEW_PERMISSION in actor.permissions

    def can_manage(self):
        actor = self._session.current_actor()
        return actor is not None and {MANAGE_PERMISSION, TRIP_PERMISSION} <= set(actor.permissions)

    def _writer(self, expected_actor):
        actor = self._session.require_authenticated()
        if expected_actor is not None and not self._session.is_current(expected_actor):
            raise PermissionError("انتهت جلسة الدخول.")
        if not {MANAGE_PERMISSION, TRIP_PERMISSION} <= set(actor.permissions):
            raise PermissionError("تسليم الذهب للمسوقجي واستلامه للمالك فقط.")
        return actor

    def _verified(self, connection, session_actor):
        """Strict (locking) write check of BOTH rights inside the transaction; the actor of the posting is the verified one."""
        self._authorization.require_permission_for_write(connection, session_actor, TRIP_PERMISSION)
        return self._authorization.require_permission_for_write(connection, session_actor, MANAGE_PERMISSION)

    # ------------------------------------------------------------------ reads
    def party_holdings(self, party_id, *, _expected_actor=None):
        """«ذهب المحل لدى المسوقجي»: the sources still at the party and the whole document history (reversed ones included)."""
        actor = self._session.require_authenticated()
        if _expected_actor is not None and not self._session.is_current(_expected_actor):
            raise PermissionError("انتهت جلسة الدخول.")
        if VIEW_PERMISSION not in actor.permissions:
            raise PermissionError("عرض ذهب المحل لدى المسوقجي للمالك فقط.")
        party_id = _trip_id(party_id)
        with self._transaction() as connection:
            self._authorization.require_permission_read(connection, actor, VIEW_PERMISSION)
            holdings = self._holdings(connection)
            pools = [pool_row(r) for r in holdings.open_pools(party_id=party_id)]
            history = [document_row(r) for r in holdings.party_documents(party_id)]
            logo_pools = [logo_pool_row(r) for r in holdings.logo_open_pools(party_id=party_id)]
            logo_history = [logo_document_row(r) for r in holdings.logo_party_documents(party_id)]
            settlements = [holding_settlement_row(r) for r in holdings.holding_settlements(party_id)]
        total = sum((p["balance"] for p in pools), ZERO_WEIGHT)
        pure = sum((p["pure"] for p in pools if p["pure"] is not None), Decimal("0"))
        return {"party_id": str(party_id), "pools": pools, "history": history, "total": total, "pure": pure,
                "logo_pools": logo_pools, "logo_history": logo_history, "settlements": settlements,
                "can_manage": MANAGE_PERMISSION in actor.permissions and TRIP_PERMISSION in actor.permissions,
                "can_settle": MANAGE_PERMISSION in actor.permissions and "SUPPLIER_GOLD_SETTLE" in actor.permissions,
                "can_reverse": "SUPPLIER_GOLD_SETTLE_REVERSE" in actor.permissions and "INTERMEDIARY_HOLDING_REVERSE" in actor.permissions}

    # ------------------------------------------------------------------ shared checks
    @staticmethod
    def handover_check(lot, party):
        """The party / lot rules shared by the preview and the posting (stored facts only)."""
        if lot["fineness"] is None:
            raise TripError("LOT_ASSAY_UNKNOWN", "عيار هذه السبكة الفعلي غير معلوم: لا تُسلَّم لمسوقجي قبل معرفة عيارها (لا يُستنتج العيار).")
        if lot["outstanding"] != lot["weight"]:
            raise TripError("LOT_PARTIAL_NOT_ALLOWED", "رصيد السبكة في العهدة جزئي: اعكس الرجوع الجزئي أولًا؛ تُسلَّم السبكة كاملة فقط.")
        if not bool(party.get("is_intermediary", False)):
            raise TripError("INTERMEDIARY_ROLE_REQUIRED", "هذا الطرف ليس مسوقجيًا: أضف له دور المسوقجي أولًا.")
        if not bool(party.get("is_active", True)):
            raise TripError("PARTY_INACTIVE", "المسوقجي موقوف: لا تسليم جديد له (ما عنده يمكن استلامه).")

    # ------------------------------------------------------------------ results and replay
    @staticmethod
    def _result(entry, document, kind, duplicate):
        return {"entry_id": str(entry["id"]), "trip_id": str(entry["trip_id"]), "trip_code": trip_code(entry["trip_id"]),
                "code": entry_code(entry["trip_id"], entry["entry_no"]), "kind": kind, "created": False, "duplicate": duplicate,
                "document_id": str(document["id"]), "document_code": holding_code(document["id"]), "pool_id": str(document["pool_id"]),
                "party_id": str(document["party_id"]), "lot_id": str(document["lot_id"]), "weight": str(document["weight_grams"]),
                "fineness": str(document["fineness_permille"])}

    def _prior(self, repository, holdings, settlements, key, digest, actor, kind):
        """The original linked records of an already-recorded key, decided BEFORE any availability is derived. Any other use of the key
        (another operation, another payload, another actor, a supplier settlement) is a conflict."""
        entry, document, header = repository.by_request(key), holdings.by_request(key), settlements.by_request(key)
        if entry is None and document is None and header is None and holdings.logo_by_request(key) is None:
            return None
        link = None if entry is None else holdings.link_by_entry(entry["id"])
        if (entry is None or document is None or header is not None or link is None or entry["entry_kind"] != kind
                or int(link["document_id"]) != int(document["id"]) or entry["request_hash"] != digest or document["request_hash"] != digest
                or int(entry["performed_by_user_id"]) != int(actor.user_id)):
            raise TripError("REQUEST_CONFLICT", "طلب العملية مستخدم لعملية مختلفة.")
        return self._result(entry, document, kind, True)

    def _replay_after_twin(self, session_actor, key, digest, kind, original):
        with self._transaction() as connection:
            actor = self._verified(connection, session_actor)
            result = self._prior(self._repository(connection), self._holdings(connection), self._parties(connection), key, digest, actor, kind)
            if result is None:
                raise original
            return result

    # ------------------------------------------------------------------ handover
    def hand_over_lot(self, trip_id, party_id, lot_id, handover_date, reference, notes, request_key, *, weight=None, _expected_actor=None):
        """Hand ONE whole known-assay lot of an OPEN custody to ONE active intermediary-role party."""
        session_actor = self._writer(_expected_actor)
        trip_id, party_id, lot_id = _trip_id(trip_id), _trip_id(party_id), _trip_id(lot_id)
        supplied = None if weight is None or not str(weight).strip() else parse_weight(weight)
        handed_on = _entry_date(handover_date)
        reference, notes, key = _text(reference, PAYMENT_REFERENCE_MAX, "المرجع"), _text(notes, NOTES_MAX, "الملاحظات"), _key(request_key)
        digest = hashlib.sha256(json.dumps([LOT_HOLD, trip_id, party_id, lot_id, handed_on.isoformat(), reference, notes,
                                           None if supplied is None else str(supplied)], ensure_ascii=False).encode()).hexdigest()
        try:
            return self._post_handover(session_actor, trip_id, party_id, lot_id, supplied, handed_on, reference, notes, key, digest)
        except IntegrityError as exc:
            if getattr(exc, "errno", None) != 1062 or not any(k in str(exc) for k in KEY_CONSTRAINTS):
                raise
            return self._replay_after_twin(session_actor, key, digest, LOT_HOLD, exc)

    def _post_handover(self, session_actor, trip_id, party_id, lot_id, supplied, handed_on, reference, notes, key, digest):
        with self._transaction() as connection:
            actor = self._verified(connection, session_actor)
            repository, holdings, parties = self._repository(connection), self._holdings(connection), self._parties(connection)
            trip = repository.trip(trip_id, lock=True)                        # trip -> lot -> party -> pool
            if trip is None:
                raise TripError("TRIP_NOT_FOUND", "العهدة غير موجودة.")
            replay = self._prior(repository, holdings, parties, key, digest, actor, LOT_HOLD)
            if replay is not None:
                return replay                                                 # the original records, before any availability is derived
            if trip["status"] != OPEN:
                raise TripError("TRIP_CLOSED", "العهدة مغلقة: أعد فتحها أولًا.")
            locked = repository.lock_lots([lot_id])
            if not locked:
                raise TripError("LOT_NOT_FOUND", "الشحنة غير موجودة.")
            lot = locked[0]
            full = Decimal(str(lot["weight_grams"]))
            current = next(iter(repository.custody_lots(trip_id, lot_id=lot_id)), None)       # CURRENT: nested reversal filter locks too
            outstanding = Decimal("0") if current is None else Decimal(str(current["outstanding_grams"]))
            if outstanding == 0:
                raise TripError("LOT_NOT_IN_CUSTODY", "الشحنة ليست في هذه العهدة (في المحل أو في عهدة أخرى أو لدى مورد أو مسوقجي).")
            if supplied is not None and supplied != full:
                raise TripError("LOT_PARTIAL_NOT_ALLOWED", "تُسلَّم السبكة كاملة فقط؛ الكمية المرسلة لا تطابق وزنها الكامل.")
            party = parties.get_supplier(party_id, lock=True)                                   # inactive refused below, never silently
            if party is None:
                raise TripError("PARTY_NOT_FOUND", "المسوقجي غير موجود.")
            self.handover_check({"weight": full, "outstanding": outstanding,
                                 "fineness": None if lot["fineness_permille"] is None else Decimal(str(lot["fineness_permille"]))}, party)
            if Decimal(str(repository.shop_lot_balance(lot_id))) != 0 or Decimal(str(holdings.lot_held_total(lot_id))) != 0:
                raise TripError("LOT_IDENTITY_CONFLICT", "الشحنة تظهر في أكثر من مكان: لا يمكن التسليم قبل مراجعة أرصدتها.")
            now = repository.db_now()
            pool_id = holdings.insert_pool(party_id=party_id, lot=lot, user_id=actor.user_id)
            pool = {"id": pool_id, "party_id": party_id, "lot_id": lot_id, "weight_grams": full, "fineness_permille": lot["fineness_permille"]}
            entry_no = repository.next_entry_no(trip_id)
            entry_id = repository.insert_entry(trip_id=trip_id, entry_no=entry_no, kind=LOT_HOLD, entry_date=handed_on, notes=notes, key=key,
                                               digest=digest, actor=actor, now=now)
            document_id = holdings.insert_document(kind=HANDOVER, pool=pool, trip_id=trip_id, document_date=handed_on, reference=reference,
                                                   notes=notes, confirmation_code=None, confirmed_by=None, confirmed_at=None, key=key,
                                                   digest=digest, actor=actor, now=now)
            code, ih = entry_code(trip_id, entry_no), holding_code(document_id)
            custody_id = repository.insert_lot_movement(lot_id=lot_id, entry_id=entry_id, movement_type=HOLD_OUT, weight_delta=-full)
            holdings.insert_movement(pool_id=pool_id, document_id=document_id, doc_kind=HANDOVER, movement_type=MOVE_HANDOVER, weight=full, delta=full)
            holdings.insert_link(entry_id=entry_id, entry_kind=LOT_HOLD, trip_id=trip_id, document_id=document_id, doc_kind=HANDOVER,
                                 pool_id=pool_id, lot_id=lot_id, weight=full, custody_movement_id=custody_id, movement_type=HOLD_OUT, delta=-full)
            label = (f"تسليم سبكة لمسوقجي {ih} من عهدة السوق {code}: {full:,.3f} جرام • العيار الفعلي {lot['fineness_permille']} بالألف "
                     f"• {party['name']}")
            metadata = {"trip_id": trip_id, "party_id": party_id, "pool_id": pool_id, "lot_id": str(lot_id), "origin_melt_entry_id": str(lot["entry_id"]),
                        "lot_kind": lot["lot_kind"], "document": ih, "custody_entry": code, "weight": str(full),
                        "fineness": str(lot["fineness_permille"]), "date": handed_on.isoformat(), "employee_id": actor.employee_id}
            repository.audit(actor=actor, event_type="MARKET_TRIP_LOT_HOLD", entity_id=entry_id, reference=code, description=label,
                             metadata=metadata, now=now)
            holdings.audit(actor=actor, event_type="INTERMEDIARY_HOLDING_HANDOVER", entity_id=document_id, reference=ih, description=label,
                           metadata=metadata, now=now)
            entry = repository.by_request(key)
            result = self._result(entry, holdings.by_request(key), LOT_HOLD, False)
            if not self._session.is_current(session_actor):
                raise PermissionError("انتهت جلسة الدخول.")
        return result

    # ------------------------------------------------------------------ normal intact return to the original custody
    def return_holding(self, pool_id, intact_confirmed, return_date, reference, notes, request_key, *, _expected_actor=None):
        """Take ONE whole source back into the ORIGINAL OPEN custody. Needs the OWNER's explicit intact confirmation; nothing else
        (a full balance alone) certifies the same, whole, undivided bar."""
        session_actor = self._writer(_expected_actor)
        pool_id = _pool_id(pool_id)
        returned_on = _entry_date(return_date)
        reference, notes, key = _text(reference, PAYMENT_REFERENCE_MAX, "المرجع"), _text(notes, NOTES_MAX, "الملاحظات"), _key(request_key)
        confirmed = intact_confirmed is True or str(intact_confirmed).strip().lower() == "true"
        digest = hashlib.sha256(json.dumps([HOLD_RETURN, pool_id, returned_on.isoformat(), reference, notes, confirmed],
                                           ensure_ascii=False).encode()).hexdigest()
        try:
            return self._post_return(session_actor, pool_id, confirmed, returned_on, reference, notes, key, digest)
        except IntegrityError as exc:
            if getattr(exc, "errno", None) != 1062 or not any(k in str(exc) for k in KEY_CONSTRAINTS):
                raise
            return self._replay_after_twin(session_actor, key, digest, HOLD_RETURN, exc)

    def _post_return(self, session_actor, pool_id, confirmed, returned_on, reference, notes, key, digest):
        with self._transaction() as connection:
            actor = self._verified(connection, session_actor)
            repository, holdings, parties = self._repository(connection), self._holdings(connection), self._parties(connection)
            handover = holdings.pool_handover(pool_id)                           # immutable: a plain read only finds the trip to lock
            if handover is None:
                raise TripError("HOLDING_NOT_FOUND", "مصدر الذهب لدى المسوقجي غير موجود.")
            trip_id, lot_id, party_id = int(handover["trip_id"]), int(handover["lot_id"]), int(handover["party_id"])
            trip = repository.trip(trip_id, lock=True)                           # trip -> lot -> party -> pool
            replay = self._prior(repository, holdings, parties, key, digest, actor, HOLD_RETURN)
            if replay is not None:
                return replay
            if not confirmed:
                raise TripError("INTACT_CONFIRMATION_REQUIRED", f"سجّل التأكيد الصريح: «{CONFIRMATION_TEXT}». الرصيد وحده لا يثبت ذلك.")
            if trip["status"] != OPEN:
                raise TripError("TRIP_CLOSED", f"العهدة الأصلية {trip_code(trip_id)} مغلقة: أعد فتحها صراحةً قبل تسجيل الرجوع.")
            locked = repository.lock_lots([lot_id])
            if not locked:
                raise TripError("LOT_NOT_FOUND", "الشحنة غير موجودة.")
            lot = locked[0]
            if parties.get_supplier(party_id, lock=True) is None:                # an inactive party is resolved normally
                raise TripError("PARTY_NOT_FOUND", "المسوقجي غير موجود.")
            pool = holdings.lock_pool(pool_id)
            full = Decimal(str(pool["weight_grams"]))
            balance = Decimal(str(holdings.pool_balance(pool_id)))                # CURRENT, compensations included
            if balance == 0:
                raise TripError("HOLDING_NOT_AVAILABLE", "لا يوجد رصيد من هذه السبكة لدى المسوقجي (رُجعت أو عُكس تسليمها).")
            allocated = [r for r in holdings.pool_settlement_history(pool_id) if r["physical_declaration"] in ALLOCATING]     # CURRENT, reversed ones included
            if allocated:                                                       # a full balance after a reversal never certifies an intact bar
                raise TripError("ALLOCATION_HISTORY_BLOCKS_RETURN", allocation_note(allocated[0]["id"]))
            if balance != full:
                raise TripError("HOLDING_PARTIAL", "رصيد المصدر لا يطابق وزن السبكة كاملة: لا يُسجَّل الرجوع حتى تُراجع الأرصدة.")
            current = next(iter(repository.custody_lots(trip_id, lot_id=lot_id)), None)
            in_custody = Decimal("0") if current is None else Decimal(str(current["outstanding_grams"]))
            if (in_custody != 0 or Decimal(str(repository.shop_lot_balance(lot_id))) != 0
                    or Decimal(str(holdings.lot_held_total(lot_id))) != balance):
                raise TripError("LOT_IDENTITY_CONFLICT", "هوية السبكة غير مؤكدة (ظاهرة في أكثر من مكان): لم يتحرك أي رصيد.")
            now = repository.db_now()
            entry_no = repository.next_entry_no(trip_id)
            entry_id = repository.insert_entry(trip_id=trip_id, entry_no=entry_no, kind=HOLD_RETURN, entry_date=returned_on, notes=notes,
                                               key=key, digest=digest, actor=actor, now=now)
            document_id = holdings.insert_document(kind=RETURN, pool=pool, trip_id=trip_id, document_date=returned_on, reference=reference,
                                                   notes=notes, confirmation_code=CONFIRMATION_CODE, confirmed_by=actor.user_id,
                                                   confirmed_at=now, key=key, digest=digest, actor=actor, now=now)
            code, ih = entry_code(trip_id, entry_no), holding_code(document_id)
            custody_id = repository.insert_lot_movement(lot_id=lot_id, entry_id=entry_id, movement_type=HOLD_IN, weight_delta=full)
            holdings.insert_movement(pool_id=pool_id, document_id=document_id, doc_kind=RETURN, movement_type=MOVE_RETURN, weight=full, delta=-full)
            holdings.insert_link(entry_id=entry_id, entry_kind=HOLD_RETURN, trip_id=trip_id, document_id=document_id, doc_kind=RETURN,
                                 pool_id=pool_id, lot_id=lot_id, weight=full, custody_movement_id=custody_id, movement_type=HOLD_IN, delta=full)
            label = (f"رجوع سبكة من المسوقجي {ih} إلى عهدة السوق {code}: {full:,.3f} جرام • العيار الفعلي {pool['fineness_permille']} بالألف "
                     f"• تأكيد المالك: {CONFIRMATION_TEXT}")
            metadata = {"trip_id": trip_id, "party_id": party_id, "pool_id": pool_id, "lot_id": str(lot_id), "origin_melt_entry_id": str(lot["entry_id"]),
                        "document": ih, "custody_entry": code, "weight": str(full), "fineness": str(pool["fineness_permille"]),
                        "intact_confirmation": CONFIRMATION_CODE, "confirmed_by_user_id": actor.user_id, "confirmed_at": now.isoformat(sep=" "),
                        "date": returned_on.isoformat(), "employee_id": actor.employee_id}
            repository.audit(actor=actor, event_type="MARKET_TRIP_HOLD_RETURN", entity_id=entry_id, reference=code, description=label,
                             metadata=metadata, now=now)
            holdings.audit(actor=actor, event_type="INTERMEDIARY_HOLDING_RETURN", entity_id=document_id, reference=ih, description=label,
                           metadata=metadata, now=now)
            result = self._result(repository.by_request(key), holdings.by_request(key), HOLD_RETURN, False)
            if not self._session.is_current(session_actor):
                raise PermissionError("انتهت جلسة الدخول.")
        return result

    # ------------------------------------------------------------------ master-karat LOGO holdings (049): pooled physical grams, partial returns
    @staticmethod
    def logo_handover_check(available, grams, party):
        """Shared by the preview and the posting (stored facts only): an active intermediary and a quantity within the effective LOGO outstanding."""
        if grams <= 0:
            raise TripError("WEIGHT_INVALID", "اكتب كمية موجبة بالجرام.")
        if not bool(party.get("is_intermediary", False)):
            raise TripError("INTERMEDIARY_ROLE_REQUIRED", "هذا الطرف ليس مسوقجيًا: أضف له دور المسوقجي أولًا.")
        if not bool(party.get("is_active", True)):
            raise TripError("PARTY_INACTIVE", "المسوقجي موقوف: لا تسليم جديد له (ما عنده يمكن استلامه).")
        if grams > available:
            raise TripError("LOGO_EXCEEDS_CUSTODY", f"المتاح من لوجو هذا العيار في العهدة {available:,.3f} جرام فقط.")

    @staticmethod
    def _logo_result(entry, document, kind, duplicate):
        return {"entry_id": str(entry["id"]), "trip_id": str(entry["trip_id"]), "trip_code": trip_code(entry["trip_id"]),
                "code": entry_code(entry["trip_id"], entry["entry_no"]), "kind": kind, "created": False, "duplicate": duplicate,
                "document_id": str(document["id"]), "document_code": logo_holding_code(document["id"]), "pool_id": str(document["pool_id"]),
                "party_id": str(document["party_id"]), "karat_id": str(document["karat_id"]), "weight": str(document["weight_grams"])}

    def _prior_logo(self, repository, holdings, settlements, key, digest, actor, kind):
        """Exact replay of a LOGO request, decided before any availability is derived; any other use of the key is a conflict."""
        entry, document, header = repository.by_request(key), holdings.logo_by_request(key), settlements.by_request(key)
        if entry is None and document is None and header is None and holdings.by_request(key) is None:
            return None
        link = None if entry is None else holdings.logo_link_by_entry(entry["id"])
        if (entry is None or document is None or header is not None or link is None or entry["entry_kind"] != kind
                or int(link["document_id"]) != int(document["id"]) or entry["request_hash"] != digest or document["request_hash"] != digest
                or int(entry["performed_by_user_id"]) != int(actor.user_id)):
            raise TripError("REQUEST_CONFLICT", "طلب العملية مستخدم لعملية مختلفة.")
        return self._logo_result(entry, document, kind, True)

    def _replay_logo_after_twin(self, session_actor, key, digest, kind, original):
        with self._transaction() as connection:
            actor = self._verified(connection, session_actor)
            result = self._prior_logo(self._repository(connection), self._holdings(connection), self._parties(connection), key, digest, actor, kind)
            if result is None:
                raise original
            return result

    def hand_over_logo(self, trip_id, party_id, karat_id, grams, handover_date, reference, notes, request_key, *, _expected_actor=None):
        """Hand pooled LOGO grams of ONE master karat of an OPEN custody to ONE active intermediary. One source pool per handover."""
        session_actor = self._writer(_expected_actor)
        trip_id, party_id, karat_id = _trip_id(trip_id), _trip_id(party_id), _trip_id(karat_id)
        quantity = parse_weight(grams)
        handed_on = _entry_date(handover_date)
        reference, notes, key = _text(reference, PAYMENT_REFERENCE_MAX, "المرجع"), _text(notes, NOTES_MAX, "الملاحظات"), _key(request_key)
        digest = hashlib.sha256(json.dumps([LOGO_HOLD, trip_id, party_id, karat_id, str(quantity), handed_on.isoformat(), reference, notes],
                                           ensure_ascii=False).encode()).hexdigest()
        try:
            return self._post_logo_handover(session_actor, trip_id, party_id, karat_id, quantity, handed_on, reference, notes, key, digest)
        except IntegrityError as exc:
            if getattr(exc, "errno", None) != 1062 or not any(k in str(exc) for k in KEY_CONSTRAINTS):
                raise
            return self._replay_logo_after_twin(session_actor, key, digest, LOGO_HOLD, exc)

    def _post_logo_handover(self, session_actor, trip_id, party_id, karat_id, quantity, handed_on, reference, notes, key, digest):
        with self._transaction() as connection:
            actor = self._verified(connection, session_actor)
            repository, holdings, parties = self._repository(connection), self._holdings(connection), self._parties(connection)
            trip = repository.trip(trip_id, lock=True)                           # trip -> party (no lots) -> new pool
            if trip is None:
                raise TripError("TRIP_NOT_FOUND", "العهدة غير موجودة.")
            replay = self._prior_logo(repository, holdings, parties, key, digest, actor, LOGO_HOLD)
            if replay is not None:
                return replay
            if trip["status"] != OPEN:
                raise TripError("TRIP_CLOSED", "العهدة مغلقة: أعد فتحها أولًا.")
            party = parties.get_supplier(party_id, lock=True)
            if party is None:
                raise TripError("PARTY_NOT_FOUND", "المسوقجي غير موجود.")
            karat = repository.karat(karat_id)
            if karat is None or not karat["is_active"]:
                raise TripError("KARAT_NOT_FOUND", "العيار غير موجود أو غير نشط.")
            custody = custody_view(repository.custody_cash(trip_id, current=True), repository.custody_gold(trip_id, current=True))      # CURRENT reads
            line = next((g for g in custody["gold"] if g["karat_id"] == karat_id and g["designation"] == "LOGO"), None)
            available = ZERO_WEIGHT if line is None else line["outstanding"]
            self.logo_handover_check(available, quantity, party)
            now = repository.db_now()
            pool_id = holdings.insert_logo_pool(party_id=party_id, karat_id=karat_id, trip_id=trip_id, weight=quantity, user_id=actor.user_id)
            pool = {"id": pool_id, "party_id": party_id, "karat_id": karat_id, "trip_id": trip_id}
            entry_no = repository.next_entry_no(trip_id)
            entry_id = repository.insert_entry(trip_id=trip_id, entry_no=entry_no, kind=LOGO_HOLD, entry_date=handed_on, notes=notes, key=key,
                                               digest=digest, actor=actor, now=now)
            document_id = holdings.insert_logo_document(kind=HANDOVER, pool=pool, weight=quantity, document_date=handed_on, reference=reference,
                                                        notes=notes, key=key, digest=digest, actor=actor, now=now)
            code, ig = entry_code(trip_id, entry_no), logo_holding_code(document_id)
            holdings.insert_logo_movement(pool_id=pool_id, document_id=document_id, doc_kind=HANDOVER, movement_type=MOVE_HANDOVER, weight=quantity, delta=quantity)
            holdings.insert_logo_link(entry_id=entry_id, entry_kind=LOGO_HOLD, trip_id=trip_id, document_id=document_id, doc_kind=HANDOVER,
                                      pool_id=pool_id, karat_id=karat_id, weight=quantity)
            label = f"تسليم لوجو لمسوقجي {ig} من عهدة السوق {code}: {quantity:,.3f} جرام عيار {int(karat['karat_value'])} • {party['name']}"
            metadata = {"trip_id": trip_id, "party_id": party_id, "pool_id": pool_id, "karat": int(karat["karat_value"]), "designation": "LOGO",
                        "document": ig, "custody_entry": code, "weight": str(quantity), "date": handed_on.isoformat(), "employee_id": actor.employee_id}
            repository.audit(actor=actor, event_type="MARKET_TRIP_LOGO_HOLD", entity_id=entry_id, reference=code, description=label, metadata=metadata, now=now)
            holdings.audit(actor=actor, event_type="INTERMEDIARY_LOGO_HANDOVER", entity_id=document_id, reference=ig, description=label, metadata=metadata, now=now)
            result = self._logo_result(repository.by_request(key), holdings.logo_by_request(key), LOGO_HOLD, False)
            if not self._session.is_current(session_actor):
                raise PermissionError("انتهت جلسة الدخول.")
        return result

    def return_logo(self, pool_id, grams, return_date, reference, notes, request_key, *, _expected_actor=None):
        """Take back some or all of ONE source into the ORIGINAL OPEN custody. No intact-bar confirmation: LOGO gold is pooled grams."""
        session_actor = self._writer(_expected_actor)
        pool_id = _pool_id(pool_id)
        quantity = parse_weight(grams)
        returned_on = _entry_date(return_date)
        reference, notes, key = _text(reference, PAYMENT_REFERENCE_MAX, "المرجع"), _text(notes, NOTES_MAX, "الملاحظات"), _key(request_key)
        digest = hashlib.sha256(json.dumps([HOLD_BACK, pool_id, str(quantity), returned_on.isoformat(), reference, notes], ensure_ascii=False).encode()).hexdigest()
        try:
            return self._post_logo_return(session_actor, pool_id, quantity, returned_on, reference, notes, key, digest)
        except IntegrityError as exc:
            if getattr(exc, "errno", None) != 1062 or not any(k in str(exc) for k in KEY_CONSTRAINTS):
                raise
            return self._replay_logo_after_twin(session_actor, key, digest, HOLD_BACK, exc)

    def _post_logo_return(self, session_actor, pool_id, quantity, returned_on, reference, notes, key, digest):
        with self._transaction() as connection:
            actor = self._verified(connection, session_actor)
            repository, holdings, parties = self._repository(connection), self._holdings(connection), self._parties(connection)
            handover = holdings.logo_pool_handover(pool_id)                       # immutable: a plain read only finds the trip to lock
            if handover is None:
                raise TripError("HOLDING_NOT_FOUND", "مصدر الذهب لدى المسوقجي غير موجود.")
            trip_id, party_id, karat_id = int(handover["trip_id"]), int(handover["party_id"]), int(handover["karat_id"])
            trip = repository.trip(trip_id, lock=True)                            # trip -> party -> pool
            replay = self._prior_logo(repository, holdings, parties, key, digest, actor, HOLD_BACK)
            if replay is not None:
                return replay
            if quantity <= 0:
                raise TripError("WEIGHT_INVALID", "اكتب كمية موجبة بالجرام.")
            if trip["status"] != OPEN:
                raise TripError("TRIP_CLOSED", f"العهدة الأصلية {trip_code(trip_id)} مغلقة: أعد فتحها صراحةً قبل تسجيل الرجوع.")
            if parties.get_supplier(party_id, lock=True) is None:                 # an inactive party is resolved normally
                raise TripError("PARTY_NOT_FOUND", "المسوقجي غير موجود.")
            pool = holdings.lock_logo_pool(pool_id)
            balance = Decimal(str(holdings.logo_pool_balance(pool_id)))           # CURRENT, compensations included
            if balance <= 0:
                raise TripError("HOLDING_NOT_AVAILABLE", "لا يوجد رصيد من هذا المصدر لدى المسوقجي (رُجع كله أو عُكس تسليمه).")
            if quantity > balance:
                raise TripError("LOGO_RETURN_EXCEEDS_HOLDING", f"المتبقي من هذا المصدر لدى المسوقجي {balance:,.3f} جرام فقط.")
            now = repository.db_now()
            entry_no = repository.next_entry_no(trip_id)
            entry_id = repository.insert_entry(trip_id=trip_id, entry_no=entry_no, kind=HOLD_BACK, entry_date=returned_on, notes=notes, key=key,
                                               digest=digest, actor=actor, now=now)
            document_id = holdings.insert_logo_document(kind=RETURN, pool=pool, weight=quantity, document_date=returned_on, reference=reference,
                                                        notes=notes, key=key, digest=digest, actor=actor, now=now)
            code, ig = entry_code(trip_id, entry_no), logo_holding_code(document_id)
            holdings.insert_logo_movement(pool_id=pool_id, document_id=document_id, doc_kind=RETURN, movement_type=MOVE_RETURN, weight=quantity, delta=-quantity)
            holdings.insert_logo_link(entry_id=entry_id, entry_kind=HOLD_BACK, trip_id=trip_id, document_id=document_id, doc_kind=RETURN,
                                      pool_id=pool_id, karat_id=karat_id, weight=quantity)
            label = f"رجوع لوجو من المسوقجي {ig} إلى عهدة السوق {code}: {quantity:,.3f} جرام (المتبقي لديه {balance - quantity:,.3f})"
            metadata = {"trip_id": trip_id, "party_id": party_id, "pool_id": pool_id, "karat_id": karat_id, "document": ig, "custody_entry": code,
                        "weight": str(quantity), "remaining": str(balance - quantity), "date": returned_on.isoformat(), "employee_id": actor.employee_id}
            repository.audit(actor=actor, event_type="MARKET_TRIP_HOLD_BACK", entity_id=entry_id, reference=code, description=label, metadata=metadata, now=now)
            holdings.audit(actor=actor, event_type="INTERMEDIARY_LOGO_RETURN", entity_id=document_id, reference=ig, description=label, metadata=metadata, now=now)
            result = self._logo_result(repository.by_request(key), holdings.logo_by_request(key), HOLD_BACK, False)
            if not self._session.is_current(session_actor):
                raise PermissionError("انتهت جلسة الدخول.")
        return result
