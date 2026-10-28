-- Rollback of 086. Refuses when a later migration exists or a customer cash movement was recorded.
SET @masgold_sql = IF(EXISTS(SELECT 1 FROM schema_migrations WHERE version > 86)
   OR EXISTS(SELECT 1 FROM financial_operations WHERE category IN ('CUSTOMER_CASH_IN','CUSTOMER_CASH_OUT')),
 'SELECT * FROM ROLLBACK_086_REFUSED_later_migration_or_data_exists', 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;
DELETE FROM cash_categories WHERE code IN ('CUSTOMER_CASH_IN','CUSTOMER_CASH_OUT');
DELETE FROM customer_transaction_types WHERE code IN ('CUSTOMER_CASH_IN','CUSTOMER_CASH_OUT');
DELETE FROM schema_migrations WHERE version = 86;
