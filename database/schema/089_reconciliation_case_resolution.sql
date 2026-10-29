-- Manager review of an unidentified (F5) sale (owner 2026-10-29): the review records HOW the case was settled.
--   MATCH_STOCK = the real piece was found in stock and linked by its barcode (the piece leaves the stock as SOLD);
--   FROM_SCRAP  = the weight came out of the Scrap of the sold karat (a COUNTED sale-out movement);
--   NO_SOURCE   = no source exists anywhere: nothing moves, the written reason is the record.
-- Only two columns and a CHECK are added; the existing statuses (MATCHED / CLOSED / CANCELLED) and events are used as they are.
SET @masgold_sql = IF(EXISTS(SELECT 1 FROM schema_migrations WHERE version=88),
 'DO 0', 'SELECT * FROM MIGRATION_089_REFUSED_missing_088');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(NOT EXISTS(SELECT 1 FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=DATABASE()
 AND TABLE_NAME='inventory_reconciliation_cases' AND COLUMN_NAME='resolution_code'),
 "ALTER TABLE inventory_reconciliation_cases ADD COLUMN resolution_code VARCHAR(20) NULL AFTER matched_inventory_item_id", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(NOT EXISTS(SELECT 1 FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=DATABASE()
 AND TABLE_NAME='inventory_reconciliation_cases' AND COLUMN_NAME='resolution_notes'),
 "ALTER TABLE inventory_reconciliation_cases ADD COLUMN resolution_notes VARCHAR(500) NULL AFTER resolution_code", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(NOT EXISTS(SELECT 1 FROM information_schema.TABLE_CONSTRAINTS WHERE CONSTRAINT_SCHEMA=DATABASE()
 AND TABLE_NAME='inventory_reconciliation_cases' AND CONSTRAINT_NAME='chk_reconciliation_resolution_code'),
 "ALTER TABLE inventory_reconciliation_cases ADD CONSTRAINT chk_reconciliation_resolution_code CHECK (
   resolution_code IS NULL OR resolution_code IN ('MATCH_STOCK', 'FROM_SCRAP', 'NO_SOURCE'))", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

INSERT INTO schema_migrations(version,name) VALUES (89,'Reconciliation case resolution')
 ON DUPLICATE KEY UPDATE name=VALUES(name);
