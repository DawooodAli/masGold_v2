"""Supplier-to-supplier gold transfer (migration 108, GTxxxxxx): the real service over a small in-memory ledger (no database). The SQL is covered by the static schema test."""
from contextlib import contextmanager
from datetime import date, datetime
from decimal import Decimal as D
from types import SimpleNamespace
import unittest
import uuid

from mysql.connector import IntegrityError

from repositories.market_trip_repository import entry_code
from services.supplier_gold_transfer_service import SupplierGoldTransferService
from services.supplier_rules import SupplierRuleError
from tests.auth_fakes import FakeAuthorization
from tests.market_trip_fakes import session_for

OWNER = frozenset({"APP_LOGIN", "SUPPLIER_VIEW", "SUPPLIER_GOLD_SETTLE", "SUPPLIER_GOLD_SETTLE_REVERSE"})
MANAGER = frozenset({"APP_LOGIN", "SUPPLIER_VIEW", "SUPPLIER_GOLD_SETTLE"})


def key():
    return str(uuid.uuid4())


class Store:
    """The tables the service touches. Suppliers: 5 المتحدة (21K, holds 265.100 g for the shop), 2 مكرم (21K, the shop owes 92.740 g), 3 ورشة (18K, the shop owes 50 g)."""
    def __init__(self):
        self.suppliers = {5: self.party(5, "المتحدة", 1, 21), 2: self.party(2, "مكرم", 1, 21), 3: self.party(3, "ورشة", 2, 18),
                          4: dict(self.party(4, "مسوقجي فقط", None, None), is_supplier=0, is_intermediary=1)}
        self.moves, self.transfers, self.audits, self.locks, self.foreign_keys = [], [], [], [], set()
        self.markers_schema, self.open_trip = True, 10                                    # 109: the custody log markers; trip 10 is the newest OPEN legacy custody
        self.entries, self.links, self.order = [], [], []                                 # the log entries written, their transfer links, and the lock / write order
        self.types = {code: i for i, code in enumerate(("ACCOUNT_TRANSFER_OUT", "ACCOUNT_TRANSFER_IN", "ACCOUNT_TRANSFER_OUT_REVERSAL", "ACCOUNT_TRANSFER_IN_REVERSAL"), 1)}
        self.now = datetime(2026, 11, 24, 12, 0, 0)
        for supplier_id, karat, weight in ((5, 1, "-265.100"), (2, 1, "92.740"), (3, 2, "50.000")):
            self.moves.append({"supplier_id": supplier_id, "karat_id": karat, "weight": D(weight), "source_type": "PURCHASE_INVOICE", "source_id": 1})

    @staticmethod
    def party(supplier_id, name, karat_id, karat_value):
        return {"id": supplier_id, "name": name, "karat_id": karat_id, "karat_value": karat_value, "is_active": 1, "is_supplier": 1, "is_intermediary": 0}

    def balance(self, supplier_id, karat_id):
        return sum((m["weight"] for m in self.moves if m["supplier_id"] == supplier_id and m["karat_id"] == karat_id), D("0.000"))


