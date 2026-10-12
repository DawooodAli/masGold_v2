"""Whole assayed lot holdings at a market intermediary (048): source pools, IH documents, signed holding movements and the typed custody links.

Persistence only. Every write uses the caller's transaction and only INSERTs (nothing here is ever updated or deleted). The holding ledger
uses explicit signed compensation: balance(pool) = SUM(weight_delta) over ALL of the pool's movements; a reversal adds a movement, it never
excludes an original one. The custody side keeps its own convention (a reversed custody entry stops counting) in MarketTripRepository.
Every locking read is a CURRENT read (FOR SHARE / FOR UPDATE) and none has a nested lookup that could use an older snapshot."""
import json

from repositories.base_repository import BaseRepository
from repositories.invoice_repository import _id

HANDOVER, RETURN = "HANDOVER", "RETURN"                       # document kinds
MOVE_HANDOVER, MOVE_RETURN = "HANDOVER", "RETURN"             # original movements
MOVE_HANDOVER_REVERSAL, MOVE_RETURN_REVERSAL = "HANDOVER_REVERSAL", "RETURN_REVERSAL"      # compensations posted by a reversal
LOT_HOLD, HOLD_RETURN = "LOT_HOLD", "HOLD_RETURN"             # custody entry kinds
HOLD_OUT, HOLD_IN = "HOLD_OUT", "HOLD_IN"                     # custody lot movement types
CONFIRMATION_CODE = "SAME_BAR_WHOLE_UNDIVIDED"                # the stored code of the owner's «same bar, whole, undivided» confirmation
SOURCE = "INTERMEDIARY_HOLDING"
LOGO_HOLD, HOLD_BACK = "LOGO_HOLD", "HOLD_BACK"               # 049 custody entry kinds (master-karat LOGO grams)


def holding_code(document_id):
    """IH + 6 digits. Free prefix (S, SP, SM, SG, MT, DP, DR, CS, FO, TR, RV, FC, RT, EX are taken); SG stays reserved for supplier settlements."""
    return f"IH{int(document_id):06d}"


def logo_holding_code(document_id):
    """IG + 6 digits (049): a master-karat LOGO document. IH stays the assayed-lot document."""
    return f"IG{int(document_id):06d}"


