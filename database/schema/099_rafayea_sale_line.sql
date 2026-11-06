-- Rafayea sale line «رفايع» (owner 2026-11-06): loose parts without a barcode (قفل، دلاية...) sold by WEIGHT from the clasp stock (098). A new invoice line type SELL_RAFAYEA
-- carries karat, weight, the gold price, the making (the entered invoice total stays authoritative, exactly like F5 / F7) and the typed kind in item_name_snapshot. It has no
-- inventory piece and no old-gold movement; its stock effect is one SALE_OUT row of clasp_stock_movements (reference INVOICE_LINE) posted in the same transaction.
-- Only CHECK lists are widened (every step guarded, re-run safe); no row is touched. Returns and exchanges of such a line are refused by the services; a full invoice reversal gives the grams back.
SET @masgold_sql = IF(EXISTS(SELECT 1 FROM schema_migrations WHERE version=98),
 'DO 0', 'SELECT * FROM MIGRATION_099_REFUSED_missing_098');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(
    EXISTS (SELECT 1 FROM information_schema.TABLE_CONSTRAINTS tc
              JOIN information_schema.CHECK_CONSTRAINTS cc
                ON cc.CONSTRAINT_SCHEMA = tc.CONSTRAINT_SCHEMA AND cc.CONSTRAINT_NAME = tc.CONSTRAINT_NAME
             WHERE tc.CONSTRAINT_SCHEMA = DATABASE() AND tc.TABLE_NAME = 'invoice_lines'
               AND tc.CONSTRAINT_NAME = 'chk_invoice_line_type' AND cc.CHECK_CLAUSE NOT LIKE '%SELL_RAFAYEA%'),
    'ALTER TABLE invoice_lines DROP CHECK chk_invoice_line_type',
    'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(
    NOT EXISTS (SELECT 1 FROM information_schema.TABLE_CONSTRAINTS
                 WHERE CONSTRAINT_SCHEMA = DATABASE() AND TABLE_NAME = 'invoice_lines' AND CONSTRAINT_NAME = 'chk_invoice_line_type'),
    "ALTER TABLE invoice_lines ADD CONSTRAINT chk_invoice_line_type CHECK (line_type IN ('SELL_INVENTORY', 'BUY_OLD_GOLD', 'OTHER', 'SELL_OLD_GOLD', 'SELL_UNIDENTIFIED', 'SELL_RAFAYEA'))",
    'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(
    EXISTS (SELECT 1 FROM information_schema.TABLE_CONSTRAINTS tc
              JOIN information_schema.CHECK_CONSTRAINTS cc
                ON cc.CONSTRAINT_SCHEMA = tc.CONSTRAINT_SCHEMA AND cc.CONSTRAINT_NAME = tc.CONSTRAINT_NAME
             WHERE tc.CONSTRAINT_SCHEMA = DATABASE() AND tc.TABLE_NAME = 'invoice_lines'
               AND tc.CONSTRAINT_NAME = 'chk_invoice_line_identification' AND cc.CHECK_CLAUSE NOT LIKE '%CLASP_STOCK%'),
    'ALTER TABLE invoice_lines DROP CHECK chk_invoice_line_identification',
    'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(
    NOT EXISTS (SELECT 1 FROM information_schema.TABLE_CONSTRAINTS
                 WHERE CONSTRAINT_SCHEMA = DATABASE() AND TABLE_NAME = 'invoice_lines' AND CONSTRAINT_NAME = 'chk_invoice_line_identification'),
    "ALTER TABLE invoice_lines ADD CONSTRAINT chk_invoice_line_identification CHECK (identification_method IS NULL OR identification_method IN ('SCAN', 'SEARCH', 'MANUAL', 'OLD_GOLD_STOCK', 'CLASP_STOCK'))",
    'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(
    EXISTS (SELECT 1 FROM information_schema.TABLE_CONSTRAINTS tc
              JOIN information_schema.CHECK_CONSTRAINTS cc
                ON cc.CONSTRAINT_SCHEMA = tc.CONSTRAINT_SCHEMA AND cc.CONSTRAINT_NAME = tc.CONSTRAINT_NAME
             WHERE tc.CONSTRAINT_SCHEMA = DATABASE() AND tc.TABLE_NAME = 'invoice_lines'
               AND tc.CONSTRAINT_NAME = 'chk_invoice_line_native_values' AND cc.CHECK_CLAUSE NOT LIKE '%SELL_RAFAYEA%'),
    'ALTER TABLE invoice_lines DROP CHECK chk_invoice_line_native_values',
    'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(
    NOT EXISTS (SELECT 1 FROM information_schema.TABLE_CONSTRAINTS
                 WHERE CONSTRAINT_SCHEMA = DATABASE() AND TABLE_NAME = 'invoice_lines' AND CONSTRAINT_NAME = 'chk_invoice_line_native_values'),
    "ALTER TABLE invoice_lines ADD CONSTRAINT chk_invoice_line_native_values CHECK (record_origin <> 'NATIVE' OR (line_type IN ('SELL_INVENTORY', 'BUY_OLD_GOLD', 'SELL_OLD_GOLD', 'SELL_UNIDENTIFIED', 'SELL_RAFAYEA') AND quantity = 1 AND gold_price_per_gram IS NOT NULL AND line_total IS NOT NULL AND gross_total IS NOT NULL AND gross_total = ROUND(weight_grams * gold_price_per_gram, 2)))",
    'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(
    NOT EXISTS (SELECT 1 FROM information_schema.TABLE_CONSTRAINTS
                 WHERE CONSTRAINT_SCHEMA = DATABASE() AND TABLE_NAME = 'invoice_lines' AND CONSTRAINT_NAME = 'chk_invoice_line_native_rafayea'),
    "ALTER TABLE invoice_lines ADD CONSTRAINT chk_invoice_line_native_rafayea CHECK (record_origin <> 'NATIVE' OR line_type <> 'SELL_RAFAYEA' OR (old_gold_movement_id IS NULL AND item_name_snapshot IS NOT NULL AND making_total IS NOT NULL AND line_total = gross_total + making_total AND discount_type IS NULL AND discount_amount IS NULL AND identification_method = 'CLASP_STOCK'))",
    'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

INSERT INTO schema_migrations(version,name) VALUES (99,'Rafayea sale line: SELL_RAFAYEA invoice line type, CLASP_STOCK identification and its shape rule')
 ON DUPLICATE KEY UPDATE name=VALUES(name);
