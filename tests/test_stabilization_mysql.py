"""Focused REAL-MySQL validation of the stabilization patch: Gold Position aggregation (SQL + arithmetic), marketer integration, snapshot consistency under a concurrent
transfer, request-key integrity (sequential and concurrent) and a bounded performance measurement.

Explicit opt-in (process-local): MASGOLD_STABILIZATION_REHEARSAL=1 (the performance class additionally needs MASGOLD_STABILIZATION_PERF=1). Connects to
masgold_rehearsal ONLY: the target name, the rehearsal marker and migration level >= 55 are asserted before any write, and every connection re-asserts DATABASE().
Real services, real repositories, real SQL, REPEATABLE READ connections. Fixtures are committed and NEVER cleaned (immutable ledgers); everything is tagged (STAB...).
Nothing here disables a constraint, drops, truncates or rebuilds anything. This is NOT a stress test and repeated successes are evidence, not proof."""
from contextlib import contextmanager
from dataclasses import replace
from decimal import Decimal as D
import json
import logging
import os
import threading
import time
import unittest
from uuid import uuid4

from mysql.connector import Error as MySqlError

from app.session import AppSession
from config.database_config import DatabaseConfig
from database.connection import DatabaseConnection
from repositories.authentication_repository import AuthenticationRepository
from repositories.market_trip_repository import MarketTripRepository
from repositories.marketer_custody_repository import MarketerCustodyRepository
from repositories.intermediary_holding_repository import IntermediaryHoldingRepository
from repositories.supplier_gold_settlement_repository import SupplierGoldSettlementRepository
from services import custody_rules
from services.authentication_service import AuthenticationResult, _VERIFIED
from services.correction_service import CorrectionService
from services.gold_position_service import GoldPositionService
from services.intermediary_holding_service import IntermediaryHoldingService
from services.market_trip_service import MarketTripService, custody_view
from services.marketer_custody_service import MarketerCustodyService
from services.supplier_gold_settlement_service import SupplierGoldSettlementService
from services.supplier_payment_service import SupplierPaymentService
from services.supplier_service import SupplierService
from tests.test_market_trip_hardening_mysql import UnlockedAuthorization

TARGET = "masgold_rehearsal"
DATE = "2026-10-17"
REFERENCE = {21: D("6000.0000"), 18: D("5142.8571"), 24: D("7000.0000")}
OUTPUT = os.environ.get("MASGOLD_STAB_OUT", os.path.join(os.environ.get("TEMP", "."), "stab_mysql_outcomes.json"))
ENABLED = os.environ.get("MASGOLD_STABILIZATION_REHEARSAL") == "1"
PERF = os.environ.get("MASGOLD_STABILIZATION_PERF") == "1"
OUTCOMES = {}


def record(section, value):
    OUTCOMES.setdefault(section, []).append(value)
    with open(OUTPUT, "w", encoding="utf-8") as handle:
        json.dump(OUTCOMES, handle, ensure_ascii=False, indent=1, default=str)


def key():
    return str(uuid4())


class Env:
    """Built once per process: sessions, parties, Scrap, services. All writes are committed fixtures tagged STAB."""
    built = False

    @classmethod
    def build(cls):
        if cls.built:
            return cls
        cls.config = replace(DatabaseConfig.from_env(), database=TARGET)
        with cls.tx() as c:
            q = c.cursor(dictionary=True)
            q.execute("SELECT DATABASE() AS db, MAX(version) AS v FROM schema_migrations")
            row = q.fetchone()
            assert row["db"] == TARGET and int(row["v"]) >= 55, row
            q.execute("SELECT target_database FROM masgold_rehearsal_marker WHERE marker_key='MASGOLD_SCHEMA_REHEARSAL'")
            assert q.fetchone()["target_database"] == TARGET
            q.execute("SELECT id,karat_value FROM karats")
            cls.karats = {r["karat_value"]: r["id"] for r in q.fetchall()}
            q.close()
        cls.main, cls.user1 = cls.make_session("main")
        cls.other, cls.user2 = cls.make_session("other")
        with cls.tx() as c:                                         # Scrap for the issues (the same OPENING_BALANCE fixture the other rehearsal suites use)
            q = c.cursor()
            for karat in (21, 18, 24):
                q.execute("INSERT INTO old_gold_movements(movement_type_id,karat_id,weight_delta_grams,created_by_user_id) "
                          "SELECT id,%s,10000,%s FROM old_gold_movement_types WHERE code='OPENING_BALANCE'", (cls.karats[karat], cls.user1))
                assert q.rowcount == 1
            q.execute("SELECT id FROM cash_accounts WHERE code='MAIN_SAFE'")
            assert q.fetchone() is not None
            q.close()
        suppliers = SupplierService(cls.main, transaction_factory=cls.tx)
        tag = uuid4().hex[:8]
        cls.marketer_only = str(suppliers.create_supplier(f"STAB مسوقجي {tag}", is_supplier=False, is_intermediary=True))
        cls.marketer_two = str(suppliers.create_supplier(f"STAB مسوقجي ثان {tag}", is_supplier=False, is_intermediary=True))
        cls.dual = str(suppliers.create_supplier(f"STAB مسوقجي ومورد {tag}", karat_id=cls.karats[21], is_supplier=True, is_intermediary=True))
        cls.supplier18 = str(suppliers.create_supplier(f"STAB مورد 18 {tag}", karat_id=cls.karats[18]))
        cls.supplier21 = str(suppliers.create_supplier(f"STAB مورد 21 {tag}", karat_id=cls.karats[21]))
        cls.built = True
        return cls

    @classmethod
    def make_session(cls, label):
        with cls.tx() as c:
            q = c.cursor()
            q.execute("INSERT INTO employees(name) VALUES(%s)", (f"STAB rehearsal {label}",))
            emp = q.lastrowid
            q.execute("INSERT INTO users(employee_id,username,password_hash,display_name) VALUES(%s,%s,'unusable-rehearsal-hash',%s)",
                      (emp, f"stab-{label}-" + uuid4().hex[:12], f"STAB {label}"))
            user = q.lastrowid
            q.execute("INSERT INTO user_roles(user_id,role_id) SELECT %s,id FROM roles WHERE code='OWNER'", (user,))
            assert q.rowcount == 1
            q.close()
            auth = AuthenticationRepository(c)
            identity, permissions = auth.get_identity(user), auth.effective_permissions(user)
        session = AppSession()
        session._establish(AuthenticationResult(user, emp, identity["display_name"], permissions, _VERIFIED), int(session.generation))
        return session, user

    @classmethod
    @contextmanager
    def tx(cls, readonly=False, wait=15):
        assert cls.config.database == TARGET
        c = DatabaseConnection(cls.config).connect()
        try:
            q = c.cursor()
            q.execute("SELECT DATABASE()")
            assert q.fetchone() == (TARGET,)
            q.execute(f"SET SESSION innodb_lock_wait_timeout = {int(wait)}")             # every wait is bounded
            q.close()
            c.rollback()
            c.start_transaction(isolation_level="REPEATABLE READ", readonly=readonly)
            yield c
            if readonly:
                c.rollback()
            else:
                c.commit()
        except BaseException:
            c.rollback()
            raise
        finally:
            c.close()

    @classmethod
    def read_tx(cls):
        """What GoldPositionService.read_transaction does, pointed at the rehearsal target: READ ONLY, REPEATABLE READ, always rolled back."""
        return cls.tx(readonly=True)

    @classmethod
    def services(cls, session, authorization=None, trip_repo=MarketTripRepository, marketer_repo=MarketerCustodyRepository, sg_repo=SupplierGoldSettlementRepository):
        extra = {} if authorization is None else {"authorization": authorization}
        corrections = CorrectionService(session, transaction_factory=cls.tx, **extra)
        kw = dict(transaction_factory=cls.tx, **extra)
        trips = MarketTripService(session, repository=trip_repo, price_reference=lambda: dict(REFERENCE), corrections=corrections, **kw)
        return type("S", (), dict(
            corrections=corrections, trips=trips,
            marketer=MarketerCustodyService(session, repository=marketer_repo, price_reference=lambda: dict(REFERENCE), corrections=corrections, **kw),
            direct=SupplierGoldSettlementService(session, repository=sg_repo, corrections=corrections, **kw),
            pay=SupplierPaymentService(session, corrections=corrections, **kw),
            hold=IntermediaryHoldingService(session, **kw),
            position=GoldPositionService(session, transaction_factory=cls.read_tx, **extra)))()


