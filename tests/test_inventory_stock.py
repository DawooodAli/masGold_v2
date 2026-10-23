"""Inventory close pass: manual entry without a responsible employee, بيان المخزون (Stock Balance),
الجرد بالباركود (Barcode Stocktake) and the barcode print idempotency. Real services / view-models over
in-memory doubles; the live database is blocked. The SQL itself is proven by test_inventory_stock_mysql."""
from contextlib import contextmanager
from decimal import Decimal as D
import unittest
from unittest.mock import patch


from app.session import AppSession
from app.viewmodels.stock_balance_viewmodel import StockBalanceViewModel
from app.viewmodels.stocktake_viewmodel import DUPLICATE, UNKNOWN, StocktakeViewModel
from repositories.inventory_repository import CURRENT_STOCK
from services.authentication_service import AuthenticationResult, _VERIFIED
from services.inventory_service import InventoryService
from tests.auth_fakes import FakeAuthorization

K21, K18, K24 = (1, 21, 1), (2, 18, 2), (3, 24, 3)        # (karat_id, value, sort_order)


def piece(item_id, karat, item_type, weight, status="IN_STOCK", stock_class="NEW_STOCK", barcode=None, size=None):
    return {"inventory_item_id": item_id, "karat_id": karat[0], "karat_value": karat[1], "karat_sort_order": karat[2],
            "karat_name": f"{karat[1]}K", "item_type_id": hash(item_type) % 1000, "item_type_name": item_type,
            "weight_grams": D(weight), "inventory_status": status, "stock_classification": stock_class,
            "barcode_value": barcode, "size": size}


class Repo:
    """InventoryRepository contract over a list of pieces: the same predicate (IN_STOCK) and grouping."""

    def __init__(self, connection):
        self.store = connection.store

    def _current(self):
        return [p for p in self.store.pieces if p["inventory_status"] == "IN_STOCK"]

    def get_stock_balance(self, stock_class=None):
        groups = {}
        for p in self._current():
            if stock_class and p["stock_classification"] != stock_class:
                continue
            key = (p["karat_sort_order"], -p["karat_value"], p["item_type_name"])
            g = groups.setdefault(key, {"karat_id": p["karat_id"], "karat_value": p["karat_value"],
                                        "karat_name": p["karat_name"], "karat_sort_order": p["karat_sort_order"],
                                        "item_type_id": p["item_type_id"], "item_type_name": p["item_type_name"],
                                        "piece_count": 0, "weight_grams": D("0.000")})
            g["piece_count"] += 1
            g["weight_grams"] += p["weight_grams"]
        return [groups[k] for k in sorted(groups)]

    def get_stocktake_source(self, item_type_id=None, karat_id=None):
        return [dict(p) for p in self._current()
                if (item_type_id is None or p["item_type_id"] == item_type_id)
                and (karat_id is None or p["karat_id"] == karat_id)]

    def get_stocktake_filters(self):
        types = sorted({(p["item_type_id"], p["item_type_name"]) for p in self.store.pieces}, key=lambda t: t[1])
        return {"item_types": [{"id": i, "name": n} for i, n in types],
                "karats": [{"id": k[0], "karat_value": k[1], "name": f"{k[1]}K"} for k in (K21, K18, K24)]}

    # manual entry
    def get_active_item_type_by_id(self, item_type_id):
        return {"id": item_type_id, "default_category_id": None}

    def get_active_karat_by_id(self, karat_id):
        return {"id": karat_id}

    def get_active_employee_by_id(self, employee_id):
        return {"id": employee_id} if employee_id == 5 else None

    def get_inventory_movement_type_id(self, code):
        return 1

    def create_inventory_item(self, **values):
        self.store.created.append(values)
        return 41

    def assign_barcode(self, **values):
        self.store.__dict__.setdefault("assigned", []).append(values)

    # correction of a piece's type and karat
    def lock_item_for_correction(self, item_id):
        return next((dict(p, id=item_id) for p in self.store.pieces if p["inventory_item_id"] == item_id), None)

    def update_item_type_and_karat(self, item_id, item_type_id, karat_id):
        piece = next(p for p in self.store.pieces if p["inventory_item_id"] == item_id)
        piece["item_type_id"], piece["karat_id"] = item_type_id, karat_id
        return 1

    def audit_item_correction(self, **values):
        self.store.__dict__.setdefault("audits", []).append(values)

    # a spoiled piece becomes Scrap
    def lock_old_gold_balance(self, karat_id):
        self.store.__dict__.setdefault("locks", []).append(("scrap", karat_id))

    def lock_item_for_scrap(self, item_id):
        piece = next((p for p in self.store.pieces if p["inventory_item_id"] == item_id), None)
        return None if piece is None else dict(piece, id=item_id, barcode_value=piece.get("barcode_value"))

    def mark_item_scrapped(self, item_id, user_id):
        piece = next(p for p in self.store.pieces if p["inventory_item_id"] == item_id)
        piece["inventory_status"] = "SCRAPPED"
        return 1

    def add_scrapped_movement(self, *, item, reason, user_id):
        self.store.movements.append({"kind": "SCRAPPED", "reason": reason})
        return 501

    def add_scrap_in_from_stock(self, *, item, notes, user_id):
        self.store.__dict__.setdefault("scrap_in", []).append((item["karat_id"], item["weight_grams"], notes))
        return 601

    def create_inventory_movement(self, **values):
        self.store.movements.append(values)
        return 1


