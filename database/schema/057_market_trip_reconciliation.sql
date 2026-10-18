-- Market-trip BOOK reconciliation document (R2-S1). Prepared; NOT applied to any database by the task that wrote it, and the application code that uses it is DISABLED by default
-- (`MarketTripBookService(reconciliation_enabled=False)`): no screen, setting or production path posts it.
--   entry kind      BK_RECON joins market_trip_entries (chk_mte_kind) and the BOOK branch of chk_mte_mode_kind: it exists only in BOOK trips, carries no book line, no Scrap leg, no
--                   trade and no stock row, and moves no cash. Its reversal is the ordinary FULL_REVERSAL correction of the entry (CorrectionService, source MARKET_TRIP_ENTRY).
--   reconciliation  market_trip_reconciliations: ONE immutable row per document. It stores the exact figures on the 1/26,250,000 g lattice (a_star_units, delta_units, rho_units,
--                   sum_abs_units as whole numbers), the posted figures (b_star, loss_posted, rounding_settlement), the counters, the two digests, the versions, the canonical snapshot
--                   (JSON) and the owner's decision (reason, note, effective date with the window it was validated against, confirming user and time).
--                   Identities between the columns are enforced WITHOUT division or tolerance (multiplication of exact DECIMALs only); the CHECK expressions have NOT been executed on a
--                   MySQL server yet: their behaviour is a later verification gate, not a claim of this file. The digest columns are ascii_bin and the CHECK uses REGEXP_LIKE with the
--                   case-sensitive match type; the service validates the same format (64 lowercase ASCII hex characters) before any write.
--   one effective   at most one NON-reversed reconciliation per trip is enforced by the service under the trip lock (a reversed one stops counting, so a NEW generation is then
--                   allowed): there is deliberately no UNIQUE(trip_id). UNIQUE(trip_id, generation) is only a defence against two writers that bypass the lock; it does not prove the rule.
--   reversed state  derived from the correction record, never stored. Nothing here updates or deletes history.
-- No new movement type, permission or correction source. Every step is guarded, so a re-run changes nothing.
SET @masgold_sql = IF(EXISTS(SELECT 1 FROM schema_migrations WHERE version=56),
 'DO 0', 'SELECT * FROM MIGRATION_057_REFUSED_missing_056');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