class Base:
    @classmethod
    def setUpClass(cls):
        if not ENABLED:
            raise unittest.SkipTest("explicit rehearsal-only opt-in (MASGOLD_STABILIZATION_REHEARSAL=1)")
        cls.env = Env.build()
        cls.s = Env.services(cls.env.main)
        cls.k = cls.env.karats

    def query(self, sql, params=()):
        with Env.tx() as c:
            q = c.cursor(dictionary=True)
            q.execute(sql, params)
            rows = q.fetchall()
            q.close()
            return rows

    def counts(self, *tables):
        return {t: self.query(f"SELECT COUNT(*) AS n FROM {t}")[0]["n"] for t in tables}

    def trip(self, grams="20", designation="LOGO", cash="", karat=21, tag="STAB"):
        return self.s.trips.issue("", cash, [{"karat_id": self.k[karat], "weight": grams, "designation": designation}], DATE, f"{tag} {uuid4().hex[:6]}", key())

    def pos(self, service=None):
        return (service or self.s.position).position()


def terms(row):
    return {name: D(str(row.get(name) or 0)) for name in ("issued", "returned", "melted", "heat_delta", "supplier_settled", "held_out", "held_back", "sold", "purchased")}


def gold_map(rows):
    """{(karat_id, designation): terms + outstanding}; all-zero rows are display-irrelevant and dropped."""
    out = {}
    for r in rows:
        t = terms(r)
        t["outstanding"] = custody_rules.gold_outstanding(r)
        if any(v != 0 for v in t.values()):
            out[(int(r["karat_id"]), r["designation"])] = t
    return out


