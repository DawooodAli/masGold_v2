"""Stocktake write-off: the approval service (fake repository, no database) and the static contract of migration 073."""
from contextlib import contextmanager
from decimal import Decimal as D
import unittest

from services.stocktake_adjust_service import StocktakeAdjustService, StocktakeError
from tests import migration_sql as sql
from tests.auth_fakes import FakeAuthorization
from tests.market_trip_fakes import session_for

MANAGER = frozenset({"APP_LOGIN", "STOCKTAKE_ADJUST", "OLD_GOLD_MANAGE"})


class Store:
    def __init__(self):
        self.pieces = {1: ("21", "5.000"), 2: ("21", "6.250"), 3: ("18", "4.000"), 4: ("18", "3.500")}
        self.adjustments, self.removed, self.movements, self.items, self.audits = {}, [], [], [], []


class Repo:
    store = None

    def __init__(self, connection=None):
        pass

    def now(self):
        return "2026-10-22 10:00:00"

    def has_schema(self):
        return True

    def adjustment_by_key(self, key):
        return self.store.adjustments.get(key)

    def scope_pieces(self, item_type_id, karat_id, factory_id=None):
        return [{"id": i, "karat_id": 2 if k == "21" else 1, "karat_value": int(k), "weight_grams": D(w), "stock_classification": "NEW_STOCK", "barcode_value": f"B{i}"}
                for i, (k, w) in sorted(self.store.pieces.items()) if i not in self.store.removed and (karat_id is None or karat_id == (2 if k == "21" else 1))]

    def pieces_by_ids(self, ids):
        return [r for r in self.scope_pieces(None, None) if r["id"] in set(ids)]

    def movement_type_id(self, code):
        return 9

    def insert_adjustment(self, **kw):
        n = len(self.store.adjustments) + 1
        self.store.adjustments[kw["request_key"]] = {"id": n, "code": f"SK{n:06d}", "request_hash": kw["request_hash"], "written_off_count": kw["written_off"][0],
                                                      "written_off_grams": kw["written_off"][1], "detail": kw}
        return n, f"SK{n:06d}"

    def remove_piece(self, item_id, user_id):
        if item_id in self.store.removed:
            return False
        self.store.removed.append(item_id)
        return True

    def insert_movement(self, **kw):
        self.store.movements.append(kw)
        return len(self.store.movements)

    def insert_item(self, **kw):
        self.store.items.append(kw)

    def audit(self, **kw):
        self.store.audits.append(kw)

    # restoring a piece an approved stocktake removed
    def lock_removed_piece(self, item_id):
        if item_id not in self.store.pieces:
            return None
        status = "REMOVED" if item_id in self.store.removed else "SOLD" if item_id in getattr(self.store, "sold", set()) else "IN_STOCK"
        return {"id": item_id, "inventory_status": status, "stock_classification": "NEW_STOCK", "weight_grams": D(self.store.pieces[item_id][1]),
                "write_offs": 1 if item_id in self.store.removed or item_id in getattr(self.store, "restored", []) else 0, "barcode_value": f"B{item_id}"}

    def restore_piece(self, item_id, user_id, from_status="REMOVED"):
        if from_status == "SOLD":
            if item_id not in getattr(self.store, "sold", set()):
                return False
            self.store.sold.remove(item_id)
            self.store.__dict__.setdefault("restocked", []).append(item_id)
            return True
        if item_id not in self.store.removed:
            return False
        self.store.removed.remove(item_id)
        self.store.__dict__.setdefault("restored", []).append(item_id)
        return True

    def current_sale_invoices(self, item_id):
        return getattr(self.store, "current_invoices", {}).get(item_id, [])

    # the Scrap a returning sold piece is taken from
    def karat(self, karat_id):
        return {1: {"id": 1, "karat_value": 18, "is_active": 1}, 2: {"id": 2, "karat_value": 21, "is_active": 1}, 3: {"id": 3, "karat_value": 24, "is_active": 0}}.get(int(karat_id))

    def lock_scrap_ledger(self, karat_id):
        return getattr(self.store, "scrap", {}).get(int(karat_id), (D("0.000"), D("0.000")))

    def insert_scrap_out_for_restock(self, *, item, karat_id, notes, user_id):
        self.store.__dict__.setdefault("scrap_out", []).append((karat_id, item["weight_grams"], notes))
        return 55

    def scrap_karats(self):
        return [{"karat_id": 2, "karat_value": 21, "karat_name": "عيار 21", "physical_grams": D("10.000"), "held_grams": D("1.500")}]

    def insert_restore_movement(self, **kw):
        self.store.movements.append(dict(kw, restore=True))

    def audit_restore(self, **kw):
        self.store.audits.append(dict(kw, code="RESTORE"))


