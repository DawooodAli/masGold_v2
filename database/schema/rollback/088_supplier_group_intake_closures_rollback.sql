-- Rollback of 088. Refuses when a later migration exists or any group was taken off the entry list.
SET @masgold_sql = IF(EXISTS(SELECT 1 FROM schema_migrations WHERE version > 88)
   OR EXISTS(SELECT 1 FROM supplier_group_intake_closures),
 'SELECT * FROM ROLLBACK_088_REFUSED_later_migration_or_closures_exist', 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;
DROP TABLE IF EXISTS supplier_group_intake_closures;
DELETE FROM schema_migrations WHERE version = 88;
