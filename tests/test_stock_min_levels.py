"""Minimum stock levels: the owner-only service, the validation of pieces and grams, and the inventory alerts listing what is below its minimum (fake repositories, no database)."""
from contextlib import contextmanager
from decimal import Decimal as D
import unittest

from services.report_service import ReportService
from services.stock_min_level_service import LevelError, StockMinLevelService, parse_grams, parse_pieces
from tests import test_report_alerts as alerts
from tests.auth_fakes import FakeAuthorization
from tests.market_trip_fakes import session_for

OWNER = frozenset({"APP_LOGIN", "SETTINGS_MANAGE"})


class Store:
    def __init__(self):
        self.rules, self.audits, self.next = {}, [], 1


class Repo:
    store = None

    def __init__(self, connection=None):
        pass

    def now(self):
        return "2026-10-22 10:00:00"

    def item_types(self):
        return [{"id": 1, "name": "دبلة"}, {"id": 2, "name": "خاتم"}]

    def karats(self):
        return [{"id": 2, "karat_value": 21}, {"id": 1, "karat_value": 18}]

    def levels(self):
        return [{"id": i, "item_type_id": r["t"], "karat_id": r["k"], "item_type": "دبلة" if r["t"] == 1 else "خاتم", "karat_value": 21 if r["k"] == 2 else 18, "min_pieces": r["p"], "min_grams": r["g"],
                 "pieces": 4, "grams": D("21.500")} for i, r in self.store.rules.items()]

    def level(self, level_id, lock=False):
        r = self.store.rules.get(int(level_id))
        return None if r is None else {"id": int(level_id), "item_type_id": r["t"], "karat_id": r["k"]}

    def type_exists(self, item_type_id):
        return next((t for t in self.item_types() if t["id"] == item_type_id), None)

    def karat_exists(self, karat_id):
        return next((k for k in self.karats() if k["id"] == karat_id), None)

    def upsert(self, *, item_type_id, karat_id, min_pieces, min_grams, user_id):
        existing = next((i for i, r in self.store.rules.items() if (r["t"], r["k"]) == (item_type_id, karat_id)), None)
        i = existing or self.store.next
        if existing is None:
            self.store.next += 1
        self.store.rules[i] = {"t": item_type_id, "k": karat_id, "p": min_pieces, "g": min_grams}
        return i

    def delete(self, level_id):
        self.store.rules.pop(int(level_id), None)

    def audit(self, **kwargs):
        self.store.audits.append(kwargs["event_type"])


class ServiceTests(unittest.TestCase):
    def build(self, permissions=OWNER):
        self.store = Store()
        Repo.store = self.store
        self.session = session_for(permissions)

        @contextmanager
        def transaction():
            yield object()
        return StockMinLevelService(self.session, transaction_factory=transaction, repository=Repo, authorization=FakeAuthorization(self.session))

    def test_save_replaces_the_rule_of_the_same_type_and_karat_and_audits(self):
        service = self.build()
        service.save(1, 2, "5", "")
        service.save(1, 2, "", "20.5")                                              # the same (type, karat): replaced, not added
        self.assertEqual(self.store.rules, {1: {"t": 1, "k": 2, "p": None, "g": D("20.500")}})
        service.save(2, 2, "3", "10")
        self.assertEqual(len(self.store.rules), 2)
        self.assertEqual(self.store.audits, ["STOCK_MIN_LEVEL_SET"] * 3)
        overview = service.overview()
        self.assertEqual(([t["name"] for t in overview["types"]], [k["value"] for k in overview["karats"]], len(overview["levels"])), (["دبلة", "خاتم"], [21, 18], 2))

    def test_remove_deletes_and_an_unknown_rule_is_refused(self):
        service = self.build()
        service.save(1, 2, "5", "")
        self.assertEqual(service.remove(1), {"id": "1", "deleted": True})
        self.assertEqual(self.store.rules, {})
        with self.assertRaises(LevelError) as raised:
            service.remove(1)
        self.assertEqual(raised.exception.code, "NOT_FOUND")

    def test_numbers_are_validated_before_anything_is_written(self):
        service = self.build()
        for pieces, grams, code in (("", "", "AMOUNT_REQUIRED"), ("0", "", "PIECES_INVALID"), ("2.5", "", "PIECES_INVALID"), ("", "-1", "GRAMS_INVALID"), ("", "1.2345", "GRAMS_INVALID"), ("", "abc", "GRAMS_INVALID")):
            with self.assertRaises(LevelError) as raised:
                service.save(1, 2, pieces, grams)
            self.assertEqual(raised.exception.code, code, (pieces, grams))
        for t, k, code in ((99, 2, "TYPE_NOT_FOUND"), (1, 99, "KARAT_NOT_FOUND")):
            with self.assertRaises(LevelError) as raised:
                service.save(t, k, "1", "")
            self.assertEqual(raised.exception.code, code)
        self.assertEqual((parse_pieces(" 12 "), parse_grams("5.250")), (12, D("5.250")))
        self.assertEqual(self.store.rules, {})

    def test_only_the_settings_right_may_use_it(self):
        service = self.build(frozenset({"APP_LOGIN"}))
        for call in (service.overview, lambda: service.save(1, 2, "1", ""), lambda: service.remove(1)):
            with self.assertRaises(PermissionError):
                call()


