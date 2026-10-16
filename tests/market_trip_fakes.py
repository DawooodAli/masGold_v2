"""In-memory doubles for the market-trip tests: a transactional fake database (a failed transaction restores every table, so
atomicity is testable), the repository contracts of MarketTripRepository / the Scrap lock / the cash account / the
CorrectionRepository subset a full reversal uses, with the 037/039/040 database checks and unique keys. The SQL itself is proven by the
schema tests and the rehearsal run; real-MySQL concurrency is NOT claimed from anything in here."""
import copy
from contextlib import contextmanager
from datetime import datetime
from decimal import Decimal as D

from mysql.connector import IntegrityError

from app.session import AppSession
from repositories.market_trip_repository import (CASH_TYPE, CREATE_MOVEMENT, DESIGNATIONS, ISSUE, MELT_DESIGNATION, SCRAP_TYPE,
                                                 SHOP_ISSUE_MOVEMENT, SHOP_ISSUE_OUT, SHOP_ISSUE_REVERSAL, SHOP_RETURN_IN, SHOP_RETURN_MOVEMENT,
                                                 SHOP_RETURN_REVERSAL, SOURCE, UNCLASSIFIED)
from services.authentication_service import AuthenticationResult, _VERIFIED
from tests.auth_fakes import FakeAuthorization

OWNER = frozenset({"APP_LOGIN", "MARKET_TRIP_MANAGE", "TRANSACTION_CORRECT", "SUPPLIER_MAKING_PAY", "SUPPLIER_MAKING_PAY_REVERSE",
                   "SUPPLIER_GOLD_SETTLE", "SUPPLIER_GOLD_SETTLE_REVERSE",
                   "INTERMEDIARY_HOLDING_VIEW", "INTERMEDIARY_HOLDING_MANAGE", "INTERMEDIARY_HOLDING_REVERSE"})
OWNER_NO_CORRECT = frozenset({"APP_LOGIN", "MARKET_TRIP_MANAGE"})
MANAGER = frozenset({"APP_LOGIN", "TRANSACTION_CORRECT", "SUPPLIER_MAKING_PAY"})


def session_for(permissions=OWNER, user_id=7):
    session = AppSession()
    session._establish(AuthenticationResult(user_id, None, "المالك", frozenset(permissions), _VERIFIED), int(session.generation))
    return session


class Db:
    def __init__(self):
        self.now = datetime(2026, 10, 10, 10, 0, 0)
        self.karats = {1: {"id": 1, "karat_value": 21, "name": "21K", "is_active": 1},
                       2: {"id": 2, "karat_value": 18, "name": "18K", "is_active": 1},
                       3: {"id": 3, "karat_value": 24, "name": "24K", "is_active": 1},
                       4: {"id": 4, "karat_value": 22, "name": "22K", "is_active": 0}}
        self.cash_accounts = {1: {"id": 1, "code": "MAIN_SAFE", "name": "الخزنة الرئيسية", "is_active": 1}}
        self.cash_moves = [{"id": 1, "account_id": 1, "amount_delta": D("50000.00"), "reference_type": "OPENING", "reference_id": "0",
                            "type_code": "OPENING_BALANCE"}]
        # counted Scrap: 100 g of 21K (5 g of it HELD for manager review), 50 g of 18K, none of 24K
        self.scrap = [{"id": 1, "karat_id": 1, "weight_delta": D("100.000"), "reference_type": "OPENING", "reference_id": "0", "type_code": "OPENING"},
                      {"id": 2, "karat_id": 2, "weight_delta": D("50.000"), "reference_type": "OPENING", "reference_id": "0", "type_code": "OPENING"}]
        self.holds = {1: D("5.000")}
        self.trips, self.entries, self.cash_legs, self.gold_legs, self.events = {}, {}, [], [], []
        self.melts, self.melt_inputs, self.lots, self.lot_moves = {}, [], {}, []          # 039
        self.heats, self.heat_lines = {}, []                                                  # 042: Logo heating headers and lines
        self.suppliers = {10: {"id": 10, "name": "مورد نشط", "is_active": 1, "is_supplier": True, "is_intermediary": False, "karat_id": 1},
                          11: {"id": 11, "name": "مورد متوقف", "is_active": 0, "is_supplier": True, "is_intermediary": False, "karat_id": 2},
                          12: {"id": 12, "name": "وسيط فقط", "is_active": 1, "is_supplier": False, "is_intermediary": True, "karat_id": None}}   # 043 / 044
        self.settlements, self.settlement_legs, self.sup_gold = {}, [], []                      # 045: SG documents, legs and the supplier gold ledger
        self.lot_settle_links = {}
        self.holding_pools, self.holding_docs, self.holding_moves, self.holding_links = {}, {}, [], {}      # 048
        self.receipts, self.pieces, self.stock_moves = {}, {}, []                                          # 051: HR documents, RP pieces, shop receipt stock
        self.logo_pools, self.logo_docs, self.logo_moves, self.logo_links = {}, {}, [], {}                  # 049
        self.gold_settle_links = {}                                                              # 046: entry id -> the SG document it made
        self.gold_sales = {}                                                                     # 053: entry id -> its sale document
        self.gold_buys, self.gold_buy_lines = {}, []                                             # 054: entry id -> purchase document, and its scrap lines
        self.sm_payments, self.sm_making, self.sm_legs, self.supplier_links = {}, [], [], {}
        self.shop_moves, self.lot_returns, self.lot_issues = [], [], []                      # 040/041: shop-side lot ledger, return / issue links
        self.corrections, self.audit_rows = [], []
        self.sequences = {"trip": 0, "entry": 0, "cash_move": 1, "scrap": 2, "correction": 0, "lot": 0, "lot_move": 0, "shop_move": 0,
                          "payment": 0, "sm_making": 0, "settlement": 0, "sup_gold": 0, "pool": 0, "doc": 0, "hmove": 0, "lpool": 0, "ldoc": 0, "lmove": 0, "receipt": 0, "piece": 0, "smove": 0}
        self.fail_at = None
        self.transactions = 0
        self.lock_log = []          # resource acquisitions in order, kept across a rollback so a refused post still shows what it locked

    def next(self, name):
        self.sequences[name] += 1
        return self.sequences[name]

    @contextmanager
    def transaction(self):
        self.transactions += 1
        snapshot = copy.deepcopy({k: v for k, v in self.__dict__.items() if k not in ("fail_at", "transactions", "lock_log")})
        try:
            yield self
        except BaseException:
            for key, value in snapshot.items():
                setattr(self, key, value)
            raise

    def cursor(self, dictionary=False):
        return _AuditCursor(self)

    # ---- helpers the tests read
    def cash(self, account=1):
        return sum((m["amount_delta"] for m in self.cash_moves if m["account_id"] == account), D("0.00"))

    def physical(self, karat_id):
        return sum((m["weight_delta"] for m in self.scrap if m["karat_id"] == karat_id), D("0.000"))

    def available(self, karat_id):
        return self.physical(karat_id) - self.holds.get(karat_id, D("0.000"))


class _AuditCursor:
    def __init__(self, db):
        self.db = db

    def execute(self, sql, params=()):
        assert sql.startswith("INSERT INTO audit_log"), sql[:60]
        if self.db.fail_at == "audit":
            raise RuntimeError("forced failure at audit")
        self.db.audit_rows.append({"user_id": params[0], "event_type": params[1], "entity_type": params[2],
                                   "entity_id": params[3], "reference": params[4]})

    def close(self):
        pass


def _dup(name):
    return IntegrityError(f"1062 (23000): Duplicate entry 'x' for key 'market_trip_entries.{name}'", errno=1062)


