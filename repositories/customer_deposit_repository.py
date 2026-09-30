"""عربون / استرداد عربون persistence (027). All writes use the caller's transaction; nothing is ever updated or
deleted. The customer balance stays the canonical customer_transactions ledger."""
import json

from repositories.base_repository import BaseRepository
from repositories.invoice_repository import _id

SOURCE = "CUSTOMER_DEPOSIT"


def deposit_code(direction, deposit_id):
    """DP + 6 digits for عربون (IN), DR + 6 digits for استرداد عربون (OUT)."""
    return f"{'DP' if direction == 'IN' else 'DR'}{int(deposit_id):06d}"


class CustomerDepositRepository(BaseRepository):

    def _execute(self, query, params=()):
        cursor = self.connection.cursor()
        try:
            cursor.execute(query, params)
            return cursor.lastrowid, cursor.rowcount
        finally:
            cursor.close()

    def by_request(self, key):
        return self._fetch_one("SELECT * FROM customer_deposits WHERE request_key = %s FOR UPDATE", (key,))

    def outstanding(self, customer_id):
        """Deposits minus refunds of the customer, excluding every fully reversed row (read under the
        caller's customer lock, so concurrent refunds serialize on it)."""
        row = self._fetch_one(
            """
            SELECT COALESCE(SUM(IF(d.direction = 'IN', d.amount, -d.amount)), 0) AS outstanding
            FROM customer_deposits d
            WHERE d.customer_id = %s AND NOT EXISTS (
                SELECT 1 FROM transaction_corrections c WHERE c.source_type = %s AND c.source_id = d.id
                  AND c.correction_type = 'FULL_REVERSAL')
            """, (_id(customer_id), SOURCE))
        return row["outstanding"]

    def source(self, deposit_id, lock=False):
        row = self._fetch_one(
            "SELECT d.*, c.name AS customer_name FROM customer_deposits d JOIN customers c ON c.id = d.customer_id "
            "WHERE d.id = %s" + (" FOR UPDATE" if lock else ""), (_id(deposit_id),))
        if row is None:
            return None
        row = dict(row)
        row["id"], row["customer_id"] = str(row["id"]), str(row["customer_id"])
        return row

    def insert_header(self, *, customer_id, direction, amount, key, digest, cash_account_id, before, after, notes,
                      actor, now):
        deposit_id, _ = self._execute(
            """
            INSERT INTO customer_deposits (customer_id, direction, amount, request_key, request_hash, cash_account_id,
                balance_before, balance_after, notes, performed_by_user_id, performed_by_employee_id, occurred_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (_id(customer_id), direction, amount, key, digest, int(cash_account_id), before, after, notes,
             actor.user_id, actor.employee_id, now))
        return str(deposit_id)

    def post_cash(self, *, deposit_id, code, direction, amount, cash_account_id, text, actor, now):
        """The real cash movement (a negative safe balance is allowed)."""
        type_code = "CUSTOMER_DEPOSIT" if direction == "IN" else "CUSTOMER_DEPOSIT_REFUND"
        movement_id, count = self._execute(
            """
            INSERT INTO cash_movements (cash_account_id, movement_type_id, amount_delta, reference_type, reference_id,
                reference_code, description, occurred_at, created_by_user_id)
            SELECT %s, t.id, %s, %s, %s, %s, %s, %s, %s FROM cash_movement_types t WHERE t.code = %s AND t.is_active = TRUE
            """,
            (int(cash_account_id), amount if direction == "IN" else -amount, SOURCE, _id(deposit_id), code, text, now,
             actor.user_id, type_code))
        if count != 1:
            raise RuntimeError(f"cash_movement_types {type_code} is missing (migration 027)")
        return str(movement_id)

    def audit(self, *, actor, deposit_id, code, direction, description, metadata, now):
        self._execute(
            "INSERT INTO audit_log (user_id, event_type, entity_type, entity_id, entity_reference, description, "
            "metadata_json, occurred_at) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
            (actor.user_id, SOURCE if direction == "IN" else "CUSTOMER_DEPOSIT_REFUND", SOURCE, int(deposit_id), code,
             description, json.dumps(metadata, ensure_ascii=False, default=str), now))

    def db_now(self):
        return self._fetch_one("SELECT NOW() AS now")["now"].replace(microsecond=0)
