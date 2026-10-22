-- Rollback of 069. REFUSES when the owner added any category of his own (is_system = 0) or a later migration exists: recorded categories are never deleted to make a rollback pass.
-- Disabled or renamed system categories are restored to the fixed list of 068 together with the CHECK.
SET @masgold_sql = IF(
 EXISTS(SELECT 1 FROM cash_categories WHERE is_system = 0)
 OR EXISTS(SELECT 1 FROM schema_migrations WHERE version > 69),
 'SELECT * FROM ROLLBACK_069_REFUSED_custom_categories_exist', 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;
ALTER TABLE financial_operations DROP FOREIGN KEY fk_fo_category;
ALTER TABLE financial_operations DROP CHECK chk_fo_category;
ALTER TABLE financial_operations ADD CONSTRAINT chk_fo_category CHECK (category IS NULL OR (operation_type='MANUAL_DEPOSIT' AND category IN ('REVENUE','REPAIR')) OR (operation_type='MANUAL_WITHDRAWAL' AND category IN ('EXPENSE','SALARY','RENT','REPAIR','UTILITIES','TRANSPORT','MAINTENANCE','HOSPITALITY','STAFF_ADVANCE')));
DROP TABLE IF EXISTS cash_categories;
DELETE FROM schema_migrations WHERE version = 69;
