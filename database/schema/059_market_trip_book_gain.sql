-- Market-trip BOOK Phase 3: the evidenced gain BK_GAIN («زيادة رحلة السوق — مكافئ عيار21»). Prepared; NOT applied to any database by the task that wrote it.
-- The application paths that use it stay inside the disabled BOOK mode (no production path creates a BOOK trip).
--   entry kind   BK_GAIN (BOOK only): ONE incoming book line computed from the surplus's OWN physical facts (weight + master karat or assay in permille), INDEPENDENT
--                posting, no Scrap leg, no price, no stock item. It is an INPUT of the trip's book; it never nets a loss and is never created automatically.
--   record       market_trip_gains: the owner's explanation, the evidence reference (weighing / assay slip) and the explicit confirmation (user + time), 1:1 with the
--                entry through the composite keys of the entry table. Insert-only; a reversal is the ordinary full reversal of the entry (CorrectionService).
--   reconciliation records may carry the v3 source / snapshot formats, used ONLY for trips that contain a BK_GAIN entry (v1 / v2 records stay valid unchanged).
-- No existing row is rewritten. The CHECK expressions have NOT been executed on a MySQL server: that is a later verification gate. Every step is guarded (re-run safe).
SET @masgold_sql = IF(EXISTS(SELECT 1 FROM schema_migrations WHERE version=58),
 'DO 0', 'SELECT * FROM MIGRATION_059_REFUSED_missing_058');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

