"""Read only list of the most recent audit_log rows (who did what, when) for the iPad activity card."""
from repositories.base_repository import BaseRepository


class AuditViewRepository(BaseRepository):
    def recent(self, text, limit):
        like = f"%{text}%"
        return self._fetch_all(
            """
            SELECT a.id, a.occurred_at, a.event_type, a.entity_type, a.entity_reference, a.description, u.display_name AS user_name
            FROM audit_log a
            LEFT JOIN users u ON u.id = a.user_id
            WHERE (%s = '' OR a.event_type LIKE %s OR a.entity_reference LIKE %s OR a.description LIKE %s OR u.display_name LIKE %s)
            ORDER BY a.id DESC
            LIMIT %s
            """, (text, like, like, like, like, int(limit)))
