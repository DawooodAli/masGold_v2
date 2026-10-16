-- Sale of custody gold (بيع ذهب العهدة): the OWNER sells gold that is already in an OPEN market custody for cash at a price agreed by hand.
--   GOLD_SALE   a new custody entry kind (MTxxxxxx-n). It consumes outstanding custody gold exactly like the other custody consumers
--               (custody gold of (karat, designation) = ... - sold for cash) and ADDS the proceeds to the custody CASH
--               (custody cash = issued - returned - paid to suppliers + proceeds of sales), over the entries that were not reversed.
--               The cash stays in custody: NO cash_movements / bank_movements row exists (Main Safe moves only at a normal custody return).
--   sale link   market_trip_gold_sales: one immutable row per entry with the consumed source (an aggregate (karat, designation) line OR one
--               whole known-assay lot), the AGREED total price (authoritative) with its basis, the buyer (a registered party OR a
--               third-party name) and the evidence of the price warning (reference price, deviation, owner acknowledgement).
--   lots        SALE_OUT is a new negative movement of the custody lot ledger; the database ties the sale to that exact movement.
-- A sale NEVER writes a supplier / making / settlement ledger: it has no foreign key to any of them. The buyer is only an identity reference
-- (a registered party may be a supplier or an intermediary; no role is required or granted). Every step is guarded, so a re-run changes nothing.
SET @masgold_sql = IF(EXISTS(SELECT 1 FROM schema_migrations WHERE version=52),
 'DO 0', 'SELECT * FROM MIGRATION_053_REFUSED_missing_052');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