class Store:
    def __init__(self, pieces=()):
        self.pieces, self.created, self.movements, self.reads = list(pieces), [], [], 0


def session_for(permissions=frozenset({"APP_LOGIN", "INVENTORY_MANAGE"}), user_id=9):
    session = AppSession()
    session._establish(AuthenticationResult(user_id, None, "موظف", frozenset(permissions), _VERIFIED),
                       int(session.generation))
    return session


class FakeBarcodes:
    """The automatic barcode allocation (BarcodeRepository.allocate_barcode_value) without a database."""
    def __init__(self, connection):
        self.store = connection.store

    def allocate_barcode_value(self, for_date):
        self.store.auto_barcodes = getattr(self.store, "auto_barcodes", 0) + 1
        return f"{self.store.auto_barcodes:02d}{for_date.strftime('%y%m%d')}"


def service_for(store, session=None, authorization=None):
    @contextmanager
    def transaction():
        store.reads += 1
        yield type("T", (), {"connection": type("C", (), {"store": store})()})()
    session = session or session_for()
    return InventoryService(transaction_factory=transaction, repository=Repo, session=session,
                            authorization=authorization or FakeAuthorization(session), barcode_repository=FakeBarcodes)


MIXED = [
    piece(1, K21, "خاتم", "3.600", barcode="01261001", size="55"),
    piece(2, K21, "خاتم", "4.125", barcode="02261001"),
    piece(3, K21, "سلسلة", "12.005", stock_class="OLD_STOCK", barcode="03261001"),
    piece(4, K18, "دبلة", "4.500", barcode="04261001"),
    piece(5, K24, "سبيكة", "31.103", barcode="05261001"),
    piece(6, K21, "خاتم", "9.999", status="SOLD", barcode="06261001"),
    piece(7, K21, "خاتم", "8.000", status="RETURNED_PENDING", barcode="07261001"),
    piece(8, K18, "دبلة", "2.000", status="CONVERTED_TO_OLD_GOLD", barcode="08261001"),
    piece(9, K21, "أسورة", "5.000", status="REMOVED", barcode="09261001"),
    piece(10, K18, "دبلة", "1.001"),                                    # current, no barcode yet
]


class CurrentStockPredicateTests(unittest.TestCase):
    def test_one_predicate_defines_current_stock(self):
        self.assertEqual(CURRENT_STOCK, "ii.inventory_status = 'IN_STOCK'")
        from pathlib import Path
        sql = Path("repositories/inventory_repository.py").read_text(encoding="utf-8")
        self.assertEqual(sql.count("inventory_status = 'IN_STOCK'"), 1)          # never re-typed per query
        self.assertGreaterEqual(sql.count("CURRENT_STOCK"), 5)


