-- Rollback of 102 (supplier adjustment reversal). Refuses when a later migration exists or any reversal row was recorded (history is never deleted silently).
SET @masgold_sql = IF(EXISTS(SELECT 1 FROM schema_migrations WHERE version > 102),
 'SELECT * FROM ROLLBACK_102_REFUSED_later_migration_exists', 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;
SET @masgold_sql = IF(EXISTS(SELECT 1 FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'supplier_account_adjustments' AND COLUMN_NAME = 'reverses_id')
   AND (SELECT COUNT(*) FROM supplier_account_adjustments WHERE reverses_id IS NOT NULL) > 0,
 'SELECT * FROM ROLLBACK_102_REFUSED_adjustment_reversals_exist', 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;
ALTER TABLE supplier_account_adjustments DROP CHECK chk_saa_reverses;
ALTER TABLE supplier_account_adjustments DROP FOREIGN KEY fk_saa_reverses;
ALTER TABLE supplier_account_adjustments DROP INDEX uq_saa_reverses;
ALTER TABLE supplier_account_adjustments DROP COLUMN reverses_id;
DELETE FROM schema_migrations WHERE version = 102;
