-- Market-custody SETTLEMENT, step 2a: the melt recorded AFTER the owner returns from the market («تسجيل سبك»). Prepared; NOT applied to any database by the task that wrote it.
--   entry kind   ST_MELT (LEGACY trips only): grams of several karats that left WITHOUT a destination are melted into ONE bar. The bar is NOT a lot with an identity: it only
--                adds its 21K-equivalent to the trip's BAR pool (distributed in parts by the later steps). The bar's weight and integer fineness are recorded for the reports.
--   document     market_trip_settle_melts: workshop, melted grams, bar weight, fineness (an INTEGER per mille), theoretical / actual Eq21 (0.001 g) and the loss split in
--                weight loss + fineness loss (signed: negative = gain); loss = theoretical - actual and weight loss + fineness loss = loss are CHECKs.
--   inputs       market_trip_settle_melt_inputs: the melted grams per karat (they leave the karat's UNCLASSIFIED custody).
--   moves        market_trip_settle_moves: the insert-only signed ledger of the settlement pools: RAW (grams of one karat, the gold that left without a destination) and BAR
--                (21K-equivalent grams). A move belongs to its entry and stops counting when the entry is reversed (the custody convention). Later steps extend the kinds.
-- No existing row is rewritten. The CHECK expressions have NOT been executed on a MySQL server before the rehearsal gate. Every step is guarded (re-run safe).
SET @masgold_sql = IF(EXISTS(SELECT 1 FROM schema_migrations WHERE version=59),
 'DO 0', 'SELECT * FROM MIGRATION_060_REFUSED_missing_059');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

-- 1. the entry kind (LEGACY only)
SET @masgold_sql := IF(EXISTS(SELECT 1 FROM information_schema.CHECK_CONSTRAINTS WHERE CONSTRAINT_SCHEMA=DATABASE() AND CONSTRAINT_NAME='chk_mte_kind' AND CHECK_CLAUSE NOT LIKE '%ST_MELT%'),
 "ALTER TABLE market_trip_entries DROP CHECK chk_mte_kind", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;
SET @masgold_sql := IF(NOT EXISTS(SELECT 1 FROM information_schema.TABLE_CONSTRAINTS WHERE CONSTRAINT_SCHEMA=DATABASE() AND TABLE_NAME='market_trip_entries' AND CONSTRAINT_NAME='chk_mte_kind'),
 "ALTER TABLE market_trip_entries ADD CONSTRAINT chk_mte_kind CHECK (entry_kind IN ('ISSUE','RETURN','MELT','LOT_RETURN','LOT_ISSUE','HEAT','SUPPLIER_PAY','GOLD_SETTLE','LOT_SETTLE','LOT_HOLD','HOLD_RETURN','LOGO_HOLD','HOLD_BACK','GOLD_SALE','GOLD_BUY','BK_ISSUE','BK_BUY','BK_SELL','BK_RETURN','BK_RECON','BK_ISS_STK','BK_DLV_SUP','BK_DLV_MKT','BK_RECEIVE','BK_GAIN','ST_MELT'))", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;
SET @masgold_sql := IF(EXISTS(SELECT 1 FROM information_schema.CHECK_CONSTRAINTS WHERE CONSTRAINT_SCHEMA=DATABASE() AND CONSTRAINT_NAME='chk_mte_mode_kind' AND CHECK_CLAUSE NOT LIKE '%ST_MELT%'),
 "ALTER TABLE market_trip_entries DROP CHECK chk_mte_mode_kind", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;
SET @masgold_sql := IF(NOT EXISTS(SELECT 1 FROM information_schema.TABLE_CONSTRAINTS WHERE CONSTRAINT_SCHEMA=DATABASE() AND TABLE_NAME='market_trip_entries' AND CONSTRAINT_NAME='chk_mte_mode_kind'),
 "ALTER TABLE market_trip_entries ADD CONSTRAINT chk_mte_mode_kind CHECK ((trip_mode = 'LEGACY' AND entry_kind IN ('ISSUE','RETURN','MELT','LOT_RETURN','LOT_ISSUE','HEAT','SUPPLIER_PAY','GOLD_SETTLE','LOT_SETTLE','LOT_HOLD','HOLD_RETURN','LOGO_HOLD','HOLD_BACK','GOLD_SALE','GOLD_BUY','ST_MELT')) OR (trip_mode = 'BOOK' AND entry_kind IN ('ISSUE','RETURN','SUPPLIER_PAY','BK_ISSUE','BK_BUY','BK_SELL','BK_RETURN','BK_RECON','BK_ISS_STK','BK_DLV_SUP','BK_DLV_MKT','BK_RECEIVE','BK_GAIN')))", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

-- 2. the melt document (1:1 with its entry)
CREATE TABLE IF NOT EXISTS market_trip_settle_melts (
 entry_id BIGINT UNSIGNED NOT NULL,
 entry_kind VARCHAR(12) NOT NULL DEFAULT 'ST_MELT',
 workshop VARCHAR(120) NOT NULL,
 melt_date DATE NOT NULL,
 input_weight_grams DECIMAL(14,3) NOT NULL,
 output_weight_grams DECIMAL(14,3) NOT NULL,
 fineness_permille SMALLINT UNSIGNED NOT NULL,
 theoretical_eq21 DECIMAL(14,3) NOT NULL,
 actual_eq21 DECIMAL(14,3) NOT NULL,
 loss_eq21 DECIMAL(14,3) NOT NULL,
 weight_loss_eq21 DECIMAL(14,3) NOT NULL,
 fineness_loss_eq21 DECIMAL(14,3) NOT NULL,
 created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
 PRIMARY KEY (entry_id),
 KEY idx_mtstm_date (melt_date),
 CONSTRAINT fk_mtstm_entry FOREIGN KEY (entry_id, entry_kind) REFERENCES market_trip_entries(id, entry_kind),
 CONSTRAINT chk_mtstm_kind CHECK (entry_kind = 'ST_MELT'),
 CONSTRAINT chk_mtstm_workshop CHECK (CHAR_LENGTH(TRIM(workshop)) > 0),
 CONSTRAINT chk_mtstm_weights CHECK (input_weight_grams > 0 AND output_weight_grams > 0),
 CONSTRAINT chk_mtstm_fineness CHECK (fineness_permille BETWEEN 1 AND 1000),
 CONSTRAINT chk_mtstm_eq21 CHECK (theoretical_eq21 >= 0 AND actual_eq21 >= 0),
 CONSTRAINT chk_mtstm_loss CHECK (loss_eq21 = theoretical_eq21 - actual_eq21 AND weight_loss_eq21 + fineness_loss_eq21 = loss_eq21)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- 3. the melted grams per karat
CREATE TABLE IF NOT EXISTS market_trip_settle_melt_inputs (
 entry_id BIGINT UNSIGNED NOT NULL,
 karat_id TINYINT UNSIGNED NOT NULL,
 weight_grams DECIMAL(14,3) NOT NULL,
 PRIMARY KEY (entry_id, karat_id),
 KEY idx_mtstmi_karat (karat_id),
 CONSTRAINT fk_mtstmi_melt FOREIGN KEY (entry_id) REFERENCES market_trip_settle_melts(entry_id),
 CONSTRAINT fk_mtstmi_karat FOREIGN KEY (karat_id) REFERENCES karats(id),
 CONSTRAINT chk_mtstmi_weight CHECK (weight_grams > 0)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- 4. the signed pool ledger (RAW grams of one karat / BAR Eq21 grams); a reversed entry's moves stop counting
CREATE TABLE IF NOT EXISTS market_trip_settle_moves (
 id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
 entry_id BIGINT UNSIGNED NOT NULL,
 entry_kind VARCHAR(12) NOT NULL,
 trip_id BIGINT UNSIGNED NOT NULL,
 pool VARCHAR(4) NOT NULL,
 karat_id TINYINT UNSIGNED DEFAULT NULL,
 delta DECIMAL(14,3) NOT NULL,
 created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
 PRIMARY KEY (id),
 KEY idx_mtsm_trip (trip_id, pool, karat_id),
 KEY idx_mtsm_entry (entry_id),
 CONSTRAINT fk_mtsm_entry FOREIGN KEY (entry_id, entry_kind, trip_id) REFERENCES market_trip_entries(id, entry_kind, trip_id),
 CONSTRAINT fk_mtsm_karat FOREIGN KEY (karat_id) REFERENCES karats(id),
 CONSTRAINT chk_mtsm_kind CHECK (entry_kind IN ('ST_MELT')),
 CONSTRAINT chk_mtsm_pool CHECK ((pool = 'RAW' AND karat_id IS NOT NULL) OR (pool = 'BAR' AND karat_id IS NULL)),
 CONSTRAINT chk_mtsm_delta CHECK (delta <> 0)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

INSERT INTO schema_migrations(version,name) VALUES (60,'Market-custody settlement step 2a: ST_MELT entries, melt document and inputs, settlement pool moves')
 ON DUPLICATE KEY UPDATE name=VALUES(name);
