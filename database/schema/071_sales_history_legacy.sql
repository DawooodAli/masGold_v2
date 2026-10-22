-- Sales history of the old shop, imported ONCE as an immutable DAILY history for the demand / seasonality report. Prepared; NOT applied to any database by the task that wrote it.
--   sales_history_legacy: one row per day = the distinct bills of that day and the sum of the grams of the bills that had a usable weight (services/sales_history_rules.py). The old table has no karat,
--   so the grams are the old shop's sold grams of every karat together; a day whose bills had no weight (2024) keeps `bills` and zero grams with bills_with_weight = 0 (the weights are unknown, never guessed).
--   Insert-only evidence: nothing in the application edits it. The report uses it for the days BEFORE the owner's initial balance (the start of the new system) and the new invoices from then on.
-- No existing row is rewritten. The CHECK expressions have NOT been executed on a MySQL server before the rehearsal gate. Every step is guarded (re-run safe).
SET @masgold_sql = IF(EXISTS(SELECT 1 FROM schema_migrations WHERE version=70),
 'DO 0', 'SELECT * FROM MIGRATION_071_REFUSED_missing_070');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

CREATE TABLE IF NOT EXISTS sales_history_legacy (
 sale_date DATE NOT NULL,
 bills SMALLINT UNSIGNED NOT NULL,
 bills_with_weight SMALLINT UNSIGNED NOT NULL,
 grams DECIMAL(14,3) NOT NULL DEFAULT 0,
 source VARCHAR(20) NOT NULL DEFAULT 'GOLDDB',
 imported_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
 PRIMARY KEY (sale_date),
 CONSTRAINT chk_slh_bills CHECK (bills >= 1 AND bills_with_weight <= bills),
 CONSTRAINT chk_slh_grams CHECK (grams >= 0 AND (bills_with_weight > 0 OR grams = 0)),
 CONSTRAINT chk_slh_source CHECK (source = 'GOLDDB')
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

INSERT INTO schema_migrations(version,name) VALUES (71,'Sales history of the old shop as an immutable daily history for the demand report')
 ON DUPLICATE KEY UPDATE name=VALUES(name);
