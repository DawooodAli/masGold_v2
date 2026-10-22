"""Persistence of the minimum stock levels (migration 072): one rule per (item type, karat) saying how many pieces and / or grams the owner wants to keep in stock."""
import json

from repositories.base_repository import BaseRepository


class StockMinLevelRepository(BaseRepository):
    def now(self):
        return self._fetch_one("SELECT NOW() AS now")["now"].replace(microsecond=0)

    def item_types(self):
        return self._fetch_all("SELECT id, name FROM item_types WHERE is_active = TRUE ORDER BY name, id")

    def karats(self):
        return self._fetch_all("SELECT id, karat_value FROM karats WHERE is_active = TRUE ORDER BY karat_value DESC")

    def levels(self):
        """Every rule with what is in stock now for its type and karat (finished pieces IN_STOCK)."""
        return self._fetch_all(
            """
            SELECT l.id, l.item_type_id, l.karat_id, t.name AS item_type, k.karat_value, l.min_pieces, l.min_grams,
                   COALESCE(s.pieces, 0) AS pieces, COALESCE(s.grams, 0) AS grams
            FROM stock_min_levels l
            JOIN item_types t ON t.id = l.item_type_id
            JOIN karats k ON k.id = l.karat_id
            LEFT JOIN (SELECT item_type_id, karat_id, COUNT(*) AS pieces, SUM(weight_grams) AS grams FROM inventory_items WHERE inventory_status = 'IN_STOCK' GROUP BY item_type_id, karat_id) s
                   ON s.item_type_id = l.item_type_id AND s.karat_id = l.karat_id
            WHERE l.is_active = TRUE ORDER BY t.name, k.karat_value DESC
            """)

    def level(self, level_id, lock=False):
        return self._fetch_one("SELECT id, item_type_id, karat_id FROM stock_min_levels WHERE id = %s" + (" FOR UPDATE" if lock else ""), (level_id,))

    def type_exists(self, item_type_id):
        return self._fetch_one("SELECT id, name FROM item_types WHERE id = %s AND is_active = TRUE", (item_type_id,))

    def karat_exists(self, karat_id):
        return self._fetch_one("SELECT id, karat_value FROM karats WHERE id = %s AND is_active = TRUE", (karat_id,))

    def _execute(self, query, params=()):
        cursor = self.connection.cursor()
        try:
            cursor.execute(query, params)
            return cursor.lastrowid, cursor.rowcount
        finally:
            cursor.close()

    def upsert(self, *, item_type_id, karat_id, min_pieces, min_grams, user_id):
        self._execute("INSERT INTO stock_min_levels (item_type_id, karat_id, min_pieces, min_grams, is_active, created_by_user_id) VALUES (%s, %s, %s, %s, 1, %s) "
                      "ON DUPLICATE KEY UPDATE min_pieces = VALUES(min_pieces), min_grams = VALUES(min_grams), is_active = 1", (item_type_id, karat_id, min_pieces, min_grams, user_id))
        return self._fetch_one("SELECT id FROM stock_min_levels WHERE item_type_id = %s AND karat_id = %s", (item_type_id, karat_id))["id"]

    def delete(self, level_id):
        self._execute("DELETE FROM stock_min_levels WHERE id = %s", (level_id,))

    def audit(self, *, user_id, event_type, level_id, description, metadata, occurred_at):
        cursor = self.connection.cursor()
        try:
            cursor.execute("INSERT INTO audit_log (user_id, event_type, entity_type, entity_id, entity_reference, description, metadata_json, occurred_at) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                           (user_id, event_type, "STOCK_MIN_LEVEL", int(level_id), str(level_id), description, json.dumps(metadata, ensure_ascii=False, default=str), occurred_at))
        finally:
            cursor.close()