class FakeRepo:
    store = None

    def __init__(self, connection=None):
        pass

    def has_schema(self):
        return True

    def has_marker_schema(self):
        return self.store.markers_schema

    def latest_open_trip(self):
        return self.store.open_trip

    def link_entry(self, transfer_id, entry_id):
        self.store.links.append((int(transfer_id), int(entry_id)))

    def get_supplier(self, supplier_id, lock=False):
        if lock:
            self.store.locks.append(int(supplier_id))
            self.store.order.append(("supplier", int(supplier_id)))
        row = self.store.suppliers.get(int(supplier_id))
        return None if row is None else dict(row)

    def source_candidates(self, exclude_supplier_id):
        out = []
        for s in self.store.suppliers.values():
            if s["id"] != int(exclude_supplier_id) and s["is_supplier"] and s["karat_id"] is not None:
                held = -self.store.balance(s["id"], s["karat_id"])
                if held > 0:
                    out.append({"id": s["id"], "name": s["name"], "is_active": s["is_active"], "karat_id": s["karat_id"], "karat_value": s["karat_value"], "held": held})
        return out

    def karat_balance(self, supplier_id, karat_id):
        return self.store.balance(int(supplier_id), int(karat_id))

    def transfer(self, transfer_id, lock=False):
        row = next((t for t in self.store.transfers if t["id"] == int(transfer_id)), None)
        return None if row is None else dict(row, from_name=self.store.suppliers[row["from_supplier_id"]]["name"], to_name=self.store.suppliers[row["to_supplier_id"]]["name"])

    def by_request(self, k):
        return next((dict(t) for t in self.store.transfers if t["request_key"] == k), None)

    def reversal_of(self, transfer_id):
        return next((t for t in self.store.transfers if t["reverses_id"] == int(transfer_id)), None)

    def request_key_used_elsewhere(self, k):
        return k in self.store.foreign_keys

    def insert_transfer(self, *, source, recipient, from_weight, to_weight, analytical, difference, transfer_date, reference, notes, key, digest, actor, now, reverses_id=None, reason=None):
        if any(t["request_key"] == key for t in self.store.transfers):
            raise IntegrityError("1062 (23000): Duplicate entry for key 'supplier_gold_transfers.uq_sgt_request'", errno=1062)
        row = {"id": len(self.store.transfers) + 1, "from_supplier_id": int(source["id"]), "to_supplier_id": int(recipient["id"]), "from_karat_id": int(source["karat_id"]),
               "from_karat_value": int(source["karat_value"]), "to_karat_id": int(recipient["karat_id"]), "to_karat_value": int(recipient["karat_value"]),
               "from_weight_grams": from_weight, "to_weight_grams": to_weight, "to_analytical_grams": analytical, "rounding_difference_grams": difference,
               "transfer_date": transfer_date, "reference": reference, "notes": notes, "reverses_id": reverses_id, "reason": reason, "request_key": key, "request_hash": digest,
               "performed_by_user_id": actor.user_id}
        self.store.transfers.append(row)
        return row["id"]

    def gold_type_id(self, code):
        return self.store.types.get(code)

    def insert_gold_movement(self, *, supplier_id, type_id, karat_id, weight_delta, eq21_delta, source_id, reference, description, now, user_id):
        assert weight_delta != 0 and (weight_delta > 0) == (eq21_delta > 0)                       # chk_sgm_sign
        code = next(c for c, i in self.store.types.items() if i == type_id)
        self.store.moves.append({"supplier_id": int(supplier_id), "karat_id": int(karat_id), "weight": weight_delta, "eq21": eq21_delta, "source_type": "SUPPLIER_GOLD_TRANSFER",
                                 "source_id": int(source_id), "type": code, "reference": reference, "description": description})

    def db_now(self):
        return self.store.now

    def audit(self, **kwargs):
        self.store.audits.append(kwargs)


class FakeTrips:
    """The slice of MarketTripRepository the marker needs: lock a trip, number and insert a log entry."""
    store = None

    def __init__(self, connection=None):
        pass

    def trip(self, trip_id, lock=False):
        if lock:
            self.store.order.append(("trip", int(trip_id)))
        return {"id": int(trip_id), "status": "OPEN"}

    def next_entry_no(self, trip_id):
        return 1 + max([e["entry_no"] for e in self.store.entries if e["trip_id"] == int(trip_id)] or [0])

    def insert_entry(self, *, trip_id, entry_no, kind, entry_date, notes, key, digest, actor, now):
        assert all(e["request_key"] != key for e in self.store.entries)                      # uq_mte_request
        self.store.entries.append({"id": 500 + len(self.store.entries), "trip_id": int(trip_id), "entry_no": entry_no, "kind": kind, "date": entry_date, "notes": notes, "request_key": key})
        return self.store.entries[-1]["id"]


class Case(unittest.TestCase):
    def setUp(self):
        self.store = Store()
        FakeRepo.store = self.store
        FakeTrips.store = self.store

    def service(self, permissions=OWNER):
        session = session_for(permissions)

        @contextmanager
        def transaction():
            yield SimpleNamespace()
        return SupplierGoldTransferService(session, transaction_factory=transaction, repository=FakeRepo, trips=FakeTrips, authorization=FakeAuthorization(session))

    def move(self, service, source=5, recipient=2, weight="1", k=None, reference="", notes=""):
        return service.transfer(source, recipient, weight, date(2026, 11, 24), reference, notes, k or key())


