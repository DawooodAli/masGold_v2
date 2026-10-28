-- Rollback of 082 (bar / pound cashback). REFUSES when any line carries a scrap kind or a cashback, or a later migration exists: history is never deleted to make it pass.
SET @masgold_sql = IF(
 EXISTS(SELECT 1 FROM invoice_lines WHERE scrap_kind IS NOT NULL OR cashback_amount IS NOT NULL)
 OR EXISTS(SELECT 1 FROM schema_migrations WHERE version > 82),
 'SELECT * FROM ROLLBACK_082_REFUSED_cashback_history_exists', 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;
SET @masgold_sql := IF(EXISTS (SELECT 1 FROM information_schema.CHECK_CONSTRAINTS WHERE CONSTRAINT_SCHEMA = DATABASE() AND CONSTRAINT_NAME = 'chk_invoice_line_native_old_gold' AND CHECK_CLAUSE LIKE '%cashback_amount%'),
 "ALTER TABLE invoice_lines DROP CHECK chk_invoice_line_native_old_gold", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;
SET @masgold_sql := IF(NOT EXISTS (SELECT 1 FROM information_schema.TABLE_CONSTRAINTS WHERE CONSTRAINT_SCHEMA = DATABASE() AND TABLE_NAME = 'invoice_lines' AND CONSTRAINT_NAME = 'chk_invoice_line_native_old_gold'),
 "ALTER TABLE invoice_lines ADD CONSTRAINT chk_invoice_line_native_old_gold CHECK (record_origin <> 'NATIVE' OR line_type <> 'BUY_OLD_GOLD' OR (making_per_gram IS NULL AND making_total IS NULL AND old_gold_movement_id IS NOT NULL AND discount_amount IS NOT NULL AND line_total = gross_total - discount_amount))", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;
SET @masgold_sql := IF(EXISTS (SELECT 1 FROM information_schema.TABLE_CONSTRAINTS WHERE CONSTRAINT_SCHEMA = DATABASE() AND TABLE_NAME = 'invoice_lines' AND CONSTRAINT_NAME = 'chk_invoice_line_scrap_kind'),
 "ALTER TABLE invoice_lines DROP CHECK chk_invoice_line_scrap_kind", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;
SET @masgold_sql := IF(EXISTS (SELECT 1 FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'invoice_lines' AND COLUMN_NAME = 'cashback_amount'),
 "ALTER TABLE invoice_lines DROP COLUMN cashback_amount", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;
SET @masgold_sql := IF(EXISTS (SELECT 1 FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'invoice_lines' AND COLUMN_NAME = 'scrap_kind'),
 "ALTER TABLE invoice_lines DROP COLUMN scrap_kind", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;
DELETE FROM schema_migrations WHERE version = 82;
