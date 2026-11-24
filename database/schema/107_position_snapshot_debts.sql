-- Debts of the shop on the stocktaking sheets (owner 2026-11-24): the gold profit must take away the money the shop OWES. Prepared; NOT applied to any database by the task that wrote it.
--   position_snapshots gets three NULLABLE EGP columns, all >= 0 (the amount the shop owes, a positive number):
--     owed_customers_egp  the customers' CREDIT (vw_customer_balances, the negative balances, i.e. owed TO the customer)
--     owed_suppliers_egp  the NET supplier MAKING account the shop owes (supplier_making_movements; a net credit of the suppliers counts as zero debt)
--     owed_marketer_egp   a marketer's cash balance that is NEGATIVE (the shop owes him); a positive custody cash is the shop's own money and is not a debt
--   NULL = the sheet was taken before this migration (its debts were never recorded and are never recomputed historically): a profit that needs that sheet is reported as unavailable.
-- No existing row is rewritten. Every step is guarded (re-run safe).
SET @masgold_sql = IF(EXISTS(SELECT 1 FROM schema_migrations WHERE version=106),
 'DO 0', 'SELECT * FROM MIGRATION_107_REFUSED_missing_106');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;
SET @masgold_sql = IF(EXISTS(SELECT 1 FROM information_schema.TABLES WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='position_snapshots'),
 'DO 0', 'SELECT * FROM MIGRATION_107_REFUSED_missing_067_position_snapshots');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(NOT EXISTS(SELECT 1 FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='position_snapshots' AND COLUMN_NAME='owed_customers_egp'),
 "ALTER TABLE position_snapshots ADD COLUMN owed_customers_egp DECIMAL(16,2) DEFAULT NULL AFTER bank_egp", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;
SET @masgold_sql := IF(NOT EXISTS(SELECT 1 FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='position_snapshots' AND COLUMN_NAME='owed_suppliers_egp'),
 "ALTER TABLE position_snapshots ADD COLUMN owed_suppliers_egp DECIMAL(16,2) DEFAULT NULL AFTER owed_customers_egp", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;
SET @masgold_sql := IF(NOT EXISTS(SELECT 1 FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='position_snapshots' AND COLUMN_NAME='owed_marketer_egp'),
 "ALTER TABLE position_snapshots ADD COLUMN owed_marketer_egp DECIMAL(16,2) DEFAULT NULL AFTER owed_suppliers_egp", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(NOT EXISTS(SELECT 1 FROM information_schema.TABLE_CONSTRAINTS WHERE CONSTRAINT_SCHEMA=DATABASE() AND TABLE_NAME='position_snapshots' AND CONSTRAINT_NAME='chk_pos_snapshot_debts'),
 "ALTER TABLE position_snapshots ADD CONSTRAINT chk_pos_snapshot_debts CHECK ((owed_customers_egp IS NULL AND owed_suppliers_egp IS NULL AND owed_marketer_egp IS NULL) OR (owed_customers_egp >= 0 AND owed_suppliers_egp >= 0 AND owed_marketer_egp >= 0))", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

INSERT INTO schema_migrations(version,name) VALUES (107,'Debts (customers, suppliers, marketer) on the stocktaking sheets for the gold profit')
 ON DUPLICATE KEY UPDATE name=VALUES(name);
