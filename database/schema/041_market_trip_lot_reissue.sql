-- Market-trip melting (M3): a whole bar that returned to the shop can leave again in a DIFFERENT open custody, and return from there.
--   LOT_ISSUE    a new entry kind (MT000001-n) in the DESTINATION custody. It moves one whole lot between two locations:
--                  shop ledger        TRIP_ISSUE   (negative grams, market_trip_lot_shop_movements)
--                  custody movement   SHOP_ISSUE   (positive grams of the destination trip, market_trip_lot_movements)
--                A return from that custody is the existing LOT_RETURN (SHOP_RETURN out of the custody, TRIP_RETURN into the shop): the
--                exact opposite transfer. The current location of a lot is DERIVED from the effective movements (a lot is in the shop
--                or in exactly one custody); it is never read from the lot's origin melt, so the origin trip may stay closed.
--   reversal     the full reversal of a LOT_ISSUE posts the exact opposite shop movement (ISSUE_REVERSAL, positive grams) and the
--                custody grams disappear because the reversed entry stops counting; nothing is rewritten.
--   link         market_trip_lot_issues: entry -> lot -> its custody movement + its shop movement (one each, immutable).
-- The lot, its identity, weight, kind, assay and the stored melt analysis are never touched. No cash, bank, Scrap, supplier or inventory
-- object exists in a reissue. Every step is guarded, so a re-run changes nothing.
SET @masgold_sql = IF(EXISTS(SELECT 1 FROM schema_migrations WHERE version=40),
 'DO 0', 'SELECT * FROM MIGRATION_041_REFUSED_missing_040');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

