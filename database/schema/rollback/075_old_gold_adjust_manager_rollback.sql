-- Rollback of 075. Refuses when a later migration exists or a manual Scrap adjustment was posted; otherwise it only takes the right back from the MANAGER role (the owner keeps it).
SET @masgold_sql = IF(
 EXISTS(SELECT 1 FROM schema_migrations WHERE version > 75)
 OR EXISTS(SELECT 1 FROM old_gold_movements WHERE reference_type = 'MANUAL_ADJUSTMENT'),
 'SELECT * FROM ROLLBACK_075_REFUSED_adjustments_exist', 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;
DELETE rp FROM role_permissions rp JOIN roles r ON r.id = rp.role_id JOIN permissions p ON p.id = rp.permission_id WHERE r.code = 'MANAGER' AND p.code = 'OLD_GOLD_ADJUST';
DELETE FROM schema_migrations WHERE version = 75;
