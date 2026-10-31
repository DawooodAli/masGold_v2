-- Rollback of 093. Refuses when a later migration exists or any making receipt was posted.
SET @masgold_sql = IF(EXISTS(SELECT 1 FROM schema_migrations WHERE version > 93)
   OR EXISTS(SELECT 1 FROM supplier_making_payments WHERE direction = 'RECEIVE'),
 'SELECT * FROM ROLLBACK_093_REFUSED_later_migration_or_receipt_exists', 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;
ALTER TABLE supplier_making_payments DROP CHECK chk_smp_direction;
ALTER TABLE supplier_making_payments DROP CHECK chk_smp_balance;
ALTER TABLE supplier_making_payments ADD CONSTRAINT chk_smp_balance CHECK (balance_after = balance_before - amount);
ALTER TABLE supplier_making_payments DROP COLUMN direction;
DELETE FROM supplier_making_movement_types WHERE code IN ('RECEIPT','RECEIPT_REVERSAL');
DELETE FROM cash_movement_types WHERE code = 'SUPPLIER_MAKING_RECEIPT';
DELETE FROM bank_movement_types WHERE code = 'SUPPLIER_MAKING_RECEIPT';
DELETE FROM schema_migrations WHERE version = 93;
