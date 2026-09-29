"""CS and its correction semantics against transactional stores; no application DB."""
import copy
from contextlib import contextmanager
from decimal import Decimal as D
from uuid import uuid4
import unittest
from unittest.mock import patch
from tests import test_correction_service as fixture
from services.customer_account_service import CustomerAccountService, PERMISSION
from services.correction_service import CorrectionError, SettlementLeg, CS
from services.invoice_service import PaymentInput


class AccountRepo:
    failure = None
    def __init__(self, connection): self.store=connection.store
    def fail(self, step):
        if self.failure==step: raise RuntimeError(step)
    def lock_balance(self, cid):
        return sum((r[1] for r in self.store.ledger if r[0]==str(cid)),D('0'))
    def by_request(self,key):
        return next((r for r in self.store.cs.values() if r['request_key']==key),None)
    def source(self,sid,lock=False): return copy.deepcopy(self.store.cs.get(str(sid)))
    def db_now(self): return self.store.now
    def insert_header(self,cid,key,digest,direction,amount,before,after,notes,actor,now):
        sid=str(len(self.store.cs)+1)
        self.store.cs[sid]=dict(id=sid,customer_id=cid,request_key=key,request_hash=digest,
            direction=direction,amount=amount,balance_before=before,balance_after=after,notes=notes,
            performed_by_user_id=actor.user_id,occurred_at=now,payments=[])
        self.fail('header'); return sid
    def post_payment(self,sid,code,p,amount,direction,bank,cash,actor,now):
        self.store.money.append(('BANK' if bank else 'CASH',int((bank or cash)['id']),
            amount if direction=='IN' else -amount,CS,sid))
        self.fail('money')
        self.store.cs[sid]['payments'].append(dict(method_code=p.method_code,bank_account_id=p.bank_account_id,
            amount=amount,direction=direction))
        self.fail('leg')
    def _execute(self,*args): self.store.audit.append('CS'); self.fail('audit')


class Customers(fixture.CustomerRepo):
    def add_transaction(self,**kw):
        self.store.ledger.append((str(kw['customer_id']),kw['amount_delta'],kw['reference_type'],str(kw['reference_id'])))
        result=str(len(self.store.ledger))
        if AccountRepo.failure=='ledger':raise RuntimeError('ledger')
        return result