# ============================================================================================================================ A. parity
class GoldPositionParityTests(Base, unittest.TestCase):
    def build(self):
        s, k = self.s, self.k
        a = self.trip("30", "LOGO", cash="50000", tag="STAB-PARITY")
        tid = a["trip_id"]
        s.trips.return_custody(tid, "", [{"karat_id": k[21], "weight": "4", "designation": "LOGO"}], DATE, "", key())
        s.trips.heat(tid, [{"karat_id": k[21], "input_weight": "3", "output_weight": "2.5"}], DATE, "ws", "", "", "", key())
        pool = s.hold.hand_over_logo(tid, self.env.dual, k[21], "5", DATE, "", "", key())
        s.hold.return_logo(str(pool["pool_id"]), "2", DATE, "", "", key())
        s.trips.settle_supplier_gold(tid, self.env.supplier18, k[21], "LOGO", "1", DATE, "", "", key())
        sale = s.trips.sell_gold(tid, "CUSTODY_GOLD", k[21], "LOGO", None, "3", "TOTAL", "17000", "", "تاجر STAB", DATE, "", "", key(), True)
        s.trips.buy_gold(tid, [{"karat_id": k[21], "designation": "LOGO", "weight": "2.5", "price": "15000"}], "", "تاجر STAB", DATE, "", "", key(), True)
        buy = s.trips.buy_gold(tid, [{"karat_id": k[24], "designation": "MELT", "weight": "1.5", "price": "10000"}], "", "تاجر STAB", DATE, "", "", key(), True)   # a purchase-only line
        extra = s.trips.buy_gold(tid, [{"karat_id": k[21], "designation": "LOGO", "weight": "1", "price": "6000"}], "", "تاجر STAB", DATE, "", "", key(), True)
        s.corrections.reverse("MARKET_TRIP_ENTRY", extra["entry_id"], "STAB reversed purchase")
        melt_trip = self.trip("20", "MELT", karat=21, tag="STAB-MELT")
        melt = s.trips.melt(melt_trip["trip_id"], [{"karat_id": k[21], "weight": "20"}], DATE, "ws", {"weight": "10", "fineness": "875"},
                            [{"weight": "1", "fineness": ""}, {"weight": "2", "fineness": "750"}], "assay", "", "", key())
        lots = self.query("SELECT id, lot_kind, fineness_permille FROM market_trip_lots WHERE entry_id=%s ORDER BY id", (melt["entry_id"],))
        main = next(l for l in lots if l["lot_kind"] == "MAIN")
        unknown = next(l for l in lots if l["fineness_permille"] is None)
        s.trips.return_lots(melt_trip["trip_id"], [{"lot_id": str(main["id"])}], DATE, "", key())                  # the bar goes back to the shop ...
        other = self.trip("1", "LOGO", tag="STAB-REISSUE")
        s.trips.issue_lots(other["trip_id"], [{"lot_id": str(main["id"])}], DATE, "", key())                         # ... and is reissued into ANOTHER custody
        sold_then_reversed = s.trips.sell_gold(tid, "CUSTODY_GOLD", k[21], "LOGO", None, "0.5", "TOTAL", "3000", "", "تاجر STAB", DATE, "", "", key(), True)
        return dict(trip=int(tid), melt_trip=int(melt_trip["trip_id"]), other=int(other["trip_id"]), sale=sale["entry_id"], buy=buy["entry_id"], extra=extra["entry_id"],
                    reversed_sale=sold_then_reversed["entry_id"], main_lot=main["id"], unknown_lot=unknown["id"])

    def oracle(self, c, trip_id):
        """An INDEPENDENT derivation straight from the stored rows (not the repository's SQL): reversed entries are those with a FULL_REVERSAL correction."""
        q = c.cursor(dictionary=True)
        q.execute("SELECT source_id FROM transaction_corrections WHERE source_type='MARKET_TRIP_ENTRY' AND correction_type='FULL_REVERSAL'")
        reversed_ids = {int(r["source_id"]) for r in q.fetchall()}
        q.execute("SELECT id, entry_kind FROM market_trip_entries WHERE trip_id=%s", (trip_id,))
        entries = {int(r["id"]): r["entry_kind"] for r in q.fetchall() if int(r["id"]) not in reversed_ids}
        out = {}

        def add(karat, designation, delta):
            out[(int(karat), designation)] = out.get((int(karat), designation), D("0")) + D(str(delta))
        for table, sql in (("gold", "SELECT entry_id, karat_id, designation, weight_grams FROM market_trip_entry_gold WHERE entry_id IN ({})"),
                           ("melt", "SELECT entry_id, karat_id, weight_grams FROM market_trip_melt_inputs WHERE entry_id IN ({})"),
                           ("heat", "SELECT entry_id, karat_id, input_weight_grams, output_weight_grams FROM market_trip_heat_lines WHERE entry_id IN ({})"),
                           ("settle", "SELECT entry_id, karat_id, designation, weight_grams FROM market_trip_gold_settlements WHERE entry_id IN ({})"),
                           ("hold", "SELECT entry_id, karat_id, designation, weight_grams FROM market_trip_logo_hold_links WHERE entry_id IN ({})"),
                           ("sale", "SELECT entry_id, karat_id, designation, weight_grams FROM market_trip_gold_sales WHERE source_kind='CUSTODY_GOLD' AND entry_id IN ({})"),
                           ("buy", "SELECT entry_id, karat_id, designation, weight_grams FROM market_trip_gold_buy_lines WHERE entry_id IN ({})")):
            if not entries:
                break
            marks = ",".join(["%s"] * len(entries))
            q.execute(sql.format(marks), tuple(entries))
            for r in q.fetchall():
                kind = entries[int(r["entry_id"])]
                if table == "gold":
                    add(r["karat_id"], r["designation"], r["weight_grams"] if kind == "ISSUE" else -D(str(r["weight_grams"])))
                elif table == "melt":
                    add(r["karat_id"], "MELT", -D(str(r["weight_grams"])))
                elif table == "heat":
                    add(r["karat_id"], "LOGO", D(str(r["output_weight_grams"])) - D(str(r["input_weight_grams"])))
                elif table in ("settle", "sale"):
                    add(r["karat_id"], r["designation"], -D(str(r["weight_grams"])))
                elif table == "hold":
                    add(r["karat_id"], r["designation"], -D(str(r["weight_grams"])) if kind == "LOGO_HOLD" else D(str(r["weight_grams"])))
                elif table == "buy":
                    add(r["karat_id"], r["designation"], r["weight_grams"])
        q.close()
        return {k: v for k, v in out.items() if v != 0}

    def test_aggregate_equals_per_trip_for_every_trip_and_equals_an_independent_oracle(self):
        fx = self.build()
        with Env.tx() as c:
            repo = MarketTripRepository(c)
            by_trip, lots_by_trip = repo.custody_gold_by_trip(), repo.custody_lots_by_trip()
            checked = mismatched = 0
            for trip in repo.all_trip_ids():
                tid = int(trip["id"])
                aggregated, per_trip = gold_map(by_trip.get(tid, [])), gold_map(repo.custody_gold(tid))
                self.assertEqual(aggregated, per_trip, f"trip {tid}: aggregate != per-trip read")
                lots_a = {int(l["id"]): D(str(l["outstanding_grams"])) for l in lots_by_trip.get(tid, []) if D(str(l["outstanding_grams"])) != 0}
                lots_p = {int(l["id"]): D(str(l["outstanding_grams"])) for l in repo.custody_lots(tid) if D(str(l["outstanding_grams"])) != 0}
                self.assertEqual(lots_a, lots_p, f"trip {tid}: lots differ")
                checked += 1
            self.assertGreater(checked, 200)                                                       # every trip of the rehearsal database, not only the fixture
            for tid in (fx["trip"], fx["melt_trip"], fx["other"]):
                expected = self.oracle(c, tid)
                got = {k: v["outstanding"] for k, v in gold_map(by_trip.get(tid, [])).items() if v["outstanding"] != 0}
                self.assertEqual(got, expected, f"trip {tid}: aggregate != oracle from the stored rows")
        record("parity", {"trips_compared": checked, "fixture_trips": [fx["trip"], fx["melt_trip"], fx["other"]], "result": "identical"})

    def test_fixture_terms_and_reversals_come_from_the_stored_rows(self):
        fx = self.build()
        with Env.tx() as c:
            repo = MarketTripRepository(c)
            row = gold_map(repo.custody_gold_by_trip()[fx["trip"]])[(self.k[21], "LOGO")]
            # issued 30, returned 4, heat 2.5-3 = -0.5, handed out 5 / back 2, settled 1, sold 3 + 0.5 (the 0.5 sale is reversed below only by a later check), bought 2.5 (+1 reversed)
            self.assertEqual((row["issued"], row["returned"], row["heat_delta"], row["supplier_settled"], row["held_out"], row["held_back"], row["purchased"]),
                             (D("30"), D("4"), D("-0.5"), D("1"), D("5"), D("2"), D("2.5")))
            self.assertEqual(row["sold"], D("3.5"))
            purchase_only = gold_map(repo.custody_gold_by_trip()[fx["trip"]])[(self.k[24], "MELT")]
            self.assertEqual((purchase_only["issued"], purchase_only["purchased"], purchase_only["outstanding"]), (D("0"), D("1.5"), D("1.5")))
            stored = D(str(self.query("SELECT weight_grams FROM market_trip_gold_sales WHERE entry_id=%s", (fx["reversed_sale"],))[0]["weight_grams"]))
            before = row["outstanding"]
        self.s.corrections.reverse("MARKET_TRIP_ENTRY", fx["reversed_sale"], "STAB reverse the 0.5 g sale")
        with Env.tx() as c:
            after = gold_map(MarketTripRepository(c).custody_gold_by_trip()[fx["trip"]])[(self.k[21], "LOGO")]
            self.assertEqual(after["outstanding"] - before, stored)                              # the stored sale weight returns to custody, exactly
            self.assertEqual(after["sold"], D("3"))
            reversal = c.cursor(dictionary=True)
            reversal.execute("SELECT COUNT(*) AS n FROM transaction_corrections WHERE source_type='MARKET_TRIP_ENTRY' AND source_id=%s AND correction_type='FULL_REVERSAL'",
                             (fx["reversed_sale"],))
            self.assertEqual(reversal.fetchone()["n"], 1)
            reversal.close()
        record("parity", {"reversal_restored_exactly": str(stored)})

    def test_lots_follow_the_custody_that_holds_them_and_unknown_assay_stays_unknown(self):
        fx = self.build()
        with Env.tx() as c:
            lots = MarketTripRepository(c).custody_lots_by_trip()
        holder = {int(l["id"]): t for t, rows in lots.items() for l in rows}
        self.assertEqual(holder[fx["main_lot"]], int(fx["other"]))                                 # reissued into a custody other than its origin
        self.assertEqual(holder[fx["unknown_lot"]], int(fx["melt_trip"]))
        unknown = next(l for l in lots[int(fx["melt_trip"])] if int(l["id"]) == fx["unknown_lot"])
        self.assertIsNone(unknown["fineness_permille"])
        before = self.pos()
        position = self.pos()
        melt_trip = next(t for t in position["custody"]["trips"] if t["trip_id"] == str(fx["melt_trip"]))
        residue = next(l for l in melt_trip["lots"] if l["lot_id"] == str(fx["unknown_lot"]))
        self.assertIsNone(residue["eq21"])                                                          # never inferred
        self.assertGreaterEqual(position["summary"]["unknown_in_custody"], D("1.000"))
        self.assertEqual(position["summary"]["unknown_grams"], before["summary"]["unknown_grams"])


