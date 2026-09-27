-- ============================================================
-- masGold v2
-- Migration 017: real payment accounts for Billing
--
-- Invoice money is posted to the existing Cash/Bank ledgers
-- (007): physical cash to cash_movements, every other method to
-- bank_movements on a real, configured account. A mobile wallet
-- is such an account too, so:
--   1. bank_accounts.account_type  BANK | MOBILE_WALLET
--      (INSTAPAY / BANK_TRANSFER use BANK accounts,
--       MOBILE_WALLET uses MOBILE_WALLET accounts)
--   2. MOBILE_WALLET now requires its account, like INSTAPAY
--
-- No account, balance or movement is created here. Existing
-- accounts stay BANK. The target database is chosen by the
-- caller; this file never names a database. Re-runnable.
--
-- Rollback: rollback/017_payment_accounts_rollback.sql
-- ============================================================


SET @masgold_saved_sql_mode := @@SESSION.sql_mode;
SET SESSION sql_mode = 'ONLY_FULL_GROUP_BY,STRICT_TRANS_TABLES,NO_ZERO_IN_DATE,NO_ZERO_DATE,ERROR_FOR_DIVISION_BY_ZERO,NO_ENGINE_SUBSTITUTION';


-- ============================================================
-- 0. PREFLIGHT
-- ============================================================

SET @masgold_sql := IF(
    NOT EXISTS (SELECT 1 FROM schema_migrations WHERE version = 16),
    'SELECT 1 FROM MIGRATION_017_REFUSED_migration_016_missing',
    'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;


-- ============================================================
-- 1. ACCOUNT TYPE
-- ============================================================

SET @masgold_sql := IF(
    NOT EXISTS (SELECT 1 FROM information_schema.COLUMNS
                 WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'bank_accounts'
                   AND COLUMN_NAME = 'account_type'),
    "ALTER TABLE bank_accounts ADD COLUMN account_type VARCHAR(20) NOT NULL DEFAULT 'BANK' AFTER name",
    'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(
    NOT EXISTS (SELECT 1 FROM information_schema.TABLE_CONSTRAINTS
                 WHERE CONSTRAINT_SCHEMA = DATABASE() AND TABLE_NAME = 'bank_accounts'
                   AND CONSTRAINT_NAME = 'chk_bank_account_type'),
    "ALTER TABLE bank_accounts ADD CONSTRAINT chk_bank_account_type CHECK (
        account_type IN ('BANK', 'MOBILE_WALLET')
    )",
    'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;


-- ============================================================
-- 2. MOBILE_WALLET needs its real account
-- ============================================================

UPDATE payment_methods
   SET requires_bank_account = TRUE
 WHERE code = 'MOBILE_WALLET'
   AND requires_bank_account = FALSE;


-- ============================================================
-- REGISTER MIGRATION
-- ============================================================

INSERT INTO schema_migrations (
    version,
    name
)
VALUES (
    17,
    'Payment accounts: bank account type (BANK / MOBILE_WALLET), wallet requires its account'
)
ON DUPLICATE KEY UPDATE
    name = VALUES(name);


SET SESSION sql_mode = @masgold_saved_sql_mode;