class ManualEntryTests(unittest.TestCase):
    def test_created_without_an_employee_and_actor_kept(self):
        store = Store()
        new = service_for(store).create_inventory_item(item_type_id=1, karat_id=1, weight_grams="3.600",
                                                       stock_classification="NEW_STOCK")
        self.assertEqual(new, 41)
        self.assertIsNone(store.created[0]["entered_by_employee_id"])          # no fake / logged-in employee
        self.assertEqual(store.created[0]["created_by_user_id"], 9)            # the AUTHENTICATED session actor
        self.assertIsNone(store.movements[0]["performed_by_employee_id"])
        self.assertEqual(store.movements[0]["performed_by_user_id"], 9)

    def test_a_piece_entered_without_a_barcode_gets_the_next_automatic_one_and_a_typed_one_is_kept(self):
        store = Store()
        service = service_for(store)
        service.create_inventory_item(item_type_id=1, karat_id=1, weight_grams="3.600", stock_classification="NEW_STOCK")
        service.create_inventory_item(item_type_id=1, karat_id=1, weight_grams="2.000", stock_classification="NEW_STOCK", barcode="MY-77")
        self.assertEqual([a["barcode_value"] for a in store.assigned], [store.assigned[0]["barcode_value"], "MY-77"])
        self.assertRegex(store.assigned[0]["barcode_value"], r"^01\d{6}$")                         # NNYYMMDD, the canonical sequence

    def test_a_piece_in_stock_can_have_its_type_and_karat_corrected_with_a_reason_and_an_audit(self):
        store = Store([dict(MIXED[0], inventory_item_id=7, inventory_status="IN_STOCK")])
        service = service_for(store)
        with self.assertRaises(ValueError):
            service.correct_item(7, 2, 2, "   ")                                           # the reason is mandatory
        with self.assertRaises(ValueError):
            service.correct_item(7, store.pieces[0]["item_type_id"], store.pieces[0]["karat_id"], "لا تغيير")
        service.correct_item(7, 99, 3, "اتدخلت بعيار غلط")
        self.assertEqual((store.pieces[0]["item_type_id"], store.pieces[0]["karat_id"]), (99, 3))
        audit = store.audits[0]
        self.assertEqual((audit["item_id"], audit["metadata"]["reason"], audit["metadata"]["after"]), (7, "اتدخلت بعيار غلط", {"item_type_id": 99, "karat_id": 3}))

    def test_a_spoiled_piece_in_stock_becomes_scrap_of_the_same_karat_and_weight_with_one_audit(self):
        piece = dict(MIXED[0], inventory_item_id=7, inventory_status="IN_STOCK")
        store = Store([piece])
        service = service_for(store, session_for(frozenset({"APP_LOGIN", "INVENTORY_MANAGE", "OLD_GOLD_MANAGE"})))
        with self.assertRaises(ValueError):
            service.convert_item_to_scrap(7, "  ")                                              # the reason is mandatory
        service.convert_item_to_scrap(7, "القطعة بايظة")
        self.assertEqual(piece["inventory_status"], "SCRAPPED")
        self.assertEqual((store.scrap_in[0][0], store.scrap_in[0][1]), (piece["karat_id"], piece["weight_grams"]))
        self.assertEqual((len(store.movements), store.audits[0]["event_type"], store.audits[0]["metadata"]["old_gold_movement_id"]), (1, "INVENTORY_ITEM_TO_SCRAP", 601))
        with self.assertRaises(ValueError):
            service.convert_item_to_scrap(7, "تاني")                                            # no longer in stock: never twice
        self.assertEqual(len(store.scrap_in), 1)

    def test_converting_to_scrap_needs_both_the_stock_and_the_scrap_rights(self):
        store = Store([dict(MIXED[0], inventory_item_id=7, inventory_status="IN_STOCK")])
        with self.assertRaises(PermissionError):
            service_for(store).convert_item_to_scrap(7, "x")                                     # INVENTORY_MANAGE only
        with self.assertRaises(PermissionError):
            service_for(store, session_for(frozenset({"APP_LOGIN", "OLD_GOLD_MANAGE"}))).convert_item_to_scrap(7, "x")
        self.assertFalse(hasattr(store, "scrap_in"))

    def test_a_sold_or_missing_piece_is_never_corrected(self):
        store = Store([dict(MIXED[0], inventory_item_id=8, inventory_status="SOLD")])
        service = service_for(store)
        for item_id in (8, 404):
            with self.assertRaises(ValueError):
                service.correct_item(item_id, 99, 3, "x")
        self.assertFalse(hasattr(store, "audits"))

    def test_other_required_fields_still_refused(self):
        service = service_for(Store())
        for values, message in (({"karat_id": 1, "weight_grams": "1"}, "Item type is required."),
                                ({"item_type_id": 1, "weight_grams": "1"}, "Karat is required."),
                                ({"item_type_id": 1, "karat_id": 1}, "Weight is required."),
                                ({"item_type_id": 1, "karat_id": 1, "weight_grams": "0"},
                                 "Weight must be greater than zero.")):
            with self.subTest(message), self.assertRaisesRegex(ValueError, message):
                service.create_inventory_item(stock_classification="NEW_STOCK", **values)

    def test_an_explicit_historical_employee_is_still_validated(self):
        store = Store()
        service_for(store).create_inventory_item(employee_id=5, item_type_id=1, karat_id=1, weight_grams="1",
                                                 stock_classification="NEW_STOCK")
        self.assertEqual(store.created[0]["entered_by_employee_id"], 5)
        with self.assertRaisesRegex(ValueError, "Employee is not active"):
            service_for(Store()).create_inventory_item(employee_id=6, item_type_id=1, karat_id=1,
                                                       weight_grams="1", stock_classification="NEW_STOCK")

    def test_viewmodel_slot_takes_no_employee(self):
        from app.viewmodels.inventory_viewmodel import InventoryViewModel
        store = Store()
        with patch("app.viewmodels.inventory_viewmodel.InventoryService",
                   lambda: type("S", (), {"get_stock_items": lambda s, **k: [], "get_stock_summary": lambda s: {},
                                          "create_inventory_item": service_for(store).create_inventory_item})()):
            vm = InventoryViewModel()
        self.assertTrue(vm.createInventoryItem(1, 1, 0, 0, "3.600", "55", "NEW_STOCK", "", ""))
        self.assertIsNone(store.created[0]["entered_by_employee_id"])


