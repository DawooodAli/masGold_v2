"""Manageable income / expense categories (Settings): add / rename / disable / delete rules, the owner-only right, and the treasury using the table (fake repositories, no database)."""
from contextlib import contextmanager
from decimal import Decimal as D
import unittest
from uuid import uuid4

from services.cash_category_service import SEED, CashCategoryService, CategoryError
from services.correction_service import CorrectionError
from tests.auth_fakes import FakeAuthorization
from tests.market_trip_fakes import session_for
from tests.test_financial_accounts import FinancialTestCase

OWNER = frozenset({"APP_LOGIN", "SETTINGS_MANAGE"})


class Store:
    now = None

    def __init__(self):
        self.rows = [{"id": i + 1, "code": c, "name": n, "direction": d, "is_active": 1, "is_system": 1} for i, (c, n, d) in enumerate(SEED)]
        self.used, self.audits = {"RENT": 3}, []


class FakeRepo:
    store = None

    def __init__(self, connection=None):
        pass

    def now(self):
        return "2026-10-22 10:00:00"

    def categories(self):
        return [dict(r) for r in self.store.rows]

    def category(self, code, lock=False):
        return next((dict(r) for r in self.store.rows if r["code"] == code), None)

    def name_taken(self, name, except_code=None):
        return any(r["name"] == name and r["code"] != (except_code or "") for r in self.store.rows)

    def next_sort_order(self):
        return 1000

    def usage(self, code):
        return self.store.used.get(code, 0)

    def insert(self, *, code, name, direction, sort_order, user_id):
        self.store.rows.append({"id": len(self.store.rows) + 1, "code": code, "name": name, "direction": direction, "is_active": 1, "is_system": 0})

    def rename(self, code, name):
        next(r for r in self.store.rows if r["code"] == code)["name"] = name

    def set_active(self, code, active):
        next(r for r in self.store.rows if r["code"] == code)["is_active"] = 1 if active else 0

    def delete(self, code):
        assert not self.store.used.get(code)                                     # fk_fo_category
        self.store.rows = [r for r in self.store.rows if r["code"] != code]
        return 1

    def audit(self, **kwargs):
        self.store.audits.append(kwargs["event_type"])


class ManagementTests(unittest.TestCase):
    def build(self, permissions=OWNER):
        self.store = Store()
        FakeRepo.store = self.store
        self.session = session_for(permissions)

        @contextmanager
        def transaction():
            yield object()
        return CashCategoryService(self.session, transaction_factory=transaction, repository=FakeRepo, authorization=FakeAuthorization(self.session))

    def test_the_seed_is_the_ten_categories_that_existed_in_code(self):
        self.assertEqual([c for c, _, _ in SEED], ["EXPENSE", "SALARY", "RENT", "UTILITIES", "TRANSPORT", "MAINTENANCE", "HOSPITALITY", "STAFF_ADVANCE", "REVENUE", "REPAIR"])
        service = self.build()
        listed = service.categories()
        self.assertEqual([(c["code"], c["used"]) for c in listed if c["code"] in ("RENT", "SALARY")], [("SALARY", False), ("RENT", True)])

    def test_add_generates_a_code_validates_the_name_and_direction_and_is_audited(self):
        service = self.build()
        added = service.add("  تأمينات  ", "OUT")
        self.assertEqual((added["name"], added["direction"], added["system"], added["active"]), ("تأمينات", "OUT", False, True))
        self.assertRegex(added["code"], r"^C[0-9A-F]{12}$")
        for name, direction, code in (("", "OUT", "NAME_REQUIRED"), ("x" * 61, "OUT", "NAME_LONG"), ("تأمينات", "OUT", "NAME_TAKEN"), ("جديد", "SIDEWAYS", "DIRECTION_INVALID")):
            with self.assertRaises(CategoryError) as raised:
                service.add(name, direction)
            self.assertEqual(raised.exception.code, code)
        self.assertEqual(self.store.audits, ["CASH_CATEGORY_ADDED"])

    def test_rename_keeps_the_code_and_refuses_a_taken_name(self):
        service = self.build()
        renamed = service.rename("RENT", "إيجار المحل")
        self.assertEqual((renamed["code"], renamed["name"]), ("RENT", "إيجار المحل"))
        with self.assertRaises(CategoryError) as raised:
            service.rename("SALARY", "إيجار المحل")
        self.assertEqual(raised.exception.code, "NAME_TAKEN")

    def test_a_used_category_can_only_be_disabled_an_unused_one_can_be_deleted(self):
        service = self.build()
        with self.assertRaises(CategoryError) as raised:
            service.delete("RENT")
        self.assertEqual(raised.exception.code, "CATEGORY_USED")
        self.assertFalse(service.set_active("RENT", False)["active"])
        self.assertTrue(service.set_active("RENT", True)["active"])
        self.assertEqual(service.delete("TRANSPORT"), {"code": "TRANSPORT", "deleted": True})
        self.assertNotIn("TRANSPORT", [r["code"] for r in self.store.rows])
        with self.assertRaises(CategoryError):
            service.delete("TRANSPORT")

    def test_only_the_settings_right_may_manage_or_see_the_list(self):
        service = self.build(frozenset({"APP_LOGIN"}))
        for call in (service.categories, lambda: service.add("x", "OUT"), lambda: service.delete("EXPENSE"), lambda: service.set_active("EXPENSE", False)):
            with self.assertRaises(PermissionError):
                call()
        self.assertEqual(len(self.store.rows), 10)


