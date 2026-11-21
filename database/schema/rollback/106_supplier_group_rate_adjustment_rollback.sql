-- Rollback of 106. Refuses when a later migration exists or any group making-rate adjustment was posted (history is never deleted silently).
SET @masgold_sql = IF(EXISTS(SELECT 1 FROM schema_migrations WHERE version > 106)
   OR EXISTS(SELECT 1 FROM supplier_invoice_group_rate_adjustments),
 'SELECT * FROM ROLLBACK_106_REFUSED_later_migration_or_adjustment_exists', 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;
DROP TABLE IF EXISTS supplier_invoice_group_rate_adjustments;
DELETE FROM supplier_making_movement_types WHERE code = 'GROUP_RATE_ADJUSTMENT';
DELETE FROM schema_migrations WHERE version = 106;
