"""موضع الذهب: one READ-ONLY view of where ALL shop-owned gold is, in physical grams and in 21K equivalent.

Sources (each a derived balance of the ledger that owns it; nothing here writes or re-derives a rule):
  SHOP        Scrap (vw_old_gold_available: physical / held / available), whole lots in the shop, legacy receipt stock,
              finished jewelry in stock (IN_STOCK) and returned pieces awaiting inspection (RETURNED_PENDING)
  CUSTODY     every market trip's outstanding gold per (karat, designation) and the assayed lots currently in it
  INTERMEDIARY  shop-owned gold held by a مسوقجي: assayed lot pools, master-karat LOGO pools and the marketer account's 21K gold (055)

No double counting: a gram has exactly one location because each ledger posts an explicit OUT where the gram leaves and an IN where it
arrives. Lots are additionally checked to appear in ONE place only (a conflict becomes a visible warning, never a silent sum).

21K equivalent: a master karat K converts as grams x K / 21; an assayed lot as grams x fineness / 1000 x 24 / 21 (pure x 24 / 21, the
same Eq21 definition as a melt analysis). A gram whose assay is unknown (a legacy receipt piece, an unassayed residue) is NEVER
inferred: it is listed in physical grams and reported apart as «غير محسوب» and is not in any 21K figure.

Strict separation: supplier payables / receivables are never read, shown or netted here. Cash (including the marketer's cash) is out of scope (gold only).
Every custody balance of EVERY trip is derived (no list limit): a total is never silently truncated. The custody gold / lots come from two display-only aggregations
(`custody_gold_by_trip`, `custody_lots_by_trip`: fixed number of queries, NO locking reads) so the whole read is ONE snapshot; the locking `custody_gold` / `custody_lots`
stay the reads of posting and correction decisions.
Every figure is computed in exact Decimal; each displayed figure is quantized once (0.001 g, ROUND_HALF_UP) from the exact sums.
One REPEATABLE READ transaction, read only, always rolled back. Rights (re-checked in the transaction against the database):
MANAGEMENT_REVIEW + MARKET_TRIP_MANAGE + INTERMEDIARY_HOLDING_VIEW: a partial total would mislead, so a missing right refuses the whole read.
"""
from contextlib import contextmanager
from decimal import ROUND_HALF_UP, Decimal, localcontext

from repositories.inventory_repository import InventoryRepository
from repositories.intermediary_holding_repository import IntermediaryHoldingRepository
from repositories.market_trip_repository import MarketTripRepository
from repositories.marketer_custody_repository import MarketerCustodyRepository
from services.authorization_service import AuthorizationService
from services.market_trip_service import custody_view

REQUIRED_PERMISSIONS = ("MANAGEMENT_REVIEW", "MARKET_TRIP_MANAGE", "INTERMEDIARY_HOLDING_VIEW")
BASE_KARAT = Decimal(21)
STEP = Decimal("0.001")
ZERO = Decimal("0")
DESIGNATION_TEXT = {"MELT": "للسبك", "LOGO": "لوجو", "UNCLASSIFIED": "غير مصنّف"}


class _Discard(Exception):
    pass


@contextmanager
def read_transaction():
    """One consistent, read-only snapshot (REPEATABLE READ), always rolled back."""
    from config.database_config import DatabaseConfig
    from database.connection import DatabaseConnection
    connection = DatabaseConnection(DatabaseConfig.from_env()).connect()
    try:
        cursor = connection.cursor()
        try:
            cursor.execute("SET SESSION innodb_lock_wait_timeout = 10")
        finally:
            cursor.close()
        connection.start_transaction(isolation_level="REPEATABLE READ", readonly=True)
        yield connection
    finally:
        try:
            connection.rollback()
        finally:
            connection.close()


def _dec(value):
    return Decimal(str(value if value is not None else 0))


def eq21_karat(grams, karat):
    """Exact 21K equivalent of master-karat grams (not quantized)."""
    with localcontext() as context:
        context.prec = 50
        return _dec(grams) * _dec(karat) / BASE_KARAT


