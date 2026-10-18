"""Two focused real-QML paths with services over offline fakes; no app DB."""
from PySide6.QtTest import QTest
from PySide6.QtCore import QPointF
from pathlib import Path
import os
from tests.test_market_trip_book_ui import BookPageTests


class ApprovalPageTests(BookPageTests):
    maxDiff = None
    def reveal(self, item):
        flick = item.parentItem()
        while flick is not None and flick.objectName() != "approvalScroll":
            flick = flick.parentItem()
        if flick is None:
            return super().reveal(item)
        pos = item.mapToItem(flick.property("contentItem"), QPointF(0, 0))
        limit = max(0, flick.property("contentHeight") - flick.height())
        flick.setProperty("contentY", float(max(0, min(pos.y() - 8, limit))))
        QTest.qWait(50)

    def capture(self, name):
        target = os.environ.get('APPROVAL_SCREENSHOT_DIR')
        if target:
            path = Path(target); path.mkdir(exist_ok=True, parents=True)
            self.root.grabWindow().save(str(path / name))

    def test_owner_reviews_confirms_and_gets_document_at_compact_size(self):
        self.tvm._service._reconciliation_enabled = True
        trip = self.make_book_trip(weight='1')
        self.show(trip)
        self.resize(1080, 680)
        self.click('openReconciliationApproval')
        self.wait_until(lambda: self.item('reconciliationApprovalDialog').property('opened'), 'approval dialog')
        self.wait_until(lambda: bool(self.tvm.reconciliationApproval.get('sourceDigest')), 'approval preview')
        self.assertFalse(self.item('confirmReconciliationApproval').property('enabled'))
        self.capture('approval-top-1080.png')
        self.pick('approvalReason', 1)
        for name in ('approvalRecordingComplete','approvalLossRecognised','approvalRoundingSeparate'):
            self.click(name)
        self.assertTrue(self.item('confirmReconciliationApproval').property('enabled'))
        self.capture('approval-confirmations-1080.png')
        self.assert_inside_window(self.item('confirmReconciliationApproval'))
        self.click('confirmReconciliationApproval')
        self.closed('reconciliationApprovalDialog')
        self.wait_idle()
        self.assertEqual(len(self.db.reconciliations), 1)
        self.assertTrue(self.tvm.lastReconciliation.get('code'))
        self.assertEqual(self.warnings, [])

    def test_default_gate_hides_approval_and_cannot_prepare_it(self):
        trip = self.make_book_trip(weight='1')
        self.show(trip)
        self.assertFalse(self.item('openReconciliationApproval').property('visible'))
        self.tvm.prepareReconciliation()
        self.assertFalse(self.tvm.reconciliationApproval)
        self.assertEqual(len(self.db.reconciliations), 0)

    def test_other_reason_requires_note_and_accepts_one_character(self):
        self.tvm._service._reconciliation_enabled = True
        trip = self.make_book_trip(weight='1')
        self.show(trip)
        self.click('openReconciliationApproval')
        self.wait_until(lambda: bool(self.tvm.reconciliationApproval.get('sourceDigest')), 'approval preview')
        self.pick('approvalReason', 4)
        for name in ('approvalRecordingComplete','approvalLossRecognised','approvalRoundingSeparate'):
            self.click(name)
        self.assertFalse(self.item('confirmReconciliationApproval').property('enabled'))
        self.type_in('approvalNote', 'x')
        self.assertTrue(self.item('confirmReconciliationApproval').property('enabled'))
        self.click('confirmReconciliationApproval')
        self.closed('reconciliationApprovalDialog')
        self.wait_idle()
        self.assertEqual(self.tvm.lastReconciliation['reason_code'], 'OTHER')
        self.assertEqual(self.tvm.lastReconciliation['note'], 'x')
        self.assertEqual(self.warnings, [])

    def test_approval_layout_at_three_sizes(self):
        self.tvm._service._reconciliation_enabled = True
        trip = self.make_book_trip(weight='1')
        self.show(trip)
        self.click('openReconciliationApproval')
        self.wait_until(lambda: bool(self.tvm.reconciliationApproval.get('sourceDigest')), 'approval preview')
        for width, height in ((1440,900),(1280,800),(1080,680)):
            self.resize(width,height)
            scroll = self.item('approvalScroll')
            scroll.setProperty('contentY',0.0)
            QTest.qWait(120)
            self.assert_inside_window(self.item('reconciliationApprovalTitle'))
            self.assert_inside_window(self.item('confirmReconciliationApproval'))
            self.capture('approval-top-%dx%d.png' % (width,height))
            for name in ('approvalRecordingComplete','approvalLossRecognised','approvalRoundingSeparate'):
                box = self.item(name)
                if not box.property('checked'):
                    self.click(box)
            scroll.setProperty('contentY', max(0.0, scroll.property('contentHeight') - scroll.height()))
            QTest.qWait(100)
            self.capture('approval-confirmations-%dx%d.png' % (width,height))
        self.assertEqual(len(self.db.reconciliations),0)
        self.assertEqual(self.warnings,[])
