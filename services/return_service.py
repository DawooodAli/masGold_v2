"""Return V1 (مرتجع): a Return transaction linked to one finalized invoice (022).

The original invoice is immutable: a Return never creates, edits or voids an invoice row, and a
customer old-gold purchase on the same invoice is never touched. A Return has its own reference
(RT000001), header, lines, real-money legs, approval and audit.

What can be returned (one after-sale lifecycle use per piece / gram):
  SELL_INVENTORY  the identified piece, whole (never a fraction of one physical piece), once —
                  not after an Exchange took it (and an Exchange never takes it after a Return).
                  SOLD → RETURNED_PENDING + the existing RETURNED_ITEM_INSPECTION review.
  SELL_OLD_GOLD   F7 weight, fully or partially: original grams − grams already returned.
                  The exact grams go back into the COUNTED old-gold stock (RETURN_OLD_GOLD_IN).
  SELL_UNIDENTIFIED (F5) is refused: no workflow resolves an unidentified sale to a real
                  inventory piece yet, and a Return never invents an identity.

Valuation (never QML), per line: gold = returned grams × the CURRENT SHOP_BUY of that exact karat
(the quote is snapshotted per line). Historical making (the original line's making_total, the
part attributable to the returned grams) is refunded only up to exactly 72 hours after the
original finalize; missing historical making inside the window is refused, never guessed.
After 72 hours: gold only, and a manager approval (RETURN_APPROVAL) of the exact selection.
Deadline: the end of the 15th calendar day from the invoice's local date (day 1 = invoice day),
never extended by an approval.

Settlement: real money OUT legs (cash / wallet / InstaPay / bank; several allowed) plus an
explicit customer-account part must equal the Return total exactly. A payment account may go
negative. Finalize is one transaction; lock order: actor → prices → original invoice → Return
header → approval review → inventory items (ascending id) → customer → bank accounts.
"""
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
import hashlib
import json

from repositories.customer_repository import CustomerRepository
from repositories.invoice_repository import InvoiceRepository
from repositories.invoice_return_repository import InvoiceReturnRepository, return_code
from services.authorization_service import AuthorizationService
from services.gold_price_service import GoldPriceService
from services.invoice_service import EGYPT_UTC_OFFSETS, InvoiceService, invoice_transaction


RETURN_PERMISSION, APPROVAL_PERMISSION = "INVOICE_RETURN", "MANAGEMENT_REVIEW"
MAKING_WINDOW_HOURS = 72
RETURN_DAYS = 15
MAX_RETURN_LINES = 20
GRAM, MONEY = Decimal("0.001"), Decimal("0.01")
ZERO, ZERO_GRAMS = Decimal("0.00"), Decimal("0.000")
EDITABLE = ("DRAFT", "PENDING_APPROVAL", "APPROVED", "REJECTED")
STATUS_TEXT = {"DRAFT": "مسودة", "PENDING_APPROVAL": "بانتظار موافقة المدير", "APPROVED": "وافق المدير",
               "REJECTED": "رفض المدير", "FINALIZED": "تم المرتجع", "CANCELED": "ملغي"}
LINE_REFUSALS = {
    "BUY_OLD_GOLD": "الكسر المشترى من العميل ليس بيعاً؛ لا يُرد.",
    "SELL_UNIDENTIFIED": "البند اليدوي غير محدد في المخزون؛ لا يُرد في هذا الإصدار.",
    "OTHER": "هذا البند لا يُرد.",
}
EXCHANGED_TEXT = "تم استبدال هذه القطعة"
RETURNED_TEXT = "تم رد هذه القطعة"
NO_WEIGHT_TEXT = "لا يوجد وزن متاح للمرتجع"
EXPIRED_TEXT = "انتهت مدة الاسترداد"
NOT_SOLD_TEXT = "القطعة ليست في حالة مباعة"
WINDOW_TEXT = "داخل مدة رد المصنعية"
NO_WINDOW_TEXT = "انتهت مدة رد المصنعية — قيمة الذهب فقط"
APPROVAL_TEXT = "يحتاج موافقة المدير"


class ReturnError(ValueError):
    """A business rule refused the Return; `code` is stable for the UI and tests."""

    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


class _Discard(Exception):
    """Carries a read result out of a transaction that is always rolled back."""


@dataclass(frozen=True)
class ReturnSelection:
    source_line_id: str
    weight_grams: str | None = None           # F7 only (partial allowed); an identified piece is whole


@dataclass(frozen=True)
class ReturnDraft:
    source_invoice_id: str
    lines: tuple = field(default_factory=tuple)
    payments: tuple = field(default_factory=tuple)     # PaymentInput, always paid to the customer (OUT)
    account_amount: str | None = None                  # explicit part kept on the customer's account
    notes: str | None = None                           # optional, never required
    return_id: str | None = None                       # an existing draft / approved Return


@dataclass(frozen=True)
class _Line:
    source_line_id: str
    line_type: str
    inventory_item_id: str | None
    karat_id: int
    karat: int
    source_weight: Decimal
    returned_before: Decimal
    weight: Decimal
    item_name: str | None
    barcode: str | None
    historical_making: Decimal | None
    price_per_gram: Decimal = ZERO
    quote_id: int | None = None
    provider_timestamp: datetime | None = None
    gold_amount: Decimal = ZERO
    making_refund: Decimal = ZERO


def _money(value):
    return None if value is None else str(value)


def _grams(value):
    return None if value is None else str(Decimal(value).quantize(GRAM))


def _day(value):
    return value if isinstance(value, date) and not isinstance(value, datetime) else value.date()