# ============================================================================================================================ B. marketer
class MarketerIntegrationTests(Base, unittest.TestCase):
    def test_marketer_only_gold_is_counted_once_cash_is_excluded_and_unknown_stays_outside(self):
        m, p = self.s.marketer, self.env.marketer_only
        before = self.pos()
        m.cash_handover(p, "200000", DATE, "STAB", "", key())
        mid = self.pos()
        self.assertEqual(mid["summary"]["total_grams"], before["summary"]["total_grams"])         # cash never enters a gold total
        m.purchase(p, [{"weight": "10", "price": "58000"}], "", "تاجر STAB", DATE, "", "", key(), True)
        after = self.pos()
        for field in ("total_grams", "total_eq21", "intermediary_eq21"):
            self.assertEqual(D(str(after["summary"][field])) - D(str(before["summary"][field])), D("10.000"), field)
        self.assertEqual(after["summary"]["unknown_grams"], before["summary"]["unknown_grams"])
        row = next(x for x in after["intermediary"]["parties"] if x["party_id"] == p)
        self.assertEqual((row["marketer"]["grams"], row["lots"], row["logo"]), (D("10.000"), [], []))
        with Env.tx() as c:
            self.assertEqual(MarketerCustodyRepository(c).display_gold_balance(p), D("10.000"))
            self.assertEqual(IntermediaryHoldingRepository(c).marketer_gold_balance(p), D("10.000"))
            self.assertEqual({r["party_id"]: D(str(r["balance"])) for r in MarketerCustodyRepository(c).party_gold_balances()}[int(p)], D("10.000"))
            self.assertEqual([D(str(r["balance"])) for r in MarketerCustodyRepository(c).party_gold_balances(p)], [D("10.000")])
        # a return to Scrap changes location, not the total; a sale / reversal moves the physical gold
        ret = m.gold_return(p, "4", DATE, "", "", key())
        moved = self.pos()
        self.assertEqual(moved["summary"]["total_grams"], after["summary"]["total_grams"])
        self.assertEqual(D(str(moved["shop"]["parts"]["scrap"]["grams"])) - D(str(after["shop"]["parts"]["scrap"]["grams"])), D("4.000"))
        self.s.corrections.reverse("MARKETER_DOCUMENT", ret["id"], "STAB")
        restored = self.pos()
        self.assertEqual((restored["intermediary"]["parts"]["marketer"]["grams"], restored["shop"]["parts"]["scrap"]["grams"]),
                         (after["intermediary"]["parts"]["marketer"]["grams"], after["shop"]["parts"]["scrap"]["grams"]))
        record("marketer", {"marketer_only_delta": "10.000", "location_move_total_unchanged": True})

    def test_mixed_legacy_lot_logo_and_marketer_holdings_are_counted_once_each(self):
        s, k, dual = self.s, self.k, self.env.dual
        trip = self.trip("20", "LOGO", tag="STAB-MIXED")
        melt_trip = self.trip("20", "MELT", tag="STAB-MIXED")
        melt = s.trips.melt(melt_trip["trip_id"], [{"karat_id": k[21], "weight": "20"}], DATE, "ws", {"weight": "10", "fineness": "875"}, [], "assay", "", "", key())
        lot = self.query("SELECT id FROM market_trip_lots WHERE entry_id=%s ORDER BY id", (melt["entry_id"],))[0]["id"]
        before = self.pos()
        s.hold.hand_over_logo(trip["trip_id"], dual, k[21], "5", DATE, "", "", key())
        s.hold.hand_over_lot(melt_trip["trip_id"], dual, str(lot), DATE, "", "", key())
        s.marketer.cash_handover(dual, "100000", DATE, "STAB", "", key())
        s.marketer.purchase(dual, [{"weight": "4", "price": "23000"}], "", "تاجر STAB", DATE, "", "", key(), True)
        after = self.pos()
        row = next(x for x in after["intermediary"]["parties"] if x["party_id"] == dual)
        old_row = next((x for x in before["intermediary"]["parties"] if x["party_id"] == dual), {"logo": [], "lots": [], "marketer": None})     # earlier fixtures may already hold some
        self.assertEqual((len(row["logo"]) - len(old_row["logo"]), len(row["lots"]) - len(old_row["lots"]), row["marketer"]["grams"]), (1, 1, D("4.000")))
        self.assertEqual(len({p["pool_id"] for p in row["logo"]}), len(row["logo"]))                          # no pool listed twice
        d = lambda path: D(str(path(after))) - D(str(path(before)))
        self.assertEqual(d(lambda p: p["intermediary"]["parts"]["holdings"]["grams"]), D("15.000"))       # 5 LOGO + 10 LOT, each once
        self.assertEqual(d(lambda p: p["intermediary"]["parts"]["marketer"]["grams"]), D("4.000"))
        self.assertEqual(d(lambda p: p["intermediary"]["grams"]), D("19.000"))
        self.assertEqual(d(lambda p: p["summary"]["total_grams"]), D("4.000"))                           # the legacy 15 g only moved from custody to the holder
        self.assertEqual(after["summary"]["unknown_grams"], before["summary"]["unknown_grams"])
        # the supplier account of the dual party is not part of any gold total
        self.assertNotIn("payable", repr(after).lower())
        record("marketer", {"mixed_party_each_source_once": True, "holdings_delta": "15.000", "marketer_delta": "4.000"})


