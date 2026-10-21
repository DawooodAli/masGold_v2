"""Persistence of the position snapshots (migration 067) and the two plain reads they need (net supplier gold, cash / bank balances). Insert-only: nothing here edits or deletes a snapshot."""
import json

from repositories.base_repository import BaseRepository

COLUMNS = ("snapshot_kind", "taken_at", "gold_shop_eq21", "gold_custody_eq21", "gold_intermediary_eq21", "gold_book_eq21", "gold_suppliers_eq21", "gold_total_eq21",
           "unknown_grams", "cash_egp", "bank_egp", "created_by_user_id")


class PositionSnapshotRepository(BaseRepository):
    def now(self):
        return self._fetch_one("SELECT NOW() AS now")["now"].replace(microsecond=0)

    def opening(self):
        return self._fetch_one("SELECT * FROM position_snapshots WHERE snapshot_kind = 'OPENING' ORDER BY id LIMIT 1")

    def latest_on_date(self, day):
        """The newest non-opening snapshot taken on this calendar day, or None."""
        return self._fetch_one("SELECT * FROM position_snapshots WHERE snapshot_kind <> 'OPENING' AND DATE(taken_at) = %s ORDER BY taken_at DESC, id DESC LIMIT 1", (day,))

    def supplier_gold(self):
        """The supplier gold ledger summed per supplier karat (positive = the shop owes the supplier)."""
        return self._fetch_all("SELECT k.karat_value, COALESCE(SUM(m.weight_delta_grams), 0) AS grams FROM supplier_gold_movements m JOIN karats k ON k.id = m.karat_id GROUP BY k.karat_value")

    def cash_and_bank(self):
        cash = self._fetch_one("SELECT COALESCE(SUM(amount_delta), 0) AS total FROM cash_movements")["total"]
        bank = self._fetch_one("SELECT COALESCE(SUM(amount_delta), 0) AS total FROM bank_movements")["total"]
        return cash, bank

    def insert(self, values):
        cursor = self.connection.cursor()
        try:
            cursor.execute(f"INSERT INTO position_snapshots ({', '.join(COLUMNS)}) VALUES ({', '.join(['%s'] * len(COLUMNS))})", tuple(values[c] for c in COLUMNS))
            return cursor.lastrowid
        finally:
            cursor.close()

    def audit(self, *, user_id, event_type, entity_id, description, metadata, occurred_at):
        cursor = self.connection.cursor()
        try:
            cursor.execute("INSERT INTO audit_log (user_id, event_type, entity_type, entity_id, entity_reference, description, metadata_json, occurred_at) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                           (user_id, event_type, "POSITION_SNAPSHOT", int(entity_id), str(entity_id), description, json.dumps(metadata, ensure_ascii=False, default=str), occurred_at))
        finally:
            cursor.close()