def eq21_lot(grams, fineness_permille):
    """Exact 21K equivalent of assayed grams (pure x 24 / 21); None when the assay is unknown (never inferred)."""
    if fineness_permille is None:
        return None
    with localcontext() as context:
        context.prec = 50
        return _dec(grams) * _dec(fineness_permille) / Decimal(1000) * Decimal(24) / BASE_KARAT


def _q(value):
    return None if value is None else value.quantize(STEP, rounding=ROUND_HALF_UP)


def _grams(value):
    return _q(_dec(value))


class _Bucket:
    """Exact running sums of one location: physical grams, known 21K and the grams whose assay is unknown."""

    def __init__(self):
        self.grams, self.eq21, self.unknown = ZERO, ZERO, ZERO

    def add(self, grams, eq21):
        grams = _dec(grams)
        self.grams += grams
        if eq21 is None:
            self.unknown += grams
        else:
            self.eq21 += eq21

    def as_row(self):
        return {"grams": _q(self.grams), "eq21": _q(self.eq21), "unknown_grams": _q(self.unknown)}


def build_position(*, scrap, shop_lots, receipt_stock, inventory, returned_pending, trips, pools, logo_pools, marketer_gold=()):
    """Pure aggregation of already-fetched rows (the service reads, this derives; it is unit-tested with plain rows).

    scrap              rows of vw_old_gold_available (karat_value, physical_grams, held_grams, available_grams)
    shop_lots          MarketTripRepository.shop_lots() rows
    receipt_stock      legacy receipt pieces still in shop stock (piece_id, party_name, receipt_id, stock_grams)
    inventory          [{karat_value, weight_grams}] IN_STOCK finished pieces
    returned_pending   [{karat_value, weight_grams}] RETURNED_PENDING pieces (physically back in the shop)
    trips              [{"code", "status", "custody": custody_view(...)}] (only gold / lots are read)
    pools / logo_pools open holding pools (balance > 0)
    marketer_gold      [{party_id, party_name, balance}] the 21K physical grams of each marketer account (055), from its signed ledger
    """
    shop, custody, intermediary = _Bucket(), _Bucket(), _Bucket()
    holding_part, marketer_part = _Bucket(), _Bucket()
    parts = {"scrap": _Bucket(), "lots": _Bucket(), "receipts": _Bucket(), "inventory": _Bucket(), "returned": _Bucket()}
    lot_places, warnings = {}, []

    def place(lot_id, where):
        lot_places.setdefault(str(lot_id), []).append(where)

    out = {}
    # ---- SHOP
    scrap_rows = []
    for r in sorted(scrap, key=lambda x: -int(x["karat_value"])):
        physical, held, available = _dec(r["physical_grams"]), _dec(r["held_grams"]), _dec(r["available_grams"])
        if physical == 0 and held == 0 and available == 0:
            continue
        eq = eq21_karat(physical, r["karat_value"])
        scrap_rows.append({"karat": int(r["karat_value"]), "physical": _grams(physical), "held": _grams(held),
                           "available": _grams(available), "eq21": _q(eq)})
        parts["scrap"].add(physical, eq)
    lot_rows = []
    for r in shop_lots:
        stock = _dec(r["stock_grams"])
        eq = eq21_lot(stock, r.get("fineness_permille"))
        lot_rows.append({"lot_id": str(r["id"]), "kind": r["lot_kind"], "trip_id": str(r["trip_id"]), "entry_no": int(r["entry_no"]),
                         "lot_no": int(r["lot_no"]), "label": r.get("label") or "", "grams": _grams(stock),
                         "fineness": None if r.get("fineness_permille") is None else _dec(r["fineness_permille"]), "eq21": _q(eq)})
        parts["lots"].add(stock, eq)
        place(r["id"], "SHOP")
    receipt_rows = []
    for r in receipt_stock:
        stock = _dec(r["stock_grams"])
        if stock <= 0:
            continue
        receipt_rows.append({"piece_id": str(r["piece_id"]), "receipt_id": str(r["receipt_id"]), "party": r.get("party_name") or "",
                             "grams": _grams(stock), "eq21": None})
        parts["receipts"].add(stock, None)            # the first slice records an UNKNOWN assay only: never inferred
    inventory_rows = []
    for kind, source, bucket in (("IN_STOCK", inventory, parts["inventory"]), ("RETURNED_PENDING", returned_pending, parts["returned"])):
        for r in sorted(source, key=lambda x: -int(x["karat_value"])):
            weight = _dec(r["weight_grams"])
            if weight == 0:
                continue
            eq = eq21_karat(weight, r["karat_value"])
            inventory_rows.append({"status": kind, "karat": int(r["karat_value"]), "grams": _grams(weight), "eq21": _q(eq)})
            bucket.add(weight, eq)
    for bucket in parts.values():
        shop.grams += bucket.grams
        shop.eq21 += bucket.eq21
        shop.unknown += bucket.unknown
    out["shop"] = {"scrap": scrap_rows, "lots": lot_rows, "receipts": receipt_rows, "inventory": inventory_rows,
                   "parts": {name: bucket.as_row() for name, bucket in parts.items()}, **shop.as_row()}

    # ---- CUSTODY (outstanding only; a settled trip has nothing to show)
    trip_rows = []
    for t in trips:
        view = t["custody"]
        gold_rows, lots = [], []
        for g in view["gold"]:
            weight = _dec(g["outstanding"])
            if weight == 0:
                continue
            eq = eq21_karat(weight, g["karat_value"])
            gold_rows.append({"karat": int(g["karat_value"]), "designation": g["designation"],
                              "designation_text": DESIGNATION_TEXT.get(g["designation"], g["designation"]),
                              "grams": _grams(weight), "eq21": _q(eq)})
            custody.add(weight, eq)
        for l in view["lots"]:
            weight = _dec(l["outstanding"])
            if weight == 0:
                continue
            eq = eq21_lot(weight, l["fineness"])
            lots.append({"lot_id": l["lot_id"], "kind": l["kind"], "code": l.get("code") or "", "grams": _grams(weight),
                         "fineness": l["fineness"], "eq21": _q(eq)})
            custody.add(weight, eq)
            place(l["lot_id"], f"CUSTODY:{t['code']}")
        if gold_rows or lots:
            trip_rows.append({"trip_id": str(t["id"]), "code": t["code"], "status": t["status"], "gold": gold_rows, "lots": lots})
    out["custody"] = {"trips": trip_rows, **custody.as_row()}

    # ---- INTERMEDIARY holdings (still shop-owned)
    parties = {}
    for r in pools:
        balance = _dec(r["balance"])
        if balance <= 0:
            continue
        eq = eq21_lot(balance, r.get("fineness_permille"))
        row = parties.setdefault(str(r["party_id"]), {"party_id": str(r["party_id"]), "party_name": r["party_name"], "lots": [], "logo": [], "marketer": None})
        row["lots"].append({"pool_id": str(r["pool_id"]), "lot_id": str(r["lot_id"]), "kind": r["lot_kind"], "grams": _grams(balance),
                            "original": _grams(r["weight_grams"]), "fineness": None if r.get("fineness_permille") is None else _dec(r["fineness_permille"]),
                            "eq21": _q(eq)})
        intermediary.add(balance, eq)
        holding_part.add(balance, eq)
        place(r["lot_id"], f"INTERMEDIARY:{r['party_name']}")
    for r in logo_pools:
        balance = _dec(r["balance"])
        if balance <= 0:
            continue
        eq = eq21_karat(balance, r["karat_value"])
        row = parties.setdefault(str(r["party_id"]), {"party_id": str(r["party_id"]), "party_name": r["party_name"], "lots": [], "logo": [], "marketer": None})
        row["logo"].append({"pool_id": str(r["pool_id"]), "karat": int(r["karat_value"]), "grams": _grams(balance), "eq21": _q(eq)})
        intermediary.add(balance, eq)
        holding_part.add(balance, eq)
    # the marketer account (055): 21K physical grams = 21K equivalent, counted once per party from its own ledger (never from the LOT / LOGO pools above)
    for r in marketer_gold:
        balance = _dec(r["balance"])
        if balance == 0:
            continue
        if balance < 0:                                    # the ledger forbids it: show it, never net it against another holding
            warnings.append({"code": "MARKETER_GOLD_NEGATIVE", "party_id": str(r["party_id"]), "party_name": r["party_name"], "grams": _grams(balance)})
            continue
        eq = eq21_karat(balance, 21)
        row = parties.setdefault(str(r["party_id"]), {"party_id": str(r["party_id"]), "party_name": r["party_name"], "lots": [], "logo": [], "marketer": None})
        row["marketer"] = {"grams": _grams(balance), "eq21": _q(eq)}
        intermediary.add(balance, eq)
        marketer_part.add(balance, eq)
    out["intermediary"] = {"parties": sorted(parties.values(), key=lambda p: p["party_name"]), **intermediary.as_row(),
                           "parts": {"holdings": holding_part.as_row(), "marketer": marketer_part.as_row()}}

    # ---- one place per lot: a conflict is shown, never summed silently
    for lot_id, where in lot_places.items():
        if len(where) > 1:
            warnings.append({"code": "LOT_IN_SEVERAL_PLACES", "lot_id": lot_id, "places": where})

    total = {"grams": shop.grams + custody.grams + intermediary.grams, "eq21": shop.eq21 + custody.eq21 + intermediary.eq21,
             "unknown": shop.unknown + custody.unknown + intermediary.unknown}
    out["summary"] = {"shop_eq21": _q(shop.eq21), "custody_eq21": _q(custody.eq21), "intermediary_eq21": _q(intermediary.eq21),
                      "total_eq21": _q(total["eq21"]), "total_grams": _q(total["grams"]), "unknown_grams": _q(total["unknown"]),
                      "unknown_in_shop": _q(shop.unknown), "unknown_in_custody": _q(custody.unknown),
                      "unknown_with_intermediary": _q(intermediary.unknown)}
    out["warnings"] = warnings
    return out


