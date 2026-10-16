-- Marketer (مسوقجي) custody account: the shop's CASH and 21K GOLD held in trust by a market intermediary, and the cycle gold -> cash -> gold.
--   marketer_documents       immutable documents of the account (the codes are MH cash handover, MR return of cash or of gold, MB cash purchase of gold, MS sale of gold):
--                            a CASH_HANDOVER moves cash Main Safe -> marketer, a CASH_RETURN the opposite, a GOLD_RETURN moves 21K grams marketer -> counted shop Scrap, a
--                            PURCHASE spends marketer cash and adds 21K gold (the VENDOR is mandatory), a SALE removes 21K gold and adds marketer cash (the BUYER is mandatory).
--   marketer_purchase_lines  the per-line price of a purchase (21K actual grams and the agreed price).
--   marketer_cash_movements / marketer_gold_movements   insert-only signed ledgers; a balance is the SUM of every movement and a reversal is an explicit
--                            compensation (the original movement stays counted). Gold is ALWAYS 21K (karat_value=21 is a CHECK) and physical grams; cash is EGP.
--   supplier settlement      the supplier gold settlement (SG) gains the source HOLDING_MARKETER: the marketer's 21K grams are delivered to a supplier, converted by the
--                            existing rule (grams x 21 / supplier karat), ONE negative marketer gold movement and ONE supplier gold movement, tied by composite keys.
-- ISOLATION: no table here references a supplier ledger (supplier_gold_movements, supplier_making_movements, supplier_making_payments) and no balance is shared; the
-- marketer party is only an identity reference (suppliers.id). The ONE bridge between the two worlds is the explicit settlement document above (never a netting).
-- Shop-side effects of cash handovers / returns and of a gold return use NEW movement types, never the market-trip ones. Every step is guarded (a re-run changes nothing).
SET @masgold_sql = IF(EXISTS(SELECT 1 FROM schema_migrations WHERE version=54),
 'DO 0', 'SELECT * FROM MIGRATION_055_REFUSED_missing_054');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

-- 1. movement types of the SHOP side (cash Main Safe <-> marketer, Scrap <- marketer)
INSERT INTO cash_movement_types(code,name,expected_direction,description) VALUES
 ('MARKETER_CASH_HANDOVER','Marketer Cash Handover','OUT','Cash handed from the Main Safe to a marketer (held in trust, not a payment)'),
 ('MARKETER_CASH_RETURN','Marketer Cash Return','IN','Cash returned by a marketer to the Main Safe')
 ON DUPLICATE KEY UPDATE name=VALUES(name), description=VALUES(description);
INSERT INTO old_gold_movement_types(code,name,description,expected_direction) VALUES
 ('MARKETER_GOLD_RETURN_IN','Marketer Gold Return In','21K gold returned by a marketer: enters the counted shop Scrap balance','IN'),
 ('REVERSAL_MARKETER_GOLD_RETURN_OUT','Reversal Marketer Gold Return Out','A reversed marketer gold return: the Scrap goes back to the marketer','OUT')
 ON DUPLICATE KEY UPDATE name=VALUES(name), description=VALUES(description), expected_direction=VALUES(expected_direction);

