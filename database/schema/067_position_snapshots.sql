-- Gold / cash position SNAPSHOTS for the gold profit report. Prepared; NOT applied to any database by the task that wrote it.
--   position_snapshots: insert-only "stocktaking sheets". One OPENING row (the owner's initial balance, entered once from Settings) and DAILY / MANUAL rows taken by the application from the
--   consolidated gold position (services/gold_position_service.py) plus the net supplier gold and the cash / bank balances at that moment. All gold is in 21K-equivalent grams.
--   gold_suppliers_eq21 is SIGNED: positive = the suppliers hold gold for the shop (counted as shop gold), negative = the shop owes the suppliers (subtracted).
--   The profit of a period is the difference between two snapshots; no historical balance is ever recomputed. Nothing here edits a ledger.
-- No existing row is rewritten. The CHECK expressions have NOT been executed on a MySQL server before the rehearsal gate. Every step is guarded (re-run safe).
SET @masgold_sql = IF(EXISTS(SELECT 1 FROM schema_migrations WHERE version=66),
 'DO 0', 'SELECT * FROM MIGRATION_067_REFUSED_missing_066');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

CREATE TABLE IF NOT EXISTS position_snapshots (
 id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
 snapshot_kind VARCHAR(10) NOT NULL,
 taken_at DATETIME NOT NULL,
 gold_shop_eq21 DECIMAL(16,3) NOT NULL DEFAULT 0,
 gold_custody_eq21 DECIMAL(16,3) NOT NULL DEFAULT 0,
 gold_intermediary_eq21 DECIMAL(16,3) NOT NULL DEFAULT 0,
 gold_book_eq21 DECIMAL(16,3) NOT NULL DEFAULT 0,
 gold_suppliers_eq21 DECIMAL(16,3) NOT NULL DEFAULT 0,
 gold_total_eq21 DECIMAL(16,3) NOT NULL,
 unknown_grams DECIMAL(16,3) NOT NULL DEFAULT 0,
 cash_egp DECIMAL(16,2) NOT NULL DEFAULT 0,
 bank_egp DECIMAL(16,2) NOT NULL DEFAULT 0,
 created_by_user_id INT UNSIGNED DEFAULT NULL,
 created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
 opening_flag TINYINT GENERATED ALWAYS AS (IF(snapshot_kind = 'OPENING', 1, NULL)) STORED,
 PRIMARY KEY (id),
 UNIQUE KEY uq_position_snapshot_one_opening (opening_flag),
 KEY idx_position_snapshots_taken (taken_at),
 CONSTRAINT chk_pos_snapshot_kind CHECK (snapshot_kind IN ('OPENING', 'DAILY', 'MANUAL')),
 CONSTRAINT chk_pos_snapshot_total CHECK (gold_total_eq21 = gold_shop_eq21 + gold_custody_eq21 + gold_intermediary_eq21 + gold_book_eq21 + gold_suppliers_eq21),
 CONSTRAINT chk_pos_snapshot_unknown CHECK (unknown_grams >= 0),
 CONSTRAINT fk_pos_snapshot_user FOREIGN KEY (created_by_user_id) REFERENCES users(id) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

INSERT INTO schema_migrations(version,name) VALUES (67,'Position snapshots (opening balance and daily stocktaking) for the gold profit report')
 ON DUPLICATE KEY UPDATE name=VALUES(name);
