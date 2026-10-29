-- Rollback of 089. Refuses when a later migration exists or any case already carries a resolution.
SET @masgold_sql = IF(EXISTS(SELECT 1 FROM schema_migrations WHERE version > 89)
   OR EXISTS(SELECT 1 FROM inventory_reconciliation_cases WHERE resolution_code IS NOT NULL),
 'SELECT * FROM ROLLBACK_089_REFUSED_later_migration_or_resolved_cases_exist', 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;
ALTER TABLE inventory_reconciliation_cases DROP CHECK chk_reconciliation_resolution_code;
ALTER TABLE inventory_reconciliation_cases DROP COLUMN resolution_notes;
ALTER TABLE inventory_reconciliation_cases DROP COLUMN resolution_code;
DELETE FROM schema_migrations WHERE version = 89;
