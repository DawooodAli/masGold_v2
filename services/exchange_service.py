"""Exchange V1 (استبدال): a lifecycle transaction linked to one finalized invoice.

The original invoice is immutable: an exchange never creates, edits or voids an
invoice row, so it never counts as a sale. It is stored in the 019 lifecycle
tables and appears on the original invoice's timeline as EXCHANGED.

Scope V1: identified stock pieces only (SELL_INVENTORY lines with an inventory
item). Old gold bought, F7 old-gold sales and F5 manual lines are refused. One
original sale line can be consumed once (service check + UNIQUE key).

Valuation (never QML). Karat quotes are NOT assumed proportional; each price is
an actual authoritative quote, never derived from another karat:
  SAME_KARAT — every returned and replacement piece has one karat K:
      delta = replacement weight − returned weight (actual grams),
      valued at K's own SHOP_SELL (delta > 0) or SHOP_BUY (delta < 0).
  PURE_24K   — karats differ: both sides become pure grams
      (pure = weight × karat / 24, 3 decimals per piece),
      delta = replacement_pure − returned_pure, valued at the actual 24K
      SHOP_SELL (delta > 0) or SHOP_BUY (delta < 0).
  delta = 0  → no gold difference, whatever the price did since the sale.
Making, always on a pure-gold basis (M × 24/K per pure gram, weighted by pure
weight), applied to the pure equivalent of the valued delta:
  delta > 0  → replacement making (typed) on the extra gold;
  delta < 0  → the original invoice's historical making, only within
               MAKING_REFUND_WINDOW_HOURS of the original finalize time;
               missing historical making inside the window is refused.

Signed result S: > 0 على العميل, < 0 للعميل, 0 لا يوجد فرق. S is settled by
real money legs (direction-aware, posted to cash / bank in the same
transaction) plus an optional explicit customer-account part; nothing else.
Zero needs no payment and writes none.

Returned piece: SOLD → RETURNED_PENDING (EXCHANGE_RETURN_IN), never IN_STOCK.
Replacement: IN_STOCK → SOLD (EXCHANGE_REPLACEMENT_OUT).
Finalize is one transaction; lock order: actor → prices → original invoice →
inventory items (ascending id) → customer → bank accounts.
"""
from dataclasses import dataclass, field
from datetime import timedelta
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
import json
import re

from repositories.customer_repository import CustomerRepository
from repositories.invoice_lifecycle_repository import InvoiceLifecycleRepository, exchange_code
from repositories.invoice_repository import InvoiceRepository
from services.authorization_service import AuthorizationService
from services.gold_price_service import GoldPriceService
from services.invoice_service import (
    EGYPT_UTC_OFFSETS, IDENTIFICATION_METHODS, OUT_OF_RANGE, InvoiceService, invoice_transaction,
)
from services.invoice_service import REASONS as BILLING_REASONS      # the authoritative F5 reason list


EXCHANGE_PERMISSION = "INVOICE_EXCHANGE"
MAKING_REFUND_WINDOW_HOURS = 72
PURE_KARAT = 24
SAME_KARAT, PURE_24K = "SAME_KARAT", "PURE_24K"
MAX_EXCHANGE_ITEMS = 10
MAKING_PER_GRAM_MAX = Decimal("100000")
GRAM, PURE, RATE, MONEY = Decimal("0.001"), Decimal("0.000001"), Decimal("0.0001"), Decimal("0.01")
ZERO = Decimal("0.00")

CUSTOMER_OWES, SHOP_OWES, NO_DIFFERENCE = "CUSTOMER_OWES", "SHOP_OWES", "NONE"
DIRECTION_TEXT = {CUSTOMER_OWES: "على العميل", SHOP_OWES: "للعميل", NO_DIFFERENCE: "لا يوجد فرق"}
RETURNED_PENDING_TEXT = "القطعة المرتجعة بانتظار الفحص"
REFUND_TEXT = "استرداد مصنعية من الفاتورة الأصلية"
REFUND_EXPIRED_TEXT = "انتهت مدة استرداد المصنعية"
HISTORICAL_MAKING_MISSING_TEXT = "تعذر حساب مصنعية الفاتورة الأصلية بدقة"
# Why a line of the original invoice cannot be exchanged in V1.
LINE_REFUSALS = {
    "BUY_OLD_GOLD": "الكسر المشترى من العميل لا يُستبدل في هذا الإصدار.",
    "SELL_OLD_GOLD": "البيع من الكسر لا يُستبدل في هذا الإصدار.",
    "SELL_UNIDENTIFIED": "البند اليدوي لا يُستبدل في هذا الإصدار: القطعة غير محددة في المخزون.",
    "OTHER": "هذا البند لا يُستبدل.",
}
ALREADY_EXCHANGED_TEXT = "تم استبدال هذه القطعة من قبل."


class ExchangeError(ValueError):
    """A business rule refused the exchange; `code` is stable for the UI."""

    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


class _Discard(Exception):
    """Carries a read result out of a transaction that is always rolled back."""


MANUAL, OLD_GOLD_STOCK = "MANUAL", "OLD_GOLD_STOCK"      # F5 إدخال يدوي / F7 من الكسر (as in Billing)
REPLACEMENT_SOURCES = IDENTIFICATION_METHODS + (MANUAL, OLD_GOLD_STOCK)


@dataclass(frozen=True)
class ReplacementInput:
    """A piece given to the customer, added the same ways Billing adds a sold piece:
    SCAN / SEARCH  an identified stock piece (inventory_item_id);
    MANUAL (F5)    no inventory identity: item type, karat, weight, reason (Billing's REASONS);
    OLD_GOLD_STOCK (F7) weight taken from the COUNTED old-gold stock of a karat.
    making_per_gram (ج/جرام of the piece's own weight) is typed by the employee and used only when
    the customer receives more pure gold."""
    inventory_item_id: str | None = None
    making_per_gram: str | None = None
    identified_by: str = "SCAN"
    karat_id: int | None = None                 # MANUAL / OLD_GOLD_STOCK only
    weight_grams: str | None = None
    item_type_id: int | None = None
    factory_id: int | None = None
    description: str | None = None
    size: str | None = None
    notes: str | None = None
    reason_code: str | None = None              # MANUAL only (Billing F5 reasons)

    @property
    def described(self):
        return self.identified_by in (MANUAL, OLD_GOLD_STOCK)


@dataclass(frozen=True)
class ExchangeDraft:
    source_invoice_id: str
    returned_line_ids: tuple = field(default_factory=tuple)
    replacements: tuple = field(default_factory=tuple)
    payments: tuple = field(default_factory=tuple)     # PaymentInput; direction follows the result
    account_amount: str | None = None                  # explicit customer-account part (positive)
    notes: str | None = None


