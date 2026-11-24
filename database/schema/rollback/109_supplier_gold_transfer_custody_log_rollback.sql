-- Rollback of 109 (supplier gold transfer markers in the market custody log). Refuses when a later migration exists or any marker entry exists (history is never deleted silently).
SET @masgold_sql = IF(EXISTS(SELECT 1 FROM schema_migrations WHERE version > 109),
 'SELECT * FROM ROLLBACK_109_REFUSED_later_migration_exists', 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;
SET @masgold_sql = IF((SELECT COUNT(*) FROM market_trip_entries WHERE entry_kind IN ('SUP_XFER','SUP_XFER_REV')) > 0,
 'SELECT * FROM ROLLBACK_109_REFUSED_transfer_markers_exist', 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;
DROP TABLE IF EXISTS supplier_gold_transfer_entries;
SET @masgold_sql := IF(EXISTS(SELECT 1 FROM information_schema.CHECK_CONSTRAINTS WHERE CONSTRAINT_SCHEMA=DATABASE() AND CONSTRAINT_NAME='chk_mte_kind' AND CHECK_CLAUSE LIKE '%SUP_XFER%'),
 "ALTER TABLE market_trip_entries DROP CHECK chk_mte_kind", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;
SET @masgold_sql := IF(NOT EXISTS(SELECT 1 FROM information_schema.TABLE_CONSTRAINTS WHERE CONSTRAINT_SCHEMA=DATABASE() AND TABLE_NAME='market_trip_entries' AND CONSTRAINT_NAME='chk_mte_kind'),
 "ALTER TABLE market_trip_entries ADD CONSTRAINT chk_mte_kind CHECK (entry_kind IN ('ISSUE','RETURN','MELT','LOT_RETURN','LOT_ISSUE','HEAT','SUPPLIER_PAY','GOLD_SETTLE','LOT_SETTLE','LOT_HOLD','HOLD_RETURN','LOGO_HOLD','HOLD_BACK','GOLD_SALE','GOLD_BUY','BK_ISSUE','BK_BUY','BK_SELL','BK_RETURN','BK_RECON','BK_ISS_STK','BK_DLV_SUP','BK_DLV_MKT','BK_RECEIVE','BK_GAIN','ST_MELT','ST_BAR_SUP','ST_BAR_MKT','ST_LOGO','ST_EXPENSE','ST_CASH_BANK','ST_CASH_OFF','ST_RET_MKT','ST_LOSS','ST_ADJUST','ST_BAR_FEE','ST_CASH_PRIV'))", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;
SET @masgold_sql := IF(EXISTS(SELECT 1 FROM information_schema.CHECK_CONSTRAINTS WHERE CONSTRAINT_SCHEMA=DATABASE() AND CONSTRAINT_NAME='chk_mte_mode_kind' AND CHECK_CLAUSE LIKE '%SUP_XFER%'),
 "ALTER TABLE market_trip_entries DROP CHECK chk_mte_mode_kind", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;
SET @masgold_sql := IF(NOT EXISTS(SELECT 1 FROM information_schema.TABLE_CONSTRAINTS WHERE CONSTRAINT_SCHEMA=DATABASE() AND TABLE_NAME='market_trip_entries' AND CONSTRAINT_NAME='chk_mte_mode_kind'),
 "ALTER TABLE market_trip_entries ADD CONSTRAINT chk_mte_mode_kind CHECK ((trip_mode = 'LEGACY' AND entry_kind IN ('ISSUE','RETURN','MELT','LOT_RETURN','LOT_ISSUE','HEAT','SUPPLIER_PAY','GOLD_SETTLE','LOT_SETTLE','LOT_HOLD','HOLD_RETURN','LOGO_HOLD','HOLD_BACK','GOLD_SALE','GOLD_BUY','ST_MELT','ST_BAR_SUP','ST_BAR_MKT','ST_LOGO','ST_EXPENSE','ST_CASH_BANK','ST_CASH_OFF','ST_RET_MKT','ST_LOSS','ST_ADJUST','ST_BAR_FEE','ST_CASH_PRIV')) OR (trip_mode = 'BOOK' AND entry_kind IN ('ISSUE','RETURN','SUPPLIER_PAY','BK_ISSUE','BK_BUY','BK_SELL','BK_RETURN','BK_RECON','BK_ISS_STK','BK_DLV_SUP','BK_DLV_MKT','BK_RECEIVE','BK_GAIN')))", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;
DELETE FROM schema_migrations WHERE version = 109;
