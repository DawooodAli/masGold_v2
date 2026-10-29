"""مراجعات المدير: counts for everyone, details for reviewers.

Review sources: unidentified sales (inventory_reconciliation_cases), making /
pricing reviews, approved cash settlements, old-gold discount changes and
returned-item inspections (management_reviews). Every read runs in a
transaction that is always rolled back.

Returned-item inspection (021) is the only review resolved here, by a manager
(MANAGEMENT_REVIEW, re-checked in the transaction), with one of two outcomes:
  RETURN_TO_STOCK      RETURNED_PENDING → IN_STOCK: the same inventory item and
                       the same stored barcode (nothing new is created).
  CONVERT_TO_OLD_GOLD  RETURNED_PENDING → CONVERTED_TO_OLD_GOLD (terminal, never
                       sellable; the barcode stays as history) plus a COUNTED
                       RETURNED_ITEM_TO_OLD_GOLD old-gold IN of the exact karat
                       and weight.
One transaction; lock order: actor → review + lifecycle line → inventory item
(→ the karat's old-gold ledger). A resolved review is refused authoritatively.

An Exchange piece finalized after 032 is already IN Scrap (CONVERTED_TO_OLD_GOLD, its exact
weight credited and HELD in old_gold_holds). The same two decisions then mean:
  RETURN_TO_STOCK      إرجاع من الكسر إلى المخزون: the held weight leaves Scrap
                       (EXCHANGE_RETURN_TO_STOCK_OUT) and the SAME item / barcode is IN_STOCK again.
  CONVERT_TO_OLD_GOLD  تثبيت في الكسر: no movement at all — the hold becomes COMMITTED and the
                       weight ordinary available Scrap (never credited twice).
Lock order: actor → review → item → the karat's Scrap ledger → the hold (consumers lock the
ledger before the holds too). A hold that is no longer HELD refuses a second decision.
"""
import json
from decimal import Decimal

from repositories.invoice_lifecycle_repository import InvoiceLifecycleRepository
from repositories.management_review_repository import INSPECTION, ManagementReviewRepository
from services.authorization_service import AuthorizationService
from services.invoice_service import invoice_transaction


REVIEW_PERMISSIONS = ("MANAGEMENT_REVIEW", "INVENTORY_RECONCILE")
RESOLVE_PERMISSION = "MANAGEMENT_REVIEW"
RETURN_TO_STOCK, CONVERT_TO_OLD_GOLD = "RETURN_TO_STOCK", "CONVERT_TO_OLD_GOLD"
CONVERTED_STATUS = "CONVERTED_TO_OLD_GOLD"
_OUTCOMES = {
    RETURN_TO_STOCK: ("IN_STOCK", "RETURNED_ITEM_RESTOCKED", "إعادة قطعة مرتجعة للمخزون"),
    CONVERT_TO_OLD_GOLD: (CONVERTED_STATUS, "RETURNED_ITEM_TO_OLD_GOLD", "تحويل قطعة مرتجعة إلى الكسر"),
}


_HELD_TEXT = {
    RETURN_TO_STOCK: (None, None, "إرجاع قطعة مرتجعة من الكسر إلى المخزون"),
    CONVERT_TO_OLD_GOLD: (None, None, "تثبيت قطعة مرتجعة في الكسر"),
}


SCRAP_STATE_TEXT = {"HELD": "معلّقة لقرار المدير", "RETURNED_TO_STOCK": "أعيدت من الكسر إلى المخزون",
                   "COMMITTED": "مثبّتة في الكسر", "REVERSED": "عُكس الاستبدال"}


def scrap_destination(karat):
    """Where a held Exchange piece physically is: the karat's Scrap balance (the top-bar الكسر)."""
    return f"رصيد الكسر — عيار {karat}"


