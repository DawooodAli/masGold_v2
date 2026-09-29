"""سجل الفواتير V1: read-only search, detail and reprint of finalized invoices.

Nothing here writes. Every read runs in a transaction that is always rolled back. History
shows the invoice AS FINALIZED:
- the customer, item, factory, karat, weight and prices come from the invoice's own snapshots;
- إجمالي الفاتورة is the stored header total (invoices.net_amount + net_direction);
- the real money is the stored invoice_payments only (cash / wallet / InstaPay / bank);
- the customer-account part is the ledger entry THIS invoice wrote (never the running balance);
- gold value, making and average making are the values saved in the FINALIZED event at finalize
  time — never recalculated from payments, today's price or today's making settings.
Legacy (imported) invoices are partial historical data: an unknown value stays unknown (None).
Reprint goes through the existing (frozen) InvoicePrintService; it never writes either.
"""
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
import json

from repositories.invoice_repository import InvoiceRepository
from services.invoice_service import invoice_transaction

PAGE_SIZE = 50
INVOICE_TYPES = ("SELL", "BUY", "MIXED")
SETTLEMENT_STATUSES = ("SETTLED", "PARTIAL", "UNSETTLED", "OVERPAID")
SALE_LINE_TYPES = ("SELL_INVENTORY", "SELL_UNIDENTIFIED", "SELL_OLD_GOLD")
LEGACY_LABEL = "فاتورة قديمة — بيانات تاريخية جزئية"
VIEW_PERMISSION, REPRINT_PERMISSION, MANAGER_PERMISSION = "INVOICE_VIEW", "INVOICE_REPRINT", "MANAGEMENT_REVIEW"
# Exchange V1: a separate permission; viewing history never grants it.
EXCHANGE_PERMISSION, EXCHANGED_EVENT = "INVOICE_EXCHANGE", "EXCHANGED"
RETURN_PERMISSION, RETURNED_EVENT = "INVOICE_RETURN", "RETURNED"
ZERO = Decimal("0.00")


class _Discard(Exception):
    """Carries a read result out of a transaction that is always rolled back."""


@dataclass(frozen=True)
class LifecycleEvent:
    """One structured timeline entry. Future lifecycle transactions (collection, payout, exchange,
    return, reversal, correction) add rows with their own event_type / amount / linked record."""
    event_id: str
    event_type: str
    occurred_at: object
    actor: str
    amount: Decimal | None = None
    linked_record_id: str | None = None
    status: str = "DONE"


def _decimal(value):
    return None if value in (None, "") else Decimal(str(value))


def transaction_kind(sale_lines, old_gold_lines):
    """What the invoice IS, from its saved lines: SALE (shop-sold lines only), OLD_GOLD_PURCHASE (customer
    old gold only) or MIXED (both). Independent of the settlement direction (a MIXED invoice may pay the
    customer) and of the stored invoice_type."""
    if sale_lines and old_gold_lines:
        return "MIXED"
    if old_gold_lines:
        return "OLD_GOLD_PURCHASE"
    return "SALE"


