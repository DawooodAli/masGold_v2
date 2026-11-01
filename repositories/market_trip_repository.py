"""Market-trip custody (037, melting 039, lot returns 040, lot reissue 041, Logo heating 042, supplier payments 043, supplier gold settlements 046): trips, their insert-only entries, and the cash / Scrap movements those entries post.

All writes use the caller's transaction and only INSERT (the trip header's `status` is the one updated column; every change
of it is an insert-only event). Custody is never stored: it is derived from the entries that were not reversed.
A reversed entry is one that has a FULL_REVERSAL row in transaction_corrections (source_type MARKET_TRIP_ENTRY)."""
import json
from decimal import Decimal

from mysql.connector import Error as MySqlError

from repositories.base_repository import BaseRepository
from repositories.invoice_repository import _id
from repositories.request_keys import require_no_marketer_key

SOURCE = "MARKET_TRIP_ENTRY"            # transaction_corrections.source_type and the cash / Scrap reference_type
MAIN_SAFE = "MAIN_SAFE"
ISSUE, RETURN, MELT, LOT_RETURN, LOT_ISSUE, HEAT = "ISSUE", "RETURN", "MELT", "LOT_RETURN", "LOT_ISSUE", "HEAT"
SUPPLIER_PAY = "SUPPLIER_PAY"          # a supplier making payment made with custody cash (043)
GOLD_SETTLE = "GOLD_SETTLE"            # custody gold delivered to a supplier against its gold account (046, LOGO only in this slice)
LOT_SETTLE, LOT_SETTLE_MOVEMENT = "LOT_SETTLE", "SUPPLIER_SETTLE"
GOLD_BUY = "GOLD_BUY"          # 054: cash spent in custody on scrap bought in the market
GOLD_SALE, SALE_MOVEMENT = "GOLD_SALE", "SALE_OUT"      # 053: custody gold sold for cash (aggregate line or a whole lot); the lot ledger movement
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


def _share(current, of=""):
    """The locking clause of a custody read: a DECISION (`current=True`) locks its rows; a DISPLAY read (`current=False`) takes NO lock at all, outer query or nested lookup."""
    if not current:
        return ""
    return f" FOR SHARE OF {of}" if of else " FOR SHARE"


def trip_code(trip_id):
    """MT + 6 digits. Distinct from S, SP, SM, DP, DR, CS, FO, TR, RV, FC, RT, EX."""
    return f"MT{int(trip_id):06d}"


def entry_code(trip_id, entry_no):
    """The reference of one entry: its trip code and its number inside the trip (MT000001-2)."""
    return f"{trip_code(trip_id)}-{int(entry_no)}"


