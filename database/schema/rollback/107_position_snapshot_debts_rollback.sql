-- Rollback of 107 (debts on the stocktaking sheets). Refuses when a later migration exists or any sheet already records debts (the recorded figures are never dropped silently).
SET @masgold_sql = IF(EXISTS(SELECT 1 FROM schema_migrations WHERE version > 107),
 'SELECT * FROM ROLLBACK_107_REFUSED_later_migration_exists', 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;
SET @masgold_sql = IF(EXISTS(SELECT 1 FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='position_snapshots' AND COLUMN_NAME='owed_customers_egp')
   AND (SELECT COUNT(*) FROM position_snapshots WHERE owed_customers_egp IS NOT NULL) > 0,
 'SELECT * FROM ROLLBACK_107_REFUSED_snapshot_debts_exist', 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;
SET @masgold_sql := IF(EXISTS(SELECT 1 FROM information_schema.TABLE_CONSTRAINTS WHERE CONSTRAINT_SCHEMA=DATABASE() AND TABLE_NAME='position_snapshots' AND CONSTRAINT_NAME='chk_pos_snapshot_debts'),
 "ALTER TABLE position_snapshots DROP CHECK chk_pos_snapshot_debts", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;
SET @masgold_sql := IF(EXISTS(SELECT 1 FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='position_snapshots' AND COLUMN_NAME='owed_marketer_egp'),
 "ALTER TABLE position_snapshots DROP COLUMN owed_marketer_egp", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;
SET @masgold_sql := IF(EXISTS(SELECT 1 FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='position_snapshots' AND COLUMN_NAME='owed_suppliers_egp'),
 "ALTER TABLE position_snapshots DROP COLUMN owed_suppliers_egp", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;
SET @masgold_sql := IF(EXISTS(SELECT 1 FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='position_snapshots' AND COLUMN_NAME='owed_customers_egp'),
 "ALTER TABLE position_snapshots DROP COLUMN owed_customers_egp", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;
DELETE FROM schema_migrations WHERE version = 107;
