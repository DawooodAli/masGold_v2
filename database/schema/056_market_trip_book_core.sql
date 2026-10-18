-- Market-trip BOOK core (Phase 1 of the equivalent-book workflow). Prepared and reviewed; NOT applied to any database by the task that wrote it.
--   accounting_mode   market_trips.accounting_mode LEGACY | BOOK, set when the trip is created and immutable afterwards. EVERY existing trip becomes LEGACY through the
--                     column DEFAULT (nothing is recalculated or converted); a trip created by the old paths stays LEGACY; no code path creates a BOOK trip in normal use.
--   mode isolation    market_trip_entries.trip_mode is copied from the trip and tied to it by the composite foreign key (trip_id, trip_mode) -> market_trips(id, accounting_mode)
--                     ON UPDATE RESTRICT (so the mode of a trip that has any entry can never change), and the row-local CHECK chk_mte_mode_kind lets each entry kind exist
--                     only in its mode: BK_* kinds only in BOOK, the legacy-only kinds only in LEGACY, the cash kinds (ISSUE, RETURN, SUPPLIER_PAY) in both (the cash ledger is
--                     shared, never duplicated). market_trip_entry_gold (the legacy gold legs) is tied to LEGACY entries only, so a BOOK entry can never carry a legacy gold leg.
--                     MySQL CHECKs cannot read another table and this repository uses no triggers: the mode travels on the rows and the keys do the joining.
--   book lines        market_trip_book_lines: ONE row per document line of a BOOK entry (BK_ISSUE, BK_BUY, BK_SELL, BK_RETURN): the PHYSICAL facts (weight and either a master karat
--                     or an assay in permille), the six-decimal analytical equivalent (audit evidence), the POSTED equivalent (0.001 g, once per line) and the stored difference.
--                     The book balance of a trip is derived: SUM(signed_eq21_posted) over the entries that were not reversed; availability recomputes the exact analytical value
--                     from the stored physical facts. The CHECKs tie the evidence to the facts (|posted - analytical| <= 0.0005, analytical = weight x factor within the six-decimal rounding).
--   trade documents   market_trip_book_trades: the immutable purchase / sale document (counterparty identity is mandatory, total, price-warning evidence). The cash of a trade stays
--                     in the CUSTODY cash (like 053/054): no cash_movements, bank_movements or Main Safe row is written for it.
--   assayed stock     shop_assayed_stock_movements: the insert-only ledger of assayed gold that returned to the shop from a BOOK trip at a non-master assay (the item = a BK_RETURN
--                     FINENESS line, with its physical facts, origin entry and posted amount). A balance is the SUM of every movement (grams AND posted amount); a reversal is an
--                     explicit compensation row (the original stays counted). The ISSUE / ISSUE_REVERSAL rows (reuse in a later trip) are part of the SAME shape so the ledger never has
--                     to be redesigned; Phase 1 posts RECEIVE and RECEIVE_REVERSAL only.
-- No new movement type, correction source or permission: Scrap uses the existing TRIP_CUSTODY_OUT / TRIP_CUSTODY_RETURN_IN types and their reversals, the corrections use the
-- existing MARKET_TRIP_ENTRY source (OWNER + TRANSACTION_CORRECT). Every step is guarded, so a re-run changes nothing.
SET @masgold_sql = IF(EXISTS(SELECT 1 FROM schema_migrations WHERE version=55),
 'DO 0', 'SELECT * FROM MIGRATION_056_REFUSED_missing_055');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

