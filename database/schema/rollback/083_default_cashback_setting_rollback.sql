-- Rollback of 083 (default cashback setting). Refuses when a later migration exists.
SET @masgold_sql = IF(EXISTS(SELECT 1 FROM schema_migrations WHERE version > 83),
 'SELECT * FROM ROLLBACK_083_REFUSED_later_migration_exists', 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;
DELETE FROM system_settings WHERE setting_key = 'default_cashback_amount';
DELETE FROM schema_migrations WHERE version = 83;
