"""رصيد القفل (persistence only): the weight-only clasp ledger per karat. Every write runs on the caller transaction; nothing here commits and no row is ever updated or deleted."""
import json
from decimal import Decimal

from repositories.base_repository import BaseRepository


class ClaspStockRepository(BaseRepository):

    def _execute(self, query, params=()):
        cursor = self.connection.cursor()
        try:
            cursor.execute(query, params)
            return cursor.lastrowid
        finally:
            cursor.close()

    def has_schema(self):
        return self._fetch_one("SELECT 1 AS ok FROM information_schema.tables WHERE table_schema = DATABASE() AND table_name = 'clasp_stock_movements'") is not None

    def karat(self, karat_id):
        return self._fetch_one("SELECT id, karat_value, name, is_active FROM karats WHERE id = %s", (int(karat_id),))

    def balances(self):
        """The grams of every active karat (SUM of the signed ledger)."""
        return self._fetch_all(
            """
            SELECT k.id AS karat_id, k.karat_value, k.name AS karat_name, COALESCE(SUM(c.delta_grams), 0) AS grams
            FROM karats k LEFT JOIN clasp_stock_movements c ON c.karat_id = k.id
            WHERE k.is_active = TRUE GROUP BY k.id, k.karat_value, k.name, k.sort_order ORDER BY k.sort_order, k.karat_value
            """)

    def lock_balance(self, karat_id):
        """Locks the karat ledger rows (the lock every clasp consumer takes) and returns the grams."""
        rows = self._fetch_all("SELECT delta_grams FROM clasp_stock_movements WHERE karat_id = %s FOR UPDATE", (int(karat_id),))
        return sum((Decimal(str(r["delta_grams"])) for r in rows), Decimal("0.000"))

    def by_key(self, request_key):
        return self._fetch_one("SELECT id, karat_id, movement_code, delta_grams, created_by_user_id FROM clasp_stock_movements WHERE request_key = %s", (request_key,))

    def recent(self, limit=30):
        return self._fetch_all(
            "SELECT c.id, k.karat_value, c.movement_code, c.delta_grams, c.reason, c.created_at, u.username FROM clasp_stock_movements c "
            "JOIN karats k ON k.id = c.karat_id JOIN users u ON u.id = c.created_by_user_id ORDER BY c.id DESC LIMIT %s", (int(limit),))

    def insert(self, *, karat_id, code, delta, reason, request_key, user_id, reference_type=None, reference_id=None):
        return str(self._execute(
            "INSERT INTO clasp_stock_movements (karat_id, movement_code, delta_grams, reason, reference_type, reference_id, request_key, created_by_user_id, created_at) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, NOW())", (int(karat_id), code, delta, reason, reference_type, reference_id, request_key, user_id)))

    def audit(self, *, user_id, movement_id, description, metadata):
        self._execute(
            "INSERT INTO audit_log (user_id, event_type, entity_type, entity_id, entity_reference, description, metadata_json, occurred_at) "
            "VALUES (%s, 'CLASP_STOCK_CHANGED', 'CLASP_STOCK_MOVEMENT', %s, %s, %s, %s, NOW())",
            (user_id, int(movement_id), f"CL{int(movement_id)}", description, json.dumps(metadata, ensure_ascii=False, default=str)))
