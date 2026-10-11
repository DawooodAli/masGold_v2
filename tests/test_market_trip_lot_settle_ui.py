"""047 real-click QML checks over real services with transactional fake persistence."""
from decimal import Decimal as D
from unittest.mock import patch
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from tests.test_market_trip_gold_settle_ui import GoldSettlePageTests

class LotSettlePageTests(GoldSettlePageTests):
    def prepare_lots(self, weight='10', fineness='875', residues=()):
        self.open_page()
        s=self.tvm._service
        t=s.issue('','',[{'karat_id':'1','weight':'20','designation':'MELT'}],'2026-10-10','',self.tvm.newRequestKey())
        s.melt(t['trip_id'],[{'karat_id':'1','weight':'20'}],'2026-10-10','ورشة',{'weight':weight,'fineness':fineness},
               [{'weight':w,'fineness':f} for w,f in residues],'','','',self.tvm.newRequestKey())
        self.tvm.selectTrip(t['trip_id']);self.tvm.refresh();self.wait_idle()
        self.wait_until(lambda:self.tvm.detail.get('lotSettleAvailable'),'whole lot available')
        return t['trip_id']

    def open_lot_settle(self):
        self.reveal(self.item('custodyLotSettleButton'));self.click('custodyLotSettleButton')
        self.wait_until(lambda:self.item('tripLotSettleDialog').property('opened'),'lot settlement dialog')
        self.wait_until(lambda:len(self.tvm.context.get('goldSuppliers',[]))==2,'supplier context')
        self.pick('custodyLotSettleSupplierCombo',2);self.pick('custodyLotSettleLotCombo',1)

    def test_whole_bar_real_click_at_1080_and_post_commit_supplier_refresh(self):
        self.root.setWidth(1080);self.root.setHeight(680);trip=self.prepare_lots()
        unchanged=(len(self.db.scrap),self.db.cash(),len(self.db.shop_moves));reloads=self.stub.reloads
        self.open_lot_settle()
        for name in ('custodyLotSettleSupplierCombo','custodyLotSettleLotCombo','confirmCustodyLotSettleButton','cancelCustodyLotSettleButton'):
            self.assert_inside_window(self.item(name))
        self.assertIn('875',self.text('custodyLotSettleFacts'));self.assertIn('10.000',self.text('custodyLotSettleFacts'))
        self.assertIn('11.667',self.text('custodyLotSettleCredit'));self.assertIn('11.666667',self.text('custodyLotSettleAnalysis'))
        self.click('confirmCustodyLotSettleButton')
        self.wait_until(lambda:len(self.db.settlements)==1,'posted');self.closed('tripLotSettleDialog');self.wait_idle()
        self.assertEqual(unchanged,(len(self.db.scrap),self.db.cash(),len(self.db.shop_moves)))
        self.wait_until(lambda:self.stub.reloads>reloads,'supplier refreshed')
        self.assertIn('SG000001',' '.join(self.visible_texts('entryWhat')))
        self.assertEqual(self.tvm._service.trip_detail(trip)['custody']['lots'],[])
        self.assertEqual(self.warnings,[])

    def test_residue_label_unknown_assay_and_zero_credit_preview(self):
        self.prepare_lots('1','0.1',[('1','500'),('2','')]);self.open_lot_settle()
        self.assertIn('تبقى السبكة',self.text('custodyLotSettlePreviewError'))
        self.assertFalse(self.item('confirmCustodyLotSettleButton').property('enabled'))
        self.pick('custodyLotSettleLotCombo',2)
        self.assertIn('رواسب',self.text('custodyLotSettleFacts'))
        self.assertTrue(self.item('confirmCustodyLotSettleButton').property('enabled'))
        self.pick('custodyLotSettleLotCombo',3)
        self.assertIn('غير معلوم',self.text('custodyLotSettlePreviewError'))
        self.assertFalse(self.item('confirmCustodyLotSettleButton').property('enabled'))

    def test_refusal_preserves_fields_retry_and_double_submission(self):
        self.prepare_lots();self.open_lot_settle();self.type_in('custodyLotSettleReferenceField','مرجع خاص')
        self.db.fail_at='lot_link';self.click('confirmCustodyLotSettleButton');self.wait_idle()
        self.assertTrue(self.item('tripLotSettleDialog').property('opened'))
        self.assertEqual(self.text('custodyLotSettleReferenceField'),'مرجع خاص');self.assertEqual(self.db.settlements,{})
        self.db.fail_at=None
        self.click('confirmCustodyLotSettleButton')
        # A second submission while the worker owns the write cannot launch another delivery.
        self.tvm.settleLot(self.tvm.selectedTripId, str(self.tvm.context['goldSuppliers'][1]['id']), str(next(iter(self.db.lots))), '2026-10-10', '', '', self.tvm.newRequestKey())
        self.wait_until(lambda:len(self.db.settlements)==1,'retry posted');self.wait_idle()
        self.assertEqual(len(self.db.lot_settle_links),1)

    def test_custody_reversal_restores_original_lot_and_refreshes_supplier(self):
        trip=self.prepare_lots();original=next(iter(self.db.lots));self.open_lot_settle()
        self.click('confirmCustodyLotSettleButton');self.wait_until(lambda:len(self.db.settlements)==1,'posted')
        self.closed('tripLotSettleDialog');self.wait_idle();reloads=self.stub.reloads
        buttons=self.reverse_buttons();self.assertEqual(len(buttons),3)
        self.reveal(buttons[-1]);self.click(buttons[-1])
        self.wait_until(lambda:self.item('tripReasonDialog').property('opened'),'reversal dialog')
        self.assertIn('الشحنة الأصلية',self.text('tripReasonNote'))
        self.assertFalse(self.item('confirmTripReasonButton').property('enabled'))
        self.type_in('tripReasonField','تسوية بالخطأ');self.click('confirmTripReasonButton')
        self.wait_until(lambda:len(self.db.corrections)==1,'reversed');self.closed('tripReasonDialog');self.wait_idle()
        restored=self.tvm._service.trip_detail(trip)['custody']['lots'][0]
        self.assertEqual(restored['lot_id'],str(original));self.assertEqual(restored['outstanding'],D('10'))
        self.assertIn('RV000001',' '.join(self.visible_texts('entryState')))
        self.wait_until(lambda:self.stub.reloads>reloads,'supplier refresh after reversal')
        self.assertEqual(self.warnings,[])
