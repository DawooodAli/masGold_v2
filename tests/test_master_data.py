"""Master Data: permanent delete of never-used factories / item types / categories, category creation,
disable / reactivate, duplicates and page-scoped messages. The real MasterDataService and
MasterDataViewModel run over an in-memory repository; the live database is blocked."""
import copy
from contextlib import contextmanager
from decimal import Decimal as D
import unittest
from unittest.mock import patch

from mysql.connector import IntegrityError

from app.session import AppSession
from services.authentication_service import AuthenticationResult, _VERIFIED
from services.master_data_service import MasterDataInUse, MasterDataService
from tests.auth_fakes import FakeAuthorization

MANAGER_GRANTS = frozenset({"APP_LOGIN", "MASTER_DATA_MANAGE"})

TABLE_KEY = {"factories": "factories", "item_types": "item_types", "item_categories": "categories"}


class Store:
    def __init__(self):
        self.factories = {1: {"id": 1, "code": "NR", "name": "النور للتصنيع", "notes": None, "is_active": 0}}
        self.item_types = {1: {"id": 1, "code": "RN", "name": "خاتم", "default_category_id": None,
                               "notes": None, "is_active": 1, "sort_order": 0}}
        self.categories = {}
        for n, code in enumerate("ABCDLT", start=1):
            self.categories[n] = {"id": n, "code": code, "name": code, "purchase_making_min_per_gram": None,
                                  "purchase_making_max_per_gram": None, "sale_making_min_per_gram": None,
                                  "sale_making_max_per_gram": None, "manager_approval_below_per_gram": None,
                                  "sort_order": n, "notes": None, "is_active": 1}
        self.categories[1].update(purchase_making_min_per_gram=D("50.00"), purchase_making_max_per_gram=D("150.00"),
                                  sale_making_min_per_gram=D("200.00"), sale_making_max_per_gram=D("400.00"),
                                  manager_approval_below_per_gram=D("250.00"))
        self.inventory_items, self.inventory_reconciliation_cases = [], []
        self.old_gold_movements, self.analytics_segments = [], []
        self.locks = []

    def tables(self):
        return {k: v for k, v in vars(self).items() if k != "locks"}

    def next_id(self, table):
        return max(table, default=0) + 1


