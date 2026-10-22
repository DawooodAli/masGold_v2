"""Persistence of the making COST per gram of each item category (migration 070). One number per category for every karat; it is the CURRENT cost (a piece keeps the cost it was entered with)."""
import json

from repositories.base_repository import BaseRepository


class CategoryCostRepository(BaseRepository):
    def now(self):
        return self._fetch_one("SELECT NOW() AS now")["now"].replace(microsecond=0)

    def categories(self):
        """Every active category with its purchase-making range (a hint when typing the cost) and its current cost (NULL = not set)."""
        return self._fetch_all(
            """
            SELECT c.id, c.code, c.name, c.purchase_making_min_per_gram AS range_min, c.purchase_making_max_per_gram AS range_max, c.is_active, cc.making_cost_per_gram AS cost
            FROM item_categories c LEFT JOIN item_category_costs cc ON cc.category_id = c.id
            WHERE c.is_active = TRUE ORDER BY c.sort_order, c.code
            """)

    def category(self, category_id, lock=False):
        return self._fetch_one("SELECT id, code, name FROM item_categories WHERE id = %s AND is_active = TRUE" + (" FOR UPDATE" if lock else ""), (category_id,))

    def cost(self, category_id):
        row = self._fetch_one("SELECT making_cost_per_gram AS cost FROM item_category_costs WHERE category_id = %s", (category_id,))
        return None if row is None else row["cost"]

    def _execute(self, query, params=()):
        cursor = self.connection.cursor()
        try:
            cursor.execute(query, params)
            return cursor.rowcount
        finally:
            cursor.close()

    def set_cost(self, category_id, amount, user_id):
        self._execute("INSERT INTO item_category_costs (category_id, making_cost_per_gram, updated_by_user_id) VALUES (%s, %s, %s) "
                      "ON DUPLICATE KEY UPDATE making_cost_per_gram = VALUES(making_cost_per_gram), updated_by_user_id = VALUES(updated_by_user_id)", (category_id, amount, user_id))

    def clear_cost(self, category_id):
        self._execute("DELETE FROM item_category_costs WHERE category_id = %s", (category_id,))

    def audit(self, *, user_id, event_type, category_id, code, description, metadata, occurred_at):
        cursor = self.connection.cursor()
        try:
            cursor.execute("INSERT INTO audit_log (user_id, event_type, entity_type, entity_id, entity_reference, description, metadata_json, occurred_at) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                           (user_id, event_type, "ITEM_CATEGORY_COST", int(category_id), code, description, json.dumps(metadata, ensure_ascii=False, default=str), occurred_at))
        finally:
            cursor.close()
