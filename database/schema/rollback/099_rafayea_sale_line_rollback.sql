-- Rollback of 099 (rafayea sale line). Refuses when a later migration exists or any SELL_RAFAYEA line exists (history is never rewritten).
SET @masgold_sql = IF(EXISTS(SELECT 1 FROM schema_migrations WHERE version > 99),
 'SELECT * FROM ROLLBACK_099_REFUSED_later_migration_exists', 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;
SET @masgold_sql = IF(EXISTS(SELECT 1 FROM invoice_lines WHERE line_type = 'SELL_RAFAYEA'),
 'SELECT * FROM ROLLBACK_099_REFUSED_rafayea_lines_exist', 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;
ALTER TABLE invoice_lines DROP CHECK chk_invoice_line_native_rafayea;
ALTER TABLE invoice_lines DROP CHECK chk_invoice_line_native_values;
ALTER TABLE invoice_lines DROP CHECK chk_invoice_line_identification;
ALTER TABLE invoice_lines DROP CHECK chk_invoice_line_type;
ALTER TABLE invoice_lines ADD CONSTRAINT chk_invoice_line_type CHECK (line_type IN ('SELL_INVENTORY', 'BUY_OLD_GOLD', 'OTHER', 'SELL_OLD_GOLD', 'SELL_UNIDENTIFIED'));
ALTER TABLE invoice_lines ADD CONSTRAINT chk_invoice_line_identification CHECK (identification_method IS NULL OR identification_method IN ('SCAN', 'SEARCH', 'MANUAL', 'OLD_GOLD_STOCK'));
ALTER TABLE invoice_lines ADD CONSTRAINT chk_invoice_line_native_values CHECK (record_origin <> 'NATIVE' OR (line_type IN ('SELL_INVENTORY', 'BUY_OLD_GOLD', 'SELL_OLD_GOLD', 'SELL_UNIDENTIFIED') AND quantity = 1 AND gold_price_per_gram IS NOT NULL AND line_total IS NOT NULL AND gross_total IS NOT NULL AND gross_total = ROUND(weight_grams * gold_price_per_gram, 2)));
DELETE FROM schema_migrations WHERE version = 99;
