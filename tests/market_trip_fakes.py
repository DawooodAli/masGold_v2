"""In-memory doubles for the market-trip tests: a transactional fake database (a failed transaction restores every table, so
atomicity is testable), the repository contracts of MarketTripRepository / the Scrap lock / the cash account / the
CorrectionRepository subset a full reversal uses, with the 037/039 database checks and unique keys. The SQL itself is proven by the
schema tests and the rehearsal run; real-MySQL concurrency is NOT claimed from anything in here."""
import copy
from contextlib import contextmanager
from datetime import datetime
from decimal import Decimal as D

from mysql.connector import IntegrityError

from app.session import AppSession
from repositories.market_trip_repository import (CASH_TYPE, CREATE_MOVEMENT, DESIGNATIONS, ISSUE, MELT_DESIGNATION, SCRAP_TYPE,
                                                 SOURCE, UNCLASSIFIED)
from services.authentication_service import AuthenticationResult, _VERIFIED
from tests.auth_fakes import FakeAuthorization

OWNER = frozenset({"APP_LOGIN", "MARKET_TRIP_MANAGE", "TRANSACTION_CORRECT"})
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
        self.corrections, self.audit_rows = [], []
        self.sequences = {"trip": 0, "entry": 0, "cash_move": 1, "scrap": 2, "correction": 0, "lot": 0}
        self.fail_at = None
        self.transactions = 0

    def next(self, name):
        self.sequences[name] += 1
        return self.sequences[name]

    @contextmanager
    def transaction(self):
        self.transactions += 1
        snapshot = copy.deepcopy({k: v for k, v in self.__dict__.items() if k not in ("fail_at", "transactions")})
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

    def shop_scrap(self):
        return [{"karat_id": k["id"], "karat_value": k["karat_value"], "physical_grams": self.db.physical(k["id"]),
                 "held_grams": self.db.holds.get(k["id"], D("0.000")), "available_grams": self.db.available(k["id"])}
                for k in sorted(self.db.karats.values(), key=lambda k: -k["karat_value"]) if k["is_active"]]

    # ---- trips
    def insert_trip(self, *, notes, actor, now):
        self._fail("trip")
        trip_id = self.db.next("trip")
        self.db.trips[trip_id] = {"id": trip_id, "status": "OPEN", "notes": notes, "created_by_user_id": actor.user_id,
                                  "created_at": now, "status_changed_at": now}
        return trip_id

    def trip(self, trip_id, lock=False):
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
        assert kind in ("ISSUE", "RETURN", "MELT")                                           # chk_mte_kind
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
        assert movement_type == CREATE_MOVEMENT or movement_type in ("TEST_USE",)            # chk_mtlm_type (M1: CREATE only)
        assert weight_delta != 0 and (movement_type != CREATE_MOVEMENT or weight_delta > 0)  # chk_mtlm_delta
        if any((m["lot_id"], m["entry_id"], m["movement_type"]) == (int(lot_id), int(entry_id), movement_type) for m in self.db.lot_moves):
            raise _dup("uq_mtlm_lot_entry_type")
        self.db.lot_moves.append({"lot_id": int(lot_id), "entry_id": int(entry_id), "movement_type": movement_type,
                                  "weight_delta": weight_delta})

    # ---- derived custody (entries that were not reversed)
    def _live(self, trip_id, exclude):
        reversed_ids = {int(c["source_id"]) for c in self.db.corrections
                        if c["source_type"] == SOURCE and c["correction_type"] == "FULL_REVERSAL"}
        return [e for e in self.db.entries.values()
                if e["trip_id"] == int(trip_id) and e["id"] not in reversed_ids and e["id"] != int(exclude or 0)]

    def custody_cash(self, trip_id, exclude_entry_id=None):
        issued = returned = D("0.00")
        for e in self._live(trip_id, exclude_entry_id):
            for leg in (l for l in self.db.cash_legs if l["entry_id"] == e["id"]):
                issued, returned = (issued + leg["amount"], returned) if e["entry_kind"] == "ISSUE" else (issued, returned + leg["amount"])
        return issued, returned

    def custody_gold(self, trip_id, exclude_entry_id=None):
        out = {}
        live = self._live(trip_id, exclude_entry_id)
        for e in live:
            for leg in (l for l in self.db.gold_legs if l["entry_id"] == e["id"]):
                row = out.setdefault((leg["karat_id"], leg["designation"]), {
                    "karat_id": leg["karat_id"], "karat_value": self.db.karats[leg["karat_id"]]["karat_value"],
                    "designation": leg["designation"], "issued": D("0.000"), "returned": D("0.000"), "melted": D("0.000")})
                row["issued" if e["entry_kind"] == "ISSUE" else "returned"] += leg["weight_grams"]
            for i in (i for i in self.db.melt_inputs if i["entry_id"] == e["id"]):
                row = out.setdefault((i["karat_id"], MELT_DESIGNATION), {
                    "karat_id": i["karat_id"], "karat_value": self.db.karats[i["karat_id"]]["karat_value"],
                    "designation": MELT_DESIGNATION, "issued": D("0.000"), "returned": D("0.000"), "melted": D("0.000")})
                row["melted"] += i["weight_grams"]
        order = {"MELT": 0, "LOGO": 1, "UNCLASSIFIED": 2}
        return sorted(out.values(), key=lambda r: (-r["karat_value"], order[r["designation"]]))

    def custody_lots(self, trip_id, exclude_entry_id=None):
        live_ids = {e["id"] for e in self._live(trip_id, exclude_entry_id)}
        rows = []
        for lot in sorted(self.db.lots.values(), key=lambda l: (self.db.entries[l["entry_id"]]["entry_no"], l["lot_no"])):
            if lot["entry_id"] not in live_ids:
                continue
            outstanding = sum((m["weight_delta"] for m in self.db.lot_moves if m["lot_id"] == lot["id"] and m["entry_id"] in live_ids),
                              D("0.000"))
            rows.append({**lot, "entry_no": self.db.entries[lot["entry_id"]]["entry_no"], "outstanding_grams": outstanding})
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

    def entries(self, trip_id):
        rows = []
        for e in sorted((e for e in self.db.entries.values() if e["trip_id"] == int(trip_id)), key=lambda e: e["entry_no"]):
            cash = next((l for l in self.db.cash_legs if l["entry_id"] == e["id"]), None)
            reversal = next((c for c in self.db.corrections if c["source_type"] == SOURCE and c["source_id"] == str(e["id"])
                             and c["correction_type"] == "FULL_REVERSAL"), None)
            rows.append({**e, "actor_name": "المالك", "cash_amount": cash and cash["amount"], "cash_account_name": cash and "الخزنة الرئيسية",
                         "reversal_id": reversal and int(reversal["correction_id"]), "reversal_reason": reversal and reversal["reason"],
                         "reversal_at": reversal and reversal["occurred_at"],
                         "gold": [{"karat_id": g["karat_id"], "karat_value": self.db.karats[g["karat_id"]]["karat_value"],
                                   "designation": g["designation"], "weight_grams": g["weight_grams"]}
                                  for g in self.db.gold_legs if g["entry_id"] == e["id"]]})
        return rows

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
                                   "entity_id": int(entity_id), "reference": reference})


