-- Old-gold BAR / POUND lines with a CASHBACK (owner request 2026-10-28). Prepared; NOT applied to any database by the task that wrote it.
--   invoice_lines.scrap_kind: NULL = an ordinary scrap line (percent discount); 'BAR' = a gold bar (always karat 24), 'POUND' = a gold pound / sovereign (always karat 21).
--   invoice_lines.cashback_amount: the extra EGP the shop pays the customer on a BAR / POUND line instead of a discount (a line with a scrap kind has no discount).
--   The BUY_OLD_GOLD total becomes gross_total - discount_amount + COALESCE(cashback_amount, 0). Existing rows are unchanged (both columns NULL).
SET @masgold_sql = IF(EXISTS(SELECT 1 FROM schema_migrations WHERE version=81),
 'DO 0', 'SELECT * FROM MIGRATION_082_REFUSED_missing_081');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(NOT EXISTS (SELECT 1 FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'invoice_lines' AND COLUMN_NAME = 'scrap_kind'),
 "ALTER TABLE invoice_lines ADD COLUMN scrap_kind VARCHAR(8) NULL", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;
SET @masgold_sql := IF(NOT EXISTS (SELECT 1 FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'invoice_lines' AND COLUMN_NAME = 'cashback_amount'),
 "ALTER TABLE invoice_lines ADD COLUMN cashback_amount DECIMAL(16,2) NULL", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(NOT EXISTS (SELECT 1 FROM information_schema.TABLE_CONSTRAINTS WHERE CONSTRAINT_SCHEMA = DATABASE() AND TABLE_NAME = 'invoice_lines' AND CONSTRAINT_NAME = 'chk_invoice_line_scrap_kind'),
 "ALTER TABLE invoice_lines ADD CONSTRAINT chk_invoice_line_scrap_kind CHECK ((scrap_kind IS NULL AND cashback_amount IS NULL) OR (scrap_kind IN ('BAR', 'POUND') AND line_type = 'BUY_OLD_GOLD' AND cashback_amount IS NOT NULL AND cashback_amount >= 0 AND discount_type IS NULL AND discount_amount = 0))", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(EXISTS (SELECT 1 FROM information_schema.CHECK_CONSTRAINTS WHERE CONSTRAINT_SCHEMA = DATABASE() AND CONSTRAINT_NAME = 'chk_invoice_line_native_old_gold' AND CHECK_CLAUSE NOT LIKE '%cashback_amount%'),
 "ALTER TABLE invoice_lines DROP CHECK chk_invoice_line_native_old_gold", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;
SET @masgold_sql := IF(NOT EXISTS (SELECT 1 FROM information_schema.TABLE_CONSTRAINTS WHERE CONSTRAINT_SCHEMA = DATABASE() AND TABLE_NAME = 'invoice_lines' AND CONSTRAINT_NAME = 'chk_invoice_line_native_old_gold'),
 "ALTER TABLE invoice_lines ADD CONSTRAINT chk_invoice_line_native_old_gold CHECK (record_origin <> 'NATIVE' OR line_type <> 'BUY_OLD_GOLD' OR (making_per_gram IS NULL AND making_total IS NULL AND old_gold_movement_id IS NOT NULL AND discount_amount IS NOT NULL AND line_total = gross_total - discount_amount + COALESCE(cashback_amount, 0)))", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

-- Business limits in System Settings (owner request 2026-10-28). The discount is open, so min = "below this goes to the manager reviews"; max is no longer enforced.
INSERT INTO system_settings (setting_key, setting_value, setting_type, description, is_public) VALUES
('sale_old_gold_discount_min_pct', '1.25', 'DECIMAL', 'أقل نسبة خصم للكسر في فاتورة فيها بيع؛ أقل منها تُراجَع من المدير', 0),
('cashback_review_limit', '100', 'DECIMAL', 'أعلى كاش باك للسبائك/الجنيهات بدون مراجعة (ج)؛ أعلى منه يُراجَع من المدير', 0)
ON DUPLICATE KEY UPDATE description = VALUES(description);
UPDATE system_settings SET description = 'أقل نسبة خصم للكسر في فاتورة كسر فقط؛ أقل منها تُراجَع من المدير' WHERE setting_key = 'min_old_gold_discount_pct';
UPDATE system_settings SET description = 'غير مفعّل — الخصم مفتوح بلا حد أعلى' WHERE setting_key = 'max_old_gold_discount_pct';

INSERT INTO schema_migrations(version,name) VALUES (82,'Old-gold bar and pound lines with cashback')
 ON DUPLICATE KEY UPDATE name=VALUES(name);