-- 2. the correction source
SET @masgold_sql := IF(EXISTS(SELECT 1 FROM information_schema.TABLE_CONSTRAINTS tc JOIN information_schema.CHECK_CONSTRAINTS cc ON cc.CONSTRAINT_SCHEMA=tc.CONSTRAINT_SCHEMA AND cc.CONSTRAINT_NAME=tc.CONSTRAINT_NAME WHERE tc.TABLE_SCHEMA=DATABASE() AND tc.TABLE_NAME='transaction_corrections' AND tc.CONSTRAINT_NAME='chk_correction_source' AND cc.CHECK_CLAUSE NOT LIKE '%MARKETER_DOCUMENT%'), "ALTER TABLE transaction_corrections DROP CHECK chk_correction_source", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(NOT EXISTS(SELECT 1 FROM information_schema.TABLE_CONSTRAINTS WHERE CONSTRAINT_SCHEMA=DATABASE() AND TABLE_NAME='transaction_corrections' AND CONSTRAINT_NAME='chk_correction_source'), "ALTER TABLE transaction_corrections ADD CONSTRAINT chk_correction_source CHECK ((source_type IN ('INVOICE','EXCHANGE','RETURN') AND invoice_id IS NOT NULL) OR (source_type='CUSTOMER_ACCOUNT_SETTLEMENT' AND invoice_id IS NULL) OR (source_type IN ('FINANCIAL_OPERATION','CUSTOMER_DEPOSIT','SUPPLIER_MAKING_PAYMENT','MARKET_TRIP_ENTRY','SUPPLIER_GOLD_SETTLEMENT','HOLDING_RECEIPT','SUPPLIER_PURCHASE_INVOICE','MARKETER_DOCUMENT') AND invoice_id IS NULL AND correction_type='FULL_REVERSAL'))", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

-- 3. the documents
CREATE TABLE IF NOT EXISTS marketer_documents (
 id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
 doc_kind VARCHAR(14) NOT NULL,
 party_id INT UNSIGNED NOT NULL,
 karat_value TINYINT UNSIGNED NOT NULL DEFAULT 21,
 cash_amount DECIMAL(16,2) DEFAULT NULL,
 weight_grams DECIMAL(12,3) DEFAULT NULL,
 counterparty_kind VARCHAR(16) DEFAULT NULL,
 counterparty_party_id INT UNSIGNED DEFAULT NULL,
 counterparty_name VARCHAR(150) DEFAULT NULL,
 counterparty_key VARCHAR(150) DEFAULT NULL,
 reference_total DECIMAL(16,2) DEFAULT NULL,
 deviation_percent DECIMAL(9,2) DEFAULT NULL,
 price_warning_acknowledged TINYINT(1) NOT NULL DEFAULT 0,
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
 UNIQUE KEY uq_mkd_request (request_key),
 UNIQUE KEY uq_mkd_kind (id,doc_kind),
 UNIQUE KEY uq_mkd_cash (id,doc_kind,party_id,cash_amount),
 UNIQUE KEY uq_mkd_weight (id,doc_kind,party_id,weight_grams),
 KEY idx_mkd_party (party_id,id),
 KEY idx_mkd_counterparty_party (counterparty_party_id),
 KEY idx_mkd_counterparty_key (counterparty_key),
 KEY idx_mkd_date (document_date),
 CONSTRAINT fk_mkd_party FOREIGN KEY (party_id) REFERENCES suppliers(id),
 CONSTRAINT fk_mkd_counterparty FOREIGN KEY (counterparty_party_id) REFERENCES suppliers(id),
 CONSTRAINT fk_mkd_user FOREIGN KEY (performed_by_user_id) REFERENCES users(id),
 CONSTRAINT fk_mkd_employee FOREIGN KEY (performed_by_employee_id) REFERENCES employees(id),
 CONSTRAINT chk_mkd_karat CHECK (karat_value = 21),
 CONSTRAINT chk_mkd_warning CHECK ((reference_total IS NULL AND deviation_percent IS NULL) OR (reference_total IS NOT NULL AND deviation_percent IS NOT NULL)),
 CONSTRAINT chk_mkd_shape CHECK (
  (doc_kind IN ('CASH_HANDOVER','CASH_RETURN') AND cash_amount IS NOT NULL AND cash_amount > 0 AND weight_grams IS NULL AND counterparty_kind IS NULL
   AND counterparty_party_id IS NULL AND counterparty_name IS NULL AND counterparty_key IS NULL AND reference_total IS NULL)
  OR (doc_kind = 'GOLD_RETURN' AND cash_amount IS NULL AND weight_grams IS NOT NULL AND weight_grams > 0 AND counterparty_kind IS NULL
   AND counterparty_party_id IS NULL AND counterparty_name IS NULL AND counterparty_key IS NULL AND reference_total IS NULL)
  OR (doc_kind IN ('PURCHASE','SALE') AND cash_amount IS NOT NULL AND cash_amount > 0 AND weight_grams IS NOT NULL AND weight_grams > 0
   AND ((counterparty_kind = 'REGISTERED_PARTY' AND counterparty_party_id IS NOT NULL) OR (counterparty_kind = 'THIRD_PARTY' AND counterparty_party_id IS NULL))
   AND counterparty_name IS NOT NULL AND CHAR_LENGTH(TRIM(counterparty_name)) > 0 AND counterparty_key IS NOT NULL AND CHAR_LENGTH(TRIM(counterparty_key)) > 0))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- the shop-side movement a cash handover / return (Main Safe) or a gold return (counted Scrap) posted: linked AFTER both rows exist (insert-only, so no UPDATE is needed)
CREATE TABLE IF NOT EXISTS marketer_shop_links (
 document_id BIGINT UNSIGNED NOT NULL,
 doc_kind VARCHAR(14) NOT NULL,
 shop_cash_movement_id BIGINT UNSIGNED DEFAULT NULL,
 shop_scrap_movement_id BIGINT UNSIGNED DEFAULT NULL,
 PRIMARY KEY (document_id),
 UNIQUE KEY uq_mksl_cash (shop_cash_movement_id),
 UNIQUE KEY uq_mksl_scrap (shop_scrap_movement_id),
 CONSTRAINT fk_mksl_document FOREIGN KEY (document_id, doc_kind) REFERENCES marketer_documents(id, doc_kind),
 CONSTRAINT fk_mksl_cash FOREIGN KEY (shop_cash_movement_id) REFERENCES cash_movements(id),
 CONSTRAINT fk_mksl_scrap FOREIGN KEY (shop_scrap_movement_id) REFERENCES old_gold_movements(id),
 CONSTRAINT chk_mksl_shape CHECK (
  (doc_kind IN ('CASH_HANDOVER','CASH_RETURN') AND shop_cash_movement_id IS NOT NULL AND shop_scrap_movement_id IS NULL)
  OR (doc_kind = 'GOLD_RETURN' AND shop_cash_movement_id IS NULL AND shop_scrap_movement_id IS NOT NULL))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS marketer_purchase_lines (
 document_id BIGINT UNSIGNED NOT NULL,
 doc_kind VARCHAR(14) NOT NULL DEFAULT 'PURCHASE',
 line_no SMALLINT UNSIGNED NOT NULL,
 weight_grams DECIMAL(12,3) NOT NULL,
 line_price DECIMAL(16,2) NOT NULL,
 PRIMARY KEY (document_id, line_no),
 CONSTRAINT fk_mkpl_document FOREIGN KEY (document_id, doc_kind) REFERENCES marketer_documents(id, doc_kind),
 CONSTRAINT chk_mkpl_shape CHECK (doc_kind = 'PURCHASE' AND weight_grams > 0 AND line_price > 0)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- 4. the cash ledger (EGP, insert-only, signed; a reversal is a compensation counted beside the original)
CREATE TABLE IF NOT EXISTS marketer_cash_movements (
 id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
 party_id INT UNSIGNED NOT NULL,
 document_id BIGINT UNSIGNED NOT NULL,
 doc_kind VARCHAR(14) NOT NULL,
 movement_type VARCHAR(24) NOT NULL,
 cash_amount DECIMAL(16,2) NOT NULL,
 amount_delta DECIMAL(16,2) NOT NULL,
 correction_id BIGINT UNSIGNED DEFAULT NULL,
 created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
 PRIMARY KEY (id),
 UNIQUE KEY uq_mkcm_document_type (document_id, movement_type),
 UNIQUE KEY uq_mkcm_correction (correction_id),
 KEY idx_mkcm_party (party_id, id),
 CONSTRAINT fk_mkcm_document FOREIGN KEY (document_id, doc_kind, party_id, cash_amount) REFERENCES marketer_documents(id, doc_kind, party_id, cash_amount),
 CONSTRAINT fk_mkcm_correction FOREIGN KEY (correction_id) REFERENCES transaction_corrections(id),
 CONSTRAINT chk_mkcm_shape CHECK (cash_amount > 0 AND (
  (movement_type = 'CASH_HANDOVER' AND doc_kind = 'CASH_HANDOVER' AND amount_delta = cash_amount AND correction_id IS NULL)
  OR (movement_type = 'CASH_RETURN' AND doc_kind = 'CASH_RETURN' AND amount_delta = -cash_amount AND correction_id IS NULL)
  OR (movement_type = 'SALE_PROCEEDS' AND doc_kind = 'SALE' AND amount_delta = cash_amount AND correction_id IS NULL)
  OR (movement_type = 'PURCHASE_SPEND' AND doc_kind = 'PURCHASE' AND amount_delta = -cash_amount AND correction_id IS NULL)
  OR (movement_type = 'CASH_HANDOVER_REVERSAL' AND doc_kind = 'CASH_HANDOVER' AND amount_delta = -cash_amount AND correction_id IS NOT NULL)
  OR (movement_type = 'CASH_RETURN_REVERSAL' AND doc_kind = 'CASH_RETURN' AND amount_delta = cash_amount AND correction_id IS NOT NULL)
  OR (movement_type = 'SALE_PROCEEDS_REVERSAL' AND doc_kind = 'SALE' AND amount_delta = -cash_amount AND correction_id IS NOT NULL)
  OR (movement_type = 'PURCHASE_SPEND_REVERSAL' AND doc_kind = 'PURCHASE' AND amount_delta = cash_amount AND correction_id IS NOT NULL)))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- 5. the supplier settlement source HOLDING_MARKETER (the existing SG document; the marketer gold movement is tied to it by composite keys)
SET @masgold_sql := IF(NOT EXISTS(SELECT 1 FROM information_schema.STATISTICS WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='supplier_gold_settlements' AND INDEX_NAME='uq_sgs_marketer'), "ALTER TABLE supplier_gold_settlements ADD UNIQUE KEY uq_sgs_marketer (id,holding_party_id,holding_weight_grams)", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(EXISTS(SELECT 1 FROM information_schema.TABLE_CONSTRAINTS tc JOIN information_schema.CHECK_CONSTRAINTS cc ON cc.CONSTRAINT_SCHEMA=tc.CONSTRAINT_SCHEMA AND cc.CONSTRAINT_NAME=tc.CONSTRAINT_NAME WHERE tc.TABLE_SCHEMA=DATABASE() AND tc.TABLE_NAME='supplier_gold_settlements' AND tc.CONSTRAINT_NAME='chk_sgs_source' AND cc.CHECK_CLAUSE NOT LIKE '%HOLDING_MARKETER%'), "ALTER TABLE supplier_gold_settlements DROP CHECK chk_sgs_source", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(NOT EXISTS(SELECT 1 FROM information_schema.TABLE_CONSTRAINTS WHERE CONSTRAINT_SCHEMA=DATABASE() AND TABLE_NAME='supplier_gold_settlements' AND CONSTRAINT_NAME='chk_sgs_source'), "ALTER TABLE supplier_gold_settlements ADD CONSTRAINT chk_sgs_source CHECK (source_kind IN ('SHOP_SCRAP','CUSTODY_GOLD','CUSTODY_LOT','HOLDING_LOT','HOLDING_LOGO','HOLDING_MARKETER'))", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(EXISTS(SELECT 1 FROM information_schema.TABLE_CONSTRAINTS tc JOIN information_schema.CHECK_CONSTRAINTS cc ON cc.CONSTRAINT_SCHEMA=tc.CONSTRAINT_SCHEMA AND cc.CONSTRAINT_NAME=tc.CONSTRAINT_NAME WHERE tc.TABLE_SCHEMA=DATABASE() AND tc.TABLE_NAME='supplier_gold_settlements' AND tc.CONSTRAINT_NAME='chk_sgs_shape' AND cc.CHECK_CLAUSE NOT LIKE '%HOLDING_MARKETER%'), "ALTER TABLE supplier_gold_settlements DROP CHECK chk_sgs_shape", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(NOT EXISTS(SELECT 1 FROM information_schema.TABLE_CONSTRAINTS WHERE CONSTRAINT_SCHEMA=DATABASE() AND TABLE_NAME='supplier_gold_settlements' AND CONSTRAINT_NAME='chk_sgs_shape'), "ALTER TABLE supplier_gold_settlements ADD CONSTRAINT chk_sgs_shape CHECK ((source_kind IN ('SHOP_SCRAP','CUSTODY_GOLD') AND delivered_karat_id IS NOT NULL AND delivered_karat_value IS NOT NULL AND delivered_lot_id IS NULL AND delivered_lot_weight_grams IS NULL AND origin_melt_entry_id IS NULL AND delivered_fineness_permille IS NULL AND holding_party_id IS NULL AND lot_pool_id IS NULL AND logo_pool_id IS NULL AND physical_declaration IS NULL AND holding_weight_grams IS NULL) OR (source_kind='CUSTODY_LOT' AND delivered_karat_id IS NULL AND delivered_karat_value IS NULL AND delivered_lot_id IS NOT NULL AND delivered_lot_weight_grams IS NOT NULL AND delivered_lot_weight_grams=delivered_weight_grams AND origin_melt_entry_id IS NOT NULL AND delivered_fineness_permille IS NOT NULL AND delivered_fineness_permille>0 AND delivered_fineness_permille<=1000 AND holding_party_id IS NULL AND lot_pool_id IS NULL AND logo_pool_id IS NULL AND physical_declaration IS NULL AND holding_weight_grams IS NULL) OR (source_kind='HOLDING_LOGO' AND delivered_karat_id IS NOT NULL AND delivered_karat_value IS NOT NULL AND delivered_lot_id IS NULL AND delivered_lot_weight_grams IS NULL AND origin_melt_entry_id IS NULL AND delivered_fineness_permille IS NULL AND holding_party_id IS NOT NULL AND logo_pool_id IS NOT NULL AND lot_pool_id IS NULL AND physical_declaration IS NULL AND holding_weight_grams IS NOT NULL AND holding_weight_grams=delivered_weight_grams) OR (source_kind='HOLDING_LOT' AND delivered_karat_id IS NULL AND delivered_karat_value IS NULL AND delivered_lot_id IS NOT NULL AND delivered_lot_weight_grams IS NOT NULL AND delivered_weight_grams<=delivered_lot_weight_grams AND origin_melt_entry_id IS NOT NULL AND delivered_fineness_permille IS NOT NULL AND delivered_fineness_permille>0 AND delivered_fineness_permille<=1000 AND holding_party_id IS NOT NULL AND lot_pool_id IS NOT NULL AND logo_pool_id IS NULL AND physical_declaration IS NOT NULL AND physical_declaration IN ('WHOLE_ORIGINAL_BAR','GRAM_ENTITLEMENT','PHYSICALLY_DIVIDED') AND (physical_declaration<>'WHOLE_ORIGINAL_BAR' OR delivered_weight_grams=delivered_lot_weight_grams) AND holding_weight_grams IS NOT NULL AND holding_weight_grams=delivered_weight_grams) OR (source_kind='HOLDING_MARKETER' AND delivered_karat_id IS NOT NULL AND delivered_karat_value IS NOT NULL AND delivered_karat_value=21 AND delivered_lot_id IS NULL AND delivered_lot_weight_grams IS NULL AND origin_melt_entry_id IS NULL AND delivered_fineness_permille IS NULL AND holding_party_id IS NOT NULL AND lot_pool_id IS NULL AND logo_pool_id IS NULL AND physical_declaration IS NULL AND holding_weight_grams IS NOT NULL AND holding_weight_grams=delivered_weight_grams))", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(EXISTS(SELECT 1 FROM information_schema.TABLE_CONSTRAINTS tc JOIN information_schema.CHECK_CONSTRAINTS cc ON cc.CONSTRAINT_SCHEMA=tc.CONSTRAINT_SCHEMA AND cc.CONSTRAINT_NAME=tc.CONSTRAINT_NAME WHERE tc.TABLE_SCHEMA=DATABASE() AND tc.TABLE_NAME='supplier_gold_settlement_legs' AND tc.CONSTRAINT_NAME='chk_sgsl_source' AND cc.CHECK_CLAUSE NOT LIKE '%HOLDING_MARKETER%'), "ALTER TABLE supplier_gold_settlement_legs DROP CHECK chk_sgsl_source", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(NOT EXISTS(SELECT 1 FROM information_schema.TABLE_CONSTRAINTS WHERE CONSTRAINT_SCHEMA=DATABASE() AND TABLE_NAME='supplier_gold_settlement_legs' AND CONSTRAINT_NAME='chk_sgsl_source'), "ALTER TABLE supplier_gold_settlement_legs ADD CONSTRAINT chk_sgsl_source CHECK (source_kind IN ('SHOP_SCRAP','CUSTODY_GOLD','CUSTODY_LOT','HOLDING_LOT','HOLDING_LOGO','HOLDING_MARKETER'))", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

-- 6. the gold ledger (21K physical grams, insert-only, signed)
CREATE TABLE IF NOT EXISTS marketer_gold_movements (
 id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
 party_id INT UNSIGNED NOT NULL,
 karat_value TINYINT UNSIGNED NOT NULL DEFAULT 21,
 document_id BIGINT UNSIGNED DEFAULT NULL,
 doc_kind VARCHAR(14) DEFAULT NULL,
 settlement_id BIGINT UNSIGNED DEFAULT NULL,
 movement_type VARCHAR(24) NOT NULL,
 weight_grams DECIMAL(12,3) NOT NULL,
 weight_delta DECIMAL(12,3) NOT NULL,
 correction_id BIGINT UNSIGNED DEFAULT NULL,
 created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
 PRIMARY KEY (id),
 UNIQUE KEY uq_mkgm_document_type (document_id, movement_type),
 UNIQUE KEY uq_mkgm_settlement_type (settlement_id, movement_type),
 UNIQUE KEY uq_mkgm_correction (correction_id, movement_type),
 KEY idx_mkgm_party (party_id, id),
 CONSTRAINT fk_mkgm_document FOREIGN KEY (document_id, doc_kind, party_id, weight_grams) REFERENCES marketer_documents(id, doc_kind, party_id, weight_grams),
 CONSTRAINT fk_mkgm_settlement FOREIGN KEY (settlement_id, party_id, weight_grams) REFERENCES supplier_gold_settlements(id, holding_party_id, holding_weight_grams),
 CONSTRAINT fk_mkgm_correction FOREIGN KEY (correction_id) REFERENCES transaction_corrections(id),
 CONSTRAINT chk_mkgm_karat CHECK (karat_value = 21),
 CONSTRAINT chk_mkgm_shape CHECK (weight_grams > 0 AND (
  (document_id IS NOT NULL AND settlement_id IS NULL AND (
    (movement_type = 'PURCHASE_IN' AND doc_kind = 'PURCHASE' AND weight_delta = weight_grams AND correction_id IS NULL)
    OR (movement_type = 'SALE_OUT' AND doc_kind = 'SALE' AND weight_delta = -weight_grams AND correction_id IS NULL)
    OR (movement_type = 'GOLD_RETURN' AND doc_kind = 'GOLD_RETURN' AND weight_delta = -weight_grams AND correction_id IS NULL)
    OR (movement_type = 'PURCHASE_IN_REVERSAL' AND doc_kind = 'PURCHASE' AND weight_delta = -weight_grams AND correction_id IS NOT NULL)
    OR (movement_type = 'SALE_OUT_REVERSAL' AND doc_kind = 'SALE' AND weight_delta = weight_grams AND correction_id IS NOT NULL)
    OR (movement_type = 'GOLD_RETURN_REVERSAL' AND doc_kind = 'GOLD_RETURN' AND weight_delta = weight_grams AND correction_id IS NOT NULL)))
  OR (document_id IS NULL AND doc_kind IS NULL AND settlement_id IS NOT NULL AND (
    (movement_type = 'SETTLEMENT' AND weight_delta = -weight_grams AND correction_id IS NULL)
    OR (movement_type = 'SETTLEMENT_REVERSAL' AND weight_delta = weight_grams AND correction_id IS NOT NULL)))))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

INSERT INTO schema_migrations(version,name) VALUES (55,'Marketer custody account: cash and 21K gold ledgers, MH/MR/MB/MS documents and the HOLDING_MARKETER supplier settlement source')
 ON DUPLICATE KEY UPDATE name=VALUES(name);