class TreasuryUsesTheTableTests(FinancialTestCase):
    def setUp(self):
        super().setUp()
        self.store.categories = [{"code": c, "name": n, "direction": d, "is_active": 1} for c, n, d in SEED]
        self.store.categories.append({"code": "CCUSTOM00001", "name": "تأمينات", "direction": "OUT", "is_active": 1})
        self.store.categories.append({"code": "CCUSTOM00002", "name": "مكافآت", "direction": "OUT", "is_active": 0})

    def move(self, direction, kind, amount="100"):
        return self.fin.cash_movement("CASH:1", direction, kind, amount, "", str(uuid4()))

    def test_a_custom_category_is_offered_booked_and_named_from_the_table(self):
        types = {t["code"]: t for t in self.fin.cash_view()["types"]}
        self.assertIn("CCUSTOM00001", types)
        self.assertEqual((types["CCUSTOM00001"]["text"], types["CCUSTOM00001"]["directions"]), ("تأمينات", ["OUT"]))
        self.assertNotIn("CCUSTOM00002", types)                                                   # a disabled category is not offered
        before = self.cash()
        self.move("OUT", "CCUSTOM00001", "250")
        self.assertEqual(self.cash(), before - D("250"))
        self.assertEqual(list(self.store.ops.values())[-1]["category"], "CCUSTOM00001")
        self.assertEqual(self.store.op_legs[-1]["description"], "تأمينات")

    def test_a_disabled_wrong_direction_or_unknown_category_is_refused_before_any_write(self):
        before = self.store.tables()
        for direction, kind in (("OUT", "CCUSTOM00002"), ("IN", "CCUSTOM00001"), ("OUT", "NOT_THERE"), ("IN", "EXPENSE")):
            with self.assertRaises(CorrectionError) as raised:
                self.move(direction, kind)
            self.assertEqual(raised.exception.code, "TYPE_INVALID", (direction, kind))
        self.assertEqual(self.store.tables(), before)

    def test_a_renamed_category_shows_its_current_name_on_old_and_new_rows(self):
        from tests.test_cash_screen import cash_row
        self.store.cash_rows = [cash_row(2, "-1500", "RENT", ("-1500", 1), operation_id=9)]
        next(c for c in self.store.categories if c["code"] == "RENT")["name"] = "إيجار المحل"
        view = self.fin.cash_view()
        self.assertEqual([r["label"] for r in view["out"]["rows"]], ["إيجار المحل"])
        self.assertIn({"code": "RENT", "text": "إيجار المحل"}, view["labels"])


if __name__ == "__main__":
    unittest.main()
