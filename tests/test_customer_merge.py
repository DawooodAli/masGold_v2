"""Customer merge / delete (migration 087) against a transactional in-memory store (no application database).
Real-MySQL proof: test_customer_merge_mysql.py (opt-in)."""
import copy
from contextlib import contextmanager
from decimal import Decimal as D
import unittest

from app.session import AppSession
from repositories import customer_merge_repository as repo_module
from services.authentication_service import AuthenticationResult, _VERIFIED
from services.customer_merge_service import CustomerMergeError, CustomerMergeService
from tests.auth_fakes import FakeAuthorization

MANAGER = frozenset({"APP_LOGIN", "TRANSACTION_CORRECT"})
CASHIER = frozenset({"APP_LOGIN", "CUSTOMER_ACCOUNT_SETTLE"})
TABLES = [t for t, _ in repo_module.ALL_TABLES]


class Store:
    def __init__(self):
        self.customers = {"1": {"id": 1, "customer_code": "C000001", "name": "مصطفى موسى", "mobile": None, "is_active": 1, "merged_into_customer_id": None},
                          "2": {"id": 2, "customer_code": "C000002", "name": "مصطفي موسي", "mobile": None, "is_active": 1, "merged_into_customer_id": None},
                          "3": {"id": 3, "customer_code": "C000003", "name": "عميل نظيف", "mobile": None, "is_active": 1, "merged_into_customer_id": None}}
        self.rows = {t: [] for t in TABLES}             # table -> [customer_id, ...]
        self.ledger = []                                  # (customer_id, delta)
        self.flags = []                                   # {customer_id, type, active}
        self.audit = []


class Repo:
    fail_move = None

    def __init__(self, connection):
        self.store = connection.store

    def lock_customers(self, ids):
        return {str(i): dict(self.store.customers[str(i)]) for i in sorted({int(x) for x in ids}) if str(i) in self.store.customers}

    def balance(self, cid):
        return sum((d for c, d in self.store.ledger if c == int(cid)), D("0.00"))

    def usage(self, cid):
        out = {}
        for t in TABLES:
            n = sum(1 for c in self.store.rows[t] if c == int(cid))
            if t == "customer_transactions":
                n = sum(1 for c, _ in self.store.ledger if c == int(cid))
            if t == "customer_flags":
                n = sum(1 for f in self.store.flags if f["customer_id"] == int(cid))
            out[t] = n
        return out

    def move(self, table, column, source, target):
        if self.fail_move == table:
            raise RuntimeError("boom")
        s, t = int(source), int(target)
        if table == "customer_transactions":
            moved = [x for x in self.store.ledger if x[0] == s]
            self.store.ledger = [(t if c == s else c, d) for c, d in self.store.ledger]
            return len(moved)
        if table == "customer_flags":
            n = 0
            for f in self.store.flags:
                if f["customer_id"] == s:
                    f["customer_id"] = t
                    n += 1
            return n
        n = sum(1 for c in self.store.rows[table] if c == s)
        self.store.rows[table] = [t if c == s else c for c in self.store.rows[table]]
        return n

    def deactivate_duplicate_flags(self, source, target, user, reason):
        have = {f["type"] for f in self.store.flags if f["customer_id"] == int(target) and f["active"]}
        n = 0
        for f in self.store.flags:
            if f["customer_id"] == int(source) and f["active"] and f["type"] in have:
                f["active"] = False
                n += 1
        return n

    def mark_merged(self, source, target, user):
        row = self.store.customers[str(source)]
        row["is_active"], row["merged_into_customer_id"] = 0, int(target)

    def delete_customer(self, cid):
        return 1 if self.store.customers.pop(str(cid), None) else 0

    def audit(self, *, actor, event_type, customer_id, description, metadata):
        self.store.audit.append((event_type, str(customer_id), metadata))

    def _fetch_one(self, query, params=()):
        if "COUNT(*)" in query:
            return {"n": sum(1 for c in self.store.customers.values() if c["merged_into_customer_id"] == int(params[0]))}
        row = self.store.customers.get(str(params[0]))
        return None if row is None else dict(row)


class Conn:
    def __init__(self, store):
        self.store = store


class MergeCase(unittest.TestCase):
    def setUp(self):
        Repo.fail_move = None
        self.addCleanup(setattr, Repo, "fail_move", None)
        self.store = Store()
        self.service = self.make(MANAGER)

    def make(self, permissions):
        session = AppSession()
        session._establish(AuthenticationResult(3, None, "المدير", permissions, _VERIFIED), int(session.generation))

        @contextmanager
        def transaction():
            saved = copy.deepcopy(vars(self.store))
            try:
                yield Conn(self.store)
            except BaseException:
                self.store.__dict__.clear()
                self.store.__dict__.update(saved)
                raise
        return CustomerMergeService(session, transaction_factory=transaction, repository=Repo, authorization=FakeAuthorization(session, None))

    def refused(self, code, call):
        before = copy.deepcopy(vars(self.store))
        with self.assertRaises(CustomerMergeError) as ctx:
            call()
        self.assertEqual(ctx.exception.code, code)
        self.assertEqual(vars(self.store), before)                                     # nothing written

    def seed(self):
        self.store.ledger += [(1, D("500.00")), (2, D("-200.00"))]
        self.store.rows["invoices"] += [1, 1, 2]
        self.store.rows["invoice_customer_snapshots"] += [1, 1, 2]
        self.store.rows["customer_deposits"] += [2]


