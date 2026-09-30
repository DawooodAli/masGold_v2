"""CS persistence. All writes use the caller's transaction; no mutable customer balance."""
from decimal import Decimal
from repositories.correction_repository import CorrectionRepository, _ids
from repositories.invoice_repository import _id

SOURCE = 'CUSTOMER_ACCOUNT_SETTLEMENT'


def settlement_code(value):
    return f'CS{int(value):06d}'


class CustomerAccountRepository(CorrectionRepository):
    def statement(self, customer_id, start=None, end=None, source='', search='', offset=0):
        # UNION includes money-only CS corrections as explicit zero-impact events.
        # Windows run before filters and paging; joins are one-to-one, never N+1.
        return self._fetch_all('''
          WITH movements AS (
            SELECT t.id, 'LEDGER' AS row_kind, t.amount_delta, t.occurred_at,
              t.reference_type, t.reference_id, t.reference_code, t.description, t.notes,
              t.created_by_user_id AS actor_id
            FROM customer_transactions t WHERE t.customer_id=%s
            UNION ALL
            SELECT c.id, 'EVENT', 0, c.occurred_at, 'TRANSACTION_CORRECTION', c.id,
              CONCAT('FC',LPAD(c.id,GREATEST(6,CHAR_LENGTH(c.id)),'0')), c.reason,NULL,c.performed_by_user_id
            FROM transaction_corrections c
            JOIN customer_account_settlements cs ON c.source_type='CUSTOMER_ACCOUNT_SETTLEMENT'
              AND cs.id=c.source_id
            WHERE cs.customer_id=%s AND c.correction_type='SETTLEMENT_CORRECTION'
          ), ordered AS (
            SELECT m.*, SUM(amount_delta) OVER (ORDER BY occurred_at,CASE WHEN row_kind='LEDGER' THEN 0 ELSE 1 END,id
              ROWS UNBOUNDED PRECEDING) AS running_balance FROM movements m
          ), payment_details AS (
            SELECT p.settlement_id, JSON_ARRAYAGG(JSON_OBJECT('method',p.payment_method_code,
              'amount',CAST(p.amount AS CHAR),'account',b.name)) AS payment_details
            FROM customer_account_settlement_payments p
            JOIN customer_account_settlements s ON s.id=p.settlement_id AND s.customer_id=%s
            LEFT JOIN bank_accounts b ON b.id=p.bank_account_id GROUP BY p.settlement_id
          ), resolved AS (
            SELECT o.*, u.display_name AS actor_name,
              CASE WHEN i.id IS NOT NULL THEN 'INVOICE'
                   WHEN x.id IS NOT NULL THEN 'EXCHANGE'
                   WHEN r.id IS NOT NULL THEN 'RETURN'
                   WHEN c.id IS NOT NULL THEN c.correction_type
                   WHEN cs.id IS NOT NULL THEN 'CUSTOMER_ACCOUNT_SETTLEMENT'
                   WHEN cd.id IS NOT NULL THEN IF(cd.direction='IN','CUSTOMER_DEPOSIT','CUSTOMER_DEPOSIT_REFUND')
                   ELSE 'UNKNOWN' END AS source_type,
              COALESCE(i.id,x.source_invoice_id,r.source_invoice_id,c.invoice_id) AS invoice_id,
              COALESCE(i.invoice_number,origin.invoice_number) AS invoice_number,
              COALESCE(i.invoice_number,
                CASE WHEN x.id IS NOT NULL THEN CONCAT('EX',LPAD(x.id,GREATEST(6,CHAR_LENGTH(x.id)),'0'))
                     WHEN r.id IS NOT NULL THEN CONCAT('RT',LPAD(r.id,GREATEST(6,CHAR_LENGTH(r.id)),'0'))
                     WHEN c.id IS NOT NULL THEN CONCAT(IF(c.correction_type='FULL_REVERSAL','RV','FC'),LPAD(c.id,GREATEST(6,CHAR_LENGTH(c.id)),'0'))
                     WHEN cs.id IS NOT NULL THEN CONCAT('CS',LPAD(cs.id,GREATEST(6,CHAR_LENGTH(cs.id)),'0'))
                     WHEN cd.id IS NOT NULL THEN CONCAT(IF(cd.direction='IN','DP','DR'),LPAD(cd.id,GREATEST(6,CHAR_LENGTH(cd.id)),'0')) END,
                o.reference_code) AS source_reference,
              CASE WHEN cs.id IS NOT NULL THEN cs.id
                   WHEN c.source_type='CUSTOMER_ACCOUNT_SETTLEMENT' THEN c.source_id END AS settlement_id,
              c.source_reference AS corrected_source, pd.payment_details, COALESCE(cs.notes, cd.notes) AS settlement_notes,
              cd.id AS deposit_id
            FROM ordered o
            LEFT JOIN users u ON u.id=o.actor_id
            LEFT JOIN invoices i ON o.reference_type='INVOICE' AND i.id=o.reference_id
            LEFT JOIN invoice_lifecycle_transactions x ON o.reference_type='INVOICE_LIFECYCLE' AND x.id=o.reference_id
            LEFT JOIN invoice_returns r ON o.reference_type='INVOICE_RETURN' AND r.id=o.reference_id
            LEFT JOIN transaction_corrections c ON o.reference_type='TRANSACTION_CORRECTION' AND c.id=o.reference_id
            LEFT JOIN customer_account_settlements cs ON o.reference_type='CUSTOMER_ACCOUNT_SETTLEMENT' AND cs.id=o.reference_id
            LEFT JOIN customer_deposits cd ON o.reference_type='CUSTOMER_DEPOSIT' AND cd.id=o.reference_id
            LEFT JOIN payment_details pd ON pd.settlement_id=cs.id
            LEFT JOIN invoices origin ON origin.id=COALESCE(x.source_invoice_id,r.source_invoice_id,c.invoice_id)
          )
          SELECT * FROM resolved WHERE (%s IS NULL OR occurred_at>=%s)
            AND (%s IS NULL OR occurred_at<%s) AND (%s='' OR source_type=%s)
            AND (%s='' OR LOCATE(%s,COALESCE(source_reference,''))>0
              OR LOCATE(%s,COALESCE(invoice_number,''))>0 OR LOCATE(%s,COALESCE(description,''))>0)
          ORDER BY occurred_at DESC,CASE WHEN row_kind='LEDGER' THEN 0 ELSE 1 END DESC,id DESC LIMIT 51 OFFSET %s
        ''', (int(customer_id),int(customer_id),int(customer_id),start,start,end,end,source,source,
              search,search,search,search,offset))

    def lock_balance(self, customer_id):
        # A current/locking read, including after waiting for another terminal.
        row = self._fetch_one('SELECT id FROM customers WHERE id=%s FOR UPDATE', (_id(customer_id),))
        if row is None:
            raise ValueError('العميل غير موجود.')
        rows = self._fetch_all('SELECT amount_delta FROM customer_transactions WHERE customer_id=%s '
                               'ORDER BY id FOR UPDATE', (_id(customer_id),))
        return sum((Decimal(r['amount_delta']) for r in rows), Decimal('0.00'))

    def by_request(self, key):
        return self._fetch_one('SELECT * FROM customer_account_settlements WHERE request_key=%s FOR UPDATE', (key,))

    def source(self, source_id, lock=False):
        row = self._fetch_one('SELECT * FROM customer_account_settlements WHERE id=%s' +
                              (' FOR UPDATE' if lock else ''), (_id(source_id),))
        if row is None:
            return None
        row = _ids(row)
        row['id'] = str(row['id'])
        customer = self._fetch_one('SELECT name FROM customers WHERE id=%s', (_id(row['customer_id']),))
        row['customer_name'] = customer['name'] if customer else None
        row['payments'] = [_ids(r) for r in self._fetch_all(
            'SELECT payment_method_code AS method_code, bank_account_id, amount FROM '
            'customer_account_settlement_payments WHERE settlement_id=%s ORDER BY id', (_id(source_id),))]
        for leg in row['payments']:
            leg['direction'] = row['direction']
        return row

    def insert_header(self, customer_id, key, digest, direction, amount, before, after, notes, actor, now):
        value, _ = self._execute('INSERT INTO customer_account_settlements '
            '(customer_id,request_key,request_hash,direction,amount,balance_before,balance_after,notes,'
            'performed_by_user_id,performed_by_employee_id,occurred_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)',
            (_id(customer_id),key,digest,direction,amount,before,after,notes,actor.user_id,actor.employee_id,now))
        return str(value)

    def post_payment(self, settlement_id, code, payment, amount, direction, bank, cash, actor, now):
        ledger, account = ('BANK', bank['id']) if bank else ('CASH', cash['id'])
        table, column = ('bank_movements','bank_account_id') if bank else ('cash_movements','cash_account_id')
        delta = amount if direction == 'IN' else -amount
        value, count = self._execute(f'INSERT INTO {table} ({column},movement_type_id,amount_delta,'
            'reference_type,reference_id,reference_code,description,occurred_at,created_by_user_id) '
            f'SELECT %s,id,%s,%s,%s,%s,%s,%s,%s FROM {ledger.lower()}_movement_types '
            'WHERE code=%s AND is_active=TRUE',
            (int(account),delta,SOURCE,_id(settlement_id),code,'تسوية حساب '+code,now,actor.user_id,SOURCE))
        if count != 1:
            raise RuntimeError('Settlement movement type is missing')
        self._execute('INSERT INTO customer_account_settlement_payments '
            '(settlement_id,payment_method_code,bank_account_id,amount,cash_movement_id,bank_movement_id) '
            'VALUES (%s,%s,%s,%s,%s,%s)',
            (_id(settlement_id),payment.method_code,int(account) if bank else None,amount,
             value if not bank else None,value if bank else None))