-- 1. the entry kind (BOOK only)
SET @masgold_sql := IF(EXISTS(SELECT 1 FROM information_schema.CHECK_CONSTRAINTS WHERE CONSTRAINT_SCHEMA=DATABASE() AND CONSTRAINT_NAME='chk_mte_kind' AND CHECK_CLAUSE NOT LIKE '%BK_GAIN%'),
 "ALTER TABLE market_trip_entries DROP CHECK chk_mte_kind", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;
SET @masgold_sql := IF(NOT EXISTS(SELECT 1 FROM information_schema.TABLE_CONSTRAINTS WHERE CONSTRAINT_SCHEMA=DATABASE() AND TABLE_NAME='market_trip_entries' AND CONSTRAINT_NAME='chk_mte_kind'),
 "ALTER TABLE market_trip_entries ADD CONSTRAINT chk_mte_kind CHECK (entry_kind IN ('ISSUE','RETURN','MELT','LOT_RETURN','LOT_ISSUE','HEAT','SUPPLIER_PAY','GOLD_SETTLE','LOT_SETTLE','LOT_HOLD','HOLD_RETURN','LOGO_HOLD','HOLD_BACK','GOLD_SALE','GOLD_BUY','BK_ISSUE','BK_BUY','BK_SELL','BK_RETURN','BK_RECON','BK_ISS_STK','BK_DLV_SUP','BK_DLV_MKT','BK_RECEIVE','BK_GAIN'))", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;
SET @masgold_sql := IF(EXISTS(SELECT 1 FROM information_schema.CHECK_CONSTRAINTS WHERE CONSTRAINT_SCHEMA=DATABASE() AND CONSTRAINT_NAME='chk_mte_mode_kind' AND CHECK_CLAUSE NOT LIKE '%BK_GAIN%'),
 "ALTER TABLE market_trip_entries DROP CHECK chk_mte_mode_kind", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;
SET @masgold_sql := IF(NOT EXISTS(SELECT 1 FROM information_schema.TABLE_CONSTRAINTS WHERE CONSTRAINT_SCHEMA=DATABASE() AND TABLE_NAME='market_trip_entries' AND CONSTRAINT_NAME='chk_mte_mode_kind'),
 "ALTER TABLE market_trip_entries ADD CONSTRAINT chk_mte_mode_kind CHECK ((trip_mode = 'LEGACY' AND entry_kind IN ('ISSUE','RETURN','MELT','LOT_RETURN','LOT_ISSUE','HEAT','SUPPLIER_PAY','GOLD_SETTLE','LOT_SETTLE','LOT_HOLD','HOLD_RETURN','LOGO_HOLD','HOLD_BACK','GOLD_SALE','GOLD_BUY')) OR (trip_mode = 'BOOK' AND entry_kind IN ('ISSUE','RETURN','SUPPLIER_PAY','BK_ISSUE','BK_BUY','BK_SELL','BK_RETURN','BK_RECON','BK_ISS_STK','BK_DLV_SUP','BK_DLV_MKT','BK_RECEIVE','BK_GAIN')))", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

-- 2. book lines: BK_GAIN is an incoming line without a leg, a price or a stock item (chk_mtbl_posted / chk_mtbl_stock already force INDEPENDENT for it)
SET @masgold_sql := IF(EXISTS(SELECT 1 FROM information_schema.CHECK_CONSTRAINTS WHERE CONSTRAINT_SCHEMA=DATABASE() AND CONSTRAINT_NAME='chk_mtbl_kind' AND CHECK_CLAUSE NOT LIKE '%BK_GAIN%'),
 "ALTER TABLE market_trip_book_lines DROP CHECK chk_mtbl_kind", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;
SET @masgold_sql := IF(NOT EXISTS(SELECT 1 FROM information_schema.TABLE_CONSTRAINTS WHERE CONSTRAINT_SCHEMA=DATABASE() AND TABLE_NAME='market_trip_book_lines' AND CONSTRAINT_NAME='chk_mtbl_kind'),
 "ALTER TABLE market_trip_book_lines ADD CONSTRAINT chk_mtbl_kind CHECK ((entry_kind IN ('BK_ISSUE','BK_BUY','BK_ISS_STK','BK_RECEIVE','BK_GAIN') AND direction = 'IN') OR (entry_kind IN ('BK_SELL','BK_RETURN','BK_DLV_SUP','BK_DLV_MKT') AND direction = 'OUT'))", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;
SET @masgold_sql := IF(EXISTS(SELECT 1 FROM information_schema.CHECK_CONSTRAINTS WHERE CONSTRAINT_SCHEMA=DATABASE() AND CONSTRAINT_NAME='chk_mtbl_leg' AND CHECK_CLAUSE NOT LIKE '%BK_GAIN%'),
 "ALTER TABLE market_trip_book_lines DROP CHECK chk_mtbl_leg", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;
SET @masgold_sql := IF(NOT EXISTS(SELECT 1 FROM information_schema.TABLE_CONSTRAINTS WHERE CONSTRAINT_SCHEMA=DATABASE() AND TABLE_NAME='market_trip_book_lines' AND CONSTRAINT_NAME='chk_mtbl_leg'),
 "ALTER TABLE market_trip_book_lines ADD CONSTRAINT chk_mtbl_leg CHECK ((entry_kind = 'BK_ISSUE' AND basis = 'MASTER_KARAT' AND scrap_movement_id IS NOT NULL AND line_price IS NULL) OR (entry_kind = 'BK_RETURN' AND basis = 'MASTER_KARAT' AND scrap_movement_id IS NOT NULL AND line_price IS NULL) OR (entry_kind = 'BK_RETURN' AND basis = 'FINENESS' AND scrap_movement_id IS NULL AND line_price IS NULL) OR (entry_kind IN ('BK_BUY','BK_SELL') AND scrap_movement_id IS NULL AND line_price IS NOT NULL AND line_price > 0) OR (entry_kind IN ('BK_ISS_STK','BK_DLV_SUP','BK_DLV_MKT','BK_RECEIVE','BK_GAIN') AND scrap_movement_id IS NULL AND line_price IS NULL))", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

-- 3. the gain record
CREATE TABLE IF NOT EXISTS market_trip_gains (
 entry_id BIGINT UNSIGNED NOT NULL,
 entry_kind VARCHAR(12) NOT NULL DEFAULT 'BK_GAIN',
 trip_id BIGINT UNSIGNED NOT NULL,
 trip_mode VARCHAR(8) NOT NULL DEFAULT 'BOOK',
 explanation VARCHAR(500) NOT NULL,
 evidence_reference VARCHAR(120) NOT NULL,
 confirmed_by_user_id INT UNSIGNED NOT NULL,
 confirmed_at DATETIME NOT NULL,
 created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
 PRIMARY KEY (entry_id),
 KEY idx_mtg_trip (trip_id),
 CONSTRAINT fk_mtg_entry FOREIGN KEY (entry_id, entry_kind, trip_id) REFERENCES market_trip_entries(id, entry_kind, trip_id),
 CONSTRAINT fk_mtg_mode FOREIGN KEY (entry_id, trip_mode) REFERENCES market_trip_entries(id, trip_mode) ON UPDATE RESTRICT ON DELETE RESTRICT,
 CONSTRAINT fk_mtg_user FOREIGN KEY (confirmed_by_user_id) REFERENCES users(id),
 CONSTRAINT chk_mtg_head CHECK (entry_kind = 'BK_GAIN' AND trip_mode = 'BOOK'),
 CONSTRAINT chk_mtg_text CHECK (CHAR_LENGTH(TRIM(explanation)) > 0 AND CHAR_LENGTH(TRIM(evidence_reference)) > 0)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- 4. reconciliation records may use the v3 formats (only for trips that contain a BK_GAIN)
SET @masgold_sql := IF(EXISTS(SELECT 1 FROM information_schema.CHECK_CONSTRAINTS WHERE CONSTRAINT_SCHEMA=DATABASE() AND CONSTRAINT_NAME='chk_mtr_head' AND CHECK_CLAUSE NOT LIKE '%source-v3%'),
 "ALTER TABLE market_trip_reconciliations DROP CHECK chk_mtr_head", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;
SET @masgold_sql := IF(NOT EXISTS(SELECT 1 FROM information_schema.TABLE_CONSTRAINTS WHERE CONSTRAINT_SCHEMA=DATABASE() AND TABLE_NAME='market_trip_reconciliations' AND CONSTRAINT_NAME='chk_mtr_head'),
 "ALTER TABLE market_trip_reconciliations ADD CONSTRAINT chk_mtr_head CHECK (entry_kind = 'BK_RECON' AND trip_mode = 'BOOK' AND generation >= 1 AND algorithm = 'book-recon-v1' AND source_algorithm IN ('book-recon-source-v1','book-recon-source-v2','book-recon-source-v3') AND snapshot_format IN ('book-recon-snapshot-v1','book-recon-snapshot-v2','book-recon-snapshot-v3') AND unit_denominator = 26250000 AND JSON_TYPE(snapshot) = 'OBJECT')", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

INSERT INTO schema_migrations(version,name) VALUES (59,'Market-trip BOOK Phase 3: evidenced gain document and record, v3 reconciliation formats')
 ON DUPLICATE KEY UPDATE name=VALUES(name);
