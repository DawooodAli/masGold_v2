-- Rollback of 092. Refuses when a later migration exists or any group adjustment was posted.
SET @masgold_sql = IF(EXISTS(SELECT 1 FROM schema_migrations WHERE version > 92)
   OR EXISTS(SELECT 1 FROM supplier_invoice_group_adjustments),
 'SELECT * FROM ROLLBACK_092_REFUSED_later_migration_or_adjustment_exists', 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;
DROP TABLE IF EXISTS supplier_invoice_group_adjustments;
DELETE rp FROM role_permissions rp JOIN permissions p ON p.id = rp.permission_id WHERE p.code = 'SUPPLIER_INVOICE_ADJUST';
DELETE FROM permissions WHERE code = 'SUPPLIER_INVOICE_ADJUST';
DELETE FROM supplier_gold_movement_types WHERE code = 'GROUP_ADJUSTMENT';
DELETE FROM schema_migrations WHERE version = 92;
