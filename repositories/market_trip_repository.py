"""Market-trip custody (037, melting 039, lot returns 040, lot reissue 041, Logo heating 042, supplier payments 043, supplier gold settlements 046): trips, their insert-only entries, and the cash / Scrap movements those entries post.

All writes use the caller's transaction and only INSERT (the trip header's `status` is the one updated column; every change
of it is an insert-only event). Custody is never stored: it is derived from the entries that were not reversed.
A reversed entry is one that has a FULL_REVERSAL row in transaction_corrections (source_type MARKET_TRIP_ENTRY)."""
import json

from repositories.base_repository import BaseRepository
from repositories.invoice_repository import _id

SOURCE = "MARKET_TRIP_ENTRY"            # transaction_corrections.source_type and the cash / Scrap reference_type
MAIN_SAFE = "MAIN_SAFE"
ISSUE, RETURN, MELT, LOT_RETURN, LOT_ISSUE, HEAT = "ISSUE", "RETURN", "MELT", "LOT_RETURN", "LOT_ISSUE", "HEAT"
SUPPLIER_PAY = "SUPPLIER_PAY"          # a supplier making payment made with custody cash (043)
GOLD_SETTLE = "GOLD_SETTLE"            # custody gold delivered to a supplier against its gold account (046, LOGO only in this slice)
LOT_SETTLE, LOT_SETTLE_MOVEMENT = "LOT_SETTLE", "SUPPLIER_SETTLE"
LOT_HOLD, HOLD_RETURN = "LOT_HOLD", "HOLD_RETURN"          # 048: a whole lot handed to / taken back from an intermediary (holding links)
MELT_DESIGNATION, LOGO_DESIGNATION, UNCLASSIFIED = "MELT", "LOGO", "UNCLASSIFIED"     # the owner's decision on an issued gold line
DESIGNATIONS = (MELT_DESIGNATION, LOGO_DESIGNATION, UNCLASSIFIED)
MAIN_LOT, RESIDUE_LOT, CREATE_MOVEMENT = "MAIN", "RESIDUE", "CREATE"
SHOP_RETURN_MOVEMENT = "SHOP_RETURN"                      # custody-side lot movement of a LOT_RETURN (negative grams)
SHOP_RETURN_IN, SHOP_RETURN_REVERSAL = "TRIP_RETURN", "RETURN_REVERSAL"      # shop-side lot ledger movement types
SHOP_ISSUE_MOVEMENT = "SHOP_ISSUE"                                 # custody-side lot movement of a LOT_ISSUE (positive grams)
SHOP_ISSUE_OUT, SHOP_ISSUE_REVERSAL = "TRIP_ISSUE", "ISSUE_REVERSAL"         # shop-side movements of a reissue and of its reversal
OPEN, CLOSED = "OPEN", "CLOSED"
CASH_TYPE = {ISSUE: "TRIP_CASH_ISSUE", RETURN: "TRIP_CASH_RETURN"}
SCRAP_TYPE = {ISSUE: "TRIP_CUSTODY_OUT", RETURN: "TRIP_CUSTODY_RETURN_IN"}

def _not_reversed(alias="e", *, current=False):
    # A locking outer query does not make its nested correction lookup current under RR.
    # Lot write/dependency callers already hold trip -> original lot locks before requesting this form.
    return ("NOT EXISTS (SELECT 1 FROM transaction_corrections c WHERE c.source_type = '" + SOURCE + "' "
            f"AND c.source_id = {alias}.id AND c.correction_type = 'FULL_REVERSAL'"
            + (" FOR SHARE" if current else "") + ")")


_NOT_REVERSED = _not_reversed("e")


def trip_code(trip_id):
    """MT + 6 digits. Distinct from S, SP, SM, DP, DR, CS, FO, TR, RV, FC, RT, EX."""
    return f"MT{int(trip_id):06d}"


def entry_code(trip_id, entry_no):
    """The reference of one entry: its trip code and its number inside the trip (MT000001-2)."""
    return f"{trip_code(trip_id)}-{int(entry_no)}"