class StockBalanceTests(unittest.TestCase):
    def balance(self, pieces=MIXED, stock_class=None):
        return service_for(Store(pieces)).get_stock_balance(stock_class)

    def test_only_current_stock_grouped_by_karat_then_item_type(self):
        b = self.balance()
        self.assertEqual([k["karat_value"] for k in b["karats"]], [21, 18, 24])      # dynamic, ordered
        k21 = b["karats"][0]
        self.assertEqual([(r["item_type_name"], r["piece_count"], r["weight_grams"]) for r in k21["item_types"]],
                         [("خاتم", 2, D("7.725")), ("سلسلة", 1, D("12.005"))])       # SOLD/RETURNED/REMOVED out
        self.assertEqual((k21["piece_count"], k21["weight_grams"]), (3, D("19.730")))
        self.assertEqual((b["karats"][1]["piece_count"], b["karats"][1]["weight_grams"]), (2, D("5.501")))
        self.assertEqual((b["piece_count"], b["weight_grams"]), (6, D("56.334")))
        self.assertIsInstance(b["weight_grams"], D)
        self.assertNotIn("أسورة", [r["item_type_name"] for k in b["karats"] for r in k["item_types"]])

    def test_new_and_old_filters(self):
        new = self.balance(stock_class="NEW_STOCK")
        old = self.balance(stock_class="OLD_STOCK")
        self.assertEqual((new["piece_count"], new["weight_grams"]), (5, D("44.329")))
        self.assertEqual((old["piece_count"], old["weight_grams"]), (1, D("12.005")))
        self.assertEqual([k["karat_value"] for k in old["karats"]], [21])           # empty karats omitted
        self.assertEqual(self.balance(stock_class="ALL")["piece_count"], 6)
        with self.assertRaises(ValueError):
            self.balance(stock_class="SOLD")

    def test_empty_inventory(self):
        b = self.balance(pieces=[])
        self.assertEqual((b["karats"], b["piece_count"], b["weight_grams"]), ([], 0, D("0.000")))

    def test_viewmodel_display(self):
        vm = StockBalanceViewModel(service=service_for(Store(MIXED)))
        self.assertFalse(vm.loaded)                                    # nothing loaded until opened
        vm.refresh()
        self.assertEqual((vm.totalCount, vm.totalWeightText, vm.isEmpty), (6, "56.334", False))
        self.assertEqual(vm.karats[0]["title"], "عيار 21")
        self.assertEqual(vm.karats[0]["rows"][1], {"name": "سلسلة", "count": 1, "weight_text": "12.005"})
        vm.setFilter("OLD_STOCK")
        self.assertEqual((vm.filter, vm.totalCount, [k["title"] for k in vm.karats]), ("OLD_STOCK", 1, ["عيار 21"]))
        big = StockBalanceViewModel(service=service_for(Store([piece(i, K21, "خاتم", "1234.567") for i in range(3)])))
        big.refresh()
        self.assertEqual(big.totalWeightText, "3,703.701")
        empty = StockBalanceViewModel(service=service_for(Store()))
        empty.refresh()
        self.assertTrue(empty.isEmpty)


