from mysql.connector import Error
from mysql.connector.connection import MySQLConnection

from config.database_config import DatabaseConfig


class DatabaseConnection:

    def __init__(self, config: DatabaseConfig):
        self.config = config

    def connect(self) -> MySQLConnection:
        try:
            connection = MySQLConnection(
                host=self.config.host,
                port=self.config.port,
                database=self.config.database,
                user=self.config.user,
                password=self.config.password,
                autocommit=False,
            )

            return connection

        except Error as exc:
            raise RuntimeError(
                f"Could not connect to masGold database: {exc}"
            ) from exc