-- Refuse rollback once deposit history exists. Never erase ledger history.
SET @masgold_sql = IF(
 EXISTS(SELECT 1 FROM customer_deposits)
 OR EXISTS(SELECT 1 FROM transaction_corrections WHERE source_type='CUSTOMER_DEPOSIT')
 OR EXISTS(SELECT 1 FROM customer_transactions WHERE reference_type='CUSTOMER_DEPOSIT')
 OR EXISTS(SELECT 1 FROM cash_movements WHERE reference_type='CUSTOMER_DEPOSIT')
 OR EXISTS(SELECT 1 FROM schema_migrations WHERE version>27),
 'SELECT * FROM ROLLBACK_027_REFUSED_history_or_later_migration', 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;
ALTER TABLE transaction_corrections DROP CHECK chk_correction_source;
ALTER TABLE transaction_corrections ADD CONSTRAINT chk_correction_source CHECK (
 (source_type IN ('INVOICE','EXCHANGE','RETURN') AND invoice_id IS NOT NULL)
 OR (source_type='CUSTOMER_ACCOUNT_SETTLEMENT' AND invoice_id IS NULL)
 OR (source_type='FINANCIAL_OPERATION' AND invoice_id IS NULL AND correction_type='FULL_REVERSAL'));
DROP TABLE customer_deposits;
DELETE FROM customer_transaction_types WHERE code IN ('CUSTOMER_DEPOSIT','CUSTOMER_DEPOSIT_REFUND');
DELETE FROM cash_movement_types WHERE code IN ('CUSTOMER_DEPOSIT','CUSTOMER_DEPOSIT_REFUND');
DELETE FROM schema_migrations WHERE version=27;
