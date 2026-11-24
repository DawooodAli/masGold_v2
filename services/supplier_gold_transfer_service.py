"""Supplier-to-supplier gold transfer «تسوية ذهب من حساب مورد» (GTxxxxxx, migration 108).

The gold a SOURCE supplier holds for the shop (its ledger balance in its own dedicated karat is negative, «لنا عند المورد») settles what the shop owes a RECIPIENT supplier. One transaction
posts the immutable GT document and exactly two supplier gold movements and nothing else (no Scrap, custody, cash, bank, making or inventory):
  * the SOURCE ledger moves UP by the transferred grams of ITS karat (it holds less for the shop),
  * the RECIPIENT ledger moves DOWN by the credited grams of ITS karat (the shop owes it less).
credited = transferred x source karat / recipient karat (services.supplier_rules.settlement_credit: 0.001 g ROUND_HALF_UP, the six-decimal analytical value and the rounding difference are stored).
The transfer is limited to what the source holds for the shop in its karat; the recipient's payable may cross zero (as with every supplier settlement). Both parties need the SUPPLIER role and a resolved karat.

Rights (explicit grants, re-checked inside the transaction through AuthorizationService; the actor is the AppSession user, never a caller identity):
  SUPPLIER_GOLD_SETTLE          OWNER, MANAGER   post a transfer (the same right as every supplier gold settlement)
  SUPPLIER_GOLD_SETTLE_REVERSE  OWNER only       reverse one (mandatory reason)
Idempotent on the request key (same key + same payload + same user returns the first result; anything else is a REQUEST_CONFLICT). Lock order: actor -> both suppliers in ASCENDING id -> request key.
A reversal is one more immutable row naming the original and posting the EXACT opposite stored movements; a reversal is never reversed and an original is reversed once.
"""
import hashlib
import json
import uuid
from decimal import Decimal

from mysql.connector import IntegrityError

from repositories.market_trip_repository import MarketTripRepository, entry_code
from repositories.supplier_gold_transfer_repository import (IN, IN_REVERSAL, MARKER, MARKER_REVERSAL, OUT, OUT_REVERSAL, SupplierGoldTransferRepository, transfer_code)
from services import supplier_rules as rules
from services.authorization_service import AuthorizationService
from services.invoice_service import invoice_transaction
from services.supplier_gold_settlement_service import _date, _id, _key
from services.supplier_service import authenticated_actor, clean_text

SETTLE, REVERSE = "SUPPLIER_GOLD_SETTLE", "SUPPLIER_GOLD_SETTLE_REVERSE"
REFERENCE_MAX, NOTES_MAX, REASON_MAX = 100, 1000, 500
SCHEMA_MESSAGE = "قاعدة البيانات تحتاج تحديث 108 قبل تحويل الذهب بين الموردين."


def _is_request_key_violation(exc):
    return getattr(exc, "errno", None) == 1062 and "uq_sgt_request" in str(exc)