def eligibility(finalized_at, invoice_date, now):
    """The Return windows of one invoice at `now` (all local shop time):
    making refund while now ≤ finalized_at + 72 h (exactly 72 h still refunds);
    allowed while now < 00:00 of (invoice day + 15 days) — the end of the 15th calendar day."""
    making_ends = finalized_at + timedelta(hours=MAKING_WINDOW_HOURS)
    deadline = datetime.combine(_day(invoice_date or finalized_at) + timedelta(days=RETURN_DAYS), datetime.min.time())
    window = now <= making_ends
    return {"making_window_open": window, "requires_approval": not window, "expired": now >= deadline,
            "making_ends_at": making_ends, "deadline_at": deadline,
            "last_allowed_at": deadline - timedelta(seconds=1),
            "age_days": (_day(now) - _day(finalized_at)).days}


def fingerprint(lines):
    """The normalized selection (source line + grams) and its sha256; an approval binds to it."""
    normalized = sorted(({"source_line_id": str(l.source_line_id), "weight_grams": _grams(l.weight)} for l in lines),
                        key=lambda row: int(row["source_line_id"]))
    text = json.dumps(normalized, sort_keys=True, separators=(",", ":"))
    return normalized, hashlib.sha256(text.encode("utf-8")).hexdigest()


def _allocated(total, grams, source):
    return (total * grams / source).quantize(MONEY, rounding=ROUND_HALF_UP)


def value_lines(lines, price_set, window_open):
    """Current SHOP_BUY gold value per line (its own karat's actual quote) + historical making
    attributable to the returned grams (cumulative allocation, so parts always add up to the line's making)."""
    valued = []
    for line in lines:
        quote = price_set.prices.get(line.karat_id)
        if quote is None:
            raise ReturnError("PRICE_MISSING", f"لا يوجد سعر شراء معتمد لعيار {line.karat}.")
        price = Decimal(quote.shop_buy_price_per_gram)
        gold = (line.weight * price).quantize(MONEY, rounding=ROUND_HALF_UP)
        refund = ZERO
        if window_open:
            if line.historical_making is None:
                raise ReturnError("HISTORICAL_MAKING_UNAVAILABLE",
                                  "تعذر تحديد مصنعية الفاتورة الأصلية بدقة؛ لا يمكن رد المصنعية.")
            total = line.historical_making
            refund = (_allocated(total, line.returned_before + line.weight, line.source_weight)
                      - _allocated(total, line.returned_before, line.source_weight))
        valued.append(_Line(**{**line.__dict__, "price_per_gram": price, "quote_id": quote.quote_id,
                               "provider_timestamp": None if quote.provider_timestamp is None
                               else quote.provider_timestamp.replace(tzinfo=None),
                               "gold_amount": gold, "making_refund": refund}))
    return valued


def _parse_weight(text):
    try:
        value = Decimal(str(text).strip())
    except (InvalidOperation, ValueError):
        raise ReturnError("WEIGHT_INVALID", "اكتب الوزن بالأرقام.") from None
    if not value.is_finite() or value <= 0 or value != value.quantize(GRAM):
        raise ReturnError("WEIGHT_INVALID", "اكتب وزناً صحيحاً أكبر من صفر (حتى 3 أرقام عشرية).")
    return value.quantize(GRAM)


def line_state(line):
    """(returnable, reason, remaining grams) of one original invoice line."""
    sold = Decimal(str(line["weight_grams"]))
    returned = Decimal(str(line.get("returned_grams") or "0"))
    if line["line_type"] == "SELL_INVENTORY" and line.get("inventory_item_id") not in (None, ""):
        if line.get("consumed_by_exchange"):
            return False, EXCHANGED_TEXT, ZERO_GRAMS
        if returned > 0:
            return False, RETURNED_TEXT, ZERO_GRAMS
        if line.get("item_status") != "SOLD":
            return False, NOT_SOLD_TEXT, ZERO_GRAMS
        return True, "", sold
    if line["line_type"] == "SELL_OLD_GOLD":
        remaining = sold - returned
        if line.get("consumed_by_exchange"):
            return False, EXCHANGED_TEXT, ZERO_GRAMS
        return (remaining > 0), ("" if remaining > 0 else NO_WEIGHT_TEXT), max(remaining, ZERO_GRAMS)
    return False, LINE_REFUSALS.get(line["line_type"], LINE_REFUSALS["OTHER"]), ZERO_GRAMS


def resolve_selection(selections, source_lines):
    """Validated _Line rows (not yet valued) from the employee's selection and the authoritative lines."""
    if not selections:
        raise ReturnError("RETURN_LINES_REQUIRED", "اختر ما يرده العميل.")
    if len(selections) > MAX_RETURN_LINES:
        raise ReturnError("TOO_MANY_LINES", f"الحد الأقصى {MAX_RETURN_LINES} بنداً في المرتجع الواحد.")
    by_id = {str(line["line_id"]): line for line in source_lines}
    chosen, seen = [], set()
    for selection in selections:
        line_id = str(selection.source_line_id or "").strip()
        if line_id in seen:
            raise ReturnError("DUPLICATE_LINE", "لا يمكن اختيار نفس البند مرتين.")
        seen.add(line_id)
        line = by_id.get(line_id)
        if line is None:
            raise ReturnError("LINE_NOT_IN_INVOICE", "البند المختار ليس من هذه الفاتورة.")
        returnable, reason, remaining = line_state(line)
        if not returnable:
            code = {EXCHANGED_TEXT: "ALREADY_EXCHANGED", RETURNED_TEXT: "ALREADY_RETURNED",
                    NO_WEIGHT_TEXT: "NO_WEIGHT_AVAILABLE", NOT_SOLD_TEXT: "ITEM_NOT_SOLD"}.get(reason, "LINE_NOT_RETURNABLE")
            raise ReturnError(code, reason)
        sold = Decimal(str(line["weight_grams"]))
        identified = line["line_type"] == "SELL_INVENTORY"
        if identified:
            if selection.weight_grams not in (None, "") and _parse_weight(selection.weight_grams) != sold:
                raise ReturnError("WEIGHT_NOT_PARTIAL", "القطعة المحددة تُرد كاملة بوزنها المباع.")
            weight = sold
        else:
            if selection.weight_grams in (None, ""):
                raise ReturnError("WEIGHT_REQUIRED", "اكتب الوزن المرتجع.")
            weight = _parse_weight(selection.weight_grams)
            if weight > remaining:
                raise ReturnError("WEIGHT_EXCEEDS_REMAINING",
                                  f"الوزن المرتجع {weight:,.3f} أكبر من المتاح {remaining:,.3f} جرام.")
        chosen.append(_Line(
            source_line_id=line_id, line_type=line["line_type"],
            inventory_item_id=str(line["inventory_item_id"]) if identified else None,
            karat_id=int(line["karat_id"]), karat=int(line["karat_value"]), source_weight=sold,
            returned_before=Decimal(str(line.get("returned_grams") or "0")), weight=weight,
            item_name=line.get("item_name_snapshot"),
            barcode=(line.get("stored_barcode") or line.get("barcode_snapshot")) if identified else None,
            historical_making=None if line.get("making_total") is None else Decimal(str(line["making_total"]))))
    return chosen


