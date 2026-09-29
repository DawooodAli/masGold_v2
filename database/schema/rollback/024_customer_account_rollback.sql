-- Refuse rollback once CS financial/audit history exists. Never erase ledger history.
SET @masgold_sql = IF(
 EXISTS(SELECT 1 FROM customer_account_settlements)
 OR EXISTS(SELECT 1 FROM transaction_corrections WHERE source_type='CUSTOMER_ACCOUNT_SETTLEMENT')
 OR EXISTS(SELECT 1 FROM customer_transactions WHERE reference_type='CUSTOMER_ACCOUNT_SETTLEMENT')
 OR EXISTS(SELECT 1 FROM cash_movements WHERE reference_type='CUSTOMER_ACCOUNT_SETTLEMENT')
 OR EXISTS(SELECT 1 FROM bank_movements WHERE reference_type='CUSTOMER_ACCOUNT_SETTLEMENT')
 OR EXISTS(SELECT 1 FROM schema_migrations WHERE version>24),
 'SELECT * FROM ROLLBACK_024_REFUSED_history_or_later_migration', 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;
ALTER TABLE transaction_corrections DROP CHECK chk_correction_source;
ALTER TABLE transaction_corrections MODIFY COLUMN source_type VARCHAR(20) NOT NULL,
 MODIFY COLUMN invoice_id BIGINT UNSIGNED NOT NULL,
 MODIFY COLUMN reversal_key VARCHAR(40) GENERATED ALWAYS AS
  (CASE WHEN correction_type='FULL_REVERSAL' THEN CONCAT(source_type, ':', source_id) ELSE NULL END) STORED,
 MODIFY COLUMN settlement_key VARCHAR(60) GENERATED ALWAYS AS
  (CASE WHEN correction_type='SETTLEMENT_CORRECTION' THEN CONCAT(source_type, ':', source_id, ':', chain_sequence) ELSE NULL END) STORED;
ALTER TABLE transaction_corrections ADD CONSTRAINT chk_correction_source CHECK (source_type IN ('INVOICE','EXCHANGE','RETURN'));
DROP TABLE customer_account_settlement_payments;
DROP TABLE customer_account_settlements;
DELETE rp FROM role_permissions rp JOIN permissions p ON p.id=rp.permission_id WHERE p.code='CUSTOMER_ACCOUNT_SETTLE';
DELETE FROM permissions WHERE code='CUSTOMER_ACCOUNT_SETTLE';
DELETE FROM customer_transaction_types WHERE code='CUSTOMER_ACCOUNT_SETTLEMENT';
DELETE FROM cash_movement_types WHERE code='CUSTOMER_ACCOUNT_SETTLEMENT';
DELETE FROM bank_movement_types WHERE code='CUSTOMER_ACCOUNT_SETTLEMENT';
DELETE FROM schema_migrations WHERE version=24;
