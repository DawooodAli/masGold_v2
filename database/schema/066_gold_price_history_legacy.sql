-- Gold price HISTORY of the old shop (the old price table, hand-typed daily prices) imported ONCE as an immutable daily history for the gold price indicators. Prepared; NOT applied to any database by the task that wrote it.
--   gold_price_history_legacy: one row per (day, karat) = the LAST accepted entry of the day, cleaned by services/price_history_rules.py (zeros and absurd values rejected, a swapped buy / sell pair
--   corrected and flagged, `entries_in_day` kept). Karat 21 only (the old table is 21K). Insert-only evidence: nothing in the application edits it; the live prices (gold_price_quotes) are untouched.
--   shop_buy = what the shop PAYS (old column `low`), shop_sell = what the shop CHARGES (old column `high`).
-- No existing row is rewritten. The CHECK expressions have NOT been executed on a MySQL server before the rehearsal gate. Every step is guarded (re-run safe).
SET @masgold_sql = IF(EXISTS(SELECT 1 FROM schema_migrations WHERE version=65),
 'DO 0', 'SELECT * FROM MIGRATION_066_REFUSED_missing_065');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

CREATE TABLE IF NOT EXISTS gold_price_history_legacy (
 price_date DATE NOT NULL,
 karat_value TINYINT UNSIGNED NOT NULL,
 shop_buy DECIMAL(14,4) NOT NULL,
 shop_sell DECIMAL(14,4) NOT NULL,
 source VARCHAR(20) NOT NULL DEFAULT 'GOLDDB',
 entries_in_day SMALLINT UNSIGNED NOT NULL,
 swapped TINYINT(1) NOT NULL DEFAULT 0,
 imported_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
 PRIMARY KEY (price_date, karat_value),
 CONSTRAINT chk_gphl_karat CHECK (karat_value IN (18, 21, 22, 24)),
 CONSTRAINT chk_gphl_prices CHECK (shop_buy > 0 AND shop_sell >= shop_buy),
 CONSTRAINT chk_gphl_source CHECK (source = 'GOLDDB'),
 CONSTRAINT chk_gphl_entries CHECK (entries_in_day >= 1)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

INSERT INTO schema_migrations(version,name) VALUES (66,'Gold price history of the old shop as an immutable daily history for the price indicators')
 ON DUPLICATE KEY UPDATE name=VALUES(name);
