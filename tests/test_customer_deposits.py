"""عربون / استرداد عربون (027) against a transactional in-memory store: deposit from zero / while owing,
partial refund, the available-deposit rule, atomic rollback, reversal, labels. No application database."""
import copy
from contextlib import contextmanager
from datetime import datetime
from decimal import Decimal as D
from unittest.mock import patch
from uuid import uuid4
import unittest

from app.session import AppSession
from services.authentication_service import AuthenticationResult, _VERIFIED
from services.correction_service import CD, CorrectionError
from services.customer_deposit_service import CustomerDepositService
from services.financial_account_service import CASH_LABELS, CASH_TYPES, CASH_TYPE_TEXT
from tests import test_correction_service as fixture

CASHIER = frozenset({"APP_LOGIN", "CUSTOMER_ACCOUNT_SETTLE", OPERATE := "FINANCIAL_ACCOUNT_OPERATE"})


class Store:
    def __init__(self):
        self.now = datetime(2026, 9, 30, 12, 0)
        self.ledger = []            # (customer, delta, reference_type, reference_id)
        self.money = []             # (ledger, account, delta, reference_type, reference_id)
        self.deposits, self.audit, self.corrections = {}, [], []
        self.customers = {"12": {"customer_id": "12", "is_active": 1}, "13": {"customer_id": "13", "is_active": 0}}

    def balance(self, customer="12"):
        return sum((r[1] for r in self.ledger if r[0] == customer), D("0.00"))

    def cash(self):
        return sum((m[2] for m in self.money if m[:2] == ("CASH", 1)), D("0.00"))


class Conn:
    def __init__(self, store):
        self.store = store


class Deposits:
    fail_on = None

    def __init__(self, connection):
        self.store = connection.store

    def _fail(self, step):
        if Deposits.fail_on == step:
            raise RuntimeError(step)

    def by_request(self, key):
        return next((dict(d) for d in self.store.deposits.values() if d["request_key"] == key), None)

    def outstanding(self, customer_id):
        reversed_ids = {c["source_id"] for c in self.store.corrections if c["source_type"] == CD}
        return sum((d["amount"] if d["direction"] == "IN" else -d["amount"] for d in self.store.deposits.values()
                    if d["customer_id"] == str(customer_id) and d["id"] not in reversed_ids), D("0.00"))

    def insert_header(self, *, customer_id, direction, amount, key, digest, cash_account_id, before, after, notes,
                      actor, now):
        assert amount > 0 and (after == before - amount if direction == "IN" else
                               before < 0 and after == before + amount and after <= 0)    # chk_cd_direction
        deposit_id = str(len(self.store.deposits) + 1)
        self.store.deposits[deposit_id] = {"id": deposit_id, "customer_id": customer_id, "direction": direction,
                                           "amount": amount, "request_key": key, "request_hash": digest,
                                           "performed_by_user_id": actor.user_id, "balance_after": after}
        self._fail("header")
        return deposit_id

    def post_cash(self, *, deposit_id, code, direction, amount, cash_account_id, text, actor, now):
        self.store.money.append(("CASH", 1, amount if direction == "IN" else -amount, "CUSTOMER_DEPOSIT", deposit_id))
        self._fail("cash")

    def audit(self, **kw):
        self.store.audit.append((kw["code"], kw["description"]))
        self._fail("audit")

    def db_now(self):
        return self.store.now


class Balances:
    def __init__(self, connection):
        self.store = connection.store

    def lock_balance(self, customer_id):
        return self.store.balance(str(customer_id))


class Customers:
    def __init__(self, connection):
        self.store = connection.store

    def get_balance(self, customer_id):
        return self.store.balance(str(customer_id))

    def add_transaction(self, *, customer_id, type_code, amount_delta, reference_type, reference_id, **kw):
        assert (type_code == "CUSTOMER_DEPOSIT" and amount_delta < 0) or \
            (type_code == "CUSTOMER_DEPOSIT_REFUND" and amount_delta > 0)                      # type direction
        self.store.ledger.append((str(customer_id), amount_delta, reference_type, str(reference_id)))
        Deposits(Conn(self.store))._fail("ledger")


class Accounts:
    def __init__(self, connection):
        pass

    def account(self, ledger, account_id, lock=False):
        return {"id": 1, "name": "الخزنة الرئيسية", "account_type": "CASH", "is_active": 1}


class Invoices:
    def __init__(self, connection):
        self.store = connection.store

    def lock_customer(self, customer_id):
        return self.store.customers.get(str(customer_id))

    def active_cash_accounts(self):
        return [{"id": 1, "code": "MAIN_SAFE", "name": "الخزنة الرئيسية"}]


