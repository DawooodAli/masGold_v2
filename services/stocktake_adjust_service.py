"""الجرد: اعتماد الجرد وشطب المتبقي. After a barcode stocktake the owner / manager approves it: every piece of the counted scope that was NOT scanned leaves the stock (status REMOVED, movement
STOCKTAKE_WRITE_OFF) with a mandatory reason, and the reconciliation (expected / found / written off, per karat) is stored with the approval. Nothing is deleted.

The screen sends the two id lists of its session (scanned, remaining) but decides nothing: inside ONE transaction the service locks every piece IN_STOCK of the scope and requires that scanned + remaining
are EXACTLY that set (a piece sold or added meanwhile refuses the approval: count again), so the stored figures are derived from the database, never from the client. Right STOCKTAKE_ADJUST
(OWNER, MANAGER), rechecked in the transaction (the actor is the AppSession user). One request key and hash; an exact replay returns the original."""
import hashlib
import json
from decimal import Decimal

from repositories.stocktake_adjust_repository import StocktakeAdjustRepository
from services.authorization_service import AuthorizationService
from services.invoice_service import invoice_transaction

PERMISSION = "STOCKTAKE_ADJUST"
SCRAP_PERMISSION = "OLD_GOLD_MANAGE"        # a piece coming back from the Scrap also moves Scrap grams
D = Decimal
REASONS = {"SOLD_OUTSIDE": "اتباعت خارج السيستم", "MISSING": "مفقودة", "DATA_ERROR": "خطأ في تسجيل المخزون", "OTHER": "سبب آخر"}
ZERO = Decimal("0.000")


