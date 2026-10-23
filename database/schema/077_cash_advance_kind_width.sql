-- Fix for 076: the kind THIRD_PARTY (an outside lender) is 11 characters but cash_advances.kind was VARCHAR(10) (found by the first real-MySQL insert on the rehearsal database).
-- Forward only: widens the column to VARCHAR(12); every stored value is kept. Prepared; guarded (re-run safe).
SET @masgold_sql = IF(EXISTS(SELECT 1 FROM schema_migrations WHERE version=76),
 'DO 0', 'SELECT * FROM MIGRATION_077_REFUSED_missing_076');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF((SELECT CHARACTER_MAXIMUM_LENGTH FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='cash_advances' AND COLUMN_NAME='kind') < 12,
 'ALTER TABLE cash_advances MODIFY COLUMN kind VARCHAR(12) NOT NULL', 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

INSERT INTO schema_migrations(version,name) VALUES (77,'Cash advances: kind column wide enough for THIRD_PARTY')
 ON DUPLICATE KEY UPDATE name=VALUES(name);
