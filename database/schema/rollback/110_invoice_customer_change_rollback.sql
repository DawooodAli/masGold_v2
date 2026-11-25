-- Rollback of 110 (customer ledger type for changing the customer of a finalized invoice). Refuses when a later migration exists or any ledger row already uses the type (history is never deleted silently).
SET @masgold_sql = IF(EXISTS(SELECT 1 FROM schema_migrations WHERE version > 110),
 'SELECT * FROM ROLLBACK_110_REFUSED_later_migration_exists', 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;
SET @masgold_sql = IF((SELECT COUNT(*) FROM customer_transactions ct JOIN customer_transaction_types tt ON tt.id = ct.transaction_type_id WHERE tt.code = 'INVOICE_CUSTOMER_CHANGE') > 0,
 'SELECT * FROM ROLLBACK_110_REFUSED_customer_changes_exist', 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;
DELETE FROM customer_transaction_types WHERE code = 'INVOICE_CUSTOMER_CHANGE';
DELETE FROM schema_migrations WHERE version = 110;