-- 1. the GOLD_SALE entry kind
SET @masgold_sql := IF(EXISTS (SELECT 1 FROM information_schema.CHECK_CONSTRAINTS WHERE CONSTRAINT_SCHEMA = DATABASE()
 AND CONSTRAINT_NAME = 'chk_mte_kind' AND CHECK_CLAUSE NOT LIKE '%GOLD_SALE%'),
 "ALTER TABLE market_trip_entries DROP CHECK chk_mte_kind", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(NOT EXISTS (SELECT 1 FROM information_schema.TABLE_CONSTRAINTS WHERE CONSTRAINT_SCHEMA = DATABASE()
 AND TABLE_NAME = 'market_trip_entries' AND CONSTRAINT_NAME = 'chk_mte_kind'),
 "ALTER TABLE market_trip_entries ADD CONSTRAINT chk_mte_kind CHECK (entry_kind IN ('ISSUE','RETURN','MELT','LOT_RETURN','LOT_ISSUE','HEAT','SUPPLIER_PAY','GOLD_SETTLE','LOT_SETTLE','LOT_HOLD','HOLD_RETURN','LOGO_HOLD','HOLD_BACK','GOLD_SALE'))", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

-- 2. the SALE_OUT custody lot movement (a whole lot leaves the custody; negative only)
SET @masgold_sql := IF(EXISTS(SELECT 1 FROM information_schema.CHECK_CONSTRAINTS WHERE CONSTRAINT_SCHEMA = DATABASE()
 AND CONSTRAINT_NAME = 'chk_mtlm_type' AND CHECK_CLAUSE NOT LIKE '%SALE_OUT%'), "ALTER TABLE market_trip_lot_movements DROP CHECK chk_mtlm_type", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(NOT EXISTS(SELECT 1 FROM information_schema.TABLE_CONSTRAINTS WHERE CONSTRAINT_SCHEMA = DATABASE()
 AND TABLE_NAME = 'market_trip_lot_movements' AND CONSTRAINT_NAME = 'chk_mtlm_type'),
 "ALTER TABLE market_trip_lot_movements ADD CONSTRAINT chk_mtlm_type CHECK (movement_type IN ('CREATE','SHOP_RETURN','SHOP_ISSUE','SUPPLIER_SETTLE','HOLD_OUT','HOLD_IN','SALE_OUT'))", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(EXISTS(SELECT 1 FROM information_schema.CHECK_CONSTRAINTS WHERE CONSTRAINT_SCHEMA = DATABASE()
 AND CONSTRAINT_NAME = 'chk_mtlm_delta' AND CHECK_CLAUSE NOT LIKE '%SALE_OUT%'), "ALTER TABLE market_trip_lot_movements DROP CHECK chk_mtlm_delta", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(NOT EXISTS(SELECT 1 FROM information_schema.TABLE_CONSTRAINTS WHERE CONSTRAINT_SCHEMA = DATABASE()
 AND TABLE_NAME = 'market_trip_lot_movements' AND CONSTRAINT_NAME = 'chk_mtlm_delta'),
 "ALTER TABLE market_trip_lot_movements ADD CONSTRAINT chk_mtlm_delta CHECK (weight_delta <> 0 AND (movement_type NOT IN ('CREATE','SHOP_ISSUE','HOLD_IN') OR weight_delta>0) AND (movement_type NOT IN ('SHOP_RETURN','SUPPLIER_SETTLE','HOLD_OUT','SALE_OUT') OR weight_delta<0))", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

-- 3. the immutable sale document of a custody entry
CREATE TABLE IF NOT EXISTS market_trip_gold_sales (
 entry_id BIGINT UNSIGNED NOT NULL,
 entry_kind VARCHAR(12) NOT NULL DEFAULT 'GOLD_SALE',
 source_kind VARCHAR(12) NOT NULL,
 karat_id TINYINT UNSIGNED DEFAULT NULL,
 designation VARCHAR(12) DEFAULT NULL,
 lot_id BIGINT UNSIGNED DEFAULT NULL,
 fineness_permille DECIMAL(5,1) DEFAULT NULL,
 custody_movement_id BIGINT UNSIGNED DEFAULT NULL,
 movement_type VARCHAR(20) DEFAULT NULL,
 weight_grams DECIMAL(14,3) NOT NULL,
 weight_delta DECIMAL(12,3) DEFAULT NULL,
 total_price DECIMAL(16,2) NOT NULL,
 price_per_gram DECIMAL(16,4) DEFAULT NULL,
 price_basis VARCHAR(9) NOT NULL,
 buyer_kind VARCHAR(16) NOT NULL,
 buyer_party_id INT UNSIGNED DEFAULT NULL,
 buyer_name VARCHAR(150) NOT NULL,
 reference_price_per_gram DECIMAL(16,4) DEFAULT NULL,
 deviation_percent DECIMAL(9,2) DEFAULT NULL,
 price_warning_acknowledged TINYINT(1) NOT NULL DEFAULT 0,
 sale_date DATE NOT NULL,
 reference VARCHAR(100) DEFAULT NULL,
 created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
 PRIMARY KEY (entry_id),
 UNIQUE KEY uq_mtgsale_movement (custody_movement_id),
 KEY idx_mtgsale_lot (lot_id),
 KEY idx_mtgsale_buyer (buyer_party_id),
 KEY idx_mtgsale_source (karat_id, designation),
 CONSTRAINT fk_mtgsale_entry FOREIGN KEY (entry_id, entry_kind) REFERENCES market_trip_entries(id, entry_kind),
 CONSTRAINT fk_mtgsale_karat FOREIGN KEY (karat_id) REFERENCES karats(id),
 CONSTRAINT fk_mtgsale_lot FOREIGN KEY (lot_id) REFERENCES market_trip_lots(id),
 CONSTRAINT fk_mtgsale_party FOREIGN KEY (buyer_party_id) REFERENCES suppliers(id),
 CONSTRAINT fk_mtgsale_movement FOREIGN KEY (custody_movement_id, lot_id, entry_id, movement_type, weight_delta)
  REFERENCES market_trip_lot_movements(id, lot_id, entry_id, movement_type, weight_delta),
 CONSTRAINT chk_mtgsale_kind CHECK (entry_kind = 'GOLD_SALE'),
 CONSTRAINT chk_mtgsale_source CHECK (
  (source_kind = 'CUSTODY_GOLD' AND karat_id IS NOT NULL AND designation IN ('MELT','LOGO','UNCLASSIFIED')
   AND lot_id IS NULL AND fineness_permille IS NULL AND custody_movement_id IS NULL AND movement_type IS NULL AND weight_delta IS NULL)
  OR (source_kind = 'CUSTODY_LOT' AND lot_id IS NOT NULL AND fineness_permille IS NOT NULL AND fineness_permille > 0 AND fineness_permille <= 1000
   AND custody_movement_id IS NOT NULL AND movement_type = 'SALE_OUT' AND weight_delta = -weight_grams
   AND karat_id IS NULL AND designation IS NULL)),
 CONSTRAINT chk_mtgsale_buyer CHECK (
  (buyer_kind = 'REGISTERED_PARTY' AND buyer_party_id IS NOT NULL)
  OR (buyer_kind = 'THIRD_PARTY' AND buyer_party_id IS NULL)),
 CONSTRAINT chk_mtgsale_price CHECK (weight_grams > 0 AND total_price > 0
  AND ((price_basis = 'TOTAL' AND price_per_gram IS NULL) OR (price_basis = 'PER_GRAM' AND price_per_gram IS NOT NULL AND price_per_gram > 0))),
 CONSTRAINT chk_mtgsale_warning CHECK (
  (reference_price_per_gram IS NULL AND deviation_percent IS NULL)
  OR (reference_price_per_gram IS NOT NULL AND deviation_percent IS NOT NULL))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

INSERT INTO schema_migrations(version,name) VALUES (53,'Sale of custody gold: GOLD_SALE entries, SALE_OUT lot movements and the immutable sale document')
 ON DUPLICATE KEY UPDATE name=VALUES(name);