class TransferTests(Case):
    def test_one_gram_leaves_almuttahida_and_reduces_what_the_shop_owes_makram(self):
        result = self.move(self.service(), weight="1")
        self.assertEqual((result["code"], result["transferred_weight"], result["credited_weight"], result["duplicate"]), ("GT000001", "1.000", "1.000", False))
        self.assertEqual(self.store.balance(5, 1), D("-264.100"))                          # holds 1 g less for the shop
        self.assertEqual(self.store.balance(2, 1), D("91.740"))                            # the shop owes 1 g less
        rows = [m for m in self.store.moves if m["source_type"] == "SUPPLIER_GOLD_TRANSFER"]
        self.assertEqual([(m["supplier_id"], m["weight"], m["type"], m["reference"]) for m in rows],
                         [(5, D("1.000"), "ACCOUNT_TRANSFER_OUT", "GT000001"), (2, D("-1.000"), "ACCOUNT_TRANSFER_IN", "GT000001")])
        self.assertEqual(self.store.audits[-1]["event_type"], "SUPPLIER_GOLD_TRANSFERRED")

    def test_the_shop_net_gold_position_does_not_move(self):
        before = sum(self.store.balance(s, k) * (21 if k == 1 else 18) for s, k in ((5, 1), (2, 1), (3, 2)))
        self.move(self.service(), weight="12.345")
        self.assertEqual(sum(self.store.balance(s, k) * (21 if k == 1 else 18) for s, k in ((5, 1), (2, 1), (3, 2))), before)

    def test_a_different_karat_recipient_is_credited_by_the_settlement_rule(self):
        # 10 g of 21K to an 18K supplier = 10 x 21 / 18 = 11.667 g (posted ROUND_HALF_UP at 0.001, the analytical value and the rounding difference are stored)
        result = self.move(self.service(), recipient=3, weight="10")
        self.assertEqual((result["credited_weight"], result["analytical"], result["rounding_difference"]), ("11.667", "11.666667", "0.000333"))
        self.assertEqual(self.store.balance(3, 2), D("50.000") - D("11.667"))
        self.assertEqual(self.store.balance(5, 1), D("-255.100"))

    def test_the_transfer_is_limited_to_what_the_source_holds_for_the_shop(self):
        with self.assertRaises(SupplierRuleError) as caught:
            self.move(self.service(), weight="265.101")
        self.assertEqual(caught.exception.code, "SOURCE_SHORT")
        self.move(self.service(), weight="265.100")                                        # exactly all of it is fine
        self.assertEqual(self.store.balance(5, 1), D("0.000"))
        with self.assertRaises(SupplierRuleError) as caught:
            self.move(self.service(), weight="0.001")
        self.assertEqual(caught.exception.code, "SOURCE_EMPTY")

    def test_a_supplier_the_shop_owes_cannot_be_a_source_and_the_parties_must_be_suppliers_with_a_karat(self):
        for source, recipient, code in ((2, 5, "SOURCE_EMPTY"), (5, 5, "SAME_SUPPLIER"), (4, 2, "SUPPLIER_ROLE_REQUIRED"), (5, 4, "SUPPLIER_ROLE_REQUIRED"), (99, 2, "SOURCE_NOT_FOUND"),
                                        (5, 99, "SUPPLIER_NOT_FOUND")):
            with self.assertRaises(SupplierRuleError, msg=str((source, recipient))) as caught:
                self.move(self.service(), source=source, recipient=recipient)
            self.assertEqual(caught.exception.code, code)
        self.store.suppliers[2]["karat_id"] = None
        with self.assertRaises(SupplierRuleError) as caught:
            self.move(self.service())
        self.assertEqual(caught.exception.code, "SUPPLIER_KARAT_MISSING")
        self.assertEqual(self.store.transfers, [])

    def test_the_weight_is_exact_positive_and_at_most_three_decimals(self):
        for weight in ("0", "-1", "", "abc", "1.2345"):
            with self.assertRaises(SupplierRuleError, msg=weight):
                self.move(self.service(), weight=weight)
        self.assertEqual(self.store.transfers, [])

    def test_both_suppliers_are_locked_in_ascending_id_order_whichever_direction(self):
        self.move(self.service(), source=5, recipient=2)
        self.assertEqual(self.store.locks, [2, 5])

    def test_the_right_is_required_and_rechecked_and_the_manager_may_post_but_not_reverse(self):
        with self.assertRaises(PermissionError):
            self.move(self.service({"APP_LOGIN", "SUPPLIER_VIEW"}))
        self.assertEqual(self.store.transfers, [])
        posted = self.move(self.service(MANAGER))
        with self.assertRaises(PermissionError):
            self.service(MANAGER).reverse(posted["id"], "خطأ", key())
        self.assertEqual(len(self.store.transfers), 1)

    def test_an_exact_replay_posts_once_and_a_different_payload_or_key_owner_conflicts(self):
        service, k = self.service(), key()
        first = self.move(service, weight="2", k=k)
        again = self.move(service, weight="2", k=k)
        self.assertTrue(again["duplicate"])
        self.assertEqual(first["id"], again["id"])
        self.assertEqual(len([m for m in self.store.moves if m["source_type"] == "SUPPLIER_GOLD_TRANSFER"]), 2)
        with self.assertRaises(SupplierRuleError) as caught:
            self.move(service, weight="3", k=k)
        self.assertEqual(caught.exception.code, "REQUEST_CONFLICT")
        other = key()
        self.store.foreign_keys.add(other)
        with self.assertRaises(SupplierRuleError) as caught:
            self.move(service, k=other)
        self.assertEqual(caught.exception.code, "REQUEST_CONFLICT")
        with self.assertRaises(SupplierRuleError):
            service.transfer(5, 2, "2", date(2026, 11, 24), "", "", "not-a-uuid")

    def test_the_sources_list_names_only_suppliers_that_hold_gold_for_the_shop(self):
        sources = self.service().sources(2)
        self.assertEqual([(s["supplier_id"], s["source_type"], s["karat_value"], s["available"], s["key"]) for s in sources], [("5", "SUPPLIER", 21, "265.100", "supplier:5")])
        self.assertIn("المتحدة", sources[0]["text"])
        self.assertIn("265.100", sources[0]["text"])
        self.assertEqual(self.service().sources(5), [])                                     # nobody else holds gold for the shop
        with self.assertRaises(PermissionError):
            self.service({"APP_LOGIN"}).sources(2)

    def test_the_context_rereads_the_source_balance(self):
        self.assertEqual(self.service().context(5, 2)["available"], D("265.100"))
        self.move(self.service(), weight="65.1")
        self.assertEqual(self.service().context(5, 2)["available"], D("200.000"))
        with self.assertRaises(SupplierRuleError):
            self.service().context(2, 5)


