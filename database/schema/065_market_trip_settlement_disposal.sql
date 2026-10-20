-- Market-custody SETTLEMENT, step 2f: the remainder after the trip (return to the shop, loss) and the issue-weight correction. Prepared; NOT applied to any database by the task that wrote it.
--   entry kind ST_RET_MKT  market return: grams of the RAW pool (unmelted gold, own karat), the LOGO pool (own karat) or the BAR pool (21K-equivalent grams) go back to the shop's counted Scrap
--                          (one IN movement per target karat; the bar returns as 21K). The pool loses the grams; no cash, supplier or lot movement.
--   entry kind ST_LOSS     loss: the same sources leave the custody with a classification CRUCIBLE (خسية بوتقة) or MARKET (خسية سوق); no Scrap, cash or supplier movement.
--   entry kind ST_ADJUST   issue-weight correction of one karat: INCREASE (the real issued weight was higher: Scrap OUT, custody RAW +g) or DECREASE (lower: Scrap IN, custody RAW -g), reason mandatory.
--   All three are LEGACY-only. Lines are stored in market_trip_settle_lines (ST_RET_MKT / ST_LOSS) and market_trip_settle_adjust (ST_ADJUST). Pool moves keep using market_trip_settle_moves.
-- No existing row is rewritten. The CHECK expressions have NOT been executed on a MySQL server before the rehearsal gate. Every step is guarded (re-run safe).
SET @masgold_sql = IF(EXISTS(SELECT 1 FROM schema_migrations WHERE version=64),
 'DO 0', 'SELECT * FROM MIGRATION_065_REFUSED_missing_064');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(EXISTS(SELECT 1 FROM information_schema.CHECK_CONSTRAINTS WHERE CONSTRAINT_SCHEMA=DATABASE() AND CONSTRAINT_NAME='chk_mte_kind' AND CHECK_CLAUSE NOT LIKE '%ST_RET_MKT%'),
 "ALTER TABLE market_trip_entries DROP CHECK chk_mte_kind", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;
SET @masgold_sql := IF(NOT EXISTS(SELECT 1 FROM information_schema.TABLE_CONSTRAINTS WHERE CONSTRAINT_SCHEMA=DATABASE() AND TABLE_NAME='market_trip_entries' AND CONSTRAINT_NAME='chk_mte_kind'),
 "ALTER TABLE market_trip_entries ADD CONSTRAINT chk_mte_kind CHECK (entry_kind IN ('ISSUE','RETURN','MELT','LOT_RETURN','LOT_ISSUE','HEAT','SUPPLIER_PAY','GOLD_SETTLE','LOT_SETTLE','LOT_HOLD','HOLD_RETURN','LOGO_HOLD','HOLD_BACK','GOLD_SALE','GOLD_BUY','BK_ISSUE','BK_BUY','BK_SELL','BK_RETURN','BK_RECON','BK_ISS_STK','BK_DLV_SUP','BK_DLV_MKT','BK_RECEIVE','BK_GAIN','ST_MELT','ST_BAR_SUP','ST_BAR_MKT','ST_LOGO','ST_EXPENSE','ST_CASH_BANK','ST_CASH_OFF','ST_RET_MKT','ST_LOSS','ST_ADJUST'))", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;
SET @masgold_sql := IF(EXISTS(SELECT 1 FROM information_schema.CHECK_CONSTRAINTS WHERE CONSTRAINT_SCHEMA=DATABASE() AND CONSTRAINT_NAME='chk_mte_mode_kind' AND CHECK_CLAUSE NOT LIKE '%ST_RET_MKT%'),
 "ALTER TABLE market_trip_entries DROP CHECK chk_mte_mode_kind", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;
SET @masgold_sql := IF(NOT EXISTS(SELECT 1 FROM information_schema.TABLE_CONSTRAINTS WHERE CONSTRAINT_SCHEMA=DATABASE() AND TABLE_NAME='market_trip_entries' AND CONSTRAINT_NAME='chk_mte_mode_kind'),
 "ALTER TABLE market_trip_entries ADD CONSTRAINT chk_mte_mode_kind CHECK ((trip_mode = 'LEGACY' AND entry_kind IN ('ISSUE','RETURN','MELT','LOT_RETURN','LOT_ISSUE','HEAT','SUPPLIER_PAY','GOLD_SETTLE','LOT_SETTLE','LOT_HOLD','HOLD_RETURN','LOGO_HOLD','HOLD_BACK','GOLD_SALE','GOLD_BUY','ST_MELT','ST_BAR_SUP','ST_BAR_MKT','ST_LOGO','ST_EXPENSE','ST_CASH_BANK','ST_CASH_OFF','ST_RET_MKT','ST_LOSS','ST_ADJUST')) OR (trip_mode = 'BOOK' AND entry_kind IN ('ISSUE','RETURN','SUPPLIER_PAY','BK_ISSUE','BK_BUY','BK_SELL','BK_RETURN','BK_RECON','BK_ISS_STK','BK_DLV_SUP','BK_DLV_MKT','BK_RECEIVE','BK_GAIN')))", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;
SET @masgold_sql := IF(EXISTS(SELECT 1 FROM information_schema.CHECK_CONSTRAINTS WHERE CONSTRAINT_SCHEMA=DATABASE() AND CONSTRAINT_NAME='chk_mtsm_kind' AND CHECK_CLAUSE NOT LIKE '%ST_RET_MKT%'),
 "ALTER TABLE market_trip_settle_moves DROP CHECK chk_mtsm_kind", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;
SET @masgold_sql := IF(NOT EXISTS(SELECT 1 FROM information_schema.TABLE_CONSTRAINTS WHERE CONSTRAINT_SCHEMA=DATABASE() AND TABLE_NAME='market_trip_settle_moves' AND CONSTRAINT_NAME='chk_mtsm_kind'),
 "ALTER TABLE market_trip_settle_moves ADD CONSTRAINT chk_mtsm_kind CHECK (entry_kind IN ('ST_MELT','ST_BAR_SUP','ST_BAR_MKT','ST_LOGO','GOLD_SALE','ST_RET_MKT','ST_LOSS','ST_ADJUST'))", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

CREATE TABLE IF NOT EXISTS market_trip_settle_lines (
 id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
 entry_id BIGINT UNSIGNED NOT NULL,
 entry_kind VARCHAR(12) NOT NULL,
 line_no SMALLINT UNSIGNED NOT NULL,
 source VARCHAR(4) NOT NULL,
 karat_id TINYINT UNSIGNED DEFAULT NULL,
 weight_grams DECIMAL(14,3) NOT NULL,
 classification VARCHAR(8) DEFAULT NULL,
 created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
 PRIMARY KEY (id),
 UNIQUE KEY uq_mtsl_line (entry_id, line_no),
 CONSTRAINT fk_mtsl_entry FOREIGN KEY (entry_id, entry_kind) REFERENCES market_trip_entries(id, entry_kind),
 CONSTRAINT fk_mtsl_karat FOREIGN KEY (karat_id) REFERENCES karats(id),
 CONSTRAINT chk_mtsl_weight CHECK (weight_grams > 0),
 CONSTRAINT chk_mtsl_source CHECK ((source IN ('RAW','LOGO') AND karat_id IS NOT NULL) OR (source = 'BAR' AND karat_id IS NULL)),
 CONSTRAINT chk_mtsl_kind CHECK ((entry_kind = 'ST_RET_MKT' AND classification IS NULL) OR (entry_kind = 'ST_LOSS' AND classification IN ('CRUCIBLE','MARKET')))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS market_trip_settle_adjust (
 entry_id BIGINT UNSIGNED NOT NULL,
 entry_kind VARCHAR(12) NOT NULL DEFAULT 'ST_ADJUST',
 karat_id TINYINT UNSIGNED NOT NULL,
 direction VARCHAR(8) NOT NULL,
 weight_grams DECIMAL(14,3) NOT NULL,
 reason VARCHAR(300) NOT NULL,
 created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
 PRIMARY KEY (entry_id),
 CONSTRAINT fk_mtsa_entry FOREIGN KEY (entry_id, entry_kind) REFERENCES market_trip_entries(id, entry_kind),
 CONSTRAINT fk_mtsa_karat FOREIGN KEY (karat_id) REFERENCES karats(id),
 CONSTRAINT chk_mtsa_kind CHECK (entry_kind = 'ST_ADJUST'),
 CONSTRAINT chk_mtsa_direction CHECK (direction IN ('INCREASE','DECREASE')),
 CONSTRAINT chk_mtsa_weight CHECK (weight_grams > 0),
 CONSTRAINT chk_mtsa_reason CHECK (CHAR_LENGTH(TRIM(reason)) > 0)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

INSERT INTO schema_migrations(version,name) VALUES (65,'Market-custody settlement step 2f: return to the shop, loss and issue-weight correction')
 ON DUPLICATE KEY UPDATE name=VALUES(name);
