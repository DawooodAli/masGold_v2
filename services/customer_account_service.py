"""Explicit, idempotent settlement of canonical customer balances."""
import hashlib
import json
from decimal import Decimal
from uuid import UUID
from repositories.customer_account_repository import CustomerAccountRepository, SOURCE, settlement_code
from repositories.customer_repository import CustomerRepository
from repositories.invoice_repository import InvoiceRepository
from services.authorization_service import AuthorizationService
from services.invoice_service import InvoiceService, PaymentInput, invoice_transaction
from services.correction_service import CorrectionError, _money_value
from services.customer_service import CustomerService
from datetime import datetime, time, timedelta

PERMISSION = 'CUSTOMER_ACCOUNT_SETTLE'


class CustomerAccountService:
    def __init__(self, session, *, transaction_factory=invoice_transaction,
                 repository=CustomerAccountRepository, invoices=InvoiceRepository,
                 customers=CustomerRepository, authorization=None):
        self.session, self.transaction = session, transaction_factory
        self.repository, self.invoices, self.customers = repository, invoices, customers
        self.authorization = authorization or AuthorizationService(session)

    def customer_invoices(self, customer_id, start='', end='', invoice_type='', search='', page=1,
                          *, _expected_actor=None):
        """All this customer's finalized invoice history, independent of ledger allocation."""
        from services.invoice_history_service import InvoiceHistoryService
        customer_id = str(CustomerService._customer_id(customer_id))
        if len(search)>150:
            raise ValueError('نص البحث طويل.')
        return InvoiceHistoryService(self.session, transaction_factory=self.transaction,
            invoice_repository=self.invoices).search(customer_id=customer_id, text=search.strip(),
                date_from=CustomerService._statement_date(start,'تاريخ البداية'),
                date_to=CustomerService._statement_date(end,'تاريخ النهاية'),
                invoice_type=invoice_type or None, page=CustomerService._page_number(page),
                _expected_actor=_expected_actor)

    def statement(self, customer_id, start='', end='', source='', search='', page=1, *, _expected_actor=None):
        actor = self.session.require_authenticated()
        if _expected_actor is not None and not self.session.is_current(_expected_actor):
            raise PermissionError('انتهت جلسة الدخول.')
        if not ({'CUSTOMERS_VIEW',PERMISSION} & actor.permissions):
            raise PermissionError('ليس لديك صلاحية عرض الحساب.')
        cid = CustomerService._customer_id(customer_id)
        page = CustomerService._page_number(page)
        first = CustomerService._statement_date(start,'تاريخ البداية')
        last = CustomerService._statement_date(end,'تاريخ النهاية')
        if first and last and first>last:
            raise ValueError('تاريخ البداية بعد النهاية.')
        if source not in ('','INVOICE','EXCHANGE','RETURN','FULL_REVERSAL','SETTLEMENT_CORRECTION',SOURCE,'UNKNOWN'):
            raise ValueError('نوع المصدر غير صحيح.')
        if len(search)>150:
            raise ValueError('نص البحث طويل.')
        with self.transaction() as connection:
            self.authorization.require_permission(connection,actor,
                'CUSTOMERS_VIEW' if 'CUSTOMERS_VIEW' in actor.permissions else PERMISSION)
            customer = self.customers(connection).get_customer_by_id(cid)
            if not customer:
                raise ValueError('العميل غير موجود.')
            rows = self.repository(connection).statement(cid,
                datetime.combine(first,time.min) if first else None,
                datetime.combine(last+timedelta(days=1),time.min) if last else None,
                source,search.strip(),(page-1)*50)
            accounts = self.invoices(connection).active_bank_accounts()
        return {'customer':customer,'rows':rows[:50],'has_more':len(rows)>50,'page':page,
                'accounts':accounts,'can_settle':PERMISSION in actor.permissions and bool(customer['balance'])}

    def finalize(self, customer_id, payments, request_key, notes='', *, _expected_actor=None):
        actor = self.session.require_authenticated()
        if _expected_actor is not None and not self.session.is_current(_expected_actor):
            raise PermissionError('انتهت جلسة الدخول.')
        if PERMISSION not in actor.permissions:
            raise PermissionError('ليس لديك صلاحية تسوية الحساب.')
        customer_id = str(CustomerService._customer_id(customer_id))
        key = str(UUID(str(request_key)))
        notes = (notes or '').strip()
        if len(notes)>1000 or not 1 <= len(payments) <= 6:
            raise ValueError('راجع طرق الدفع والملاحظات.')
        normalized = []
        for p in payments:
            if p.method_code not in ('PHYSICAL_CASH','MOBILE_WALLET','INSTAPAY','BANK_TRANSFER'):
                raise CorrectionError('PAYMENT_METHOD_NOT_ALLOWED','طريقة الدفع غير متاحة للتسوية.')
            amount = _money_value(p.amount)
            if amount <= 0:
                raise CorrectionError('AMOUNT_INVALID','مبلغ التسوية يجب أن يكون أكبر من صفر.')
            normalized.append((p, amount))
        total = sum((a for _,a in normalized), Decimal('0.00'))
        digest = hashlib.sha256(json.dumps([customer_id,notes,
            [(p.method_code,str(p.bank_account_id or ''),str(a),p.direction) for p,a in normalized]],
            ensure_ascii=False).encode()).hexdigest()
        with self.transaction() as connection:
            verified = self.authorization.require_permission(connection,actor,PERMISSION)
            repo, invoices = self.repository(connection), self.invoices(connection)
            before = repo.lock_balance(customer_id)
            prior = repo.by_request(key)
            if prior:
                if prior['request_hash'] != digest or prior['performed_by_user_id'] != verified.user_id:
                    raise CorrectionError('REQUEST_CONFLICT','طلب التسوية مستخدم لعملية مختلفة.')
                return {'id':str(prior['id']),'code':settlement_code(prior['id']),
                        'balance_after':str(prior['balance_after'])}
            if not before:
                raise CorrectionError('BALANCE_ZERO','الحساب متوازن.')
            if total>abs(before):
                raise CorrectionError('SETTLEMENT_EXCEEDS_BALANCE','مبلغ التسوية يتجاوز الرصيد الحالي.')
            direction = 'IN' if before>0 else 'OUT'
            if any(p.direction not in (None,direction) for p,_ in normalized):
                raise CorrectionError('DIRECTION_CHANGED','تغير اتجاه الرصيد؛ أعد تحميل الحساب.')
            _, banks, cash, problems = InvoiceService._payment_accounts(invoices, normalized, lock=True)
            if problems:
                raise problems[0]
            after = before-total if direction=='IN' else before+total
            now = repo.db_now()
            sid = repo.insert_header(customer_id,key,digest,direction,total,before,after,notes,verified,now)
            code = settlement_code(sid)
            for index,(p,amount) in enumerate(normalized):
                repo.post_payment(sid,code,p,amount,direction,banks.get(index),cash,verified,now)
            self.customers(connection).add_transaction(customer_id=int(customer_id),type_code=SOURCE,
                amount_delta=after-before,created_by_user_id=verified.user_id,reference_type=SOURCE,
                reference_id=sid,reference_code=code,description='تسوية حساب '+code,notes=notes)
            repo._execute('INSERT INTO audit_log (user_id,event_type,entity_type,entity_id,entity_reference,'
                'description,metadata_json,occurred_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)',
                (verified.user_id,SOURCE,SOURCE,int(sid),code,'تسوية حساب '+code,
                 json.dumps({'before':str(before),'after':str(after),'amount':str(total),
                             'direction':direction,'employee_id':verified.employee_id}),now))
            if not self.session.is_current(actor):
                raise PermissionError('انتهت جلسة الدخول.')
        return {'id':sid,'code':code,'balance_after':str(after)}
