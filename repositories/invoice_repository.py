"""Persistence for canonical Billing V2 (the `invoices` family).

Every method runs on the caller's connection and transaction; nothing here
commits. BIGINT ids leave this repository as decimal strings (QML-safe); ids
passed in may be strings or integers.
"""
from datetime import date
from decimal import Decimal
import json
import re

from repositories.base_repository import BaseRepository


INVOICE_SEQUENCE_MAX = 99
_BIGINT_UNSIGNED_MAX = 18446744073709551615

# BIGINT columns returned by reads, converted to decimal strings.
_BIGINT_FIELDS = frozenset({
    "invoice_id", "customer_id", "source_invoice_id", "line_id", "inventory_item_id",
    "old_gold_movement_id", "quote_id", "payment_id", "reverses_payment_id",
    "event_id", "related_invoice_id", "snapshot_id",
})


class InvoiceNumberExhausted(RuntimeError):
    """All 99 invoice numbers of a business day are used."""


class InvoiceRepositoryError(RuntimeError):
    pass


def _id(value):
    """Accept a BIGINT id as int or decimal string; reject anything else."""
    if isinstance(value, bool):
        raise ValueError("Invalid id.")
    text = str(value).strip()
    # ASCII digits only: str.isdecimal() would also accept Arabic-Indic digits.
    if not re.fullmatch(r"[0-9]{1,20}", text) or not 0 < int(text) <= _BIGINT_UNSIGNED_MAX:
        raise ValueError("Invalid id.")
    return int(text)


def _optional_id(value):
    return None if value is None or value == "" else _id(value)


def _stringify_ids(row):
    if row is None:
        return None
    return {key: (str(value) if key in _BIGINT_FIELDS and value is not None else value)
            for key, value in row.items()}


def _json(value):
    if value is None:
        return None
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      default=lambda v: str(v) if isinstance(v, Decimal) else v.isoformat())


