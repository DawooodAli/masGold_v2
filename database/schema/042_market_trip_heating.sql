-- Market-trip Logo heating (حمّية اللوجو): LOGO-designated gold in custody may lose (or, with an explanation, gain) weight when it is
-- heated without being melted.
--   HEAT         a new entry kind (MT000001-n) on an OPEN trip. It consumes the declared processed LOGO grams of one or more karats
--                and restores the ACTUAL resulting grams under the SAME karat and the LOGO designation. The karat never changes and
--                no purity, pure-gold or Eq21 figure is stored: only physical weights.
--   header       market_trip_heats: workshop / operator, optional reference, the explanation of a weight gain, and the totals.
--   lines        market_trip_heat_lines: one line per (entry, karat): input grams, output grams and their signed difference.
-- A heat is immutable evidence: it is never updated or deleted; a reversed heat stays readable (its reversal is a
-- transaction_corrections row). The custody of (karat, LOGO) is derived as issued - returned - melted + SUM(output - input) over the
-- heats that were not reversed. A heat posts no cash, bank, Scrap, supplier, inventory-piece or lot movement and is not part of any
-- melt analysis. Every step is guarded, so a re-run changes nothing.
SET @masgold_sql = IF(EXISTS(SELECT 1 FROM schema_migrations WHERE version=41),
 'DO 0', 'SELECT * FROM MIGRATION_042_REFUSED_missing_041');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

-- 1. the HEAT entry kind
SET @masgold_sql := IF(EXISTS (SELECT 1 FROM information_schema.CHECK_CONSTRAINTS WHERE CONSTRAINT_SCHEMA = DATABASE()
 AND CONSTRAINT_NAME = 'chk_mte_kind' AND CHECK_CLAUSE NOT LIKE '%HEAT%'),
 "ALTER TABLE market_trip_entries DROP CHECK chk_mte_kind", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(NOT EXISTS (SELECT 1 FROM information_schema.TABLE_CONSTRAINTS WHERE CONSTRAINT_SCHEMA = DATABASE()
 AND TABLE_NAME = 'market_trip_entries' AND CONSTRAINT_NAME = 'chk_mte_kind'),
 "ALTER TABLE market_trip_entries ADD CONSTRAINT chk_mte_kind CHECK (entry_kind IN ('ISSUE','RETURN','MELT','LOT_RETURN','LOT_ISSUE','HEAT'))", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

-- 2. the heating document (one row per HEAT entry; never updated, never deleted)
CREATE TABLE IF NOT EXISTS market_trip_heats (
 entry_id BIGINT UNSIGNED NOT NULL,
 workshop VARCHAR(120) NOT NULL,
 reference VARCHAR(120) DEFAULT NULL,
 gain_explanation VARCHAR(1000) DEFAULT NULL,
 total_input_grams DECIMAL(12,3) NOT NULL,
 total_output_grams DECIMAL(12,3) NOT NULL,
 total_difference_grams DECIMAL(12,3) NOT NULL,
 created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
 PRIMARY KEY (entry_id),
 CONSTRAINT fk_mth_entry FOREIGN KEY (entry_id) REFERENCES market_trip_entries(id),
 CONSTRAINT chk_mth_workshop CHECK (CHAR_LENGTH(TRIM(workshop)) > 0),
 CONSTRAINT chk_mth_weights CHECK (total_input_grams > 0 AND total_output_grams > 0
  AND total_difference_grams = total_output_grams - total_input_grams)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- 3. the lines: the declared processed input and the actual output of each karat (physical grams, signed difference)
CREATE TABLE IF NOT EXISTS market_trip_heat_lines (
 id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
 entry_id BIGINT UNSIGNED NOT NULL,
 karat_id TINYINT UNSIGNED NOT NULL,
 designation VARCHAR(12) NOT NULL DEFAULT 'LOGO',
 input_weight_grams DECIMAL(12,3) NOT NULL,
 output_weight_grams DECIMAL(12,3) NOT NULL,
 difference_grams DECIMAL(12,3) NOT NULL,
 PRIMARY KEY (id),
 UNIQUE KEY uq_mthl_entry_karat (entry_id, karat_id),
 KEY idx_mthl_karat (karat_id),
 CONSTRAINT fk_mthl_heat FOREIGN KEY (entry_id) REFERENCES market_trip_heats(entry_id),
 CONSTRAINT fk_mthl_karat FOREIGN KEY (karat_id) REFERENCES karats(id),
 CONSTRAINT chk_mthl_designation CHECK (designation = 'LOGO'),
 CONSTRAINT chk_mthl_weights CHECK (input_weight_grams > 0 AND output_weight_grams > 0
  AND difference_grams = output_weight_grams - input_weight_grams)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

INSERT INTO schema_migrations(version,name) VALUES (42,'Market-trip Logo heating: HEAT entries with immutable header and per-karat weight lines')
 ON DUPLICATE KEY UPDATE name=VALUES(name);
