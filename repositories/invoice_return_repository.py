"""Persistence for Return V1 (022): invoice_returns / invoice_return_lines / invoice_return_payments.

A Return is linked to one finalized invoice and never writes or edits an invoice row. Every
method runs on the caller's connection and transaction; nothing here commits. BIGINT ids leave
as decimal strings.
"""
from repositories.base_repository import BaseRepository
from repositories.invoice_repository import _id, _json, _optional_id


REFERENCE_TYPE = "INVOICE_RETURN"
_IDS = frozenset({"return_id", "source_invoice_id", "customer_id", "line_id", "source_invoice_line_id",
                  "inventory_item_id", "old_gold_movement_id", "payment_id", "review_id", "return_line_id",
                  "consumed_by_exchange", "invoice_id", "price_quote_id", "item_id", "lifecycle_id",
                  "lifecycle_line_id", "source_lifecycle_line_id", "hold_id"})


def return_code(return_id):
    """The visible reference of a Return: RT + 6-digit id."""
    return f"RT{int(return_id):06d}"


def _ids(row):
    if row is None:
        return None
    import json
    out = {}
    for key, value in row.items():
        if key in _IDS and value is not None:
            value = str(value)
        elif key.endswith("_json") and isinstance(value, (str, bytes, bytearray)):
            value = json.loads(value)
        out[key] = value
    return out


