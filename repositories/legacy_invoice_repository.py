"""Persistence of the cancellation of an OLD (imported) invoice: the invoice, its sold pieces and the movement that puts each piece back in stock. Nothing is deleted."""
import json

from repositories.base_repository import BaseRepository


class LegacyInvoiceRepository(BaseRepository):
    def now(self):
        return self._fetch_one("SELECT NOW() AS now")["now"].replace(microsecond=0)

    def invoice_by_number(self, number, lock=False):
        return self._fetch_one(
            "SELECT i.id, i.invoice_number, i.invoice_status, i.record_origin, i.invoice_date, i.net_amount, i.net_direction, i.customer_id, s.customer_name_snapshot "
            "FROM invoices i LEFT JOIN invoice_customer_snapshots s ON s.invoice_id = i.id WHERE i.invoice_number = %s" + (" FOR UPDATE OF i" if lock else ""), (number,))

    def lines(self, invoice_id):
        return self._fetch_all(
            "SELECT l.id, l.line_number, l.line_type, l.inventory_item_id, l.barcode_snapshot, l.item_name_snapshot, l.weight_grams, k.karat_value "
            "FROM invoice_lines l JOIN karats k ON k.id = l.karat_id WHERE l.invoice_id = %s ORDER BY l.line_number", (invoice_id,))

    def items(self, ids, lock=False):
        if not ids:
            return []
        marks = ", ".join(["%s"] * len(ids))
        return self._fetch_all(f"SELECT id, inventory_status, stock_classification, weight_grams FROM inventory_items WHERE id IN ({marks}) ORDER BY id" + (" FOR UPDATE" if lock else ""), tuple(int(i) for i in ids))

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

    def restock_piece(self, item_id, user_id):
        _, count = self._execute("UPDATE inventory_items SET inventory_status = 'IN_STOCK', updated_by_user_id = %s, updated_at = NOW() WHERE id = %s AND inventory_status = 'SOLD'", (user_id, item_id))
        return count == 1

    def insert_movement(self, *, item_id, movement_type_id, stock_classification, weight, reason, user_id, reference_id):
        new_id, _ = self._execute(
            "INSERT INTO inventory_movements (inventory_item_id, movement_type_id, from_stock_classification, to_stock_classification, from_inventory_status, to_inventory_status, "
            "from_weight_grams, to_weight_grams, reason, reference_type, reference_id, occurred_at, performed_by_user_id, performed_by_employee_id) "
            "VALUES (%s, %s, %s, %s, 'SOLD', 'IN_STOCK', %s, %s, %s, 'LEGACY_INVOICE_VOID', %s, NOW(), %s, NULL)",
            (item_id, movement_type_id, stock_classification, stock_classification, weight, weight, reason, reference_id, user_id))
        return new_id

    def void_invoice(self, invoice_id, user_id, reason):
        _, count = self._execute("UPDATE invoices SET invoice_status = 'VOIDED', voided_at = NOW(), voided_by_user_id = %s, void_reason = %s WHERE id = %s AND invoice_status = 'FINALIZED' AND record_origin = 'LEGACY'",
                                 (user_id, reason, invoice_id))
        return count == 1

    def audit(self, *, user_id, invoice_id, number, description, metadata, occurred_at):
        self._execute("INSERT INTO audit_log (user_id, event_type, entity_type, entity_id, entity_reference, description, metadata_json, occurred_at) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                      (user_id, "LEGACY_INVOICE_VOIDED", "INVOICE", int(invoice_id), str(number), description, json.dumps(metadata, ensure_ascii=False, default=str), occurred_at))
