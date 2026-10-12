-- Master-karat LOGO holdings at a market intermediary (مسوقجي). Pooled PHYSICAL grams of one master karat, designation LOGO: not an identified bar, no
-- fineness, no pure-gold figure. The 048 whole-lot tables and rules are untouched; these parallel tables keep one immutable source pool per handover,
-- IG documents, signed holding movements (a reversal is an explicit compensation row, the original stays counted) and a typed link per custody entry.
-- Custody LOGO outstanding = issued - returned - melted + heat_delta - supplier_settled - held_out + held_back (effective entries only).
SET @masgold_sql = IF(EXISTS(SELECT 1 FROM schema_migrations WHERE version=48),
 'DO 0', 'SELECT * FROM MIGRATION_049_REFUSED_missing_048');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(EXISTS(SELECT 1 FROM information_schema.TABLE_CONSTRAINTS WHERE CONSTRAINT_SCHEMA=DATABASE() AND TABLE_NAME='market_trip_entries' AND CONSTRAINT_NAME='chk_mte_kind'), "ALTER TABLE market_trip_entries DROP CHECK chk_mte_kind", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(NOT EXISTS(SELECT 1 FROM information_schema.TABLE_CONSTRAINTS WHERE CONSTRAINT_SCHEMA=DATABASE() AND TABLE_NAME='market_trip_entries' AND CONSTRAINT_NAME='chk_mte_kind'), "ALTER TABLE market_trip_entries ADD CONSTRAINT chk_mte_kind CHECK (entry_kind IN ('ISSUE','RETURN','MELT','LOT_RETURN','LOT_ISSUE','HEAT','SUPPLIER_PAY','GOLD_SETTLE','LOT_SETTLE','LOT_HOLD','HOLD_RETURN','LOGO_HOLD','HOLD_BACK'))", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

-- One immutable source pool per handover: party, karat, ORIGINAL custody trip and the handed-over quantity. Nothing is merged, not even for the same party,
-- karat and trip.
CREATE TABLE IF NOT EXISTS logo_holding_pools (
 id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
 party_id INT UNSIGNED NOT NULL,
 karat_id TINYINT UNSIGNED NOT NULL,
 designation VARCHAR(8) NOT NULL DEFAULT 'LOGO',
 trip_id BIGINT UNSIGNED NOT NULL,
 weight_grams DECIMAL(12,3) NOT NULL,
 created_by_user_id INT UNSIGNED NOT NULL,
 created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
 PRIMARY KEY (id),
 UNIQUE KEY uq_lhp_facts (id,party_id,karat_id,trip_id),
 UNIQUE KEY uq_lhp_weight (id,weight_grams),
 KEY idx_lhp_party (party_id,id),
 KEY idx_lhp_trip (trip_id,id),
 CONSTRAINT fk_lhp_party FOREIGN KEY (party_id) REFERENCES suppliers(id),
 CONSTRAINT fk_lhp_karat FOREIGN KEY (karat_id) REFERENCES karats(id),
 CONSTRAINT fk_lhp_trip FOREIGN KEY (trip_id) REFERENCES market_trips(id),
 CONSTRAINT fk_lhp_user FOREIGN KEY (created_by_user_id) REFERENCES users(id),
 CONSTRAINT chk_lhp_facts CHECK (designation='LOGO' AND weight_grams>0)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- IG documents. A HANDOVER credits exactly its pool (handover_pool_id is unique, so one pool has ONE original handover, and its quantity equals the pool's);
-- a RETURN has any positive quantity (the service caps it by the pool balance under the locks) and must name the ORIGINAL trip of the pool.
CREATE TABLE IF NOT EXISTS logo_holding_documents (
 id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
 doc_kind VARCHAR(8) NOT NULL,
 pool_id BIGINT UNSIGNED NOT NULL,
 party_id INT UNSIGNED NOT NULL,
 karat_id TINYINT UNSIGNED NOT NULL,
 trip_id BIGINT UNSIGNED NOT NULL,
 weight_grams DECIMAL(12,3) NOT NULL,
 handover_pool_id BIGINT UNSIGNED DEFAULT NULL,
 handover_weight DECIMAL(12,3) DEFAULT NULL,
 document_date DATE NOT NULL,
 reference VARCHAR(100) DEFAULT NULL,
 notes VARCHAR(1000) DEFAULT NULL,
 request_key CHAR(36) NOT NULL,
 request_hash CHAR(64) NOT NULL,
 performed_by_user_id INT UNSIGNED NOT NULL,
 performed_by_employee_id INT UNSIGNED DEFAULT NULL,
 occurred_at DATETIME NOT NULL,
 created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
 PRIMARY KEY (id),
 UNIQUE KEY uq_lhd_request (request_key),
 UNIQUE KEY uq_lhd_one_handover (handover_pool_id),
 UNIQUE KEY uq_lhd_movement (id,doc_kind,pool_id,weight_grams),
 UNIQUE KEY uq_lhd_link (id,doc_kind,pool_id,karat_id,weight_grams,trip_id),
 KEY idx_lhd_pool (pool_id,id),
 KEY idx_lhd_party (party_id,id),
 CONSTRAINT fk_lhd_pool FOREIGN KEY (pool_id,party_id,karat_id,trip_id) REFERENCES logo_holding_pools(id,party_id,karat_id,trip_id),
 CONSTRAINT fk_lhd_handover FOREIGN KEY (handover_pool_id,handover_weight) REFERENCES logo_holding_pools(id,weight_grams),
 CONSTRAINT fk_lhd_user FOREIGN KEY (performed_by_user_id) REFERENCES users(id),
 CONSTRAINT fk_lhd_employee FOREIGN KEY (performed_by_employee_id) REFERENCES employees(id),
 CONSTRAINT chk_lhd_shape CHECK (weight_grams>0 AND (
  (doc_kind='HANDOVER' AND handover_pool_id IS NOT NULL AND handover_pool_id=pool_id AND handover_weight IS NOT NULL AND handover_weight=weight_grams)
  OR (doc_kind='RETURN' AND handover_pool_id IS NULL AND handover_weight IS NULL)))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Signed movements. balance(pool) = SUM(weight_delta) over ALL movements; a reversal posts a compensation (never excludes the original).
CREATE TABLE IF NOT EXISTS logo_holding_movements (
 id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
 pool_id BIGINT UNSIGNED NOT NULL,
 document_id BIGINT UNSIGNED NOT NULL,
 doc_kind VARCHAR(8) NOT NULL,
 movement_type VARCHAR(20) NOT NULL,
 weight_grams DECIMAL(12,3) NOT NULL,
 weight_delta DECIMAL(12,3) NOT NULL,
 correction_id BIGINT UNSIGNED DEFAULT NULL,
 created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
 PRIMARY KEY (id),
 UNIQUE KEY uq_lhm_document_type (document_id,movement_type),
 UNIQUE KEY uq_lhm_correction (correction_id),
 KEY idx_lhm_pool (pool_id,id),
 CONSTRAINT fk_lhm_document FOREIGN KEY (document_id,doc_kind,pool_id,weight_grams) REFERENCES logo_holding_documents(id,doc_kind,pool_id,weight_grams),
 CONSTRAINT fk_lhm_correction FOREIGN KEY (correction_id) REFERENCES transaction_corrections(id),
 CONSTRAINT chk_lhm_shape CHECK (weight_grams>0 AND (
  (movement_type='HANDOVER' AND doc_kind='HANDOVER' AND weight_delta=weight_grams AND correction_id IS NULL)
  OR (movement_type='RETURN' AND doc_kind='RETURN' AND weight_delta=-weight_grams AND correction_id IS NULL)
  OR (movement_type='HANDOVER_REVERSAL' AND doc_kind='HANDOVER' AND weight_delta=-weight_grams AND correction_id IS NOT NULL)
  OR (movement_type='RETURN_REVERSAL' AND doc_kind='RETURN' AND weight_delta=weight_grams AND correction_id IS NOT NULL)))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Typed custody link: one custody entry <-> one IG document, with the karat and quantity the custody arithmetic reads (held_out / held_back).
CREATE TABLE IF NOT EXISTS market_trip_logo_hold_links (
 entry_id BIGINT UNSIGNED NOT NULL,
 entry_kind VARCHAR(12) NOT NULL,
 trip_id BIGINT UNSIGNED NOT NULL,
 document_id BIGINT UNSIGNED NOT NULL,
 doc_kind VARCHAR(8) NOT NULL,
 pool_id BIGINT UNSIGNED NOT NULL,
 karat_id TINYINT UNSIGNED NOT NULL,
 designation VARCHAR(8) NOT NULL DEFAULT 'LOGO',
 weight_grams DECIMAL(12,3) NOT NULL,
 PRIMARY KEY (entry_id),
 UNIQUE KEY uq_mtlhl_document (document_id),
 KEY idx_mtlhl_karat (karat_id),
 CONSTRAINT fk_mtlhl_entry FOREIGN KEY (entry_id,entry_kind,trip_id) REFERENCES market_trip_entries(id,entry_kind,trip_id),
 CONSTRAINT fk_mtlhl_document FOREIGN KEY (document_id,doc_kind,pool_id,karat_id,weight_grams,trip_id) REFERENCES logo_holding_documents(id,doc_kind,pool_id,karat_id,weight_grams,trip_id),
 CONSTRAINT chk_mtlhl_shape CHECK (designation='LOGO' AND weight_grams>0 AND (
  (entry_kind='LOGO_HOLD' AND doc_kind='HANDOVER') OR (entry_kind='HOLD_BACK' AND doc_kind='RETURN')))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

INSERT INTO schema_migrations(version,name) VALUES (49,'Master-karat LOGO holdings at a market intermediary: pools, IG documents, signed movements and custody links')
 ON DUPLICATE KEY UPDATE name=VALUES(name);
