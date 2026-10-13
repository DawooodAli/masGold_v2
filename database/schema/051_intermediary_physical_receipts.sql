-- Physical receipts from an intermediary's holdings (LOT pools with permanent allocation history only). A receipt consumes MEASURED grams of ONE pool's holding
-- entitlement and creates ONE new shop-held receipt piece (identity RP, provenance only to the pool/lot/melt, never the original bar). Measured grams equal the
-- consumed holding grams (one weight column; a mismatch is refused by the service, so no discrepancy record exists), unreceived grams stay in holdings, and the
-- first slice records UNKNOWN assay only: no fineness, no pure-gold figure, no purity equivalence. The pool ledger gets explicit RECEIPT / RECEIPT_REVERSAL movements
-- (originals stay counted); the shop receipt stock is its own insert-only ledger (RECEIVE / RECEIVE_REVERSAL). Reversal is one canonical correction (source
-- HOLDING_RECEIPT). No new permission. Every shape CHECK uses explicit AND/OR branches with IS NULL / IS NOT NULL (no chained equality, no NULL-passing IN).
SET @masgold_sql = IF(EXISTS(SELECT 1 FROM schema_migrations WHERE version=50),
 'DO 0', 'SELECT * FROM MIGRATION_051_REFUSED_missing_050');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(NOT EXISTS(SELECT 1 FROM information_schema.STATISTICS WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='intermediary_holding_pools' AND INDEX_NAME='uq_ihp_receipt'), "ALTER TABLE intermediary_holding_pools ADD UNIQUE KEY uq_ihp_receipt (id,party_id,lot_id,origin_melt_entry_id)", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

-- HR document: one receipt of ONE pool.
CREATE TABLE IF NOT EXISTS holding_receipts (
 id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
 pool_id BIGINT UNSIGNED NOT NULL,
 party_id INT UNSIGNED NOT NULL,
 lot_id BIGINT UNSIGNED NOT NULL,
 origin_melt_entry_id BIGINT UNSIGNED NOT NULL,
 grams DECIMAL(12,3) NOT NULL,
 receipt_date DATE NOT NULL,
 reference VARCHAR(100) DEFAULT NULL,
 notes VARCHAR(1000) DEFAULT NULL,
 request_key CHAR(36) NOT NULL,
 request_hash CHAR(64) NOT NULL,
 performed_by_user_id INT UNSIGNED NOT NULL,
 performed_by_employee_id INT UNSIGNED DEFAULT NULL,
 occurred_at DATETIME NOT NULL,
 created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
 PRIMARY KEY (id),
 UNIQUE KEY uq_hr_request (request_key),
 UNIQUE KEY uq_hr_piece (id,pool_id,party_id,lot_id,origin_melt_entry_id,grams),
 UNIQUE KEY uq_hr_movement (id,pool_id,grams),
 KEY idx_hr_pool (pool_id,id),
 KEY idx_hr_party (party_id,id),
 CONSTRAINT fk_hr_pool FOREIGN KEY (pool_id,party_id,lot_id,origin_melt_entry_id) REFERENCES intermediary_holding_pools(id,party_id,lot_id,origin_melt_entry_id),
 CONSTRAINT fk_hr_user FOREIGN KEY (performed_by_user_id) REFERENCES users(id),
 CONSTRAINT fk_hr_employee FOREIGN KEY (performed_by_employee_id) REFERENCES employees(id),
 CONSTRAINT chk_hr_grams CHECK (grams>0)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- RP: the NEW identity of the received gold, always in the SHOP, assay UNKNOWN (the first slice), provenance copied through the composite key.
CREATE TABLE IF NOT EXISTS receipt_pieces (
 id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
 receipt_id BIGINT UNSIGNED NOT NULL,
 pool_id BIGINT UNSIGNED NOT NULL,
 party_id INT UNSIGNED NOT NULL,
 lot_id BIGINT UNSIGNED NOT NULL,
 origin_melt_entry_id BIGINT UNSIGNED NOT NULL,
 grams DECIMAL(12,3) NOT NULL,
 identity_kind VARCHAR(14) NOT NULL DEFAULT 'RECEIPT_PIECE',
 location VARCHAR(4) NOT NULL DEFAULT 'SHOP',
 assay_status VARCHAR(7) NOT NULL DEFAULT 'UNKNOWN',
 fineness_permille DECIMAL(5,1) DEFAULT NULL,
 pure_grams DECIMAL(18,6) DEFAULT NULL,
 created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
 PRIMARY KEY (id),
 UNIQUE KEY uq_rp_receipt (receipt_id),
 UNIQUE KEY uq_rp_stock (id,receipt_id,grams),
 KEY idx_rp_pool (pool_id,id),
 CONSTRAINT fk_rp_receipt FOREIGN KEY (receipt_id,pool_id,party_id,lot_id,origin_melt_entry_id,grams) REFERENCES holding_receipts(id,pool_id,party_id,lot_id,origin_melt_entry_id,grams),
 CONSTRAINT chk_rp_shape CHECK (grams>0 AND identity_kind='RECEIPT_PIECE' AND location='SHOP' AND assay_status='UNKNOWN' AND fineness_permille IS NULL AND pure_grams IS NULL)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- The shop receipt stock ledger: stock of a piece = SUM(weight_delta). A reversal is a compensation, never an exclusion.
CREATE TABLE IF NOT EXISTS receipt_stock_movements (
 id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
 piece_id BIGINT UNSIGNED NOT NULL,
 receipt_id BIGINT UNSIGNED NOT NULL,
 movement_type VARCHAR(16) NOT NULL,
 weight_grams DECIMAL(12,3) NOT NULL,
 weight_delta DECIMAL(12,3) NOT NULL,
 correction_id BIGINT UNSIGNED DEFAULT NULL,
 created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
 PRIMARY KEY (id),
 UNIQUE KEY uq_rsm_piece_type (piece_id,movement_type),
 UNIQUE KEY uq_rsm_correction (correction_id),
 KEY idx_rsm_piece (piece_id,id),
 CONSTRAINT fk_rsm_piece FOREIGN KEY (piece_id,receipt_id,weight_grams) REFERENCES receipt_pieces(id,receipt_id,grams),
 CONSTRAINT fk_rsm_correction FOREIGN KEY (correction_id) REFERENCES transaction_corrections(id),
 CONSTRAINT chk_rsm_shape CHECK (weight_grams>0 AND (
  (movement_type='RECEIVE' AND weight_delta=weight_grams AND correction_id IS NULL)
  OR (movement_type='RECEIVE_REVERSAL' AND weight_delta=-weight_grams AND correction_id IS NOT NULL)))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
SET @masgold_sql := IF(NOT EXISTS(SELECT 1 FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='intermediary_holding_movements' AND COLUMN_NAME='receipt_id'), "ALTER TABLE intermediary_holding_movements ADD COLUMN receipt_id BIGINT UNSIGNED NULL", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(EXISTS(SELECT 1 FROM information_schema.TABLE_CONSTRAINTS tc JOIN information_schema.CHECK_CONSTRAINTS cc ON cc.CONSTRAINT_SCHEMA=tc.CONSTRAINT_SCHEMA AND cc.CONSTRAINT_NAME=tc.CONSTRAINT_NAME WHERE tc.TABLE_SCHEMA=DATABASE() AND tc.TABLE_NAME='intermediary_holding_movements' AND tc.CONSTRAINT_NAME='chk_ihm_shape' AND cc.CHECK_CLAUSE NOT LIKE '%RECEIPT_REVERSAL%'), "ALTER TABLE intermediary_holding_movements DROP CHECK chk_ihm_shape", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(NOT EXISTS(SELECT 1 FROM information_schema.TABLE_CONSTRAINTS WHERE CONSTRAINT_SCHEMA=DATABASE() AND TABLE_NAME='intermediary_holding_movements' AND CONSTRAINT_NAME='chk_ihm_shape'), "ALTER TABLE intermediary_holding_movements ADD CONSTRAINT chk_ihm_shape CHECK (weight_grams>0 AND ((document_id IS NOT NULL AND settlement_id IS NULL AND receipt_id IS NULL AND movement_type='HANDOVER' AND doc_kind='HANDOVER' AND weight_delta=weight_grams AND correction_id IS NULL) OR (document_id IS NOT NULL AND settlement_id IS NULL AND receipt_id IS NULL AND movement_type='RETURN' AND doc_kind='RETURN' AND weight_delta=-weight_grams AND correction_id IS NULL) OR (document_id IS NOT NULL AND settlement_id IS NULL AND receipt_id IS NULL AND movement_type='HANDOVER_REVERSAL' AND doc_kind='HANDOVER' AND weight_delta=-weight_grams AND correction_id IS NOT NULL) OR (document_id IS NOT NULL AND settlement_id IS NULL AND receipt_id IS NULL AND movement_type='RETURN_REVERSAL' AND doc_kind='RETURN' AND weight_delta=weight_grams AND correction_id IS NOT NULL) OR (document_id IS NULL AND doc_kind IS NULL AND settlement_id IS NOT NULL AND receipt_id IS NULL AND movement_type='SETTLEMENT' AND weight_delta=-weight_grams AND correction_id IS NULL) OR (document_id IS NULL AND doc_kind IS NULL AND settlement_id IS NOT NULL AND receipt_id IS NULL AND movement_type='SETTLEMENT_REVERSAL' AND weight_delta=weight_grams AND correction_id IS NOT NULL) OR (document_id IS NULL AND doc_kind IS NULL AND settlement_id IS NULL AND receipt_id IS NOT NULL AND movement_type='RECEIPT' AND weight_delta=-weight_grams AND correction_id IS NULL) OR (document_id IS NULL AND doc_kind IS NULL AND settlement_id IS NULL AND receipt_id IS NOT NULL AND movement_type='RECEIPT_REVERSAL' AND weight_delta=weight_grams AND correction_id IS NOT NULL)))", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(NOT EXISTS(SELECT 1 FROM information_schema.STATISTICS WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='intermediary_holding_movements' AND INDEX_NAME='uq_ihm_receipt_type'), "ALTER TABLE intermediary_holding_movements ADD UNIQUE KEY uq_ihm_receipt_type (receipt_id,movement_type)", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(NOT EXISTS(SELECT 1 FROM information_schema.STATISTICS WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='intermediary_holding_movements' AND INDEX_NAME='idx_ihm_receipt'), "ALTER TABLE intermediary_holding_movements ADD KEY idx_ihm_receipt (receipt_id,pool_id,weight_grams)", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(NOT EXISTS(SELECT 1 FROM information_schema.TABLE_CONSTRAINTS WHERE CONSTRAINT_SCHEMA=DATABASE() AND TABLE_NAME='intermediary_holding_movements' AND CONSTRAINT_NAME='fk_ihm_receipt'), "ALTER TABLE intermediary_holding_movements ADD CONSTRAINT fk_ihm_receipt FOREIGN KEY (receipt_id,pool_id,weight_grams) REFERENCES holding_receipts(id,pool_id,grams)", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(EXISTS(SELECT 1 FROM information_schema.TABLE_CONSTRAINTS tc JOIN information_schema.CHECK_CONSTRAINTS cc ON cc.CONSTRAINT_SCHEMA=tc.CONSTRAINT_SCHEMA AND cc.CONSTRAINT_NAME=tc.CONSTRAINT_NAME WHERE tc.TABLE_SCHEMA=DATABASE() AND tc.TABLE_NAME='transaction_corrections' AND tc.CONSTRAINT_NAME='chk_correction_source' AND cc.CHECK_CLAUSE NOT LIKE '%HOLDING_RECEIPT%'), "ALTER TABLE transaction_corrections DROP CHECK chk_correction_source", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(NOT EXISTS(SELECT 1 FROM information_schema.TABLE_CONSTRAINTS WHERE CONSTRAINT_SCHEMA=DATABASE() AND TABLE_NAME='transaction_corrections' AND CONSTRAINT_NAME='chk_correction_source'), "ALTER TABLE transaction_corrections ADD CONSTRAINT chk_correction_source CHECK ((source_type IN ('INVOICE','EXCHANGE','RETURN') AND invoice_id IS NOT NULL) OR (source_type='CUSTOMER_ACCOUNT_SETTLEMENT' AND invoice_id IS NULL) OR (source_type IN ('FINANCIAL_OPERATION','CUSTOMER_DEPOSIT','SUPPLIER_MAKING_PAYMENT','MARKET_TRIP_ENTRY','SUPPLIER_GOLD_SETTLEMENT','HOLDING_RECEIPT') AND invoice_id IS NULL AND correction_type='FULL_REVERSAL'))", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

INSERT INTO schema_migrations(version,name) VALUES (51,'Physical receipts from intermediary holdings: HR documents, RP shop receipt pieces and receipt stock')
 ON DUPLICATE KEY UPDATE name=VALUES(name);