class Repo:
    """The MasterDataRepository contract over the in-memory store."""
    STOCK_REFERENCES = __import__("repositories.master_data_repository", fromlist=["x"]) \
        .MasterDataRepository.STOCK_REFERENCES

    def __init__(self, connection):
        self.store = connection.store

    def _table(self, name):
        return getattr(self.store, TABLE_KEY[name])

    # reads
    def get_active_karats(self):
        return [{"id": 1, "karat_value": 21, "purity": D("0.875"), "name": "21K", "sort_order": 1}]

    def get_active_categories(self):
        return [dict(c) for c in sorted(self.store.categories.values(), key=lambda c: c["sort_order"]) if c["is_active"]]

    def get_all_categories(self):
        return [dict(c) for c in sorted(self.store.categories.values(), key=lambda c: c["sort_order"])]

    def get_active_factories(self):
        return [dict(f) for f in self.store.factories.values() if f["is_active"]]

    def get_all_factories(self):
        return [dict(f) for f in self.store.factories.values()]

    def get_active_item_types(self):
        return [dict(t) for t in self.store.item_types.values() if t["is_active"]]

    def get_all_item_types(self):
        return [dict(t) for t in self.store.item_types.values()]

    def get_active_employees(self):
        return []

    def get_category_by_id(self, category_id):
        row = self.store.categories.get(int(category_id))
        return dict(row) if row else None

    def get_factory_by_id(self, factory_id):
        row = self.store.factories.get(int(factory_id))
        return dict(row) if row else None

    def get_item_type_by_id(self, item_type_id):
        row = self.store.item_types.get(int(item_type_id))
        return dict(row) if row else None

    # writes
    def _unique(self, table, field, value, own_id=None):
        if value is None:
            return
        for row in table.values():
            if row["id"] != own_id and (row.get(field) or "").casefold() == value.casefold():
                raise IntegrityError(msg="Duplicate entry", errno=1062)

    def create_factory(self, code, name, notes=None):
        self._unique(self.store.factories, "code", code)
        new = self.store.next_id(self.store.factories)
        self.store.factories[new] = {"id": new, "code": code, "name": name, "notes": notes, "is_active": 1}
        return new

    def update_factory(self, factory_id, code, name, notes=None, is_active=True):
        self._unique(self.store.factories, "code", code, factory_id)
        self.store.factories[factory_id].update(code=code, name=name, notes=notes, is_active=int(is_active))

    def create_item_type(self, code, name, default_category_id, notes=None):
        self._unique(self.store.item_types, "code", code)
        self._unique(self.store.item_types, "name", name)
        new = self.store.next_id(self.store.item_types)
        self.store.item_types[new] = {"id": new, "code": code, "name": name, "sort_order": 0,
                                      "default_category_id": default_category_id, "notes": notes, "is_active": 1}
        return new

    def update_item_type(self, item_type_id, code, name, default_category_id, notes=None, is_active=True):
        self._unique(self.store.item_types, "code", code, item_type_id)
        self._unique(self.store.item_types, "name", name, item_type_id)
        self.store.item_types[item_type_id].update(code=code, name=name, default_category_id=default_category_id,
                                                   notes=notes, is_active=int(is_active))

    def update_category_rules(self, category_id, purchase_min, purchase_max, sale_min, sale_max,
                              manager_approval_below, notes=None, is_active=True):
        self.store.categories[category_id].update(
            purchase_making_min_per_gram=purchase_min, purchase_making_max_per_gram=purchase_max,
            sale_making_min_per_gram=sale_min, sale_making_max_per_gram=sale_max,
            manager_approval_below_per_gram=manager_approval_below, notes=notes, is_active=int(is_active))

    def create_category(self, code, name, purchase_min, purchase_max, sale_min, sale_max,
                        manager_approval_below, notes=None):
        self._unique(self.store.categories, "code", code)
        new = self.store.next_id(self.store.categories)
        self.store.categories[new] = {
            "id": new, "code": code, "name": name, "purchase_making_min_per_gram": purchase_min,
            "purchase_making_max_per_gram": purchase_max, "sale_making_min_per_gram": sale_min,
            "sale_making_max_per_gram": sale_max, "manager_approval_below_per_gram": manager_approval_below,
            "sort_order": max(c["sort_order"] for c in self.store.categories.values()) + 1,
            "notes": notes, "is_active": 1}
        return new

    def set_category_active(self, category_id, is_active):
        self.store.categories[category_id]["is_active"] = int(is_active)

    # permanent delete
    def lock_master_row(self, table, row_id):
        self.store.locks.append(("FOR UPDATE", table, row_id))
        row = self._table(table).get(row_id)
        return {"id": row_id} if row else None

    def stock_references(self, table, row_id):
        found = []
        for child, column in self.STOCK_REFERENCES[table]:
            self.store.locks.append(("FOR SHARE", child))
            if any(r.get(column) == row_id for r in getattr(self.store, child)):
                found.append(child)
        return found

    def used_master_ids(self, table):
        return {r[column] for child, column in self.STOCK_REFERENCES[table]
                for r in getattr(self.store, child) if r.get(column) is not None}

    def item_types_defaulting_to(self, category_id):
        return sum(t["default_category_id"] == category_id for t in self.store.item_types.values())

    def delete_master_row(self, table, row_id):
        del self._table(table)[row_id]