# ============================================================================================================================ 4. snapshot
class PausingTripRepository(MarketTripRepository):
    """The REAL repository; after its real aggregate read has executed it waits (bounded) before returning, so the reader's snapshot is established and the
    remaining location sources have not been read yet. No SQL result is replaced."""
    pause_after = None
    paused = None
    resume = None

    def _pause(self, name):
        if PausingTripRepository.pause_after == name:
            PausingTripRepository.paused.set()
            assert PausingTripRepository.resume.wait(60), "the writer never finished"

    def custody_gold_by_trip(self):
        result = super().custody_gold_by_trip()
        self._pause("custody_gold_by_trip")
        return result

    def settle_bar_by_trip(self):
        return {}                                                           # 060: no melted bar in these fixtures

    def custody_lots_by_trip(self):
        result = super().custody_lots_by_trip()
        self._pause("custody_lots_by_trip")
        return result


class SnapshotConsistencyTests(Base, unittest.TestCase):
    def reader(self, pause_after):
        PausingTripRepository.pause_after, PausingTripRepository.paused, PausingTripRepository.resume = pause_after, threading.Event(), threading.Event()
        service = GoldPositionService(self.env.other, transaction_factory=Env.read_tx, trip_repository=PausingTripRepository)
        box = {}

        def run():
            try:
                box["result"] = service.position()
            except BaseException as exc:                                                       # a timeout / deadlock is a RESULT, kept for the assertion below
                box["error"] = (type(exc).__name__, getattr(exc, "errno", None), str(exc)[:160])
        thread = threading.Thread(target=run, name="reader-A")
        thread.start()
        return thread, box

    def scenario(self, pause_after, transfer, label, part_a, part_b):
        control_before = self.pos()
        thread, box = self.reader(pause_after)
        self.assertTrue(PausingTripRepository.paused.wait(60), "reader A never reached its pause")
        started = time.time()
        transfer()                                                                              # writer B commits while A holds its snapshot
        writer_seconds = round(time.time() - started, 3)
        PausingTripRepository.resume.set()
        thread.join(60)
        self.assertFalse(thread.is_alive(), "reader A did not finish")
        PausingTripRepository.pause_after = None
        self.assertNotIn("error", box, f"reader A failed: {box.get('error')}")
        a, fresh = box["result"], self.pos()
        total = lambda p: D(str(p["summary"]["total_grams"]))
        self.assertEqual(part_a(a), part_a(control_before), "A must see the PRE-transfer breakdown")
        self.assertNotEqual(part_a(fresh), part_a(a), "the fresh read must see the transfer")
        self.assertEqual(total(a), total(control_before))
        self.assertEqual(total(fresh), total(a), "the transferred grams were omitted or counted twice")
        record("snapshot", {"case": label, "writer_commit_seconds_while_A_open": writer_seconds, "A_total": str(total(a)), "fresh_total": str(total(fresh)),
                            "A_breakdown": str(part_a(a)), "fresh_breakdown": str(part_a(fresh)), "B_blocked_by_A": writer_seconds > 10})

    def test_custody_gold_returned_to_scrap_during_a_read(self):
        trip = self.trip("8", "LOGO", tag="STAB-SNAPSHOT")
        breakdown = lambda p: (D(str(p["custody"]["grams"])), D(str(p["shop"]["parts"]["scrap"]["grams"])))
        self.scenario("custody_gold_by_trip",
                      lambda: self.s.trips.return_custody(trip["trip_id"], "", [{"karat_id": self.k[21], "weight": "2", "designation": "LOGO"}], DATE, "", key()),
                      "custody gold -> Scrap (paused after the custody gold aggregate, before Scrap)", breakdown, breakdown)

    def test_lot_returned_to_the_shop_during_a_read(self):
        trip = self.trip("20", "MELT", tag="STAB-SNAPSHOT-LOT")
        melt = self.s.trips.melt(trip["trip_id"], [{"karat_id": self.k[21], "weight": "20"}], DATE, "ws", {"weight": "10", "fineness": "875"}, [], "assay", "", "", key())
        lot = self.query("SELECT id FROM market_trip_lots WHERE entry_id=%s ORDER BY id", (melt["entry_id"],))[0]["id"]
        breakdown = lambda p: (D(str(p["custody"]["grams"])), D(str(p["shop"]["parts"]["lots"]["grams"])))
        self.scenario("custody_lots_by_trip",
                      lambda: self.s.trips.return_lots(trip["trip_id"], [{"lot_id": str(lot)}], DATE, "", key()),
                      "LOT custody -> shop lots (paused after the custody lot aggregate, before the shop lots)", breakdown, breakdown)


