-- Rollback of 074. REFUSES when any advance exists or a later migration exists: the owner's records are never deleted to make a rollback pass.
SET @masgold_sql = IF(
 EXISTS(SELECT 1 FROM cash_advances)
 OR EXISTS(SELECT 1 FROM schema_migrations WHERE version > 74),
 'SELECT * FROM ROLLBACK_074_REFUSED_advances_exist', 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;
DROP TABLE IF EXISTS cash_advance_repayments;
DROP TABLE IF EXISTS cash_advances;
DELETE FROM cash_categories WHERE code IN ('CUSTOMER_CUSTODY', 'ADVANCE_REPAY');
DELETE FROM schema_migrations WHERE version = 74;
