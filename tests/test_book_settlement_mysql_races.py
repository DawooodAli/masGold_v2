"""Explicit opt-in 057 session/date and controlled transaction schedules.

Needs MASGOLD_SETTLEMENT_REHEARSAL=1, MASGOLD_REHEARSAL_PASSWORD,
MASGOLD_REHEARSAL_EVIDENCE_DIR. Rehearsal localhost only. Fixtures retained.
Test-only unlocked authorization isolates trip locks without changing production.
"""
import os
import unittest

@unittest.skipUnless(os.environ.get('MASGOLD_SETTLEMENT_REHEARSAL') == '1', 'explicit rehearsal-only opt-in')
class SettlementMySqlRaceTests(unittest.TestCase):
    def test_remaining_controlled_schedules(self):
        """Focused 057 live acceptance; only dedicated rehearsal credentials, no dotenv."""
        import os,sys,json,threading
        from pathlib import Path
        from decimal import Decimal as D, ROUND_HALF_UP
        from unittest.mock import patch
        ROOT=Path(os.environ['MASGOLD_REHEARSAL_EVIDENCE_DIR']).resolve()
        sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
        import dotenv
        with patch.object(dotenv,'load_dotenv',return_value=False):
            from config.database_config import DatabaseConfig
            from tests.test_market_trip_book_mysql import BookCoreMySqlTests,karat
            from repositories.market_trip_repository import MarketTripRepository as Repo
            from services import book_settlement as bs
            from services.market_trip_service import TripError
            from mysql.connector import Error as SqlError
        from tools.schema_rehearsal.mysql_client import Credentials,option_file,MysqlClient,DEFAULT_MYSQL,MysqlError
        from tools.schema_rehearsal.rehearsal import Rehearsal
        from tools.schema_rehearsal.chain import load_chain

        OUT=ROOT/'mysql-acceptance-remaining'
        OUT.mkdir(exist_ok=True)
        assert not (OUT/'results.json').exists()
        results=[]
        def check(name,fn):
            try: fn(); results.append({'case':name,'status':'PASS'})
            except Exception as exc:
                results.append({'case':name,'status':'FAIL','error':type(exc).__name__+': '+str(exc)})
            (OUT/'results.json').write_text(json.dumps(results,indent=2,default=str),encoding='utf-8')
            print(results[-1],flush=True)

        config=DatabaseConfig('localhost',3306,'masgold_rehearsal','masgold_build',os.environ['MASGOLD_REHEARSAL_PASSWORD'])
        with patch.object(DatabaseConfig,'from_env',return_value=config): BookCoreMySqlTests.setUpClass()
        case=BookCoreMySqlTests('test_the_migration_is_registered_and_the_objects_exist')
        service=case.service
        service._reconciliation_enabled=True
        ALL={k:True for k in bs.CONFIRMATION_KEYS}
        def args(trip,key=None):
            with case.tx() as c: digest=bs.build(Repo(c).reconciliation_source_current(trip))[3]
            return [trip,digest,None,'OPERATING_LOSS',None,dict(ALL),dict(bs.CONTRACT_VERSIONS),key or case.key()]
        def reconcile(trip): return service.reconcile(*args(trip))
        from contextlib import contextmanager
        from datetime import datetime,timedelta
        from mysql.connector import IntegrityError
        from services.market_trip_book_service import MarketTripBookService
        from services.correction_service import CorrectionService
        from tests.test_market_trip_hardening_mysql import UnlockedAuthorization
        from tests.test_market_trip_book_mysql import REFERENCE

        def fail_code(code,fn):
            try:fn()
            except TripError as exc:assert exc.code==code,(code,exc.code)
            else:raise AssertionError('Expected '+code)

        def totals(): return case.counts(('market_trip_entries','market_trip_reconciliations','audit_log','transaction_corrections','old_gold_movements','cash_movements'))
        def make_service(tx,unlocked=False):
            auth=UnlockedAuthorization(case.session) if unlocked else None
            corrector=CorrectionService(case.session,transaction_factory=tx,authorization=auth)
            return MarketTripBookService(case.session,transaction_factory=tx,authorization=auth,corrections=corrector,price_reference=lambda:dict(REFERENCE),book_creation_enabled=True,reconciliation_enabled=True)

        def expiry():
            trip=case.book_trip('1'); p=args(trip); before=totals(); wrote=[]
            original=Repo.audit
            def audit(repo,*a,**kw): original(repo,*a,**kw); wrote.append(True)
            current=type(case.session).is_current
            def is_current(session,*a,**kw): return False if wrote else current(session,*a,**kw)
            with patch.object(Repo,'audit',audit),patch.object(type(case.session),'is_current',is_current):
                try: service.reconcile(*p)
                except PermissionError: pass
                else: raise AssertionError('Expected session expiry')
            assert wrote and totals()==before
        check('session expires after audit before commit, real rollback',expiry)

        def midnight():
            trip=case.book_trip('1'); p=args(trip)
            @contextmanager
            def at_clock(value):
                with case.tx() as c:
                    q=c.cursor(); q.execute('SET timestamp=UNIX_TIMESTAMP(%s)',(value,)); q.close()
                    yield c
            first_service=make_service(lambda:at_clock('2026-10-19 23:59:59'))
            first=first_service.reconcile(*p)
            after=make_service(lambda:at_clock('2026-10-20 00:00:01'))
            again=after.reconcile(*p)
            assert again['entry_id']==first['entry_id'] and again['effective_date']=='2026-10-19'
            explicit=list(p); explicit[2]='2026-10-19'
            fail_code('REQUEST_CONFLICT',lambda:after.reconcile(*explicit))
        check('MySQL session clock crosses midnight, TODAY replay and explicit-date conflict',midnight)

        def recovery():
            trip=case.book_trip('1'); p=args(trip); first=service.reconcile(*p); seen=[]
            original=Repo.trip
            def trip_read(repo,tid,lock=False):
                seen.append(lock); return original(repo,tid,lock=lock)
            error=IntegrityError(msg="Duplicate entry for key 'uq_mte_request_key'",errno=1062)
            with patch.object(service,'_post_reconciliation',side_effect=error),patch.object(Repo,'trip',trip_read):
                again=service.reconcile(*p)
            assert again['entry_id']==first['entry_id'] and again['duplicate'] and True in seen
        check('injected duplicate-key recovery uses real locked SQL replay',recovery)

        def json_order():
            trip=case.book_trip('1'); p=args(trip); first=service.reconcile(*p)
            with case.tx() as c:
                q=c.cursor(); q.execute('SELECT snapshot FROM market_trip_reconciliations WHERE entry_id=%s',(first['entry_id'],))
                snapshot=json.loads(q.fetchone()[0]); text=json.dumps(dict(reversed(list(snapshot.items()))),ensure_ascii=False,indent=3)
                q.execute('UPDATE market_trip_reconciliations SET snapshot=%s WHERE entry_id=%s',(text,first['entry_id'])); q.close()
                @contextmanager
                def held(): yield c
                again=make_service(held).reconcile(*p)
                assert again['entry_id']==first['entry_id'] and again['snapshot_digest']==first['snapshot_digest']
                c.rollback()
        check('driver JSON key order and whitespace do not change canonical replay',json_order)

        def schedule(gold_kind,recon_first):
            trip=case.book_trip('10',cash='1000')
            p=args(trip); attempt=threading.Event(); outcomes=[]
            @contextmanager
            def bounded():
                with case.tx() as c:
                    q=c.cursor(); q.execute('SET SESSION innodb_lock_wait_timeout=12'); q.close(); yield c
            second=make_service(bounded,True)
            key=case.key()
            def gold(s):
                if gold_kind=='ISSUE': return s.book_issue(trip,[karat(case.karats[21],'.1')],'2026-10-17','',key)
                if gold_kind=='RETURN': return s.book_return(trip,[karat(case.karats[21],'.1')],'2026-10-17','',key)
                if gold_kind=='BUY': return s.book_buy(trip,[karat(case.karats[21],'.1','1')],'','test vendor','2026-10-17','','',key,True)
                if gold_kind=='SELL': return s.book_sell(trip,[karat(case.karats[21],'.1','1')],'','test buyer','2026-10-17','','',key,True)
                if gold_kind=='CASH': return s.issue(trip,'1',[],'2026-10-17','',key)
            original=Repo.trip
            def trip_read(repo,tid,lock=False):
                if threading.current_thread().name=='acceptance-waiter' and lock: attempt.set()
                return original(repo,tid,lock=lock)
            def run():
                try: outcomes.append(('ok',gold(second) if recon_first else second.reconcile(*p)))
                except TripError as exc:
                    print('RACE DIAGNOSTICS',gold_kind,recon_first,json.dumps(getattr(exc,'diagnostics',None),ensure_ascii=True),flush=True)
                    outcomes.append(('refused',exc.code))
                except Exception as exc: outcomes.append(('error',type(exc).__name__+': '+str(exc)))
            with patch.object(Repo,'trip',trip_read):
                with bounded() as c:
                    original(Repo(c),trip,lock=True)
                    @contextmanager
                    def held(): yield c
                    first=make_service(held,True)
                    thread=threading.Thread(target=run,name='acceptance-waiter',daemon=True); thread.start()
                    assert attempt.wait(8),'second did not reach trip lock'
                    if recon_first: first.reconcile(*p)
                    else: gold(first)
                thread.join(18)
                assert not thread.is_alive(),'timeout'
            expected='RECONCILIATION_FINAL' if recon_first else 'RECONCILIATION_STALE'
            if gold_kind=='CASH': assert len(outcomes)==1 and outcomes[0][0]=='ok',outcomes
            else: assert outcomes==[('refused',expected)],outcomes
        for kind in ('ISSUE','RETURN','BUY','SELL','CASH'):
            for first in (True,False): check('trip contention '+kind+(' reconciliation first' if first else 'posting first'),lambda k=kind,f=first:schedule(k,f))

        def reverse_source(recon_first):
            trip=case.book_trip('10')
            returned=service.book_return(trip,[karat(case.karats[21],'1')],'2026-10-17','',case.key())
            p=args(trip); attempt=threading.Event(); outcomes=[]
            second=make_service(case.tx,True)
            original=Repo.trip
            def trip_read(repo,tid,lock=False):
                if threading.current_thread().name=='reverse-waiter' and lock: attempt.set()
                return original(repo,tid,lock=lock)
            def run():
                try: outcomes.append(('ok',second.reverse(returned['entry_id'],'acceptance') if recon_first else second.reconcile(*p)))
                except Exception as exc: outcomes.append(('refused',getattr(exc,'code',type(exc).__name__)))
            with patch.object(Repo,'trip',trip_read):
                with case.tx() as c:
                    original(Repo(c),trip,lock=True)
                    @contextmanager
                    def held(): yield c
                    first=make_service(held,True)
                    thread=threading.Thread(target=run,name='reverse-waiter',daemon=True); thread.start()
                    assert attempt.wait(8)
                    if recon_first: first.reconcile(*p)
                    else: first.reverse(returned['entry_id'],'acceptance')
                thread.join(18); assert not thread.is_alive()
            expected='RECONCILIATION_FINAL' if recon_first else 'RECONCILIATION_STALE'
            assert outcomes==[('refused',expected)],outcomes
        for first in (True,False): check('source reversal vs reconciliation '+str(first),lambda f=first:reverse_source(f))

        def reverse_recon_post(reverse_first):
            trip=case.book_trip('1'); initial=service.reconcile(*args(trip))
            event=threading.Event(); outcomes=[]; second=make_service(case.tx,True); original=Repo.trip
            def trip_read(repo,tid,lock=False):
                if threading.current_thread().name=='recon-reverse-waiter' and lock:event.set()
                return original(repo,tid,lock=lock)
            def gold(s):return s.book_issue(trip,[karat(case.karats[21],'.1')],'2026-10-17','',case.key())
            def run():
                try:outcomes.append(('ok',gold(second) if reverse_first else second.reverse(initial['entry_id'],'acceptance')))
                except Exception as exc:outcomes.append(('refused',getattr(exc,'code',type(exc).__name__)))
            with patch.object(Repo,'trip',trip_read):
                with case.tx() as c:
                    original(Repo(c),trip,lock=True)
                    @contextmanager
                    def held():yield c
                    first=make_service(held,True)
                    th=threading.Thread(target=run,name='recon-reverse-waiter',daemon=True);th.start();assert event.wait(8)
                    if reverse_first:first.reverse(initial['entry_id'],'acceptance')
                    else:
                        try:gold(first)
                        except TripError as exc:assert exc.code=='RECONCILIATION_FINAL'
                        else:raise AssertionError('gold passed active reconciliation')
                th.join(18);assert not th.is_alive()
            assert len(outcomes)==1 and outcomes[0][0]=='ok',outcomes
        for first in (True,False):check('reconciliation reversal vs gold posting '+str(first),lambda f=first:reverse_recon_post(f))
        print('PASS',sum(r['status']=='PASS' for r in results),'FAIL',sum(r['status']=='FAIL' for r in results))
        assert all(r['status']=='PASS' for r in results),results
