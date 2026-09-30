"""Canonical Billing V2: preview and atomic finalization of native invoices.

The draft carries only what an employee chooses or measures: inventory item
ids, making charges, old-gold karat/weight/discount, payments, customer and
salesperson. Everything else (item karat and weight, gold prices, every total,
N/P/R) is recalculated here with InvoiceCalculator from the database, so totals
coming from QML are never trusted.

Finalization is one transaction. Lock order: actor/user rows -> salesperson ->
price settings/active prices -> customer (a new customer is created here) ->
inventory items (ascending id) -> old-gold karat ledgers (ascending karat) ->
the day's invoice-number sequence. Any failure rolls everything back.

Commercial truth: the salesman never types a sale price. The real money
decides it:
    effective_sale_total = O + P_in − P_out
  O = net old gold bought, P = real money received / paid. The customer
  account is a separate ledger instruction (على العميل + / للعميل −) that never
  changes the price, the making, the average or the money direction.
  Making = effective sale − sold gold value, allocated by weight; the average
  making per gram and the category review follow every change of money.
  * one money direction per invoice (IN: the customer pays, OUT: the shop pays);
    payment legs may be split across methods, never offset;
  * every real payment is posted in the same transaction to its real ledger:
    physical cash to cash_movements, wallet / InstaPay / bank to bank_movements
    on the selected, typed account;
  * old gold only: nothing can absorb a rounding gap, so it is settled
    explicitly (limits, approval) or put on the account;
  * with any new-gold sale line the old-gold discount must be 1.25–3.00%;
  * F5 unidentified sales open a reconciliation case; F7 sales from old-gold
    stock deduct COUNTED grams; reviews never change price, balance or invoice.

Times: business date and stored invoice times come from the MySQL server clock
(Egypt local, the app's existing CURRENT_TIMESTAMP convention); a finalize is
refused if the server is not on Egypt time. Provider timestamps in price
snapshots stay UTC, as in the Gold Price Engine.
"""
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import timedelta
from decimal import Decimal, InvalidOperation
import uuid

from config.database_config import DatabaseConfig
from database.connection import DatabaseConnection
from repositories.customer_repository import CustomerRepository
from repositories.dead_cash_repository import DeadCashRepository
from repositories.authentication_repository import AuthenticationRepository
from repositories.invoice_repository import InvoiceRepository
from services.authorization_service import AuthorizationService
from services.gold_price_service import GoldPriceService
from services.invoice_calculator import ZERO, InvoiceCalculationError, InvoiceCalculator as Calc


# Real money only. INSTAPAY / BANK_TRANSFER need a configured, active bank account.
ALLOWED_PAYMENT_METHODS = ("PHYSICAL_CASH", "MOBILE_WALLET", "INSTAPAY", "BANK_TRANSFER")
METHOD_TEXT = {"PHYSICAL_CASH": "نقدي", "MOBILE_WALLET": "محفظة", "INSTAPAY": "InstaPay",
               "BANK_TRANSFER": "تحويل بنكي"}
# The account type each non-cash method posts to (bank_accounts.account_type, 017).
ACCOUNT_TYPE_FOR_METHOD = {"MOBILE_WALLET": "MOBILE_WALLET", "INSTAPAY": "BANK", "BANK_TRANSFER": "BANK"}
# Shown while a supported method has no active account of its type (the method stays visible).
NO_ACCOUNT_TEXT = {"MOBILE_WALLET": "لا توجد محفظة مفعلة", "BANK": "لا يوجد حساب بنكي مفعّل"}
WITHIN_RANGE, OUT_OF_RANGE, NOT_CONFIGURED = "WITHIN_RANGE", "OUT_OF_RANGE", "NOT_CONFIGURED"
# Old-gold discount allowed on any invoice that also sells new gold (hard rule).
SALE_DISCOUNT_MIN, SALE_DISCOUNT_MAX = Decimal("1.25"), Decimal("3.00")
CUSTOMER_OWES, SHOP_OWES = "CUSTOMER_OWES", "SHOP_OWES"
ACCOUNT_DIRECTIONS = (CUSTOMER_OWES, SHOP_OWES)
# Egypt standard time and summer time.
EGYPT_UTC_OFFSETS = (timedelta(hours=2), timedelta(hours=3))


class _DiscardPreview(Exception):
    """Raised inside preview's transaction so the factory rolls it back."""


# One printed A5 bill holds at most 10 sale rows (the prototype's rule): sold pieces, manual pieces
# and sales from the old-gold stock count; old gold bought from the customer does not.
MAX_SALE_ITEMS = 10
MAX_SALE_ITEMS_TEXT = "الحد الأقصى للفاتورة 10 أصناف بيع"
DUPLICATE_SALE_ITEM_TEXT = "لا يمكن بيع نفس القطعة مرتين في الفاتورة نفسها"


class InvoiceError(ValueError):
    """A business rule refused the invoice; `code` is stable for the UI."""

    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


IDENTIFICATION_METHODS = ("SCAN", "SEARCH")
ITEM_SEARCH_LIMIT = 30


@dataclass(frozen=True)
class SaleLineInput:
    """A sold stock piece. Its price is never typed: the real money decides it."""
    inventory_item_id: str
    # How the employee identified the piece: SCAN (barcode) or SEARCH (بحث عن قطعة).
    identified_by: str = "SCAN"


UNIDENTIFIED = "UNIDENTIFIED"            # F5 إدخال يدوي
OLD_GOLD_STOCK = "OLD_GOLD_STOCK"        # F7 بيع من الكسر
# F5 = the exact inventory piece could not be identified (a piece found through F3 is a normal sale).
REASONS = {"NO_BARCODE": "لم يُنشأ لها باركود", "BARCODE_LOST": "الباركود مفقود أو تالف",
           "NOT_FOUND": "لم أجد القطعة في البحث", "OTHER": "سبب آخر"}
SETTLE, ACCOUNT = "SETTLE", "ACCOUNT"
DEFAULT_LIMITS = {"settlement_self_limit": Decimal("50.00"), "settlement_approval_limit": Decimal("500.00"),
                  "old_gold_discount_default_percent": Decimal("1.50")}


@dataclass(frozen=True)
class DescribedSaleInput:
    """A sold piece described by the employee (F5) or sold from old-gold stock (F7).

    Never carries an inventory id: F5 opens a reconciliation case, F7 is a
    weight-ledger sale. The description is invoice information only."""
    kind: str
    karat_id: int
    weight_grams: str
    item_type_id: int | None = None
    factory_id: int | None = None
    description: str | None = None
    size: str | None = None
    notes: str | None = None
    reason_code: str | None = None           # F5 only


@dataclass(frozen=True)
class NewCustomerInput:
    """Typed in Billing; created only inside the finalize transaction."""
    name: str
    mobile: str | None = None
    place: str | None = None


@dataclass(frozen=True)
class SettlementApprover:
    """A manager verified at the counter (username + password), never from QML data."""
    user_id: int
    display_name: str
    _proof: object = field(repr=False, compare=False, default=None)


_APPROVER_PROOF = object()


@dataclass(frozen=True)
class OldGoldLineInput:
    karat_id: int
    weight_grams: str
    discount_type: str | None = None
    discount_value: str | None = None
    notes: str | None = None


@dataclass(frozen=True)
class PaymentInput:
    method_code: str
    amount: str
    direction: str | None = None          # inferred from the invoice net when omitted
    reference: str | None = None
    bank_account_id: str | None = None    # the real receiving/paying account (INSTAPAY / BANK_TRANSFER)


@dataclass(frozen=True)
class InvoiceDraft:
    salesperson_employee_id: int
    customer_id: str | None = None
    sale_lines: tuple = field(default_factory=tuple)
    old_gold_lines: tuple = field(default_factory=tuple)
    payments: tuple = field(default_factory=tuple)
    notes: str | None = None
    described_sale_lines: tuple = field(default_factory=tuple)
    payment_direction: str | None = None          # IN | OUT; chosen only for an exchange
    account_amount: str | None = None             # customer-ledger amount (positive), never part of the price
    account_direction: str | None = None          # CUSTOMER_OWES (على العميل, +) | SHOP_OWES (للعميل, −)
    settlement_choice: str | None = None          # SETTLE: old-gold-only rounding gap
    settlement_reason: str | None = None
    settlement_approver: SettlementApprover | None = None
    new_customer: NewCustomerInput | None = None
    # إجمالي الفاتورة: the employee's authoritative commercial total (positive, in the invoice's
    # direction). With sold pieces it IS the price (making = it − gold value); the real payment legs
    # and the customer-account part only ALLOCATE it and must add up to it exactly.
    invoice_total: str | None = None


