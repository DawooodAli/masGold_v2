-- Rollback of 097 (small piece making settings). Refuses when a later migration exists.
SET @masgold_sql = IF(EXISTS(SELECT 1 FROM schema_migrations WHERE version > 97),
 'SELECT * FROM ROLLBACK_097_REFUSED_later_migration_exists', 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;
DELETE FROM system_settings WHERE setting_key IN ('small_piece_making_min', 'small_piece_making_max');
DELETE FROM schema_migrations WHERE version = 97;
