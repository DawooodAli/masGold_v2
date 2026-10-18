"""Explicit opt-in 057 live acceptance; never loads application .env.

Requires MASGOLD_SETTLEMENT_REHEARSAL=1, MASGOLD_REHEARSAL_PASSWORD,
and MASGOLD_REHEARSAL_EVIDENCE_DIR. Only localhost:3306 / masgold_build /
masgold_rehearsal. Negative updates always roll back; positive fixtures remain.
"""
import os
import unittest


@unittest.skipUnless(os.environ.get('MASGOLD_SETTLEMENT_REHEARSAL') == '1', 'explicit rehearsal-only opt-in')
class SettlementMySqlAcceptanceTests(unittest.TestCase):
    def test_focused_acceptance_batch(self):
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

        OUT=ROOT/'mysql-acceptance-batch'
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
        trip=case.book_trip('1')
        payload=args(trip)
        first=service.reconcile(*payload)
        entry=first['entry_id']
        def fail_code(code,fn):
            try: fn()
            except TripError as exc: assert exc.code==code,(code,exc.code)
            else: raise AssertionError('Expected '+code)
        def replay():
            result=service.reconcile(*payload)
            assert result['entry_id']==entry and result['duplicate']
        check('exact replay real JSON connector roundtrip',replay)
        def json_roundtrip():
            with case.tx() as c: row=Repo(c).reconciliation_of_entry(entry)
            row.update(id=row['entry_id'])
            assert bs.verify_record(row)==[],bs.verify_record(row)
        check('stored snapshot digest and numerical consistency',json_roundtrip)
        check('payload conflict',lambda: fail_code('REQUEST_CONFLICT',lambda: service.reconcile(*[*(payload[:4]),'changed',*payload[5:]])))
        check('new key refuses active reconciliation',lambda: fail_code('RECONCILIATION_EXISTS',lambda: reconcile(trip)))
        check('gold write behind reconciliation refused',lambda: fail_code('RECONCILIATION_FINAL',lambda:service.book_return(trip,[karat(case.karats[21],'0.1')],'2026-10-17','',case.key())))

        def raw_update(fields,accept=False,errno=None):
            # Mutate only inside a transaction which is unconditionally rolled back.
            with case.tx() as c:
                q=c.cursor()
                try:
                    try: q.execute('UPDATE market_trip_reconciliations SET '+','.join(k+'=%s' for k in fields)+' WHERE entry_id=%s',(*fields.values(),entry))
                    except SqlError as exc:
                        if accept: raise
                        assert exc.errno in (3819,1452,1048,1264,3140,4025,1406,1062,1366),exc
                        if errno is not None: assert exc.errno==errno,exc
                    else: assert accept,'Invalid row unexpectedly accepted'
                finally: c.rollback(); q.close()

        for column in ('source_digest','snapshot_digest'):
            for label,value in [('upper','A'*64),('short','a'*63),('long','a'*65),('newline','a'*63+'\n'),('nonascii','é'*64)]:
                check(column+' '+label,lambda col=column,v=value:raw_update({col:v}))
        for label,fields in {
            'nonzero loss requires reason':{'reason_code':None},
            'OTHER requires note':{'reason_code':'OTHER','note':None},
            'UNEXPLAINED refuses blank note':{'reason_code':'UNEXPLAINED','note':'   '},
            'wrong entry kind':{'entry_kind':'BK_ISSUE'},
            'wrong mode':{'trip_mode':'LEGACY'},
            'wrong trip fk':{'trip_id':999999999},
            'wrong user fk':{'confirmed_by_user_id':4294967295},
            'wrong unit denominator':{'unit_denominator':1},
            'array snapshot':{'snapshot':'[]'},
            'invalid JSON':{'snapshot':'{'},
            'counter order':{'reversed_count':4294967295},
            'zero generation':{'generation':0},
            'generation overflow':{'generation':65536},
            'decimal overflow':{'a_star_units':'1'+'0'*30},
            'future effective date':{'effective_date':'2099-01-01'},
            'broken analytical identity':{'a_star_units':1},
            'broken posted identity':{'rounding_settlement':D('.001')},
            'broken delta identity':{'delta_units':1},
            'broken rho identity':{'rho_units':1},
        }.items(): check(label,lambda fields=fields:raw_update(fields))
        for reason in ('OPERATING_LOSS','PURITY_DIFFERENCE','TRADING_LOSS','OTHER','UNEXPLAINED'):
            check('valid reason '+reason,lambda r=reason:raw_update({'reason_code':r,'note':'x'},True))
        for units in (0,3000,13124,13125,13126,26250):
            a=D(units)/D(26250000)
            loss=a.quantize(D('.001'),rounding=ROUND_HALF_UP)
            fields={'a_star_units':units,'a_star':a.quantize(D('.000001'),rounding=ROUND_HALF_UP),'b_star':loss,'loss_posted':loss,'rounding_settlement':0,'delta_units':loss*26250000-units,'rho_units':loss*26250000-units,'sum_abs_units':units,'reason_code':None if loss==0 else 'OPERATING_LOSS'}
            check('lattice boundary '+str(units),lambda f=fields:raw_update(f,True))
        check('maximum generation accepted',lambda:raw_update({'generation':65535},True))

        def counts():
            return case.counts(('market_trip_entries','market_trip_reconciliations','audit_log','old_gold_movements','cash_movements'))
        # Table name is inspected from the repository, not inferred at runtime.
        def atomic(method):
            t=case.book_trip('1'); p=args(t)
            before=counts()
            original=getattr(Repo,method)
            def broken(repo,*a,**kw): original(repo,*a,**kw); raise RuntimeError('acceptance rollback injection')
            with patch.object(Repo,method,broken):
                try: service.reconcile(*p)
                except RuntimeError as exc: assert str(exc)=='acceptance rollback injection'
                else: raise AssertionError('Expected injection')
            assert counts()==before
        for method in ('insert_entry','insert_reconciliation','audit'): check('atomic rollback after '+method,lambda m=method:atomic(m))

        def stale():
            t=case.book_trip('1'); p=args(t)
            service.book_return(t,[karat(case.karats[21],'.1')],'2026-10-17','',case.key())
            fail_code('RECONCILIATION_STALE',lambda:service.reconcile(*p))
        check('stale source refuses',stale)
        def reversed_replay():
            service.reverse(entry,'acceptance reversal')
            replay()
            newer=reconcile(trip)
            assert int(newer['generation'])==2,newer
        check('replay after reversal and next generation',reversed_replay)

        def race(same_key):
            t=case.book_trip('1'); p=args(t); q=list(p)
            if not same_key: q[-1]=case.key()
            barrier=threading.Barrier(2); got=[]
            def post(arg):
                try: barrier.wait(5); got.append(('ok',service.reconcile(*arg)))
                except TripError as exc: got.append(('refused',exc.code))
                except Exception as exc: got.append(('unexpected',type(exc).__name__+str(exc)))
            threads=[threading.Thread(target=post,args=(a,),daemon=True) for a in (p,q)]
            for th in threads: th.start()
            for th in threads: th.join(20)
            assert not any(th.is_alive() for th in threads),'race timeout'
            assert len(got)==2,got
            if same_key: assert all(k=='ok' for k,_ in got) and got[0][1]['entry_id']==got[1][1]['entry_id'],got
            else: assert sorted(k for k,_ in got)==['ok','refused'] and next(v for k,v in got if k=='refused')=='RECONCILIATION_EXISTS',got
        check('concurrent reconcile same request',lambda:race(True))
        check('concurrent reconcile different requests',lambda:race(False))

        def rollback_guard():
            # Execute the refusal prefix ONLY; deliberately never submit the DROP suffix.
            sql=(Path(__file__).resolve().parents[1]/'database/schema/rollback/057_market_trip_reconciliation_rollback.sql').read_text()
            prefix=sql[:sql.index('DROP TABLE')]
            with option_file(Credentials(config.host,config.port,config.user,config.password)) as opts:
                client=MysqlClient(DEFAULT_MYSQL,opts)
                before=client.query('SHOW CREATE TABLE market_trip_reconciliations',config.database)
                try: client.run_script(prefix,config.database)
                except MysqlError as exc: assert 'rollback_057_refused' in str(exc).lower(),exc
                else: raise AssertionError('Rollback guard did not refuse')
                assert client.query('SHOW CREATE TABLE market_trip_reconciliations',config.database)==before
        check('rollback guard refuses retained history',rollback_guard)
        print('PASS',sum(r['status']=='PASS' for r in results),'FAIL',sum(r['status']=='FAIL' for r in results),flush=True)
        assert all(r['status']=='PASS' for r in results),results