class DepositTests(unittest.TestCase):
    def setUp(self):
        Deposits.fail_on = None
        self.addCleanup(setattr, Deposits, "fail_on", None)
        self.store = Store()
        self.service = self.make(CASHIER)

    def make(self, permissions):
        session = AppSession()
        session._establish(AuthenticationResult(3, None, "كاشير", permissions, _VERIFIED), int(session.generation))

        @contextmanager
        def transaction():
            saved = copy.deepcopy(vars(self.store))
            try:
                yield Conn(self.store)
            except BaseException:
                self.store.__dict__.clear()
                self.store.__dict__.update(saved)
                raise
        return CustomerDepositService(session, transaction_factory=transaction, repository=Deposits,
                                      balances=Balances, customers=Customers, accounts=Accounts, invoices=Invoices,
                                      authorization=fixture.Authorization(session, permissions))

    def deposit(self, amount, customer="12", key=None):
        return self.service.deposit(customer, amount, "", key or str(uuid4()))

    def refund(self, amount, key=None):
        return self.service.refund("12", amount, "", key or str(uuid4()))

    def refused(self, code, call):
        before = copy.deepcopy(vars(self.store))
        with self.assertRaises(CorrectionError) as context:
            call()
        self.assertEqual(context.exception.code, code)
        self.assertEqual(vars(self.store), before)

    def test_deposit_from_zero_creates_customer_credit(self):
        result = self.deposit("5000")
        self.assertEqual((result["code"], result["balance_after"]), ("DP000001", "-5000.00"))
        self.assertEqual(self.store.balance(), D("-5000.00"))                  # للعميل 5,000
        self.assertEqual(self.store.cash(), D("5000.00"))                      # الخزنة +5,000
        self.assertEqual(self.store.ledger[0][2:], ("CUSTOMER_DEPOSIT", "1"))

    def test_refund_status_separates_capacity_from_current_balance_without_writes(self):
        self.deposit('2000')
        before=copy.deepcopy(vars(self.store))
        self.assertEqual(self.service.refund_status('12'),{'available':'2000.00','balance':'-2000.00'})
        self.assertEqual(vars(self.store),before)
        self.store.ledger.append(('12',D('3000'),'INVOICE','999'))
        before=copy.deepcopy(vars(self.store))
        self.assertEqual(self.service.refund_status('12'),{'available':'0.00','balance':'1000.00'})
        self.assertEqual(vars(self.store),before)

    def test_refund_viewmodel_blocks_zero_and_excess_without_submit(self):
        from unittest.mock import Mock
        from app.viewmodels.customer_account_viewmodel import CustomerAccountViewModel
        vm=CustomerAccountViewModel(self.service._session,Mock())
        vm._customer_id='12'; vm._data={'customer':{'balance':D('0')}}
        vm._deposits=Mock(refund_status=self.service.refund_status,refund=Mock())
        def run(operation,call):
            result=call(_expected_actor=self.service._session.current_actor())
            if operation=='refund_status':vm._refund_status=result
        vm._run=run
        for balance in (D('0'),D('1000')):
            self.store.ledger=[('12',balance,'INVOICE','999')]
            vm.beginRefund(); vm.setRefundAmount('10')
            self.assertFalse(vm.canRefund)
            self.assertEqual(vm.refundAvailability,'لا يوجد رصيد عربون متاح للاسترداد حاليًا.')
            vm.depositAction('OUT','10','')
            vm._deposits.refund.assert_not_called()
        self.assertEqual(vm.refundBalance,'رصيد العميل الحالي: عليه 1,000.00 ج')
        self.store.ledger=[]; self.deposit('2000')
        vm.beginRefund(); vm.setRefundAmount('2001')
        self.assertFalse(vm.canRefund)
        self.assertEqual(vm.refundAmountMessage,'المتاح للاسترداد من العربون 2,000.00 ج فقط.')
        vm.depositAction('OUT','2001',''); vm._deposits.refund.assert_not_called()
        self.assertEqual(vm.refundAvailability,'المتاح للاسترداد من العربون: 2,000.00 ج')
        self.assertEqual(vm.refundBalance,'رصيد العميل الحالي: له 2,000.00 ج')
        for amount in ('0','-1','NaN','Infinity','garbage'):
            vm.setRefundAmount(amount); self.assertFalse(vm.canRefund)
            vm.depositAction('OUT',amount,''); vm._deposits.refund.assert_not_called()
        vm.setRefundAmount('٢٠٠٠'); self.assertTrue(vm.canRefund)
        vm.depositAction('OUT','٢٠٠٠',''); vm._deposits.refund.assert_called_once()

    def test_deposit_when_customer_already_owes_moves_the_one_balance(self):
        self.store.ledger.append(("12", D("3000.00"), "INVOICE", "500"))
        self.deposit("5000")
        self.assertEqual(self.store.balance(), D("-2000.00"))                  # owes 3,000 → للعميل 2,000
        self.assertEqual(self.service.available("12"), "2000.00")              # refundable = credit, not 5,000

    def test_partial_refund_and_limit(self):
        self.deposit("5000")
        result = self.refund("2000")
        self.assertEqual((result["code"], result["balance_after"]), ("DR000002", "-3000.00"))
        self.assertEqual((self.store.balance(), self.store.cash()), (D("-3000.00"), D("3000.00")))
        self.refused("REFUND_EXCEEDS_DEPOSIT", lambda: self.refund("3000.01"))
        self.refund("3000")
        self.assertEqual(self.store.balance(), D("0.00"))                      # never crosses zero
        self.refused("REFUND_EXCEEDS_DEPOSIT", lambda: self.refund("0.01"))

    def test_refund_limited_to_deposits_not_to_other_credit(self):
        self.store.ledger.append(("12", D("-9000.00"), "INVOICE_RETURN", "800"))   # credit from a Return
        self.refused("REFUND_EXCEEDS_DEPOSIT", lambda: self.refund("1"))
        self.deposit("1000")
        self.refused("REFUND_EXCEEDS_DEPOSIT", lambda: self.refund("1000.01"))
        self.refund("1000")

    def test_customer_required_amount_and_permission(self):
        self.refused("CUSTOMER_REQUIRED", lambda: self.deposit("10", customer="999"))
        self.refused("CUSTOMER_INACTIVE", lambda: self.deposit("10", customer="13"))
        for amount in ("0", "-1", "abc"):
            self.refused("AMOUNT_INVALID", lambda a=amount: self.deposit(a))
        for permissions in (frozenset({"APP_LOGIN", OPERATE}), frozenset({"APP_LOGIN", "PAYMENT_RECORD"})):
            with self.assertRaises(PermissionError):
                self.make(permissions).deposit("12", "10", "", str(uuid4()))

    def test_idempotent_request(self):
        key = str(uuid4())
        first, second = self.deposit("500", key=key), self.deposit("500", key=key)
        self.assertEqual((second["code"], second["duplicate"]), (first["code"], True))
        self.assertEqual(len(self.store.deposits), 1)
        self.refused("REQUEST_CONFLICT", lambda: self.deposit("600", key=key))

    def test_cash_and_customer_ledger_commit_atomically(self):
        for step in ("header", "cash", "ledger", "audit"):
            with self.subTest(step=step):
                Deposits.fail_on = step
                before = copy.deepcopy(vars(self.store))
                with self.assertRaises(RuntimeError):
                    self.deposit("700")
                self.assertEqual(vars(self.store), before)                     # both unchanged
        Deposits.fail_on = None


