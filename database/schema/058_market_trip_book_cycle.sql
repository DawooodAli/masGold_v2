-- Market-trip BOOK Phase 2: the market cycle of a BOOK trip. Prepared; NOT applied to any database by the task that wrote it. The application paths that use it stay
-- inside the disabled BOOK mode (no screen creates a BOOK trip in production; `book_creation_enabled` and `reconciliation_enabled` stay False).
--   new entry kinds  BK_ISS_STK (identified assayed-return stock of the SHOP -> a BOOK trip, partial or whole), BK_RECEIVE (a marketer's 21K entitlement -> a BOOK trip),
--                    BK_DLV_SUP (BOOK trip -> a SUPPLIER's gold account, in the supplier's own karat), BK_DLV_MKT (BOOK trip -> a marketer's 21K entitlement). BOOK only.
--   book lines       `posting_basis` INDEPENDENT (posted = round3 of the line's exact analytical value, |difference| <= 0.0005, as before) or ALLOCATED (BK_ISS_STK only: the
--                    posted figure is the share of the stock item's STORED posted amount allocated to the issued grams; it is never re-rounded and its difference may
--                    exceed 0.0005; a positive weight may carry a zero posted share). A BK_ISS_STK line names the stock item it consumed (stock_item_entry_id, line_no).
--   supplier         source kind TRIP_BOOK of the SG document (delivered master karat OR assay, never both; no Scrap leg) + market_trip_book_supplier_links (entry <-> SG).
--   marketer         marketer_gold_movements gains a trip-entry leg (TRIP_BOOK_IN / TRIP_BOOK_OUT and their reversal compensations), tied to the entry and its kind.
--   reconciliation   the record may carry the v2 source / snapshot formats (used only for trips that contain a Phase 2 kind); v1 records stay valid unchanged.
-- No existing row is rewritten. The CHECK expressions have NOT been executed on a MySQL server: that is a later verification gate. Every step is guarded (re-run safe).
SET @masgold_sql = IF(EXISTS(SELECT 1 FROM schema_migrations WHERE version=57),
 'DO 0', 'SELECT * FROM MIGRATION_058_REFUSED_missing_057');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

-- 1. the entry kinds (BOOK only)
SET @masgold_sql := IF(EXISTS(SELECT 1 FROM information_schema.CHECK_CONSTRAINTS WHERE CONSTRAINT_SCHEMA=DATABASE() AND CONSTRAINT_NAME='chk_mte_kind' AND CHECK_CLAUSE NOT LIKE '%BK_DLV_MKT%'),
 "ALTER TABLE market_trip_entries DROP CHECK chk_mte_kind", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;
SET @masgold_sql := IF(NOT EXISTS(SELECT 1 FROM information_schema.TABLE_CONSTRAINTS WHERE CONSTRAINT_SCHEMA=DATABASE() AND TABLE_NAME='market_trip_entries' AND CONSTRAINT_NAME='chk_mte_kind'),
 "ALTER TABLE market_trip_entries ADD CONSTRAINT chk_mte_kind CHECK (entry_kind IN ('ISSUE','RETURN','MELT','LOT_RETURN','LOT_ISSUE','HEAT','SUPPLIER_PAY','GOLD_SETTLE','LOT_SETTLE','LOT_HOLD','HOLD_RETURN','LOGO_HOLD','HOLD_BACK','GOLD_SALE','GOLD_BUY','BK_ISSUE','BK_BUY','BK_SELL','BK_RETURN','BK_RECON','BK_ISS_STK','BK_DLV_SUP','BK_DLV_MKT','BK_RECEIVE'))", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;
SET @masgold_sql := IF(EXISTS(SELECT 1 FROM information_schema.CHECK_CONSTRAINTS WHERE CONSTRAINT_SCHEMA=DATABASE() AND CONSTRAINT_NAME='chk_mte_mode_kind' AND CHECK_CLAUSE NOT LIKE '%BK_DLV_MKT%'),
 "ALTER TABLE market_trip_entries DROP CHECK chk_mte_mode_kind", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;
SET @masgold_sql := IF(NOT EXISTS(SELECT 1 FROM information_schema.TABLE_CONSTRAINTS WHERE CONSTRAINT_SCHEMA=DATABASE() AND TABLE_NAME='market_trip_entries' AND CONSTRAINT_NAME='chk_mte_mode_kind'),
 "ALTER TABLE market_trip_entries ADD CONSTRAINT chk_mte_mode_kind CHECK ((trip_mode = 'LEGACY' AND entry_kind IN ('ISSUE','RETURN','MELT','LOT_RETURN','LOT_ISSUE','HEAT','SUPPLIER_PAY','GOLD_SETTLE','LOT_SETTLE','LOT_HOLD','HOLD_RETURN','LOGO_HOLD','HOLD_BACK','GOLD_SALE','GOLD_BUY')) OR (trip_mode = 'BOOK' AND entry_kind IN ('ISSUE','RETURN','SUPPLIER_PAY','BK_ISSUE','BK_BUY','BK_SELL','BK_RETURN','BK_RECON','BK_ISS_STK','BK_DLV_SUP','BK_DLV_MKT','BK_RECEIVE')))", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

-- 2. book lines: posting basis, the consumed stock item, the widened kind / posted / leg rules
SET @masgold_sql := IF(NOT EXISTS(SELECT 1 FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='market_trip_book_lines' AND COLUMN_NAME='posting_basis'),
 "ALTER TABLE market_trip_book_lines ADD COLUMN posting_basis VARCHAR(11) NOT NULL DEFAULT 'INDEPENDENT' AFTER signed_eq21_posted", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;
SET @masgold_sql := IF(NOT EXISTS(SELECT 1 FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='market_trip_book_lines' AND COLUMN_NAME='stock_item_entry_id'),
 "ALTER TABLE market_trip_book_lines ADD COLUMN stock_item_entry_id BIGINT UNSIGNED DEFAULT NULL AFTER scrap_movement_id, ADD COLUMN stock_item_line_no SMALLINT UNSIGNED DEFAULT NULL AFTER stock_item_entry_id", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;
SET @masgold_sql := IF(NOT EXISTS(SELECT 1 FROM information_schema.STATISTICS WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='market_trip_book_lines' AND INDEX_NAME='uq_mtbl_stock'),
 "ALTER TABLE market_trip_book_lines ADD UNIQUE KEY uq_mtbl_stock (entry_id, stock_item_entry_id, stock_item_line_no)", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;
SET @masgold_sql := IF(NOT EXISTS(SELECT 1 FROM information_schema.TABLE_CONSTRAINTS WHERE CONSTRAINT_SCHEMA=DATABASE() AND TABLE_NAME='market_trip_book_lines' AND CONSTRAINT_NAME='fk_mtbl_stock_item'),
 "ALTER TABLE market_trip_book_lines ADD CONSTRAINT fk_mtbl_stock_item FOREIGN KEY (stock_item_entry_id, stock_item_line_no) REFERENCES market_trip_book_lines(entry_id, line_no)", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;
SET @masgold_sql := IF(EXISTS(SELECT 1 FROM information_schema.CHECK_CONSTRAINTS WHERE CONSTRAINT_SCHEMA=DATABASE() AND CONSTRAINT_NAME='chk_mtbl_kind' AND CHECK_CLAUSE NOT LIKE '%BK_ISS_STK%'),
 "ALTER TABLE market_trip_book_lines DROP CHECK chk_mtbl_kind", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;
SET @masgold_sql := IF(NOT EXISTS(SELECT 1 FROM information_schema.TABLE_CONSTRAINTS WHERE CONSTRAINT_SCHEMA=DATABASE() AND TABLE_NAME='market_trip_book_lines' AND CONSTRAINT_NAME='chk_mtbl_kind'),
 "ALTER TABLE market_trip_book_lines ADD CONSTRAINT chk_mtbl_kind CHECK ((entry_kind IN ('BK_ISSUE','BK_BUY','BK_ISS_STK','BK_RECEIVE') AND direction = 'IN') OR (entry_kind IN ('BK_SELL','BK_RETURN','BK_DLV_SUP','BK_DLV_MKT') AND direction = 'OUT'))", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;
SET @masgold_sql := IF(EXISTS(SELECT 1 FROM information_schema.CHECK_CONSTRAINTS WHERE CONSTRAINT_SCHEMA=DATABASE() AND CONSTRAINT_NAME='chk_mtbl_posted' AND CHECK_CLAUSE NOT LIKE '%ALLOCATED%'),
 "ALTER TABLE market_trip_book_lines DROP CHECK chk_mtbl_posted", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;
SET @masgold_sql := IF(NOT EXISTS(SELECT 1 FROM information_schema.TABLE_CONSTRAINTS WHERE CONSTRAINT_SCHEMA=DATABASE() AND TABLE_NAME='market_trip_book_lines' AND CONSTRAINT_NAME='chk_mtbl_posted'),
 "ALTER TABLE market_trip_book_lines ADD CONSTRAINT chk_mtbl_posted CHECK (eq21_posted >= 0 AND eq21_analytical >= 0 AND ABS((eq21_posted - eq21_analytical) - posting_difference) <= 0.000001 AND ((direction = 'IN' AND signed_eq21_posted = eq21_posted) OR (direction = 'OUT' AND signed_eq21_posted = -eq21_posted)) AND ((posting_basis = 'INDEPENDENT' AND ABS(posting_difference) <= 0.0005) OR (posting_basis = 'ALLOCATED' AND entry_kind = 'BK_ISS_STK')))", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;
SET @masgold_sql := IF(EXISTS(SELECT 1 FROM information_schema.CHECK_CONSTRAINTS WHERE CONSTRAINT_SCHEMA=DATABASE() AND CONSTRAINT_NAME='chk_mtbl_leg' AND CHECK_CLAUSE NOT LIKE '%BK_ISS_STK%'),
 "ALTER TABLE market_trip_book_lines DROP CHECK chk_mtbl_leg", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;
SET @masgold_sql := IF(NOT EXISTS(SELECT 1 FROM information_schema.TABLE_CONSTRAINTS WHERE CONSTRAINT_SCHEMA=DATABASE() AND TABLE_NAME='market_trip_book_lines' AND CONSTRAINT_NAME='chk_mtbl_leg'),
 "ALTER TABLE market_trip_book_lines ADD CONSTRAINT chk_mtbl_leg CHECK ((entry_kind = 'BK_ISSUE' AND basis = 'MASTER_KARAT' AND scrap_movement_id IS NOT NULL AND line_price IS NULL) OR (entry_kind = 'BK_RETURN' AND basis = 'MASTER_KARAT' AND scrap_movement_id IS NOT NULL AND line_price IS NULL) OR (entry_kind = 'BK_RETURN' AND basis = 'FINENESS' AND scrap_movement_id IS NULL AND line_price IS NULL) OR (entry_kind IN ('BK_BUY','BK_SELL') AND scrap_movement_id IS NULL AND line_price IS NOT NULL AND line_price > 0) OR (entry_kind IN ('BK_ISS_STK','BK_DLV_SUP','BK_DLV_MKT','BK_RECEIVE') AND scrap_movement_id IS NULL AND line_price IS NULL))", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;
SET @masgold_sql := IF(NOT EXISTS(SELECT 1 FROM information_schema.TABLE_CONSTRAINTS WHERE CONSTRAINT_SCHEMA=DATABASE() AND TABLE_NAME='market_trip_book_lines' AND CONSTRAINT_NAME='chk_mtbl_basis_kind'),
 "ALTER TABLE market_trip_book_lines ADD CONSTRAINT chk_mtbl_basis_kind CHECK (posting_basis IN ('INDEPENDENT','ALLOCATED'))", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;
SET @masgold_sql := IF(NOT EXISTS(SELECT 1 FROM information_schema.TABLE_CONSTRAINTS WHERE CONSTRAINT_SCHEMA=DATABASE() AND TABLE_NAME='market_trip_book_lines' AND CONSTRAINT_NAME='chk_mtbl_stock'),
 "ALTER TABLE market_trip_book_lines ADD CONSTRAINT chk_mtbl_stock CHECK ((entry_kind = 'BK_ISS_STK' AND basis = 'FINENESS' AND stock_item_entry_id IS NOT NULL AND stock_item_line_no IS NOT NULL) OR (entry_kind <> 'BK_ISS_STK' AND stock_item_entry_id IS NULL AND stock_item_line_no IS NULL AND posting_basis = 'INDEPENDENT'))", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

-- 3. supplier gold settlement from a BOOK trip (source TRIP_BOOK; never a Scrap leg)
SET @masgold_sql := IF(EXISTS(SELECT 1 FROM information_schema.CHECK_CONSTRAINTS WHERE CONSTRAINT_SCHEMA=DATABASE() AND CONSTRAINT_NAME='chk_sgs_source' AND CHECK_CLAUSE NOT LIKE '%TRIP_BOOK%'),
 "ALTER TABLE supplier_gold_settlements DROP CHECK chk_sgs_source", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;
SET @masgold_sql := IF(NOT EXISTS(SELECT 1 FROM information_schema.TABLE_CONSTRAINTS WHERE CONSTRAINT_SCHEMA=DATABASE() AND TABLE_NAME='supplier_gold_settlements' AND CONSTRAINT_NAME='chk_sgs_source'),
 "ALTER TABLE supplier_gold_settlements ADD CONSTRAINT chk_sgs_source CHECK (source_kind IN ('SHOP_SCRAP','CUSTODY_GOLD','CUSTODY_LOT','HOLDING_LOT','HOLDING_LOGO','HOLDING_MKT','TRIP_BOOK'))", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;
SET @masgold_sql := IF(EXISTS(SELECT 1 FROM information_schema.CHECK_CONSTRAINTS WHERE CONSTRAINT_SCHEMA=DATABASE() AND CONSTRAINT_NAME='chk_sgs_shape' AND CHECK_CLAUSE NOT LIKE '%TRIP_BOOK%'),
 "ALTER TABLE supplier_gold_settlements DROP CHECK chk_sgs_shape", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;
SET @masgold_sql := IF(NOT EXISTS(SELECT 1 FROM information_schema.TABLE_CONSTRAINTS WHERE CONSTRAINT_SCHEMA=DATABASE() AND TABLE_NAME='supplier_gold_settlements' AND CONSTRAINT_NAME='chk_sgs_shape'),
 "ALTER TABLE supplier_gold_settlements ADD CONSTRAINT chk_sgs_shape CHECK ((source_kind IN ('SHOP_SCRAP','CUSTODY_GOLD') AND delivered_karat_id IS NOT NULL AND delivered_karat_value IS NOT NULL AND delivered_lot_id IS NULL AND delivered_lot_weight_grams IS NULL AND origin_melt_entry_id IS NULL AND delivered_fineness_permille IS NULL AND holding_party_id IS NULL AND lot_pool_id IS NULL AND logo_pool_id IS NULL AND physical_declaration IS NULL AND holding_weight_grams IS NULL) OR (source_kind='CUSTODY_LOT' AND delivered_karat_id IS NULL AND delivered_karat_value IS NULL AND delivered_lot_id IS NOT NULL AND delivered_lot_weight_grams IS NOT NULL AND delivered_lot_weight_grams=delivered_weight_grams AND origin_melt_entry_id IS NOT NULL AND delivered_fineness_permille IS NOT NULL AND delivered_fineness_permille>0 AND delivered_fineness_permille<=1000 AND holding_party_id IS NULL AND lot_pool_id IS NULL AND logo_pool_id IS NULL AND physical_declaration IS NULL AND holding_weight_grams IS NULL) OR (source_kind='HOLDING_LOGO' AND delivered_karat_id IS NOT NULL AND delivered_karat_value IS NOT NULL AND delivered_lot_id IS NULL AND delivered_lot_weight_grams IS NULL AND origin_melt_entry_id IS NULL AND delivered_fineness_permille IS NULL AND holding_party_id IS NOT NULL AND logo_pool_id IS NOT NULL AND lot_pool_id IS NULL AND physical_declaration IS NULL AND holding_weight_grams IS NOT NULL AND holding_weight_grams=delivered_weight_grams) OR (source_kind='HOLDING_LOT' AND delivered_karat_id IS NULL AND delivered_karat_value IS NULL AND delivered_lot_id IS NOT NULL AND delivered_lot_weight_grams IS NOT NULL AND delivered_weight_grams<=delivered_lot_weight_grams AND origin_melt_entry_id IS NOT NULL AND delivered_fineness_permille IS NOT NULL AND delivered_fineness_permille>0 AND delivered_fineness_permille<=1000 AND holding_party_id IS NOT NULL AND lot_pool_id IS NOT NULL AND logo_pool_id IS NULL AND physical_declaration IS NOT NULL AND physical_declaration IN ('WHOLE_ORIGINAL_BAR','GRAM_ENTITLEMENT','PHYSICALLY_DIVIDED') AND (physical_declaration<>'WHOLE_ORIGINAL_BAR' OR delivered_weight_grams=delivered_lot_weight_grams) AND holding_weight_grams IS NOT NULL AND holding_weight_grams=delivered_weight_grams) OR (source_kind='HOLDING_MKT' AND delivered_karat_id IS NOT NULL AND delivered_karat_value IS NOT NULL AND delivered_karat_value=21 AND delivered_lot_id IS NULL AND delivered_lot_weight_grams IS NULL AND origin_melt_entry_id IS NULL AND delivered_fineness_permille IS NULL AND holding_party_id IS NOT NULL AND lot_pool_id IS NULL AND logo_pool_id IS NULL AND physical_declaration IS NULL AND holding_weight_grams IS NOT NULL AND holding_weight_grams=delivered_weight_grams) OR (source_kind='TRIP_BOOK' AND delivered_lot_id IS NULL AND delivered_lot_weight_grams IS NULL AND origin_melt_entry_id IS NULL AND holding_party_id IS NULL AND lot_pool_id IS NULL AND logo_pool_id IS NULL AND physical_declaration IS NULL AND holding_weight_grams IS NULL AND ((delivered_karat_id IS NOT NULL AND delivered_karat_value IS NOT NULL AND delivered_fineness_permille IS NULL) OR (delivered_karat_id IS NULL AND delivered_karat_value IS NULL AND delivered_fineness_permille IS NOT NULL AND delivered_fineness_permille>0 AND delivered_fineness_permille<=1000))))", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;
SET @masgold_sql := IF(EXISTS(SELECT 1 FROM information_schema.CHECK_CONSTRAINTS WHERE CONSTRAINT_SCHEMA=DATABASE() AND CONSTRAINT_NAME='chk_sgsl_source' AND CHECK_CLAUSE NOT LIKE '%TRIP_BOOK%'),
 "ALTER TABLE supplier_gold_settlement_legs DROP CHECK chk_sgsl_source", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;
SET @masgold_sql := IF(NOT EXISTS(SELECT 1 FROM information_schema.TABLE_CONSTRAINTS WHERE CONSTRAINT_SCHEMA=DATABASE() AND TABLE_NAME='supplier_gold_settlement_legs' AND CONSTRAINT_NAME='chk_sgsl_source'),
 "ALTER TABLE supplier_gold_settlement_legs ADD CONSTRAINT chk_sgsl_source CHECK (source_kind IN ('SHOP_SCRAP','CUSTODY_GOLD','CUSTODY_LOT','HOLDING_LOT','HOLDING_LOGO','HOLDING_MKT','TRIP_BOOK'))", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;
SET @masgold_sql := IF(NOT EXISTS(SELECT 1 FROM information_schema.STATISTICS WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='supplier_gold_settlements' AND INDEX_NAME='uq_sgs_book_link'),
 "ALTER TABLE supplier_gold_settlements ADD UNIQUE KEY uq_sgs_book_link (id, source_kind, supplier_id, delivered_weight_grams)", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;
CREATE TABLE IF NOT EXISTS market_trip_book_supplier_links (
 entry_id BIGINT UNSIGNED NOT NULL,
 entry_kind VARCHAR(12) NOT NULL DEFAULT 'BK_DLV_SUP',
 trip_id BIGINT UNSIGNED NOT NULL,
 settlement_id BIGINT UNSIGNED NOT NULL,
 source_kind VARCHAR(12) NOT NULL DEFAULT 'TRIP_BOOK',
 supplier_id INT UNSIGNED NOT NULL,
 weight_grams DECIMAL(14,3) NOT NULL,
 created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
 PRIMARY KEY (entry_id),
 UNIQUE KEY uq_mtbsl_settlement (settlement_id),
 KEY idx_mtbsl_supplier (supplier_id),
 CONSTRAINT fk_mtbsl_entry FOREIGN KEY (entry_id, entry_kind, trip_id) REFERENCES market_trip_entries(id, entry_kind, trip_id),
 CONSTRAINT fk_mtbsl_settlement FOREIGN KEY (settlement_id, source_kind, supplier_id, weight_grams) REFERENCES supplier_gold_settlements(id, source_kind, supplier_id, delivered_weight_grams),
 CONSTRAINT chk_mtbsl_kind CHECK (entry_kind = 'BK_DLV_SUP' AND source_kind = 'TRIP_BOOK' AND weight_grams > 0)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- 4. the marketer's 21K entitlement: a leg tied to the BOOK trip entry
SET @masgold_sql := IF(NOT EXISTS(SELECT 1 FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='marketer_gold_movements' AND COLUMN_NAME='trip_entry_id'),
 "ALTER TABLE marketer_gold_movements ADD COLUMN trip_entry_id BIGINT UNSIGNED DEFAULT NULL AFTER settlement_id, ADD COLUMN trip_entry_kind VARCHAR(12) DEFAULT NULL AFTER trip_entry_id", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;
SET @masgold_sql := IF(NOT EXISTS(SELECT 1 FROM information_schema.STATISTICS WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='marketer_gold_movements' AND INDEX_NAME='uq_mkgm_trip_entry_type'),
 "ALTER TABLE marketer_gold_movements ADD UNIQUE KEY uq_mkgm_trip_entry_type (trip_entry_id, movement_type)", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;
SET @masgold_sql := IF(NOT EXISTS(SELECT 1 FROM information_schema.TABLE_CONSTRAINTS WHERE CONSTRAINT_SCHEMA=DATABASE() AND TABLE_NAME='marketer_gold_movements' AND CONSTRAINT_NAME='fk_mkgm_trip_entry'),
 "ALTER TABLE marketer_gold_movements ADD CONSTRAINT fk_mkgm_trip_entry FOREIGN KEY (trip_entry_id, trip_entry_kind) REFERENCES market_trip_entries(id, entry_kind)", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;
SET @masgold_sql := IF(EXISTS(SELECT 1 FROM information_schema.CHECK_CONSTRAINTS WHERE CONSTRAINT_SCHEMA=DATABASE() AND CONSTRAINT_NAME='chk_mkgm_shape' AND CHECK_CLAUSE NOT LIKE '%TRIP_BOOK_IN%'),
 "ALTER TABLE marketer_gold_movements DROP CHECK chk_mkgm_shape", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;
SET @masgold_sql := IF(NOT EXISTS(SELECT 1 FROM information_schema.TABLE_CONSTRAINTS WHERE CONSTRAINT_SCHEMA=DATABASE() AND TABLE_NAME='marketer_gold_movements' AND CONSTRAINT_NAME='chk_mkgm_shape'),
 "ALTER TABLE marketer_gold_movements ADD CONSTRAINT chk_mkgm_shape CHECK (weight_grams > 0 AND ( (document_id IS NOT NULL AND settlement_id IS NULL AND trip_entry_id IS NULL AND ( (movement_type = 'PURCHASE_IN' AND doc_kind = 'PURCHASE' AND weight_delta = weight_grams AND correction_id IS NULL) OR (movement_type = 'SALE_OUT' AND doc_kind = 'SALE' AND weight_delta = -weight_grams AND correction_id IS NULL) OR (movement_type = 'GOLD_RETURN' AND doc_kind = 'GOLD_RETURN' AND weight_delta = -weight_grams AND correction_id IS NULL) OR (movement_type = 'PURCHASE_IN_REVERSAL' AND doc_kind = 'PURCHASE' AND weight_delta = -weight_grams AND correction_id IS NOT NULL) OR (movement_type = 'SALE_OUT_REVERSAL' AND doc_kind = 'SALE' AND weight_delta = weight_grams AND correction_id IS NOT NULL) OR (movement_type = 'GOLD_RETURN_REVERSAL' AND doc_kind = 'GOLD_RETURN' AND weight_delta = weight_grams AND correction_id IS NOT NULL))) OR (document_id IS NULL AND doc_kind IS NULL AND settlement_id IS NOT NULL AND trip_entry_id IS NULL AND ( (movement_type = 'SETTLEMENT' AND weight_delta = -weight_grams AND correction_id IS NULL) OR (movement_type = 'SETTLEMENT_REVERSAL' AND weight_delta = weight_grams AND correction_id IS NOT NULL))) OR (document_id IS NULL AND doc_kind IS NULL AND settlement_id IS NULL AND trip_entry_id IS NOT NULL AND ((movement_type = 'TRIP_BOOK_IN' AND trip_entry_kind = 'BK_DLV_MKT' AND weight_delta = weight_grams AND correction_id IS NULL) OR (movement_type = 'TRIP_BOOK_IN_REVERSAL' AND trip_entry_kind = 'BK_DLV_MKT' AND weight_delta = -weight_grams AND correction_id IS NOT NULL) OR (movement_type = 'TRIP_BOOK_OUT' AND trip_entry_kind = 'BK_RECEIVE' AND weight_delta = -weight_grams AND correction_id IS NULL) OR (movement_type = 'TRIP_BOOK_OUT_REVERSAL' AND trip_entry_kind = 'BK_RECEIVE' AND weight_delta = weight_grams AND correction_id IS NOT NULL)))))", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

-- 5. reconciliation records may use the v2 source / snapshot formats (only for trips with a Phase 2 kind)
SET @masgold_sql := IF(EXISTS(SELECT 1 FROM information_schema.CHECK_CONSTRAINTS WHERE CONSTRAINT_SCHEMA=DATABASE() AND CONSTRAINT_NAME='chk_mtr_head' AND CHECK_CLAUSE NOT LIKE '%source-v2%'),
 "ALTER TABLE market_trip_reconciliations DROP CHECK chk_mtr_head", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;
SET @masgold_sql := IF(NOT EXISTS(SELECT 1 FROM information_schema.TABLE_CONSTRAINTS WHERE CONSTRAINT_SCHEMA=DATABASE() AND TABLE_NAME='market_trip_reconciliations' AND CONSTRAINT_NAME='chk_mtr_head'),
 "ALTER TABLE market_trip_reconciliations ADD CONSTRAINT chk_mtr_head CHECK (entry_kind = 'BK_RECON' AND trip_mode = 'BOOK' AND generation >= 1 AND algorithm = 'book-recon-v1' AND source_algorithm IN ('book-recon-source-v1','book-recon-source-v2') AND snapshot_format IN ('book-recon-snapshot-v1','book-recon-snapshot-v2') AND unit_denominator = 26250000 AND JSON_TYPE(snapshot) = 'OBJECT')", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

INSERT INTO schema_migrations(version,name) VALUES (58,'Market-trip BOOK Phase 2: stock reissue, supplier and marketer delivery and marketer receipt kinds')
 ON DUPLICATE KEY UPDATE name=VALUES(name);
