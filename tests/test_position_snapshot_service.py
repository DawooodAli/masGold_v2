"""Position snapshot service: owner-only rights, the initial balance once, one DAILY sheet per day (fake repository, no database)."""
from contextlib import contextmanager
from datetime import datetime
from decimal import Decimal as D
import unittest

from services.position_snapshot_service import PositionSnapshotService, SnapshotError
from tests.auth_fakes import FakeAuthorization
from tests.market_trip_fakes import session_for

OWNER = frozenset({"APP_LOGIN", "SETTINGS_MANAGE", "MANAGEMENT_REVIEW", "MARKET_TRIP_MANAGE", "INTERMEDIARY_HOLDING_VIEW"})
SUMMARY = {"shop_eq21": D("1000.000"), "custody_eq21": D("50.000"), "intermediary_eq21": D("20.000"), "book_eq21": D("0.000"), "unknown_grams": D("0.000")}


class Store:
    now = datetime(2026, 10, 22, 9, 0, 0)

    def __init__(self):
        self.rows, self.audits = [], []


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

    def test_the_initial_balance_is_entered_once_with_a_signed_supplier_figure_and_audited(self):
        service = self.build()
        service.set_opening("5000", "-120.5", "200000", "30000")
        row = self.store.rows[0]
        self.assertEqual((row["snapshot_kind"], row["gold_total_eq21"], row["cash_egp"], row["bank_egp"]), ("OPENING", D("4879.500"), D("200000.00"), D("30000.00")))
        self.assertEqual(self.store.audits[0]["event_type"], "POSITION_OPENING_BALANCE")
        with self.assertRaises(SnapshotError) as raised:
            service.set_opening("1", "0", "0", "0")
        self.assertEqual(raised.exception.code, "ALREADY_INITIALIZED")
        self.assertEqual(len(self.store.rows), 1)

    def test_the_initial_balance_needs_settings_and_the_owner_rights(self):
        for missing in ("SETTINGS_MANAGE", "MANAGEMENT_REVIEW"):
            service = self.build(OWNER - {missing})
            with self.assertRaises(PermissionError):
                service.set_opening("5000", "0", "0", "0")
            self.assertEqual(self.store.rows, [])

    def test_invalid_numbers_are_refused_before_any_write(self):
        service = self.build()
        for gold, cash in (("abc", "0"), ("-1", "0"), ("NaN", "0"), ("5000", "x")):
            with self.assertRaises(SnapshotError):
                service.set_opening(gold, "0", cash, "0")
        self.assertEqual(self.store.rows, [])

    def test_a_daily_sheet_waits_for_the_initial_balance_and_is_taken_once_a_day(self):
        service = self.build()
        self.assertEqual(service.ensure_daily(), {"taken": False, "reason": "NO_OPENING"})
        service.set_opening("5000", "0", "0", "0")
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
        service.set_opening("5000", "0", "0", "0")
        service.take("MANUAL")
        self.assertEqual([a["event_type"] for a in self.store.audits], ["POSITION_OPENING_BALANCE", "POSITION_SNAPSHOT"])


if __name__ == "__main__":
    unittest.main()
