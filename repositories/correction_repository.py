"""Persistence for Reversal / Correction V1 (023): transaction_corrections / transaction_correction_legs.

Reads the stored effects of a finalized Invoice / Exchange / Return exactly as they were posted
(money movements, customer-ledger rows, lines) and writes the corrective rows. Nothing here edits
or deletes an original row; the only updates are the status of a physical piece, an open review /
reconciliation case being closed, and the link of a reversed lifecycle line to its reversal.
Every method runs on the caller's connection and transaction; nothing here commits.
"""
import json

from repositories.base_repository import BaseRepository
from repositories.invoice_repository import _id, _json, _optional_id


REFERENCE_TYPE = "TRANSACTION_CORRECTION"
SOURCE_REFERENCE = {"INVOICE": "INVOICE", "EXCHANGE": "INVOICE_LIFECYCLE", "RETURN": "INVOICE_RETURN", "CUSTOMER_ACCOUNT_SETTLEMENT": "CUSTOMER_ACCOUNT_SETTLEMENT"}
_IDS = frozenset({"correction_id", "source_id", "invoice_id", "previous_correction_id", "line_id", "inventory_item_id",
                  "old_gold_movement_id", "lifecycle_id", "return_id", "customer_id", "review_id", "case_id",
                  "reversed_by_correction_id", "source_invoice_line_id", "source_invoice_id"})


def _ids(row):
    if row is None:
        return None
    out = {}
    for key, value in row.items():
        if key in _IDS and value is not None:
            value = str(value)
        elif key.endswith("_json") and isinstance(value, (str, bytes, bytearray)):
            value = json.loads(value)
        out[key] = value
    return out


