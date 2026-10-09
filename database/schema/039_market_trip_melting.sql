-- Market-trip melting (M1): the owner's explicit gold designation, mixed-karat melting from custody, assayed output lots and the
-- permanent processing difference.
--   designation  every gold line of a custody entry now says what the OWNER decided: MELT (للسبك) or LOGO (لوجو). Custody is tracked per
--                (karat, designation); both may exist for the same karat. Lines written before this migration are UNCLASSIFIED (the
--                column default): they can only be returned, never melted. No historical row is rewritten or guessed.
--   MELT entry   a new entry kind (MT000001-n) that consumes MELT-designated custody (any master karat, physical grams) and creates
--                assayed output LOTS: one MAIN lot with its fineness (parts per thousand, 0.1 precision) and optional RESIDUE lots whose
--                fineness may be unknown. No cash, shop Scrap or supplier movement exists in a melt.
--   analysis     the melt row keeps immutable analytical snapshots (input / nominal pure / output / known pure gold, signed differences,
--                21K equivalents). A difference is never written off, never touches a supplier account and never disappears: a
--                reversed melt keeps its row.
--   lots         outstanding lot grams are derived from insert-only lot movements of entries that were not reversed. M1 only creates
--                lots (the CREATE movement is a lot's own birth, never a dependency); shop return of a lot is the next task.
-- Entries stay immutable (the only correction is the full reversal through transaction_corrections). Every step is guarded, so a
-- re-run changes nothing.
SET @masgold_sql = IF(EXISTS(SELECT 1 FROM schema_migrations WHERE version=38),
 'DO 0', 'SELECT * FROM MIGRATION_039_REFUSED_missing_038');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

-- 1. designation of a gold line (existing rows become UNCLASSIFIED through the column default)
SET @masgold_sql := IF(NOT EXISTS (SELECT 1 FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = DATABASE()
 AND TABLE_NAME = 'market_trip_entry_gold' AND COLUMN_NAME = 'designation'),
 "ALTER TABLE market_trip_entry_gold ADD COLUMN designation VARCHAR(12) NOT NULL DEFAULT 'UNCLASSIFIED' AFTER karat_id", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(NOT EXISTS (SELECT 1 FROM information_schema.TABLE_CONSTRAINTS WHERE CONSTRAINT_SCHEMA = DATABASE()
 AND TABLE_NAME = 'market_trip_entry_gold' AND CONSTRAINT_NAME = 'chk_mteg_designation'),
 "ALTER TABLE market_trip_entry_gold ADD CONSTRAINT chk_mteg_designation CHECK (designation IN ('MELT','LOGO','UNCLASSIFIED'))", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

-- one leg per (entry, karat, designation): the new key is added before the old one is dropped (the entry FK needs an index)
SET @masgold_sql := IF(NOT EXISTS (SELECT 1 FROM information_schema.STATISTICS WHERE TABLE_SCHEMA = DATABASE()
 AND TABLE_NAME = 'market_trip_entry_gold' AND INDEX_NAME = 'uq_mteg_entry_karat_des'),
 "ALTER TABLE market_trip_entry_gold ADD UNIQUE KEY uq_mteg_entry_karat_des (entry_id, karat_id, designation)", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(EXISTS (SELECT 1 FROM information_schema.STATISTICS WHERE TABLE_SCHEMA = DATABASE()
 AND TABLE_NAME = 'market_trip_entry_gold' AND INDEX_NAME = 'uq_mteg_entry_karat'),
 "ALTER TABLE market_trip_entry_gold DROP INDEX uq_mteg_entry_karat", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

-- 2. the MELT entry kind
SET @masgold_sql := IF(EXISTS (SELECT 1 FROM information_schema.CHECK_CONSTRAINTS WHERE CONSTRAINT_SCHEMA = DATABASE()
 AND CONSTRAINT_NAME = 'chk_mte_kind' AND CHECK_CLAUSE NOT LIKE '%MELT%'),
 "ALTER TABLE market_trip_entries DROP CHECK chk_mte_kind", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(NOT EXISTS (SELECT 1 FROM information_schema.TABLE_CONSTRAINTS WHERE CONSTRAINT_SCHEMA = DATABASE()
 AND TABLE_NAME = 'market_trip_entries' AND CONSTRAINT_NAME = 'chk_mte_kind'),
 "ALTER TABLE market_trip_entries ADD CONSTRAINT chk_mte_kind CHECK (entry_kind IN ('ISSUE','RETURN','MELT'))", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

-- 3. the melt document and its permanent analysis (one row per MELT entry; never updated, never deleted)
CREATE TABLE IF NOT EXISTS market_trip_melts (
 entry_id BIGINT UNSIGNED NOT NULL,
 workshop VARCHAR(120) NOT NULL,
 assay_reference VARCHAR(120) DEFAULT NULL,
 gain_explanation VARCHAR(1000) DEFAULT NULL,
 input_weight_grams DECIMAL(12,3) NOT NULL,
 nominal_pure_grams DECIMAL(18,6) NOT NULL,
 output_weight_grams DECIMAL(12,3) NOT NULL,
 known_output_pure_grams DECIMAL(18,6) NOT NULL,
 weight_difference_grams DECIMAL(12,3) NOT NULL,
 pure_complete TINYINT(1) NOT NULL,
 pure_difference_grams DECIMAL(18,6) DEFAULT NULL,
 nominal_eq21_grams DECIMAL(18,6) NOT NULL,
 known_output_eq21_grams DECIMAL(18,6) NOT NULL,
 eq21_difference_grams DECIMAL(18,6) DEFAULT NULL,
 created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
 PRIMARY KEY (entry_id),
 CONSTRAINT fk_mtm_entry FOREIGN KEY (entry_id) REFERENCES market_trip_entries(id),
 CONSTRAINT chk_mtm_workshop CHECK (CHAR_LENGTH(TRIM(workshop)) > 0),
 CONSTRAINT chk_mtm_weights CHECK (input_weight_grams > 0 AND output_weight_grams > 0
  AND weight_difference_grams = output_weight_grams - input_weight_grams),
 CONSTRAINT chk_mtm_gain CHECK (output_weight_grams <= input_weight_grams
  OR (gain_explanation IS NOT NULL AND CHAR_LENGTH(TRIM(gain_explanation)) > 0)),
 CONSTRAINT chk_mtm_complete CHECK (pure_complete IN (0,1) AND ((pure_complete = 1) = (pure_difference_grams IS NOT NULL))
  AND ((pure_complete = 1) = (eq21_difference_grams IS NOT NULL)))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- The master-karat inputs of a melt: physical grams as entered and the nominal pure gold of the karat (weight * karat / 24).
CREATE TABLE IF NOT EXISTS market_trip_melt_inputs (
 id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
 entry_id BIGINT UNSIGNED NOT NULL,
 karat_id TINYINT UNSIGNED NOT NULL,
 weight_grams DECIMAL(12,3) NOT NULL,
 pure_grams DECIMAL(18,6) NOT NULL,
 PRIMARY KEY (id),
 UNIQUE KEY uq_mtmi_entry_karat (entry_id, karat_id),
 KEY idx_mtmi_karat (karat_id),
 CONSTRAINT fk_mtmi_melt FOREIGN KEY (entry_id) REFERENCES market_trip_melts(entry_id),
 CONSTRAINT fk_mtmi_karat FOREIGN KEY (karat_id) REFERENCES karats(id),
 CONSTRAINT chk_mtmi_weight CHECK (weight_grams > 0 AND pure_grams > 0)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Assayed output lots: lot 1 is the MAIN output (fineness mandatory); the others are RESIDUES whose fineness may be unknown
-- (never inferred). Fineness is parts per thousand with one decimal; a lot is not a master karat.
CREATE TABLE IF NOT EXISTS market_trip_lots (
 id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
 entry_id BIGINT UNSIGNED NOT NULL,
 lot_no INT UNSIGNED NOT NULL,
 lot_kind VARCHAR(7) NOT NULL,
 label VARCHAR(120) DEFAULT NULL,
 weight_grams DECIMAL(12,3) NOT NULL,
 fineness_permille DECIMAL(5,1) DEFAULT NULL,
 pure_grams DECIMAL(18,6) DEFAULT NULL,
 PRIMARY KEY (id),
 UNIQUE KEY uq_mtl_entry_no (entry_id, lot_no),
 CONSTRAINT fk_mtl_melt FOREIGN KEY (entry_id) REFERENCES market_trip_melts(entry_id),
 CONSTRAINT chk_mtl_kind CHECK (lot_kind IN ('MAIN','RESIDUE') AND ((lot_kind = 'MAIN') = (lot_no = 1))),
 CONSTRAINT chk_mtl_weight CHECK (weight_grams > 0),
 CONSTRAINT chk_mtl_fineness CHECK ((fineness_permille IS NULL OR (fineness_permille > 0 AND fineness_permille <= 1000))
  AND (lot_kind <> 'MAIN' OR fineness_permille IS NOT NULL)
  AND ((fineness_permille IS NULL) = (pure_grams IS NULL)))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Insert-only lot movements. Outstanding grams of a lot = SUM(weight_delta) over the movements of entries that were not reversed.
-- CREATE is the lot's own birth (the melt entry itself); a later entry that moves the lot is a downstream dependency of that melt.
CREATE TABLE IF NOT EXISTS market_trip_lot_movements (
 id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
 lot_id BIGINT UNSIGNED NOT NULL,
 entry_id BIGINT UNSIGNED NOT NULL,
 movement_type VARCHAR(20) NOT NULL,
 weight_delta DECIMAL(12,3) NOT NULL,
 created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
 PRIMARY KEY (id),
 UNIQUE KEY uq_mtlm_lot_entry_type (lot_id, entry_id, movement_type),
 KEY idx_mtlm_entry (entry_id),
 CONSTRAINT fk_mtlm_lot FOREIGN KEY (lot_id) REFERENCES market_trip_lots(id),
 CONSTRAINT fk_mtlm_entry FOREIGN KEY (entry_id) REFERENCES market_trip_entries(id),
 CONSTRAINT chk_mtlm_type CHECK (movement_type IN ('CREATE')),
 CONSTRAINT chk_mtlm_delta CHECK (weight_delta <> 0 AND (movement_type <> 'CREATE' OR weight_delta > 0))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

INSERT INTO schema_migrations(version,name) VALUES (39,'Market-trip melting: gold designation, melt documents, assayed lots and permanent processing differences')
 ON DUPLICATE KEY UPDATE name=VALUES(name);
