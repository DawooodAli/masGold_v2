-- Supplier gold transfer shown in the market custody log (owner 2026-11-24). Prepared; NOT applied to any database by the task that wrote it.
--   A supplier-to-supplier gold transfer (migration 108) is recorded in the latest OPEN legacy market custody as a LOG marker so the movement is visible there: entry kinds SUP_XFER (the
--   transfer) and SUP_XFER_REV (its reversal). A marker has NO cash, gold or Scrap leg: the custody balances never change. The marker is tied to its transfer row by
--   supplier_gold_transfer_entries (one marker per transfer row, one transfer row per marker). The marker is immutable and is never reversed on its own: the transfer is reversed
--   from the supplier page, which records a second marker. No existing row is rewritten. Every step is guarded (re-run safe).
SET @masgold_sql = IF(EXISTS(SELECT 1 FROM schema_migrations WHERE version=108),
 'DO 0', 'SELECT * FROM MIGRATION_109_REFUSED_missing_108');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(EXISTS(SELECT 1 FROM information_schema.CHECK_CONSTRAINTS WHERE CONSTRAINT_SCHEMA=DATABASE() AND CONSTRAINT_NAME='chk_mte_kind' AND CHECK_CLAUSE NOT LIKE '%SUP_XFER%'),
 "ALTER TABLE market_trip_entries DROP CHECK chk_mte_kind", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;
SET @masgold_sql := IF(NOT EXISTS(SELECT 1 FROM information_schema.TABLE_CONSTRAINTS WHERE CONSTRAINT_SCHEMA=DATABASE() AND TABLE_NAME='market_trip_entries' AND CONSTRAINT_NAME='chk_mte_kind'),
 "ALTER TABLE market_trip_entries ADD CONSTRAINT chk_mte_kind CHECK (entry_kind IN ('ISSUE','RETURN','MELT','LOT_RETURN','LOT_ISSUE','HEAT','SUPPLIER_PAY','GOLD_SETTLE','LOT_SETTLE','LOT_HOLD','HOLD_RETURN','LOGO_HOLD','HOLD_BACK','GOLD_SALE','GOLD_BUY','BK_ISSUE','BK_BUY','BK_SELL','BK_RETURN','BK_RECON','BK_ISS_STK','BK_DLV_SUP','BK_DLV_MKT','BK_RECEIVE','BK_GAIN','ST_MELT','ST_BAR_SUP','ST_BAR_MKT','ST_LOGO','ST_EXPENSE','ST_CASH_BANK','ST_CASH_OFF','ST_RET_MKT','ST_LOSS','ST_ADJUST','ST_BAR_FEE','ST_CASH_PRIV','SUP_XFER','SUP_XFER_REV'))", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;
SET @masgold_sql := IF(EXISTS(SELECT 1 FROM information_schema.CHECK_CONSTRAINTS WHERE CONSTRAINT_SCHEMA=DATABASE() AND CONSTRAINT_NAME='chk_mte_mode_kind' AND CHECK_CLAUSE NOT LIKE '%SUP_XFER%'),
 "ALTER TABLE market_trip_entries DROP CHECK chk_mte_mode_kind", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;
SET @masgold_sql := IF(NOT EXISTS(SELECT 1 FROM information_schema.TABLE_CONSTRAINTS WHERE CONSTRAINT_SCHEMA=DATABASE() AND TABLE_NAME='market_trip_entries' AND CONSTRAINT_NAME='chk_mte_mode_kind'),
 "ALTER TABLE market_trip_entries ADD CONSTRAINT chk_mte_mode_kind CHECK ((trip_mode = 'LEGACY' AND entry_kind IN ('ISSUE','RETURN','MELT','LOT_RETURN','LOT_ISSUE','HEAT','SUPPLIER_PAY','GOLD_SETTLE','LOT_SETTLE','LOT_HOLD','HOLD_RETURN','LOGO_HOLD','HOLD_BACK','GOLD_SALE','GOLD_BUY','ST_MELT','ST_BAR_SUP','ST_BAR_MKT','ST_LOGO','ST_EXPENSE','ST_CASH_BANK','ST_CASH_OFF','ST_RET_MKT','ST_LOSS','ST_ADJUST','ST_BAR_FEE','ST_CASH_PRIV','SUP_XFER','SUP_XFER_REV')) OR (trip_mode = 'BOOK' AND entry_kind IN ('ISSUE','RETURN','SUPPLIER_PAY','BK_ISSUE','BK_BUY','BK_SELL','BK_RETURN','BK_RECON','BK_ISS_STK','BK_DLV_SUP','BK_DLV_MKT','BK_RECEIVE','BK_GAIN')))", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

CREATE TABLE IF NOT EXISTS supplier_gold_transfer_entries (
 transfer_id BIGINT UNSIGNED NOT NULL,
 entry_id BIGINT UNSIGNED NOT NULL,
 PRIMARY KEY (transfer_id),
 UNIQUE KEY uq_sgte_entry (entry_id),
 CONSTRAINT fk_sgte_transfer FOREIGN KEY (transfer_id) REFERENCES supplier_gold_transfers(id),
 CONSTRAINT fk_sgte_entry FOREIGN KEY (entry_id) REFERENCES market_trip_entries(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

INSERT INTO schema_migrations(version,name) VALUES (109,'Supplier gold transfer markers in the market custody log (SUP_XFER, SUP_XFER_REV)')
 ON DUPLICATE KEY UPDATE name=VALUES(name);
