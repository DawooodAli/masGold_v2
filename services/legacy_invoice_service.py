"""إلغاء فاتورة قديمة (من مراجعات المدير): cancel an OLD invoice that was imported from the previous program, and put its sold pieces back in stock.

* Only a LEGACY invoice that is still FINALIZED. New invoices are corrected through Reversal / Correction, never here.
* In ONE transaction (invoice locked first, then its pieces in ascending id order): every piece of the invoice goes SOLD -> IN_STOCK with a stock movement that names the invoice and the reason; the invoice
  becomes VOIDED with who / when / why; an audit row is written. A piece that is NOT SOLD any more (for example it was sold again on a new invoice) refuses the whole cancellation, naming its barcode.
* Old-gold lines (bought from the customer or sold from the old-gold stock) are history only: nothing moved in the counted old gold when they were imported, so nothing moves now.
* The customer's balance is NOT touched: the old balances were imported as one opening entry per customer; if this invoice's unpaid remainder is part of it, the owner adjusts it by hand.
Right: TRANSACTION_CORRECT (manager / owner), rechecked in the transaction (the actor is the session user); a reason is mandatory and one explicit confirmation is required."""
from decimal import Decimal

from repositories.legacy_invoice_repository import LegacyInvoiceRepository
from services.authorization_service import AuthorizationService
from services.invoice_service import invoice_transaction

PERMISSION = "TRANSACTION_CORRECT"
MOVEMENT_CODE = "RETURNED_ITEM_RESTOCKED"
MAX_REASON = 500


