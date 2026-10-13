"""Explicit opt-in 047 rehearsal checks. Never connects to the DEV database."""
from contextlib import contextmanager
from dataclasses import replace
from decimal import Decimal as D
import os
import threading
import unittest
from unittest.mock import patch
from uuid import uuid4

from config.database_config import DatabaseConfig
from database.connection import DatabaseConnection
from app.session import AppSession
from repositories.authentication_repository import AuthenticationRepository
from services.authentication_service import AuthenticationResult, _VERIFIED
from services.market_trip_service import MarketTripService, TripError
from services.correction_service import CorrectionService, CorrectionError
from services.supplier_service import SupplierService
from services.supplier_gold_settlement_service import SupplierGoldSettlementService
from services.supplier_rules import SupplierRuleError
from repositories.market_trip_repository import MarketTripRepository, SHOP_RETURN_IN
from repositories.supplier_gold_settlement_repository import SupplierGoldSettlementRepository
from repositories.correction_repository import CorrectionRepository

TARGET='masgold_rehearsal'
DATE='2026-10-11'

@unittest.skipUnless(os.environ.get('MASGOLD_LOT_SETTLE_REHEARSAL')=='1','explicit rehearsal-only opt-in')
class WholeLotMySqlTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.config=replace(DatabaseConfig.from_env(),database=TARGET)
        with cls.tx() as c:
            q=c.cursor(dictionary=True)
            q.execute('SELECT DATABASE() AS db, MAX(version) AS v FROM schema_migrations')
            row=q.fetchone();assert row['db']==TARGET and row['v'] in (47,48,49,50,51),row;cls.version=row['v']   # an explicit pair: the code is 048-aware, so only the guard test can run at 47
            q.execute("SELECT target_database FROM masgold_rehearsal_marker WHERE marker_key='MASGOLD_SCHEMA_REHEARSAL'")
            assert q.fetchone()['target_database']==TARGET
            q.execute("INSERT INTO employees(name) VALUES('047 rehearsal owner')");emp=q.lastrowid
            q.execute("INSERT INTO users(employee_id,username,password_hash,display_name) VALUES(%s,%s,'unusable-rehearsal-hash','047 owner')",(emp,'rehearsal047-'+uuid4().hex));cls.user=q.lastrowid
            q.execute("INSERT INTO user_roles(user_id,role_id) SELECT %s,id FROM roles WHERE code='OWNER'",(cls.user,))
            q.execute('SELECT id,karat_value FROM karats');cls.karats={r['karat_value']:r['id'] for r in q.fetchall()}
            q.execute("INSERT INTO old_gold_movements(movement_type_id,karat_id,weight_delta_grams,created_by_user_id) SELECT id,%s,10000,%s FROM old_gold_movement_types WHERE code='OPENING_BALANCE'",(cls.karats[21],cls.user))
            assert q.rowcount==1
            auth=AuthenticationRepository(c);identity=auth.get_identity(cls.user);permissions=auth.effective_permissions(cls.user)
            cls.session=AppSession();cls.session._establish(AuthenticationResult(cls.user,emp,identity['display_name'],permissions,_VERIFIED),int(cls.session.generation))
            q.close()
        cls.supplier_service=SupplierService(cls.session,transaction_factory=cls.tx)
        cls.sup18=cls.supplier_service.create_supplier('047 inactive supplier '+uuid4().hex[:12],karat_id=cls.karats[18])
        cls.sup24=cls.supplier_service.create_supplier('047 24K supplier '+uuid4().hex[:12],karat_id=cls.karats[24])
        with cls.tx() as c:
            q=c.cursor();q.execute('UPDATE suppliers SET is_active=0 WHERE id=%s',(cls.sup18,));q.close()

    @classmethod
    @contextmanager
    def tx(cls):
        assert cls.config.database==TARGET
        c=DatabaseConnection(cls.config).connect()
        try:
            q=c.cursor();q.execute('SELECT DATABASE()');assert q.fetchone()==(TARGET,);q.close();c.rollback()
            c.start_transaction(isolation_level='REPEATABLE READ')
            yield c
            c.commit()
        except BaseException:
            c.rollback();raise
        finally:c.close()

    def setUp(self):
        self.service=MarketTripService(self.session,transaction_factory=self.tx)
        self.correct=CorrectionService(self.session,transaction_factory=self.tx)
        self.service._corrections=self.correct

    def query(self,sql,params=()):
        with self.tx() as c:
            q=c.cursor(dictionary=True);q.execute(sql,params);rows=q.fetchall();q.close();return rows

    def lots(self,weight='10',fineness='875',residues=()):
        trip=self.service.issue('','',[{'karat_id':self.karats[21],'weight':'20','designation':'MELT'}],DATE,'047 rehearsal',str(uuid4()))
        melt=self.service.melt(trip['trip_id'],[{'karat_id':self.karats[21],'weight':'20'}],DATE,'047 workshop',{'weight':weight,'fineness':fineness},[{'weight':w,'fineness':f} for w,f in residues],'047 assay','','',str(uuid4()))
        rows=self.query('SELECT * FROM market_trip_lots WHERE entry_id=%s ORDER BY id',(melt['entry_id'],))
        return trip,melt,rows

    def settle(self,trip,lot,supplier=None,key=None,**kw):
        return self.service.settle_supplier_lot(trip['trip_id'],supplier or self.sup18,str(lot['id']),DATE,'047 ref','047 notes',key or str(uuid4()),**kw)

    def snapshot(self):
        tables=('supplier_gold_settlements','supplier_gold_settlement_legs','supplier_gold_movements','market_trip_entries','market_trip_lot_movements','market_trip_lot_settlements','transaction_corrections','transaction_correction_legs','audit_log')
        return {t:self.query(f'SELECT COUNT(*) AS n FROM {t}')[0]['n'] for t in tables}

    def test_original_exact_effects_replay_reversal_and_history(self):
        trip,melt,lots=self.lots();lot=lots[0];key=str(uuid4())
        unchanged={t:self.query(f'SELECT COUNT(*) AS n FROM {t}') for t in ('old_gold_movements','cash_movements','bank_movements','supplier_making_movements','market_trip_lot_shop_movements','inventory_items')}
        done=self.settle(trip,lot,key=key)
        self.assertEqual((done['credited_weight'],done['analytical'],done['rounding_difference']),('11.667','11.666667','0.000333'))
        for t,n in unchanged.items():self.assertEqual(n,self.query(f'SELECT COUNT(*) AS n FROM {t}'),t)
        self.assertEqual(self.settle(trip,lot,key=key)['settlement_id'],done['settlement_id'])
        detail=self.supplier_service.supplier_detail(self.sup18)
        self.assertLess(detail['balances']['gold_balance'],0)
        sg=SupplierGoldSettlementService(self.session,transaction_factory=self.tx).settlement_detail(done['settlement_id'])
        self.assertEqual(str(sg['lot_id']),str(lot['id']))
        with self.assertRaises(CorrectionError) as e:self.correct.reverse('SUPPLIER_GOLD_SETTLEMENT',done['settlement_id'],'047')
        self.assertIn(done['code'],str(e.exception))
        with self.assertRaises(CorrectionError):self.correct.reverse('MARKET_TRIP_ENTRY',melt['entry_id'],'047')
        gold=self.query("SELECT * FROM supplier_gold_movements WHERE source_type='SUPPLIER_GOLD_SETTLEMENT' AND source_id=%s",(done['settlement_id'],))[0]
        rv=self.correct.reverse('MARKET_TRIP_ENTRY',done['entry_id'],'047 reverse')
        reversed_gold=self.query("SELECT * FROM supplier_gold_movements WHERE source_type='TRANSACTION_CORRECTION' AND source_id=%s",(rv['correction_id'],))[0]
        self.assertEqual((reversed_gold['weight_delta_grams'],reversed_gold['equivalent_21_delta_grams']),(-gold['weight_delta_grams'],-gold['equivalent_21_delta_grams']))
        self.assertEqual(self.service.trip_detail(trip['trip_id'])['custody']['lots'][0]['lot_id'],str(lot['id']))
        self.assertEqual(self.settle(trip,lot,key=key)['settlement_id'],done['settlement_id'])
        self.settle(trip,lot)
        self.service.close(trip['trip_id']);self.service.reopen(trip['trip_id'],'047 reopen')

    def test_boundaries_known_unknown_residues_and_wrong_quantities(self):
        t,m,l=self.lots('4.999','0.1',residues=[('1','500'),('2','')]);before=self.snapshot()
        with self.assertRaises(SupplierRuleError) as e:self.settle(t,l[0],self.sup24)
        self.assertEqual(e.exception.code,'CREDIT_ROUNDS_TO_ZERO');self.assertEqual(before,self.snapshot())
        self.settle(t,l[1]);
        with self.assertRaises(SupplierRuleError):self.settle(t,l[2])
        t,m,l=self.lots('5','0.1');done=self.settle(t,l[0],self.sup24);self.assertEqual(done['credited_weight'],'0.001')
        t,m,l=self.lots()
        with self.assertRaises(TripError):self.settle(t,l[0],weight='9.999')
        other,_,_=self.lots()
        with self.assertRaises(TripError):self.settle(other,l[0])
        self.settle(t,l[0])
        with self.assertRaises(TripError):self.settle(t,l[0])
        with self.assertRaises(TripError):self.service.return_lots(t['trip_id'],[{'lot_id':str(l[0]['id'])}],DATE,'',str(uuid4()))

    def test_reissue_origin_stays_closed_dependency_then_same_identity(self):
        t,m,l=self.lots();lot=l[0]
        self.service.return_lots(t['trip_id'],[{'lot_id':str(lot['id'])}],DATE,'',str(uuid4()))
        self.service.close(t['trip_id'])
        current=self.service.create_trip('','',[{'lot_id':str(lot['id'])}],DATE,'',str(uuid4()))
        done=self.settle(current,lot)
        issue=self.query("SELECT id FROM market_trip_entries WHERE trip_id=%s AND entry_kind='LOT_ISSUE'",(current['trip_id'],))[0]
        with self.assertRaises(CorrectionError):self.correct.reverse('MARKET_TRIP_ENTRY',issue['id'],'047')
        self.correct.reverse('MARKET_TRIP_ENTRY',done['entry_id'],'047')
        self.assertEqual(self.service.trip_detail(current['trip_id'])['custody']['lots'][0]['lot_id'],str(lot['id']))
        self.assertEqual(self.query('SELECT status FROM market_trips WHERE id=%s',(t['trip_id'],))[0]['status'],'CLOSED')

    def test_posting_rollback_after_each_persistent_stage(self):
        t,m,l=self.lots()
        stages=((SupplierGoldSettlementRepository,'insert_lot_header'),(MarketTripRepository,'insert_entry'),(MarketTripRepository,'insert_lot_movement'),(SupplierGoldSettlementRepository,'insert_gold_movement'),(SupplierGoldSettlementRepository,'insert_leg'),(MarketTripRepository,'insert_lot_settlement_link'),(SupplierGoldSettlementRepository,'audit'),(MarketTripRepository,'audit'))
        for cls,name in stages:
            original=getattr(cls,name)
            def fail(instance,*a,_original=original,**kw):_original(instance,*a,**kw);raise RuntimeError('047 injected')
            before=self.snapshot()
            with self.subTest(stage=name),patch.object(cls,name,fail),self.assertRaisesRegex(RuntimeError,'047 injected'):self.settle(t,l[0])
            self.assertEqual(before,self.snapshot(),name)

    def test_reversal_rollback_after_each_persistent_stage(self):
        t,m,l=self.lots();done=self.settle(t,l[0])
        # A custody gold reversal writes exactly the correction, supplier gold movement and audit; it has no correction leg.
        stages=((CorrectionRepository,'insert_correction'),(SupplierGoldSettlementRepository,'insert_gold_movement'),(CorrectionService,'_audit'))
        for cls,name in stages:
            original=getattr(cls,name)
            def fail(instance,*a,_original=original,**kw):_original(instance,*a,**kw);raise RuntimeError('047 injected')
            before=self.snapshot()
            replacement=staticmethod(fail) if isinstance(cls.__dict__.get(name),staticmethod) else fail
            with self.subTest(stage=name),patch.object(cls,name,replacement),self.assertRaisesRegex(RuntimeError,'047 injected'):self.correct.reverse('MARKET_TRIP_ENTRY',done['entry_id'],'047')
            self.assertEqual(before,self.snapshot(),name)

    def test_two_thread_same_lot_smoke_with_shared_authorization_locks(self):
        t,m,l=self.lots();barrier=threading.Barrier(2);results=[]
        def run():
            service=MarketTripService(self.session,transaction_factory=self.tx)
            try:
                barrier.wait(timeout=10)
                results.append(service.settle_supplier_lot(t['trip_id'],self.sup18,str(l[0]['id']),DATE,'','',str(uuid4())))
            except BaseException as e:results.append(e)
        threads=[threading.Thread(target=run) for _ in range(2)]
        for thread in threads:thread.start()
        for thread in threads:thread.join(timeout=20);self.assertFalse(thread.is_alive())
        self.assertEqual(sum(isinstance(r,dict) for r in results),1)
        self.assertEqual(sum(isinstance(r,TripError) and r.code=='LOT_NOT_IN_CUSTODY' for r in results),1)

    def test_composite_foreign_keys_and_explicit_source_shapes(self):
        t,m,l=self.lots();done=self.settle(t,l[0]);other,otherm,otherlots=self.lots()
        movement=self.query('SELECT * FROM market_trip_lot_settlements WHERE entry_id=%s',(done['entry_id'],))[0]['custody_movement_id']
        probes=(
            ('UPDATE supplier_gold_settlements SET delivered_karat_id=%s,delivered_karat_value=21 WHERE id=%s',(self.karats[21],done['settlement_id']),3819),
            ('UPDATE supplier_gold_settlements SET delivered_fineness_permille=874.9 WHERE id=%s',(done['settlement_id'],),(1451,1452)),
            ('UPDATE supplier_gold_settlements SET delivered_lot_weight_grams=9 WHERE id=%s',(done['settlement_id'],),3819),
            ('UPDATE supplier_gold_settlements SET origin_melt_entry_id=%s WHERE id=%s',(otherm['entry_id'],done['settlement_id']),(1451,1452)),
            ('UPDATE market_trip_lot_settlements SET supplier_id=%s WHERE entry_id=%s',(self.sup24,done['entry_id']),1452),
            ('UPDATE market_trip_lot_settlements SET lot_id=%s WHERE entry_id=%s',(otherlots[0]['id'],done['entry_id']),1452),
            ('UPDATE market_trip_lot_settlements SET movement_type=\'CREATE\' WHERE entry_id=%s',(done['entry_id'],),3819),
            ('UPDATE market_trip_entries SET entry_kind=\'ISSUE\' WHERE id=%s',(done['entry_id'],),1451),
            ('UPDATE market_trip_lot_movements SET weight_delta=10 WHERE id=%s',(movement,),3819),
            ('UPDATE supplier_gold_settlements SET delivered_fineness_permille=NULL WHERE id=%s',(done['settlement_id'],),3819),
        )
        for sql,args,errno in probes:
            with self.subTest(sql=sql):
                with self.assertRaises(Exception) as e:
                    with self.tx() as c:
                        q=c.cursor();q.execute(sql,args);q.close()
                        self.fail('invalid shape accepted')
                # a row that is both a child (fk_sgs_lot_facts: 1452) and a parent (fk_mtls_header: 1451) is refused by whichever constraint InnoDB evaluates first;
                # the evaluation order depends on the table's constraint set (it changed when 050 added foreign keys): the refusal is the contract
                self.assertIn(getattr(e.exception,'errno',None),errno if isinstance(errno,tuple) else (errno,))

    def rollback_refusal(self, stage):
        from tools.schema_rehearsal.mysql_client import Credentials, option_file, MysqlClient, DEFAULT_MYSQL, MysqlError
        from tools.schema_rehearsal.guards import prepare_sql
        from pathlib import Path
        sql=prepare_sql('047_supplier_gold_whole_lot_settlement_rollback.sql',Path('database/schema/rollback/047_supplier_gold_whole_lot_settlement_rollback.sql').read_text(encoding='utf8'))
        with option_file(Credentials(self.config.host,self.config.port,self.config.user,self.config.password)) as options:
            before=self.snapshot()
            with self.assertRaises(MysqlError) as e:MysqlClient(DEFAULT_MYSQL,options).run_script(sql,TARGET)
            self.assertIn('ROLLBACK_047_REFUSED',str(e.exception).upper());self.assertEqual(before,self.snapshot(),stage)

    @unittest.skipUnless(os.environ.get('MASGOLD_LOT_SETTLE_REHEARSAL')=='1','explicit rehearsal-only opt-in')
    def test_047_rollback_refuses_because_047_business_history_exists_on_a_schema_at_exactly_047(self):
        """Run ONLY on a disposable schema genuinely at 047 (no 048 object, no later migration): the refusal can only be the 047 history."""
        if self.version!=47:self.skipTest('needs a sandbox at exactly 047 (the 048-aware code runs the other tests at 048)')
        self.assertEqual(self.query('SELECT MAX(version) AS v FROM schema_migrations')[0]['v'],47)
        t,m,l=self.lots();done=self.settle(t,l[0])
        self.rollback_refusal('settled history');self.assertEqual(self.query('SELECT MAX(version) AS v FROM schema_migrations')[0]['v'],47)
        with self.tx() as c:                                   # reversed consumption is history too: the stored rows stay
            q=c.cursor();q.execute("INSERT INTO transaction_corrections(correction_type,source_type,source_id,source_reference,invoice_id,reason,performed_by_user_id,occurred_at) VALUES('FULL_REVERSAL','MARKET_TRIP_ENTRY',%s,'047 probe',NULL,'047 rollback probe',%s,NOW())",(done['entry_id'],self.user));q.close()
        self.rollback_refusal('reversed history');self.assertEqual(self.query('SELECT MAX(version) AS v FROM schema_migrations')[0]['v'],47)

    def test_047_rollback_refuses_while_a_later_migration_exists_at_048(self):
        """At 048 the guard also refuses for the later migration; the isolated case (clean 001-048, zero 047 rows) was run by the rehearsal script."""
        if self.version not in (48,49,50,51):self.skipTest('needs a sandbox at 048 or later')
        history=sum(self.query(sql)[0]['n'] for sql in ('SELECT COUNT(*) AS n FROM market_trip_lot_settlements',"SELECT COUNT(*) AS n FROM supplier_gold_settlements WHERE source_kind='CUSTODY_LOT'",
            "SELECT COUNT(*) AS n FROM market_trip_entries WHERE entry_kind='LOT_SETTLE'","SELECT COUNT(*) AS n FROM market_trip_lot_movements WHERE movement_type='SUPPLIER_SETTLE'"))
        if history:self.skipTest(f'{history} 047 history rows exist: a refusal here could be the history guard, so it would not isolate the later-migration guard (needs a clean 001-048+ schema)')
        self.rollback_refusal('later migration');self.assertEqual(self.query('SELECT MAX(version) AS v FROM schema_migrations')[0]['v'],self.version)

    def test_controlled_lot_block_and_current_trip_lock_without_authorization_holder(self):
        """A raw holder does not acquire actor/grant locks; this is a resource-lock probe, not a two-writer stress test."""
        t,m,l=self.lots();entered=threading.Event();finished=threading.Event();results=[]
        original=MarketTripRepository.lock_lots
        def observed(repo,ids):entered.set();return original(repo,ids)
        def run():
            try:results.append(self.settle(t,l[0]))
            except BaseException as e:results.append(e)
            finally:finished.set()
        with self.tx() as holder:
            q=holder.cursor();q.execute('SELECT id FROM market_trip_lots WHERE id=%s FOR UPDATE',(l[0]['id'],));q.fetchall();q.close()
            with patch.object(MarketTripRepository,'lock_lots',observed):
                worker=threading.Thread(target=run);worker.start()
                try:
                    self.assertTrue(entered.wait(10))
                    with self.assertRaises(Exception) as e:
                        with self.tx() as observer:
                            q=observer.cursor();q.execute('SELECT id FROM market_trips WHERE id=%s FOR UPDATE NOWAIT',(t['trip_id'],));q.fetchall();q.close()
                    self.assertEqual(getattr(e.exception,'errno',None),3572)
                    self.assertFalse(finished.is_set())
                finally:holder.commit();worker.join(15)
        self.assertFalse(worker.is_alive());self.assertEqual(len(results),1);self.assertIsInstance(results[0],dict)

    def test_reissued_settlement_does_not_wait_for_its_origin_trip_lock(self):
        t,m,l=self.lots();lot=l[0]
        self.service.return_lots(t['trip_id'],[{'lot_id':str(lot['id'])}],DATE,'',str(uuid4()))
        self.service.close(t['trip_id'])
        current=self.service.create_trip('','',[{'lot_id':str(lot['id'])}],DATE,'',str(uuid4()))
        results=[]
        def run():
            try:results.append(self.settle(current,lot))
            except BaseException as e:results.append(e)
        with self.tx() as holder:
            q=holder.cursor();q.execute('SELECT id FROM market_trips WHERE id=%s FOR UPDATE',(t['trip_id'],));q.fetchall();q.close()
            worker=threading.Thread(target=run);worker.start();worker.join(10)
            alive=worker.is_alive()
            holder.commit()
            worker.join(15)
        self.assertFalse(alive,'reissued lot incorrectly waits for origin trip')
        self.assertEqual(len(results),1);self.assertIsInstance(results[0],dict)

    def test_failure_before_commit_rolls_back_posting_and_reversal(self):
        t,m,l=self.lots()
        @contextmanager
        def no_commit():
            with self.tx() as c:
                yield c
                raise RuntimeError('047 before commit')
        before=self.snapshot()
        service=MarketTripService(self.session,transaction_factory=no_commit)
        with self.assertRaisesRegex(RuntimeError,'before commit'):
            service.settle_supplier_lot(t['trip_id'],self.sup18,str(l[0]['id']),DATE,'','',str(uuid4()))
        self.assertEqual(before,self.snapshot())
        done=self.settle(t,l[0]);before=self.snapshot()
        correct=CorrectionService(self.session,transaction_factory=no_commit)
        with self.assertRaisesRegex(RuntimeError,'before commit'):correct.reverse('MARKET_TRIP_ENTRY',done['entry_id'],'047')
        self.assertEqual(before,self.snapshot())

    def test_legacy_partial_outstanding_refuses_without_completing_or_rewriting_it(self):
        t,m,l=self.lots();lot=l[0]
        # Rehearsal fixture for an earlier-build partial return, inserted as immutable historical rows.
        with self.tx() as c:
            repo=MarketTripRepository(c);actor=self.session.current_actor();now=repo.db_now()
            repo.trip(t['trip_id'],lock=True);repo.lock_lots([lot['id']])
            entry=repo.insert_entry(trip_id=t['trip_id'],entry_no=repo.next_entry_no(t['trip_id']),kind='LOT_RETURN',entry_date=DATE,
                notes='synthetic earlier-build partial return',key=str(uuid4()),digest='0'*64,actor=actor,now=now)
            custody=repo.insert_lot_movement(lot_id=lot['id'],entry_id=entry,movement_type='SHOP_RETURN',weight_delta=D('-1'))
            shop=repo.insert_shop_lot_movement(lot_id=lot['id'],entry_id=entry,movement_type=SHOP_RETURN_IN,weight_delta=D('1'))
            repo.insert_lot_return(entry_id=entry,lot_id=lot['id'],weight=D('1'),custody_movement_id=custody,shop_movement_id=shop)
        before=self.snapshot()
        with self.assertRaises(TripError) as e:self.settle(t,lot)
        self.assertEqual(e.exception.code,'LOT_PARTIAL_NOT_ALLOWED');self.assertEqual(before,self.snapshot())
        self.assertEqual(self.service.trip_detail(t['trip_id'])['custody']['lots'][0]['outstanding'],D('9'))

    def test_existing_scrap_logo_flows_and_request_key_conflicts(self):
        direct=SupplierGoldSettlementService(self.session,transaction_factory=self.tx)
        key=str(uuid4());scrap=direct.settle(self.sup18,self.karats[21],'1',DATE,'','',key)
        self.assertEqual(scrap['credited_weight'],'1.167')
        self.assertEqual(self.query('SELECT source_kind FROM supplier_gold_settlements WHERE id=%s',(scrap['id'],))[0]['source_kind'],'SHOP_SCRAP')
        logo=self.service.issue('','',[{'karat_id':self.karats[21],'weight':'2','designation':'LOGO'}],DATE,'',str(uuid4()))
        done=self.service.settle_supplier_gold(logo['trip_id'],self.sup18,self.karats[21],'LOGO','1',DATE,'','',str(uuid4()))
        self.assertEqual(done['credited_weight'],'1.167')
        t,m,l=self.lots()
        before=self.snapshot()
        with self.assertRaises(TripError) as e:self.settle(t,l[0],key=key)
        self.assertEqual(e.exception.code,'REQUEST_CONFLICT');self.assertEqual(before,self.snapshot())

        key=str(uuid4());lot_done=self.settle(t,l[0],key=key);before=self.snapshot()
        with self.assertRaises(SupplierRuleError) as e:direct.settle(self.sup18,self.karats[21],'1',DATE,'','',key)
        self.assertEqual(e.exception.code,'REQUEST_CONFLICT');self.assertEqual(before,self.snapshot())
        with self.assertRaises(TripError) as e:self.settle(t,l[0],supplier=self.sup24,key=key)
        self.assertEqual(e.exception.code,'REQUEST_CONFLICT');self.assertEqual(before,self.snapshot())

    @staticmethod
    @contextmanager
    def on_connection(connection):
        """Reuse the reader's existing RR transaction, including its earlier snapshot."""
        yield connection

    def test_rr_snapshot_before_committed_consumption_sees_dependency_and_refuses_melt_reverse(self):
        trip,melt,lots=self.lots();lot=lots[0]
        with self.tx() as reader:
            q=reader.cursor()
            q.execute('SELECT @@session.transaction_isolation');self.assertEqual(q.fetchone(),('REPEATABLE-READ',))
            q.execute('SELECT COUNT(*) FROM market_trip_lot_movements WHERE lot_id=%s',(lot['id'],))
            original_count=q.fetchone()[0]
            done=self.settle(trip,lot)  # separate connection commits after the reader's snapshot
            q.execute('SELECT COUNT(*) FROM market_trip_lot_movements WHERE lot_id=%s',(lot['id'],))
            self.assertEqual(q.fetchone()[0],original_count)  # prove the earlier consistent snapshot remains
            q.close()
            repo=MarketTripRepository(reader)
            repo.trip(trip['trip_id'],lock=True);repo.lock_lots([lot['id']])
            self.assertIn(int(done['entry_id']),[int(r['entry_id']) for r in repo.lot_downstream(melt['entry_id'])])
            correct=CorrectionService(self.session,transaction_factory=lambda:self.on_connection(reader))
            with self.assertRaises(CorrectionError) as error:
                correct.reverse('MARKET_TRIP_ENTRY',melt['entry_id'],'047 RR dependency regression')
            self.assertEqual(error.exception.code,'DEPENDENT_LOT_USE_EXISTS')

    def test_rr_snapshot_before_reversed_return_preserves_effective_filters(self):
        trip,melt,lots=self.lots();lot=lots[0]
        returned=self.service.return_lots(trip['trip_id'],[{'lot_id':str(lot['id'])}],DATE,'',str(uuid4()))
        with self.tx() as reader:
            q=reader.cursor();q.execute('SELECT COUNT(*) FROM transaction_corrections');original_count=q.fetchone()[0]
            self.correct.reverse('MARKET_TRIP_ENTRY',returned['entry_id'],'047 RR reversed dependency regression')
            q.execute('SELECT COUNT(*) FROM transaction_corrections');self.assertEqual(q.fetchone()[0],original_count)
            q.close()
            repo=MarketTripRepository(reader)
            repo.trip(trip['trip_id'],lock=True);repo.lock_lots([lot['id']])
            self.assertEqual(repo.lot_downstream(melt['entry_id']),[])
            self.assertEqual(repo.custody_lots(trip['trip_id'],lot_id=lot['id'])[0]['outstanding_grams'],lot['weight_grams'])
            self.assertEqual(repo.shop_lot_balance(lot['id']),D('0'))

    def test_rr_snapshot_before_reissue_sees_shop_consumption_and_refuses_return_reverse(self):
        trip,melt,lots=self.lots();lot=lots[0]
        returned=self.service.return_lots(trip['trip_id'],[{'lot_id':str(lot['id'])}],DATE,'',str(uuid4()))
        with self.tx() as reader:
            q=reader.cursor();q.execute('SELECT COALESCE(SUM(weight_delta),0) FROM market_trip_lot_shop_movements WHERE lot_id=%s',(lot['id'],))
            self.assertEqual(q.fetchone()[0],lot['weight_grams'])
            self.service.create_trip('','',[{'lot_id':str(lot['id'])}],DATE,'',str(uuid4()))
            q.execute('SELECT COALESCE(SUM(weight_delta),0) FROM market_trip_lot_shop_movements WHERE lot_id=%s',(lot['id'],))
            self.assertEqual(q.fetchone()[0],lot['weight_grams']);q.close()
            repo=MarketTripRepository(reader)
            repo.trip(trip['trip_id'],lock=True);repo.lock_lots([lot['id']])
            self.assertEqual(repo.shop_lot_balance(lot['id']),D('0'))
            correct=CorrectionService(self.session,transaction_factory=lambda:self.on_connection(reader))
            with self.assertRaises(CorrectionError) as error:
                correct.reverse('MARKET_TRIP_ENTRY',returned['entry_id'],'047 RR reissue regression')
            self.assertEqual(error.exception.code,'SHOP_LOT_STOCK_INSUFFICIENT')
