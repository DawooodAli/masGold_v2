-- Distinguish Scrap-backed issue-weight corrections from an evidenced physical increase with no Scrap movement.
SET @masgold_sql = IF(EXISTS(SELECT 1 FROM schema_migrations WHERE version = 78),
 'DO 0', 'SELECT * FROM MIGRATION_079_REFUSED_missing_078');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(NOT EXISTS(SELECT 1 FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=DATABASE()
 AND TABLE_NAME='market_trip_settle_adjust' AND COLUMN_NAME='source_kind'),
 "ALTER TABLE market_trip_settle_adjust ADD COLUMN source_kind VARCHAR(12) NOT NULL DEFAULT 'SHOP_SCRAP' AFTER direction", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(NOT EXISTS(SELECT 1 FROM information_schema.TABLE_CONSTRAINTS WHERE CONSTRAINT_SCHEMA=DATABASE()
 AND TABLE_NAME='market_trip_settle_adjust' AND CONSTRAINT_NAME='chk_mtsa_source_kind'),
 "ALTER TABLE market_trip_settle_adjust ADD CONSTRAINT chk_mtsa_source_kind CHECK (
  (direction = 'INCREASE' AND source_kind IN ('SHOP_SCRAP','UNSOURCED'))
  OR (direction = 'DECREASE' AND source_kind = 'SHOP_SCRAP'))", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

INSERT INTO schema_migrations(version,name) VALUES (79,'Market-custody issue-weight correction source')
 ON DUPLICATE KEY UPDATE name=VALUES(name);
