"""Canonical Billing V2: preview and atomic finalization of native invoices.

The draft carries only what an employee chooses or measures: inventory item
ids, making charges, old-gold karat/weight/discount, payments, customer and
salesperson. Everything else (item karat and weight, gold prices, every total,
N/P/R) is recalculated here with InvoiceCalculator from the database, so totals
coming from QML are never trusted.

Finalization is one transaction. Lock order: actor/user rows -> salesperson ->
price settings/active prices -> customer -> inventory items (ascending id) ->
the day's invoice-number sequence. Any failure rolls everything back.

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
from repositories.invoice_repository import InvoiceRepository
from services.authorization_service import AuthorizationService
from services.gold_price_service import GoldPriceService
from services.invoice_calculator import InvoiceCalculator as Calc


ALLOWED_PAYMENT_METHODS = ("PHYSICAL_CASH", "MOBILE_WALLET")
# Egypt standard time and summer time.
EGYPT_UTC_OFFSETS = (timedelta(hours=2), timedelta(hours=3))


class _DiscardPreview(Exception):
    """Raised inside preview's transaction so the factory rolls it back."""


class InvoiceError(ValueError):
    """A business rule refused the invoice; `code` is stable for the UI."""

    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


IDENTIFICATION_METHODS = ("SCAN", "SEARCH")
ITEM_SEARCH_LIMIT = 30


@dataclass(frozen=True)
class SaleLineInput:
    inventory_item_id: str
    making_per_gram: str | None = None
    making_total: str | None = None
    # How the employee identified the piece: SCAN (barcode) or SEARCH (بحث عن قطعة).
    identified_by: str = "SCAN"


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


@dataclass(frozen=True)
class InvoiceDraft:
    salesperson_employee_id: int
    customer_id: str | None = None
    sale_lines: tuple = field(default_factory=tuple)
    old_gold_lines: tuple = field(default_factory=tuple)
    payments: tuple = field(default_factory=tuple)
    notes: str | None = None


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


