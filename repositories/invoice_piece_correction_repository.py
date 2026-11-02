"""Persistence of the barcode correction of a sold piece: the invoice line, the two pieces and their stock movements. Nothing is deleted and no migration is needed."""
import json

from repositories.base_repository import BaseRepository


class InvoicePieceCorrectionRepository(BaseRepository):
    def now(self):
        return self._fetch_one("SELECT NOW() AS now")["now"].replace(microsecond=0)

    def invoice_by_number(self, number, lock=False):
        return self._fetch_one(
            "SELECT id, invoice_number, invoice_status FROM invoices WHERE invoice_number = %s" + (" FOR UPDATE" if lock else ""), (number,))

    def line(self, invoice_id, line_id, lock=False):
        return self._fetch_one(
            "SELECT id, line_number, line_type, inventory_item_id, karat_id, weight_grams, barcode_snapshot FROM invoice_lines WHERE invoice_id = %s AND id = %s" + (" FOR UPDATE" if lock else ""),
            (int(invoice_id), int(line_id)))

    def is_reversed(self, invoice_id):
        row = self._fetch_one("SELECT COUNT(*) AS n FROM transaction_corrections WHERE source_type = 'INVOICE' AND source_id = %s AND correction_type = 'FULL_REVERSAL'", (int(invoice_id),))
        return int(row["n"]) > 0

    def lifecycle_count(self, invoice_id):
        """Exchanges and Returns of this invoice (any state): a piece that was exchanged or returned is corrected through them, never here."""
        row = self._fetch_one("SELECT (SELECT COUNT(*) FROM invoice_lifecycle_transactions WHERE source_invoice_id = %s) + (SELECT COUNT(*) FROM invoice_returns WHERE source_invoice_id = %s) AS n",
                              (int(invoice_id), int(invoice_id)))
        return int(row["n"])

    def find_item_id_by_barcode(self, barcode):
        row = self._fetch_one("SELECT inventory_item_id FROM inventory_barcodes WHERE barcode_value = %s AND barcode_status = 'ACTIVE'", (str(barcode),))
        return None if row is None else int(row["inventory_item_id"])

    def items(self, ids):
        """Both pieces locked in ascending id order, with the descriptive values the line snapshots carry."""
        ids = sorted({int(i) for i in ids})
        marks = ", ".join(["%s"] * len(ids))
        return self._fetch_all(
            f"""SELECT ii.id, ii.karat_id, ii.weight_grams, ii.size, ii.stock_classification, ii.inventory_status, it.name AS item_type_name, f.name AS factory_name,
                       cat.code AS category_code, ib.barcode_value
                FROM inventory_items ii JOIN item_types it ON it.id = ii.item_type_id LEFT JOIN factories f ON f.id = ii.factory_id
                LEFT JOIN item_categories cat ON cat.id = ii.category_id
                LEFT JOIN inventory_barcodes ib ON ib.inventory_item_id = ii.id AND ib.barcode_status = 'ACTIVE'
                WHERE ii.id IN ({marks}) ORDER BY ii.id FOR UPDATE OF ii""", tuple(ids))

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

    def set_status(self, item_id, from_status, to_status, user_id):
        _, count = self._execute("UPDATE inventory_items SET inventory_status = %s, updated_by_user_id = %s, updated_at = NOW() WHERE id = %s AND inventory_status = %s",
                                 (to_status, user_id, int(item_id), from_status))
        return count == 1

    def insert_movement(self, *, item, movement_type_id, from_status, to_status, reason, user_id, invoice_id, invoice_number):
        self._execute(
            "INSERT INTO inventory_movements (inventory_item_id, movement_type_id, from_stock_classification, to_stock_classification, from_inventory_status, to_inventory_status, "
            "from_weight_grams, to_weight_grams, reference_type, reference_id, reference_code, reason, occurred_at, performed_by_user_id, performed_by_employee_id) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'INVOICE_PIECE_CORRECTION', %s, %s, %s, NOW(), %s, NULL)",
            (int(item["id"]), movement_type_id, item["stock_classification"], item["stock_classification"], from_status, to_status, item["weight_grams"], item["weight_grams"],
             int(invoice_id), str(invoice_number), reason, user_id))

    def repoint_line(self, line_id, item):
        """The line now names the piece that really went out; its price, making and total are untouched."""
        _, count = self._execute(
            "UPDATE invoice_lines SET inventory_item_id = %s, item_name_snapshot = %s, factory_snapshot = %s, category_snapshot = %s, size_snapshot = %s, barcode_snapshot = %s WHERE id = %s",
            (int(item["id"]), item["item_type_name"], item["factory_name"], item["category_code"], item["size"], item["barcode_value"], int(line_id)))
        return count == 1

    def audit(self, *, user_id, invoice_id, number, description, metadata, occurred_at):
        self._execute("INSERT INTO audit_log (user_id, event_type, entity_type, entity_id, entity_reference, description, metadata_json, occurred_at) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                      (user_id, "INVOICE_PIECE_CORRECTED", "INVOICE", int(invoice_id), str(number), description, json.dumps(metadata, ensure_ascii=False, default=str), occurred_at))
