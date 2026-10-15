"""Supplier purchase invoices, making groups, the two immutable supplier ledgers and the intake links (034).
All writes use the caller's transaction. A ledger row is only ever inserted; a posted invoice's header and groups
are never updated afterwards (the service refuses, and nothing here offers such an update for POSTED rows)."""
from decimal import Decimal

from repositories.supplier_repository import SupplierRepository, _id

ACTIVE_BARCODE_JOIN = ("LEFT JOIN inventory_barcodes ib ON ib.inventory_item_id = ii.id "
                       "AND ib.barcode_status = 'ACTIVE'")
PURCHASE_SOURCE = "SUPPLIER_PURCHASE_INVOICE"     # the movement source of a posted invoice AND the correction source of its reversal (052)
PURCHASE_MOVEMENT = "PURCHASE_INVOICE"
REVERSAL_MOVEMENT = "PURCHASE_INVOICE_REVERSAL"   # the movement type (both ledgers) of the exact opposite entry posted by a full reversal


class SupplierPurchaseRepository(SupplierRepository):

    # ------------------------------------------------------------------ invoices
    _INVOICE = """
        SELECT i.*, s.name AS supplier_name, s.is_active AS supplier_active, k.karat_value, k.name AS karat_name
        FROM supplier_purchase_invoices i
        JOIN suppliers s ON s.id = i.supplier_id
        JOIN karats k ON k.id = i.karat_id
    """

    def by_draft_key(self, key):
        return self._fetch_one(self._INVOICE + " WHERE i.draft_request_key = %s FOR UPDATE", (key,))

    def get_invoice(self, invoice_id, lock=False):
        return self._fetch_one(self._INVOICE + " WHERE i.id = %s" + (" FOR UPDATE" if lock else ""),
                               (_id(invoice_id),))

    def insert_invoice(self, *, supplier_id, karat_id, supplier_reference, invoice_date, weight, gross_making, notes,
                       key, user_id):
        new_id, _ = self._execute(
            """
            INSERT INTO supplier_purchase_invoices (supplier_id, karat_id, supplier_reference, invoice_date,
                supplier_weight_grams, gross_making_total, status, notes, draft_request_key, created_by_user_id)
            VALUES (%s, %s, %s, %s, %s, %s, 'DRAFT', %s, %s, %s)
            """, (_id(supplier_id), int(karat_id), supplier_reference, invoice_date, weight, gross_making, notes,
                  key, user_id))
        return int(new_id)

    def update_draft(self, *, invoice_id, supplier_id, karat_id, supplier_reference, invoice_date, weight,
                     gross_making, notes):
        """Only a DRAFT row can match: a posted or cancelled invoice is never changed (rowcount 0)."""
        _, count = self._execute(
            """
            UPDATE supplier_purchase_invoices
            SET supplier_id = %s, karat_id = %s, supplier_reference = %s, invoice_date = %s,
                supplier_weight_grams = %s, gross_making_total = %s, notes = %s
            WHERE id = %s AND status = 'DRAFT'
            """, (_id(supplier_id), int(karat_id), supplier_reference, invoice_date, weight, gross_making, notes,
                  _id(invoice_id)))
        return count

    def replace_groups(self, invoice_id, groups):
        """Draft groups only (the service has locked a DRAFT invoice); posted groups are never touched."""
        self._execute("DELETE FROM supplier_invoice_groups WHERE invoice_id = %s", (_id(invoice_id),))
        for group in groups:
            self._execute(
                """
                INSERT INTO supplier_invoice_groups (invoice_id, group_no, description, supplier_weight_grams,
                    making_per_gram, making_total)
                VALUES (%s, %s, %s, %s, %s, %s)
                """, (_id(invoice_id), group["group_no"], group["description"], group["supplier_weight_grams"],
                      group["making_per_gram"], group["making_total"]))

    def groups(self, invoice_id):
        return self._fetch_all("SELECT * FROM supplier_invoice_groups WHERE invoice_id = %s ORDER BY group_no",
                               (_id(invoice_id),))

    def lock_group(self, group_id):
        return self._fetch_one("SELECT * FROM supplier_invoice_groups WHERE id = %s FOR UPDATE", (_id(group_id),))

    def purchase_categories(self):
        """The ACTIVE inventory categories that have a purchase-making range (the existing item_categories rules the
        group's making rate is matched against). A category with neither bound is not configured and is left out."""
        return self._fetch_all(
            """
            SELECT id, code, name, is_active, purchase_making_min_per_gram, purchase_making_max_per_gram
            FROM item_categories
            WHERE is_active = 1
              AND (purchase_making_min_per_gram IS NOT NULL OR purchase_making_max_per_gram IS NOT NULL)
            ORDER BY id
            """)

    def mark_posted(self, *, invoice_id, key, user_id, now):
        _, count = self._execute(
            "UPDATE supplier_purchase_invoices SET status = 'POSTED', post_request_key = %s, posted_by_user_id = %s, "
            "posted_at = %s WHERE id = %s AND status = 'DRAFT'", (key, user_id, now, _id(invoice_id)))
        return count

    def mark_cancelled(self, *, invoice_id, now):
        _, count = self._execute(
            "UPDATE supplier_purchase_invoices SET status = 'CANCELLED', cancelled_at = %s "
            "WHERE id = %s AND status = 'DRAFT'", (now, _id(invoice_id)))
        return count

    def list_invoices(self, supplier_id=None, status=None, limit=200):
        where, params = [], []
        if supplier_id is not None:
            where.append("i.supplier_id = %s")
            params.append(_id(supplier_id))
        if status:
            where.append("i.status = %s")
            params.append(status)
        params.append(int(limit))
        return self._fetch_all(
            f"""
            SELECT i.id, i.supplier_id, s.name AS supplier_name, i.karat_id, k.karat_value, i.supplier_reference,
                   i.invoice_date, i.supplier_weight_grams, i.gross_making_total, i.status, i.created_at, i.posted_at,
                   COALESCE(p.piece_count, 0) AS piece_count, COALESCE(p.actual_weight, 0) AS actual_weight,
                   rv.id AS reversal_id
            FROM supplier_purchase_invoices i
            JOIN suppliers s ON s.id = i.supplier_id
            JOIN karats k ON k.id = i.karat_id
            LEFT JOIN transaction_corrections rv ON rv.source_type = 'SUPPLIER_PURCHASE_INVOICE' AND rv.source_id = i.id
                 AND rv.correction_type = 'FULL_REVERSAL'
            LEFT JOIN (SELECT sp.invoice_id, COUNT(*) AS piece_count, SUM(ii.weight_grams) AS actual_weight
                       FROM supplier_invoice_pieces sp JOIN inventory_items ii ON ii.id = sp.inventory_item_id
                       GROUP BY sp.invoice_id) p ON p.invoice_id = i.id
            {'WHERE ' + ' AND '.join(where) if where else ''}
            ORDER BY i.invoice_date DESC, i.id DESC
            LIMIT %s
            """, tuple(params))

    # ------------------------------------------------------------------ reversal (052): the reversed state is DERIVED from the correction record
    def reversal(self, invoice_id, lock=False):
        """The FULL_REVERSAL correction of an invoice, or None. `lock=True` is a locking CURRENT read, for decisions: under REPEATABLE READ a plain read
        could return a snapshot older than a reversal committed while this transaction waited for the invoice lock."""
        return self._fetch_one(
            "SELECT id AS correction_id, occurred_at, reason FROM transaction_corrections "
            "WHERE source_type = %s AND source_id = %s AND correction_type = 'FULL_REVERSAL'" + (" FOR SHARE" if lock else ""),
            (PURCHASE_SOURCE, _id(invoice_id)))

    def intaken_pieces(self, invoice_id, lock=False):
        """The pieces linked to the invoice (any later status). A decision reads them CURRENT under the invoice lock."""
        return self._fetch_all("SELECT id, inventory_item_id FROM supplier_invoice_pieces WHERE invoice_id = %s ORDER BY id"
                               + (" FOR SHARE" if lock else ""), (_id(invoice_id),))

    def posted_gold_movement(self, invoice_id):
        """The GOLD movement the posting wrote (its stored values are what a reversal negates)."""
        return self._fetch_one(
            "SELECT m.id, m.supplier_id, m.karat_id, m.weight_delta_grams, m.equivalent_21_delta_grams "
            "FROM supplier_gold_movements m JOIN supplier_gold_movement_types t ON t.id = m.movement_type_id "
            "WHERE m.source_type = %s AND m.source_id = %s AND t.code = %s",
            (PURCHASE_SOURCE, _id(invoice_id), PURCHASE_MOVEMENT))

    def posted_making_movement(self, invoice_id):
        """The MAKING movement the posting wrote (None for an invoice without making)."""
        return self._fetch_one(
            "SELECT m.id, m.supplier_id, m.amount_delta "
            "FROM supplier_making_movements m JOIN supplier_making_movement_types t ON t.id = m.movement_type_id "
            "WHERE m.source_type = %s AND m.source_id = %s AND t.code = %s",
            (PURCHASE_SOURCE, _id(invoice_id), PURCHASE_MOVEMENT))

    # ------------------------------------------------------------------ ledgers (insert only)
    def gold_type_id(self, code):
        row = self._fetch_one("SELECT id FROM supplier_gold_movement_types WHERE code = %s AND is_active = TRUE",
                              (code,))
        return None if row is None else int(row["id"])

    def making_type_id(self, code):
        row = self._fetch_one("SELECT id FROM supplier_making_movement_types WHERE code = %s AND is_active = TRUE",
                              (code,))
        return None if row is None else int(row["id"])

    def insert_gold_movement(self, *, supplier_id, type_id, karat_id, weight_delta, eq21_delta, source_type,
                             source_id, reference, description, now, user_id):
        new_id, _ = self._execute(
            """
            INSERT INTO supplier_gold_movements (supplier_id, movement_type_id, karat_id, weight_delta_grams,
                equivalent_21_delta_grams, source_type, source_id, source_reference, description, occurred_at,
                created_by_user_id)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """, (_id(supplier_id), int(type_id), int(karat_id), weight_delta, eq21_delta, source_type,
                  _id(source_id), reference, description, now, user_id))
        return int(new_id)

    def insert_making_movement(self, *, supplier_id, type_id, amount_delta, source_type, source_id, reference,
                               description, now, user_id):
        new_id, _ = self._execute(
            """
            INSERT INTO supplier_making_movements (supplier_id, movement_type_id, amount_delta, source_type,
                source_id, source_reference, description, occurred_at, created_by_user_id)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            """, (_id(supplier_id), int(type_id), amount_delta, source_type, _id(source_id), reference, description,
                  now, user_id))
        return int(new_id)

    # ------------------------------------------------------------------ intake progress and pieces
    def group_progress(self, invoice_id):
        """Per group: pieces linked and their ACTUAL shop-scale weight (every linked piece counts, whatever its
        later status). Context for the weight variance only — never an accounting input."""
        return self._fetch_all(
            """
            SELECT g.id AS group_id, COUNT(sp.id) AS piece_count, COALESCE(SUM(ii.weight_grams), 0) AS actual_weight
            FROM supplier_invoice_groups g
            LEFT JOIN supplier_invoice_pieces sp ON sp.group_id = g.id
            LEFT JOIN inventory_items ii ON ii.id = sp.inventory_item_id
            WHERE g.invoice_id = %s GROUP BY g.id ORDER BY g.group_no
            """, (_id(invoice_id),))

    def group_actual_weight(self, group_id):
        """The authoritative cumulative ACTUAL weight of a group: every piece ever linked to it counts, whatever its
        later inventory status (selling or returning a piece never frees intake allowance) — the same inclusion rule as
        group_progress. A locking CURRENT read (FOR SHARE), not a consistent-snapshot read: under REPEATABLE READ a
        plain SUM could be answered from a snapshot taken before a concurrent intake of the same group committed.
        Call it while holding the group row lock (lock_group) so concurrent intakes of one group are serialized."""
        row = self._fetch_one(
            """
            SELECT COALESCE(SUM(ii.weight_grams), 0) AS actual_weight
            FROM supplier_invoice_pieces sp
            JOIN inventory_items ii ON ii.id = sp.inventory_item_id
            WHERE sp.group_id = %s FOR SHARE
            """, (_id(group_id),))
        return Decimal(str(row["actual_weight"]))

    def piece_by_request(self, key):
        return self._fetch_one("SELECT * FROM supplier_invoice_pieces WHERE request_key = %s FOR UPDATE", (key,))

    def insert_piece(self, *, inventory_item_id, invoice_id, group_id, making_per_gram, key, user_id):
        new_id, _ = self._execute(
            """
            INSERT INTO supplier_invoice_pieces (inventory_item_id, invoice_id, group_id, making_per_gram,
                request_key, created_by_user_id)
            VALUES (%s, %s, %s, %s, %s, %s)
            """, (_id(inventory_item_id), _id(invoice_id), _id(group_id), making_per_gram, key, user_id))
        return int(new_id)

    def pieces(self, invoice_id, group_id=None, limit=500):
        where, params = ["sp.invoice_id = %s"], [_id(invoice_id)]
        if group_id is not None:
            where.append("sp.group_id = %s")
            params.append(_id(group_id))
        params.append(int(limit))
        return self._fetch_all(
            f"""
            SELECT sp.id, sp.inventory_item_id, sp.group_id, g.group_no, sp.making_per_gram, sp.created_at,
                   ii.weight_grams, ii.size, ii.inventory_status, it.name AS item_type_name, ib.barcode_value
            FROM supplier_invoice_pieces sp
            JOIN supplier_invoice_groups g ON g.id = sp.group_id
            JOIN inventory_items ii ON ii.id = sp.inventory_item_id
            JOIN item_types it ON it.id = ii.item_type_id
            {ACTIVE_BARCODE_JOIN}
            WHERE {' AND '.join(where)}
            ORDER BY sp.id DESC LIMIT %s
            """, tuple(params))

    def piece_for_item(self, inventory_item_id):
        """Reverse traceability: the supplier invoice / group a piece came from (None for other stock)."""
        return self._fetch_one(
            """
            SELECT sp.inventory_item_id, sp.invoice_id, sp.group_id, g.group_no, sp.making_per_gram,
                   i.supplier_id, s.name AS supplier_name, i.supplier_reference
            FROM supplier_invoice_pieces sp
            JOIN supplier_invoice_groups g ON g.id = sp.group_id
            JOIN supplier_purchase_invoices i ON i.id = sp.invoice_id
            JOIN suppliers s ON s.id = i.supplier_id
            WHERE sp.inventory_item_id = %s
            """, (_id(inventory_item_id),))
