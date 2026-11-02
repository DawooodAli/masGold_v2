"""تصحيح باركود قطعة مباعة: the barcode on a sold line of a FINALIZED invoice is wrong; the piece that really went out replaces it.

* The real piece must be IN_STOCK with the SAME karat and the SAME weight as the line (owner decision 2026-11-02), so the invoice's price, making, total, payments and customer account stay exactly as they were.
* In ONE transaction (invoice, then the line, then both pieces in ascending id order): the line names the real piece (its descriptive snapshots and barcode follow), the real piece goes IN_STOCK -> SOLD and the
  piece that was recorded by mistake goes SOLD -> IN_STOCK, each with a stock movement that names the invoice and the reason; one audit row keeps the old and the new piece.
* Refused: a voided or fully reversed invoice, an invoice that has an Exchange or a Return (they are corrected through their own screens), a line that is not an identified sold piece, a real piece that is not in stock or
  differs in karat / weight, and a piece that is not SOLD any more.
Right: TRANSACTION_CORRECT (manager / owner), rechecked in the transaction (the actor is the session user); a reason is mandatory and one explicit confirmation is required."""
from decimal import Decimal

from app.barcode_input import canonical_barcode
from repositories.invoice_piece_correction_repository import InvoicePieceCorrectionRepository
from services.authorization_service import AuthorizationService
from services.invoice_service import invoice_transaction

PERMISSION = "TRANSACTION_CORRECT"
RESTOCK_CODE = "RETURNED_ITEM_RESTOCKED"
SOLD_CODE = "SOLD"
MAX_REASON = 500


