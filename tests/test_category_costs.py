"""Making cost per gram of each item category: the owner-only service, the validation of the amount, and the cost being FROZEN into a piece at stock entry (fake repositories, no database)."""
from contextlib import contextmanager
from decimal import Decimal as D
import unittest

from services.category_cost_service import CategoryCostService, CostError, parse_cost
from tests.auth_fakes import FakeAuthorization
from tests.market_trip_fakes import session_for
from tests.test_inventory_stock import Repo as StockRepo, Store, service_for

OWNER = frozenset({"APP_LOGIN", "SETTINGS_MANAGE"})


class CostStore:
    def __init__(self):
        self.categories = {1: {"id": 1, "code": "A", "name": "A", "range_min": D("50"), "range_max": D("200")}, 2: {"id": 2, "code": "B", "name": "B", "range_min": None, "range_max": None}}
        self.costs, self.audits = {1: D("150.00")}, []


class CostRepo:
    store = None

    def __init__(self, connection=None):
        pass

    def now(self):
        return "2026-10-22 10:00:00"

    def categories(self):
        return [{**c, "cost": self.store.costs.get(c["id"]), "is_active": 1} for c in self.store.categories.values()]

    def category(self, category_id, lock=False):
        return self.store.categories.get(int(category_id))

    def cost(self, category_id):
        return self.store.costs.get(int(category_id))

    def set_cost(self, category_id, amount, user_id):
        self.store.costs[int(category_id)] = amount

    def clear_cost(self, category_id):
        self.store.costs.pop(int(category_id), None)

    def audit(self, **kwargs):
        self.store.audits.append((kwargs["event_type"], kwargs["metadata"]))


class ServiceTests(unittest.TestCase):
    def build(self, permissions=OWNER):
        self.store = CostStore()
        CostRepo.store = self.store
        self.session = session_for(permissions)

        @contextmanager
        def transaction():
            yield object()
        return CategoryCostService(self.session, transaction_factory=transaction, repository=CostRepo, authorization=FakeAuthorization(self.session))

    def test_the_list_shows_every_category_with_its_cost_or_none(self):
        listed = {c["code"]: c for c in self.build().costs()}
        self.assertEqual((listed["A"]["cost"], listed["A"]["range_max"], listed["B"]["cost"]), (D("150.00"), D("200"), None))

    def test_set_replaces_the_cost_and_audits_before_and_after_and_clear_removes_it(self):
        service = self.build()
        self.assertEqual(service.set_cost(1, "175.5")["cost"], D("175.50"))
        self.assertEqual(service.set_cost(2, "120")["cost"], D("120.00"))
        self.assertEqual(self.store.audits[0], ("CATEGORY_MAKING_COST_SET", {"before": "150.00", "after": "175.50"}))
        service.clear_cost(1)
        self.assertNotIn(1, self.store.costs)
        self.assertEqual(self.store.audits[-1][0], "CATEGORY_MAKING_COST_CLEARED")

    def test_invalid_amounts_unknown_categories_and_missing_rights_are_refused(self):
        service = self.build()
        for value in ("", "abc", "-1", "10.555", "NaN", "100000"):
            with self.assertRaises(CostError, msg=value):
                parse_cost(value)
        with self.assertRaises(CostError) as raised:
            service.set_cost(99, "10")
        self.assertEqual(raised.exception.code, "NOT_FOUND")
        self.assertEqual(self.store.costs, {1: D("150.00")})
        guest = self.build(frozenset({"APP_LOGIN"}))
        for call in (guest.costs, lambda: guest.set_cost(1, "10"), lambda: guest.clear_cost(1)):
            with self.assertRaises(PermissionError):
                call()
        self.assertEqual(self.store.costs, {1: D("150.00")})


class CategoryRepo(StockRepo):
    """The manual-entry fake plus a category with a cost (A = 150) and one without."""
    def get_active_category_by_id(self, category_id):
        return {"id": category_id, "code": "A"} if category_id in (1, 2) else None

    def get_category_making_cost(self, category_id):
        return D("150.00") if category_id == 1 else None


class FreezeTests(unittest.TestCase):
    def enter(self, **values):
        store = Store()
        session = session_for(frozenset({"APP_LOGIN", "INVENTORY_MANAGE"}))

        @contextmanager
        def transaction():
            yield type("T", (), {"connection": type("C", (), {"store": store})()})()
        from services.inventory_service import InventoryService
        from tests.test_inventory_stock import FakeBarcodes
        service = InventoryService(transaction_factory=transaction, repository=CategoryRepo, session=session, authorization=FakeAuthorization(session), barcode_repository=FakeBarcodes)
        service.create_inventory_item(item_type_id=1, karat_id=1, weight_grams="3.600", stock_classification="NEW_STOCK", **values)
        return store.created[0]

    def test_the_category_cost_is_frozen_into_the_new_piece(self):
        self.assertEqual(self.enter(category_id=1)["acquisition_making_per_gram"], D("150.00"))

    def test_a_category_without_a_cost_or_no_category_leaves_the_piece_cost_empty(self):
        self.assertNotIn("acquisition_making_per_gram", self.enter(category_id=2))
        self.assertNotIn("acquisition_making_per_gram", self.enter())

    def test_a_real_acquisition_making_is_never_replaced_by_the_category_cost(self):
        from services.inventory_service import InventoryService
        store = Store()
        session = session_for(frozenset({"APP_LOGIN"}))

        @contextmanager
        def transaction():
            yield type("T", (), {"connection": type("C", (), {"store": store})()})()
        from tests.test_inventory_stock import FakeBarcodes
        service = InventoryService(transaction_factory=transaction, repository=CategoryRepo, session=session, authorization=FakeAuthorization(session), barcode_repository=FakeBarcodes)
        service.create_item_on_connection(type("C", (), {"store": store})(), actor_user_id=9, item_type_id=1, karat_id=1, weight_grams="3.600", acquisition_source="SUPPLIER_PURCHASE",
                                          category_id=1, acquisition_making_per_gram=D("90.00"))
        self.assertEqual(store.created[0]["acquisition_making_per_gram"], D("90.00"))                 # the supplier's real rate wins over the category's 150


if __name__ == "__main__":
    unittest.main()
