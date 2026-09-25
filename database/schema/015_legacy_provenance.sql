-- ============================================================
-- masGold v2
-- Migration 015: Legacy provenance
--
-- Structures only. No legacy data is loaded here; the legacy
-- migration tool (tools/legacy_migration) writes the data.
--
-- The target database is chosen by the caller; this file never
-- names a database.
--
-- Re-runnable and additive. The only column changes relax NOT NULL
-- on amounts that a legacy bill cannot prove; 014's native CHECKs
-- (record_origin = 'NATIVE') keep every native row complete.
--
-- Rollback: rollback/015_legacy_provenance_rollback.sql
-- ============================================================


SET @masgold_saved_sql_mode := @@SESSION.sql_mode;
SET SESSION sql_mode = 'ONLY_FULL_GROUP_BY,STRICT_TRANS_TABLES,NO_ZERO_IN_DATE,NO_ZERO_DATE,ERROR_FOR_DIVISION_BY_ZERO,NO_ENGINE_SUBSTITUTION';


-- ============================================================
-- 0. PREFLIGHT
-- ============================================================

-- The relaxations below are only safe once 014's native CHECKs exist.
SET @masgold_sql := IF(
    NOT EXISTS (SELECT 1 FROM schema_migrations WHERE version = 14)
    OR (SELECT COUNT(*) FROM information_schema.TABLE_CONSTRAINTS
         WHERE CONSTRAINT_SCHEMA = DATABASE()
           AND CONSTRAINT_NAME IN ('chk_invoice_native_totals',
                                   'chk_invoice_line_native_values')) <> 2,
    'SELECT 1 FROM MIGRATION_015_REFUSED_migration_014_missing',
    'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;


-- ============================================================
-- 1. INVOICES: unknown legacy split, provenance
--
-- Exchange bills in golddb kept only the net amount, so the
-- sell/buy split is NULL (unknown), never a fabricated 0.
-- net_amount stays NOT NULL.
-- ============================================================

SET @masgold_sql := IF(
    EXISTS (SELECT 1 FROM information_schema.COLUMNS
             WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'invoices'
               AND COLUMN_NAME = 'sell_total' AND IS_NULLABLE = 'NO'),
    "ALTER TABLE invoices MODIFY COLUMN sell_total DECIMAL(16,2) NULL DEFAULT NULL",
    'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(
    EXISTS (SELECT 1 FROM information_schema.COLUMNS
             WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'invoices'
               AND COLUMN_NAME = 'buy_total' AND IS_NULLABLE = 'NO'),
    "ALTER TABLE invoices MODIFY COLUMN buy_total DECIMAL(16,2) NULL DEFAULT NULL",
    'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(
    NOT EXISTS (SELECT 1 FROM information_schema.COLUMNS
                 WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'invoices'
                   AND COLUMN_NAME = 'provenance_json'),
    "ALTER TABLE invoices ADD COLUMN provenance_json JSON NULL AFTER record_origin",
    'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;


-- ============================================================
-- 2. INVOICE LINES: unknown legacy amounts, provenance
--
-- Legacy sales were typed as lump sums; per-line totals (and for
-- some lines the price) cannot be proven. NULL = unknown.
-- Legacy lines can only belong to LEGACY invoices
-- (fk_invoice_line_invoice_origin, 014).
-- ============================================================

SET @masgold_sql := IF(
    EXISTS (SELECT 1 FROM information_schema.COLUMNS
             WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'invoice_lines'
               AND COLUMN_NAME = 'gold_price_per_gram' AND IS_NULLABLE = 'NO'),
    "ALTER TABLE invoice_lines MODIFY COLUMN gold_price_per_gram DECIMAL(14,4) NULL",
    'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(
    EXISTS (SELECT 1 FROM information_schema.COLUMNS
             WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'invoice_lines'
               AND COLUMN_NAME = 'line_total' AND IS_NULLABLE = 'NO'),
    "ALTER TABLE invoice_lines MODIFY COLUMN line_total DECIMAL(16,2) NULL",
    'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(
    NOT EXISTS (SELECT 1 FROM information_schema.COLUMNS
                 WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'invoice_lines'
                   AND COLUMN_NAME = 'provenance_json'),
    "ALTER TABLE invoice_lines ADD COLUMN provenance_json JSON NULL AFTER notes",
    'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;


-- ============================================================
-- 3. OLD GOLD: counted vs reference-only movements
--
-- COUNTED        affects the live old-gold balance (default)
-- REFERENCE_ONLY imported legacy history; never affects balances.
--
-- The opening balance at cut-over is a physical count posted as
-- OPENING_BALANCE (COUNTED), never derived from legacy history.
-- ============================================================

SET @masgold_sql := IF(
    NOT EXISTS (SELECT 1 FROM information_schema.COLUMNS
                 WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'old_gold_movements'
                   AND COLUMN_NAME = 'balance_effect'),
    "ALTER TABLE old_gold_movements ADD COLUMN balance_effect VARCHAR(20) NOT NULL DEFAULT 'COUNTED' AFTER weight_delta_grams",
    'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(
    NOT EXISTS (SELECT 1 FROM information_schema.STATISTICS
                 WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'old_gold_movements'
                   AND INDEX_NAME = 'idx_old_gold_balance_effect'),
    "ALTER TABLE old_gold_movements ADD KEY idx_old_gold_balance_effect (karat_id, balance_effect)",
    'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

-- Reference-only rows must carry their legacy source.
SET @masgold_sql := IF(
    NOT EXISTS (SELECT 1 FROM information_schema.TABLE_CONSTRAINTS
                 WHERE CONSTRAINT_SCHEMA = DATABASE() AND TABLE_NAME = 'old_gold_movements'
                   AND CONSTRAINT_NAME = 'chk_old_gold_balance_effect'),
    "ALTER TABLE old_gold_movements ADD CONSTRAINT chk_old_gold_balance_effect CHECK (
        balance_effect = 'COUNTED'
        OR (balance_effect = 'REFERENCE_ONLY'
            AND legacy_source_table IS NOT NULL
            AND legacy_source_id IS NOT NULL)
    )",
    'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;


-- Same definition as 004, restricted to COUNTED rows.
-- vw_jrd_current_gold_components reads this view, so the JRD
-- old-gold component follows automatically.
CREATE OR REPLACE VIEW vw_old_gold_balances AS

SELECT
    k.id AS karat_id,
    k.karat_value,
    k.name AS karat_name,

    CAST(
        COALESCE(
            SUM(m.weight_delta_grams),
            0
        )
        AS DECIMAL(14,3)
    ) AS balance_grams,

    CAST(
        COALESCE(
            SUM(
                CASE
                    WHEN m.weight_delta_grams > 0
                    THEN m.weight_delta_grams
                    ELSE 0
                END
            ),
            0
        )
        AS DECIMAL(14,3)
    ) AS total_in_grams,

    CAST(
        COALESCE(
            SUM(
                CASE
                    WHEN m.weight_delta_grams < 0
                    THEN ABS(m.weight_delta_grams)
                    ELSE 0
                END
            ),
            0
        )
        AS DECIMAL(14,3)
    ) AS total_out_grams

FROM karats k

LEFT JOIN old_gold_movements m
    ON m.karat_id = k.id
   AND m.balance_effect = 'COUNTED'

GROUP BY
    k.id,
    k.karat_value,
    k.name;


-- ============================================================
-- 4. IMPORT RUNS
--
-- One row per ETL run. Rehearsals end at RECONCILED; only a
-- PRODUCTION_CUTOVER run can be APPROVED, and only by a user.
-- ============================================================

CREATE TABLE IF NOT EXISTS legacy_import_runs (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,

    source_system VARCHAR(50) NOT NULL,

    source_file VARCHAR(500) NOT NULL,
    source_sha256 CHAR(64) NOT NULL,
    source_dump_at DATETIME DEFAULT NULL,

    etl_version VARCHAR(50) NOT NULL,

    run_mode VARCHAR(30) NOT NULL,
    target_database VARCHAR(64) NOT NULL,

    status VARCHAR(20) NOT NULL DEFAULT 'RUNNING',

    started_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    finished_at DATETIME DEFAULT NULL,

    reconciliation_json JSON DEFAULT NULL,

    approved_by_user_id INT UNSIGNED DEFAULT NULL,
    approved_at DATETIME DEFAULT NULL,

    notes VARCHAR(1000) DEFAULT NULL,

    PRIMARY KEY (id),

    KEY idx_legacy_import_runs_status (
        status
    ),

    KEY idx_legacy_import_runs_source (
        source_system,
        source_sha256
    ),

    CONSTRAINT fk_legacy_import_run_approver
        FOREIGN KEY (approved_by_user_id)
        REFERENCES users(id),

    CONSTRAINT chk_legacy_import_run_status
        CHECK (
            status IN (
                'RUNNING',
                'FAILED',
                'RECONCILED',
                'APPROVED'
            )
        ),

    CONSTRAINT chk_legacy_import_run_mode
        CHECK (
            run_mode IN (
                'REHEARSAL',
                'PRODUCTION_CUTOVER'
            )
        ),

    CONSTRAINT chk_legacy_import_run_sha256
        CHECK (source_sha256 REGEXP '^[0-9a-f]{64}$'),

    CONSTRAINT chk_legacy_import_run_finished
        CHECK (
            (status = 'RUNNING' AND finished_at IS NULL)
            OR (status <> 'RUNNING' AND finished_at IS NOT NULL)
        ),

    CONSTRAINT chk_legacy_import_run_reconciled
        CHECK (
            status NOT IN ('RECONCILED', 'APPROVED')
            OR reconciliation_json IS NOT NULL
        ),

    CONSTRAINT chk_legacy_import_run_approval
        CHECK (
            (status = 'APPROVED'
                AND run_mode = 'PRODUCTION_CUTOVER'
                AND approved_by_user_id IS NOT NULL
                AND approved_at IS NOT NULL)
            OR (status <> 'APPROVED'
                AND approved_by_user_id IS NULL
                AND approved_at IS NULL)
        )
) ENGINE=InnoDB
  DEFAULT CHARSET=utf8mb4
  COLLATE=utf8mb4_unicode_ci;


-- ============================================================
-- 5. RECORD MAP
--
-- source row -> canonical row. Raw legacy values that have no
-- canonical column (cust_id 'OLD', original cashAction, C.../B...
-- document ids) live in metadata_json.
--
-- The UNIQUE key has no run_id: a second run into the same
-- database collides, so imports only run into a clean database.
-- ============================================================

CREATE TABLE IF NOT EXISTS legacy_record_map (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,

    run_id BIGINT UNSIGNED NOT NULL,

    source_system VARCHAR(50) NOT NULL,
    source_table VARCHAR(64) NOT NULL,
    source_pk VARCHAR(191) NOT NULL,

    target_table VARCHAR(64) NOT NULL,
    target_id BIGINT UNSIGNED NOT NULL,

    mapping_role VARCHAR(20) NOT NULL,
    completeness VARCHAR(20) NOT NULL,

    metadata_json JSON DEFAULT NULL,

    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,

    PRIMARY KEY (id),

    UNIQUE KEY uq_legacy_record_map_source (
        source_system,
        source_table,
        source_pk,
        target_table,
        mapping_role
    ),

    KEY idx_legacy_record_map_target (
        target_table,
        target_id
    ),

    KEY idx_legacy_record_map_run (
        run_id
    ),

    CONSTRAINT fk_legacy_record_map_run
        FOREIGN KEY (run_id)
        REFERENCES legacy_import_runs(id),

    CONSTRAINT chk_legacy_record_map_role
        CHECK (
            mapping_role IN (
                'PRIMARY',
                'DERIVED'
            )
        ),

    CONSTRAINT chk_legacy_record_map_completeness
        CHECK (
            completeness IN (
                'COMPLETE',
                'PARTIAL',
                'UNKNOWN'
            )
        )
) ENGINE=InnoDB
  DEFAULT CHARSET=utf8mb4
  COLLATE=utf8mb4_unicode_ci;


-- ============================================================
-- 6. EXCEPTIONS
--
-- Anything not imported cleanly, plus owner-review flags
-- (|balance| >= 50,000 EGP, #131, #977, every placeholder
-- balance). Amounts are never changed by an exception.
-- ============================================================

CREATE TABLE IF NOT EXISTS legacy_exceptions (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,

    run_id BIGINT UNSIGNED NOT NULL,

    source_system VARCHAR(50) NOT NULL,
    source_table VARCHAR(64) DEFAULT NULL,
    source_pk VARCHAR(191) DEFAULT NULL,

    exception_code VARCHAR(60) NOT NULL,
    severity VARCHAR(20) NOT NULL,

    detail_json JSON DEFAULT NULL,

    resolution_status VARCHAR(20) NOT NULL DEFAULT 'OPEN',
    resolved_by_user_id INT UNSIGNED DEFAULT NULL,
    resolved_at DATETIME DEFAULT NULL,
    resolution_notes VARCHAR(1000) DEFAULT NULL,

    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,

    PRIMARY KEY (id),

    UNIQUE KEY uq_legacy_exception (
        run_id,
        source_system,
        source_table,
        source_pk,
        exception_code
    ),

    KEY idx_legacy_exceptions_code (
        exception_code
    ),

    KEY idx_legacy_exceptions_status (
        resolution_status
    ),

    CONSTRAINT fk_legacy_exception_run
        FOREIGN KEY (run_id)
        REFERENCES legacy_import_runs(id),

    CONSTRAINT fk_legacy_exception_resolver
        FOREIGN KEY (resolved_by_user_id)
        REFERENCES users(id),

    CONSTRAINT chk_legacy_exception_severity
        CHECK (
            severity IN (
                'INFO',
                'WARNING',
                'CRITICAL'
            )
        ),

    CONSTRAINT chk_legacy_exception_resolution
        CHECK (
            (resolution_status = 'OPEN'
                AND resolved_by_user_id IS NULL
                AND resolved_at IS NULL)
            OR (resolution_status IN ('ACCEPTED', 'RESOLVED')
                AND resolved_by_user_id IS NOT NULL
                AND resolved_at IS NOT NULL
                AND resolution_notes IS NOT NULL)
        )
) ENGINE=InnoDB
  DEFAULT CHARSET=utf8mb4
  COLLATE=utf8mb4_unicode_ci;


-- ============================================================
-- 7. CODES
--
-- Legacy customer-ledger codes keep the original business meaning
-- of golddb cash.cashAction (see D7):
--   متبقي          -> LEGACY_REMAINDER
--   سداد           -> LEGACY_PAYMENT
--   عربون          -> LEGACY_DEPOSIT
--   استرداد عربون  -> LEGACY_DEPOSIT_REFUND
-- ============================================================

INSERT INTO customer_transaction_types (
    code,
    name,
    expected_direction,
    description
)
VALUES

    (
        'LEGACY_REMAINDER',
        'Legacy Remainder',
        'INCREASE',
        'Imported golddb remainder; customer owes the shop'
    ),

    (
        'LEGACY_PAYMENT',
        'Legacy Payment',
        'DECREASE',
        'Imported golddb later payment by the customer'
    ),

    (
        'LEGACY_DEPOSIT',
        'Legacy Deposit',
        'DECREASE',
        'Imported golddb deposit paid by the customer'
    ),

    (
        'LEGACY_DEPOSIT_REFUND',
        'Legacy Deposit Refund',
        'INCREASE',
        'Imported golddb deposit returned to the customer'
    )

ON DUPLICATE KEY UPDATE
    name = VALUES(name),
    expected_direction = VALUES(expected_direction),
    description = VALUES(description);


INSERT INTO old_gold_movement_types (
    code,
    name,
    description,
    expected_direction
)
VALUES

    (
        'OPENING_BALANCE',
        'Opening Balance',
        'Physical count per karat posted at production cut-over',
        'IN'
    )

ON DUPLICATE KEY UPDATE
    name = VALUES(name),
    description = VALUES(description),
    expected_direction = VALUES(expected_direction);


-- ============================================================
-- REGISTER MIGRATION
-- ============================================================

INSERT INTO schema_migrations (
    version,
    name
)
VALUES (
    15,
    'Legacy provenance: import runs, record map, exceptions, reference-only old gold'
)
ON DUPLICATE KEY UPDATE
    name = VALUES(name);


SET SESSION sql_mode = @masgold_saved_sql_mode;