-- 1. the new entry kind (BOOK only)
SET @masgold_sql := IF(EXISTS (SELECT 1 FROM information_schema.CHECK_CONSTRAINTS WHERE CONSTRAINT_SCHEMA = DATABASE()
 AND CONSTRAINT_NAME = 'chk_mte_kind' AND CHECK_CLAUSE NOT LIKE '%BK_RECON%'),
 "ALTER TABLE market_trip_entries DROP CHECK chk_mte_kind", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(NOT EXISTS (SELECT 1 FROM information_schema.TABLE_CONSTRAINTS WHERE CONSTRAINT_SCHEMA = DATABASE()
 AND TABLE_NAME = 'market_trip_entries' AND CONSTRAINT_NAME = 'chk_mte_kind'),
 "ALTER TABLE market_trip_entries ADD CONSTRAINT chk_mte_kind CHECK (entry_kind IN ('ISSUE','RETURN','MELT','LOT_RETURN','LOT_ISSUE','HEAT','SUPPLIER_PAY','GOLD_SETTLE','LOT_SETTLE','LOT_HOLD','HOLD_RETURN','LOGO_HOLD','HOLD_BACK','GOLD_SALE','GOLD_BUY','BK_ISSUE','BK_BUY','BK_SELL','BK_RETURN','BK_RECON'))", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(EXISTS (SELECT 1 FROM information_schema.CHECK_CONSTRAINTS WHERE CONSTRAINT_SCHEMA = DATABASE()
 AND CONSTRAINT_NAME = 'chk_mte_mode_kind' AND CHECK_CLAUSE NOT LIKE '%BK_RECON%'),
 "ALTER TABLE market_trip_entries DROP CHECK chk_mte_mode_kind", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(NOT EXISTS (SELECT 1 FROM information_schema.TABLE_CONSTRAINTS WHERE CONSTRAINT_SCHEMA = DATABASE()
 AND TABLE_NAME = 'market_trip_entries' AND CONSTRAINT_NAME = 'chk_mte_mode_kind'),
 "ALTER TABLE market_trip_entries ADD CONSTRAINT chk_mte_mode_kind CHECK ((trip_mode = 'LEGACY' AND entry_kind IN ('ISSUE','RETURN','MELT','LOT_RETURN','LOT_ISSUE','HEAT','SUPPLIER_PAY','GOLD_SETTLE','LOT_SETTLE','LOT_HOLD','HOLD_RETURN','LOGO_HOLD','HOLD_BACK','GOLD_SALE','GOLD_BUY')) OR (trip_mode = 'BOOK' AND entry_kind IN ('ISSUE','RETURN','SUPPLIER_PAY','BK_ISSUE','BK_BUY','BK_SELL','BK_RETURN','BK_RECON')))", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

-- 2. the reconciliation document (immutable)
CREATE TABLE IF NOT EXISTS market_trip_reconciliations (
 entry_id BIGINT UNSIGNED NOT NULL,
 entry_kind VARCHAR(12) NOT NULL DEFAULT 'BK_RECON',
 trip_id BIGINT UNSIGNED NOT NULL,
 trip_mode VARCHAR(8) NOT NULL DEFAULT 'BOOK',
 generation SMALLINT UNSIGNED NOT NULL,
 algorithm VARCHAR(24) NOT NULL,
 source_algorithm VARCHAR(24) NOT NULL,
 snapshot_format VARCHAR(24) NOT NULL,
 effective_date DATE NOT NULL,
 date_floor DATE NOT NULL,
 date_floor_source VARCHAR(16) NOT NULL,
 unit_denominator INT UNSIGNED NOT NULL,
 a_star_units DECIMAL(30,0) NOT NULL,
 a_star DECIMAL(21,6) NOT NULL,
 b_star DECIMAL(18,3) NOT NULL,
 loss_posted DECIMAL(18,3) NOT NULL,
 rounding_settlement DECIMAL(18,3) NOT NULL,
 delta_units DECIMAL(30,0) NOT NULL,
 rho_units DECIMAL(30,0) NOT NULL,
 sum_abs_units DECIMAL(30,0) NOT NULL,
 entry_count INT UNSIGNED NOT NULL,
 line_count INT UNSIGNED NOT NULL,
 reversed_count INT UNSIGNED NOT NULL,
 source_digest CHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
 snapshot_digest CHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
 snapshot JSON NOT NULL,
 reason_code VARCHAR(24) DEFAULT NULL,
 note VARCHAR(1000) DEFAULT NULL,
 confirmed_by_user_id INT UNSIGNED NOT NULL,
 confirmed_at DATETIME NOT NULL,
 created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
 PRIMARY KEY (entry_id),
 UNIQUE KEY uq_mtr_trip_generation (trip_id, generation),
 CONSTRAINT fk_mtr_entry FOREIGN KEY (entry_id, entry_kind, trip_id) REFERENCES market_trip_entries(id, entry_kind, trip_id),
 CONSTRAINT fk_mtr_mode FOREIGN KEY (entry_id, trip_mode) REFERENCES market_trip_entries(id, trip_mode) ON UPDATE RESTRICT ON DELETE RESTRICT,
 CONSTRAINT fk_mtr_user FOREIGN KEY (confirmed_by_user_id) REFERENCES users(id),
 CONSTRAINT chk_mtr_head CHECK (
  entry_kind = 'BK_RECON' AND trip_mode = 'BOOK' AND generation >= 1 AND algorithm = 'book-recon-v1' AND source_algorithm = 'book-recon-source-v1'
  AND snapshot_format = 'book-recon-snapshot-v1' AND unit_denominator = 26250000 AND JSON_TYPE(snapshot) = 'OBJECT'),
 CONSTRAINT chk_mtr_digests CHECK (
  REGEXP_LIKE(source_digest, '^[0-9a-f]{64}$', 'c') AND REGEXP_LIKE(snapshot_digest, '^[0-9a-f]{64}$', 'c')),
 CONSTRAINT chk_mtr_date CHECK (
  date_floor_source IN ('LAST_GOLD_ENTRY','TRIP_CREATED') AND date_floor <= effective_date AND effective_date <= DATE(confirmed_at)),
 CONSTRAINT chk_mtr_counts CHECK (reversed_count <= entry_count),
 CONSTRAINT chk_mtr_balance CHECK (
  a_star_units >= 0 AND sum_abs_units >= 0 AND loss_posted >= 0
  AND ABS(a_star * 26250000 - a_star_units) < 13.125
  AND loss_posted + rounding_settlement = b_star
  AND loss_posted * 26250000 <= a_star_units + 13125 AND a_star_units + 13125 < loss_posted * 26250000 + 26250
  AND delta_units = loss_posted * 26250000 - a_star_units AND ABS(delta_units) <= 13125
  AND rho_units = b_star * 26250000 - a_star_units
  AND rounding_settlement * 26250000 = rho_units - delta_units
  AND ABS(rounding_settlement * 26250000) <= sum_abs_units + 13125),
 CONSTRAINT chk_mtr_reason CHECK (
  (reason_code IS NULL AND loss_posted = 0 AND (note IS NULL OR CHAR_LENGTH(TRIM(note)) > 0))
  OR (reason_code IS NOT NULL AND reason_code IN ('OPERATING_LOSS','PURITY_DIFFERENCE','TRADING_LOSS') AND (note IS NULL OR CHAR_LENGTH(TRIM(note)) > 0))
  OR (reason_code IS NOT NULL AND reason_code IN ('OTHER','UNEXPLAINED') AND note IS NOT NULL AND CHAR_LENGTH(TRIM(note)) > 0))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

INSERT INTO schema_migrations(version,name) VALUES (57,'Market-trip BOOK reconciliation document: BK_RECON entry kind (BOOK only) and the immutable market_trip_reconciliations record (exact lattice figures, digests, canonical snapshot, owner decision)')
 ON DUPLICATE KEY UPDATE name=VALUES(name);
