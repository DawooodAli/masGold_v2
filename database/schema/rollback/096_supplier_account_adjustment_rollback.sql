-- Rollback of 096. Refuses when a later migration exists or any account adjustment was posted.
SET @masgold_sql = IF(EXISTS(SELECT 1 FROM schema_migrations WHERE version > 96)
   OR EXISTS(SELECT 1 FROM supplier_account_adjustments),
 'SELECT * FROM ROLLBACK_096_REFUSED_later_migration_or_adjustment_exists', 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;
DROP TABLE IF EXISTS supplier_account_adjustments;
DELETE rp FROM role_permissions rp JOIN permissions p ON p.id = rp.permission_id WHERE p.code = 'SUPPLIER_ACCOUNT_ADJUST';
DELETE FROM permissions WHERE code = 'SUPPLIER_ACCOUNT_ADJUST';
DELETE FROM supplier_gold_movement_types WHERE code = 'ACCOUNT_ADJUSTMENT';
DELETE FROM supplier_making_movement_types WHERE code = 'ACCOUNT_ADJUSTMENT';
DELETE FROM schema_migrations WHERE version = 96;