class CorrectionRepository(BaseRepository):

    def _execute(self, query, params=()):
        cursor = self.connection.cursor()
        try:
            cursor.execute(query, params)
            return cursor.lastrowid, cursor.rowcount
        finally:
            cursor.close()

    # ------------------------------------------------------------ corrections of a source

    def corrections_for_source(self, source_type, source_id):
        return [_ids(r) for r in self._fetch_all(
            """
            SELECT id AS correction_id, correction_type, chain_sequence, after_json, occurred_at
            FROM transaction_corrections WHERE source_type = %s AND source_id = %s ORDER BY id
            """, (source_type, _id(source_id)))]

    def effective_legs(self, correction_id):
        return [_ids(r) for r in self._fetch_all(
            """
            SELECT leg_kind, payment_method_code, bank_account_id, amount FROM transaction_correction_legs
            WHERE correction_id = %s AND leg_role = 'EFFECTIVE' ORDER BY id
            """, (_id(correction_id),))]

    def corrections_for_invoice(self, invoice_id):
        """Every correction anchored on one invoice (its own and its Exchanges' / Returns'), oldest first."""
        rows = [_ids(r) for r in self._fetch_all(
            """
            SELECT c.id AS correction_id, c.correction_type, c.source_type, c.source_id, c.source_reference,
                   c.chain_sequence, c.reason, c.money_direction, c.settled_total, c.before_json, c.after_json,
                   c.effects_json, c.occurred_at, u.display_name AS performed_by_name
            FROM transaction_corrections c LEFT JOIN users u ON u.id = c.performed_by_user_id
            WHERE c.invoice_id = %s ORDER BY c.id
            """, (_id(invoice_id),))]
        for row in rows:
            row["posted"] = [_ids(r) for r in self._fetch_all(
                """
                SELECT l.leg_kind, l.amount, l.cash_movement_id, l.bank_movement_id, l.customer_transaction_id,
                       b.name AS bank_account_name
                FROM transaction_correction_legs l
                LEFT JOIN bank_movements bm ON bm.id = l.bank_movement_id
                LEFT JOIN bank_accounts b ON b.id = bm.bank_account_id
                WHERE l.correction_id = %s AND l.leg_role = 'POSTED' ORDER BY l.id
                """, (_id(row["correction_id"]),))]
        return rows

    def invoice_id_for_code(self, correction_id):
        row = self._fetch_one("SELECT invoice_id FROM transaction_corrections WHERE id = %s", (_id(correction_id),))
        return None if row is None else str(row["invoice_id"])

    # ------------------------------------------------------------ what a source posted (and its corrections)

    def _references(self, source_type, source_id, correction_ids):
        clauses, params = ["(m.reference_type = %s AND m.reference_id = %s)"], [SOURCE_REFERENCE[source_type], _id(source_id)]
        if correction_ids:
            clauses.append(f"(m.reference_type = '{REFERENCE_TYPE}' AND m.reference_id IN "
                           f"({', '.join(['%s'] * len(correction_ids))}))")
            params += [_id(c) for c in correction_ids]
        return " OR ".join(clauses), tuple(params)

    def money_by_account(self, source_type, source_id, correction_ids=()):
        """Signed money per real ledger account: what the source (and its settlement corrections) posted."""
        where, params = self._references(source_type, source_id, correction_ids)
        cash = self._fetch_all(f"SELECT m.cash_account_id AS account_id, SUM(m.amount_delta) AS total FROM cash_movements m "
                               f"WHERE {where} GROUP BY m.cash_account_id", params)
        bank = self._fetch_all(f"SELECT m.bank_account_id AS account_id, SUM(m.amount_delta) AS total FROM bank_movements m "
                               f"WHERE {where} GROUP BY m.bank_account_id", params)
        return ({("CASH", int(r["account_id"])): r["total"] for r in cash}
                | {("BANK", int(r["account_id"])): r["total"] for r in bank})

    def account_by_customer(self, source_type, source_id, correction_ids=()):
        where, params = self._references(source_type, source_id, correction_ids)
        return {str(r["customer_id"]): r["total"] for r in self._fetch_all(
            f"SELECT m.customer_id, SUM(m.amount_delta) AS total FROM customer_transactions m WHERE {where} "
            f"GROUP BY m.customer_id", params)}

    # ------------------------------------------------------------ sources

    def invoice_payment_legs(self, invoice_id):
        return [_ids(r) for r in self._fetch_all(
            """
            SELECT pm.code AS method_code, p.bank_account_id, p.direction, p.amount FROM invoice_payments p
            JOIN payment_methods pm ON pm.id = p.payment_method_id WHERE p.invoice_id = %s ORDER BY p.id
            """, (_id(invoice_id),))]

    def exchange_source(self, lifecycle_id):
        header = self._fetch_one(
            """
            SELECT t.id AS lifecycle_id, t.source_invoice_id, t.customer_id, t.signed_amount, t.transaction_status,
                   t.occurred_at FROM invoice_lifecycle_transactions t WHERE t.id = %s
            """, (_id(lifecycle_id),))
        if header is None:
            return None
        result = _ids(header)
        result["lines"] = [_ids(r) for r in self._fetch_all(
            """
            SELECT id AS line_id, line_role, identification_method, inventory_item_id, old_gold_movement_id,
                   karat_id, karat_value, weight_grams, item_name_snapshot, reversed_by_correction_id
            FROM invoice_lifecycle_lines WHERE lifecycle_transaction_id = %s ORDER BY line_number
            """, (_id(lifecycle_id),))]
        result["payments"] = [_ids(r) for r in self._fetch_all(
            """
            SELECT pm.code AS method_code, p.bank_account_id, p.direction, p.amount FROM invoice_lifecycle_payments p
            JOIN payment_methods pm ON pm.id = p.payment_method_id WHERE p.lifecycle_transaction_id = %s ORDER BY p.id
            """, (_id(lifecycle_id),))]
        return result

    def return_source(self, return_id):
        header = self._fetch_one(
            """
            SELECT id AS return_id, source_invoice_id, customer_id, return_status, total_amount, money_paid,
                   account_amount, finalized_at FROM invoice_returns WHERE id = %s
            """, (_id(return_id),))
        if header is None:
            return None
        result = _ids(header)
        result["lines"] = [_ids(r) for r in self._fetch_all(
            """
            SELECT id AS line_id, line_type, inventory_item_id, old_gold_movement_id, karat_id, karat_value,
                   weight_grams, item_name_snapshot, reversed_by_correction_id
            FROM invoice_return_lines WHERE return_id = %s ORDER BY line_number
            """, (_id(return_id),))]
        result["payments"] = [_ids(r) for r in self._fetch_all(
            """
            SELECT pm.code AS method_code, p.bank_account_id, p.direction, p.amount FROM invoice_return_payments p
            JOIN payment_methods pm ON pm.id = p.payment_method_id WHERE p.return_id = %s ORDER BY p.id
            """, (_id(return_id),))]
        return result

    def lifecycle_of_invoice(self, invoice_id):
        """Every Exchange / finalized Return of the invoice with whether it is still active (not reversed)."""
        exchanges = [_ids(r) for r in self._fetch_all(
            """
            SELECT t.id AS lifecycle_id, t.occurred_at,
                   (SELECT COUNT(*) FROM invoice_lifecycle_lines l WHERE l.lifecycle_transaction_id = t.id
                     AND l.reversed_by_correction_id IS NULL) AS active_lines
            FROM invoice_lifecycle_transactions t WHERE t.source_invoice_id = %s ORDER BY t.id
            """, (_id(invoice_id),))]
        returns = [_ids(r) for r in self._fetch_all(
            """
            SELECT r.id AS return_id, r.finalized_at,
                   (SELECT COUNT(*) FROM invoice_return_lines l WHERE l.return_id = r.id
                     AND l.reversed_by_correction_id IS NULL) AS active_lines
            FROM invoice_returns r WHERE r.source_invoice_id = %s AND r.return_status = 'FINALIZED' ORDER BY r.id
            """, (_id(invoice_id),))]
        return exchanges, returns

    # ------------------------------------------------------------ locks

    def lock_items(self, item_ids):
        ids = sorted({_id(i) for i in item_ids})
        if not ids:
            return {}
        rows = self._fetch_all(
            f"SELECT id AS inventory_item_id, inventory_status, weight_grams, stock_classification FROM inventory_items "
            f"WHERE id IN ({', '.join(['%s'] * len(ids))}) ORDER BY id FOR UPDATE", tuple(ids))
        return {str(r["inventory_item_id"]): _ids(r) for r in rows}

    def inspection_for(self, *, lifecycle_line_id=None, return_line_id=None):
        column, value = ("lifecycle_line_id", lifecycle_line_id) if lifecycle_line_id else ("return_line_id", return_line_id)
        return _ids(self._fetch_one(
            f"SELECT id AS review_id, review_status, resolution_code FROM management_reviews "
            f"WHERE review_type = 'RETURNED_ITEM_INSPECTION' AND {column} = %s FOR UPDATE", (_id(value),)))

    def case_for(self, *, invoice_line_id=None, lifecycle_line_id=None):
        column, value = ("invoice_line_id", invoice_line_id) if invoice_line_id else ("lifecycle_line_id", lifecycle_line_id)
        return _ids(self._fetch_one(
            f"SELECT id AS case_id, case_status FROM inventory_reconciliation_cases "
            f"WHERE case_type = 'UNIDENTIFIED_SALE' AND {column} = %s FOR UPDATE", (_id(value),)))

    def lock_old_gold_balance(self, karat_id):
        rows = self._fetch_all(
            "SELECT weight_delta_grams FROM old_gold_movements WHERE karat_id = %s AND weight_delta_grams IS NOT NULL "
            "AND balance_effect = 'COUNTED' FOR UPDATE", (int(karat_id),))
        from decimal import Decimal
        return sum((Decimal(str(r["weight_delta_grams"])) for r in rows), Decimal("0.000"))

    def db_now(self):
        return self._fetch_one("SELECT NOW() AS now")["now"].replace(microsecond=0)

    # ------------------------------------------------------------ writes

    def insert_correction(self, *, correction_type, source_type, source_id, source_reference, invoice_id,
                          chain_sequence, previous_correction_id, money_direction, settled_total, reason, before,
                          after, effects, performed_by_user_id, performed_by_employee_id, occurred_at):
        correction_id, _ = self._execute(
            """
            INSERT INTO transaction_corrections (
                correction_type, correction_status, source_type, source_id, source_reference, invoice_id,
                chain_sequence, previous_correction_id, money_direction, settled_total, reason, before_json,
                after_json, effects_json, performed_by_user_id, performed_by_employee_id, occurred_at
            )
            VALUES (%s, 'FINALIZED', %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (correction_type, source_type, _id(source_id), source_reference, _optional_id(invoice_id), chain_sequence,
             _optional_id(previous_correction_id), money_direction, settled_total, reason, _json(before), _json(after),
             _json(effects), performed_by_user_id, performed_by_employee_id, occurred_at))
        return str(correction_id)

    def insert_leg(self, *, correction_id, leg_role, leg_kind, amount, payment_method_code=None, bank_account_id=None,
                   cash_movement_id=None, bank_movement_id=None, customer_transaction_id=None):
        leg_id, _ = self._execute(
            """
            INSERT INTO transaction_correction_legs (
                correction_id, leg_role, leg_kind, payment_method_code, bank_account_id, amount,
                cash_movement_id, bank_movement_id, customer_transaction_id
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (_id(correction_id), leg_role, leg_kind, payment_method_code, bank_account_id, amount,
             _optional_id(cash_movement_id), _optional_id(bank_movement_id), _optional_id(customer_transaction_id)))
        return str(leg_id)

    def add_money(self, *, ledger, account_id, delta, type_code, correction_id, code, description, occurred_at,
                  created_by_user_id):
        """One signed movement on the real cash / bank ledger (a negative balance is allowed)."""
        table, column = ("cash_movements", "cash_account_id") if ledger == "CASH" else ("bank_movements", "bank_account_id")
        movement_id, _ = self._execute(
            f"""
            INSERT INTO {table} (
                {column}, movement_type_id, amount_delta, reference_type, reference_id, reference_code, description,
                occurred_at, created_by_user_id
            )
            SELECT %s, t.id, %s, %s, %s, %s, %s, %s, %s
            FROM {table[:-10]}_movement_types t WHERE t.code = %s
            """,
            (int(account_id), delta, REFERENCE_TYPE, _id(correction_id), code, description, occurred_at,
             created_by_user_id, type_code))
        if not movement_id:
            raise RuntimeError(f"{table} type {type_code} is missing")
        return str(movement_id)

    def set_item_status(self, *, inventory_item_id, from_status, to_status, updated_by_user_id):
        _, rowcount = self._execute(
            "UPDATE inventory_items SET inventory_status = %s, updated_by_user_id = %s WHERE id = %s AND inventory_status = %s",
            (to_status, updated_by_user_id, _id(inventory_item_id), from_status))
        if rowcount != 1:
            raise RuntimeError(f"Inventory item {inventory_item_id} is no longer {from_status}.")

    def add_inventory_movement(self, *, item, movement_code, from_status, to_status, correction_id, code, reason,
                               performed_by_user_id, performed_by_employee_id):
        movement_id, _ = self._execute(
            """
            INSERT INTO inventory_movements (
                inventory_item_id, movement_type_id, from_stock_classification, to_stock_classification,
                from_inventory_status, to_inventory_status, from_weight_grams, to_weight_grams,
                reference_type, reference_id, reference_code, reason, performed_by_user_id, performed_by_employee_id
            )
            SELECT %s, t.id, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
            FROM inventory_movement_types t WHERE t.code = %s AND t.is_active = TRUE
            """,
            (_id(item["inventory_item_id"]), item["stock_classification"], item["stock_classification"], from_status,
             to_status, item["weight_grams"], item["weight_grams"], REFERENCE_TYPE, _id(correction_id), code, reason,
             performed_by_user_id, performed_by_employee_id, movement_code))
        if not movement_id:
            raise RuntimeError(f"inventory movement type {movement_code} is missing")
        return str(movement_id)

    def add_old_gold(self, *, karat_id, weight_delta, movement_code, correction_id, code, notes, created_by_user_id,
                     employee_id, customer_id=None):
        movement_id, _ = self._execute(
            """
            INSERT INTO old_gold_movements (
                movement_type_id, karat_id, weight_delta_grams, balance_effect, reference_type, reference_id,
                reference_code, notes, created_by_user_id, performed_by_user_id, received_by_employee_id,
                movement_code, customer_id
            )
            SELECT t.id, %s, %s, 'COUNTED', %s, %s, %s, %s, %s, %s, %s, %s, %s
            FROM old_gold_movement_types t WHERE t.code = %s AND t.is_active = TRUE
            """,
            (int(karat_id), weight_delta, REFERENCE_TYPE, _id(correction_id), code, notes, created_by_user_id,
             created_by_user_id, employee_id, movement_code, _optional_id(customer_id), movement_code))
        if not movement_id:
            raise RuntimeError(f"old-gold movement type {movement_code} is missing")
        return str(movement_id)

    def dismiss_review(self, *, review_id, user_id, at, notes):
        _, rowcount = self._execute(
            """
            UPDATE management_reviews SET review_status = 'DISMISSED', resolved_by_user_id = %s, resolved_at = %s,
                   resolution_notes = %s
            WHERE id = %s AND review_type = 'RETURNED_ITEM_INSPECTION' AND review_status = 'OPEN'
            """, (user_id, at, notes, _id(review_id)))
        if rowcount != 1:
            raise RuntimeError("inspection review is no longer open")

    def cancel_case(self, *, case_id, user_id, at, notes):
        """The unidentified-sale case of a reversed sale: CANCELLED (never matched to a fake item)."""
        _, rowcount = self._execute(
            "UPDATE inventory_reconciliation_cases SET case_status = 'CANCELLED', resolved_by_user_id = %s, "
            "resolved_at = %s WHERE id = %s AND case_status = 'OPEN'", (user_id, at, _id(case_id)))
        if rowcount != 1:
            raise RuntimeError("reconciliation case is no longer open")
        self._execute(
            """
            INSERT INTO inventory_reconciliation_case_events (case_id, event_type, from_status, to_status, notes,
                                                              performed_by_user_id, occurred_at)
            VALUES (%s, 'CANCELLED', 'OPEN', 'CANCELLED', %s, %s, %s)
            """, (_id(case_id), notes, user_id, at))

    def mark_lines_reversed(self, *, table, line_ids, correction_id):
        assert table in ("invoice_lifecycle_lines", "invoice_return_lines")
        ids = [_id(i) for i in line_ids]
        if not ids:
            return
        _, rowcount = self._execute(
            f"UPDATE {table} SET reversed_by_correction_id = %s WHERE id IN ({', '.join(['%s'] * len(ids))}) "
            f"AND reversed_by_correction_id IS NULL", (_id(correction_id), *ids))
        if rowcount != len(ids):
            raise RuntimeError("lifecycle line already reversed")
