from typing import Any

from mysql.connector import ProgrammingError
from mysql.connector.connection import MySQLConnection


class BaseRepository:

    def __init__(self, connection: MySQLConnection):
        self.connection = connection

    def _fetch_all(
        self,
        query: str,
        params: tuple | None = None,
    ) -> list[dict[str, Any]]:

        cursor = self.connection.cursor(dictionary=True)

        try:
            cursor.execute(query, params or ())
            return cursor.fetchall()

        finally:
            cursor.close()

    def _fetch_one(
        self,
        query: str,
        params: tuple | None = None,
    ) -> dict[str, Any] | None:

        cursor = self.connection.cursor(dictionary=True)

        try:
            cursor.execute(query, params or ())
            return cursor.fetchone()

        finally:
            cursor.close()

    def business_values(self, keys: tuple[str, ...]) -> dict[str, str]:
        """The stored text of the given System Settings keys (migration 081), for BUSINESS logic that
        applies a rule (a report threshold, a discount limit, a price warning).

        No permission is checked here on purpose: opening the settings screen needs
        SYSTEM_SETTINGS_VIEW, but a cashier running the alerts report or posting an invoice must not
        be blocked by a settings right. A database below migration 081 has no `system_settings` table
        and simply gets no rows, so every caller falls back to its own hard-coded default."""
        if not keys:
            return {}
        placeholders = ", ".join(["%s"] * len(keys))
        try:
            rows = self._fetch_all(
                "SELECT setting_key, setting_value FROM system_settings "
                f"WHERE setting_key IN ({placeholders})",
                tuple(keys),
            )
        except ProgrammingError as exc:
            if getattr(exc, "errno", None) == 1146:                       # no such table: a database below 081
                return {}
            raise
        return {str(row["setting_key"]): row["setting_value"] for row in rows}