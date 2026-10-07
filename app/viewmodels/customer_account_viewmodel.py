"""Customer account statement and explicit real-money settlement. No SQL/UI arithmetic."""
from uuid import uuid4
from decimal import Decimal, InvalidOperation
import json
from PySide6.QtCore import QObject, Property, Signal, Slot
from app.formatting import money
from app.viewmodels.correction_viewmodel import METHODS, ACCOUNT_TYPE
from services.customer_account_service import CustomerAccountService, PERMISSION
from services.invoice_service import PaymentInput
from services.number_text import normalize_digits

LABELS = {'INVOICE':'فاتورة','EXCHANGE':'استبدال','RETURN':'مرتجع',
          'FULL_REVERSAL':'عكس','SETTLEMENT_CORRECTION':'تصحيح تسوية',
          'CUSTOMER_ACCOUNT_SETTLEMENT':'تسوية حساب','CUSTOMER_DEPOSIT':'عربون',
          'CUSTOMER_DEPOSIT_REFUND':'استرداد عربون','DEAD_CASH_WRITE_OFF':'شطب رصيد',
          'DEAD_CASH_REVERSAL':'عكس شطب رصيد','UNKNOWN':'حركة قديمة / غير محددة'}


def balance_text(value):
    value = Decimal(str(value))
    return ('على العميل: ' if value>0 else 'للعميل: ') + money(abs(value)) if value else 'الحساب متوازن'