@dataclass(frozen=True)
class _Piece:
    inventory_item_id: str
    karat_id: int
    karat: int
    weight: Decimal
    pure: Decimal
    item_name: str | None
    barcode: str | None
    source_line_id: str | None = None
    making_per_gram: Decimal | None = None
    making_per_pure_gram: Decimal | None = None
    making_total: Decimal | None = None
    identified_by: str | None = None
    category_id: int | None = None
    # MANUAL / OLD_GOLD_STOCK replacements (never an inventory id)
    factory_name: str | None = None
    size: str | None = None
    notes: str | None = None
    reason_code: str | None = None
    item_type_id: int | None = None
    factory_id: int | None = None

    @property
    def described(self):
        return self.identified_by in (MANUAL, OLD_GOLD_STOCK)


@dataclass(frozen=True)
class _Result:
    returned: tuple
    replacements: tuple
    returned_pure: Decimal
    replacement_pure: Decimal
    pure_delta: Decimal
    valuation_basis: str             # SAME_KARAT | PURE_24K
    valued_delta: Decimal            # actual grams (same karat) or pure grams (mixed): what is priced
    price_basis: str | None          # SHOP_SELL | SHOP_BUY
    price_karat: int | None          # the karat whose actual quote was used
    price_per_gram: Decimal | None
    gold_amount: Decimal
    making_rate: Decimal | None
    making_charge: Decimal
    making_refund: Decimal
    refund_eligible: bool
    window_open: bool
    signed_amount: Decimal
    making_missing: bool = False           # delta > 0 and a replacement's making is not typed yet

    @property
    def direction(self):
        return CUSTOMER_OWES if self.signed_amount > 0 else SHOP_OWES if self.signed_amount < 0 else NO_DIFFERENCE


def pure_weight(weight, karat):
    """Pure (24K-equivalent) grams of one piece, 6 decimals: exact for 18/21/24K (3.500 g 21K = 3.0625)."""
    return (Decimal(weight) * Decimal(int(karat)) / Decimal(24)).quantize(PURE, rounding=ROUND_HALF_UP)


def _grams(value):
    """Grams as text: at least 3 decimals, more only when the value has them (8.750, 3.0625)."""
    if value is None:
        return None
    value = Decimal(value)
    exact = value.normalize()
    places = max(3, -exact.as_tuple().exponent if exact.as_tuple().exponent < 0 else 0)
    return str(value.quantize(Decimal(1).scaleb(-places)))


def _money(value):
    return None if value is None else str(value)


def _is_blank_or_zero(text):
    if text in (None, ""):
        return True
    try:
        return Decimal(str(text).strip()) == 0
    except (InvalidOperation, ValueError):
        return False


def _decimal(text, code, message):
    try:
        value = Decimal(str(text).strip())
    except (InvalidOperation, ValueError):
        raise ExchangeError(code, message) from None
    if not value.is_finite():
        raise ExchangeError(code, message)
    return value


def calculate(returned, replacements, *, price_set, within_window):
    """The deterministic exchange result. `returned` pieces carry their original line's
    historical making_total (None when not stored); `replacements` carry the typed making."""
    returned_pure = sum((p.pure for p in returned), Decimal("0.000"))
    replacement_pure = sum((p.pure for p in replacements), Decimal("0.000"))
    pure_delta = replacement_pure - returned_pure
    karats = {p.karat for p in (*returned, *replacements)}
    if len(karats) == 1:
        valuation, (karat,) = SAME_KARAT, karats
        delta = (sum((p.weight for p in replacements), Decimal("0.000"))
                 - sum((p.weight for p in returned), Decimal("0.000")))
        # The making below works on pure grams: the pure equivalent of the actual-weight delta.
        pure_of_delta = delta * Decimal(karat) / Decimal(PURE_KARAT)
    else:
        valuation, karat, delta, pure_of_delta = PURE_24K, PURE_KARAT, pure_delta, pure_delta
    basis = price = price_karat = rate = None
    gold = charge = refund = ZERO
    eligible = missing = False
    if delta != 0:
        # The actual authoritative quote of that karat; never derived from another karat.
        quote = next((p for p in price_set.prices.values() if int(p.karat) == karat), None)
        if quote is None:
            raise ExchangeError("PRICE_MISSING", f"لا يوجد سعر معتمد لعيار {karat}.")
        basis = "SHOP_SELL" if delta > 0 else "SHOP_BUY"
        price = Decimal(quote.shop_sell_price_per_gram if delta > 0 else quote.shop_buy_price_per_gram)
        price_karat = karat
        gold = (abs(delta) * price).quantize(MONEY, rounding=ROUND_HALF_UP)
    if delta > 0:
        missing = any(p.making_per_gram is None for p in replacements)
        if not missing:
            total = sum((p.making_total for p in replacements), ZERO)
            rate = (total / replacement_pure).quantize(RATE, rounding=ROUND_HALF_UP)
            charge = (pure_of_delta * rate).quantize(MONEY, rounding=ROUND_HALF_UP)
    elif delta < 0 and within_window:
        if any(p.making_total is None for p in returned):
            raise ExchangeError("HISTORICAL_MAKING_MISSING", HISTORICAL_MAKING_MISSING_TEXT)
        total = sum((p.making_total for p in returned), ZERO)
        rate = (total / returned_pure).quantize(RATE, rounding=ROUND_HALF_UP)
        refund = (abs(pure_of_delta) * rate).quantize(MONEY, rounding=ROUND_HALF_UP)
        eligible = True
    signed = gold + charge if delta > 0 else -(gold + refund) if delta < 0 else ZERO
    return _Result(tuple(returned), tuple(replacements), returned_pure, replacement_pure, pure_delta, valuation,
                   delta, basis, price_karat, price, gold, rate, charge, refund, eligible, within_window, signed,
                   missing)


MAKING_REQUIRED_TEXT = "اكتب مصنعية القطعة البديلة (ج/جرام)."
SEARCH_LIMIT = 30


