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

    def scope_pieces(self, item_type_id, karat_id, factory_id=None):
        """Every piece IN_STOCK of the scope, LOCKED (the same predicate as the stocktake source)."""
        where, params = "ii.inventory_status = 'IN_STOCK'", []
        if item_type_id is not None:
            where += " AND ii.item_type_id = %s"
            params.append(item_type_id)
        if karat_id is not None:
            where += " AND ii.karat_id = %s"
            params.append(karat_id)
        if factory_id is not None:
            where += " AND ii.factory_id = %s"
            params.append(factory_id)
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

    # ---- a piece written off by a stocktake that turns up again
    def lock_removed_piece(self, item_id):
        """The piece, locked, with how many STOCKTAKE_WRITE_OFF movements it has (a restore is only for a piece an approved stocktake removed)."""
        return self._fetch_one(
            "SELECT ii.id, ii.inventory_status, ii.stock_classification, ii.weight_grams, "
            "(SELECT COUNT(*) FROM inventory_movements mv JOIN inventory_movement_types t ON t.id = mv.movement_type_id WHERE mv.inventory_item_id = ii.id AND t.code = 'STOCKTAKE_WRITE_OFF') AS write_offs, "
            "(SELECT b.barcode_value FROM inventory_barcodes b WHERE b.inventory_item_id = ii.id AND b.barcode_status = 'ACTIVE' LIMIT 1) AS barcode_value "
            "FROM inventory_items ii WHERE ii.id = %s FOR UPDATE", (int(item_id),))

    def restore_piece(self, item_id, user_id, from_status="REMOVED"):
        _, count = self._execute("UPDATE inventory_items SET inventory_status = 'IN_STOCK', updated_by_user_id = %s, updated_at = NOW() WHERE id = %s AND inventory_status = %s",
                                 (user_id, int(item_id), from_status))
        return count == 1

    def insert_restore_movement(self, *, item, movement_type_id, reason, user_id, from_status="REMOVED", reference_type="STOCKTAKE_RESTORE"):
        new_id, _ = self._execute(
            "INSERT INTO inventory_movements (inventory_item_id, movement_type_id, from_stock_classification, to_stock_classification, from_inventory_status, to_inventory_status, "
            "from_weight_grams, to_weight_grams, reason, reference_type, reference_id, occurred_at, performed_by_user_id, performed_by_employee_id) "
            "VALUES (%s, %s, %s, %s, %s, 'IN_STOCK', %s, %s, %s, %s, %s, NOW(), %s, NULL)",
            (int(item["id"]), movement_type_id, item["stock_classification"], item["stock_classification"], from_status, item["weight_grams"], item["weight_grams"], reason, reference_type,
             int(item["id"]), user_id))
        return new_id

    def scrap_karats(self):
        """Every active karat with its COUNTED physical and held Scrap grams (the choices of the restock dialog)."""
        return self._fetch_all(
            """
            SELECT k.id AS karat_id, k.karat_value, k.name AS karat_name, COALESCE(SUM(og.weight_delta_grams), 0) AS physical_grams,
                   (SELECT COALESCE(SUM(h.weight_grams), 0) FROM old_gold_holds h WHERE h.karat_id = k.id AND h.hold_status = 'HELD') AS held_grams
            FROM karats k LEFT JOIN old_gold_movements og ON og.karat_id = k.id AND og.weight_delta_grams IS NOT NULL AND og.balance_effect = 'COUNTED'
            WHERE k.is_active = TRUE GROUP BY k.id, k.karat_value, k.name, k.sort_order ORDER BY k.sort_order, k.karat_value
            """)

    def karat(self, karat_id):
        return self._fetch_one("SELECT id, karat_value, name, is_active FROM karats WHERE id = %s", (int(karat_id),))

    def lock_scrap_ledger(self, karat_id):
        """Locks the karat's COUNTED Scrap ledger and held rows (the lock every Scrap consumer takes) and returns (physical, held) grams."""
        rows = self._fetch_all("SELECT weight_delta_grams FROM old_gold_movements WHERE karat_id = %s AND weight_delta_grams IS NOT NULL AND balance_effect = 'COUNTED' FOR UPDATE", (int(karat_id),))
        held = self._fetch_all("SELECT weight_grams FROM old_gold_holds WHERE karat_id = %s AND hold_status = 'HELD' FOR UPDATE", (int(karat_id),))
        from decimal import Decimal
        return (sum((Decimal(str(r["weight_delta_grams"])) for r in rows), Decimal("0.000")), sum((Decimal(str(r["weight_grams"])) for r in held), Decimal("0.000")))

    def insert_scrap_out_for_restock(self, *, item, karat_id, notes, user_id):
        """COUNTED MANUFACTURE_OUT (negative): the grams of a piece bought back as Scrap leave the Scrap when the SAME piece goes back into the stock."""
        new_id, count = self._execute(
            "INSERT INTO old_gold_movements (movement_type_id, karat_id, weight_delta_grams, balance_effect, related_inventory_item_id, reference_type, reference_id, reference_code, notes, "
            "created_by_user_id, performed_by_user_id, received_by_employee_id, movement_code) "
            "SELECT t.id, %s, %s, 'COUNTED', %s, 'INVENTORY_ITEM', %s, %s, %s, %s, %s, NULL, 'MANUFACTURE_OUT' FROM old_gold_movement_types t WHERE t.code = 'MANUFACTURE_OUT' AND t.is_active = TRUE",
            (int(karat_id), -item["weight_grams"], int(item["id"]), int(item["id"]), f"#{int(item['id'])}", notes, user_id, user_id))
        if count != 1:
            raise RuntimeError("old-gold movement type MANUFACTURE_OUT is missing")
        return int(new_id)

    def current_sale_invoices(self, item_id):
        """The numbers of the CURRENT (not old-program, not fully reversed) finalized invoices that sold this piece. A piece sold on one of them comes back through Return / Exchange, never here."""
        rows = self._fetch_all(
            "SELECT DISTINCT i.invoice_number FROM invoice_lines l JOIN invoices i ON i.id = l.invoice_id "
            "LEFT JOIN transaction_corrections c ON c.source_type = 'INVOICE' AND c.source_id = i.id AND c.correction_type = 'FULL_REVERSAL' "
            "WHERE l.inventory_item_id = %s AND l.line_type = 'SELL_INVENTORY' AND i.invoice_status = 'FINALIZED' AND i.record_origin <> 'LEGACY' AND c.id IS NULL "
            "ORDER BY i.invoice_number", (int(item_id),))
        return [r["invoice_number"] for r in rows]

    def audit_restore(self, *, user_id, item_id, description, metadata):
        self._execute("INSERT INTO audit_log (user_id, event_type, entity_type, entity_id, entity_reference, description, metadata_json, occurred_at) VALUES (%s, 'STOCKTAKE_PIECE_RESTORED', 'INVENTORY_ITEM', %s, %s, %s, %s, NOW())",
                      (user_id, int(item_id), f"#{int(item_id)}", description, json.dumps(metadata, ensure_ascii=False, default=str)))

    def insert_item(self, *, adjustment_id, item_id, karat_id, weight, barcode, movement_id):
        self._execute("INSERT INTO stocktake_adjustment_items (adjustment_id, inventory_item_id, karat_id, weight_grams, barcode_snapshot, inventory_movement_id) VALUES (%s, %s, %s, %s, %s, %s)",
                      (adjustment_id, item_id, karat_id, weight, barcode, movement_id))

    def audit(self, *, user_id, adjustment_id, code, description, metadata, occurred_at):
        self._execute("INSERT INTO audit_log (user_id, event_type, entity_type, entity_id, entity_reference, description, metadata_json, occurred_at) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                      (user_id, "STOCKTAKE_WRITE_OFF", "STOCKTAKE_ADJUSTMENT", int(adjustment_id), code, description, json.dumps(metadata, ensure_ascii=False, default=str), occurred_at))
