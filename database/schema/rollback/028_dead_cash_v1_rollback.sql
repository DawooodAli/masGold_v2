-- Refuse rollback once Dead Cash V1 history exists (a write-off, its warning or its ledger rows).
-- Never erase history: the 005 scaffolding tables themselves are not dropped by this rollback.
SET @masgold_sql = IF(
 EXISTS(SELECT 1 FROM dead_cash_writeoffs)
 OR EXISTS(SELECT 1 FROM customer_flags WHERE flag_type='DEAD_CASH_WARNING')
 OR EXISTS(SELECT 1 FROM customer_transactions t JOIN customer_transaction_types y ON y.id=t.transaction_type_id
           WHERE y.code IN ('DEAD_CASH_WRITE_OFF','DEAD_CASH_REVERSAL'))
 OR EXISTS(SELECT 1 FROM schema_migrations WHERE version>28),
 'SELECT * FROM ROLLBACK_028_REFUSED_history_or_later_migration', 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;
ALTER TABLE dead_cash_writeoffs DROP CHECK chk_dead_cash_reversal_state;
ALTER TABLE dead_cash_writeoffs DROP CHECK chk_dead_cash_reason;
ALTER TABLE dead_cash_writeoffs DROP CHECK chk_dead_cash_v1_receivable;
ALTER TABLE dead_cash_writeoffs DROP INDEX uq_dead_cash_request;
ALTER TABLE dead_cash_writeoffs DROP COLUMN request_hash;
ALTER TABLE dead_cash_writeoffs DROP COLUMN request_key;
DELETE FROM schema_migrations WHERE version=28;
