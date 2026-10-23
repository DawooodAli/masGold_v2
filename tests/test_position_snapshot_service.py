"""Position snapshot service: owner-only rights, the initial balance once, one DAILY sheet per day (fake repository, no database)."""
from contextlib import contextmanager
from datetime import datetime
from decimal import Decimal as D
import unittest

from services.position_snapshot_service import PositionSnapshotService, SnapshotError
from tests.auth_fakes import FakeAuthorization
from tests.market_trip_fakes import session_for

OWNER = frozenset({"APP_LOGIN", "SETTINGS_MANAGE", "MANAGEMENT_REVIEW", "MARKET_TRIP_MANAGE", "INTERMEDIARY_HOLDING_VIEW", "CASH_ADJUST", "BANK_MANAGE"})
SUMMARY = {"shop_eq21": D("1000.000"), "custody_eq21": D("50.000"), "intermediary_eq21": D("20.000"), "book_eq21": D("0.000"), "unknown_grams": D("0.000")}


class Store:
    now = datetime(2026, 10, 22, 9, 0, 0)

    def __init__(self):
        self.rows, self.audits, self.scrap, self.money = [], [], [], []
        self.accounts = [{"id": 1, "name": "الخزنة الرئيسية", "ledger": "CASH", "account_type": "CASH", "movements": 0}, {"id": 5, "name": "بنك مصر", "ledger": "BANK", "account_type": "BANK", "movements": 0},
                         {"id": 6, "name": "فودافون كاش", "ledger": "BANK", "account_type": "MOBILE_WALLET", "movements": 0}]


class FakeRepo:
    store = None

    def __init__(self, connection=None):
        pass

    def now(self):
        return self.store.now

    def opening(self):
        return next((r for r in self.store.rows if r["snapshot_kind"] == "OPENING"), None)

    def latest_on_date(self, day):
        rows = [r for r in self.store.rows if r["snapshot_kind"] != "OPENING" and r["taken_at"].date() == day]
        return rows[-1] if rows else None

    def supplier_gold(self):
        return [{"karat_value": 21, "grams": D("30.000")}]

    def ledgers_posted(self):
        return any(a["event_type"] == "POSITION_OPENING_LEDGERS" for a in self.store.audits)

    def accounts_for_opening(self, lock=False):
        return [dict(a) for a in self.store.accounts]

    def add_money_opening(self, *, ledger, account_id, amount, user_id, now):
        self.store.money.append((ledger, account_id, amount))

    def add_scrap_opening(self, *, karat_value, grams, user_id, now):
        self.store.scrap.append((karat_value, grams))

    def opening_totals(self):
        return ([{"karat_value": k, "grams": g} for k, g in self.store.scrap], sum((m[2] for m in self.store.money if m[0] == "CASH"), D(0)), sum((m[2] for m in self.store.money if m[0] == "BANK"), D(0)))

    def cash_and_bank(self):
        return D("1000.00"), D("500.00")

    def insert(self, values):
        row = {**values, "id": len(self.store.rows) + 1}
        self.store.rows.append(row)
        return row["id"]

    def audit(self, **kwargs):
        self.store.audits.append(kwargs)


class FakePosition:
    calls = 0

    def position(self, *, _expected_actor=None):
        FakePosition.calls += 1
        return {"summary": SUMMARY}