# ============================================================================================================================ 5. request keys
class RequestKeySequentialTests(Base, unittest.TestCase):
    TABLES = ("marketer_documents", "marketer_cash_movements", "market_trips", "market_trip_entries", "supplier_gold_settlements", "supplier_gold_movements",
              "supplier_making_payments", "supplier_making_movements", "cash_movements", "old_gold_movements", "logo_holding_documents", "intermediary_holding_pools")

    def refused(self, call):
        before = self.counts(*self.TABLES)
        with self.assertRaises(ValueError) as raised:
            call()
        self.assertEqual(getattr(raised.exception, "code", None), "REQUEST_CONFLICT", repr(raised.exception))
        self.assertEqual(self.counts(*self.TABLES), before, "the refused call left a persistent side effect")
        return type(raised.exception).__name__

    def test_a_marketer_document_key_is_refused_by_the_older_flows(self):
        s, k, p = self.s, self.k, self.env.marketer_only
        kk = key()
        s.marketer.cash_handover(p, "5000", DATE, "STAB", "", kk)
        grams = self.trip("2", "LOGO", tag="STAB-KEY")
        names = {
            "custody entry": self.refused(lambda: s.trips.issue("", "", [{"karat_id": k[21], "weight": "1", "designation": "LOGO"}], DATE, "STAB", kk)),
            "direct supplier gold settlement": self.refused(lambda: s.direct.settle(self.env.supplier18, k[21], "1", DATE, "", "", kk)),
            "supplier making payment": self.refused(lambda: s.pay.pay(self.env.supplier18, "CASH", None, "10", DATE, "", "", kk)),
            "legacy LOGO holding document": self.refused(lambda: s.hold.hand_over_logo(grams["trip_id"], self.env.dual, k[21], "1", DATE, "", "", kk)),
        }
        record("keys_sequential", {"marketer_key_refused_by": names})

    def test_a_legacy_flow_key_is_refused_by_the_marketer_account(self):
        s, k, p = self.s, self.k, self.env.marketer_only
        issued = key()
        trip = s.trips.issue("", "", [{"karat_id": k[21], "weight": "3", "designation": "LOGO"}], DATE, "STAB-KEY", issued)
        sg_key, sm_key, hold_key = key(), key(), key()
        s.direct.settle(self.env.supplier18, k[21], "1", DATE, "", "", sg_key)
        s.pay.pay(self.env.supplier18, "CASH", None, "10", DATE, "", "", sm_key)
        s.hold.hand_over_logo(trip["trip_id"], self.env.marketer_two, k[21], "1", DATE, "", "", hold_key)
        names = {name: self.refused(lambda kk=kk: s.marketer.cash_handover(p, "1000", DATE, "STAB", "", kk))
                 for name, kk in (("custody entry", issued), ("direct supplier gold settlement", sg_key), ("supplier making payment", sm_key), ("LOGO holding document", hold_key))}
        record("keys_sequential", {"legacy_key_refused_by_marketer": names})

    def test_exact_replay_and_intentionally_linked_documents_remain_valid(self):
        s, k, p = self.s, self.k, self.env.marketer_only
        kk = key()
        first = s.marketer.cash_handover(p, "1500", DATE, "STAB", "", kk)
        before = self.counts(*self.TABLES)
        again = s.marketer.cash_handover(p, "1500", DATE, "STAB", "", kk)
        self.assertEqual((again["id"], again["duplicate"]), (first["id"], True))
        self.assertEqual(self.counts(*self.TABLES), before)                                       # exact replay writes nothing
        s.corrections.reverse("MARKETER_DOCUMENT", first["id"], "STAB reverse then replay")
        before = self.counts(*self.TABLES)
        self.assertEqual(s.marketer.cash_handover(p, "1500", DATE, "STAB", "", kk)["id"], first["id"])      # replay after the reversal still returns the original
        self.assertEqual(self.counts(*self.TABLES), before)
        # a custody entry and its SG header deliberately share ONE key: the guard only looks at marketer documents, so this stays valid and replays exactly
        trip = self.trip("5", "LOGO", tag="STAB-LINKED")
        linked = key()
        done = s.trips.settle_supplier_gold(trip["trip_id"], self.env.supplier18, k[21], "LOGO", "1", DATE, "", "", linked)
        rows = (self.query("SELECT COUNT(*) AS n FROM market_trip_entries WHERE request_key=%s", (linked,))[0]["n"],
                self.query("SELECT COUNT(*) AS n FROM supplier_gold_settlements WHERE request_key=%s", (linked,))[0]["n"],
                self.query("SELECT COUNT(*) AS n FROM marketer_documents WHERE request_key=%s", (linked,))[0]["n"])
        self.assertEqual(rows, (1, 1, 0))
        before = self.counts(*self.TABLES)
        self.assertTrue(s.trips.settle_supplier_gold(trip["trip_id"], self.env.supplier18, k[21], "LOGO", "1", DATE, "", "", linked)["duplicate"])
        self.assertEqual(self.counts(*self.TABLES), before)
        record("keys_sequential", {"replay_zero_writes": True, "replay_after_reversal": True, "linked_entry_and_sg_share_key": True})


# ============================================================================================================================ 5. concurrent
class BarrierMixin:
    barrier = None
    waits = []                                                                                  # per round: "met" (both sides reached the point) / "timeout" (the other side never arrived)

    def _wait(self):
        try:
            BarrierMixin.barrier.wait()
            BarrierMixin.waits.append("met")
        except threading.BrokenBarrierError:
            BarrierMixin.waits.append("timeout")                                                # the other side never arrived (serialized by a lock): proceed


class BarrierMarketerRepo(BarrierMixin, MarketerCustodyRepository):
    def insert_document(self, **kw):
        self._wait()                                                                            # every lock and the cross-flow check are already behind us
        return super().insert_document(**kw)


class BarrierTripRepo(BarrierMixin, MarketTripRepository):
    def insert_entry(self, **kw):
        self._wait()                                                                            # (the request-key guard runs INSIDE the real insert_entry, after this)
        return super().insert_entry(**kw)


class BarrierSgRepo(BarrierMixin, SupplierGoldSettlementRepository):
    def insert_header(self, **kw):
        self._wait()
        return super().insert_header(**kw)


