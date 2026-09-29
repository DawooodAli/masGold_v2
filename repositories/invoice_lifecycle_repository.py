"""Persistence for invoice lifecycle transactions (019): Exchange V1.

A lifecycle transaction is linked to one finalized invoice and never writes
or edits an invoice row. Every method runs on the caller's connection and
transaction; nothing here commits. BIGINT ids leave as decimal strings.
"""
from repositories.base_repository import BaseRepository
from repositories.invoice_repository import _id, _json, _optional_id, _stringify_ids


REFERENCE_TYPE = "INVOICE_LIFECYCLE"

_LIFECYCLE_IDS = frozenset({"lifecycle_id", "source_invoice_id", "customer_id", "line_id",
                            "source_invoice_line_id", "inventory_item_id", "payment_id"})


def exchange_code(lifecycle_id):
    """The printed/visible reference of an exchange: EX + 6-digit id."""
    return f"EX{int(lifecycle_id):06d}"


def _ids(row):
    if row is None:
        return None
    return {key: (str(value) if key in _LIFECYCLE_IDS and value is not None else value)
            for key, value in _stringify_ids(row).items()}


class InvoiceLifecycleRepository(BaseRepository):

    def _execute(self, query, params=()):
        cursor = self.connection.cursor()
        try:
            cursor.execute(query, params)
            return cursor.lastrowid, cursor.rowcount
        finally:
            cursor.close()

    # ------------------------------------------------------------ reads

    def source_lines(self, invoice_id):
        """Every line of the original invoice with the lifecycle that already consumed it (if any)."""
        rows = self._fetch_all(
            """
            SELECT l.id AS line_id, l.line_number, l.line_type, l.inventory_item_id, l.karat_id,
                   k.karat_value, l.weight_grams, l.item_name_snapshot, l.barcode_snapshot,
                   l.making_per_gram, l.making_total, l.gross_total, l.line_total,
                   c.lifecycle_transaction_id AS consumed_by
            FROM invoice_lines l
            JOIN karats k ON k.id = l.karat_id
            LEFT JOIN invoice_lifecycle_lines c ON c.source_invoice_line_id = l.id
            WHERE l.invoice_id = %s
            ORDER BY l.line_number
            """, (_id(invoice_id),))
        return [dict(_ids(row), consumed_by=None if row["consumed_by"] is None else str(row["consumed_by"]))
                for row in rows]

    def for_invoice(self, invoice_id):
        """Lifecycle transactions of one original invoice, oldest first."""
        return [_ids(row) for row in self._fetch_all(
            """
            SELECT t.id AS lifecycle_id, t.transaction_type, t.transaction_status, t.signed_amount,
                   t.pure_delta_grams, t.occurred_at, u.display_name AS performed_by_name
            FROM invoice_lifecycle_transactions t
            LEFT JOIN users u ON u.id = t.performed_by_user_id
            WHERE t.source_invoice_id = %s
            ORDER BY t.id
            """, (_id(invoice_id),))]

    def get_transaction(self, lifecycle_id):
        """Header, lines and money legs; None when missing."""
        header = self._fetch_one(
            """
            SELECT t.id AS lifecycle_id, t.transaction_type, t.transaction_status, t.source_invoice_id,
                   i.invoice_number AS source_invoice_number, t.source_finalized_at, t.customer_id,
                   t.returned_pure_grams, t.replacement_pure_grams, t.pure_delta_grams, t.valuation_basis,
                   t.valued_delta_grams, t.price_basis, t.price_karat_value, t.price_per_gram, t.gold_amount,
                   t.making_rate_per_gram, t.making_charge,
                   t.making_refund, t.making_refund_eligible, t.signed_amount, t.money_received,
                   t.money_paid, t.account_signed, t.price_snapshot_json, t.details_json, t.notes,
                   t.occurred_at, u.display_name AS performed_by_name
            FROM invoice_lifecycle_transactions t
            JOIN invoices i ON i.id = t.source_invoice_id
            LEFT JOIN users u ON u.id = t.performed_by_user_id
            WHERE t.id = %s
            """, (_id(lifecycle_id),))
        if header is None:
            return None
        lines = self._fetch_all(
            """
            SELECT l.id AS line_id, l.line_role, l.line_number, l.source_invoice_line_id, l.inventory_item_id,
                   l.karat_id, l.karat_value, l.weight_grams, l.pure_weight_grams, l.making_per_gram,
                   l.making_per_pure_gram, l.making_total, l.item_name_snapshot, l.barcode_snapshot,
                   l.identification_method,
                   r.review_status AS inspection_status, r.resolution_code AS inspection_resolution,
                   r.resolved_at AS inspection_resolved_at, ru.display_name AS inspection_resolved_by,
                   (SELECT b.barcode_value FROM inventory_barcodes b WHERE b.inventory_item_id = l.inventory_item_id
                     ORDER BY b.barcode_status = 'ACTIVE' DESC, b.id DESC LIMIT 1) AS stored_barcode
            FROM invoice_lifecycle_lines l
            LEFT JOIN management_reviews r ON r.lifecycle_line_id = l.id
            LEFT JOIN users ru ON ru.id = r.resolved_by_user_id
            WHERE l.lifecycle_transaction_id = %s ORDER BY l.line_number
            """, (_id(lifecycle_id),))
        payments = self._fetch_all(
            """
            SELECT p.id AS payment_id, pm.code AS payment_method_code, p.direction, p.amount,
                   p.bank_account_id, b.name AS bank_account_name
            FROM invoice_lifecycle_payments p
            JOIN payment_methods pm ON pm.id = p.payment_method_id
            LEFT JOIN bank_accounts b ON b.id = p.bank_account_id
            WHERE p.lifecycle_transaction_id = %s ORDER BY p.id
            """, (_id(lifecycle_id),))
        result = _ids(header)
        result["lines"] = [_ids(row) for row in lines]
        result["payments"] = [_ids(row) for row in payments]
        return result

    # ------------------------------------------------------------ inserts

    def insert_transaction(self, *, source_invoice_id, source_finalized_at, customer_id, calculation,
                           money_received, money_paid, account_signed, price_snapshot, details, notes,
                           performed_by_user_id, performed_by_employee_id, occurred_at):
        c = calculation
        lifecycle_id, _ = self._execute(
            """
            INSERT INTO invoice_lifecycle_transactions (
                transaction_type, transaction_status, source_invoice_id, source_finalized_at, customer_id,
                returned_pure_grams, replacement_pure_grams, pure_delta_grams, valuation_basis,
                valued_delta_grams, price_basis, price_karat_value, price_per_gram, gold_amount,
                making_rate_per_gram, making_charge, making_refund,
                making_refund_eligible, signed_amount, money_received, money_paid, account_signed,
                price_snapshot_json, details_json, notes, performed_by_user_id, performed_by_employee_id,
                occurred_at
            )
            VALUES ('EXCHANGE', 'FINALIZED', %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (_id(source_invoice_id), source_finalized_at, _optional_id(customer_id),
             c.returned_pure, c.replacement_pure, c.pure_delta, c.valuation_basis, c.valued_delta,
             c.price_basis, c.price_karat, c.price_per_gram,
             c.gold_amount, c.making_rate, c.making_charge, c.making_refund, c.refund_eligible,
             c.signed_amount, money_received, money_paid, account_signed, _json(price_snapshot),
             _json(details), notes, performed_by_user_id, performed_by_employee_id, occurred_at),
        )
        return str(lifecycle_id)

    def insert_line(self, *, lifecycle_id, line_role, line_number, line, old_gold_movement_id=None):
        if getattr(line, "described", False):
            return self._insert_described_line(lifecycle_id, line_role, line_number, line, old_gold_movement_id)
        line_id, _ = self._execute(
            """
            INSERT INTO invoice_lifecycle_lines (
                lifecycle_transaction_id, line_role, line_number, source_invoice_line_id, inventory_item_id,
                karat_id, karat_value, weight_grams, pure_weight_grams, making_per_gram,
                making_per_pure_gram, making_total, item_name_snapshot, barcode_snapshot,
                identification_method
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (_id(lifecycle_id), line_role, line_number, _optional_id(line.source_line_id),
             _id(line.inventory_item_id), int(line.karat_id), int(line.karat), line.weight, line.pure,
             line.making_per_gram, line.making_per_pure_gram, line.making_total, line.item_name,
             line.barcode, line.identified_by),
        )
        return str(line_id)

    def _insert_described_line(self, lifecycle_id, line_role, line_number, line, old_gold_movement_id):
        """MANUAL (F5) / OLD_GOLD_STOCK (F7) replacement (migration 020): no inventory id, its own snapshots."""
        line_id, _ = self._execute(
            """
            INSERT INTO invoice_lifecycle_lines (
                lifecycle_transaction_id, line_role, line_number, source_invoice_line_id, inventory_item_id,
                old_gold_movement_id, karat_id, karat_value, weight_grams, pure_weight_grams, making_per_gram,
                making_per_pure_gram, making_total, item_name_snapshot, barcode_snapshot, factory_snapshot,
                size_snapshot, identification_method, reason_code, notes
            )
            VALUES (%s, %s, %s, NULL, NULL, %s, %s, %s, %s, %s, %s, %s, %s, %s, NULL, %s, %s, %s, %s, %s)
            """,
            (_id(lifecycle_id), line_role, line_number, _optional_id(old_gold_movement_id), int(line.karat_id),
             int(line.karat), line.weight, line.pure, line.making_per_gram, line.making_per_pure_gram,
             line.making_total, line.item_name, line.factory_name, line.size, line.identified_by, line.reason_code,
             line.notes),
        )
        return str(line_id)

    def add_old_gold_replacement_out(self, *, karat_id, weight_grams, lifecycle_id, gold_price_per_gram,
                                     price_quote_id, customer_id, created_by_user_id, employee_id, notes=None):
        """F7 replacement: COUNTED EXCHANGE_REPLACEMENT_OUT (negative weight) on the old-gold ledger."""
        code = exchange_code(lifecycle_id)
        movement_id, _ = self._execute(
            """
            INSERT INTO old_gold_movements (
                movement_type_id, karat_id, weight_delta_grams, balance_effect,
                gold_price_per_gram, cash_value, price_quote_id,
                reference_type, reference_id, reference_code, notes,
                created_by_user_id, performed_by_user_id, received_by_employee_id,
                movement_code, customer_id
            )
            SELECT t.id, %s, %s, 'COUNTED', %s, NULL, %s, %s, %s, %s, %s, %s, %s, %s,
                   'EXCHANGE_REPLACEMENT_OUT', %s
            FROM old_gold_movement_types t WHERE t.code = 'EXCHANGE_REPLACEMENT_OUT' AND t.is_active = TRUE
            """,
            (int(karat_id), -abs(weight_grams), gold_price_per_gram, _optional_id(price_quote_id), REFERENCE_TYPE,
             _id(lifecycle_id), code, notes, created_by_user_id, created_by_user_id, employee_id,
             _optional_id(customer_id)),
        )
        if not movement_id:
            raise RuntimeError("old-gold movement type EXCHANGE_REPLACEMENT_OUT is missing")
        return str(movement_id)

    def open_unidentified_replacement_case(self, *, lifecycle_line_id, exchange_code, item_type_id, karat_id,
                                           factory_id, category_id, weight_grams, size, reason_code, reason, notes,
                                           created_by_user_id, salesperson_employee_id, sold_at):
        """F5 parity: Billing's UNIDENTIFIED_SALE reconciliation case, linked to the exchange line (020)."""
        case_id, _ = self._execute(
            """
            INSERT INTO inventory_reconciliation_cases (
                case_type, case_status, observed_karat_id, observed_item_type_id, observed_category_id,
                observed_factory_id, observed_weight_grams, observed_size, reference_number,
                lifecycle_line_id, reason, reason_code, notes,
                created_by_user_id, salesperson_employee_id, sold_at
            )
            VALUES ('UNIDENTIFIED_SALE', 'OPEN', %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (int(karat_id), _optional_id(item_type_id), _optional_id(category_id), _optional_id(factory_id),
             weight_grams, size, exchange_code, _id(lifecycle_line_id), reason, reason_code, notes,
             created_by_user_id, salesperson_employee_id, sold_at),
        )
        self._execute(
            """
            INSERT INTO inventory_reconciliation_case_events (case_id, event_type, to_status, notes,
                                                              performed_by_user_id, occurred_at)
            VALUES (%s, 'OPENED', 'OPEN', %s, %s, %s)
            """, (int(case_id), f"بديل يدوي — استبدال {exchange_code}", created_by_user_id, sold_at))
        return str(case_id)

    def open_returned_item_inspection(self, *, lifecycle_line_id, invoice_id, summary, details, created_by_user_id):
        """One RETURNED_ITEM_INSPECTION manager review per returned line (021; UNIQUE lifecycle_line_id)."""
        review_id, _ = self._execute(
            """
            INSERT INTO management_reviews (review_type, review_status, invoice_id, lifecycle_line_id, summary,
                                            details_json, created_by_user_id)
            VALUES ('RETURNED_ITEM_INSPECTION', 'OPEN', %s, %s, %s, %s, %s)
            """,
            (_optional_id(invoice_id), _id(lifecycle_line_id), summary, _json(details), created_by_user_id),
        )
        return str(review_id)

    def insert_payment(self, *, lifecycle_id, payment_method_id, bank_account_id, direction, amount,
                       payment_reference, cash_movement_id, bank_movement_id, occurred_at, created_by_user_id):
        """Real money only; offsets and balances never become payment rows."""
        payment_id, _ = self._execute(
            """
            INSERT INTO invoice_lifecycle_payments (
                lifecycle_transaction_id, payment_method_id, bank_account_id, direction, amount,
                payment_reference, cash_movement_id, bank_movement_id, occurred_at, created_by_user_id
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (_id(lifecycle_id), int(payment_method_id), bank_account_id, direction, amount, payment_reference,
             _optional_id(cash_movement_id), _optional_id(bank_movement_id), occurred_at, created_by_user_id),
        )
        return str(payment_id)

    def _ledger_movement(self, table, account_column, account_id, *, direction, amount, lifecycle_id,
                         created_by_user_id, occurred_at, payment_reference=None):
        code = exchange_code(lifecycle_id)
        extra_column, extra_value = ((", payment_reference", ", %s"), (payment_reference,)) \
            if table == "bank_movements" else (("", ""), ())
        movement_id, _ = self._execute(
            f"""
            INSERT INTO {table} (
                {account_column}, movement_type_id, amount_delta{extra_column[0]},
                reference_type, reference_id, reference_code, description, occurred_at, created_by_user_id
            )
            SELECT %s, t.id, %s{extra_column[1]}, %s, %s, %s, %s, %s, %s
            FROM {table[:-10]}_movement_types t WHERE t.code = %s
            """,
            (int(account_id), amount if direction == "IN" else -amount, *extra_value, REFERENCE_TYPE,
             _id(lifecycle_id), code, f"استبدال {code}", occurred_at, created_by_user_id,
             f"EXCHANGE_PAYMENT_{direction}"),
        )
        if not movement_id:
            raise RuntimeError(f"{table} type EXCHANGE_PAYMENT_{direction} is missing")
        return str(movement_id)

    def add_cash_movement(self, *, cash_account_id, **kwargs):
        return self._ledger_movement("cash_movements", "cash_account_id", cash_account_id, **kwargs)

    def add_bank_movement(self, *, bank_account_id, **kwargs):
        return self._ledger_movement("bank_movements", "bank_account_id", bank_account_id, **kwargs)

    # ------------------------------------------------------------ inventory

    def mark_item_returned_pending(self, *, inventory_item_id, updated_by_user_id):
        """SOLD → RETURNED_PENDING (waiting for inspection; never sellable)."""
        _, rowcount = self._execute(
            """
            UPDATE inventory_items
            SET inventory_status = 'RETURNED_PENDING', updated_by_user_id = %s
            WHERE id = %s AND inventory_status = 'SOLD'
            """,
            (updated_by_user_id, _id(inventory_item_id)),
        )
        if rowcount != 1:
            raise RuntimeError(f"Inventory item {inventory_item_id} is no longer sold.")

    def add_inventory_movement(self, *, item, movement_code, from_status, to_status, lifecycle_id,
                               performed_by_user_id, performed_by_employee_id):
        code = exchange_code(lifecycle_id)
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
            SELECT %s, t.id, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
            FROM inventory_movement_types t WHERE t.code = %s AND t.is_active = TRUE
            """,
            (_id(item["inventory_item_id"]), item["stock_classification"], item["stock_classification"],
             from_status, to_status, item["weight_grams"], item["weight_grams"], REFERENCE_TYPE,
             _id(lifecycle_id), code, f"استبدال {code}", performed_by_user_id, performed_by_employee_id,
             movement_code),
        )
        if not movement_id:
            raise RuntimeError(f"inventory movement type {movement_code} is missing")
        return str(movement_id)
