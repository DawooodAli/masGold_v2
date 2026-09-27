-- ============================================================
-- masGold v2
-- Rollback of migration 017: payment accounts
--
-- Refused while a wallet account exists or a wallet payment
-- was recorded. Every guard runs before the first change; a
-- failed guard stops the script with
-- "Table '<db>.ROLLBACK_017_REFUSED_<reason>' doesn't exist".
-- Re-runnable.
-- ============================================================


SET @masgold_saved_sql_mode := @@SESSION.sql_mode;
SET SESSION sql_mode = 'ONLY_FULL_GROUP_BY,STRICT_TRANS_TABLES,NO_ZERO_IN_DATE,NO_ZERO_DATE,ERROR_FOR_DIVISION_BY_ZERO,NO_ENGINE_SUBSTITUTION';


-- ============================================================
-- 0. GUARDS
-- ============================================================

SET @masgold_rows := 0;
SET @masgold_sql := IF(
    EXISTS (SELECT 1 FROM information_schema.COLUMNS
             WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'bank_accounts'
               AND COLUMN_NAME = 'account_type'),
    'SELECT COUNT(*) INTO @masgold_rows FROM bank_accounts WHERE account_type <> ''BANK''',
    'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;
SET @masgold_sql := IF(@masgold_rows > 0, 'SELECT 1 FROM ROLLBACK_017_REFUSED_wallet_accounts_exist', 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(
    EXISTS (SELECT 1 FROM invoice_payments p JOIN payment_methods m ON m.id = p.payment_method_id
             WHERE m.code = 'MOBILE_WALLET' AND p.bank_account_id IS NOT NULL),
    'SELECT 1 FROM ROLLBACK_017_REFUSED_wallet_payments_recorded', 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;


-- ============================================================
-- 1. UNDO
-- ============================================================

UPDATE payment_methods
   SET requires_bank_account = FALSE
 WHERE code = 'MOBILE_WALLET'
   AND requires_bank_account = TRUE;

SET @masgold_sql := IF(
    EXISTS (SELECT 1 FROM information_schema.TABLE_CONSTRAINTS
             WHERE CONSTRAINT_SCHEMA = DATABASE() AND TABLE_NAME = 'bank_accounts'
               AND CONSTRAINT_NAME = 'chk_bank_account_type'),
    "ALTER TABLE bank_accounts DROP CHECK chk_bank_account_type", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(
    EXISTS (SELECT 1 FROM information_schema.COLUMNS
             WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'bank_accounts'
               AND COLUMN_NAME = 'account_type'),
    "ALTER TABLE bank_accounts DROP COLUMN account_type", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;


DELETE FROM schema_migrations WHERE version = 17;

SET SESSION sql_mode = @masgold_saved_sql_mode;