class TripRepo:
    """MarketTripRepository contract with the 037 checks (chk_*), unique keys and insert-only legs."""

    def __init__(self, db):
        self.db = db

    def _fail(self, step):
        if self.db.fail_at == step:
            raise RuntimeError(f"forced failure at {step}")

    def db_now(self):
        return self.db.now

    def main_safe(self):
        row = next((a for a in self.db.cash_accounts.values() if a["code"] == "MAIN_SAFE"), None)
        return None if row is None else dict(row)

    def cash_balance(self, account_id):
        return self.db.cash(int(account_id))

    def karat(self, karat_id):
        row = self.db.karats.get(int(karat_id))
        return None if row is None else dict(row)

    def active_karats(self):
        return [dict(k) for k in sorted(self.db.karats.values(), key=lambda k: -k["karat_value"]) if k["is_active"]]

    def suppliers(self):
        return [{"id": r["id"], "name": r["name"], "is_active": r["is_active"], "karat_id": r.get("karat_id"),
                 "karat_value": None if r.get("karat_id") is None else self.db.karats[r["karat_id"]]["karat_value"],
                 "making_balance": sum((m["amount_delta"] for m in self.db.sm_making if m["supplier_id"] == r["id"]), D("0.00"))}
                for r in sorted(self.db.suppliers.values(), key=lambda r: (-r["is_active"], r["name"], r["id"])) if r.get("is_supplier", True)]

    def shop_scrap(self):
        return [{"karat_id": k["id"], "karat_value": k["karat_value"], "physical_grams": self.db.physical(k["id"]),
                 "held_grams": self.db.holds.get(k["id"], D("0.000")), "available_grams": self.db.available(k["id"])}
                for k in sorted(self.db.karats.values(), key=lambda k: -k["karat_value"]) if k["is_active"]]

    def intermediaries(self):
        return [{"id": r["id"], "name": r["name"], "is_active": r["is_active"], "is_supplier": r.get("is_supplier", True)}
                for r in sorted(self.db.suppliers.values(), key=lambda r: (-r["is_active"], r["name"], r["id"])) if r.get("is_intermediary")]

    # ---- trips
    def insert_trip(self, *, notes, actor, now):
        self._fail("trip")
        self.db.lock_log.append(("trip",))
        trip_id = self.db.next("trip")
        self.db.trips[trip_id] = {"id": trip_id, "status": "OPEN", "notes": notes, "created_by_user_id": actor.user_id,
                                  "created_at": now, "status_changed_at": now}
        return trip_id

    def trip(self, trip_id, lock=False):
        if lock:
            self.db.lock_log.append(("trip",))
        row = self.db.trips.get(int(trip_id))
        return None if row is None else dict(row)

    def set_status(self, *, trip_id, status, now):
        assert status in ("OPEN", "CLOSED")                                                  # chk_mt_status
        self.db.trips[int(trip_id)].update(status=status, status_changed_at=now)

    def insert_event(self, *, trip_id, event_type, reason, actor, now):
        self._fail("event")
        assert event_type in ("OPENED", "CLOSED", "REOPENED")                                # chk_mtse_type
        assert event_type != "REOPENED" or (reason or "").strip()                            # chk_mtse_reason
        self.db.events.append({"trip_id": int(trip_id), "event_type": event_type, "reason": reason,
                               "actor_name": "المالك", "occurred_at": now})

    def events(self, trip_id):
        return [dict(e) for e in self.db.events if e["trip_id"] == int(trip_id)]

    def trips(self, limit=100):
        return [{**t, "entry_count": sum(1 for e in self.db.entries.values() if e["trip_id"] == t["id"])}
                for t in sorted(self.db.trips.values(), key=lambda t: -t["id"])][:limit]

    # ---- entries
    def by_request(self, key):
        row = next((e for e in self.db.entries.values() if e["request_key"] == key), None)
        return None if row is None else dict(row)

    def next_entry_no(self, trip_id):
        return 1 + max((e["entry_no"] for e in self.db.entries.values() if e["trip_id"] == int(trip_id)), default=0)

    def insert_entry(self, *, trip_id, entry_no, kind, entry_date, notes, key, digest, actor, now):
        self._fail("entry")
        assert kind in ("ISSUE", "RETURN", "MELT", "LOT_RETURN", "LOT_ISSUE", "HEAT", "SUPPLIER_PAY", "GOLD_SETTLE", "LOT_SETTLE", "LOT_HOLD", "HOLD_RETURN", "LOGO_HOLD", "HOLD_BACK", "GOLD_SALE", "GOLD_BUY")                # chk_mte_kind
        if any(e["request_key"] == key for e in self.db.entries.values()):
            raise _dup("uq_mte_request")
        if any((e["trip_id"], e["entry_no"]) == (int(trip_id), entry_no) for e in self.db.entries.values()):
            raise _dup("uq_mte_no")
        entry_id = self.db.next("entry")
        self.db.entries[entry_id] = {"id": entry_id, "trip_id": int(trip_id), "entry_no": entry_no, "entry_kind": kind,
                                     "entry_date": entry_date, "notes": notes, "request_key": key, "request_hash": digest,
                                     "performed_by_user_id": actor.user_id, "occurred_at": now}
        return entry_id

    def post_cash(self, *, kind, account_id, entry_id, code, amount, description, actor, now):
        self._fail("cash")
        move_id = self.db.next("cash_move")
        self.db.cash_moves.append({"id": move_id, "account_id": int(account_id),
                                   "amount_delta": -amount if kind == ISSUE else amount, "reference_type": SOURCE,
                                   "reference_id": str(entry_id), "reference_code": code, "type_code": CASH_TYPE[kind]})
        return move_id

    def post_scrap(self, *, kind, karat_id, weight, entry_id, code, notes, actor, now):
        self._fail("scrap")
        assert weight > 0
        move_id = self.db.next("scrap")
        self.db.scrap.append({"id": move_id, "karat_id": int(karat_id), "weight_delta": -weight if kind == ISSUE else weight,
                              "reference_type": SOURCE, "reference_id": str(entry_id), "reference_code": code,
                              "type_code": SCRAP_TYPE[kind]})
        return move_id

    def insert_cash_leg(self, *, entry_id, amount, cash_account_id, cash_movement_id):
        self._fail("leg")
        assert amount > 0                                                                    # chk_mtec_amount
        if any(l["entry_id"] == int(entry_id) for l in self.db.cash_legs):
            raise _dup("uq_mtec_entry")
        self.db.cash_legs.append({"entry_id": int(entry_id), "amount": amount, "cash_account_id": int(cash_account_id),
                                  "cash_movement_id": cash_movement_id})

    def insert_gold_leg(self, *, entry_id, karat_id, designation, weight, scrap_movement_id):
        self._fail("leg")
        assert weight > 0                                                                    # chk_mteg_weight
        assert designation in DESIGNATIONS                                                   # chk_mteg_designation
        if any((l["entry_id"], l["karat_id"], l["designation"]) == (int(entry_id), int(karat_id), designation) for l in self.db.gold_legs):
            raise _dup("uq_mteg_entry_karat_des")
        if any(l["scrap_movement_id"] == scrap_movement_id for l in self.db.gold_legs):
            raise _dup("uq_mteg_movement")
        self.db.gold_legs.append({"entry_id": int(entry_id), "karat_id": int(karat_id), "designation": designation,
                                  "weight_grams": weight, "scrap_movement_id": scrap_movement_id})

    # ---- melting (039): the CHECK constraints of the four tables are mirrored as asserts
    def insert_melt(self, *, entry_id, workshop, assay_reference, gain_explanation, analysis):
        self._fail("melt")
        assert (workshop or "").strip()                                                      # chk_mtm_workshop
        assert analysis["input_weight"] > 0 and analysis["output_weight"] > 0                # chk_mtm_weights
        assert analysis["weight_difference"] == analysis["output_weight"] - analysis["input_weight"]
        assert analysis["output_weight"] <= analysis["input_weight"] or (gain_explanation or "").strip()     # chk_mtm_gain
        assert analysis["pure_complete"] == (analysis["pure_difference"] is not None) == (analysis["eq21_difference"] is not None)
        assert int(entry_id) not in self.db.melts and self.db.entries[int(entry_id)]["entry_kind"] == "MELT"
        self.db.melts[int(entry_id)] = {"entry_id": int(entry_id), "workshop": workshop, "assay_reference": assay_reference,
                                        "gain_explanation": gain_explanation,
                                        **{k: v for k, v in analysis.items()}}

    def insert_melt_input(self, *, entry_id, karat_id, weight, pure):
        self._fail("melt_input")
        assert weight > 0 and pure > 0                                                       # chk_mtmi_weight
        if any((i["entry_id"], i["karat_id"]) == (int(entry_id), int(karat_id)) for i in self.db.melt_inputs):
            raise _dup("uq_mtmi_entry_karat")
        self.db.melt_inputs.append({"entry_id": int(entry_id), "karat_id": int(karat_id), "weight_grams": weight, "pure_grams": pure})

    def insert_lot(self, *, entry_id, lot_no, kind, label, weight, fineness, pure):
        self._fail("lot")
        assert kind in ("MAIN", "RESIDUE") and ((kind == "MAIN") == (lot_no == 1))           # chk_mtl_kind
        assert weight > 0                                                                    # chk_mtl_weight
        assert fineness is None or 0 < fineness <= 1000                                      # chk_mtl_fineness
        assert kind != "MAIN" or fineness is not None
        assert (fineness is None) == (pure is None)
        if any((l["entry_id"], l["lot_no"]) == (int(entry_id), lot_no) for l in self.db.lots.values()):
            raise _dup("uq_mtl_entry_no")
        lot_id = self.db.next("lot")
        self.db.lots[lot_id] = {"id": lot_id, "entry_id": int(entry_id), "lot_no": lot_no, "lot_kind": kind, "label": label,
                                "weight_grams": weight, "fineness_permille": fineness, "pure_grams": pure}
        return lot_id

    def insert_lot_movement(self, *, lot_id, entry_id, movement_type, weight_delta):
        self._fail("lot_movement")
        assert movement_type in (CREATE_MOVEMENT, SHOP_RETURN_MOVEMENT, SHOP_ISSUE_MOVEMENT, "SUPPLIER_SETTLE", "HOLD_OUT", "HOLD_IN", "SALE_OUT")  # chk_mtlm_type
        assert movement_type != "SALE_OUT" or weight_delta < 0                                # chk_mtlm_delta (053)
        assert movement_type != "HOLD_OUT" or weight_delta < 0                               # chk_mtlm_delta (048)
        assert movement_type != "HOLD_IN" or weight_delta > 0
        assert weight_delta != 0 and (movement_type != CREATE_MOVEMENT or weight_delta > 0)  # chk_mtlm_delta
        assert movement_type != SHOP_RETURN_MOVEMENT or weight_delta < 0
        assert movement_type != SHOP_ISSUE_MOVEMENT or weight_delta > 0
        if any((m["lot_id"], m["entry_id"], m["movement_type"]) == (int(lot_id), int(entry_id), movement_type) for m in self.db.lot_moves):
            raise _dup("uq_mtlm_lot_entry_type")
        move_id = self.db.next("lot_move")
        self.db.lot_moves.append({"id": move_id, "lot_id": int(lot_id), "entry_id": int(entry_id), "movement_type": movement_type,
                                  "weight_delta": weight_delta})
        return move_id

    # ---- lot returns to the shop (040)
    def lock_lots(self, lot_ids):
        out = []
        self.db.lock_log.append(("lots", tuple(sorted({int(i) for i in lot_ids}))))
        for lot_id in sorted({int(i) for i in lot_ids}):
            lot = self.db.lots.get(lot_id)
            if lot is not None:
                entry = self.db.entries[lot["entry_id"]]
                out.append({**lot, "trip_id": entry["trip_id"], "entry_no": entry["entry_no"]})
        return out

    def insert_shop_lot_movement(self, *, lot_id, entry_id, movement_type, weight_delta, correction_id=None):
        self._fail("shop_movement")
        assert (movement_type == SHOP_RETURN_IN and weight_delta > 0 and correction_id is None) or \
               (movement_type == SHOP_RETURN_REVERSAL and weight_delta < 0 and correction_id is not None) or \
               (movement_type == SHOP_ISSUE_OUT and weight_delta < 0 and correction_id is None) or \
               (movement_type == SHOP_ISSUE_REVERSAL and weight_delta > 0 and correction_id is not None)      # chk_mtlsm_shape
        if any((m["lot_id"], m["entry_id"], m["movement_type"]) == (int(lot_id), int(entry_id), movement_type) for m in self.db.shop_moves):
            raise _dup("uq_mtlsm_lot_entry_type")
        move_id = self.db.next("shop_move")
        self.db.shop_moves.append({"id": move_id, "lot_id": int(lot_id), "entry_id": int(entry_id), "movement_type": movement_type,
                                   "weight_delta": weight_delta, "correction_id": correction_id})
        return move_id

    def insert_lot_issue(self, *, entry_id, lot_id, weight, custody_movement_id, shop_movement_id):
        self._fail("lot_issue")
        assert weight > 0                                                                    # chk_mtli_weight
        for key, value in (("entry_lot", (int(entry_id), int(lot_id))), ("custody", custody_movement_id), ("shop", shop_movement_id)):
            if any(((r["entry_id"], r["lot_id"]) if key == "entry_lot" else r[key + "_movement_id"]) == value for r in self.db.lot_issues):
                raise _dup("uq_mtli_" + key)
        self.db.lot_issues.append({"entry_id": int(entry_id), "lot_id": int(lot_id), "weight_grams": weight,
                                   "custody_movement_id": custody_movement_id, "shop_movement_id": shop_movement_id})

    def entry_lot_issues(self, entry_id):
        return [{**r, "lot_no": self.db.lots[r["lot_id"]]["lot_no"], "melt_entry_id": self.db.lots[r["lot_id"]]["entry_id"]}
                for r in sorted(self.db.lot_issues, key=lambda r: r["lot_id"]) if r["entry_id"] == int(entry_id)]

    def lot_by_id(self, lot_id):
        lot = self.db.lots.get(int(lot_id))
        return None if lot is None else {**lot, "origin_trip_id": self.db.entries[lot["entry_id"]]["trip_id"],
                                         "entry_no": self.db.entries[lot["entry_id"]]["entry_no"]}

    def insert_lot_return(self, *, entry_id, lot_id, weight, custody_movement_id, shop_movement_id):
        self._fail("lot_return")
        assert weight > 0                                                                    # chk_mtlr_weight
        for key, value in (("entry_lot", (int(entry_id), int(lot_id))), ("custody", custody_movement_id), ("shop", shop_movement_id)):
            if any(((r["entry_id"], r["lot_id"]) if key == "entry_lot" else r[key + "_movement_id"]) == value for r in self.db.lot_returns):
                raise _dup("uq_mtlr_" + key)
        self.db.lot_returns.append({"entry_id": int(entry_id), "lot_id": int(lot_id), "weight_grams": weight,
                                    "custody_movement_id": custody_movement_id, "shop_movement_id": shop_movement_id})

    def entry_lot_returns(self, entry_id):
        return [{**r, "lot_no": self.db.lots[r["lot_id"]]["lot_no"], "melt_entry_id": self.db.lots[r["lot_id"]]["entry_id"]}
                for r in sorted(self.db.lot_returns, key=lambda r: r["lot_id"]) if r["entry_id"] == int(entry_id)]

    def shop_lot_balance(self, lot_id):
        return sum((m["weight_delta"] for m in self.db.shop_moves if m["lot_id"] == int(lot_id)), D("0.000"))

    def shop_lots(self):
        rows = []
        for lot in sorted(self.db.lots.values(), key=lambda l: (self.db.entries[l["entry_id"]]["trip_id"],
                                                                 self.db.entries[l["entry_id"]]["entry_no"], l["lot_no"])):
            stock = self.shop_lot_balance(lot["id"])
            if stock > 0:
                entry = self.db.entries[lot["entry_id"]]
                rows.append({"id": lot["id"], "melt_entry_id": lot["entry_id"], "trip_id": entry["trip_id"], "entry_no": entry["entry_no"],
                             "lot_no": lot["lot_no"], "lot_kind": lot["lot_kind"], "label": lot["label"],
                             "original_weight": lot["weight_grams"], "fineness_permille": lot["fineness_permille"], "stock_grams": stock})
        return rows

    # ---- derived custody (entries that were not reversed)
    def _live(self, trip_id, exclude):
        reversed_ids = {int(c["source_id"]) for c in self.db.corrections
                        if c["source_type"] == SOURCE and c["correction_type"] == "FULL_REVERSAL"}
        return [e for e in self.db.entries.values()
                if e["trip_id"] == int(trip_id) and e["id"] not in reversed_ids and e["id"] != int(exclude or 0)]

    def custody_cash(self, trip_id, exclude_entry_id=None, *, current=False):
        issued = returned = paid = proceeds = spent = D("0.00")
        for e in self._live(trip_id, exclude_entry_id):
            for leg in (l for l in self.db.cash_legs if l["entry_id"] == e["id"]):
                issued, returned = (issued + leg["amount"], returned) if e["entry_kind"] == "ISSUE" else (issued, returned + leg["amount"])
            if e["id"] in self.db.supplier_links:
                paid += self.db.supplier_links[e["id"]]["amount"]
            if e["id"] in self.db.gold_sales:
                proceeds += self.db.gold_sales[e["id"]]["total_price"]
            if e["id"] in self.db.gold_buys:
                spent += self.db.gold_buys[e["id"]]["total_price"]
        return issued, returned, paid, proceeds, spent

    def custody_gold(self, trip_id, exclude_entry_id=None, *, current=False):
        out = {}
        live = self._live(trip_id, exclude_entry_id)
        for e in live:
            for leg in (l for l in self.db.gold_legs if l["entry_id"] == e["id"]):
                row = out.setdefault((leg["karat_id"], leg["designation"]), {
                    "karat_id": leg["karat_id"], "karat_value": self.db.karats[leg["karat_id"]]["karat_value"],
                    "designation": leg["designation"], "issued": D("0.000"), "returned": D("0.000"), "melted": D("0.000"),
                    "heat_delta": D("0.000"), "supplier_settled": D("0.000")})
                row["issued" if e["entry_kind"] == "ISSUE" else "returned"] += leg["weight_grams"]
            for i in (i for i in self.db.melt_inputs if i["entry_id"] == e["id"]):
                row = out.setdefault((i["karat_id"], MELT_DESIGNATION), {
                    "karat_id": i["karat_id"], "karat_value": self.db.karats[i["karat_id"]]["karat_value"],
                    "designation": MELT_DESIGNATION, "issued": D("0.000"), "returned": D("0.000"), "melted": D("0.000"),
                    "heat_delta": D("0.000"), "supplier_settled": D("0.000")})
                row["melted"] += i["weight_grams"]
            for h in (h for h in self.db.heat_lines if h["entry_id"] == e["id"]):
                row = out.setdefault((h["karat_id"], "LOGO"), {
                    "karat_id": h["karat_id"], "karat_value": self.db.karats[h["karat_id"]]["karat_value"],
                    "designation": "LOGO", "issued": D("0.000"), "returned": D("0.000"), "melted": D("0.000"), "heat_delta": D("0.000"),
                    "supplier_settled": D("0.000")})
                row["heat_delta"] += h["output_weight_grams"] - h["input_weight_grams"]
            held = self.db.logo_links.get(e["id"])
            if held is not None:                                                                  # 049: handed to / taken back from an intermediary
                row = out.setdefault((held["karat_id"], "LOGO"), {
                    "karat_id": held["karat_id"], "karat_value": self.db.karats[held["karat_id"]]["karat_value"],
                    "designation": "LOGO", "issued": D("0.000"), "returned": D("0.000"), "melted": D("0.000"), "heat_delta": D("0.000"),
                    "supplier_settled": D("0.000")})
                row["held_out" if held["entry_kind"] == "LOGO_HOLD" else "held_back"] = row.get("held_out" if held["entry_kind"] == "LOGO_HOLD" else "held_back", D("0.000")) + held["weight_grams"]
            link = self.db.gold_settle_links.get(e["id"])
            if link is not None:                                                                  # 046: delivered to a supplier from custody
                row = out.setdefault((link["karat_id"], link["designation"]), {
                    "karat_id": link["karat_id"], "karat_value": self.db.karats[link["karat_id"]]["karat_value"],
                    "designation": link["designation"], "issued": D("0.000"), "returned": D("0.000"), "melted": D("0.000"),
                    "heat_delta": D("0.000"), "supplier_settled": D("0.000")})
                row["supplier_settled"] += link["weight_grams"]
            sale = self.db.gold_sales.get(e["id"])
            if sale is not None and sale["source_kind"] == "CUSTODY_GOLD":                         # 053: sold for cash from custody
                row = out.setdefault((sale["karat_id"], sale["designation"]), {
                    "karat_id": sale["karat_id"], "karat_value": self.db.karats[sale["karat_id"]]["karat_value"],
                    "designation": sale["designation"], "issued": D("0.000"), "returned": D("0.000"), "melted": D("0.000"),
                    "heat_delta": D("0.000"), "supplier_settled": D("0.000")})
                row["sold"] = row.get("sold", D("0.000")) + sale["weight_grams"]
            for line in (l for l in self.db.gold_buy_lines if l["entry_id"] == e["id"]):             # 054: bought for cash
                row = out.setdefault((line["karat_id"], line["designation"]), {
                    "karat_id": line["karat_id"], "karat_value": self.db.karats[line["karat_id"]]["karat_value"],
                    "designation": line["designation"], "issued": D("0.000"), "returned": D("0.000"), "melted": D("0.000"),
                    "heat_delta": D("0.000"), "supplier_settled": D("0.000")})
                row["purchased"] = row.get("purchased", D("0.000")) + line["weight_grams"]
        order = {"MELT": 0, "LOGO": 1, "UNCLASSIFIED": 2}
        return sorted(out.values(), key=lambda r: (-r["karat_value"], order[r["designation"]]))

    def custody_lots(self, trip_id, exclude_entry_id=None, *, lot_id=None):
        """Lots CURRENTLY held by this trip's custody: derived from the movements of this trip's live entries, never from the lot's origin."""
        live_ids = {e["id"] for e in self._live(trip_id, exclude_entry_id)}
        rows = []
        for lot in sorted(self.db.lots.values(), key=lambda l: (self.db.entries[l["entry_id"]]["trip_id"],
                                                                 self.db.entries[l["entry_id"]]["entry_no"], l["lot_no"])):
            moves = [m for m in self.db.lot_moves if m["lot_id"] == lot["id"] and m["entry_id"] in live_ids]
            if not moves or (lot_id is not None and lot["id"] != int(lot_id)):
                continue
            origin = self.db.entries[lot["entry_id"]]
            rows.append({**lot, "entry_no": origin["entry_no"], "origin_trip_id": origin["trip_id"],
                         "outstanding_grams": sum((m["weight_delta"] for m in moves), D("0.000"))})
        return rows

    def lot_downstream(self, entry_id):
        reversed_ids = {int(c["source_id"]) for c in self.db.corrections
                        if c["source_type"] == SOURCE and c["correction_type"] == "FULL_REVERSAL"}
        mine = {l["id"] for l in self.db.lots.values() if l["entry_id"] == int(entry_id)}
        seen = {}
        for m in self.db.lot_moves:
            if m["lot_id"] in mine and m["movement_type"] != CREATE_MOVEMENT and m["entry_id"] not in reversed_ids:
                e = self.db.entries[m["entry_id"]]
                seen[e["id"]] = {"entry_id": e["id"], "trip_id": e["trip_id"], "entry_no": e["entry_no"]}
        return list(seen.values())

    def melts(self, trip_id):
        rows = []
        for e in sorted((e for e in self.db.entries.values() if e["trip_id"] == int(trip_id) and e["id"] in self.db.melts),
                        key=lambda e: e["entry_no"]):
            reversal = next((c for c in self.db.corrections if c["source_type"] == SOURCE and c["source_id"] == str(e["id"])
                             and c["correction_type"] == "FULL_REVERSAL"), None)
            m = self.db.melts[e["id"]]
            rows.append({**m, "trip_id": e["trip_id"], "entry_no": e["entry_no"], "entry_date": e["entry_date"], "notes": e["notes"],
                         "occurred_at": e["occurred_at"], "actor_name": "المالك",
                         "input_weight_grams": m["input_weight"], "nominal_pure_grams": m["nominal_pure"],
                         "output_weight_grams": m["output_weight"], "known_output_pure_grams": m["known_output_pure"],
                         "weight_difference_grams": m["weight_difference"], "pure_difference_grams": m["pure_difference"],
                         "nominal_eq21_grams": m["nominal_eq21"], "known_output_eq21_grams": m["known_output_eq21"],
                         "eq21_difference_grams": m["eq21_difference"],
                         "reversal_id": reversal and int(reversal["correction_id"]), "reversal_reason": reversal and reversal["reason"],
                         "reversal_at": reversal and reversal["occurred_at"],
                         "inputs": [{"karat_id": i["karat_id"], "karat_value": self.db.karats[i["karat_id"]]["karat_value"],
                                     "weight_grams": i["weight_grams"], "pure_grams": i["pure_grams"]}
                                    for i in sorted((i for i in self.db.melt_inputs if i["entry_id"] == e["id"]),
                                                    key=lambda i: -self.db.karats[i["karat_id"]]["karat_value"])],
                         "lots": [dict(l) for l in sorted((l for l in self.db.lots.values() if l["entry_id"] == e["id"]),
                                                          key=lambda l: l["lot_no"])]})
        return rows

    # ---- Logo heating (042): the CHECK constraints of the two tables are mirrored as asserts
    def insert_heat(self, *, entry_id, workshop, reference, gain_explanation, total_input, total_output):
        self._fail("heat")
        assert (workshop or "").strip()                                                      # chk_mth_workshop
        assert total_input > 0 and total_output > 0                                          # chk_mth_weights
        assert int(entry_id) not in self.db.heats and self.db.entries[int(entry_id)]["entry_kind"] == "HEAT"
        self.db.heats[int(entry_id)] = {"entry_id": int(entry_id), "workshop": workshop, "reference": reference,
                                        "gain_explanation": gain_explanation, "total_input_grams": total_input,
                                        "total_output_grams": total_output, "total_difference_grams": total_output - total_input}

    def insert_heat_line(self, *, entry_id, karat_id, input_weight, output_weight):
        self._fail("heat_line")
        assert input_weight > 0 and output_weight > 0                                        # chk_mthl_weights
        assert int(entry_id) in self.db.heats                                                # fk_mthl_heat
        if any((l["entry_id"], l["karat_id"]) == (int(entry_id), int(karat_id)) for l in self.db.heat_lines):
            raise _dup("uq_mthl_entry_karat")
        self.db.heat_lines.append({"entry_id": int(entry_id), "karat_id": int(karat_id), "designation": "LOGO",
                                   "input_weight_grams": input_weight, "output_weight_grams": output_weight,
                                   "difference_grams": output_weight - input_weight})

    def entry_heat_lines(self, entry_id):
        return [dict(l) for l in sorted((l for l in self.db.heat_lines if l["entry_id"] == int(entry_id)), key=lambda l: l["karat_id"])]

    def heats(self, trip_id):
        rows = []
        for e in sorted((e for e in self.db.entries.values() if e["trip_id"] == int(trip_id) and e["id"] in self.db.heats),
                        key=lambda e: e["entry_no"]):
            reversal = next((c for c in self.db.corrections if c["source_type"] == SOURCE and c["source_id"] == str(e["id"])
                             and c["correction_type"] == "FULL_REVERSAL"), None)
            rows.append({**self.db.heats[e["id"]], "trip_id": e["trip_id"], "entry_no": e["entry_no"], "entry_date": e["entry_date"],
                         "notes": e["notes"], "occurred_at": e["occurred_at"], "actor_name": "المالك",
                         "reversal_id": reversal and int(reversal["correction_id"]), "reversal_reason": reversal and reversal["reason"],
                         "reversal_at": reversal and reversal["occurred_at"],
                         "lines": [{**l, "karat_value": self.db.karats[l["karat_id"]]["karat_value"]}
                                   for l in sorted((l for l in self.db.heat_lines if l["entry_id"] == e["id"]),
                                                   key=lambda l: -self.db.karats[l["karat_id"]]["karat_value"])]})
        return rows

    def later_heat_entries(self, trip_id, after_entry_no, karat_ids):
        wanted = {int(k) for k in karat_ids}
        return [{"id": e["id"], "trip_id": e["trip_id"], "entry_no": e["entry_no"]}
                for e in sorted(self._live(trip_id, None), key=lambda e: e["entry_no"])
                if e["entry_kind"] == "HEAT" and e["entry_no"] > int(after_entry_no)
                and any(l["entry_id"] == e["id"] and l["karat_id"] in wanted for l in self.db.heat_lines)]

    def later_gold_settlements(self, trip_id, after_entry_no, karat_ids):
        wanted = {int(k) for k in karat_ids}
        return [{"id": e["id"], "trip_id": e["trip_id"], "entry_no": e["entry_no"]}
                for e in sorted(self._live(trip_id, None), key=lambda e: e["entry_no"])
                if e["entry_kind"] == "GOLD_SETTLE" and e["entry_no"] > int(after_entry_no)
                and self.db.gold_settle_links.get(e["id"], {}).get("karat_id") in wanted]

    def later_logo_returns(self, trip_id, after_entry_no, karat_ids):
        wanted = {int(k) for k in karat_ids}
        return [{"id": e["id"], "trip_id": e["trip_id"], "entry_no": e["entry_no"]}
                for e in sorted(self._live(trip_id, None), key=lambda e: e["entry_no"])
                if e["entry_kind"] == "RETURN" and e["entry_no"] > int(after_entry_no)
                and any(g["entry_id"] == e["id"] and g["designation"] == "LOGO" and g["karat_id"] in wanted for g in self.db.gold_legs)]

    def entries(self, trip_id):
        rows = []
        for e in sorted((e for e in self.db.entries.values() if e["trip_id"] == int(trip_id)), key=lambda e: e["entry_no"]):
            cash = next((l for l in self.db.cash_legs if l["entry_id"] == e["id"]), None)
            reversal = next((c for c in self.db.corrections if c["source_type"] == SOURCE and c["source_id"] == str(e["id"])
                             and c["correction_type"] == "FULL_REVERSAL"), None)
            rows.append({**e, "actor_name": "المالك", "cash_amount": cash and cash["amount"], "cash_account_name": cash and "الخزنة الرئيسية",
                         "reversal_id": reversal and int(reversal["correction_id"]), "reversal_reason": reversal and reversal["reason"],
                         "reversal_at": reversal and reversal["occurred_at"],
                         "lot_issues": [{"lot_id": r["lot_id"], "weight_grams": r["weight_grams"], "lot_no": self.db.lots[r["lot_id"]]["lot_no"],
                                         "lot_kind": self.db.lots[r["lot_id"]]["lot_kind"],
                                         "fineness_permille": self.db.lots[r["lot_id"]]["fineness_permille"],
                                         "trip_id": self.db.entries[self.db.lots[r["lot_id"]]["entry_id"]]["trip_id"],
                                         "melt_entry_no": self.db.entries[self.db.lots[r["lot_id"]]["entry_id"]]["entry_no"]}
                                        for r in self.db.lot_issues if r["entry_id"] == e["id"]],
                         "lot_returns": [{"lot_id": r["lot_id"], "weight_grams": r["weight_grams"], "lot_no": self.db.lots[r["lot_id"]]["lot_no"],
                                          "lot_kind": self.db.lots[r["lot_id"]]["lot_kind"],
                                          "fineness_permille": self.db.lots[r["lot_id"]]["fineness_permille"], "trip_id": self.db.entries[self.db.lots[r["lot_id"]]["entry_id"]]["trip_id"],
                                          "melt_entry_no": self.db.entries[self.db.lots[r["lot_id"]]["entry_id"]]["entry_no"]}
                                         for r in self.db.lot_returns if r["entry_id"] == e["id"]],
                         "supplier_payment": self.entry_supplier_payment(e["id"]),
                         "gold_settlement": self.entry_gold_settlement(e["id"]),
                         "gold_sale": self.entry_gold_sale(e["id"]),
                         "gold_buy": self.entry_gold_buy(e["id"]),
                         "lot_settlement": self.entry_lot_settlement(e["id"]),
                         "holding": self.entry_holding(e["id"]),
                         "logo_holding": self.entry_logo_holding(e["id"]),
                         "gold": [{"karat_id": g["karat_id"], "karat_value": self.db.karats[g["karat_id"]]["karat_value"],
                                   "designation": g["designation"], "weight_grams": g["weight_grams"]}
                                  for g in self.db.gold_legs if g["entry_id"] == e["id"]]})
        return rows

    # ---- supplier payments from custody (043): the CHECK / unique constraints of the link are mirrored as asserts
    def insert_supplier_payment_link(self, *, entry_id, payment_id, supplier_id, amount):
        self._fail("link")
        assert amount > 0                                                                    # chk_mtsp_amount
        assert int(entry_id) in self.db.entries and int(payment_id) in self.db.sm_payments and int(supplier_id) in self.db.suppliers   # FKs
        assert self.db.sm_payments[int(payment_id)]["amount"] == amount                      # the service keeps the two amounts equal
        if int(entry_id) in self.db.supplier_links or any(l["payment_id"] == int(payment_id) for l in self.db.supplier_links.values()):
            raise _dup("uq_mtsp_payment")
        self.db.supplier_links[int(entry_id)] = {"entry_id": int(entry_id), "payment_id": int(payment_id), "supplier_id": int(supplier_id),
                                                 "amount": amount}

    def entry_supplier_payment(self, entry_id):
        link = self.db.supplier_links.get(int(entry_id))
        return None if link is None else {**link, "supplier_name": self.db.suppliers[link["supplier_id"]]["name"]}

    def payment_entry(self, payment_id):
        link = next((l for l in self.db.supplier_links.values() if l["payment_id"] == int(payment_id)), None)
        if link is None:
            return None
        entry = self.db.entries[link["entry_id"]]
        return {**link, "trip_id": entry["trip_id"], "entry_no": entry["entry_no"], "request_hash": entry["request_hash"],
                "performed_by_user_id": entry["performed_by_user_id"]}

    # ---- supplier gold settlements from custody (046)
    def insert_gold_settlement_link(self, *, entry_id, settlement_id, supplier_id, karat_id, designation, weight):
        self._fail("gold_link")
        assert weight > 0 and designation == "LOGO"                                          # chk_mtgs_weight / chk_mtgs_designation
        assert int(entry_id) in self.db.entries                                              # fk_mtgs_entry
        header = self.db.settlements.get(int(settlement_id))
        assert header is not None and (header["source_kind"], header["supplier_id"], header["delivered_karat_id"],
                                       header["delivered_weight_grams"]) == ("CUSTODY_GOLD", int(supplier_id), int(karat_id), weight)   # fk_mtgs_settlement
        if int(entry_id) in self.db.gold_settle_links or any(l["settlement_id"] == int(settlement_id) for l in self.db.gold_settle_links.values()):
            raise _dup("uq_mtgs_settlement")
        self.db.gold_settle_links[int(entry_id)] = {"entry_id": int(entry_id), "settlement_id": int(settlement_id), "supplier_id": int(supplier_id),
                                                    "karat_id": int(karat_id), "designation": designation, "weight_grams": weight}

    # ---- cash gold purchase (054): the CHECK / FK constraints of the purchase tables are mirrored as asserts
    def insert_gold_buy(self, *, entry_id, vendor_kind, vendor_party_id, vendor_name, vendor_key, total, reference_total, deviation, acknowledged,
                        buy_date, reference):
        self._fail("gold_buy")
        assert int(entry_id) in self.db.entries and self.db.entries[int(entry_id)]["entry_kind"] == "GOLD_BUY"       # fk_mtgb_entry
        assert total > 0                                                                                       # chk_mtgb_total
        assert (vendor_kind == "REGISTERED_PARTY" and vendor_party_id is not None) or (vendor_kind == "THIRD_PARTY" and vendor_party_id is None)
        assert vendor_name.strip() and vendor_key.strip()                                                      # chk_mtgb_vendor: the vendor is MANDATORY
        assert vendor_party_id is None or int(vendor_party_id) in self.db.suppliers                           # fk_mtgb_party
        assert (reference_total is None) == (deviation is None)                                                # chk_mtgb_warning
        if int(entry_id) in self.db.gold_buys:
            raise _dup("PRIMARY")
        self.db.gold_buys[int(entry_id)] = {
            "entry_id": int(entry_id), "vendor_kind": vendor_kind, "vendor_party_id": vendor_party_id, "vendor_name": vendor_name,
            "vendor_name_key": vendor_key, "total_price": total, "reference_total": reference_total, "deviation_percent": deviation,
            "price_warning_acknowledged": 1 if acknowledged else 0, "buy_date": buy_date, "reference": reference}

    def insert_gold_buy_line(self, *, entry_id, line_no, karat_id, designation, weight, price):
        self._fail("gold_buy_line")
        assert int(entry_id) in self.db.gold_buys                                                              # fk_mtgbl_buy
        assert designation in ("MELT", "LOGO") and weight > 0 and price > 0                                    # chk_mtgbl_*
        assert int(karat_id) in self.db.karats                                                                 # fk_mtgbl_karat
        assert not any((l["entry_id"], l["line_no"]) == (int(entry_id), int(line_no)) for l in self.db.gold_buy_lines)
        self.db.gold_buy_lines.append({"entry_id": int(entry_id), "line_no": int(line_no), "karat_id": int(karat_id), "designation": designation,
                                       "weight_grams": weight, "line_price": price})

    def entry_gold_buy(self, entry_id):
        head = self.db.gold_buys.get(int(entry_id))
        if head is None:
            return None
        lines = [{**l, "karat_value": self.db.karats[l["karat_id"]]["karat_value"]}
                 for l in sorted((l for l in self.db.gold_buy_lines if l["entry_id"] == int(entry_id)), key=lambda l: l["line_no"])]
        return {**head, "lines": lines}

    # ---- sale of custody gold (053): the CHECK / FK constraints of market_trip_gold_sales are mirrored as asserts
    def buyers(self):
        return [{"id": r["id"], "name": r["name"], "is_active": r["is_active"], "is_supplier": r.get("is_supplier", True),
                 "is_intermediary": r.get("is_intermediary", False)}
                for r in sorted(self.db.suppliers.values(), key=lambda r: (-r["is_active"], r["name"], r["id"]))]

    def party(self, party_id, lock=False):
        row = self.db.suppliers.get(int(party_id))
        return None if row is None else {"id": row["id"], "name": row["name"], "is_active": row["is_active"],
                                         "is_supplier": row.get("is_supplier", True), "is_intermediary": row.get("is_intermediary", False)}

    def insert_gold_sale(self, *, entry_id, source_kind, karat_id, designation, lot, weight, movement_id, total, per_gram, basis, buyer_kind,
                         buyer_party_id, buyer_name, reference_price, deviation, acknowledged, sale_date, reference):
        self._fail("gold_sale")
        assert int(entry_id) in self.db.entries and self.db.entries[int(entry_id)]["entry_kind"] == "GOLD_SALE"      # fk_mtgsale_entry
        assert weight > 0 and total > 0                                                                       # chk_mtgsale_price
        assert (basis == "TOTAL" and per_gram is None) or (basis == "PER_GRAM" and per_gram is not None and per_gram > 0)
        assert (buyer_kind == "REGISTERED_PARTY" and buyer_party_id is not None) or (buyer_kind == "THIRD_PARTY" and buyer_party_id is None)
        assert buyer_party_id is None or int(buyer_party_id) in self.db.suppliers                             # fk_mtgsale_party
        assert (reference_price is None) == (deviation is None)                                               # chk_mtgsale_warning
        if source_kind == "CUSTODY_GOLD":                                                                      # chk_mtgsale_source
            assert karat_id is not None and designation in ("MELT", "LOGO", "UNCLASSIFIED") and lot is None and movement_id is None
        else:
            assert source_kind == "CUSTODY_LOT" and lot is not None and lot["fineness_permille"] is not None
            assert karat_id is None and designation is None
            move = next(m for m in self.db.lot_moves if m["id"] == movement_id)                               # fk_mtgsale_movement
            assert (move["entry_id"], move["lot_id"], move["movement_type"], move["weight_delta"]) == (int(entry_id), lot["id"], "SALE_OUT", -weight)
        if int(entry_id) in self.db.gold_sales:
            raise _dup("PRIMARY")
        self.db.gold_sales[int(entry_id)] = {
            "entry_id": int(entry_id), "source_kind": source_kind, "karat_id": karat_id, "designation": designation,
            "lot_id": None if lot is None else lot["id"], "fineness_permille": None if lot is None else lot["fineness_permille"],
            "custody_movement_id": movement_id, "weight_grams": weight, "total_price": total, "price_per_gram": per_gram, "price_basis": basis,
            "buyer_kind": buyer_kind, "buyer_party_id": buyer_party_id, "buyer_name": buyer_name, "reference_price_per_gram": reference_price,
            "deviation_percent": deviation, "price_warning_acknowledged": 1 if acknowledged else 0, "sale_date": sale_date, "reference": reference}

    def entry_gold_sale(self, entry_id):
        sale = self.db.gold_sales.get(int(entry_id))
        if sale is None:
            return None
        lot = None if sale["lot_id"] is None else self.db.lots[sale["lot_id"]]
        origin = None if lot is None else self.db.entries[lot["entry_id"]]
        return {**sale, "karat_value": None if sale["karat_id"] is None else self.db.karats[sale["karat_id"]]["karat_value"],
                "lot_no": None if lot is None else lot["lot_no"], "lot_kind": None if lot is None else lot["lot_kind"],
                "origin_trip_id": None if origin is None else origin["trip_id"], "origin_entry_no": None if origin is None else origin["entry_no"]}

    def melt_lot_ids(self, entry_id):
        return sorted(l["id"] for l in self.db.lots.values() if l["entry_id"] == int(entry_id))

    def insert_lot_settlement_link(self, *, entry_id, settlement_id, supplier_id, lot, weight, movement_id):
        self._fail("lot_link")
        h=self.db.settlements[int(settlement_id)]
        assert (h["source_kind"],h["delivered_lot_id"],h["delivered_weight_grams"],h["delivered_fineness_permille"]) == ("CUSTODY_LOT",lot["id"],weight,lot["fineness_permille"])
        m=next(m for m in self.db.lot_moves if m["id"]==movement_id)
        assert (m["entry_id"],m["lot_id"],m["movement_type"],m["weight_delta"]) == (int(entry_id),lot["id"],"SUPPLIER_SETTLE",-weight)
        self.db.lot_settle_links[int(entry_id)]={"entry_id":int(entry_id),"settlement_id":int(settlement_id),"supplier_id":int(supplier_id),"lot_id":lot["id"],"origin_melt_entry_id":lot["entry_id"],"weight_grams":weight,"fineness_permille":lot["fineness_permille"],"custody_movement_id":movement_id}

    def entry_lot_settlement(self, entry_id):
        x=self.db.lot_settle_links.get(int(entry_id))
        if x is None: return None
        h=self.db.settlements[x["settlement_id"]];l=self.db.lots[x["lot_id"]];o=self.db.entries[l["entry_id"]]
        return {**h,**x,"lot_no":l["lot_no"],"lot_kind":l["lot_kind"],"origin_trip_id":o["trip_id"],"origin_entry_no":o["entry_no"],"supplier_name":self.db.suppliers[x["supplier_id"]]["name"]}

    def entry_logo_holding(self, entry_id):
        link = self.db.logo_links.get(int(entry_id))
        if link is None:
            return None
        document = self.db.logo_docs[link["document_id"]]
        return {**link, "party_id": document["party_id"], "party_name": self.db.suppliers[document["party_id"]]["name"],
                "karat_value": self.db.karats[link["karat_id"]]["karat_value"]}

    def entry_holding(self, entry_id):
        link = self.db.holding_links.get(int(entry_id))
        if link is None:
            return None
        document = self.db.holding_docs[link["document_id"]]
        lot = self.db.lots[link["lot_id"]]
        origin = self.db.entries[lot["entry_id"]]
        return {**link, "party_id": document["party_id"], "party_name": self.db.suppliers[document["party_id"]]["name"],
                "fineness_permille": document["fineness_permille"], "lot_no": lot["lot_no"], "lot_kind": lot["lot_kind"], "label": lot["label"],
                "origin_trip_id": origin["trip_id"], "origin_entry_no": origin["entry_no"]}

    def entry_gold_settlement(self, entry_id):
        link = self.db.gold_settle_links.get(int(entry_id))
        if link is None:
            return None
        header = self.db.settlements[link["settlement_id"]]
        return {**link, "karat_value": self.db.karats[link["karat_id"]]["karat_value"], "supplier_name": self.db.suppliers[link["supplier_id"]]["name"],
                "credited_weight_grams": header["credited_weight_grams"], "credited_analytical_grams": header["credited_analytical_grams"],
                "rounding_difference_grams": header["rounding_difference_grams"], "target_karat_value": header["target_karat_value"]}

    # ---- as a correction source
    def entry_source(self, entry_id, lock=False):
        row = self.db.entries.get(int(entry_id))
        return None if row is None else {**row, "trip_status": self.db.trips[row["trip_id"]]["status"]}

    def entry_cash_leg(self, entry_id):
        row = next((l for l in self.db.cash_legs if l["entry_id"] == int(entry_id)), None)
        return None if row is None else dict(row)

    def entry_gold_legs(self, entry_id):
        out = []
        for g in (g for g in self.db.gold_legs if g["entry_id"] == int(entry_id)):
            move = next(m for m in self.db.scrap if m["id"] == g["scrap_movement_id"])
            out.append({"karat_id": g["karat_id"], "karat_value": self.db.karats[g["karat_id"]]["karat_value"],
                        "designation": g["designation"], "weight_grams": g["weight_grams"], "weight_delta_grams": move["weight_delta"]})
        return out

    def audit(self, *, actor, event_type, entity_id, reference, description, metadata, now):
        self._fail("audit")
        self.db.audit_rows.append({"user_id": actor.user_id, "event_type": event_type, "entity_type": "MARKET_TRIP",
                                   "entity_id": int(entity_id), "reference": reference, "metadata": copy.deepcopy(metadata)})


