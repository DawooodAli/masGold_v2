from mysql.connector.connection import MySQLConnection

from config.database_config import DatabaseConfig
from database.connection import DatabaseConnection


class DatabaseTransaction:
    """
    Provides one atomic database transaction.

    Usage:

        with DatabaseTransaction() as tx:
            cursor = tx.connection.cursor()
            cursor.execute(...)

    If no exception occurs:
        COMMIT

    If any exception occurs:
        ROLLBACK
    """

    def __init__(self):
        config = DatabaseConfig.from_env()
        self.database = DatabaseConnection(config)

        self.connection: MySQLConnection | None = None
        self._completed = False

    def __enter__(self) -> "DatabaseTransaction":
        self.connection = self.database.connect()
        self.connection.start_transaction()

        return self

    def commit(self) -> None:
        if self.connection is None:
            raise RuntimeError(
                "Cannot commit: transaction connection is not open."
            )

        self.connection.commit()
        self._completed = True

    def rollback(self) -> None:
        if self.connection is None:
            return

        self.connection.rollback()
        self._completed = True

    def __exit__(
        self,
        exc_type,
        exc_value,
        traceback,
    ) -> bool:

        try:

            if self.connection is None:
                return False

            if exc_type is not None:
                self.rollback()

            elif not self._completed:
                self.commit()

        finally:

            if (
                self.connection is not None
                and self.connection.is_connected()
            ):
                self.connection.close()

        # False means:
        # Do not hide exceptions.
        return False