"""Deadlock handling of the marketer flows (migration 055 services): when MySQL picks a marketer transaction as the deadlock victim (errno 1213) the service recovers
from a FRESH transaction AFTER the failed one is rolled back and closed. Real MarketerCustodyService over the in-memory transactional database (tests/market_trip_fakes.py +
tests/marketer_fakes.py), with the deadlock injected at the repository INSERT. NOT real-MySQL tests: that a real victim is rolled back by InnoDB, the exact errno and the lock
schedule are covered by tests/test_stabilization_mysql.py (RequestKeyConcurrentTests) only."""
from contextlib import contextmanager
import logging
import unittest

from mysql.connector.errors import IntegrityError, InternalError

from services.marketer_custody_service import MarketerCustodyService, RETRY_MESSAGE, is_deadlock
from services.market_trip_service import TripError
from tests.market_trip_fakes import AccountsRepo, OWNER, ScrapRepo, SettlementRepo, session_for
from tests.marketer_fakes import MarketerRepo
from tests.test_marketer_custody import DUAL, MARKETER, MarketerCase, REFERENCE, SUPPLIER24, TODAY, line


def deadlock(errno=1213):
    return InternalError(msg="Deadlock found when trying to get lock; try restarting transaction", errno=errno, sqlstate="40001")


class VictimRepo(MarketerRepo):
    """The marketer repository that fails ONCE with a database error at the first document / settlement INSERT, and logs every write after that."""
    fail_with = None
    log = []

    def _maybe_fail(self):
        VictimRepo.log.append("insert")
        if VictimRepo.fail_with is not None:
            error, VictimRepo.fail_with = VictimRepo.fail_with, None
            raise error

    def insert_document(self, **kw):
        self._maybe_fail()
        return super().insert_document(**kw)

    def insert_settlement_header(self, **kw):
        self._maybe_fail()
        return super().insert_settlement_header(**kw)

    def by_request(self, key):
        VictimRepo.log.append("by_request")
        return super().by_request(key)

    def key_used_elsewhere(self, key):
        VictimRepo.log.append("key_used_elsewhere")
        return super().key_used_elsewhere(key)


class RecoveryCase(MarketerCase):
    def setUp(self):
        super().setUp()
        self.events, self.after_rollback = [], []
        outer = self

        @contextmanager
        def logged():
            outer.events.append("begin")
            try:
                with outer.db.transaction() as connection:
                    yield connection
            except BaseException:
                outer.events.append("rollback")
                hooks, outer.after_rollback = list(outer.after_rollback), []
                for hook in hooks:                                          # what OTHER committed transactions do while this one is gone
                    hook()
                raise
            else:
                outer.events.append("commit")
        VictimRepo.fail_with, VictimRepo.log = None, []
        self.victim = MarketerCustodyService(self.session, transaction_factory=logged, repository=VictimRepo, accounts=AccountsRepo, scrap=ScrapRepo,
                                             settlements=SettlementRepo, authorization=self.authorization, corrections=self.corrections,
                                             price_reference=lambda: dict(REFERENCE))

    def committed_owner(self, key):
        """A custody entry committed by another transaction under the key (what the winner of the race leaves behind)."""
        self.db.entries[900] = {"id": 900, "trip_id": 1, "entry_no": 1, "entry_kind": "ISSUE", "request_key": key, "request_hash": "x", "performed_by_user_id": 7,
                                "entry_date": TODAY, "notes": "", "occurred_at": None}

    def first_unit_of_work(self):
        return self.events[:2]


class RecoveryOrderTests(RecoveryCase):
    def test_recovery_runs_only_after_the_failed_transaction_rolled_back(self):
        key = self.key()
        VictimRepo.fail_with = deadlock()
        self.after_rollback.append(lambda: self.committed_owner(key))
        with self.assertRaises(TripError) as refused:
            self.victim.cash_handover(MARKETER, "1000", TODAY, "", "", key)
        self.assertEqual(refused.exception.code, "REQUEST_CONFLICT")
        self.assertEqual(self.events, ["begin", "rollback", "begin", "rollback"])        # the recovery transaction begins strictly after the rollback
        reads = VictimRepo.log[VictimRepo.log.index("insert") + 1:]
        self.assertEqual(reads, ["by_request", "key_used_elsewhere"])                    # recovery only READS the ownership: no INSERT is retried
        self.assertEqual(VictimRepo.log.count("insert"), 1)

    def test_a_recovery_read_never_happens_inside_the_failed_transaction(self):
        key = self.key()
        VictimRepo.fail_with = deadlock()
        seen = []
        original = VictimRepo.by_request
        VictimRepo.by_request = lambda repo, k: (seen.append(list(self.events)), original(repo, k))[1]
        try:
            with self.assertRaises(TripError):
                self.victim.cash_handover(MARKETER, "1000", TODAY, "", "", key)
        finally:
            VictimRepo.by_request = original
        self.assertEqual(seen[-1], ["begin", "rollback", "begin"])                       # the last (recovery) read ran in the second transaction, after the first rolled back