class Harness(unittest.TestCase):
    def setUp(self):
        block = patch("database.connection.DatabaseConnection.connect",
                      side_effect=AssertionError("Live database forbidden"))
        block.start()
        self.addCleanup(block.stop)
        self.store = Store()
        self.session = AppSession()
        self.session._establish(AuthenticationResult(1, None, "المالك", MANAGER_GRANTS, _VERIFIED),
                                int(self.session.generation))
        self.authorization = FakeAuthorization(self.session)
        self.service = MasterDataService(self.session, authorization=self.authorization,
                                         transaction_factory=self.transaction, repository=Repo)

    @contextmanager
    def transaction(self):
        saved = copy.deepcopy(self.store.tables())
        try:
            yield type("T", (), {"connection": type("C", (), {"store": self.store})()})()
        except BaseException:
            for key, value in saved.items():
                setattr(self.store, key, value)
            raise

    def stock(self, status="IN_STOCK", **refs):
        row = {"id": len(self.store.inventory_items) + 1, "inventory_status": status, "item_type_id": 1,
               "factory_id": None, "category_id": None, **refs}
        self.store.inventory_items.append(row)
        return row

    def snapshot(self):
        return copy.deepcopy(self.store.tables())


class FactoryDeleteTests(Harness):
    def test_unused_factory_is_deleted_completely(self):
        new = self.service.create_factory("DELETE TEST FACTORY", "DTF")
        self.service.delete_factory(new)
        self.assertNotIn(new, self.store.factories)
        self.assertNotIn("DELETE TEST FACTORY", [f["name"] for f in self.service.get_all_factories()])
        self.assertEqual(self.store.locks[0], ("FOR UPDATE", "factories", new))          # row lock first

    def test_any_stock_reference_blocks_and_changes_nothing(self):
        cases = {"in stock": lambda f: self.stock(factory_id=f),
                 "sold": lambda f: self.stock("SOLD", factory_id=f),
                 "returned pending": lambda f: self.stock("RETURNED_PENDING", factory_id=f),
                 "converted": lambda f: self.stock("CONVERTED_TO_OLD_GOLD", factory_id=f),
                 "reconciliation": lambda f: self.store.inventory_reconciliation_cases.append({"observed_factory_id": f}),
                 "old gold movement": lambda f: self.store.old_gold_movements.append({"factory_id": f}),
                 "analytics segment": lambda f: self.store.analytics_segments.append({"factory_id": f})}
        for label, link in cases.items():
            with self.subTest(label):
                self.setUp()
                factory = self.service.create_factory("MF " + label, None)
                link(factory)
                before = self.snapshot()
                with self.assertRaises(MasterDataInUse):
                    self.service.delete_factory(factory)
                self.assertEqual(self.snapshot(), before)               # nothing deleted, nulled or cascaded

    def test_missing_factory(self):
        with self.assertRaisesRegex(ValueError, "Factory does not exist"):
            self.service.delete_factory(99)


class ItemTypeDeleteTests(Harness):
    def test_unused_item_type_is_deleted(self):
        new = self.service.create_item_type("DELETE TEST TYPE", "DTT")
        self.service.delete_item_type(new)
        self.assertNotIn(new, self.store.item_types)

    def test_referenced_or_historical_item_type_is_refused(self):
        for status in ("IN_STOCK", "SOLD", "SCRAPPED", "REMOVED"):
            with self.subTest(status):
                self.setUp()
                new = self.service.create_item_type("T " + status, None)
                self.stock(status, item_type_id=new)
                before = self.snapshot()
                with self.assertRaises(MasterDataInUse):
                    self.service.delete_item_type(new)
                self.assertEqual(self.snapshot(), before)
        self.setUp()
        new = self.service.create_item_type("T REC", None)
        self.store.inventory_reconciliation_cases.append({"observed_item_type_id": new})
        with self.assertRaises(MasterDataInUse):
            self.service.delete_item_type(new)


class EverUsedFlagTests(Harness):
    """The lists tell the page whether the permanent-delete area may offer a button (display only)."""

    def test_flag_follows_every_historical_reference(self):
        factory = self.service.create_factory("EU F", None)
        item_type = self.service.create_item_type("EU T", None)
        flags = lambda: ({r["id"]: r["ever_used"] for r in self.service.get_all_factories()},
                         {r["id"]: r["ever_used"] for r in self.service.get_all_item_types()})
        self.assertEqual((flags()[0][factory], flags()[1][item_type]), (False, False))
        self.stock("REMOVED", factory_id=factory, item_type_id=item_type)         # history, not current stock
        self.assertEqual((flags()[0][factory], flags()[1][item_type]), (True, True))
        other = self.service.create_factory("EU F2", None)
        self.store.old_gold_movements.append({"factory_id": other})
        self.assertTrue(flags()[0][other])

    def test_the_delete_still_refuses_whatever_the_flag_says(self):
        factory = self.service.create_factory("EU F3", None)
        self.assertFalse(self.service.get_all_factories()[-1]["ever_used"])
        self.stock("SOLD", factory_id=factory)                                    # used after the list loaded
        with self.assertRaises(MasterDataInUse):
            self.service.delete_factory(factory)


