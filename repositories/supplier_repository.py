"""Supplier master and supplier ledger reads (034). Balances are always SUM(delta) of the immutable ledgers —
never a stored or floored column. Sign: delta > 0 = the shop owes the supplier (see services/supplier_rules.py)."""
import json

from repositories.base_repository import BaseRepository


def _id(value):
    return int(value)


class SupplierRepository(BaseRepository):

    def _execute(self, query, params=()):
        cursor = self.connection.cursor()
        try:
            cursor.execute(query, params)
            return cursor.lastrowid, cursor.rowcount
        finally:
            cursor.close()

    # ------------------------------------------------------------------ master
    def list_suppliers(self, search=None, active=None, limit=500):
        where, params = [], []
        if search:
            where.append("(s.name LIKE %s OR s.phone LIKE %s)")
            like = f"%{search}%"
            params += [like, like]
        if active is not None:
            where.append("s.is_active = %s")
            params.append(1 if active else 0)
        params.append(int(limit))
        return self._fetch_all(
            f"""
            SELECT s.id, s.name, s.phone, s.notes, s.is_active, s.created_at,
                   COALESCE(g.eq21, 0) AS gold_eq21_balance, COALESCE(m.amount, 0) AS making_balance,
                   COALESCE(i.posted_count, 0) AS posted_invoice_count
            FROM suppliers s
            LEFT JOIN (SELECT supplier_id, SUM(equivalent_21_delta_grams) AS eq21
                       FROM supplier_gold_movements GROUP BY supplier_id) g ON g.supplier_id = s.id
            LEFT JOIN (SELECT supplier_id, SUM(amount_delta) AS amount
                       FROM supplier_making_movements GROUP BY supplier_id) m ON m.supplier_id = s.id
            LEFT JOIN (SELECT supplier_id, COUNT(*) AS posted_count
                       FROM supplier_purchase_invoices WHERE status = 'POSTED' GROUP BY supplier_id) i
                   ON i.supplier_id = s.id
            {'WHERE ' + ' AND '.join(where) if where else ''}
            ORDER BY s.is_active DESC, s.name, s.id
            LIMIT %s
            """, tuple(params))

    def get_supplier(self, supplier_id, lock=False):
        return self._fetch_one("SELECT * FROM suppliers WHERE id = %s" + (" FOR UPDATE" if lock else ""),
                               (_id(supplier_id),))

    def create_supplier(self, *, name, phone, notes, user_id):
        new_id, _ = self._execute(
            "INSERT INTO suppliers (name, phone, notes, created_by_user_id, updated_by_user_id) "
            "VALUES (%s, %s, %s, %s, %s)", (name, phone, notes, user_id, user_id))
        return int(new_id)

    def update_supplier(self, *, supplier_id, name, phone, notes, user_id):
        self._execute("UPDATE suppliers SET name = %s, phone = %s, notes = %s, updated_by_user_id = %s "
                      "WHERE id = %s", (name, phone, notes, user_id, _id(supplier_id)))

    def set_active(self, *, supplier_id, active, user_id):
        self._execute("UPDATE suppliers SET is_active = %s, updated_by_user_id = %s WHERE id = %s",
                      (1 if active else 0, user_id, _id(supplier_id)))

    def get_karat(self, karat_id):
        return self._fetch_one("SELECT id, karat_value, name, is_active FROM karats WHERE id = %s", (int(karat_id),))

    def reference_count(self, supplier_id):
        """Rows that reference the supplier (invoices and both ledgers). A referenced supplier is never deleted."""
        row = self._fetch_one(
            """
            SELECT (SELECT COUNT(*) FROM supplier_purchase_invoices WHERE supplier_id = %s)
                 + (SELECT COUNT(*) FROM supplier_gold_movements WHERE supplier_id = %s)
                 + (SELECT COUNT(*) FROM supplier_making_movements WHERE supplier_id = %s) AS references_count
            """, (_id(supplier_id),) * 3)
        return int(row["references_count"])

    def delete_supplier(self, supplier_id):
        self._execute("DELETE FROM suppliers WHERE id = %s", (_id(supplier_id),))

    # ------------------------------------------------------------------ balances
    def gold_balances(self, supplier_id):
        """Per karat: contractual grams and their 21K equivalent (SUM of the immutable gold ledger)."""
        return self._fetch_all(
            """
            SELECT m.karat_id, k.karat_value, SUM(m.weight_delta_grams) AS weight_balance,
                   SUM(m.equivalent_21_delta_grams) AS eq21_balance
            FROM supplier_gold_movements m JOIN karats k ON k.id = m.karat_id
            WHERE m.supplier_id = %s GROUP BY m.karat_id, k.karat_value ORDER BY k.karat_value
            """, (_id(supplier_id),))

    def making_balance(self, supplier_id):
        row = self._fetch_one("SELECT COALESCE(SUM(amount_delta), 0) AS balance FROM supplier_making_movements "
                              "WHERE supplier_id = %s", (_id(supplier_id),))
        return row["balance"]

    # ------------------------------------------------------------------ ledgers (running balance over the whole ledger)
    def gold_ledger(self, supplier_id, limit=200):
        return self._fetch_all(
            """
            SELECT x.* FROM (
                SELECT m.id, m.occurred_at, t.code AS movement_code, t.name AS movement_name, m.karat_id,
                       k.karat_value, m.weight_delta_grams, m.equivalent_21_delta_grams, m.source_type, m.source_id,
                       m.source_reference, m.description, u.display_name AS actor_name,
                       SUM(m.equivalent_21_delta_grams) OVER (ORDER BY m.occurred_at, m.id) AS eq21_balance_after
                FROM supplier_gold_movements m
                JOIN supplier_gold_movement_types t ON t.id = m.movement_type_id
                JOIN karats k ON k.id = m.karat_id
                LEFT JOIN users u ON u.id = m.created_by_user_id
                WHERE m.supplier_id = %s
            ) x ORDER BY x.occurred_at DESC, x.id DESC LIMIT %s
            """, (_id(supplier_id), int(limit)))

    def making_ledger(self, supplier_id, limit=200):
        return self._fetch_all(
            """
            SELECT x.* FROM (
                SELECT m.id, m.occurred_at, t.code AS movement_code, t.name AS movement_name, m.amount_delta,
                       m.source_type, m.source_id, m.source_reference, m.description, u.display_name AS actor_name,
                       SUM(m.amount_delta) OVER (ORDER BY m.occurred_at, m.id) AS balance_after
                FROM supplier_making_movements m
                JOIN supplier_making_movement_types t ON t.id = m.movement_type_id
                LEFT JOIN users u ON u.id = m.created_by_user_id
                WHERE m.supplier_id = %s
            ) x ORDER BY x.occurred_at DESC, x.id DESC LIMIT %s
            """, (_id(supplier_id), int(limit)))

    def audit(self, *, actor, event_type, entity_id, reference, description, metadata, now, entity_type="SUPPLIER"):
        self._execute(
            "INSERT INTO audit_log (user_id, event_type, entity_type, entity_id, entity_reference, description, "
            "metadata_json, occurred_at) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
            (actor.user_id, event_type, entity_type, int(entity_id), reference, description,
             json.dumps(metadata, ensure_ascii=False, default=str), now))

    def db_now(self):
        return self._fetch_one("SELECT NOW() AS now")["now"].replace(microsecond=0)