class GoldPositionService:
    def __init__(self, session, *, transaction_factory=read_transaction, authorization=None,
                 trip_repository=MarketTripRepository, holding_repository=IntermediaryHoldingRepository,
                 inventory_repository=InventoryRepository, marketer_repository=MarketerCustodyRepository):
        self._session = session
        self._transaction = transaction_factory
        self._authorization = authorization or AuthorizationService(session)
        self._trips, self._holdings, self._inventory = trip_repository, holding_repository, inventory_repository
        self._marketers = marketer_repository

    def can_view(self):
        actor = self._session.current_actor()
        return actor is not None and set(REQUIRED_PERMISSIONS) <= set(actor.permissions)

    def position(self, *, _expected_actor=None):
        actor = self._session.require_authenticated()
        if _expected_actor is not None and not self._session.is_current(_expected_actor):
            raise PermissionError("انتهت جلسة الدخول.")
        if not set(REQUIRED_PERMISSIONS) <= set(actor.permissions):
            raise PermissionError("موضع الذهب للمالك فقط.")
        try:
            with self._transaction() as connection:
                for code in REQUIRED_PERMISSIONS:
                    self._authorization.require_permission_read(connection, actor, code)
                trips_repo, holdings, inventory = self._trips(connection), self._holdings(connection), self._inventory(connection)
                marketers = self._marketers(connection)                    # the SAME connection / snapshot as every other source
                # ONE snapshot, no locks: eight aggregate queries whatever the number of trips (never a query per trip, never the cash or a document history)
                gold_by_trip, lots_by_trip = trips_repo.custody_gold_by_trip(), trips_repo.custody_lots_by_trip()
                trips = []
                for row in trips_repo.all_trip_ids():                       # EVERY trip: no list limit on a physical total
                    custody = custody_view(("0.00", "0.00"), gold_by_trip.get(int(row["id"]), []), lots_by_trip.get(int(row["id"]), []), trip_id=row["id"])
                    if any(_dec(g["outstanding"]) != 0 for g in custody["gold"]) or custody["lots"]:
                        trips.append({"id": row["id"], "code": f"MT{int(row['id']):06d}", "status": row["status"], "custody": custody})
                result = build_position(
                    scrap=trips_repo.shop_scrap(), shop_lots=trips_repo.shop_lots(), receipt_stock=holdings.receipt_stock(),
                    inventory=inventory.get_stock_weight_by_karat(), returned_pending=inventory.get_returned_pending_weight_by_karat(),
                    trips=trips, pools=holdings.open_pools(), logo_pools=holdings.logo_open_pools(), marketer_gold=marketers.party_gold_balances())
                raise _Discard(result)
        except _Discard as done:
            return done.args[0]
