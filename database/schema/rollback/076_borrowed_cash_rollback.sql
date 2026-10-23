-- Rollback of 076. REFUSES when any borrowed advance exists or a later migration exists: the owner's records are never deleted to make a rollback pass.
SET @masgold_sql = IF(
 EXISTS(SELECT 1 FROM cash_advances WHERE direction = 'BORROW')
 OR EXISTS(SELECT 1 FROM schema_migrations WHERE version > 76),
 'SELECT * FROM ROLLBACK_076_REFUSED_borrowed_advances_exist', 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;
ALTER TABLE cash_advances DROP CHECK chk_cash_advance_direction;
ALTER TABLE cash_advances DROP CHECK chk_cash_advance_party;
ALTER TABLE cash_advances DROP COLUMN lender_name;
ALTER TABLE cash_advances DROP COLUMN direction;
ALTER TABLE cash_advances ADD CONSTRAINT chk_cash_advance_party CHECK (
 (kind = 'EMPLOYEE' AND employee_id IS NOT NULL AND customer_id IS NULL)
 OR (kind = 'CUSTOMER' AND customer_id IS NOT NULL AND employee_id IS NULL));
DELETE FROM cash_categories WHERE code IN ('BORROWED_CASH', 'BORROW_REPAY');
DELETE FROM schema_migrations WHERE version = 76;
