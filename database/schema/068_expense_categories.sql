-- More expense categories for the categorised manual cash movements of 025 / 026 (the treasury «حركة نقدية جديدة»). Prepared; NOT applied to any database by the task that wrote it.
--   New cash OUT categories: UTILITIES (كهرباء ومياه), TRANSPORT (نقل), MAINTENANCE (صيانة), HOSPITALITY (بوفيه وضيافة), STAFF_ADVANCE (سلف موظفين). Existing ones are unchanged:
--   OUT EXPENSE / SALARY / RENT / REPAIR, IN REVENUE / REPAIR. Only the CHECK constraint chk_fo_category is widened; no row is rewritten and no column changes.
-- The CHECK expression has NOT been executed on a MySQL server before the rehearsal gate. Every step is guarded (re-run safe).
SET @masgold_sql = IF(EXISTS(SELECT 1 FROM schema_migrations WHERE version=67),
 'DO 0', 'SELECT * FROM MIGRATION_068_REFUSED_missing_067');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(EXISTS(SELECT 1 FROM information_schema.TABLE_CONSTRAINTS tc JOIN information_schema.CHECK_CONSTRAINTS cc ON cc.CONSTRAINT_SCHEMA=tc.CONSTRAINT_SCHEMA AND cc.CONSTRAINT_NAME=tc.CONSTRAINT_NAME
 WHERE tc.TABLE_SCHEMA=DATABASE() AND tc.TABLE_NAME='financial_operations' AND tc.CONSTRAINT_NAME='chk_fo_category' AND cc.CHECK_CLAUSE NOT LIKE '%STAFF_ADVANCE%'),
 'ALTER TABLE financial_operations DROP CHECK chk_fo_category', 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(NOT EXISTS(SELECT 1 FROM information_schema.TABLE_CONSTRAINTS WHERE CONSTRAINT_SCHEMA=DATABASE() AND TABLE_NAME='financial_operations' AND CONSTRAINT_NAME='chk_fo_category'),
 "ALTER TABLE financial_operations ADD CONSTRAINT chk_fo_category CHECK (category IS NULL OR (operation_type='MANUAL_DEPOSIT' AND category IN ('REVENUE','REPAIR')) OR (operation_type='MANUAL_WITHDRAWAL' AND category IN ('EXPENSE','SALARY','RENT','REPAIR','UTILITIES','TRANSPORT','MAINTENANCE','HOSPITALITY','STAFF_ADVANCE')))",
 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

INSERT INTO schema_migrations(version,name) VALUES (68,'More expense categories for categorised manual cash movements')
 ON DUPLICATE KEY UPDATE name=VALUES(name);