class ScrapRepo:
    """OldGoldConversionRepository.lock_old_gold_balance / held_old_gold: counted rows plus HELD grams as one negative row."""

    def __init__(self, db):
        self.db = db

    def lock_old_gold_balance(self, karat_id):
        rows = [{"id": m["id"], "weight_delta_grams": m["weight_delta"]} for m in self.db.scrap if m["karat_id"] == int(karat_id)]
        held = self.db.holds.get(int(karat_id), D("0.000"))
        return rows + ([{"id": None, "weight_delta_grams": -held}] if held else [])

    def held_old_gold(self, karat_id):
        return self.db.holds.get(int(karat_id), D("0.000"))


class AccountsRepo:
    def __init__(self, db):
        self.db = db

    def account(self, ledger, account_id, lock=False):
        row = self.db.cash_accounts.get(int(account_id)) if ledger == "CASH" else None
        return None if row is None else {"id": row["id"], "name": row["name"], "account_type": "CASH", "is_active": row["is_active"]}


class Corrections:
    """The CorrectionRepository subset a standalone full reversal uses, with the 023/037 contracts."""

    def __init__(self, db):
        self.db = db

    def _fail(self, step):
        if self.db.fail_at == step:
            raise RuntimeError(f"forced failure at {step}")

    def corrections_for_source(self, source_type, source_id):
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
        assert kw["source_type"] == SOURCE and kw["invoice_id"] is None and kw["correction_type"] == "FULL_REVERSAL"
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
        assert movement_code in ("REVERSAL_TRIP_ISSUE_IN", "REVERSAL_TRIP_RETURN_OUT")
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
                                authorization=authorization, corrections=corrections)
    return db, session, authorization, service, corrections