class PieceCorrectionError(ValueError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


class InvoicePieceCorrectionService:
    def __init__(self, session, *, transaction_factory=invoice_transaction, repository=InvoicePieceCorrectionRepository, authorization=None):
        self._session = session
        self._transaction = transaction_factory
        self._repository = repository
        self._authorization = authorization or AuthorizationService(session)

    def can_correct(self):
        actor = self._session.current_actor()
        return actor is not None and PERMISSION in actor.permissions

    def correct(self, number, line_id, new_barcode, reason, confirmed, *, _expected_actor=None):
        session_actor = self._session.require_authenticated()
        if _expected_actor is not None and not self._session.is_current(_expected_actor):
            raise PermissionError("انتهت جلسة الدخول.")
        if PERMISSION not in session_actor.permissions:
            raise PermissionError("تصحيح الباركود للمدير والمالك فقط.")
        number = str(number or "").strip()
        barcode, bad = canonical_barcode(new_barcode)
        if not number or not str(line_id or "").strip().isdigit():
            raise PieceCorrectionError("LINE_REQUIRED", "اختر سطر الصنف المراد تصحيحه.")
        if not barcode or bad:
            raise PieceCorrectionError("BARCODE_REQUIRED", "اكتب أو امسح الباركود الصحيح للقطعة.")
        text = str(reason or "").strip()
        if not text:
            raise PieceCorrectionError("REASON_REQUIRED", "اكتب سبب التصحيح.")
        if len(text) > MAX_REASON:
            raise PieceCorrectionError("REASON_TOO_LONG", f"السبب {MAX_REASON} حرف على الأكثر.")
        if confirmed is not True:
            raise PieceCorrectionError("CONFIRM_REQUIRED", "أكّد أن القطعة الصحيحة هي التي خرجت فعلًا.")
        with self._transaction() as connection:
            actor = self._authorization.require_permission_for_write(connection, session_actor, PERMISSION)
            repository = self._repository(connection)
            invoice = repository.invoice_by_number(number, lock=True)
            if invoice is None:
                raise PieceCorrectionError("NOT_FOUND", "لا توجد فاتورة بهذا الرقم.")
            if invoice["invoice_status"] != "FINALIZED":
                raise PieceCorrectionError("NOT_FINALIZED", "الفاتورة ملغاة أو غير مكتملة.")
            if repository.is_reversed(invoice["id"]):
                raise PieceCorrectionError("INVOICE_REVERSED", "الفاتورة معكوسة بالكامل: لا يُصحَّح فيها شيء.")
            if repository.lifecycle_count(invoice["id"]):
                raise PieceCorrectionError("HAS_LIFECYCLE", "للفاتورة استبدال أو مرتجع: تُصحَّح القطعة من خلالهما وليس من هنا.")
            line = repository.line(invoice["id"], int(line_id), lock=True)
            if line is None or line["line_type"] != "SELL_INVENTORY" or line["inventory_item_id"] is None:
                raise PieceCorrectionError("LINE_NOT_PIECE", "هذا السطر ليس قطعة مباعة بهويتها.")
            old_id = int(line["inventory_item_id"])
            new_id = repository.find_item_id_by_barcode(barcode)
            if new_id is None:
                raise PieceCorrectionError("BARCODE_UNKNOWN", "لا توجد قطعة بهذا الباركود.")
            if new_id == old_id:
                raise PieceCorrectionError("SAME_PIECE", "هذا هو الباركود المسجل على الفاتورة بالفعل.")
            items = {int(item["id"]): item for item in repository.items([old_id, new_id])}
            old, new = items.get(old_id), items.get(new_id)
            if old is None or old["inventory_status"] != "SOLD":
                raise PieceCorrectionError("OLD_NOT_SOLD", "القطعة المسجلة على الفاتورة لم تعد «مباعة»: لا يمكن التصحيح.")
            if new is None or new["inventory_status"] != "IN_STOCK":
                raise PieceCorrectionError("NEW_NOT_IN_STOCK", "القطعة الصحيحة ليست بالمخزون (مباعة أو خارج المخزون).")
            if int(new["karat_id"]) != int(line["karat_id"]) or Decimal(str(new["weight_grams"])) != Decimal(str(line["weight_grams"])):
                raise PieceCorrectionError("KARAT_OR_WEIGHT", "القطعة الصحيحة يجب أن تكون بنفس العيار ونفس الوزن المسجل على السطر.")
            restock, sold = repository.movement_type_id(RESTOCK_CODE), repository.movement_type_id(SOLD_CODE)
            if restock is None or sold is None:
                raise PieceCorrectionError("MOVEMENT_TYPE_MISSING", "نوع حركة المخزون غير موجود.")
            if not repository.set_status(old_id, "SOLD", "IN_STOCK", actor.user_id) or not repository.set_status(new_id, "IN_STOCK", "SOLD", actor.user_id):
                raise PieceCorrectionError("STATE_CHANGED", "تغيّرت حالة قطعة أثناء التصحيح. أعد المحاولة.")
            now = repository.now()
            reason = f"تصحيح باركود فاتورة {number}: {text}"[:500]
            repository.insert_movement(item=old, movement_type_id=restock, from_status="SOLD", to_status="IN_STOCK", reason=reason, user_id=actor.user_id, invoice_id=invoice["id"], invoice_number=number)
            repository.insert_movement(item=new, movement_type_id=sold, from_status="IN_STOCK", to_status="SOLD", reason=reason, user_id=actor.user_id, invoice_id=invoice["id"], invoice_number=number)
            if not repository.repoint_line(line["id"], new):
                raise PieceCorrectionError("STATE_CHANGED", "تعذّر تحديث السطر. أعد المحاولة.")
            repository.audit(user_id=actor.user_id, invoice_id=invoice["id"], number=number, description=f"تصحيح باركود فاتورة {number}",
                             metadata={"reason": text, "line_id": str(line["id"]), "line_number": int(line["line_number"]), "old_item_id": old_id, "old_barcode": old["barcode_value"] or line["barcode_snapshot"],
                                       "new_item_id": new_id, "new_barcode": new["barcode_value"]}, occurred_at=now)
            if not self._session.is_current(session_actor):
                raise PermissionError("انتهت جلسة الدخول.")
        return {"number": number, "line_id": str(line["id"]), "old_barcode": old["barcode_value"] or line["barcode_snapshot"], "new_barcode": new["barcode_value"]}
