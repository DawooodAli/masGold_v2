from typing import Any

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