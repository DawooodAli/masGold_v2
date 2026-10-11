-- Supplier GOLD settlement from market custody (تسوية ذهب من العهدة, SGxxxxxx + MTxxxxxx-n): the OWNER delivers LOGO gold that is already in an
-- OPEN market custody to a supplier against its gold account.
--   GOLD_SETTLE   a new custody entry kind. It consumes outstanding custody gold: custody gold of (karat, designation) = issued - returned -
--                 melted + effective heating differences - delivered to suppliers, over the entries that were not reversed. NO second Scrap
--                 movement exists (the grams left the shop once, at issue), and no cash, making, bank or lot movement either.
--   SG document   the existing immutable supplier settlement document with the new source CUSTODY_GOLD: delivered physical grams and master
--                 karat, the supplier's karat, the posted credit, the analytical value and the rounding difference (the 045 columns, unchanged).
--   link          market_trip_gold_settlements: entry -> SG document with the consumed source line (karat + designation). The database keeps the
--                 link EQUAL to the document in source, supplier, delivered karat and delivered weight (one composite foreign key), so the
--                 grams taken from custody can never differ from the grams the document delivered.
--   legs          every SG leg now states its source kind and the database refuses a SCRAP leg on any source but SHOP_SCRAP: a custody
--                 settlement can only ever carry its one GOLD leg (the structural guard against a second Scrap deduction).
-- The reversed state of the entry, the document and the supplier ledger derives from the SAME correction record (the full reversal of the
-- custody entry); the direct 045 reversal path refuses a CUSTODY_GOLD document. Only LOGO gold is settleable in this slice (a scope check that a
-- later migration may widen; MELT and unclassified gold are NOT permanently prohibited). Every step is guarded, so a re-run changes nothing.
SET @masgold_sql = IF(EXISTS(SELECT 1 FROM schema_migrations WHERE version=45),
 'DO 0', 'SELECT * FROM MIGRATION_046_REFUSED_missing_045');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

