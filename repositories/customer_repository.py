from datetime import datetime

from repositories.base_repository import BaseRepository
from utils.search_normalization import (
    mysql_search_expression,
    normalize_mobile_search,
    normalize_search_text,
)


class CustomerRepository(BaseRepository):
    """Customer profiles are writable; balances and histories are read-only."""

    _CUSTOMER_SELECT = """
        SELECT
            c.id AS customer_id,
            c.customer_code,
            c.name,
            c.mobile,
            c.place,
            c.customer_type_id,
            ty.code AS customer_type_code,
            ty.name AS customer_type_name,
            ty.is_active AS customer_type_is_active,
            c.is_active,
            c.notes,
            c.created_at,
            c.updated_at,
            c.created_by_user_id,
            c.updated_by_user_id,
            b.balance,
            b.balance_status,
            b.last_transaction_at,
            (
                SELECT COUNT(*) FROM customer_flags f
                WHERE f.customer_id = c.id AND f.is_active = TRUE
            ) AS active_warning_count,
            EXISTS (
                SELECT 1 FROM customer_flags f
                WHERE f.customer_id = c.id AND f.is_active = TRUE
                  AND f.flag_type = 'DEAD_CASH_WARNING'
            ) AS has_active_dead_cash_warning,
            EXISTS (
                SELECT 1 FROM dead_cash_writeoffs d
                WHERE d.customer_id = c.id
            ) AS has_dead_cash_history,
            (
                SELECT COUNT(*) FROM dead_cash_writeoffs d
                WHERE d.customer_id = c.id AND d.is_reversed = FALSE
            ) AS unreversed_dead_cash_count
        FROM customers c
        LEFT JOIN customer_types ty
            ON ty.id = c.customer_type_id
        JOIN vw_customer_balances b
            ON b.customer_id = c.id
    """

    def get_customer_types(self) -> list[dict]:
        return self._fetch_all(
            """
            SELECT id, code, name, is_active, sort_order
            FROM customer_types
            ORDER BY sort_order, code, id
            """
        )

    def get_customer_transaction_types(self) -> list[dict]:
        # Inactive types still describe historical movements.
        return self._fetch_all(
            """
            SELECT id, code, name, expected_direction, is_active
            FROM customer_transaction_types
            ORDER BY code, id
            """
        )

    def get_customer_type_by_id(
        self,
        customer_type_id: int,
        for_update: bool = False,
    ) -> dict | None:
        query = """
            SELECT id, code, name, is_active, sort_order
            FROM customer_types
            WHERE id = %s
        """
        if for_update:
            query += " FOR UPDATE"
        return self._fetch_one(query, (customer_type_id,))

    def search_customers(
        self,
        search: str = "",
        is_active: bool | None = None,
        customer_type_id: int | None = None,
        balance_status: str | None = None,
        limit: int = 101,
        offset: int = 0,
    ) -> list[dict]:
        search = search.strip()
        text = normalize_search_text(search)
        mobile = normalize_mobile_search(search)

        def like_pattern(value: str) -> str:
            # Escape after normalization: employee input is a literal substring.
            escaped = value.replace("!", "!!").replace("%", "!%").replace("_", "!_")
            return f"%{escaped}%"

        name_sql, name_params = mysql_search_expression("c.name")
        mobile_sql, mobile_params = mysql_search_expression("c.mobile", mobile=True)
        place_sql, place_params = mysql_search_expression("c.place")
        numeric_id = None
        if text.isdecimal():
            # Search normalization also accepts Arabic/Persian digits. A long mobile
            # number remains searchable text without overflowing BIGINT.
            significant_digits = text.lstrip("0")
            if len(significant_digits) <= 20:
                candidate = int(significant_digits or "0")
                if 0 < candidate <= 18446744073709551615:
                    numeric_id = candidate

        # Bind emptiness as booleans: an accent-insensitive SQL collation can
        # consider nonempty marks-only input equal to the empty string.
        return self._fetch_all(
            self._CUSTOMER_SELECT
            + f"""
            WHERE (
                %s
                OR c.id = %s
                OR c.customer_code LIKE %s ESCAPE '!'
                OR (%s AND {name_sql} LIKE %s ESCAPE '!')
                OR (%s AND {mobile_sql} LIKE %s ESCAPE '!')
                OR (%s AND {place_sql} LIKE %s ESCAPE '!')
            )
            AND (%s IS NULL OR c.is_active = %s)
            AND (%s IS NULL OR c.customer_type_id = %s)
            AND (%s IS NULL OR b.balance_status = %s)
            ORDER BY c.id DESC
            LIMIT %s OFFSET %s
            """,
            (
                not search, numeric_id, like_pattern(search),
                bool(text), *name_params, like_pattern(text),
                bool(mobile), *mobile_params, like_pattern(mobile),
                bool(text), *place_params, like_pattern(text),
                is_active, is_active,
                customer_type_id, customer_type_id,
                balance_status, balance_status,
                limit, offset,
            ),
        )

    def get_customer_by_id(self, customer_id: int) -> dict | None:
        return self._fetch_one(
            self._CUSTOMER_SELECT + " WHERE c.id = %s",
            (customer_id,),
        )

    def get_customer_for_update(self, customer_id: int) -> dict | None:
        return self._fetch_one(
            """
            SELECT
                id, customer_code, name, mobile, place,
                customer_type_id, is_active, notes, updated_at
            FROM customers
            WHERE id = %s
            FOR UPDATE
            """,
            (customer_id,),
        )

    def get_customer_transactions(
        self,
        customer_id: int,
        limit: int = 101,
        offset: int = 0,
    ) -> list[dict]:
        result = self.get_customer_statement(customer_id, limit=limit, offset=offset)
        return result["items"] if result is not None else []

    _STATEMENT_SUMMARY_FIELDS = (
        "ledger_count", "ledger_max_id", "ledger_balance", "opening_balance",
        "period_net", "closing_balance", "matching_count", "matching_net",
    )
    _STATEMENT_MOVEMENT_FIELDS = (
        "transaction_id", "customer_id", "transaction_type_code",
        "transaction_type_name", "expected_direction", "amount_delta",
        "reference_type", "reference_id", "reference_code", "description",
        "notes", "occurred_at", "created_at", "created_by_user_id",
        "legacy_source_table", "legacy_source_id", "running_balance",
    )

    def get_customer_statement(
        self,
        customer_id: int,
        *,
        from_at: datetime | None = None,
        to_exclusive: datetime | None = None,
        transaction_type_code: str | None = None,
        limit: int = 101,
        offset: int = 0,
    ) -> dict | None:
        # One SQL statement gives profile/view balance, totals, and page the
        # same MVCC snapshot, even if the connection isolation later changes.
        # These identifiers are fixed code constants, never employee input.
        summary_columns = ",\n".join(
            f"s.{field} AS summary_{field}" for field in self._STATEMENT_SUMMARY_FIELDS
        )
        movement_columns = ",\n".join(
            f"p.{field} AS movement_{field}" for field in self._STATEMENT_MOVEMENT_FIELDS
        )
        rows = self._fetch_all(
            f"""
            WITH customer_profile AS (
                {self._CUSTOMER_SELECT}
                WHERE c.id = %s
            ), ledger AS (
                SELECT
                    t.id AS transaction_id,
                    t.customer_id,
                    tt.code AS transaction_type_code,
                    tt.name AS transaction_type_name,
                    tt.expected_direction,
                    t.amount_delta,
                    t.reference_type,
                    t.reference_id,
                    t.reference_code,
                    t.description,
                    t.notes,
                    t.occurred_at,
                    t.created_at,
                    t.created_by_user_id,
                    t.legacy_source_table,
                    t.legacy_source_id,
                    SUM(t.amount_delta) OVER (
                        ORDER BY t.occurred_at ASC, t.id ASC
                        ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
                    ) AS running_balance
                FROM customer_transactions t
                JOIN customer_transaction_types tt
                    ON tt.id = t.transaction_type_id
                WHERE t.customer_id = %s
            ), classified AS (
                SELECT ledger.*,
                    (
                        (%s IS NULL OR occurred_at >= %s)
                        AND (%s IS NULL OR occurred_at < %s)
                    ) AS in_range
                FROM ledger
            ), summary AS (
                SELECT
                    COUNT(*) AS ledger_count,
                    COALESCE(MAX(transaction_id), 0) AS ledger_max_id,
                    COALESCE(SUM(amount_delta), 0) AS ledger_balance,
                    COALESCE(SUM(CASE
                        WHEN %s IS NOT NULL AND occurred_at < %s
                        THEN amount_delta ELSE 0
                    END), 0) AS opening_balance,
                    COALESCE(SUM(CASE
                        WHEN in_range THEN amount_delta ELSE 0
                    END), 0) AS period_net,
                    COALESCE(SUM(CASE
                        WHEN %s IS NULL OR occurred_at < %s
                        THEN amount_delta ELSE 0
                    END), 0) AS closing_balance,
                    COUNT(CASE
                        WHEN in_range AND (%s IS NULL OR transaction_type_code = %s)
                        THEN 1
                    END) AS matching_count,
                    COALESCE(SUM(CASE
                        WHEN in_range AND (%s IS NULL OR transaction_type_code = %s)
                        THEN amount_delta ELSE 0
                    END), 0) AS matching_net
                FROM classified
            ), statement_page AS (
                SELECT * FROM classified
                WHERE in_range AND (%s IS NULL OR transaction_type_code = %s)
                ORDER BY occurred_at DESC, transaction_id DESC
                LIMIT %s OFFSET %s
            )
            SELECT cp.*,
                {summary_columns},
                {movement_columns},
                CURRENT_TIMESTAMP AS statement_loaded_at
            FROM customer_profile cp
            CROSS JOIN summary s
            LEFT JOIN statement_page p ON TRUE
            ORDER BY p.occurred_at DESC, p.transaction_id DESC
            """,
            (
                customer_id, customer_id,
                from_at, from_at, to_exclusive, to_exclusive,
                from_at, from_at, to_exclusive, to_exclusive,
                transaction_type_code, transaction_type_code,
                transaction_type_code, transaction_type_code,
                transaction_type_code, transaction_type_code,
                limit, offset,
            ),
        )
        if not rows:
            return None
        first = rows[0]
        customer = {
            key: value for key, value in first.items()
            if not key.startswith(("summary_", "movement_"))
            and key != "statement_loaded_at"
        }
        summary = {
            field: first[f"summary_{field}"] for field in self._STATEMENT_SUMMARY_FIELDS
        }
        summary["current_balance"] = customer["balance"]
        # The LEFT JOIN supplies one summary-only row for an empty page.
        items = [
            {field: row[f"movement_{field}"] for field in self._STATEMENT_MOVEMENT_FIELDS}
            for row in rows if row["movement_transaction_id"] is not None
        ]
        return {
            "customer": customer,
            "summary": summary,
            "items": items,
            "loaded_at": first["statement_loaded_at"],
        }

    def get_active_customer_flags(self, customer_id: int) -> list[dict]:
        return self._fetch_all(
            """
            SELECT
                id AS flag_id,
                flag_type,
                severity,
                reason,
                related_dead_cash_id,
                created_at,
                created_by_user_id
            FROM customer_flags
            WHERE customer_id = %s AND is_active = TRUE
            ORDER BY
                CASE severity
                    WHEN 'CRITICAL' THEN 0
                    WHEN 'WARNING' THEN 1
                    ELSE 2
                END,
                created_at DESC,
                id DESC
            """,
            (customer_id,),
        )

    def get_dead_cash_history(
        self,
        customer_id: int,
        limit: int = 101,
        offset: int = 0,
    ) -> list[dict]:
        return self._fetch_all(
            """
            SELECT
                id AS dead_cash_id,
                amount,
                balance_before,
                balance_after,
                writeoff_transaction_id,
                reason,
                notes,
                occurred_at,
                created_by_user_id,
                is_reversed,
                reversed_at,
                reversed_by_user_id,
                reversal_transaction_id,
                reversal_reason
            FROM dead_cash_writeoffs
            WHERE customer_id = %s
            ORDER BY occurred_at DESC, id DESC
            LIMIT %s OFFSET %s
            """,
            (customer_id, limit, offset),
        )

    def create_customer(
        self,
        name: str,
        mobile: str | None,
        place: str | None,
        customer_type_id: int | None,
        notes: str | None,
        created_by_user_id: int | None = None,
    ) -> int:
        cursor = self.connection.cursor()
        try:
            cursor.execute(
                """
                INSERT INTO customers (
                    name, customer_code, mobile, place,
                    customer_type_id, notes, created_by_user_id
                )
                VALUES (%s, NULL, %s, %s, %s, %s, %s)
                """,
                (
                    name, mobile, place,
                    customer_type_id, notes, created_by_user_id,
                ),
            )
            customer_id = int(cursor.lastrowid)
            # Both statements use the service's transaction. Only this newly
            # inserted row receives a code; existing customers are untouched.
            cursor.execute(
                """
                UPDATE customers
                SET customer_code = %s
                WHERE id = %s AND customer_code IS NULL
                """,
                (f"C{customer_id:06d}", customer_id),
            )
            if cursor.rowcount != 1:
                raise RuntimeError("تعذر تعيين كود العميل الجديد.")
            return customer_id
        finally:
            cursor.close()

    def update_customer(
        self,
        customer_id: int,
        name: str,
        mobile: str | None,
        place: str | None,
        customer_type_id: int | None,
        notes: str | None,
        updated_by_user_id: int | None = None,
    ) -> None:
        cursor = self.connection.cursor()
        try:
            cursor.execute(
                """
                UPDATE customers
                SET name = %s,
                    mobile = %s,
                    place = %s,
                    customer_type_id = %s,
                    notes = %s,
                    updated_by_user_id = %s
                WHERE id = %s
                """,
                (
                    name, mobile, place,
                    customer_type_id, notes, updated_by_user_id, customer_id,
                ),
            )
        finally:
            cursor.close()

    def set_customer_active(
        self,
        customer_id: int,
        is_active: bool,
        updated_by_user_id: int | None = None,
    ) -> None:
        cursor = self.connection.cursor()
        try:
            cursor.execute(
                """
                UPDATE customers
                SET is_active = %s, updated_by_user_id = %s
                WHERE id = %s
                """,
                (is_active, updated_by_user_id, customer_id),
            )
        finally:
            cursor.close()