def with_scrap_story(row):
    """The returned piece's authoritative history (032 rows only): the exchange, its entry into Scrap and
    the manager decision, each from stored timestamps / actors — never composed in QML."""
    if not row or not row.get("hold_id"):
        return row
    karat, weight = row.get("karat_value"), row.get("hold_weight_grams")
    destination = scrap_destination(karat)
    from repositories.invoice_lifecycle_repository import exchange_code
    events = []
    if row.get("invoice_finalized_at"):
        events.append({"code": "SALE", "title": "بيع بالفاتورة", "detail": f"فاتورة {row.get('invoice_number') or '—'}",
                       "by": "", "at": row.get("invoice_finalized_at")})
    if row.get("source") == "RETURN":
        if row.get("replacement_exchange_id"):
            events.append({"code": "EXCHANGE", "title": "حصل عليها العميل بالاستبدال",
                           "detail": exchange_code(row["replacement_exchange_id"]), "by": "", "at": None})
        events.append({"code": "RETURN", "title": "مرتجع",
                       "detail": f"{row.get('source_code') or ''} — فاتورة {row.get('invoice_number') or '—'}",
                       "by": row.get("exchanged_by_name") or "", "at": row.get("exchanged_at")})
    else:
        events.append({"code": "EXCHANGE", "title": "عملية استبدال",
                       "detail": f"{row.get('exchange_code') or ''} — فاتورة {row.get('invoice_number') or '—'}",
                       "by": row.get("exchanged_by_name") or "", "at": row.get("exchanged_at")})
    events.append({"code": "SCRAP_IN", "title": "تم تحويل القطعة إلى الكسر",
                   "detail": f"عيار {karat} — {weight} جرام — {destination}", "by": row.get("exchanged_by_name") or "",
                   "at": row.get("scrap_entered_at")})
    if row.get("hold_status") == "RETURNED_TO_STOCK":
        events.append({"code": "RETURN_TO_STOCK", "title": "تمت إعادة القطعة من الكسر إلى المخزون",
                       "detail": "", "by": row.get("resolved_by_name") or "", "at": row.get("resolved_at")})
    elif row.get("hold_status") == "COMMITTED":
        events.append({"code": "COMMITTED", "title": "تم تثبيت القطعة داخل الكسر", "detail": "",
                       "by": row.get("resolved_by_name") or "", "at": row.get("resolved_at")})
    return dict(row, scrap_destination=destination, scrap_state_text=SCRAP_STATE_TEXT.get(row.get("hold_status"), ""),
                timeline=events)