class DepositReversalTests(fixture.CorrectionTestCase):
    """Reversal through the real CorrectionService (full reversal only)."""

    def setUp(self):
        super().setUp()
        self.store.deposits = {
            "1": {"id": "1", "customer_id": "12", "direction": "IN", "amount": D("5000.00"), "cash_account_id": 1,
                  "customer_name": "عميل", "occurred_at": datetime(2026, 9, 30, 11, 0)},
            "2": {"id": "2", "customer_id": "12", "direction": "OUT", "amount": D("2000.00"), "cash_account_id": 1,
                  "customer_name": "عميل", "occurred_at": datetime(2026, 9, 30, 11, 30)}}
        self.store.money += [("CASH", 1, D("5000.00"), "CUSTOMER_DEPOSIT", "1"),
                             ("CASH", 1, D("-2000.00"), "CUSTOMER_DEPOSIT", "2")]
        self.store.ledger += [("12", D("-5000.00"), "CUSTOMER_DEPOSIT", "1"), ("12", D("2000.00"), "CUSTOMER_DEPOSIT", "2")]
        store = self.store

        class Repo:
            def __init__(self, connection):
                pass

            def source(self, deposit_id, lock=False):
                row = store.deposits.get(str(deposit_id))
                return None if row is None else dict(row)

            def outstanding(self, customer_id):
                reversed_ids = {c["source_id"] for c in store.corrections if c["source_type"] == CD}
                return sum((d["amount"] if d["direction"] == "IN" else -d["amount"] for d in store.deposits.values()
                            if d["id"] not in reversed_ids), D("0.00"))
        for target, value in (("repositories.customer_deposit_repository.CustomerDepositRepository", Repo),
                              ("repositories.customer_account_repository.CustomerAccountRepository.lock_balance",
                               lambda self, cid: D("0")),
                              ("repositories.financial_account_repository.FinancialAccountRepository.account",
                               lambda self, *a, **k: {"id": 1})):
            p = patch(target, value)
            p.start()
            self.addCleanup(p.stop)
        refs = patch.dict(fixture.REF, {CD: CD})
        refs.start()
        self.addCleanup(refs.stop)

    def test_refund_reversal_then_deposit_reversal(self):
        self.assertRefused("DEPOSIT_ALREADY_REFUNDED", lambda: self.service.reverse(CD, "1", "عربون بالخطأ"))
        cash = self.store.balance("CASH", 1)
        self.service.reverse(CD, "2", "استرداد بالخطأ")
        self.assertEqual(self.store.balance("CASH", 1), cash + D("2000"))
        result = self.service.reverse(CD, "1", "عربون بالخطأ")
        self.assertEqual(result["source_reference"], "DP000001")
        self.assertEqual(self.store.balance("CASH", 1), cash - D("3000"))
        mine = sum((r[1] for r in self.store.ledger if r[0] == "12" and r[2] in ("CUSTOMER_DEPOSIT", "TRANSACTION_CORRECTION")
                    and (r[2] != "TRANSACTION_CORRECTION" or r[3] in {c["correction_id"] for c in self.store.corrections})),
                   D("0.00"))
        self.assertEqual(mine, D("0.00"))                                      # every deposit effect undone
        self.assertRefused("ALREADY_REVERSED", lambda: self.service.reverse(CD, "1", "مرة ثانية"))
        self.assertRefused("SETTLEMENT_NOT_SUPPORTED", lambda: self.service.current_settlement(CD, "2"))
        self.assertEqual(self.service.deposit_target("1")["targets"][0]["settlement_allowed"], False)

    def test_reversal_names_use_stored_direction_for_targets_and_preview(self):
        from app.viewmodels.correction_viewmodel import CorrectionViewModel
        for source_id,label,plain in [('1','العربون','عربون'),('2','استرداد العربون','استرداد عربون')]:
            target=self.service.deposit_target(source_id)
            self.assertEqual(target['targets'][0]['source_text'],label)
            preview=self.service.preview_reversal(CD,source_id)
            self.assertEqual(preview['source_text'],label)
            vm=CorrectionViewModel(self.service._session,self.service)
            vm._targets=target; vm.selectTarget(0); vm._reversal=preview
            self.assertEqual(vm.header['title'],f"عكس {plain} {target['invoice_number']}")
            self.assertEqual(vm.reversal['title'],f"{label} {target['invoice_number']}")

    def test_supported_source_label_mapping_preserves_customer_settlement(self):
        from services.correction_service import correction_source_text, CS, FO, SMP, MTE, SGS, HRC, SPI, SOURCES
        expected={'INVOICE':'الفاتورة','EXCHANGE':'الاستبدال','RETURN':'المرتجع',
                  CS:'تسوية حساب',FO:'العملية المالية',CD:'العربون',SMP:'دفعة المصنعيات',MTE:'حركة عهدة السوق',SGS:'تسوية ذهب المورد',HRC:'استلام ذهب من المسوقجي',SPI:'فاتورة شراء مورد'}
        self.assertEqual({s:correction_source_text(s) for s in SOURCES},expected)
        from app.viewmodels.correction_viewmodel import CorrectionViewModel
        vm=CorrectionViewModel(self.service._session,self.service)
        vm._targets={'invoice_id':'','invoice_number':'CS000001','targets':[
            {'source_type':CS,'reference':'CS000001'}]}
        vm.selectTarget(0)
        self.assertEqual(vm.header['title'],'تصحيح / عكس — تسوية حساب CS000001')