-- 1. the GOLD_SETTLE entry kind
SET @masgold_sql := IF(EXISTS (SELECT 1 FROM information_schema.CHECK_CONSTRAINTS WHERE CONSTRAINT_SCHEMA = DATABASE()
 AND CONSTRAINT_NAME = 'chk_mte_kind' AND CHECK_CLAUSE NOT LIKE '%GOLD_SETTLE%'),
 "ALTER TABLE market_trip_entries DROP CHECK chk_mte_kind", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(NOT EXISTS (SELECT 1 FROM information_schema.TABLE_CONSTRAINTS WHERE CONSTRAINT_SCHEMA = DATABASE()
 AND TABLE_NAME = 'market_trip_entries' AND CONSTRAINT_NAME = 'chk_mte_kind'),
 "ALTER TABLE market_trip_entries ADD CONSTRAINT chk_mte_kind CHECK (entry_kind IN ('ISSUE','RETURN','MELT','LOT_RETURN','LOT_ISSUE','HEAT','SUPPLIER_PAY','GOLD_SETTLE'))", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

-- 2. the SG document: the CUSTODY_GOLD source, and the key the links and legs reference
SET @masgold_sql := IF(EXISTS (SELECT 1 FROM information_schema.CHECK_CONSTRAINTS WHERE CONSTRAINT_SCHEMA = DATABASE()
 AND CONSTRAINT_NAME = 'chk_sgs_source' AND CHECK_CLAUSE NOT LIKE '%CUSTODY_GOLD%'),
 "ALTER TABLE supplier_gold_settlements DROP CHECK chk_sgs_source", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(NOT EXISTS (SELECT 1 FROM information_schema.TABLE_CONSTRAINTS WHERE CONSTRAINT_SCHEMA = DATABASE()
 AND TABLE_NAME = 'supplier_gold_settlements' AND CONSTRAINT_NAME = 'chk_sgs_source'),
 "ALTER TABLE supplier_gold_settlements ADD CONSTRAINT chk_sgs_source CHECK (source_kind IN ('SHOP_SCRAP','CUSTODY_GOLD'))", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(NOT EXISTS (SELECT 1 FROM information_schema.STATISTICS WHERE TABLE_SCHEMA = DATABASE()
 AND TABLE_NAME = 'supplier_gold_settlements' AND INDEX_NAME = 'uq_sgs_link'),
 "ALTER TABLE supplier_gold_settlements ADD UNIQUE KEY uq_sgs_link (id, source_kind, supplier_id, delivered_karat_id, delivered_weight_grams)", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

-- 3. the legs state their source kind (every existing leg belongs to a SHOP_SCRAP document); a SCRAP leg exists on SHOP_SCRAP only
SET @masgold_sql := IF(NOT EXISTS (SELECT 1 FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = DATABASE()
 AND TABLE_NAME = 'supplier_gold_settlement_legs' AND COLUMN_NAME = 'source_kind'),
 "ALTER TABLE supplier_gold_settlement_legs ADD COLUMN source_kind VARCHAR(12) NOT NULL DEFAULT 'SHOP_SCRAP' AFTER settlement_id", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(EXISTS (SELECT 1 FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = DATABASE()
 AND TABLE_NAME = 'supplier_gold_settlement_legs' AND COLUMN_NAME = 'source_kind' AND COLUMN_DEFAULT IS NOT NULL),
 "ALTER TABLE supplier_gold_settlement_legs ALTER COLUMN source_kind DROP DEFAULT", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(NOT EXISTS (SELECT 1 FROM information_schema.TABLE_CONSTRAINTS WHERE CONSTRAINT_SCHEMA = DATABASE()
 AND TABLE_NAME = 'supplier_gold_settlement_legs' AND CONSTRAINT_NAME = 'chk_sgsl_source'),
 "ALTER TABLE supplier_gold_settlement_legs ADD CONSTRAINT chk_sgsl_source CHECK (source_kind IN ('SHOP_SCRAP','CUSTODY_GOLD'))", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(NOT EXISTS (SELECT 1 FROM information_schema.TABLE_CONSTRAINTS WHERE CONSTRAINT_SCHEMA = DATABASE()
 AND TABLE_NAME = 'supplier_gold_settlement_legs' AND CONSTRAINT_NAME = 'chk_sgsl_scrap_only'),
 "ALTER TABLE supplier_gold_settlement_legs ADD CONSTRAINT chk_sgsl_scrap_only CHECK (leg_kind <> 'SCRAP' OR source_kind = 'SHOP_SCRAP')", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(NOT EXISTS (SELECT 1 FROM information_schema.STATISTICS WHERE TABLE_SCHEMA = DATABASE()
 AND TABLE_NAME = 'supplier_gold_settlement_legs' AND INDEX_NAME = 'idx_sgsl_source'),
 "ALTER TABLE supplier_gold_settlement_legs ADD KEY idx_sgsl_source (settlement_id, source_kind)", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(NOT EXISTS (SELECT 1 FROM information_schema.TABLE_CONSTRAINTS WHERE CONSTRAINT_SCHEMA = DATABASE()
 AND TABLE_NAME = 'supplier_gold_settlement_legs' AND CONSTRAINT_NAME = 'fk_sgsl_source'),
 "ALTER TABLE supplier_gold_settlement_legs ADD CONSTRAINT fk_sgsl_source FOREIGN KEY (settlement_id, source_kind) REFERENCES supplier_gold_settlements(id, source_kind)", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

-- 4. the immutable link between the custody entry and the SG document (equal to the document by foreign key; the grams consumed from custody)
CREATE TABLE IF NOT EXISTS market_trip_gold_settlements (
 entry_id BIGINT UNSIGNED NOT NULL,
 settlement_id BIGINT UNSIGNED NOT NULL,
 source_kind VARCHAR(12) NOT NULL DEFAULT 'CUSTODY_GOLD',
 supplier_id INT UNSIGNED NOT NULL,
 karat_id TINYINT UNSIGNED NOT NULL,
 designation VARCHAR(12) NOT NULL,
 weight_grams DECIMAL(14,3) NOT NULL,
 created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
 PRIMARY KEY (entry_id),
 UNIQUE KEY uq_mtgs_settlement (settlement_id),
 KEY idx_mtgs_header (settlement_id, source_kind, supplier_id, karat_id, weight_grams),
 KEY idx_mtgs_supplier (supplier_id),
 KEY idx_mtgs_source (karat_id, designation),
 CONSTRAINT fk_mtgs_entry FOREIGN KEY (entry_id) REFERENCES market_trip_entries(id),
 CONSTRAINT fk_mtgs_settlement FOREIGN KEY (settlement_id, source_kind, supplier_id, karat_id, weight_grams)
  REFERENCES supplier_gold_settlements(id, source_kind, supplier_id, delivered_karat_id, delivered_weight_grams),
 CONSTRAINT chk_mtgs_kind CHECK (source_kind = 'CUSTODY_GOLD'),
 CONSTRAINT chk_mtgs_designation CHECK (designation IN ('LOGO')),
 CONSTRAINT chk_mtgs_weight CHECK (weight_grams > 0)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

INSERT INTO schema_migrations(version,name) VALUES (46,'Supplier gold settlement from market custody: GOLD_SETTLE entries, CUSTODY_GOLD source, source-compatible legs and the entry-settlement link')
 ON DUPLICATE KEY UPDATE name=VALUES(name);
