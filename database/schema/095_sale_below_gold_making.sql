-- A sale agreed BELOW the gold value (owner 2026-11-03): the invoice is saved normally and goes to the manager's review. The line keeps its exact economics
-- (line_total = gross_total + making_total), so the derived making of such a line is NEGATIVE. Only the sign CHECK of the stored total making is lifted;
-- the per-gram making (typed, never derived for these lines) stays non-negative. Every step is guarded (re-run safe).
SET @masgold_sql = IF(EXISTS(SELECT 1 FROM schema_migrations WHERE version=94),
 'DO 0', 'SELECT * FROM MIGRATION_095_REFUSED_missing_094');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(EXISTS(SELECT 1 FROM information_schema.TABLE_CONSTRAINTS WHERE CONSTRAINT_SCHEMA=DATABASE()
 AND TABLE_NAME='invoice_lines' AND CONSTRAINT_NAME='chk_invoice_line_making_total' AND CONSTRAINT_TYPE='CHECK'),
 'ALTER TABLE invoice_lines DROP CHECK chk_invoice_line_making_total', 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

INSERT INTO schema_migrations(version,name) VALUES (95,'Invoice lines: a sale below the gold value stores a negative total making')
 ON DUPLICATE KEY UPDATE name=VALUES(name);