class StocktakeError(ValueError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def _ids(values):
    out = []
    for v in values or []:
        text = str(v).strip()
        if not text.isdigit() or int(text) <= 0:
            raise StocktakeError("IDS_INVALID", "بيانات الجرد غير صالحة، أعد الجرد.")
        out.append(int(text))
    if len(set(out)) != len(out):
        raise StocktakeError("IDS_INVALID", "بيانات الجرد غير صالحة، أعد الجرد.")
    return out


def _optional_id(value):
    return int(value) if str(value or "").strip() not in ("", "0") else None


class StocktakeAdjustService:
    def __init__(self, session, *, transaction_factory=invoice_transaction, repository=StocktakeAdjustRepository, authorization=None):
        self._session = session
        self._transaction = transaction_factory
        self._repository = repository
        self._authorization = authorization or AuthorizationService(session)

    def can_adjust(self):
        actor = self._session.current_actor()
        return actor is not None and PERMISSION in actor.permissions

    def scrap_karats(self, *, _expected_actor=None):
        """The karats the grams of a piece coming back from the Scrap can be taken from, with the AVAILABLE grams of each (held manager-review Scrap excluded)."""
        session_actor = self._session.require_authenticated()
        if _expected_actor is not None and not self._session.is_current(_expected_actor):
            raise PermissionError("انتهت جلسة الدخول.")
        if PERMISSION not in session_actor.permissions:
            raise PermissionError("ليس لديك صلاحية إعادة القطع للمخزون.")
        with self._transaction() as connection:
            self._authorization.require_permission_read(connection, session_actor, PERMISSION)
            rows = self._repository(connection).scrap_karats()
        return [{"id": str(r["karat_id"]), "karat": int(r["karat_value"]), "available": str(D(str(r["physical_grams"])) - D(str(r["held_grams"]))),
                 "text": f"عيار {r['karat_value']} — المتاح {D(str(r['physical_grams'])) - D(str(r['held_grams'])):,.3f} جرام"} for r in rows]

    def restore_piece(self, piece_id, reason, scrap_karat_id=None, *, _expected_actor=None):
        """قطعة اتشطبت بالجرد ورجعت ظهرت، أو قطعة مباعة (فاتورة قديمة) رجعت للمحل: returns ONE piece an approved stocktake removed (REMOVED) - or a piece SOLD on an old-program invoice that came back, with the SAME barcode - to the stock, with a mandatory reason. The stored stocktake document is never rewritten; the
        piece gets a RESTORED movement and an audit row. A piece that was never removed by a stocktake, or is already back, is refused. Right STOCKTAKE_ADJUST, rechecked in the transaction."""
        session_actor = self._session.require_authenticated()
        if _expected_actor is not None and not self._session.is_current(_expected_actor):
            raise PermissionError("انتهت جلسة الدخول.")
        text = str(reason or "").strip()
        if not text:
            raise StocktakeError("REASON_REQUIRED", "اكتب سبب إعادة القطعة للمخزون.")
        if len(text) > 300:
            raise StocktakeError("NOTE_TOO_LONG", "السبب 300 حرف على الأكثر.")
        raw = str(piece_id or "").strip()
        if not raw.isdigit() or int(raw) <= 0:
            raise StocktakeError("PIECE_NOT_FOUND", "القطعة غير موجودة.")
        with self._transaction() as connection:
            actor = self._authorization.require_permission_for_write(connection, session_actor, PERMISSION)
            repository = self._repository(connection)
            piece = repository.lock_removed_piece(int(raw))
            if piece is None:
                raise StocktakeError("PIECE_NOT_FOUND", "القطعة غير موجودة.")
            sold = piece["inventory_status"] == "SOLD"
            scrap_movement = None
            if sold:
                # Owner 2026-11-08: a piece that left the shop (sold, typically on an old-program invoice) and comes back is put in stock again as the SAME piece with the SAME barcode.
                # Owner 2026-11-08: also for a piece sold on a CURRENT invoice - the customer's money was settled by the Scrap purchase invoice, so NO invoice and NO customer account is touched here.
                pass
            elif piece["inventory_status"] != "REMOVED" or int(piece["write_offs"] or 0) < 1:
                raise StocktakeError("NOT_REMOVED_BY_STOCKTAKE", "هذه القطعة لم تُشطب بجرد ولا مباعة بفاتورة قديمة، أو رجعت للمخزون بالفعل.")
            movement_type = repository.movement_type_id("RETURNED_ITEM_RESTOCKED" if sold else "RESTORED")
            if movement_type is None:
                raise StocktakeError("SCHEMA_OUTDATED", "نوع حركة الإرجاع غير موجود.")
            from_status = "SOLD" if sold else "REMOVED"
            if sold:
                # A piece the shop bought back goes through the Scrap first: its exact grams leave the Scrap of the karat the owner CHOOSES when the same piece goes back into the stock.
                if str(scrap_karat_id or "").strip() == "":
                    raise StocktakeError("SCRAP_KARAT_REQUIRED", "اختار كسر عيار كام هتخصم منه جرامات القطعة.")
                self._authorization.require_permission_for_write(connection, session_actor, SCRAP_PERMISSION)
                try:
                    karat = repository.karat(int(str(scrap_karat_id).strip()))
                except (TypeError, ValueError):
                    karat = None
                if karat is None or not karat["is_active"]:
                    raise StocktakeError("KARAT_INVALID", "عيار الكسر غير موجود أو غير نشط.")
                physical, held = repository.lock_scrap_ledger(int(karat["id"]))
                available = physical - held
                weight = D(str(piece["weight_grams"]))
                if weight > available:
                    raise StocktakeError("SCRAP_SHORTAGE", f"كسر عيار {karat['karat_value']} المتاح {available:,.3f} جرام فقط، لا يكفي لوزن القطعة {weight:,.3f} جرام.")
            if not repository.restore_piece(int(raw), actor.user_id, from_status):
                raise StocktakeError("NOT_REMOVED_BY_STOCKTAKE", "هذه القطعة لم تُشطب بجرد ولا مباعة بفاتورة قديمة، أو رجعت للمخزون بالفعل.")
            if sold:
                scrap_movement = repository.insert_scrap_out_for_restock(item=piece, karat_id=int(karat["id"]), user_id=actor.user_id,
                                                                         notes=f"رجوع القطعة #{int(raw)} للمخزون بنفس الباركود {piece.get('barcode_value') or '—'} — {text}")
            repository.insert_restore_movement(item=piece, movement_type_id=movement_type, user_id=actor.user_id, from_status=from_status,
                                               reason=(f"رجوع قطعة مباعة (فاتورة قديمة) للمخزون بنفس الباركود — {text}" if sold else f"إرجاع قطعة مشطوبة بالجرد — {text}"),
                                               reference_type="PIECE_RESTOCK" if sold else "STOCKTAKE_RESTORE")
            repository.audit_restore(user_id=actor.user_id, item_id=int(raw),
                                     description=(f"رجوع القطعة المباعة #{int(raw)} للمخزون بنفس الباركود" if sold else f"إرجاع القطعة #{int(raw)} للمخزون بعد شطبها بجرد"),
                                     metadata={"reason": text, "barcode": piece.get("barcode_value"), "weight_grams": str(piece["weight_grams"]), "from_status": from_status,
                                               "scrap_karat_id": None if not sold else int(karat["id"]), "old_gold_movement_id": scrap_movement})
            if not self._session.is_current(session_actor):
                raise PermissionError("انتهت جلسة الدخول.")
        return {"piece_id": raw, "restored": True, "scrap_movement_id": scrap_movement}

    def approve(self, item_type_id, karat_id, scanned_ids, remaining_ids, reason_code, note, confirmed, request_key, *, factory_id=None, _expected_actor=None):
        """The whole counted scope: scanned + remaining must be exactly the pieces IN_STOCK of the scope."""
        return self._post("SCOPE", item_type_id, karat_id, scanned_ids, remaining_ids, reason_code, note, confirmed, request_key, _expected_actor, factory_id)

    def write_off_pieces(self, piece_ids, reason_code, note, confirmed, request_key, *, _expected_actor=None):
        """Selected pieces only (a row deleted from المتبقي): each must still be IN_STOCK; no scope is claimed, so the stored reconciliation has found = 0."""
        return self._post("PIECES", None, None, [], piece_ids, reason_code, note, confirmed, request_key, _expected_actor)

    def _post(self, mode, item_type_id, karat_id, scanned_ids, remaining_ids, reason_code, note, confirmed, request_key, _expected_actor, factory_id=None):
        session_actor = self._session.require_authenticated()
        if _expected_actor is not None and not self._session.is_current(_expected_actor):
            raise PermissionError("انتهت جلسة الدخول.")
        reason = str(reason_code or "").strip().upper()
        if reason not in REASONS:
            raise StocktakeError("REASON_REQUIRED", "اختر سبب الشطب.")
        note = str(note or "").strip() or None
        if note is not None and len(note) > 300:
            raise StocktakeError("NOTE_TOO_LONG", "الملاحظة 300 حرف على الأكثر.")
        if reason == "OTHER" and note is None:
            raise StocktakeError("NOTE_REQUIRED", "اكتب ملاحظة توضّح السبب.")
        if confirmed is not True:
            raise StocktakeError("CONFIRM_REQUIRED", "أكّد أن القطع المتبقية غير موجودة فعلاً.")
        key = str(request_key or "").strip()
        if not 8 <= len(key) <= 64:
            raise StocktakeError("REQUEST_KEY_INVALID", "مفتاح الطلب غير صالح.")
        type_id, karat, factory = _optional_id(item_type_id), _optional_id(karat_id), _optional_id(factory_id)
        scanned, remaining = _ids(scanned_ids), _ids(remaining_ids)
        if not remaining:
            raise StocktakeError("NOTHING_TO_WRITE_OFF", "لا توجد قطع متبقية لشطبها.")
        if mode == "SCOPE" and not scanned:
            raise StocktakeError("NOTHING_FOUND", "لم يتم مسح أي قطعة: لا يمكن اعتماد جرد فارغ.")
        if set(scanned) & set(remaining):
            raise StocktakeError("IDS_INVALID", "بيانات الجرد غير صالحة، أعد الجرد.")
        payload = {"mode": mode, "type": type_id, "karat": karat, "scanned": sorted(scanned), "remaining": sorted(remaining), "reason": reason, "note": note}
        if factory is not None:
            payload["factory"] = factory                                    # the hash of a scope without a factory stays what it always was
        digest = hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()
        off_ids = set(remaining)
        with self._transaction() as connection:
            actor = self._authorization.require_permission_for_write(connection, session_actor, PERMISSION)
            repository = self._repository(connection)
            if not repository.has_schema():
                raise StocktakeError("SCHEMA_OUTDATED", "قاعدة البيانات تحتاج تحديث 073 قبل اعتماد الجرد.")
            existing = repository.adjustment_by_key(key)
            if existing is not None:
                if existing["request_hash"] != digest:
                    raise StocktakeError("REQUEST_CONFLICT", "مفتاح الطلب مستخدم لطلب مختلف.")
                return {"code": existing["code"], "id": str(existing["id"]), "written_off_count": int(existing["written_off_count"]),
                        "written_off_grams": Decimal(str(existing["written_off_grams"])), "replayed": True}
            pieces = {int(r["id"]): r for r in (repository.scope_pieces(type_id, karat, factory) if mode == "SCOPE" else repository.pieces_by_ids(sorted(off_ids)))}
            if set(pieces) != set(scanned) | off_ids:
                raise StocktakeError("STOCK_CHANGED", "المخزون تغيّر أثناء الجرد (قطعة اتباعت أو اتضافت). أعد الجرد ثم اعتمده.")
            if mode == "SCOPE" and any(not pieces[i].get("barcode_value") for i in off_ids):
                raise StocktakeError("PIECE_WITHOUT_BARCODE", "من المتبقي قطع بدون باركود لا يمكن مسحها: احذفها واحدة واحدة أو أعطها باركود، ثم اعتمد الجرد.")
            movement_type = repository.movement_type_id("STOCKTAKE_WRITE_OFF")
            if movement_type is None:
                raise StocktakeError("SCHEMA_OUTDATED", "نوع حركة الشطب غير موجود: طبّق التحديث 073.")
            summary, totals = {}, {"expected": [0, ZERO], "found": [0, ZERO], "off": [0, ZERO]}
            for item_id, row in pieces.items():
                side = "off" if item_id in off_ids else "found"
                weight = Decimal(str(row["weight_grams"])).quantize(Decimal("0.001"))
                entry = summary.setdefault(str(row["karat_value"]), {"expected": [0, "0.000"], "found": [0, "0.000"], "off": [0, "0.000"]})
                for name in ("expected", side):
                    entry[name][0] += 1
                    entry[name][1] = str(Decimal(entry[name][1]) + weight)
                    totals[name][0] += 1
                    totals[name][1] += weight
            now = repository.now()
            adjustment_id, code = repository.insert_adjustment(
                placeholder_code="PENDING-" + digest[:8], item_type_id=type_id, karat_id=karat, expected=totals["expected"], found=totals["found"], written_off=totals["off"],
                reason_code=reason, note=note, summary=summary, request_key=key, request_hash=digest, user_id=actor.user_id, confirmed_at=now)
            for item_id in remaining:
                row = pieces[item_id]
                weight = Decimal(str(row["weight_grams"])).quantize(Decimal("0.001"))
                if not repository.remove_piece(item_id, actor.user_id):
                    raise StocktakeError("STOCK_CHANGED", "المخزون تغيّر أثناء الجرد. أعد الجرد ثم اعتمده.")
                movement_id = repository.insert_movement(item_id=item_id, movement_type_id=movement_type, stock_classification=row["stock_classification"], weight=weight,
                                                         reason=f"شطب جرد {code}: {REASONS[reason]}", user_id=actor.user_id, reference_id=adjustment_id)
                repository.insert_item(adjustment_id=adjustment_id, item_id=item_id, karat_id=row["karat_id"], weight=weight, barcode=row["barcode_value"], movement_id=movement_id)
            repository.audit(user_id=actor.user_id, adjustment_id=adjustment_id, code=code, description=f"اعتماد جرد وشطب {totals['off'][0]} قطعة ({REASONS[reason]})",
                             metadata={"reason": reason, "note": note, "expected": [totals["expected"][0], str(totals["expected"][1])], "found": [totals["found"][0], str(totals["found"][1])],
                                       "written_off": [totals["off"][0], str(totals["off"][1])], "summary": summary}, occurred_at=now)
            if not self._session.is_current(session_actor):
                raise PermissionError("انتهت جلسة الدخول.")
        return {"code": code, "id": str(adjustment_id), "written_off_count": totals["off"][0], "written_off_grams": totals["off"][1], "replayed": False}