-- 1. the LOT_ISSUE entry kind
SET @masgold_sql := IF(EXISTS (SELECT 1 FROM information_schema.CHECK_CONSTRAINTS WHERE CONSTRAINT_SCHEMA = DATABASE()
 AND CONSTRAINT_NAME = 'chk_mte_kind' AND CHECK_CLAUSE NOT LIKE '%LOT_ISSUE%'),
 "ALTER TABLE market_trip_entries DROP CHECK chk_mte_kind", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(NOT EXISTS (SELECT 1 FROM information_schema.TABLE_CONSTRAINTS WHERE CONSTRAINT_SCHEMA = DATABASE()
 AND TABLE_NAME = 'market_trip_entries' AND CONSTRAINT_NAME = 'chk_mte_kind'),
 "ALTER TABLE market_trip_entries ADD CONSTRAINT chk_mte_kind CHECK (entry_kind IN ('ISSUE','RETURN','MELT','LOT_RETURN','LOT_ISSUE'))", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

-- 2. the custody-side lot movement SHOP_ISSUE (positive grams entering the destination custody)
SET @masgold_sql := IF(EXISTS (SELECT 1 FROM information_schema.CHECK_CONSTRAINTS WHERE CONSTRAINT_SCHEMA = DATABASE()
 AND CONSTRAINT_NAME = 'chk_mtlm_type' AND CHECK_CLAUSE NOT LIKE '%SHOP_ISSUE%'),
 "ALTER TABLE market_trip_lot_movements DROP CHECK chk_mtlm_type", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(NOT EXISTS (SELECT 1 FROM information_schema.TABLE_CONSTRAINTS WHERE CONSTRAINT_SCHEMA = DATABASE()
 AND TABLE_NAME = 'market_trip_lot_movements' AND CONSTRAINT_NAME = 'chk_mtlm_type'),
 "ALTER TABLE market_trip_lot_movements ADD CONSTRAINT chk_mtlm_type CHECK (movement_type IN ('CREATE','SHOP_RETURN','SHOP_ISSUE'))", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(EXISTS (SELECT 1 FROM information_schema.CHECK_CONSTRAINTS WHERE CONSTRAINT_SCHEMA = DATABASE()
 AND CONSTRAINT_NAME = 'chk_mtlm_delta' AND CHECK_CLAUSE NOT LIKE '%SHOP_ISSUE%'),
 "ALTER TABLE market_trip_lot_movements DROP CHECK chk_mtlm_delta", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(NOT EXISTS (SELECT 1 FROM information_schema.TABLE_CONSTRAINTS WHERE CONSTRAINT_SCHEMA = DATABASE()
 AND TABLE_NAME = 'market_trip_lot_movements' AND CONSTRAINT_NAME = 'chk_mtlm_delta'),
 "ALTER TABLE market_trip_lot_movements ADD CONSTRAINT chk_mtlm_delta CHECK (weight_delta <> 0
  AND (movement_type <> 'CREATE' OR weight_delta > 0) AND (movement_type <> 'SHOP_RETURN' OR weight_delta < 0)
  AND (movement_type <> 'SHOP_ISSUE' OR weight_delta > 0))", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

-- 3. the shop-side ledger gains TRIP_ISSUE (negative) and ISSUE_REVERSAL (positive, always tied to a correction)
SET @masgold_sql := IF(EXISTS (SELECT 1 FROM information_schema.CHECK_CONSTRAINTS WHERE CONSTRAINT_SCHEMA = DATABASE()
 AND CONSTRAINT_NAME = 'chk_mtlsm_shape' AND CHECK_CLAUSE NOT LIKE '%TRIP_ISSUE%'),
 "ALTER TABLE market_trip_lot_shop_movements DROP CHECK chk_mtlsm_shape", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(NOT EXISTS (SELECT 1 FROM information_schema.TABLE_CONSTRAINTS WHERE CONSTRAINT_SCHEMA = DATABASE()
 AND TABLE_NAME = 'market_trip_lot_shop_movements' AND CONSTRAINT_NAME = 'chk_mtlsm_shape'),
 "ALTER TABLE market_trip_lot_shop_movements ADD CONSTRAINT chk_mtlsm_shape CHECK (
  (movement_type = 'TRIP_RETURN' AND weight_delta > 0 AND correction_id IS NULL)
  OR (movement_type = 'RETURN_REVERSAL' AND weight_delta < 0 AND correction_id IS NOT NULL)
  OR (movement_type = 'TRIP_ISSUE' AND weight_delta < 0 AND correction_id IS NULL)
  OR (movement_type = 'ISSUE_REVERSAL' AND weight_delta > 0 AND correction_id IS NOT NULL))", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

-- 4. the immutable issue line: which lot entered which custody, with its two movements (shop out, custody in)
CREATE TABLE IF NOT EXISTS market_trip_lot_issues (
 id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
 entry_id BIGINT UNSIGNED NOT NULL,
 lot_id BIGINT UNSIGNED NOT NULL,
 weight_grams DECIMAL(12,3) NOT NULL,
 custody_movement_id BIGINT UNSIGNED NOT NULL,
 shop_movement_id BIGINT UNSIGNED NOT NULL,
 PRIMARY KEY (id),
 UNIQUE KEY uq_mtli_entry_lot (entry_id, lot_id),
 UNIQUE KEY uq_mtli_custody (custody_movement_id),
 UNIQUE KEY uq_mtli_shop (shop_movement_id),
 KEY idx_mtli_lot (lot_id),
 CONSTRAINT fk_mtli_entry FOREIGN KEY (entry_id) REFERENCES market_trip_entries(id),
 CONSTRAINT fk_mtli_lot FOREIGN KEY (lot_id) REFERENCES market_trip_lots(id),
 CONSTRAINT fk_mtli_custody FOREIGN KEY (custody_movement_id) REFERENCES market_trip_lot_movements(id),
 CONSTRAINT fk_mtli_shop FOREIGN KEY (shop_movement_id) REFERENCES market_trip_lot_shop_movements(id),
 CONSTRAINT chk_mtli_weight CHECK (weight_grams > 0)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

INSERT INTO schema_migrations(version,name) VALUES (41,'Market-trip lot reissue: LOT_ISSUE entries, shop-out / custody-in movements and issue links')
 ON DUPLICATE KEY UPDATE name=VALUES(name);