@contextmanager
def invoice_transaction():
    """One MySQL transaction on the server's local (Egypt) session time zone."""
    connection = DatabaseConnection(DatabaseConfig.from_env()).connect()
    try:
        cursor = connection.cursor()
        try:
            cursor.execute("SET SESSION innodb_lock_wait_timeout = 10")
        finally:
            cursor.close()
        connection.start_transaction()
        yield connection
        connection.commit()
    except BaseException:
        connection.rollback()
        raise
    finally:
        connection.close()


def _money(value):
    return None if value is None else str(value)


def _is_zero(text):
    """An account amount of 0 means no customer-ledger movement at all."""
    try:
        return Decimal(str(text).strip()) == 0
    except (InvalidOperation, ValueError):
        return False


@dataclass(frozen=True)
class _Calculated:
    sales: list
    olds: list
    payments: list
    settlement: object
    agreed: object                 # allocation of the derived making (None when not valid / no sale)
    direction: str                 # IN | OUT
    gold_value: Decimal            # sold gold value (zero making)
    old_total: Decimal             # O
    effective_sale: Decimal | None
    making_total: Decimal | None
    account_signed: Decimal        # customer-ledger delta of this invoice (independent of the price)
    adjustment: Decimal            # A (old gold only)
    gap: Decimal | None            # old gold only: R − L; None when something is sold
    problems: list