class ScrapRepo:
    """OldGoldConversionRepository.lock_old_gold_balance / held_old_gold: counted rows plus HELD grams as one negative row."""

    def __init__(self, db):
        self.db = db

    def lock_old_gold_balance(self, karat_id):
        self.db.lock_log.append(("scrap", int(karat_id)))
        rows = [{"id": m["id"], "weight_delta_grams": m["weight_delta"]} for m in self.db.scrap if m["karat_id"] == int(karat_id)]
        held = self.db.holds.get(int(karat_id), D("0.000"))
        return rows + ([{"id": None, "weight_delta_grams": -held}] if held else [])

    def held_old_gold(self, karat_id):
        return self.db.holds.get(int(karat_id), D("0.000"))


class AccountsRepo:
    def __init__(self, db):
        self.db = db

    def account(self, ledger, account_id, lock=False):
        if lock:
            self.db.lock_log.append(("account", ledger, int(account_id)))
        row = self.db.cash_accounts.get(int(account_id)) if ledger == "CASH" else None
        return None if row is None else {"id": row["id"], "name": row["name"], "account_type": "CASH", "is_active": row["is_active"]}


class Corrections:
    """The CorrectionRepository subset a standalone full reversal uses, with the 023/037 contracts."""

    def __init__(self, db):
        self.db = db

    def _fail(self, step):
        if self.db.fail_at == step:
            raise RuntimeError(f"forced failure at {step}")

    def corrections_for_source(self, source_type, source_id, current=False):
        return [{"correction_id": c["correction_id"], "correction_type": c["correction_type"], "chain_sequence": None,
                 "after_json": None, "occurred_at": c["occurred_at"]}
                for c in self.db.corrections if (c["source_type"], c["source_id"]) == (source_type, str(source_id))]

    def money_by_account(self, source_type, source_id, correction_ids=()):
        from repositories.correction_repository import SOURCE_REFERENCE
        found = {(SOURCE_REFERENCE[source_type], str(source_id))} | {("TRANSACTION_CORRECTION", str(c)) for c in correction_ids}
        out = {}
        for m in self.db.cash_moves:
            if (m["reference_type"], m["reference_id"]) in found:
                out[("CASH", m["account_id"])] = out.get(("CASH", m["account_id"]), D("0.00")) + m["amount_delta"]
        return out

    def account_by_customer(self, source_type, source_id, correction_ids=()):
        return {}

    def db_now(self):
        return self.db.now

    def insert_correction(self, **kw):
        self._fail("correction")
        assert kw["reason"].strip()
        assert kw["source_type"] in (SOURCE, "SUPPLIER_GOLD_SETTLEMENT", "HOLDING_RECEIPT", "MARKETER_DOCUMENT") and kw["invoice_id"] is None and kw["correction_type"] == "FULL_REVERSAL"
        if any((c["source_type"], c["source_id"], c["correction_type"]) == (kw["source_type"], str(kw["source_id"]), "FULL_REVERSAL")
               for c in self.db.corrections):
            raise RuntimeError("Duplicate entry for key uq_correction_one_reversal")
        correction_id = str(self.db.next("correction"))
        self.db.corrections.append({"correction_id": correction_id, "correction_type": "FULL_REVERSAL", "source_type": kw["source_type"],
                                    "source_id": str(kw["source_id"]), "source_reference": kw["source_reference"],
                                    "reason": kw["reason"], "occurred_at": kw["occurred_at"], "invoice_id": None})
        return correction_id

    def insert_leg(self, **kw):
        self._fail("leg")

    def add_money(self, *, ledger, account_id, delta, type_code, correction_id, **kw):
        self._fail("reversal_cash")
        assert type_code == "REVERSAL_PAYMENT"
        move_id = self.db.next("cash_move")
        self.db.cash_moves.append({"id": move_id, "account_id": int(account_id), "amount_delta": delta,
                                   "reference_type": "TRANSACTION_CORRECTION", "reference_id": str(correction_id),
                                   "type_code": type_code})
        return str(move_id)

    def lock_old_gold_balance(self, karat_id):
        return self.db.available(int(karat_id))

    def held_old_gold(self, karat_id):
        return self.db.holds.get(int(karat_id), D("0.000"))

    def add_old_gold(self, *, karat_id, weight_delta, movement_code, correction_id, **kw):
        self._fail("reversal_scrap")
        assert movement_code in ("REVERSAL_TRIP_ISSUE_IN", "REVERSAL_TRIP_RETURN_OUT", "REVERSAL_SUPPLIER_GOLD_SETTLEMENT_IN", "REVERSAL_MARKETER_GOLD_RETURN_OUT")
        move_id = self.db.next("scrap")
        self.db.scrap.append({"id": move_id, "karat_id": int(karat_id), "weight_delta": weight_delta,
                              "reference_type": "TRANSACTION_CORRECTION", "reference_id": str(correction_id), "type_code": movement_code})
        assert self.db.physical(int(karat_id)) >= 0                                          # stock never negative
        return str(move_id)

    def lock_items(self, ids):
        return {}


