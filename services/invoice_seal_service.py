"""Seal code of an invoice: compute it for the printed / PDF invoice and verify one an employee types (owner 2026-11-16). Rules are in services/invoice_seal.py."""
from contextlib import contextmanager
import logging

from repositories.invoice_repository import InvoiceRepository
from repositories.invoice_seal_repository import InvoiceSealRepository
from services import invoice_seal as rules
from services.invoice_service import invoice_transaction

logger = logging.getLogger(__name__)

VIEW_PERMISSION = "INVOICE_VIEW"
NOT_FOUND_TEXT = "لا توجد فاتورة بهذا الرقم."
GENUINE_TEXT = "الكود صحيح: الفاتورة أصلية ومطابقة لما هو محفوظ."
MISMATCH_TEXT = "الكود غير مطابق: الرقم أو المبلغ أو التاريخ أو الكود غير صحيح. الفاتورة غير موثوقة."
UNAVAILABLE_TEXT = "تعذر التحقق الآن (مفتاح الختم غير متاح)."
DIRECTION_TEXT = {"IN": "مستلم من العميل", "OUT": "مدفوع للعميل", "SETTLED": "بدون نقدية"}


def facts_of(invoice):
    """The stored header facts the code covers, copied BEFORE any display adjustment (an exchange difference, the sale-total view)."""
    return {"number": invoice.get("invoice_number"), "net_amount": invoice.get("net_amount"),
            "net_direction": invoice.get("net_direction"), "invoice_date": invoice.get("invoice_date")}


class InvoiceSealService:
    def __init__(self, session=None, *, transaction_factory=invoice_transaction, repository=InvoiceSealRepository):
        self._session = session
        self._transaction = transaction_factory
        self._repository = repository

    # ------------------------------------------------------------ key
    def _key(self):
        """The secret key; created once in its OWN committed transaction (a print reads inside a transaction that is always rolled back)."""
        with self._transaction() as connection:
            value = self._repository(connection).key(create=False)
        if value:
            return value
        with self._transaction() as connection:
            return self._repository(connection).key(create=True)

    # ------------------------------------------------------------ compute (for printing)
    def seal(self, facts):
        """{"code", "check"} for the stored facts, or None. Never raises: a failure here must never stop a print."""
        try:
            key = self._key()
            if not key or not facts or not facts.get("number"):
                return None
            return {"code": rules.code_for(key, facts["number"], facts["net_amount"], facts["net_direction"], facts["invoice_date"]),
                    "check": rules.check_for(key, facts["number"])}
        except Exception:                                   # noqa: BLE001 - see docstring
            logger.warning("Invoice seal could not be computed", exc_info=True)
            return None

    # ------------------------------------------------------------ verify (screen)
    def verify(self, number, code, *, _expected_actor=None):
        """{"state": GENUINE | MISMATCH | NOT_FOUND | UNAVAILABLE, "message", "invoice": {...} | None}. Read only."""
        actor = self._session.require_authenticated() if self._session is not None else None
        if actor is not None and _expected_actor is not None and not self._session.is_current(_expected_actor):
            raise PermissionError("انتهت جلسة الدخول.")
        if actor is not None and VIEW_PERMISSION not in actor.permissions:
            raise PermissionError("لا توجد صلاحية للتحقق من الفواتير.")
        number = str(number or "").strip()
        if not number:
            return {"state": "NOT_FOUND", "message": NOT_FOUND_TEXT, "invoice": None}
        key = self._key()
        with self._transaction() as connection:
            invoices = InvoiceRepository(connection)
            invoice_id = invoices.get_invoice_id_by_number(number)
            invoice = invoices.get_invoice(invoice_id) if invoice_id else None
        if invoice is None:
            return {"state": "NOT_FOUND", "message": NOT_FOUND_TEXT, "invoice": None}
        if not key:
            return {"state": "UNAVAILABLE", "message": UNAVAILABLE_TEXT, "invoice": None}
        from services.correction_rules import reversal_of
        facts = facts_of(invoice)
        expected = rules.code_for(key, facts["number"], facts["net_amount"], facts["net_direction"], facts["invoice_date"])
        genuine = rules.matches(expected, code)
        summary = {"number": str(invoice["invoice_number"]), "total": str(invoice.get("net_amount")),
                   "direction": DIRECTION_TEXT.get(invoice.get("net_direction"), ""),
                   "date": str(invoice.get("invoice_date"))[:10], "status": invoice.get("invoice_status"),
                   "reversed_by": reversal_of(invoice)} if genuine else None
        return {"state": "GENUINE" if genuine else "MISMATCH", "message": GENUINE_TEXT if genuine else MISMATCH_TEXT, "invoice": summary}
