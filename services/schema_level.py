"""The schema level this build of the application needs, and a clear message when the database is behind it.

A missing table or column raised by a newer query (MySQL 1146 / 1054) used to surface as the generic «تعذر تحميل بيانات الفاتورة» on every screen that reads
it. When that happens, the worker asks `schema_problem()`: if the database's highest applied migration is lower than REQUIRED_SCHEMA_LEVEL, the employee gets
the specific instruction instead. Read only; any failure of the check itself returns None (the generic message then stays)."""

REQUIRED_SCHEMA_LEVEL = 71            # the highest migration in database/schema (a test keeps this equal to the chain)
SCHEMA_ERRNOS = (1146, 1054)          # ER_NO_SUCH_TABLE, ER_BAD_FIELD_ERROR


def outdated_message(current, required=REQUIRED_SCHEMA_LEVEL):
    return f"قاعدة البيانات على المستوى {current} والمطلوب {required}: طبّق التحديثات."


def current_level(connection):
    cursor = connection.cursor()
    try:
        cursor.execute("SELECT MAX(version) FROM schema_migrations")
        row = cursor.fetchone()
        return None if row is None or row[0] is None else int(row[0])
    finally:
        cursor.close()


def schema_problem(connection_factory=None, required=REQUIRED_SCHEMA_LEVEL):
    """The instruction message when the database is behind `required`, else None (also None when the level cannot be read)."""
    try:
        if connection_factory is None:
            from config.database_config import DatabaseConfig
            from database.connection import DatabaseConnection
            connection = DatabaseConnection(DatabaseConfig.from_env()).connect()
        else:
            connection = connection_factory()
        try:
            level = current_level(connection)
            connection.rollback()
        finally:
            connection.close()
    except Exception:
        return None
    return outdated_message(level, required) if level is not None and level < required else None