class IntermediaryHoldingRepository(BaseRepository):

    def _execute(self, query, params=()):
        cursor = self.connection.cursor()
        try:
            cursor.execute(query, params)
            return cursor.lastrowid, cursor.rowcount
        finally:
            cursor.close()

    # ------------------------------------------------------------------ writes (insert-only)
    def insert_pool(self, *, party_id, lot, user_id):
        pool_id, _ = self._execute(
            "INSERT INTO intermediary_holding_pools (party_id, lot_id, origin_melt_entry_id, weight_grams, fineness_permille, created_by_user_id) "
            "VALUES (%s, %s, %s, %s, %s, %s)",
            (_id(party_id), int(lot["id"]), int(lot["entry_id"]), lot["weight_grams"], lot["fineness_permille"], user_id))
        return int(pool_id)

    def insert_document(self, *, kind, pool, trip_id, document_date, reference, notes, confirmation_code, confirmed_by, confirmed_at,
                        key, digest, actor, now):
        document_id, _ = self._execute(
            """
            INSERT INTO intermediary_holding_documents (doc_kind, pool_id, party_id, lot_id, weight_grams, fineness_permille, trip_id,
                document_date, reference, notes, intact_confirmation_code, confirmed_by_user_id, confirmed_at, request_key, request_hash,
                performed_by_user_id, performed_by_employee_id, occurred_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """, (kind, int(pool["id"]), int(pool["party_id"]), int(pool["lot_id"]), pool["weight_grams"], pool["fineness_permille"],
                  _id(trip_id), document_date, reference, notes, confirmation_code, confirmed_by, confirmed_at, key, digest,
                  actor.user_id, actor.employee_id, now))
        return int(document_id)

    def insert_movement(self, *, pool_id, document_id, doc_kind, movement_type, weight, delta, correction_id=None):
        movement_id, _ = self._execute(
            "INSERT INTO intermediary_holding_movements (pool_id, document_id, doc_kind, movement_type, weight_grams, weight_delta, correction_id) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s)",
            (_id(pool_id), _id(document_id), doc_kind, movement_type, weight, delta, None if correction_id is None else _id(correction_id)))
        return int(movement_id)

    def insert_link(self, *, entry_id, entry_kind, trip_id, document_id, doc_kind, pool_id, lot_id, weight, custody_movement_id,
                    movement_type, delta):
        self._execute(
            "INSERT INTO market_trip_holding_links (entry_id, entry_kind, trip_id, document_id, doc_kind, pool_id, lot_id, weight_grams, "
            "custody_movement_id, movement_type, weight_delta) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (_id(entry_id), entry_kind, _id(trip_id), _id(document_id), doc_kind, _id(pool_id), int(lot_id), weight, int(custody_movement_id),
             movement_type, delta))

    def audit(self, *, actor, event_type, entity_id, reference, description, metadata, now):
        self._execute(
            "INSERT INTO audit_log (user_id, event_type, entity_type, entity_id, entity_reference, description, "
            "metadata_json, occurred_at) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
            (actor.user_id, event_type, SOURCE, int(entity_id), reference, description,
             json.dumps(metadata, ensure_ascii=False, default=str), now))

    # ------------------------------------------------------------------ current (locking) reads used by decisions
    def by_request(self, key):
        return self._fetch_one("SELECT * FROM intermediary_holding_documents WHERE request_key = %s FOR UPDATE", (key,))

    def lock_pool(self, pool_id):
        """The pool row is immutable; locking it is the serialization point of the pool's balance."""
        return self._fetch_one("SELECT * FROM intermediary_holding_pools WHERE id = %s FOR UPDATE", (_id(pool_id),))

    def pool_balance(self, pool_id):
        """Grams the party holds from this source NOW (a CURRENT read of every movement, compensations included)."""
        return self._fetch_one("SELECT COALESCE(SUM(weight_delta), 0) AS grams FROM intermediary_holding_movements WHERE pool_id = %s FOR SHARE",
                               (_id(pool_id),))["grams"]

    def lot_held_total(self, lot_id):
        """Grams of this lot held by ANY party across every pool it ever had (a CURRENT read): what keeps a lot from being in two places."""
        return self._fetch_one(
            "SELECT COALESCE(SUM(m.weight_delta), 0) AS grams FROM intermediary_holding_movements m "
            "JOIN intermediary_holding_pools p ON p.id = m.pool_id WHERE p.lot_id = %s FOR SHARE OF m", (int(lot_id),))["grams"]

    def history_count(self, party_id):
        """Every pool or document of the party, reversed ones included: it keeps the intermediary role and the party row (CURRENT read)."""
        total = 0
        for table in ("intermediary_holding_pools", "intermediary_holding_documents", "logo_holding_pools", "logo_holding_documents"):
            total += int(self._fetch_one(f"SELECT COUNT(*) AS n FROM {table} WHERE party_id = %s FOR SHARE", (_id(party_id),))["n"])
        return total

    # ------------------------------------------------------------------ master-karat LOGO holdings (049)
    def insert_logo_pool(self, *, party_id, karat_id, trip_id, weight, user_id):
        pool_id, _ = self._execute(
            "INSERT INTO logo_holding_pools (party_id, karat_id, designation, trip_id, weight_grams, created_by_user_id) VALUES (%s, %s, 'LOGO', %s, %s, %s)",
            (_id(party_id), int(karat_id), _id(trip_id), weight, user_id))
        return int(pool_id)

    def insert_logo_document(self, *, kind, pool, weight, document_date, reference, notes, key, digest, actor, now):
        pool_ref, pool_weight = (int(pool["id"]), weight) if kind == HANDOVER else (None, None)
        document_id, _ = self._execute(
            """
            INSERT INTO logo_holding_documents (doc_kind, pool_id, party_id, karat_id, trip_id, weight_grams, handover_pool_id, handover_weight,
                document_date, reference, notes, request_key, request_hash, performed_by_user_id, performed_by_employee_id, occurred_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """, (kind, int(pool["id"]), int(pool["party_id"]), int(pool["karat_id"]), int(pool["trip_id"]), weight, pool_ref, pool_weight,
                  document_date, reference, notes, key, digest, actor.user_id, actor.employee_id, now))
        return int(document_id)

    def insert_logo_movement(self, *, pool_id, document_id, doc_kind, movement_type, weight, delta, correction_id=None):
        movement_id, _ = self._execute(
            "INSERT INTO logo_holding_movements (pool_id, document_id, doc_kind, movement_type, weight_grams, weight_delta, correction_id) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s)",
            (_id(pool_id), _id(document_id), doc_kind, movement_type, weight, delta, None if correction_id is None else _id(correction_id)))
        return int(movement_id)

    def insert_logo_link(self, *, entry_id, entry_kind, trip_id, document_id, doc_kind, pool_id, karat_id, weight):
        self._execute(
            "INSERT INTO market_trip_logo_hold_links (entry_id, entry_kind, trip_id, document_id, doc_kind, pool_id, karat_id, designation, weight_grams) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, 'LOGO', %s)",
            (_id(entry_id), entry_kind, _id(trip_id), _id(document_id), doc_kind, _id(pool_id), int(karat_id), weight))

    def logo_by_request(self, key):
        return self._fetch_one("SELECT * FROM logo_holding_documents WHERE request_key = %s FOR UPDATE", (key,))

    def lock_logo_pool(self, pool_id):
        return self._fetch_one("SELECT * FROM logo_holding_pools WHERE id = %s FOR UPDATE", (_id(pool_id),))

    def logo_pool_balance(self, pool_id):
        """Grams the party still holds from this source (a CURRENT read of every movement, compensations included)."""
        return self._fetch_one("SELECT COALESCE(SUM(weight_delta), 0) AS grams FROM logo_holding_movements WHERE pool_id = %s FOR SHARE", (_id(pool_id),))["grams"]

    def logo_pool(self, pool_id):
        return self._fetch_one("SELECT * FROM logo_holding_pools WHERE id = %s", (_id(pool_id),))

    def logo_pool_handover(self, pool_id):
        return self._fetch_one("SELECT * FROM logo_holding_documents WHERE pool_id = %s AND doc_kind = 'HANDOVER' ORDER BY id LIMIT 1", (_id(pool_id),))

    def logo_link_by_entry(self, entry_id):
        return self._fetch_one(
            "SELECT l.*, d.party_id, d.document_date, d.request_key FROM market_trip_logo_hold_links l "
            "JOIN logo_holding_documents d ON d.id = l.document_id WHERE l.entry_id = %s", (_id(entry_id),))

    def logo_party_documents(self, party_id):
        return self._fetch_all(
            """
            SELECT d.id AS document_id, d.doc_kind, d.pool_id, d.weight_grams, d.document_date, d.reference, d.notes, d.occurred_at,
                   u.display_name AS actor_name, d.karat_id, kk.karat_value, k.entry_id, ce.trip_id, ce.entry_no, t.status AS trip_status,
                   (SELECT COALESCE(SUM(m.weight_delta), 0) FROM logo_holding_movements m WHERE m.pool_id = d.pool_id) AS pool_balance,
                   c.id AS reversal_id, c.reason AS reversal_reason
            FROM logo_holding_documents d
            JOIN karats kk ON kk.id = d.karat_id
            JOIN market_trip_logo_hold_links k ON k.document_id = d.id
            JOIN market_trip_entries ce ON ce.id = k.entry_id
            JOIN market_trips t ON t.id = ce.trip_id
            LEFT JOIN users u ON u.id = d.performed_by_user_id
            LEFT JOIN transaction_corrections c ON c.source_type = 'MARKET_TRIP_ENTRY' AND c.source_id = k.entry_id
                 AND c.correction_type = 'FULL_REVERSAL'
            WHERE d.party_id = %s ORDER BY d.id DESC
            """, (_id(party_id),))

    def logo_open_pools(self, *, party_id=None, trip_id=None):
        where, params = ["bal.grams > 0"], []
        if party_id is not None:
            where.append("p.party_id = %s")
            params.append(_id(party_id))
        if trip_id is not None:
            where.append("p.trip_id = %s")
            params.append(_id(trip_id))
        return self._fetch_all(
            f"""
            SELECT p.id AS pool_id, p.party_id, s.name AS party_name, s.is_active AS party_active, p.karat_id, kk.karat_value, p.weight_grams,
                   p.trip_id, t.status AS trip_status, h.id AS handover_id, h.document_date, he.entry_no AS handover_entry_no, bal.grams AS balance
            FROM logo_holding_pools p
            JOIN (SELECT pool_id, SUM(weight_delta) AS grams FROM logo_holding_movements GROUP BY pool_id) bal ON bal.pool_id = p.id
            JOIN suppliers s ON s.id = p.party_id
            JOIN karats kk ON kk.id = p.karat_id
            JOIN market_trips t ON t.id = p.trip_id
            JOIN logo_holding_documents h ON h.pool_id = p.id AND h.doc_kind = 'HANDOVER'
            JOIN market_trip_logo_hold_links hk ON hk.document_id = h.id
            JOIN market_trip_entries he ON he.id = hk.entry_id
            WHERE {' AND '.join(where)} ORDER BY p.id DESC
            """, tuple(params))

    # ------------------------------------------------------------------ plain reads
    def pool(self, pool_id):
        return self._fetch_one("SELECT * FROM intermediary_holding_pools WHERE id = %s", (_id(pool_id),))

    def pool_handover(self, pool_id):
        """The HANDOVER document of a pool with its custody trip (immutable, so a plain read is enough to find the trip to lock)."""
        return self._fetch_one("SELECT * FROM intermediary_holding_documents WHERE pool_id = %s AND doc_kind = 'HANDOVER' ORDER BY id LIMIT 1",
                               (_id(pool_id),))

    def link_by_entry(self, entry_id):
        return self._fetch_one(
            "SELECT l.*, d.party_id, d.fineness_permille, d.document_date, d.request_key FROM market_trip_holding_links l "
            "JOIN intermediary_holding_documents d ON d.id = l.document_id WHERE l.entry_id = %s", (_id(entry_id),))

    def document(self, document_id):
        return self._fetch_one("SELECT * FROM intermediary_holding_documents WHERE id = %s", (_id(document_id),))

    def party_documents(self, party_id):
        """Every document of the party (newest first) with its source, the custody entry that posted it, the pool's CURRENT balance and the
        reversal (RV) of that entry. Display only: a reversed document stays listed."""
        return self._fetch_all(
            """
            SELECT d.id AS document_id, d.doc_kind, d.pool_id, d.weight_grams, d.fineness_permille, d.document_date, d.reference, d.notes,
                   d.occurred_at, d.confirmed_at, d.intact_confirmation_code, u.display_name AS actor_name, cu.display_name AS confirmed_by_name,
                   p.lot_id, l.lot_no, l.lot_kind, l.label, oe.trip_id AS origin_trip_id, oe.entry_no AS origin_entry_no,
                   k.entry_id, ce.trip_id, ce.entry_no, t.status AS trip_status,
                   (SELECT COALESCE(SUM(m.weight_delta), 0) FROM intermediary_holding_movements m WHERE m.pool_id = d.pool_id) AS pool_balance,
                   c.id AS reversal_id, c.reason AS reversal_reason
            FROM intermediary_holding_documents d
            JOIN intermediary_holding_pools p ON p.id = d.pool_id
            JOIN market_trip_lots l ON l.id = p.lot_id
            JOIN market_trip_entries oe ON oe.id = l.entry_id
            JOIN market_trip_holding_links k ON k.document_id = d.id
            JOIN market_trip_entries ce ON ce.id = k.entry_id
            JOIN market_trips t ON t.id = ce.trip_id
            LEFT JOIN users u ON u.id = d.performed_by_user_id
            LEFT JOIN users cu ON cu.id = d.confirmed_by_user_id
            LEFT JOIN transaction_corrections c ON c.source_type = 'MARKET_TRIP_ENTRY' AND c.source_id = k.entry_id
                 AND c.correction_type = 'FULL_REVERSAL'
            WHERE d.party_id = %s ORDER BY d.id DESC
            """, (_id(party_id),))

    def open_pools(self, *, party_id=None, trip_id=None):
        """Pools with grams still at a party, one row each, with the original handover and its custody trip. Display only."""
        where, params = ["bal.grams > 0"], []
        if party_id is not None:
            where.append("p.party_id = %s")
            params.append(_id(party_id))
        if trip_id is not None:
            where.append("h.trip_id = %s")
            params.append(_id(trip_id))
        return self._fetch_all(
            f"""
            SELECT p.id AS pool_id, p.party_id, s.name AS party_name, s.is_active AS party_active, p.lot_id, p.weight_grams, p.fineness_permille,
                   l.lot_no, l.lot_kind, l.label, oe.trip_id AS origin_trip_id, oe.entry_no AS origin_entry_no,
                   h.id AS handover_id, h.document_date, h.trip_id, t.status AS trip_status, he.entry_no AS handover_entry_no, bal.grams AS balance
            FROM intermediary_holding_pools p
            JOIN (SELECT pool_id, SUM(weight_delta) AS grams FROM intermediary_holding_movements GROUP BY pool_id) bal ON bal.pool_id = p.id
            JOIN suppliers s ON s.id = p.party_id
            JOIN market_trip_lots l ON l.id = p.lot_id
            JOIN market_trip_entries oe ON oe.id = l.entry_id
            JOIN intermediary_holding_documents h ON h.pool_id = p.id AND h.doc_kind = 'HANDOVER'
            JOIN market_trip_holding_links hk ON hk.document_id = h.id
            JOIN market_trip_entries he ON he.id = hk.entry_id
            JOIN market_trips t ON t.id = h.trip_id
            WHERE {' AND '.join(where)} ORDER BY p.id DESC
            """, tuple(params))
