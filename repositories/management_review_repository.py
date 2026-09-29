"""مراجعات المدير: one read model over the review sources.

Unidentified sales live in inventory_reconciliation_cases (003/016);
making/pricing, cash-settlement and old-gold discount reviews live in
management_reviews (016); returned-item inspections too, each linked to
its lifecycle RETURNED line (021).
"""
import json

from repositories.base_repository import BaseRepository
from repositories.invoice_lifecycle_repository import exchange_code


REVIEW_TYPES = ("INVENTORY_MATCH", "MAKING_PRICE", "CASH_SETTLEMENT", "OLD_GOLD_DISCOUNT",
                "RETURNED_ITEM_INSPECTION")
INSPECTION = "RETURNED_ITEM_INSPECTION"
REFERENCE_TYPE = "MANAGEMENT_REVIEW"

# The piece's stored barcode: its ACTIVE barcode, else the latest one it carried (never re-assigned).
_STORED_BARCODE = """(SELECT b.barcode_value FROM inventory_barcodes b WHERE b.inventory_item_id = ii.id
                      ORDER BY b.barcode_status = 'ACTIVE' DESC, b.id DESC LIMIT 1)"""


def _stringify(row):
    if row is None:
        return None
    return {key: (str(value) if key.endswith("_id") and value is not None and key != "karat_id" else value)
            for key, value in row.items()}


