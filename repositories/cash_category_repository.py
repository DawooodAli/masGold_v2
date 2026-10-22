"""Persistence of the manageable income / expense categories (migration 069). A category is a name and a direction; the manual cash movements reference it by `code`. Deleting a category that
any financial operation uses is refused by the foreign key; the service checks first and tells the owner to disable it instead."""
import json

from repositories.base_repository import BaseRepository

MISSING_TABLE = 1146


class CashCategoryRepository(BaseRepository):
    def now(self):
        return self._fetch_one("SELECT NOW() AS now")["now"].replace(microsecond=0)

    def categories(self):
        """Every category (active or not, system or the owner's), in display order."""
        return self._fetch_all("SELECT id, code, name, direction, is_active, is_system, sort_order FROM cash_categories ORDER BY sort_order, name, id")

    def category(self, code, lock=False):
        return self._fetch_one("SELECT id, code, name, direction, is_active, is_system FROM cash_categories WHERE code = %s" + (" FOR UPDATE" if lock else ""), (code,))

    def name_taken(self, name, except_code=None):
        row = self._fetch_one("SELECT code FROM cash_categories WHERE name = %s AND code <> %s", (name, except_code or ""))
        return row is not None

    def next_sort_order(self):
        return int(self._fetch_one("SELECT COALESCE(MAX(sort_order), 0) + 10 AS next FROM cash_categories")["next"])

    def usage(self, code):
        """How many financial operations used this category (any state, reversed ones included)."""
        return int(self._fetch_one("SELECT COUNT(*) AS n FROM financial_operations WHERE category = %s", (code,))["n"])

    def _execute(self, query, params=()):
        cursor = self.connection.cursor()
        try:
            cursor.execute(query, params)
            return cursor.rowcount
        finally:
            cursor.close()

    def insert(self, *, code, name, direction, sort_order, user_id):
        self._execute("INSERT INTO cash_categories (code, name, direction, is_active, is_system, sort_order, created_by_user_id) VALUES (%s, %s, %s, 1, 0, %s, %s)",
                      (code, name, direction, sort_order, user_id))

    def rename(self, code, name):
        self._execute("UPDATE cash_categories SET name = %s WHERE code = %s", (name, code))

    def set_active(self, code, active):
        self._execute("UPDATE cash_categories SET is_active = %s WHERE code = %s", (1 if active else 0, code))

    def delete(self, code):
        return self._execute("DELETE FROM cash_categories WHERE code = %s", (code,))

    def audit(self, *, user_id, event_type, entity_id, code, description, metadata, occurred_at):
        cursor = self.connection.cursor()
        try:
            cursor.execute("INSERT INTO audit_log (user_id, event_type, entity_type, entity_id, entity_reference, description, metadata_json, occurred_at) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                           (user_id, event_type, "CASH_CATEGORY", int(entity_id), code, description, json.dumps(metadata, ensure_ascii=False, default=str), occurred_at))
        finally:
            cursor.close()