def _gold_rows(legs, held, heated, melted, settled, sold, bought, settle=(), settle_in=()):
    """The per-(karat, designation) custody rows of ONE trip from the derived sums of its entries that were not reversed. Pure Python, shared by the locking
    decision read `custody_gold` and the display aggregation `custody_gold_by_trip`, so both always combine the terms identically (the arithmetic itself lives in
    services.custody_rules)."""
    rows = {(int(r["karat_id"]), r["designation"]): {**r, "melted": 0, "heat_delta": 0, "supplier_settled": 0} for r in legs}
    for h in held:
        key = (int(h["karat_id"]), h["designation"])
        row = rows.setdefault(key, {"karat_id": h["karat_id"], "karat_value": h["karat_value"], "designation": h["designation"],
                                    "issued": 0, "returned": 0, "melted": 0, "heat_delta": 0, "supplier_settled": 0})
        row["held_out"], row["held_back"] = h["held_out"], h["held_back"]
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
    for s in sold:                                                           # 053: sold for cash (an aggregate line only; a lot sale is a lot movement)
        key = (int(s["karat_id"]), s["designation"])
        row = rows.setdefault(key, {"karat_id": s["karat_id"], "karat_value": s["karat_value"], "designation": s["designation"],
                                    "issued": 0, "returned": 0, "melted": 0, "heat_delta": 0, "supplier_settled": 0})
        row["sold"] = s["sold"]
    for b in bought:                                                         # 054: bought for cash (scrap lines of GOLD_BUY entries)
        key = (int(b["karat_id"]), b["designation"])
        row = rows.setdefault(key, {"karat_id": b["karat_id"], "karat_value": b["karat_value"], "designation": b["designation"],
                                    "issued": 0, "returned": 0, "melted": 0, "heat_delta": 0, "supplier_settled": 0})
        row["purchased"] = b["purchased"]
    for r in settle:                                                         # 060: grams that left the RAW pool of the settlement (UNCLASSIFIED gold only)
        key = (int(r["karat_id"]), UNCLASSIFIED)
        row = rows.setdefault(key, {"karat_id": r["karat_id"], "karat_value": r["karat_value"], "designation": UNCLASSIFIED,
                                    "issued": 0, "returned": 0, "melted": 0, "heat_delta": 0, "supplier_settled": 0})
        row["settle_out"] = r["settle_out"]
    for r in settle_in:                                                      # 062: grams classified as LOGO (the unmelted remainder), LOGO rows only
        key = (int(r["karat_id"]), LOGO_DESIGNATION)
        row = rows.setdefault(key, {"karat_id": r["karat_id"], "karat_value": r["karat_value"], "designation": LOGO_DESIGNATION,
                                    "issued": 0, "returned": 0, "melted": 0, "heat_delta": 0, "supplier_settled": 0})
        row["settle_in"] = r["settle_in"]
    order = {MELT_DESIGNATION: 0, LOGO_DESIGNATION: 1, UNCLASSIFIED: 2}
    return sorted(rows.values(), key=lambda r: (-int(r["karat_value"]), order[r["designation"]]))


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
        """A new OPEN trip (every trip is a plain custody; the accounting_mode column keeps its LEGACY default)."""
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

    def events(self, trip_id, *, newest_first=False):
        """The trip's status events. `newest_first` is the DISPLAY order (`occurred_at DESC, id DESC`); the default keeps the chronological one."""
        order = "e.occurred_at DESC, e.id DESC" if newest_first else "e.id"
        return self._fetch_all(
            "SELECT e.id, e.event_type, e.reason, e.occurred_at, u.display_name AS actor_name FROM market_trip_status_events e "
            "LEFT JOIN users u ON u.id = e.performed_by_user_id WHERE e.trip_id = %s ORDER BY " + order, (_id(trip_id),))

    def trips(self, limit=100):
        return self._fetch_all(
            """
            SELECT t.id, t.status, t.accounting_mode, t.notes, t.created_at, t.status_changed_at,
                   (SELECT COUNT(*) FROM market_trip_entries e WHERE e.trip_id = t.id) AS entry_count
            FROM market_trips t ORDER BY t.id DESC LIMIT %s
            """, (int(limit),))

    def trip_page(self, status, page, page_size):
        """ONE page of the trips of a status. The filter (status) and the order are applied in SQL BEFORE the limit, so no trip is ever hidden behind a cap:
        OPEN: `created_at DESC, id DESC`; CLOSED (the archive): `status_changed_at DESC, id DESC` (the closing time; a reopened trip is OPEN again and
        returns to its `created_at` position). One extra row is read so the caller knows whether a next page exists (it is returned, not hidden)."""
        order = "t.status_changed_at DESC, t.id DESC" if status == "CLOSED" else "t.created_at DESC, t.id DESC"
        return self._fetch_all(
            f"""
            SELECT t.id, t.status, t.accounting_mode, t.notes, t.created_at, t.status_changed_at,
                   (SELECT COUNT(*) FROM market_trip_entries e WHERE e.trip_id = t.id) AS entry_count
            FROM market_trips t WHERE t.status = %s ORDER BY {order} LIMIT %s OFFSET %s
            """, (status, int(page_size) + 1, (int(page) - 1) * int(page_size)))

    def all_trip_ids(self):
        """EVERY trip (id, status), oldest first, with no limit and no per-trip document history: the consolidated gold position derives each trip's
        outstanding custody from the ledger and must never silently drop an older trip (`trips()` is the paged list of the custody screens)."""
        return self._fetch_all("SELECT t.id, t.status FROM market_trips t ORDER BY t.id")

    # ------------------------------------------------------------------ entries
    def by_request(self, key):
        return self._fetch_one("SELECT * FROM market_trip_entries WHERE request_key = %s FOR UPDATE", (key,))

    def next_entry_no(self, trip_id):
        """Read under the trip lock: every writer of this trip's entries holds it."""
        row = self._fetch_one("SELECT COALESCE(MAX(entry_no), 0) + 1 AS n FROM market_trip_entries WHERE trip_id = %s FOR SHARE",
                              (_id(trip_id),))
        return int(row["n"])

    def insert_entry(self, *, trip_id, entry_no, kind, entry_date, notes, key, digest, actor, now):
        require_no_marketer_key(self._fetch_one, key)                    # 055: a marketer document owns this key -> REQUEST_CONFLICT, nothing is written
        entry_id, _ = self._execute(
            f"""
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

    def entry_logo_holding(self, entry_id):
        """The master-karat LOGO document a LOGO_HOLD / HOLD_BACK entry posted (None for any other entry) - 049."""
        return self._fetch_one(
            "SELECT k.entry_id, k.document_id, k.doc_kind, k.pool_id, k.karat_id, kk.karat_value, k.weight_grams, d.party_id, s.name AS party_name "
            "FROM market_trip_logo_hold_links k JOIN logo_holding_documents d ON d.id = k.document_id JOIN suppliers s ON s.id = d.party_id "
            "JOIN karats kk ON kk.id = k.karat_id WHERE k.entry_id = %s", (_id(entry_id),))

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

    def custody_lots(self, trip_id, exclude_entry_id=None, *, lot_id=None, current=True):
        """The assayed lots CURRENTLY held by this trip's custody, with their OUTSTANDING grams. The location is derived from the effective
        movements of this trip's entries (a CREATE of its own melt, a SHOP_ISSUE of a reissued bar, a SHOP_RETURN out), never from the
        lot's origin: a bar reissued into this custody is listed here and not in its origin's. Movements of reversed entries do not count."""
        # Write callers lock the current trip and affected lot facts explicitly. Locking unrelated lot facts or the immutable
        # origin entry here would invert the ascending lot order of origin-melt reversal. Movement reads are current/locking in the DECISION form (`current=True`, the default) and plain snapshot reads in the DISPLAY form.
        return self._fetch_all(
            f"""
            SELECT l.id, l.entry_id, l.lot_no, l.lot_kind, l.label, l.weight_grams, l.fineness_permille, l.pure_grams, oe.entry_no,
                   oe.trip_id AS origin_trip_id, COALESCE(SUM(m.weight_delta), 0) AS outstanding_grams
            FROM market_trip_lots l
            JOIN market_trip_entries oe ON oe.id = l.entry_id
            JOIN market_trip_lot_movements m ON m.lot_id = l.id
            JOIN market_trip_entries me ON me.id = m.entry_id
            WHERE me.trip_id = %s AND me.id <> %s AND (%s IS NULL OR l.id = %s) AND {_not_reversed('me', current=current)}
            GROUP BY l.id, l.entry_id, l.lot_no, l.lot_kind, l.label, l.weight_grams, l.fineness_permille, l.pure_grams, oe.entry_no, oe.trip_id
            ORDER BY l.id{_share(current, 'm, me')}
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

    def melts(self, trip_id, *, newest_first=False):
        """Every melt of the trip - reversed ones too: the analysis is permanent - with inputs and lots."""
        order = "e.occurred_at DESC, e.id DESC" if newest_first else "e.entry_no"
        rows = self._fetch_all(
            """
            SELECT mt.*, e.trip_id, e.entry_no, e.entry_date, e.notes, e.occurred_at, u.display_name AS actor_name,
                   r.id AS reversal_id, r.reason AS reversal_reason, r.occurred_at AS reversal_at
            FROM market_trip_melts mt JOIN market_trip_entries e ON e.id = mt.entry_id
            LEFT JOIN users u ON u.id = e.performed_by_user_id
            LEFT JOIN transaction_corrections r ON r.source_type = '""" + SOURCE + """' AND r.source_id = e.id
                 AND r.correction_type = 'FULL_REVERSAL'
            WHERE e.trip_id = %s ORDER BY """ + order + """
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

    def heats(self, trip_id, *, newest_first=False):
        """Every heating document of the trip - reversed ones too: the measurements are permanent - with its lines."""
        order = "e.occurred_at DESC, e.id DESC" if newest_first else "e.entry_no"
        rows = self._fetch_all(
            """
            SELECT ht.*, e.trip_id, e.entry_no, e.entry_date, e.notes, e.occurred_at, u.display_name AS actor_name,
                   r.id AS reversal_id, r.reason AS reversal_reason, r.occurred_at AS reversal_at
            FROM market_trip_heats ht JOIN market_trip_entries e ON e.id = ht.entry_id
            LEFT JOIN users u ON u.id = e.performed_by_user_id
            LEFT JOIN transaction_corrections r ON r.source_type = '""" + SOURCE + """' AND r.source_id = e.id
                 AND r.correction_type = 'FULL_REVERSAL'
            WHERE e.trip_id = %s ORDER BY """ + order + """
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
    def custody_cash(self, trip_id, exclude_entry_id=None, *, current=True):
        """(issued, returned, paid to suppliers) cash of the entries that were not reversed. A supplier payment made from custody
        consumes custody cash without any Main Safe movement. `current=True` (the default, so a caller that forgets stays safe) is the DECISION form: the entry
        and leg rows are locking reads and so is the nested reversal lookup. `current=False` is the DISPLAY form: plain snapshot reads, no locking clause anywhere."""
        not_reversed = _not_reversed("e", current=current)
        bank_trades = self.has_trade_bank_schema()

        def custody_only(alias):                                           # 091: a purchase / sale paid or received through a bank account never touches the custody cash
            return f" AND {alias}.payment_method = 'CUSTODY'" if bank_trades else ""
        row = self._fetch_one(
            f"""
            SELECT COALESCE(SUM(IF(e.entry_kind = 'ISSUE', c.amount, 0)), 0) AS issued,
                   COALESCE(SUM(IF(e.entry_kind = 'RETURN', c.amount, 0)), 0) AS returned
            FROM market_trip_entries e JOIN market_trip_entry_cash c ON c.entry_id = e.id
            WHERE e.trip_id = %s AND e.id <> %s AND {not_reversed}{_share(current)}
            """, (_id(trip_id), int(exclude_entry_id or 0)))
        paid = self._fetch_one(
            f"""
            SELECT COALESCE(SUM(p.amount), 0) AS paid
            FROM market_trip_entries e JOIN market_trip_supplier_payments p ON p.entry_id = e.id
            WHERE e.trip_id = %s AND e.id <> %s AND {not_reversed}{_share(current, 'e, p')}
            """, (_id(trip_id), int(exclude_entry_id or 0)))
        sold = self._fetch_one(
            f"""
            SELECT COALESCE(SUM(s.total_price), 0) AS proceeds
            FROM market_trip_entries e JOIN market_trip_gold_sales s ON s.entry_id = e.id
            WHERE e.trip_id = %s AND e.id <> %s AND {not_reversed}{custody_only('s')}{_share(current, 'e, s')}
            """, (_id(trip_id), int(exclude_entry_id or 0)))
        bought = self._fetch_one(
            f"""
            SELECT COALESCE(SUM(b.total_price), 0) AS spent
            FROM market_trip_entries e JOIN market_trip_gold_buys b ON b.entry_id = e.id
            WHERE e.trip_id = %s AND e.id <> %s AND {not_reversed}{custody_only('b')}{_share(current, 'e, b')}
            """, (_id(trip_id), int(exclude_entry_id or 0)))
        base = (row["issued"], row["returned"], paid["paid"], sold["proceeds"], bought["spent"])
        has_settle, has_fee = self.has_cash_settle_schema(), self.has_bar_fee_schema()
        if not has_settle and not has_fee:
            return base
        settled = {"settled": 0}
        if has_settle:
            settled = self._fetch_one(                                      # 064: expense, bank deposit and write-off consume custody cash (no Main Safe movement)
                f"""
                SELECT COALESCE(SUM(s.amount), 0) AS settled
                FROM market_trip_entries e JOIN market_trip_settle_cash s ON s.entry_id = e.id
                WHERE e.trip_id = %s AND e.id <> %s AND {not_reversed}{_share(current, 'e, s')}
                """, (_id(trip_id), int(exclude_entry_id or 0)))
        if not has_fee:
            return base + (settled["settled"],)
        fees = self._fetch_one(                                             # 080: a bar fee received in cash enters custody cash (no Main Safe movement)
            f"""
            SELECT COALESCE(SUM(f.amount), 0) AS fee
            FROM market_trip_entries e JOIN market_trip_bar_fees f ON f.entry_id = e.id
            WHERE e.trip_id = %s AND e.id <> %s AND {not_reversed}{_share(current, 'e, f')}
            """, (_id(trip_id), int(exclude_entry_id or 0)))
        return base + (settled["settled"], fees["fee"])

    # ------------------------------------------------------------------ bar fee (080)
    def has_bar_fee_schema(self):
        """True when migration 080 is present (the bar fee document exists). Asked once per repository object."""
        if not hasattr(self, "_bar_fee"):
            row = self._fetch_one("SELECT COUNT(*) AS n FROM information_schema.TABLES WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'market_trip_bar_fees'")
            self._bar_fee = bool(row and int(row["n"]))
        return self._bar_fee

    def insert_bar_fee(self, *, entry_id, party_id, party_kind, amount, making_movement_id, reference):
        self._execute("INSERT INTO market_trip_bar_fees (entry_id, party_id, party_kind, amount, making_movement_id, reference) VALUES (%s, %s, %s, %s, %s, %s)",
                      (_id(entry_id), int(party_id), party_kind, amount, making_movement_id, reference))

    def entry_bar_fee(self, entry_id):
        """The fee a ST_BAR_FEE entry recorded (None for any other entry or a database before 080)."""
        if not self.has_bar_fee_schema():
            return None
        return self._fetch_one(
            "SELECT f.entry_id, f.party_id, f.party_kind, f.amount, f.making_movement_id, f.reference, s.name AS party_name "
            "FROM market_trip_bar_fees f JOIN suppliers s ON s.id = f.party_id WHERE f.entry_id = %s", (_id(entry_id),))

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

    # ------------------------------------------------------------------ cash gold purchase (054)
    def insert_gold_buy(self, *, entry_id, vendor_kind, vendor_party_id, vendor_name, vendor_key, total, reference_total, deviation, acknowledged,
                        buy_date, reference, bank_account_id=None, bank_movement_id=None):
        """The immutable purchase document: the vendor identity (mandatory), the authoritative total and the price-warning evidence."""
        columns, values, extra = "", "", ()
        if self.has_trade_bank_schema():
            columns, values = ", payment_method, bank_account_id, bank_movement_id", ", %s, %s, %s"
            extra = ("BANK" if bank_account_id is not None else "CUSTODY", bank_account_id, bank_movement_id)
        self._execute(
            f"""INSERT INTO market_trip_gold_buys (entry_id, vendor_kind, vendor_party_id, vendor_name, vendor_name_key, total_price, reference_total,
               deviation_percent, price_warning_acknowledged, buy_date, reference{columns}) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s{values})""",
            (_id(entry_id), vendor_kind, vendor_party_id, vendor_name, vendor_key, total, reference_total, deviation, 1 if acknowledged else 0,
             buy_date, reference or None) + extra)

    def insert_gold_buy_line(self, *, entry_id, line_no, karat_id, designation, weight, price):
        self._execute("INSERT INTO market_trip_gold_buy_lines (entry_id, line_no, karat_id, designation, weight_grams, line_price) "
                      "VALUES (%s, %s, %s, %s, %s, %s)", (_id(entry_id), int(line_no), int(karat_id), designation, weight, price))

    def entry_gold_buy(self, entry_id):
        """The purchase a GOLD_BUY entry made (None for any other entry): the document and its lines."""
        head = self._fetch_one("SELECT * FROM market_trip_gold_buys WHERE entry_id = %s", (_id(entry_id),))
        if head is None:
            return None
        head["lines"] = self._fetch_all(
            "SELECT l.line_no, l.karat_id, k.karat_value, l.designation, l.weight_grams, l.line_price FROM market_trip_gold_buy_lines l "
            "JOIN karats k ON k.id = l.karat_id WHERE l.entry_id = %s ORDER BY l.line_no", (_id(entry_id),))
        return head

    # ------------------------------------------------------------------ sale of custody gold (053)
    def buyers(self):
        """Every party that may be named as a buyer (a supplier and / or an intermediary), active ones first. The sale needs no role."""
        return self._fetch_all("SELECT id, name, is_active, is_supplier, is_intermediary FROM suppliers ORDER BY is_active DESC, name, id")

    def party(self, party_id, lock=False):
        return self._fetch_one("SELECT id, name, is_active, is_supplier, is_intermediary FROM suppliers WHERE id = %s"
                               + (" FOR SHARE" if lock else ""), (int(party_id),))

    def insert_gold_sale(self, *, entry_id, source_kind, karat_id, designation, lot, weight, movement_id, total, per_gram, basis, buyer_kind,
                         buyer_party_id, buyer_name, reference_price, deviation, acknowledged, sale_date, reference, bank_account_id=None, bank_movement_id=None):
        """The immutable sale document. An aggregate line states (karat, designation); a whole lot states the lot, its stored assay and
        the exact SALE_OUT movement (the database ties them together)."""
        columns, values, extra = "", "", ()
        if self.has_trade_bank_schema():
            columns, values = ", payment_method, bank_account_id, bank_movement_id", ", %s, %s, %s"
            extra = ("BANK" if bank_account_id is not None else "CUSTODY", bank_account_id, bank_movement_id)
        self._execute(
            f"""INSERT INTO market_trip_gold_sales
               (entry_id, source_kind, karat_id, designation, lot_id, fineness_permille, custody_movement_id, movement_type, weight_grams,
                weight_delta, total_price, price_per_gram, price_basis, buyer_kind, buyer_party_id, buyer_name, reference_price_per_gram,
                deviation_percent, price_warning_acknowledged, sale_date, reference{columns})
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s{values})""",
            (_id(entry_id), source_kind, karat_id, designation, None if lot is None else int(lot["id"]),
             None if lot is None else lot["fineness_permille"], None if movement_id is None else int(movement_id),
             None if lot is None else SALE_MOVEMENT, weight, None if lot is None else -weight, total, per_gram, basis, buyer_kind,
             buyer_party_id, buyer_name, reference_price, deviation, 1 if acknowledged else 0, sale_date, reference or None) + extra)

    def entry_gold_sale(self, entry_id):
        """The sale a GOLD_SALE entry made (None for any other entry), with the source facts and the buyer."""
        return self._fetch_one(
            """SELECT g.*, k.karat_value, l.lot_no, l.lot_kind, oe.trip_id AS origin_trip_id, oe.entry_no AS origin_entry_no
               FROM market_trip_gold_sales g LEFT JOIN karats k ON k.id = g.karat_id LEFT JOIN market_trip_lots l ON l.id = g.lot_id
               LEFT JOIN market_trip_entries oe ON oe.id = l.entry_id WHERE g.entry_id = %s""", (_id(entry_id),))

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

    def custody_gold(self, trip_id, exclude_entry_id=None, *, current=True):
        """Per (karat, designation): grams issued, returned and consumed by melts, over the entries that were not reversed, plus the
        signed effective Logo heating differences (output - input) and the grams delivered to suppliers (046). A melt consumes
        MELT-designated custody only (`melted` is non-zero only on MELT), a heat changes LOGO custody only (`heat_delta` is non-zero only
        on LOGO) and a supplier settlement consumes LOGO custody only in this slice (`supplier_settled`). The arithmetic that combines
        these terms lives in services.custody_rules, the single definition every caller uses. The nested reversal lookup is a snapshot
        read unless `current=True` (the default: DECISION form, outer rows and nested lookup locked); `current=False` is the lock-free DISPLAY form (see `custody_cash`)."""
        not_reversed = _not_reversed("e", current=current)
        legs = self._fetch_all(
            f"""
            SELECT g.karat_id, k.karat_value, g.designation,
                   COALESCE(SUM(IF(e.entry_kind = 'ISSUE', g.weight_grams, 0)), 0) AS issued,
                   COALESCE(SUM(IF(e.entry_kind = 'RETURN', g.weight_grams, 0)), 0) AS returned
            FROM market_trip_entries e JOIN market_trip_entry_gold g ON g.entry_id = e.id JOIN karats k ON k.id = g.karat_id
            WHERE e.trip_id = %s AND e.id <> %s AND {not_reversed}
            GROUP BY g.karat_id, k.karat_value, g.designation{_share(current, 'e, g')}
            """, (_id(trip_id), int(exclude_entry_id or 0)))
        melted = self._fetch_all(
            f"""
            SELECT i.karat_id, k.karat_value, COALESCE(SUM(i.weight_grams), 0) AS melted
            FROM market_trip_entries e JOIN market_trip_melt_inputs i ON i.entry_id = e.id JOIN karats k ON k.id = i.karat_id
            WHERE e.trip_id = %s AND e.id <> %s AND {not_reversed}
            GROUP BY i.karat_id, k.karat_value{_share(current, 'e, i')}
            """, (_id(trip_id), int(exclude_entry_id or 0)))
        heated = self._fetch_all(
            f"""
            SELECT h.karat_id, k.karat_value, COALESCE(SUM(h.output_weight_grams - h.input_weight_grams), 0) AS heat_delta
            FROM market_trip_entries e JOIN market_trip_heat_lines h ON h.entry_id = e.id JOIN karats k ON k.id = h.karat_id
            WHERE e.trip_id = %s AND e.id <> %s AND {not_reversed}
            GROUP BY h.karat_id, k.karat_value{_share(current, 'e, h')}
            """, (_id(trip_id), int(exclude_entry_id or 0)))
        settled = self._fetch_all(
            f"""
            SELECT l.karat_id, k.karat_value, l.designation, COALESCE(SUM(l.weight_grams), 0) AS supplier_settled
            FROM market_trip_entries e JOIN market_trip_gold_settlements l ON l.entry_id = e.id JOIN karats k ON k.id = l.karat_id
            WHERE e.trip_id = %s AND e.id <> %s AND {not_reversed}
            GROUP BY l.karat_id, k.karat_value, l.designation{_share(current, 'e, l')}
            """, (_id(trip_id), int(exclude_entry_id or 0)))
        held = self._fetch_all(
            f"""
            SELECT l.karat_id, k.karat_value, l.designation,
                   COALESCE(SUM(IF(e.entry_kind = 'LOGO_HOLD', l.weight_grams, 0)), 0) AS held_out,
                   COALESCE(SUM(IF(e.entry_kind = 'HOLD_BACK', l.weight_grams, 0)), 0) AS held_back
            FROM market_trip_entries e JOIN market_trip_logo_hold_links l ON l.entry_id = e.id JOIN karats k ON k.id = l.karat_id
            WHERE e.trip_id = %s AND e.id <> %s AND {not_reversed}
            GROUP BY l.karat_id, k.karat_value, l.designation{_share(current, 'e, l')}
            """, (_id(trip_id), int(exclude_entry_id or 0)))
        sold = self._fetch_all(
            f"""
            SELECT s.karat_id, k.karat_value, s.designation, COALESCE(SUM(s.weight_grams), 0) AS sold
            FROM market_trip_entries e JOIN market_trip_gold_sales s ON s.entry_id = e.id JOIN karats k ON k.id = s.karat_id
            WHERE e.trip_id = %s AND e.id <> %s AND s.source_kind = 'CUSTODY_GOLD' AND {not_reversed}
            GROUP BY s.karat_id, k.karat_value, s.designation{_share(current, 'e, s')}
            """, (_id(trip_id), int(exclude_entry_id or 0)))
        bought = self._fetch_all(
            f"""
            SELECT l.karat_id, k.karat_value, l.designation, COALESCE(SUM(l.weight_grams), 0) AS purchased
            FROM market_trip_entries e JOIN market_trip_gold_buy_lines l ON l.entry_id = e.id JOIN karats k ON k.id = l.karat_id
            WHERE e.trip_id = %s AND e.id <> %s AND {not_reversed}
            GROUP BY l.karat_id, k.karat_value, l.designation{_share(current, 'e, l')}
            """, (_id(trip_id), int(exclude_entry_id or 0)))
        return _gold_rows(legs, held, heated, melted, settled, sold, bought, self.settle_raw_out(trip_id, exclude_entry_id, current=current),
                          self.settle_logo_in(trip_id, exclude_entry_id, current=current))

    # ------------------------------------------------------------------ display aggregation (Gold Position): NO locks, a fixed number of queries
    def custody_gold_by_trip(self):
        """DISPLAY ONLY. The same per-(karat, designation) custody rows as `custody_gold`, for EVERY trip at once: seven aggregate queries (issued / returned, melted,
        heating differences, supplier settlements, intermediary handovers, sales, purchases), each GROUPed BY trip, whatever the number of trips. Plain
        non-locking reads (no FOR SHARE) with the snapshot form of the reversal lookup, so inside one REPEATABLE READ transaction every figure comes from the SAME
        snapshot; the locking `custody_gold` stays the only read of a decision. Returns {trip_id: rows}."""
        nr = _not_reversed("e")
        legs = self._fetch_all(
            f"""
            SELECT e.trip_id, g.karat_id, k.karat_value, g.designation,
                   COALESCE(SUM(IF(e.entry_kind = 'ISSUE', g.weight_grams, 0)), 0) AS issued,
                   COALESCE(SUM(IF(e.entry_kind = 'RETURN', g.weight_grams, 0)), 0) AS returned
            FROM market_trip_entries e JOIN market_trip_entry_gold g ON g.entry_id = e.id JOIN karats k ON k.id = g.karat_id
            WHERE {nr}
            GROUP BY e.trip_id, g.karat_id, k.karat_value, g.designation
            """)
        melted = self._fetch_all(
            f"""
            SELECT e.trip_id, i.karat_id, k.karat_value, COALESCE(SUM(i.weight_grams), 0) AS melted
            FROM market_trip_entries e JOIN market_trip_melt_inputs i ON i.entry_id = e.id JOIN karats k ON k.id = i.karat_id
            WHERE {nr}
            GROUP BY e.trip_id, i.karat_id, k.karat_value
            """)
        heated = self._fetch_all(
            f"""
            SELECT e.trip_id, h.karat_id, k.karat_value, COALESCE(SUM(h.output_weight_grams - h.input_weight_grams), 0) AS heat_delta
            FROM market_trip_entries e JOIN market_trip_heat_lines h ON h.entry_id = e.id JOIN karats k ON k.id = h.karat_id
            WHERE {nr}
            GROUP BY e.trip_id, h.karat_id, k.karat_value
            """)
        settled = self._fetch_all(
            f"""
            SELECT e.trip_id, l.karat_id, k.karat_value, l.designation, COALESCE(SUM(l.weight_grams), 0) AS supplier_settled
            FROM market_trip_entries e JOIN market_trip_gold_settlements l ON l.entry_id = e.id JOIN karats k ON k.id = l.karat_id
            WHERE {nr}
            GROUP BY e.trip_id, l.karat_id, k.karat_value, l.designation
            """)
        held = self._fetch_all(
            f"""
            SELECT e.trip_id, l.karat_id, k.karat_value, l.designation,
                   COALESCE(SUM(IF(e.entry_kind = 'LOGO_HOLD', l.weight_grams, 0)), 0) AS held_out,
                   COALESCE(SUM(IF(e.entry_kind = 'HOLD_BACK', l.weight_grams, 0)), 0) AS held_back
            FROM market_trip_entries e JOIN market_trip_logo_hold_links l ON l.entry_id = e.id JOIN karats k ON k.id = l.karat_id
            WHERE {nr}
            GROUP BY e.trip_id, l.karat_id, k.karat_value, l.designation
            """)
        sold = self._fetch_all(
            f"""
            SELECT e.trip_id, s.karat_id, k.karat_value, s.designation, COALESCE(SUM(s.weight_grams), 0) AS sold
            FROM market_trip_entries e JOIN market_trip_gold_sales s ON s.entry_id = e.id JOIN karats k ON k.id = s.karat_id
            WHERE s.source_kind = 'CUSTODY_GOLD' AND {nr}
            GROUP BY e.trip_id, s.karat_id, k.karat_value, s.designation
            """)
        bought = self._fetch_all(
            f"""
            SELECT e.trip_id, l.karat_id, k.karat_value, l.designation, COALESCE(SUM(l.weight_grams), 0) AS purchased
            FROM market_trip_entries e JOIN market_trip_gold_buy_lines l ON l.entry_id = e.id JOIN karats k ON k.id = l.karat_id
            WHERE {nr}
            GROUP BY e.trip_id, l.karat_id, k.karat_value, l.designation
            """)

        def by_trip(rows):
            grouped = {}
            for row in rows:
                grouped.setdefault(int(row["trip_id"]), []).append(row)
            return grouped
        settle = []
        if self.has_settlement_schema():                                    # 060: the RAW pool moves of every trip (grams that left UNCLASSIFIED custody)
            settle = self._fetch_all(
                f"""
                SELECT e.trip_id, m.karat_id, k.karat_value, COALESCE(SUM(-m.delta), 0) AS settle_out
                FROM market_trip_entries e JOIN market_trip_settle_moves m ON m.entry_id = e.id JOIN karats k ON k.id = m.karat_id
                WHERE m.pool = 'RAW' AND {nr}
                GROUP BY e.trip_id, m.karat_id, k.karat_value
                """)
        settle_in = []
        if self.has_settlement_schema():                                    # 062: the LOGO pool moves of every trip
            settle_in = self._fetch_all(
                f"""
                SELECT e.trip_id, m.karat_id, k.karat_value, COALESCE(SUM(m.delta), 0) AS settle_in
                FROM market_trip_entries e JOIN market_trip_settle_moves m ON m.entry_id = e.id JOIN karats k ON k.id = m.karat_id
                WHERE m.pool = 'LOGO' AND {nr}
                GROUP BY e.trip_id, m.karat_id, k.karat_value
                """)
        terms = [by_trip(rows) for rows in (legs, held, heated, melted, settled, sold, bought, settle, settle_in)]
        trips = sorted(set().union(*[set(t) for t in terms]))
        return {trip: _gold_rows(*[t.get(trip, []) for t in terms]) for trip in trips}

    def custody_lots_by_trip(self):
        """DISPLAY ONLY. The assayed lots every custody CURRENTLY holds, with their outstanding grams, in ONE aggregate query (no trip filter, no locks, snapshot
        reversal lookup): the same derivation as `custody_lots` (location = the effective movements of the custody's own entries). Returns {custody trip id: rows}."""
        rows = self._fetch_all(
            f"""
            SELECT me.trip_id AS custody_trip_id, l.id, l.entry_id, l.lot_no, l.lot_kind, l.label, l.weight_grams, l.fineness_permille, l.pure_grams, oe.entry_no,
                   oe.trip_id AS origin_trip_id, COALESCE(SUM(m.weight_delta), 0) AS outstanding_grams
            FROM market_trip_lots l
            JOIN market_trip_entries oe ON oe.id = l.entry_id
            JOIN market_trip_lot_movements m ON m.lot_id = l.id
            JOIN market_trip_entries me ON me.id = m.entry_id
            WHERE {_not_reversed('me')}
            GROUP BY me.trip_id, l.id, l.entry_id, l.lot_no, l.lot_kind, l.label, l.weight_grams, l.fineness_permille, l.pure_grams, oe.entry_no, oe.trip_id
            HAVING SUM(m.weight_delta) <> 0
            ORDER BY me.trip_id, l.id
            """)
        grouped = {}
        for row in rows:
            grouped.setdefault(int(row["custody_trip_id"]), []).append(row)
        return grouped

    # ------------------------------------------------------------------ history
    def entries(self, trip_id, *, newest_first=False):
        """The trip's documents with their reference numbers (`entry_no` is a stable reference, never the display order): `newest_first` orders by
        `occurred_at DESC, id DESC`."""
        order = "e.occurred_at DESC, e.id DESC" if newest_first else "e.entry_no"
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
            WHERE e.trip_id = %s ORDER BY """ + order + """
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
            row["gold_sale"] = self.entry_gold_sale(row["id"])
            row["gold_buy"] = self.entry_gold_buy(row["id"])
            row["lot_settlement"] = self.entry_lot_settlement(row["id"])
            row["settle_melt"] = self.entry_settle_melt(row["id"]) if row["entry_kind"] == "ST_MELT" else None
            row["bar_supplier"] = self.entry_bar_supplier(row["id"]) if row["entry_kind"] == "ST_BAR_SUP" else None
            row["bar_marketer"] = self.entry_marketer_leg(row["id"]) if row["entry_kind"] == "ST_BAR_MKT" else None
            row["settle_logo"] = self.entry_settle_logo(row["id"]) if row["entry_kind"] == "ST_LOGO" else []
            row["settle_cash"] = self.entry_settle_cash(row["id"]) if row["entry_kind"] in ("ST_EXPENSE", "ST_CASH_BANK", "ST_CASH_OFF") else None
            row["settle_lines"] = self.entry_settle_lines(row["id"]) if row["entry_kind"] in ("ST_RET_MKT", "ST_LOSS") else []
            row["settle_adjust"] = self.entry_settle_adjust(row["id"]) if row["entry_kind"] == "ST_ADJUST" else None
            row["bar_fee"] = self.entry_bar_fee(row["id"]) if row["entry_kind"] == "ST_BAR_FEE" else None
            row["holding"] = self.entry_holding(row["id"])
            row["logo_holding"] = self.entry_logo_holding(row["id"])
            row["gold"] = self._fetch_all(
                "SELECT g.karat_id, k.karat_value, g.designation, g.weight_grams FROM market_trip_entry_gold g "
                "JOIN karats k ON k.id = g.karat_id WHERE g.entry_id = %s ORDER BY k.karat_value DESC, g.designation", (int(row["id"]),))
        return rows

    def entry_reversal_id(self, entry_id):
        """The FULL_REVERSAL correction of an entry, or None (a locking read)."""
        row = self._fetch_one("SELECT c.id FROM transaction_corrections c WHERE c.source_type = '" + SOURCE + "' AND c.source_id = %s AND c.correction_type = 'FULL_REVERSAL' FOR SHARE",
                              (_id(entry_id),))
        return None if row is None else row["id"]

    def has_phase2_schema(self):
        """True when migration 058 is present (its supplier link table exists). Asked once per repository object; every Phase 2 read is guarded by it so a
        database before 058 keeps every existing path working (it cannot hold a Phase 2 row)."""
        if not hasattr(self, "_phase2"):
            row = self._fetch_one("SELECT COUNT(*) AS n FROM information_schema.TABLES WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'market_trip_book_supplier_links'")
            self._phase2 = bool(row and int(row["n"]))
        return self._phase2

    # ------------------------------------------------------------------ settlement (060): melt recorded after the owner returns
    def has_settlement_schema(self):
        """True when migration 060 is present (the settlement move ledger exists). Asked once per repository object; every settlement read is guarded by it so a
        database before 060 keeps every existing path working (it cannot hold a settlement row)."""
        if not hasattr(self, "_settlement"):
            row = self._fetch_one("SELECT COUNT(*) AS n FROM information_schema.TABLES WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'market_trip_settle_moves'")
            self._settlement = bool(row and int(row["n"]))
        return self._settlement

    def has_trade_bank_schema(self):
        """True when migration 091 is present (gold purchase / sale may be paid or received through a bank account). Asked once per repository object."""
        if not hasattr(self, "_trade_bank"):
            row = self._fetch_one("SELECT COUNT(*) AS n FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'market_trip_gold_buys' AND COLUMN_NAME = 'payment_method'")
            self._trade_bank = bool(row and int(row["n"]))
        return self._trade_bank

    def post_bank_trade(self, *, type_code, account_id, entry_id, code, delta, description, actor, now):
        """The real money movement of a gold purchase (OUT, negative) or sale (IN, positive) paid / received through a bank / wallet account (091)."""
        movement_id, count = self._execute(
            """
            INSERT INTO bank_movements (bank_account_id, movement_type_id, amount_delta, reference_type, reference_id, reference_code, description, occurred_at, created_by_user_id)
            SELECT %s, t.id, %s, %s, %s, %s, %s, %s, %s FROM bank_movement_types t WHERE t.code = %s AND t.is_active = TRUE
            """, (int(account_id), delta, SOURCE, _id(entry_id), code, description, now, actor.user_id, type_code))
        if count != 1:
            raise RuntimeError(f"bank_movement_types {type_code} is missing (migration 091)")
        return int(movement_id)

    def has_cash_settle_schema(self):
        """True when migration 064 is present (the custody cash settlement document exists). Asked once per repository object."""
        if not hasattr(self, "_cash_settle"):
            row = self._fetch_one("SELECT COUNT(*) AS n FROM information_schema.TABLES WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'market_trip_settle_cash'")
            self._cash_settle = bool(row and int(row["n"]))
        return self._cash_settle

    def active_bank_accounts(self):
        return self._fetch_all("SELECT id, name, account_type, bank_name, account_label FROM bank_accounts WHERE is_active = TRUE ORDER BY account_type, name, id")

    def bank_account(self, account_id, *, lock=False):
        return self._fetch_one("SELECT id, name, is_active FROM bank_accounts WHERE id = %s" + (" FOR UPDATE" if lock else ""), (int(account_id),))

    def post_bank_in(self, *, account_id, entry_id, code, amount, description, actor, now):
        """The real money IN of a custody cash deposit (ST_CASH_BANK). Returns the bank movement id."""
        movement_id, count = self._execute(
            """
            INSERT INTO bank_movements (bank_account_id, movement_type_id, amount_delta, reference_type, reference_id, reference_code, description, occurred_at, created_by_user_id)
            SELECT %s, t.id, %s, %s, %s, %s, %s, %s, %s FROM bank_movement_types t WHERE t.code = 'TRIP_CUSTODY_BANK_IN' AND t.is_active = TRUE
            """, (int(account_id), amount, SOURCE, _id(entry_id), code, description, now, actor.user_id))
        if count != 1:
            raise RuntimeError("bank_movement_types TRIP_CUSTODY_BANK_IN is missing (migration 064)")
        return int(movement_id)

    def insert_settle_cash(self, *, entry_id, kind, amount, description, reference, bank_account_id, bank_movement_id):
        """The immutable cash settlement document (1:1 with its ST_EXPENSE / ST_CASH_BANK / ST_CASH_OFF entry)."""
        self._execute(
            "INSERT INTO market_trip_settle_cash (entry_id, entry_kind, amount, description, reference, bank_account_id, bank_movement_id) VALUES (%s, %s, %s, %s, %s, %s, %s)",
            (_id(entry_id), kind, amount, description or None, reference or None, None if bank_account_id is None else int(bank_account_id),
             None if bank_movement_id is None else int(bank_movement_id)))

    def entry_settle_cash(self, entry_id):
        """The cash settlement an entry made (None for any other entry or before 064)."""
        if not self.has_cash_settle_schema():
            return None
        return self._fetch_one(
            "SELECT s.*, b.name AS bank_account_name FROM market_trip_settle_cash s LEFT JOIN bank_accounts b ON b.id = s.bank_account_id WHERE s.entry_id = %s", (_id(entry_id),))

    def has_disposal_schema(self):
        """True when migration 065 is present (the disposal lines / adjustment documents exist). Asked once per repository object."""
        if not hasattr(self, "_disposal"):
            row = self._fetch_one("SELECT COUNT(*) AS n FROM information_schema.TABLES WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'market_trip_settle_lines'")
            self._disposal = bool(row and int(row["n"]))
        return self._disposal

    def has_adjustment_source_schema(self):
        """True when migration 079 distinguishes Scrap-backed and unsourced weight corrections."""
        if not hasattr(self, "_adjustment_source"):
            row = self._fetch_one(
                "SELECT COUNT(*) AS n FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = DATABASE() "
                "AND TABLE_NAME = 'market_trip_settle_adjust' AND COLUMN_NAME = 'source_kind'")
            self._adjustment_source = bool(row and int(row["n"]))
        return self._adjustment_source

    def insert_settle_line(self, *, entry_id, kind, line_no, source, karat_id, weight, classification):
        """One stored line of an ST_RET_MKT / ST_LOSS entry: the source pool (RAW / LOGO of a karat, or BAR in 21K-equivalent grams), the grams and the loss classification."""
        self._execute("INSERT INTO market_trip_settle_lines (entry_id, entry_kind, line_no, source, karat_id, weight_grams, classification) VALUES (%s, %s, %s, %s, %s, %s, %s)",
                      (_id(entry_id), kind, int(line_no), source, None if karat_id is None else int(karat_id), weight, classification))

    def entry_settle_lines(self, entry_id):
        if not self.has_disposal_schema():
            return []
        return self._fetch_all(
            "SELECT l.line_no, l.source, l.karat_id, k.karat_value, l.weight_grams, l.classification FROM market_trip_settle_lines l LEFT JOIN karats k ON k.id = l.karat_id "
            "WHERE l.entry_id = %s ORDER BY l.line_no", (_id(entry_id),))

    def insert_settle_adjust(self, *, entry_id, karat_id, direction, source_kind="SHOP_SCRAP", weight, reason):
        if self.has_adjustment_source_schema():
            self._execute("INSERT INTO market_trip_settle_adjust (entry_id, entry_kind, karat_id, direction, source_kind, weight_grams, reason) VALUES (%s, 'ST_ADJUST', %s, %s, %s, %s, %s)",
                          (_id(entry_id), int(karat_id), direction, source_kind, weight, reason))
        elif source_kind == "SHOP_SCRAP":
            self._execute("INSERT INTO market_trip_settle_adjust (entry_id, entry_kind, karat_id, direction, weight_grams, reason) VALUES (%s, 'ST_ADJUST', %s, %s, %s, %s)",
                          (_id(entry_id), int(karat_id), direction, weight, reason))
        else:
            raise RuntimeError("migration 079 is required for unsourced custody adjustments")

    def entry_settle_adjust(self, entry_id):
        if not self.has_disposal_schema():
            return None
        return self._fetch_one("SELECT a.*, k.karat_value FROM market_trip_settle_adjust a JOIN karats k ON k.id = a.karat_id WHERE a.entry_id = %s", (_id(entry_id),))

    def insert_settle_melt(self, *, entry_id, workshop, melt_date, figures):
        """The immutable melt document (1:1 with its ST_MELT entry): melted grams, bar weight and integer fineness, theoretical / actual Eq21 and the loss split."""
        self._execute(
            "INSERT INTO market_trip_settle_melts (entry_id, entry_kind, workshop, melt_date, input_weight_grams, output_weight_grams, fineness_permille, theoretical_eq21, "
            "actual_eq21, loss_eq21, weight_loss_eq21, fineness_loss_eq21) VALUES (%s, 'ST_MELT', %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (_id(entry_id), workshop, melt_date, figures["input_weight"], figures["output_weight"], int(figures["fineness"]), figures["theoretical_eq21"],
             figures["actual_eq21"], figures["loss_eq21"], figures["weight_loss_eq21"], figures["fineness_loss_eq21"]))

    def insert_settle_melt_input(self, *, entry_id, karat_id, weight):
        self._execute("INSERT INTO market_trip_settle_melt_inputs (entry_id, karat_id, weight_grams) VALUES (%s, %s, %s)", (_id(entry_id), int(karat_id), weight))

    def insert_settle_move(self, *, entry_id, trip_id, kind, pool, karat_id, delta):
        """One signed move of the settlement pools: RAW (grams of `karat_id`) or BAR (Eq21 grams, no karat). Insert-only; a reversed entry's moves stop counting."""
        self._execute("INSERT INTO market_trip_settle_moves (entry_id, entry_kind, trip_id, pool, karat_id, delta) VALUES (%s, %s, %s, %s, %s, %s)",
                      (_id(entry_id), kind, _id(trip_id), pool, None if karat_id is None else int(karat_id), delta))

    def settle_raw_out(self, trip_id, exclude_entry_id=None, *, current=True):
        """Per karat: grams that left the RAW pool through entries that were not reversed (a positive number = consumed)."""
        if not self.has_settlement_schema():
            return []
        return self._fetch_all(
            f"""
            SELECT m.karat_id, k.karat_value, COALESCE(SUM(-m.delta), 0) AS settle_out
            FROM market_trip_entries e JOIN market_trip_settle_moves m ON m.entry_id = e.id JOIN karats k ON k.id = m.karat_id
            WHERE e.trip_id = %s AND e.id <> %s AND m.pool = 'RAW' AND {_not_reversed('e', current=current)}
            GROUP BY m.karat_id, k.karat_value{_share(current, 'e, m')}
            """, (_id(trip_id), int(exclude_entry_id or 0)))

    def settle_logo_in(self, trip_id, exclude_entry_id=None, *, current=True):
        """Per karat: grams classified as LOGO through entries that were not reversed (062)."""
        if not self.has_settlement_schema():
            return []
        return self._fetch_all(
            f"""
            SELECT m.karat_id, k.karat_value, COALESCE(SUM(m.delta), 0) AS settle_in
            FROM market_trip_entries e JOIN market_trip_settle_moves m ON m.entry_id = e.id JOIN karats k ON k.id = m.karat_id
            WHERE e.trip_id = %s AND e.id <> %s AND m.pool = 'LOGO' AND {_not_reversed('e', current=current)}
            GROUP BY m.karat_id, k.karat_value{_share(current, 'e, m')}
            """, (_id(trip_id), int(exclude_entry_id or 0)))

    def entry_settle_logo(self, entry_id):
        """The grams a ST_LOGO entry classified, per karat (empty for any other entry or before 062)."""
        if not self.has_settlement_schema():
            return []
        return self._fetch_all(
            "SELECT m.karat_id, k.karat_value, m.delta AS weight_grams FROM market_trip_settle_moves m JOIN karats k ON k.id = m.karat_id "
            "WHERE m.entry_id = %s AND m.pool = 'LOGO' ORDER BY k.karat_value DESC", (_id(entry_id),))

    def settle_bar_balance(self, trip_id, exclude_entry_id=None, *, current=True):
        """The BAR pool of a trip (Eq21 grams): the sum of its BAR moves over the entries that were not reversed. 0.000 before 060."""
        if not self.has_settlement_schema():
            return Decimal("0.000")
        row = self._fetch_one(
            f"""
            SELECT COALESCE(SUM(m.delta), 0) AS balance
            FROM market_trip_entries e JOIN market_trip_settle_moves m ON m.entry_id = e.id
            WHERE e.trip_id = %s AND e.id <> %s AND m.pool = 'BAR' AND {_not_reversed('e', current=current)}{_share(current, 'e, m')}
            """, (_id(trip_id), int(exclude_entry_id or 0)))
        return Decimal(str(row["balance"] if row else 0))

    def settle_bar_by_trip(self):
        """DISPLAY ONLY: the BAR pool of EVERY trip in one aggregate query (no locks, snapshot reversal lookup). Returns {trip_id: Eq21 grams} for non-zero pools."""
        if not self.has_settlement_schema():
            return {}
        rows = self._fetch_all(
            f"""
            SELECT e.trip_id, COALESCE(SUM(m.delta), 0) AS balance
            FROM market_trip_entries e JOIN market_trip_settle_moves m ON m.entry_id = e.id
            WHERE m.pool = 'BAR' AND {_not_reversed('e')}
            GROUP BY e.trip_id HAVING SUM(m.delta) <> 0
            """)
        return {int(r["trip_id"]): Decimal(str(r["balance"])) for r in rows}

    def entry_settle_melt(self, entry_id):
        """The melt document of one ST_MELT entry with its melted grams per karat (None for any other entry or before 060)."""
        if not self.has_settlement_schema():
            return None
        doc = self._fetch_one(
            "SELECT m.entry_id, m.workshop, m.melt_date, m.input_weight_grams, m.output_weight_grams, m.fineness_permille, m.theoretical_eq21, m.actual_eq21, m.loss_eq21, "
            "m.weight_loss_eq21, m.fineness_loss_eq21 FROM market_trip_settle_melts m WHERE m.entry_id = %s", (_id(entry_id),))
        if doc is None:
            return None
        doc["inputs"] = self._fetch_all(
            "SELECT i.karat_id, k.karat_value, i.weight_grams FROM market_trip_settle_melt_inputs i JOIN karats k ON k.id = i.karat_id WHERE i.entry_id = %s "
            "ORDER BY k.karat_value DESC", (_id(entry_id),))
        return doc

    # ------------------------------------------------------------------ settlement step 2b (061): the melted bar delivered to a supplier
    def has_distribution_schema(self):
        """True when migration 061 is present (the bar supplier link table exists). Asked once per repository object; a database before 061 cannot hold a bar delivery."""
        if not hasattr(self, "_distribution"):
            row = self._fetch_one("SELECT COUNT(*) AS n FROM information_schema.TABLES WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'market_trip_bar_supplier_links'")
            self._distribution = bool(row and int(row["n"]))
        return self._distribution

    def insert_bar_supplier_link(self, *, entry_id, trip_id, settlement_id, supplier_id, weight):
        self._execute("INSERT INTO market_trip_bar_supplier_links (entry_id, entry_kind, trip_id, settlement_id, source_kind, supplier_id, weight_grams) "
                      "VALUES (%s, 'ST_BAR_SUP', %s, %s, 'TRIP_BAR', %s, %s)", (_id(entry_id), _id(trip_id), _id(settlement_id), _id(supplier_id), weight))

    def entry_bar_supplier(self, entry_id):
        """The supplier delivery a ST_BAR_SUP entry made, with the SG document's stored figures (None for any other entry or before 061)."""
        if not self.has_distribution_schema():
            return None
        return self._fetch_one(
            "SELECT l.entry_id, l.settlement_id, l.supplier_id, l.weight_grams, s.name AS supplier_name, g.delivered_karat_value, g.credited_weight_grams, "
            "g.credited_analytical_grams, g.rounding_difference_grams, g.target_karat_value "
            "FROM market_trip_bar_supplier_links l JOIN suppliers s ON s.id = l.supplier_id JOIN supplier_gold_settlements g ON g.id = l.settlement_id "
            "WHERE l.entry_id = %s", (_id(entry_id),))

    def entry_marketer_leg(self, entry_id):
        """The marketer 21K leg of a BK_DLV_MKT / BK_RECEIVE entry (None before 058 or for any other entry)."""
        if not self.has_phase2_schema():
            return None
        return self._fetch_one(
            "SELECT m.id, m.party_id, s.name AS party_name, m.trip_entry_kind, m.movement_type, m.weight_grams, m.weight_delta FROM marketer_gold_movements m "
            "JOIN suppliers s ON s.id = m.party_id WHERE m.trip_entry_id = %s AND m.movement_type IN ('TRIP_BOOK_IN', 'TRIP_BOOK_OUT')", (_id(entry_id),))

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