class SupplierGoldTransferService:
    def __init__(self, session, *, transaction_factory=invoice_transaction, repository=SupplierGoldTransferRepository, trips=MarketTripRepository, authorization=None):
        self._session, self._transaction, self._repository, self._trips = session, transaction_factory, repository, trips
        self._authorization = authorization or AuthorizationService(session)

    # ------------------------------------------------------------------ UI hints (never the authority)
    def can_transfer(self):
        actor = self._session.current_actor()
        return actor is not None and SETTLE in actor.permissions

    def can_reverse(self):
        actor = self._session.current_actor()
        return actor is not None and REVERSE in actor.permissions

    # ------------------------------------------------------------------ reads
    def sources(self, to_supplier_id, *, _expected_actor=None):
        """The suppliers that hold gold for the shop and may fund a settlement of `to_supplier_id`: a display catalogue, never an availability decision (the chosen one is read again under the locks)."""
        actor = authenticated_actor(self._session, SETTLE, "ليس لديك صلاحية تسوية ذهب الموردين.", _expected_actor)
        recipient = _id(to_supplier_id, "SUPPLIER_NOT_FOUND", "المورد غير موجود.")
        with self._transaction() as connection:
            self._authorization.require_permission_read(connection, actor, SETTLE)
            repository = self._repository(connection)
            if not repository.has_schema():
                return []
            rows = repository.source_candidates(recipient)
        return [{"key": f"supplier:{int(r['id'])}", "source_type": "SUPPLIER", "supplier_id": str(r["id"]), "karat_id": str(r["karat_id"]),
                 "karat_value": int(r["karat_value"]), "available": str(Decimal(str(r["held"]))),
                 "text": f"حساب مورد: {r['name']} • لنا عنده {Decimal(str(r['held'])):,.3f} جرام عيار {int(r['karat_value'])}" + ("" if r["is_active"] else " (غير نشط)")}
                for r in rows]

    def context(self, from_supplier_id, to_supplier_id, *, _expected_actor=None):
        """What the dialog needs after a source is picked: the grams the source holds for the shop (read now) and its karat."""
        actor = authenticated_actor(self._session, SETTLE, "ليس لديك صلاحية تسوية ذهب الموردين.", _expected_actor)
        source_id, recipient_id = _id(from_supplier_id, "SUPPLIER_NOT_FOUND", "المورد غير موجود."), _id(to_supplier_id, "SUPPLIER_NOT_FOUND", "المورد غير موجود.")
        with self._transaction() as connection:
            self._authorization.require_permission_read(connection, actor, SETTLE)
            repository = self._repository(connection)
            source = repository.get_supplier(source_id)
            if source is None or not rules.has_supplier_role(source) or source["karat_id"] is None:
                raise rules.SupplierRuleError("SOURCE_NOT_FOUND", "مورد المصدر غير موجود أو عياره غير محدد.")
            held = -Decimal(str(repository.karat_balance(source_id, source["karat_id"])))
        if held <= 0:
            raise rules.SupplierRuleError("SOURCE_EMPTY", "لا يوجد ذهب لنا عند هذا المورد.")
        return {"supplier_id": str(recipient_id), "available": held, "karat_value": int(source["karat_value"]), "name": source["name"]}

    @staticmethod
    def document(row):
        return {"id": str(row["id"]), "code": transfer_code(row["id"]), "from_supplier_id": str(row["from_supplier_id"]), "from_name": row.get("from_name"),
                "to_supplier_id": str(row["to_supplier_id"]), "to_name": row.get("to_name"), "from_karat": int(row["from_karat_value"]), "to_karat": int(row["to_karat_value"]),
                "transferred_weight": row["from_weight_grams"], "credited_weight": row["to_weight_grams"], "analytical": row["to_analytical_grams"],
                "rounding_difference": row["rounding_difference_grams"], "reverses": None if row.get("reverses_id") is None else str(row["reverses_id"])}

    # ------------------------------------------------------------------ the custody log marker (109)
    def _lock_trip(self, connection, repository):
        """The newest OPEN legacy custody, locked BEFORE any supplier (the order every custody operation uses), or None (no marker is written). Below 109 there is no marker at all."""
        if not repository.has_marker_schema():
            return None
        trip_id = repository.latest_open_trip()
        if trip_id is None:
            return None
        trips = self._trips(connection)
        trip = trips.trip(trip_id, lock=True)
        return trip_id if trip is not None and trip["status"] == "OPEN" else None

    def _write_marker(self, connection, repository, trip_id, transfer_id, kind, moved_on, key, digest, actor, now, text):
        """ONE immutable log entry in the custody that records the transfer; it has no cash, gold or Scrap leg, so the custody balances never change. Returns its code."""
        if trip_id is None:
            return None
        trips = self._trips(connection)
        entry_no = trips.next_entry_no(trip_id)
        entry_key = str(uuid.uuid5(uuid.NAMESPACE_URL, f"masgold-supplier-gold-transfer-marker:{key}"))                  # its own request key: never the transfer's (a key is used once per table)
        entry_id = trips.insert_entry(trip_id=trip_id, entry_no=entry_no, kind=kind, entry_date=moved_on, notes=text, key=entry_key, digest=digest, actor=actor, now=now)
        repository.link_entry(transfer_id, entry_id)
        return entry_code(trip_id, entry_no)

    # ------------------------------------------------------------------ the transfer
    def transfer(self, from_supplier_id, to_supplier_id, weight, transfer_date, reference, notes, request_key, *, _expected_actor=None):
        session_actor = authenticated_actor(self._session, SETTLE, "ليس لديك صلاحية تسوية ذهب الموردين.", _expected_actor)
        source_id = _id(from_supplier_id, "SOURCE_NOT_FOUND", "اختر المورد الذي يُسحب الذهب من حسابه.")
        recipient_id = _id(to_supplier_id, "SUPPLIER_NOT_FOUND", "المورد غير موجود.")
        if source_id == recipient_id:
            raise rules.SupplierRuleError("SAME_SUPPLIER", "لا يمكن التحويل من حساب المورد إلى نفسه.")
        grams = rules.parse_delivered_weight(weight)
        moved_on = _date(transfer_date)
        reference_text, notes_text = clean_text(reference, REFERENCE_MAX, "المرجع"), clean_text(notes, NOTES_MAX, "الملاحظات")
        key = _key(request_key)
        digest = hashlib.sha256(json.dumps([source_id, recipient_id, str(grams), moved_on.isoformat(), reference_text, notes_text], ensure_ascii=False).encode()).hexdigest()
        try:
            return self._post(session_actor, source_id, recipient_id, grams, moved_on, reference_text, notes_text, key, digest)
        except IntegrityError as exc:
            if not _is_request_key_violation(exc):
                raise                                          # any other integrity error is a real defect: never hidden
            return self._replay(session_actor, key, digest, exc)

    def _post(self, session_actor, source_id, recipient_id, grams, moved_on, reference, notes, key, digest):
        with self._transaction() as connection:
            actor = self._authorization.require_permission_for_write(connection, session_actor, SETTLE)
            repository = self._repository(connection)
            if not repository.has_schema():
                raise rules.SupplierRuleError("SCHEMA_OUTDATED", SCHEMA_MESSAGE)
            trip_id = self._lock_trip(connection, repository)                                   # custody trip -> suppliers (ascending id) -> request key
            parties = {}
            for party_id in sorted({source_id, recipient_id}):                                  # ascending id: two concurrent opposite transfers cannot deadlock
                parties[party_id] = repository.get_supplier(party_id, lock=True)
            prior = repository.by_request(key)
            if prior is not None:
                return self._replayed(prior, digest, actor)
            if repository.request_key_used_elsewhere(key):
                raise rules.SupplierRuleError("REQUEST_CONFLICT", "طلب العملية مستخدم لعملية مختلفة.")
            source, recipient = parties[source_id], parties[recipient_id]
            if source is None:
                raise rules.SupplierRuleError("SOURCE_NOT_FOUND", "مورد المصدر غير موجود.")
            if recipient is None:
                raise rules.SupplierRuleError("SUPPLIER_NOT_FOUND", "المورد غير موجود.")
            for party in (source, recipient):
                rules.require_supplier_role(party)
                if party["karat_id"] is None:
                    raise rules.SupplierRuleError("SUPPLIER_KARAT_MISSING", f"عيار المورد {party['name']} غير محدد: حدّده من تعديل المورد أولًا.")
            held = -Decimal(str(repository.karat_balance(source_id, source["karat_id"])))
            if held <= 0:
                raise rules.SupplierRuleError("SOURCE_EMPTY", f"لا يوجد ذهب لنا عند {source['name']}.")
            if grams > held:
                raise rules.SupplierRuleError("SOURCE_SHORT", f"الوزن أكبر من الذهب الذي لنا عند {source['name']}: المتاح {held:,.3f} جرام.")
            credited, analytical, difference = rules.settlement_credit(grams, source["karat_value"], recipient["karat_value"])
            now = repository.db_now()
            transfer_id = repository.insert_transfer(source=source, recipient=recipient, from_weight=grams, to_weight=credited, analytical=analytical, difference=difference,
                                                     transfer_date=moved_on, reference=reference, notes=notes, key=key, digest=digest, actor=actor, now=now)
            code = transfer_code(transfer_id)
            self._post_movements(repository, transfer_id, code, source, recipient, grams, credited, out_code=OUT, in_code=IN, sign=1,
                                 label_out=f"سحب ذهب لتسوية {recipient['name']}", label_in=f"تسوية ذهب من حساب {source['name']}", now=now, user_id=actor.user_id)
            marker = self._write_marker(connection, repository, trip_id, transfer_id, MARKER, moved_on, key, digest, actor, now,
                                        f"{code}: {grams:,.3f} جرام عيار {source['karat_value']} من حساب {source['name']} ← {credited:,.3f} جرام عيار {recipient['karat_value']} للمورد {recipient['name']}")
            repository.audit(actor=actor, event_type="SUPPLIER_GOLD_TRANSFERRED", entity_id=transfer_id, entity_type="SUPPLIER_GOLD_TRANSFER", reference=code,
                             description=f"تسوية ذهب {recipient['name']} من حساب {source['name']}",
                             metadata={"from": source_id, "to": recipient_id, "transferred": str(grams), "from_karat": int(source["karat_value"]),
                                       "credited": str(credited), "to_karat": int(recipient["karat_value"]), "custody_log": marker}, now=now)
            if not self._session.is_current(session_actor):
                raise PermissionError("انتهت جلسة الدخول.")
            return {"id": str(transfer_id), "code": code, "from_supplier_id": str(source_id), "to_supplier_id": str(recipient_id), "from_karat": int(source["karat_value"]),
                    "to_karat": int(recipient["karat_value"]), "transferred_weight": str(grams), "credited_weight": str(credited), "analytical": str(analytical),
                    "rounding_difference": str(difference), "custody_log": marker, "duplicate": False}

    @staticmethod
    def _post_movements(repository, transfer_id, code, source, recipient, from_weight, to_weight, *, out_code, in_code, sign, label_out, label_in, now, user_id):
        """The two ledger rows. sign +1 = the transfer (source UP, recipient DOWN), -1 = its exact opposite."""
        out_type, in_type = repository.gold_type_id(out_code), repository.gold_type_id(in_code)
        if out_type is None or in_type is None:
            raise RuntimeError("supplier gold movement types ACCOUNT_TRANSFER_* are missing (migration 108)")
        out_delta, in_delta = sign * from_weight, -sign * to_weight
        repository.insert_gold_movement(supplier_id=source["id"], type_id=out_type, karat_id=source["karat_id"], weight_delta=out_delta,
                                        eq21_delta=rules.equivalent_21(out_delta, source["karat_value"]), source_id=transfer_id, reference=code, description=label_out, now=now, user_id=user_id)
        repository.insert_gold_movement(supplier_id=recipient["id"], type_id=in_type, karat_id=recipient["karat_id"], weight_delta=in_delta,
                                        eq21_delta=rules.equivalent_21(in_delta, recipient["karat_value"]), source_id=transfer_id, reference=code, description=label_in, now=now, user_id=user_id)

    @staticmethod
    def _replayed(prior, digest, actor):
        if prior["request_hash"] != digest or int(prior["performed_by_user_id"]) != int(actor.user_id) or prior.get("reverses_id") is not None:
            raise rules.SupplierRuleError("REQUEST_CONFLICT", "طلب التحويل مستخدم لعملية مختلفة.")
        return {"id": str(prior["id"]), "code": transfer_code(prior["id"]), "from_supplier_id": str(prior["from_supplier_id"]), "to_supplier_id": str(prior["to_supplier_id"]),
                "from_karat": int(prior["from_karat_value"]), "to_karat": int(prior["to_karat_value"]), "transferred_weight": str(prior["from_weight_grams"]),
                "credited_weight": str(prior["to_weight_grams"]), "analytical": str(prior["to_analytical_grams"]), "rounding_difference": str(prior["rounding_difference_grams"]),
                "duplicate": True}

    def _replay(self, session_actor, key, digest, original):
        """The losing side of a concurrent same-key request, in a new transaction: decided from a FRESH read, nothing is written."""
        with self._transaction() as connection:
            actor = self._authorization.require_permission_for_write(connection, session_actor, SETTLE)
            prior = self._repository(connection).by_request(key)
            if prior is None:
                raise original
            return self._replayed(prior, digest, actor)

    # ------------------------------------------------------------------ reversal
    def reverse(self, transfer_id, reason, request_key, *, _expected_actor=None):
        """«عكس تسوية ذهب من حساب مورد»: OWNER only (rechecked in the transaction), a mandatory reason. The EXACT stored grams and karats are undone, never a recomputed amount: the source
        holds the gold for the shop again and the recipient owes the credit again. An original is reversed once and a reversal is never reversed."""
        session_actor = authenticated_actor(self._session, REVERSE, "عكس تسوية الذهب بين الموردين للمالك فقط.", _expected_actor)
        original_id = _id(transfer_id, "TRANSFER_NOT_FOUND", "التحويل غير موجود.")
        key = _key(request_key)
        reason_text = clean_text(reason, REASON_MAX, "السبب", required=True)
        digest = hashlib.sha256(json.dumps(["REVERSE", original_id, reason_text], ensure_ascii=False).encode()).hexdigest()
        with self._transaction() as connection:
            actor = self._authorization.require_permission_for_write(connection, session_actor, REVERSE)
            repository = self._repository(connection)
            if not repository.has_schema():
                raise rules.SupplierRuleError("SCHEMA_OUTDATED", SCHEMA_MESSAGE)
            prior = repository.by_request(key)
            if prior is not None:
                if prior.get("reverses_id") is None or int(prior["reverses_id"]) != original_id or prior["request_hash"] != digest or int(prior["performed_by_user_id"]) != int(actor.user_id):
                    raise rules.SupplierRuleError("REQUEST_CONFLICT", "طلب العكس مستخدم لعملية مختلفة.")
                return {"id": str(prior["id"]), "code": transfer_code(prior["id"]), "reverses": str(original_id), "duplicate": True}
            original = repository.transfer(original_id)
            if original is None:
                raise rules.SupplierRuleError("TRANSFER_NOT_FOUND", "التحويل غير موجود.")
            trip_id = self._lock_trip(connection, repository)
            for party_id in sorted({int(original["from_supplier_id"]), int(original["to_supplier_id"])}):
                repository.get_supplier(party_id, lock=True)
            original = repository.transfer(original_id, lock=True)                 # lock order: suppliers (ascending) -> the document (a concurrent reversal waits here)
            if original.get("reverses_id") is not None:
                raise rules.SupplierRuleError("TRANSFER_IS_REVERSAL", "هذه حركة عكس لتحويل سابق: لا تُعكس مرة أخرى.")
            if repository.reversal_of(original_id) is not None:
                raise rules.SupplierRuleError("ALREADY_REVERSED", "هذا التحويل معكوس بالفعل.")
            if repository.request_key_used_elsewhere(key):
                raise rules.SupplierRuleError("REQUEST_CONFLICT", "طلب العملية مستخدم لعملية مختلفة.")
            source = {"id": original["from_supplier_id"], "karat_id": original["from_karat_id"], "karat_value": original["from_karat_value"], "name": original["from_name"]}
            recipient = {"id": original["to_supplier_id"], "karat_id": original["to_karat_id"], "karat_value": original["to_karat_value"], "name": original["to_name"]}
            from_weight, to_weight = Decimal(str(original["from_weight_grams"])), Decimal(str(original["to_weight_grams"]))
            now = repository.db_now()
            new_id = repository.insert_transfer(source=source, recipient=recipient, from_weight=from_weight, to_weight=to_weight,
                                                analytical=original["to_analytical_grams"], difference=original["rounding_difference_grams"],
                                                transfer_date=original["transfer_date"], reference=transfer_code(original_id), notes=None, key=key, digest=digest, actor=actor,
                                                now=now, reverses_id=original_id, reason=reason_text)
            code = transfer_code(new_id)
            self._post_movements(repository, new_id, code, source, recipient, from_weight, to_weight, out_code=OUT_REVERSAL, in_code=IN_REVERSAL, sign=-1,
                                 label_out=f"عكس سحب ذهب لتسوية {recipient['name']} — {reason_text}", label_in=f"عكس تسوية ذهب من حساب {source['name']} — {reason_text}",
                                 now=now, user_id=actor.user_id)
            marker = self._write_marker(connection, repository, trip_id, new_id, MARKER_REVERSAL, now.date(), key, digest, actor, now,
                                        f"عكس {transfer_code(original_id)} ({code}): {reason_text}")
            repository.audit(actor=actor, event_type="SUPPLIER_GOLD_TRANSFER_REVERSED", entity_id=new_id, entity_type="SUPPLIER_GOLD_TRANSFER", reference=code,
                             description=f"عكس {transfer_code(original_id)}",
                             metadata={"reverses": original_id, "reason": reason_text, "transferred": str(from_weight), "credited": str(to_weight), "custody_log": marker}, now=now)
            if not self._session.is_current(session_actor):
                raise PermissionError("انتهت جلسة الدخول.")
            return {"id": str(new_id), "code": code, "reverses": str(original_id), "custody_log": marker, "duplicate": False}