class Invoices:
    def __init__(self, db):
        pass

    def active_bank_accounts(self):
        return []


class Customers:
    def __init__(self, db):
        pass


def build(permissions=OWNER, user_id=7, granted=None, session=None):
    """(db, session, authorization, MarketTripService, CorrectionService) over one fake database."""
    from services.correction_service import CorrectionService
    from services.market_trip_service import MarketTripService
    db = Db()
    session = session or session_for(permissions, user_id)
    authorization = FakeAuthorization(session, granted)
    corrections = CorrectionService(session, authorization=authorization, transaction_factory=db.transaction,
                                    invoice_repository=Invoices, correction_repository=Corrections, customer_repository=Customers)
    service = MarketTripService(session, transaction_factory=db.transaction, repository=TripRepo, accounts=AccountsRepo, scrap=ScrapRepo,
                                authorization=authorization, corrections=corrections, supplier_payments=SupplierPayRepo,
                                gold_settlements=SettlementRepo, holdings=HoldingRepo)
    return db, session, authorization, service, corrections


class HoldingRepo:
    """IntermediaryHoldingRepository over the same fake database: the 048 CHECK / unique / composite-foreign-key constraints are mirrored as
    asserts, every movement is a real row (compensations included) and the acquisition order is logged in db.lock_log."""

    def __init__(self, db):
        self.db = db

    def _fail(self, step):
        if self.db.fail_at == step:
            raise RuntimeError(f"forced failure at {step}")

    def insert_pool(self, *, party_id, lot, user_id):
        self._fail("pool")
        assert lot["weight_grams"] > 0 and lot["fineness_permille"] is not None and 0 < lot["fineness_permille"] <= 1000        # chk_ihp_facts
        stored = self.db.lots[int(lot["id"])]
        assert (stored["entry_id"], stored["weight_grams"], stored["fineness_permille"]) == (lot["entry_id"], lot["weight_grams"], lot["fineness_permille"])   # fk_ihp_lot
        assert int(party_id) in self.db.suppliers                                              # fk_ihp_party
        pool_id = self.db.next("pool")
        self.db.holding_pools[pool_id] = {"id": pool_id, "party_id": int(party_id), "lot_id": int(lot["id"]), "origin_melt_entry_id": int(lot["entry_id"]),
                                          "weight_grams": lot["weight_grams"], "fineness_permille": lot["fineness_permille"]}
        return pool_id

    def insert_document(self, *, kind, pool, trip_id, document_date, reference, notes, confirmation_code, confirmed_by, confirmed_at, key, digest,
                        actor, now):
        self._fail("document")
        assert kind in ("HANDOVER", "RETURN")                                                  # chk_ihd_kind
        stored = self.db.holding_pools[int(pool["id"])]
        assert (stored["party_id"], stored["lot_id"], stored["weight_grams"], stored["fineness_permille"]) == (
            int(pool["party_id"]), int(pool["lot_id"]), pool["weight_grams"], pool["fineness_permille"])   # fk_ihd_pool
        assert int(trip_id) in self.db.trips                                                   # fk_ihd_trip
        if kind == "HANDOVER":                                                                 # chk_ihd_confirmation
            assert confirmation_code is None and confirmed_by is None and confirmed_at is None
        else:
            assert confirmation_code == "SAME_BAR_WHOLE_UNDIVIDED" and confirmed_by is not None and confirmed_at is not None
        if any(d["request_key"] == key for d in self.db.holding_docs.values()):
            raise _dup("uq_ihd_request")
        document_id = self.db.next("doc")
        self.db.holding_docs[document_id] = {
            "id": document_id, "doc_kind": kind, "pool_id": int(pool["id"]), "party_id": int(pool["party_id"]), "lot_id": int(pool["lot_id"]),
            "weight_grams": pool["weight_grams"], "fineness_permille": pool["fineness_permille"], "trip_id": int(trip_id),
            "document_date": document_date, "reference": reference, "notes": notes, "intact_confirmation_code": confirmation_code,
            "confirmed_by_user_id": confirmed_by, "confirmed_at": confirmed_at, "request_key": key, "request_hash": digest,
            "performed_by_user_id": actor.user_id, "occurred_at": now}
        return document_id

    def insert_movement(self, *, pool_id, document_id, doc_kind, movement_type, weight, delta, correction_id=None):
        self._fail("holding_movement")
        document = self.db.holding_docs[int(document_id)]
        assert (document["doc_kind"], document["pool_id"], document["weight_grams"]) == (doc_kind, int(pool_id), weight)      # fk_ihm_document
        shapes = {"HANDOVER": ("HANDOVER", weight, False), "RETURN": ("RETURN", -weight, False),
                  "HANDOVER_REVERSAL": ("HANDOVER", -weight, True), "RETURN_REVERSAL": ("RETURN", weight, True)}
        kind, expected, needs_correction = shapes[movement_type]                              # chk_ihm_shape
        assert weight > 0 and kind == doc_kind and delta == expected and (correction_id is not None) == needs_correction
        if any((m["document_id"], m["movement_type"]) == (int(document_id), movement_type) for m in self.db.holding_moves):
            raise _dup("uq_ihm_document_type")
        if correction_id is not None and any(m["correction_id"] == int(correction_id) for m in self.db.holding_moves):
            raise _dup("uq_ihm_correction")
        move_id = self.db.next("hmove")
        self.db.holding_moves.append({"id": move_id, "pool_id": int(pool_id), "document_id": int(document_id), "doc_kind": doc_kind,
                                      "movement_type": movement_type, "weight_grams": weight, "weight_delta": delta,
                                      "correction_id": None if correction_id is None else int(correction_id)})
        return move_id

    def insert_link(self, *, entry_id, entry_kind, trip_id, document_id, doc_kind, pool_id, lot_id, weight, custody_movement_id, movement_type, delta):
        self._fail("holding_link")
        entry = self.db.entries[int(entry_id)]
        assert (entry["entry_kind"], entry["trip_id"]) == (entry_kind, int(trip_id))          # fk_mthl_entry
        document = self.db.holding_docs[int(document_id)]
        assert (document["doc_kind"], document["pool_id"], document["lot_id"], document["weight_grams"], document["trip_id"]) == (
            doc_kind, int(pool_id), int(lot_id), weight, int(trip_id))                        # fk_mthl_document
        movement = next(m for m in self.db.lot_moves if m["id"] == int(custody_movement_id))
        assert (movement["lot_id"], movement["entry_id"], movement["movement_type"], movement["weight_delta"]) == (
            int(lot_id), int(entry_id), movement_type, delta)                                 # fk_mthl_movement
        assert weight > 0 and ((entry_kind, doc_kind, movement_type, delta) == ("LOT_HOLD", "HANDOVER", "HOLD_OUT", -weight)
                               or (entry_kind, doc_kind, movement_type, delta) == ("HOLD_RETURN", "RETURN", "HOLD_IN", weight))   # chk_mthl_shape
        if int(entry_id) in self.db.holding_links or any(l["document_id"] == int(document_id) for l in self.db.holding_links.values()):
            raise _dup("uq_mthl_document")
        if any(l["custody_movement_id"] == int(custody_movement_id) for l in self.db.holding_links.values()):
            raise _dup("uq_mthl_movement")
        self.db.holding_links[int(entry_id)] = {"entry_id": int(entry_id), "entry_kind": entry_kind, "trip_id": int(trip_id), "document_id": int(document_id),
                                                "doc_kind": doc_kind, "pool_id": int(pool_id), "lot_id": int(lot_id), "weight_grams": weight,
                                                "custody_movement_id": int(custody_movement_id), "movement_type": movement_type, "weight_delta": delta}

    def audit(self, *, actor, event_type, entity_id, reference, description, metadata, now):
        self._fail("holding_audit")
        self.db.audit_rows.append({"user_id": actor.user_id, "event_type": event_type, "entity_type": "INTERMEDIARY_HOLDING",
                                   "entity_id": int(entity_id), "reference": reference, "metadata": copy.deepcopy(metadata)})

    def by_request(self, key):
        row = next((d for d in self.db.holding_docs.values() if d["request_key"] == key), None)
        return None if row is None else dict(row)

    def lock_pool(self, pool_id):
        self.db.lock_log.append(("pool", int(pool_id)))
        row = self.db.holding_pools.get(int(pool_id))
        return None if row is None else dict(row)

    def pool(self, pool_id):
        row = self.db.holding_pools.get(int(pool_id))
        return None if row is None else dict(row)

    def pool_balance(self, pool_id):
        return sum((m["weight_delta"] for m in self.db.holding_moves if m["pool_id"] == int(pool_id)), D("0.000"))

    def pool_return_events(self, pool_id, *, lock=False):
        settlements = []
        for settlement in sorted(self.db.settlements.values(), key=lambda row: row["id"]):
            if settlement.get("lot_pool_id") != int(pool_id):
                continue
            reversal = next((correction for correction in self.db.corrections
                            if correction["correction_type"] == "FULL_REVERSAL"
                            and correction["source_type"] == "SUPPLIER_GOLD_SETTLEMENT"
                            and correction["source_id"] == str(settlement["id"])), None)
            settlements.append({"kind": "SUPPLIER_SETTLEMENT", "id": str(settlement["id"]),
                                "physical_declaration": settlement["physical_declaration"],
                                "reversal_id": None if reversal is None else str(reversal["correction_id"])})
        receipts = []
        for receipt in sorted(self.db.receipts.values(), key=lambda row: row["id"]):
            if receipt["pool_id"] != int(pool_id):
                continue
            piece = next(piece for piece in self.db.pieces.values() if piece["receipt_id"] == receipt["id"])
            reversal = next((correction for correction in self.db.corrections
                            if correction["correction_type"] == "FULL_REVERSAL"
                            and correction["source_type"] == "HOLDING_RECEIPT"
                            and correction["source_id"] == str(receipt["id"])), None)
            movements = sorted((movement for movement in self.db.stock_moves if movement["piece_id"] == piece["id"]),
                               key=lambda movement: movement["id"])
            receipts.append({
                "kind": "PHYSICAL_RECEIPT", "id": str(receipt["id"]), "piece_id": str(piece["id"]),
                "grams": format(receipt["grams"], ".3f"),
                "reversal_id": None if reversal is None else str(reversal["correction_id"]),
                "stock_grams": format(self.piece_stock(piece["id"]), ".3f"),
                "stock_movements": [
                    {"id": str(movement["id"]), "type": movement["movement_type"],
                     "delta": format(movement["weight_delta"], ".3f"),
                     "correction_id": None if movement["correction_id"] is None else str(movement["correction_id"])}
                    for movement in movements
                ],
            })
        return [*settlements, *receipts]

    def lot_held_total(self, lot_id):
        pools = {p["id"] for p in self.db.holding_pools.values() if p["lot_id"] == int(lot_id)}
        return sum((m["weight_delta"] for m in self.db.holding_moves if m["pool_id"] in pools), D("0.000"))

    def history_count(self, party_id):
        return (sum(1 for p in self.db.holding_pools.values() if p["party_id"] == int(party_id))
                + sum(1 for d in self.db.holding_docs.values() if d["party_id"] == int(party_id))
                + sum(1 for p in self.db.logo_pools.values() if p["party_id"] == int(party_id))
                + sum(1 for d in self.db.logo_docs.values() if d["party_id"] == int(party_id))
                + sum(1 for s in self.db.settlements.values() if s.get("holding_party_id") == int(party_id))
                + sum(1 for r in self.db.receipts.values() if r["party_id"] == int(party_id)))

    # ---- 051: physical receipts (the constraints of migration 051 mirrored as asserts)
    def insert_receipt(self, *, pool, grams, receipt_date, reference, notes, key, digest, actor, now):
        self._fail("receipt")
        stored = self.db.holding_pools[int(pool["id"])]                                          # fk_hr_pool
        assert (stored["party_id"], stored["lot_id"], stored["origin_melt_entry_id"]) == (int(pool["party_id"]), int(pool["lot_id"]), int(pool["origin_melt_entry_id"]))
        assert grams > 0                                                                         # chk_hr_grams
        if any(r["request_key"] == key for r in self.db.receipts.values()):
            raise _dup("uq_hr_request")
        receipt_id = self.db.next("receipt")
        self.db.receipts[receipt_id] = {"id": receipt_id, "pool_id": int(pool["id"]), "party_id": int(pool["party_id"]), "lot_id": int(pool["lot_id"]),
                                        "origin_melt_entry_id": int(pool["origin_melt_entry_id"]), "grams": grams, "receipt_date": receipt_date,
                                        "reference": reference, "notes": notes, "request_key": key, "request_hash": digest,
                                        "performed_by_user_id": actor.user_id, "occurred_at": now}
        return receipt_id

    def insert_piece(self, *, receipt_id, pool, grams):
        self._fail("piece")
        receipt = self.db.receipts[int(receipt_id)]                                              # fk_rp_receipt
        assert (receipt["pool_id"], receipt["party_id"], receipt["lot_id"], receipt["origin_melt_entry_id"], receipt["grams"]) == (
            int(pool["id"]), int(pool["party_id"]), int(pool["lot_id"]), int(pool["origin_melt_entry_id"]), grams)
        if any(p["receipt_id"] == int(receipt_id) for p in self.db.pieces.values()):
            raise _dup("uq_rp_receipt")
        piece_id = self.db.next("piece")
        self.db.pieces[piece_id] = {"id": piece_id, "receipt_id": int(receipt_id), "pool_id": int(pool["id"]), "grams": grams, "identity_kind": "RECEIPT_PIECE",
                                    "location": "SHOP", "assay_status": "UNKNOWN", "fineness_permille": None, "pure_grams": None}
        return piece_id

    def insert_stock_movement(self, *, piece_id, receipt_id, movement_type, weight, delta, correction_id=None):
        self._fail("stock_movement")
        piece = self.db.pieces[int(piece_id)]                                                    # fk_rsm_piece
        assert (piece["receipt_id"], piece["grams"]) == (int(receipt_id), weight)
        expected, needs_correction = {"RECEIVE": (weight, False), "RECEIVE_REVERSAL": (-weight, True)}[movement_type]        # chk_rsm_shape
        assert weight > 0 and delta == expected and (correction_id is not None) == needs_correction
        if any((m["piece_id"], m["movement_type"]) == (int(piece_id), movement_type) for m in self.db.stock_moves):
            raise _dup("uq_rsm_piece_type")
        if correction_id is not None and any(m["correction_id"] == int(correction_id) for m in self.db.stock_moves):
            raise _dup("uq_rsm_correction")
        move_id = self.db.next("smove")
        self.db.stock_moves.append({"id": move_id, "piece_id": int(piece_id), "receipt_id": int(receipt_id), "movement_type": movement_type,
                                    "weight_grams": weight, "weight_delta": delta, "correction_id": None if correction_id is None else int(correction_id)})
        return move_id

    def insert_receipt_movement(self, *, pool_id, receipt_id, movement_type, weight, delta, correction_id=None):
        self._fail("receipt_movement")
        receipt = self.db.receipts[int(receipt_id)]                                              # fk_ihm_receipt
        assert (receipt["pool_id"], receipt["grams"]) == (int(pool_id), weight)
        expected, needs_correction = {"RECEIPT": (-weight, False), "RECEIPT_REVERSAL": (weight, True)}[movement_type]        # chk_ihm_shape (receipt branches)
        assert weight > 0 and delta == expected and (correction_id is not None) == needs_correction
        if any(m.get("receipt_id") == int(receipt_id) and m["movement_type"] == movement_type for m in self.db.holding_moves):
            raise _dup("uq_ihm_receipt_type")
        if correction_id is not None and any(m["correction_id"] == int(correction_id) for m in self.db.holding_moves):
            raise _dup("uq_ihm_correction")
        move_id = self.db.next("hmove")
        self.db.holding_moves.append({"id": move_id, "pool_id": int(pool_id), "document_id": None, "doc_kind": None, "settlement_id": None,
                                      "receipt_id": int(receipt_id), "movement_type": movement_type, "weight_grams": weight, "weight_delta": delta,
                                      "correction_id": None if correction_id is None else int(correction_id)})
        return move_id

    def receipt_by_request(self, key):
        row = next((r for r in self.db.receipts.values() if r["request_key"] == key), None)
        return None if row is None else dict(row)

    def receipt(self, receipt_id):
        row = self.db.receipts.get(int(receipt_id))
        if row is None:
            return None
        piece = next(p for p in self.db.pieces.values() if p["receipt_id"] == int(receipt_id))
        return {**row, "piece_id": piece["id"]}

    def lock_piece(self, piece_id):
        self.db.lock_log.append(("piece", int(piece_id)))
        row = self.db.pieces.get(int(piece_id))
        return None if row is None else dict(row)

    def piece_stock(self, piece_id):
        return sum((m["weight_delta"] for m in self.db.stock_moves if m["piece_id"] == int(piece_id)), D("0.000"))

    display_piece_stock = piece_stock

    def receipt_movement(self, receipt_id):
        row = next((m for m in self.db.holding_moves if m.get("receipt_id") == int(receipt_id) and m["movement_type"] == "RECEIPT"), None)
        return None if row is None else dict(row)

    def party_receipts(self, party_id):
        rows = []
        for r in sorted(self.db.receipts.values(), key=lambda r: -r["id"]):
            if r["party_id"] != int(party_id):
                continue
            piece = next(p for p in self.db.pieces.values() if p["receipt_id"] == r["id"])
            lot = self.db.lots[r["lot_id"]]
            origin = self.db.entries[lot["entry_id"]]
            pool = self.db.holding_pools[r["pool_id"]]
            reversal = next((c for c in self.db.corrections if c["correction_type"] == "FULL_REVERSAL" and c["source_type"] == "HOLDING_RECEIPT"
                             and c["source_id"] == str(r["id"])), None)
            rows.append({"id": r["id"], "pool_id": r["pool_id"], "party_id": r["party_id"], "lot_id": r["lot_id"], "grams": r["grams"],
                         "receipt_date": r["receipt_date"], "reference": r["reference"], "notes": r["notes"], "occurred_at": r["occurred_at"],
                         "actor_name": "المالك", "piece_id": piece["id"], "assay_status": piece["assay_status"], "lot_no": lot["lot_no"],
                         "lot_kind": lot["lot_kind"], "origin_trip_id": origin["trip_id"], "origin_entry_no": origin["entry_no"],
                         "pool_weight": pool["weight_grams"], "source_fineness": pool["fineness_permille"], "stock_grams": self.piece_stock(piece["id"]),
                         "reversal_id": reversal and int(reversal["correction_id"]), "reversal_reason": reversal and reversal["reason"]})
        return rows

    # ---- 050: supplier settlement from a holding
    def pool_settlement_history(self, pool_id):
        return [{"id": s["id"], "physical_declaration": s["physical_declaration"]}
                for s in sorted(self.db.settlements.values(), key=lambda s: s["id"]) if s.get("lot_pool_id") == int(pool_id)]

    def display_pool_history(self, pool_id):
        return self.pool_settlement_history(pool_id)

    def logo_pool_settlements(self, pool_id):
        return [{"id": s["id"]} for s in sorted(self.db.settlements.values(), key=lambda s: s["id"]) if s.get("logo_pool_id") == int(pool_id)]

    def display_balance(self, *, logo, pool_id):
        moves = self.db.logo_moves if logo else self.db.holding_moves
        return sum((m["weight_delta"] for m in moves if m["pool_id"] == int(pool_id)), D("0.000"))

    def insert_settlement_movement(self, *, logo, pool_id, settlement_id, movement_type, weight, delta, correction_id=None):
        self._fail("settlement_movement")
        moves = self.db.logo_moves if logo else self.db.holding_moves
        header = self.db.settlements[int(settlement_id)]
        key = "logo_pool_id" if logo else "lot_pool_id"
        assert header[key] == int(pool_id) and header["holding_weight_grams"] == weight                       # fk_ihm_settlement / fk_lhm_settlement
        assert weight > 0                                                                                    # chk_*_shape
        expected, needs_correction = {"SETTLEMENT": (-weight, False), "SETTLEMENT_REVERSAL": (weight, True)}[movement_type]
        assert delta == expected and (correction_id is not None) == needs_correction
        if any(m.get("settlement_id") == int(settlement_id) and m["movement_type"] == movement_type for m in moves):
            raise _dup("uq_lhm_settlement_type" if logo else "uq_ihm_settlement_type")
        if correction_id is not None and any(m["correction_id"] == int(correction_id) for m in moves):
            raise _dup("uq_lhm_correction" if logo else "uq_ihm_correction")
        move_id = self.db.next("lmove" if logo else "hmove")
        moves.append({"id": move_id, "pool_id": int(pool_id), "document_id": None, "doc_kind": None, "settlement_id": int(settlement_id),
                      "movement_type": movement_type, "weight_grams": weight, "weight_delta": delta,
                      "correction_id": None if correction_id is None else int(correction_id)})
        return move_id

    def settlement_movement(self, *, logo, settlement_id):
        moves = self.db.logo_moves if logo else self.db.holding_moves
        return next((dict(m) for m in moves if m.get("settlement_id") == int(settlement_id) and m["movement_type"] == "SETTLEMENT"), None)

    def holding_settlements(self, party_id):
        rows = []
        for s in sorted(self.db.settlements.values(), key=lambda s: -s["id"]):
            if s.get("holding_party_id") != int(party_id):
                continue
            reversal = next((c for c in self.db.corrections if c["correction_type"] == "FULL_REVERSAL" and c["source_type"] == "SUPPLIER_GOLD_SETTLEMENT"
                             and c["source_id"] == str(s["id"])), None)
            rows.append({**{k: s.get(k) for k in ("id", "source_kind", "supplier_id", "lot_pool_id", "logo_pool_id", "delivered_weight_grams",
                                                  "credited_weight_grams", "credited_analytical_grams", "rounding_difference_grams", "target_karat_value",
                                                  "delivered_karat_value", "delivered_fineness_permille", "delivered_lot_weight_grams",
                                                  "physical_declaration", "settlement_date", "occurred_at")},
                         "supplier_name": self.db.suppliers[s["supplier_id"]]["name"], "self_settlement_confirmed": s.get("self_settlement_confirmed", 0),
                         "actor_name": "المالك", "reversal_id": reversal and int(reversal["correction_id"]), "reversal_reason": reversal and reversal["reason"]})
        return rows
    # ---- 049: master-karat LOGO holdings
    def insert_logo_pool(self, *, party_id, karat_id, trip_id, weight, user_id):
        self._fail("logo_pool")
        assert weight > 0 and int(party_id) in self.db.suppliers and int(karat_id) in self.db.karats and int(trip_id) in self.db.trips   # chk_lhp_facts + FKs
        pool_id = self.db.next("lpool")
        self.db.logo_pools[pool_id] = {"id": pool_id, "party_id": int(party_id), "karat_id": int(karat_id), "trip_id": int(trip_id), "weight_grams": weight}
        return pool_id

    def insert_logo_document(self, *, kind, pool, weight, document_date, reference, notes, key, digest, actor, now):
        self._fail("logo_document")
        stored = self.db.logo_pools[int(pool["id"])]
        assert (stored["party_id"], stored["karat_id"], stored["trip_id"]) == (int(pool["party_id"]), int(pool["karat_id"]), int(pool["trip_id"]))   # fk_lhd_pool
        assert kind in ("HANDOVER", "RETURN") and weight > 0
        if kind == "HANDOVER":                                                                 # chk_lhd_shape, fk_lhd_handover, uq_lhd_one_handover
            assert weight == stored["weight_grams"]
            if any(d["handover_pool_id"] == int(pool["id"]) for d in self.db.logo_docs.values()):
                raise _dup("uq_lhd_one_handover")
        if any(d["request_key"] == key for d in self.db.logo_docs.values()):
            raise _dup("uq_lhd_request")
        document_id = self.db.next("ldoc")
        self.db.logo_docs[document_id] = {
            "id": document_id, "doc_kind": kind, "pool_id": int(pool["id"]), "party_id": int(pool["party_id"]), "karat_id": int(pool["karat_id"]),
            "trip_id": int(pool["trip_id"]), "weight_grams": weight, "handover_pool_id": int(pool["id"]) if kind == "HANDOVER" else None,
            "document_date": document_date, "reference": reference, "notes": notes, "request_key": key, "request_hash": digest,
            "performed_by_user_id": actor.user_id, "occurred_at": now}
        return document_id

    def insert_logo_movement(self, *, pool_id, document_id, doc_kind, movement_type, weight, delta, correction_id=None):
        self._fail("logo_movement")
        document = self.db.logo_docs[int(document_id)]
        assert (document["doc_kind"], document["pool_id"], document["weight_grams"]) == (doc_kind, int(pool_id), weight)      # fk_lhm_document
        shapes = {"HANDOVER": ("HANDOVER", weight, False), "RETURN": ("RETURN", -weight, False),
                  "HANDOVER_REVERSAL": ("HANDOVER", -weight, True), "RETURN_REVERSAL": ("RETURN", weight, True)}
        kind, expected, needs_correction = shapes[movement_type]                              # chk_lhm_shape
        assert weight > 0 and kind == doc_kind and delta == expected and (correction_id is not None) == needs_correction
        if any((m["document_id"], m["movement_type"]) == (int(document_id), movement_type) for m in self.db.logo_moves):
            raise _dup("uq_lhm_document_type")
        if correction_id is not None and any(m["correction_id"] == int(correction_id) for m in self.db.logo_moves):
            raise _dup("uq_lhm_correction")
        move_id = self.db.next("lmove")
        self.db.logo_moves.append({"id": move_id, "pool_id": int(pool_id), "document_id": int(document_id), "doc_kind": doc_kind,
                                   "movement_type": movement_type, "weight_grams": weight, "weight_delta": delta,
                                   "correction_id": None if correction_id is None else int(correction_id)})
        return move_id

    def insert_logo_link(self, *, entry_id, entry_kind, trip_id, document_id, doc_kind, pool_id, karat_id, weight):
        self._fail("logo_link")
        entry = self.db.entries[int(entry_id)]
        assert (entry["entry_kind"], entry["trip_id"]) == (entry_kind, int(trip_id))          # fk_mtlhl_entry
        document = self.db.logo_docs[int(document_id)]
        assert (document["doc_kind"], document["pool_id"], document["karat_id"], document["weight_grams"], document["trip_id"]) == (
            doc_kind, int(pool_id), int(karat_id), weight, int(trip_id))                      # fk_mtlhl_document
        assert weight > 0 and (entry_kind, doc_kind) in (("LOGO_HOLD", "HANDOVER"), ("HOLD_BACK", "RETURN"))     # chk_mtlhl_shape
        if int(entry_id) in self.db.logo_links or any(l["document_id"] == int(document_id) for l in self.db.logo_links.values()):
            raise _dup("uq_mtlhl_document")
        self.db.logo_links[int(entry_id)] = {"entry_id": int(entry_id), "entry_kind": entry_kind, "trip_id": int(trip_id), "document_id": int(document_id),
                                             "doc_kind": doc_kind, "pool_id": int(pool_id), "karat_id": int(karat_id), "designation": "LOGO", "weight_grams": weight}

    def logo_by_request(self, key):
        row = next((d for d in self.db.logo_docs.values() if d["request_key"] == key), None)
        return None if row is None else dict(row)

    def lock_logo_pool(self, pool_id):
        self.db.lock_log.append(("logo_pool", int(pool_id)))
        row = self.db.logo_pools.get(int(pool_id))
        return None if row is None else dict(row)

    def logo_pool(self, pool_id):
        row = self.db.logo_pools.get(int(pool_id))
        return None if row is None else dict(row)

    def logo_pool_balance(self, pool_id):
        return sum((m["weight_delta"] for m in self.db.logo_moves if m["pool_id"] == int(pool_id)), D("0.000"))

    def logo_pool_handover(self, pool_id):
        row = next((d for d in sorted(self.db.logo_docs.values(), key=lambda d: d["id"]) if d["pool_id"] == int(pool_id) and d["doc_kind"] == "HANDOVER"), None)
        return None if row is None else dict(row)

    def logo_link_by_entry(self, entry_id):
        link = self.db.logo_links.get(int(entry_id))
        if link is None:
            return None
        document = self.db.logo_docs[link["document_id"]]
        return {**link, "party_id": document["party_id"], "document_date": document["document_date"], "request_key": document["request_key"]}

    def logo_party_documents(self, party_id):
        rows = []
        for d in sorted(self.db.logo_docs.values(), key=lambda d: -d["id"]):
            if d["party_id"] != int(party_id):
                continue
            link = next(l for l in self.db.logo_links.values() if l["document_id"] == d["id"])
            entry = self.db.entries[link["entry_id"]]
            reversal = self._reversal(link["entry_id"])
            rows.append({"document_id": d["id"], "doc_kind": d["doc_kind"], "pool_id": d["pool_id"], "weight_grams": d["weight_grams"],
                         "document_date": d["document_date"], "reference": d["reference"], "notes": d["notes"], "occurred_at": d["occurred_at"],
                         "actor_name": "المالك", "karat_id": d["karat_id"], "karat_value": self.db.karats[d["karat_id"]]["karat_value"],
                         "entry_id": entry["id"], "trip_id": entry["trip_id"], "entry_no": entry["entry_no"],
                         "trip_status": self.db.trips[entry["trip_id"]]["status"], "pool_balance": self.logo_pool_balance(d["pool_id"]),
                         "reversal_id": reversal and int(reversal["correction_id"]), "reversal_reason": reversal and reversal["reason"]})
        return rows

    def logo_open_pools(self, *, party_id=None, trip_id=None):
        rows = []
        for p in sorted(self.db.logo_pools.values(), key=lambda p: -p["id"]):
            balance = self.logo_pool_balance(p["id"])
            if balance <= 0 or (party_id is not None and p["party_id"] != int(party_id)) or (trip_id is not None and p["trip_id"] != int(trip_id)):
                continue
            handover = self.logo_pool_handover(p["id"])
            link = next(l for l in self.db.logo_links.values() if l["document_id"] == handover["id"])
            rows.append({"pool_id": p["id"], "party_id": p["party_id"], "party_name": self.db.suppliers[p["party_id"]]["name"],
                         "party_active": self.db.suppliers[p["party_id"]]["is_active"], "karat_id": p["karat_id"],
                         "karat_value": self.db.karats[p["karat_id"]]["karat_value"], "weight_grams": p["weight_grams"], "trip_id": p["trip_id"],
                         "trip_status": self.db.trips[p["trip_id"]]["status"], "handover_id": handover["id"], "document_date": handover["document_date"],
                         "handover_entry_no": self.db.entries[link["entry_id"]]["entry_no"], "balance": balance})
        return rows


    def pool_handover(self, pool_id):
        row = next((d for d in sorted(self.db.holding_docs.values(), key=lambda d: d["id"])
                    if d["pool_id"] == int(pool_id) and d["doc_kind"] == "HANDOVER"), None)
        return None if row is None else dict(row)

    def link_by_entry(self, entry_id):
        link = self.db.holding_links.get(int(entry_id))
        if link is None:
            return None
        document = self.db.holding_docs[link["document_id"]]
        return {**link, "party_id": document["party_id"], "fineness_permille": document["fineness_permille"],
                "document_date": document["document_date"], "request_key": document["request_key"]}

    def document(self, document_id):
        row = self.db.holding_docs.get(int(document_id))
        return None if row is None else dict(row)

    def _reversal(self, entry_id):
        return next((c for c in self.db.corrections if c["source_type"] == SOURCE and c["source_id"] == str(entry_id)
                     and c["correction_type"] == "FULL_REVERSAL"), None)

    def party_documents(self, party_id):
        rows = []
        for d in sorted(self.db.holding_docs.values(), key=lambda d: -d["id"]):
            if d["party_id"] != int(party_id):
                continue
            link = next(l for l in self.db.holding_links.values() if l["document_id"] == d["id"])
            entry = self.db.entries[link["entry_id"]]
            lot = self.db.lots[d["lot_id"]]
            origin = self.db.entries[lot["entry_id"]]
            reversal = self._reversal(link["entry_id"])
            rows.append({"document_id": d["id"], "doc_kind": d["doc_kind"], "pool_id": d["pool_id"], "weight_grams": d["weight_grams"],
                         "fineness_permille": d["fineness_permille"], "document_date": d["document_date"], "reference": d["reference"],
                         "notes": d["notes"], "occurred_at": d["occurred_at"], "confirmed_at": d["confirmed_at"],
                         "intact_confirmation_code": d["intact_confirmation_code"], "actor_name": "المالك",
                         "confirmed_by_name": None if d["confirmed_by_user_id"] is None else "المالك", "lot_id": d["lot_id"],
                         "lot_no": lot["lot_no"], "lot_kind": lot["lot_kind"], "label": lot["label"], "origin_trip_id": origin["trip_id"],
                         "origin_entry_no": origin["entry_no"], "entry_id": entry["id"], "trip_id": entry["trip_id"], "entry_no": entry["entry_no"],
                         "trip_status": self.db.trips[entry["trip_id"]]["status"], "pool_balance": self.pool_balance(d["pool_id"]),
                         "reversal_id": reversal and int(reversal["correction_id"]), "reversal_reason": reversal and reversal["reason"]})
        return rows

    def open_pools(self, *, party_id=None, trip_id=None):
        rows = []
        for p in sorted(self.db.holding_pools.values(), key=lambda p: -p["id"]):
            balance = self.pool_balance(p["id"])
            if balance <= 0 or (party_id is not None and p["party_id"] != int(party_id)):
                continue
            handover = self.pool_handover(p["id"])
            if trip_id is not None and handover["trip_id"] != int(trip_id):
                continue
            link = next(l for l in self.db.holding_links.values() if l["document_id"] == handover["id"])
            lot = self.db.lots[p["lot_id"]]
            origin = self.db.entries[lot["entry_id"]]
            rows.append({"pool_id": p["id"], "party_id": p["party_id"], "party_name": self.db.suppliers[p["party_id"]]["name"],
                         "party_active": self.db.suppliers[p["party_id"]]["is_active"], "lot_id": p["lot_id"], "weight_grams": p["weight_grams"],
                         "fineness_permille": p["fineness_permille"], "lot_no": lot["lot_no"], "lot_kind": lot["lot_kind"], "label": lot["label"],
                         "origin_trip_id": origin["trip_id"], "origin_entry_no": origin["entry_no"], "handover_id": handover["id"],
                         "document_date": handover["document_date"], "trip_id": handover["trip_id"],
                         "trip_status": self.db.trips[handover["trip_id"]]["status"],
                         "handover_entry_no": self.db.entries[link["entry_id"]]["entry_no"], "balance": balance,
                         "settlement_count": len(self.pool_settlement_history(p["id"])),
                         "allocation_count": sum(1 for h in self.pool_settlement_history(p["id"]) if h["physical_declaration"] in ("GRAM_ENTITLEMENT", "PHYSICALLY_DIVIDED")),
                         "first_allocation_id": next((h["id"] for h in self.pool_settlement_history(p["id"])
                                                      if h["physical_declaration"] in ("GRAM_ENTITLEMENT", "PHYSICALLY_DIVIDED")), None)})
        return rows


