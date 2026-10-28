-- Rollback of 087. Refuses when a later migration exists or any customer was merged (the moved records would lose their trail).
SET @masgold_sql = IF(EXISTS(SELECT 1 FROM schema_migrations WHERE version > 87)
   OR EXISTS(SELECT 1 FROM customers WHERE merged_into_customer_id IS NOT NULL),
 'SELECT * FROM ROLLBACK_087_REFUSED_later_migration_or_merged_customers_exist', 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;
ALTER TABLE customers DROP CHECK chk_customers_merged_inactive;
ALTER TABLE customers DROP FOREIGN KEY fk_customers_merged_into;
ALTER TABLE customers DROP COLUMN merged_into_customer_id;
DELETE FROM schema_migrations WHERE version = 87;