class MergeTests(MergeCase):
    def test_every_record_moves_to_the_survivor_and_the_balances_add_up(self):
        self.seed()
        result = self.service.merge("1", "2", "نفس الشخص")
        self.assertEqual((result["source_id"], result["target_id"], result["balance_after"], result["duplicate"]), ("1", "2", "300.00", False))
        self.assertEqual(self.store.rows["invoices"], [2, 2, 2])
        self.assertEqual(self.store.rows["invoice_customer_snapshots"], [2, 2, 2])
        self.assertEqual({c for c, _ in self.store.ledger}, {2})
        merged = self.store.customers["1"]
        self.assertEqual((merged["is_active"], merged["merged_into_customer_id"]), (0, 2))   # kept, retired, pointing at the survivor
        event, subject, meta = self.store.audit[-1]
        self.assertEqual((event, subject, meta["moved"]["invoices"], meta["target_balance_after"]), ("CUSTOMER_MERGED", "1", 2, "300.00"))

    def test_an_exact_repeat_answers_the_same_and_moves_nothing_more(self):
        self.seed()
        self.service.merge("1", "2", "نفس الشخص")
        again = self.service.merge("1", "2", "نفس الشخص")
        self.assertTrue(again["duplicate"])
        self.assertEqual(len(self.store.audit), 1)

    def test_refusals_write_nothing(self):
        self.seed()
        self.refused("REASON_REQUIRED", lambda: self.service.merge("1", "2", "  "))
        self.refused("SAME_CUSTOMER", lambda: self.service.merge("1", "1", "x"))
        self.refused("CUSTOMER_NOT_FOUND", lambda: self.service.merge("1", "99", "x"))
        self.service.merge("1", "2", "x")
        self.refused("ALREADY_MERGED", lambda: self.service.merge("1", "3", "x"))        # merged into someone else
        self.refused("TARGET_INACTIVE", lambda: self.service.merge("3", "1", "x"))       # a retired customer cannot be the survivor

    def test_a_failure_in_the_middle_rolls_everything_back(self):
        self.seed()
        Repo.fail_move = "customer_account_settlements"
        before = copy.deepcopy(vars(self.store))
        with self.assertRaises(RuntimeError):
            self.service.merge("1", "2", "x")
        self.assertEqual(vars(self.store), before)

    def test_a_duplicate_active_flag_is_closed_so_only_one_stays(self):
        self.store.flags += [{"customer_id": 1, "type": "DEAD_CASH_WARNING", "active": True}, {"customer_id": 2, "type": "DEAD_CASH_WARNING", "active": True}]
        self.service.merge("1", "2", "x")
        self.assertEqual([f["active"] for f in self.store.flags if f["customer_id"] == 2], [False, True])

    def test_only_managers_and_owners_may_merge_or_delete(self):
        clerk = self.make(CASHIER)
        for call in (lambda: clerk.merge("1", "2", "x"), lambda: clerk.delete("3"), lambda: clerk.preview("1", "2"), lambda: clerk.usage("1")):
            with self.assertRaises(PermissionError):
                call()
        self.assertFalse(clerk.can_manage())
        self.assertTrue(self.service.can_manage())

    def test_the_preview_shows_what_moves_and_the_combined_balance(self):
        self.seed()
        p = self.service.preview("1", "2")
        self.assertEqual((p["source_balance"], p["target_balance"], p["combined_balance"]), ("500.00", "-200.00", "300.00"))
        self.assertIn(("invoices", 2), [(m["table"], m["count"]) for m in p["moved"]])
        self.assertEqual(self.store.customers["1"]["is_active"], 1)                       # a preview changes nothing


class DeleteTests(MergeCase):
    def test_a_customer_nothing_points_at_is_deleted(self):
        self.assertTrue(self.service.usage("3")["deletable"])
        self.service.delete("3")
        self.assertNotIn("3", self.store.customers)
        self.assertEqual(self.store.audit[-1][0], "CUSTOMER_DELETED")

    def test_anything_referenced_blocks_the_delete_and_names_what_holds_it(self):
        self.store.ledger.append((3, D("0.00")))                                          # even a zero-balance ledger row counts
        self.assertFalse(self.service.usage("3")["deletable"])
        with self.assertRaises(CustomerMergeError) as ctx:
            self.service.delete("3")
        self.assertEqual(ctx.exception.code, "CUSTOMER_IN_USE")
        self.assertIn("حركات الحساب", ctx.exception.message)
        self.assertIn("3", self.store.customers)

    def test_a_survivor_of_a_merge_cannot_be_deleted(self):
        self.service.merge("1", "2", "x")
        self.assertFalse(self.service.usage("2")["deletable"])
        self.refused("CUSTOMER_IN_USE", lambda: self.service.delete("2"))
        self.refused("CUSTOMER_IN_USE", lambda: self.service.delete("1"))                 # the retired one keeps its trail too


if __name__ == "__main__":
    unittest.main()