def exchange_summary(row):
    """One stored exchange (InvoiceLifecycleRepository.get_transaction row), read-only, for History."""
    signed = Decimal(str(row["signed_amount"]))
    direction = CUSTOMER_OWES if signed > 0 else SHOP_OWES if signed < 0 else NO_DIFFERENCE

    def piece(line):
        return {"role": line["line_role"], "inventory_item_id": line["inventory_item_id"],
                "item_name": line.get("item_name_snapshot"), "barcode": line.get("barcode_snapshot"),
                "karat": line["karat_value"], "weight_grams": _money(line["weight_grams"]),
                "pure_grams": _grams(line["pure_weight_grams"]),
                "making_per_gram": _money(line.get("making_per_gram")),
                # How a replacement was added: SCAN / SEARCH / MANUAL / OLD_GOLD_STOCK (None for returned pieces).
                "source": line.get("identification_method")}
    return {"lifecycle_id": row["lifecycle_id"], "code": exchange_code(row["lifecycle_id"]), "read_only": True,
            "source_invoice_id": row["source_invoice_id"], "source_invoice_number": row["source_invoice_number"],
            "occurred_at": row["occurred_at"].isoformat(sep=" "), "actor": row.get("performed_by_name") or "",
            "returned": [piece(line) for line in row["lines"] if line["line_role"] == "RETURNED"],
            "replacements": [piece(line) for line in row["lines"] if line["line_role"] == "REPLACEMENT"],
            "returned_pure_grams": _grams(row["returned_pure_grams"]),
            "replacement_pure_grams": _grams(row["replacement_pure_grams"]),
            "pure_delta_grams": _grams(row["pure_delta_grams"]),
            "valuation_basis": row["valuation_basis"], "valued_delta_grams": _grams(row["valued_delta_grams"]),
            "price_basis": row.get("price_basis"), "price_karat": row.get("price_karat_value"),
            "price_per_gram": _money(row.get("price_per_gram")),
            "gold_amount": _money(row["gold_amount"]), "making_charge": _money(row["making_charge"]),
            "making_refund": _money(row["making_refund"]),
            "making_refund_applies": bool(row["making_refund_eligible"]),
            "signed_amount": _money(signed), "amount": _money(abs(signed)), "direction": direction,
            "direction_text": DIRECTION_TEXT[direction],
            "money_received": _money(row["money_received"]), "money_paid": _money(row["money_paid"]),
            "account_signed": _money(row["account_signed"]),
            "payments": [{"method_code": p["payment_method_code"], "direction": p["direction"],
                          "amount": _money(p["amount"]), "account_name": p.get("bank_account_name")}
                         for p in row["payments"]],
            "returned_pending_text": RETURNED_PENDING_TEXT, "notes": row.get("notes") or ""}


def _making_problems(result):
    return [ExchangeError("REPLACEMENT_MAKING_REQUIRED", MAKING_REQUIRED_TEXT)] if result.making_missing else []


