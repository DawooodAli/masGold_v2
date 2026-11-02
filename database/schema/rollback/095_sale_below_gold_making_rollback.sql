-- Rollback of 095. Refuses when a later migration exists or any invoice line already stores a negative total making (the sign CHECK could not be restored).
SET @masgold_sql = IF(EXISTS(SELECT 1 FROM schema_migrations WHERE version > 95)
   OR EXISTS(SELECT 1 FROM invoice_lines WHERE making_total < 0),
 'SELECT * FROM ROLLBACK_095_REFUSED_later_migration_or_negative_making_exists', 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;
SET @masgold_sql := IF(NOT EXISTS(SELECT 1 FROM information_schema.TABLE_CONSTRAINTS WHERE CONSTRAINT_SCHEMA=DATABASE()
 AND TABLE_NAME='invoice_lines' AND CONSTRAINT_NAME='chk_invoice_line_making_total' AND CONSTRAINT_TYPE='CHECK'),
 'ALTER TABLE invoice_lines ADD CONSTRAINT chk_invoice_line_making_total CHECK (making_total IS NULL OR making_total >= 0)', 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;
DELETE FROM schema_migrations WHERE version = 95;
