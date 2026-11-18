"""Small helpers that several services used to copy (identical bodies), kept in one place."""
from decimal import Decimal


def dec(value):
    """A Decimal from any stored value; None becomes 0 (never goes through float)."""
    return Decimal(str(value if value is not None else 0))


def query_rows(connection, sql, params=()):
    """Run one SELECT on an OPEN transaction connection and return its rows as dictionaries."""
    cursor = connection.cursor(dictionary=True)
    try:
        cursor.execute(sql, params)
        return cursor.fetchall()
    finally:
        cursor.close()