class RequestKeyConcurrentTests(Base, unittest.TestCase):
    """One key raced between two flows on two independent connections / users. Locks the harness retains: PRODUCTION authorization X-locks the user row and the
    shared role / permission rows until commit (writers serialize there before any resource), so the production variant mostly measures the post-wait decision; the
    UNLOCKED variant (the existing test-only authorization used by the other rehearsal suites, identical verification but no authorization row locks) lets the two
    transactions overlap. Both keep the real request-key guard. Resource locks kept: marketer handover = party + MAIN_SAFE; custody issue = new trip + Scrap(21K);
    direct settlement = supplier + Scrap(21K) (so the issue/settlement pair share the Scrap row when they overlap).

    Every round must end with EXACTLY ONE successful operation and EXACTLY ONE committed operation, the winner's exact accounting deltas, no movement of the loser, and a
    BUSINESS refusal (REQUEST_CONFLICT) for the loser: a raw database error, a second commit or a retry-error without a committed owner all FAIL the round."""
    ROUNDS = 4
    COUNT_TABLES = ("marketer_documents", "marketer_cash_movements", "marketer_shop_links", "marketer_gold_movements", "cash_movements", "old_gold_movements",
                    "market_trips", "market_trip_entries", "market_trip_entry_gold", "market_trip_entry_cash", "supplier_gold_settlements", "supplier_gold_settlement_legs",
                    "supplier_gold_movements", "supplier_making_payments", "intermediary_holding_documents", "logo_holding_documents", "transaction_corrections")
    # the rows each winner must add (everything else must stay at zero)
    EXPECTED_ROWS = {"marketer": {"marketer_documents": 1, "marketer_cash_movements": 1, "marketer_shop_links": 1, "cash_movements": 1},
                     "issue": {"market_trips": 1, "market_trip_entries": 1, "market_trip_entry_gold": 1, "old_gold_movements": 1},
                     "settlement": {"supplier_gold_settlements": 1, "supplier_gold_settlement_legs": 2, "old_gold_movements": 1, "supplier_gold_movements": 1}}

    def build_pair(self, variant):
        auth_a = UnlockedAuthorization(self.env.main) if variant == "unlocked" else None
        auth_b = UnlockedAuthorization(self.env.other) if variant == "unlocked" else None
        a = Env.services(self.env.main, auth_a, marketer_repo=BarrierMarketerRepo)
        b = Env.services(self.env.other, auth_b, trip_repo=BarrierTripRepo, sg_repo=BarrierSgRepo)
        return a, b

    def snapshot(self):
        k = self.k
        return {"rows": self.counts(*self.COUNT_TABLES),
                "marketer_cash": D(str(self.query("SELECT COALESCE(SUM(amount_delta),0) AS b FROM marketer_cash_movements WHERE party_id=%s", (self.env.marketer_only,))[0]["b"])),
                "main_safe": D(str(self.query("SELECT COALESCE(SUM(amount_delta),0) AS b FROM cash_movements WHERE cash_account_id=(SELECT id FROM cash_accounts WHERE code='MAIN_SAFE')")[0]["b"])),
                "scrap21": D(str(self.query("SELECT COALESCE(SUM(weight_delta_grams),0) AS b FROM old_gold_movements WHERE karat_id=%s", (k[21],))[0]["b"]))}

    def under_key(self, kk):
        return {"marketer_documents": self.query("SELECT COUNT(*) AS n FROM marketer_documents WHERE request_key=%s", (kk,))[0]["n"],
                "custody_entries": self.query("SELECT COUNT(*) AS n FROM market_trip_entries WHERE request_key=%s", (kk,))[0]["n"],
                "supplier_gold_settlements": self.query("SELECT COUNT(*) AS n FROM supplier_gold_settlements WHERE request_key=%s", (kk,))[0]["n"],
                "stab_race_trips": self.query("SELECT COUNT(*) AS n FROM market_trips WHERE notes LIKE %s", (f"STABRACE {kk}%",))[0]["n"]}

    def race(self, variant, flow):
        k = self.k
        legacy_name = "issue" if flow == "handover-vs-issue" else "settlement"
        for round_no in range(self.ROUNDS):
            a, b = self.build_pair(variant)
            kk = key()
            BarrierMixin.barrier = threading.Barrier(2, timeout=6 if variant == "production" else 20)
            BarrierMixin.waits = []
            holder, before = {}, self.snapshot()
            captured = []
            handler = type("H", (logging.Handler,), {"emit": lambda self_, rec: captured.append(rec.getMessage())})()
            marketer_log = logging.getLogger("services.marketer_custody_service")
            marketer_log.addHandler(handler)

            def side(label, call):
                try:
                    holder[label] = ("ok", call())
                except BaseException as exc:
                    holder[label] = ("error", type(exc).__name__, getattr(exc, "code", None), getattr(exc, "errno", None), str(exc)[:140])
            calls = {"marketer": lambda: a.marketer.cash_handover(self.env.marketer_only, "1", DATE, f"STABRACE {kk}", "", kk)}
            if legacy_name == "issue":
                calls["legacy"] = lambda: b.trips.issue("", "", [{"karat_id": k[21], "weight": "0.100", "designation": "LOGO"}], DATE, f"STABRACE {kk}", kk)
            else:
                calls["legacy"] = lambda: b.direct.settle(self.env.supplier21, k[21], "0.100", DATE, f"STABRACE {kk}", "", kk)
            threads = [threading.Thread(target=side, args=(name, fn), name=f"race-{name}") for name, fn in calls.items()]
            started = time.time()
            try:
                for t in threads:
                    t.start()
                for t in threads:
                    t.join(90)
            finally:
                marketer_log.removeHandler(handler)
                BarrierMixin.barrier.abort()                                # release anything still waiting at the synchronization point (also when an assertion below fails)
                for t in threads:
                    if t.is_alive():
                        t.join(30)
            self.assertEqual([t.name for t in threads if t.is_alive()], [], "a racing worker did not finish (a timeout is a result to investigate)")
            after, eff = self.snapshot(), self.under_key(kk)
            delta_rows = {t: after["rows"][t] - before["rows"][t] for t in self.COUNT_TABLES if after["rows"][t] != before["rows"][t]}
            oks = [n for n, v in holder.items() if v[0] == "ok"]
            winner = None if len(oks) != 1 else ("marketer" if oks[0] == "marketer" else legacy_name)
            deltas = {"marketer_cash": str(after["marketer_cash"] - before["marketer_cash"]), "main_safe": str(after["main_safe"] - before["main_safe"]),
                      "scrap21": str(after["scrap21"] - before["scrap21"])}
            row = {"variant": variant, "flow": flow, "round": round_no + 1, "seconds": round(time.time() - started, 2), "winner": winner, "outcomes": holder,
                   "rows_under_key": eff, "row_deltas": delta_rows, "accounting_deltas": deltas, "synchronization": list(BarrierMixin.waits),
                   "deadlock_recoveries_logged": [m[:120] for m in captured if "deadlock victim" in m]}
            record("keys_concurrent", row)
            # --- exactly one success, exactly one commit
            self.assertEqual(len(oks), 1, f"expected exactly ONE successful operation: {row}")
            committed = eff["marketer_documents"] + eff["custody_entries"] + eff["supplier_gold_settlements"]
            self.assertEqual(committed, 1, f"expected exactly ONE committed operation under the key: {row}")
            # --- the loser: a business refusal, never a raw database error, never a retry error without a committed owner
            (loser,) = [n for n in holder if n != oks[0]]
            self.assertEqual(holder[loser][0], "error", row)
            self.assertEqual(holder[loser][2], "REQUEST_CONFLICT", f"the loser must get REQUEST_CONFLICT (got {holder[loser][1:4]}): {row}")
            self.assertNotEqual(holder[loser][1], "InternalError", row)
            # --- winner-specific effects, and nothing of the loser (no orphan movement anywhere)
            self.assertEqual(delta_rows, self.EXPECTED_ROWS[winner], f"row deltas differ from what the winner alone may write: {row}")
            if winner == "marketer":
                self.assertEqual((after["marketer_cash"] - before["marketer_cash"], after["main_safe"] - before["main_safe"], after["scrap21"] - before["scrap21"]),
                                 (D("1.00"), D("-1.00"), D("0")), row)
                self.assertEqual((eff["custody_entries"], eff["stab_race_trips"], eff["supplier_gold_settlements"]), (0, 0, 0), row)
            elif winner == "issue":
                self.assertEqual((after["marketer_cash"] - before["marketer_cash"], after["main_safe"] - before["main_safe"], after["scrap21"] - before["scrap21"]),
                                 (D("0"), D("0"), D("-0.100")), row)
                self.assertEqual((eff["custody_entries"], eff["stab_race_trips"], eff["marketer_documents"]), (1, 1, 0), row)
            else:
                self.assertEqual((after["marketer_cash"] - before["marketer_cash"], after["main_safe"] - before["main_safe"], after["scrap21"] - before["scrap21"]),
                                 (D("0"), D("0"), D("-0.100")), row)
                self.assertEqual((eff["supplier_gold_settlements"], eff["marketer_documents"], eff["custody_entries"], eff["stab_race_trips"]), (1, 0, 0, 0), row)
                sg = self.query("SELECT id, supplier_id, source_kind, delivered_weight_grams, credited_weight_grams, target_karat_value FROM supplier_gold_settlements WHERE request_key=%s", (kk,))[0]
                self.assertEqual((str(sg["supplier_id"]), sg["source_kind"], sg["delivered_weight_grams"], sg["credited_weight_grams"], sg["target_karat_value"]),
                                 (self.env.supplier21, "SHOP_SCRAP", D("0.100"), D("0.100"), 21), row)         # a 21K delivery to a 21K supplier credits the same grams
                legs = self.query("SELECT l.leg_kind, og.weight_delta_grams AS scrap, sg.weight_delta_grams AS gold, sg.supplier_id FROM supplier_gold_settlement_legs l "
                                  "LEFT JOIN old_gold_movements og ON og.id=l.old_gold_movement_id LEFT JOIN supplier_gold_movements sg ON sg.id=l.supplier_gold_movement_id "
                                  "WHERE l.settlement_id=%s ORDER BY l.leg_kind", (sg["id"],))
                self.assertEqual([(r["leg_kind"], r["scrap"], r["gold"]) for r in legs], [("GOLD", None, D("-0.100")), ("SCRAP", D("-0.100"), None)], row)
                self.assertEqual(str(legs[0]["supplier_id"]), self.env.supplier21)

    def test_handover_vs_custody_issue_production_authorization(self):
        self.race("production", "handover-vs-issue")

    def test_handover_vs_custody_issue_overlapping_transactions(self):
        self.race("unlocked", "handover-vs-issue")

    def test_handover_vs_direct_gold_settlement_production_authorization(self):
        self.race("production", "handover-vs-settlement")

    def test_handover_vs_direct_gold_settlement_overlapping_transactions(self):
        self.race("unlocked", "handover-vs-settlement")


