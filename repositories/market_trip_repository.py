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
from services import book_rules

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
BK_ISSUE, BK_BUY, BK_SELL, BK_RETURN = book_rules.BK_ISSUE, book_rules.BK_BUY, book_rules.BK_SELL, book_rules.BK_RETURN     # Phase 1 BOOK entry kinds
LEGACY, BOOK = book_rules.LEGACY, book_rules.BOOK
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
    def insert_trip(self, *, notes, actor, now, mode=LEGACY):
        """A new OPEN trip. The mode is written ONLY for BOOK (a LEGACY trip takes the column DEFAULT, so the statement of every normal creation is the one it always was)."""
        if mode == BOOK:
            trip_id, _ = self._execute("INSERT INTO market_trips (status, accounting_mode, notes, created_by_user_id, created_at, status_changed_at) "
                                       "VALUES ('OPEN', 'BOOK', %s, %s, %s, %s)", (notes, actor.user_id, now, now))
        else:
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
        # 056: the ONE insert path of every entry kind checks the kind against the TRIP's mode (the caller holds the trip lock); the database repeats the rule
        # (chk_mte_mode_kind + the composite foreign key), so a BOOK trip can never receive a legacy-only entry nor a LEGACY trip a book entry.
        mode = book_rules.trip_mode(self.trip(trip_id))
        book_rules.require_kind_in_mode(kind, mode)
        columns, marks, values = "", "", ()
        if mode == BOOK:
            columns, marks, values = ", trip_mode", ", %s", (BOOK,)
        entry_id, _ = self._execute(
            f"""
            INSERT INTO market_trip_entries (trip_id, entry_no, entry_kind, entry_date, notes, request_key, request_hash,
                performed_by_user_id, performed_by_employee_id, occurred_at{columns})
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s{marks})
            """, (_id(trip_id), entry_no, kind, entry_date, notes, key, digest, actor.user_id, actor.employee_id, now, *values))
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
            WHERE e.trip_id = %s AND e.id <> %s AND {not_reversed}{_share(current, 'e, s')}
            """, (_id(trip_id), int(exclude_entry_id or 0)))
        bought = self._fetch_one(
            f"""
            SELECT COALESCE(SUM(b.total_price), 0) AS spent
            FROM market_trip_entries e JOIN market_trip_gold_buys b ON b.entry_id = e.id
            WHERE e.trip_id = %s AND e.id <> %s AND {not_reversed}{_share(current, 'e, b')}
            """, (_id(trip_id), int(exclude_entry_id or 0)))
        trades = self._fetch_one(                                          # 056: BOOK purchases spend and BOOK sales add custody cash (the same two terms)
            f"""
            SELECT COALESCE(SUM(IF(t.entry_kind = 'BK_SELL', t.total_price, 0)), 0) AS proceeds,
                   COALESCE(SUM(IF(t.entry_kind = 'BK_BUY', t.total_price, 0)), 0) AS spent
            FROM market_trip_entries e JOIN market_trip_book_trades t ON t.entry_id = e.id
            WHERE e.trip_id = %s AND e.id <> %s AND {not_reversed}{_share(current, 'e, t')}
            """, (_id(trip_id), int(exclude_entry_id or 0)))
        base = (row["issued"], row["returned"], paid["paid"], sold["proceeds"] + trades["proceeds"], bought["spent"] + trades["spent"])
        if not self.has_cash_settle_schema():
            return base
        settled = self._fetch_one(                                          # 064: expense, bank deposit and write-off consume custody cash (no Main Safe movement)
            f"""
            SELECT COALESCE(SUM(s.amount), 0) AS settled
            FROM market_trip_entries e JOIN market_trip_settle_cash s ON s.entry_id = e.id
            WHERE e.trip_id = %s AND e.id <> %s AND {not_reversed}{_share(current, 'e, s')}
            """, (_id(trip_id), int(exclude_entry_id or 0)))
        return base + (settled["settled"],)

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
                        buy_date, reference):
        """The immutable purchase document: the vendor identity (mandatory), the authoritative total and the price-warning evidence."""
        self._execute(
            """INSERT INTO market_trip_gold_buys (entry_id, vendor_kind, vendor_party_id, vendor_name, vendor_name_key, total_price, reference_total,
               deviation_percent, price_warning_acknowledged, buy_date, reference) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            (_id(entry_id), vendor_kind, vendor_party_id, vendor_name, vendor_key, total, reference_total, deviation, 1 if acknowledged else 0,
             buy_date, reference or None))

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
                         buyer_party_id, buyer_name, reference_price, deviation, acknowledged, sale_date, reference):
        """The immutable sale document. An aggregate line states (karat, designation); a whole lot states the lot, its stored assay and
        the exact SALE_OUT movement (the database ties them together)."""
        self._execute(
            """INSERT INTO market_trip_gold_sales
               (entry_id, source_kind, karat_id, designation, lot_id, fineness_permille, custody_movement_id, movement_type, weight_grams,
                weight_delta, total_price, price_per_gram, price_basis, buyer_kind, buyer_party_id, buyer_name, reference_price_per_gram,
                deviation_percent, price_warning_acknowledged, sale_date, reference)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            (_id(entry_id), source_kind, karat_id, designation, None if lot is None else int(lot["id"]),
             None if lot is None else lot["fineness_permille"], None if movement_id is None else int(movement_id),
             None if lot is None else SALE_MOVEMENT, weight, None if lot is None else -weight, total, per_gram, basis, buyer_kind,
             buyer_party_id, buyer_name, reference_price, deviation, 1 if acknowledged else 0, sale_date, reference or None))

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
            if str(row["entry_kind"]).startswith("BK_"):                       # 056: only BOOK entries carry book lines / a trade
                row["book_lines"] = self.entry_book_lines(row["id"])
                row["book_trade"] = self.entry_book_trade(row["id"])
                row["gain"] = self.gain_of_entry(row["id"]) if row["entry_kind"] == "BK_GAIN" else None
                row["recon"] = self.reconciliation_display(row["id"]) if row["entry_kind"] == "BK_RECON" else None
            row["holding"] = self.entry_holding(row["id"])
            row["logo_holding"] = self.entry_logo_holding(row["id"])
            row["gold"] = self._fetch_all(
                "SELECT g.karat_id, k.karat_value, g.designation, g.weight_grams FROM market_trip_entry_gold g "
                "JOIN karats k ON k.id = g.karat_id WHERE g.entry_id = %s ORDER BY k.karat_value DESC, g.designation", (int(row["id"]),))
        return rows

    # ------------------------------------------------------------------ BOOK core (056): book lines, trade documents, assayed return stock
    def insert_book_line(self, *, entry_id, line_no, trip_id, kind, line, line_price=None, scrap_movement_id=None, posting_basis=None, stock_item=None):
        """One stored line of a BOOK entry: the physical facts, the six-decimal analytical evidence, the POSTED equivalent and its signed form
        (`line` is a services.book_rules.Line, computed once from the facts). A Phase 2 line (058) may carry an ALLOCATED posting basis and the stock item it
        consumed; every other line keeps the statement it always had (so nothing references a 058 column before that migration)."""
        if posting_basis is not None or stock_item is not None:
            self._execute(
                """INSERT INTO market_trip_book_lines (entry_id, line_no, trip_id, trip_mode, entry_kind, direction, weight_grams, basis, karat_id, karat_value,
                   fineness_permille, eq21_analytical, eq21_posted, posting_difference, signed_eq21_posted, posting_basis, line_price, scrap_movement_id,
                   stock_item_entry_id, stock_item_line_no)
                   VALUES (%s, %s, %s, 'BOOK', %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                (_id(entry_id), int(line_no), _id(trip_id), kind, line.direction, line.weight, line.basis, line.karat_id, line.karat_value, line.fineness,
                 line.analytical_evidence, line.posted, line.difference, line.signed, posting_basis or book_rules.INDEPENDENT_BASIS, line_price,
                 None if scrap_movement_id is None else int(scrap_movement_id), None if stock_item is None else _id(stock_item[0]),
                 None if stock_item is None else int(stock_item[1])))
            return
        self._execute(
            """INSERT INTO market_trip_book_lines (entry_id, line_no, trip_id, trip_mode, entry_kind, direction, weight_grams, basis, karat_id, karat_value,
               fineness_permille, eq21_analytical, eq21_posted, posting_difference, signed_eq21_posted, line_price, scrap_movement_id)
               VALUES (%s, %s, %s, 'BOOK', %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            (_id(entry_id), int(line_no), _id(trip_id), kind, line.direction, line.weight, line.basis, line.karat_id, line.karat_value, line.fineness,
             line.analytical_evidence, line.posted, line.difference, line.signed, line_price, None if scrap_movement_id is None else int(scrap_movement_id)))

    def insert_book_trade(self, *, entry_id, kind, counterparty_kind, party_id, name, name_key, total, reference_total, deviation, acknowledged, trade_date, reference):
        """The immutable purchase / sale document: the counterparty identity (mandatory), the authoritative total and the price-warning evidence."""
        self._execute(
            """INSERT INTO market_trip_book_trades (entry_id, entry_kind, counterparty_kind, party_id, counterparty_name, counterparty_name_key, total_price,
               reference_total, deviation_percent, price_warning_acknowledged, trade_date, reference) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            (_id(entry_id), kind, counterparty_kind, party_id, name, name_key, total, reference_total, deviation, 1 if acknowledged else 0, trade_date,
             reference or None))

    def book_lines(self, trip_id, exclude_entry_id=None, *, current=False):
        """The EFFECTIVE book lines of one trip (the entries that were not reversed): the input of the exact balance.
        `current=True` is for DECISIONS made under the trip lock: the entry and line rows are locking reads AND the nested reversal lookup is a current read.
        `current=False` is the DISPLAY form: a plain snapshot read throughout (no locking clause anywhere, the reversal lookup is the connection's snapshot too), so the
        balance, the entry history and the stock items read in one REPEATABLE READ transaction always come from the same state."""
        return self._fetch_all(
            f"""
            SELECT l.entry_id, l.line_no, l.entry_kind, l.direction, l.weight_grams, l.basis, l.karat_id, l.karat_value, l.fineness_permille,
                   l.eq21_posted, l.signed_eq21_posted
            FROM market_trip_entries e JOIN market_trip_book_lines l ON l.entry_id = e.id
            WHERE e.trip_id = %s AND e.id <> %s AND {_not_reversed('e', current=current)}
            ORDER BY l.entry_id, l.line_no{" FOR SHARE OF e, l" if current else ""}
            """, (_id(trip_id), int(exclude_entry_id or 0)))

    def reconciliation_source(self, trip_id, *, approval=False):
        """DISPLAY ONLY (R1 reconciliation preview). Everything the pure preview compares, from ONE trip and a fixed number of queries (five), every one a plain
        snapshot read: NO locking clause anywhere (outer queries, subqueries or reversal lookups) and no write. Run inside the caller's single read transaction so all five
        come from the same snapshot. The rows are the STORED facts: nothing is aggregated away and no list limit exists.

          entries  every entry of the trip (cash kinds and unknown kinds included, so the preview can classify them) with the correction that reversed it, if any
          lines    every book line whose OWN trip_id is the trip (selected independently of the entries, so a line of a missing entry is visible as an orphan)
          scrap    the ORIGINAL Scrap legs that reference an entry of the trip (reverse direction: a leg that no line claims is visible)
          trades   the trade headers of the trip's entries
          stock    the RECEIVE / RECEIVE_REVERSAL stock records of the trip's entries

        A section whose read fails is NOT turned into an empty list: its name is returned in `incomplete` and the preview refuses to trust itself."""
        trip = _id(trip_id)
        result = {"trip_id": int(trip), "entries": [], "lines": [], "scrap": [], "trades": [], "stock": [], "incomplete": []}
        queries = (
            ("entries", "SOURCE_ENTRIES_UNREADABLE",
             """
             SELECT e.id, e.trip_id, e.trip_mode, e.entry_no, e.entry_kind, c.id AS correction_id
             FROM market_trip_entries e
             LEFT JOIN transaction_corrections c ON c.source_type = '""" + SOURCE + """' AND c.source_id = e.id AND c.correction_type = 'FULL_REVERSAL'
             WHERE e.trip_id = %s ORDER BY e.id
             """),
            ("lines", "SOURCE_LINES_UNREADABLE",
             """
             SELECT l.entry_id, l.line_no, l.trip_id, l.trip_mode, l.entry_kind, l.direction, l.weight_grams, l.basis, l.karat_id, l.karat_value, l.fineness_permille,
                    l.eq21_analytical, l.eq21_posted, l.posting_difference, l.signed_eq21_posted, l.line_price, l.scrap_movement_id
             FROM market_trip_book_lines l WHERE l.trip_id = %s ORDER BY l.entry_id, l.line_no
             """),
            ("scrap", "SOURCE_LEGS_UNREADABLE",
             """
             SELECT m.id, m.karat_id, m.weight_delta_grams, m.movement_code, m.reference_id
             FROM old_gold_movements m JOIN market_trip_entries e ON e.id = m.reference_id
             WHERE m.reference_type = '""" + SOURCE + """' AND e.trip_id = %s AND m.movement_code IN ('TRIP_CUSTODY_OUT', 'TRIP_CUSTODY_RETURN_IN')
             ORDER BY m.id
             """),
            ("trades", "SOURCE_TRADES_UNREADABLE",
             """
             SELECT t.entry_id, t.entry_kind, t.total_price
             FROM market_trip_book_trades t JOIN market_trip_entries e ON e.id = t.entry_id WHERE e.trip_id = %s ORDER BY t.entry_id
             """),
            ("stock", "SOURCE_STOCK_UNREADABLE",
             """
             SELECT s.item_entry_id, s.item_line_no, s.movement_type, s.grams_delta, s.eq21_posted_delta
             FROM shop_assayed_stock_movements s JOIN market_trip_entries e ON e.id = s.item_entry_id
             WHERE e.trip_id = %s AND s.movement_type IN ('RECEIVE', 'RECEIVE_REVERSAL') ORDER BY s.id
             """),
        )
        extra = self._line_extra()                                         # 058 columns of a line, or their pre-058 constants
        for name, code, sql in queries:
            if name == "lines":
                sql = sql.replace("l.line_price, l.scrap_movement_id" + chr(10), "l.line_price, l.scrap_movement_id" + extra + chr(10))
            if approval and name == "entries":
                sql = sql.replace("e.entry_kind, c.id AS correction_id", "e.entry_kind, e.entry_date, c.id AS correction_id, c.correction_type")
            if approval and name == "stock":
                sql = sql.replace("SELECT s.item_entry_id", "SELECT s.id, s.item_entry_id")
            try:
                result[name] = self._fetch_all(sql, (trip,))
            except MySqlError as exc:
                result["incomplete"].append((code, f"{type(exc).__name__}"))
        try:                                                               # 058: the Phase 2 legs (three plain reads; none before 058)
            result.update(self.phase2_source(trip, current=False))
        except MySqlError as exc:
            result["incomplete"].append(("SOURCE_PHASE2_UNREADABLE", f"{type(exc).__name__}"))
        return result

    # ------------------------------------------------------------------ BOOK reconciliation document (057): DECISION reads and the immutable insert
    def reconciliation_source_current(self, trip_id):
        """DECISION form of `reconciliation_source` (R2-S1 settlement): the SAME five sections, but every one a LOCKING read taken under the trip lock (FOR SHARE), so a
        reconciliation is built from CURRENT committed rows and never from a snapshot that an earlier plain read of this transaction pinned. Differences from the display
        form, all deliberate: the entries carry `entry_date` and the type of the reversing correction (the date window and the reversal state of the digest) and the stock
        records carry their `s.id` (the identity of a stock record inside the snapshot). A failing read is NOT turned into an incomplete section: the error propagates and the
        transaction rolls back (a deadlock or lock timeout must never be swallowed). The locks taken here are a starting point that still needs a concurrent-transaction test on
        a real engine; nothing in this method proves their effect."""
        # Joined entries must also use CURRENT reads after waiting for the trip lock.
        # Leaving e outside OF can mix current legs with a pinned entry snapshot.
        trip = _id(trip_id)
        extra = self._line_extra()                                         # the 058 capability is asked FIRST (one statement), then the five sections
        result = {"trip_id": int(trip), "entries": [], "lines": [], "scrap": [], "trades": [], "stock": [], "incomplete": []}
        result["entries"] = self._fetch_all(
            """
            SELECT e.id, e.trip_id, e.trip_mode, e.entry_no, e.entry_kind, e.entry_date, c.id AS correction_id, c.correction_type AS correction_type
            FROM market_trip_entries e
            LEFT JOIN transaction_corrections c ON c.source_type = '""" + SOURCE + """' AND c.source_id = e.id AND c.correction_type = 'FULL_REVERSAL'
            WHERE e.trip_id = %s ORDER BY e.id FOR SHARE OF e, c
            """, (trip,))
        result["lines"] = self._fetch_all(
            """
            SELECT l.entry_id, l.line_no, l.trip_id, l.trip_mode, l.entry_kind, l.direction, l.weight_grams, l.basis, l.karat_id, l.karat_value, l.fineness_permille,
                   l.eq21_analytical, l.eq21_posted, l.posting_difference, l.signed_eq21_posted, l.line_price, l.scrap_movement_id""" + extra + """
            FROM market_trip_book_lines l WHERE l.trip_id = %s ORDER BY l.entry_id, l.line_no FOR SHARE
            """, (trip,))
        result["scrap"] = self._fetch_all(
            """
            SELECT m.id, m.karat_id, m.weight_delta_grams, m.movement_code, m.reference_id
            FROM old_gold_movements m JOIN market_trip_entries e ON e.id = m.reference_id
            WHERE m.reference_type = '""" + SOURCE + """' AND e.trip_id = %s AND m.movement_code IN ('TRIP_CUSTODY_OUT', 'TRIP_CUSTODY_RETURN_IN')
            ORDER BY m.id FOR SHARE OF m, e
            """, (trip,))
        result["trades"] = self._fetch_all(
            """
            SELECT t.entry_id, t.entry_kind, t.total_price
            FROM market_trip_book_trades t JOIN market_trip_entries e ON e.id = t.entry_id WHERE e.trip_id = %s ORDER BY t.entry_id FOR SHARE OF t, e
            """, (trip,))
        result["stock"] = self._fetch_all(
            """
            SELECT s.id, s.item_entry_id, s.item_line_no, s.movement_type, s.grams_delta, s.eq21_posted_delta
            FROM shop_assayed_stock_movements s JOIN market_trip_entries e ON e.id = s.item_entry_id
            WHERE e.trip_id = %s AND s.movement_type IN ('RECEIVE', 'RECEIVE_REVERSAL') ORDER BY s.id FOR SHARE OF s, e
            """, (trip,))
        result.update(self.phase2_source(trip, current=True))              # 058: the Phase 2 legs, locked like the other sections
        return result

    def effective_reconciliation_entries(self, trip_id):
        """The reconciliation documents (BK_RECON entries) of the trip that were NOT reversed: a CURRENT locking read (the entry rows and the nested reversal lookup). It reads
        `market_trip_entries` and `transaction_corrections` ONLY - never the 057 table - so it is safe on a database that has not applied migration 057 (it finds nothing)."""
        return self._fetch_all(
            f"""
            SELECT e.id, e.entry_no FROM market_trip_entries e
            WHERE e.trip_id = %s AND e.entry_kind = 'BK_RECON' AND {_not_reversed('e', current=True)} ORDER BY e.id FOR SHARE
            """, (_id(trip_id),))

    def next_reconciliation_generation(self, trip_id):
        """1 + the highest generation ever recorded for the trip (a reversed reconciliation still holds its generation). A locking read; the caller holds the trip lock."""
        row = self._fetch_one("SELECT COALESCE(MAX(generation), 0) + 1 AS n FROM market_trip_reconciliations WHERE trip_id = %s FOR SHARE", (_id(trip_id),))
        return int(row["n"])

    def insert_reconciliation(self, *, entry_id, trip_id, generation, effective_date, date_floor, date_floor_source, a_star_units, a_star, b_star, loss_posted,
                              rounding_settlement, delta_units, rho_units, sum_abs_units, entry_count, line_count, reversed_count, source_digest, snapshot_digest,
                              snapshot_text, reason_code, note, user_id, confirmed_at, algorithm, source_algorithm, snapshot_format, unit_denominator):
        """The immutable reconciliation record. Every figure is passed exactly as computed (integers for the lattice columns, Decimal for the posted ones): nothing is rounded,
        cut or defaulted here."""
        self._execute(
            """INSERT INTO market_trip_reconciliations (entry_id, entry_kind, trip_id, trip_mode, generation, algorithm, source_algorithm, snapshot_format, effective_date,
               date_floor, date_floor_source, unit_denominator, a_star_units, a_star, b_star, loss_posted, rounding_settlement, delta_units, rho_units, sum_abs_units,
               entry_count, line_count, reversed_count, source_digest, snapshot_digest, snapshot, reason_code, note, confirmed_by_user_id, confirmed_at)
               VALUES (%s, 'BK_RECON', %s, 'BOOK', %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            (_id(entry_id), _id(trip_id), int(generation), algorithm, source_algorithm, snapshot_format, effective_date, date_floor, date_floor_source, int(unit_denominator),
             a_star_units, a_star, b_star, loss_posted, rounding_settlement, delta_units, rho_units, sum_abs_units, entry_count, line_count, reversed_count,
             source_digest, snapshot_digest, snapshot_text, reason_code, note, user_id, confirmed_at))

    def book_report_rows(self, date_from, date_to):
        """DISPLAY ONLY (plain reads, no lock): the reconciliations (057) and evidenced gains (059) whose EFFECTIVE date (the entry date) is inside the inclusive range,
        newest first, each with its recorded time, trip, figures and - when reversed - the reversal reference. Reversed documents are LISTED (never dropped); how they
        enter period totals is not decided here. A section whose table does not exist yet is empty, never an error."""
        def has(table):
            row = self._fetch_one("SELECT COUNT(*) AS n FROM information_schema.TABLES WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s", (table,))
            return bool(row and int(row["n"]))
        reversal = ("LEFT JOIN transaction_corrections c ON c.source_type = '" + SOURCE + "' AND c.source_id = e.id AND c.correction_type = 'FULL_REVERSAL'")
        out = {"reconciliations": [], "gains": []}
        if has("market_trip_reconciliations"):
            out["reconciliations"] = self._fetch_all(
                "SELECT e.id AS entry_id, e.trip_id, e.entry_no, e.entry_date, r.generation, r.a_star, r.b_star, r.loss_posted, r.rounding_settlement, r.reason_code, r.note, "
                "r.confirmed_at, u.username AS confirmed_by, c.id AS reversal_id, c.reason AS reversal_reason, c.occurred_at AS reversal_at "
                "FROM market_trip_reconciliations r JOIN market_trip_entries e ON e.id = r.entry_id LEFT JOIN users u ON u.id = r.confirmed_by_user_id " + reversal +
                " WHERE e.entry_date >= %s AND e.entry_date <= %s ORDER BY e.entry_date DESC, e.id DESC", (date_from, date_to))
        if has("market_trip_gains"):
            out["gains"] = self._fetch_all(
                "SELECT e.id AS entry_id, e.trip_id, e.entry_no, e.entry_date, g.explanation, g.evidence_reference, g.confirmed_at, u.username AS confirmed_by, "
                "l.weight_grams, l.basis, l.karat_value, l.fineness_permille, l.eq21_posted, l.eq21_analytical, "
                "c.id AS reversal_id, c.reason AS reversal_reason, c.occurred_at AS reversal_at "
                "FROM market_trip_gains g JOIN market_trip_entries e ON e.id = g.entry_id JOIN market_trip_book_lines l ON l.entry_id = e.id AND l.line_no = 1 "
                "LEFT JOIN users u ON u.id = g.confirmed_by_user_id " + reversal +
                " WHERE e.entry_date >= %s AND e.entry_date <= %s ORDER BY e.entry_date DESC, e.id DESC", (date_from, date_to))
        return out

    def reconciliation_display(self, entry_id):
        """DISPLAY ONLY: the stored figures of a BK_RECON entry for the documents list (plain read, no lock; None when the 057 table is absent or the row is missing)."""
        exists = self._fetch_one("SELECT COUNT(*) AS n FROM information_schema.TABLES WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'market_trip_reconciliations'")
        if not (exists and int(exists["n"])):
            return None
        return self._fetch_one("SELECT r.generation, r.effective_date, r.a_star, r.b_star, r.loss_posted, r.rounding_settlement, r.reason_code, r.note, r.confirmed_at, "
                               "r.source_algorithm, u.username AS confirmed_by FROM market_trip_reconciliations r LEFT JOIN users u ON u.id = r.confirmed_by_user_id "
                               "WHERE r.entry_id = %s", (_id(entry_id),))

    def reconciliation_of_entry(self, entry_id):
        """The stored reconciliation of a BK_RECON entry with its entry number (a locking read: a replay must see the row that the first request committed)."""
        return self._fetch_one(
            "SELECT r.*, e.entry_no FROM market_trip_reconciliations r JOIN market_trip_entries e ON e.id = r.entry_id WHERE r.entry_id = %s FOR SHARE OF r", (_id(entry_id),))

    def entry_reversal_id(self, entry_id):
        """The FULL_REVERSAL correction of an entry, or None (a locking read)."""
        row = self._fetch_one("SELECT c.id FROM transaction_corrections c WHERE c.source_type = '" + SOURCE + "' AND c.source_id = %s AND c.correction_type = 'FULL_REVERSAL' FOR SHARE",
                              (_id(entry_id),))
        return None if row is None else row["id"]

    def book_groups_by_trip(self):
        """DISPLAY ONLY (Gold Position). The effective book lines of EVERY trip grouped by (trip, direction, basis, karat / assay): the factor is identical inside a
        group, so the exact analytical value follows from SUM(weight). One aggregate query, no locks, snapshot reversal lookup. Returns {trip_id: rows}."""
        rows = self._fetch_all(
            f"""
            SELECT e.trip_id, l.direction, l.basis, l.karat_value, l.fineness_permille, COALESCE(SUM(l.weight_grams), 0) AS weight_grams,
                   COALESCE(SUM(l.eq21_posted), 0) AS eq21_posted, COUNT(*) AS `lines`
            FROM market_trip_entries e JOIN market_trip_book_lines l ON l.entry_id = e.id
            WHERE {_not_reversed('e')}
            GROUP BY e.trip_id, l.direction, l.basis, l.karat_value, l.fineness_permille
            """)
        grouped = {}
        for row in rows:
            grouped.setdefault(int(row["trip_id"]), []).append(row)
        return grouped

    # ------------------------------------------------------------------ BOOK Phase 2 (058): stock reissue, supplier and marketer delivery, marketer receipt
    def has_phase2_schema(self):
        """True when migration 058 is present (its supplier link table exists). Asked once per repository object; every Phase 2 read is guarded by it so a
        database before 058 keeps every existing path working (it cannot hold a Phase 2 row)."""
        if not hasattr(self, "_phase2"):
            row = self._fetch_one("SELECT COUNT(*) AS n FROM information_schema.TABLES WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'market_trip_book_supplier_links'")
            self._phase2 = bool(row and int(row["n"]))
        return self._phase2

    def _line_extra(self):
        return (", l.posting_basis, l.stock_item_entry_id, l.stock_item_line_no" if self.has_phase2_schema()
                else ", 'INDEPENDENT' AS posting_basis, NULL AS stock_item_entry_id, NULL AS stock_item_line_no")

    def has_gain_schema(self):
        """True when migration 059 is present (the gain record table exists). Only asked after 058 was found (a database without 058 cannot hold 059)."""
        if not hasattr(self, "_gain"):
            row = self._fetch_one("SELECT COUNT(*) AS n FROM information_schema.TABLES WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'market_trip_gains'")
            self._gain = bool(row and int(row["n"]))
        return self._gain

    def insert_gain(self, *, entry_id, trip_id, explanation, evidence_reference, user_id, confirmed_at):
        """059: the owner's explanation, evidence reference and explicit confirmation of a BK_GAIN entry (insert-only, 1:1 with the entry)."""
        self._execute("INSERT INTO market_trip_gains (entry_id, entry_kind, trip_id, trip_mode, explanation, evidence_reference, confirmed_by_user_id, confirmed_at) "
                      "VALUES (%s, 'BK_GAIN', %s, 'BOOK', %s, %s, %s, %s)", (_id(entry_id), _id(trip_id), explanation, evidence_reference, int(user_id), confirmed_at))

    def gain_of_entry(self, entry_id):
        """The gain record of one BK_GAIN entry (None before 059 or when absent)."""
        if not self.has_phase2_schema() or not self.has_gain_schema():
            return None
        return self._fetch_one("SELECT g.entry_id, g.trip_id, g.explanation, g.evidence_reference, g.confirmed_by_user_id, g.confirmed_at, u.username AS confirmed_by "
                               "FROM market_trip_gains g LEFT JOIN users u ON u.id = g.confirmed_by_user_id WHERE g.entry_id = %s", (_id(entry_id),))

    # ------------------------------------------------------------------ settlement (060): melt recorded after the owner returns
    def has_settlement_schema(self):
        """True when migration 060 is present (the settlement move ledger exists). Asked once per repository object; every settlement read is guarded by it so a
        database before 060 keeps every existing path working (it cannot hold a settlement row)."""
        if not hasattr(self, "_settlement"):
            row = self._fetch_one("SELECT COUNT(*) AS n FROM information_schema.TABLES WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'market_trip_settle_moves'")
            self._settlement = bool(row and int(row["n"]))
        return self._settlement

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

    def insert_settle_adjust(self, *, entry_id, karat_id, direction, weight, reason):
        self._execute("INSERT INTO market_trip_settle_adjust (entry_id, entry_kind, karat_id, direction, weight_grams, reason) VALUES (%s, 'ST_ADJUST', %s, %s, %s, %s)",
                      (_id(entry_id), int(karat_id), direction, weight, reason))

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

    def insert_book_supplier_link(self, *, entry_id, trip_id, settlement_id, supplier_id, weight):
        self._execute("INSERT INTO market_trip_book_supplier_links (entry_id, entry_kind, trip_id, settlement_id, source_kind, supplier_id, weight_grams) "
                      "VALUES (%s, 'BK_DLV_SUP', %s, %s, 'TRIP_BOOK', %s, %s)", (_id(entry_id), _id(trip_id), _id(settlement_id), _id(supplier_id), weight))

    def entry_book_supplier(self, entry_id):
        """The supplier delivery of a BK_DLV_SUP entry: its SG document, supplier and the STORED supplier gold movement (what a reversal restores)."""
        if not self.has_phase2_schema():
            return None
        return self._fetch_one(
            """SELECT k.entry_id, k.settlement_id, k.supplier_id, k.weight_grams, s.name AS supplier_name, g.delivered_karat_id, g.delivered_karat_value,
                      g.delivered_fineness_permille, g.target_karat_id, g.target_karat_value, g.credited_weight_grams, g.credited_analytical_grams,
                      g.rounding_difference_grams, m.id AS gold_movement_id, m.karat_id AS gold_karat_id, m.weight_delta_grams, m.equivalent_21_delta_grams
               FROM market_trip_book_supplier_links k JOIN supplier_gold_settlements g ON g.id = k.settlement_id JOIN suppliers s ON s.id = k.supplier_id
               LEFT JOIN supplier_gold_settlement_legs lg ON lg.settlement_id = g.id AND lg.leg_kind = 'GOLD'
               LEFT JOIN supplier_gold_movements m ON m.id = lg.supplier_gold_movement_id
               WHERE k.entry_id = %s""", (_id(entry_id),))

    def entry_stock_issues(self, entry_id):
        """The stock ISSUE rows a BK_ISS_STK entry posted (one per consumed item, STORED grams and allocated posted share)."""
        return self._fetch_all(
            "SELECT item_entry_id, item_line_no, grams_delta, eq21_posted_delta, eq21_analytical, posting_difference, eq21_independent, posting_basis "
            "FROM shop_assayed_stock_movements WHERE source_entry_id = %s AND movement_type = 'ISSUE' ORDER BY item_entry_id, item_line_no", (_id(entry_id),))

    def entry_marketer_leg(self, entry_id):
        """The marketer 21K leg of a BK_DLV_MKT / BK_RECEIVE entry (None before 058 or for any other entry)."""
        if not self.has_phase2_schema():
            return None
        return self._fetch_one(
            "SELECT m.id, m.party_id, s.name AS party_name, m.trip_entry_kind, m.movement_type, m.weight_grams, m.weight_delta FROM marketer_gold_movements m "
            "JOIN suppliers s ON s.id = m.party_id WHERE m.trip_entry_id = %s AND m.movement_type IN ('TRIP_BOOK_IN', 'TRIP_BOOK_OUT')", (_id(entry_id),))

    def phase2_source(self, trip_id, *, current):
        """The Phase 2 evidence of one trip for the reconciliation reads (display: plain snapshot; decision: locking). Three fixed queries, none before 058:
          stock_issues  the stock ISSUE / ISSUE_REVERSAL rows whose SOURCE entry is in this trip (the BK_ISS_STK legs)
          suppliers     the TRIP_BOOK supplier links of this trip with their SG facts (the BK_DLV_SUP legs)
          marketer      the marketer trip-entry legs of this trip's entries (the BK_DLV_MKT / BK_RECEIVE legs)
        plus two sections for the allocation check of every stock item a BK_ISS_STK line of this trip names (one query each, never one per line):
          stock_origins the item's own BK_RETURN line (facts and stored posted amount)
          stock_history EVERY movement of those items, of any trip, in id order (receipt, issues, their compensations) with the stored evidence
        and, from 059, `gains`: the gain records of this trip's BK_GAIN entries (the key is ABSENT before 059: no gain can exist there)."""
        out = {"stock_issues": [], "suppliers": [], "marketer": [], "stock_origins": [], "stock_history": []}
        if not self.has_phase2_schema():
            return out
        trip = _id(trip_id)
        lock = (lambda of: f" FOR SHARE OF {of}") if current else (lambda of: "")
        out["stock_issues"] = self._fetch_all(
            "SELECT s.id, s.item_entry_id, s.item_line_no, s.source_entry_id, s.movement_type, s.grams_delta, s.eq21_posted_delta, s.posting_basis, "
            "s.eq21_analytical, s.posting_difference, s.eq21_independent "
            "FROM shop_assayed_stock_movements s JOIN market_trip_entries e ON e.id = s.source_entry_id "
            "WHERE e.trip_id = %s AND s.movement_type IN ('ISSUE', 'ISSUE_REVERSAL') ORDER BY s.id" + lock("s, e"), (trip,))
        items = ("SELECT DISTINCT b.stock_item_entry_id, b.stock_item_line_no FROM market_trip_book_lines b "
                 "WHERE b.trip_id = %s AND b.entry_kind = 'BK_ISS_STK' AND b.stock_item_entry_id IS NOT NULL")
        out["stock_origins"] = self._fetch_all(
            "SELECT o.entry_id, o.line_no, o.entry_kind, o.basis, o.weight_grams, o.fineness_permille, o.eq21_posted FROM market_trip_book_lines o "
            f"WHERE (o.entry_id, o.line_no) IN ({items}) ORDER BY o.entry_id, o.line_no" + lock("o"), (trip,))
        out["stock_history"] = self._fetch_all(
            "SELECT h.id, h.item_entry_id, h.item_line_no, h.movement_type, h.source_entry_id, h.correction_id, h.grams_delta, h.eq21_posted_delta, "
            "h.eq21_analytical, h.posting_difference, h.eq21_independent, h.posting_basis FROM shop_assayed_stock_movements h "
            f"WHERE (h.item_entry_id, h.item_line_no) IN ({items}) ORDER BY h.id" + lock("h"), (trip,))
        out["suppliers"] = self._fetch_all(
            "SELECT k.entry_id, k.settlement_id, k.supplier_id, k.weight_grams, g.delivered_karat_id, g.delivered_karat_value, g.delivered_fineness_permille, "
            "g.target_karat_value, g.credited_weight_grams FROM market_trip_book_supplier_links k JOIN supplier_gold_settlements g ON g.id = k.settlement_id "
            "WHERE k.trip_id = %s ORDER BY k.entry_id" + lock("k, g"), (trip,))
        out["marketer"] = self._fetch_all(
            "SELECT m.id, m.party_id, m.trip_entry_id, m.trip_entry_kind, m.movement_type, m.weight_grams, m.weight_delta "
            "FROM marketer_gold_movements m JOIN market_trip_entries e ON e.id = m.trip_entry_id WHERE e.trip_id = %s ORDER BY m.id" + lock("m, e"), (trip,))
        if self.has_gain_schema():
            out["gains"] = self._fetch_all(
                "SELECT g.entry_id, g.explanation, g.evidence_reference, g.confirmed_by_user_id, g.confirmed_at FROM market_trip_gains g WHERE g.trip_id = %s "
                "ORDER BY g.entry_id" + lock("g"), (trip,))
        return out

    def effective_reconciliations_display(self):
        """DISPLAY ONLY (Gold Position). Every BK_RECON entry that was NOT reversed (snapshot reversal lookup, no locks), each with its stored record (057) joined 1:1 by
        its primary key, so no source line is multiplied. A missing record comes back with NULL record fields (the caller refuses it, never reads it as zero).
        A database without the 057 table cannot hold a record: the entries are still read (a BK_RECON entry there is reported, never ignored)."""
        exists = self._fetch_one("SELECT COUNT(*) AS n FROM information_schema.TABLES WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'market_trip_reconciliations'")
        if exists and int(exists["n"]):
            record = ("r.entry_id AS record_entry_id, r.entry_kind AS record_kind, r.trip_id AS record_trip_id, r.trip_mode AS record_mode, r.generation, "
                      "r.a_star_units, r.b_star, r.loss_posted, r.rounding_settlement", "LEFT JOIN market_trip_reconciliations r ON r.entry_id = e.id")
        else:
            record = ("NULL AS record_entry_id, NULL AS record_kind, NULL AS record_trip_id, NULL AS record_mode, NULL AS generation, NULL AS a_star_units, "
                      "NULL AS b_star, NULL AS loss_posted, NULL AS rounding_settlement", "")
        return self._fetch_all(
            f"""
            SELECT e.id AS entry_id, e.trip_id, e.trip_mode, e.entry_kind, e.entry_no, {record[0]}
            FROM market_trip_entries e {record[1]}
            WHERE e.entry_kind = 'BK_RECON' AND {_not_reversed('e')}
            ORDER BY e.trip_id, e.id
            """)

    def entry_book_lines(self, entry_id):
        """The stored lines of a BOOK entry with the signed grams of their Scrap movement (what a reversal negates: the exact stored figures)."""
        return self._fetch_all(
            "SELECT l.line_no, l.entry_kind, l.direction, l.weight_grams, l.basis, l.karat_id, l.karat_value, l.fineness_permille, l.eq21_analytical, l.eq21_posted, "
            "l.posting_difference, l.signed_eq21_posted, l.line_price, l.scrap_movement_id, m.weight_delta_grams "
            "FROM market_trip_book_lines l LEFT JOIN old_gold_movements m ON m.id = l.scrap_movement_id WHERE l.entry_id = %s ORDER BY l.line_no", (_id(entry_id),))

    def entry_book_trade(self, entry_id):
        """The purchase / sale document a BK_BUY / BK_SELL entry made (None for any other entry)."""
        return self._fetch_one("SELECT * FROM market_trip_book_trades WHERE entry_id = %s", (_id(entry_id),))

    def insert_stock_movement(self, *, item_entry_id, item_line_no, movement_type, grams_delta, posted_delta, analytical, difference, independent, posting_basis,
                              source_entry_id, correction_id, user_id):
        """One row of the assayed return stock ledger (insert-only). The grams and the posted equivalent travel together; the figures are STORED, never recomputed."""
        movement_id, _ = self._execute(
            """INSERT INTO shop_assayed_stock_movements (item_entry_id, item_line_no, movement_type, grams_delta, eq21_posted_delta, eq21_analytical,
               posting_difference, eq21_independent, posting_basis, source_entry_id, correction_id, created_by_user_id)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            (_id(item_entry_id), int(item_line_no), movement_type, grams_delta, posted_delta, analytical, difference, independent, posting_basis,
             _id(source_entry_id), None if correction_id is None else _id(correction_id), user_id))
        return int(movement_id)

    def stock_item(self, entry_id, line_no, lock=False):
        """One assayed-stock item (a BK_RETURN FINENESS line) with its physical facts, posted amount and origin. lock=True takes the item row FIRST
        (the stock lock order: trip -> item rows ascending -> Scrap / cash)."""
        return self._fetch_one(
            "SELECT l.entry_id, l.line_no, l.trip_id, e.entry_no, l.weight_grams, l.fineness_permille, l.eq21_analytical, l.eq21_posted, l.posting_difference "
            "FROM market_trip_book_lines l JOIN market_trip_entries e ON e.id = l.entry_id "
            "WHERE l.entry_id = %s AND l.line_no = %s AND l.entry_kind = 'BK_RETURN' AND l.basis = 'FINENESS'" + (" FOR UPDATE OF l" if lock else ""),
            (_id(entry_id), int(line_no)))

    def stock_balance(self, entry_id, line_no):
        """(grams, posted equivalent) CURRENTLY in the shop for one item: the SUM of every movement (a locking read for a decision)."""
        row = self._fetch_one(
            "SELECT COALESCE(SUM(grams_delta), 0) AS grams, COALESCE(SUM(eq21_posted_delta), 0) AS posted FROM shop_assayed_stock_movements "
            "WHERE item_entry_id = %s AND item_line_no = %s FOR SHARE", (_id(entry_id), int(line_no)))
        return row["grams"], row["posted"]

    def entry_stock_items(self, entry_id):
        """The assayed-stock items a BK_RETURN entry created (its FINENESS lines)."""
        return self._fetch_all(
            "SELECT l.line_no, l.weight_grams, l.fineness_permille, l.eq21_analytical, l.eq21_posted, l.posting_difference FROM market_trip_book_lines l "
            "WHERE l.entry_id = %s AND l.entry_kind = 'BK_RETURN' AND l.basis = 'FINENESS' ORDER BY l.line_no", (_id(entry_id),))

    def stock_movements_of_entry(self, entry_id):
        """Every stock movement that REFERS to an item of this entry other than its own receive / receive-reversal (an issue from the item): what depends on it."""
        return self._fetch_all(
            "SELECT m.id, m.item_line_no, m.movement_type, m.grams_delta, m.eq21_posted_delta, m.source_entry_id FROM shop_assayed_stock_movements m "
            "WHERE m.item_entry_id = %s AND m.movement_type NOT IN ('RECEIVE', 'RECEIVE_REVERSAL') ORDER BY m.id FOR SHARE", (_id(entry_id),))

    def assayed_stock_items(self):
        """DISPLAY ONLY (Gold Position and the trip screens): every assayed-stock item with grams or posted equivalent still in the shop, with its facts
        and origin, in ONE aggregate query without locks."""
        return self._fetch_all(
            """
            SELECT l.entry_id, l.line_no, e.trip_id, e.entry_no, l.fineness_permille, l.weight_grams AS original_weight, l.eq21_posted AS original_posted,
                   COALESCE(SUM(m.grams_delta), 0) AS grams, COALESCE(SUM(m.eq21_posted_delta), 0) AS posted
            FROM shop_assayed_stock_movements m
            JOIN market_trip_book_lines l ON l.entry_id = m.item_entry_id AND l.line_no = m.item_line_no
            JOIN market_trip_entries e ON e.id = l.entry_id
            GROUP BY l.entry_id, l.line_no, e.trip_id, e.entry_no, l.fineness_permille, l.weight_grams, l.eq21_posted
            HAVING SUM(m.grams_delta) <> 0 OR SUM(m.eq21_posted_delta) <> 0
            ORDER BY e.trip_id, e.entry_no, l.line_no
            """)

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