class ClassificationTests(RecoveryCase):
    def test_a_committed_conflicting_owner_becomes_request_conflict(self):
        key = self.key()
        VictimRepo.fail_with = deadlock()
        self.after_rollback.append(lambda: self.committed_owner(key))
        before_docs = len(self.db.mk_docs)
        with self.assertLogs("services.marketer_custody_service", level=logging.WARNING) as logged:
            with self.assertRaises(TripError) as refused:
                self.victim.cash_handover(MARKETER, "1000", TODAY, "", "", key)
        self.assertEqual(refused.exception.code, "REQUEST_CONFLICT")
        self.assertIn("deadlock victim", " ".join(logged.output))                         # the recovery is visible in the diagnostics whatever its outcome
        self.assertEqual((len(self.db.mk_docs), self.db.cash()), (before_docs, self.db.cash()))
        self.assertEqual(len(self.db.mk_docs), 0)

    def test_a_deadlock_with_no_committed_owner_is_not_labelled_request_conflict(self):
        key = self.key()
        before = self.state()
        VictimRepo.fail_with = deadlock()
        with self.assertLogs("services.marketer_custody_service", level=logging.WARNING) as logged:
            with self.assertRaises(TripError) as refused:
                self.victim.cash_handover(MARKETER, "1000", TODAY, "", "", key)
        error = refused.exception
        self.assertEqual(error.code, "CONCURRENT_RETRY")
        self.assertNotEqual(error.code, "REQUEST_CONFLICT")
        self.assertEqual(str(error), RETRY_MESSAGE)
        self.assertTrue(any(ord(ch) > 0x600 for ch in str(error)))                        # a clear Arabic message
        self.assertTrue(is_deadlock(error.__cause__) and error.database_errno == 1213)    # the database error stays available for diagnostics
        self.assertEqual(self.state(), before)                                            # complete rollback: no partial accounting effect
        text = " ".join(logged.output)
        self.assertIn("1213", text)
        self.assertIn(key[:8], text)
        self.assertNotIn(key, text)                                                       # only a key prefix is logged
        # the request key is preserved: the user's retry with the SAME key now succeeds exactly once
        done = self.victim.cash_handover(MARKETER, "1000", TODAY, "", "", key)
        self.assertEqual((done["duplicate"], len(self.db.mk_docs)), (False, 1))

    def test_a_deadlock_never_persists_a_partial_effect_in_the_recovery(self):
        key = self.key()
        VictimRepo.fail_with = deadlock()
        cash, scrap, ledgers = self.db.cash(), len(self.db.scrap), self.supplier_ledgers()
        with self.assertRaises(TripError):
            self.victim.cash_handover(MARKETER, "1000", TODAY, "", "", key)
        self.assertEqual((self.db.cash(), len(self.db.scrap), self.supplier_ledgers(), len(self.db.mk_docs), len(self.db.mk_cash)), (cash, scrap, ledgers, 0, 0))
        self.assertEqual(VictimRepo.log.count("insert"), 1)                               # one attempt, none repeated by the recovery


class ReplayRuleTests(RecoveryCase):
    def test_an_exact_compatible_request_that_already_succeeded_is_replayed(self):
        key = self.key()
        VictimRepo.fail_with = deadlock()
        twin = {}
        self.after_rollback.append(lambda: twin.update(self.service.cash_handover(MARKETER, "1000", TODAY, "", "", key)))
        done = self.victim.cash_handover(MARKETER, "1000", TODAY, "", "", key)
        self.assertEqual((done["id"], done["duplicate"]), (twin["id"], True))
        self.assertEqual(len(self.db.mk_docs), 1)                                          # exactly one document under the key

    def test_a_changed_payload_is_a_conflict(self):
        key = self.key()
        VictimRepo.fail_with = deadlock()
        self.after_rollback.append(lambda: self.service.cash_handover(MARKETER, "2000", TODAY, "", "", key))
        with self.assertRaises(TripError) as refused:
            self.victim.cash_handover(MARKETER, "1000", TODAY, "", "", key)
        self.assertEqual(refused.exception.code, "REQUEST_CONFLICT")
        self.assertEqual(len(self.db.mk_docs), 1)

    def test_a_changed_actor_is_a_conflict(self):
        key = self.key()
        VictimRepo.fail_with = deadlock()
        other = session_for(OWNER, user_id=99)
        stranger = self.make_service(other, type(self.authorization)(other))
        self.after_rollback.append(lambda: stranger.cash_handover(MARKETER, "1000", TODAY, "", "", key))
        with self.assertRaises(TripError) as refused:
            self.victim.cash_handover(MARKETER, "1000", TODAY, "", "", key)
        self.assertEqual(refused.exception.code, "REQUEST_CONFLICT")

    def test_another_operation_on_the_same_key_is_a_conflict(self):
        key = self.key()
        VictimRepo.fail_with = deadlock()
        self.hand("5000")
        self.after_rollback.append(lambda: self.service.cash_return(MARKETER, "1000", TODAY, "", "", key))
        with self.assertRaises(TripError) as refused:
            self.victim.cash_handover(MARKETER, "1000", TODAY, "", "", key)
        self.assertEqual(refused.exception.code, "REQUEST_CONFLICT")


