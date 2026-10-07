-- Old-gold → new-stock conversion without a manually chosen employee (owner review 2026-10-02).
-- The authenticated actor is recorded in the existing performed_by_user_id; the employee becomes
-- optional historical metadata. Every conversion must still name an actor (employee OR user).
-- Existing rows are untouched (all of them carry an employee).
SET @masgold_sql = IF(EXISTS(SELECT 1 FROM schema_migrations WHERE version=29),
 'DO 0', 'SELECT * FROM MIGRATION_030_REFUSED_missing_029');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(EXISTS(SELECT 1 FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=DATABASE()
 AND TABLE_NAME='old_gold_conversion_batches' AND COLUMN_NAME='performed_by_employee_id' AND IS_NULLABLE='NO'),
 "ALTER TABLE old_gold_conversion_batches MODIFY COLUMN performed_by_employee_id INT UNSIGNED DEFAULT NULL",
 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(NOT EXISTS(SELECT 1 FROM information_schema.TABLE_CONSTRAINTS WHERE CONSTRAINT_SCHEMA=DATABASE()
 AND TABLE_NAME='old_gold_conversion_batches' AND CONSTRAINT_NAME='chk_old_gold_conversion_actor'),
 "ALTER TABLE old_gold_conversion_batches ADD CONSTRAINT chk_old_gold_conversion_actor
   CHECK (performed_by_employee_id IS NOT NULL OR performed_by_user_id IS NOT NULL)", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

INSERT INTO schema_migrations(version,name) VALUES (30,'Old-gold conversion: authenticated actor instead of a chosen employee')
 ON DUPLICATE KEY UPDATE name=VALUES(name);
