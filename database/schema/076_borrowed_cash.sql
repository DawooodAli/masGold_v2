-- Borrowed cash (سلفة مستلفة): the shop takes cash from an employee, a customer or an outside person and gives it back later, no interest. Prepared; NOT applied to any database by the task that wrote it.
--   cash_advances gains `direction` (LEND = the shop lent, as before; BORROW = the shop owes) and `lender_name` (the typed name of an outside lender, kind THIRD_PARTY).
--   The money stays on the canonical ledgers: a borrow is a manual DEPOSIT (category BORROWED_CASH), its repayment a manual WITHDRAWAL (category BORROW_REPAY); what is still owed is derived, never stored.
--   Existing rows are LEND (the column DEFAULT). Every step is guarded (re-run safe).
SET @masgold_sql = IF(EXISTS(SELECT 1 FROM schema_migrations WHERE version=75),
 'DO 0', 'SELECT * FROM MIGRATION_076_REFUSED_missing_075');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

INSERT INTO cash_categories(code, name, direction, is_active, is_system, sort_order) VALUES
 ('BORROWED_CASH', 'سلفة مستلفة', 'IN', 1, 1, 96),
 ('BORROW_REPAY', 'سداد سلفة مستلفة', 'OUT', 1, 1, 86)
 ON DUPLICATE KEY UPDATE code = code;

SET @masgold_sql := IF(NOT EXISTS(SELECT 1 FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='cash_advances' AND COLUMN_NAME='direction'),
 "ALTER TABLE cash_advances ADD COLUMN direction VARCHAR(6) NOT NULL DEFAULT 'LEND' AFTER kind", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(NOT EXISTS(SELECT 1 FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='cash_advances' AND COLUMN_NAME='lender_name'),
 'ALTER TABLE cash_advances ADD COLUMN lender_name VARCHAR(100) DEFAULT NULL AFTER customer_id', 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(EXISTS(SELECT 1 FROM information_schema.TABLE_CONSTRAINTS WHERE CONSTRAINT_SCHEMA=DATABASE() AND TABLE_NAME='cash_advances' AND CONSTRAINT_NAME='chk_cash_advance_party'),
 'ALTER TABLE cash_advances DROP CHECK chk_cash_advance_party', 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(NOT EXISTS(SELECT 1 FROM information_schema.TABLE_CONSTRAINTS WHERE CONSTRAINT_SCHEMA=DATABASE() AND TABLE_NAME='cash_advances' AND CONSTRAINT_NAME='chk_cash_advance_party'),
 "ALTER TABLE cash_advances ADD CONSTRAINT chk_cash_advance_party CHECK (
  (kind = 'EMPLOYEE' AND employee_id IS NOT NULL AND customer_id IS NULL AND lender_name IS NULL)
  OR (kind = 'CUSTOMER' AND customer_id IS NOT NULL AND employee_id IS NULL AND lender_name IS NULL)
  OR (kind = 'THIRD_PARTY' AND employee_id IS NULL AND customer_id IS NULL AND lender_name IS NOT NULL AND CHAR_LENGTH(TRIM(lender_name)) > 0))", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(NOT EXISTS(SELECT 1 FROM information_schema.TABLE_CONSTRAINTS WHERE CONSTRAINT_SCHEMA=DATABASE() AND TABLE_NAME='cash_advances' AND CONSTRAINT_NAME='chk_cash_advance_direction'),
 "ALTER TABLE cash_advances ADD CONSTRAINT chk_cash_advance_direction CHECK (
  (direction = 'LEND' AND kind IN ('EMPLOYEE', 'CUSTOMER'))
  OR direction = 'BORROW')", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

INSERT INTO schema_migrations(version,name) VALUES (76,'Borrowed cash: the shop borrows from an employee, a customer or an outside person and repays')
 ON DUPLICATE KEY UPDATE name=VALUES(name);