class MarketTripRepository(BaseRepository):

    def _execute(self, query, params=()):
        cursor = self.connection.cursor()
        try:
            cursor.execute(query, params)
            return cursor.lastrowid, cursor.rowcount
        finally:
            cursor.close()

    def db_now(self):
        return self._fetch_one("SELECT NOW() AS now")["now"].replace(microsecond=0)

    # ------------------------------------------------------------------ accounts and master data
    def main_safe(self):
        """The Main Safe resolved by its stable code (never a hard-coded id)."""
        return self._fetch_one("SELECT id, code, name, is_active FROM cash_accounts WHERE code = %s", (MAIN_SAFE,))

    def cash_balance(self, cash_account_id):
        return self._fetch_one("SELECT COALESCE(SUM(amount_delta), 0) AS balance FROM cash_movements WHERE cash_account_id = %s",
                               (int(cash_account_id),))["balance"]

    def karat(self, karat_id):
        return self._fetch_one("SELECT id, karat_value, name, is_active FROM karats WHERE id = %s", (int(karat_id),))

    def active_karats(self):
        return self._fetch_all("SELECT id, karat_value, name FROM karats WHERE is_active = TRUE ORDER BY karat_value DESC")

    def shop_scrap(self):
        """What the SHOP holds per karat: physical, held for manager review, and available (never includes trip custody)."""
        return self._fetch_all("SELECT karat_id, karat_value, physical_grams, held_grams, available_grams "
                               "FROM vw_old_gold_available ORDER BY karat_value DESC")

    # ------------------------------------------------------------------ trips
    def insert_trip(self, *, notes, actor, now):
        trip_id, _ = self._execute("INSERT INTO market_trips (status, notes, created_by_user_id, created_at, status_changed_at) "
                                   "VALUES ('OPEN', %s, %s, %s, %s)", (notes, actor.user_id, now, now))
        return int(trip_id)

    def trip(self, trip_id, lock=False):
        return self._fetch_one("SELECT * FROM market_trips WHERE id = %s" + (" FOR UPDATE" if lock else ""), (_id(trip_id),))

    def set_status(self, *, trip_id, status, now):
        _, count = self._execute("UPDATE market_trips SET status = %s, status_changed_at = %s WHERE id = %s",
                                 (status, now, _id(trip_id)))
        if count != 1:
            raise RuntimeError("trip status was not changed")

    def insert_event(self, *, trip_id, event_type, reason, actor, now):
        self._execute("INSERT INTO market_trip_status_events (trip_id, event_type, reason, performed_by_user_id, occurred_at) "
                      "VALUES (%s, %s, %s, %s, %s)", (_id(trip_id), event_type, reason, actor.user_id, now))

    def events(self, trip_id):
        return self._fetch_all(
            "SELECT e.event_type, e.reason, e.occurred_at, u.display_name AS actor_name FROM market_trip_status_events e "
            "LEFT JOIN users u ON u.id = e.performed_by_user_id WHERE e.trip_id = %s ORDER BY e.id", (_id(trip_id),))

    def trips(self, limit=100):
        return self._fetch_all(
            """
            SELECT t.id, t.status, t.notes, t.created_at, t.status_changed_at,
                   (SELECT COUNT(*) FROM market_trip_entries e WHERE e.trip_id = t.id) AS entry_count
            FROM market_trips t ORDER BY t.id DESC LIMIT %s
            """, (int(limit),))

    # ------------------------------------------------------------------ entries
    def by_request(self, key):
        return self._fetch_one("SELECT * FROM market_trip_entries WHERE request_key = %s FOR UPDATE", (key,))

    def next_entry_no(self, trip_id):
        """Read under the trip lock: every writer of this trip's entries holds it."""
        row = self._fetch_one("SELECT COALESCE(MAX(entry_no), 0) + 1 AS n FROM market_trip_entries WHERE trip_id = %s FOR SHARE",
                              (_id(trip_id),))
        return int(row["n"])

    def insert_entry(self, *, trip_id, entry_no, kind, entry_date, notes, key, digest, actor, now):
        entry_id, _ = self._execute(
            """
            INSERT INTO market_trip_entries (trip_id, entry_no, entry_kind, entry_date, notes, request_key, request_hash,
                performed_by_user_id, performed_by_employee_id, occurred_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """, (_id(trip_id), entry_no, kind, entry_date, notes, key, digest, actor.user_id, actor.employee_id, now))
        return int(entry_id)

    def post_cash(self, *, kind, account_id, entry_id, code, amount, description, actor, now):
        """The real cash movement of an entry on MAIN_SAFE: OUT for an issue, IN for a return (a negative balance is allowed)."""
        delta = -amount if kind == ISSUE else amount
        type_code = CASH_TYPE[kind]
        movement_id, count = self._execute(
            """
            INSERT INTO cash_movements (cash_account_id, movement_type_id, amount_delta, reference_type, reference_id,
                reference_code, description, occurred_at, created_by_user_id)
            SELECT %s, t.id, %s, %s, %s, %s, %s, %s, %s FROM cash_movement_types t WHERE t.code = %s AND t.is_active = TRUE
            """, (int(account_id), delta, SOURCE, _id(entry_id), code, description, now, actor.user_id, type_code))
        if count != 1:
            raise RuntimeError(f"cash_movement_types {type_code} is missing (migration 037)")
        return int(movement_id)

    def post_scrap(self, *, kind, karat_id, weight, entry_id, code, notes, actor, now):
        """The counted Scrap movement of one karat: OUT for an issue, IN for a return. Physical grams, never converted."""
        delta = -weight if kind == ISSUE else weight
        type_code = SCRAP_TYPE[kind]
        movement_id, count = self._execute(
            """
            INSERT INTO old_gold_movements (movement_type_id, karat_id, weight_delta_grams, balance_effect, reference_type,
                reference_id, reference_code, notes, created_by_user_id, performed_by_user_id, received_by_employee_id,
                movement_code, occurred_at)
            SELECT t.id, %s, %s, 'COUNTED', %s, %s, %s, %s, %s, %s, %s, %s, %s FROM old_gold_movement_types t
            WHERE t.code = %s AND t.is_active = TRUE
            """, (int(karat_id), delta, SOURCE, _id(entry_id), code, notes, actor.user_id, actor.user_id, actor.employee_id,
                  type_code, now, type_code))
        if count != 1:
            raise RuntimeError(f"old_gold_movement_types {type_code} is missing (migration 037)")
        return int(movement_id)

    def insert_cash_leg(self, *, entry_id, amount, cash_account_id, cash_movement_id):
        self._execute("INSERT INTO market_trip_entry_cash (entry_id, amount, cash_account_id, cash_movement_id) VALUES (%s, %s, %s, %s)",
                      (_id(entry_id), amount, int(cash_account_id), int(cash_movement_id)))

    def insert_gold_leg(self, *, entry_id, karat_id, designation, weight, scrap_movement_id):
        self._execute("INSERT INTO market_trip_entry_gold (entry_id, karat_id, designation, weight_grams, scrap_movement_id) "
                      "VALUES (%s, %s, %s, %s, %s)", (_id(entry_id), int(karat_id), designation, weight, int(scrap_movement_id)))

    # ------------------------------------------------------------------ melting (039)
    def insert_melt(self, *, entry_id, workshop, assay_reference, gain_explanation, analysis):
        """The melt document with its immutable analytical snapshots (see services.market_trip_service.analyse)."""
        self._execute(
            """
            INSERT INTO market_trip_melts (entry_id, workshop, assay_reference, gain_explanation, input_weight_grams, nominal_pure_grams,
                output_weight_grams, known_output_pure_grams, weight_difference_grams, pure_complete, pure_difference_grams,
                nominal_eq21_grams, known_output_eq21_grams, eq21_difference_grams)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """, (_id(entry_id), workshop, assay_reference, gain_explanation, analysis["input_weight"], analysis["nominal_pure"],
                  analysis["output_weight"], analysis["known_output_pure"], analysis["weight_difference"],
                  1 if analysis["pure_complete"] else 0, analysis["pure_difference"], analysis["nominal_eq21"],
                  analysis["known_output_eq21"], analysis["eq21_difference"]))

    def insert_melt_input(self, *, entry_id, karat_id, weight, pure):
        self._execute("INSERT INTO market_trip_melt_inputs (entry_id, karat_id, weight_grams, pure_grams) VALUES (%s, %s, %s, %s)",
                      (_id(entry_id), int(karat_id), weight, pure))

    def insert_lot(self, *, entry_id, lot_no, kind, label, weight, fineness, pure):
        lot_id, _ = self._execute(
            "INSERT INTO market_trip_lots (entry_id, lot_no, lot_kind, label, weight_grams, fineness_permille, pure_grams) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s)", (_id(entry_id), int(lot_no), kind, label, weight, fineness, pure))
        return int(lot_id)

    def insert_lot_movement(self, *, lot_id, entry_id, movement_type, weight_delta):
        movement_id, _ = self._execute(
            "INSERT INTO market_trip_lot_movements (lot_id, entry_id, movement_type, weight_delta) VALUES (%s, %s, %s, %s)",
            (int(lot_id), _id(entry_id), movement_type, weight_delta))
        return int(movement_id)

    # ------------------------------------------------------------------ lot returns to the shop (040)
    def lock_lots(self, lot_ids):
        """Lock the lot rows in ascending id order (the order every writer of lot grams uses)."""
        ids = sorted({int(i) for i in lot_ids})
        if not ids:
            return []
        marks = ", ".join(["%s"] * len(ids))
        return self._fetch_all(
            f"SELECT l.id, l.entry_id, l.weight_grams, e.trip_id, e.entry_no, l.lot_no, l.lot_kind, l.label, l.fineness_permille FROM market_trip_lots l "
            f"JOIN market_trip_entries e ON e.id = l.entry_id WHERE l.id IN ({marks}) ORDER BY l.id FOR UPDATE OF l", tuple(ids))

    def insert_shop_lot_movement(self, *, lot_id, entry_id, movement_type, weight_delta, correction_id=None):
        movement_id, _ = self._execute(
            "INSERT INTO market_trip_lot_shop_movements (lot_id, entry_id, movement_type, weight_delta, correction_id) "
            "VALUES (%s, %s, %s, %s, %s)", (int(lot_id), _id(entry_id), movement_type, weight_delta,
                                            None if correction_id is None else _id(correction_id)))
        return int(movement_id)

    def melt_lot_ids(self, entry_id):
        return [int(r["id"]) for r in self._fetch_all("SELECT id FROM market_trip_lots WHERE entry_id = %s ORDER BY id", (_id(entry_id),))]

    def insert_lot_settlement_link(self, *, entry_id, settlement_id, supplier_id, lot, weight, movement_id):
        self._execute("""INSERT INTO market_trip_lot_settlements
            (entry_id, settlement_id, supplier_id, lot_id, origin_melt_entry_id, weight_grams, fineness_permille,
             custody_movement_id, weight_delta) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (_id(entry_id), _id(settlement_id), _id(supplier_id), int(lot["id"]), int(lot["entry_id"]), weight,
             lot["fineness_permille"], int(movement_id), -weight))

    def entry_holding(self, entry_id):
        """The intermediary holding document a LOT_HOLD / HOLD_RETURN entry posted (None for any other entry), with its party and lot."""
        return self._fetch_one(
            "SELECT k.entry_id, k.document_id, k.doc_kind, k.pool_id, k.lot_id, k.weight_grams, d.party_id, s.name AS party_name, "
            "d.fineness_permille, l.lot_no, l.lot_kind, l.label, oe.trip_id AS origin_trip_id, oe.entry_no AS origin_entry_no "
            "FROM market_trip_holding_links k JOIN intermediary_holding_documents d ON d.id = k.document_id "
            "JOIN suppliers s ON s.id = d.party_id JOIN market_trip_lots l ON l.id = k.lot_id "
            "JOIN market_trip_entries oe ON oe.id = l.entry_id WHERE k.entry_id = %s", (_id(entry_id),))

    def entry_lot_settlement(self, entry_id):
        return self._fetch_one("""SELECT x.*, s.name AS supplier_name, l.lot_no, l.lot_kind,
            oe.trip_id AS origin_trip_id, oe.entry_no AS origin_entry_no, g.target_karat_value,
            g.credited_weight_grams, g.credited_analytical_grams, g.rounding_difference_grams
            FROM market_trip_lot_settlements x JOIN supplier_gold_settlements g ON g.id=x.settlement_id
            JOIN suppliers s ON s.id=x.supplier_id JOIN market_trip_lots l ON l.id=x.lot_id
            JOIN market_trip_entries oe ON oe.id=x.origin_melt_entry_id WHERE x.entry_id=%s""", (_id(entry_id),))

    def insert_lot_issue(self, *, entry_id, lot_id, weight, custody_movement_id, shop_movement_id):
        self._execute(
            "INSERT INTO market_trip_lot_issues (entry_id, lot_id, weight_grams, custody_movement_id, shop_movement_id) "
            "VALUES (%s, %s, %s, %s, %s)", (_id(entry_id), int(lot_id), weight, int(custody_movement_id), int(shop_movement_id)))

    def entry_lot_issues(self, entry_id):
        """The stored lot lines of a LOT_ISSUE entry (what its reversal negates: the exact stored grams)."""
        return self._fetch_all(
            "SELECT li.lot_id, li.weight_grams, li.custody_movement_id, li.shop_movement_id, l.lot_no, l.entry_id AS melt_entry_id "
            "FROM market_trip_lot_issues li JOIN market_trip_lots l ON l.id = li.lot_id WHERE li.entry_id = %s ORDER BY li.lot_id",
            (_id(entry_id),))

    def lot_by_id(self, lot_id):
        return self._fetch_one("SELECT l.id, l.weight_grams, l.fineness_permille, l.lot_no, l.lot_kind, l.label, e.trip_id AS origin_trip_id, "
                               "e.entry_no FROM market_trip_lots l JOIN market_trip_entries e ON e.id = l.entry_id WHERE l.id = %s", (int(lot_id),))

    def insert_lot_return(self, *, entry_id, lot_id, weight, custody_movement_id, shop_movement_id):
        self._execute(
            "INSERT INTO market_trip_lot_returns (entry_id, lot_id, weight_grams, custody_movement_id, shop_movement_id) "
            "VALUES (%s, %s, %s, %s, %s)", (_id(entry_id), int(lot_id), weight, int(custody_movement_id), int(shop_movement_id)))

    def entry_lot_returns(self, entry_id):
        """The stored lot lines of a LOT_RETURN entry (what a reversal negates: the exact stored grams)."""
        return self._fetch_all(
            "SELECT lr.lot_id, lr.weight_grams, lr.custody_movement_id, lr.shop_movement_id, l.lot_no, l.entry_id AS melt_entry_id "
            "FROM market_trip_lot_returns lr JOIN market_trip_lots l ON l.id = lr.lot_id WHERE lr.entry_id = %s ORDER BY lr.lot_id",
            (_id(entry_id),))

    def shop_lot_balance(self, lot_id):
        """Grams of one lot currently held by the shop (a locking read for the reversal check)."""
        row = self._fetch_one("SELECT COALESCE(SUM(weight_delta), 0) AS grams FROM market_trip_lot_shop_movements WHERE lot_id = %s FOR SHARE",
                              (int(lot_id),))
        return row["grams"]

    def shop_lots(self):
        """The shop-side lot stock: one row per lot with grams in the shop. Separate from Scrap and inventory pieces."""
        return self._fetch_all(
            """
            SELECT l.id, l.entry_id AS melt_entry_id, e.trip_id, e.entry_no, l.lot_no, l.lot_kind, l.label, l.weight_grams AS original_weight,
                   l.fineness_permille, SUM(s.weight_delta) AS stock_grams
            FROM market_trip_lots l JOIN market_trip_entries e ON e.id = l.entry_id
            JOIN market_trip_lot_shop_movements s ON s.lot_id = l.id
            GROUP BY l.id, l.entry_id, e.trip_id, e.entry_no, l.lot_no, l.lot_kind, l.label, l.weight_grams, l.fineness_permille
            HAVING SUM(s.weight_delta) > 0 ORDER BY e.trip_id, e.entry_no, l.lot_no
            """)

    def custody_lots(self, trip_id, exclude_entry_id=None, *, lot_id=None):
        """The assayed lots CURRENTLY held by this trip's custody, with their OUTSTANDING grams. The location is derived from the effective
        movements of this trip's entries (a CREATE of its own melt, a SHOP_ISSUE of a reissued bar, a SHOP_RETURN out), never from the
        lot's origin: a bar reissued into this custody is listed here and not in its origin's. Movements of reversed entries do not count."""
        # Write callers lock the current trip and affected lot facts explicitly. Locking unrelated lot facts or the immutable
        # origin entry here would invert the ascending lot order of origin-melt reversal. Movement reads remain current/locking.
        return self._fetch_all(
            f"""
            SELECT l.id, l.entry_id, l.lot_no, l.lot_kind, l.label, l.weight_grams, l.fineness_permille, l.pure_grams, oe.entry_no,
                   oe.trip_id AS origin_trip_id, COALESCE(SUM(m.weight_delta), 0) AS outstanding_grams
            FROM market_trip_lots l
            JOIN market_trip_entries oe ON oe.id = l.entry_id
            JOIN market_trip_lot_movements m ON m.lot_id = l.id
            JOIN market_trip_entries me ON me.id = m.entry_id
            WHERE me.trip_id = %s AND me.id <> %s AND (%s IS NULL OR l.id = %s) AND {_not_reversed('me', current=True)}
            GROUP BY l.id, l.entry_id, l.lot_no, l.lot_kind, l.label, l.weight_grams, l.fineness_permille, l.pure_grams, oe.entry_no, oe.trip_id
            ORDER BY l.id FOR SHARE OF m, me
            """, (_id(trip_id), int(exclude_entry_id or 0), lot_id, lot_id))

    def lot_downstream(self, entry_id):
        """Current dependencies after the caller locks the melt's original lots; CREATE is never a dependency.

        Lock movement/consuming-entry rows on this connection, not an earlier RR snapshot or an origin-trip row.
        The caller already holds the current trip and lot locks in their established order.
        """
        return self._fetch_all(
            f"""
            SELECT DISTINCT me.id AS entry_id, me.trip_id, me.entry_no
            FROM market_trip_lots l JOIN market_trip_lot_movements m ON m.lot_id = l.id
            JOIN market_trip_entries me ON me.id = m.entry_id
            WHERE l.entry_id = %s AND m.movement_type <> '{CREATE_MOVEMENT}' AND {_not_reversed('me', current=True)}
            ORDER BY me.id FOR SHARE OF m, me
            """, (_id(entry_id),))

    def melts(self, trip_id):
        """Every melt of the trip - reversed ones too: the analysis is permanent - with inputs and lots."""
        rows = self._fetch_all(
            """
            SELECT mt.*, e.trip_id, e.entry_no, e.entry_date, e.notes, e.occurred_at, u.display_name AS actor_name,
                   r.id AS reversal_id, r.reason AS reversal_reason, r.occurred_at AS reversal_at
            FROM market_trip_melts mt JOIN market_trip_entries e ON e.id = mt.entry_id
            LEFT JOIN users u ON u.id = e.performed_by_user_id
            LEFT JOIN transaction_corrections r ON r.source_type = '""" + SOURCE + """' AND r.source_id = e.id
                 AND r.correction_type = 'FULL_REVERSAL'
            WHERE e.trip_id = %s ORDER BY e.entry_no
            """, (_id(trip_id),))
        for row in rows:
            row["inputs"] = self._fetch_all(
                "SELECT i.karat_id, k.karat_value, i.weight_grams, i.pure_grams FROM market_trip_melt_inputs i "
                "JOIN karats k ON k.id = i.karat_id WHERE i.entry_id = %s ORDER BY k.karat_value DESC", (int(row["entry_id"]),))
            row["lots"] = self._fetch_all(
                "SELECT id, lot_no, lot_kind, label, weight_grams, fineness_permille, pure_grams FROM market_trip_lots "
                "WHERE entry_id = %s ORDER BY lot_no", (int(row["entry_id"]),))
        return rows

    # ------------------------------------------------------------------ Logo heating (042)
    def insert_heat(self, *, entry_id, workshop, reference, gain_explanation, total_input, total_output):
        """The immutable heating header; the difference is stored beside the two weights it was derived from."""
        self._execute(
            "INSERT INTO market_trip_heats (entry_id, workshop, reference, gain_explanation, total_input_grams, total_output_grams, "
            "total_difference_grams) VALUES (%s, %s, %s, %s, %s, %s, %s)",
            (_id(entry_id), workshop, reference, gain_explanation, total_input, total_output, total_output - total_input))

    def insert_heat_line(self, *, entry_id, karat_id, input_weight, output_weight):
        self._execute(
            "INSERT INTO market_trip_heat_lines (entry_id, karat_id, designation, input_weight_grams, output_weight_grams, difference_grams) "
            "VALUES (%s, %s, 'LOGO', %s, %s, %s)",
            (_id(entry_id), int(karat_id), input_weight, output_weight, output_weight - input_weight))

    def heats(self, trip_id):
        """Every heating document of the trip - reversed ones too: the measurements are permanent - with its lines."""
        rows = self._fetch_all(
            """
            SELECT ht.*, e.trip_id, e.entry_no, e.entry_date, e.notes, e.occurred_at, u.display_name AS actor_name,
                   r.id AS reversal_id, r.reason AS reversal_reason, r.occurred_at AS reversal_at
            FROM market_trip_heats ht JOIN market_trip_entries e ON e.id = ht.entry_id
            LEFT JOIN users u ON u.id = e.performed_by_user_id
            LEFT JOIN transaction_corrections r ON r.source_type = '""" + SOURCE + """' AND r.source_id = e.id
                 AND r.correction_type = 'FULL_REVERSAL'
            WHERE e.trip_id = %s ORDER BY e.entry_no
            """, (_id(trip_id),))
        for row in rows:
            row["lines"] = self._fetch_all(
                "SELECT l.karat_id, k.karat_value, l.input_weight_grams, l.output_weight_grams, l.difference_grams FROM market_trip_heat_lines l "
                "JOIN karats k ON k.id = l.karat_id WHERE l.entry_id = %s ORDER BY k.karat_value DESC", (int(row["entry_id"]),))
        return rows

    def entry_heat_lines(self, entry_id):
        return self._fetch_all("SELECT karat_id, input_weight_grams, output_weight_grams FROM market_trip_heat_lines WHERE entry_id = %s "
                               "ORDER BY karat_id", (_id(entry_id),))

    def later_heat_entries(self, trip_id, after_entry_no, karat_ids):
        """Effective (not reversed) HEAT entries of the trip numbered after `after_entry_no` that carry a line of one of the karats.
        A reversal-guard decision under the trip lock: the nested reversal lookup is a CURRENT read (see `_not_reversed`)."""
        ids = sorted({int(k) for k in karat_ids})
        if not ids:
            return []
        marks = ", ".join(["%s"] * len(ids))
        return self._fetch_all(
            f"""
            SELECT DISTINCT e.id, e.trip_id, e.entry_no FROM market_trip_entries e JOIN market_trip_heat_lines h ON h.entry_id = e.id
            WHERE e.trip_id = %s AND e.entry_no > %s AND e.entry_kind = 'HEAT' AND h.karat_id IN ({marks}) AND {_not_reversed('e', current=True)}
            ORDER BY e.entry_no FOR SHARE OF e, h
            """, (_id(trip_id), int(after_entry_no), *ids))

    def later_logo_returns(self, trip_id, after_entry_no, karat_ids):
        """Effective RETURN entries of the trip numbered after `after_entry_no` that return LOGO gold of one of the karats.
        A reversal-guard decision under the trip lock: the nested reversal lookup is a CURRENT read (see `_not_reversed`)."""
        ids = sorted({int(k) for k in karat_ids})
        if not ids:
            return []
        marks = ", ".join(["%s"] * len(ids))
        return self._fetch_all(
            f"""
            SELECT DISTINCT e.id, e.trip_id, e.entry_no FROM market_trip_entries e JOIN market_trip_entry_gold g ON g.entry_id = e.id
            WHERE e.trip_id = %s AND e.entry_no > %s AND e.entry_kind = 'RETURN' AND g.designation = 'LOGO' AND g.karat_id IN ({marks})
              AND {_not_reversed('e', current=True)}
            ORDER BY e.entry_no FOR SHARE OF e, g
            """, (_id(trip_id), int(after_entry_no), *ids))

    # ------------------------------------------------------------------ custody (derived, read under the trip lock)
    def custody_cash(self, trip_id, exclude_entry_id=None, *, current=False):
        """(issued, returned, paid to suppliers) cash of the entries that were not reversed. A supplier payment made from custody
        consumes custody cash without any Main Safe movement. The entry and leg rows are locking (current) reads; the nested
        reversal lookup is a snapshot read unless `current=True`, which every DECISION made under the trip lock passes (a display
        read does not, so it takes no locks on correction rows)."""
        not_reversed = _not_reversed("e", current=current)
        row = self._fetch_one(
            f"""
            SELECT COALESCE(SUM(IF(e.entry_kind = 'ISSUE', c.amount, 0)), 0) AS issued,
                   COALESCE(SUM(IF(e.entry_kind = 'RETURN', c.amount, 0)), 0) AS returned
            FROM market_trip_entries e JOIN market_trip_entry_cash c ON c.entry_id = e.id
            WHERE e.trip_id = %s AND e.id <> %s AND {not_reversed} FOR SHARE
            """, (_id(trip_id), int(exclude_entry_id or 0)))
        paid = self._fetch_one(
            f"""
            SELECT COALESCE(SUM(p.amount), 0) AS paid
            FROM market_trip_entries e JOIN market_trip_supplier_payments p ON p.entry_id = e.id
            WHERE e.trip_id = %s AND e.id <> %s AND {not_reversed} FOR SHARE OF e, p
            """, (_id(trip_id), int(exclude_entry_id or 0)))
        return row["issued"], row["returned"], paid["paid"]

    # ------------------------------------------------------------------ supplier payments from custody (043)
    def suppliers(self):
        """Every SUPPLIER (an inactive one can still be paid; an intermediary-only party has no making account) with its current net making
        position (delta > 0: the shop owes it)."""
        return self._fetch_all(
            "SELECT s.id, s.name, s.is_active, s.karat_id, k.karat_value, COALESCE((SELECT SUM(m.amount_delta) FROM supplier_making_movements m "
            "WHERE m.supplier_id = s.id), 0) AS making_balance FROM suppliers s LEFT JOIN karats k ON k.id = s.karat_id "
            "WHERE s.is_supplier = 1 ORDER BY s.is_active DESC, s.name, s.id")

    def intermediaries(self):
        """Every party with the intermediary (مسوقجي) role, active ones first (a handover needs an ACTIVE one; the service re-checks it)."""
        return self._fetch_all("SELECT id, name, is_active, is_supplier FROM suppliers WHERE is_intermediary = 1 ORDER BY is_active DESC, name, id")

    def insert_supplier_payment_link(self, *, entry_id, payment_id, supplier_id, amount):
        self._execute("INSERT INTO market_trip_supplier_payments (entry_id, payment_id, supplier_id, amount) VALUES (%s, %s, %s, %s)",
                      (_id(entry_id), _id(payment_id), int(supplier_id), amount))

    def entry_supplier_payment(self, entry_id):
        """The payment a SUPPLIER_PAY entry made (None for any other entry)."""
        return self._fetch_one(
            "SELECT l.entry_id, l.payment_id, l.supplier_id, l.amount, s.name AS supplier_name FROM market_trip_supplier_payments l "
            "JOIN suppliers s ON s.id = l.supplier_id WHERE l.entry_id = %s", (_id(entry_id),))

    def payment_entry(self, payment_id):
        """The custody entry that made a supplier payment (None for a direct cash / bank payment), with its stored request facts."""
        return self._fetch_one(
            "SELECT l.entry_id, l.supplier_id, l.amount, e.trip_id, e.entry_no, e.request_hash, e.performed_by_user_id "
            "FROM market_trip_supplier_payments l JOIN market_trip_entries e ON e.id = l.entry_id WHERE l.payment_id = %s",
            (_id(payment_id),))

    # ------------------------------------------------------------------ supplier gold settlements from custody (046)
    def insert_gold_settlement_link(self, *, entry_id, settlement_id, supplier_id, karat_id, designation, weight):
        """The immutable link entry -> SG document with the consumed source line (the database keeps it equal to the document)."""
        self._execute(
            "INSERT INTO market_trip_gold_settlements (entry_id, settlement_id, supplier_id, karat_id, designation, weight_grams) "
            "VALUES (%s, %s, %s, %s, %s, %s)", (_id(entry_id), _id(settlement_id), int(supplier_id), int(karat_id), designation, weight))

    def entry_gold_settlement(self, entry_id):
        """The settlement a GOLD_SETTLE entry made (None for any other entry), with the document's stored figures."""
        return self._fetch_one(
            "SELECT l.entry_id, l.settlement_id, l.supplier_id, l.karat_id, k.karat_value, l.designation, l.weight_grams, s.name AS supplier_name, "
            "g.credited_weight_grams, g.credited_analytical_grams, g.rounding_difference_grams, g.target_karat_value "
            "FROM market_trip_gold_settlements l JOIN suppliers s ON s.id = l.supplier_id JOIN karats k ON k.id = l.karat_id "
            "JOIN supplier_gold_settlements g ON g.id = l.settlement_id WHERE l.entry_id = %s", (_id(entry_id),))

    def later_gold_settlements(self, trip_id, after_entry_no, karat_ids):
        """Effective (not reversed) GOLD_SETTLE entries of the trip numbered after `after_entry_no` that delivered gold of one of the karats.
        A reversal-guard decision under the trip lock: the nested reversal lookup is a CURRENT read (see `_not_reversed`)."""
        ids = sorted({int(k) for k in karat_ids})
        if not ids:
            return []
        marks = ", ".join(["%s"] * len(ids))
        return self._fetch_all(
            f"""
            SELECT DISTINCT e.id, e.trip_id, e.entry_no FROM market_trip_entries e JOIN market_trip_gold_settlements l ON l.entry_id = e.id
            WHERE e.trip_id = %s AND e.entry_no > %s AND e.entry_kind = 'GOLD_SETTLE' AND l.karat_id IN ({marks})
              AND {_not_reversed('e', current=True)}
            ORDER BY e.entry_no FOR SHARE OF e, l
            """, (_id(trip_id), int(after_entry_no), *ids))

    def custody_gold(self, trip_id, exclude_entry_id=None, *, current=False):
        """Per (karat, designation): grams issued, returned and consumed by melts, over the entries that were not reversed, plus the
        signed effective Logo heating differences (output - input) and the grams delivered to suppliers (046). A melt consumes
        MELT-designated custody only (`melted` is non-zero only on MELT), a heat changes LOGO custody only (`heat_delta` is non-zero only
        on LOGO) and a supplier settlement consumes LOGO custody only in this slice (`supplier_settled`). The arithmetic that combines
        these terms lives in services.custody_rules, the single definition every caller uses. The nested reversal lookup is a snapshot
        read unless `current=True`, which every DECISION made under the trip lock passes (see `custody_cash`)."""
        not_reversed = _not_reversed("e", current=current)
        legs = self._fetch_all(
            f"""
            SELECT g.karat_id, k.karat_value, g.designation,
                   COALESCE(SUM(IF(e.entry_kind = 'ISSUE', g.weight_grams, 0)), 0) AS issued,
                   COALESCE(SUM(IF(e.entry_kind = 'RETURN', g.weight_grams, 0)), 0) AS returned
            FROM market_trip_entries e JOIN market_trip_entry_gold g ON g.entry_id = e.id JOIN karats k ON k.id = g.karat_id
            WHERE e.trip_id = %s AND e.id <> %s AND {not_reversed}
            GROUP BY g.karat_id, k.karat_value, g.designation FOR SHARE OF e, g
            """, (_id(trip_id), int(exclude_entry_id or 0)))
        melted = self._fetch_all(
            f"""
            SELECT i.karat_id, k.karat_value, COALESCE(SUM(i.weight_grams), 0) AS melted
            FROM market_trip_entries e JOIN market_trip_melt_inputs i ON i.entry_id = e.id JOIN karats k ON k.id = i.karat_id
            WHERE e.trip_id = %s AND e.id <> %s AND {not_reversed}
            GROUP BY i.karat_id, k.karat_value FOR SHARE OF e, i
            """, (_id(trip_id), int(exclude_entry_id or 0)))
        heated = self._fetch_all(
            f"""
            SELECT h.karat_id, k.karat_value, COALESCE(SUM(h.output_weight_grams - h.input_weight_grams), 0) AS heat_delta
            FROM market_trip_entries e JOIN market_trip_heat_lines h ON h.entry_id = e.id JOIN karats k ON k.id = h.karat_id
            WHERE e.trip_id = %s AND e.id <> %s AND {not_reversed}
            GROUP BY h.karat_id, k.karat_value FOR SHARE OF e, h
            """, (_id(trip_id), int(exclude_entry_id or 0)))
        settled = self._fetch_all(
            f"""
            SELECT l.karat_id, k.karat_value, l.designation, COALESCE(SUM(l.weight_grams), 0) AS supplier_settled
            FROM market_trip_entries e JOIN market_trip_gold_settlements l ON l.entry_id = e.id JOIN karats k ON k.id = l.karat_id
            WHERE e.trip_id = %s AND e.id <> %s AND {not_reversed}
            GROUP BY l.karat_id, k.karat_value, l.designation FOR SHARE OF e, l
            """, (_id(trip_id), int(exclude_entry_id or 0)))
        rows = {(int(r["karat_id"]), r["designation"]): {**r, "melted": 0, "heat_delta": 0, "supplier_settled": 0} for r in legs}
        for h in heated:
            key = (int(h["karat_id"]), LOGO_DESIGNATION)
            row = rows.setdefault(key, {"karat_id": h["karat_id"], "karat_value": h["karat_value"], "designation": LOGO_DESIGNATION,
                                        "issued": 0, "returned": 0, "melted": 0, "heat_delta": 0, "supplier_settled": 0})
            row["heat_delta"] = h["heat_delta"]
        for m in melted:
            key = (int(m["karat_id"]), MELT_DESIGNATION)
            row = rows.setdefault(key, {"karat_id": m["karat_id"], "karat_value": m["karat_value"], "designation": MELT_DESIGNATION,
                                        "issued": 0, "returned": 0, "melted": 0, "heat_delta": 0, "supplier_settled": 0})
            row["melted"] = m["melted"]
        for s in settled:
            key = (int(s["karat_id"]), s["designation"])
            row = rows.setdefault(key, {"karat_id": s["karat_id"], "karat_value": s["karat_value"], "designation": s["designation"],
                                        "issued": 0, "returned": 0, "melted": 0, "heat_delta": 0, "supplier_settled": 0})
            row["supplier_settled"] = s["supplier_settled"]
        order = {MELT_DESIGNATION: 0, LOGO_DESIGNATION: 1, UNCLASSIFIED: 2}
        return sorted(rows.values(), key=lambda r: (-int(r["karat_value"]), order[r["designation"]]))

    # ------------------------------------------------------------------ history
    def entries(self, trip_id):
        rows = self._fetch_all(
            """
            SELECT e.id, e.trip_id, e.entry_no, e.entry_kind, e.entry_date, e.notes, e.occurred_at, u.display_name AS actor_name,
                   c.amount AS cash_amount, ca.name AS cash_account_name,
                   r.id AS reversal_id, r.reason AS reversal_reason, r.occurred_at AS reversal_at
            FROM market_trip_entries e
            LEFT JOIN users u ON u.id = e.performed_by_user_id
            LEFT JOIN market_trip_entry_cash c ON c.entry_id = e.id
            LEFT JOIN cash_accounts ca ON ca.id = c.cash_account_id
            LEFT JOIN transaction_corrections r ON r.source_type = '""" + SOURCE + """' AND r.source_id = e.id
                 AND r.correction_type = 'FULL_REVERSAL'
            WHERE e.trip_id = %s ORDER BY e.entry_no
            """, (_id(trip_id),))
        for row in rows:
            row["lot_returns"] = self._fetch_all(
                "SELECT lr.lot_id, lr.weight_grams, l.lot_no, l.lot_kind, l.fineness_permille, me.trip_id, me.entry_no AS melt_entry_no "
                "FROM market_trip_lot_returns lr JOIN market_trip_lots l ON l.id = lr.lot_id "
                "JOIN market_trip_entries me ON me.id = l.entry_id WHERE lr.entry_id = %s ORDER BY lr.lot_id", (int(row["id"]),))
            row["lot_issues"] = self._fetch_all(
                "SELECT li.lot_id, li.weight_grams, l.lot_no, l.lot_kind, l.fineness_permille, me.trip_id, me.entry_no AS melt_entry_no "
                "FROM market_trip_lot_issues li JOIN market_trip_lots l ON l.id = li.lot_id "
                "JOIN market_trip_entries me ON me.id = l.entry_id WHERE li.entry_id = %s ORDER BY li.lot_id", (int(row["id"]),))
            row["supplier_payment"] = self.entry_supplier_payment(row["id"])
            row["gold_settlement"] = self.entry_gold_settlement(row["id"])
            row["lot_settlement"] = self.entry_lot_settlement(row["id"])
            row["holding"] = self.entry_holding(row["id"])
            row["gold"] = self._fetch_all(
                "SELECT g.karat_id, k.karat_value, g.designation, g.weight_grams FROM market_trip_entry_gold g "
                "JOIN karats k ON k.id = g.karat_id WHERE g.entry_id = %s ORDER BY k.karat_value DESC, g.designation", (int(row["id"]),))
        return rows

    # ------------------------------------------------------------------ as a correction source
    def entry_source(self, entry_id, lock=False):
        """The entry with its trip status. With lock=True the entry row is locked (the caller holds the trip and account locks)."""
        return self._fetch_one(
            "SELECT e.*, t.status AS trip_status FROM market_trip_entries e JOIN market_trips t ON t.id = e.trip_id "
            "WHERE e.id = %s" + (" FOR UPDATE OF e" if lock else ""), (_id(entry_id),))

    def entry_cash_leg(self, entry_id):
        return self._fetch_one("SELECT amount, cash_account_id, cash_movement_id FROM market_trip_entry_cash WHERE entry_id = %s",
                               (_id(entry_id),))

    def entry_gold_legs(self, entry_id):
        """The stored gold of an entry with the signed grams its Scrap movement posted (what a reversal negates)."""
        return self._fetch_all(
            "SELECT g.karat_id, k.karat_value, g.designation, g.weight_grams, m.weight_delta_grams FROM market_trip_entry_gold g "
            "JOIN karats k ON k.id = g.karat_id JOIN old_gold_movements m ON m.id = g.scrap_movement_id "
            "WHERE g.entry_id = %s ORDER BY g.karat_id", (_id(entry_id),))

    # ------------------------------------------------------------------ audit
    def audit(self, *, actor, event_type, entity_id, reference, description, metadata, now):
        self._execute(
            "INSERT INTO audit_log (user_id, event_type, entity_type, entity_id, entity_reference, description, "
            "metadata_json, occurred_at) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
            (actor.user_id, event_type, "MARKET_TRIP", int(entity_id), reference, description,
             json.dumps(metadata, ensure_ascii=False, default=str), now))
