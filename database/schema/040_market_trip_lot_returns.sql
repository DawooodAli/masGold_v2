-- Market-trip melting (M2): the owner returns melt output / residue LOTS from market custody to a separate shop-side lot stock.
--   LOT_RETURN   a new entry kind (MT000001-n). It lists the lots (and the exact physical grams of each) that leave custody. The custody
--                side reuses the M1 lot movements (new type SHOP_RETURN, negative); the shop side is a NEW immutable ledger of its own,
--                market_trip_lot_shop_movements, per lot. A lot keeps its identity, its original melt reference, its kind and its exact
--                assay (parts per thousand, never a master karat): returning never changes the lot or the stored melt analysis.
--   shop lots    the shop holds returned lots apart from the Scrap ledger and from inventory pieces: no cash, bank, supplier, inventory or
--                Scrap movement exists in a lot return. Shop grams of a lot = SUM(weight_delta) of its shop movements.
--   reversal     the full reversal of a LOT_RETURN (transaction_corrections) posts the exact opposite shop movement (RETURN_REVERSAL) and
--                the custody grams come back because the reversed entry's own custody movement stops counting; nothing is rewritten.
-- Traceability: trip -> melt entry -> lot -> LOT_RETURN entry -> custody movement + shop movement (market_trip_lot_returns).
-- Every step is guarded, so a re-run changes nothing.
SET @masgold_sql = IF(EXISTS(SELECT 1 FROM schema_migrations WHERE version=39),
 'DO 0', 'SELECT * FROM MIGRATION_040_REFUSED_missing_039');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

