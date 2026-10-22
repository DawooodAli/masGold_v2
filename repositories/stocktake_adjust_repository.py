"""Persistence of the stocktake write-off (migration 073): the pieces of one counted scope are locked, the missing ones leave the stock (status REMOVED, a STOCKTAKE_WRITE_OFF movement) and the
approval is stored with its reconciliation. Nothing is deleted."""
import json

from repositories.base_repository import BaseRepository


class StocktakeAdjustRepository(BaseRepository):
    def now(self):
        return self._fetch_one("SELECT NOW() AS now")["now"].replace(microsecond=0)

    def has_schema(self):
        return self._fetch_one("SELECT 1 AS ok FROM information_schema.TABLES WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'stocktake_adjustments'") is not None

    def adjustment_by_key(self, request_key):
        return self._fetch_one("SELECT id, code, request_hash, written_off_count, written_off_grams FROM stocktake_adjustments WHERE request_key = %s", (request_key,))

    def scope_pieces(self, item_type_id, karat_id):
        """Every piece IN_STOCK of the scope, LOCKED (the same predicate as the stocktake source)."""
        where, params = "ii.inventory_status = 'IN_STOCK'", []
        if item_type_id is not None:
            where += " AND ii.item_type_id = %s"
            params.append(item_type_id)
        if karat_id is not None:
            where += " AND ii.karat_id = %s"
            params.append(karat_id)
        return self._fetch_all(
            f"""
            SELECT ii.id, ii.karat_id, k.karat_value, ii.weight_grams, ii.stock_classification,
                   (SELECT ib.barcode_value FROM inventory_barcodes ib WHERE ib.inventory_item_id = ii.id AND ib.barcode_status = 'ACTIVE' LIMIT 1) AS barcode_value
            FROM inventory_items ii
            JOIN karats k ON k.id = ii.karat_id
            WHERE {where}
            ORDER BY ii.id
            FOR UPDATE OF ii
            """, tuple(params))

    def pieces_by_ids(self, ids):
        """The chosen pieces that are still IN_STOCK, LOCKED."""
        if not ids:
            return []
        marks = ", ".join(["%s"] * len(ids))
        return self._fetch_all(
            f"""
            SELECT ii.id, ii.karat_id, k.karat_value, ii.weight_grams, ii.stock_classification,
                   (SELECT ib.barcode_value FROM inventory_barcodes ib WHERE ib.inventory_item_id = ii.id AND ib.barcode_status = 'ACTIVE' LIMIT 1) AS barcode_value
            FROM inventory_items ii
            JOIN karats k ON k.id = ii.karat_id
            WHERE ii.inventory_status = 'IN_STOCK' AND ii.id IN ({marks})
            ORDER BY ii.id
            FOR UPDATE OF ii
            """, tuple(int(i) for i in ids))

    def movement_type_id(self, code):
        row = self._fetch_one("SELECT id FROM inventory_movement_types WHERE code = %s AND is_active = TRUE", (code,))
        return None if row is None else int(row["id"])

    def _execute(self, query, params=()):
        cursor = self.connection.cursor()
        try:
            cursor.execute(query, params)
            return cursor.lastrowid, cursor.rowcount
        finally:
            cursor.close()

    def insert_adjustment(self, *, placeholder_code, item_type_id, karat_id, expected, found, written_off, reason_code, note, summary, request_key, request_hash, user_id, confirmed_at):
        new_id, _ = self._execute(
            "INSERT INTO stocktake_adjustments (code, item_type_id, karat_id, expected_count, expected_grams, found_count, found_grams, written_off_count, written_off_grams, reason_code, note, "
            "summary_json, request_key, request_hash, confirmed_by_user_id, confirmed_at) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (placeholder_code, item_type_id, karat_id, expected[0], expected[1], found[0], found[1], written_off[0], written_off[1], reason_code, note,
             json.dumps(summary, ensure_ascii=False), request_key, request_hash, user_id, confirmed_at))
        code = f"SK{new_id:06d}"
        self._execute("UPDATE stocktake_adjustments SET code = %s WHERE id = %s", (code, new_id))
        return new_id, code

    def remove_piece(self, item_id, user_id):
        _, count = self._execute("UPDATE inventory_items SET inventory_status = 'REMOVED', updated_by_user_id = %s, updated_at = NOW() WHERE id = %s AND inventory_status = 'IN_STOCK'", (user_id, item_id))
        return count == 1

    def insert_movement(self, *, item_id, movement_type_id, stock_classification, weight, reason, user_id, reference_id):
        new_id, _ = self._execute(
            "INSERT INTO inventory_movements (inventory_item_id, movement_type_id, from_stock_classification, to_stock_classification, from_inventory_status, to_inventory_status, "
            "from_weight_grams, to_weight_grams, reason, reference_type, reference_id, occurred_at, performed_by_user_id, performed_by_employee_id) "
            "VALUES (%s, %s, %s, %s, 'IN_STOCK', 'REMOVED', %s, %s, %s, 'STOCKTAKE_ADJUSTMENT', %s, NOW(), %s, NULL)",
            (item_id, movement_type_id, stock_classification, stock_classification, weight, weight, reason, reference_id, user_id))
        return new_id

    def insert_item(self, *, adjustment_id, item_id, karat_id, weight, barcode, movement_id):
        self._execute("INSERT INTO stocktake_adjustment_items (adjustment_id, inventory_item_id, karat_id, weight_grams, barcode_snapshot, inventory_movement_id) VALUES (%s, %s, %s, %s, %s, %s)",
                      (adjustment_id, item_id, karat_id, weight, barcode, movement_id))

    def audit(self, *, user_id, adjustment_id, code, description, metadata, occurred_at):
        self._execute("INSERT INTO audit_log (user_id, event_type, entity_type, entity_id, entity_reference, description, metadata_json, occurred_at) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                      (user_id, "STOCKTAKE_WRITE_OFF", "STOCKTAKE_ADJUSTMENT", int(adjustment_id), code, description, json.dumps(metadata, ensure_ascii=False, default=str), occurred_at))