class LegacyInvoiceError(ValueError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


class LegacyInvoiceService:
    def __init__(self, session, *, transaction_factory=invoice_transaction, repository=LegacyInvoiceRepository, authorization=None):
        self._session = session
        self._transaction = transaction_factory
        self._repository = repository
        self._authorization = authorization or AuthorizationService(session)

    def can_void(self):
        actor = self._session.current_actor()
        return actor is not None and PERMISSION in actor.permissions

    def _actor(self, expected_actor):
        actor = self._session.require_authenticated()
        if expected_actor is not None and not self._session.is_current(expected_actor):
            raise PermissionError("انتهت جلسة الدخول.")
        if PERMISSION not in actor.permissions:
            raise PermissionError("إلغاء الفواتير للمدير والمالك فقط.")
        return actor

    @staticmethod
    def _number(value):
        number = str(value or "").strip()
        if not number or len(number) > 100:
            raise LegacyInvoiceError("NUMBER_REQUIRED", "اكتب رقم الفاتورة.")
        return number

    def _load(self, repository, number, lock):
        invoice = repository.invoice_by_number(number, lock=lock)
        if invoice is None:
            raise LegacyInvoiceError("NOT_FOUND", "لا توجد فاتورة بهذا الرقم.")
        if invoice["record_origin"] != "LEGACY":
            raise LegacyInvoiceError("NOT_LEGACY", "هذه فاتورة جديدة: تُلغى بالعكس من «تصحيح العمليات» وليس من هنا.")
        if invoice["invoice_status"] == "VOIDED":
            raise LegacyInvoiceError("ALREADY_VOIDED", "هذه الفاتورة ملغاة بالفعل.")
        if invoice["invoice_status"] != "FINALIZED":
            raise LegacyInvoiceError("NOT_FINALIZED", "الفاتورة غير مكتملة.")
        lines = repository.lines(invoice["id"])
        ids = sorted({int(line["inventory_item_id"]) for line in lines if line["line_type"] == "SELL_INVENTORY" and line["inventory_item_id"] is not None})
        items = {int(item["id"]): item for item in repository.items(ids, lock=lock)}
        return invoice, lines, ids, items

    def lookup(self, number, *, _expected_actor=None):
        """What cancelling would do (read only): the invoice and each sold piece with its current state."""
        self._actor(_expected_actor)
        number = self._number(number)
        with self._transaction() as connection:
            invoice, lines, ids, items = self._load(self._repository(connection), number, False)
        pieces = [{"barcode": line["barcode_snapshot"] or "بدون باركود", "item": line["item_name_snapshot"] or "—", "karat": int(line["karat_value"]), "weight": Decimal(str(line["weight_grams"])),
                   "status": (items.get(int(line["inventory_item_id"])) or {}).get("inventory_status") if line["inventory_item_id"] is not None else None}
                  for line in lines if line["line_type"] == "SELL_INVENTORY"]
        return {"number": invoice["invoice_number"], "date": invoice["invoice_date"], "customer": invoice["customer_name_snapshot"] or "—", "total": Decimal(str(invoice["net_amount"])),
                "pieces": pieces, "old_gold_lines": sum(1 for line in lines if line["line_type"] != "SELL_INVENTORY"), "blocked": [p["barcode"] for p in pieces if p["status"] != "SOLD"]}

    def void(self, number, reason, confirmed, *, _expected_actor=None):
        session_actor = self._actor(_expected_actor)
        number = self._number(number)
        text = str(reason or "").strip()
        if not text:
            raise LegacyInvoiceError("REASON_REQUIRED", "اكتب سبب الإلغاء.")
        if len(text) > MAX_REASON:
            raise LegacyInvoiceError("REASON_TOO_LONG", f"السبب {MAX_REASON} حرف على الأكثر.")
        if confirmed is not True:
            raise LegacyInvoiceError("CONFIRM_REQUIRED", "أكّد إلغاء الفاتورة وإرجاع القطع للمخزون.")
        with self._transaction() as connection:
            actor = self._authorization.require_permission_for_write(connection, session_actor, PERMISSION)
            repository = self._repository(connection)
            invoice, lines, ids, items = self._load(repository, number, True)
            bad = [line for line in lines if line["line_type"] == "SELL_INVENTORY" and line["inventory_item_id"] is not None
                   and (int(line["inventory_item_id"]) not in items or items[int(line["inventory_item_id"])]["inventory_status"] != "SOLD")]
            if bad:
                names = [line["barcode_snapshot"] or str(line["inventory_item_id"]) for line in bad]
                raise LegacyInvoiceError("PIECE_NOT_SOLD", "لا يمكن الإلغاء: القطعة لم تعد «مباعة» (اتباعت تاني أو اتغيرت حالتها): " + "، ".join(names[:5]))
            movement_type = repository.movement_type_id(MOVEMENT_CODE)
            if movement_type is None:
                raise LegacyInvoiceError("MOVEMENT_TYPE_MISSING", "نوع حركة المخزون غير موجود.")
            now = repository.now()
            weights = Decimal(0)
            for item_id in ids:
                item = items[item_id]
                if not repository.restock_piece(item_id, actor.user_id):
                    raise LegacyInvoiceError("PIECE_NOT_SOLD", "تغيّرت حالة قطعة أثناء الإلغاء. أعد المحاولة.")
                weight = Decimal(str(item["weight_grams"]))
                weights += weight
                repository.insert_movement(item_id=item_id, movement_type_id=movement_type, stock_classification=item["stock_classification"], weight=weight,
                                           reason=f"إلغاء فاتورة قديمة {number}: {text}"[:500], user_id=actor.user_id, reference_id=invoice["id"])
            if not repository.void_invoice(invoice["id"], actor.user_id, text):
                raise LegacyInvoiceError("ALREADY_VOIDED", "هذه الفاتورة ملغاة بالفعل.")
            repository.audit(user_id=actor.user_id, invoice_id=invoice["id"], number=number, description=f"إلغاء فاتورة قديمة {number} وإرجاع {len(ids)} قطعة للمخزون",
                             metadata={"reason": text, "pieces": len(ids), "grams": str(weights)}, occurred_at=now)
            if not self._session.is_current(session_actor):
                raise PermissionError("انتهت جلسة الدخول.")
        return {"number": number, "restocked": len(ids), "grams": weights}