class ExchangeService:
    def __init__(self, session, *, gold_prices=None, authorization=None, transaction_factory=invoice_transaction,
                 invoice_repository=InvoiceRepository, lifecycle_repository=InvoiceLifecycleRepository,
                 customer_repository=CustomerRepository):
        self._session = session
        self._gold_prices = gold_prices or GoldPriceService(session)
        self._authorization = authorization or AuthorizationService(session)
        self._transaction = transaction_factory
        self._invoices = invoice_repository
        self._lifecycle = lifecycle_repository
        self._customers = customer_repository

    # ------------------------------------------------------------ helpers

    def _actor(self, expected_actor):
        actor = self._session.require_authenticated()
        if expected_actor is not None and not self._session.is_current(expected_actor):
            raise PermissionError("انتهت جلسة الدخول.")
        # Viewing invoice history never grants an exchange.
        if EXCHANGE_PERMISSION not in actor.permissions:
            raise PermissionError("لا توجد صلاحية لإجراء استبدال.")
        return actor

    def can_exchange(self):
        actor = self._session.current_actor()
        return bool(actor and EXCHANGE_PERMISSION in actor.permissions)

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
            raise ExchangeError("CLOCK_NOT_EGYPT", "توقيت خادم قاعدة البيانات ليس بتوقيت القاهرة.")
        return row["local_now"].replace(microsecond=0)

    def _karat_ids(self, connection):
        return [int(r["id"]) for r in self._query(connection, "SELECT id FROM karats WHERE is_active = TRUE ORDER BY id")]

    @staticmethod
    def _source(invoice):
        if invoice is None:
            raise ExchangeError("INVOICE_NOT_FOUND", "الفاتورة غير موجودة.")
        if invoice["record_origin"] != "NATIVE":
            raise ExchangeError("INVOICE_LEGACY", "الاستبدال متاح لفواتير النظام الجديدة فقط.")
        if invoice["invoice_status"] != "FINALIZED":
            raise ExchangeError("INVOICE_NOT_FINALIZED", "لا يمكن الاستبدال: الفاتورة ليست نهائية.")
        if invoice["invoice_type"] not in ("SELL", "MIXED"):
            raise ExchangeError("INVOICE_NOT_SALE", "لا يمكن الاستبدال: الفاتورة لا تحتوي على بيع.")
        if invoice.get("finalized_at") is None:
            raise ExchangeError("INVOICE_NOT_FINALIZED", "لا يمكن الاستبدال: وقت اعتماد الفاتورة غير معروف.")
        return invoice

    @staticmethod
    def _line_status(line):
        """None when the line can be exchanged, else the Arabic reason."""
        if line["line_type"] != "SELL_INVENTORY" or line.get("inventory_item_id") in (None, ""):
            return LINE_REFUSALS.get(line["line_type"], LINE_REFUSALS["OTHER"])
        if line.get("consumed_by"):
            return ALREADY_EXCHANGED_TEXT
        return None

    def _pieces(self, draft, lines, items, invoices=None, karats=None):
        """Validated returned / replacement pieces from DB rows (`items`: inventory rows by id)."""
        if not draft.returned_line_ids:
            raise ExchangeError("RETURNED_REQUIRED", "اختر القطعة المرتجعة من الفاتورة.")
        if not draft.replacements:
            raise ExchangeError("REPLACEMENT_REQUIRED", "أضف القطعة البديلة.")
        if len(draft.returned_line_ids) > MAX_EXCHANGE_ITEMS or len(draft.replacements) > MAX_EXCHANGE_ITEMS:
            raise ExchangeError("TOO_MANY_ITEMS", f"الحد الأقصى {MAX_EXCHANGE_ITEMS} قطع في كل جهة.")
        by_id = {str(line["line_id"]): line for line in lines}
        chosen = [str(value).strip() for value in draft.returned_line_ids]
        if len(set(chosen)) != len(chosen):
            raise ExchangeError("DUPLICATE_RETURNED", "لا يمكن إرجاع نفس القطعة مرتين.")
        returned = []
        for line_id in chosen:
            line = by_id.get(line_id)
            if line is None:
                raise ExchangeError("LINE_NOT_IN_INVOICE", "البند المختار ليس من هذه الفاتورة.")
            reason = self._line_status(line)
            if reason:
                raise ExchangeError("ALREADY_EXCHANGED" if reason == ALREADY_EXCHANGED_TEXT else "LINE_NOT_EXCHANGEABLE",
                                    reason)
            item = items.get(str(line["inventory_item_id"]))
            if item is None or item["inventory_status"] != "SOLD":
                raise ExchangeError("RETURNED_NOT_SOLD", "القطعة المرتجعة ليست في حالة مباعة؛ لا يمكن استبدالها.")
            weight = Decimal(str(line["weight_grams"]))
            pure = pure_weight(weight, line["karat_value"])
            making = None if line.get("making_total") is None else Decimal(str(line["making_total"]))
            returned.append(_Piece(
                inventory_item_id=str(line["inventory_item_id"]), karat_id=int(line["karat_id"]),
                karat=int(line["karat_value"]), weight=weight, pure=pure, item_name=line.get("item_name_snapshot"),
                barcode=line.get("barcode_snapshot"), source_line_id=line_id,
                making_per_gram=None if line.get("making_per_gram") is None else Decimal(str(line["making_per_gram"])),
                making_per_pure_gram=None if making is None else (making / pure).quantize(RATE, rounding=ROUND_HALF_UP),
                making_total=making))
        returned_ids = {p.inventory_item_id for p in returned}
        seen, replacements, described = set(), [], []
        for entry in draft.replacements:
            if entry.identified_by not in REPLACEMENT_SOURCES:
                raise ExchangeError("IDENTIFICATION_INVALID", "طريقة تحديد القطعة غير صحيحة.")
            if entry.described:
                piece, resolved = self._described_piece(entry, invoices, karats)
                replacements.append(piece)
                described.append(resolved)
                continue
            item_id = str(entry.inventory_item_id or "").strip()
            if item_id in seen:
                raise ExchangeError("DUPLICATE_REPLACEMENT", "لا يمكن إضافة نفس القطعة البديلة مرتين.")
            seen.add(item_id)
            if item_id in returned_ids:
                raise ExchangeError("REPLACEMENT_IS_RETURNED", "القطعة البديلة هي نفسها القطعة المرتجعة.")
            if entry.identified_by not in IDENTIFICATION_METHODS:
                raise ExchangeError("IDENTIFICATION_INVALID", "طريقة تحديد القطعة غير صحيحة.")
            item = items.get(item_id)
            if item is None:
                raise ExchangeError("ITEM_NOT_FOUND", f"القطعة رقم {item_id} غير موجودة.")
            if item["inventory_status"] != "IN_STOCK":
                raise ExchangeError("ITEM_UNAVAILABLE", f"القطعة رقم {item_id} غير متاحة في المخزون.")
            weight = Decimal(str(item["weight_grams"]))
            pure = pure_weight(weight, item["karat_value"])
            per_gram = total = per_pure = None
            if entry.making_per_gram not in (None, ""):
                per_gram = _decimal(entry.making_per_gram, "MAKING_INVALID", "اكتب المصنعية بالأرقام.")
                if per_gram < 0 or per_gram > MAKING_PER_GRAM_MAX:
                    raise ExchangeError("MAKING_INVALID", "المصنعية يجب أن تكون صفراً أو أكثر.")
                per_gram = per_gram.quantize(MONEY, rounding=ROUND_HALF_UP)
                total = (per_gram * weight).quantize(MONEY, rounding=ROUND_HALF_UP)
                per_pure = (per_gram * Decimal(24) / Decimal(int(item["karat_value"]))).quantize(
                    RATE, rounding=ROUND_HALF_UP)
            replacements.append(_Piece(
                inventory_item_id=item_id, karat_id=int(item["karat_id"]), karat=int(item["karat_value"]),
                weight=weight, pure=pure, item_name=item.get("item_type_name"), barcode=item.get("barcode_value"),
                making_per_gram=per_gram, making_per_pure_gram=per_pure, making_total=total,
                identified_by=entry.identified_by, category_id=item.get("category_id")))
        return returned, replacements, described

    @staticmethod
    def _making(entry, weight, karat):
        """(per gram, total, per pure gram) of a typed replacement making, or (None, None, None)."""
        if entry.making_per_gram in (None, ""):
            return None, None, None
        per_gram = _decimal(entry.making_per_gram, "MAKING_INVALID", "اكتب المصنعية بالأرقام.")
        if per_gram < 0 or per_gram > MAKING_PER_GRAM_MAX:
            raise ExchangeError("MAKING_INVALID", "المصنعية يجب أن تكون صفراً أو أكثر.")
        per_gram = per_gram.quantize(MONEY, rounding=ROUND_HALF_UP)
        return (per_gram, (per_gram * weight).quantize(MONEY, rounding=ROUND_HALF_UP),
                (per_gram * Decimal(24) / Decimal(int(karat))).quantize(RATE, rounding=ROUND_HALF_UP))

    @staticmethod
    def _described_piece(entry, invoices, karats):
        """A MANUAL (F5) or OLD_GOLD_STOCK (F7) replacement, validated by Billing's own rules
        (InvoiceService._described: item type, factory, text limits, F5 reasons incl. «سبب آخر» notes)."""
        from types import SimpleNamespace
        from services.invoice_service import OLD_GOLD_STOCK as BILLING_F7, UNIDENTIFIED, DescribedSaleInput
        if isinstance(entry.karat_id, bool) or not str(entry.karat_id or "").isdigit():
            raise ExchangeError("KARAT_REQUIRED", "اختر العيار.")
        karat = (karats or {}).get(int(entry.karat_id))
        if karat is None:
            raise ExchangeError("PRICE_MISSING", "لا يوجد سعر معتمد لهذا العيار.")
        weight = _decimal(entry.weight_grams, "WEIGHT_INVALID", "اكتب الوزن بالأرقام.")
        if weight <= 0 or weight != weight.quantize(GRAM) or weight > Decimal("100000"):
            raise ExchangeError("WEIGHT_INVALID", "اكتب وزناً صحيحاً (حتى 3 أرقام عشرية).")
        weight = weight.quantize(GRAM)                       # 6 → 6.000, exactly as stored
        billing_kind = UNIDENTIFIED if entry.identified_by == MANUAL else BILLING_F7
        line = DescribedSaleInput(kind=billing_kind, karat_id=int(entry.karat_id), weight_grams=str(weight),
                                  item_type_id=entry.item_type_id or None, factory_id=entry.factory_id or None,
                                  description=entry.description, size=entry.size, notes=entry.notes,
                                  reason_code=entry.reason_code if entry.identified_by == MANUAL else None)
        try:
            (resolved,) = InvoiceService._described(None, invoices, SimpleNamespace(described_sale_lines=(line,)))
        except ValueError as error:                      # InvoiceError: the same wording as Billing
            raise ExchangeError(getattr(error, "code", "LINE_INVALID"), str(error)) from None
        per_gram, total, per_pure = ExchangeService._making(entry, weight, karat)
        piece = _Piece(
            inventory_item_id=None, karat_id=int(entry.karat_id), karat=int(karat), weight=weight,
            pure=pure_weight(weight, karat), item_name=resolved["item_name"], barcode=None,
            making_per_gram=per_gram, making_per_pure_gram=per_pure, making_total=total,
            identified_by=entry.identified_by, category_id=resolved["category_id"],
            factory_name=resolved["factory_name"], size=resolved["size"], notes=resolved["notes"],
            reason_code=line.reason_code, item_type_id=resolved["item_type_id"], factory_id=resolved["factory_id"])
        return piece, resolved

    @staticmethod
    def _old_gold_shortages(described, balances):
        """F7 grams requested per karat vs the COUNTED stock BEFORE this exchange (Billing's own rule:
        nothing returned in the same exchange can be reused — returned pieces are inventory, never old gold)."""
        return InvoiceService._old_gold_shortages(described, balances)

    @staticmethod
    def _shortage_error(shortages, karats):
        karat_id, available, grams = shortages[0]
        return ExchangeError("OLD_GOLD_STOCK_INSUFFICIENT",
                             f"رصيد الكسر عيار {(karats or {}).get(karat_id, karat_id)} غير كافٍ: "
                             f"المتاح {available:,.3f} جرام والمطلوب {grams:,.3f} جرام.")

    def _settlement(self, draft, result, customer_id):
        """(money legs as (PaymentInput, direction, amount), received, paid, account_signed, problems)."""
        problems, legs = [], []
        signed = result.signed_amount
        direction = "IN" if signed > 0 else "OUT"
        account = ZERO
        if signed == 0:
            # No difference: nothing is owed, so no money leg and no account entry (never a fake payment).
            if draft.payments or not _is_blank_or_zero(draft.account_amount):
                problems.append(ExchangeError("NO_DIFFERENCE_NO_MONEY", "لا يوجد فرق؛ لا تسجل أي مبلغ."))
            return [], ZERO, ZERO, ZERO, problems
        if draft.account_amount not in (None, ""):
            try:
                account = _decimal(draft.account_amount, "ACCOUNT_INVALID", "اكتب مبلغ الحساب بالأرقام.")
            except ExchangeError as error:
                problems.append(error)
            if account < 0 or account != account.quantize(MONEY):
                problems.append(ExchangeError("ACCOUNT_INVALID", "مبلغ الحساب غير صحيح."))
                account = ZERO
        for payment in draft.payments:
            try:
                amount = _decimal(payment.amount, "PAYMENT_INVALID", "اكتب المبلغ بالأرقام.")
            except ExchangeError as error:
                problems.append(error)
                continue
            if amount <= 0 or amount != amount.quantize(MONEY):
                problems.append(ExchangeError("PAYMENT_INVALID", "المبلغ يجب أن يكون أكبر من صفر."))
                continue
            if payment.direction not in (None, direction):
                problems.append(ExchangeError("PAYMENT_DIRECTION", "اتجاه المبلغ لا يطابق فرق الاستبدال."))
                continue
            legs.append((payment, direction, amount))
        if account and customer_id in (None, ""):
            problems.append(ExchangeError("ACCOUNT_NEEDS_CUSTOMER",
                                          "لا يمكن استخدام حساب العميل: الفاتورة الأصلية بدون عميل مسجل."))
        money = sum((amount for _, _, amount in legs), ZERO)
        if not problems and money + account != abs(signed):
            problems.append(ExchangeError(
                "AMOUNT_MISMATCH",
                f"المبالغ المسجلة {money + account:,.2f} ج لا تساوي فرق الاستبدال {abs(signed):,.2f} ج."))
        received, paid = (money, ZERO) if signed > 0 else (ZERO, money)
        return legs, received, paid, account if signed > 0 else -account, problems

    @staticmethod
    def _making_review(result, ranges):
        """Replacement making outside its category range → one management review (never blocks)."""
        if result.valued_delta <= 0:
            return []
        out = []
        for piece in result.replacements:
            row = ranges.get(piece.category_id) if piece.category_id is not None else None
            if not row or piece.making_per_gram is None:           # not typed yet: a blocking problem already
                continue
            low, high = row.get("sale_making_min_per_gram"), row.get("sale_making_max_per_gram")
            if low is None or high is None:
                continue
            if not Decimal(str(low)) <= piece.making_per_gram <= Decimal(str(high)):
                out.append({"inventory_item_id": piece.inventory_item_id, "category": row.get("name"),
                            "making_per_gram": piece.making_per_gram, "min": low, "max": high,
                            "status": OUT_OF_RANGE})
        return out

    @staticmethod
    def _within_window(invoice, now):
        return now <= invoice["finalized_at"] + timedelta(hours=MAKING_REFUND_WINDOW_HOURS)

    @staticmethod
    def _piece_view(piece):
        return {"inventory_item_id": piece.inventory_item_id, "source_line_id": piece.source_line_id,
                "item_name": piece.item_name, "barcode": piece.barcode, "karat": piece.karat,
                "weight_grams": _money(piece.weight), "pure_grams": _grams(piece.pure),
                "making_per_gram": _money(piece.making_per_gram),
                "making_per_pure_gram": _money(piece.making_per_pure_gram)}

    def _view(self, result, invoice, now, price_set):
        within = result.window_open
        return {
            "returned": [self._piece_view(p) for p in result.returned],
            "replacements": [self._piece_view(p) for p in result.replacements],
            "returned_pure_grams": _grams(result.returned_pure),
            "replacement_pure_grams": _grams(result.replacement_pure),
            "pure_delta_grams": _grams(result.pure_delta),
            "valuation_basis": result.valuation_basis,
            "valued_delta_grams": _grams(result.valued_delta),
            "price_basis": result.price_basis,
            "price_basis_text": {"SHOP_SELL": "سعر بيع المحل", "SHOP_BUY": "سعر شراء المحل"}.get(result.price_basis, ""),
            "price_karat": result.price_karat,
            "price_per_gram": _money(result.price_per_gram),
            "gold_amount": _money(result.gold_amount),
            "making_rate_per_pure_gram": _money(result.making_rate),
            "making_charge": _money(result.making_charge),
            "making_required": result.valued_delta > 0,
            "making_missing": result.making_missing,
            "making_refund": _money(result.making_refund),
            "making_refund_applies": result.refund_eligible,
            "making_refund_text": REFUND_TEXT if result.refund_eligible else "",
            # Shown only when a refund would have applied but the window has passed.
            "making_refund_expired": result.valued_delta < 0 and not within,
            "making_refund_expired_text": REFUND_EXPIRED_TEXT if result.valued_delta < 0 and not within else "",
            "refund_window_hours": MAKING_REFUND_WINDOW_HOURS,
            "refund_window_ends_at": (invoice["finalized_at"] + timedelta(hours=MAKING_REFUND_WINDOW_HOURS)).isoformat(sep=" "),
            "signed_amount": _money(result.signed_amount),
            "amount": _money(abs(result.signed_amount)),
            "direction": result.direction,
            "direction_text": DIRECTION_TEXT[result.direction],
            "money_direction": "IN" if result.signed_amount > 0 else "OUT" if result.signed_amount < 0 else None,
            "returned_pending_text": RETURNED_PENDING_TEXT,
            "price_token": price_set.price_token, "price_status": price_set.status,
            "requires_price_acknowledgement": price_set.requires_acknowledgement,
            "price_warnings": list(price_set.warnings),
            "now": now.isoformat(sep=" "),
        }

    # ------------------------------------------------------------ read side

    def context(self, invoice_id, *, _expected_actor=None):
        """The original invoice for the Exchange workspace: every sale line with whether it can be
        exchanged (and why not), the customer and the refund window. Read-only."""
        self._actor(_expected_actor)

        def read(connection):
            invoices = self._invoices(connection)
            invoice = invoices.get_invoice(invoice_id)
            lines = self._lifecycle(connection).source_lines(invoice_id) if invoice else []
            return (invoice, lines, self._clock(connection), invoices.active_bank_accounts(), invoices.reference_lists(),
                    invoices.old_gold_balances())
        invoice, lines, now, accounts, lists, balances = self._read(read)
        invoice = self._source(invoice)
        rows = []
        for line in lines:
            if line["line_type"] == "BUY_OLD_GOLD":
                continue
            reason = self._line_status(line)
            weight = Decimal(str(line["weight_grams"]))
            rows.append({"line_id": line["line_id"], "line_number": line["line_number"],
                         "line_type": line["line_type"], "inventory_item_id": line.get("inventory_item_id"),
                         "item_name": line.get("item_name_snapshot"), "barcode": line.get("barcode_snapshot"),
                         "karat": line["karat_value"], "weight_grams": _money(weight),
                         "pure_grams": _grams(pure_weight(weight, line["karat_value"])),
                         "exchangeable": reason is None, "reason": reason or "",
                         "exchanged_by": line.get("consumed_by")})
        return {"invoice_id": invoice["invoice_id"], "invoice_number": invoice["invoice_number"],
                "finalized_at": invoice["finalized_at"].isoformat(sep=" "),
                "customer_id": invoice.get("customer_id"),
                "customer_name": invoice.get("customer_name_snapshot") or "",
                "has_customer": invoice.get("customer_id") not in (None, ""),
                "refund_window_open": self._within_window(invoice, now),
                "refund_window_hours": MAKING_REFUND_WINDOW_HOURS,
                # Billing's own lists for F5 (manual) and F7 (from old gold) replacements.
                "item_types": [{"id": int(r["id"]), "name": r["name"]} for r in lists["item_types"]],
                "factories": [{"id": int(r["id"]), "name": r["name"]} for r in lists["factories"]],
                "old_gold_stock": [{"karat_id": int(r["karat_id"]), "karat": int(r["karat_value"]),
                                    "grams": _money(Decimal(str(r["balance_grams"])))} for r in balances],
                "reasons": [{"code": code, "text": text} for code, text in BILLING_REASONS.items()],
                "bank_accounts": [{"id": str(r["id"]), "name": r["name"],
                                   "account_type": r.get("account_type") or "BANK"} for r in accounts],
                "lines": rows}

    def find_replacement_by_barcode(self, barcode, *, _expected_actor=None):
        """The IN_STOCK piece carrying this ACTIVE barcode (read-only)."""
        self._actor(_expected_actor)
        barcode = (barcode or "").strip()
        if not barcode:
            raise ExchangeError("BARCODE_REQUIRED", "أدخل الباركود.")

        def lookup(connection):
            invoices = self._invoices(connection)
            item_id = invoices.find_item_id_by_barcode(barcode)
            if item_id is None:
                raise ExchangeError("BARCODE_NOT_FOUND", f"لا توجد قطعة بالباركود {barcode}.")
            return invoices.read_sale_items([item_id])[0]
        item = self._read(lookup)
        if item["inventory_status"] != "IN_STOCK":
            raise ExchangeError("ITEM_UNAVAILABLE", "القطعة غير متاحة في المخزون.")
        weight = Decimal(str(item["weight_grams"]))
        return {"inventory_item_id": item["inventory_item_id"], "barcode": item.get("barcode_value"),
                "item_name": item.get("item_type_name"), "karat": item.get("karat_value"),
                "weight_grams": _money(weight), "pure_grams": _grams(pure_weight(weight, item["karat_value"]))}

    def detail(self, lifecycle_id, *, _expected_actor=None):
        """One exchange, read-only (History)."""
        actor = self._session.require_authenticated()
        if _expected_actor is not None and not self._session.is_current(_expected_actor):
            raise PermissionError("انتهت جلسة الدخول.")
        if "INVOICE_VIEW" not in actor.permissions and EXCHANGE_PERMISSION not in actor.permissions:
            raise PermissionError("لا توجد صلاحية لعرض سجل الفواتير.")
        row = self._read(lambda connection: self._lifecycle(connection).get_transaction(lifecycle_id))
        if row is None:
            raise ExchangeError("EXCHANGE_NOT_FOUND", "عملية الاستبدال غير موجودة.")
        return exchange_summary(row)

    def search_replacements(self, *, text="", karat_id=None, weight=None, tolerance="0.05", exclude_ids=(),
                            _expected_actor=None):
        """Candidates for a replacement piece: IN_STOCK only (Billing's own search query), read-only.
        Never chooses: the employee selects the physical piece."""
        self._actor(_expected_actor)
        weight_min = weight_max = target = None
        if weight not in (None, ""):
            try:
                target, spread = Decimal(str(weight).strip()), Decimal(str(tolerance or "0").strip())
            except InvalidOperation:
                raise ExchangeError("SEARCH_WEIGHT_INVALID", "اكتب الوزن بالأرقام.") from None
            if not target.is_finite() or target <= 0 or not spread.is_finite() or not 0 <= spread <= 5:
                raise ExchangeError("SEARCH_WEIGHT_INVALID", "اكتب وزناً صحيحاً وهامشاً من 0 إلى 5 جرام.")
            weight_min, weight_max = target - spread, target + spread
        rows = self._read(lambda connection: self._invoices(connection).search_sellable_items(
            text=(text or "").strip(), karat_id=int(karat_id) if karat_id else None, weight_min=weight_min,
            weight_max=weight_max, exclude_ids=tuple(exclude_ids), weight_target=target,
            limit=SEARCH_LIMIT + 1))
        items = [{"inventory_item_id": row["inventory_item_id"], "barcode": row.get("barcode_value"),
                  "item_name": row.get("item_type_name"), "karat": row.get("karat_value"),
                  "weight_grams": _money(Decimal(str(row["weight_grams"]))),
                  "pure_grams": _grams(pure_weight(Decimal(str(row["weight_grams"])), row["karat_value"])),
                  "factory_name": row.get("factory_name"), "size": row.get("size")}
                 for row in rows[:SEARCH_LIMIT]]
        return {"items": items, "truncated": len(rows) > SEARCH_LIMIT}

    # ------------------------------------------------------------ preview

    def preview(self, draft: ExchangeDraft, *, _expected_actor=None):
        """Deterministic, read-only recalculation for the screen; nothing is written."""
        self._actor(_expected_actor)

        def read(connection):
            invoices, lifecycle = self._invoices(connection), self._lifecycle(connection)
            now = self._clock(connection)
            price_set = self._gold_prices.get_billing_price_set(self._karat_ids(connection))
            invoice = self._source(invoices.get_invoice(draft.source_invoice_id))
            lines = lifecycle.source_lines(draft.source_invoice_id)
            ids = self._item_ids(draft, lines)
            items = {row["inventory_item_id"]: row for row in (invoices.read_sale_items(ids) if ids else [])}
            karats = {int(p.karat_id): int(p.karat) for p in price_set.prices.values()}
            returned, replacements, described = self._pieces(draft, lines, items, invoices, karats)
            result = calculate(returned, replacements, price_set=price_set,
                               within_window=self._within_window(invoice, now))
            ranges = invoices.category_ranges([p.category_id for p in replacements])
            legs, received, paid, account, problems = self._settlement(draft, result, invoice.get("customer_id"))
            problems = _making_problems(result) + problems
            if any(p.identified_by == OLD_GOLD_STOCK for p in replacements):
                balances = {int(r["karat_id"]): Decimal(str(r["balance_grams"])) for r in invoices.old_gold_balances()}
                shortages = self._old_gold_shortages(described, balances)
                if shortages:
                    problems.insert(0, self._shortage_error(shortages, karats))
            problems += InvoiceService._payment_accounts(
                invoices, [(payment, None) for payment, _, _ in legs])[3]
            return invoice, now, price_set, result, ranges, (received, paid, account), problems
        invoice, now, price_set, result, ranges, (received, paid, account), problems = self._read(read)
        review = self._making_review(result, ranges)
        return {**self._view(result, invoice, now, price_set),
                "money_received": _money(received), "money_paid": _money(paid), "account_signed": _money(account),
                "making_review_required": bool(review), "making_review": review,
                "blocking_problems": [{"code": p.code, "message": str(p)} for p in problems]}

    @staticmethod
    def _item_ids(draft, lines):
        by_id = {str(line["line_id"]): line for line in lines}
        ids = [str(by_id[str(v).strip()]["inventory_item_id"]) for v in draft.returned_line_ids
               if str(v).strip() in by_id and by_id[str(v).strip()].get("inventory_item_id") not in (None, "")]
        # ASCII digits only (str.isdigit() would accept Arabic-Indic digits the repository refuses).
        ids += [str(r.inventory_item_id).strip() for r in draft.replacements
                if re.fullmatch(r"[0-9]{1,20}", str(r.inventory_item_id).strip())]
        unique = []
        for value in ids:
            if value not in unique:
                unique.append(value)
        return unique

    # ------------------------------------------------------------ finalize

    def finalize(self, draft: ExchangeDraft, price_token: str, *, stale_acknowledged=False, _expected_actor=None):
        session_actor = self._actor(_expected_actor)
        with self._transaction() as connection:
            actor = self._authorization.require_permission(connection, session_actor, EXCHANGE_PERMISSION)
            if draft.payments:
                self._authorization.require_permission(connection, session_actor, "PAYMENT_RECORD")
            invoices, lifecycle = self._invoices(connection), self._lifecycle(connection)
            customers = self._customers(connection)
            now = self._clock(connection)
            price_set = self._gold_prices.lock_and_validate_for_finalization(
                connection, price_token, self._karat_ids(connection), acknowledged=stale_acknowledged is True)
            # The original invoice row serializes concurrent exchanges of the same invoice.
            if invoices.lock_invoice(draft.source_invoice_id) is None:
                raise ExchangeError("INVOICE_NOT_FOUND", "الفاتورة غير موجودة.")
            invoice = self._source(invoices.get_invoice(draft.source_invoice_id))
            lines = lifecycle.source_lines(draft.source_invoice_id)
            ids = self._item_ids(draft, lines)
            items = {row["inventory_item_id"]: row for row in (invoices.lock_sale_items(ids) if ids else [])}
            karats = {int(p.karat_id): int(p.karat) for p in price_set.prices.values()}
            returned, replacements, described = self._pieces(draft, lines, items, invoices, karats)
            # F7: lock each karat's COUNTED old-gold ledger and read the balance BEFORE this exchange writes.
            balances = {karat_id: invoices.lock_old_gold_balance(karat_id)
                        for karat_id in sorted({p.karat_id for p in replacements if p.identified_by == OLD_GOLD_STOCK})}
            shortages = self._old_gold_shortages(described, balances)
            if shortages:
                raise self._shortage_error(shortages, karats)
            result = calculate(returned, replacements, price_set=price_set,
                               within_window=self._within_window(invoice, now))
            legs, received, paid, account, problems = self._settlement(draft, result, invoice.get("customer_id"))
            problems = _making_problems(result) + problems
            customer = None
            if account != 0:
                customer = invoices.lock_customer(invoice["customer_id"])
                if customer is None or not customer["is_active"] or customer["record_origin"] == "LEGACY_PLACEHOLDER":
                    problems.append(ExchangeError("CUSTOMER_INACTIVE", "حساب العميل غير متاح."))
            methods, accounts, cash_account, payment_problems = InvoiceService._payment_accounts(
                invoices, [(payment, None) for payment, _, _ in legs], lock=True)
            problems += payment_problems
            if problems:
                raise problems[0]
            review = self._making_review(result, invoices.category_ranges([p.category_id for p in replacements]))

            snapshot = {
                "price_token": price_set.price_token, "price_status": price_set.status,
                "source_code": price_set.provenance.get("source_code"),
                "valuation_basis": result.valuation_basis, "price_karat": result.price_karat,
                "price_basis": result.price_basis, "price_per_gram": result.price_per_gram,
                "stale_acknowledged": bool(price_set.requires_acknowledgement and stale_acknowledged),
                "prices": [{"karat_id": p.karat_id, "karat": p.karat, "shop_buy": p.shop_buy_price_per_gram,
                            "shop_sell": p.shop_sell_price_per_gram, "quote_id": p.quote_id,
                            "provider_timestamp": None if p.provider_timestamp is None
                            else p.provider_timestamp.replace(tzinfo=None)}
                           for p in sorted(price_set.prices.values(), key=lambda p: p.karat)],
            }
            details = {"returned_item_ids": [p.inventory_item_id for p in returned],
                       "replacement_item_ids": [p.inventory_item_id for p in replacements if not p.described],
                       # How each replacement was added (audit / History / review): SCAN, SEARCH, MANUAL, OLD_GOLD_STOCK.
                       "replacement_sources": [p.identified_by for p in replacements],
                       "refund_window_hours": MAKING_REFUND_WINDOW_HOURS, "refund_window_open": result.window_open,
                       "payments": [{"method": p.method_code, "direction": d, "amount": a} for p, d, a in legs]}
            if review:
                details["making_review"] = review
            notes = (draft.notes or "").strip() or None
            lifecycle_id = lifecycle.insert_transaction(
                source_invoice_id=invoice["invoice_id"], source_finalized_at=invoice["finalized_at"],
                customer_id=invoice.get("customer_id"), calculation=result, money_received=received,
                money_paid=paid, account_signed=account, price_snapshot=snapshot, details=details, notes=notes,
                performed_by_user_id=actor.user_id, performed_by_employee_id=actor.employee_id, occurred_at=now)
            code = exchange_code(lifecycle_id)
            number, case_ids = 0, []
            for role, pieces in (("RETURNED", returned), ("REPLACEMENT", replacements)):
                for piece in pieces:
                    number += 1
                    movement_id = None
                    if piece.identified_by == OLD_GOLD_STOCK:
                        # F7: the exact weight leaves the COUNTED old-gold stock (its own exchange movement).
                        quote = price_set.prices.get(piece.karat_id)
                        movement_id = lifecycle.add_old_gold_replacement_out(
                            karat_id=piece.karat_id, weight_grams=piece.weight, lifecycle_id=lifecycle_id,
                            gold_price_per_gram=None if quote is None else quote.shop_sell_price_per_gram,
                            price_quote_id=None if quote is None else quote.quote_id,
                            customer_id=invoice.get("customer_id"), created_by_user_id=actor.user_id,
                            employee_id=actor.employee_id, notes=piece.item_name)
                    line_id = lifecycle.insert_line(lifecycle_id=lifecycle_id, line_role=role, line_number=number,
                                                    line=piece, old_gold_movement_id=movement_id)
                    if piece.identified_by == MANUAL:
                        # F5 parity: the same UNIDENTIFIED_SALE reconciliation case (manager review) as Billing.
                        from services.invoice_service import REASONS
                        case_ids.append(lifecycle.open_unidentified_replacement_case(
                            lifecycle_line_id=line_id, exchange_code=code, item_type_id=piece.item_type_id,
                            karat_id=piece.karat_id, factory_id=piece.factory_id, category_id=piece.category_id,
                            weight_grams=piece.weight, size=piece.size, reason_code=piece.reason_code,
                            reason=REASONS[piece.reason_code], notes=piece.notes, created_by_user_id=actor.user_id,
                            salesperson_employee_id=actor.employee_id, sold_at=now))
            for piece in returned:
                lifecycle.mark_item_returned_pending(inventory_item_id=piece.inventory_item_id,
                                                     updated_by_user_id=actor.user_id)
                lifecycle.add_inventory_movement(
                    item=items[piece.inventory_item_id], movement_code="EXCHANGE_RETURN_IN", from_status="SOLD",
                    to_status="RETURNED_PENDING", lifecycle_id=lifecycle_id, performed_by_user_id=actor.user_id,
                    performed_by_employee_id=actor.employee_id)
            for piece in replacements:
                if piece.described:
                    continue                  # no inventory piece: F5 has its case, F7 its old-gold movement
                invoices.mark_item_sold(inventory_item_id=piece.inventory_item_id, updated_by_user_id=actor.user_id)
                lifecycle.add_inventory_movement(
                    item=items[piece.inventory_item_id], movement_code="EXCHANGE_REPLACEMENT_OUT",
                    from_status="IN_STOCK", to_status="SOLD", lifecycle_id=lifecycle_id,
                    performed_by_user_id=actor.user_id, performed_by_employee_id=actor.employee_id)
            # Real money only, each leg posted to its real ledger in this transaction.
            for index, (payment, direction, amount) in enumerate(legs):
                method, bank = methods[payment.method_code], accounts.get(index)
                ledger = dict(direction=direction, amount=amount, lifecycle_id=lifecycle_id,
                              created_by_user_id=actor.user_id, occurred_at=now)
                cash_id = bank_id = None
                if bank is None:
                    cash_id = lifecycle.add_cash_movement(cash_account_id=int(cash_account["id"]), **ledger)
                else:
                    bank_id = lifecycle.add_bank_movement(bank_account_id=int(bank["id"]),
                                                          payment_reference=payment.reference, **ledger)
                lifecycle.insert_payment(
                    lifecycle_id=lifecycle_id, payment_method_id=method["payment_method_id"],
                    bank_account_id=None if bank is None else int(bank["id"]), direction=direction, amount=amount,
                    payment_reference=payment.reference, cash_movement_id=cash_id, bank_movement_id=bank_id,
                    occurred_at=now, created_by_user_id=actor.user_id)
            if account != 0:
                customers.add_transaction(
                    customer_id=int(customer["customer_id"]),
                    type_code="EXCHANGE_BALANCE_DUE" if account > 0 else "EXCHANGE_CREDIT",
                    amount_delta=account, created_by_user_id=actor.user_id,
                    reference_type="INVOICE_LIFECYCLE", reference_id=lifecycle_id, reference_code=code,
                    description=f"فرق استبدال {code} — فاتورة {invoice['invoice_number']}")
            if review:
                invoices.insert_management_review(
                    review_type="MAKING_PRICE", invoice_id=invoice["invoice_id"],
                    summary=f"مصنعية خارج النطاق — استبدال {code}",
                    details={"lifecycle_id": lifecycle_id, "exchange_code": code, "lines": review},
                    created_by_user_id=actor.user_id)
            event = {"lifecycle_id": lifecycle_id, "exchange_code": code, "signed_amount": result.signed_amount,
                     "unidentified_case_ids": case_ids,
                     "pure_delta_grams": result.pure_delta, "valued_delta_grams": result.valued_delta,
                     "valuation_basis": result.valuation_basis, "direction": result.direction}
            invoices.insert_event(invoice_id=invoice["invoice_id"], event_type="EXCHANGED",
                                  performed_by_user_id=actor.user_id, details=event)
            self._audit(connection, actor, lifecycle_id, code, {**event, **details}, now)
            if not self._session.is_current(session_actor):
                raise PermissionError("انتهت جلسة الدخول.")
        return {"lifecycle_id": lifecycle_id, "code": code, "occurred_at": now.isoformat(sep=" "),
                "signed_amount": _money(result.signed_amount), "direction": result.direction,
                "direction_text": DIRECTION_TEXT[result.direction], "making_review_required": bool(review),
                "unidentified_cases": len(case_ids)}

    @staticmethod
    def _audit(connection, actor, lifecycle_id, code, details, now):
        cursor = connection.cursor()
        try:
            cursor.execute(
                "INSERT INTO audit_log (user_id, event_type, entity_type, entity_id, entity_reference, "
                "description, metadata_json, occurred_at) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                (actor.user_id, "INVOICE_EXCHANGED", "INVOICE_LIFECYCLE", int(lifecycle_id), code,
                 f"استبدال {code}", json.dumps(details, ensure_ascii=False, default=str), now))
        finally:
            cursor.close()