class CustodyLogTests(Case):
    """Owner 2026-11-24: the transfer is also visible in the market custody log, as a marker entry with no leg."""
    def test_a_transfer_writes_one_marker_in_the_newest_open_custody_and_never_a_custody_leg(self):
        result = self.move(self.service(), weight="1")
        self.assertEqual(result["custody_log"], entry_code(10, 1))                          # MT000010-1
        entry = self.store.entries[0]
        self.assertEqual((entry["trip_id"], entry["kind"], entry["date"]), (10, "SUP_XFER", date(2026, 11, 24)))
        self.assertIn("GT000001", entry["notes"])
        self.assertIn("المتحدة", entry["notes"])
        self.assertEqual(self.store.links, [(1, entry["id"])])

    def test_the_custody_is_locked_before_the_suppliers_and_the_marker_comes_with_the_same_transaction_inputs(self):
        self.move(self.service())
        self.assertEqual(self.store.order, [("trip", 10), ("supplier", 2), ("supplier", 5)])

    def test_the_marker_has_its_own_request_key_distinct_from_the_transfers(self):
        k = key()
        self.move(self.service(), k=k)
        self.assertNotEqual(self.store.entries[0]["request_key"], k)
        self.assertNotEqual(self.store.entries[0]["request_key"], self.store.transfers[0]["request_key"])

    def test_without_an_open_custody_or_below_109_no_marker_is_written_and_the_transfer_still_posts(self):
        self.store.open_trip = None
        self.assertIsNone(self.move(self.service())["custody_log"])
        self.store.markers_schema = False
        self.store.open_trip = 10
        self.assertIsNone(self.move(self.service())["custody_log"])
        self.assertEqual((self.store.entries, len(self.store.transfers)), ([], 2))

    def test_a_replay_writes_no_second_marker(self):
        service, k = self.service(), key()
        self.move(service, k=k)
        self.move(service, k=k)
        self.assertEqual(len(self.store.entries), 1)

    def test_a_refused_transfer_writes_no_marker(self):
        with self.assertRaises(SupplierRuleError):
            self.move(self.service(), weight="999")
        self.assertEqual((self.store.entries, self.store.links), ([], []))

    def test_the_reversal_writes_its_own_marker_with_the_reason(self):
        service = self.service()
        posted = self.move(service)
        reversed_ = service.reverse(posted["id"], "سُجِّل بالخطأ", key())
        self.assertEqual(reversed_["custody_log"], entry_code(10, 2))
        marker = self.store.entries[1]
        self.assertEqual(marker["kind"], "SUP_XFER_REV")
        self.assertIn("سُجِّل بالخطأ", marker["notes"])
        self.assertIn("GT000001", marker["notes"])
        self.assertEqual(self.store.order[-3:], [("trip", 10), ("supplier", 2), ("supplier", 5)])


