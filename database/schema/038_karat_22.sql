-- 22K support: the 22K master row and the provenance of derived gold-price quotes.
--
-- Master: 22K is inserted ONLY when absent. An existing row keeps its identity and its descriptive / purity fields (nothing is
-- overwritten); if it is inactive it is REACTIVATED (explicitly approved by the OWNER: only is_active changes). The outcome
-- (INSERTED / ALREADY_ACTIVE / REACTIVATED) and any inconsistency of an existing row (purity other than 0.916667, name other than
-- '22K') are recorded in the audit log, once.
--
-- Pricing: a 22K quote is DERIVED from the approved 24K quote of the same published group (price24 * 22 / 24, 4 decimals,
-- ROUND_HALF_UP). The new nullable gold_price_quotes.derived_from_quote_id names the exact 24K quote it came from; it may be set
-- only on DERIVED quotes. Existing quotes keep NULL (history is never rewritten) and invoice snapshots keep their quote_id.
-- Every step is guarded, so a re-run changes nothing.
SET @masgold_sql = IF(EXISTS(SELECT 1 FROM schema_migrations WHERE version=37),
 'DO 0', 'SELECT * FROM MIGRATION_038_REFUSED_missing_037');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @k22_first := NOT EXISTS(SELECT 1 FROM schema_migrations WHERE version = 38);
SET @k22_state := (SELECT CASE WHEN COUNT(*) = 0 THEN 'INSERTED' WHEN MAX(is_active) = 1 THEN 'ALREADY_ACTIVE' ELSE 'REACTIVATED' END
                   FROM karats WHERE karat_value = 22);
SET @k22_issue := (SELECT CASE WHEN COUNT(*) = 0 THEN NULL
                               WHEN MAX(purity) <> 0.916667 OR MAX(name) <> '22K' THEN 'EXISTING_ROW_DIFFERS_FROM_EXPECTED_PURITY_OR_NAME'
                               ELSE NULL END
                   FROM karats WHERE karat_value = 22);

INSERT INTO karats (karat_value, purity, name, sort_order)
 SELECT 22, 0.916667, '22K', 4 FROM DUAL WHERE NOT EXISTS (SELECT 1 FROM karats WHERE karat_value = 22);
UPDATE karats SET is_active = TRUE WHERE karat_value = 22 AND is_active = FALSE;

INSERT INTO audit_log (user_id, event_type, entity_type, entity_id, entity_reference, description, metadata_json, occurred_at)
 SELECT NULL, 'KARAT_22_SUPPORT', 'KARAT', k.id, '22K', CONCAT('22K master row: ', @k22_state),
        JSON_OBJECT('state', @k22_state, 'issue', @k22_issue, 'purity', CAST(k.purity AS CHAR), 'name', k.name), NOW()
 FROM karats k WHERE k.karat_value = 22 AND @k22_first;

SET @masgold_sql := IF(NOT EXISTS (SELECT 1 FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = DATABASE()
 AND TABLE_NAME = 'gold_price_quotes' AND COLUMN_NAME = 'derived_from_quote_id'),
 "ALTER TABLE gold_price_quotes ADD COLUMN derived_from_quote_id BIGINT UNSIGNED DEFAULT NULL AFTER notes", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(NOT EXISTS (SELECT 1 FROM information_schema.STATISTICS WHERE TABLE_SCHEMA = DATABASE()
 AND TABLE_NAME = 'gold_price_quotes' AND INDEX_NAME = 'idx_gold_quotes_derived'),
 "ALTER TABLE gold_price_quotes ADD KEY idx_gold_quotes_derived (derived_from_quote_id)", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(NOT EXISTS (SELECT 1 FROM information_schema.TABLE_CONSTRAINTS WHERE CONSTRAINT_SCHEMA = DATABASE()
 AND TABLE_NAME = 'gold_price_quotes' AND CONSTRAINT_NAME = 'fk_gold_quotes_derived'),
 "ALTER TABLE gold_price_quotes ADD CONSTRAINT fk_gold_quotes_derived FOREIGN KEY (derived_from_quote_id) REFERENCES gold_price_quotes(id)",
 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(NOT EXISTS (SELECT 1 FROM information_schema.TABLE_CONSTRAINTS WHERE CONSTRAINT_SCHEMA = DATABASE()
 AND TABLE_NAME = 'gold_price_quotes' AND CONSTRAINT_NAME = 'chk_gold_quote_derived'),
 "ALTER TABLE gold_price_quotes ADD CONSTRAINT chk_gold_quote_derived CHECK (derived_from_quote_id IS NULL OR quote_method = 'DERIVED')",
 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

INSERT INTO schema_migrations(version,name) VALUES (38,'22K support: master row and derived gold-price quote provenance')
 ON DUPLICATE KEY UPDATE name=VALUES(name);