class DepositLabelTests(unittest.TestCase):
    def test_cash_screen_labels_and_no_tafeet_or_cs_duplicate(self):
        self.assertEqual((CASH_LABELS["DEPOSIT"], CASH_LABELS["DEPOSIT_REFUND"]), ("عربون", "استرداد عربون"))
        self.assertNotIn("تيفيت", set(CASH_LABELS.values()) | set(CASH_TYPE_TEXT.values()))
        self.assertNotIn("DEPOSIT", CASH_TYPES)                                # عربون needs a customer: not here
        from services import customer_deposit_service as module
        from pathlib import Path
        source = Path(module.__file__).read_text(encoding="utf-8")
        self.assertNotIn("customer_account_settlements", source)                # no CS path reused / duplicated
        self.assertNotIn("CUSTOMER_ACCOUNT_SETTLEMENT", source)

    def test_customer_statement_labels(self):
        from app.viewmodels.customer_account_viewmodel import LABELS
        self.assertEqual((LABELS["CUSTOMER_DEPOSIT"], LABELS["CUSTOMER_DEPOSIT_REFUND"]), ("عربون", "استرداد عربون"))
        import inspect
        from repositories.customer_account_repository import CustomerAccountRepository
        sql = inspect.getsource(CustomerAccountRepository.statement)
        self.assertIn("IF(cd.direction='IN','CUSTOMER_DEPOSIT','CUSTOMER_DEPOSIT_REFUND')", sql)
        self.assertIn("IF(cd.direction='IN','DP','DR')", sql)