class InvoiceHistoryService:
    def __init__(self, session, *, transaction_factory=invoice_transaction, invoice_repository=InvoiceRepository,
                 page_size=PAGE_SIZE, print_service=None, lifecycle_repository=None):
        self._session = session
        self._lifecycle = lifecycle_repository
        self._transaction = transaction_factory
        self._invoices = invoice_repository
        self._page_size = page_size
        self._print_service = print_service

    # ------------------------------------------------------------ access
    def _actor(self, expected_actor, permission=VIEW_PERMISSION):
        actor = self._session.require_authenticated()
        if expected_actor is not None and not self._session.is_current(expected_actor):
            raise PermissionError("انتهت جلسة الدخول.")
        if permission not in actor.permissions:
            raise PermissionError("لا توجد صلاحية لإعادة طباعة الفواتير." if permission == REPRINT_PERMISSION
                                  else "لا توجد صلاحية لعرض سجل الفواتير.")
        return actor

    def _read(self, work):
        try:
            with self._transaction() as connection:
                raise _Discard(work(self._invoices(connection)))
        except _Discard as done:
            return done.args[0]

    # ------------------------------------------------------------ search (one summary query per page)
    def search(self, *, text="", date_from=None, date_to=None, invoice_type=None, settlement_status=None, page=1,
               _expected_actor=None):
        """One page, newest first. date_to is inclusive; returns {items, page, has_more}."""
        self._actor(_expected_actor)
        for value in (date_from, date_to):
            if value is not None and not isinstance(value, date):
                raise ValueError("تاريخ البحث غير صحيح.")
        if date_from and date_to and date_from > date_to:
            raise ValueError("تاريخ البداية بعد تاريخ النهاية.")
        if invoice_type not in (None,) + INVOICE_TYPES:
            raise ValueError("نوع العملية غير صحيح.")
        if settlement_status not in (None,) + SETTLEMENT_STATUSES:
            raise ValueError("حالة الفاتورة غير صحيحة.")
        page = max(1, int(page))
        rows = self._read(lambda invoices: invoices.search_invoice_history(
            text=text, date_from=date_from,
            date_to_exclusive=None if date_to is None else date_to + timedelta(days=1),
            invoice_type=invoice_type, settlement_status=settlement_status,
            limit=self._page_size + 1, offset=(page - 1) * self._page_size))
        return {"items": [self._summary(row) for row in rows[:self._page_size]], "page": page,
                "has_more": len(rows) > self._page_size}

    @staticmethod
    def _summary(row):
        legacy = row["record_origin"] == "LEGACY"
        received, paid = _decimal(row.get("money_received")) or ZERO, _decimal(row.get("money_paid")) or ZERO
        return {
            "invoice_id": row["invoice_id"], "invoice_number": row["invoice_number"],
            "finalized_at": row.get("finalized_at") or row["invoice_date"],
            "invoice_type": row["invoice_type"], "invoice_status": row["invoice_status"],
            # From the saved lines (computed in the list query), consistent with the detail page.
            "transaction_kind": row.get("transaction_kind")
                                or {"SELL": "SALE", "BUY": "OLD_GOLD_PURCHASE"}.get(row["invoice_type"], "MIXED"),
            "settlement_status": row["settlement_status"], "is_legacy": legacy,
            "customer_name": row.get("customer_name_snapshot"), "salesperson": row.get("employee_name"),
            "invoice_total": row["net_amount"], "net_direction": row["net_direction"],
            # Real money that moved, in the invoice's own direction (IN for a sale, OUT when the shop pays).
            "money_moved": paid if row["net_direction"] == "SHOP_PAYS_CUSTOMER" else received,
        }

    # ------------------------------------------------------------ exact invoice-number lookup
    def find_by_number(self, invoice_number, *, _expected_actor=None):
        """The printed invoice barcode is Code128 of the invoice number: resolve it exactly (never an
        inventory lookup, never a partial match). Returns the invoice id or None."""
        self._actor(_expected_actor)
        number = (invoice_number or "").strip()
        if not number:
            return None
        return self._read(lambda invoices: invoices.get_invoice_id_by_number(number))

    # ------------------------------------------------------------ detail (loaded only when opened)
    def detail(self, invoice_id, *, _expected_actor=None):
        actor = self._actor(_expected_actor)

        def read(invoices):
            invoice = invoices.get_invoice(invoice_id)
            if invoice is None:
                return None
            invoice["payment_accounts"] = invoices.get_payment_accounts(invoice_id)
            invoice["account_entries"] = invoices.invoice_account_allocation(invoice_id, invoice.get("customer_id"))
            invoice["exchanges"] = self._exchanges(invoices, invoice_id, invoice.get("events", []))
            invoice["returns"] = self._returns(invoices, invoice_id, invoice.get("events", []))
            return invoice
        invoice = self._read(read)
        if invoice is None:
            raise ValueError("الفاتورة غير موجودة.")
        manager = MANAGER_PERMISSION in actor.permissions
        legacy = invoice["record_origin"] == "LEGACY"
        finalized = next((e for e in invoice.get("events", []) if e["event_type"] == "FINALIZED"), None)
        saved = self._details(finalized)

        accounts = {row["payment_id"]: row.get("bank_account_name") for row in invoice["payment_accounts"]}
        payments = [{"payment_id": p["payment_id"], "method_code": p["payment_method_code"],
                     "method_name": p["payment_method_name"], "direction": p["direction"],
                     "amount": _decimal(p["amount"]), "account_name": accounts.get(p["payment_id"]),
                     "occurred_at": p.get("occurred_at")} for p in invoice["payments"]]
        received = sum((p["amount"] for p in payments if p["direction"] == "IN"), ZERO)
        paid = sum((p["amount"] for p in payments if p["direction"] == "OUT"), ZERO)
        entries = invoice["account_entries"]
        account = sum((_decimal(e["amount_delta"]) for e in entries), ZERO) if entries else (
            _decimal(saved.get("L")) if saved.get("L") not in (None, "") else ZERO)

        sale_lines = [self._sale_line(line) for line in invoice["lines"] if line["line_type"] in SALE_LINE_TYPES]
        old_gold_lines = [self._old_gold_line(line) for line in invoice["lines"] if line["line_type"] == "BUY_OLD_GOLD"]
        economics = {
            # The values saved when the invoice was finalized; None = not stored (older / legacy invoice).
            "gold_value": _decimal(saved.get("gold_value_total")),
            "effective_sale": _decimal(saved.get("effective_sale_total")),
            "average_making": _decimal(saved.get("average_making_per_gram")),
            "making_total": _decimal(saved.get("making_total")) if manager else None,
            "making_status": saved.get("making_status"),
        }
        return {
            "invoice_id": invoice["invoice_id"], "invoice_number": invoice["invoice_number"],
            "invoice_type": invoice["invoice_type"], "invoice_status": invoice["invoice_status"],
            "settlement_status": invoice["settlement_status"], "record_origin": invoice["record_origin"],
            "is_legacy": legacy, "legacy_label": LEGACY_LABEL if legacy else "", "read_only": True,
            "finalized_at": invoice.get("finalized_at") or invoice["invoice_date"],
            "salesperson": invoice.get("employee_name"), "finalized_by": invoice.get("finalized_by_name"),
            "customer": {"customer_id": invoice.get("customer_id"),
                         "code": invoice.get("customer_code_snapshot"), "name": invoice.get("customer_name_snapshot"),
                         "mobile": invoice.get("mobile_snapshot"), "place": invoice.get("place_snapshot")},
            "invoice_total": invoice["net_amount"], "net_direction": invoice["net_direction"],
            "sell_total": invoice.get("sell_total"), "buy_total": invoice.get("buy_total"),
            "sale_lines": sale_lines,
            "old_gold_lines": old_gold_lines,
            # What the invoice IS comes from its saved lines, never from the money direction.
            "transaction_kind": transaction_kind(sale_lines, old_gold_lines),
            "payments": payments, "money_received": received, "money_paid": paid,
            "account_signed": account,                    # + على العميل, − للعميل (this invoice only)
            "economics": economics,
            "price_snapshots": [{"karat": p["karat_value"], "buy": _decimal(p.get("shop_buy_price_per_gram")),
                                 "sell": _decimal(p.get("shop_sell_price_per_gram")),
                                 "source": p.get("source_code_snapshot"),
                                 "provider_timestamp": p.get("provider_timestamp")}
                                for p in invoice.get("price_snapshots", [])],
            "timeline": self._timeline(invoice.get("events", [])),
            # Exchanges of this invoice, shown inside the same detail (no second, repeating page).
            "exchanges": invoice.get("exchanges", []),
            "notes": invoice.get("notes") or "",
            "can_reprint": REPRINT_PERMISSION in actor.permissions,
            # The Exchange action; the service still checks the permission and every rule itself.
            "can_exchange": (EXCHANGE_PERMISSION in actor.permissions and not legacy
                             and invoice["invoice_status"] == "FINALIZED"
                             and invoice["invoice_type"] in ("SELL", "MIXED")
                             and self._has_unexchanged_piece(sale_lines, invoice.get("exchanges", []))),
            # Returns of this invoice (finalized ones; the invoice itself stays exactly as finalized).
            "returns": invoice.get("returns", []),
            "can_return": (RETURN_PERMISSION in actor.permissions and not legacy
                           and invoice["invoice_status"] == "FINALIZED"
                           and invoice["invoice_type"] in ("SELL", "MIXED") and bool(sale_lines)),
            "can_see_manager_details": manager,
        }


    @staticmethod
    def _has_unexchanged_piece(sale_lines, exchanges):
        """A stock piece of this invoice that no exchange has taken back yet (display only)."""
        returned = {p["inventory_item_id"] for x in exchanges for p in x["returned"]}
        return any(line["kind"] == "INVENTORY" and line.get("inventory_item_id") not in (None, "")
                   and str(line["inventory_item_id"]) not in returned for line in sale_lines)

    def _exchanges(self, invoices, invoice_id, events):
        """Only read when the invoice was exchanged (an EXCHANGED event exists): one lifecycle read each."""
        if not any(e["event_type"] == EXCHANGED_EVENT for e in events):
            return []
        from repositories.invoice_lifecycle_repository import InvoiceLifecycleRepository
        from services.exchange_service import exchange_summary
        lifecycle = (self._lifecycle or InvoiceLifecycleRepository)(invoices.connection)
        return [exchange_summary(lifecycle.get_transaction(row["lifecycle_id"]))
                for row in lifecycle.for_invoice(invoice_id)]

    def _returns(self, invoices, invoice_id, events):
        """Only read when the invoice has a finalized Return (a RETURNED event): one query set each."""
        if not any(e["event_type"] == RETURNED_EVENT for e in events):
            return []
        from repositories.invoice_return_repository import InvoiceReturnRepository
        from services.return_service import return_summary
        return [return_summary(row) for row in InvoiceReturnRepository(invoices.connection).for_invoice(invoice_id)
                if row["return_status"] == "FINALIZED"]

    @staticmethod
    def _details(event):
        if not event:
            return {}
        value = event.get("details_json")
        if isinstance(value, str):
            try:
                return json.loads(value) or {}
            except ValueError:
                return {}
        return value or {}

    @staticmethod
    def _sale_line(line):
        """Everything a future exchange / return needs, straight from the line's own snapshots."""
        kind = {"SELL_INVENTORY": "INVENTORY", "SELL_UNIDENTIFIED": "MANUAL",
                "SELL_OLD_GOLD": "OLD_GOLD_STOCK"}[line["line_type"]]
        weight, price = _decimal(line["weight_grams"]), _decimal(line.get("gold_price_per_gram"))
        return {"line_id": line["line_id"], "line_number": line.get("line_number"), "line_type": line["line_type"],
                "kind": kind, "identification_method": line.get("identification_method"),
                "inventory_item_id": line.get("inventory_item_id"), "barcode": line.get("barcode_snapshot"),
                "item_name": line.get("item_name_snapshot"), "category": line.get("category_snapshot"),
                "factory": line.get("factory_snapshot"), "karat": line["karat_value"], "weight": weight,
                "quantity": line.get("quantity") or 1, "size": line.get("size_snapshot"),
                "notes": line.get("notes") or "", "gold_price": price,
                "gold_value": None if price is None else (weight * price).quantize(Decimal("0.01")),
                "making_total": _decimal(line.get("making_total")), "line_total": _decimal(line.get("line_total"))}

    @staticmethod
    def _old_gold_line(line):
        return {"line_id": line["line_id"], "karat": line["karat_value"], "weight": _decimal(line["weight_grams"]),
                "gold_price": _decimal(line.get("gold_price_per_gram")), "gross": _decimal(line.get("gross_total")),
                "discount_type": line.get("discount_type"), "discount_value": _decimal(line.get("discount_value")),
                "discount_amount": _decimal(line.get("discount_amount")),
                "line_total": _decimal(line.get("line_total")), "notes": line.get("notes") or ""}

    @classmethod
    def _timeline(cls, events):
        """The stored invoice_events, oldest first (deterministic: time, then id). An EXCHANGED event
        carries the exchange's signed difference and links to its lifecycle transaction."""
        ordered = sorted(events, key=lambda e: (e["occurred_at"], int(e["event_id"])))
        timeline = []
        for e in ordered:
            linked = None if e.get("related_invoice_id") is None else str(e["related_invoice_id"])
            amount = None
            if e["event_type"] == EXCHANGED_EVENT:
                saved = cls._details(e)
                amount = _decimal(saved.get("signed_amount"))
                linked = None if saved.get("lifecycle_id") in (None, "") else str(saved["lifecycle_id"])
            elif e["event_type"] == RETURNED_EVENT:
                saved = cls._details(e)                   # money the shop paid back: negative for the shop
                amount = None if saved.get("total_amount") in (None, "") else -_decimal(saved["total_amount"])
                linked = None if saved.get("return_id") in (None, "") else str(saved["return_id"])
            timeline.append(LifecycleEvent(event_id=str(e["event_id"]), event_type=e["event_type"],
                                           occurred_at=e["occurred_at"], actor=e.get("performed_by_name") or "",
                                           amount=amount, linked_record_id=linked))
        return timeline

    # ------------------------------------------------------------ reprint (read-only)
    def reprint(self, invoice_id, *, _expected_actor=None):
        """The ORIGINAL finalized invoice through the frozen print service: no number, no payment,
        no ledger, no inventory, no timestamp change — a printer failure changes nothing either."""
        self._actor(_expected_actor, REPRINT_PERMISSION)
        service = self._print_service
        if service is None:
            from services.invoice_print_service import InvoicePrintService
            service = self._print_service = InvoicePrintService(self._session)
        return service.print_invoice(invoice_id, _expected_actor=_expected_actor)