class ManagementReviewRepository(BaseRepository):
    def _execute(self, query, params=()):
        cursor = self.connection.cursor()
        try:
            cursor.execute(query, params)
            return cursor.lastrowid, cursor.rowcount
        finally:
            cursor.close()

    def open_counts(self):
        row = self._fetch_one(
            """
            SELECT
                (SELECT COUNT(*) FROM inventory_reconciliation_cases
                  WHERE case_type = 'UNIDENTIFIED_SALE' AND case_status = 'OPEN') AS inventory_match,
                (SELECT COUNT(*) FROM management_reviews
                  WHERE review_type = 'MAKING_PRICE' AND review_status = 'OPEN') AS making_price,
                (SELECT COUNT(*) FROM management_reviews
                  WHERE review_type = 'CASH_SETTLEMENT' AND review_status = 'OPEN') AS cash_settlement,
                (SELECT COUNT(*) FROM management_reviews
                  WHERE review_type = 'OLD_GOLD_DISCOUNT' AND review_status = 'OPEN') AS old_gold_discount,
                (SELECT COUNT(*) FROM management_reviews
                  WHERE review_type = 'RETURNED_ITEM_INSPECTION' AND review_status = 'OPEN') AS inspection
            """)
        return {"INVENTORY_MATCH": int(row["inventory_match"]), "MAKING_PRICE": int(row["making_price"]),
                "CASH_SETTLEMENT": int(row["cash_settlement"]),
                "OLD_GOLD_DISCOUNT": int(row["old_gold_discount"]),
                INSPECTION: int(row["inspection"])}

    # ------------------------------------------------------------ returned-item inspection (021)

    _INSPECTION_SELECT = f"""
        SELECT r.id AS review_id, r.review_status, r.resolution_code, r.resolved_at, r.created_at,
               r.invoice_id, i.invoice_number, r.lifecycle_line_id, l.lifecycle_transaction_id AS lifecycle_id,
               t.occurred_at AS exchanged_at, l.source_invoice_line_id, l.inventory_item_id,
               l.barcode_snapshot, l.item_name_snapshot, l.karat_id, l.karat_value, l.weight_grams,
               ii.inventory_status, ii.weight_grams AS item_weight_grams, ii.size, ii.notes AS item_notes,
               ii.stock_classification, it.name AS item_type_name, f.name AS factory_name,
               cat.name AS category_name, s.customer_name_snapshot,
               cu.display_name AS created_by_name, ru.display_name AS resolved_by_name,
               {_STORED_BARCODE} AS stored_barcode
        FROM management_reviews r
        JOIN invoice_lifecycle_lines l ON l.id = r.lifecycle_line_id
        JOIN invoice_lifecycle_transactions t ON t.id = l.lifecycle_transaction_id
        JOIN inventory_items ii ON ii.id = l.inventory_item_id
        LEFT JOIN invoices i ON i.id = r.invoice_id
        LEFT JOIN item_types it ON it.id = ii.item_type_id
        LEFT JOIN factories f ON f.id = ii.factory_id
        LEFT JOIN item_categories cat ON cat.id = ii.category_id
        LEFT JOIN invoice_customer_snapshots s ON s.invoice_id = r.invoice_id
        LEFT JOIN users cu ON cu.id = r.created_by_user_id
        LEFT JOIN users ru ON ru.id = r.resolved_by_user_id
        WHERE r.review_type = 'RETURNED_ITEM_INSPECTION'
    """

    def _inspection(self, row):
        row = _stringify(row)
        if row is not None:
            row["exchange_code"] = exchange_code(row["lifecycle_id"])
            # Authoritative stored barcode; the exchange line's snapshot only if the piece has no barcode row.
            row["stored_barcode"] = row.get("stored_barcode") or row.get("barcode_snapshot")
        return row

    def open_inspections(self, limit=200):
        return [self._inspection(row) for row in self._fetch_all(
            self._INSPECTION_SELECT + " AND r.review_status = 'OPEN' ORDER BY r.id DESC LIMIT %s", (int(limit),))]

    def lock_inspection(self, review_id):
        """The review and its lifecycle line, locked (FOR UPDATE), then read in full."""
        locked = self._fetch_one(
            """
            SELECT r.id FROM management_reviews r
            JOIN invoice_lifecycle_lines l ON l.id = r.lifecycle_line_id
            WHERE r.id = %s AND r.review_type = 'RETURNED_ITEM_INSPECTION'
            FOR UPDATE
            """, (int(review_id),))
        if locked is None:
            return None
        return self._inspection(self._fetch_one(self._INSPECTION_SELECT + " AND r.id = %s", (int(review_id),)))

    def lock_old_gold_balance(self, karat_id):
        """The same COUNTED-ledger lock Billing and the karat conversion take."""
        self._fetch_all(
            "SELECT id FROM old_gold_movements WHERE karat_id = %s AND weight_delta_grams IS NOT NULL "
            "AND balance_effect = 'COUNTED' FOR UPDATE", (int(karat_id),))

    def db_now(self):
        return self._fetch_one("SELECT NOW() AS now")["now"].replace(microsecond=0)

    def lock_item(self, inventory_item_id):
        return _stringify(self._fetch_one(
            """
            SELECT id AS inventory_item_id, karat_id, weight_grams, stock_classification, inventory_status
            FROM inventory_items WHERE id = %s FOR UPDATE
            """, (int(inventory_item_id),)))

    def set_item_status(self, *, inventory_item_id, from_status, to_status, updated_by_user_id):
        _, rowcount = self._execute(
            "UPDATE inventory_items SET inventory_status = %s, updated_by_user_id = %s "
            "WHERE id = %s AND inventory_status = %s",
            (to_status, updated_by_user_id, int(inventory_item_id), from_status))
        if rowcount != 1:
            raise RuntimeError(f"Inventory item {inventory_item_id} is no longer {from_status}.")

    def add_inventory_movement(self, *, item, movement_code, from_status, to_status, review_id, reference_code,
                               reason, performed_by_user_id, performed_by_employee_id):
        movement_id, _ = self._execute(
            """
            INSERT INTO inventory_movements (
                inventory_item_id, movement_type_id,
                from_stock_classification, to_stock_classification,
                from_inventory_status, to_inventory_status,
                from_weight_grams, to_weight_grams,
                reference_type, reference_id, reference_code, reason,
                performed_by_user_id, performed_by_employee_id
            )
            SELECT %s, t.id, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
            FROM inventory_movement_types t WHERE t.code = %s AND t.is_active = TRUE
            """,
            (int(item["inventory_item_id"]), item["stock_classification"], item["stock_classification"],
             from_status, to_status, item["weight_grams"], item["weight_grams"], REFERENCE_TYPE, int(review_id),
             reference_code, reason, performed_by_user_id, performed_by_employee_id, movement_code))
        if not movement_id:
            raise RuntimeError(f"inventory movement type {movement_code} is missing")
        return str(movement_id)

    def add_old_gold_in(self, *, karat_id, weight_grams, inventory_item_id, review_id, reference_code, notes,
                        customer_id, created_by_user_id, employee_id):
        """COUNTED RETURNED_ITEM_TO_OLD_GOLD (positive weight): the piece's exact karat and weight."""
        movement_id, _ = self._execute(
            """
            INSERT INTO old_gold_movements (
                movement_type_id, karat_id, weight_delta_grams, balance_effect, related_inventory_item_id,
                reference_type, reference_id, reference_code, notes,
                created_by_user_id, performed_by_user_id, received_by_employee_id, movement_code, customer_id
            )
            SELECT t.id, %s, %s, 'COUNTED', %s, %s, %s, %s, %s, %s, %s, %s, 'RETURNED_ITEM_TO_OLD_GOLD', %s
            FROM old_gold_movement_types t WHERE t.code = 'RETURNED_ITEM_TO_OLD_GOLD' AND t.is_active = TRUE
            """,
            (int(karat_id), abs(weight_grams), int(inventory_item_id), REFERENCE_TYPE, int(review_id),
             reference_code, notes, created_by_user_id, created_by_user_id, employee_id,
             None if customer_id in (None, "") else int(customer_id)))
        if not movement_id:
            raise RuntimeError("old-gold movement type RETURNED_ITEM_TO_OLD_GOLD is missing")
        return str(movement_id)

    def resolve_inspection(self, *, review_id, resolution_code, resolved_by_user_id, resolved_at, notes):
        _, rowcount = self._execute(
            """
            UPDATE management_reviews
            SET review_status = 'RESOLVED', resolution_code = %s, resolved_by_user_id = %s, resolved_at = %s,
                resolution_notes = %s
            WHERE id = %s AND review_type = 'RETURNED_ITEM_INSPECTION' AND review_status = 'OPEN'
            """, (resolution_code, resolved_by_user_id, resolved_at, notes, int(review_id)))
        return rowcount == 1

    def returned_lines_without_inspection(self):
        """Backfill: RETURNED exchange lines whose piece still waits (RETURNED_PENDING) without a review."""
        return [_stringify(row) for row in self._fetch_all(
            f"""
            SELECT l.id AS lifecycle_line_id, l.lifecycle_transaction_id AS lifecycle_id,
                   t.source_invoice_id AS invoice_id, l.source_invoice_line_id, l.inventory_item_id,
                   l.karat_value, l.weight_grams, l.item_name_snapshot, l.barcode_snapshot,
                   {_STORED_BARCODE} AS stored_barcode
            FROM invoice_lifecycle_lines l
            JOIN invoice_lifecycle_transactions t ON t.id = l.lifecycle_transaction_id
            JOIN inventory_items ii ON ii.id = l.inventory_item_id
            LEFT JOIN management_reviews r ON r.lifecycle_line_id = l.id
            WHERE l.line_role = 'RETURNED' AND ii.inventory_status = 'RETURNED_PENDING' AND r.id IS NULL
            ORDER BY l.id
            FOR UPDATE OF l
            """)]

    def open_inventory_cases(self, limit=200):
        return self._fetch_all(
            """
            SELECT c.id AS case_id, c.invoice_id, i.invoice_number, c.sold_at, c.reason_code, c.reason, c.notes,
                   c.observed_weight_grams, c.observed_size, k.karat_value, it.name AS item_type_name,
                   f.name AS factory_name, e.name AS salesperson_name, u.display_name AS created_by_name,
                   s.customer_name_snapshot
            FROM inventory_reconciliation_cases c
            LEFT JOIN invoices i ON i.id = c.invoice_id
            LEFT JOIN karats k ON k.id = c.observed_karat_id
            LEFT JOIN item_types it ON it.id = c.observed_item_type_id
            LEFT JOIN factories f ON f.id = c.observed_factory_id
            LEFT JOIN employees e ON e.id = c.salesperson_employee_id
            LEFT JOIN users u ON u.id = c.created_by_user_id
            LEFT JOIN invoice_customer_snapshots s ON s.invoice_id = c.invoice_id
            WHERE c.case_type = 'UNIDENTIFIED_SALE' AND c.case_status = 'OPEN'
            ORDER BY c.id DESC
            LIMIT %s
            """, (int(limit),))

    def open_reviews(self, review_type, limit=200):
        rows = self._fetch_all(
            """
            SELECT r.id AS review_id, r.review_type, r.invoice_id, i.invoice_number, r.summary, r.details_json,
                   r.created_at, u.display_name AS created_by_name
            FROM management_reviews r
            LEFT JOIN invoices i ON i.id = r.invoice_id
            LEFT JOIN users u ON u.id = r.created_by_user_id
            WHERE r.review_type = %s AND r.review_status = 'OPEN'
            ORDER BY r.id DESC
            LIMIT %s
            """, (review_type, int(limit)))
        for row in rows:
            if isinstance(row.get("details_json"), str):
                row["details_json"] = json.loads(row["details_json"])
        return rows
