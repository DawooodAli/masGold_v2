-- Refuse rollback once financial-operation history exists. Never erase ledger history.
SET @masgold_sql = IF(
 EXISTS(SELECT 1 FROM financial_operations)
 OR EXISTS(SELECT 1 FROM financial_operation_legs)
 OR EXISTS(SELECT 1 FROM transaction_corrections WHERE source_type='FINANCIAL_OPERATION')
 OR EXISTS(SELECT 1 FROM cash_movements WHERE reference_type='FINANCIAL_OPERATION')
 OR EXISTS(SELECT 1 FROM bank_movements WHERE reference_type='FINANCIAL_OPERATION')
 OR EXISTS(SELECT 1 FROM schema_migrations WHERE version>25),
 'SELECT * FROM ROLLBACK_025_REFUSED_history_or_later_migration', 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;
ALTER TABLE transaction_corrections DROP CHECK chk_correction_source;
ALTER TABLE transaction_corrections ADD CONSTRAINT chk_correction_source CHECK (
 (source_type IN ('INVOICE','EXCHANGE','RETURN') AND invoice_id IS NOT NULL)
 OR (source_type='CUSTOMER_ACCOUNT_SETTLEMENT' AND invoice_id IS NULL));
DROP TABLE financial_operation_legs;
DROP TABLE financial_operations;
DELETE rp FROM role_permissions rp JOIN permissions p ON p.id=rp.permission_id WHERE p.code='FINANCIAL_ACCOUNT_OPERATE';
DELETE FROM permissions WHERE code='FINANCIAL_ACCOUNT_OPERATE';
DELETE FROM cash_movement_types WHERE code IN ('MANUAL_DEPOSIT','MANUAL_WITHDRAWAL','ACCOUNT_TRANSFER_IN','ACCOUNT_TRANSFER_OUT');
DELETE FROM bank_movement_types WHERE code IN ('MANUAL_DEPOSIT','MANUAL_WITHDRAWAL','ACCOUNT_TRANSFER_IN','ACCOUNT_TRANSFER_OUT');
DELETE FROM schema_migrations WHERE version=25;