def _parse_amount(text, code, message):
    try:
        value = Decimal(str(text).strip())
    except (InvalidOperation, ValueError):
        raise ReturnError(code, message) from None
    if not value.is_finite() or value < 0 or value != value.quantize(MONEY):
        raise ReturnError(code, message)
    return value


def settlement(draft, total, customer_id):
    """(legs as (PaymentInput, amount), money paid, account part, problems): real OUT money + the explicit
    customer-account part must equal the Return total exactly; no fallback, no fake zero payment."""
    problems, legs, account = [], [], ZERO
    if draft.account_amount not in (None, ""):
        try:
            account = _parse_amount(draft.account_amount, "ACCOUNT_INVALID", "مبلغ حساب العميل غير صحيح.")
        except ReturnError as error:
            problems.append(error)
    for payment in draft.payments:
        try:
            amount = _parse_amount(payment.amount, "PAYMENT_INVALID", "اكتب المبلغ بالأرقام.")
        except ReturnError as error:
            problems.append(error)
            continue
        if amount == 0:
            problems.append(ReturnError("PAYMENT_INVALID", "المبلغ يجب أن يكون أكبر من صفر."))
            continue
        if payment.direction not in (None, "OUT"):
            problems.append(ReturnError("PAYMENT_DIRECTION", "المرتجع مدفوع للعميل فقط."))
            continue
        legs.append((payment, amount))
    if account and customer_id in (None, ""):
        problems.append(ReturnError("ACCOUNT_NEEDS_CUSTOMER",
                                    "لا يمكن استخدام حساب العميل: الفاتورة الأصلية بدون عميل مسجل."))
    paid = sum((amount for _, amount in legs), ZERO)
    if not problems and paid + account != total:
        remaining = total - paid - account
        problems.append(ReturnError(
            "AMOUNT_MISMATCH" if remaining < 0 else "SETTLEMENT_INCOMPLETE",
            f"المدفوع للعميل وحسابه {paid + account:,.2f} ج لا يساوي إجمالي المرتجع {total:,.2f} ج."))
    return legs, paid, account, problems


def return_summary(row):
    """One stored Return (InvoiceReturnRepository.for_invoice row), read-only, for History."""
    from services.exchange_service import returned_disposition
    status, approval = row["return_status"], row.get("approval") or {}
    pieces = []
    for line in row.get("lines", []):
        identified = line["line_type"] == "SELL_INVENTORY"
        disposition = returned_disposition(line) if identified else {
            "code": "OLD_GOLD", "text": "عاد الوزن إلى رصيد الكسر", "barcode": None, "by": "", "at": None}
        pieces.append({"line_type": line["line_type"], "item_name": line.get("item_name_snapshot"),
                       "barcode": line.get("stored_barcode") or line.get("barcode_snapshot"),
                       "karat": line["karat_value"], "weight_grams": _grams(line["weight_grams"]),
                       "source_weight_grams": _grams(line["source_weight_grams"]),
                       "shop_buy_price_per_gram": _money(line["shop_buy_price_per_gram"]),
                       "price_provider_timestamp": None if line.get("price_provider_timestamp") is None
                       else line["price_provider_timestamp"].isoformat(sep=" "),
                       "gold_amount": _money(line["gold_amount"]), "making_refund": _money(line["making_refund"]),
                       "disposition": disposition})
    at = row.get("finalized_at") or row.get("canceled_at") or row.get("created_at")
    return {"return_id": row["return_id"], "code": return_code(row["return_id"]), "status": status,
            "status_text": STATUS_TEXT.get(status, status), "read_only": True,
            "occurred_at": None if at is None else at.isoformat(sep=" "),
            "actor": row.get("finalized_by_name") or row.get("canceled_by_name") or row.get("created_by_name") or "",
            "making_window_open": bool(row["making_window_open"]), "requires_approval": bool(row["requires_approval"]),
            "approval_status": approval.get("resolution_code") or approval.get("review_status"),
            "approval_by": approval.get("resolved_by_name") or "",
            "age_days": None if row.get("finalized_at") is None
            else (_day(row["finalized_at"]) - _day(row["source_finalized_at"])).days,
            "lines": pieces, "gold_amount": _money(row["gold_amount"]), "making_refund": _money(row["making_refund"]),
            "total_amount": _money(row["total_amount"]), "money_paid": _money(row["money_paid"]),
            "account_amount": _money(row["account_amount"]),
            "payments": [{"method_code": p["payment_method_code"], "amount": _money(p["amount"]),
                          "account_name": p.get("bank_account_name")} for p in row.get("payments", [])],
            "notes": row.get("notes") or ""}


