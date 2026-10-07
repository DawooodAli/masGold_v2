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


# History list: the invoice kind from its saved lines (the same rule as the detail page).
HISTORY_KIND_FOR_TYPE = {"SELL": "SALE", "BUY": "OLD_GOLD_PURCHASE", "MIXED": "MIXED"}
_HISTORY_SOLD = ("EXISTS (SELECT 1 FROM invoice_lines hl WHERE hl.invoice_id = i.id "
                 "AND hl.line_type IN ('SELL_INVENTORY', 'SELL_UNIDENTIFIED', 'SELL_OLD_GOLD'))")
_HISTORY_OLD = ("EXISTS (SELECT 1 FROM invoice_lines ho WHERE ho.invoice_id = i.id "
                "AND ho.line_type = 'BUY_OLD_GOLD')")
_HISTORY_KIND_SQL = (f"(CASE WHEN {_HISTORY_SOLD} AND {_HISTORY_OLD} THEN 'MIXED' "
                     f"WHEN {_HISTORY_OLD} THEN 'OLD_GOLD_PURCHASE' ELSE 'SALE' END)")



def lock_available_old_gold(repository, karat_id):
    """AVAILABLE Scrap of one karat, locked (032): the COUNTED ledger rows and the HELD Exchange holds are
    both locked FOR UPDATE, so neither a consumer nor a manager decision can race this answer."""
    rows = repository._fetch_all(
        """
        SELECT weight_delta_grams FROM old_gold_movements
        WHERE karat_id = %s AND weight_delta_grams IS NOT NULL AND balance_effect = 'COUNTED'
        FOR UPDATE
        """, (int(karat_id),))
    held = repository._fetch_all(
        "SELECT weight_grams FROM old_gold_holds WHERE karat_id = %s AND hold_status = 'HELD' FOR UPDATE",
        (int(karat_id),))
    return (sum((Decimal(str(row["weight_delta_grams"])) for row in rows), Decimal("0.000"))
            - sum((Decimal(str(row["weight_grams"])) for row in held), Decimal("0.000")))

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
                ib.barcode_value,
                ii.item_type_id, ii.factory_id, ii.category_id, ii.notes AS item_notes
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

    def search_sellable_items(self, *, text="", karat_id=None, weight_min=None, weight_max=None, size="",
                              exclude_ids=(), weight_target=None, limit=31, item_type_id=None, factory_id=None):
        """IN_STOCK items matching practical filters (no locks). Never picks one:
        the caller shows every candidate and the employee chooses."""
        from utils.search_normalization import mysql_search_expression, normalize_search_text
        text, size = (text or "").strip(), (size or "").strip()
        # Same search-only Arabic folding as Customers V2 (أ/إ/آ → ا, ة → ه, ى → ي, marks);
        # stored names are compared through the folding expression and never changed.
        folded = normalize_search_text(text)

        def like(value):
            return "%" + value.replace("!", "!!").replace("%", "!%").replace("_", "!_") + "%"
        type_sql, type_params = mysql_search_expression("it.name")
        factory_sql, factory_params = mysql_search_expression("f.name")
        category_sql, category_params = mysql_search_expression("cat.name")
        excluded = sorted({_id(value) for value in exclude_ids})
        not_in = f"AND ii.id NOT IN ({', '.join(['%s'] * len(excluded))})" if excluded else ""
        order = "ABS(ii.weight_grams - %s), ii.id DESC" if weight_target is not None else "ii.id DESC"
        params = [not text,
                  bool(folded), *type_params, like(folded), bool(folded), *factory_params, like(folded),
                  bool(folded), *category_params, like(folded), like(text), like(text),
                  karat_id, karat_id, item_type_id, item_type_id, factory_id, factory_id,
                  weight_min, weight_min, weight_max, weight_max, size, like(size),
                  *excluded]
        if weight_target is not None:
            params.append(weight_target)
        params.append(int(limit))
        rows = self._fetch_all(
            f"""
            SELECT
                ii.id AS inventory_item_id, ii.karat_id, k.karat_value, ii.weight_grams, ii.size,
                it.name AS item_type_name, f.name AS factory_name,
                cat.name AS category_name, cat.code AS category_code, ib.barcode_value,
                ii.item_type_id, ii.factory_id, ii.category_id, ii.notes AS item_notes
            FROM inventory_items ii
            JOIN item_types it ON it.id = ii.item_type_id
            JOIN karats k ON k.id = ii.karat_id
            LEFT JOIN factories f ON f.id = ii.factory_id
            LEFT JOIN item_categories cat ON cat.id = ii.category_id
            LEFT JOIN inventory_barcodes ib
                ON ib.inventory_item_id = ii.id AND ib.barcode_status = 'ACTIVE'
            WHERE ii.inventory_status = 'IN_STOCK'
              AND (%s
                   OR (%s AND {type_sql} LIKE %s ESCAPE '!')
                   OR (%s AND {factory_sql} LIKE %s ESCAPE '!')
                   OR (%s AND {category_sql} LIKE %s ESCAPE '!')
                   OR cat.code LIKE %s ESCAPE '!' OR ib.barcode_value LIKE %s ESCAPE '!')
              AND (%s IS NULL OR ii.karat_id = %s)
              AND (%s IS NULL OR ii.item_type_id = %s)
              AND (%s IS NULL OR ii.factory_id = %s)
              AND (%s IS NULL OR ii.weight_grams >= %s)
              AND (%s IS NULL OR ii.weight_grams <= %s)
              AND (%s = '' OR ii.size LIKE %s ESCAPE '!')
              {not_in}
            ORDER BY {order}
            LIMIT %s
            """,
            tuple(params),
        )
        return [_stringify_ids(row) for row in rows]

    def sellable_facets(self, *, item_type_id=None, karat_id=None, exclude_ids=()):
        """What IN_STOCK stock actually offers at each step of الصنف → العيار → المصنع."""
        excluded = sorted({_id(value) for value in exclude_ids})
        not_in = f"AND ii.id NOT IN ({', '.join(['%s'] * len(excluded))})" if excluded else ""

        def facet(select, join, where, params):
            return self._fetch_all(
                f"""
                SELECT {select}, COUNT(*) AS item_count
                FROM inventory_items ii {join}
                WHERE ii.inventory_status = 'IN_STOCK' {where} {not_in}
                GROUP BY 1, 2 ORDER BY 2
                """, (*params, *excluded))
        types = facet("it.id AS id, it.name AS name", "JOIN item_types it ON it.id = ii.item_type_id", "", ())
        karats = facet("k.id AS id, k.karat_value AS name", "JOIN karats k ON k.id = ii.karat_id",
                       "AND ii.item_type_id = %s", (item_type_id,)) if item_type_id else []
        factories = facet("f.id AS id, f.name AS name", "JOIN factories f ON f.id = ii.factory_id",
                          "AND ii.item_type_id = %s AND ii.karat_id = %s",
                          (item_type_id, karat_id)) if item_type_id and karat_id else []
        return {"item_types": types, "karats": karats, "factories": factories}

    def reference_lists(self):
        """Active item types and factories (for manual / old-gold-stock sales)."""
        return {"item_types": self._fetch_all(
                    "SELECT id, name, default_category_id FROM item_types WHERE is_active = TRUE ORDER BY name"),
                "factories": self._fetch_all(
                    "SELECT id, name FROM factories WHERE is_active = TRUE ORDER BY name")}

    def get_item_type(self, item_type_id):
        return self._fetch_one("SELECT id, name, default_category_id, is_active FROM item_types WHERE id = %s",
                               (int(item_type_id),))

    def get_factory(self, factory_id):
        return self._fetch_one("SELECT id, name, is_active FROM factories WHERE id = %s", (int(factory_id),))

    def category_ranges(self, category_ids):
        ids = sorted({int(i) for i in category_ids if i is not None})
        if not ids:
            return {}
        rows = self._fetch_all(
            f"SELECT id, code, name, sale_making_min_per_gram, sale_making_max_per_gram, "
            f"manager_approval_below_per_gram "
            f"FROM item_categories WHERE id IN ({', '.join(['%s'] * len(ids))})", tuple(ids))
        return {int(row["id"]): row for row in rows}

    def billing_settings(self):
        return self._fetch_one(
            "SELECT settlement_self_limit, settlement_approval_limit, old_gold_discount_default_percent "
            "FROM billing_settings WHERE id = 1")

    # ------------------------------------------------------------ old-gold stock (weight ledger)

    def old_gold_balances(self):
        """AVAILABLE Scrap per karat (032): COUNTED grams (legacy REFERENCE_ONLY history excluded) minus the
        Exchange-returned weight still HELD for the manager's decision. What F7 may consume."""
        return self._fetch_all(
            """
            SELECT k.id AS karat_id, k.karat_value,
                   COALESCE(SUM(og.weight_delta_grams), 0)
                   - (SELECT COALESCE(SUM(h.weight_grams), 0) FROM old_gold_holds h WHERE h.karat_id = k.id AND h.hold_status = 'HELD') AS balance_grams,
                   COALESCE(SUM(og.weight_delta_grams), 0) AS physical_grams,
                   (SELECT COALESCE(SUM(h.weight_grams), 0) FROM old_gold_holds h WHERE h.karat_id = k.id AND h.hold_status = 'HELD') AS held_grams
            FROM karats k
            LEFT JOIN old_gold_movements og
                ON og.karat_id = k.id AND og.weight_delta_grams IS NOT NULL AND og.balance_effect = 'COUNTED'
            WHERE k.is_active = TRUE
            GROUP BY k.id, k.karat_value
            ORDER BY k.karat_value
            """)

    def lock_old_gold_balance(self, karat_id):
        """Locks this karat's COUNTED ledger rows (the same lock the karat-conversion path takes) and its
        HELD Scrap holds, and returns the AVAILABLE balance before this transaction's own movements."""
        return lock_available_old_gold(self, karat_id)

    def held_old_gold(self, karat_id):
        """Grams HELD for a manager decision (032); a plain read — callers lock the holds first."""
        row = self._fetch_one(
            "SELECT COALESCE(SUM(weight_grams), 0) AS held FROM old_gold_holds "
            "WHERE karat_id = %s AND hold_status = 'HELD'", (int(karat_id),))
        return Decimal(str(row["held"])) if row else Decimal("0.000")

    def add_invoice_sale_out_movement(self, *, karat_id, weight_grams, gold_price_per_gram, cash_value,
                                      price_quote_id, movement_group_id, invoice_id, invoice_number,
                                      customer_id, created_by_user_id, employee_id, notes=None):
        """Weight sold from the old-gold stock on an invoice: COUNTED INVOICE_SALE_OUT (negative)."""
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
                    'INVOICE_SALE_OUT', %s)
            """,
            (self.old_gold_movement_type_id("INVOICE_SALE_OUT"), int(karat_id), -Decimal(str(weight_grams)),
             gold_price_per_gram, cash_value, _optional_id(price_quote_id), movement_group_id,
             _id(invoice_id), invoice_number, notes, created_by_user_id, created_by_user_id,
             employee_id, _optional_id(customer_id)),
        )
        return str(movement_id)

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

    def active_bank_accounts(self):
        """Configured bank / wallet accounts (017 account_type); never invented."""
        return self._fetch_all(
            "SELECT id, name, account_type, bank_name, account_label FROM bank_accounts "
            "WHERE is_active = TRUE ORDER BY account_type, name")

    def get_bank_account(self, bank_account_id, lock=False):
        return self._fetch_one(
            "SELECT id, name, account_type, is_active FROM bank_accounts WHERE id = %s"
            + (" FOR SHARE" if lock else ""), (int(bank_account_id),))

    def active_cash_accounts(self):
        return self._fetch_all("SELECT id, code, name FROM cash_accounts WHERE is_active = TRUE ORDER BY id")

    # ------------------------------------------------------------ real money ledgers (007)

    def add_cash_movement(self, *, cash_account_id, invoice_payment_id, direction, amount, invoice_id,
                          invoice_number, created_by_user_id, occurred_at):
        """Physical cash of one invoice payment: +IN / −OUT on the shop's cash account."""
        movement_id, _ = self._execute(
            """
            INSERT INTO cash_movements (
                cash_account_id, movement_type_id, amount_delta, invoice_payment_id,
                reference_type, reference_id, reference_code, description, occurred_at, created_by_user_id
            )
            SELECT %s, t.id, %s, %s, 'INVOICE', %s, %s, %s, %s, %s
            FROM cash_movement_types t WHERE t.code = %s
            """,
            (int(cash_account_id), amount if direction == "IN" else -amount, _id(invoice_payment_id),
             _id(invoice_id), invoice_number, f"فاتورة {invoice_number}", occurred_at, created_by_user_id,
             f"INVOICE_PAYMENT_{direction}"),
        )
        if not movement_id:
            raise RuntimeError("cash movement type INVOICE_PAYMENT_%s is missing" % direction)
        return str(movement_id)

    def add_bank_movement(self, *, bank_account_id, invoice_payment_id, direction, amount, invoice_id,
                          invoice_number, created_by_user_id, occurred_at, payment_reference=None):
        """Wallet / InstaPay / bank money of one invoice payment on its selected account."""
        movement_id, _ = self._execute(
            """
            INSERT INTO bank_movements (
                bank_account_id, movement_type_id, amount_delta, invoice_payment_id, payment_reference,
                reference_type, reference_id, reference_code, description, occurred_at, created_by_user_id
            )
            SELECT %s, t.id, %s, %s, %s, 'INVOICE', %s, %s, %s, %s, %s
            FROM bank_movement_types t WHERE t.code = %s
            """,
            (int(bank_account_id), amount if direction == "IN" else -amount, _id(invoice_payment_id),
             payment_reference, _id(invoice_id), invoice_number, f"فاتورة {invoice_number}", occurred_at,
             created_by_user_id, f"INVOICE_PAYMENT_{direction}"),
        )
        if not movement_id:
            raise RuntimeError("bank movement type INVOICE_PAYMENT_%s is missing" % direction)
        return str(movement_id)

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
                         making_per_gram, making_total, gross_total, line_total, notes=None,
                         identification_method=None):
        """item: a row from lock_sale_items(); snapshots are frozen from it."""
        line_id, _ = self._execute(
            """
            INSERT INTO invoice_lines (
                invoice_id, record_origin, line_number, line_type, identification_method, inventory_item_id,
                karat_id, weight_grams, quantity,
                item_name_snapshot, factory_snapshot, category_snapshot, size_snapshot, barcode_snapshot,
                gold_price_per_gram, making_per_gram, making_total, gross_total, line_total, notes
            )
            VALUES (%s, 'NATIVE', %s, 'SELL_INVENTORY', %s, %s, %s, %s, 1,
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (_id(invoice_id), int(line_number), identification_method, _id(item["inventory_item_id"]),
             int(item["karat_id"]), item["weight_grams"],
             item.get("item_type_name"), item.get("factory_name"), item.get("category_code"),
             item.get("size"), item.get("barcode_value"),
             gold_price_per_gram, making_per_gram, making_total, gross_total, line_total, notes),
        )
        return str(line_id)

    def insert_described_sale_line(self, *, invoice_id, line_number, line_type, karat_id, weight_grams,
                                   item_name, factory_name, size, gold_price_per_gram, making_total,
                                   gross_total, line_total, old_gold_movement_id=None, notes=None):
        """SELL_UNIDENTIFIED (F5) or SELL_OLD_GOLD (F7): never an inventory identity."""
        if line_type not in ("SELL_UNIDENTIFIED", "SELL_OLD_GOLD"):
            raise ValueError("Unsupported described sale line.")
        method = "MANUAL" if line_type == "SELL_UNIDENTIFIED" else "OLD_GOLD_STOCK"
        line_id, _ = self._execute(
            """
            INSERT INTO invoice_lines (
                invoice_id, record_origin, line_number, line_type, identification_method,
                inventory_item_id, old_gold_movement_id, karat_id, weight_grams, quantity,
                item_name_snapshot, factory_snapshot, size_snapshot,
                gold_price_per_gram, making_per_gram, making_total, gross_total, line_total, notes
            )
            VALUES (%s, 'NATIVE', %s, %s, %s, NULL, %s, %s, %s, 1, %s, %s, %s, %s, NULL, %s, %s, %s, %s)
            """,
            (_id(invoice_id), int(line_number), line_type, method, _optional_id(old_gold_movement_id),
             int(karat_id), weight_grams, item_name, factory_name, size,
             gold_price_per_gram, making_total, gross_total, line_total, notes),
        )
        return str(line_id)

    # ------------------------------------------------------------ unidentified sales, settlement, reviews

    def open_unidentified_sale_case(self, *, invoice_id, invoice_number, invoice_line_id, item_type_id,
                                    karat_id, factory_id, category_id, weight_grams, size, reason_code,
                                    reason, notes, created_by_user_id, salesperson_employee_id, sold_at):
        case_id, _ = self._execute(
            """
            INSERT INTO inventory_reconciliation_cases (
                case_type, case_status, observed_karat_id, observed_item_type_id, observed_category_id,
                observed_factory_id, observed_weight_grams, observed_size, reference_number,
                invoice_id, invoice_line_id, reason, reason_code, notes,
                created_by_user_id, salesperson_employee_id, sold_at
            )
            VALUES ('UNIDENTIFIED_SALE', 'OPEN', %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (int(karat_id), _optional_id(item_type_id), _optional_id(category_id), _optional_id(factory_id),
             weight_grams, size, invoice_number, _id(invoice_id), _id(invoice_line_id), reason, reason_code,
             notes, created_by_user_id, salesperson_employee_id, sold_at),
        )
        self._execute(
            """
            INSERT INTO inventory_reconciliation_case_events (case_id, event_type, to_status, notes,
                                                              performed_by_user_id, occurred_at)
            VALUES (%s, 'OPENED', 'OPEN', %s, %s, %s)
            """, (int(case_id), f"بيع يدوي — فاتورة {invoice_number}", created_by_user_id, sold_at))
        return str(case_id)

    def insert_settlement_adjustment(self, *, invoice_id, residual_signed, amount_signed, reason,
                                     accepted_by_user_id, approved_by_user_id=None, approved_at=None):
        adjustment_id, _ = self._execute(
            """
            INSERT INTO invoice_settlement_adjustments (
                invoice_id, residual_signed, amount_signed, reason, accepted_by_user_id,
                approved_by_user_id, approved_at
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            """,
            (_id(invoice_id), residual_signed, amount_signed, reason, accepted_by_user_id,
             approved_by_user_id, approved_at),
        )
        return str(adjustment_id)

    def insert_management_review(self, *, review_type, invoice_id, summary, details, created_by_user_id):
        review_id, _ = self._execute(
            """
            INSERT INTO management_reviews (review_type, invoice_id, summary, details_json, created_by_user_id)
            VALUES (%s, %s, %s, %s, %s)
            """,
            (review_type, _optional_id(invoice_id), summary, _json(details), created_by_user_id),
        )
        return str(review_id)

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
            s.balance_before, s.balance_after, s.dead_cash_warning_active,
            cu.display_name AS created_by_name, fu.display_name AS finalized_by_name
        FROM invoices i
        LEFT JOIN employees e ON e.id = i.employee_id
        LEFT JOIN invoice_customer_snapshots s ON s.invoice_id = i.id
        LEFT JOIN users cu ON cu.id = i.created_by_user_id
        LEFT JOIN users fu ON fu.id = i.finalized_by_user_id
    """

    def get_invoice(self, invoice_id):
        """Header, lines, payments, price snapshots and events; None if missing."""
        invoice_id = _id(invoice_id)
        header = self._fetch_one(self._HEADER + " WHERE i.id = %s", (invoice_id,))
        if header is None:
            return None
        lines = self._fetch_all(
            """
            SELECT l.id AS line_id, line_number, line_type, record_origin, inventory_item_id,
                   old_gold_movement_id, l.karat_id, k.karat_value, weight_grams, quantity,
                   item_name_snapshot, factory_snapshot, category_snapshot, size_snapshot,
                   barcode_snapshot, gold_price_per_gram, making_per_gram, making_total,
                   gross_total, discount_type, discount_value, discount_amount, line_total, notes
            FROM invoice_lines l JOIN karats k ON k.id = l.karat_id
            WHERE invoice_id = %s ORDER BY line_number
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
            SELECT s.id AS snapshot_id, s.karat_id, k.karat_value, shop_buy_price_per_gram, shop_sell_price_per_gram,
                   quote_id, source_code_snapshot, source_name_snapshot, provider_timestamp, captured_at
            FROM invoice_price_snapshots s JOIN karats k ON k.id = s.karat_id
            WHERE invoice_id = %s ORDER BY k.karat_value
            """, (invoice_id,))
        events = self._fetch_all(
            """
            SELECT ev.id AS event_id, event_type, related_invoice_id, notes, details_json,
                   occurred_at, performed_by_user_id, u.display_name AS performed_by_name
            FROM invoice_events ev LEFT JOIN users u ON u.id = ev.performed_by_user_id
            WHERE invoice_id = %s ORDER BY ev.id
            """, (invoice_id,))
        result = _stringify_ids(header)
        result["lines"] = [_stringify_ids(row) for row in lines]
        result["payments"] = [_stringify_ids(row) for row in payments]
        result["price_snapshots"] = [_stringify_ids(row) for row in prices]
        result["events"] = [_stringify_ids(row) for row in events]
        return result

    def get_payment_accounts(self, invoice_id):
        """The real bank/wallet account behind each payment leg (printing only; get_invoice is untouched)."""
        rows = self._fetch_all(
            """
            SELECT p.id AS payment_id, p.bank_account_id, b.name AS bank_account_name,
                   b.bank_name, b.account_type AS bank_account_type
            FROM invoice_payments p LEFT JOIN bank_accounts b ON b.id = p.bank_account_id
            WHERE p.invoice_id = %s ORDER BY p.id
            """, (_id(invoice_id),))
        return [_stringify_ids(row) for row in rows]

    def search_invoice_history(self, *, text="", date_from=None, date_to_exclusive=None, invoice_type=None,
                               settlement_status=None, customer_id=None, limit=51, offset=0):
        """سجل الفواتير: ONE summary query per page, newest first (invoice_date is the finalize time of
        a native invoice and is indexed). `text`: invoice-number or phone prefix, or customer name
        (Arabic-folded, from the invoice's own snapshot). Real money is the stored payments only."""
        from utils.search_normalization import mysql_search_expression, normalize_search_text
        text = (text or "").strip()
        folded = normalize_search_text(text)

        def escaped(value, contains=False):
            value = value.replace("!", "!!").replace("%", "!%").replace("_", "!_")
            return ("%" + value + "%") if contains else (value + "%")
        name_sql, name_params = mysql_search_expression("s.customer_name_snapshot")
        # The invoice KIND from its saved lines (never the stored invoice_type), in the same one query:
        # two EXISTS probes on invoice_lines(invoice_id) per row of the bounded page — no N+1.
        kind_sql = _HISTORY_KIND_SQL
        kind = HISTORY_KIND_FOR_TYPE.get(invoice_type) if invoice_type else None
        rows = self._fetch_all(
            f"""
            SELECT i.id AS invoice_id, i.invoice_number, i.invoice_type, i.invoice_status, i.record_origin,
                   i.invoice_date, i.finalized_at, i.net_amount, i.net_direction, i.settlement_status,
                   i.sell_total, i.buy_total, i.legacy_bill_number, {kind_sql} AS transaction_kind,
                   s.customer_name_snapshot, s.mobile_snapshot, e.name AS employee_name,
                   (SELECT COALESCE(SUM(CASE WHEN p.direction = 'IN' THEN p.amount ELSE 0 END), 0)
                    FROM invoice_payments p WHERE p.invoice_id = i.id) AS money_received,
                   (SELECT COALESCE(SUM(CASE WHEN p.direction = 'OUT' THEN p.amount ELSE 0 END), 0)
                    FROM invoice_payments p WHERE p.invoice_id = i.id) AS money_paid
            FROM invoices i
            LEFT JOIN invoice_customer_snapshots s ON s.invoice_id = i.id
            LEFT JOIN employees e ON e.id = i.employee_id
            WHERE i.invoice_status <> 'DRAFT'
              AND (%s IS NULL OR i.customer_id = %s)
              AND (%s OR i.invoice_number LIKE %s ESCAPE '!' OR i.legacy_bill_number LIKE %s ESCAPE '!'
                   OR s.mobile_snapshot LIKE %s ESCAPE '!'
                   OR (%s AND {name_sql} LIKE %s ESCAPE '!'))
              AND (%s IS NULL OR i.invoice_date >= %s)
              AND (%s IS NULL OR i.invoice_date < %s)
              AND (%s IS NULL OR {kind_sql} = %s)
              AND (%s IS NULL OR i.settlement_status = %s)
            ORDER BY i.invoice_date DESC, i.id DESC
            LIMIT %s OFFSET %s
            """,
            (_optional_id(customer_id), _optional_id(customer_id),
             not text, escaped(text), escaped(text), escaped(text),
             bool(folded), *name_params, escaped(folded, contains=True),
             date_from, date_from, date_to_exclusive, date_to_exclusive,
             kind, kind, settlement_status, settlement_status, int(limit), int(offset)))
        return [_stringify_ids(row) for row in rows]

    def get_invoice_id_by_number(self, invoice_number):
        """Exact match on the unique invoice number (the printed invoice barcode): no LIKE scan."""
        row = self._fetch_one("SELECT id FROM invoices WHERE invoice_number = %s AND invoice_status <> 'DRAFT'",
                              (invoice_number,))
        return None if row is None else str(row["id"])

    def invoice_account_allocation(self, invoice_id, customer_id):
        """The customer-ledger entry THIS invoice wrote (على العميل +, للعميل −), never the running balance."""
        if customer_id in (None, ""):
            return []
        return [_stringify_ids(row) for row in self._fetch_all(
            """
            SELECT ct.id AS transaction_id, ct.amount_delta, ct.occurred_at, tt.code AS type_code
            FROM customer_transactions ct
            JOIN customer_transaction_types tt ON tt.id = ct.transaction_type_id
            WHERE ct.customer_id = %s AND ct.reference_type = 'INVOICE' AND ct.reference_id = %s
            ORDER BY ct.id
            """, (_id(customer_id), _id(invoice_id)))]

    def customer_movements_after(self, customer_id, after_transaction_id, limit=50):
        """The customer's ledger AFTER this invoice's own entry (by id: never a clock comparison), with the
        real methods of each Customer Account Settlement. Read only; history is never rewritten."""
        return [_stringify_ids(row) for row in self._fetch_all(
            """
            SELECT ct.id AS transaction_id, ct.occurred_at, ct.amount_delta, ct.reference_type, ct.reference_code,
                   ct.description, tt.code AS type_code, tt.name AS type_name, u.display_name AS actor,
                   (SELECT GROUP_CONCAT(CONCAT(sp.payment_method_code, ':', sp.amount) ORDER BY sp.id SEPARATOR '|')
                      FROM customer_account_settlement_payments sp
                     WHERE ct.reference_type = 'CUSTOMER_ACCOUNT_SETTLEMENT' AND sp.settlement_id = ct.reference_id)
                   AS settlement_legs
            FROM customer_transactions ct
            JOIN customer_transaction_types tt ON tt.id = ct.transaction_type_id
            LEFT JOIN users u ON u.id = ct.created_by_user_id
            WHERE ct.customer_id = %s AND ct.id > %s
            ORDER BY ct.id
            LIMIT %s
            """, (_id(customer_id), _id(after_transaction_id), int(limit)))]

    def customer_balance(self, customer_id):
        row = self._fetch_one("SELECT COALESCE(SUM(amount_delta), 0) AS balance FROM customer_transactions "
                              "WHERE customer_id = %s", (_id(customer_id),))
        return row["balance"] if row else 0

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