class InvoiceService:
    def __init__(self, session, *, gold_prices=None, authorization=None,
                 transaction_factory=invoice_transaction,
                 invoice_repository=InvoiceRepository,
                 customer_repository=CustomerRepository,
                 dead_cash_repository=DeadCashRepository,
                 approver_repository=AuthenticationRepository, authentication=None):
        self._session = session
        self._approvers = approver_repository
        self._authentication = authentication
        self._gold_prices = gold_prices or GoldPriceService(session)
        self._authorization = authorization or AuthorizationService(session)
        self._transaction = transaction_factory
        self._invoices = invoice_repository
        self._customers = customer_repository
        self._dead_cash = dead_cash_repository

    # ------------------------------------------------------------ helpers

    def _actor(self, expected_actor):
        """The session's actor; a background worker passes the actor that started
        the request, which must still be the current login."""
        actor = self._session.require_authenticated()
        if expected_actor is not None and not self._session.is_current(expected_actor):
            raise PermissionError("انتهت جلسة الدخول.")
        return actor

    def _read_only(self, work):
        """Run work(connection) in a transaction that is always rolled back."""
        try:
            with self._transaction() as connection:
                raise _DiscardPreview(work(connection))
        except _DiscardPreview as done:
            return done.args[0]

    @staticmethod
    def _query(connection, sql, params=()):
        cursor = connection.cursor(dictionary=True)
        try:
            cursor.execute(sql, params)
            return cursor.fetchall()
        finally:
            cursor.close()

    def _business_clock(self, connection):
        row = self._query(connection, "SELECT NOW() AS local_now, UTC_TIMESTAMP() AS utc_now")[0]
        offset = row["local_now"] - row["utc_now"]
        # Round to the minute: the two functions are read in the same statement.
        offset = timedelta(minutes=round(offset.total_seconds() / 60))
        if offset not in EGYPT_UTC_OFFSETS:
            raise InvoiceError("CLOCK_NOT_EGYPT",
                               "توقيت خادم قاعدة البيانات ليس بتوقيت القاهرة؛ لا يمكن ترقيم الفاتورة.")
        return row["local_now"].replace(microsecond=0)

    def _karat_ids(self, connection):
        return [int(r["id"]) for r in self._query(
            connection, "SELECT id FROM karats WHERE is_active = TRUE ORDER BY id")]

    def _salesperson(self, connection, employee_id, lock):
        if employee_id is None or isinstance(employee_id, bool):
            raise InvoiceError("SALESPERSON_REQUIRED", "يجب اختيار البائع.")
        rows = self._query(connection,
                           "SELECT id, name, is_active FROM employees WHERE id = %s"
                           + (" FOR SHARE" if lock else ""), (int(employee_id),))
        if not rows or not rows[0]["is_active"]:
            raise InvoiceError("SALESPERSON_INACTIVE", "البائع غير موجود أو غير نشط.")
        return rows[0]

    def _customer(self, invoices, customers, dead_cash, customer_id, lock):
        """(customer row, balance, dead-cash warning active) or (None, None, False)."""
        if customer_id in (None, ""):
            return None, None, False
        if lock:
            customer = invoices.lock_customer(customer_id)
        else:
            row = customers.get_customer_by_id(int(customer_id))
            customer = None if row is None else {
                "customer_id": str(row["customer_id"]), "customer_code": row["customer_code"],
                "name": row["name"], "mobile": row["mobile"], "place": row["place"],
                "is_active": row["is_active"], "record_origin": row["record_origin"],
                "customer_type_name": row.get("customer_type_name")}
        if customer is None:
            raise InvoiceError("CUSTOMER_NOT_FOUND", "العميل غير موجود.")
        if customer["record_origin"] == "LEGACY_PLACEHOLDER":
            raise InvoiceError("CUSTOMER_PLACEHOLDER",
                               "حساب عميل قديم غير مسجل؛ يُستخدم لتسوية الرصيد القديم فقط.")
        if not customer["is_active"]:
            raise InvoiceError("CUSTOMER_INACTIVE", "العميل غير نشط.")
        balance = customers.get_balance(int(customer["customer_id"]))
        warning = dead_cash.get_active_warning(int(customer["customer_id"])) is not None
        return customer, balance, warning

    @staticmethod
    def _require_unique_items(draft):
        """One physical piece can be sold once per invoice: the same inventory item (whatever path
        added it — barcode or search) may not appear twice. Same attributes on different pieces are
        fine; F5 / F7 lines have no piece identity and are not checked here."""
        ids = [int(line.inventory_item_id) for line in draft.sale_lines
               if line.inventory_item_id not in (None, "")]
        if len(ids) != len(set(ids)):
            raise InvoiceError("DUPLICATE_SALE_ITEM", DUPLICATE_SALE_ITEM_TEXT)

    def _items(self, invoices, draft):
        self._require_unique_items(draft)
        for line in draft.sale_lines:
            if line.identified_by not in IDENTIFICATION_METHODS:
                raise InvoiceError("IDENTIFICATION_INVALID", "طريقة تحديد القطعة غير صحيحة.")
        ids = [line.inventory_item_id for line in draft.sale_lines]
        items = invoices.lock_sale_items(ids) if ids else []
        found = {item["inventory_item_id"]: item for item in items}
        for item_id in ids:
            item = found.get(str(int(item_id)))
            if item is None:
                raise InvoiceError("ITEM_NOT_FOUND", f"القطعة رقم {item_id} غير موجودة.")
            if item["inventory_status"] != "IN_STOCK":
                raise InvoiceError("ITEM_UNAVAILABLE", f"القطعة رقم {item_id} غير متاحة للبيع.")
        return [found[str(int(i))] for i in ids]

    def _described(self, invoices, draft):
        """Validate F5 / F7 lines and resolve their item type / factory names."""
        resolved = []
        for line in draft.described_sale_lines:
            if line.kind not in (UNIDENTIFIED, OLD_GOLD_STOCK):
                raise InvoiceError("LINE_KIND_INVALID", "نوع البند غير صحيح.")
            if isinstance(line.karat_id, bool) or not str(line.karat_id).isdigit():
                raise InvoiceError("KARAT_REQUIRED", "اختر العيار.")
            for value, label, limit in ((line.description, "الوصف", 150), (line.size, "المقاس", 50),
                                        (line.notes, "الملاحظات", 1000)):
                if value is not None and (not isinstance(value, str) or len(value) > limit):
                    raise InvoiceError("TEXT_INVALID", f"{label}: الحد الأقصى {limit} حرفاً.")
            item_type = invoices.get_item_type(line.item_type_id) if line.item_type_id else None
            if line.item_type_id and (item_type is None or not item_type["is_active"]):
                raise InvoiceError("ITEM_TYPE_INVALID", "الصنف غير موجود.")
            factory = invoices.get_factory(line.factory_id) if line.factory_id else None
            if line.factory_id and (factory is None or not factory["is_active"]):
                raise InvoiceError("FACTORY_INVALID", "المصنع غير موجود.")
            description = (line.description or "").strip()
            name = " — ".join(v for v in ((item_type or {}).get("name"), description) if v)
            if line.kind == UNIDENTIFIED:
                if item_type is None:
                    raise InvoiceError("ITEM_TYPE_REQUIRED", "اختر الصنف للبيع اليدوي.")
                if line.reason_code not in REASONS:
                    raise InvoiceError("REASON_REQUIRED", "اختر سبب البيع اليدوي.")
                if line.reason_code == "OTHER" and not (line.notes or "").strip():
                    raise InvoiceError("REASON_NOTES_REQUIRED", "اكتب السبب في الملاحظات عند اختيار «سبب آخر».")
            elif not name:
                raise InvoiceError("DESCRIPTION_REQUIRED", "اكتب وصف القطعة المباعة من الكسر.")
            resolved.append({"input": line, "item_name": name, "item_type_id": line.item_type_id,
                             "factory_id": line.factory_id, "factory_name": (factory or {}).get("name"),
                             "category_id": (item_type or {}).get("default_category_id"),
                             "karat_id": int(line.karat_id), "size": (line.size or "").strip() or None,
                             "notes": (line.notes or "").strip() or None})
        return resolved

    def _direction(self, draft, has_sales, gold_value, old_total):
        """The invoice's one money direction: IN (customer pays) or OUT (shop pays).

        Sale only → IN; old gold only → OUT; an exchange follows the employee's
        explicit choice, else the gold-value baseline (sold gold vs old gold)."""
        if has_sales and not draft.old_gold_lines:
            return "IN"
        if not has_sales:
            return "OUT"
        if draft.payment_direction in ("IN", "OUT"):
            return draft.payment_direction
        return "IN" if gold_value >= old_total else "OUT"

    def _calculate(self, draft, items, price_set, described=(), limits=None):
        """Authoritative commercial result from DB rows, the locked prices and real money.

        effective_sale_total = O + P + L
        (O = net old gold, P = signed real money now, L = signed customer-account part:
        على العميل > 0, للعميل < 0). L is the DEFERRED part of the same commercial amount,
        never extra money: the ledgers receive P only, the customer ledger receives L.
        Making = effective sale − sold gold value, allocated by weight; never typed."""
        limits = limits or DEFAULT_LIMITS
        prices = price_set.prices
        specs = []                 # (weight, sell price) for every sold line, in line order
        for item in items:
            price = prices.get(int(item["karat_id"]))
            if price is None:
                raise InvoiceError("PRICE_MISSING", "لا يوجد سعر معتمد لعيار القطعة.")
            specs.append((item["weight_grams"], price.shop_sell_price_per_gram))
        for line in described:
            price = prices.get(line["karat_id"])
            if price is None:
                raise InvoiceError("PRICE_MISSING", "لا يوجد سعر معتمد لهذا العيار.")
            specs.append((line["input"].weight_grams, price.shop_sell_price_per_gram))
        olds = []
        for line in draft.old_gold_lines:
            if isinstance(line.karat_id, bool) or int(line.karat_id) not in prices:
                raise InvoiceError("PRICE_MISSING", "لا يوجد سعر معتمد لعيار الكسر.")
            olds.append(Calc.old_gold_line(line.weight_grams,
                                           prices[int(line.karat_id)].shop_buy_price_per_gram,
                                           discount_type=line.discount_type,
                                           discount_value=line.discount_value))
        # Gold value only (zero making): the baseline for the exchange direction.
        gold_lines = [Calc.sale_line(weight, price, making_total="0") for weight, price in specs]
        gold_value = sum((line.gold_value for line in gold_lines), ZERO)
        old_total = sum((line.line_total for line in olds), ZERO)
        has_sales = bool(specs)
        direction = self._direction(draft, has_sales, gold_value, old_total)

        payments = []
        for payment in draft.payments:
            if payment.method_code not in ALLOWED_PAYMENT_METHODS:
                raise InvoiceError("PAYMENT_METHOD_NOT_ALLOWED",
                                   "طريقة الدفع غير متاحة (متاح: نقدي، محفظة، InstaPay، تحويل بنكي).")
            if payment.direction not in (None, direction):
                raise InvoiceError("MIXED_PAYMENT_DIRECTIONS", "كل مبالغ الفاتورة في اتجاه واحد.")
            payments.append((payment, Calc.payment(direction, payment.amount)))
        money = [p for _, p in payments]
        paid_in = sum((p.amount for p in money if p.direction == "IN"), ZERO)
        paid_out = sum((p.amount for p in money if p.direction == "OUT"), ZERO)
        # Customer account: the deferred part of the commercial amount (same direction as the money).
        account = ZERO
        if draft.account_amount not in (None, "") and not _is_zero(draft.account_amount):
            try:
                account = Calc.payment("IN", draft.account_amount).amount
            except InvoiceCalculationError:
                raise InvoiceError("ACCOUNT_AMOUNT_INVALID", "مبلغ الحساب غير صحيح.") from None
        account_problem = None
        if account and draft.account_direction not in ACCOUNT_DIRECTIONS:
            # Reported (the numbers stay on screen); nothing is posted until a direction is chosen.
            account, account_problem = ZERO, InvoiceError("ACCOUNT_DIRECTION_REQUIRED", "اختر: على العميل أو للعميل.")
        account_signed = account if draft.account_direction == CUSTOMER_OWES else -account
        problems, sales, agreed, adjustment = [account_problem] if account_problem else [], gold_lines, None, ZERO

        if has_sales:
            # The account part follows the settlement direction: على العميل when the customer pays,
            # للعميل when the shop pays. Opposite adjustments belong to the customer workflows.
            if account_signed and (account_signed > 0) != (direction == "IN"):
                problems.append(InvoiceError(
                    "ACCOUNT_DIRECTION_MISMATCH",
                    "في هذه الفاتورة يكون الحساب " + ("على العميل" if direction == "IN" else "للعميل") + " فقط."))
                account_signed = ZERO
            allocated = paid_in - paid_out + account_signed         # P + L (signed)
            total = None
            if draft.invoice_total not in (None, "") and not _is_zero(draft.invoice_total):
                try:
                    total = Calc.payment("IN", draft.invoice_total).amount
                except InvoiceCalculationError:
                    raise InvoiceError("INVOICE_TOTAL_INVALID", "إجمالي الفاتورة غير صحيح.") from None
            if total is not None:
                # The authoritative total: payment splits never change the price or the making.
                total_signed = total if direction == "IN" else -total
                if (allocated and (allocated > 0) != (total_signed > 0)) or abs(allocated) > total:
                    problems.append(InvoiceError(
                        "PAYMENT_ABOVE_TOTAL", "مجموع طرق الدفع وحساب العميل لا يمكن أن يتجاوز إجمالي الفاتورة."))
                elif allocated != total_signed:
                    problems.append(InvoiceError(
                        "PAYMENT_ALLOCATION_INCOMPLETE",
                        f"المتبقي من إجمالي الفاتورة {total - abs(allocated):,.2f} ج: أضف طريقة دفع "
                        "أو ضعه على حساب العميل."))
                effective = old_total + total_signed
            else:
                # Commercial money = actual payment now + deferred account part (P + L).
                effective = old_total + allocated
            making = effective - gold_value
            if effective <= 0:
                problems.append(InvoiceError("PAYMENT_REQUIRED", "أدخل المبلغ الفعلي المستلم من العميل أو المدفوع له."))
            elif making < 0:
                problems.append(InvoiceError(
                    "SALE_BELOW_GOLD", f"المبلغ الفعلي يجعل سعر البيع ({effective:,.2f} ج) أقل من قيمة الذهب "
                                       f"({gold_value:,.2f} ج)."))
            else:
                agreed = Calc.agreed_sale(specs, effective)
                sales = [Calc.sale_line(weight, price, making_total=making)
                         for (weight, price), making in zip(specs, agreed.allocations)]
            try:
                settlement = Calc.settlement(sales, olds, money)
            except InvoiceCalculationError:
                settlement = Calc.settlement(sales, olds)        # display only; a problem already blocks
        else:
            effective = making = None
            try:
                settlement = Calc.settlement(sales, olds, money)
            except InvoiceCalculationError as error:
                problems.append(InvoiceError(error.code, str(error)))
                settlement = Calc.settlement(sales, olds)
            residual = settlement.residual_signed
            if account_signed and (residual == 0 or (account_signed > 0) != (residual > 0)
                                   or abs(account_signed) > abs(residual)):
                # Old gold only: the account part is the unpaid old-gold value owed TO the customer.
                problems.append(InvoiceError("ACCOUNT_AMOUNT_INVALID",
                                             "في شراء الكسر فقط: المبلغ للعميل ولا يزيد عن المتبقي له."))
                gap = ZERO
            else:
                # Old gold only: no sale can absorb a gap, so R − L is settled explicitly (limits apply).
                gap = residual - account_signed
                if gap and draft.settlement_choice == SETTLE and abs(gap) <= limits["settlement_approval_limit"]:
                    adjustment = gap
                    settlement = Calc.settlement(sales, olds, money, adjustment=adjustment)
        return _Calculated(sales=sales, olds=olds, payments=payments, settlement=settlement, agreed=agreed,
                           direction=direction, gold_value=gold_value, old_total=old_total,
                           effective_sale=effective, making_total=making, account_signed=account_signed,
                           adjustment=adjustment, gap=None if has_sales else gap, problems=problems)

    def _rules(self, calculated, customer, warning, dead_cash_confirmed, draft, limits, approval_ok=False):
        """Rules shared by preview (reported) and finalize (enforced)."""
        # A discount outside the allowed range is the first thing to fix (it changes O).
        problems = self._discount_problems(calculated, draft) + list(calculated.problems)
        gap = calculated.gap or ZERO                     # old gold only: R − L still to decide
        if gap and draft.settlement_choice != SETTLE:
            problems.append(InvoiceError("SETTLEMENT_CHOICE_REQUIRED",
                                         "يوجد فرق على الفاتورة: سوِّه أو أضفه إلى حساب العميل."))
        if gap and draft.settlement_choice == SETTLE:
            amount = abs(gap)
            if amount > limits["settlement_approval_limit"]:
                problems.append(InvoiceError(
                    "SETTLEMENT_OVER_LIMIT",
                    f"الفرق أكبر من {limits['settlement_approval_limit']:,.0f} ج؛ لا يمكن تسويته. "
                    "أضفه إلى حساب العميل أو راجع المبلغ."))
            elif amount > limits["settlement_self_limit"]:
                if not (draft.settlement_reason or "").strip():
                    problems.append(InvoiceError("SETTLEMENT_REASON_REQUIRED",
                                                 "تسوية فرق أكبر من 50 ج تحتاج سبباً."))
                if not approval_ok:
                    problems.append(InvoiceError("SETTLEMENT_APPROVAL_REQUIRED",
                                                 "تسوية فرق أكبر من 50 ج تحتاج موافقة المدير."))
        # Every invoice names its customer (an existing one or a new name): no anonymous invoice.
        if customer is None or not str((customer or {}).get("name") or "x").strip():
            problems.insert(0, InvoiceError("CUSTOMER_NAME_REQUIRED", "اسم العميل مطلوب لإتمام الفاتورة"))
        if warning and calculated.account_signed > 0 and not dead_cash_confirmed:
            problems.append(InvoiceError("DEAD_CASH_CONFIRMATION_REQUIRED",
                                         "العميل عليه تحذير ديون معدومة؛ يلزم تأكيد إنشاء مديونية جديدة."))
        return problems

    @staticmethod
    def _discount_problems(calculated, draft):
        """With any new-gold sale line the old-gold discount is a PERCENT within 1.25–3.00.

        Old gold only (the customer only sells to the shop) keeps the free discount."""
        if calculated.gap is not None:                 # no sale line: old gold only
            return []
        for line in calculated.olds:
            if line.discount_type != "PERCENT" or not SALE_DISCOUNT_MIN <= line.discount_value <= SALE_DISCOUNT_MAX:
                return [InvoiceError("OLD_GOLD_DISCOUNT_RANGE",
                                     f"خصم الكسر في فاتورة بها بيع يجب أن يكون بين {SALE_DISCOUNT_MIN}% و "
                                     f"{SALE_DISCOUNT_MAX}%.")]
        return []

    @staticmethod
    def _limits(invoices):
        row = invoices.billing_settings() if hasattr(invoices, "billing_settings") else None
        return {key: Decimal(str(row[key])) for key in DEFAULT_LIMITS} if row else dict(DEFAULT_LIMITS)

    @staticmethod
    def _new_customer_values(new_customer):
        name = (new_customer.name or "").strip()
        values = {"name": name, "mobile": (new_customer.mobile or "").strip() or None,
                  "place": (new_customer.place or "").strip() or None}
        if not name:
            raise InvoiceError("CUSTOMER_NAME_REQUIRED", "اسم العميل مطلوب لإتمام الفاتورة")
        for key, label, limit in (("name", "اسم العميل", 150), ("mobile", "رقم التليفون", 50),
                                  ("place", "المنطقة", 150)):
            if values[key] and len(values[key]) > limit:
                raise InvoiceError("NEW_CUSTOMER_INVALID", f"{label}: الحد الأقصى {limit} حرفاً.")
        return values

    def _approval_ok(self, connection, actor, draft):
        """The actor holds the approval permission, or a verified manager approved it."""
        if "INVOICE_SETTLEMENT_APPROVE" in actor.permissions:
            return actor.user_id
        approver = draft.settlement_approver
        if approver is None or not isinstance(approver, SettlementApprover) or approver._proof is not _APPROVER_PROOF:
            return None
        if connection is None:
            return approver.user_id
        repository = self._approvers(connection)
        user = repository.get_identity(approver.user_id, lock=True)
        if (not user or not user["is_active"] or user["must_change_password"]
                or (user["employee_id"] is not None and not user.get("employee_active"))):
            return None
        permissions = repository.effective_permissions(user["id"], lock=True)
        if "APP_LOGIN" not in permissions or "INVOICE_SETTLEMENT_APPROVE" not in permissions:
            return None
        return int(user["id"])

    def verify_settlement_approver(self, username, password, *, _expected_actor=None):
        """A manager types their own credentials at the counter; nothing is logged in.

        Uses the normal credential check (cooldown, generic errors) and requires
        INVOICE_SETTLEMENT_APPROVE. The result is an opaque grant for one finalize."""
        self._require_create(_expected_actor)
        from services.authentication_service import AuthenticationService
        authentication = self._authentication or AuthenticationService()
        result = authentication.verify_credentials(username, password)
        if "INVOICE_SETTLEMENT_APPROVE" not in result.permissions:
            raise PermissionError("هذا المستخدم لا يملك صلاحية الموافقة على التسوية.")
        return SettlementApprover(int(result.user_id), result.display_name, _APPROVER_PROOF)

    @staticmethod
    def _making_groups(sales, categories, category_ranges):
        """Sale making per category: WITHIN_RANGE, OUT_OF_RANGE or NOT_CONFIGURED.

        Sale lines only (the old-gold side never enters). A piece without a
        category, or a category without a range, is NOT_CONFIGURED, never a pass.
        Internal control only: never changes price, balance or validity."""
        groups = {}
        for line, category_id in zip(sales, categories):
            key = None if category_id is None else int(category_id)
            group = groups.setdefault(key, [Decimal("0"), Decimal("0.000")])
            group[0] += line.making_total
            group[1] += line.weight_grams
        result = []
        for category_id, (making, weight) in sorted(groups.items(), key=lambda g: (g[0] is None, g[0] or 0)):
            row = category_ranges.get(category_id) if category_id is not None else None
            low = None if row is None else row.get("sale_making_min_per_gram")
            high = None if row is None else row.get("sale_making_max_per_gram")
            average = (making / weight).quantize(Decimal("0.01")) if weight > 0 else Decimal("0.00")
            if low is None and high is None:
                status = NOT_CONFIGURED
            elif (low is not None and average < Decimal(str(low))) or (
                    high is not None and average > Decimal(str(high))):
                status = OUT_OF_RANGE
            else:
                status = WITHIN_RANGE
            result.append({"category_id": category_id, "category_code": None if row is None else row.get("code"),
                           "status": status, "average_making_per_gram": str(average), "weight_grams": str(weight),
                           "sale_making_min_per_gram": None if low is None else str(low),
                           "sale_making_max_per_gram": None if high is None else str(high)})
        return result

    @classmethod
    def _making_review(cls, sales, categories, category_ranges):
        """Only the groups outside their configured range (these open a review)."""
        return [g for g in cls._making_groups(sales, categories, category_ranges) if g["status"] == OUT_OF_RANGE]

    @staticmethod
    def _making_status(groups):
        statuses = {g["status"] for g in groups}
        for status in (OUT_OF_RANGE, NOT_CONFIGURED, WITHIN_RANGE):
            if status in statuses:
                return status
        return None

    @staticmethod
    def _discount_changes(olds, draft_lines, default_percent):
        """Old-gold lines whose discount is not the configured default percentage.

        effect_on_net_signed = actual discount − default discount: what the change
        did to the invoice net (negative: the customer received more)."""
        changes = []
        for line, entry in zip(olds, draft_lines):
            if line.discount_type == "PERCENT" and line.discount_value == default_percent:
                continue
            default = Calc.old_gold_line(line.weight_grams, line.gold_price_per_gram,
                                         discount_type="PERCENT", discount_value=default_percent)
            changes.append({"karat_id": int(entry.karat_id), "weight_grams": line.weight_grams,
                            "gross_total": line.gross_total, "default_percent": default_percent,
                            "discount_type": line.discount_type or "NONE",
                            "discount_value": line.discount_value if line.discount_type else Decimal("0.00"),
                            "discount_amount": line.discount_amount, "default_discount_amount": default.discount_amount,
                            "effect_on_net_signed": line.discount_amount - default.discount_amount})
        return changes

    @staticmethod
    def _payment_accounts(invoices, payments, lock=False):
        """Where each real payment is posted: the cash account or a real, typed bank/wallet account.

        Returns (methods by code, bank/wallet account row per payment index, cash account row, problems).
        A payment that maps to no real ledger is refused (money is never recorded unposted)."""
        methods, accounts, cash, problems = {}, {}, None, []
        for index, (payment, _) in enumerate(payments):
            method = invoices.get_payment_method(payment.method_code)
            if method is None or not method["is_active"]:
                problems.append(InvoiceError("PAYMENT_METHOD_NOT_ALLOWED", "طريقة الدفع غير متاحة."))
                continue
            methods[payment.method_code] = method
            account = payment.bank_account_id
            label = METHOD_TEXT.get(payment.method_code, payment.method_code)
            if method["affects_physical_cash"]:
                if account not in (None, ""):
                    problems.append(InvoiceError("BANK_ACCOUNT_NOT_EXPECTED", f"{label} لا يحتاج حساباً بنكياً."))
                if cash is None:
                    rows = invoices.active_cash_accounts()
                    if len(rows) != 1:
                        problems.append(InvoiceError("CASH_ACCOUNT_UNRESOLVED",
                                                     "لا يوجد حساب نقدية واحد نشط لتسجيل النقدية."))
                        continue
                    cash = rows[0]
            elif not method["requires_bank_account"]:
                problems.append(InvoiceError("PAYMENT_ACCOUNT_NOT_CONFIGURED",
                                             f"{label} غير مربوطة بحساب حقيقي بعد."))
            elif account in (None, ""):
                expected = ACCOUNT_TYPE_FOR_METHOD.get(payment.method_code, "BANK")
                active = invoices.active_bank_accounts() if hasattr(invoices, "active_bank_accounts") else [{}]
                if not any((row.get("account_type") or "BANK") == expected for row in active):
                    problems.append(InvoiceError("PAYMENT_ACCOUNT_MISSING", f"{label}: {NO_ACCOUNT_TEXT[expected]}."))
                else:
                    problems.append(InvoiceError("BANK_ACCOUNT_REQUIRED", f"اختر الحساب لـ {label}."))
            else:
                row = invoices.get_bank_account(account, lock=lock) if str(account).isdigit() else None
                expected = ACCOUNT_TYPE_FOR_METHOD.get(payment.method_code, "BANK")
                if row is None or not row["is_active"]:
                    problems.append(InvoiceError("BANK_ACCOUNT_INVALID", "الحساب غير موجود أو غير نشط."))
                elif (row.get("account_type") or "BANK") != expected:
                    problems.append(InvoiceError("BANK_ACCOUNT_TYPE", f"هذا الحساب لا يصلح لـ {label}."))
                else:
                    accounts[index] = row
        return methods, accounts, cash, problems

    @staticmethod
    def _old_gold_shortages(described, balances):
        """F7 grams requested per karat vs the COUNTED balance before this invoice."""
        requested = {}
        for line in described:
            if line["input"].kind == OLD_GOLD_STOCK:
                requested[line["karat_id"]] = requested.get(line["karat_id"], Decimal("0.000")) + Decimal(
                    str(line["input"].weight_grams).strip())
        shortages = []
        for karat_id, grams in sorted(requested.items()):
            available = balances.get(karat_id, Decimal("0.000"))
            if grams > available:
                shortages.append((karat_id, available, grams))
        return shortages

    # ------------------------------------------------------------ preview

    def _require_create(self, expected_actor):
        actor = self._actor(expected_actor)
        if "INVOICE_FINALIZE" not in actor.permissions and "INVOICE_CREATE" not in actor.permissions:
            raise PermissionError("لا توجد صلاحية لإنشاء فاتورة.")
        return actor

    def price_board(self, *, _expected_actor=None):
        """The Billing price set (all active karats) and its token; read-only."""
        self._require_create(_expected_actor)
        price_set = self._gold_prices.get_billing_price_set(self._read_only(self._karat_ids))
        return {
            "price_token": price_set.price_token, "price_status": price_set.status,
            "requires_price_acknowledgement": price_set.requires_acknowledgement,
            "price_warnings": list(price_set.warnings),
            "prices": [{"karat_id": p.karat_id, "karat": p.karat,
                        "shop_buy": _money(p.shop_buy_price_per_gram),
                        "shop_sell": _money(p.shop_sell_price_per_gram)}
                       for p in sorted(price_set.prices.values(), key=lambda p: p.karat)],
        }

    def find_item_by_barcode(self, barcode, *, _expected_actor=None):
        """The IN_STOCK item carrying this ACTIVE barcode; read-only."""
        self._require_create(_expected_actor)
        barcode = (barcode or "").strip()
        if not barcode:
            raise InvoiceError("BARCODE_REQUIRED", "أدخل الباركود.")

        def lookup(connection):
            invoices = self._invoices(connection)
            item_id = invoices.find_item_id_by_barcode(barcode)
            if item_id is None:
                raise InvoiceError("BARCODE_NOT_FOUND", f"لا توجد قطعة بالباركود {barcode}.")
            return self._preview_items(invoices, [item_id])[0]

        item = self._read_only(lookup)
        return {"inventory_item_id": item["inventory_item_id"], "barcode": item.get("barcode_value"),
                "item_name": item.get("item_type_name"), "karat_id": int(item["karat_id"]),
                "karat": item.get("karat_value"), "weight_grams": _money(item["weight_grams"]),
                "factory_name": item.get("factory_name"), "size": item.get("size"),
                "item_notes": item.get("item_notes")}

    def search_sellable_items(self, *, text="", karat_id=None, weight=None, tolerance="0.05", size="",
                              exclude_ids=(), item_type_id=None, factory_id=None, _expected_actor=None):
        """Candidates for بحث عن قطعة: IN_STOCK items only, read-only.

        Returns every match (up to a limit) and never chooses one: the employee
        must explicitly select the physical piece.
        """
        self._require_create(_expected_actor)
        weight_min = weight_max = target = None
        if weight not in (None, ""):
            try:
                target, spread = Decimal(str(weight).strip()), Decimal(str(tolerance or "0").strip())
            except InvalidOperation:
                raise InvoiceError("SEARCH_WEIGHT_INVALID", "اكتب الوزن بالأرقام.") from None
            if not target.is_finite() or target <= 0 or not spread.is_finite() or not 0 <= spread <= 5:
                raise InvoiceError("SEARCH_WEIGHT_INVALID", "اكتب وزناً صحيحاً وهامشاً من 0 إلى 5 جرام.")
            weight_min, weight_max = target - spread, target + spread
        if karat_id is not None and (isinstance(karat_id, bool) or int(karat_id) <= 0):
            karat_id = None
        rows = self._read_only(lambda connection: self._invoices(connection).search_sellable_items(
            text=text, karat_id=None if karat_id is None else int(karat_id), weight_min=weight_min,
            weight_max=weight_max, size=size, exclude_ids=tuple(exclude_ids), weight_target=target,
            limit=ITEM_SEARCH_LIMIT + 1, item_type_id=item_type_id or None, factory_id=factory_id or None))
        items = [{"inventory_item_id": row["inventory_item_id"], "barcode": row.get("barcode_value"),
                  "item_name": row.get("item_type_name"), "karat_id": int(row["karat_id"]),
                  "karat": row.get("karat_value"), "weight_grams": _money(row["weight_grams"]),
                  "factory_name": row.get("factory_name"), "size": row.get("size"),
                  "category_name": row.get("category_name"), "item_notes": row.get("item_notes")}
                 for row in rows[:ITEM_SEARCH_LIMIT]]
        return {"items": items, "truncated": len(rows) > ITEM_SEARCH_LIMIT}

    def item_search_facets(self, *, item_type_id=None, karat_id=None, exclude_ids=(), _expected_actor=None):
        """الصنف → العيار → المصنع choices that actually exist in IN_STOCK stock."""
        self._require_create(_expected_actor)
        result = self._read_only(lambda connection: self._invoices(connection).sellable_facets(
            item_type_id=item_type_id or None, karat_id=karat_id or None, exclude_ids=tuple(exclude_ids)))
        return {key: [{"id": int(r["id"]), "name": str(r["name"]), "count": int(r["item_count"])} for r in rows]
                for key, rows in result.items()}

    def reference_lists(self, *, _expected_actor=None):
        """Item types, factories, old-gold stock and billing settings for the page."""
        self._require_create(_expected_actor)

        def read(connection):
            invoices = self._invoices(connection)
            lists = invoices.reference_lists()
            methods = [invoices.get_payment_method(code) for code in ALLOWED_PAYMENT_METHODS]
            accounts = invoices.active_bank_accounts() if hasattr(invoices, "active_bank_accounts") else []
            return lists, invoices.old_gold_balances(), self._limits(invoices), methods, accounts
        lists, balances, limits, methods, accounts = self._read_only(read)
        return {"payment_methods": [{"code": m["code"], "requires_bank_account": bool(m["requires_bank_account"]),
                                     "affects_physical_cash": bool(m.get("affects_physical_cash")),
                                     "account_type": ACCOUNT_TYPE_FOR_METHOD.get(m["code"])}
                                    for m in methods if m is not None and m["is_active"]],
                "bank_accounts": [{"id": str(r["id"]), "name": r["name"], "account_type": r.get("account_type") or "BANK",
                                   "bank_name": r.get("bank_name") or "",
                                   "account_label": r.get("account_label") or ""} for r in accounts],
                "item_types": [{"id": int(r["id"]), "name": r["name"]} for r in lists["item_types"]],
                "factories": [{"id": int(r["id"]), "name": r["name"]} for r in lists["factories"]],
                "old_gold_stock": [{"karat_id": int(r["karat_id"]), "karat": int(r["karat_value"]),
                                    "grams": _money(Decimal(str(r["balance_grams"])))} for r in balances],
                "settings": {key: _money(value) for key, value in limits.items()}}

    def search_customers(self, text, *, _expected_actor=None):
        """Billing customer picker: inline suggestions by name or phone (Customers V2 normalized search).

        An operational part of invoice creation, authorized by the invoice permissions (never by
        CUSTOMERS_VIEW) and re-checked against the database without a lock. Returns only what Billing
        needs to choose a customer — at most 8 active customers, no statement / history / profile."""
        actor = self._require_create(_expected_actor)
        text = (text or "").strip()
        if not text:
            return []

        def read(connection):
            self._authorization.require_any_permission_read(connection, actor, ("INVOICE_CREATE", "INVOICE_FINALIZE"))
            return self._customers(connection).search_customers(search=text, is_active=True, limit=8)
        rows = self._read_only(read)
        return [{"customer_id": str(r["customer_id"]), "customer_code": r["customer_code"], "name": r["name"],
                 "mobile": r["mobile"], "place": r["place"], "balance": _money(r.get("balance")),
                 "dead_cash_warning": bool(r.get("has_active_dead_cash_warning"))} for r in rows]

    def preview(self, draft: InvoiceDraft, *, _expected_actor=None):
        """Read-only recalculation for the screen; nothing is written."""
        self._require_create(_expected_actor)
        try:
            with self._transaction() as connection:
                invoices = self._invoices(connection)
                customers, dead_cash = self._customers(connection), self._dead_cash(connection)
                self._salesperson(connection, draft.salesperson_employee_id, lock=False)
                price_set = self._gold_prices.get_billing_price_set(self._karat_ids(connection))
                limits = self._limits(invoices)
                if draft.customer_id not in (None, "") and draft.new_customer is not None:
                    raise InvoiceError("CUSTOMER_CONFLICT", "اختر عميلاً موجوداً أو أدخل عميلاً جديداً، وليس الاثنين.")
                new_customer, same_mobile = None, []
                if draft.new_customer is not None:
                    new_customer = self._new_customer_values(draft.new_customer)
                    if new_customer["mobile"]:
                        same_mobile = customers.find_active_by_mobile(new_customer["mobile"])
                customer, balance, warning = self._customer(invoices, customers, dead_cash,
                                                            draft.customer_id, lock=False)
                ids = [line.inventory_item_id for line in draft.sale_lines]
                self._require_unique_items(draft)
                items = self._preview_items(invoices, ids)
                described = self._described(invoices, draft)
                calculated = self._calculate(draft, items, price_set, described=described, limits=limits)
                payment_problems = self._payment_accounts(invoices, calculated.payments)[3]
                balances = {int(r["karat_id"]): Decimal(str(r["balance_grams"]))
                            for r in invoices.old_gold_balances()} if described else {}
                category_ranges = invoices.category_ranges(
                    [i.get("category_id") for i in items] + [d["category_id"] for d in described])
                approval = self._approval_ok(None, self._session.require_authenticated(), draft)
                # Leave through the transaction's own rollback path: preview never commits.
                raise _DiscardPreview()
        except _DiscardPreview:
            pass
        sales, olds, settlement = calculated.sales, calculated.olds, calculated.settlement
        problems = self._rules(calculated, customer or new_customer, warning, dead_cash_confirmed=False,
                               draft=draft, limits=limits, approval_ok=approval is not None)
        problems += payment_problems
        for karat_id, available, grams in self._old_gold_shortages(described, balances):
            karat = next((p.karat for p in price_set.prices.values() if p.karat_id == karat_id), karat_id)
            problems.append(InvoiceError(
                "OLD_GOLD_STOCK_INSUFFICIENT",
                f"رصيد الكسر عيار {karat} غير كافٍ: المتاح {available:,.3f} جرام والمطلوب {grams:,.3f} جرام."))
        categories = [i.get("category_id") for i in items] + [d["category_id"] for d in described]
        # Making review only on a valid derived price (re-run on every financial change).
        groups = self._making_groups(sales, categories, category_ranges) if calculated.agreed else []
        review = [g for g in groups if g["status"] == OUT_OF_RANGE]
        discount_changes = self._discount_changes(olds, draft.old_gold_lines,
                                                  limits["old_gold_discount_default_percent"])
        gap = calculated.gap or ZERO
        return {
            "price_token": price_set.price_token,
            "price_status": price_set.status,
            "requires_price_acknowledgement": price_set.requires_acknowledgement,
            "price_warnings": list(price_set.warnings),
            "sale_lines": [{"inventory_item_id": item["inventory_item_id"], "barcode": item.get("barcode_value"),
                            "item_name": item.get("item_type_name"), "karat": item.get("karat_value"),
                            "weight_grams": _money(line.weight_grams),
                            "gold_price_per_gram": _money(line.gold_price_per_gram),
                            "making_total": _money(line.making_total), "gold_value": _money(line.gold_value),
                            "line_total": _money(line.line_total)} for line, item in zip(sales, items)],
            "old_gold_lines": [{"karat_id": int(d.karat_id), "weight_grams": _money(line.weight_grams),
                                "gold_price_per_gram": _money(line.gold_price_per_gram),
                                "gross_total": _money(line.gross_total),
                                "discount_amount": _money(line.discount_amount),
                                "line_total": _money(line.line_total)}
                               for line, d in zip(olds, draft.old_gold_lines)],
            "described_lines": [{"kind": d["input"].kind, "item_name": d["item_name"],
                                 "factory_name": d["factory_name"], "karat_id": d["karat_id"],
                                 "weight_grams": _money(line.weight_grams),
                                 "gold_price_per_gram": _money(line.gold_price_per_gram),
                                 "making_total": _money(line.making_total), "line_total": _money(line.line_total)}
                                for d, line in zip(described, sales[len(items):])],
            **self._commercial_view(calculated),
            "making_review_required": bool(review),
            "making_review_status": self._making_status(groups),
            "making_groups": groups,
            "old_gold_discount_changed": bool(discount_changes),
            "old_gold_discount_default_percent": _money(limits["old_gold_discount_default_percent"]),
            "old_gold_discount_range": ([_money(SALE_DISCOUNT_MIN), _money(SALE_DISCOUNT_MAX)]
                                        if calculated.gap is None else None),
            "settlement_limits": {key: _money(value) for key, value in limits.items()},
            "settlement_needs_approval": bool(gap and abs(gap) > limits["settlement_self_limit"]
                                              and approval is None),
            "settlement_approved_by_actor": approval is not None and draft.settlement_approver is None,
            "new_customer_same_mobile": [{"customer_id": str(r["customer_id"]), "name": r["name"],
                                          "customer_code": r["customer_code"]} for r in same_mobile],
            "customer_balance_before": _money(balance),
            "customer_balance_after": _money(None if balance is None else balance + calculated.account_signed),
            "dead_cash_warning_active": warning,
            "requires_dead_cash_confirmation": any(p.code == "DEAD_CASH_CONFIRMATION_REQUIRED" for p in problems),
            "blocking_problems": [{"code": p.code, "message": str(p)} for p in problems
                                  if p.code != "DEAD_CASH_CONFIRMATION_REQUIRED"],
        }

    def _preview_items(self, invoices, ids):
        # Preview must not hold row locks: read the same rows without FOR UPDATE.
        items = invoices.read_sale_items(ids) if ids else []
        found = {item["inventory_item_id"]: item for item in items}
        result = []
        for item_id in ids:
            item = found.get(str(int(item_id)))
            if item is None:
                raise InvoiceError("ITEM_NOT_FOUND", f"القطعة رقم {item_id} غير موجودة.")
            if item["inventory_status"] != "IN_STOCK":
                raise InvoiceError("ITEM_UNAVAILABLE", f"القطعة رقم {item_id} غير متاحة للبيع.")
            result.append(item)
        return result

    @staticmethod
    def _commercial_view(calculated):
        settlement, agreed = calculated.settlement, calculated.agreed
        has_sales = calculated.gap is None
        weight = sum((line.weight_grams for line in calculated.sales), Decimal("0.000"))
        average = None
        if has_sales and calculated.making_total is not None and weight > 0 and calculated.effective_sale > 0:
            average = (calculated.making_total / weight).quantize(Decimal("0.01"))
        # F8: the full amount exists only when nothing is sold (the old-gold value minus the account part).
        full = None
        if not has_sales and settlement.old_gold_total:
            full = settlement.old_gold_total - abs(calculated.account_signed)
        return {
            "payment_direction": calculated.direction,
            "gold_value_total": _money(calculated.gold_value) if has_sales else None,
            "effective_sale_total": _money(calculated.effective_sale),
            # Internal / audit only; the salesman sees the average.
            "making_total": _money(calculated.making_total),
            "average_making_per_gram": _money(average),
            "account_signed": _money(calculated.account_signed),
            "account_amount": _money(abs(calculated.account_signed)),
            "gap_signed": _money(calculated.gap),
            "settlement_adjustment_signed": _money(calculated.adjustment),
            "full_amount": _money(full),
            "invoice_type": settlement.invoice_type,
            "sale_total": _money(settlement.sale_total),
            "old_gold_total": _money(settlement.old_gold_total),
            "net_amount": _money(settlement.net_amount),
            "net_direction": settlement.net_direction,
            "money_received": _money(settlement.money_received),
            "money_paid": _money(settlement.money_paid),
            "account_residual_amount": _money(abs(calculated.account_signed)),
            "account_residual_direction": ("CUSTOMER_OWES_SHOP" if calculated.account_signed > 0
                                           else "SHOP_OWES_CUSTOMER" if calculated.account_signed < 0 else "NONE"),
            "settlement_status": settlement.settlement_status,
        }

    # ------------------------------------------------------------ finalize

    def finalize(self, draft: InvoiceDraft, price_token: str, *, stale_acknowledged=False,
                 dead_cash_confirmed=False, _expected_actor=None):
        session_actor = self._actor(_expected_actor)
        if not draft.sale_lines and not draft.old_gold_lines and not draft.described_sale_lines:
            raise InvoiceError("EMPTY_INVOICE", "الفاتورة لا تحتوي على أي بنود.")
        if len(draft.sale_lines) + len(draft.described_sale_lines) > MAX_SALE_ITEMS:
            raise InvoiceError("TOO_MANY_SALE_ITEMS", MAX_SALE_ITEMS_TEXT)
        if draft.customer_id not in (None, "") and draft.new_customer is not None:
            raise InvoiceError("CUSTOMER_CONFLICT", "اختر عميلاً موجوداً أو أدخل عميلاً جديداً، وليس الاثنين.")
        with self._transaction() as connection:
            # Session checks use the session's own actor object; the fresh copy
            # returned by authorization is the audit snapshot for attribution.
            actor = self._authorization.require_permission(connection, session_actor, "INVOICE_FINALIZE")
            if draft.payments:
                self._authorization.require_permission(connection, session_actor, "PAYMENT_RECORD")
            invoices = self._invoices(connection)
            customers, dead_cash = self._customers(connection), self._dead_cash(connection)

            salesperson = self._salesperson(connection, draft.salesperson_employee_id, lock=True)
            now = self._business_clock(connection)
            price_set = self._gold_prices.lock_and_validate_for_finalization(
                connection, price_token, self._karat_ids(connection), acknowledged=stale_acknowledged is True)
            limits = self._limits(invoices)
            customer_id, created_customer = draft.customer_id, False
            if draft.new_customer is not None:
                values = self._new_customer_values(draft.new_customer)
                # Re-check (another terminal may have created this customer meanwhile).
                same = customers.find_active_by_mobile(values["mobile"]) if values["mobile"] else []
                if same:
                    customer_id = str(same[0]["customer_id"])
                else:
                    customer_id = str(customers.create_customer(values["name"], values["mobile"], values["place"],
                                                                None, None, created_by_user_id=actor.user_id))
                    created_customer = True
            customer, balance, warning = self._customer(invoices, customers, dead_cash, customer_id, lock=True)
            items = self._items(invoices, draft)
            described = self._described(invoices, draft)
            # Old-gold stock: lock each karat's COUNTED ledger and read the balance
            # before this invoice writes anything (its own buy-ins never count).
            balances = {karat_id: invoices.lock_old_gold_balance(karat_id)
                        for karat_id in sorted({d["karat_id"] for d in described
                                                if d["input"].kind == OLD_GOLD_STOCK})}
            shortages = self._old_gold_shortages(described, balances)
            if shortages:
                karat_id, available, grams = shortages[0]
                karat = next((p.karat for p in price_set.prices.values() if p.karat_id == karat_id), karat_id)
                raise InvoiceError("OLD_GOLD_STOCK_INSUFFICIENT",
                                   f"رصيد الكسر عيار {karat} غير كافٍ: المتاح {available:,.3f} جرام "
                                   f"والمطلوب {grams:,.3f} جرام.")
            calculated = self._calculate(draft, items, price_set, described=described, limits=limits)
            sales, olds, payments = calculated.sales, calculated.olds, calculated.payments
            settlement, agreed = calculated.settlement, calculated.agreed
            approver_id = self._approval_ok(connection, actor, draft)
            problems = self._rules(calculated, customer, warning, dead_cash_confirmed is True, draft=draft,
                                   limits=limits, approval_ok=approver_id is not None)
            methods, accounts, cash_account, payment_problems = self._payment_accounts(invoices, payments, lock=True)
            problems += payment_problems
            if problems:
                raise problems[0]
            categories = [i.get("category_id") for i in items] + [d["category_id"] for d in described]
            groups = (self._making_groups(sales, categories, invoices.category_ranges(categories))
                      if agreed is not None else [])
            review = [g for g in groups if g["status"] == OUT_OF_RANGE]
            discount_changes = self._discount_changes(olds, draft.old_gold_lines,
                                                      limits["old_gold_discount_default_percent"])

            number = invoices.next_invoice_number(now.date())
            invoice_id = invoices.insert_invoice(
                invoice_number=number, invoice_type=settlement.invoice_type,
                customer_id=None if customer is None else customer["customer_id"],
                employee_id=int(salesperson["id"]), invoice_date=now,
                sell_total=settlement.sale_total, buy_total=settlement.old_gold_total,
                net_amount=settlement.net_amount, net_direction=settlement.net_direction,
                settlement_status=settlement.settlement_status, created_by_user_id=actor.user_id,
                finalized_at=now, notes=draft.notes)
            if customer is not None:
                invoices.insert_customer_snapshot(
                    invoice_id=invoice_id, customer=customer, balance_before=balance,
                    balance_after=balance + calculated.account_signed, dead_cash_warning_active=warning)
            invoices.insert_price_snapshots(
                invoice_id=invoice_id, source_code=price_set.provenance.get("source_code"),
                prices=[{"karat_id": p.karat_id, "shop_buy_price_per_gram": p.shop_buy_price_per_gram,
                         "shop_sell_price_per_gram": p.shop_sell_price_per_gram, "quote_id": p.quote_id,
                         # UTC like the Gold Price Engine tables; each karat keeps its own time.
                         "provider_timestamp": p.provider_timestamp.replace(tzinfo=None)
                         if p.provider_timestamp is not None else None}
                        for p in price_set.prices.values()])

            line_number = 0
            for line, item, draft_line in zip(sales, items, draft.sale_lines):
                line_number += 1
                invoices.insert_sale_line(
                    invoice_id=invoice_id, line_number=line_number, item=item,
                    gold_price_per_gram=line.gold_price_per_gram, making_per_gram=line.making_per_gram,
                    making_total=line.making_total, gross_total=line.gold_value, line_total=line.line_total,
                    identification_method=draft_line.identified_by)
                invoices.mark_item_sold(inventory_item_id=item["inventory_item_id"],
                                        updated_by_user_id=actor.user_id)
                invoices.add_inventory_sold_movement(
                    item=item, invoice_id=invoice_id, invoice_number=number,
                    performed_by_user_id=actor.user_id, performed_by_employee_id=int(salesperson["id"]))
            group = str(uuid.uuid4())
            case_ids = []
            for line, resolved in zip(sales[len(items):], described):
                line_number += 1
                entry = resolved["input"]
                movement_id = None
                if entry.kind == OLD_GOLD_STOCK:
                    movement_id = invoices.add_invoice_sale_out_movement(
                        karat_id=resolved["karat_id"], weight_grams=line.weight_grams,
                        gold_price_per_gram=line.gold_price_per_gram, cash_value=line.line_total,
                        price_quote_id=price_set.prices[resolved["karat_id"]].quote_id, movement_group_id=group,
                        invoice_id=invoice_id, invoice_number=number,
                        customer_id=None if customer is None else customer["customer_id"],
                        created_by_user_id=actor.user_id, employee_id=int(salesperson["id"]),
                        notes=resolved["item_name"])
                line_id = invoices.insert_described_sale_line(
                    invoice_id=invoice_id, line_number=line_number,
                    line_type="SELL_OLD_GOLD" if entry.kind == OLD_GOLD_STOCK else "SELL_UNIDENTIFIED",
                    karat_id=resolved["karat_id"], weight_grams=line.weight_grams, item_name=resolved["item_name"],
                    factory_name=resolved["factory_name"], size=resolved["size"],
                    gold_price_per_gram=line.gold_price_per_gram, making_total=line.making_total,
                    gross_total=line.gold_value, line_total=line.line_total, old_gold_movement_id=movement_id,
                    notes=resolved["notes"])
                if entry.kind == UNIDENTIFIED:
                    case_ids.append(invoices.open_unidentified_sale_case(
                        invoice_id=invoice_id, invoice_number=number, invoice_line_id=line_id,
                        item_type_id=resolved["item_type_id"], karat_id=resolved["karat_id"],
                        factory_id=resolved["factory_id"], category_id=resolved["category_id"],
                        weight_grams=line.weight_grams, size=resolved["size"], reason_code=entry.reason_code,
                        reason=REASONS[entry.reason_code], notes=resolved["notes"],
                        created_by_user_id=actor.user_id, salesperson_employee_id=int(salesperson["id"]),
                        sold_at=now))
            for line, draft_line in zip(olds, draft.old_gold_lines):
                line_number += 1
                karat_id = int(draft_line.karat_id)
                movement_id = invoices.add_customer_buy_in_movement(
                    karat_id=karat_id, weight_grams=line.weight_grams,
                    gold_price_per_gram=line.gold_price_per_gram, cash_value=line.line_total,
                    price_quote_id=price_set.prices[karat_id].quote_id, movement_group_id=group,
                    invoice_id=invoice_id, invoice_number=number,
                    customer_id=None if customer is None else customer["customer_id"],
                    created_by_user_id=actor.user_id, received_by_employee_id=int(salesperson["id"]),
                    notes=draft_line.notes)
                invoices.insert_old_gold_line(
                    invoice_id=invoice_id, line_number=line_number, karat_id=karat_id,
                    weight_grams=line.weight_grams, gold_price_per_gram=line.gold_price_per_gram,
                    gross_total=line.gross_total, discount_type=line.discount_type,
                    discount_value=line.discount_value, discount_amount=line.discount_amount,
                    line_total=line.line_total, old_gold_movement_id=movement_id, notes=draft_line.notes)

            # Real money: one invoice_payments row per leg, posted to its real ledger in this
            # transaction (cash_movements or bank_movements, linked by invoice_payment_id).
            for index, (payment, money) in enumerate(payments):
                method, account = methods[payment.method_code], accounts.get(index)
                payment_id = invoices.insert_payment(
                    invoice_id=invoice_id, payment_method_id=method["payment_method_id"],
                    bank_account_id=None if account is None else int(account["id"]),
                    direction=money.direction, amount=money.amount,
                    created_by_user_id=actor.user_id, payment_reference=payment.reference, occurred_at=now)
                ledger = dict(invoice_payment_id=payment_id, direction=money.direction, amount=money.amount,
                              invoice_id=invoice_id, invoice_number=number, created_by_user_id=actor.user_id,
                              occurred_at=now)
                if account is None:
                    invoices.add_cash_movement(cash_account_id=int(cash_account["id"]), **ledger)
                else:
                    invoices.add_bank_movement(bank_account_id=int(account["id"]),
                                               payment_reference=payment.reference, **ledger)

            residual = settlement.residual_signed
            adjustment = calculated.adjustment
            if adjustment != 0:
                # Old gold only: the explicit rounding settlement; never a balance, never a payment row.
                needs_approval = abs(adjustment) > limits["settlement_self_limit"]
                invoices.insert_settlement_adjustment(
                    invoice_id=invoice_id, residual_signed=residual, amount_signed=adjustment,
                    reason=(draft.settlement_reason or "").strip() or None, accepted_by_user_id=actor.user_id,
                    approved_by_user_id=approver_id if needs_approval else None,
                    approved_at=now if needs_approval else None)
                if needs_approval:
                    invoices.insert_management_review(
                        review_type="CASH_SETTLEMENT", invoice_id=invoice_id,
                        summary=f"تسوية فرق {abs(adjustment):,.2f} ج — فاتورة {number}",
                        details={"residual_signed": residual, "adjustment_signed": adjustment,
                                 "approved_by_user_id": approver_id, "reason": draft.settlement_reason},
                        created_by_user_id=actor.user_id)
            account = calculated.account_signed               # the separate customer-ledger instruction
            if account != 0:
                customers.add_transaction(
                    customer_id=int(customer["customer_id"]),
                    type_code="SALE_BALANCE_DUE" if account > 0 else "BUY_BALANCE_DUE",
                    amount_delta=account, created_by_user_id=actor.user_id,
                    reference_type="INVOICE", reference_id=invoice_id, reference_code=number,
                    description=f"متبقي فاتورة {number}")
            if review:
                invoices.insert_management_review(
                    review_type="MAKING_PRICE", invoice_id=invoice_id,
                    summary=f"مصنعية خارج النطاق — فاتورة {number}",
                    details={"groups": groups, "salesperson_employee_id": int(salesperson["id"]),
                             "salesperson_name": salesperson["name"],
                             "effective_sale_total": calculated.effective_sale,
                             "average_making_per_gram": None if agreed is None else agreed.average_making_per_gram},
                    created_by_user_id=actor.user_id)
            if discount_changes:
                # Separate from the making review: the discount never enters the making average.
                effect = sum((c["effect_on_net_signed"] for c in discount_changes), Decimal("0.00"))
                invoices.insert_management_review(
                    review_type="OLD_GOLD_DISCOUNT", invoice_id=invoice_id,
                    summary=f"تم تعديل نسبة خصم الكسر — فاتورة {number}",
                    details={"invoice_number": number, "salesperson_employee_id": int(salesperson["id"]),
                             "salesperson_name": salesperson["name"], "actor_user_id": actor.user_id,
                             "default_percent": limits["old_gold_discount_default_percent"],
                             "lines": discount_changes, "old_gold_total": settlement.old_gold_total,
                             "effect_on_net_signed": effect, "occurred_at": now},
                    created_by_user_id=actor.user_id)

            details = {
                "price_token": price_set.price_token, "price_status": price_set.status,
                "stale_acknowledged": bool(price_set.requires_acknowledgement and stale_acknowledged),
                "dead_cash_warning_active": warning,
                "dead_cash_confirmed": bool(warning and account > 0 and dead_cash_confirmed),
                "salesperson_employee_id": int(salesperson["id"]),
                "actor_employee_id": actor.employee_id,
                "direction": calculated.direction, "O": calculated.old_total,
                "P": settlement.payment_signed, "L": calculated.account_signed, "A": adjustment,
                "N": settlement.net_signed, "R_account": account,
                "payments": [{"method": p.method_code, "amount": m.amount, "direction": m.direction,
                              "bank_account_id": None if accounts.get(i) is None else int(accounts[i]["id"])}
                             for i, (p, m) in enumerate(payments)],
            }
            if agreed is not None:
                # effective_sale_total = O + P_in − P_out; making derived, never typed.
                details.update(effective_sale_total=calculated.effective_sale, gold_value_total=calculated.gold_value,
                               making_total=calculated.making_total,
                               average_making_per_gram=agreed.average_making_per_gram)
            if groups:
                details["making_status"] = self._making_status(groups)
                details["making_groups"] = groups
            if review:
                details["making_review"] = review
            if discount_changes:
                details["old_gold_discount_changes"] = discount_changes
            if case_ids:
                details["unidentified_sale_case_ids"] = case_ids
            if created_customer:
                details["customer_created"] = customer["customer_id"]
            searched = [str(line.inventory_item_id) for line in draft.sale_lines if line.identified_by == "SEARCH"]
            if searched:
                # Pieces found with بحث عن قطعة instead of a barcode scan.
                details["search_identified_item_ids"] = searched
            invoices.insert_event(invoice_id=invoice_id, event_type="CREATED", performed_by_user_id=actor.user_id)
            invoices.insert_event(invoice_id=invoice_id, event_type="FINALIZED",
                                  performed_by_user_id=actor.user_id, details=details)
            self._audit(connection, actor, invoice_id, number, details, now)
            if not self._session.is_current(session_actor):
                raise PermissionError("انتهت جلسة الدخول.")
        return {"invoice_id": invoice_id, "invoice_number": number, "finalized_at": now.isoformat(sep=" "),
                "customer_created": created_customer, "making_review_required": bool(review),
                "unidentified_cases": len(case_ids), "old_gold_discount_review": bool(discount_changes),
                **self._commercial_view(calculated)}

    def _audit(self, connection, actor, invoice_id, number, details, now):
        import json
        cursor = connection.cursor()
        try:
            cursor.execute(
                "INSERT INTO audit_log (user_id, event_type, entity_type, entity_id, entity_reference, "
                "description, metadata_json, occurred_at) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                (actor.user_id, "INVOICE_FINALIZED", "INVOICE", int(invoice_id), number,
                 f"فاتورة {number}", json.dumps(details, ensure_ascii=False, default=str), now))
        finally:
            cursor.close()
