-- NOT NULL can only come back while every conversion still names an employee; a conversion recorded
-- with the authenticated user only would otherwise need an invented employee. Refuse in that case.
SET @masgold_sql = IF(
 EXISTS(SELECT 1 FROM old_gold_conversion_batches WHERE performed_by_employee_id IS NULL)
 OR EXISTS(SELECT 1 FROM schema_migrations WHERE version>30),
 'SELECT * FROM ROLLBACK_030_REFUSED_actor_only_conversions_or_later_migration', 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;
ALTER TABLE old_gold_conversion_batches DROP CHECK chk_old_gold_conversion_actor;
ALTER TABLE old_gold_conversion_batches MODIFY COLUMN performed_by_employee_id INT UNSIGNED NOT NULL;
DELETE FROM schema_migrations WHERE version=30;
