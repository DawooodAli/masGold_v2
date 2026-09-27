"""Read-only invoice history: search and invoice detail for native and legacy invoices.

Nothing here writes. Every read runs in a transaction that is always rolled back.
The commercial net N comes from the stored header, P from the stored real-money
payments, and R = N - P. Legacy (imported) invoices are partial historical data:
their unknown amounts stay unknown (None) and R is not derived for them.
"""
from datetime import date, timedelta
from decimal import Decimal

from repositories.invoice_repository import InvoiceRepository
from services.invoice_service import invoice_transaction


LEGACY_LABEL = "فاتورة قديمة — بيانات تاريخية جزئية"
STATUSES = ("FINALIZED", "VOIDED")
PAGE_SIZE = 25


class _Discard(Exception):
    """Carries a read result out of a transaction that is always rolled back."""


class InvoiceHistoryService:
    def __init__(self, session, *, transaction_factory=invoice_transaction, invoice_repository=InvoiceRepository,
                 page_size=PAGE_SIZE):
        self._session = session
        self._transaction = transaction_factory
        self._invoices = invoice_repository
        self._page_size = page_size

    def _require_view(self, expected_actor):
        actor = self._session.require_authenticated()
        if expected_actor is not None and not self._session.is_current(expected_actor):
            raise PermissionError("انتهت جلسة الدخول.")
        if "INVOICE_VIEW" not in actor.permissions:
            raise PermissionError("لا توجد صلاحية لعرض الفواتير.")

    def _read(self, work):
        try:
            with self._transaction() as connection:
                raise _Discard(work(self._invoices(connection)))
        except _Discard as done:
            return done.args[0]

    # ------------------------------------------------------------ search

    def search(self, *, number="", customer_id=None, date_from=None, date_to=None, status=None, page=1,
               _expected_actor=None):
        """One page, newest first. date_to is inclusive; returns {items, page, has_more}."""
        self._require_view(_expected_actor)
        for value in (date_from, date_to):
            if value is not None and not isinstance(value, date):
                raise ValueError("تاريخ البحث غير صحيح.")
        if date_from and date_to and date_from > date_to:
            raise ValueError("تاريخ البداية بعد تاريخ النهاية.")
        if status not in (None,) + STATUSES:
            raise ValueError("حالة الفاتورة غير صحيحة.")
        page = max(1, int(page))
        rows = self._read(lambda invoices: invoices.search_invoices(
            number=number, customer_id=customer_id or None, date_from=date_from,
            date_to_exclusive=None if date_to is None else date_to + timedelta(days=1), status=status,
            limit=self._page_size + 1, offset=(page - 1) * self._page_size))
        return {"items": [self._summary(row) for row in rows[:self._page_size]], "page": page,
                "has_more": len(rows) > self._page_size}

    @staticmethod
    def _summary(row):
        legacy = row["record_origin"] == "LEGACY"
        return {
            "invoice_id": row["invoice_id"], "invoice_number": row["invoice_number"],
            "invoice_date": row["invoice_date"], "invoice_type": row["invoice_type"],
            "invoice_status": row["invoice_status"], "record_origin": row["record_origin"],
            "is_legacy": legacy, "legacy_label": LEGACY_LABEL if legacy else "",
            "customer_code": row.get("customer_code_snapshot"), "customer_name": row.get("customer_name_snapshot"),
            "salesperson": row.get("employee_name"), "net_amount": row["net_amount"],
            "net_direction": row["net_direction"], "settlement_status": row["settlement_status"],
        }

    # ------------------------------------------------------------ detail

    def detail(self, invoice_id, *, _expected_actor=None):
        self._require_view(_expected_actor)
        invoice = self._read(lambda invoices: invoices.get_invoice(invoice_id))
        if invoice is None:
            raise ValueError("الفاتورة غير موجودة.")
        legacy = invoice["record_origin"] == "LEGACY"
        net = invoice["net_amount"] or Decimal("0.00")
        signed_net = {"CUSTOMER_PAYS_SHOP": net, "SHOP_PAYS_CUSTOMER": -net}.get(invoice["net_direction"],
                                                                               Decimal("0.00"))
        received = sum((p["amount"] for p in invoice["payments"] if p["direction"] == "IN"), Decimal("0.00"))
        paid = sum((p["amount"] for p in invoice["payments"] if p["direction"] == "OUT"), Decimal("0.00"))
        residual = None if legacy else signed_net - (received - paid)
        return {
            **invoice,
            "is_legacy": legacy, "legacy_label": LEGACY_LABEL if legacy else "", "read_only": True,
            "sale_lines": [line for line in invoice["lines"] if line["line_type"] == "SELL_INVENTORY"],
            "old_gold_lines": [line for line in invoice["lines"] if line["line_type"] == "BUY_OLD_GOLD"],
            "other_lines": [line for line in invoice["lines"] if line["line_type"] == "OTHER"],
            "settlement": {"net_signed": signed_net, "net_amount": net, "net_direction": invoice["net_direction"],
                           "money_received": received, "money_paid": paid, "payment_signed": received - paid,
                           "residual_signed": residual,
                           "residual_amount": None if residual is None else abs(residual)},
        }
