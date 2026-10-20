-- Market-custody SETTLEMENT, step 2c: the melted BAR handed to a MARKETER (مسوقجي) and the unmelted remainder classified as LOGO, after the owner returns from the market. Prepared; NOT applied to any database by the task that wrote it.
--   entry kind ST_LOGO (LEGACY only): the gold that left WITHOUT a destination and was not melted is LOGO (the owner's rule): grams of the RAW pool become LOGO custody of the SAME karat
--   (moves RAW -g and LOGO +g in market_trip_settle_moves, pool value LOGO), after which every existing LOGO flow (heating, supplier settlement, marketer hand-over, sale, return) applies unchanged.
--   entry kind ST_BAR_MKT (LEGACY trips only): N grams (21K-equivalent) of the BAR pool become the marketer's 21K entitlement (055 account): ONE marketer gold movement
--   TRIP_BOOK_IN tied to the entry (composite key trip_entry_id + trip_entry_kind), a negative BAR move in market_trip_settle_moves, no Scrap / cash / supplier / lot movement.
--   The gold stays SHOP-OWNED at the marketer (a trust entitlement, not a sale); what he does next (settle a supplier from it, return it, sell it) is the existing 055 flow.
--   the entry's reversal posts the explicit compensation TRIP_BOOK_IN_REVERSAL (the original stays counted) and is refused when the marketer no longer holds the delivered amount.
-- No existing row is rewritten. The CHECK expressions have NOT been executed on a MySQL server before the rehearsal gate. Every step is guarded (re-run safe).
SET @masgold_sql = IF(EXISTS(SELECT 1 FROM schema_migrations WHERE version=61),
 'DO 0', 'SELECT * FROM MIGRATION_062_REFUSED_missing_061');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

-- 1. the entry kind (LEGACY only) and the pool-move kind
SET @masgold_sql := IF(EXISTS(SELECT 1 FROM information_schema.CHECK_CONSTRAINTS WHERE CONSTRAINT_SCHEMA=DATABASE() AND CONSTRAINT_NAME='chk_mte_kind' AND CHECK_CLAUSE NOT LIKE '%ST_BAR_MKT%'),
 "ALTER TABLE market_trip_entries DROP CHECK chk_mte_kind", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;
SET @masgold_sql := IF(NOT EXISTS(SELECT 1 FROM information_schema.TABLE_CONSTRAINTS WHERE CONSTRAINT_SCHEMA=DATABASE() AND TABLE_NAME='market_trip_entries' AND CONSTRAINT_NAME='chk_mte_kind'),
 "ALTER TABLE market_trip_entries ADD CONSTRAINT chk_mte_kind CHECK (entry_kind IN ('ISSUE','RETURN','MELT','LOT_RETURN','LOT_ISSUE','HEAT','SUPPLIER_PAY','GOLD_SETTLE','LOT_SETTLE','LOT_HOLD','HOLD_RETURN','LOGO_HOLD','HOLD_BACK','GOLD_SALE','GOLD_BUY','BK_ISSUE','BK_BUY','BK_SELL','BK_RETURN','BK_RECON','BK_ISS_STK','BK_DLV_SUP','BK_DLV_MKT','BK_RECEIVE','BK_GAIN','ST_MELT','ST_BAR_SUP','ST_BAR_MKT','ST_LOGO'))", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;
SET @masgold_sql := IF(EXISTS(SELECT 1 FROM information_schema.CHECK_CONSTRAINTS WHERE CONSTRAINT_SCHEMA=DATABASE() AND CONSTRAINT_NAME='chk_mte_mode_kind' AND CHECK_CLAUSE NOT LIKE '%ST_BAR_MKT%'),
 "ALTER TABLE market_trip_entries DROP CHECK chk_mte_mode_kind", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;
SET @masgold_sql := IF(NOT EXISTS(SELECT 1 FROM information_schema.TABLE_CONSTRAINTS WHERE CONSTRAINT_SCHEMA=DATABASE() AND TABLE_NAME='market_trip_entries' AND CONSTRAINT_NAME='chk_mte_mode_kind'),
 "ALTER TABLE market_trip_entries ADD CONSTRAINT chk_mte_mode_kind CHECK ((trip_mode = 'LEGACY' AND entry_kind IN ('ISSUE','RETURN','MELT','LOT_RETURN','LOT_ISSUE','HEAT','SUPPLIER_PAY','GOLD_SETTLE','LOT_SETTLE','LOT_HOLD','HOLD_RETURN','LOGO_HOLD','HOLD_BACK','GOLD_SALE','GOLD_BUY','ST_MELT','ST_BAR_SUP','ST_BAR_MKT','ST_LOGO')) OR (trip_mode = 'BOOK' AND entry_kind IN ('ISSUE','RETURN','SUPPLIER_PAY','BK_ISSUE','BK_BUY','BK_SELL','BK_RETURN','BK_RECON','BK_ISS_STK','BK_DLV_SUP','BK_DLV_MKT','BK_RECEIVE','BK_GAIN')))", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;
SET @masgold_sql := IF(EXISTS(SELECT 1 FROM information_schema.CHECK_CONSTRAINTS WHERE CONSTRAINT_SCHEMA=DATABASE() AND CONSTRAINT_NAME='chk_mtsm_kind' AND CHECK_CLAUSE NOT LIKE '%ST_BAR_MKT%'),
 "ALTER TABLE market_trip_settle_moves DROP CHECK chk_mtsm_kind", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;
SET @masgold_sql := IF(NOT EXISTS(SELECT 1 FROM information_schema.TABLE_CONSTRAINTS WHERE CONSTRAINT_SCHEMA=DATABASE() AND TABLE_NAME='market_trip_settle_moves' AND CONSTRAINT_NAME='chk_mtsm_kind'),
 "ALTER TABLE market_trip_settle_moves ADD CONSTRAINT chk_mtsm_kind CHECK (entry_kind IN ('ST_MELT','ST_BAR_SUP','ST_BAR_MKT','ST_LOGO'))", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;
SET @masgold_sql := IF(EXISTS(SELECT 1 FROM information_schema.CHECK_CONSTRAINTS WHERE CONSTRAINT_SCHEMA=DATABASE() AND CONSTRAINT_NAME='chk_mtsm_pool' AND CHECK_CLAUSE NOT LIKE '%LOGO%'),
 "ALTER TABLE market_trip_settle_moves DROP CHECK chk_mtsm_pool", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;
SET @masgold_sql := IF(NOT EXISTS(SELECT 1 FROM information_schema.TABLE_CONSTRAINTS WHERE CONSTRAINT_SCHEMA=DATABASE() AND TABLE_NAME='market_trip_settle_moves' AND CONSTRAINT_NAME='chk_mtsm_pool'),
 "ALTER TABLE market_trip_settle_moves ADD CONSTRAINT chk_mtsm_pool CHECK ((pool = 'RAW' AND karat_id IS NOT NULL) OR (pool = 'LOGO' AND karat_id IS NOT NULL) OR (pool = 'BAR' AND karat_id IS NULL))", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

-- 2. the marketer's gold ledger accepts the trip-entry leg of a ST_BAR_MKT entry (the 058 leg shape, one more entry kind)
SET @masgold_sql := IF(EXISTS(SELECT 1 FROM information_schema.CHECK_CONSTRAINTS WHERE CONSTRAINT_SCHEMA=DATABASE() AND CONSTRAINT_NAME='chk_mkgm_shape' AND CHECK_CLAUSE NOT LIKE '%ST_BAR_MKT%'),
 "ALTER TABLE marketer_gold_movements DROP CHECK chk_mkgm_shape", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;
SET @masgold_sql := IF(NOT EXISTS(SELECT 1 FROM information_schema.TABLE_CONSTRAINTS WHERE CONSTRAINT_SCHEMA=DATABASE() AND TABLE_NAME='marketer_gold_movements' AND CONSTRAINT_NAME='chk_mkgm_shape'),
 "ALTER TABLE marketer_gold_movements ADD CONSTRAINT chk_mkgm_shape CHECK (weight_grams > 0 AND ( (document_id IS NOT NULL AND settlement_id IS NULL AND trip_entry_id IS NULL AND ( (movement_type = 'PURCHASE_IN' AND doc_kind = 'PURCHASE' AND weight_delta = weight_grams AND correction_id IS NULL) OR (movement_type = 'SALE_OUT' AND doc_kind = 'SALE' AND weight_delta = -weight_grams AND correction_id IS NULL) OR (movement_type = 'GOLD_RETURN' AND doc_kind = 'GOLD_RETURN' AND weight_delta = -weight_grams AND correction_id IS NULL) OR (movement_type = 'PURCHASE_IN_REVERSAL' AND doc_kind = 'PURCHASE' AND weight_delta = -weight_grams AND correction_id IS NOT NULL) OR (movement_type = 'SALE_OUT_REVERSAL' AND doc_kind = 'SALE' AND weight_delta = weight_grams AND correction_id IS NOT NULL) OR (movement_type = 'GOLD_RETURN_REVERSAL' AND doc_kind = 'GOLD_RETURN' AND weight_delta = weight_grams AND correction_id IS NOT NULL))) OR (document_id IS NULL AND doc_kind IS NULL AND settlement_id IS NOT NULL AND trip_entry_id IS NULL AND ( (movement_type = 'SETTLEMENT' AND weight_delta = -weight_grams AND correction_id IS NULL) OR (movement_type = 'SETTLEMENT_REVERSAL' AND weight_delta = weight_grams AND correction_id IS NOT NULL))) OR (document_id IS NULL AND doc_kind IS NULL AND settlement_id IS NULL AND trip_entry_id IS NOT NULL AND ((movement_type = 'TRIP_BOOK_IN' AND trip_entry_kind IN ('BK_DLV_MKT','ST_BAR_MKT') AND weight_delta = weight_grams AND correction_id IS NULL) OR (movement_type = 'TRIP_BOOK_IN_REVERSAL' AND trip_entry_kind IN ('BK_DLV_MKT','ST_BAR_MKT') AND weight_delta = -weight_grams AND correction_id IS NOT NULL) OR (movement_type = 'TRIP_BOOK_OUT' AND trip_entry_kind = 'BK_RECEIVE' AND weight_delta = -weight_grams AND correction_id IS NULL) OR (movement_type = 'TRIP_BOOK_OUT_REVERSAL' AND trip_entry_kind = 'BK_RECEIVE' AND weight_delta = weight_grams AND correction_id IS NOT NULL)))))", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

INSERT INTO schema_migrations(version,name) VALUES (62,'Market-custody settlement step 2c: the melted bar handed to a marketer (ST_BAR_MKT, marketer trip-entry leg)')
 ON DUPLICATE KEY UPDATE name=VALUES(name);