-- 1. the trip mode (every existing row is LEGACY through the DEFAULT)
SET @masgold_sql := IF(NOT EXISTS(SELECT 1 FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='market_trips' AND COLUMN_NAME='accounting_mode'),
 "ALTER TABLE market_trips ADD COLUMN accounting_mode VARCHAR(8) NOT NULL DEFAULT 'LEGACY' AFTER status", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(NOT EXISTS(SELECT 1 FROM information_schema.TABLE_CONSTRAINTS WHERE CONSTRAINT_SCHEMA=DATABASE() AND TABLE_NAME='market_trips' AND CONSTRAINT_NAME='chk_mt_mode'),
 "ALTER TABLE market_trips ADD CONSTRAINT chk_mt_mode CHECK (accounting_mode IN ('LEGACY','BOOK'))", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(NOT EXISTS(SELECT 1 FROM information_schema.STATISTICS WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='market_trips' AND INDEX_NAME='uq_mt_id_mode'),
 "ALTER TABLE market_trips ADD UNIQUE KEY uq_mt_id_mode (id, accounting_mode)", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

-- 2. the entry mode, its keys and the kind-per-mode rule
SET @masgold_sql := IF(NOT EXISTS(SELECT 1 FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='market_trip_entries' AND COLUMN_NAME='trip_mode'),
 "ALTER TABLE market_trip_entries ADD COLUMN trip_mode VARCHAR(8) NOT NULL DEFAULT 'LEGACY' AFTER trip_id", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(NOT EXISTS(SELECT 1 FROM information_schema.STATISTICS WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='market_trip_entries' AND INDEX_NAME='uq_mte_id_mode'),
 "ALTER TABLE market_trip_entries ADD UNIQUE KEY uq_mte_id_mode (id, trip_mode)", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(NOT EXISTS(SELECT 1 FROM information_schema.TABLE_CONSTRAINTS WHERE CONSTRAINT_SCHEMA=DATABASE() AND TABLE_NAME='market_trip_entries' AND CONSTRAINT_NAME='fk_mte_trip_mode'),
 "ALTER TABLE market_trip_entries ADD CONSTRAINT fk_mte_trip_mode FOREIGN KEY (trip_id, trip_mode) REFERENCES market_trips(id, accounting_mode) ON UPDATE RESTRICT ON DELETE RESTRICT", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(EXISTS (SELECT 1 FROM information_schema.CHECK_CONSTRAINTS WHERE CONSTRAINT_SCHEMA = DATABASE()
 AND CONSTRAINT_NAME = 'chk_mte_kind' AND CHECK_CLAUSE NOT LIKE '%BK_RETURN%'),
 "ALTER TABLE market_trip_entries DROP CHECK chk_mte_kind", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(NOT EXISTS (SELECT 1 FROM information_schema.TABLE_CONSTRAINTS WHERE CONSTRAINT_SCHEMA = DATABASE()
 AND TABLE_NAME = 'market_trip_entries' AND CONSTRAINT_NAME = 'chk_mte_kind'),
 "ALTER TABLE market_trip_entries ADD CONSTRAINT chk_mte_kind CHECK (entry_kind IN ('ISSUE','RETURN','MELT','LOT_RETURN','LOT_ISSUE','HEAT','SUPPLIER_PAY','GOLD_SETTLE','LOT_SETTLE','LOT_HOLD','HOLD_RETURN','LOGO_HOLD','HOLD_BACK','GOLD_SALE','GOLD_BUY','BK_ISSUE','BK_BUY','BK_SELL','BK_RETURN'))", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(NOT EXISTS (SELECT 1 FROM information_schema.TABLE_CONSTRAINTS WHERE CONSTRAINT_SCHEMA = DATABASE()
 AND TABLE_NAME = 'market_trip_entries' AND CONSTRAINT_NAME = 'chk_mte_mode_kind'),
 "ALTER TABLE market_trip_entries ADD CONSTRAINT chk_mte_mode_kind CHECK ((trip_mode = 'LEGACY' AND entry_kind IN ('ISSUE','RETURN','MELT','LOT_RETURN','LOT_ISSUE','HEAT','SUPPLIER_PAY','GOLD_SETTLE','LOT_SETTLE','LOT_HOLD','HOLD_RETURN','LOGO_HOLD','HOLD_BACK','GOLD_SALE','GOLD_BUY')) OR (trip_mode = 'BOOK' AND entry_kind IN ('ISSUE','RETURN','SUPPLIER_PAY','BK_ISSUE','BK_BUY','BK_SELL','BK_RETURN')))", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

-- 3. the legacy gold legs exist only for LEGACY entries (a BOOK entry can never carry one)
SET @masgold_sql := IF(NOT EXISTS(SELECT 1 FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='market_trip_entry_gold' AND COLUMN_NAME='trip_mode'),
 "ALTER TABLE market_trip_entry_gold ADD COLUMN trip_mode VARCHAR(8) NOT NULL DEFAULT 'LEGACY' AFTER entry_id", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(NOT EXISTS(SELECT 1 FROM information_schema.TABLE_CONSTRAINTS WHERE CONSTRAINT_SCHEMA=DATABASE() AND TABLE_NAME='market_trip_entry_gold' AND CONSTRAINT_NAME='chk_mteg_mode'),
 "ALTER TABLE market_trip_entry_gold ADD CONSTRAINT chk_mteg_mode CHECK (trip_mode = 'LEGACY')", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(NOT EXISTS(SELECT 1 FROM information_schema.TABLE_CONSTRAINTS WHERE CONSTRAINT_SCHEMA=DATABASE() AND TABLE_NAME='market_trip_entry_gold' AND CONSTRAINT_NAME='fk_mteg_mode'),
 "ALTER TABLE market_trip_entry_gold ADD CONSTRAINT fk_mteg_mode FOREIGN KEY (entry_id, trip_mode) REFERENCES market_trip_entries(id, trip_mode) ON UPDATE RESTRICT ON DELETE RESTRICT", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

-- 4. the book lines (physical facts + analytical evidence + posted equivalent)
CREATE TABLE IF NOT EXISTS market_trip_book_lines (
 entry_id BIGINT UNSIGNED NOT NULL,
 line_no SMALLINT UNSIGNED NOT NULL,
 trip_id BIGINT UNSIGNED NOT NULL,
 trip_mode VARCHAR(8) NOT NULL DEFAULT 'BOOK',
 entry_kind VARCHAR(12) NOT NULL,
 direction VARCHAR(3) NOT NULL,
 weight_grams DECIMAL(14,3) NOT NULL,
 basis VARCHAR(12) NOT NULL,
 karat_id TINYINT UNSIGNED DEFAULT NULL,
 karat_value TINYINT UNSIGNED DEFAULT NULL,
 fineness_permille DECIMAL(5,1) DEFAULT NULL,
 eq21_analytical DECIMAL(18,6) NOT NULL,
 eq21_posted DECIMAL(14,3) NOT NULL,
 posting_difference DECIMAL(12,6) NOT NULL,
 signed_eq21_posted DECIMAL(14,3) NOT NULL,
 line_price DECIMAL(16,2) DEFAULT NULL,
 scrap_movement_id BIGINT UNSIGNED DEFAULT NULL,
 created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
 PRIMARY KEY (entry_id, line_no),
 UNIQUE KEY uq_mtbl_scrap (scrap_movement_id),
 UNIQUE KEY uq_mtbl_item (entry_id, line_no, entry_kind, basis),
 KEY idx_mtbl_trip (trip_id, direction),
 KEY idx_mtbl_karat (karat_id),
 CONSTRAINT fk_mtbl_entry FOREIGN KEY (entry_id, entry_kind, trip_id) REFERENCES market_trip_entries(id, entry_kind, trip_id),
 CONSTRAINT fk_mtbl_mode FOREIGN KEY (entry_id, trip_mode) REFERENCES market_trip_entries(id, trip_mode) ON UPDATE RESTRICT ON DELETE RESTRICT,
 CONSTRAINT fk_mtbl_karat FOREIGN KEY (karat_id) REFERENCES karats(id),
 CONSTRAINT fk_mtbl_scrap FOREIGN KEY (scrap_movement_id) REFERENCES old_gold_movements(id),
 CONSTRAINT chk_mtbl_mode CHECK (trip_mode = 'BOOK'),
 CONSTRAINT chk_mtbl_kind CHECK ((entry_kind IN ('BK_ISSUE','BK_BUY') AND direction = 'IN') OR (entry_kind IN ('BK_SELL','BK_RETURN') AND direction = 'OUT')),
 CONSTRAINT chk_mtbl_weight CHECK (weight_grams > 0),
 CONSTRAINT chk_mtbl_basis CHECK (
  (basis = 'MASTER_KARAT' AND karat_id IS NOT NULL AND karat_value IS NOT NULL AND karat_value > 0 AND fineness_permille IS NULL)
  OR (basis = 'FINENESS' AND karat_id IS NULL AND karat_value IS NULL AND fineness_permille IS NOT NULL AND fineness_permille > 0 AND fineness_permille <= 1000)),
 CONSTRAINT chk_mtbl_posted CHECK (
  eq21_posted >= 0 AND eq21_analytical >= 0 AND ABS(posting_difference) <= 0.0005
  AND ABS((eq21_posted - eq21_analytical) - posting_difference) <= 0.000001
  AND ((direction = 'IN' AND signed_eq21_posted = eq21_posted) OR (direction = 'OUT' AND signed_eq21_posted = -eq21_posted))),
 CONSTRAINT chk_mtbl_evidence CHECK (
  (basis = 'MASTER_KARAT' AND ABS(eq21_analytical * 21 - weight_grams * karat_value) <= 0.0000105)
  OR (basis = 'FINENESS' AND ABS(eq21_analytical * 875 - weight_grams * fineness_permille) <= 0.0004375)),
 CONSTRAINT chk_mtbl_leg CHECK (
  (entry_kind = 'BK_ISSUE' AND basis = 'MASTER_KARAT' AND scrap_movement_id IS NOT NULL AND line_price IS NULL)
  OR (entry_kind = 'BK_RETURN' AND basis = 'MASTER_KARAT' AND scrap_movement_id IS NOT NULL AND line_price IS NULL)
  OR (entry_kind = 'BK_RETURN' AND basis = 'FINENESS' AND scrap_movement_id IS NULL AND line_price IS NULL)
  OR (entry_kind IN ('BK_BUY','BK_SELL') AND scrap_movement_id IS NULL AND line_price IS NOT NULL AND line_price > 0))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- 5. the trade document of a BOOK purchase / sale (counterparty mandatory and immutable; the cash never leaves the custody)
CREATE TABLE IF NOT EXISTS market_trip_book_trades (
 entry_id BIGINT UNSIGNED NOT NULL,
 entry_kind VARCHAR(12) NOT NULL,
 counterparty_kind VARCHAR(16) NOT NULL,
 party_id INT UNSIGNED DEFAULT NULL,
 counterparty_name VARCHAR(150) NOT NULL,
 counterparty_name_key VARCHAR(150) NOT NULL,
 total_price DECIMAL(16,2) NOT NULL,
 reference_total DECIMAL(16,2) DEFAULT NULL,
 deviation_percent DECIMAL(9,2) DEFAULT NULL,
 price_warning_acknowledged TINYINT(1) NOT NULL DEFAULT 0,
 trade_date DATE NOT NULL,
 reference VARCHAR(100) DEFAULT NULL,
 created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
 PRIMARY KEY (entry_id),
 KEY idx_mtbt_party (party_id),
 KEY idx_mtbt_key (counterparty_name_key),
 KEY idx_mtbt_date (trade_date),
 CONSTRAINT fk_mtbt_entry FOREIGN KEY (entry_id, entry_kind) REFERENCES market_trip_entries(id, entry_kind),
 CONSTRAINT fk_mtbt_party FOREIGN KEY (party_id) REFERENCES suppliers(id),
 CONSTRAINT chk_mtbt_kind CHECK (entry_kind IN ('BK_BUY','BK_SELL')),
 CONSTRAINT chk_mtbt_party CHECK (
  ((counterparty_kind = 'REGISTERED_PARTY' AND party_id IS NOT NULL) OR (counterparty_kind = 'THIRD_PARTY' AND party_id IS NULL))
  AND CHAR_LENGTH(TRIM(counterparty_name)) > 0 AND CHAR_LENGTH(TRIM(counterparty_name_key)) > 0),
 CONSTRAINT chk_mtbt_total CHECK (total_price > 0),
 CONSTRAINT chk_mtbt_warning CHECK (
  (reference_total IS NULL AND deviation_percent IS NULL)
  OR (reference_total IS NOT NULL AND deviation_percent IS NOT NULL))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- 6. the assayed return stock ledger (insert-only; a balance is the SUM of every movement; reversals are explicit compensation rows)
CREATE TABLE IF NOT EXISTS shop_assayed_stock_movements (
 id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
 item_entry_id BIGINT UNSIGNED NOT NULL,
 item_line_no SMALLINT UNSIGNED NOT NULL,
 item_entry_kind VARCHAR(12) NOT NULL DEFAULT 'BK_RETURN',
 item_basis VARCHAR(12) NOT NULL DEFAULT 'FINENESS',
 movement_type VARCHAR(24) NOT NULL,
 grams_delta DECIMAL(14,3) NOT NULL,
 eq21_posted_delta DECIMAL(14,3) NOT NULL,
 eq21_analytical DECIMAL(18,6) NOT NULL,
 posting_difference DECIMAL(12,6) NOT NULL,
 eq21_independent DECIMAL(14,3) DEFAULT NULL,
 posting_basis VARCHAR(11) NOT NULL DEFAULT 'INDEPENDENT',
 source_entry_id BIGINT UNSIGNED NOT NULL,
 correction_id BIGINT UNSIGNED DEFAULT NULL,
 created_by_user_id INT UNSIGNED DEFAULT NULL,
 created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
 PRIMARY KEY (id),
 UNIQUE KEY uq_sasm_item_type_source (item_entry_id, item_line_no, movement_type, source_entry_id),
 UNIQUE KEY uq_sasm_correction_item (correction_id, item_entry_id, item_line_no),
 KEY idx_sasm_item (item_entry_id, item_line_no, id),
 KEY idx_sasm_source (source_entry_id),
 CONSTRAINT fk_sasm_item FOREIGN KEY (item_entry_id, item_line_no, item_entry_kind, item_basis) REFERENCES market_trip_book_lines(entry_id, line_no, entry_kind, basis),
 CONSTRAINT fk_sasm_source FOREIGN KEY (source_entry_id) REFERENCES market_trip_entries(id),
 CONSTRAINT fk_sasm_correction FOREIGN KEY (correction_id) REFERENCES transaction_corrections(id),
 CONSTRAINT fk_sasm_user FOREIGN KEY (created_by_user_id) REFERENCES users(id),
 CONSTRAINT chk_sasm_item CHECK (item_entry_kind = 'BK_RETURN' AND item_basis = 'FINENESS'),
 CONSTRAINT chk_sasm_type CHECK (movement_type IN ('RECEIVE','RECEIVE_REVERSAL','ISSUE','ISSUE_REVERSAL')),
 CONSTRAINT chk_sasm_basis CHECK (posting_basis IN ('INDEPENDENT','ALLOCATED')),
 CONSTRAINT chk_sasm_shape CHECK (
  (movement_type = 'RECEIVE' AND grams_delta > 0 AND eq21_posted_delta >= 0 AND correction_id IS NULL AND source_entry_id = item_entry_id
    AND posting_basis = 'INDEPENDENT' AND eq21_independent = eq21_posted_delta)
  OR (movement_type = 'RECEIVE_REVERSAL' AND grams_delta < 0 AND eq21_posted_delta <= 0 AND correction_id IS NOT NULL AND source_entry_id = item_entry_id
    AND posting_basis = 'INDEPENDENT' AND eq21_independent = -eq21_posted_delta)
  OR (movement_type = 'ISSUE' AND grams_delta < 0 AND eq21_posted_delta <= 0 AND correction_id IS NULL AND source_entry_id <> item_entry_id
    AND eq21_independent IS NOT NULL)
  OR (movement_type = 'ISSUE_REVERSAL' AND grams_delta > 0 AND eq21_posted_delta >= 0 AND correction_id IS NOT NULL AND source_entry_id <> item_entry_id
    AND eq21_independent IS NOT NULL)),
 CONSTRAINT chk_sasm_evidence CHECK (eq21_analytical >= 0 AND ABS((ABS(eq21_posted_delta) - eq21_analytical) - posting_difference) <= 0.000001)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

INSERT INTO schema_migrations(version,name) VALUES (56,'Market-trip BOOK core: immutable LEGACY/BOOK trip mode with mode-bound entries, book lines, trade documents and the assayed return stock ledger')
 ON DUPLICATE KEY UPDATE name=VALUES(name);