class InvoiceRepository(BaseRepository):

    def _execute(self, query, params=()):
        cursor = self.connection.cursor()
        try:
            cursor.execute(query, params)
            return cursor.lastrowid, cursor.rowcount
        finally:
            cursor.close()

    # ------------------------------------------------------------ numbering

    def next_invoice_number(self, business_date: date) -> str:
        """Allocate the next YYMMDDNN number for a business day (NN 01-99).

        The day's sequence row is locked until the caller's transaction ends, so
        concurrent finalizations are serialized. On first use of a day the row is
        seeded from invoice numbers already stored for that day (e.g. imported
        legacy bills); numbers that already exist are skipped, never reused.
        """
        if not isinstance(business_date, date):
            raise ValueError("business_date must be a date.")
        prefix = business_date.strftime("%y%m%d")
        self._execute(
            """
            INSERT INTO invoice_number_sequences (sequence_date, last_seq)
            SELECT %s, COALESCE(MAX(CAST(RIGHT(invoice_number, 2) AS UNSIGNED)), 0)
            FROM invoices
            WHERE invoice_number LIKE %s
              AND invoice_number REGEXP '^[0-9]{8}$'
            ON DUPLICATE KEY UPDATE sequence_date = sequence_date
            """,
            (business_date, prefix + "__"),
        )
        row = self._fetch_one(
            "SELECT last_seq FROM invoice_number_sequences WHERE sequence_date = %s FOR UPDATE",
            (business_date,),
        )
        sequence = int(row["last_seq"])
        while True:
            if sequence >= INVOICE_SEQUENCE_MAX:
                raise InvoiceNumberExhausted(
                    f"تم استخدام كل أرقام فواتير يوم {business_date.isoformat()} (99 فاتورة).")
            sequence += 1
            number = f"{prefix}{sequence:02d}"
            if self._fetch_one("SELECT 1 AS used FROM invoices WHERE invoice_number = %s",
                               (number,)) is None:
                break
        self._execute(
            "UPDATE invoice_number_sequences SET last_seq = %s WHERE sequence_date = %s",
            (sequence, business_date),
        )
        return number

    # ------------------------------------------------------------ locks

    def lock_customer(self, customer_id):
        return _stringify_ids(self._fetch_one(
            """
            SELECT
                c.id AS customer_id, c.customer_code, c.name, c.mobile, c.place,
                c.is_active, c.record_origin, c.data_completeness,
                ty.code AS customer_type_code, ty.name AS customer_type_name
            FROM customers c
            LEFT JOIN customer_types ty ON ty.id = c.customer_type_id
            WHERE c.id = %s
            FOR UPDATE OF c
            """,
            (_id(customer_id),),
        ))

    def lock_sale_items(self, item_ids):
        """Lock items in ascending id order (a fixed lock order avoids deadlocks).

        Returns rows in the requested order; missing ids are simply absent.
        """
        return self._sale_items(item_ids, lock=True)

    def read_sale_items(self, item_ids):
        """Same rows as lock_sale_items() without locks (preview only)."""
        return self._sale_items(item_ids, lock=False)

    def _sale_items(self, item_ids, lock):
        ids = [_id(value) for value in item_ids]
        if len(set(ids)) != len(ids):
            raise ValueError("Duplicate inventory item.")
        if not ids:
            return []
        placeholders = ", ".join(["%s"] * len(ids))
        rows = self._fetch_all(
            f"""
            SELECT
                ii.id AS inventory_item_id,
                ii.karat_id,
                k.karat_value,
                ii.weight_grams,
                ii.size,
                ii.stock_classification,
                ii.inventory_status,
                it.name AS item_type_name,
                f.name AS factory_name,
                cat.code AS category_code,
                ib.barcode_value
            FROM inventory_items ii
            JOIN item_types it ON it.id = ii.item_type_id
            JOIN karats k ON k.id = ii.karat_id
            LEFT JOIN factories f ON f.id = ii.factory_id
            LEFT JOIN item_categories cat ON cat.id = ii.category_id
            LEFT JOIN inventory_barcodes ib
                ON ib.inventory_item_id = ii.id AND ib.barcode_status = 'ACTIVE'
            WHERE ii.id IN ({placeholders})
            ORDER BY ii.id
            {"FOR UPDATE OF ii" if lock else ""}
            """,
            tuple(sorted(ids)),
        )
        by_id = {int(row["inventory_item_id"]): _stringify_ids(row) for row in rows}
        return [by_id[item_id] for item_id in ids if item_id in by_id]

    def find_item_id_by_barcode(self, barcode):
        """The item carrying this ACTIVE barcode, as a decimal string, or None."""
        row = self._fetch_one(
            "SELECT inventory_item_id FROM inventory_barcodes "
            "WHERE barcode_value = %s AND barcode_status = 'ACTIVE'",
            (str(barcode),),
        )
        return None if row is None else str(row["inventory_item_id"])

    def lock_invoice(self, invoice_id):
        return _stringify_ids(self._fetch_one(
            """
            SELECT id AS invoice_id, invoice_number, invoice_type, invoice_status,
                   record_origin, customer_id, net_amount, net_direction, settlement_status
            FROM invoices
            WHERE id = %s
            FOR UPDATE
            """,
            (_id(invoice_id),),
        ))

    # ------------------------------------------------------------ code lookups

    def _type_id(self, table, code):
        row = self._fetch_one(f"SELECT id, is_active FROM {table} WHERE code = %s", (code,))
        if row is None or not row["is_active"]:
            raise InvoiceRepositoryError(f"Missing or inactive code {code} in {table}.")
        return int(row["id"])

    def inventory_movement_type_id(self, code):
        return self._type_id("inventory_movement_types", code)

    def old_gold_movement_type_id(self, code):
        return self._type_id("old_gold_movement_types", code)

    def get_payment_method(self, code):
        return self._fetch_one(
            """
            SELECT id AS payment_method_id, code, name, affects_physical_cash,
                   requires_bank_account, is_active
            FROM payment_methods
            WHERE code = %s
            """,
            (code,),
        )

    # ------------------------------------------------------------ inserts

    def insert_invoice(self, *, invoice_number, invoice_type, customer_id, employee_id, invoice_date,
                       sell_total, buy_total, net_amount, net_direction, settlement_status,
                       created_by_user_id, finalized_at, notes=None):
        """Insert a FINALIZED native invoice (drafts are never persisted)."""
        invoice_id, _ = self._execute(
            """
            INSERT INTO invoices (
                invoice_number, invoice_type, invoice_status, record_origin,
                customer_id, employee_id, invoice_date,
                sell_total, buy_total, net_amount, net_direction, settlement_status,
                notes, created_by_user_id, finalized_by_user_id, finalized_at
            )
            VALUES (%s, %s, 'FINALIZED', 'NATIVE', %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (invoice_number, invoice_type, _optional_id(customer_id), employee_id, invoice_date,
             sell_total, buy_total, net_amount, net_direction, settlement_status,
             notes, created_by_user_id, created_by_user_id, finalized_at),
        )
        return str(invoice_id)

    def insert_customer_snapshot(self, *, invoice_id, customer, balance_before, balance_after,
                                 dead_cash_warning_active):
        """customer: a row from lock_customer() (or None for a walk-in)."""
        customer = customer or {}
        self._execute(
            """
            INSERT INTO invoice_customer_snapshots (
                invoice_id, customer_id, customer_code_snapshot, customer_name_snapshot,
                mobile_snapshot, place_snapshot, customer_type_snapshot,
                balance_before, balance_after, dead_cash_warning_active
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (_id(invoice_id), _optional_id(customer.get("customer_id")), customer.get("customer_code"),
             customer.get("name"), customer.get("mobile"), customer.get("place"),
             customer.get("customer_type_name"), balance_before, balance_after,
             bool(dead_cash_warning_active)),
        )

    def insert_price_snapshots(self, *, invoice_id, prices, source_code, source_name=None):
        """prices: iterable of dicts with karat_id, shop_buy_price_per_gram,
        shop_sell_price_per_gram, quote_id and provider_timestamp (each karat keeps its own)."""
        count = 0
        for price in prices:
            self._execute(
                """
                INSERT INTO invoice_price_snapshots (
                    invoice_id, karat_id, shop_buy_price_per_gram, shop_sell_price_per_gram,
                    quote_id, source_code_snapshot, source_name_snapshot, provider_timestamp
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (_id(invoice_id), int(price["karat_id"]), price["shop_buy_price_per_gram"],
                 price["shop_sell_price_per_gram"], _optional_id(price.get("quote_id")),
                 source_code, source_name, price.get("provider_timestamp")),
            )
            count += 1
        return count

    def insert_sale_line(self, *, invoice_id, line_number, item, gold_price_per_gram,
                         making_per_gram, making_total, gross_total, line_total, notes=None):
        """item: a row from lock_sale_items(); snapshots are frozen from it."""
        line_id, _ = self._execute(
            """
            INSERT INTO invoice_lines (
                invoice_id, record_origin, line_number, line_type, inventory_item_id,
                karat_id, weight_grams, quantity,
                item_name_snapshot, factory_snapshot, category_snapshot, size_snapshot, barcode_snapshot,
                gold_price_per_gram, making_per_gram, making_total, gross_total, line_total, notes
            )
            VALUES (%s, 'NATIVE', %s, 'SELL_INVENTORY', %s, %s, %s, 1,
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (_id(invoice_id), int(line_number), _id(item["inventory_item_id"]),
             int(item["karat_id"]), item["weight_grams"],
             item.get("item_type_name"), item.get("factory_name"), item.get("category_code"),
             item.get("size"), item.get("barcode_value"),
             gold_price_per_gram, making_per_gram, making_total, gross_total, line_total, notes),
        )
        return str(line_id)

    def insert_old_gold_line(self, *, invoice_id, line_number, karat_id, weight_grams,
                             gold_price_per_gram, gross_total, discount_type, discount_value,
                             discount_amount, line_total, old_gold_movement_id, notes=None):
        line_id, _ = self._execute(
            """
            INSERT INTO invoice_lines (
                invoice_id, record_origin, line_number, line_type, old_gold_movement_id,
                karat_id, weight_grams, quantity, gold_price_per_gram,
                gross_total, discount_type, discount_value, discount_amount, line_total, notes
            )
            VALUES (%s, 'NATIVE', %s, 'BUY_OLD_GOLD', %s, %s, %s, 1, %s, %s, %s, %s, %s, %s, %s)
            """,
            (_id(invoice_id), int(line_number), _id(old_gold_movement_id), int(karat_id), weight_grams,
             gold_price_per_gram, gross_total, discount_type, discount_value, discount_amount,
             line_total, notes),
        )
        return str(line_id)

    def insert_payment(self, *, invoice_id, payment_method_id, direction, amount,
                       created_by_user_id, bank_account_id=None, payment_reference=None,
                       notes=None, occurred_at=None):
        """Real money only; exchange offsets and balances never become payment rows."""
        payment_id, _ = self._execute(
            """
            INSERT INTO invoice_payments (
                invoice_id, payment_method_id, bank_account_id, direction, amount,
                payment_reference, notes, occurred_at, created_by_user_id
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, COALESCE(%s, CURRENT_TIMESTAMP), %s)
            """,
            (_id(invoice_id), int(payment_method_id), bank_account_id, direction, amount,
             payment_reference, notes, occurred_at, created_by_user_id),
        )
        return str(payment_id)

    def insert_event(self, *, invoice_id, event_type, performed_by_user_id, details=None,
                     notes=None, related_invoice_id=None):
        event_id, _ = self._execute(
            """
            INSERT INTO invoice_events (
                invoice_id, event_type, related_invoice_id, notes, details_json, performed_by_user_id
            )
            VALUES (%s, %s, %s, %s, %s, %s)
            """,
            (_id(invoice_id), event_type, _optional_id(related_invoice_id), notes, _json(details),
             performed_by_user_id),
        )
        return str(event_id)

    # ------------------------------------------------------------ inventory / old gold

    def mark_item_sold(self, *, inventory_item_id, updated_by_user_id):
        _, rowcount = self._execute(
            """
            UPDATE inventory_items
            SET inventory_status = 'SOLD', updated_by_user_id = %s
            WHERE id = %s AND inventory_status = 'IN_STOCK'
            """,
            (updated_by_user_id, _id(inventory_item_id)),
        )
        if rowcount != 1:
            raise InvoiceRepositoryError(f"Inventory item {inventory_item_id} is no longer in stock.")

    def add_inventory_sold_movement(self, *, item, invoice_id, invoice_number,
                                    performed_by_user_id, performed_by_employee_id):
        movement_id, _ = self._execute(
            """
            INSERT INTO inventory_movements (
                inventory_item_id, movement_type_id,
                from_stock_classification, to_stock_classification,
                from_inventory_status, to_inventory_status,
                from_weight_grams, to_weight_grams,
                reference_type, reference_id, reference_code, reason,
                performed_by_user_id, performed_by_employee_id
            )
            VALUES (%s, %s, %s, %s, 'IN_STOCK', 'SOLD', %s, %s, 'INVOICE', %s, %s, %s, %s, %s)
            """,
            (_id(item["inventory_item_id"]), self.inventory_movement_type_id("SOLD"),
             item["stock_classification"], item["stock_classification"],
             item["weight_grams"], item["weight_grams"],
             _id(invoice_id), invoice_number, f"فاتورة {invoice_number}",
             performed_by_user_id, performed_by_employee_id),
        )
        return str(movement_id)

    def add_customer_buy_in_movement(self, *, karat_id, weight_grams, gold_price_per_gram, cash_value,
                                     price_quote_id, movement_group_id, invoice_id, invoice_number,
                                     customer_id, created_by_user_id, received_by_employee_id, notes=None):
        """Old gold bought on an invoice: a COUNTED CUSTOMER_BUY_IN ledger movement.

        The link is reference_type='INVOICE'; old_gold_movements.invoice_id stays NULL
        because that column belongs to the frozen billing_invoices family.
        """
        movement_id, _ = self._execute(
            """
            INSERT INTO old_gold_movements (
                movement_type_id, karat_id, weight_delta_grams, balance_effect,
                gold_price_per_gram, cash_value, price_quote_id, movement_group_id,
                reference_type, reference_id, reference_code, notes,
                created_by_user_id, performed_by_user_id, received_by_employee_id,
                movement_code, customer_id
            )
            VALUES (%s, %s, %s, 'COUNTED', %s, %s, %s, %s, 'INVOICE', %s, %s, %s, %s, %s, %s,
                    'CUSTOMER_BUY_IN', %s)
            """,
            (self.old_gold_movement_type_id("CUSTOMER_BUY_IN"), int(karat_id), weight_grams,
             gold_price_per_gram, cash_value, _optional_id(price_quote_id), movement_group_id,
             _id(invoice_id), invoice_number, notes, created_by_user_id, created_by_user_id,
             received_by_employee_id, _optional_id(customer_id)),
        )
        return str(movement_id)

    # ------------------------------------------------------------ reads

    _HEADER = """
        SELECT
            i.id AS invoice_id, i.invoice_number, i.invoice_type, i.invoice_status, i.record_origin,
            i.customer_id, i.employee_id, e.name AS employee_name, i.invoice_date,
            i.sell_total, i.buy_total, i.net_amount, i.net_direction, i.settlement_status,
            i.notes, i.source_invoice_id, i.legacy_source_system, i.legacy_bill_number,
            i.legacy_completeness, i.created_by_user_id, i.finalized_by_user_id, i.finalized_at,
            i.voided_by_user_id, i.voided_at, i.void_reason,
            s.customer_code_snapshot, s.customer_name_snapshot, s.mobile_snapshot, s.place_snapshot,
            s.balance_before, s.balance_after, s.dead_cash_warning_active
        FROM invoices i
        LEFT JOIN employees e ON e.id = i.employee_id
        LEFT JOIN invoice_customer_snapshots s ON s.invoice_id = i.id
    """

    def get_invoice(self, invoice_id):
        """Header, lines, payments, price snapshots and events; None if missing."""
        invoice_id = _id(invoice_id)
        header = self._fetch_one(self._HEADER + " WHERE i.id = %s", (invoice_id,))
        if header is None:
            return None
        lines = self._fetch_all(
            """
            SELECT id AS line_id, line_number, line_type, record_origin, inventory_item_id,
                   old_gold_movement_id, karat_id, weight_grams, quantity,
                   item_name_snapshot, factory_snapshot, category_snapshot, size_snapshot,
                   barcode_snapshot, gold_price_per_gram, making_per_gram, making_total,
                   gross_total, discount_type, discount_value, discount_amount, line_total, notes
            FROM invoice_lines WHERE invoice_id = %s ORDER BY line_number
            """, (invoice_id,))
        payments = self._fetch_all(
            """
            SELECT p.id AS payment_id, pm.code AS payment_method_code, pm.name AS payment_method_name,
                   p.direction, p.amount, p.payment_reference, p.reverses_payment_id,
                   p.correction_group_id, p.notes, p.occurred_at, p.created_by_user_id
            FROM invoice_payments p JOIN payment_methods pm ON pm.id = p.payment_method_id
            WHERE p.invoice_id = %s ORDER BY p.id
            """, (invoice_id,))
        prices = self._fetch_all(
            """
            SELECT id AS snapshot_id, karat_id, shop_buy_price_per_gram, shop_sell_price_per_gram,
                   quote_id, source_code_snapshot, source_name_snapshot, provider_timestamp, captured_at
            FROM invoice_price_snapshots WHERE invoice_id = %s ORDER BY karat_id
            """, (invoice_id,))
        events = self._fetch_all(
            """
            SELECT id AS event_id, event_type, related_invoice_id, notes, details_json,
                   occurred_at, performed_by_user_id
            FROM invoice_events WHERE invoice_id = %s ORDER BY id
            """, (invoice_id,))
        result = _stringify_ids(header)
        result["lines"] = [_stringify_ids(row) for row in lines]
        result["payments"] = [_stringify_ids(row) for row in payments]
        result["price_snapshots"] = [_stringify_ids(row) for row in prices]
        result["events"] = [_stringify_ids(row) for row in events]
        return result

    def search_invoices(self, *, number=None, customer_id=None, date_from=None, date_to_exclusive=None,
                        status=None, record_origin=None, limit=101, offset=0):
        """Newest first. `number` matches the invoice number or legacy bill number prefix."""
        number = (number or "").strip()
        escaped = number.replace("!", "!!").replace("%", "!%").replace("_", "!_") + "%"
        rows = self._fetch_all(
            self._HEADER + """
            WHERE (%s OR i.invoice_number LIKE %s ESCAPE '!' OR i.legacy_bill_number LIKE %s ESCAPE '!')
              AND (%s IS NULL OR i.customer_id = %s)
              AND (%s IS NULL OR i.invoice_date >= %s)
              AND (%s IS NULL OR i.invoice_date < %s)
              AND (%s IS NULL OR i.invoice_status = %s)
              AND (%s IS NULL OR i.record_origin = %s)
            ORDER BY i.invoice_date DESC, i.id DESC
            LIMIT %s OFFSET %s
            """,
            (not number, escaped, escaped,
             _optional_id(customer_id), _optional_id(customer_id),
             date_from, date_from, date_to_exclusive, date_to_exclusive,
             status, status, record_origin, record_origin, int(limit), int(offset)),
        )
        return [_stringify_ids(row) for row in rows]