class InvoiceReturnRepository(BaseRepository):

    def _execute(self, query, params=()):
        cursor = self.connection.cursor()
        try:
            cursor.execute(query, params)
            return cursor.lastrowid, cursor.rowcount
        finally:
            cursor.close()

    # ------------------------------------------------------------ source lines and their consumption

    def source_lines(self, invoice_id):
        """Every line of the original invoice with what after-sale lifecycles already consumed:
        the exchange that took it (whole piece) and the grams finalized Returns took."""
        rows = self._fetch_all(
            """
            SELECT l.id AS line_id, l.line_number, l.line_type, l.inventory_item_id, l.karat_id, k.karat_value,
                   l.weight_grams, l.item_name_snapshot, l.barcode_snapshot, l.making_total, l.line_total,
                   ii.inventory_status AS item_status,
                   x.lifecycle_transaction_id AS consumed_by_exchange, xt.occurred_at AS exchanged_at,
                   (SELECT COALESCE(SUM(rl.weight_grams), 0) FROM invoice_return_lines rl
                     WHERE rl.source_invoice_line_id = l.id AND rl.reversed_by_correction_id IS NULL) AS returned_grams,
                   -- The historical bases already refunded from this line (a pre-033 CURRENT_BUY Return counts as
                   -- its proportional share of line_total), so the last grams take the exact remainder.
                   (SELECT COALESCE(SUM(COALESCE(rl.historical_line_value,
                                                 ROUND(l.line_total * rl.weight_grams / l.weight_grams, 2))), 0)
                      FROM invoice_return_lines rl
                     WHERE rl.source_invoice_line_id = l.id AND rl.reversed_by_correction_id IS NULL) AS returned_basis,
                   (SELECT b.barcode_value FROM inventory_barcodes b WHERE b.inventory_item_id = l.inventory_item_id
                     ORDER BY b.barcode_status = 'ACTIVE' DESC, b.id DESC LIMIT 1) AS stored_barcode
            FROM invoice_lines l
            JOIN karats k ON k.id = l.karat_id
            LEFT JOIN inventory_items ii ON ii.id = l.inventory_item_id
            LEFT JOIN invoice_lifecycle_lines x ON x.source_invoice_line_id = l.id AND x.reversed_by_correction_id IS NULL
            LEFT JOIN invoice_lifecycle_transactions xt ON xt.id = x.lifecycle_transaction_id
            WHERE l.invoice_id = %s
            ORDER BY l.line_number
            """, (_id(invoice_id),))
        return [_ids(row) for row in rows]

    def exchange_lineage(self, invoice_id):
        """Every active (not reversed) Exchange of this invoice with all its lines in stored order: the pieces the
        customer surrendered (their source) and every replacement (weight, identity, where it is now and whether
        an active Return already took it). The Return values and resolves replacement pieces from this."""
        rows = self._fetch_all(
            """
            SELECT t.id AS lifecycle_id, t.signed_amount, t.occurred_at, t.transaction_status,
                   l.id AS lifecycle_line_id, l.line_role, l.line_number, l.source_invoice_line_id, l.inventory_item_id,
                   l.karat_id, l.karat_value, l.weight_grams, l.item_name_snapshot, l.barcode_snapshot,
                   l.identification_method, l.reversed_by_correction_id,
                   ii.inventory_status AS item_status, it.name AS item_type_name,
                   (SELECT b.barcode_value FROM inventory_barcodes b WHERE b.inventory_item_id = l.inventory_item_id
                     ORDER BY b.barcode_status = 'ACTIVE' DESC, b.id DESC LIMIT 1) AS stored_barcode,
                   (SELECT rl.id FROM invoice_return_lines rl
                     WHERE rl.source_lifecycle_line_id = l.id AND rl.reversed_by_correction_id IS NULL LIMIT 1)
                       AS returned_by_line
            FROM invoice_lifecycle_transactions t
            JOIN invoice_lifecycle_lines l ON l.lifecycle_transaction_id = t.id
            LEFT JOIN inventory_items ii ON ii.id = l.inventory_item_id
            LEFT JOIN item_types it ON it.id = ii.item_type_id
            WHERE t.source_invoice_id = %s AND t.transaction_type = 'EXCHANGE' AND t.transaction_status = 'FINALIZED'
              AND l.reversed_by_correction_id IS NULL
            ORDER BY t.id, l.line_number
            """, (_id(invoice_id),))
        exchanges = {}
        for row in rows:
            row = _ids(row)
            exchange = exchanges.setdefault(row["lifecycle_id"], {
                "lifecycle_id": row["lifecycle_id"], "signed_amount": row["signed_amount"],
                "occurred_at": row["occurred_at"], "lines": []})
            exchange["lines"].append(row)
        return list(exchanges.values())

    def lock_items(self, item_ids):
        ids = sorted({_id(i) for i in item_ids})
        if not ids:
            return {}
        rows = self._fetch_all(
            f"SELECT id AS inventory_item_id, inventory_status, weight_grams, stock_classification "
            f"FROM inventory_items WHERE id IN ({', '.join(['%s'] * len(ids))}) ORDER BY id FOR UPDATE", tuple(ids))
        return {str(row["inventory_item_id"]): _ids(row) for row in rows}

    # ------------------------------------------------------------ headers

    _HEADER = """
        SELECT r.id AS return_id, r.return_status, r.source_invoice_id, i.invoice_number AS source_invoice_number,
               r.source_finalized_at, r.customer_id, r.selection_json, r.selection_hash, r.making_window_open,
               r.requires_approval, r.return_deadline_at, r.gold_amount, r.making_refund, r.total_amount,
               r.valuation_mode, r.refund_basis, r.rounding_adjustment,
               r.money_paid, r.account_amount, r.price_snapshot_json, r.details_json, r.notes,
               r.created_by_user_id, cu.display_name AS created_by_name, r.created_at,
               r.finalized_by_user_id, fu.display_name AS finalized_by_name, r.finalized_at,
               r.canceled_by_user_id, xu.display_name AS canceled_by_name, r.canceled_at
        FROM invoice_returns r
        JOIN invoices i ON i.id = r.source_invoice_id
        LEFT JOIN users cu ON cu.id = r.created_by_user_id
        LEFT JOIN users fu ON fu.id = r.finalized_by_user_id
        LEFT JOIN users xu ON xu.id = r.canceled_by_user_id
    """

    def get_return(self, return_id, lock=False):
        if lock:
            if self._fetch_one("SELECT id FROM invoice_returns WHERE id = %s FOR UPDATE", (_id(return_id),)) is None:
                return None
        return _ids(self._fetch_one(self._HEADER + " WHERE r.id = %s", (_id(return_id),)))

    def for_invoice(self, invoice_id):
        """Every Return of one invoice (any status), oldest first, with lines, payments and reviews."""
        headers = [_ids(row) for row in self._fetch_all(
            self._HEADER + " WHERE r.source_invoice_id = %s ORDER BY r.id", (_id(invoice_id),))]
        for header in headers:
            header["lines"] = self.lines(header["return_id"])
            header["payments"] = self.payments(header["return_id"])
            header["approval"] = self.latest_approval(header["return_id"])
        return headers

    def open_for_invoice(self, invoice_id):
        return [_ids(row) for row in self._fetch_all(
            self._HEADER + " WHERE r.source_invoice_id = %s AND r.return_status IN "
                           "('DRAFT', 'PENDING_APPROVAL', 'APPROVED', 'REJECTED') ORDER BY r.id",
            (_id(invoice_id),))]

    def insert_header(self, *, status, source_invoice_id, source_finalized_at, customer_id, selection, selection_hash,
                      making_window_open, requires_approval, deadline_at, gold_amount, making_refund, notes,
                      created_by_user_id, created_by_employee_id, created_at, valuation, details=None):
        """`valuation`: {"mode", "basis", "rounding", "total"} (033: total = basis + the one EGP-5 rounding)."""
        return_id, _ = self._execute(
            """
            INSERT INTO invoice_returns (
                return_status, source_invoice_id, source_finalized_at, customer_id, selection_json, selection_hash,
                making_window_open, requires_approval, return_deadline_at, gold_amount, making_refund,
                valuation_mode, refund_basis, rounding_adjustment, total_amount,
                details_json, notes, created_by_user_id, created_by_employee_id, created_at
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (status, _id(source_invoice_id), source_finalized_at, _optional_id(customer_id), _json(selection),
             selection_hash, bool(making_window_open), bool(requires_approval), deadline_at, gold_amount,
             making_refund, valuation["mode"], valuation["basis"], valuation["rounding"], valuation["total"],
             _json(details), notes, created_by_user_id, created_by_employee_id, created_at))
        return str(return_id)

    def update_selection(self, *, return_id, status, selection, selection_hash, making_window_open, gold_amount,
                         making_refund, notes, valuation):
        """Pre-finalize only: a new selection / window state (an approval of an older selection never applies)."""
        _, rowcount = self._execute(
            """
            UPDATE invoice_returns
            SET return_status = %s, selection_json = %s, selection_hash = %s, making_window_open = %s,
                requires_approval = %s, gold_amount = %s, making_refund = %s, valuation_mode = %s, refund_basis = %s,
                rounding_adjustment = %s, total_amount = %s, notes = %s
            WHERE id = %s AND return_status IN ('DRAFT', 'PENDING_APPROVAL', 'APPROVED', 'REJECTED')
            """,
            (status, _json(selection), selection_hash, bool(making_window_open), not making_window_open, gold_amount,
             making_refund, valuation["mode"], valuation["basis"], valuation["rounding"], valuation["total"], notes,
             _id(return_id)))
        if rowcount != 1:
            raise RuntimeError("return is no longer editable")

    def set_status(self, *, return_id, from_statuses, to_status):
        placeholders = ", ".join(["%s"] * len(from_statuses))
        _, rowcount = self._execute(
            f"UPDATE invoice_returns SET return_status = %s WHERE id = %s AND return_status IN ({placeholders})",
            (to_status, _id(return_id), *from_statuses))
        return rowcount == 1

    def cancel(self, *, return_id, canceled_by_user_id, canceled_at):
        _, rowcount = self._execute(
            """
            UPDATE invoice_returns SET return_status = 'CANCELED', canceled_by_user_id = %s, canceled_at = %s
            WHERE id = %s AND return_status IN ('DRAFT', 'PENDING_APPROVAL', 'APPROVED', 'REJECTED')
            """, (canceled_by_user_id, canceled_at, _id(return_id)))
        return rowcount == 1

    def finalize_header(self, *, return_id, making_window_open, gold_amount, making_refund, money_paid, account_amount,
                        price_snapshot, details, notes, finalized_by_user_id, finalized_at, valuation):
        _, rowcount = self._execute(
            """
            UPDATE invoice_returns
            SET return_status = 'FINALIZED', making_window_open = %s, requires_approval = %s, gold_amount = %s,
                making_refund = %s, valuation_mode = %s, refund_basis = %s, rounding_adjustment = %s,
                total_amount = %s, money_paid = %s, account_amount = %s,
                price_snapshot_json = %s, details_json = %s, notes = %s, finalized_by_user_id = %s, finalized_at = %s
            WHERE id = %s AND return_status IN ('DRAFT', 'APPROVED')
            """,
            (bool(making_window_open), not making_window_open, gold_amount, making_refund, valuation["mode"],
             valuation["basis"], valuation["rounding"], valuation["total"], money_paid, account_amount,
             _json(price_snapshot), _json(details), notes, finalized_by_user_id, finalized_at, _id(return_id)))
        if rowcount != 1:
            raise RuntimeError("return is no longer finalizable")

    # ------------------------------------------------------------ lines / payments

    def insert_line(self, *, return_id, line_number, line, inventory_item_id, old_gold_movement_id):
        """One source only (033): the original invoice line, or the Exchange replacement the customer holds."""
        line_id, _ = self._execute(
            """
            INSERT INTO invoice_return_lines (
                return_id, line_number, source_invoice_line_id, source_lifecycle_line_id, line_type, inventory_item_id,
                old_gold_movement_id, karat_id, karat_value, source_weight_grams, weight_grams, shop_buy_price_per_gram,
                price_quote_id, price_provider_timestamp, gold_amount, historical_making_total, making_refund,
                valuation_mode, historical_line_value, item_name_snapshot, barcode_snapshot
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (_id(return_id), line_number, _optional_id(line.invoice_line_id), _optional_id(line.lifecycle_line_id),
             line.line_type, _optional_id(inventory_item_id), _optional_id(old_gold_movement_id), int(line.karat_id),
             int(line.karat), line.source_weight, line.weight,
             line.price_per_gram if line.valuation_mode == "CURRENT_BUY" else None, _optional_id(line.quote_id),
             line.provider_timestamp, line.gold_amount, line.historical_making, line.making_refund,
             line.valuation_mode, line.historical_value, line.item_name, line.barcode))
        return str(line_id)

    def lines(self, return_id):
        return [_ids(row) for row in self._fetch_all(
            """
            SELECT rl.id AS line_id, rl.line_number, rl.source_invoice_line_id, rl.source_lifecycle_line_id,
                   rl.reversed_by_correction_id,
                   rl.line_type, rl.inventory_item_id,
                   rl.old_gold_movement_id, rl.karat_value, rl.source_weight_grams, rl.weight_grams,
                   rl.shop_buy_price_per_gram, rl.price_provider_timestamp, rl.gold_amount,
                   rl.historical_making_total, rl.making_refund, rl.valuation_mode, rl.historical_line_value,
                   rl.item_name_snapshot, rl.barcode_snapshot,
                   mr.review_status AS inspection_status, mr.resolution_code AS inspection_resolution,
                   mr.resolved_at AS inspection_resolved_at, ru.display_name AS inspection_resolved_by,
                   h.hold_status,
                   (SELECT b.barcode_value FROM inventory_barcodes b WHERE b.inventory_item_id = rl.inventory_item_id
                     ORDER BY b.barcode_status = 'ACTIVE' DESC, b.id DESC LIMIT 1) AS stored_barcode
            FROM invoice_return_lines rl
            LEFT JOIN management_reviews mr ON mr.return_line_id = rl.id
            LEFT JOIN old_gold_holds h ON h.return_line_id = rl.id
            LEFT JOIN users ru ON ru.id = mr.resolved_by_user_id
            WHERE rl.return_id = %s ORDER BY rl.line_number
            """, (_id(return_id),))]

    def payments(self, return_id):
        return [_ids(row) for row in self._fetch_all(
            """
            SELECT p.id AS payment_id, pm.code AS payment_method_code, p.amount, p.bank_account_id,
                   b.name AS bank_account_name
            FROM invoice_return_payments p
            JOIN payment_methods pm ON pm.id = p.payment_method_id
            LEFT JOIN bank_accounts b ON b.id = p.bank_account_id
            WHERE p.return_id = %s ORDER BY p.id
            """, (_id(return_id),))]

    def insert_payment(self, *, return_id, payment_method_id, bank_account_id, amount, payment_reference,
                       cash_movement_id, bank_movement_id, occurred_at, created_by_user_id):
        payment_id, _ = self._execute(
            """
            INSERT INTO invoice_return_payments (
                return_id, payment_method_id, bank_account_id, direction, amount, payment_reference,
                cash_movement_id, bank_movement_id, occurred_at, created_by_user_id
            )
            VALUES (%s, %s, %s, 'OUT', %s, %s, %s, %s, %s, %s)
            """,
            (_id(return_id), int(payment_method_id), bank_account_id, amount, payment_reference,
             _optional_id(cash_movement_id), _optional_id(bank_movement_id), occurred_at, created_by_user_id))
        return str(payment_id)

    def _ledger_out(self, table, account_column, account_id, *, amount, return_id, created_by_user_id, occurred_at,
                    payment_reference=None):
        """RETURN_PAYMENT_OUT (negative) on the real cash / bank ledger; a negative balance is allowed."""
        code = return_code(return_id)
        extra_column, extra_value = ((", payment_reference", ", %s"), (payment_reference,)) \
            if table == "bank_movements" else (("", ""), ())
        movement_id, _ = self._execute(
            f"""
            INSERT INTO {table} (
                {account_column}, movement_type_id, amount_delta{extra_column[0]},
                reference_type, reference_id, reference_code, description, occurred_at, created_by_user_id
            )
            SELECT %s, t.id, %s{extra_column[1]}, %s, %s, %s, %s, %s, %s
            FROM {table[:-10]}_movement_types t WHERE t.code = 'RETURN_PAYMENT_OUT'
            """,
            (int(account_id), -abs(amount), *extra_value, REFERENCE_TYPE, _id(return_id), code, f"مرتجع {code}",
             occurred_at, created_by_user_id))
        if not movement_id:
            raise RuntimeError(f"{table} type RETURN_PAYMENT_OUT is missing")
        return str(movement_id)

    def add_cash_out(self, *, cash_account_id, **kwargs):
        return self._ledger_out("cash_movements", "cash_account_id", cash_account_id, **kwargs)

    def add_bank_out(self, *, bank_account_id, **kwargs):
        return self._ledger_out("bank_movements", "bank_account_id", bank_account_id, **kwargs)

    # ------------------------------------------------------------ inventory / old gold

    def mark_item_returned_to_scrap(self, *, inventory_item_id, updated_by_user_id):
        """SOLD → CONVERTED_TO_OLD_GOLD (033): out of the customer's hands and into Scrap at once (held)."""
        _, rowcount = self._execute(
            "UPDATE inventory_items SET inventory_status = 'CONVERTED_TO_OLD_GOLD', updated_by_user_id = %s "
            "WHERE id = %s AND inventory_status = 'SOLD'", (updated_by_user_id, _id(inventory_item_id)))
        if rowcount != 1:
            raise RuntimeError(f"Inventory item {inventory_item_id} is no longer sold.")

    def mark_item_returned_pending(self, *, inventory_item_id, updated_by_user_id):
        """SOLD → RETURNED_PENDING (waiting for the manager's inspection; never sellable)."""
        _, rowcount = self._execute(
            "UPDATE inventory_items SET inventory_status = 'RETURNED_PENDING', updated_by_user_id = %s "
            "WHERE id = %s AND inventory_status = 'SOLD'", (updated_by_user_id, _id(inventory_item_id)))
        if rowcount != 1:
            raise RuntimeError(f"Inventory item {inventory_item_id} is no longer sold.")

    def add_inventory_return_movement(self, *, item, return_id, performed_by_user_id, performed_by_employee_id,
                                      to_status="CONVERTED_TO_OLD_GOLD"):
        code = return_code(return_id)
        movement_id, _ = self._execute(
            """
            INSERT INTO inventory_movements (
                inventory_item_id, movement_type_id, from_stock_classification, to_stock_classification,
                from_inventory_status, to_inventory_status, from_weight_grams, to_weight_grams,
                reference_type, reference_id, reference_code, reason, performed_by_user_id, performed_by_employee_id
            )
            SELECT %s, t.id, %s, %s, 'SOLD', %s, %s, %s, %s, %s, %s, %s, %s, %s
            FROM inventory_movement_types t WHERE t.code = 'INVOICE_RETURN_IN' AND t.is_active = TRUE
            """,
            (_id(item["inventory_item_id"]), item["stock_classification"], item["stock_classification"], to_status,
             item["weight_grams"], item["weight_grams"], REFERENCE_TYPE, _id(return_id), code, f"مرتجع {code}",
             performed_by_user_id, performed_by_employee_id))
        if not movement_id:
            raise RuntimeError("inventory movement type INVOICE_RETURN_IN is missing")
        return str(movement_id)

    def add_old_gold_in(self, *, karat_id, weight_grams, return_id, gold_price_per_gram, cash_value, price_quote_id,
                        customer_id, created_by_user_id, employee_id, notes=None):
        """F7 weight returned: COUNTED RETURN_OLD_GOLD_IN (positive, exact grams) on the old-gold ledger."""
        code = return_code(return_id)
        movement_id, _ = self._execute(
            """
            INSERT INTO old_gold_movements (
                movement_type_id, karat_id, weight_delta_grams, balance_effect, gold_price_per_gram, cash_value,
                price_quote_id, reference_type, reference_id, reference_code, notes,
                created_by_user_id, performed_by_user_id, received_by_employee_id, movement_code, customer_id
            )
            SELECT t.id, %s, %s, 'COUNTED', %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'RETURN_OLD_GOLD_IN', %s
            FROM old_gold_movement_types t WHERE t.code = 'RETURN_OLD_GOLD_IN' AND t.is_active = TRUE
            """,
            (int(karat_id), abs(weight_grams), gold_price_per_gram, cash_value, _optional_id(price_quote_id),
             REFERENCE_TYPE, _id(return_id), code, notes, created_by_user_id, created_by_user_id, employee_id,
             _optional_id(customer_id)))
        if not movement_id:
            raise RuntimeError("old-gold movement type RETURN_OLD_GOLD_IN is missing")
        return str(movement_id)

    def add_return_scrap_in(self, *, karat_id, weight_grams, inventory_item_id, return_id, invoice_id, customer_id,
                            created_by_user_id, employee_id, notes=None):
        """The returned identified piece enters Scrap: COUNTED RETURN_SCRAP_IN (its exact karat and physical
        weight), linked to the piece, the Return and the original invoice (HELD until the manager decides)."""
        code = return_code(return_id)
        movement_id, _ = self._execute(
            """
            INSERT INTO old_gold_movements (
                movement_type_id, karat_id, weight_delta_grams, balance_effect, related_inventory_item_id,
                reference_type, reference_id, reference_code, notes, invoice_id,
                created_by_user_id, performed_by_user_id, received_by_employee_id, movement_code, customer_id
            )
            SELECT t.id, %s, %s, 'COUNTED', %s, %s, %s, %s, %s, %s, %s, %s, %s, 'RETURN_SCRAP_IN', %s
            FROM old_gold_movement_types t WHERE t.code = 'RETURN_SCRAP_IN' AND t.is_active = TRUE
            """,
            (int(karat_id), abs(weight_grams), _id(inventory_item_id), REFERENCE_TYPE, _id(return_id), code, notes,
             _optional_id(invoice_id), created_by_user_id, created_by_user_id, employee_id, _optional_id(customer_id)))
        if not movement_id:
            raise RuntimeError("old-gold movement type RETURN_SCRAP_IN is missing")
        return str(movement_id)

    def insert_scrap_hold(self, *, review_id, return_line_id, inventory_item_id, karat_id, weight_grams,
                          in_movement_id, created_by_user_id):
        """One HELD Scrap hold per returned piece (033: UNIQUE review / Return line / IN movement)."""
        hold_id, _ = self._execute(
            """
            INSERT INTO old_gold_holds (review_id, return_line_id, inventory_item_id, karat_id, weight_grams,
                                        in_movement_id, hold_status, created_by_user_id)
            VALUES (%s, %s, %s, %s, %s, %s, 'HELD', %s)
            """,
            (_id(review_id), _id(return_line_id), _id(inventory_item_id), int(karat_id), abs(weight_grams),
             _id(in_movement_id), created_by_user_id))
        return str(hold_id)

    # ------------------------------------------------------------ management reviews

    def open_inspection(self, *, return_line_id, invoice_id, summary, details, created_by_user_id):
        """The same RETURNED_ITEM_INSPECTION review as an exchange, sourced from the Return line (022)."""
        review_id, _ = self._execute(
            """
            INSERT INTO management_reviews (review_type, review_status, invoice_id, return_line_id, summary,
                                            details_json, created_by_user_id)
            VALUES ('RETURNED_ITEM_INSPECTION', 'OPEN', %s, %s, %s, %s, %s)
            """, (_optional_id(invoice_id), _id(return_line_id), summary, _json(details), created_by_user_id))
        return str(review_id)

    def open_approval(self, *, return_id, invoice_id, summary, details, created_by_user_id):
        review_id, _ = self._execute(
            """
            INSERT INTO management_reviews (review_type, review_status, invoice_id, return_id, summary, details_json,
                                            created_by_user_id)
            VALUES ('RETURN_APPROVAL', 'OPEN', %s, %s, %s, %s, %s)
            """, (_id(invoice_id), _id(return_id), summary, _json(details), created_by_user_id))
        return str(review_id)

    def approval_by_id(self, review_id):
        row = self._fetch_one(
            "SELECT id AS review_id, return_id, review_status FROM management_reviews "
            "WHERE id = %s AND review_type = 'RETURN_APPROVAL'", (_id(review_id),))
        return _ids(row)

    def resolve_approval(self, *, review_id, resolution_code, resolved_by_user_id, resolved_at):
        _, rowcount = self._execute(
            """
            UPDATE management_reviews SET review_status = 'RESOLVED', resolution_code = %s, resolved_by_user_id = %s,
                   resolved_at = %s
            WHERE id = %s AND review_type = 'RETURN_APPROVAL' AND review_status = 'OPEN'
            """, (resolution_code, resolved_by_user_id, resolved_at, _id(review_id)))
        return rowcount == 1

    def open_approvals(self, limit=200):
        """Open RETURN_APPROVAL reviews with their Return and original invoice (manager screen)."""
        import json
        rows = self._fetch_all(
            """
            SELECT m.id AS review_id, m.return_id, m.details_json, m.created_at, u.display_name AS created_by_name,
                   r.return_status, r.selection_hash, r.source_finalized_at, r.return_deadline_at, r.gold_amount,
                   r.making_refund, r.total_amount, r.notes, i.invoice_number, i.id AS invoice_id,
                   s.customer_name_snapshot
            FROM management_reviews m
            JOIN invoice_returns r ON r.id = m.return_id
            JOIN invoices i ON i.id = r.source_invoice_id
            LEFT JOIN invoice_customer_snapshots s ON s.invoice_id = i.id
            LEFT JOIN users u ON u.id = m.created_by_user_id
            WHERE m.review_type = 'RETURN_APPROVAL' AND m.review_status = 'OPEN'
            ORDER BY m.id DESC LIMIT %s
            """, (int(limit),))
        for row in rows:
            if isinstance(row.get("details_json"), (str, bytes)):
                row["details_json"] = json.loads(row["details_json"])
        return [_ids(row) for row in rows]

    def latest_approval(self, return_id, lock=False):
        row = self._fetch_one(
            f"""
            SELECT r.id AS review_id, r.review_status, r.resolution_code, r.details_json, r.resolved_at,
                   u.display_name AS resolved_by_name
            FROM management_reviews r LEFT JOIN users u ON u.id = r.resolved_by_user_id
            WHERE r.review_type = 'RETURN_APPROVAL' AND r.return_id = %s
            ORDER BY r.id DESC LIMIT 1 {"FOR UPDATE" if lock else ""}
            """, (_id(return_id),))
        if row and isinstance(row.get("details_json"), (str, bytes)):
            import json
            row["details_json"] = json.loads(row["details_json"])
        return _ids(row)

    def dismiss_open_approvals(self, *, return_id, user_id, at, notes):
        self._execute(
            """
            UPDATE management_reviews SET review_status = 'DISMISSED', resolved_by_user_id = %s, resolved_at = %s,
                   resolution_notes = %s
            WHERE review_type = 'RETURN_APPROVAL' AND return_id = %s AND review_status = 'OPEN'
            """, (user_id, at, notes, _id(return_id)))