class AuthorizationAndScopeTests(RecoveryCase):
    def test_recovery_rechecks_authorization_in_its_own_transaction(self):
        key = self.key()
        VictimRepo.fail_with = deadlock()
        self.after_rollback.append(lambda: setattr(self.authorization, "permissions", {"APP_LOGIN"}))        # the right is gone by the time recovery starts
        with self.assertRaises(PermissionError):
            self.victim.cash_handover(MARKETER, "1000", TODAY, "", "", key)
        self.assertEqual(self.events, ["begin", "rollback", "begin", "rollback"])
        self.assertEqual(VictimRepo.log.count("insert"), 1)

    def test_lock_wait_timeout_1205_and_other_errors_are_not_handled(self):
        for error in (deadlock(1205), InternalError(msg="other", errno=1054, sqlstate="42S22"), RuntimeError("boom")):
            self.events.clear()
            VictimRepo.fail_with = error
            with self.assertRaises(type(error)):
                self.victim.cash_handover(MARKETER, "1000", TODAY, "", "", self.key())
            self.assertEqual(self.events, ["begin", "rollback"])                          # no recovery transaction was started
            self.assertFalse(is_deadlock(error))
        self.assertEqual(len(self.db.mk_docs), 0)


class SettlementRecoveryTests(RecoveryCase):
    def prepare(self):
        self.hand("200000")
        self.buy([line("10", "60000")])
        self.events.clear()

    def test_a_settlement_deadlock_with_a_committed_owner_is_request_conflict(self):
        self.prepare()
        key = self.key()
        VictimRepo.fail_with = deadlock()
        self.after_rollback.append(lambda: self.committed_owner(key))
        settlements, gold = len(self.db.settlements), self.gold()
        with self.assertRaises(TripError) as refused:
            self.victim.settle_supplier(MARKETER, SUPPLIER24, "1", False, TODAY, "", "", key)
        self.assertEqual(refused.exception.code, "REQUEST_CONFLICT")
        self.assertEqual((len(self.db.settlements), self.gold()), (settlements, gold))
        self.assertEqual(self.events, ["begin", "rollback", "begin", "rollback"])

    def test_a_settlement_deadlock_without_an_owner_is_a_retryable_error_and_writes_nothing(self):
        self.prepare()
        key = self.key()
        before = self.state()
        VictimRepo.fail_with = deadlock()
        with self.assertRaises(TripError) as refused:
            self.victim.settle_supplier(MARKETER, SUPPLIER24, "1", False, TODAY, "", "", key)
        self.assertEqual(refused.exception.code, "CONCURRENT_RETRY")
        self.assertEqual(self.state(), before)
        done = self.victim.settle_supplier(MARKETER, SUPPLIER24, "1", False, TODAY, "", "", key)          # same key, retry
        self.assertEqual((done["duplicate"], len(self.db.settlements)), (False, 1))

    def test_a_settlement_deadlock_after_an_exact_twin_commits_replays_it(self):
        self.prepare()
        key = self.key()
        VictimRepo.fail_with = deadlock()
        twin = {}
        self.after_rollback.append(lambda: twin.update(self.service.settle_supplier(MARKETER, SUPPLIER24, "1", False, TODAY, "", "", key)))
        done = self.victim.settle_supplier(MARKETER, SUPPLIER24, "1", False, TODAY, "", "", key)
        self.assertEqual((done["id"], done["duplicate"]), (twin["id"], True))
        self.assertEqual(len(self.db.settlements), 1)


