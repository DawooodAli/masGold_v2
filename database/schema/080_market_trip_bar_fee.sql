-- Market-custody BAR FEE (أجر السبيكة). Prepared; NOT applied to any database by the task that wrote it.
--   entry kind ST_BAR_FEE: a supplier / marketer pays the owner a cash fee for a bar handed to them. The fee ENTERS the custody cash (no Main Safe / bank movement).
--   For a SUPPLIER party it also posts one NEGATIVE supplier_making_movements row (type BAR_FEE): the fee is deducted from the supplier's making payable.
--   For an intermediary-only party it is custody cash only. LEGACY trips only. The reversal posts BAR_FEE_REVERSAL (positive).
SET @masgold_sql = IF(EXISTS(SELECT 1 FROM schema_migrations WHERE version=79),
 'DO 0', 'SELECT * FROM MIGRATION_080_REFUSED_missing_079');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(EXISTS(SELECT 1 FROM information_schema.CHECK_CONSTRAINTS WHERE CONSTRAINT_SCHEMA=DATABASE() AND CONSTRAINT_NAME='chk_mte_kind' AND CHECK_CLAUSE NOT LIKE '%ST_BAR_FEE%'),
 "ALTER TABLE market_trip_entries DROP CHECK chk_mte_kind", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;
SET @masgold_sql := IF(NOT EXISTS(SELECT 1 FROM information_schema.TABLE_CONSTRAINTS WHERE CONSTRAINT_SCHEMA=DATABASE() AND TABLE_NAME='market_trip_entries' AND CONSTRAINT_NAME='chk_mte_kind'),
 "ALTER TABLE market_trip_entries ADD CONSTRAINT chk_mte_kind CHECK (entry_kind IN ('ISSUE','RETURN','MELT','LOT_RETURN','LOT_ISSUE','HEAT','SUPPLIER_PAY','GOLD_SETTLE','LOT_SETTLE','LOT_HOLD','HOLD_RETURN','LOGO_HOLD','HOLD_BACK','GOLD_SALE','GOLD_BUY','BK_ISSUE','BK_BUY','BK_SELL','BK_RETURN','BK_RECON','BK_ISS_STK','BK_DLV_SUP','BK_DLV_MKT','BK_RECEIVE','BK_GAIN','ST_MELT','ST_BAR_SUP','ST_BAR_MKT','ST_LOGO','ST_EXPENSE','ST_CASH_BANK','ST_CASH_OFF','ST_RET_MKT','ST_LOSS','ST_ADJUST','ST_BAR_FEE'))", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;
SET @masgold_sql := IF(EXISTS(SELECT 1 FROM information_schema.CHECK_CONSTRAINTS WHERE CONSTRAINT_SCHEMA=DATABASE() AND CONSTRAINT_NAME='chk_mte_mode_kind' AND CHECK_CLAUSE NOT LIKE '%ST_BAR_FEE%'),
 "ALTER TABLE market_trip_entries DROP CHECK chk_mte_mode_kind", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;
SET @masgold_sql := IF(NOT EXISTS(SELECT 1 FROM information_schema.TABLE_CONSTRAINTS WHERE CONSTRAINT_SCHEMA=DATABASE() AND TABLE_NAME='market_trip_entries' AND CONSTRAINT_NAME='chk_mte_mode_kind'),
 "ALTER TABLE market_trip_entries ADD CONSTRAINT chk_mte_mode_kind CHECK ((trip_mode = 'LEGACY' AND entry_kind IN ('ISSUE','RETURN','MELT','LOT_RETURN','LOT_ISSUE','HEAT','SUPPLIER_PAY','GOLD_SETTLE','LOT_SETTLE','LOT_HOLD','HOLD_RETURN','LOGO_HOLD','HOLD_BACK','GOLD_SALE','GOLD_BUY','ST_MELT','ST_BAR_SUP','ST_BAR_MKT','ST_LOGO','ST_EXPENSE','ST_CASH_BANK','ST_CASH_OFF','ST_RET_MKT','ST_LOSS','ST_ADJUST','ST_BAR_FEE')) OR (trip_mode = 'BOOK' AND entry_kind IN ('ISSUE','RETURN','SUPPLIER_PAY','BK_ISSUE','BK_BUY','BK_SELL','BK_RETURN','BK_RECON','BK_ISS_STK','BK_DLV_SUP','BK_DLV_MKT','BK_RECEIVE','BK_GAIN')))", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

CREATE TABLE IF NOT EXISTS market_trip_bar_fees (
 entry_id BIGINT UNSIGNED NOT NULL,
 entry_kind VARCHAR(12) NOT NULL DEFAULT 'ST_BAR_FEE',
 party_id BIGINT UNSIGNED NOT NULL,
 party_kind VARCHAR(12) NOT NULL,
 amount DECIMAL(16,2) NOT NULL,
 making_movement_id BIGINT UNSIGNED DEFAULT NULL,
 reference VARCHAR(120) DEFAULT NULL,
 created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
 PRIMARY KEY (entry_id),
 CONSTRAINT fk_mtbf_entry FOREIGN KEY (entry_id, entry_kind) REFERENCES market_trip_entries(id, entry_kind),
 CONSTRAINT fk_mtbf_party FOREIGN KEY (party_id) REFERENCES suppliers(id),
 CONSTRAINT fk_mtbf_making FOREIGN KEY (making_movement_id) REFERENCES supplier_making_movements(id),
 CONSTRAINT chk_mtbf_kind CHECK (entry_kind = 'ST_BAR_FEE'),
 CONSTRAINT chk_mtbf_amount CHECK (amount > 0),
 CONSTRAINT chk_mtbf_party CHECK ((party_kind = 'SUPPLIER' AND making_movement_id IS NOT NULL)
  OR (party_kind = 'INTERMEDIARY' AND making_movement_id IS NULL))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

INSERT INTO supplier_making_movement_types(code, name, description) VALUES
 ('BAR_FEE', 'أجر سبيكة', 'Bar fee received in market custody and deducted from the supplier making payable'),
 ('BAR_FEE_REVERSAL', 'عكس أجر سبيكة', 'Reversal of a bar fee deduction')
 ON DUPLICATE KEY UPDATE name=VALUES(name), description=VALUES(description);

INSERT INTO schema_migrations(version,name) VALUES (80,'Market-custody bar fee')
 ON DUPLICATE KEY UPDATE name=VALUES(name);