class StocktakeTests(unittest.TestCase):
    def setUp(self):
        self.store = Store(MIXED)
        self.vm = StocktakeViewModel(service=service_for(self.store))
        self.vm.start()

    def test_session_starts_with_current_stock_on_the_right(self):
        self.assertEqual((self.vm.remainingCount, self.vm.scannedCount), (6, 0))
        self.assertEqual((self.vm.remainingWeightText, self.vm.scannedWeightText), ("56.334", "0.000"))
        self.assertEqual(self.vm.withoutBarcodeCount, 1)
        self.assertEqual(self.vm.remainingRows[0]["barcode"], "01261001")

    def test_scan_moves_exactly_one_piece_and_totals_change_exactly(self):
        reads = self.store.reads
        self.assertEqual(self.vm.scan("02261001"), "OK")
        self.assertEqual((self.vm.remainingCount, self.vm.scannedCount), (5, 1))
        self.assertEqual((self.vm.remainingWeightText, self.vm.scannedWeightText), ("52.209", "4.125"))
        self.assertEqual(self.vm.scannedRows[0]["barcode"], "02261001")
        self.assertNotIn("02261001", [r["barcode"] for r in self.vm.remainingRows])
        self.assertEqual(self.store.reads, reads)                         # no reload per scan
        self.assertEqual(self.vm.scan("٠١٢٦١٠٠١"), "OK")                   # Arabic-Indic digits = same barcode
        self.assertEqual(self.vm.scannedRows[0]["barcode"], "01261001")    # newest first

    def test_duplicate_unknown_and_non_current_change_nothing(self):
        self.vm.scan("01261001")
        state = (self.vm.remainingCount, self.vm.scannedCount, self.vm.remainingWeightText, self.vm.scannedWeightText)
        self.assertEqual(self.vm.scan("01261001"), "DUPLICATE")
        self.assertEqual(self.vm.message, DUPLICATE)
        for barcode in ("99999999", "06261001", "07261001", "08261001"):     # unknown, SOLD, RETURNED, CONVERTED
            with self.subTest(barcode):
                self.assertEqual(self.vm.scan(barcode), "UNKNOWN")
                self.assertEqual(self.vm.message, UNKNOWN)
        self.assertEqual(self.vm.scan("ab#c"), "INVALID")
        self.assertEqual((self.vm.remainingCount, self.vm.scannedCount, self.vm.remainingWeightText,
                          self.vm.scannedWeightText), state)
        self.assertEqual(self.store.created + self.store.movements, [])      # never a stock write

    def test_filters_and_scope_change_need_confirmation(self):
        k18 = next(o for o in self.vm.karatOptions if o["name"] == "عيار 18")
        self.assertTrue(self.vm.applyFilters(0, int(k18["id"]), False))
        self.assertEqual((self.vm.remainingCount, self.vm.remainingWeightText), (2, "5.501"))
        self.assertEqual(self.vm.scan("01261001"), "UNKNOWN")               # outside the scope
        self.vm.scan("04261001")
        self.assertFalse(self.vm.applyFilters(0, 0, False))                # scans exist: refused
        self.assertEqual(self.vm.scannedCount, 1)
        self.assertTrue(self.vm.applyFilters(0, 0, True))                  # confirmed: a new session
        self.assertEqual((self.vm.remainingCount, self.vm.scannedCount), (6, 0))
        ring = next(o for o in self.vm.itemTypeOptions if o["name"] == "خاتم")
        self.vm.applyFilters(int(ring["id"]), 0, False)
        self.assertEqual((self.vm.remainingCount, self.vm.remainingWeightText), (2, "7.725"))

    def test_reset_restores_the_full_set_and_keeps_filters(self):
        k21 = next(o for o in self.vm.karatOptions if o["name"] == "عيار 21")
        self.vm.applyFilters(0, int(k21["id"]), False)
        self.vm.scan("01261001")
        self.assertFalse(self.vm.reset(False))
        self.assertTrue(self.vm.reset(True))
        self.assertEqual((self.vm.remainingCount, self.vm.scannedCount, self.vm.karatId), (3, 0, int(k21["id"])))

    def test_per_karat_totals_and_empty_inventory(self):
        self.vm.scan("04261001")
        totals = {t["title"]: t for t in self.vm.karatTotals}
        self.assertEqual(list(totals), ["عيار 21", "عيار 18", "عيار 24"])
        self.assertEqual((totals["عيار 18"]["remaining_count"], totals["عيار 18"]["remaining_weight_text"],
                          totals["عيار 18"]["scanned_count"], totals["عيار 18"]["scanned_weight_text"]),
                         (1, "1.001", 1, "4.500"))
        empty = StocktakeViewModel(service=service_for(Store()))
        empty.start()
        self.assertEqual((empty.remainingCount, empty.scannedCount, empty.karatTotals), (0, 0, []))