class SupplierPayRepo:
    """The slice of SupplierPaymentRepository a custody payment and its reversal use, over the same fake database. The 036 / 043
    constraints of supplier_making_payments and its legs are mirrored as asserts."""
    TYPES = {"PAYMENT": 1, "PAYMENT_REVERSAL": 2}

    def __init__(self, db):
        self.db = db

    def _fail(self, step):
        if self.db.fail_at == step:
            raise RuntimeError(f"forced failure at {step}")

    def get_supplier(self, supplier_id, lock=False):
        row = self.db.suppliers.get(int(supplier_id))
        return None if row is None else dict(row)

    def by_request(self, key):
        row = next((p for p in self.db.sm_payments.values() if p["request_key"] == key), None)
        return None if row is None else dict(row)

    def making_type_id(self, code):
        return self.TYPES.get(code)

    def making_balance_current(self, supplier_id):
        return sum((m["amount_delta"] for m in self.db.sm_making if m["supplier_id"] == int(supplier_id)), D("0.00"))

    def insert_header(self, *, supplier_id, method, cash_account_id, bank_account_id, amount, payment_date, reference, notes, before, after,
                      key, digest, actor, now):
        self._fail("sm_header")
        assert amount > 0 and after == before - amount                                       # chk_smp_amount / chk_smp_balance
        assert (method == "CUSTODY" and cash_account_id is None and bank_account_id is None) or method in ("CASH", "BANK")   # chk_smp_method
        if any(p["request_key"] == key for p in self.db.sm_payments.values()):
            raise _dup("uq_smp_request")
        payment_id = self.db.next("payment")
        self.db.sm_payments[payment_id] = {"id": payment_id, "supplier_id": int(supplier_id), "payment_method": method,
                                           "cash_account_id": cash_account_id, "bank_account_id": bank_account_id, "amount": amount,
                                           "payment_date": payment_date, "reference": reference, "notes": notes, "balance_before": before,
                                           "balance_after": after, "request_key": key, "request_hash": digest,
                                           "performed_by_user_id": actor.user_id, "occurred_at": now}
        return payment_id

    def insert_making_movement(self, *, supplier_id, type_id, amount_delta, source_type, source_id, reference, description, now, user_id):
        self._fail("sm_making")
        movement_id = self.db.next("sm_making")
        self.db.sm_making.append({"id": movement_id, "supplier_id": int(supplier_id), "type_id": int(type_id), "amount_delta": amount_delta,
                                  "source_type": source_type, "source_id": int(source_id), "reference": reference})
        return movement_id

    def insert_leg(self, *, payment_id, kind, cash_movement_id=None, bank_movement_id=None, making_movement_id=None):
        self._fail("sm_leg")
        assert kind == "MAKING" and making_movement_id is not None and cash_movement_id is None and bank_movement_id is None   # no MONEY leg here
        self.db.sm_legs.append({"payment_id": int(payment_id), "leg_kind": kind, "making_movement_id": int(making_movement_id)})

    def audit(self, *, actor, event_type, entity_id, reference, description, metadata, now, entity_type="SUPPLIER"):
        self._fail("sm_audit")
        self.db.audit_rows.append({"user_id": actor.user_id, "event_type": event_type, "entity_type": entity_type, "entity_id": int(entity_id),
                                   "reference": reference, "metadata": metadata})

    def payment_making_movement(self, payment_id):
        leg = next((l for l in self.db.sm_legs if l["payment_id"] == int(payment_id) and l["leg_kind"] == "MAKING"), None)
        if leg is None:
            return None
        move = next(m for m in self.db.sm_making if m["id"] == leg["making_movement_id"])
        return {"id": move["id"], "supplier_id": move["supplier_id"], "amount_delta": move["amount_delta"]}

    def source(self, payment_id, lock=False):
        row = self.db.sm_payments.get(int(payment_id))
        return None if row is None else {**row, "supplier_name": self.db.suppliers[row["supplier_id"]]["name"]}

    def balance(self, supplier_id):
        return self.making_balance_current(supplier_id)


