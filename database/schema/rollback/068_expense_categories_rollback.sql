-- Rollback of 068. REFUSES when any operation already uses one of the new categories or a later migration exists: recorded expenses are never rewritten to make a rollback pass.
SET @masgold_sql = IF(
 EXISTS(SELECT 1 FROM financial_operations WHERE category IN ('UTILITIES','TRANSPORT','MAINTENANCE','HOSPITALITY','STAFF_ADVANCE'))
 OR EXISTS(SELECT 1 FROM schema_migrations WHERE version > 68),
 'SELECT * FROM ROLLBACK_068_REFUSED_new_categories_in_use', 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;
ALTER TABLE financial_operations DROP CHECK chk_fo_category;
ALTER TABLE financial_operations ADD CONSTRAINT chk_fo_category CHECK (category IS NULL OR (operation_type='MANUAL_DEPOSIT' AND category IN ('REVENUE','REPAIR')) OR (operation_type='MANUAL_WITHDRAWAL' AND category IN ('EXPENSE','SALARY','RENT','REPAIR')));
DELETE FROM schema_migrations WHERE version = 68;