class BarcodePrintRuleTests(unittest.TestCase):
    """A label is printed only for a piece that already has its stored barcode — always the SAME one.
    A piece marked بدون باركود is refused before any label, queue entry or barcode allocation."""

    def make(self, items):
        from services import barcode_print_service as module
        calls = {"allocated": 0, "assigned": 0, "jobs": 0}

        class FakeRepo:
            def __init__(self, connection):
                pass

            def get_items_for_print(self, ids):
                return [dict(i) for i in items if i["inventory_item_id"] in ids]

            def allocate_barcode_value(self, today):
                calls["allocated"] += 1
                return "99261001"

            def assign_barcode(self, **kw):
                calls["assigned"] += 1

        @contextmanager
        def transaction():
            yield type("T", (), {"connection": None})()

        patches = (patch.object(module, "DatabaseTransaction", transaction), patch.object(module, "BarcodeRepository", FakeRepo))
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        service = module.BarcodePrintService.__new__(module.BarcodePrintService)
        service._create_print_job = lambda **kw: calls.__setitem__("jobs", calls["jobs"] + 1) or (1, "B1", [])
        return service, calls

    ITEMS = [{"inventory_item_id": 7, "barcode_value": "01261001"}, {"inventory_item_id": 8, "barcode_value": None}]

    def test_queue_with_a_barcode_less_piece_is_refused(self):
        service, calls = self.make(self.ITEMS)
        with self.assertRaises(ValueError) as refused:
            service.get_queue([7, 8])
        self.assertIn("بدون باركود", str(refused.exception))
        self.assertIn("8", str(refused.exception))

    def test_print_refuses_without_allocating_a_job_or_a_barcode(self):
        service, calls = self.make(self.ITEMS)
        with self.assertRaises(ValueError):
            service.print_items([7, 8])
        self.assertEqual(calls, {"allocated": 0, "assigned": 0, "jobs": 0})
        self.assertEqual(self.ITEMS[1]["barcode_value"], None)

    def test_barcoded_piece_queues_and_keeps_its_stored_barcode(self):
        service, calls = self.make(self.ITEMS)
        queue = service.get_queue([7])
        self.assertEqual((queue["item_count"], queue["existing_barcode_count"], queue["missing_barcode_count"]),
                         (1, 1, 0))
        self.assertEqual(queue["items"][0]["barcode_value"], "01261001")
        items, assigned, new_ids = service._ensure_barcodes([7])
        self.assertEqual((assigned, new_ids, calls["allocated"], items[0]["barcode_value"]),
                         (0, set(), 0, "01261001"))


if __name__ == "__main__":
    unittest.main()