class SettlementRepo:
    """SupplierGoldSettlementRepository over the same fake database: the 045 CHECK / unique constraints of the document and its legs are mirrored
    as asserts, the Scrap and supplier gold ledgers are fake tables of the same Db (one snapshot / rollback covers every posting step)."""
    GOLD_TYPES = {"SETTLEMENT": 1, "SETTLEMENT_REVERSAL": 2}

    def __init__(self, db):
        self.db = db

    def _fail(self, step):
        if self.db.fail_at == step:
            raise RuntimeError(f"forced failure at {step}")

    def get_supplier(self, supplier_id, lock=False):
        if lock:
            self.db.lock_log.append(("party", int(supplier_id)))
        row = self.db.suppliers.get(int(supplier_id))
        if row is None:
            return None
        karat_id = row.get("karat_id")
        return {**row, "karat_value": None if karat_id is None else self.db.karats[karat_id]["karat_value"]}

    def get_karat(self, karat_id):
        row = self.db.karats.get(int(karat_id))
        return None if row is None else dict(row)

    def list_suppliers(self, search=None, active=None, limit=500, role=None):
        """The Supplier Master list the holding settlement dialog offers as recipients (role SUPPLIER = the supplier flag)."""
        rows = []
        for row in sorted(self.db.suppliers.values(), key=lambda s: (-s["is_active"], s["id"])):
            if role == "SUPPLIER" and not row.get("is_supplier", True):
                continue
            karat_id = row.get("karat_id")
            rows.append({**row, "karat_value": None if karat_id is None else self.db.karats[karat_id]["karat_value"]})
        return rows

    def active_karats(self):
        return [dict(k) for k in sorted(self.db.karats.values(), key=lambda k: -k["karat_value"]) if k["is_active"]]

    def shop_scrap(self):
        return [{"karat_id": k["id"], "karat_value": k["karat_value"], "physical_grams": self.db.physical(k["id"]),
                 "held_grams": self.db.holds.get(k["id"], D("0.000")), "available_grams": self.db.available(k["id"])} for k in self.active_karats()]

    def by_request(self, key):
        row = next((g for g in self.db.settlements.values() if g["request_key"] == key), None)
        return None if row is None else dict(row)

    def request_key_used_elsewhere(self, key):
        return (any(e["request_key"] == key for e in self.db.entries.values())
                or any(d["request_key"] == key for d in self.db.holding_docs.values())
                or any(d["request_key"] == key for d in self.db.logo_docs.values())
                or any(r["request_key"] == key for r in self.db.receipts.values()))

    def insert_header(self, *, supplier_id, delivered_karat, target_karat, delivered_weight, credited, analytical, difference, settlement_date,
                      reference, notes, key, digest, actor, now, source_kind="SHOP_SCRAP"):
        self._fail("sg_header")
        assert source_kind in ("SHOP_SCRAP", "CUSTODY_GOLD", "CUSTODY_LOT")                                  # chk_sgs_source (046)
        assert delivered_weight > 0 and credited > 0 and analytical > 0                       # chk_sgs_weights
        assert difference == credited - analytical                                            # chk_sgs_rounding
        if any(g["request_key"] == key for g in self.db.settlements.values()):
            raise _dup("uq_sgs_request")
        settlement_id = self.db.next("settlement")
        self.db.settlements[settlement_id] = {
            "id": settlement_id, "supplier_id": int(supplier_id), "source_kind": source_kind, "delivered_karat_id": int(delivered_karat["id"]),
            "delivered_karat_value": int(delivered_karat["karat_value"]), "target_karat_id": int(target_karat["id"]),
            "target_karat_value": int(target_karat["karat_value"]), "delivered_weight_grams": delivered_weight, "credited_weight_grams": credited,
            "credited_analytical_grams": analytical, "rounding_difference_grams": difference, "settlement_date": settlement_date,
            "reference": reference, "notes": notes, "request_key": key, "request_hash": digest, "performed_by_user_id": actor.user_id,
            "occurred_at": now}
        return settlement_id

    def insert_lot_header(self, *, supplier_id, lot, target_karat, credited, analytical, difference, settlement_date, reference, notes, key, digest, actor, now):
        self._fail("sg_header")
        assert lot["fineness_permille"] is not None and credited>0
        if any(h["request_key"]==key for h in self.db.settlements.values()): raise _dup("uq_sgs_request")
        sid=self.db.next("settlement")
        self.db.settlements[sid]={"id":sid,"supplier_id":int(supplier_id),"source_kind":"CUSTODY_LOT","delivered_lot_id":lot["id"],"origin_melt_entry_id":lot["entry_id"],"delivered_fineness_permille":lot["fineness_permille"],"delivered_karat_id":None,"delivered_karat_value":None,"target_karat_id":target_karat["id"],"target_karat_value":target_karat["karat_value"],"delivered_weight_grams":lot["weight_grams"],"credited_weight_grams":credited,"credited_analytical_grams":analytical,"rounding_difference_grams":difference,"settlement_date":settlement_date,"reference":reference,"notes":notes,"request_key":key,"request_hash":digest,"performed_by_user_id":actor.user_id,"occurred_at":now}
        return sid

    def insert_holding_header(self, *, kind, supplier_id, pool, target_karat, quantity, credited, analytical, difference, declaration, self_confirmed,
                              settlement_date, reference, notes, key, digest, actor, now):
        self._fail("sg_header")
        lot = kind == "HOLDING_LOT"
        assert kind in ("HOLDING_LOT", "HOLDING_LOGO")                                                       # chk_sgs_source
        assert quantity > 0 and credited > 0 and analytical > 0 and difference == credited - analytical     # chk_sgs_weights / chk_sgs_rounding
        if lot:                                                                                              # chk_sgs_shape + fk_sgs_lot_pool
            stored = self.db.holding_pools[int(pool["id"])]
            assert (stored["party_id"], stored["lot_id"], stored["weight_grams"], stored["fineness_permille"]) == (
                int(pool["party_id"]), int(pool["lot_id"]), pool["weight_grams"], pool["fineness_permille"])
            assert quantity <= pool["weight_grams"] and declaration in ("WHOLE_ORIGINAL_BAR", "GRAM_ENTITLEMENT", "PHYSICALLY_DIVIDED")
            assert declaration != "WHOLE_ORIGINAL_BAR" or quantity == pool["weight_grams"]
        else:                                                                                                # fk_sgs_logo_pool
            stored = self.db.logo_pools[int(pool["id"])]
            assert (stored["party_id"], stored["karat_id"]) == (int(pool["party_id"]), int(pool["karat_id"])) and declaration is None
        assert int(supplier_id) != int(pool["party_id"]) or self_confirmed                                  # chk_sgs_self
        if any(g["request_key"] == key for g in self.db.settlements.values()):
            raise _dup("uq_sgs_request")
        settlement_id = self.db.next("settlement")
        self.db.settlements[settlement_id] = {
            "id": settlement_id, "supplier_id": int(supplier_id), "source_kind": kind,
            "delivered_karat_id": None if lot else int(pool["karat_id"]), "delivered_karat_value": None if lot else int(pool["karat_value"]),
            "target_karat_id": int(target_karat["id"]), "target_karat_value": int(target_karat["karat_value"]), "delivered_weight_grams": quantity,
            "credited_weight_grams": credited, "credited_analytical_grams": analytical, "rounding_difference_grams": difference,
            "settlement_date": settlement_date, "reference": reference, "notes": notes, "request_key": key, "request_hash": digest,
            "performed_by_user_id": actor.user_id, "occurred_at": now,
            "delivered_lot_id": int(pool["lot_id"]) if lot else None, "delivered_lot_weight_grams": pool["weight_grams"] if lot else None,
            "origin_melt_entry_id": int(pool["origin_melt_entry_id"]) if lot else None,
            "delivered_fineness_permille": pool["fineness_permille"] if lot else None, "holding_party_id": int(pool["party_id"]),
            "lot_pool_id": int(pool["id"]) if lot else None, "logo_pool_id": None if lot else int(pool["id"]), "physical_declaration": declaration if lot else None,
            "holding_weight_grams": quantity, "self_settlement_confirmed": 1 if self_confirmed else 0}
        return settlement_id

    def post_scrap_out(self, *, settlement_id, karat_id, weight, code, notes, actor, now):
        self._fail("sg_scrap")
        move_id = self.db.next("scrap")
        self.db.scrap.append({"id": move_id, "karat_id": int(karat_id), "weight_delta": -weight, "reference_type": "SUPPLIER_GOLD_SETTLEMENT",
                              "reference_id": str(settlement_id), "type_code": "SUPPLIER_GOLD_SETTLEMENT_OUT"})
        return move_id

    def gold_type_id(self, code):
        return self.GOLD_TYPES.get(code)

    def insert_gold_movement(self, *, supplier_id, type_id, karat_id, weight_delta, eq21_delta, source_type, source_id, reference, description,
                             now, user_id):
        self._fail("sg_gold")
        assert (weight_delta > 0 and eq21_delta > 0) or (weight_delta < 0 and eq21_delta < 0)  # chk_sgm_sign
        if any((m["source_type"], m["source_id"], m["type_id"]) == (source_type, int(source_id), int(type_id)) for m in self.db.sup_gold):
            raise _dup("uq_sgm_source")
        movement_id = self.db.next("sup_gold")
        self.db.sup_gold.append({"id": movement_id, "supplier_id": int(supplier_id), "type_id": int(type_id), "karat_id": int(karat_id),
                                 "weight_delta": weight_delta, "eq21": eq21_delta, "source_type": source_type, "source_id": int(source_id),
                                 "reference": reference})
        return movement_id

    def insert_leg(self, *, settlement_id, kind, source_kind, old_gold_movement_id=None, supplier_gold_movement_id=None):
        self._fail("sg_leg")
        assert source_kind in ("SHOP_SCRAP", "CUSTODY_GOLD", "CUSTODY_LOT", "HOLDING_LOT", "HOLDING_LOGO", "HOLDING_MKT")   # chk_sgsl_source
        assert kind != "SCRAP" or source_kind == "SHOP_SCRAP"                                 # chk_sgsl_scrap_only: no second Scrap deduction
        assert self.db.settlements[int(settlement_id)]["source_kind"] == source_kind          # fk_sgsl_source (the composite foreign key)
        assert (kind == "SCRAP" and old_gold_movement_id is not None and supplier_gold_movement_id is None) or \
               (kind == "GOLD" and supplier_gold_movement_id is not None and old_gold_movement_id is None)      # chk_sgsl_leg
        if any((l["settlement_id"], l["leg_kind"]) == (int(settlement_id), kind) for l in self.db.settlement_legs):
            raise _dup("uq_sgsl_kind")
        self.db.settlement_legs.append({"settlement_id": int(settlement_id), "source_kind": source_kind, "leg_kind": kind,
                                        "old_gold_movement_id": old_gold_movement_id,
                                        "supplier_gold_movement_id": supplier_gold_movement_id})

    def audit(self, *, actor, event_type, entity_id, reference, description, metadata, now, entity_type="SUPPLIER"):
        self._fail("sg_audit")
        self.db.audit_rows.append({"user_id": actor.user_id, "event_type": event_type, "entity_type": entity_type, "entity_id": int(entity_id),
                                   "reference": reference, "metadata": metadata})

    def db_now(self):
        return self.db.now

    def _reversal(self, settlement_id):
        link = next((l for l in list(self.db.gold_settle_links.values()) + list(self.db.lot_settle_links.values()) if l["settlement_id"] == int(settlement_id)), None)
        return next((c for c in self.db.corrections if c["correction_type"] == "FULL_REVERSAL"
                     and ((c["source_type"] == "SUPPLIER_GOLD_SETTLEMENT" and c["source_id"] == str(settlement_id))
                          or (link is not None and c["source_type"] == "MARKET_TRIP_ENTRY" and c["source_id"] == str(link["entry_id"])))), None)

    def custody_entry(self, settlement_id):
        link = next((l for l in list(self.db.gold_settle_links.values()) + list(self.db.lot_settle_links.values()) if l["settlement_id"] == int(settlement_id)), None)
        if link is None:
            return None
        entry = self.db.entries[link["entry_id"]]
        return {"entry_id": link["entry_id"], "designation": link.get("designation"), "trip_id": entry["trip_id"], "entry_no": entry["entry_no"]}

    def settlement(self, settlement_id):
        row = self.db.settlements.get(int(settlement_id))
        if row is None:
            return None
        reversal, custody = self._reversal(settlement_id), self.custody_entry(settlement_id)
        return {**row, "supplier_name": self.db.suppliers[row["supplier_id"]]["name"], "actor_name": "المالك",
                "holding_party_name": (self.db.suppliers[row["holding_party_id"]]["name"] if row.get("holding_party_id") else None),
                "custody_entry_id": custody and custody["entry_id"], "custody_designation": custody and custody["designation"],
                "custody_trip_id": custody and custody["trip_id"], "custody_entry_no": custody and custody["entry_no"],
                "reversal_id": reversal and int(reversal["correction_id"]), "reversal_reason": reversal and reversal["reason"]}

    def source(self, settlement_id, lock=False):
        row = self.db.settlements.get(int(settlement_id))
        return None if row is None else {**row, "supplier_name": self.db.suppliers[row["supplier_id"]]["name"]}

    def scrap_movement(self, settlement_id):
        leg = next((l for l in self.db.settlement_legs if l["settlement_id"] == int(settlement_id) and l["leg_kind"] == "SCRAP"), None)
        if leg is None:
            return None
        move = next(m for m in self.db.scrap if m["id"] == leg["old_gold_movement_id"])
        return {"id": move["id"], "karat_id": move["karat_id"], "weight_delta_grams": move["weight_delta"]}

    def gold_movement(self, settlement_id):
        leg = next((l for l in self.db.settlement_legs if l["settlement_id"] == int(settlement_id) and l["leg_kind"] == "GOLD"), None)
        if leg is None:
            return None
        move = next(m for m in self.db.sup_gold if m["id"] == leg["supplier_gold_movement_id"])
        return {"id": move["id"], "supplier_id": move["supplier_id"], "karat_id": move["karat_id"], "weight_delta_grams": move["weight_delta"],
                "equivalent_21_delta_grams": move["eq21"]}

    def balance(self, supplier_id, karat_id):
        return sum((m["weight_delta"] for m in self.db.sup_gold if m["supplier_id"] == int(supplier_id) and m["karat_id"] == int(karat_id)), D("0.000"))