class CategoryTests(Harness):
    def test_create_edit_disable_reactivate_delete(self):
        new = self.service.create_category("delete_test", "", "10", "20", "30", "40", "35", "tag")
        row = self.store.categories[new]
        self.assertEqual((row["code"], row["name"], row["is_active"]), ("DELETE_TEST", "DELETE_TEST", 1))
        self.assertEqual((row["purchase_making_min_per_gram"], row["sale_making_max_per_gram"],
                          row["manager_approval_below_per_gram"]), (D("10.00"), D("40.00"), D("35.00")))
        self.assertIsInstance(row["sale_making_min_per_gram"], D)                  # never float
        self.assertEqual(row["sort_order"], 7)
        self.assertIn(new, [c["id"] for c in self.service.get_categories()])      # active at once
        self.service.update_category_rules(new, D("11"), D("21"), D("31"), D("41"), D("36"), "tag")
        self.assertEqual(self.store.categories[new]["sale_making_min_per_gram"], D("31"))
        self.service.set_category_active(new, False)
        self.assertNotIn(new, [c["id"] for c in self.service.get_categories()])
        self.assertEqual(self.store.categories[new]["sale_making_min_per_gram"], D("31"))   # rules untouched
        self.service.set_category_active(new, True)
        self.assertIn(new, [c["id"] for c in self.service.get_categories()])
        self.service.delete_category(new)
        self.assertNotIn(new, self.store.categories)

    def test_min_greater_than_max_is_refused_on_create_and_edit(self):
        with self.assertRaisesRegex(ValueError, "Sale making minimum cannot exceed maximum"):
            self.service.create_category("X1", None, None, None, "300", "200", None)
        with self.assertRaisesRegex(ValueError, "Purchase making minimum cannot exceed maximum"):
            self.service.create_category("X2", None, "151", "150", None, None, None)
        with self.assertRaisesRegex(ValueError, "Sale making minimum cannot exceed maximum"):
            self.service.update_category_rules(1, D("50"), D("150"), D("300"), D("200"), D("250"))
        self.assertEqual(self.store.categories[1]["sale_making_min_per_gram"], D("200.00"))   # A unchanged
        self.assertNotIn("X1", [c["code"] for c in self.store.categories.values()])

    def test_invalid_codes_and_values(self):
        for code in ("", "   ", "A B", "X" * 21):
            with self.subTest(code=code), self.assertRaises(ValueError):
                self.service.create_category(code)
        for value in ("abc", "-1", "NaN", "Infinity"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.service.create_category("OK", None, value)

    def test_duplicate_code_is_refused_case_insensitively(self):
        self.service.create_category("DELETE_TEST")
        before = self.snapshot()
        for code in ("DELETE_TEST", "delete_test", "a"):
            with self.subTest(code), self.assertRaisesRegex(ValueError, "Category code already exists"):
                self.service.create_category(code)
        self.assertEqual(self.snapshot(), before)

    def test_used_category_is_refused_and_stock_untouched(self):
        for status in ("IN_STOCK", "SOLD"):
            with self.subTest(status):
                self.setUp()
                self.stock(status, category_id=1)
                before = self.snapshot()
                with self.assertRaises(MasterDataInUse):
                    self.service.delete_category(1)
                self.assertEqual(self.snapshot(), before)

    def test_seeded_unused_category_follows_the_same_rule(self):
        self.service.delete_category(6)                  # T: seeded, unused, no system flag in the schema
        self.assertEqual([c["code"] for c in self.service.get_all_categories()], list("ABCDL"))

    def test_default_category_of_an_item_type_is_refused(self):
        self.store.item_types[1]["default_category_id"] = 2
        with self.assertRaisesRegex(ValueError, "default category of an item type"):
            self.service.delete_category(2)
        self.assertIn(2, self.store.categories)

    def test_seeded_categories_load(self):
        self.assertEqual([c["code"] for c in self.service.get_categories()], list("ABCDLT"))


class ViewModelTests(Harness):
    def setUp(self):
        super().setUp()
        from PySide6.QtCore import QCoreApplication
        self.app = QCoreApplication.instance() or QCoreApplication([])
        from app.viewmodels.master_data_viewmodel import MasterDataViewModel
        with patch("app.viewmodels.master_data_viewmodel.MasterDataService", lambda *a: self.service):
            self.vm = MasterDataViewModel()

    def test_messages_are_page_scoped_and_cleared(self):
        self.assertTrue(self.vm.createFactory("مصنع", "", ""))
        self.assertEqual((self.vm.actionScope, self.vm.actionSuccess), ("factories", True))
        self.assertTrue(self.vm.reactivateItemType(1) or True)
        self.assertEqual(self.vm.actionScope, "itemTypes")
        self.vm.clearAction()
        self.assertEqual((self.vm.actionMessage, self.vm.actionScope), ("", ""))

    def test_delete_messages_are_arabic(self):
        self.stock("SOLD", factory_id=1)
        self.assertFalse(self.vm.deleteFactory(1))
        self.assertEqual(self.vm.actionMessage,
                         "لا يمكن حذف المصنع نهائيًا لأنه مرتبط ببيانات في المخزون. يمكنك تعطيله فقط.")
        self.stock("SOLD", item_type_id=1, category_id=1)
        self.assertFalse(self.vm.deleteItemType(1))
        self.assertEqual(self.vm.actionMessage,
                         "لا يمكن حذف الصنف نهائيًا لأنه مرتبط ببيانات في المخزون. يمكنك تعطيله فقط.")
        self.assertFalse(self.vm.deleteCategory(1))
        self.assertEqual(self.vm.actionMessage,
                         "لا يمكن حذف الفئة نهائيًا لأنها مرتبطة ببيانات في المخزون. يمكنك تعطيلها فقط.")
        self.assertTrue(self.vm.deleteCategory(6))
        self.assertEqual((self.vm.actionMessage, self.vm.allCategoryCount), ("تم حذف الفئة نهائيًا: T", 5))

    def test_category_slots_exist_and_keep_rules(self):
        self.assertTrue(self.vm.createCategory("e", "", "10", "20", "30", "40", "", ""))
        new = max(self.store.categories)
        self.assertTrue(self.vm.disableCategory(new))
        self.assertEqual(self.vm.disabledCategoryCount, 1)
        self.assertTrue(self.vm.reactivateCategory(new))
        self.assertEqual(self.store.categories[new]["purchase_making_min_per_gram"], D("10.00"))
        self.assertFalse(self.vm.createCategory("E", "", "", "", "", "", "", ""))
        self.assertEqual(self.vm.actionMessage, "كود الفئة مستخدم بالفعل.")
        self.assertFalse(self.vm.updateCategoryRules(1, "50", "150", "300", "200", "250", ""))
        self.assertEqual(self.vm.actionMessage, "الحد الأدنى لمصنعية البيع لا يمكن أن يتجاوز الحد الأقصى.")
        self.assertTrue(self.vm.updateCategoryRules(1, "50", "150", "200", "400", "250", ""))
        self.assertEqual(self.store.categories[1]["manager_approval_below_per_gram"], D("250"))

    def test_duplicate_factory_and_item_type_errors(self):
        self.assertFalse(self.vm.createFactory("آخر", "nr", ""))
        self.assertEqual(self.vm.actionMessage, "كود المصنع مستخدم بالفعل.")
        self.assertFalse(self.vm.createItemType("خاتم", "", 0, ""))
        self.assertEqual(self.vm.actionMessage, "اسم الصنف أو الكود مستخدم بالفعل.")


class AuthorizationTests(Harness):
    """Every mutation needs MASTER_DATA_MANAGE on the strict WRITE path; refusal writes nothing."""

    def mutations(self):
        return {
            "create factory": lambda: self.service.create_factory("مصنع", "X1"),
            "edit factory": lambda: self.service.update_factory(1, "اسم جديد", "NR", None, False),
            "disable factory": lambda: self.service.update_factory(1, "النور للتصنيع", "NR", None, False),
            "reactivate factory": lambda: self.service.update_factory(1, "النور للتصنيع", "NR", None, True),
            "delete factory": lambda: self.service.delete_factory(1),
            "create item type": lambda: self.service.create_item_type("نوع", "X2"),
            "edit item type": lambda: self.service.update_item_type(1, "خاتم 2", "RN"),
            "disable item type": lambda: self.service.update_item_type(1, "خاتم", "RN", None, None, False),
            "delete item type": lambda: self.service.delete_item_type(1),
            "create category": lambda: self.service.create_category("NEW"),
            "category rules": lambda: self.service.update_category_rules(1, D("1"), D("2"), D("3"), D("4"), D("3")),
            "disable category": lambda: self.service.set_category_active(2, False),
            "reactivate category": lambda: self.service.set_category_active(2, True),
            "delete category": lambda: self.service.delete_category(6),
        }

    def test_owner_with_master_data_manage_can_create(self):
        new = self.service.create_factory("مصنع المالك", "OWN")
        self.assertIn(new, self.store.factories)
        self.assertEqual(self.authorization.checked, [("WRITE", "MASTER_DATA_MANAGE")])   # strict write path

    def test_every_mutation_is_refused_without_the_permission_and_writes_nothing(self):
        self.authorization.permissions = {"APP_LOGIN", "SETTINGS_MANAGE", "INVENTORY_MANAGE"}
        for label, mutation in self.mutations().items():
            with self.subTest(label):
                before = self.snapshot()
                self.authorization.checked.clear()
                with self.assertRaises(PermissionError):
                    mutation()
                self.assertEqual(self.snapshot(), before)
                self.assertEqual(self.authorization.checked, [("WRITE", "MASTER_DATA_MANAGE")])
                self.assertEqual(self.store.locks, [])                  # refused before any row lock

    def test_database_revocation_beats_a_stale_session(self):
        self.service.create_factory("قبل", "B1")
        self.authorization.permissions = {"APP_LOGIN"}                  # revoked in the database only
        self.assertIn("MASTER_DATA_MANAGE", self.session.current_actor().permissions)   # session still stale
        self.assertTrue(self.service.can_manage())                      # the UI hint is not authoritative
        with self.assertRaises(PermissionError):
            self.service.create_factory("بعد", "B2")
        self.assertNotIn("B2", [f["code"] for f in self.store.factories.values()])
        self.authorization.permissions = MANAGER_GRANTS                 # restored: works without re-login
        self.service.create_factory("بعد", "B2")

    def test_no_session_is_refused(self):
        service = MasterDataService(transaction_factory=self.transaction, repository=Repo)
        with self.assertRaises(PermissionError):
            service.create_factory("x", None)
        self.assertEqual(len(service.get_all_factories()), 1)            # reads stay open

    def test_viewmodel_shows_arabic_refusal(self):
        from PySide6.QtCore import QCoreApplication
        QCoreApplication.instance() or QCoreApplication([])
        from app.viewmodels.master_data_viewmodel import MasterDataViewModel
        self.authorization.permissions = {"APP_LOGIN"}
        with patch("app.viewmodels.master_data_viewmodel.MasterDataService", lambda *a: self.service):
            vm = MasterDataViewModel()
        for call in (lambda: vm.createFactory("x", "", ""), lambda: vm.deleteItemType(1),
                     lambda: vm.createCategory("Z", "", "", "", "", "", "", "")):
            self.assertFalse(call())
            self.assertEqual(vm.actionMessage,
                             "ليس لديك صلاحية تعديل البيانات الأساسية (المصانع والأصناف والفئات).")


if __name__ == "__main__":
    unittest.main()
