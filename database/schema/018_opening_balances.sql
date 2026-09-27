-- ============================================================
-- masGold v2
-- Migration 018: opening balances for cash, bank and wallet
--
-- A real opening count is recorded once per account as an
-- ordinary ledger movement (never an overwritten balance):
--   cash_movement_types.OPENING_BALANCE   (IN)
--   bank_movement_types.OPENING_BALANCE   (IN)
-- The service allows it only while the account has no movement
-- at all; later corrections belong to the adjustment types.
--
-- No account, balance or movement is created here. The target
-- database is chosen by the caller; this file never names a
-- database. Re-runnable and additive.
--
-- Rollback: rollback/018_opening_balances_rollback.sql
-- ============================================================


SET @masgold_saved_sql_mode := @@SESSION.sql_mode;
SET SESSION sql_mode = 'ONLY_FULL_GROUP_BY,STRICT_TRANS_TABLES,NO_ZERO_IN_DATE,NO_ZERO_DATE,ERROR_FOR_DIVISION_BY_ZERO,NO_ENGINE_SUBSTITUTION';


-- ============================================================
-- 0. PREFLIGHT
-- ============================================================

SET @masgold_sql := IF(
    NOT EXISTS (SELECT 1 FROM schema_migrations WHERE version = 17),
    'SELECT 1 FROM MIGRATION_018_REFUSED_migration_017_missing',
    'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;


-- ============================================================
-- 1. OPENING BALANCE MOVEMENT TYPES
-- ============================================================

INSERT INTO cash_movement_types (
    code,
    name,
    expected_direction,
    description
)
VALUES
    (
        'OPENING_BALANCE',
        'Opening Cash Count',
        'IN',
        'The real physical cash counted when the cash account starts in masGold (once per account)'
    )
ON DUPLICATE KEY UPDATE
    name = VALUES(name),
    expected_direction = VALUES(expected_direction),
    description = VALUES(description);

INSERT INTO bank_movement_types (
    code,
    name,
    expected_direction,
    description
)
VALUES
    (
        'OPENING_BALANCE',
        'Opening Account Balance',
        'IN',
        'The real bank / wallet balance when the account starts in masGold (once per account)'
    )
ON DUPLICATE KEY UPDATE
    name = VALUES(name),
    expected_direction = VALUES(expected_direction),
    description = VALUES(description);


-- ============================================================
-- REGISTER MIGRATION
-- ============================================================

INSERT INTO schema_migrations (
    version,
    name
)
VALUES (
    18,
    'Opening balances: OPENING_BALANCE cash and bank movement types'
)
ON DUPLICATE KEY UPDATE
    name = VALUES(name);


SET SESSION sql_mode = @masgold_saved_sql_mode;