class InspectionError(ValueError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


class _Discard(Exception):
    pass


class ManagementReviewService:
    def __init__(self, session, *, transaction_factory=invoice_transaction,
                 repository=ManagementReviewRepository, lifecycle_repository=InvoiceLifecycleRepository,
                 authorization=None):
        self._session = session
        self._transaction = transaction_factory
        self._repository = repository
        self._lifecycle = lifecycle_repository
        self._authorization = authorization or AuthorizationService(session)

    def _actor(self, expected_actor):
        actor = self._session.require_authenticated()
        if expected_actor is not None and not self._session.is_current(expected_actor):
            raise PermissionError("انتهت جلسة الدخول.")
        return actor

    def _read(self, work):
        try:
            with self._transaction() as connection:
                raise _Discard(work(self._repository(connection)))
        except _Discard as done:
            return done.args[0]

    def can_review(self, actor):
        return any(code in actor.permissions for code in REVIEW_PERMISSIONS)

    def counts(self, *, _expected_actor=None):
        """The sidebar indicator: the pending review workload, for reviewers only.

        A user with neither review permission (per the database, non-locking) gets an empty result —
        nothing is queried and no count is exposed; the sidebar entry simply shows no number."""
        actor = self._actor(_expected_actor)
        try:
            with self._transaction() as connection:
                verified = self._authorization.require_permission_read(connection, actor, "APP_LOGIN")
                if not set(REVIEW_PERMISSIONS) & set(verified.permissions):
                    raise _Discard({})
                raise _Discard(self._repository(connection).open_counts())
        except _Discard as done:
            return done.args[0]

    def open_reviews(self, *, _expected_actor=None):
        actor = self._actor(_expected_actor)
        if not self.can_review(actor):
            raise PermissionError("فتح مراجعات المدير يحتاج صلاحية المراجعة.")

        def read(repository):
            return {"INVENTORY_MATCH": repository.open_inventory_cases(),
                    "MAKING_PRICE": repository.open_reviews("MAKING_PRICE"),
                    "CASH_SETTLEMENT": repository.open_reviews("CASH_SETTLEMENT"),
                    "OLD_GOLD_DISCOUNT": repository.open_reviews("OLD_GOLD_DISCOUNT"),
                    INSPECTION: [with_scrap_story(row) for row in repository.open_inspections()],
                    "RETURN_APPROVAL": repository.open_return_approvals()}
        try:
            with self._transaction() as connection:
                # Either review permission opens the list: re-checked against the database, without a lock.
                self._authorization.require_any_permission_read(connection, actor, REVIEW_PERMISSIONS)
                raise _Discard(read(self._repository(connection)))
        except _Discard as done:
            return done.args[0]

    # ------------------------------------------------------------ Return approval (after 72 hours)
    def _return_service(self):
        if getattr(self, "_returns_service", None) is None:
            from services.return_service import ReturnService
            self._returns_service = ReturnService(self._session, authorization=self._authorization,
                                                  transaction_factory=self._transaction)
        return self._returns_service

    def approve_return(self, review_id, *, _expected_actor=None):
        return self._return_service().decide_approval(review_id, True, _expected_actor=_expected_actor)

    def reject_return(self, review_id, *, _expected_actor=None):
        return self._return_service().decide_approval(review_id, False, _expected_actor=_expected_actor)

    # ------------------------------------------------------------ مراجعة مخزون: an unidentified (F5) sale is settled (089)
    def match_case_to_stock(self, case_id, barcode, *, _expected_actor=None):
        """The real piece was found in stock: link it by its barcode (IN_STOCK -> SOLD on the invoice)."""
        return self._settle_case(case_id, "MATCH_STOCK", barcode, None, _expected_actor)

    def take_case_from_scrap(self, case_id, *, _expected_actor=None):
        """The piece came from the Scrap: the case's karat and weight leave the Scrap."""
        return self._settle_case(case_id, "FROM_SCRAP", None, None, _expected_actor)

    def close_case_no_source(self, case_id, reason, *, _expected_actor=None):
        """The piece exists nowhere: nothing moves; the written reason is the record."""
        return self._settle_case(case_id, "NO_SOURCE", None, reason, _expected_actor)

    def _settle_case(self, case_id, resolution, barcode, reason, expected_actor):
        session_actor = self._actor(expected_actor)
        if not self.can_resolve(session_actor):
            raise PermissionError("حسم مراجعة المخزون للمدير فقط.")
        if not str(case_id or "").strip().isdigit():
            raise InspectionError("CASE_NOT_FOUND", "حالة المراجعة غير موجودة.")
        barcode = str(barcode or "").strip()
        reason = str(reason or "").strip()
        if resolution == "MATCH_STOCK" and not barcode:
            raise InspectionError("BARCODE_REQUIRED", "أدخل باركود القطعة.")
        if resolution == "NO_SOURCE" and not reason:
            raise InspectionError("REASON_REQUIRED", "اكتب سبب عدم وجود مصدر للقطعة.")
        if len(reason) > 500:
            raise InspectionError("REASON_TOO_LONG", "السبب أطول من الحد المسموح (500 حرف).")
        with self._transaction() as connection:
            actor = self._authorization.require_permission(connection, session_actor, RESOLVE_PERMISSION)
            repository = self._repository(connection)
            case = repository.lock_unidentified_case(case_id)
            if case is None:
                raise InspectionError("CASE_NOT_FOUND", "حالة المراجعة غير موجودة.")
            if case["case_status"] != "OPEN":
                raise InspectionError("CASE_ALREADY_SETTLED", "تم حسم هذه الحالة من قبل.")
            if case["invoice_id"] is None:
                raise InspectionError("CASE_UNSUPPORTED", "هذه حالة بديل يدوي في استبدال، ولا تُحسم من هنا بعد.")
            weight = Decimal(str(case["weight_grams"]))
            now, item_id, movement_id, note = repository.db_now(), None, None, reason or None
            if resolution == "MATCH_STOCK":
                item = repository.lock_stock_item_by_barcode(barcode)
                if item is None:
                    raise InspectionError("PIECE_NOT_FOUND", "لا توجد قطعة بهذا الباركود.")
                if item["inventory_status"] != "IN_STOCK":
                    raise InspectionError("PIECE_NOT_IN_STOCK", "هذه القطعة ليست في المخزون (مباعة أو مستبعدة).")
                if int(item["karat_id"]) != int(case["karat_id"]):
                    raise InspectionError("KARAT_MISMATCH",
                                          f"عيار القطعة {item['karat_value']} يختلف عن عيار البيع {case['karat_value']}.")
                item_id = item["inventory_item_id"]
                repository.set_item_status(inventory_item_id=item_id, from_status="IN_STOCK", to_status="SOLD",
                                           updated_by_user_id=actor.user_id)
                movement_id = repository.add_inventory_movement(
                    item=item, movement_code="SOLD", from_status="IN_STOCK", to_status="SOLD", review_id=case["case_id"],
                    reference_code=case["invoice_number"], reason=f"مراجعة مخزون — فاتورة {case['invoice_number']}",
                    performed_by_user_id=actor.user_id, performed_by_employee_id=actor.employee_id)
                note = f"باركود {barcode}" + (f" — فرق الوزن {Decimal(str(item['weight_grams'])) - weight:+.3f} جم"
                                              if Decimal(str(item["weight_grams"])) != weight else "")
            elif resolution == "FROM_SCRAP":
                available = repository.lock_old_gold_balance(case["karat_id"])
                if available < weight:
                    raise InspectionError("SCRAP_SHORTAGE",
                                          f"رصيد الكسر عيار {case['karat_value']} ({available:.3f} جم) أقل من وزن البيع ({weight:.3f} جم).")
                movement_id = repository.add_case_scrap_out(
                    karat_id=case["karat_id"], weight_grams=weight, invoice_id=case["invoice_id"],
                    invoice_number=case["invoice_number"], case_id=case["case_id"], created_by_user_id=actor.user_id,
                    employee_id=actor.employee_id)
            status = "MATCHED" if resolution == "MATCH_STOCK" else "CLOSED"
            if not repository.settle_case(case_id=case["case_id"], status=status, resolution_code=resolution,
                                          matched_item_id=item_id, notes=note, user_id=actor.user_id, at=now):
                raise InspectionError("CASE_ALREADY_SETTLED", "تم حسم هذه الحالة من قبل.")
            cursor = connection.cursor()
            try:
                cursor.execute(
                    "INSERT INTO audit_log (user_id, event_type, entity_type, entity_id, entity_reference, "
                    "description, metadata_json, occurred_at) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                    (actor.user_id, f"INVENTORY_CASE_{resolution}", "INVENTORY_RECONCILIATION_CASE", int(case["case_id"]),
                     case["invoice_number"], "حسم مراجعة مخزون — بيع يدوي",
                     json.dumps({"case_id": case["case_id"], "resolution": resolution, "invoice_id": case["invoice_id"],
                                 "karat": case["karat_value"], "weight_grams": str(weight), "inventory_item_id": item_id,
                                 "movement_id": movement_id, "notes": note}, ensure_ascii=False, default=str), now))
            finally:
                cursor.close()
            if not self._session.is_current(session_actor):
                raise PermissionError("انتهت جلسة الدخول.")
        return {"case_id": case["case_id"], "resolution": resolution, "status": status}

    # ------------------------------------------------------------ «تمت المراجعة»: pricing / settlement / discount reviews
    def mark_reviewed(self, review_id, *, _expected_actor=None):
        """The manager acknowledges a pricing, cash-settlement or discount review: it leaves the open list.
        The invoice and its figures are never touched; the review row keeps who closed it and when."""
        session_actor = self._actor(_expected_actor)
        if not self.can_resolve(session_actor):
            raise PermissionError("تأكيد المراجعة للمدير فقط.")
        if not str(review_id or "").strip().isdigit():
            raise InspectionError("REVIEW_NOT_FOUND", "المراجعة غير موجودة.")
        with self._transaction() as connection:
            actor = self._authorization.require_permission(connection, session_actor, RESOLVE_PERMISSION)
            repository = self._repository(connection)
            review = repository.lock_simple_review(review_id)
            if review is None:
                raise InspectionError("REVIEW_NOT_FOUND", "المراجعة غير موجودة.")
            if review["review_status"] != "OPEN":
                raise InspectionError("REVIEW_ALREADY_RESOLVED", "تمت مراجعة هذا البند من قبل.")
            now = repository.db_now()
            if not repository.mark_reviewed(review_id=review["review_id"], resolved_by_user_id=actor.user_id, resolved_at=now):
                raise InspectionError("REVIEW_ALREADY_RESOLVED", "تمت مراجعة هذا البند من قبل.")
            cursor = connection.cursor()
            try:
                cursor.execute(
                    "INSERT INTO audit_log (user_id, event_type, entity_type, entity_id, entity_reference, "
                    "description, metadata_json, occurred_at) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                    (actor.user_id, "MANAGEMENT_REVIEW_DONE", "MANAGEMENT_REVIEW", int(review["review_id"]), None,
                     "تمت مراجعة البند", json.dumps({"review_id": review["review_id"], "review_type": review["review_type"],
                                                     "invoice_id": review["invoice_id"]}, ensure_ascii=False, default=str), now))
            finally:
                cursor.close()
            if not self._session.is_current(session_actor):
                raise PermissionError("انتهت جلسة الدخول.")
        return {"review_id": review["review_id"], "review_type": review["review_type"]}

    # ------------------------------------------------------------ returned-item inspection

    def can_resolve(self, actor=None):
        actor = actor or self._session.current_actor()
        return bool(actor and RESOLVE_PERMISSION in actor.permissions)

    def return_to_stock(self, review_id, *, notes=None, _expected_actor=None):
        return self._resolve(review_id, RETURN_TO_STOCK, notes, _expected_actor)

    def convert_to_old_gold(self, review_id, *, notes=None, _expected_actor=None):
        return self._resolve(review_id, CONVERT_TO_OLD_GOLD, notes, _expected_actor)

    def _resolve(self, review_id, outcome, notes, expected_actor):
        session_actor = self._actor(expected_actor)
        if not self.can_resolve(session_actor):
            raise PermissionError("قرار فحص القطعة المرتجعة للمدير فقط.")
        if not str(review_id or "").strip().isdigit():
            raise InspectionError("REVIEW_NOT_FOUND", "المراجعة غير موجودة.")
        to_status, movement_code, reason = _OUTCOMES[outcome]
        notes = (notes or "").strip() or None
        with self._transaction() as connection:
            actor = self._authorization.require_permission(connection, session_actor, RESOLVE_PERMISSION)
            repository = self._repository(connection)
            review = repository.lock_inspection(review_id)
            if review is None:
                raise InspectionError("REVIEW_NOT_FOUND", "المراجعة غير موجودة.")
            if review["review_status"] != "OPEN":
                raise InspectionError("REVIEW_ALREADY_RESOLVED", "تم اتخاذ قرار في هذه القطعة من قبل.")
            item = repository.lock_item(review["inventory_item_id"])
            if review.get("hold_id"):                               # an Exchange (032) or Return (033) piece
                result = self._resolve_held(repository, connection, actor, session_actor, review, item, outcome,
                                            notes)
                if not self._session.is_current(session_actor):
                    raise PermissionError("انتهت جلسة الدخول.")
                return result
            if item is None or item["inventory_status"] != "RETURNED_PENDING":
                raise InspectionError("ITEM_NOT_PENDING", "القطعة ليست بانتظار الفحص.")
            now, code = repository.db_now(), review["exchange_code"]
            source = review.get("source_label") or f"استبدال {code}"          # استبدال EX… / مرتجع RT…
            repository.set_item_status(inventory_item_id=item["inventory_item_id"], from_status="RETURNED_PENDING",
                                       to_status=to_status, updated_by_user_id=actor.user_id)
            movement_id = repository.add_inventory_movement(
                item=item, movement_code=movement_code, from_status="RETURNED_PENDING", to_status=to_status,
                review_id=review["review_id"], reference_code=code, reason=f"{reason} — {source}",
                performed_by_user_id=actor.user_id, performed_by_employee_id=actor.employee_id)
            old_gold_id = None
            if outcome == CONVERT_TO_OLD_GOLD:
                # The exact karat and weight of the piece; the karat's COUNTED ledger is locked first.
                repository.lock_old_gold_balance(item["karat_id"])
                old_gold_id = repository.add_old_gold_in(
                    karat_id=item["karat_id"], weight_grams=item["weight_grams"],
                    inventory_item_id=item["inventory_item_id"], review_id=review["review_id"], reference_code=code,
                    notes=f"قطعة مرتجعة — الباركود المسجل {review['stored_barcode'] or '—'}",
                    customer_id=None, created_by_user_id=actor.user_id, employee_id=actor.employee_id)
            if not repository.resolve_inspection(review_id=review["review_id"], resolution_code=outcome,
                                                 resolved_by_user_id=actor.user_id, resolved_at=now, notes=notes):
                raise InspectionError("REVIEW_ALREADY_RESOLVED", "تم اتخاذ قرار في هذه القطعة من قبل.")
            details = {"review_id": review["review_id"], "resolution": outcome,
                       "lifecycle_line_id": review["lifecycle_line_id"], "exchange_code": code,
                       "return_line_id": review.get("return_line_id"), "source_label": source,
                       "invoice_id": review["invoice_id"], "inventory_item_id": item["inventory_item_id"],
                       "stored_barcode": review["stored_barcode"], "karat": review["karat_value"],
                       "weight_grams": str(item["weight_grams"]), "inventory_movement_id": movement_id,
                       "old_gold_movement_id": old_gold_id}
            self._audit(connection, actor, review["review_id"], code, outcome, details, now)
            if not self._session.is_current(session_actor):
                raise PermissionError("انتهت جلسة الدخول.")
        return {"review_id": review["review_id"], "resolution": outcome, "inventory_status": to_status,
                "inventory_item_id": item["inventory_item_id"], "stored_barcode": review["stored_barcode"],
                "old_gold_movement_id": old_gold_id, "resolved_at": now.isoformat(sep=" ")}

    def _resolve_held(self, repository, connection, actor, session_actor, review, item, outcome, notes):
        """An Exchange piece already in Scrap (032): return the same piece from Scrap, or commit it there."""
        if item is None or item["inventory_status"] != CONVERTED_STATUS:
            raise InspectionError("ITEM_NOT_IN_SCRAP", "القطعة ليست في الكسر بانتظار قرار المدير.")
        physical = repository.lock_old_gold_balance(item["karat_id"])         # ledger before the hold
        hold = repository.lock_hold(review["review_id"])
        if hold is None or hold["hold_status"] != "HELD":
            raise InspectionError("REVIEW_ALREADY_RESOLVED", "تم اتخاذ قرار في هذه القطعة من قبل.")
        weight = hold["weight_grams"]
        now, code = repository.db_now(), review["exchange_code"]
        source = review.get("source_label") or f"استبدال {code}"
        out_id = movement_id = None
        to_status = CONVERTED_STATUS
        if outcome == RETURN_TO_STOCK:
            if physical < weight:
                raise InspectionError("SCRAP_QUANTITY_MISSING",
                                      "وزن القطعة غير موجود في رصيد الكسر — لا يمكن إعادتها إلى المخزون.")
            out_id = repository.add_old_gold_out_to_stock(
                karat_id=hold["karat_id"], weight_grams=weight, inventory_item_id=item["inventory_item_id"],
                review_id=review["review_id"], reference_code=code,
                notes=f"إرجاع من الكسر إلى المخزون — الباركود {review['stored_barcode'] or 'بدون باركود'}",
                created_by_user_id=actor.user_id, employee_id=actor.employee_id,
                movement_code="RETURN_TO_STOCK_OUT" if hold.get("return_line_id") else "EXCHANGE_RETURN_TO_STOCK_OUT")
            to_status = "IN_STOCK"
            repository.set_item_status(inventory_item_id=item["inventory_item_id"], from_status=CONVERTED_STATUS,
                                       to_status=to_status, updated_by_user_id=actor.user_id)
            movement_id = repository.add_inventory_movement(
                item=item, movement_code="RETURNED_ITEM_RESTOCKED", from_status=CONVERTED_STATUS,
                to_status=to_status, review_id=review["review_id"], reference_code=code,
                reason=f"إرجاع من الكسر إلى المخزون — {source}",
                performed_by_user_id=actor.user_id, performed_by_employee_id=actor.employee_id)
        if not repository.close_hold(hold_id=hold["hold_id"],
                                     hold_status="RETURNED_TO_STOCK" if outcome == RETURN_TO_STOCK else "COMMITTED",
                                     out_movement_id=out_id, resolved_by_user_id=actor.user_id, resolved_at=now):
            raise InspectionError("REVIEW_ALREADY_RESOLVED", "تم اتخاذ قرار في هذه القطعة من قبل.")
        if not repository.resolve_inspection(review_id=review["review_id"], resolution_code=outcome,
                                             resolved_by_user_id=actor.user_id, resolved_at=now, notes=notes):
            raise InspectionError("REVIEW_ALREADY_RESOLVED", "تم اتخاذ قرار في هذه القطعة من قبل.")
        details = {"review_id": review["review_id"], "resolution": outcome, "hold_id": hold["hold_id"],
                   "hold_status": "RETURNED_TO_STOCK" if outcome == RETURN_TO_STOCK else "COMMITTED",
                   "lifecycle_line_id": review["lifecycle_line_id"], "exchange_code": code, "source_label": source,
                   "invoice_id": review["invoice_id"], "inventory_item_id": item["inventory_item_id"],
                   "stored_barcode": review["stored_barcode"], "karat": review["karat_value"],
                   "weight_grams": str(weight), "scrap_in_movement_id": hold["in_movement_id"],
                   "scrap_out_movement_id": out_id, "inventory_movement_id": movement_id}
        self._audit(connection, actor, review["review_id"], code, outcome, details, now, held=True)
        return {"review_id": review["review_id"], "resolution": outcome, "inventory_status": to_status,
                "inventory_item_id": item["inventory_item_id"], "stored_barcode": review["stored_barcode"],
                "old_gold_movement_id": out_id, "hold_status": details["hold_status"],
                "resolved_at": now.isoformat(sep=" ")}

    def backfill_returned_inspections(self, *, _expected_actor=None):
        """One inspection review for each RETURNED_PENDING exchange piece that has none (idempotent:
        existing reviews are skipped and the UNIQUE lifecycle line refuses a duplicate)."""
        from services.exchange_service import inspection_review
        session_actor = self._actor(_expected_actor)
        created = []
        with self._transaction() as connection:
            actor = self._authorization.require_permission(connection, session_actor, RESOLVE_PERMISSION)
            lifecycle = self._lifecycle(connection)
            for line in self._repository(connection).returned_lines_without_inspection():
                created.append(lifecycle.open_returned_item_inspection(
                    **inspection_review(lifecycle_line_id=line["lifecycle_line_id"], lifecycle_id=line["lifecycle_id"],
                                        invoice_id=line["invoice_id"],
                                        source_invoice_line_id=line["source_invoice_line_id"],
                                        inventory_item_id=line["inventory_item_id"],
                                        barcode=line.get("stored_barcode") or line.get("barcode_snapshot"),
                                        item_name=line.get("item_name_snapshot"), karat=line["karat_value"],
                                        weight=line["weight_grams"]),
                    created_by_user_id=actor.user_id))
        return created

    @staticmethod
    def _audit(connection, actor, review_id, code, outcome, details, now, held=False):
        cursor = connection.cursor()
        try:
            cursor.execute(
                "INSERT INTO audit_log (user_id, event_type, entity_type, entity_id, entity_reference, "
                "description, metadata_json, occurred_at) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                (actor.user_id, f"RETURNED_ITEM_{outcome}", "MANAGEMENT_REVIEW", int(review_id), code,
                 (_HELD_TEXT if held else _OUTCOMES)[outcome][2] + " — "
                 + (details.get("source_label") or f"استبدال {code}"),
                 json.dumps(details, ensure_ascii=False, default=str),
                 now))
        finally:
            cursor.close()