class SettlementRecoveryPermissionTests(RecoveryCase):
    """Both fresh recovery transactions of settle_supplier (deadlock and duplicate-key) re-check MANAGE and SETTLE before the replay decision, like _settle_once.
    The document recovery keeps needing MANAGE only."""

    ERRORS = {"deadlock": lambda: deadlock(),
              "duplicate key": lambda: IntegrityError("1062 (23000): Duplicate entry 'x' for key 'supplier_gold_settlements.uq_sgs_request'", errno=1062)}

    def prepare(self):
        self.hand("200000")
        self.buy([line("10", "60000")])
        self.events.clear()
        VictimRepo.log.clear()

    def run_case(self, error, revoked):
        """The original transaction fails; meanwhile an EXACT twin commits (it would replay successfully); then the given rights are revoked."""
        self.prepare()
        key = self.key()
        VictimRepo.fail_with = error
        twin = {}
        self.after_rollback.append(lambda: twin.update(self.service.settle_supplier(MARKETER, SUPPLIER24, "1", False, TODAY, "", "", key)))
        if revoked:
            self.after_rollback.append(lambda: setattr(self.authorization, "permissions", set(OWNER) - set(revoked)))
        return key, twin

    def test_a_revoked_settle_right_blocks_the_recovery_before_the_replay(self):
        for name, make in self.ERRORS.items():
            with self.subTest(recovery=name):
                key, twin = self.run_case(make(), {"SUPPLIER_GOLD_SETTLE"})                  # MANAGE stays granted
                with self.assertRaises(PermissionError):
                    self.victim.settle_supplier(MARKETER, SUPPLIER24, "1", False, TODAY, "", "", key)
                self.assertTrue(twin, "the committed twin that would have replayed")
                self.assertEqual(self.events, ["begin", "rollback", "begin", "rollback"])     # the recovery transaction started and was refused inside
                self.assertEqual(VictimRepo.log.count("insert"), 1)                           # no recovery write (and no retried INSERT)
                self.assertNotIn("by_request", VictimRepo.log[VictimRepo.log.index("insert") + 1:])      # refused BEFORE the ownership / replay read
                self.assertEqual(len(self.db.settlements), 1)                                  # only the twin's settlement exists
                self.authorization.permissions = set(OWNER)
                self.setUp()

    def test_a_revoked_manage_right_also_blocks_it(self):
        for name, make in self.ERRORS.items():
            with self.subTest(recovery=name):
                key, twin = self.run_case(make(), {"INTERMEDIARY_HOLDING_MANAGE"})
                with self.assertRaises(PermissionError):
                    self.victim.settle_supplier(MARKETER, SUPPLIER24, "1", False, TODAY, "", "", key)
                self.assertEqual(VictimRepo.log.count("insert"), 1)
                self.assertNotIn("by_request", VictimRepo.log[VictimRepo.log.index("insert") + 1:])
                self.authorization.permissions = set(OWNER)
                self.setUp()

    def test_with_both_rights_granted_the_exact_replay_is_preserved(self):
        for name, make in self.ERRORS.items():
            with self.subTest(recovery=name):
                key, twin = self.run_case(make(), set())
                done = self.victim.settle_supplier(MARKETER, SUPPLIER24, "1", False, TODAY, "", "", key)
                self.assertEqual((done["id"], done["duplicate"]), (twin["id"], True))
                self.assertEqual(len(self.db.settlements), 1)
                self.assertEqual(VictimRepo.log.count("insert"), 1)
                self.setUp()

    def test_both_rights_are_checked_in_the_recovery_transaction(self):
        seen = []
        original = self.authorization.require_permission_for_write
        self.authorization.require_permission_for_write = lambda connection, actor, code, **kw: (seen.append((len(self.events), code)), original(connection, actor, code, **kw))[1]
        self.prepare()
        seen.clear()
        VictimRepo.fail_with = deadlock()
        with self.assertRaises(TripError):
            self.victim.settle_supplier(MARKETER, SUPPLIER24, "1", False, TODAY, "", "", self.key())
        recovery = [code for events, code in seen if events >= 3]                              # after begin / rollback / begin
        self.assertEqual(recovery, ["INTERMEDIARY_HOLDING_MANAGE", "SUPPLIER_GOLD_SETTLE"])

    def test_the_document_recovery_still_needs_manage_only(self):
        key = self.key()
        VictimRepo.fail_with = deadlock()
        twin = {}
        self.after_rollback.append(lambda: twin.update(self.service.cash_handover(MARKETER, "1000", TODAY, "", "", key)))
        self.after_rollback.append(lambda: setattr(self.authorization, "permissions", set(OWNER) - {"SUPPLIER_GOLD_SETTLE"}))
        done = self.victim.cash_handover(MARKETER, "1000", TODAY, "", "", key)            # SETTLE is not a document right: replay still works
        self.assertEqual((done["id"], done["duplicate"]), (twin["id"], True))


if __name__ == "__main__":
    unittest.main()