class LevelsRepo(alerts.AlertsRepo):
    levels = None

    def stock_min_levels(self):
        return LevelsRepo.levels


class AlertTests(alerts.AlertsCase):
    def setUp(self):
        super().setUp()
        self.service = ReportService(self.session, transaction_factory=self.service._transaction, repository=LevelsRepo, authorization=self.authorization, today=lambda: alerts.TODAY)
        # stock now: 21K دبلة 4 pieces / 21.5 g, خاتم 2 / 8 g, سلسلة 1 / 8 g; 18K غويشة 1 / 10 g
        LevelsRepo.levels = [
            {"item_type": "دبلة", "karat_value": 21, "min_pieces": 5, "min_grams": D("20.000")},       # 4 < 5: short 1 piece (21.5 g is enough)
            {"item_type": "خاتم", "karat_value": 21, "min_pieces": 2, "min_grams": D("10.000")},       # 2 pieces are enough, 8 g < 10 g: short 2.000 g
            {"item_type": "سلسلة", "karat_value": 21, "min_pieces": 1, "min_grams": None},             # fine
            {"item_type": "سوار", "karat_value": 24, "min_pieces": 3, "min_grams": None},              # nothing in stock: short 3 pieces
            {"item_type": "غويشة", "karat_value": 18, "min_pieces": None, "min_grams": D("9.000")}]    # 10 g >= 9 g: fine

    def rows(self, **filters):
        return next(t for t in self.run_alerts(**filters)["tables"] if t["key"] == "below_min")["rows"]

    def test_a_type_below_its_minimum_is_listed_with_what_is_short(self):
        rows = self.rows()
        self.assertEqual([(r["type"], r["karat"], r["short"]) for r in rows], [("سوار", "عيار 24", "3 قطعة"), ("خاتم", "عيار 21", "2.000 جرام"), ("دبلة", "عيار 21", "1 قطعة")])
        self.assertEqual(rows[2]["have"], "4 قطعة • 21.500 جرام")
        self.assertEqual({t["label"]: t["value"] for t in self.run_alerts()["totals"]}["تحت الحد الأدنى"], 3)

    def test_the_karat_filter_applies_but_a_text_search_never_hides_a_shortage(self):
        self.assertEqual([r["type"] for r in self.rows(karat="21")], ["خاتم", "دبلة"])
        self.assertEqual(len(self.rows(text="سلسلة")), 3)                                              # the search narrows the other tables only

    def test_no_rules_or_a_database_below_072_adds_no_table(self):
        for levels in ([], None):
            LevelsRepo.levels = levels
            self.assertNotIn("below_min", [t["key"] for t in self.run_alerts()["tables"]])


if __name__ == "__main__":
    unittest.main()
