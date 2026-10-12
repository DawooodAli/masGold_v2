-- Whole assayed lot holdings at a market intermediary (مسوقجي). SHOP-OWNED gold that physically sits with the party: never a supplier payable,
-- never turnover, never a new physical bar. Facts and movements are immutable; the custody side keeps its established reversal convention
-- (the custody entry stops counting) while the holding ledger uses explicit signed compensation movements (the original is never excluded).
-- A lot may be handed over many times: nothing here is unique per lot across history, only per document.
SET @masgold_sql = IF(EXISTS(SELECT 1 FROM schema_migrations WHERE version=47),
 'DO 0', 'SELECT * FROM MIGRATION_048_REFUSED_missing_047');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(EXISTS(SELECT 1 FROM information_schema.TABLE_CONSTRAINTS WHERE CONSTRAINT_SCHEMA=DATABASE() AND TABLE_NAME='market_trip_entries' AND CONSTRAINT_NAME='chk_mte_kind'), "ALTER TABLE market_trip_entries DROP CHECK chk_mte_kind", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(NOT EXISTS(SELECT 1 FROM information_schema.TABLE_CONSTRAINTS WHERE CONSTRAINT_SCHEMA=DATABASE() AND TABLE_NAME='market_trip_entries' AND CONSTRAINT_NAME='chk_mte_kind'), "ALTER TABLE market_trip_entries ADD CONSTRAINT chk_mte_kind CHECK (entry_kind IN ('ISSUE','RETURN','MELT','LOT_RETURN','LOT_ISSUE','HEAT','SUPPLIER_PAY','GOLD_SETTLE','LOT_SETTLE','LOT_HOLD','HOLD_RETURN'))", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(NOT EXISTS(SELECT 1 FROM information_schema.STATISTICS WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='market_trip_entries' AND INDEX_NAME='uq_mte_id_kind_trip'), "ALTER TABLE market_trip_entries ADD UNIQUE KEY uq_mte_id_kind_trip (id,entry_kind,trip_id)", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(EXISTS(SELECT 1 FROM information_schema.TABLE_CONSTRAINTS WHERE CONSTRAINT_SCHEMA=DATABASE() AND TABLE_NAME='market_trip_lot_movements' AND CONSTRAINT_NAME='chk_mtlm_type'), "ALTER TABLE market_trip_lot_movements DROP CHECK chk_mtlm_type", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(NOT EXISTS(SELECT 1 FROM information_schema.TABLE_CONSTRAINTS WHERE CONSTRAINT_SCHEMA=DATABASE() AND TABLE_NAME='market_trip_lot_movements' AND CONSTRAINT_NAME='chk_mtlm_type'), "ALTER TABLE market_trip_lot_movements ADD CONSTRAINT chk_mtlm_type CHECK (movement_type IN ('CREATE','SHOP_RETURN','SHOP_ISSUE','SUPPLIER_SETTLE','HOLD_OUT','HOLD_IN'))", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(EXISTS(SELECT 1 FROM information_schema.TABLE_CONSTRAINTS WHERE CONSTRAINT_SCHEMA=DATABASE() AND TABLE_NAME='market_trip_lot_movements' AND CONSTRAINT_NAME='chk_mtlm_delta'), "ALTER TABLE market_trip_lot_movements DROP CHECK chk_mtlm_delta", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(NOT EXISTS(SELECT 1 FROM information_schema.TABLE_CONSTRAINTS WHERE CONSTRAINT_SCHEMA=DATABASE() AND TABLE_NAME='market_trip_lot_movements' AND CONSTRAINT_NAME='chk_mtlm_delta'), "ALTER TABLE market_trip_lot_movements ADD CONSTRAINT chk_mtlm_delta CHECK (weight_delta <> 0 AND (movement_type NOT IN ('CREATE','SHOP_ISSUE','HOLD_IN') OR weight_delta>0) AND (movement_type NOT IN ('SHOP_RETURN','SUPPLIER_SETTLE','HOLD_OUT') OR weight_delta<0))", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

-- Immutable source pool: ONE handover of ONE lot to ONE party. A ledger identity, not a physical bar; sources are never merged even when the assay matches.
CREATE TABLE IF NOT EXISTS intermediary_holding_pools (
 id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
 party_id INT UNSIGNED NOT NULL,
 lot_id BIGINT UNSIGNED NOT NULL,
 origin_melt_entry_id BIGINT UNSIGNED NOT NULL,
 weight_grams DECIMAL(12,3) NOT NULL,
 fineness_permille DECIMAL(5,1) NOT NULL,
 created_by_user_id INT UNSIGNED NOT NULL,
 created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
 PRIMARY KEY (id),
 UNIQUE KEY uq_ihp_facts (id,party_id,lot_id,weight_grams,fineness_permille),
 KEY idx_ihp_party (party_id,id),
 KEY idx_ihp_lot (lot_id,id),
 CONSTRAINT fk_ihp_party FOREIGN KEY (party_id) REFERENCES suppliers(id),
 CONSTRAINT fk_ihp_lot FOREIGN KEY (lot_id,origin_melt_entry_id,weight_grams,fineness_permille) REFERENCES market_trip_lots(id,entry_id,weight_grams,fineness_permille),
 CONSTRAINT fk_ihp_user FOREIGN KEY (created_by_user_id) REFERENCES users(id),
 CONSTRAINT chk_ihp_facts CHECK (weight_grams>0 AND fineness_permille>0 AND fineness_permille<=1000)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Holding documents (prefix IH): HANDOVER creates the pool, RETURN takes the same whole lot back to the original custody. A reversal is NOT a
-- document: it is a compensating movement that names the original document and the RV correction.
CREATE TABLE IF NOT EXISTS intermediary_holding_documents (
 id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
 doc_kind VARCHAR(8) NOT NULL,
 pool_id BIGINT UNSIGNED NOT NULL,
 party_id INT UNSIGNED NOT NULL,
 lot_id BIGINT UNSIGNED NOT NULL,
 weight_grams DECIMAL(12,3) NOT NULL,
 fineness_permille DECIMAL(5,1) NOT NULL,
 trip_id BIGINT UNSIGNED NOT NULL,
 document_date DATE NOT NULL,
 reference VARCHAR(100) DEFAULT NULL,
 notes VARCHAR(1000) DEFAULT NULL,
 intact_confirmation_code VARCHAR(40) DEFAULT NULL,
 confirmed_by_user_id INT UNSIGNED DEFAULT NULL,
 confirmed_at DATETIME DEFAULT NULL,
 request_key CHAR(36) NOT NULL,
 request_hash CHAR(64) NOT NULL,
 performed_by_user_id INT UNSIGNED NOT NULL,
 performed_by_employee_id INT UNSIGNED DEFAULT NULL,
 occurred_at DATETIME NOT NULL,
 created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
 PRIMARY KEY (id),
 UNIQUE KEY uq_ihd_request (request_key),
 UNIQUE KEY uq_ihd_link (id,doc_kind,pool_id,lot_id,weight_grams,trip_id),
 UNIQUE KEY uq_ihd_movement (id,doc_kind,pool_id,weight_grams),
 KEY idx_ihd_pool (pool_id,id),
 KEY idx_ihd_party (party_id,id),
 KEY idx_ihd_trip (trip_id,id),
 CONSTRAINT fk_ihd_pool FOREIGN KEY (pool_id,party_id,lot_id,weight_grams,fineness_permille) REFERENCES intermediary_holding_pools(id,party_id,lot_id,weight_grams,fineness_permille),
 CONSTRAINT fk_ihd_trip FOREIGN KEY (trip_id) REFERENCES market_trips(id),
 CONSTRAINT fk_ihd_user FOREIGN KEY (performed_by_user_id) REFERENCES users(id),
 CONSTRAINT fk_ihd_confirm_user FOREIGN KEY (confirmed_by_user_id) REFERENCES users(id),
 CONSTRAINT fk_ihd_employee FOREIGN KEY (performed_by_employee_id) REFERENCES employees(id),
 CONSTRAINT chk_ihd_kind CHECK (doc_kind IN ('HANDOVER','RETURN')),
 CONSTRAINT chk_ihd_confirmation CHECK (
  (doc_kind='HANDOVER' AND intact_confirmation_code IS NULL AND confirmed_by_user_id IS NULL AND confirmed_at IS NULL)
  OR (doc_kind='RETURN' AND intact_confirmation_code='SAME_BAR_WHOLE_UNDIVIDED' AND confirmed_by_user_id IS NOT NULL AND confirmed_at IS NOT NULL))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Signed holding movements. balance(pool) = SUM(weight_delta) over ALL of its movements (compensations are real rows, nothing is excluded).
CREATE TABLE IF NOT EXISTS intermediary_holding_movements (
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
 UNIQUE KEY uq_ihm_document_type (document_id,movement_type),
 UNIQUE KEY uq_ihm_correction (correction_id),
 KEY idx_ihm_pool (pool_id,id),
 CONSTRAINT fk_ihm_document FOREIGN KEY (document_id,doc_kind,pool_id,weight_grams) REFERENCES intermediary_holding_documents(id,doc_kind,pool_id,weight_grams),
 CONSTRAINT fk_ihm_correction FOREIGN KEY (correction_id) REFERENCES transaction_corrections(id),
 CONSTRAINT chk_ihm_shape CHECK (weight_grams>0 AND (
  (movement_type='HANDOVER' AND doc_kind='HANDOVER' AND weight_delta=weight_grams AND correction_id IS NULL)
  OR (movement_type='RETURN' AND doc_kind='RETURN' AND weight_delta=-weight_grams AND correction_id IS NULL)
  OR (movement_type='HANDOVER_REVERSAL' AND doc_kind='HANDOVER' AND weight_delta=-weight_grams AND correction_id IS NOT NULL)
  OR (movement_type='RETURN_REVERSAL' AND doc_kind='RETURN' AND weight_delta=weight_grams AND correction_id IS NOT NULL)))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Typed custody link: one custody entry <-> one holding document <-> the one custody lot movement it posted. Same lot, weight, trip and movement.
CREATE TABLE IF NOT EXISTS market_trip_holding_links (
 entry_id BIGINT UNSIGNED NOT NULL,
 entry_kind VARCHAR(12) NOT NULL,
 trip_id BIGINT UNSIGNED NOT NULL,
 document_id BIGINT UNSIGNED NOT NULL,
 doc_kind VARCHAR(8) NOT NULL,
 pool_id BIGINT UNSIGNED NOT NULL,
 lot_id BIGINT UNSIGNED NOT NULL,
 weight_grams DECIMAL(12,3) NOT NULL,
 custody_movement_id BIGINT UNSIGNED NOT NULL,
 movement_type VARCHAR(20) NOT NULL,
 weight_delta DECIMAL(12,3) NOT NULL,
 PRIMARY KEY (entry_id),
 UNIQUE KEY uq_mthl_document (document_id),
 UNIQUE KEY uq_mthl_movement (custody_movement_id),
 KEY idx_mthl_pool (pool_id),
 CONSTRAINT fk_mthl_entry FOREIGN KEY (entry_id,entry_kind,trip_id) REFERENCES market_trip_entries(id,entry_kind,trip_id),
 CONSTRAINT fk_mthl_document FOREIGN KEY (document_id,doc_kind,pool_id,lot_id,weight_grams,trip_id) REFERENCES intermediary_holding_documents(id,doc_kind,pool_id,lot_id,weight_grams,trip_id),
 CONSTRAINT fk_mthl_movement FOREIGN KEY (custody_movement_id,lot_id,entry_id,movement_type,weight_delta) REFERENCES market_trip_lot_movements(id,lot_id,entry_id,movement_type,weight_delta),
 CONSTRAINT chk_mthl_shape CHECK (weight_grams>0 AND (
  (entry_kind='LOT_HOLD' AND doc_kind='HANDOVER' AND movement_type='HOLD_OUT' AND weight_delta=-weight_grams)
  OR (entry_kind='HOLD_RETURN' AND doc_kind='RETURN' AND movement_type='HOLD_IN' AND weight_delta=weight_grams)))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- OWNER-only rights (the custody entries additionally need MARKET_TRIP_MANAGE; a reversal also TRANSACTION_CORRECT).
INSERT INTO permissions(code,name,description) VALUES
 ('INTERMEDIARY_HOLDING_VIEW','View Intermediary Holdings','View shop gold held by a market intermediary (owner only)'),
 ('INTERMEDIARY_HOLDING_MANAGE','Manage Intermediary Holdings','Hand a whole assayed lot to a market intermediary and take it back (owner only)'),
 ('INTERMEDIARY_HOLDING_REVERSE','Reverse Intermediary Holdings','Reverse a handover or a return of an intermediary holding (owner only)')
 ON DUPLICATE KEY UPDATE name=VALUES(name), description=VALUES(description);
INSERT INTO role_permissions(role_id,permission_id)
 SELECT r.id,p.id FROM roles r JOIN permissions p ON p.code IN ('INTERMEDIARY_HOLDING_VIEW','INTERMEDIARY_HOLDING_MANAGE','INTERMEDIARY_HOLDING_REVERSE')
 WHERE r.code='OWNER' AND NOT EXISTS
 (SELECT 1 FROM role_permissions rp WHERE rp.role_id=r.id AND rp.permission_id=p.id);

INSERT INTO schema_migrations(version,name) VALUES (48,'Whole assayed lot holdings at a market intermediary: pools, documents, signed movements, custody links and owner rights')
 ON DUPLICATE KEY UPDATE name=VALUES(name);