-- 1. the LOT_RETURN entry kind ('LOT_RETURN' needs 10 characters: the 037 column is VARCHAR(6), widened without touching any value)
SET @masgold_sql := IF(EXISTS (SELECT 1 FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = DATABASE()
 AND TABLE_NAME = 'market_trip_entries' AND COLUMN_NAME = 'entry_kind' AND CHARACTER_MAXIMUM_LENGTH < 12),
 "ALTER TABLE market_trip_entries MODIFY COLUMN entry_kind VARCHAR(12) NOT NULL", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(EXISTS (SELECT 1 FROM information_schema.CHECK_CONSTRAINTS WHERE CONSTRAINT_SCHEMA = DATABASE()
 AND CONSTRAINT_NAME = 'chk_mte_kind' AND CHECK_CLAUSE NOT LIKE '%LOT_RETURN%'),
 "ALTER TABLE market_trip_entries DROP CHECK chk_mte_kind", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(NOT EXISTS (SELECT 1 FROM information_schema.TABLE_CONSTRAINTS WHERE CONSTRAINT_SCHEMA = DATABASE()
 AND TABLE_NAME = 'market_trip_entries' AND CONSTRAINT_NAME = 'chk_mte_kind'),
 "ALTER TABLE market_trip_entries ADD CONSTRAINT chk_mte_kind CHECK (entry_kind IN ('ISSUE','RETURN','MELT','LOT_RETURN'))", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

-- 2. the custody-side lot movement SHOP_RETURN (negative grams leaving custody)
SET @masgold_sql := IF(EXISTS (SELECT 1 FROM information_schema.CHECK_CONSTRAINTS WHERE CONSTRAINT_SCHEMA = DATABASE()
 AND CONSTRAINT_NAME = 'chk_mtlm_type' AND CHECK_CLAUSE NOT LIKE '%SHOP_RETURN%'),
 "ALTER TABLE market_trip_lot_movements DROP CHECK chk_mtlm_type", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(NOT EXISTS (SELECT 1 FROM information_schema.TABLE_CONSTRAINTS WHERE CONSTRAINT_SCHEMA = DATABASE()
 AND TABLE_NAME = 'market_trip_lot_movements' AND CONSTRAINT_NAME = 'chk_mtlm_type'),
 "ALTER TABLE market_trip_lot_movements ADD CONSTRAINT chk_mtlm_type CHECK (movement_type IN ('CREATE','SHOP_RETURN'))", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(EXISTS (SELECT 1 FROM information_schema.CHECK_CONSTRAINTS WHERE CONSTRAINT_SCHEMA = DATABASE()
 AND CONSTRAINT_NAME = 'chk_mtlm_delta' AND CHECK_CLAUSE NOT LIKE '%SHOP_RETURN%'),
 "ALTER TABLE market_trip_lot_movements DROP CHECK chk_mtlm_delta", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(NOT EXISTS (SELECT 1 FROM information_schema.TABLE_CONSTRAINTS WHERE CONSTRAINT_SCHEMA = DATABASE()
 AND TABLE_NAME = 'market_trip_lot_movements' AND CONSTRAINT_NAME = 'chk_mtlm_delta'),
 "ALTER TABLE market_trip_lot_movements ADD CONSTRAINT chk_mtlm_delta CHECK (weight_delta <> 0
  AND (movement_type <> 'CREATE' OR weight_delta > 0) AND (movement_type <> 'SHOP_RETURN' OR weight_delta < 0))", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

-- 3. the shop-side lot ledger: insert-only, separate from Scrap, one signed row per posting
CREATE TABLE IF NOT EXISTS market_trip_lot_shop_movements (
 id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
 lot_id BIGINT UNSIGNED NOT NULL,
 entry_id BIGINT UNSIGNED NOT NULL,
 movement_type VARCHAR(20) NOT NULL,
 weight_delta DECIMAL(12,3) NOT NULL,
 correction_id BIGINT UNSIGNED DEFAULT NULL,
 created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
 PRIMARY KEY (id),
 UNIQUE KEY uq_mtlsm_lot_entry_type (lot_id, entry_id, movement_type),
 KEY idx_mtlsm_entry (entry_id),
 KEY idx_mtlsm_correction (correction_id),
 CONSTRAINT fk_mtlsm_lot FOREIGN KEY (lot_id) REFERENCES market_trip_lots(id),
 CONSTRAINT fk_mtlsm_entry FOREIGN KEY (entry_id) REFERENCES market_trip_entries(id),
 CONSTRAINT fk_mtlsm_correction FOREIGN KEY (correction_id) REFERENCES transaction_corrections(id),
 CONSTRAINT chk_mtlsm_shape CHECK (
  (movement_type = 'TRIP_RETURN' AND weight_delta > 0 AND correction_id IS NULL)
  OR (movement_type = 'RETURN_REVERSAL' AND weight_delta < 0 AND correction_id IS NOT NULL))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- 4. the immutable return document line: which lot, how many grams, and its two movements (custody out, shop in)
CREATE TABLE IF NOT EXISTS market_trip_lot_returns (
 id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
 entry_id BIGINT UNSIGNED NOT NULL,
 lot_id BIGINT UNSIGNED NOT NULL,
 weight_grams DECIMAL(12,3) NOT NULL,
 custody_movement_id BIGINT UNSIGNED NOT NULL,
 shop_movement_id BIGINT UNSIGNED NOT NULL,
 PRIMARY KEY (id),
 UNIQUE KEY uq_mtlr_entry_lot (entry_id, lot_id),
 UNIQUE KEY uq_mtlr_custody (custody_movement_id),
 UNIQUE KEY uq_mtlr_shop (shop_movement_id),
 KEY idx_mtlr_lot (lot_id),
 CONSTRAINT fk_mtlr_entry FOREIGN KEY (entry_id) REFERENCES market_trip_entries(id),
 CONSTRAINT fk_mtlr_lot FOREIGN KEY (lot_id) REFERENCES market_trip_lots(id),
 CONSTRAINT fk_mtlr_custody FOREIGN KEY (custody_movement_id) REFERENCES market_trip_lot_movements(id),
 CONSTRAINT fk_mtlr_shop FOREIGN KEY (shop_movement_id) REFERENCES market_trip_lot_shop_movements(id),
 CONSTRAINT chk_mtlr_weight CHECK (weight_grams > 0)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

INSERT INTO schema_migrations(version,name) VALUES (40,'Market-trip lot returns: LOT_RETURN entries, shop-side lot ledger and return links')
 ON DUPLICATE KEY UPDATE name=VALUES(name);
