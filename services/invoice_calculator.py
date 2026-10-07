"""Pure Decimal arithmetic for Canonical Billing V2 (no database, no Qt).

Settlement semantics (CLAUDE.md §8):
    N = sale_total - old_gold_purchase_total      (commercial net, signed)
    P = money_received - money_paid               (real money only, signed)
    R = N - P                                     (residual)
    A = explicitly accepted cash settlement       (same sign as R, |A| <= |R|)
    R_account = R - A                             (the only part that reaches the customer account)

Positive values mean the customer owes / pays the shop; negative values mean the
shop owes / pays the customer. Scales match the canonical `invoices` family.
"""
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_DOWN, ROUND_HALF_UP
from services.number_text import normalize_digits


MONEY = Decimal("0.01")
WEIGHT = Decimal("0.001")
PRICE = Decimal("0.0001")
MAKING_PER_GRAM = Decimal("0.01")
PERCENT = Decimal("0.01")
ZERO = Decimal("0.00")
HUNDRED = Decimal("100")
# DECIMAL(16,2) upper bound of every stored money column.
MAX_MONEY = Decimal("99999999999999.99")

PERCENT_DISCOUNT = "PERCENT"
AMOUNT_DISCOUNT = "AMOUNT"


class InvoiceCalculationError(ValueError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class SaleLine:
    weight_grams: Decimal
    gold_price_per_gram: Decimal
    making_per_gram: Decimal | None
    making_total: Decimal
    gold_value: Decimal
    line_total: Decimal


@dataclass(frozen=True)
class OldGoldLine:
    weight_grams: Decimal
    gold_price_per_gram: Decimal
    gross_total: Decimal
    discount_type: str | None
    discount_value: Decimal | None
    discount_amount: Decimal
    line_total: Decimal


@dataclass(frozen=True)
class Payment:
    direction: str  # IN = customer pays shop, OUT = shop pays customer
    amount: Decimal


@dataclass(frozen=True)
class Settlement:
    invoice_type: str
    sale_total: Decimal
    old_gold_total: Decimal
    sale_weight_total: Decimal
    old_gold_weight_total: Decimal
    net_signed: Decimal
    net_amount: Decimal
    net_direction: str
    money_received: Decimal
    money_paid: Decimal
    payment_signed: Decimal
    residual_signed: Decimal
    residual_amount: Decimal
    residual_direction: str
    settlement_status: str
    adjustment_signed: Decimal = ZERO
    account_residual_signed: Decimal = ZERO

    @property
    def account_residual_amount(self):
        return abs(self.account_residual_signed)

    @property
    def account_residual_direction(self):
        value = self.account_residual_signed
        return "CUSTOMER_OWES_SHOP" if value > 0 else "SHOP_OWES_CUSTOMER" if value < 0 else "NONE"

    @property
    def requires_customer(self):
        """Only money deliberately left on the account needs a customer."""
        return self.account_residual_signed != 0


@dataclass(frozen=True)
class AgreedSale:
    gold_value_total: Decimal
    agreed_total: Decimal
    making_total: Decimal
    weight_total: Decimal
    average_making_per_gram: Decimal
    allocations: tuple            # making per sale line, in line order


def _decimal(value, scale, *, label, positive=False, allow_none=False):
    if value is None and allow_none:
        return None
    # float and bool are never accepted for authoritative amounts.
    if value is None or isinstance(value, (bool, float)):
        raise InvoiceCalculationError("INVALID_NUMBER", f"قيمة {label} غير صحيحة.")
    try:
        result = value if isinstance(value, Decimal) else Decimal(normalize_digits(str(value)).strip())
    except (InvalidOperation, ValueError):
        raise InvoiceCalculationError("INVALID_NUMBER", f"قيمة {label} غير صحيحة.") from None
    if not result.is_finite():
        raise InvoiceCalculationError("INVALID_NUMBER", f"قيمة {label} غير صحيحة.")
    # Excess precision is rejected, never silently rounded.
    if result != result.quantize(scale, rounding=ROUND_HALF_UP):
        raise InvoiceCalculationError("PRECISION", f"عدد الخانات العشرية في {label} أكبر من المسموح.")
    result = result.quantize(scale)
    if result < 0 or (positive and result == 0):
        condition = "أكبر من صفر" if positive else "صفراً أو أكثر"
        raise InvoiceCalculationError("OUT_OF_RANGE", f"يجب أن تكون قيمة {label} {condition}.")
    return result


def _money(value):
    result = value.quantize(MONEY, rounding=ROUND_HALF_UP)
    if abs(result) > MAX_MONEY:
        raise InvoiceCalculationError("TOO_LARGE", "المبلغ يتجاوز الحد المسموح.")
    return result


class InvoiceCalculator:
    """Stateless; every method validates its inputs and returns frozen results."""

    @staticmethod
    def sale_line(weight, price, *, making_per_gram=None, making_total=None):
        weight = _decimal(weight, WEIGHT, label="الوزن", positive=True)
        price = _decimal(price, PRICE, label="سعر الجرام", positive=True)
        if (making_per_gram is None) == (making_total is None):
            raise InvoiceCalculationError(
                "MAKING_REQUIRED", "حدد المصنعية إما للجرام أو إجمالياً (وليس الاثنين).")
        per_gram = _decimal(making_per_gram, MAKING_PER_GRAM, label="مصنعية الجرام",
                            allow_none=True)
        if per_gram is not None:
            making = _money(weight * per_gram)
        else:
            making = _money(_decimal(making_total, MONEY, label="إجمالي المصنعية"))
        gold_value = _money(weight * price)
        # Stored components always add up to the stored total.
        return SaleLine(weight, price, per_gram, making, gold_value, _money(gold_value + making))

    @staticmethod
    def old_gold_line(weight, price, *, discount_type=None, discount_value=None):
        weight = _decimal(weight, WEIGHT, label="الوزن", positive=True)
        price = _decimal(price, PRICE, label="سعر الجرام", positive=True)
        gross = _money(weight * price)
        if discount_type is None:
            if discount_value is not None:
                raise InvoiceCalculationError("DISCOUNT_TYPE", "حدد نوع الخصم (نسبة أو مبلغ).")
            return OldGoldLine(weight, price, gross, None, None, ZERO, gross)
        if discount_type == PERCENT_DISCOUNT:
            value = _decimal(discount_value, PERCENT, label="نسبة الخصم")
            if value > HUNDRED:
                raise InvoiceCalculationError("DISCOUNT_RANGE", "نسبة الخصم يجب ألا تتجاوز ١٠٠٪.")
            amount = _money(gross * value / HUNDRED)
        elif discount_type == AMOUNT_DISCOUNT:
            value = _decimal(discount_value, MONEY, label="مبلغ الخصم")
            amount = value
        else:
            raise InvoiceCalculationError("DISCOUNT_TYPE", "نوع الخصم غير معروف.")
        if amount > gross:
            raise InvoiceCalculationError("DISCOUNT_RANGE", "الخصم لا يمكن أن يتجاوز قيمة الكسر.")
        # No rounding to 5 or any other increment: the exact discount is kept.
        return OldGoldLine(weight, price, gross, discount_type, value, amount, gross - amount)

    @staticmethod
    def agreed_sale(lines, agreed_total):
        """Making derived from the price agreed with the customer.

        lines: (weight, gold_price_per_gram) per sold line, in invoice order.
        gold value = Σ weight × price; making = agreed − gold value, allocated
        by weight (rounded down per line) with the remainder on the last line.
        """
        agreed = _money(_decimal(agreed_total, MONEY, label="إجمالي البيع المتفق عليه", positive=True))
        if not lines:
            raise InvoiceCalculationError("EMPTY_INVOICE", "لا توجد قطع مباعة.")
        weights, gold = [], ZERO
        for weight, price in lines:
            weight = _decimal(weight, WEIGHT, label="الوزن", positive=True)
            price = _decimal(price, PRICE, label="سعر الجرام", positive=True)
            weights.append(weight)
            gold += _money(weight * price)
        gold = _money(gold)
        making = agreed - gold
        if making < 0:
            raise InvoiceCalculationError(
                "AGREED_BELOW_GOLD", f"الإجمالي المتفق عليه أقل من قيمة الذهب ({gold:,.2f} ج).")
        total_weight = sum(weights, Decimal("0.000"))
        allocations = [(making * w / total_weight).quantize(MONEY, rounding=ROUND_DOWN) for w in weights[:-1]]
        allocations.append(making - sum(allocations, ZERO))
        average = (making / total_weight).quantize(MAKING_PER_GRAM, rounding=ROUND_HALF_UP)
        return AgreedSale(gold, agreed, making, total_weight, average, tuple(allocations))

    @staticmethod
    def payment(direction, amount):
        if direction not in ("IN", "OUT"):
            raise InvoiceCalculationError("PAYMENT_DIRECTION", "اتجاه الدفع غير صحيح.")
        return Payment(direction, _money(_decimal(amount, MONEY, label="المبلغ المدفوع",
                                                  positive=True)))

    @staticmethod
    def settlement(sale_lines, old_gold_lines, payments=(), adjustment=None, rounding_allowance=ZERO):
        """rounding_allowance: money may exceed the net by at most this amount — only the automatic
        EGP-5 rounding of an old-gold-only settlement (< 5 ج); every other overpayment is refused."""
        sale_lines, old_gold_lines = tuple(sale_lines), tuple(old_gold_lines)
        payments = tuple(payments)
        if any(not isinstance(line, SaleLine) for line in sale_lines) or any(
                not isinstance(line, OldGoldLine) for line in old_gold_lines) or any(
                not isinstance(item, Payment) for item in payments):
            raise TypeError("Use InvoiceCalculator line and payment results.")
        payments = tuple(InvoiceCalculator.payment(p.direction, p.amount) for p in payments)
        if not sale_lines and not old_gold_lines:
            raise InvoiceCalculationError("EMPTY_INVOICE", "الفاتورة لا تحتوي على أي بنود.")
        invoice_type = ("MIXED" if sale_lines and old_gold_lines
                        else "SELL" if sale_lines else "BUY")
        sale_total = _money(sum((line.line_total for line in sale_lines), ZERO))
        old_total = _money(sum((line.line_total for line in old_gold_lines), ZERO))
        sale_weight = sum((line.weight_grams for line in sale_lines), Decimal("0.000"))
        old_weight = sum((line.weight_grams for line in old_gold_lines), Decimal("0.000"))
        net = _money(sale_total - old_total)
        net_direction = ("CUSTOMER_PAYS_SHOP" if net > 0
                         else "SHOP_PAYS_CUSTOMER" if net < 0 else "SETTLED")

        received = _money(sum((p.amount for p in payments if p.direction == "IN"), ZERO))
        paid = _money(sum((p.amount for p in payments if p.direction == "OUT"), ZERO))
        if received and paid:
            raise InvoiceCalculationError(
                "MIXED_PAYMENT_DIRECTIONS", "لا يجوز استلام ودفع نقدية في نفس الفاتورة.")
        if net == 0 and (received or paid):
            raise InvoiceCalculationError(
                "PAYMENT_NOT_EXPECTED", "صافي الفاتورة صفر؛ لا توجد مبالغ للاستلام أو الدفع.")
        if (net > 0 and paid) or (net < 0 and received):
            raise InvoiceCalculationError(
                "PAYMENT_DIRECTION", "اتجاه الدفع يخالف اتجاه صافي الفاتورة.")
        signed_payment = received - paid
        if abs(signed_payment) > abs(net) + Decimal(str(rounding_allowance or 0)):
            # Overpayment is not stored on an invoice; existing credit is shown only.
            raise InvoiceCalculationError("OVERPAYMENT", "المبلغ المدفوع أكبر من صافي الفاتورة.")

        residual = net - signed_payment
        residual_direction = ("CUSTOMER_OWES_SHOP" if residual > 0
                              else "SHOP_OWES_CUSTOMER" if residual < 0 else "NONE")
        # A: the part of R closed as a practical cash settlement (explicit, same sign as R).
        adjustment = ZERO if adjustment is None else _money(Decimal(str(adjustment)))
        if adjustment and (residual == 0 or (adjustment > 0) != (residual > 0) or abs(adjustment) > abs(residual)):
            raise InvoiceCalculationError("SETTLEMENT_INVALID", "فرق التسوية لا يطابق المتبقي على الفاتورة.")
        account_residual = residual - adjustment
        if account_residual == 0:
            status = "SETTLED"
        elif signed_payment == 0:
            status = "UNSETTLED"
        else:
            status = "PARTIAL"
        return Settlement(
            invoice_type=invoice_type, sale_total=sale_total, old_gold_total=old_total,
            sale_weight_total=sale_weight, old_gold_weight_total=old_weight,
            net_signed=net, net_amount=abs(net), net_direction=net_direction,
            money_received=received, money_paid=paid, payment_signed=signed_payment,
            residual_signed=residual, residual_amount=abs(residual),
            residual_direction=residual_direction, settlement_status=status,
            adjustment_signed=adjustment, account_residual_signed=account_residual)
