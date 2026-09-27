"""مراجعات المدير: one read model over the review sources.

Unidentified sales live in inventory_reconciliation_cases (003/016);
making/pricing, cash-settlement and old-gold discount reviews live in
management_reviews (016).
"""
import json

from repositories.base_repository import BaseRepository


REVIEW_TYPES = ("INVENTORY_MATCH", "MAKING_PRICE", "CASH_SETTLEMENT", "OLD_GOLD_DISCOUNT")


class ManagementReviewRepository(BaseRepository):
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
                  WHERE review_type = 'OLD_GOLD_DISCOUNT' AND review_status = 'OPEN') AS old_gold_discount
            """)
        return {"INVENTORY_MATCH": int(row["inventory_match"]), "MAKING_PRICE": int(row["making_price"]),
                "CASH_SETTLEMENT": int(row["cash_settlement"]),
                "OLD_GOLD_DISCOUNT": int(row["old_gold_discount"])}

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
