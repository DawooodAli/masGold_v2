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
    def list_suppliers(self, search=None, active=None, limit=500, role=None):
        where, params = [], []
        if role == "SUPPLIER":
            where.append("s.is_supplier = 1")
        elif role == "INTERMEDIARY":
            where.append("s.is_intermediary = 1")
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
            SELECT s.id, s.name, s.phone, s.notes, s.is_active, s.is_supplier, s.is_intermediary, s.created_at, s.karat_id, kk.karat_value,
                   COALESCE(g.grams, 0) AS gold_grams_balance, COALESCE(g.foreign_rows, 0) AS gold_foreign_rows,
                   COALESCE(m.amount, 0) AS making_balance, COALESCE(i.posted_count, 0) AS posted_invoice_count
            FROM suppliers s
            LEFT JOIN karats kk ON kk.id = s.karat_id
            LEFT JOIN (SELECT gm.supplier_id,
                              SUM(CASE WHEN gm.karat_id = s2.karat_id THEN gm.weight_delta_grams ELSE 0 END) AS grams,
                              SUM(CASE WHEN s2.karat_id IS NULL OR gm.karat_id <> s2.karat_id THEN 1 ELSE 0 END) AS foreign_rows
                       FROM supplier_gold_movements gm JOIN suppliers s2 ON s2.id = gm.supplier_id
                       GROUP BY gm.supplier_id) g ON g.supplier_id = s.id
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
        """The supplier with its dedicated karat (value). OF s: only the supplier row is locked, never the karat master."""
        return self._fetch_one(
            "SELECT s.*, k.karat_value FROM suppliers s LEFT JOIN karats k ON k.id = s.karat_id WHERE s.id = %s"
            + (" FOR UPDATE OF s" if lock else ""), (_id(supplier_id),))

    def create_supplier(self, *, name, phone, notes, karat_id, user_id, is_supplier=True, is_intermediary=False):
        new_id, _ = self._execute(
            "INSERT INTO suppliers (name, karat_id, phone, notes, is_supplier, is_intermediary, created_by_user_id, updated_by_user_id) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
            (name, None if karat_id is None else int(karat_id), phone, notes, 1 if is_supplier else 0, 1 if is_intermediary else 0,
             user_id, user_id))
        return int(new_id)

    def set_roles(self, *, supplier_id, is_supplier, is_intermediary, user_id):
        self._execute("UPDATE suppliers SET is_supplier = %s, is_intermediary = %s, updated_by_user_id = %s WHERE id = %s",
                      (1 if is_supplier else 0, 1 if is_intermediary else 0, user_id, _id(supplier_id)))

    def clear_karat(self, *, supplier_id, user_id):
        """An intermediary-only party has no supplier-account karat (the 044 check); only reachable when the party has no history."""
        self._execute("UPDATE suppliers SET karat_id = NULL, updated_by_user_id = %s WHERE id = %s", (user_id, _id(supplier_id)))

    def role_history_count(self, supplier_id):
        """Every row that proves the party acted as a SUPPLIER: invoices of any status (cancelled and reversed included), gold and making
        ledger rows and making payments (custody payments included). Locking CURRENT reads (FOR SHARE): call it while holding the
        supplier row lock, so a concurrent invoice or payment is never missed by an older snapshot."""
        total = 0
        for table in ("supplier_purchase_invoices", "supplier_gold_movements", "supplier_making_movements", "supplier_making_payments"):
            row = self._fetch_one(f"SELECT COUNT(*) AS n FROM {table} WHERE supplier_id = %s FOR SHARE", (_id(supplier_id),))
            total += int(row["n"])
        return total

    def set_karat(self, *, supplier_id, karat_id, user_id):
        self._execute("UPDATE suppliers SET karat_id = %s, updated_by_user_id = %s WHERE id = %s",
                      (int(karat_id), user_id, _id(supplier_id)))

    def history_karats(self, supplier_id, lock=False):
        """The distinct karats of ALL the supplier's invoices (drafts and cancelled included) and gold movements. With
        lock=True a locking CURRENT read (FOR SHARE): under REPEATABLE READ a plain read could miss an invoice that
        committed after this transaction's snapshot. Call it while holding the supplier row lock."""
        found = {}
        for table, alias in (("supplier_purchase_invoices", "i"), ("supplier_gold_movements", "m")):
            rows = self._fetch_all(
                f"SELECT DISTINCT {alias}.karat_id, k.karat_value FROM {table} {alias} JOIN karats k ON k.id = {alias}.karat_id "
                f"WHERE {alias}.supplier_id = %s" + (f" FOR SHARE OF {alias}" if lock else ""), (_id(supplier_id),))
            for row in rows:
                found[int(row["karat_id"])] = {"karat_id": int(row["karat_id"]), "karat_value": int(row["karat_value"])}
        return sorted(found.values(), key=lambda h: h["karat_value"])

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
        """Per karat: contractual PHYSICAL grams (SUM of the immutable gold ledger). No Eq21 conversion."""
        return self._fetch_all(
            """
            SELECT m.karat_id, k.karat_value, SUM(m.weight_delta_grams) AS weight_balance
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
                       k.karat_value, m.weight_delta_grams, m.source_type, m.source_id,
                       m.source_reference, m.description, u.display_name AS actor_name,
                       SUM(m.weight_delta_grams) OVER (PARTITION BY m.karat_id ORDER BY m.occurred_at, m.id) AS weight_balance_after,
                       gs.id AS settlement_id, gs.delivered_weight_grams, gs.delivered_karat_value, gs.target_karat_value,
                       gc.id AS settlement_reversal_id, grc.source_id AS reversed_settlement_id
                FROM supplier_gold_movements m
                JOIN supplier_gold_movement_types t ON t.id = m.movement_type_id
                JOIN karats k ON k.id = m.karat_id
                LEFT JOIN users u ON u.id = m.created_by_user_id
                LEFT JOIN supplier_gold_settlements gs ON t.code = 'SETTLEMENT' AND m.source_type = 'SUPPLIER_GOLD_SETTLEMENT' AND gs.id = m.source_id
                LEFT JOIN transaction_corrections gc ON gc.source_type = 'SUPPLIER_GOLD_SETTLEMENT' AND gc.source_id = gs.id
                     AND gc.correction_type = 'FULL_REVERSAL'
                LEFT JOIN transaction_corrections grc ON t.code = 'SETTLEMENT_REVERSAL' AND m.source_type = 'TRANSACTION_CORRECTION'
                     AND grc.id = m.source_id
                WHERE m.supplier_id = %s
            ) x ORDER BY x.occurred_at DESC, x.id DESC LIMIT %s
            """, (_id(supplier_id), int(limit)))

    def making_ledger(self, supplier_id, limit=200):
        """The making ledger with its running balance over the WHOLE ledger. A payment row also carries its method,
        account and reversal (036; a payment made from market custody names its custody entry and is reversed by the reversal of
        that entry, 043); a reversal row carries the payment reference it restores."""
        return self._fetch_all(
            """
            SELECT x.* FROM (
                SELECT m.id, m.occurred_at, t.code AS movement_code, t.name AS movement_name, m.amount_delta,
                       m.source_type, m.source_id, m.source_reference, m.description, u.display_name AS actor_name,
                       SUM(m.amount_delta) OVER (ORDER BY m.occurred_at, m.id) AS balance_after,
                       p.id AS payment_id, p.payment_method, ca.name AS cash_account_name, ba.name AS bank_account_name,
                       c.id AS reversal_id, te.trip_id AS custody_trip_id, te.entry_no AS custody_entry_no,
                       IF(rc.source_type = 'SUPPLIER_MAKING_PAYMENT', rc.source_id, rl.payment_id) AS reversed_payment_id,
                       IF(rc.source_type = 'SUPPLIER_MAKING_PAYMENT', rc.source_reference, NULL) AS reversed_reference,
                       re.trip_id AS reversed_custody_trip_id, re.entry_no AS reversed_custody_entry_no
                FROM supplier_making_movements m
                JOIN supplier_making_movement_types t ON t.id = m.movement_type_id
                LEFT JOIN users u ON u.id = m.created_by_user_id
                LEFT JOIN supplier_making_payments p ON t.code = 'PAYMENT' AND m.source_type = 'SUPPLIER_MAKING_PAYMENT'
                     AND p.id = m.source_id
                LEFT JOIN cash_accounts ca ON ca.id = p.cash_account_id
                LEFT JOIN bank_accounts ba ON ba.id = p.bank_account_id
                LEFT JOIN market_trip_supplier_payments tl ON tl.payment_id = p.id
                LEFT JOIN market_trip_entries te ON te.id = tl.entry_id
                LEFT JOIN transaction_corrections c ON c.correction_type = 'FULL_REVERSAL'
                     AND ((c.source_type = 'SUPPLIER_MAKING_PAYMENT' AND c.source_id = p.id)
                          OR (tl.entry_id IS NOT NULL AND c.source_type = 'MARKET_TRIP_ENTRY' AND c.source_id = tl.entry_id))
                LEFT JOIN transaction_corrections rc ON t.code = 'PAYMENT_REVERSAL' AND m.source_type = 'TRANSACTION_CORRECTION'
                     AND rc.id = m.source_id
                LEFT JOIN market_trip_supplier_payments rl ON rc.source_type = 'MARKET_TRIP_ENTRY' AND rl.entry_id = rc.source_id
                LEFT JOIN market_trip_entries re ON re.id = rl.entry_id
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