class InvoiceService:
    def __init__(self, session, *, gold_prices=None, authorization=None,
                 transaction_factory=invoice_transaction,
                 invoice_repository=InvoiceRepository,
                 customer_repository=CustomerRepository,
                 dead_cash_repository=DeadCashRepository):
        self._session = session
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

    def _items(self, invoices, draft):
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

    def _calculate(self, draft, items, price_set, net_hint=None):
        """Authoritative lines and settlement from DB rows and the locked price set."""
        prices = price_set.prices
        sales = []
        for line, item in zip(draft.sale_lines, items):
            price = prices.get(int(item["karat_id"]))
            if price is None:
                raise InvoiceError("PRICE_MISSING", "لا يوجد سعر معتمد لعيار القطعة.")
            sales.append(Calc.sale_line(item["weight_grams"], price.shop_sell_price_per_gram,
                                        making_per_gram=line.making_per_gram,
                                        making_total=line.making_total))
        olds = []
        for line in draft.old_gold_lines:
            if isinstance(line.karat_id, bool) or int(line.karat_id) not in prices:
                raise InvoiceError("PRICE_MISSING", "لا يوجد سعر معتمد لعيار الكسر.")
            olds.append(Calc.old_gold_line(line.weight_grams,
                                           prices[int(line.karat_id)].shop_buy_price_per_gram,
                                           discount_type=line.discount_type,
                                           discount_value=line.discount_value))
        # Direction of an omitted payment direction follows the commercial net.
        net = Calc.settlement(sales, olds).net_signed
        payments = []
        for payment in draft.payments:
            if payment.method_code not in ALLOWED_PAYMENT_METHODS:
                raise InvoiceError("PAYMENT_METHOD_NOT_ALLOWED",
                                   "طريقة الدفع غير متاحة حالياً (متاح: نقدي، محفظة موبايل).")
            direction = payment.direction or ("IN" if net > 0 else "OUT")
            payments.append((payment, Calc.payment(direction, payment.amount)))
        settlement = Calc.settlement(sales, olds, [p for _, p in payments])
        return sales, olds, payments, settlement

    def _rules(self, settlement, customer, warning, dead_cash_confirmed):
        """Rules shared by preview (reported) and finalize (enforced)."""
        problems = []
        if settlement.requires_customer and customer is None:
            problems.append(InvoiceError("CUSTOMER_REQUIRED",
                                         "يوجد متبقٍ على الفاتورة؛ يجب اختيار عميل."))
        if warning and settlement.residual_signed > 0 and not dead_cash_confirmed:
            problems.append(InvoiceError("DEAD_CASH_CONFIRMATION_REQUIRED",
                                         "العميل عليه تحذير ديون معدومة؛ يلزم تأكيد إنشاء مديونية جديدة."))
        return problems

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
                "factory_name": item.get("factory_name"), "size": item.get("size")}

    def search_sellable_items(self, *, text="", karat_id=None, weight=None, tolerance="0.05", size="",
                              exclude_ids=(), _expected_actor=None):
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
                raise InvoiceError("SEARCH_WEIGHT_INVALID", "اكتب وزناً صحيحاً وهامشاً من 0 إلى 5 جم.")
            weight_min, weight_max = target - spread, target + spread
        if karat_id is not None and (isinstance(karat_id, bool) or int(karat_id) <= 0):
            karat_id = None
        rows = self._read_only(lambda connection: self._invoices(connection).search_sellable_items(
            text=text, karat_id=None if karat_id is None else int(karat_id), weight_min=weight_min,
            weight_max=weight_max, size=size, exclude_ids=tuple(exclude_ids), weight_target=target,
            limit=ITEM_SEARCH_LIMIT + 1))
        items = [{"inventory_item_id": row["inventory_item_id"], "barcode": row.get("barcode_value"),
                  "item_name": row.get("item_type_name"), "karat_id": int(row["karat_id"]),
                  "karat": row.get("karat_value"), "weight_grams": _money(row["weight_grams"]),
                  "factory_name": row.get("factory_name"), "size": row.get("size"),
                  "category_name": row.get("category_name")} for row in rows[:ITEM_SEARCH_LIMIT]]
        return {"items": items, "truncated": len(rows) > ITEM_SEARCH_LIMIT}

    def preview(self, draft: InvoiceDraft, *, _expected_actor=None):
        """Read-only recalculation for the screen; nothing is written."""
        self._require_create(_expected_actor)
        try:
            with self._transaction() as connection:
                invoices = self._invoices(connection)
                customers, dead_cash = self._customers(connection), self._dead_cash(connection)
                self._salesperson(connection, draft.salesperson_employee_id, lock=False)
                price_set = self._gold_prices.get_billing_price_set(self._karat_ids(connection))
                customer, balance, warning = self._customer(invoices, customers, dead_cash,
                                                            draft.customer_id, lock=False)
                ids = [line.inventory_item_id for line in draft.sale_lines]
                items = self._preview_items(invoices, ids)
                calculated = self._calculate(draft, items, price_set)
                # Leave through the transaction's own rollback path: preview never commits.
                raise _DiscardPreview()
        except _DiscardPreview:
            pass
        sales, olds, _, settlement = calculated
        problems = self._rules(settlement, customer, warning, dead_cash_confirmed=False)
        return {
            "price_token": price_set.price_token,
            "price_status": price_set.status,
            "requires_price_acknowledgement": price_set.requires_acknowledgement,
            "price_warnings": list(price_set.warnings),
            "sale_lines": [{"inventory_item_id": item["inventory_item_id"], "barcode": item.get("barcode_value"),
                            "item_name": item.get("item_type_name"), "karat": item.get("karat_value"),
                            "weight_grams": _money(line.weight_grams),
                            "gold_price_per_gram": _money(line.gold_price_per_gram),
                            "making_per_gram": _money(line.making_per_gram),
                            "making_total": _money(line.making_total), "gold_value": _money(line.gold_value),
                            "line_total": _money(line.line_total)} for line, item in zip(sales, items)],
            "old_gold_lines": [{"karat_id": int(d.karat_id), "weight_grams": _money(line.weight_grams),
                                "gold_price_per_gram": _money(line.gold_price_per_gram),
                                "gross_total": _money(line.gross_total),
                                "discount_amount": _money(line.discount_amount),
                                "line_total": _money(line.line_total)}
                               for line, d in zip(olds, draft.old_gold_lines)],
            **self._settlement_view(settlement),
            "customer_balance_before": _money(balance),
            "customer_balance_after": _money(None if balance is None else balance + settlement.residual_signed),
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
    def _settlement_view(settlement):
        return {
            "invoice_type": settlement.invoice_type,
            "sale_total": _money(settlement.sale_total),
            "old_gold_total": _money(settlement.old_gold_total),
            "net_amount": _money(settlement.net_amount),
            "net_direction": settlement.net_direction,
            "money_received": _money(settlement.money_received),
            "money_paid": _money(settlement.money_paid),
            "residual_amount": _money(settlement.residual_amount),
            "residual_direction": settlement.residual_direction,
            "settlement_status": settlement.settlement_status,
        }

    # ------------------------------------------------------------ finalize

    def finalize(self, draft: InvoiceDraft, price_token: str, *, stale_acknowledged=False,
                 dead_cash_confirmed=False, _expected_actor=None):
        session_actor = self._actor(_expected_actor)
        if not draft.sale_lines and not draft.old_gold_lines:
            raise InvoiceError("EMPTY_INVOICE", "الفاتورة لا تحتوي على أي بنود.")
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
            customer, balance, warning = self._customer(invoices, customers, dead_cash,
                                                        draft.customer_id, lock=True)
            items = self._items(invoices, draft)
            sales, olds, payments, settlement = self._calculate(draft, items, price_set)
            problems = self._rules(settlement, customer, warning, dead_cash_confirmed is True)
            if problems:
                raise problems[0]
            methods = {}
            for payment, _ in payments:
                method = invoices.get_payment_method(payment.method_code)
                if method is None or not method["is_active"] or method["requires_bank_account"]:
                    raise InvoiceError("PAYMENT_METHOD_NOT_ALLOWED", "طريقة الدفع غير متاحة.")
                methods[payment.method_code] = method

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
                    balance_after=balance + settlement.residual_signed, dead_cash_warning_active=warning)
            invoices.insert_price_snapshots(
                invoice_id=invoice_id, source_code=price_set.provenance.get("source_code"),
                prices=[{"karat_id": p.karat_id, "shop_buy_price_per_gram": p.shop_buy_price_per_gram,
                         "shop_sell_price_per_gram": p.shop_sell_price_per_gram, "quote_id": p.quote_id,
                         # UTC like the Gold Price Engine tables; each karat keeps its own time.
                         "provider_timestamp": p.provider_timestamp.replace(tzinfo=None)
                         if p.provider_timestamp is not None else None}
                        for p in price_set.prices.values()])

            line_number = 0
            for line, item in zip(sales, items):
                line_number += 1
                invoices.insert_sale_line(
                    invoice_id=invoice_id, line_number=line_number, item=item,
                    gold_price_per_gram=line.gold_price_per_gram, making_per_gram=line.making_per_gram,
                    making_total=line.making_total, gross_total=line.gold_value, line_total=line.line_total)
                invoices.mark_item_sold(inventory_item_id=item["inventory_item_id"],
                                        updated_by_user_id=actor.user_id)
                invoices.add_inventory_sold_movement(
                    item=item, invoice_id=invoice_id, invoice_number=number,
                    performed_by_user_id=actor.user_id, performed_by_employee_id=int(salesperson["id"]))
            group = str(uuid.uuid4())
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

            # Real money only: invoice_payments. No cash_movements / bank_movements in V2.
            for payment, calculated in payments:
                invoices.insert_payment(
                    invoice_id=invoice_id, payment_method_id=methods[payment.method_code]["payment_method_id"],
                    direction=calculated.direction, amount=calculated.amount,
                    created_by_user_id=actor.user_id, payment_reference=payment.reference, occurred_at=now)

            residual = settlement.residual_signed
            if residual != 0:
                customers.add_transaction(
                    customer_id=int(customer["customer_id"]),
                    type_code="SALE_BALANCE_DUE" if residual > 0 else "BUY_BALANCE_DUE",
                    amount_delta=residual, created_by_user_id=actor.user_id,
                    reference_type="INVOICE", reference_id=invoice_id, reference_code=number,
                    description=f"متبقي فاتورة {number}")

            details = {
                "price_token": price_set.price_token, "price_status": price_set.status,
                "stale_acknowledged": bool(price_set.requires_acknowledgement and stale_acknowledged),
                "dead_cash_warning_active": warning,
                "dead_cash_confirmed": bool(warning and residual > 0 and dead_cash_confirmed),
                "salesperson_employee_id": int(salesperson["id"]),
                "actor_employee_id": actor.employee_id,
                "N": settlement.net_signed, "P": settlement.payment_signed, "R": residual,
            }
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
                **self._settlement_view(settlement)}

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
