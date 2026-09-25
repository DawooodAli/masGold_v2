"""Dead Cash write-offs, recoveries and warning flags.

History is never deleted: a write-off is reversed by a flag update plus a
reversal ledger row, and a warning is deactivated, not removed. Runs on the
caller's transaction; BIGINT ids leave as decimal strings.
"""
from decimal import Decimal

from repositories.base_repository import BaseRepository


WARNING_FLAG = "DEAD_CASH_WARNING"
WRITEOFF_DIRECTIONS = ("CUSTOMER_OWES_SHOP", "SHOP_OWES_CUSTOMER")


class DeadCashRepositoryError(RuntimeError):
    pass


def _row(row, *fields):
    if row is None:
        return None
    return {key: (str(value) if key in fields and value is not None else value) for key, value in row.items()}


class DeadCashRepository(BaseRepository):

    _WRITEOFF_IDS = ("writeoff_id", "customer_id", "writeoff_transaction_id", "reversal_transaction_id")

    def _execute(self, query, params):
        cursor = self.connection.cursor()
        try:
            cursor.execute(query, params)
            return cursor.lastrowid, cursor.rowcount
        finally:
            cursor.close()

    # ------------------------------------------------------------ write-offs

    def insert_writeoff(self, *, customer_id, amount, balance_before, balance_after,
                        writeoff_transaction_id, reason, created_by_user_id, notes=None,
                        writeoff_direction="CUSTOMER_OWES_SHOP"):
        if writeoff_direction not in WRITEOFF_DIRECTIONS:
            raise ValueError("Unknown write-off direction.")
        writeoff_id, _ = self._execute(
            """
            INSERT INTO dead_cash_writeoffs (
                customer_id, writeoff_direction, amount, balance_before, balance_after,
                writeoff_transaction_id, reason, notes, created_by_user_id
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (int(customer_id), writeoff_direction, amount, balance_before, balance_after,
             int(writeoff_transaction_id), reason, notes, created_by_user_id),
        )
        return str(writeoff_id)

    def lock_writeoff(self, writeoff_id):
        return _row(self._fetch_one(
            """
            SELECT id AS writeoff_id, customer_id, writeoff_direction, amount,
                   balance_before, balance_after, writeoff_transaction_id, reason, occurred_at,
                   is_reversed, reversed_at, reversed_by_user_id, reversal_transaction_id, reversal_reason
            FROM dead_cash_writeoffs
            WHERE id = %s
            FOR UPDATE
            """,
            (int(writeoff_id),),
        ), *self._WRITEOFF_IDS)

    def mark_writeoff_reversed(self, *, writeoff_id, reversal_transaction_id, reversed_by_user_id, reason):
        """One-way: only an unreversed write-off can be marked reversed."""
        _, rowcount = self._execute(
            """
            UPDATE dead_cash_writeoffs
            SET is_reversed = TRUE, reversed_at = CURRENT_TIMESTAMP, reversed_by_user_id = %s,
                reversal_transaction_id = %s, reversal_reason = %s
            WHERE id = %s AND is_reversed = FALSE
            """,
            (reversed_by_user_id, int(reversal_transaction_id), reason, int(writeoff_id)),
        )
        if rowcount != 1:
            raise DeadCashRepositoryError(f"Write-off {writeoff_id} is missing or already reversed.")

    # ------------------------------------------------------------ recoveries

    def insert_recovery(self, *, writeoff_id, customer_id, amount, recovery_transaction_id,
                        payment_transaction_id, created_by_user_id, notes=None):
        recovery_id, _ = self._execute(
            """
            INSERT INTO dead_cash_recoveries (
                dead_cash_writeoff_id, customer_id, amount,
                recovery_transaction_id, payment_transaction_id, notes, created_by_user_id
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            """,
            (int(writeoff_id), int(customer_id), amount, int(recovery_transaction_id),
             int(payment_transaction_id), notes, created_by_user_id),
        )
        return str(recovery_id)

    def recovered_total(self, writeoff_id) -> Decimal:
        """Sum of recoveries; call after lock_writeoff() so the cap check is serialized."""
        row = self._fetch_one(
            """
            SELECT CAST(COALESCE(SUM(amount), 0) AS DECIMAL(16,2)) AS total
            FROM dead_cash_recoveries
            WHERE dead_cash_writeoff_id = %s
            """,
            (int(writeoff_id),),
        )
        return Decimal(str(row["total"]))

    # ------------------------------------------------------------ warning flags

    def get_active_warning(self, customer_id, for_update=False):
        query = """
            SELECT id AS flag_id, customer_id, flag_type, severity, reason, related_dead_cash_id,
                   created_at, created_by_user_id
            FROM customer_flags
            WHERE customer_id = %s AND flag_type = %s AND is_active = TRUE
        """
        if for_update:
            query += " FOR UPDATE"
        return _row(self._fetch_one(query, (int(customer_id), WARNING_FLAG)),
                    "flag_id", "customer_id", "related_dead_cash_id")

    def create_warning(self, *, customer_id, reason, created_by_user_id, related_dead_cash_id=None,
                       severity="WARNING"):
        """At most one active warning per customer (uq_customer_active_flag)."""
        flag_id, _ = self._execute(
            """
            INSERT INTO customer_flags (
                customer_id, flag_type, severity, reason, related_dead_cash_id, created_by_user_id
            )
            VALUES (%s, %s, %s, %s, %s, %s)
            """,
            (int(customer_id), WARNING_FLAG, severity, reason,
             None if related_dead_cash_id is None else int(related_dead_cash_id), created_by_user_id),
        )
        return str(flag_id)

    def deactivate_warning(self, *, flag_id, deactivated_by_user_id, reason):
        """One-way: the flag row stays as history."""
        _, rowcount = self._execute(
            """
            UPDATE customer_flags
            SET is_active = FALSE, deactivated_at = CURRENT_TIMESTAMP,
                deactivated_by_user_id = %s, deactivation_reason = %s
            WHERE id = %s AND flag_type = %s AND is_active = TRUE
            """,
            (deactivated_by_user_id, reason, int(flag_id), WARNING_FLAG),
        )
        if rowcount != 1:
            raise DeadCashRepositoryError(f"Warning {flag_id} is missing or already inactive.")
