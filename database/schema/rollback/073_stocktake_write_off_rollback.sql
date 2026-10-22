-- Rollback of 073. REFUSES when any stocktake approval exists or a later migration exists: a written-off piece is never silently restored.
SET @masgold_sql = IF(
 EXISTS(SELECT 1 FROM stocktake_adjustments)
 OR EXISTS(SELECT 1 FROM schema_migrations WHERE version > 73),
 'SELECT * FROM ROLLBACK_073_REFUSED_stocktake_records_exist', 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;
DROP TABLE IF EXISTS stocktake_adjustment_items;
DROP TABLE IF EXISTS stocktake_adjustments;
DELETE FROM role_permissions WHERE permission_id IN (SELECT id FROM permissions WHERE code='STOCKTAKE_ADJUST');
DELETE FROM permissions WHERE code='STOCKTAKE_ADJUST';
DELETE FROM inventory_movement_types WHERE code='STOCKTAKE_WRITE_OFF' AND NOT EXISTS (SELECT 1 FROM inventory_movements m WHERE m.movement_type_id = inventory_movement_types.id);
DELETE FROM schema_migrations WHERE version = 73;