class ServiceTests(unittest.TestCase):
    def build(self, permissions=OWNER):
        self.store = Store()
        FakeRepo.store = self.store
        FakePosition.calls = 0
        self.session = session_for(permissions)

        @contextmanager
        def transaction():
            yield object()
        return PositionSnapshotService(self.session, transaction_factory=transaction, repository=FakeRepo, authorization=FakeAuthorization(self.session), position_service=FakePosition())

    def post_ledgers(self, service, scrap=None, cash="200000", banks=None):
        return service.post_opening_ledgers(scrap or {"21": "100", "18": "50", "24": "10"}, cash, banks or {"5": "30000", "6": "1500"})

    def test_stage_one_posts_the_scrap_the_cash_and_each_account_once_in_the_real_ledgers(self):
        service = self.build()
        self.post_ledgers(service)
        self.assertEqual(sorted(self.store.scrap), [(18, D("50.000")), (21, D("100.000")), (24, D("10.000"))])
        self.assertEqual(self.store.money, [("CASH", 1, D("200000.00")), ("BANK", 5, D("30000.00")), ("BANK", 6, D("1500.00"))])
        self.assertEqual([a["event_type"] for a in self.store.audits], ["POSITION_OPENING_LEDGERS"])
        with self.assertRaises(SnapshotError) as raised:
            self.post_ledgers(service)
        self.assertEqual(raised.exception.code, "ALREADY_INITIALIZED")
        self.assertEqual(len(self.store.scrap), 3)

    def test_stage_one_refuses_everything_when_an_account_already_has_movements(self):
        service = self.build()
        self.store.accounts[1]["movements"] = 2
        with self.assertRaises(SnapshotError) as raised:
            self.post_ledgers(service)
        self.assertEqual(raised.exception.code, "ACCOUNT_USED")
        self.assertEqual((self.store.audits, self.store.money[:0]), ([], []))                            # the marker is not written (the fake keeps the writes made before the refusal; the real transaction rolls them back)

    def test_stage_two_records_the_sheet_from_the_typed_new_gold_plus_the_scrap_and_closes_the_card(self):
        service = self.build()
        with self.assertRaises(SnapshotError) as raised:
            service.set_opening({"21": "5000"}, "0")
        self.assertEqual(raised.exception.code, "LEDGERS_FIRST")
        self.post_ledgers(service)
        result = service.set_opening({"21": "5000", "18": "210", "24": "21"}, "-120.5")
        row = self.store.rows[0]
        # new gold: 5000 + 210 x 18/21 + 21 x 24/21 = 5000 + 180 + 24 = 5204; scrap: 100 + 50 x 18/21 + 10 x 24/21 = 100 + 42.857 + 11.429 = 154.286
        self.assertEqual((result["gold_new_eq21"], result["scrap_eq21"]), (D("5204.000"), D("154.286")))
        self.assertEqual((row["snapshot_kind"], row["gold_shop_eq21"], row["gold_suppliers_eq21"], row["gold_total_eq21"]), ("OPENING", D("5358.286"), D("-120.500"), D("5237.786")))
        self.assertEqual((row["cash_egp"], row["bank_egp"]), (D("200000.00"), D("31500.00")))
        self.assertEqual(self.store.audits[-1]["event_type"], "POSITION_OPENING_BALANCE")
        with self.assertRaises(SnapshotError) as raised:
            service.set_opening({"21": "1"}, "0")
        self.assertEqual(raised.exception.code, "ALREADY_INITIALIZED")
        state = service.opening_state()
        self.assertEqual((state["ledgers_posted"], state["opening"]["gold_shop_eq21"], [a["name"] for a in state["accounts"]]), (True, D("5358.286"), ["الخزنة الرئيسية", "بنك مصر", "فودافون كاش"]))

    def test_the_initial_balance_needs_the_owner_and_account_rights(self):
        for missing in ("SETTINGS_MANAGE", "MANAGEMENT_REVIEW", "CASH_ADJUST", "BANK_MANAGE"):
            service = self.build(OWNER - {missing})
            with self.assertRaises(PermissionError):
                self.post_ledgers(service)
            self.assertEqual((self.store.rows, self.store.scrap, self.store.money), ([], [], []))

    def test_invalid_numbers_are_refused_before_any_write(self):
        service = self.build()
        for scrap, cash, banks in (({"21": "abc"}, "0", {}), ({"21": "-1"}, "0", {}), ({"21": "1.2345"}, "0", {}), ({}, "x", {}), ({}, "10.123", {}), ({}, "0", {"5": "-3"})):
            with self.assertRaises(SnapshotError):
                service.post_opening_ledgers(scrap, cash, banks)
        self.assertEqual((self.store.scrap, self.store.money, self.store.audits), ([], [], []))

    def test_a_daily_sheet_waits_for_the_initial_balance_and_is_taken_once_a_day(self):
        service = self.build()
        self.assertEqual(service.ensure_daily(), {"taken": False, "reason": "NO_OPENING"})
        self.post_ledgers(service)
        service.set_opening({"21": "5000"}, "0")
        first = service.ensure_daily()
        self.assertTrue(first["taken"])
        self.assertEqual(service.ensure_daily(), {"taken": False, "reason": "ALREADY_TAKEN"})
        self.assertEqual([r["snapshot_kind"] for r in self.store.rows], ["OPENING", "DAILY"])
        self.assertEqual(FakePosition.calls, 1)
        row = self.store.rows[1]
        self.assertEqual((row["gold_shop_eq21"], row["gold_suppliers_eq21"], row["gold_total_eq21"], row["cash_egp"], row["bank_egp"]),
                         (D("1000.000"), D("-30.000"), D("1040.000"), D("1000.00"), D("500.00")))        # 1000 + 50 + 20 - 30 supplier
        self.store.now = datetime(2026, 10, 23, 8, 0, 0)
        self.assertTrue(service.ensure_daily()["taken"])                                                 # the next day gets its own sheet

    def test_a_user_without_the_owner_rights_takes_nothing_and_is_not_bothered(self):
        service = self.build(frozenset({"APP_LOGIN", "REPORTS_VIEW"}))
        self.assertEqual(service.ensure_daily(), {"taken": False, "reason": "NOT_AUTHORISED"})
        with self.assertRaises(PermissionError):
            service.take("MANUAL")
        self.assertEqual(FakePosition.calls, 0)

    def test_a_manual_sheet_is_audited_and_refused_without_the_initial_balance(self):
        service = self.build()
        with self.assertRaises(SnapshotError) as raised:
            service.take("MANUAL")
        self.assertEqual(raised.exception.code, "NO_OPENING")
        self.post_ledgers(service)
        service.set_opening({"21": "5000"}, "0")
        service.take("MANUAL")
        self.assertEqual([a["event_type"] for a in self.store.audits], ["POSITION_OPENING_LEDGERS", "POSITION_OPENING_BALANCE", "POSITION_SNAPSHOT"])


if __name__ == "__main__":
    unittest.main()