class ReverseTests(Case):
    def test_a_reversal_posts_the_exact_opposite_and_restores_both_balances(self):
        posted = self.move(self.service(), recipient=3, weight="10")
        reversed_ = self.service().reverse(posted["id"], "سُجِّل بالخطأ", key())
        self.assertEqual((reversed_["code"], reversed_["reverses"], reversed_["duplicate"]), ("GT000002", posted["id"], False))
        self.assertEqual(self.store.balance(5, 1), D("-265.100"))
        self.assertEqual(self.store.balance(3, 2), D("50.000"))                              # the STORED credit 11.667 is restored, nothing recomputed
        types = [m["type"] for m in self.store.moves if m.get("source_id") == 2 and m["source_type"] == "SUPPLIER_GOLD_TRANSFER"]
        self.assertEqual(types, ["ACCOUNT_TRANSFER_OUT_REVERSAL", "ACCOUNT_TRANSFER_IN_REVERSAL"])
        self.assertEqual(self.store.audits[-1]["event_type"], "SUPPLIER_GOLD_TRANSFER_REVERSED")

    def test_an_original_is_reversed_once_and_a_reversal_is_never_reversed(self):
        service = self.service()
        posted = self.move(service)
        reversal = service.reverse(posted["id"], "خطأ", key())
        with self.assertRaises(SupplierRuleError) as caught:
            service.reverse(posted["id"], "مرة أخرى", key())
        self.assertEqual(caught.exception.code, "ALREADY_REVERSED")
        with self.assertRaises(SupplierRuleError) as caught:
            service.reverse(reversal["id"], "عكس العكس", key())
        self.assertEqual(caught.exception.code, "TRANSFER_IS_REVERSAL")
        self.assertEqual(len(self.store.transfers), 2)

    def test_a_reason_is_mandatory_and_the_owner_right_is_required(self):
        posted = self.move(self.service())
        for reason in ("", "   ", None):
            with self.assertRaises(SupplierRuleError):
                self.service().reverse(posted["id"], reason, key())
        with self.assertRaises(SupplierRuleError) as caught:
            self.service().reverse(99, "x", key())
        self.assertEqual(caught.exception.code, "TRANSFER_NOT_FOUND")
        self.assertEqual(len(self.store.transfers), 1)

    def test_a_reversal_replay_is_idempotent_and_the_stored_amounts_win_even_after_later_moves(self):
        service = self.service()
        posted = self.move(service, weight="5")
        self.move(service, weight="7")                                                         # a later transfer does not change what the first one reverses
        k = key()
        first = service.reverse(posted["id"], "خطأ", k)
        again = service.reverse(posted["id"], "خطأ", k)
        self.assertTrue(again["duplicate"])
        self.assertEqual(first["id"], again["id"])
        self.assertEqual(self.store.balance(5, 1), D("-265.100") + D("7.000"))
        with self.assertRaises(SupplierRuleError) as caught:
            service.reverse(posted["id"], "سبب آخر", k)
        self.assertEqual(caught.exception.code, "REQUEST_CONFLICT")


if __name__ == "__main__":
    unittest.main()
