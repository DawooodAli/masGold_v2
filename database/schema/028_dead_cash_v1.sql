-- Dead Cash V1: receivable write-off on the EXISTING scaffolding (005 dead_cash_writeoffs / customer_flags,
-- DEAD_CASH_WRITE_OFF / DEAD_CASH_REVERSAL ledger types, DEAD_CASH_MANAGE / DEAD_CASH_REVERSE permissions).
-- Adds only: request idempotency and database checks for the V1 rule — a write-off reduces a POSITIVE
-- receivable (على العميل) and never crosses zero; the reason is mandatory; the reversal state is consistent.
-- No money ledger (cash / bank) is involved. No permission, grant or ledger type is added or changed.
SET @masgold_sql = IF(EXISTS(SELECT 1 FROM schema_migrations WHERE version=27),
 'DO 0', 'SELECT * FROM MIGRATION_028_REFUSED_missing_027');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

-- Existing rows must already satisfy the V1 rules (none exist before V1); never rewrite history.
SET @masgold_sql = IF(EXISTS(SELECT 1 FROM dead_cash_writeoffs WHERE writeoff_direction<>'CUSTOMER_OWES_SHOP'
   OR balance_before<=0 OR balance_after<>balance_before-amount OR balance_after<0 OR CHAR_LENGTH(TRIM(reason))=0),
 'SELECT * FROM MIGRATION_028_REFUSED_existing_writeoffs_violate_v1', 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(NOT EXISTS(SELECT 1 FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=DATABASE()
 AND TABLE_NAME='dead_cash_writeoffs' AND COLUMN_NAME='request_key'),
 "ALTER TABLE dead_cash_writeoffs ADD COLUMN request_key CHAR(36) DEFAULT NULL AFTER customer_id", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(NOT EXISTS(SELECT 1 FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=DATABASE()
 AND TABLE_NAME='dead_cash_writeoffs' AND COLUMN_NAME='request_hash'),
 "ALTER TABLE dead_cash_writeoffs ADD COLUMN request_hash CHAR(64) DEFAULT NULL AFTER request_key", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(NOT EXISTS(SELECT 1 FROM information_schema.STATISTICS WHERE TABLE_SCHEMA=DATABASE()
 AND TABLE_NAME='dead_cash_writeoffs' AND INDEX_NAME='uq_dead_cash_request'),
 "ALTER TABLE dead_cash_writeoffs ADD UNIQUE KEY uq_dead_cash_request (request_key)", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(NOT EXISTS(SELECT 1 FROM information_schema.TABLE_CONSTRAINTS WHERE CONSTRAINT_SCHEMA=DATABASE()
 AND TABLE_NAME='dead_cash_writeoffs' AND CONSTRAINT_NAME='chk_dead_cash_v1_receivable'),
 "ALTER TABLE dead_cash_writeoffs ADD CONSTRAINT chk_dead_cash_v1_receivable CHECK (
   writeoff_direction='CUSTOMER_OWES_SHOP' AND balance_before>0
   AND balance_after=balance_before-amount AND balance_after>=0)", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(NOT EXISTS(SELECT 1 FROM information_schema.TABLE_CONSTRAINTS WHERE CONSTRAINT_SCHEMA=DATABASE()
 AND TABLE_NAME='dead_cash_writeoffs' AND CONSTRAINT_NAME='chk_dead_cash_reason'),
 "ALTER TABLE dead_cash_writeoffs ADD CONSTRAINT chk_dead_cash_reason CHECK (CHAR_LENGTH(TRIM(reason))>0)", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(NOT EXISTS(SELECT 1 FROM information_schema.TABLE_CONSTRAINTS WHERE CONSTRAINT_SCHEMA=DATABASE()
 AND TABLE_NAME='dead_cash_writeoffs' AND CONSTRAINT_NAME='chk_dead_cash_reversal_state'),
 "ALTER TABLE dead_cash_writeoffs ADD CONSTRAINT chk_dead_cash_reversal_state CHECK (
   (is_reversed=FALSE AND reversed_at IS NULL AND reversal_reason IS NULL)
   OR (is_reversed=TRUE AND reversed_at IS NOT NULL AND reversal_reason IS NOT NULL
       AND CHAR_LENGTH(TRIM(reversal_reason))>0))", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

INSERT INTO schema_migrations(version,name) VALUES (28,'Dead Cash V1: receivable write-off idempotency and checks')
 ON DUPLICATE KEY UPDATE name=VALUES(name);
