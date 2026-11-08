-- Rollback of 100 (day close). Refuses when a later migration exists or any close was recorded (history is never deleted silently).
SET @masgold_sql = IF(EXISTS(SELECT 1 FROM schema_migrations WHERE version > 100),
 'SELECT * FROM ROLLBACK_100_REFUSED_later_migration_exists', 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;
SET @masgold_sql = IF(EXISTS(SELECT 1 FROM information_schema.tables WHERE table_schema = DATABASE() AND table_name = 'day_closes')
   AND (SELECT COUNT(*) FROM day_closes) > 0,
 'SELECT * FROM ROLLBACK_100_REFUSED_day_closes_exist', 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;
DELETE FROM role_permissions WHERE permission_id IN (SELECT id FROM permissions WHERE code = 'DAY_CLOSE');
DELETE FROM permissions WHERE code = 'DAY_CLOSE';
DROP TABLE IF EXISTS day_closes;
DELETE FROM schema_migrations WHERE version = 100;
