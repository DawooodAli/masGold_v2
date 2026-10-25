"""Repository for the system_settings table (migration 081). A setting is a keyed value with a
type and an optional description; it is inserted or updated in place and its history is the
audit trail, never a second table."""
from decimal import Decimal
import json
from typing import Any, Optional

from repositories.base_repository import BaseRepository


class SystemSettingsRepository(BaseRepository):

    def now(self):
        return self._fetch_one("SELECT NOW() AS now")["now"].replace(microsecond=0)

    def get_all(self) -> list[dict[str, Any]]:
        return self._fetch_all("""
            SELECT setting_key, setting_value, setting_type, description, is_public,
                   created_at, updated_at, updated_by_user_id
            FROM system_settings
            ORDER BY setting_key
        """)

    def get_public(self) -> list[dict[str, Any]]:
        return self._fetch_all("""
            SELECT setting_key, setting_value, setting_type, description, is_public
            FROM system_settings
            WHERE is_public = 1
            ORDER BY setting_key
        """)

    def get_by_key(self, key: str) -> Optional[dict[str, Any]]:
        return self._fetch_one("""
            SELECT setting_key, setting_value, setting_type, description, is_public,
                   created_at, updated_at, updated_by_user_id
            FROM system_settings
            WHERE setting_key = %s
        """, (key,))

    def get_value(self, key: str, default: Any = None) -> Any:
        row = self._fetch_one("SELECT setting_value, setting_type FROM system_settings WHERE setting_key = %s", (key,))
        if not row:
            return default
        return self._cast_value(row["setting_value"], row["setting_type"])

    def get_public_dict(self) -> dict[str, Any]:
        """Return all public settings as a dict for QML."""
        rows = self.get_public()
        return {r["setting_key"]: self._cast_value(r["setting_value"], r["setting_type"]) for r in rows}

    def set_value(self, key: str, value: Any, setting_type: str, description: str = None,
                  is_public: int = 0, user_id: int = None) -> None:
        """Insert a setting or update the existing one in place."""
        self._execute("""
            INSERT INTO system_settings (setting_key, setting_value, setting_type, description, is_public, updated_by_user_id)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
                setting_value = VALUES(setting_value),
                setting_type = VALUES(setting_type),
                description = COALESCE(VALUES(description), description),
                is_public = VALUES(is_public),
                updated_at = CURRENT_TIMESTAMP,
                updated_by_user_id = VALUES(updated_by_user_id)
        """, (key, self._as_text(value, setting_type), setting_type, description, is_public, user_id))

    def audit(self, *, user_id, event_type, entity_type, entity_id, entity_reference, description, metadata, occurred_at):
        """Settings changes are recorded in the shared audit trail (insert-only, never rewritten)."""
        self._execute("""
            INSERT INTO audit_log (user_id, event_type, entity_type, entity_id, entity_reference, description, metadata_json, occurred_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        """, (user_id, event_type, entity_type, entity_id, entity_reference, description,
              json.dumps(metadata, ensure_ascii=False, default=str), occurred_at))

    def _execute(self, query, params=()):
        cursor = self.connection.cursor()
        try:
            cursor.execute(query, params)
            return cursor.rowcount
        finally:
            cursor.close()

    @staticmethod
    def _as_text(value, setting_type):
        """The stored form of a value is always text; the type decides how it is read back."""
        if setting_type == "JSON":
            return json.dumps(value, ensure_ascii=False, default=str)
        if setting_type == "DECIMAL":
            return str(Decimal(str(value)))
        if setting_type == "BOOL":
            return "1" if value in (True, 1, "1", "true", "TRUE", "yes", "on") else "0"
        return str(value)

    def _cast_value(self, value: str, setting_type: str) -> Any:
        if value is None:
            return None
        if setting_type == 'INT':
            return int(value)
        elif setting_type == 'DECIMAL':
            return Decimal(value)
        elif setting_type == 'BOOL':
            return value.lower() in ('1', 'true', 'yes', 'on')
        elif setting_type == 'JSON':
            return json.loads(value)
        return value  # STRING