class AccountTests(fixture.CorrectionTestCase):
    def setUp(self):
        super().setUp()
        AccountRepo.failure=None
        self.store.cs={}; self.store.ledger=[('12',D('20000'), 'INVOICE','500')]; self.store.money=[]
        self.service=self.make(fixture.MANAGER | {PERMISSION})
        self.account=CustomerAccountService(self.service._session,transaction_factory=self.service._transaction,
            repository=AccountRepo,invoices=fixture.InvoiceRepo,customers=Customers,
            authorization=self.service._authorization)
        self.patch=patch('repositories.customer_account_repository.CustomerAccountRepository',AccountRepo)
        self.patch.start(); self.addCleanup(self.patch.stop)
        self.refs=patch.dict(fixture.REF,{CS:CS}); self.refs.start(); self.addCleanup(self.refs.stop)
        self.addCleanup(setattr,AccountRepo,'failure',None)

    def settle(self,amount='7000',method='PHYSICAL_CASH',bank=None,key=None):
        return self.account.finalize('12',[PaymentInput(method,amount,None,None,bank)],key or str(uuid4()))

    def balance(self):return sum((r[1] for r in self.store.ledger),D('0'))

    def test_partial_customer_owes(self):
        result=self.settle(); self.assertEqual(result['code'],'CS000001')
        self.assertEqual(self.balance(),D('13000')); self.assertEqual(self.store.money[-1][2],D('7000'))

    def test_permission_rechecked_and_invalid_payment_routes_refused(self):
        before=copy.deepcopy(vars(self.store))
        self.account.authorization.permissions=fixture.MANAGER
        with self.assertRaises(PermissionError):self.settle()
        self.account.authorization.permissions=fixture.MANAGER | {PERMISSION}
        for method,bank in (('CUSTOMER_ACCOUNT',None),('BANK_TRANSFER',None),('BANK_TRANSFER','99999')):
            with self.subTest(method=method,bank=bank),self.assertRaises(ValueError):
                self.settle(method=method,bank=bank)
        self.assertEqual(vars(self.store),before)

    def test_shop_owes_negative_money_allowed(self):
        self.store.ledger=[('12',D('-10000'),'INVOICE','500')]
        self.settle('4000'); self.assertEqual(self.balance(),D('-6000'))
        self.assertEqual(self.store.money[-1][2],D('-4000'))

    def test_split(self):
        self.account.finalize('12',[PaymentInput('PHYSICAL_CASH','8000'),
            PaymentInput('BANK_TRANSFER','7000',bank_account_id='7'),
            PaymentInput('INSTAPAY','5000',bank_account_id='7')],str(uuid4()))
        self.assertEqual(self.balance(),0); self.assertEqual(len(self.store.money),3)

    def test_oversettlement_and_zero(self):
        for value in ('20001','0','-1','NaN','Infinity','1.001'):
            with self.subTest(value=value),self.assertRaises(ValueError):self.settle(value)
        self.assertEqual(self.balance(),D('20000')); self.assertFalse(self.store.cs)
        self.settle('20000')
        with self.assertRaises(CorrectionError) as e:self.settle('1')
        self.assertEqual(e.exception.code,'BALANCE_ZERO')

    def test_duplicate_key_returns_original_without_posting_twice(self):
        key=str(uuid4()); a=self.settle(key=key); b=self.settle(key=key)
        self.assertEqual(a,b);self.assertEqual(len(self.store.money),1)
        with self.assertRaises(CorrectionError):self.settle('6000',key=key)

    def test_stale_direction(self):
        with self.assertRaises(CorrectionError):
            self.account.finalize('12',[PaymentInput('PHYSICAL_CASH','1','OUT')],str(uuid4()))
        self.assertFalse(self.store.cs)

    def test_rollback_every_posting_stage(self):
        for stage in ('header','money','leg','ledger','audit'):
            with self.subTest(stage=stage):
                before=copy.deepcopy(vars(self.store)); AccountRepo.failure=stage
                with self.assertRaises(RuntimeError):self.settle()
                self.assertEqual(vars(self.store),before)

    def test_cash_to_bank_preserves_customer_effect(self):
        self.settle('10000'); before=copy.deepcopy(self.store.ledger)
        result=self.service.correct_settlement(CS,'1',[SettlementLeg('BANK_TRANSFER','10000','7')],None,'route')
        self.assertTrue(result['code'].startswith('FC')); self.assertEqual(self.store.ledger,before)
        self.assertIsNone(self.store.corrections[-1]['invoice_id'])
        posted=fixture.Repo(fixture.Connection(self.store)).money_by_account(CS,'1',['1'])
        self.assertEqual(posted,{('CASH',1):D('0'),('BANK',7):D('10000')})

    def test_cs_restricts_account_total_and_no_effect(self):
        self.settle('10000')
        for legs,account,code in (([SettlementLeg('BANK_TRANSFER','9000','7')],'1000','CS_ACCOUNT_FORBIDDEN'),
                                  ([SettlementLeg('BANK_TRANSFER','9000','7')],None,'TOTAL_MISMATCH'),
                                  ([SettlementLeg('PHYSICAL_CASH','10000')],None,'NO_EFFECT_CORRECTION')):
            with self.subTest(code=code),self.assertRaises(CorrectionError) as e:
                self.service.correct_settlement(CS,'1',legs,account,'route')
            self.assertEqual(e.exception.code,code)

    def test_chain_then_reverse_nets_exactly_both_signs(self):
        for sign in (1,-1):
            with self.subTest(sign=sign):
                self.store.cs={}; self.store.money=[]; self.store.corrections=[]; self.store.legs=[]
                self.store.ledger=[('12',D('20000')*sign,'INVOICE','500')]
                self.settle('10000'); original=copy.deepcopy(self.store.cs)
                self.service.correct_settlement(CS,'1',[SettlementLeg('BANK_TRANSFER','10000','7')],None,'one',expected_sequence=1)
                self.service.correct_settlement(CS,'1',[SettlementLeg('PHYSICAL_CASH','3000'),SettlementLeg('BANK_TRANSFER','7000','7')],None,'two',expected_sequence=2)
                self.assertEqual(self.balance(),D('10000')*sign)
                self.service.reverse(CS,'1','reverse')
                self.assertEqual(self.balance(),D('20000')*sign)
                self.assertEqual(sum((r[2] for r in self.store.money),D('0')),0)
                self.assertEqual(self.store.cs,original)
                for call in (lambda:self.service.reverse(CS,'1','again'),lambda:self.service.correct_settlement(CS,'1',[SettlementLeg('PHYSICAL_CASH','10000')],None,'again')):
                    with self.assertRaises(CorrectionError) as e:call()
                    self.assertEqual(e.exception.code,'ALREADY_REVERSED')

    def test_stale_correction_sequence_refused(self):
        self.settle('10000')
        self.service.correct_settlement(CS,'1',[SettlementLeg('BANK_TRANSFER','10000','7')],None,'one',expected_sequence=1)
        with self.assertRaises(CorrectionError) as e:
            self.service.correct_settlement(CS,'1',[SettlementLeg('PHYSICAL_CASH','10000')],None,'stale',expected_sequence=1)
        self.assertEqual(e.exception.code,'STALE_CORRECTION')