class ApprovalTests(unittest.TestCase):
    def build(self, permissions=MANAGER):
        self.store = Store()
        Repo.store = self.store
        self.session = session_for(permissions)

        @contextmanager
        def transaction():
            yield object()
        return StocktakeAdjustService(self.session, transaction_factory=transaction, repository=Repo, authorization=FakeAuthorization(self.session))

    def call(self, service, scanned=(1, 3), remaining=(2, 4), reason="MISSING", note="", confirmed=True, key="key-00000001", karat=0):
        return service.approve(0, karat, [str(i) for i in scanned], [str(i) for i in remaining], reason, note, confirmed, key)

    def test_the_missing_pieces_leave_the_stock_and_the_reconciliation_is_stored(self):
        service = self.build()
        result = self.call(service)
        self.assertEqual((result["code"], result["written_off_count"], result["written_off_grams"]), ("SK000001", 2, D("9.750")))
        self.assertEqual(sorted(self.store.removed), [2, 4])
        self.assertEqual([m["weight"] for m in self.store.movements], [D("6.250"), D("3.500")])
        detail = self.store.adjustments["key-00000001"]["detail"]
        self.assertEqual((detail["expected"], detail["found"], detail["written_off"]), ([4, D("18.750")], [2, D("9.000")], [2, D("9.750")]))
        self.assertEqual(detail["summary"]["21"]["off"], [1, "6.250"])
        self.assertEqual([a["code"] for a in self.store.audits], ["SK000001"])

    def test_a_piece_the_stocktake_removed_goes_back_to_the_stock_with_a_reason_and_never_twice(self):
        service = self.build()
        self.call(service)
        with self.assertRaises(StocktakeError) as refused:
            service.restore_piece("2", "  ")
        self.assertEqual(refused.exception.code, "REASON_REQUIRED")
        result = service.restore_piece("2", "لقيتها موجودة")
        self.assertEqual((result["restored"], sorted(self.store.removed)), (True, [4]))
        self.assertEqual([m.get("restore") for m in self.store.movements][-1], True)
        self.assertEqual(self.store.audits[-1]["metadata"]["reason"], "لقيتها موجودة")
        with self.assertRaises(StocktakeError) as again:
            service.restore_piece("2", "تاني")                                                  # already back in stock
        self.assertEqual(again.exception.code, "NOT_REMOVED_BY_STOCKTAKE")
        with self.assertRaises(StocktakeError) as never:
            service.restore_piece("1", "x")                                                     # never removed by a stocktake
        self.assertEqual(never.exception.code, "NOT_REMOVED_BY_STOCKTAKE")
        with self.assertRaises(StocktakeError):
            service.restore_piece("99", "x")

    def test_a_piece_sold_on_an_old_invoice_comes_back_as_the_same_piece_with_the_same_barcode(self):
        service = self.build()
        self.store.sold = {3}
        self.store.scrap = {1: (D("10.000"), D("0.000"))}                                       # 18K Scrap, the piece weighs 4.000
        with self.assertRaises(StocktakeError) as no_karat:
            service.restore_piece("3", "العميل رجّعها")                                          # the Scrap karat is mandatory
        self.assertEqual(no_karat.exception.code, "SCRAP_KARAT_REQUIRED")
        self.assertEqual(self.store.sold, {3})
        result = service.restore_piece("3", "العميل رجّعها", "1")
        self.assertEqual((result["restored"], self.store.sold, self.store.restocked, result["scrap_movement_id"]), (True, set(), [3], 55))
        self.assertEqual(self.store.scrap_out[0][:2], (1, D("4.000")))                           # exactly the piece's grams leave the chosen Scrap karat
        movement = self.store.movements[-1]
        self.assertEqual((movement["from_status"], movement["reference_type"], movement["restore"]), ("SOLD", "PIECE_RESTOCK", True))
        self.assertIn("بنفس الباركود", movement["reason"])
        self.assertEqual((self.store.audits[-1]["metadata"]["barcode"], self.store.audits[-1]["metadata"]["from_status"]), ("B3", "SOLD"))
        with self.assertRaises(StocktakeError) as again:
            service.restore_piece("3", "تاني", "1")                                             # it is in stock now
        self.assertEqual(again.exception.code, "NOT_REMOVED_BY_STOCKTAKE")

    def test_the_scrap_must_cover_the_piece_and_the_karat_must_be_active(self):
        service = self.build()
        self.store.sold = {3}
        self.store.scrap = {1: (D("5.000"), D("1.500"))}                                        # available 3.500 < 4.000
        with self.assertRaises(StocktakeError) as short:
            service.restore_piece("3", "x", "1")
        self.assertEqual(short.exception.code, "SCRAP_SHORTAGE")
        with self.assertRaises(StocktakeError) as inactive:
            service.restore_piece("3", "x", "3")
        self.assertEqual(inactive.exception.code, "KARAT_INVALID")
        self.assertEqual((self.store.sold, getattr(self.store, "scrap_out", [])), ({3}, []))

    def test_returning_a_sold_piece_needs_the_scrap_right_too_and_the_karats_list_shows_the_available_grams(self):
        service = self.build(frozenset({"APP_LOGIN", "STOCKTAKE_ADJUST"}))
        self.store.sold = {3}
        self.store.scrap = {1: (D("10.000"), D("0.000"))}
        with self.assertRaises(PermissionError):
            service.restore_piece("3", "x", "1")
        self.assertIn("المتاح 8.500", service.scrap_karats()[0]["text"])

    def test_a_piece_sold_on_a_current_invoice_is_refused_and_sent_to_the_return(self):
        service = self.build()
        self.store.sold = {3}
        self.store.current_invoices = {3: ["INV000123"]}
        with self.assertRaises(StocktakeError) as refused:
            service.restore_piece("3", "x")
        self.assertEqual(refused.exception.code, "USE_RETURN")
        self.assertIn("INV000123", str(refused.exception))
        self.assertEqual(self.store.sold, {3})

    def test_restoring_needs_the_stocktake_right(self):
        service = self.build(frozenset({"APP_LOGIN"}))
        with self.assertRaises(PermissionError):
            service.restore_piece("2", "x")

    def test_an_exact_replay_returns_the_original_and_a_different_payload_conflicts(self):
        service = self.build()
        self.call(service)
        again = self.call(service)
        self.assertTrue(again["replayed"])
        self.assertEqual(len(self.store.removed), 2)
        with self.assertRaises(StocktakeError) as raised:
            self.call(service, scanned=(1, 3, 2), remaining=(4,))
        self.assertEqual(raised.exception.code, "REQUEST_CONFLICT")

    def test_a_changed_stock_or_a_bad_request_writes_nothing(self):
        service = self.build()
        for kwargs, code in (({"scanned": (1,), "remaining": (2, 4)}, "STOCK_CHANGED"),            # piece 3 is missing from both lists
                             ({"scanned": (1, 3), "remaining": (2, 4, 9)}, "STOCK_CHANGED"),         # an unknown piece
                             ({"scanned": (), "remaining": (1, 2, 3, 4)}, "NOTHING_FOUND"),
                             ({"remaining": ()}, "NOTHING_TO_WRITE_OFF"),
                             ({"scanned": (1, 2), "remaining": (2, 3, 4)}, "IDS_INVALID"),
                             ({"reason": "NONE"}, "REASON_REQUIRED"),
                             ({"reason": "OTHER", "note": " "}, "NOTE_REQUIRED"),
                             ({"confirmed": False}, "CONFIRM_REQUIRED"),
                             ({"key": "x"}, "REQUEST_KEY_INVALID")):
            with self.assertRaises(StocktakeError, msg=str(kwargs)) as raised:
                self.call(service, **kwargs)
            self.assertEqual(raised.exception.code, code, kwargs)
        self.assertEqual((self.store.removed, self.store.adjustments), ([], {}))

    def test_single_pieces_can_be_written_off_without_a_count(self):
        service = self.build()
        result = service.write_off_pieces(["2"], "SOLD_OUTSIDE", "", True, "key-00000009")
        self.assertEqual((result["written_off_count"], result["written_off_grams"], self.store.removed), (1, D("6.250"), [2]))
        detail = self.store.adjustments["key-00000009"]["detail"]
        self.assertEqual((detail["expected"], detail["found"]), ([1, D("6.250")], [0, D("0.000")]))
        with self.assertRaises(StocktakeError) as raised:                                        # already out of the stock
            service.write_off_pieces(["2"], "MISSING", "", True, "key-00000010")
        self.assertEqual(raised.exception.code, "STOCK_CHANGED")
        with self.assertRaises(StocktakeError):
            service.write_off_pieces([], "MISSING", "", True, "key-00000011")

    def test_a_whole_count_never_writes_off_a_piece_without_a_barcode(self):
        service = self.build()
        original = Repo.scope_pieces

        def without_barcode(self, item_type_id, karat_id, factory_id=None):
            return [dict(r, barcode_value=None) if r["id"] == 4 else r for r in original(self, item_type_id, karat_id)]
        Repo.scope_pieces = without_barcode
        try:
            with self.assertRaises(StocktakeError) as raised:
                self.call(service)
        finally:
            Repo.scope_pieces = original
        self.assertEqual(raised.exception.code, "PIECE_WITHOUT_BARCODE")
        self.assertEqual(self.store.removed, [])

    def test_a_scoped_count_only_covers_its_karat(self):
        service = self.build()
        result = self.call(service, scanned=(1,), remaining=(2,), karat=2)
        self.assertEqual(result["written_off_count"], 1)
        self.assertEqual(self.store.removed, [2])

    def test_only_the_stocktake_right_may_approve(self):
        service = self.build(frozenset({"APP_LOGIN"}))
        with self.assertRaises(PermissionError):
            self.call(service)
        self.assertEqual(self.store.removed, [])
        self.assertFalse(service.can_adjust())


class MigrationContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.forward = sql.read("073_stocktake_write_off.sql")
        cls.rollback = sql.read("073_stocktake_write_off_rollback.sql", sql.ROLLBACK_DIR)

    def test_the_tables_the_right_and_the_guards(self):
        for needle in ("MIGRATION_073_REFUSED_missing_072", "CREATE TABLE IF NOT EXISTS stocktake_adjustments", "expected_count = found_count + written_off_count",
                       "UNIQUE KEY uq_stocktake_item_once (inventory_item_id)", "STOCKTAKE_WRITE_OFF", "'STOCKTAKE_ADJUST'", "r.code IN ('OWNER','MANAGER')",
                       "VALUES (73,"):
            self.assertIn(needle, self.forward)
        self.assertNotIn("TRIGGER", self.forward.upper())

    def test_the_rollback_refuses_while_any_approval_exists(self):
        for needle in ("ROLLBACK_073_REFUSED_stocktake_records_exist", "FROM stocktake_adjustments", "version > 73", "DELETE FROM schema_migrations WHERE version = 73"):
            self.assertIn(needle, self.rollback)
        self.assertLess(self.rollback.index("ROLLBACK_073_REFUSED"), self.rollback.index("DROP TABLE"))


if __name__ == "__main__":
    unittest.main()
