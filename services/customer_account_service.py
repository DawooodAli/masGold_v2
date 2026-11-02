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
LEGACY_PERMISSION = 'CUSTOMER_BALANCE_ADJUST'                 # the OWNER's right to put a balance on a customer's account (existing since 005)
LEGACY_REFERENCE = 'LEGACY_PAPER_BALANCE'
LEGACY_DESCRIPTION = 'رصيد قديم من دفتر ورقي'


class CustomerAccountService:
    def __init__(self, session, *, transaction_factory=invoice_transaction,
                 repository=CustomerAccountRepository, invoices=InvoiceRepository,
                 customers=CustomerRepository, authorization=None):
        self.session, self.transaction = session, transaction_factory
        self.repository, self.invoices, self.customers = repository, invoices, customers
        self.authorization = authorization or AuthorizationService(session)

    def can_add_legacy_balance(self):
        actor = self.session.current_actor()
        return bool(actor and LEGACY_PERMISSION in actor.permissions)

    def add_legacy_balance(self, customer_id, direction, amount, note, old_date, request_key, *, _expected_actor=None):
        """رصيد قديم: put an OLD balance (from the paper ledgers) on a customer's account, never touching the cash, bank or wallet.

        direction OWES = على العميل (+), CREDIT = للعميل (-). One ledger row (type LEGACY_ADJUSTMENT, reference LEGACY_PAPER_BALANCE, the request key as its code) and one audit row, in ONE
        transaction under the customer's lock; the row is immutable like every ledger row (a mistake is corrected by an opposite entry). A retry with the same request key returns the first result.
        OWNER only (CUSTOMER_BALANCE_ADJUST, rechecked in the transaction); the note is mandatory; the old date is optional text kept in the description (never in the future)."""
        actor = self.session.require_authenticated()
        if _expected_actor is not None and not self.session.is_current(_expected_actor):
            raise PermissionError('انتهت جلسة الدخول.')
        if LEGACY_PERMISSION not in actor.permissions:
            raise PermissionError('إضافة رصيد قديم للمالك فقط.')
        cid = str(CustomerService._customer_id(customer_id))
        if direction not in ('OWES', 'CREDIT'):
            raise CorrectionError('DIRECTION_INVALID', 'اختر: على العميل أو للعميل.')
        value = _money_value(amount)
        if value <= 0:
            raise CorrectionError('AMOUNT_INVALID', 'المبلغ يجب أن يكون أكبر من صفر.')
        value = value.quantize(Decimal('0.01'))
        note = (note or '').strip()
        if not note:
            raise CorrectionError('NOTE_REQUIRED', 'اكتب بيان الرصيد القديم (مثلاً: دفتر ورقي سنة كذا).')
        if len(note) > 1000:
            raise CorrectionError('NOTE_TOO_LONG', 'البيان طويل جداً.')
        day = CustomerService._statement_date(old_date or '', 'تاريخ الرصيد القديم')
        if day is not None and day > datetime.now().date():
            raise CorrectionError('DATE_FUTURE', 'تاريخ الرصيد القديم لا يكون في المستقبل.')
        try:
            key = str(UUID(str(request_key)))
        except (ValueError, TypeError, AttributeError):
            raise CorrectionError('REQUEST_INVALID', 'أعد فتح النافذة وحاول مرة أخرى.') from None
        signed = value if direction == 'OWES' else -value
        digest = hashlib.sha256(json.dumps([cid, direction, str(value), note, day.isoformat() if day else ''], ensure_ascii=False).encode()).hexdigest()
        description = LEGACY_DESCRIPTION + (' — بتاريخ ' + day.isoformat() if day else '')
        with self.transaction() as connection:
            verified = self.authorization.require_permission_for_write(connection, actor, LEGACY_PERMISSION)
            repo = self.repository(connection)
            before = repo.lock_balance(cid)                                   # the customer and his ledger rows, locked
            prior = repo.legacy_entry(cid, key)
            if prior:
                stored = prior.get('metadata_json')
                stored = json.loads(stored) if isinstance(stored, str) else (stored or {})
                if stored.get('request_hash') != digest or int(prior['created_by_user_id']) != int(verified.user_id):
                    raise CorrectionError('REQUEST_CONFLICT', 'طلب الرصيد القديم مستخدم لعملية مختلفة.')
                return {'id': str(prior['id']), 'balance_before': str(before - Decimal(str(prior['amount_delta']))), 'balance_after': str(before), 'duplicate': True}
            transaction_id = self.customers(connection).add_transaction(
                customer_id=int(cid), type_code='LEGACY_ADJUSTMENT', amount_delta=signed, created_by_user_id=verified.user_id, reference_type=LEGACY_REFERENCE, reference_code=key,
                description=description, notes=note, metadata={'request_hash': digest, 'old_date': day.isoformat() if day else None, 'direction': direction})
            after = before + signed
            repo._execute('INSERT INTO audit_log (user_id,event_type,entity_type,entity_id,entity_reference,description,metadata_json) VALUES (%s,%s,%s,%s,%s,%s,%s)',
                          (verified.user_id, 'CUSTOMER_LEGACY_BALANCE_ADDED', 'CUSTOMER', int(cid), 'LB%06d' % int(transaction_id), 'إضافة رصيد قديم لحساب عميل',
                           json.dumps({'customer_id': cid, 'direction': direction, 'amount': str(value), 'before': str(before), 'after': str(after), 'old_date': day.isoformat() if day else None,
                                       'note': note, 'employee_id': verified.employee_id}, ensure_ascii=False)))
            if not self.session.is_current(actor):
                raise PermissionError('انتهت جلسة الدخول.')
        return {'id': str(transaction_id), 'balance_before': str(before), 'balance_after': str(after), 'duplicate': False}

    def customer_invoices(self, customer_id, start='', end='', invoice_type='', search='', page=1,
                          *, _expected_actor=None):
        """All this customer's finalized invoice history, independent of ledger allocation."""
        from services.invoice_history_service import InvoiceHistoryService
        customer_id = str(CustomerService._customer_id(customer_id))
        if len(search)>150:
            raise ValueError('نص البحث طويل.')
        return InvoiceHistoryService(self.session, transaction_factory=self.transaction,
            invoice_repository=self.invoices, authorization=self.authorization).search(customer_id=customer_id, text=search.strip(),
                date_from=CustomerService._statement_date(start,'تاريخ البداية'),
                date_to=CustomerService._statement_date(end,'تاريخ النهاية'),
                invoice_type=invoice_type or None, page=CustomerService._page_number(page),
                _expected_actor=_expected_actor)

    @staticmethod
    def _open_custody(connection, customer_id):
        """The customer's temporary cash custody still owed (074): shown beside the account, never part of the balance. Empty when none or below 074."""
        from decimal import Decimal
        from repositories.financial_account_repository import FinancialAccountRepository
        reader = getattr(FinancialAccountRepository(connection), 'customer_open_advances', None)
        cent = Decimal('0.01')
        return [{'id':str(r['id']),'code':f"SA{int(r['id']):06d}",'direction':r.get('direction') or 'LEND','date':r['created_at'].strftime('%Y-%m-%d') if r['created_at'] else '',
                 'amount':str(Decimal(str(r['amount'])).quantize(cent)),'repaid':str(Decimal(str(r['repaid'])).quantize(cent)),
                 'owed':str((Decimal(str(r['amount']))-Decimal(str(r['repaid']))).quantize(cent)),'note':r.get('note') or ''}
                for r in (reader(customer_id) if reader else [])]

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
        if source not in ('','INVOICE','EXCHANGE','RETURN','FULL_REVERSAL','SETTLEMENT_CORRECTION',SOURCE,
                          'CUSTOMER_DEPOSIT','CUSTOMER_DEPOSIT_REFUND','DEAD_CASH_WRITE_OFF',
                          'DEAD_CASH_REVERSAL','UNKNOWN'):
            raise ValueError('نوع المصدر غير صحيح.')
        if len(search)>150:
            raise ValueError('نص البحث طويل.')
        with self.transaction() as connection:
            self.authorization.require_any_permission_read(connection,actor,('CUSTOMERS_VIEW',PERMISSION))
            customer = self.customers(connection).get_customer_by_id(cid)
            if not customer:
                raise ValueError('العميل غير موجود.')
            rows = self.repository(connection).statement(cid,
                datetime.combine(first,time.min) if first else None,
                datetime.combine(last+timedelta(days=1),time.min) if last else None,
                source,search.strip(),(page-1)*50)
            accounts = self.invoices(connection).active_bank_accounts()
            custody = self._open_custody(connection, cid)
            try:                                     # Dead Cash detail needs DEAD_CASH_VIEW (non-locking)
                self.authorization.require_permission_read(connection, actor, 'DEAD_CASH_VIEW')
            except PermissionError:
                rows = [dict(r, source_reference=None, dead_cash_reason=None, dead_cash_id=None, dead_cash_reversed=None, notes=None,
                             description={'DEAD_CASH_WRITE_OFF':'شطب رصيد','DEAD_CASH_REVERSAL':'عكس شطب رصيد'}
                             [r['source_type']])
                        if r.get('source_type') in ('DEAD_CASH_WRITE_OFF','DEAD_CASH_REVERSAL') else r
                        for r in rows]
        return {'customer':customer,'rows':rows[:50],'has_more':len(rows)>50,'page':page,
                'accounts':accounts,'can_settle':PERMISSION in actor.permissions and bool(customer['balance']),
                'custody':custody}

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