# ============================================================================================================================ 6. performance
class CountingConnection:
    def __init__(self, connection):
        self._c, self.statements = connection, []

    def cursor(self, *a, **kw):
        outer = self
        inner = self._c.cursor(*a, **kw)

        class Cur:
            def execute(self, sql, params=()):
                outer.statements.append(" ".join(str(sql).split()))
                return inner.execute(sql, params)

            def __getattr__(self, name):
                return getattr(inner, name)
        return Cur()

    def __getattr__(self, name):
        return getattr(self._c, name)


@unittest.skipUnless(PERF, "explicit performance opt-in (MASGOLD_STABILIZATION_PERF=1)")
class PositionPerformanceTests(Base, unittest.TestCase):
    TRIPS = int(os.environ.get("MASGOLD_STAB_PERF_TRIPS", "3000"))
    OUTSTANDING_EVERY = 30
    HISTORY_NEWEST = 150

    def test_several_thousand_trips(self):
        s, k = self.s, self.k
        tag = "STAB-PERF-" + uuid4().hex[:6]
        ids, started = [], time.time()
        for i in range(self.TRIPS):
            t = s.trips.issue("", "", [{"karat_id": k[21], "weight": "1", "designation": "LOGO"}], DATE, f"{tag} #{i}", key())
            ids.append(int(t["trip_id"]))
            newest = i >= self.TRIPS - self.HISTORY_NEWEST
            if newest:                                                                           # movement history: heat, a sale, then the rest returns
                s.trips.heat(t["trip_id"], [{"karat_id": k[21], "input_weight": "1", "output_weight": "0.9"}], DATE, "ws", "", "", "", key())
                s.trips.sell_gold(t["trip_id"], "CUSTODY_GOLD", k[21], "LOGO", None, "0.2", "TOTAL", "1200", "", "تاجر STAB", DATE, "", "", key(), True)
            keeps = (i % self.OUTSTANDING_EVERY == 0)                                            # trip #0 (the OLDEST) and every 30th keep their gold
            if not keeps:
                rest = "0.7" if newest else "1"
                s.trips.return_custody(t["trip_id"], "", [{"karat_id": k[21], "weight": rest, "designation": "LOGO"}], DATE, "", key())
        build_seconds = round(time.time() - started, 1)
        outstanding = [ids[i] for i in range(0, self.TRIPS, self.OUTSTANDING_EVERY)]
        with Env.tx(readonly=True) as c:
            total_trips = MarketTripRepository(c).all_trip_ids()
        entries = self.query("SELECT COUNT(*) AS n FROM market_trip_entries")[0]["n"]
        fixture = {"tag": tag, "fixture_trips": self.TRIPS, "outstanding_trips": len(outstanding), "newest_with_history": self.HISTORY_NEWEST, "oldest_trip_id": ids[0],
                   "trips_in_database": len(total_trips), "entries_in_database": entries, "build_seconds": build_seconds}
        aggregate = []
        for attempt in range(4):                                                                 # 1 initial + 3 repeats
            with Env.tx(readonly=True) as c:
                counting = CountingConnection(c)
                repo = MarketTripRepository(counting)
                t0 = time.perf_counter()
                gold, lots = repo.custody_gold_by_trip(), repo.custody_lots_by_trip()
                elapsed = time.perf_counter() - t0
            aggregate.append({"attempt": attempt + 1, "statements": len(counting.statements), "seconds": round(elapsed, 3), "trips_with_gold_rows": len(gold)})
        full = []
        for attempt in range(4):
            t0 = time.perf_counter()
            position = self.pos()
            full.append({"attempt": attempt + 1, "seconds": round(time.perf_counter() - t0, 3), "custody_trips_listed": len(position["custody"]["trips"])})
        listed = {int(t["trip_id"]) for t in position["custody"]["trips"]}
        self.assertIn(ids[0], listed, "the OLDEST trip with outstanding gold is missing from the position")
        self.assertTrue(set(outstanding) <= listed)
        self.assertTrue(all(a["statements"] == 8 for a in aggregate))
        record("performance", {"fixture": fixture, "custody_aggregate": aggregate, "position": full})


if __name__ == "__main__":
    unittest.main()