class ReturnService:
    def __init__(self, session, *, gold_prices=None, authorization=None, transaction_factory=invoice_transaction,
                 invoice_repository=InvoiceRepository, return_repository=InvoiceReturnRepository,
                 customer_repository=CustomerRepository):
        self._session = session
        self._gold_prices = gold_prices or GoldPriceService(session)
        self._authorization = authorization or AuthorizationService(session)
        self._transaction = transaction_factory
        self._invoices = invoice_repository
        self._returns = return_repository
        self._customers = customer_repository

    # ------------------------------------------------------------ plumbing
    def _actor(self, expected_actor, permission=RETURN_PERMISSION):
        actor = self._session.require_authenticated()
        if expected_actor is not None and not self._session.is_current(expected_actor):
            raise PermissionError("انتهت جلسة الدخول.")
        if permission not in actor.permissions:
            raise PermissionError("لا توجد صلاحية لإجراء مرتجع." if permission == RETURN_PERMISSION
                                  else "قرار المرتجع للمدير فقط.")
        return actor

    def can_return(self):
        actor = self._session.current_actor()
        return bool(actor and RETURN_PERMISSION in actor.permissions)

    def _read(self, work):
        try:
            with self._transaction() as connection:
                raise _Discard(work(connection))
        except _Discard as done:
            return done.args[0]

    @staticmethod
    def _query(connection, sql, params=()):
        cursor = connection.cursor(dictionary=True)
        try:
            cursor.execute(sql, params)
            return cursor.fetchall()
        finally:
            cursor.close()

    def _clock(self, connection):
        row = self._query(connection, "SELECT NOW() AS local_now, UTC_TIMESTAMP() AS utc_now")[0]
        offset = timedelta(minutes=round((row["local_now"] - row["utc_now"]).total_seconds() / 60))
        if offset not in EGYPT_UTC_OFFSETS:
            raise ReturnError("CLOCK_NOT_EGYPT", "توقيت خادم قاعدة البيانات ليس بتوقيت القاهرة.")
        return row["local_now"].replace(microsecond=0)

    def _karat_ids(self, connection):
        return [int(r["id"]) for r in self._query(connection, "SELECT id FROM karats WHERE is_active = TRUE ORDER BY id")]

    @staticmethod
    def _source(invoice):
        if invoice is None:
            raise ReturnError("INVOICE_NOT_FOUND", "الفاتورة غير موجودة.")
        if invoice["record_origin"] != "NATIVE":
            raise ReturnError("INVOICE_LEGACY", "المرتجع متاح لفواتير النظام الجديدة فقط.")
        if invoice["invoice_status"] != "FINALIZED" or invoice.get("finalized_at") is None:
            raise ReturnError("INVOICE_NOT_FINALIZED", "لا يمكن المرتجع: الفاتورة ليست نهائية.")
        if invoice["invoice_type"] not in ("SELL", "MIXED"):
            raise ReturnError("INVOICE_NOT_SALE", "لا يمكن المرتجع: الفاتورة لا تحتوي على بيع.")
        return invoice

    @staticmethod
    def _window(invoice, now):
        return eligibility(invoice["finalized_at"], invoice.get("invoice_date"), now)

    @staticmethod
    def _approval_valid(header, approval, selection_hash):
        """The approval of exactly this Return and this selection (goods and grams)."""
        return bool(header and approval and header["return_status"] == "APPROVED"
                    and header["selection_hash"] == selection_hash
                    and approval["review_status"] == "RESOLVED" and approval["resolution_code"] == "APPROVED"
                    and (approval.get("details_json") or {}).get("selection_hash") == selection_hash)

    # ------------------------------------------------------------ read side
    def context(self, invoice_id, *, _expected_actor=None):
        """The original invoice for the Return workspace: every sold line with its remaining lifecycle-eligible
        weight (and why not), the windows, current SHOP_BUY, open Returns and payment accounts. Read-only."""
        self._actor(_expected_actor)

        def read(connection):
            invoices, returns = self._invoices(connection), self._returns(connection)
            invoice = invoices.get_invoice(invoice_id)
            if invoice is None:
                return None
            return (invoice, returns.source_lines(invoice_id), returns.open_for_invoice(invoice_id),
                    self._clock(connection), invoices.active_bank_accounts(),
                    self._gold_prices.get_billing_price_set(self._karat_ids(connection)))
        result = self._read(read)
        if result is None:
            raise ReturnError("INVOICE_NOT_FOUND", "الفاتورة غير موجودة.")
        invoice, lines, open_returns, now, accounts, price_set = result
        invoice = self._source(invoice)
        window = self._window(invoice, now)
        rows = []
        for line in lines:
            if line["line_type"] == "BUY_OLD_GOLD":
                continue                                     # the customer's old gold is never part of a Return
            returnable, reason, remaining = line_state(line)
            if window["expired"]:
                returnable, reason = False, EXPIRED_TEXT
            quote = price_set.prices.get(int(line["karat_id"]))
            buy = None if quote is None else Decimal(quote.shop_buy_price_per_gram)
            sold = Decimal(str(line["weight_grams"]))
            rows.append({
                "line_id": line["line_id"], "line_number": line["line_number"], "line_type": line["line_type"],
                "identified": line["line_type"] == "SELL_INVENTORY", "partial_allowed": line["line_type"] == "SELL_OLD_GOLD",
                "item_name": line.get("item_name_snapshot"),
                "barcode": line.get("stored_barcode") or line.get("barcode_snapshot"),
                "karat_id": int(line["karat_id"]), "karat": line["karat_value"], "sold_grams": _grams(sold),
                # Authoritative lifecycle consumption: grams finalized Returns took + a whole piece an Exchange took.
                "consumed_grams": _grams(Decimal(str(line.get("returned_grams") or "0"))
                                         + (sold if line.get("consumed_by_exchange") else ZERO_GRAMS)),
                "remaining_grams": _grams(remaining), "returnable": returnable, "reason": reason,
                "making_refundable": window["making_window_open"] and line.get("making_total") is not None,
                "making_missing": window["making_window_open"] and line.get("making_total") is None,
                "shop_buy": _money(buy),
                "estimated_gold": None if buy is None else _money((remaining * buy).quantize(MONEY, rounding=ROUND_HALF_UP))})
        return {"invoice_id": invoice["invoice_id"], "invoice_number": invoice["invoice_number"],
                "finalized_at": invoice["finalized_at"].isoformat(sep=" "),
                "customer_id": invoice.get("customer_id"), "customer_name": invoice.get("customer_name_snapshot"),
                "has_customer": invoice.get("customer_id") not in (None, ""),
                "making_window_open": window["making_window_open"], "requires_approval": window["requires_approval"],
                "expired": window["expired"], "making_ends_at": window["making_ends_at"].isoformat(sep=" "),
                "last_allowed_at": window["last_allowed_at"].isoformat(sep=" "), "age_days": window["age_days"],
                "window_text": WINDOW_TEXT if window["making_window_open"] else NO_WINDOW_TEXT,
                "lines": rows,
                "open_returns": [{"return_id": r["return_id"], "code": return_code(r["return_id"]),
                                  "status": r["return_status"], "status_text": STATUS_TEXT[r["return_status"]],
                                  "selection": r["selection_json"]} for r in open_returns],
                "bank_accounts": [{"id": a["id"], "name": a["name"], "account_type": a.get("account_type") or "BANK"}
                                  for a in accounts]}

    def preview(self, draft: ReturnDraft, *, _expected_actor=None):
        """Deterministic, read-only recalculation for the screen; nothing is written."""
        self._actor(_expected_actor)

        def read(connection):
            invoices, returns = self._invoices(connection), self._returns(connection)
            now = self._clock(connection)
            price_set = self._gold_prices.get_billing_price_set(self._karat_ids(connection))
            invoice = self._source(invoices.get_invoice(draft.source_invoice_id))
            window = self._window(invoice, now)
            lines = value_lines(resolve_selection(draft.lines, returns.source_lines(draft.source_invoice_id)),
                                price_set, window["making_window_open"])
            header = returns.get_return(draft.return_id) if draft.return_id else None
            approval = returns.latest_approval(draft.return_id) if header else None
            accounts = InvoiceService._payment_accounts(invoices, [(p, None) for p in draft.payments])[3]
            return invoice, now, window, price_set, lines, header, approval, accounts
        invoice, now, window, price_set, lines, header, approval, account_problems = self._read(read)
        gold = sum((l.gold_amount for l in lines), ZERO)
        making = sum((l.making_refund for l in lines), ZERO)
        total = gold + making
        _, paid, account, problems = settlement(draft, total, invoice.get("customer_id"))
        problems = list(problems) + list(account_problems)
        _, selection_hash = fingerprint(lines)
        approval_state = "NOT_REQUIRED"
        if window["expired"]:
            problems.insert(0, ReturnError("RETURN_EXPIRED", EXPIRED_TEXT))
        elif window["requires_approval"]:
            if self._approval_valid(header, approval, selection_hash):
                approval_state = "APPROVED"
            else:
                approval_state = ("PENDING" if header and header["return_status"] == "PENDING_APPROVAL"
                                  and header["selection_hash"] == selection_hash else
                                  "REJECTED" if header and header["return_status"] == "REJECTED"
                                  and header["selection_hash"] == selection_hash else "REQUIRED")
                problems.insert(0, ReturnError("APPROVAL_REQUIRED", APPROVAL_TEXT))
        return {"lines": [self._line_view(l) for l in lines], "gold_amount": _money(gold), "making_refund": _money(making),
                "total_amount": _money(total), "money_paid": _money(paid), "account_amount": _money(account),
                "making_window_open": window["making_window_open"], "requires_approval": window["requires_approval"],
                "expired": window["expired"], "approval_state": approval_state, "selection_hash": selection_hash,
                "price_token": price_set.price_token, "price_status": price_set.status,
                "requires_price_acknowledgement": price_set.requires_acknowledgement,
                "price_warnings": list(price_set.warnings), "now": now.isoformat(sep=" "),
                "blocking_problems": [{"code": p.code, "message": str(p)} for p in problems]}

    @staticmethod
    def _line_view(line):
        return {"source_line_id": line.source_line_id, "line_type": line.line_type,
                "inventory_item_id": line.inventory_item_id, "item_name": line.item_name, "barcode": line.barcode,
                "karat": line.karat, "weight_grams": _grams(line.weight), "source_weight_grams": _grams(line.source_weight),
                "shop_buy_price_per_gram": _money(line.price_per_gram), "gold_amount": _money(line.gold_amount),
                "making_refund": _money(line.making_refund)}

    # ------------------------------------------------------------ pre-finalize states (no effects)
    def save_draft(self, draft: ReturnDraft, *, _expected_actor=None):
        """A DRAFT: the selection only; no money, stock or old-gold effect."""
        return self._store_request(draft, "DRAFT", _expected_actor)

    def request_approval(self, draft: ReturnDraft, *, _expected_actor=None):
        """After 72 hours: PENDING_APPROVAL with one open RETURN_APPROVAL review of exactly this selection.
        The same selection again is idempotent; a changed selection dismisses the old request."""
        return self._store_request(draft, "PENDING_APPROVAL", _expected_actor)

    def _store_request(self, draft, status, expected_actor):
        session_actor = self._actor(expected_actor)
        with self._transaction() as connection:
            actor = self._authorization.require_permission(connection, session_actor, RETURN_PERMISSION)
            invoices, returns = self._invoices(connection), self._returns(connection)
            now = self._clock(connection)
            if invoices.lock_invoice(draft.source_invoice_id) is None:
                raise ReturnError("INVOICE_NOT_FOUND", "الفاتورة غير موجودة.")
            invoice = self._source(invoices.get_invoice(draft.source_invoice_id))
            window = self._window(invoice, now)
            if window["expired"]:
                raise ReturnError("RETURN_EXPIRED", EXPIRED_TEXT)
            if status == "PENDING_APPROVAL" and not window["requires_approval"]:
                raise ReturnError("APPROVAL_NOT_NEEDED", "المرتجع داخل 72 ساعة لا يحتاج موافقة المدير.")
            price_set = self._gold_prices.get_billing_price_set(self._karat_ids(connection))
            lines = value_lines(resolve_selection(draft.lines, returns.source_lines(draft.source_invoice_id)),
                                price_set, window["making_window_open"])
            selection, selection_hash = fingerprint(lines)
            gold = sum((l.gold_amount for l in lines), ZERO)
            making = sum((l.making_refund for l in lines), ZERO)
            notes = (draft.notes or "").strip() or None
            header = returns.get_return(draft.return_id, lock=True) if draft.return_id else None
            if draft.return_id:
                self._check_editable(header, invoice)
                if header["selection_hash"] == selection_hash and header["return_status"] in (status, "APPROVED") \
                        and bool(header["making_window_open"]) == window["making_window_open"]:
                    return {"return_id": header["return_id"], "code": return_code(header["return_id"]),
                            "status": header["return_status"], "changed": False}
                returns.dismiss_open_approvals(return_id=header["return_id"], user_id=actor.user_id, at=now,
                                               notes="تغيّر اختيار المرتجع")
                returns.update_selection(return_id=header["return_id"], status=status, selection=selection,
                                         selection_hash=selection_hash, making_window_open=window["making_window_open"],
                                         gold_amount=gold, making_refund=making, notes=notes)
                return_id = header["return_id"]
            else:
                return_id = returns.insert_header(
                    status=status, source_invoice_id=invoice["invoice_id"], source_finalized_at=invoice["finalized_at"],
                    customer_id=invoice.get("customer_id"), selection=selection, selection_hash=selection_hash,
                    making_window_open=window["making_window_open"], requires_approval=window["requires_approval"],
                    deadline_at=window["deadline_at"], gold_amount=gold, making_refund=making, notes=notes,
                    created_by_user_id=actor.user_id, created_by_employee_id=actor.employee_id, created_at=now)
            code = return_code(return_id)
            if status == "PENDING_APPROVAL":
                returns.open_approval(
                    return_id=return_id, invoice_id=invoice["invoice_id"],
                    summary=f"موافقة مرتجع بعد 72 ساعة — {code} — فاتورة {invoice['invoice_number']}",
                    details={"return_code": code, "selection_hash": selection_hash, "selection": selection,
                             "age_days": window["age_days"], "deadline_at": window["last_allowed_at"],
                             "estimated_gold": gold, "making_refund": ZERO, "notes": notes,
                             "lines": [self._line_view(l) for l in lines]},
                    created_by_user_id=actor.user_id)
            self._audit(connection, actor, "INVOICE_RETURN_" + ("APPROVAL_REQUESTED" if status == "PENDING_APPROVAL"
                                                                  else "DRAFTED"),
                        return_id, code, {"selection": selection, "selection_hash": selection_hash}, now)
            if not self._session.is_current(session_actor):
                raise PermissionError("انتهت جلسة الدخول.")
        return {"return_id": return_id, "code": code, "status": status, "changed": True}

    @staticmethod
    def _check_editable(header, invoice):
        if header is None or str(header["source_invoice_id"]) != str(invoice["invoice_id"]):
            raise ReturnError("RETURN_NOT_FOUND", "المرتجع غير موجود لهذه الفاتورة.")
        if header["return_status"] == "FINALIZED":
            raise ReturnError("RETURN_FINALIZED", "تم المرتجع بالفعل ولا يمكن تعديله.")
        if header["return_status"] == "CANCELED":
            raise ReturnError("RETURN_CANCELED", "هذا المرتجع ملغي.")

    def decide_approval(self, review_id, approve, *, notes=None, _expected_actor=None):
        """Manager (MANAGEMENT_REVIEW): موافقة / رفض of one open RETURN_APPROVAL, bound to its Return's current
        selection. A stale review (selection changed) or an expired Return is refused; no reason required."""
        session_actor = self._actor(_expected_actor, APPROVAL_PERMISSION)
        with self._transaction() as connection:
            actor = self._authorization.require_permission(connection, session_actor, APPROVAL_PERMISSION)
            invoices, returns = self._invoices(connection), self._returns(connection)
            now = self._clock(connection)
            found = returns.approval_by_id(review_id) if str(review_id or "").isdigit() else None
            if found is None:
                raise ReturnError("REVIEW_NOT_FOUND", "طلب الموافقة غير موجود.")
            peek = returns.get_return(found["return_id"])
            invoices.lock_invoice(peek["source_invoice_id"])                  # finalize's lock order
            header = returns.get_return(found["return_id"], lock=True)
            review = returns.latest_approval(found["return_id"], lock=True)
            if review is None or review["review_id"] != found["review_id"] or review["review_status"] != "OPEN":
                raise ReturnError("REVIEW_ALREADY_DECIDED", "تم اتخاذ قرار في هذا الطلب من قبل.")
            if header["return_status"] != "PENDING_APPROVAL" \
                    or (review.get("details_json") or {}).get("selection_hash") != header["selection_hash"]:
                raise ReturnError("REVIEW_STALE", "تغيّر المرتجع بعد الطلب؛ هذا الطلب لم يعد صالحاً.")
            invoice = invoices.get_invoice(header["source_invoice_id"])
            if approve and self._window(invoice, now)["expired"]:
                raise ReturnError("RETURN_EXPIRED", EXPIRED_TEXT + " — لا يمكن الموافقة.")
            code = "APPROVED" if approve else "REJECTED"
            if not returns.resolve_approval(review_id=review["review_id"], resolution_code=code,
                                            resolved_by_user_id=actor.user_id, resolved_at=now):
                raise ReturnError("REVIEW_ALREADY_DECIDED", "تم اتخاذ قرار في هذا الطلب من قبل.")
            if not returns.set_status(return_id=header["return_id"], from_statuses=("PENDING_APPROVAL",), to_status=code):
                raise ReturnError("REVIEW_STALE", "تغيّر المرتجع بعد الطلب؛ هذا الطلب لم يعد صالحاً.")
            self._audit(connection, actor, f"INVOICE_RETURN_{code}", header["return_id"],
                        return_code(header["return_id"]),
                        {"review_id": review["review_id"], "selection_hash": header["selection_hash"],
                         "notes": (notes or "").strip() or None}, now)
            if not self._session.is_current(session_actor):
                raise PermissionError("انتهت جلسة الدخول.")
        return {"return_id": header["return_id"], "code": return_code(header["return_id"]), "status": code}

    def cancel(self, return_id, *, _expected_actor=None):
        """Only before finalize (DRAFT / PENDING_APPROVAL / APPROVED / REJECTED); nothing is deleted.
        An open approval request is dismissed; a decided one stays as history."""
        session_actor = self._actor(_expected_actor)
        with self._transaction() as connection:
            actor = self._authorization.require_permission(connection, session_actor, RETURN_PERMISSION)
            returns = self._returns(connection)
            now = self._clock(connection)
            header = returns.get_return(return_id, lock=True) if str(return_id or "").isdigit() else None
            if header is None:
                raise ReturnError("RETURN_NOT_FOUND", "المرتجع غير موجود.")
            if header["return_status"] == "FINALIZED":
                raise ReturnError("RETURN_FINALIZED_IMMUTABLE",
                                  "لا يمكن إلغاء مرتجع تم؛ التصحيح يكون بعملية عكس لاحقاً.")
            if header["return_status"] == "CANCELED" or not returns.cancel(
                    return_id=header["return_id"], canceled_by_user_id=actor.user_id, canceled_at=now):
                raise ReturnError("RETURN_CANCELED", "هذا المرتجع ملغي بالفعل.")
            returns.dismiss_open_approvals(return_id=header["return_id"], user_id=actor.user_id, at=now,
                                           notes="أُلغي المرتجع قبل التنفيذ")
            self._audit(connection, actor, "INVOICE_RETURN_CANCELED", header["return_id"],
                        return_code(header["return_id"]), {"from_status": header["return_status"]}, now)
        return {"return_id": header["return_id"], "code": return_code(header["return_id"]), "status": "CANCELED"}

    # ------------------------------------------------------------ finalize
    def finalize(self, draft: ReturnDraft, price_token: str, *, stale_acknowledged=False, _expected_actor=None):
        session_actor = self._actor(_expected_actor)
        with self._transaction() as connection:
            actor = self._authorization.require_permission(connection, session_actor, RETURN_PERMISSION)
            if draft.payments:
                self._authorization.require_permission(connection, session_actor, "PAYMENT_RECORD")
            invoices, returns, customers = self._invoices(connection), self._returns(connection), self._customers(connection)
            now = self._clock(connection)
            price_set = self._gold_prices.lock_and_validate_for_finalization(
                connection, price_token, self._karat_ids(connection), acknowledged=stale_acknowledged is True)
            # The original invoice row serializes every Return and Exchange of this invoice.
            if invoices.lock_invoice(draft.source_invoice_id) is None:
                raise ReturnError("INVOICE_NOT_FOUND", "الفاتورة غير موجودة.")
            invoice = self._source(invoices.get_invoice(draft.source_invoice_id))
            window = self._window(invoice, now)
            if window["expired"]:
                raise ReturnError("RETURN_EXPIRED", EXPIRED_TEXT)          # an approval never extends it
            header = returns.get_return(draft.return_id, lock=True) if draft.return_id else None
            if draft.return_id:
                self._check_editable(header, invoice)
                if header["return_status"] == "PENDING_APPROVAL":
                    raise ReturnError("APPROVAL_PENDING", "المرتجع بانتظار موافقة المدير.")
                if header["return_status"] == "REJECTED":
                    raise ReturnError("RETURN_REJECTED", "رفض المدير هذا المرتجع.")
            lines = resolve_selection(draft.lines, returns.source_lines(draft.source_invoice_id))
            selection, selection_hash = fingerprint(lines)
            if window["requires_approval"]:
                approval = returns.latest_approval(header["return_id"], lock=True) if header else None
                if not self._approval_valid(header, approval, selection_hash):
                    raise ReturnError("APPROVAL_REQUIRED", APPROVAL_TEXT + " لهذا الاختيار بالضبط.")
            items = returns.lock_items([l.inventory_item_id for l in lines if l.inventory_item_id])
            for line in lines:
                if line.inventory_item_id and (items.get(line.inventory_item_id) or {}).get("inventory_status") != "SOLD":
                    raise ReturnError("ITEM_NOT_SOLD", NOT_SOLD_TEXT)
            lines = value_lines(lines, price_set, window["making_window_open"])
            gold = sum((l.gold_amount for l in lines), ZERO)
            making = sum((l.making_refund for l in lines), ZERO)
            total = gold + making
            legs, paid, account, problems = settlement(draft, total, invoice.get("customer_id"))
            customer = None
            if account:
                customer = invoices.lock_customer(invoice["customer_id"])
                if customer is None or not customer["is_active"] or customer["record_origin"] == "LEGACY_PLACEHOLDER":
                    problems.append(ReturnError("CUSTOMER_INACTIVE", "حساب العميل غير متاح."))
            methods, accounts, cash_account, payment_problems = InvoiceService._payment_accounts(
                invoices, [(payment, None) for payment, _ in legs], lock=True)
            problems += payment_problems
            if problems:
                raise problems[0] if isinstance(problems[0], ReturnError) else ReturnError(
                    getattr(problems[0], "code", "PAYMENT_INVALID"), str(problems[0]))

            notes = (draft.notes or "").strip() or None
            snapshot = {"price_token": price_set.price_token, "price_status": price_set.status,
                        "source_code": price_set.provenance.get("source_code"), "basis": "SHOP_BUY",
                        "stale_acknowledged": bool(price_set.requires_acknowledgement and stale_acknowledged),
                        "prices": [{"karat_id": l.karat_id, "karat": l.karat, "shop_buy": l.price_per_gram,
                                    "quote_id": l.quote_id, "provider_timestamp": l.provider_timestamp}
                                   for l in {l.karat_id: l for l in lines}.values()]}
            if header is None:
                return_id = returns.insert_header(
                    status="DRAFT", source_invoice_id=invoice["invoice_id"], source_finalized_at=invoice["finalized_at"],
                    customer_id=invoice.get("customer_id"), selection=selection, selection_hash=selection_hash,
                    making_window_open=window["making_window_open"], requires_approval=window["requires_approval"],
                    deadline_at=window["deadline_at"], gold_amount=gold, making_refund=making, notes=notes,
                    created_by_user_id=actor.user_id, created_by_employee_id=actor.employee_id, created_at=now)
            else:
                return_id = header["return_id"]
            code = return_code(return_id)
            details = {"selection": selection, "selection_hash": selection_hash, "age_days": window["age_days"],
                       "making_window_open": window["making_window_open"],
                       "payments": [{"method": p.method_code, "amount": a} for p, a in legs]}
            returns.finalize_header(return_id=return_id, making_window_open=window["making_window_open"],
                                    gold_amount=gold, making_refund=making, money_paid=paid, account_amount=account,
                                    price_snapshot=snapshot, details=details, notes=notes,
                                    finalized_by_user_id=actor.user_id, finalized_at=now)
            review_ids, movement_ids = [], []
            for number, line in enumerate(lines, 1):
                movement_id = None
                if line.line_type == "SELL_OLD_GOLD":
                    # The exact returned grams back into the COUNTED old-gold stock of the same karat.
                    movement_id = returns.add_old_gold_in(
                        karat_id=line.karat_id, weight_grams=line.weight, return_id=return_id,
                        gold_price_per_gram=line.price_per_gram, cash_value=line.gold_amount + line.making_refund,
                        price_quote_id=line.quote_id, customer_id=invoice.get("customer_id"),
                        created_by_user_id=actor.user_id, employee_id=actor.employee_id, notes=line.item_name)
                    movement_ids.append(movement_id)
                line_id = returns.insert_line(return_id=return_id, line_number=number, line=line,
                                              inventory_item_id=line.inventory_item_id, old_gold_movement_id=movement_id)
                if line.inventory_item_id:
                    returns.mark_item_returned_pending(inventory_item_id=line.inventory_item_id,
                                                       updated_by_user_id=actor.user_id)
                    returns.add_inventory_return_movement(item=items[line.inventory_item_id], return_id=return_id,
                                                          performed_by_user_id=actor.user_id,
                                                          performed_by_employee_id=actor.employee_id)
                    review_ids.append(returns.open_inspection(
                        return_line_id=line_id, invoice_id=invoice["invoice_id"],
                        summary=f"فحص قطعة مرتجعة — مرتجع {code}"
                                + (f" — الباركود المسجل {line.barcode}" if line.barcode else ""),
                        details={"source": "RETURN", "return_id": str(return_id), "return_code": code,
                                 "return_line_id": str(line_id), "source_invoice_line_id": line.source_line_id,
                                 "inventory_item_id": line.inventory_item_id, "stored_barcode": line.barcode,
                                 "item_name": line.item_name, "karat": line.karat, "weight_grams": _grams(line.weight)},
                        created_by_user_id=actor.user_id))
            # Real money to the customer only, each leg on its real ledger (a negative balance is allowed).
            for index, (payment, amount) in enumerate(legs):
                method, bank = methods[payment.method_code], accounts.get(index)
                ledger = dict(amount=amount, return_id=return_id, created_by_user_id=actor.user_id, occurred_at=now)
                cash_id = bank_id = None
                if bank is None:
                    cash_id = returns.add_cash_out(cash_account_id=int(cash_account["id"]), **ledger)
                else:
                    bank_id = returns.add_bank_out(bank_account_id=int(bank["id"]), payment_reference=payment.reference,
                                                   **ledger)
                returns.insert_payment(return_id=return_id, payment_method_id=method["payment_method_id"],
                                       bank_account_id=None if bank is None else int(bank["id"]), amount=amount,
                                       payment_reference=payment.reference, cash_movement_id=cash_id,
                                       bank_movement_id=bank_id, occurred_at=now, created_by_user_id=actor.user_id)
            if account:
                customers.add_transaction(
                    customer_id=int(customer["customer_id"]), type_code="RETURN_CREDIT", amount_delta=-account,
                    created_by_user_id=actor.user_id, reference_type="INVOICE_RETURN", reference_id=return_id,
                    reference_code=code, description=f"مرتجع {code} — فاتورة {invoice['invoice_number']}")
            event = {"return_id": return_id, "return_code": code, "total_amount": total, "gold_amount": gold,
                     "making_refund": making, "inspection_review_ids": review_ids, "old_gold_movement_ids": movement_ids}
            invoices.insert_event(invoice_id=invoice["invoice_id"], event_type="RETURNED",
                                  performed_by_user_id=actor.user_id, details=event)
            self._audit(connection, actor, "INVOICE_RETURNED", return_id, code, {**event, **details}, now)
            if not self._session.is_current(session_actor):
                raise PermissionError("انتهت جلسة الدخول.")
        return {"return_id": return_id, "code": code, "occurred_at": now.isoformat(sep=" "),
                "gold_amount": _money(gold), "making_refund": _money(making), "total_amount": _money(total),
                "money_paid": _money(paid), "account_amount": _money(account), "inspection_reviews": len(review_ids)}

    @staticmethod
    def _audit(connection, actor, event_type, return_id, code, details, now):
        cursor = connection.cursor()
        try:
            cursor.execute(
                "INSERT INTO audit_log (user_id, event_type, entity_type, entity_id, entity_reference, "
                "description, metadata_json, occurred_at) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                (actor.user_id, event_type, "INVOICE_RETURN", int(return_id), code, f"مرتجع {code}",
                 json.dumps(details, ensure_ascii=False, default=str), now))
        finally:
            cursor.close()
