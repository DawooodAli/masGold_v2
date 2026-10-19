"""Approval UI: real services with offline transactional fakes. No DB."""
import unittest
from unittest.mock import patch

from services import book_settlement as bs
from repositories.market_trip_repository import MarketTripRepository as RecordingRepository
from services.market_trip_service import TripError
from tests.test_book_settlement_service import ReconCase
from tests.test_market_trip_book import karat, K21, TODAY
from tests.test_market_trip_book_viewmodel import BookViewModelCase


class ApprovalServiceTests(ReconCase):
    def test_preview_is_read_only_and_returns_server_contract_and_exact_confirmations(self):
        trip = self.healthy_trip()
        before = self.state()
        self.db.lock_log.clear()
        preview = self.service.reconciliation_approval_preview(trip)
        self.assertEqual({k:v for k,v in self.state().items() if k != "preview_reads"}, before)
        self.assertEqual(self.db.lock_log, [])
        self.assertEqual(preview['sourceDigest'], self.digest(trip))
        self.assertEqual(preview['contractVersions'], dict(bs.CONTRACT_VERSIONS))
        self.assertEqual(preview['loss'], '6.000')
        self.assertTrue(preview['reasonRequired'])
        self.assertIn(preview['aUnits'], preview['confirmations']['loss_recognised_as_posted'])
        posted = self.service.reconcile(trip, preview['sourceDigest'], None, 'OPERATING_LOSS', None,
                                        {k: True for k in bs.CONFIRMATION_KEYS}, preview['contractVersions'], self.k())
        self.assertEqual(posted['loss_posted'], preview['loss'])

    def test_database_permission_revocation_refuses_approval_preview(self):
        trip = self.healthy_trip()
        self.authorization.permissions = frozenset({'APP_LOGIN'})
        with self.assertRaises(PermissionError):
            self.service.reconciliation_approval_preview(trip)

    def test_approval_sql_adds_identity_and_date_without_locks(self):
        from repositories.market_trip_repository import MarketTripRepository
        from tests.test_book_settlement_repository import ParamRecorder
        recorder = ParamRecorder()
        RecordingRepository(recorder).reconciliation_source('7', approval=True)
        self.assertEqual(len(recorder.statements), 6)                                       # 058: the capability check + the five sections
        self.assertIn('e.entry_date', recorder.statements[1])                                   # [0] is the 058 capability check
        self.assertIn('c.correction_type', recorder.statements[1])
        self.assertIn('SELECT s.id,', recorder.statements[-1])
        self.assertFalse(any('FOR SHARE' in sql or 'FOR UPDATE' in sql for sql in recorder.statements))

    def test_default_gate_rejects_before_reading_database(self):
        self.service._reconciliation_enabled = False
        before = self.db.transactions
        with self.assertRaises(TripError) as caught:
            self.service.reconciliation_approval_preview('1')
        self.assertEqual(caught.exception.code, 'RECONCILIATION_NOT_ACTIVE')
        self.assertEqual(self.db.transactions, before)

    def test_bad_source_and_already_posted_sources_are_refused(self):
        trip = self.healthy_trip()
        self.reconcile(trip)
        with self.assertRaises(TripError) as caught:
            self.service.reconciliation_approval_preview(trip)
        self.assertEqual(caught.exception.code, 'RECONCILIATION_EXISTS')
        other = self.healthy_trip()
        self.db.unreadable = {'lines'}
        with self.assertRaises(TripError) as caught:
            self.service.reconciliation_approval_preview(other)
        self.assertEqual(caught.exception.code, 'RECONCILIATION_NOT_READY')


class ApprovalViewModelTests(BookViewModelCase):
    def setUp(self):
        super().setUp()
        self.service._reconciliation_enabled = True

    def approve(self, key=None, reason='OPERATING_LOSS', note='', confirmed=True):
        self.vm.approveReconciliation(self.vm.selectedTripId, reason, note, '',
                                     {k: confirmed for k in bs.CONFIRMATION_KEYS}, key or self.k())

    def test_success_presents_document_and_uses_service_snapshot(self):
        trip = self.book_trip('1')
        self.vm.prepareReconciliation()
        digest = self.vm.reconciliationApproval['sourceDigest']
        actions = []
        self.vm.actionDone.connect(actions.append)
        self.approve()
        self.assertEqual(actions, ['reconcile'])
        self.assertEqual(len(self.db.reconciliations), 1)
        self.assertEqual(self.vm.lastReconciliation['source_digest'], digest)
        self.assertEqual(self.vm.lastReconciliation['trip_id'], trip)
        self.assertFalse(self.vm.reconciliationApproval)

    def test_stale_preview_is_invalidated_and_cannot_be_resubmitted_until_reread(self):
        trip = self.book_trip('2')
        self.vm.prepareReconciliation()
        self.service.book_return(trip, [karat(K21, '1')], TODAY, '', self.k())
        request = self.k()
        self.approve(request)
        self.assertEqual(self.vm.errorCode, 'RECONCILIATION_STALE')
        self.assertNotIn('sourceDigest', self.vm.reconciliationApproval)
        self.approve(request)
        self.assertEqual(len(self.db.reconciliations), 0)
        self.vm.prepareReconciliation()
        self.approve(request)
        self.assertEqual(len(self.db.reconciliations), 1)

    def test_reason_note_and_confirmation_errors_keep_the_preview(self):
        self.book_trip('1')
        self.vm.prepareReconciliation()
        original = dict(self.vm.reconciliationApproval)
        self.approve(reason='OTHER')
        self.assertTrue(self.vm.errorMessage)
        self.assertEqual(self.vm.reconciliationApproval, original)
        self.approve(confirmed=False)
        self.assertTrue(self.vm.errorMessage)
        self.assertEqual(len(self.db.reconciliations), 0)

    def test_busy_switch_and_logout_cannot_submit_old_trip(self):
        first = self.book_trip('1')
        self.vm.prepareReconciliation()
        old = dict(self.vm.reconciliationApproval)
        self.vm._busy.add('write')
        self.approve()
        self.vm._busy.discard('write')
        self.assertEqual(len(self.db.reconciliations), 0)
        self.book_trip('2')
        self.vm._on_approval(old, '')
        self.assertNotIn('sourceDigest', self.vm.reconciliationApproval)
        self.vm.prepareReconciliation()
        self.session.logout()
        self.assertFalse(self.vm.reconciliationApproval)
        self.assertFalse(self.vm.reconciliationEnabled)


if __name__ == '__main__':
    unittest.main()
