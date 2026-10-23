"""تعديل رصيد الكسر (persistence only): the counted old-gold ledger of one karat, its held grams and the manual ADJUSTMENT_IN / ADJUSTMENT_OUT movements. Every write runs on the caller's
transaction; nothing here commits, and no existing movement is ever updated or deleted."""
import json
from decimal import Decimal

from repositories.base_repository import BaseRepository

REFERENCE_TYPE = "MANUAL_ADJUSTMENT"


class OldGoldAdjustmentRepository(BaseRepository):

    def _execute(self, query, params=()):
        cursor = self.connection.cursor()
        try:
            cursor.execute(query, params)
            return cursor.lastrowid, cursor.rowcount
        finally:
            cursor.close()

    def karat(self, karat_id):
        return self._fetch_one("SELECT id, karat_value, name, is_active FROM karats WHERE id = %s", (int(karat_id),))

    def balances(self):
        """Physical, held and available grams of every active karat (COUNTED movements only)."""
        return self._fetch_all(
            """
            SELECT k.id AS karat_id, k.karat_value, k.name AS karat_name,
                   COALESCE(SUM(og.weight_delta_grams), 0) AS physical_grams,
                   (SELECT COALESCE(SUM(h.weight_grams), 0) FROM old_gold_holds h WHERE h.karat_id = k.id AND h.hold_status = 'HELD') AS held_grams
            FROM karats k
            LEFT JOIN old_gold_movements og ON og.karat_id = k.id AND og.weight_delta_grams IS NOT NULL AND og.balance_effect = 'COUNTED'
            WHERE k.is_active = TRUE
            GROUP BY k.id, k.karat_value, k.name, k.sort_order
            ORDER BY k.sort_order, k.karat_value
            """)

    def lock_ledger(self, karat_id):
        """Locks the karat's counted ledger and held rows (the lock every Scrap consumer takes) and returns (physical, held) grams."""
        rows = self._fetch_all("SELECT weight_delta_grams FROM old_gold_movements WHERE karat_id = %s AND weight_delta_grams IS NOT NULL AND balance_effect = 'COUNTED' FOR UPDATE",
                               (int(karat_id),))
        held = self._fetch_all("SELECT weight_grams FROM old_gold_holds WHERE karat_id = %s AND hold_status = 'HELD' FOR UPDATE", (int(karat_id),))
        return (sum((Decimal(str(r["weight_delta_grams"])) for r in rows), Decimal("0.000")),
                sum((Decimal(str(r["weight_grams"])) for r in held), Decimal("0.000")))

    def by_group(self, group):
        return self._fetch_one("SELECT id, karat_id, weight_delta_grams, performed_by_user_id FROM old_gold_movements WHERE movement_group_id = %s AND reference_type = %s",
                               (group, REFERENCE_TYPE))

    def insert_adjustment(self, *, direction, karat_id, delta, reason, group, user_id):
        code = "ADJUSTMENT_IN" if direction == "IN" else "ADJUSTMENT_OUT"
        movement_id, count = self._execute(
            "INSERT INTO old_gold_movements (movement_type_id, karat_id, weight_delta_grams, balance_effect, reference_type, reference_code, notes, movement_group_id, "
            "created_by_user_id, performed_by_user_id, movement_code) "
            "SELECT t.id, %s, %s, 'COUNTED', %s, 'تعديل رصيد', %s, %s, %s, %s, %s FROM old_gold_movement_types t WHERE t.code = %s AND t.is_active = TRUE",
            (int(karat_id), delta, REFERENCE_TYPE, reason, group, user_id, user_id, code, code))
        if count != 1:
            raise RuntimeError(f"old-gold movement type {code} is missing")
        return str(movement_id)

    def audit(self, *, user_id, movement_id, description, metadata):
        self._execute(
            "INSERT INTO audit_log (user_id, event_type, entity_type, entity_id, entity_reference, description, metadata_json, occurred_at) "
            "VALUES (%s, 'OLD_GOLD_ADJUSTED', 'OLD_GOLD_MOVEMENT', %s, %s, %s, %s, NOW())",
            (user_id, int(movement_id), f"OG{int(movement_id)}", description, json.dumps(metadata, ensure_ascii=False, default=str)))