class CustomerAccountViewModel(QObject):
    changed = Signal()
    legsChanged = Signal()
    finalized = Signal(str)
    invoiceRequested = Signal(str)
    correctionRequested = Signal(str)
    depositCorrectionRequested = Signal(str)

    def __init__(self, session, service=None, parent=None):
        super().__init__(parent)
        self.session = session
        self.service = service or CustomerAccountService(session)
        self._open=False; self._busy=False; self._shutdown=False
        self._workers=set(); self._generation=0
        self._customer_id=''; self._data={}; self._error=''; self._message=''
        self._start_date=''; self._end_date=''; self._source=''; self._search=''; self._page=1
        self._legs=[]; self._seq=0; self._notes=''; self._request_key=str(uuid4())
        self._tab=0; self._invoice_data={}
        self._invoice_filters=('', '', '', ''); self._invoice_page=1
        self._refresh_pending=False; self._pending_customer=None
        self._deposit_key=str(uuid4()); self._deposits=None
        self._refund_status=None; self._refund_amount=''

    def _run(self, operation, call):
        from app.workers.invoice_worker import InvoiceWorker
        actor=self.session.current_actor()
        if self._busy or actor is None or self._shutdown:
            return
        self._busy=True; self._generation+=1
        w=InvoiceWorker(call,self.session,actor,self._generation,operation,self)
        w.completed.connect(self._completed)
        w.finished.connect(lambda: self._workers.discard(w))
        self._workers.add(w); self.changed.emit(); w.start()

    @Slot(int,str,str,object,str,str,str)
    def _completed(self, request, generation, operation, result, error, code, kind):
        actor=self.session.current_actor()
        if self._shutdown or actor is None or str(actor.generation)!=generation or request!=self._generation:
            return
        self._busy=False
        if self._pending_customer is not None:
            customer_id=self._pending_customer
            self._pending_customer=None; self._refresh_pending=False
            self.open(customer_id)
            return
        self._error = ('تعذر تنفيذ عملية الحساب؛ حدّث البيانات وأعد المحاولة.' if error and kind not in ('ValueError','PermissionError')
                       and not code else error)
        if not error:
            if operation=='statement':
                self._data=result
            elif operation=='customer_invoices':
                self._data=result['account']; self._invoice_data=result['invoices']
            elif operation=='deposit':
                self._refund_status=None
                self._message=('تم تسجيل العربون ' if result['direction']=='IN' else 'تم تسجيل استرداد العربون ')+result['code']
                self._deposit_key=str(uuid4())
                self.finalized.emit(self._customer_id)
                self.refresh()
            elif operation=='refund_status':
                self._refund_status=result
            else:
                self._message='تمت تسوية الحساب '+result['code']
                self._legs=[]; self._request_key=str(uuid4()); self.legsChanged.emit()
                self.finalized.emit(self._customer_id)
                self.refresh()
        self.changed.emit()
        if self._refresh_pending:
            self._refresh_pending=False
            self.refresh()

    @Slot(str)
    def open(self, customer_id):
        if self._busy:
            self._pending_customer=str(customer_id)
            return
        if self._customer_id==str(customer_id):
            self._open=True; self.refresh(); return
        self._customer_id=str(customer_id); self._open=True; self._data={}; self._page=1
        self._refund_status=None; self._refund_amount=''
        self._invoice_data={}; self._invoice_filters=('', '', '', ''); self._invoice_page=1
        self._error=''; self._message=''; self._legs=[]; self._notes=''
        self._start_date=self._end_date=self._source=self._search=''
        self._request_key=str(uuid4()); self.legsChanged.emit(); self.refresh()

    @Slot()
    def close(self):
        if not self._busy:
            self._open=False; self.changed.emit()

    @Slot()
    def refresh(self):
        if self._customer_id and self._open:
            if self._busy:
                self._refresh_pending=True
                return
            cid=self._customer_id
            args=(cid,self._start_date,self._end_date,self._source,self._search,self._page)
            invoice_args=(cid,*self._invoice_filters,self._invoice_page)
            invoices=self._tab==1
            def read(_expected_actor):
                account=self.service.statement(*args,_expected_actor=_expected_actor)
                if not invoices:return account
                return {'account':account,'invoices':self.service.customer_invoices(
                    *invoice_args,_expected_actor=_expected_actor)}
            self._run('customer_invoices' if invoices else 'statement',read)

    @Slot(str)
    def followSelection(self, customer_id):
        if customer_id and self._open and customer_id!=self._customer_id:
            self.open(customer_id)

    @Slot(int)
    def setTab(self, tab):
        if self._busy or tab not in (0,1) or tab==self._tab:return
        self._tab=tab; self.refresh()

    @Slot(str,str,str,str)
    def filter(self,start,end,source,search):
        if self._busy:return
        if self._tab==1:
            self._invoice_filters=(start,end,source,search)
            self._invoice_page=1; self.refresh(); return
        self._start_date,self._end_date,self._source,self._search=start,end,source,search
        self._page=1; self.refresh()

    @Slot(int)
    def page(self, step):
        if self._tab==1:
            if not self._busy and self._invoice_page+step>0:
                self._invoice_page+=step; self.refresh()
            return
        if not self._busy and self._page+step>0:
            self._page+=step; self.refresh()

    @Slot()
    def addLeg(self):
        if self._busy or len(self._legs)>=6:return
        self._seq+=1
        self._legs.append({'key':str(self._seq),'method':'PHYSICAL_CASH','bank':'','amount':''})
        self.legsChanged.emit()

    @Slot(str,str,str,str)
    def editLeg(self,key,method,bank,amount):
        if self._busy:return
        for leg in self._legs:
            if leg['key']==key:
                leg.update(method=method,bank=bank,amount=amount)

    @Slot(str)
    def removeLeg(self,key):
        if self._busy:return
        self._legs=[l for l in self._legs if l['key']!=key]; self.legsChanged.emit()

    @Slot(str)
    def finalize(self,notes):
        if not self.canSettle or self._busy:return
        direction='IN' if Decimal(str(self._data['customer']['balance']))>0 else 'OUT'
        legs=tuple(PaymentInput(l['method'],l['amount'],direction,None,l['bank'] or None) for l in self._legs)
        self._run('finalize',lambda _expected_actor:self.service.finalize(
            self._customer_id,legs,self._request_key,notes,_expected_actor=_expected_actor))

    @Slot(str)
    def openInvoice(self, invoice_id):
        rows=self._invoice_data.get('items',[]) if self._tab==1 else self._data.get('rows',[])
        if any(str(r.get('invoice_id') or '')==invoice_id for r in rows) and invoice_id:
            self.invoiceRequested.emit(invoice_id)

    @Slot()
    def beginDeposit(self):
        """Opening the عربون / استرداد عربون form starts a fresh request (kept across retries)."""
        if not self._busy:
            self._deposit_key=str(uuid4()); self._error=''; self._message=''; self.changed.emit()

    def _deposit_service(self):
        if self._deposits is None:
            from services.customer_deposit_service import CustomerDepositService
            self._deposits=CustomerDepositService(self.session)
        return self._deposits

    @Slot()
    def beginRefund(self):
        if not self.canDeposit or self._busy:return
        self.beginDeposit()
        self._refund_status=None; self._refund_amount=''
        service,cid=self._deposit_service(),self._customer_id
        self._run('refund_status',lambda _expected_actor:service.refund_status(cid,_expected_actor=_expected_actor))

    @Slot(str)
    def setRefundAmount(self, text):
        self._refund_amount=text; self._error=''; self.changed.emit()

    def _refund_value(self, text):
        try:
            value=Decimal(normalize_digits(str(text)).strip())
            return value if value.is_finite() and value>0 else None
        except (InvalidOperation,ValueError):
            return None

    def _refund_problem(self, text):
        if self._refund_status is None:return 'جارٍ التحقق من المتاح للاسترداد…'
        available=Decimal(self._refund_status['available'])
        if available<=0:return 'لا يوجد رصيد عربون متاح للاسترداد حاليًا.'
        value=self._refund_value(text)
        if value is None:return 'أدخل مبلغًا موجبًا للاسترداد.'
        if value>available:return f'المتاح للاسترداد من العربون {money(available)} فقط.'
        return ''

    @Slot(str,str,str)
    def depositAction(self,direction,amount,note):
        if not self.canDeposit or self._busy or direction not in ('IN','OUT'):return
        problem=self._refund_problem(amount) if direction=='OUT' else ''
        if problem:
            self._error=problem; self.changed.emit(); return
        service,cid,key=self._deposit_service(),self._customer_id,self._deposit_key
        call=service.deposit if direction=='IN' else service.refund
        self._run('deposit',lambda _expected_actor:call(cid,amount,note,key,_expected_actor=_expected_actor))

    @Slot(str)
    def openDepositCorrection(self, deposit_id):
        if self.canCorrect and deposit_id and any(str(r.get('deposit_id') or '')==deposit_id for r in self._data.get('rows',[])):
            self.depositCorrectionRequested.emit(deposit_id)

    @Slot(str)
    def openCorrection(self, settlement_id):
        if self.canCorrect and settlement_id and any(str(r.get('settlement_id') or '')==settlement_id for r in self._data.get('rows',[])):
            self.correctionRequested.emit(settlement_id)

    @Slot()
    def shutdown(self):
        self._shutdown=True
        for w in list(self._workers):w.wait(5000)

    @Property(bool,notify=changed)
    def isOpen(self):return self._open
    @Property(str,notify=changed)
    def customerId(self):return self._customer_id
    @Property(bool,notify=changed)
    def busy(self):return self._busy
    @Property(bool,notify=changed)
    def canSettle(self):return bool(self._data.get('can_settle'))
    @Property(bool,notify=changed)
    def canDeposit(self):
        a=self.session.current_actor()
        return bool(self._data.get('customer')) and bool(a and PERMISSION in a.permissions)
    @Property(bool,notify=changed)
    def canRefund(self):
        return self.canDeposit and not self._busy and not self._refund_problem(self._refund_amount)
    @Property(bool,notify=changed)
    def refundHasCapacity(self):
        return self._refund_status is not None and Decimal(self._refund_status['available'])>0
    @Property(str,notify=changed)
    def refundAvailability(self):
        if not self.refundHasCapacity:return self._refund_problem('')
        return 'المتاح للاسترداد من العربون: '+money(self._refund_status['available'])
    @Property(str,notify=changed)
    def refundAmountMessage(self):
        return self._refund_problem(self._refund_amount) if self.refundHasCapacity and self._refund_amount else ''
    @Property(str,notify=changed)
    def refundBalance(self):
        value=Decimal(str((self._refund_status or {}).get('balance',self._data.get('customer',{}).get('balance',0))))
        meaning=('عليه ' if value>0 else 'له ')+money(abs(value)) if value else 'الحساب متوازن'
        return 'رصيد العميل الحالي: '+meaning
    @Property(bool,notify=changed)
    def canCorrect(self):
        a=self.session.current_actor()
        return bool(a and 'TRANSACTION_CORRECT' in a.permissions)
    @Property(str,notify=changed)
    def message(self):return self._error or self._message
    @Property(str,notify=changed)
    def header(self):
        c=self._data.get('customer',{})
        return ' — '.join(str(c.get(k) or '') for k in ('name','mobile'))
    @Property(str,notify=changed)
    def balance(self):return balance_text(self._data.get('customer',{}).get('balance',0))
    @Property(str,notify=changed)
    def lastMovement(self):return str(self._data.get('customer',{}).get('last_transaction_at') or '—')
    @Property(int,notify=changed)
    def pageNumber(self):return self._invoice_page if self._tab==1 else self._page
    @Property(bool,notify=changed)
    def hasMore(self):return bool((self._invoice_data if self._tab==1 else self._data).get('has_more'))
    @Property(int,notify=changed)
    def activeTab(self):return self._tab
    @Property('QVariantMap',notify=changed)
    def filters(self):
        values=self._invoice_filters if self._tab else (self._start_date,self._end_date,self._source,self._search)
        return dict(zip(('start','end','source','search'),values))
    @Property('QVariantList',notify=changed)
    def sources(self):
        if self._tab==1:
            from app.viewmodels.invoice_history_viewmodel import TYPE_TEXT
            return [{'code':'','text':'كل العمليات'}]+[{'code':k,'text':v} for k,v in TYPE_TEXT.items()]
        return [{'code':'','text':'كل المصادر'}]+[{'code':k,'text':v} for k,v in LABELS.items()]
    @Property('QVariantList',notify=changed)
    def invoiceRows(self):
        from app.viewmodels.invoice_history_viewmodel import KIND_TYPE_TEXT,STATUS_TEXT,SETTLEMENT_TEXT
        return [{'invoiceId':str(r['invoice_id']),'number':r['invoice_number'],
                 'date':str(r['finalized_at']), 'kind':KIND_TYPE_TEXT.get(r['transaction_kind'],''),
                 'total':money(r['invoice_total']),
                 'status':STATUS_TEXT.get(r['invoice_status'],'')+' · '+SETTLEMENT_TEXT.get(r['settlement_status'],'')}
                for r in self._invoice_data.get('items',[])]
    @Property('QVariantList',notify=changed)
    def methods(self):return METHODS
    @Property('QVariantList',notify=changed)
    def accounts(self):return [{'id':str(a['id']),'name':a['name']} for a in self._data.get('accounts',[])]
    @Property('QVariantList',notify=legsChanged)
    def legs(self):return self._legs
    @Property('QVariantList',notify=changed)
    def rows(self):
        return [{'key':r['row_kind']+str(r['id']),'date':str(r['occurred_at']),
                 'source':LABELS[r['source_type']],'reference':r.get('source_reference') or '—',
                 'invoiceId':str(r.get('invoice_id') or ''),'invoiceNumber':str(r.get('invoice_number') or ''),
                 'settlementId':str(r.get('settlement_id') or ''),
                 'depositId':str(r.get('deposit_id') or ''),
                 # A write-off that is not yet reversed offers its reversal on its own row (the same flow as the
                 # customer's Dead Cash history; permission and state are re-checked by the service).
                 'deadCashId':str(r.get('dead_cash_id') or '') if r['source_type']=='DEAD_CASH_WRITE_OFF' else '',
                 'deadCashReversed':bool(r.get('dead_cash_reversed')),
                 'amountValue':f"{abs(Decimal(str(r['amount_delta']))):,.2f}",
                 'description':(('استلام من العميل — ' if r['amount_delta']<0 else 'دفع للعميل — ')
                    if r['source_type']=='CUSTOMER_ACCOUNT_SETTLEMENT' else '')+(r.get('description') or ''),
                 'actor':r.get('actor_name') or '—',
                 'notes':r.get('settlement_notes') or r.get('dead_cash_reason') or r.get('notes') or '',
                 'payments':self._payment_text(r.get('payment_details')),
                 'amount': ('دون أثر على الرصيد' if not r['amount_delta'] else
                    ('على العميل: ' if r['amount_delta']>0 else 'للعميل: ')+money(abs(r['amount_delta']))),
                 'balance':balance_text(r['running_balance']),
                 'correctedSource':r.get('corrected_source') or ''}
                for r in self._data.get('rows',[])]

    @staticmethod
    def _payment_text(raw):
        if not raw:
            return ''
        legs=json.loads(raw) if isinstance(raw,(str,bytes)) else raw
        names={m['code']:m['text'] for m in METHODS}
        return ' · '.join(names.get(p['method'],'طريقة دفع')+' '+money(p['amount'])+
                         (' — '+p['account'] if p.get('account') else '') for p in legs)
