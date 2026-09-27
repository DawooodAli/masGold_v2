-- ============================================================
-- masGold v2
-- Migration 016: Billing shop workflow
--
-- Supports the real counter workflow on the canonical invoices
-- family (no third billing architecture):
--   1. two new sale line kinds
--        SELL_UNIDENTIFIED  a physical piece sold without an exact
--                           inventory identity (F5); no inventory id
--        SELL_OLD_GOLD      sold from the bulk old-gold stock (F7);
--                           linked to its old-gold OUT movement
--   2. unidentified sales reuse inventory_reconciliation_cases (003,
--      case_type UNIDENTIFIED_SALE), now linked to invoice / line,
--      plus an append-only case event history
--   3. INVOICE_SALE_OUT old-gold movement type
--   4. invoice-level cash settlement adjustments (explicit, audited)
--   5. one management review queue for review types that have no
--      table of their own (making/pricing, cash settlement approval,
--      old-gold discount changed from the default)
--   6. billing settings (settlement limits, default old-gold discount)
--   7. permissions
--
-- The target database is chosen by the caller; this file never
-- names a database. Re-runnable and additive.
--
-- Rollback: rollback/016_billing_workflow_rollback.sql
-- ============================================================


SET @masgold_saved_sql_mode := @@SESSION.sql_mode;
SET SESSION sql_mode = 'ONLY_FULL_GROUP_BY,STRICT_TRANS_TABLES,NO_ZERO_IN_DATE,NO_ZERO_DATE,ERROR_FOR_DIVISION_BY_ZERO,NO_ENGINE_SUBSTITUTION';


-- ============================================================
-- 0. PREFLIGHT
-- ============================================================

SET @masgold_sql := IF(
    NOT EXISTS (SELECT 1 FROM schema_migrations WHERE version = 15),
    'SELECT 1 FROM MIGRATION_016_REFUSED_migration_015_missing',
    'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;


-- ============================================================
-- 1. INVOICE LINES: new sale kinds
-- ============================================================

-- How the employee identified a sold piece (audit only; never an identity).
SET @masgold_sql := IF(
    NOT EXISTS (SELECT 1 FROM information_schema.COLUMNS
                 WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'invoice_lines'
                   AND COLUMN_NAME = 'identification_method'),
    "ALTER TABLE invoice_lines ADD COLUMN identification_method VARCHAR(20) NULL AFTER line_type",
    'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(
    NOT EXISTS (SELECT 1 FROM information_schema.TABLE_CONSTRAINTS
                 WHERE CONSTRAINT_SCHEMA = DATABASE() AND TABLE_NAME = 'invoice_lines'
                   AND CONSTRAINT_NAME = 'chk_invoice_line_identification'),
    "ALTER TABLE invoice_lines ADD CONSTRAINT chk_invoice_line_identification CHECK (
        identification_method IS NULL
        OR identification_method IN ('SCAN', 'SEARCH', 'MANUAL', 'OLD_GOLD_STOCK')
    )",
    'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

-- Line type list: replaced only while it lacks the new kinds.
SET @masgold_sql := IF(
    EXISTS (SELECT 1 FROM information_schema.TABLE_CONSTRAINTS tc
              JOIN information_schema.CHECK_CONSTRAINTS cc
                ON cc.CONSTRAINT_SCHEMA = tc.CONSTRAINT_SCHEMA AND cc.CONSTRAINT_NAME = tc.CONSTRAINT_NAME
             WHERE tc.CONSTRAINT_SCHEMA = DATABASE() AND tc.TABLE_NAME = 'invoice_lines'
               AND tc.CONSTRAINT_NAME = 'chk_invoice_line_type' AND cc.CHECK_CLAUSE NOT LIKE '%SELL_OLD_GOLD%'),
    "ALTER TABLE invoice_lines DROP CHECK chk_invoice_line_type",
    'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(
    NOT EXISTS (SELECT 1 FROM information_schema.TABLE_CONSTRAINTS
                 WHERE CONSTRAINT_SCHEMA = DATABASE() AND TABLE_NAME = 'invoice_lines'
                   AND CONSTRAINT_NAME = 'chk_invoice_line_type'),
    "ALTER TABLE invoice_lines ADD CONSTRAINT chk_invoice_line_type CHECK (
        line_type IN ('SELL_INVENTORY', 'BUY_OLD_GOLD', 'OTHER', 'SELL_OLD_GOLD', 'SELL_UNIDENTIFIED')
    )",
    'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

-- Native value rule (014) extended to the new kinds.
SET @masgold_sql := IF(
    EXISTS (SELECT 1 FROM information_schema.TABLE_CONSTRAINTS tc
              JOIN information_schema.CHECK_CONSTRAINTS cc
                ON cc.CONSTRAINT_SCHEMA = tc.CONSTRAINT_SCHEMA AND cc.CONSTRAINT_NAME = tc.CONSTRAINT_NAME
             WHERE tc.CONSTRAINT_SCHEMA = DATABASE() AND tc.TABLE_NAME = 'invoice_lines'
               AND tc.CONSTRAINT_NAME = 'chk_invoice_line_native_values' AND cc.CHECK_CLAUSE NOT LIKE '%SELL_OLD_GOLD%'),
    "ALTER TABLE invoice_lines DROP CHECK chk_invoice_line_native_values",
    'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(
    NOT EXISTS (SELECT 1 FROM information_schema.TABLE_CONSTRAINTS
                 WHERE CONSTRAINT_SCHEMA = DATABASE() AND TABLE_NAME = 'invoice_lines'
                   AND CONSTRAINT_NAME = 'chk_invoice_line_native_values'),
    "ALTER TABLE invoice_lines ADD CONSTRAINT chk_invoice_line_native_values CHECK (
        record_origin <> 'NATIVE'
        OR (
            line_type IN ('SELL_INVENTORY', 'BUY_OLD_GOLD', 'SELL_OLD_GOLD', 'SELL_UNIDENTIFIED')
            AND quantity = 1
            AND gold_price_per_gram IS NOT NULL
            AND line_total IS NOT NULL
            AND gross_total IS NOT NULL
            AND gross_total = ROUND(weight_grams * gold_price_per_gram, 2)
        )
    )",
    'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

-- F5: no movement; the entered description is kept. (MySQL cannot CHECK
-- inventory_item_id, which has an FK referential action; InvoiceService
-- writes these lines without an inventory id.)
SET @masgold_sql := IF(
    NOT EXISTS (SELECT 1 FROM information_schema.TABLE_CONSTRAINTS
                 WHERE CONSTRAINT_SCHEMA = DATABASE() AND TABLE_NAME = 'invoice_lines'
                   AND CONSTRAINT_NAME = 'chk_invoice_line_native_unidentified'),
    "ALTER TABLE invoice_lines ADD CONSTRAINT chk_invoice_line_native_unidentified CHECK (
        record_origin <> 'NATIVE'
        OR line_type <> 'SELL_UNIDENTIFIED'
        OR (
            old_gold_movement_id IS NULL
            AND item_name_snapshot IS NOT NULL
            AND making_total IS NOT NULL
            AND line_total = gross_total + making_total
            AND discount_type IS NULL
            AND discount_amount IS NULL
        )
    )",
    'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

-- F7: weight-ledger sale; always linked to its own old-gold OUT movement.
SET @masgold_sql := IF(
    NOT EXISTS (SELECT 1 FROM information_schema.TABLE_CONSTRAINTS
                 WHERE CONSTRAINT_SCHEMA = DATABASE() AND TABLE_NAME = 'invoice_lines'
                   AND CONSTRAINT_NAME = 'chk_invoice_line_native_old_gold_sale'),
    "ALTER TABLE invoice_lines ADD CONSTRAINT chk_invoice_line_native_old_gold_sale CHECK (
        record_origin <> 'NATIVE'
        OR line_type <> 'SELL_OLD_GOLD'
        OR (
            old_gold_movement_id IS NOT NULL
            AND item_name_snapshot IS NOT NULL
            AND making_total IS NOT NULL
            AND line_total = gross_total + making_total
            AND discount_type IS NULL
            AND discount_amount IS NULL
        )
    )",
    'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;


-- ============================================================
-- 2. UNIDENTIFIED SALES: inventory_reconciliation_cases (003)
-- ============================================================

SET @masgold_sql := IF(
    NOT EXISTS (SELECT 1 FROM information_schema.COLUMNS
                 WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'inventory_reconciliation_cases'
                   AND COLUMN_NAME = 'invoice_id'),
    "ALTER TABLE inventory_reconciliation_cases ADD COLUMN invoice_id BIGINT UNSIGNED NULL AFTER reference_number",
    'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(
    NOT EXISTS (SELECT 1 FROM information_schema.COLUMNS
                 WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'inventory_reconciliation_cases'
                   AND COLUMN_NAME = 'invoice_line_id'),
    "ALTER TABLE inventory_reconciliation_cases ADD COLUMN invoice_line_id BIGINT UNSIGNED NULL AFTER invoice_id",
    'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(
    NOT EXISTS (SELECT 1 FROM information_schema.COLUMNS
                 WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'inventory_reconciliation_cases'
                   AND COLUMN_NAME = 'observed_size'),
    "ALTER TABLE inventory_reconciliation_cases ADD COLUMN observed_size VARCHAR(50) NULL AFTER observed_weight_grams",
    'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(
    NOT EXISTS (SELECT 1 FROM information_schema.COLUMNS
                 WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'inventory_reconciliation_cases'
                   AND COLUMN_NAME = 'reason_code'),
    "ALTER TABLE inventory_reconciliation_cases ADD COLUMN reason_code VARCHAR(30) NULL AFTER reason",
    'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(
    NOT EXISTS (SELECT 1 FROM information_schema.COLUMNS
                 WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'inventory_reconciliation_cases'
                   AND COLUMN_NAME = 'salesperson_employee_id'),
    "ALTER TABLE inventory_reconciliation_cases ADD COLUMN salesperson_employee_id INT UNSIGNED NULL AFTER created_by_user_id",
    'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(
    NOT EXISTS (SELECT 1 FROM information_schema.COLUMNS
                 WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'inventory_reconciliation_cases'
                   AND COLUMN_NAME = 'sold_at'),
    "ALTER TABLE inventory_reconciliation_cases ADD COLUMN sold_at DATETIME NULL AFTER created_at",
    'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(
    NOT EXISTS (SELECT 1 FROM information_schema.STATISTICS
                 WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'inventory_reconciliation_cases'
                   AND INDEX_NAME = 'uq_reconciliation_invoice_line'),
    "ALTER TABLE inventory_reconciliation_cases ADD UNIQUE KEY uq_reconciliation_invoice_line (invoice_line_id)",
    'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(
    NOT EXISTS (SELECT 1 FROM information_schema.STATISTICS
                 WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'inventory_reconciliation_cases'
                   AND INDEX_NAME = 'uq_reconciliation_matched_item'),
    "ALTER TABLE inventory_reconciliation_cases ADD UNIQUE KEY uq_reconciliation_matched_item (matched_inventory_item_id)",
    'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(
    NOT EXISTS (SELECT 1 FROM information_schema.STATISTICS
                 WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'inventory_reconciliation_cases'
                   AND INDEX_NAME = 'idx_reconciliation_invoice'),
    "ALTER TABLE inventory_reconciliation_cases ADD KEY idx_reconciliation_invoice (invoice_id)",
    'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(
    NOT EXISTS (SELECT 1 FROM information_schema.TABLE_CONSTRAINTS
                 WHERE CONSTRAINT_SCHEMA = DATABASE() AND TABLE_NAME = 'inventory_reconciliation_cases'
                   AND CONSTRAINT_NAME = 'fk_reconciliation_invoice'),
    "ALTER TABLE inventory_reconciliation_cases ADD CONSTRAINT fk_reconciliation_invoice FOREIGN KEY (invoice_id) REFERENCES invoices(id)",
    'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(
    NOT EXISTS (SELECT 1 FROM information_schema.TABLE_CONSTRAINTS
                 WHERE CONSTRAINT_SCHEMA = DATABASE() AND TABLE_NAME = 'inventory_reconciliation_cases'
                   AND CONSTRAINT_NAME = 'fk_reconciliation_invoice_line'),
    "ALTER TABLE inventory_reconciliation_cases ADD CONSTRAINT fk_reconciliation_invoice_line FOREIGN KEY (invoice_line_id) REFERENCES invoice_lines(id)",
    'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(
    NOT EXISTS (SELECT 1 FROM information_schema.TABLE_CONSTRAINTS
                 WHERE CONSTRAINT_SCHEMA = DATABASE() AND TABLE_NAME = 'inventory_reconciliation_cases'
                   AND CONSTRAINT_NAME = 'fk_reconciliation_salesperson'),
    "ALTER TABLE inventory_reconciliation_cases ADD CONSTRAINT fk_reconciliation_salesperson FOREIGN KEY (salesperson_employee_id) REFERENCES employees(id)",
    'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(
    NOT EXISTS (SELECT 1 FROM information_schema.TABLE_CONSTRAINTS
                 WHERE CONSTRAINT_SCHEMA = DATABASE() AND TABLE_NAME = 'inventory_reconciliation_cases'
                   AND CONSTRAINT_NAME = 'chk_reconciliation_reason_code'),
    "ALTER TABLE inventory_reconciliation_cases ADD CONSTRAINT chk_reconciliation_reason_code CHECK (
        reason_code IS NULL
        OR reason_code IN ('NO_BARCODE', 'BARCODE_LOST', 'NOT_FOUND', 'OTHER')
    )",
    'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(
    NOT EXISTS (SELECT 1 FROM information_schema.TABLE_CONSTRAINTS
                 WHERE CONSTRAINT_SCHEMA = DATABASE() AND TABLE_NAME = 'inventory_reconciliation_cases'
                   AND CONSTRAINT_NAME = 'chk_reconciliation_unidentified_sale'),
    "ALTER TABLE inventory_reconciliation_cases ADD CONSTRAINT chk_reconciliation_unidentified_sale CHECK (
        case_type <> 'UNIDENTIFIED_SALE'
        OR invoice_id IS NULL
        OR (invoice_line_id IS NOT NULL AND reason_code IS NOT NULL AND sold_at IS NOT NULL
            AND observed_weight_grams IS NOT NULL)
    )",
    'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;
-- observed_karat_id and matched_inventory_item_id have FK referential actions,
-- so MySQL cannot CHECK them; InventoryReconciliation / InvoiceService set them.

-- Every status transition, append-only (no deletes to correct history).
CREATE TABLE IF NOT EXISTS inventory_reconciliation_case_events (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    case_id BIGINT UNSIGNED NOT NULL,
    event_type VARCHAR(30) NOT NULL,
    from_status VARCHAR(20) DEFAULT NULL,
    to_status VARCHAR(20) NOT NULL,
    matched_inventory_item_id BIGINT UNSIGNED DEFAULT NULL,
    notes VARCHAR(1000) DEFAULT NULL,
    performed_by_user_id INT UNSIGNED DEFAULT NULL,
    occurred_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    KEY idx_reconciliation_event_case (case_id),
    CONSTRAINT fk_reconciliation_event_case FOREIGN KEY (case_id)
        REFERENCES inventory_reconciliation_cases(id),
    CONSTRAINT fk_reconciliation_event_item FOREIGN KEY (matched_inventory_item_id)
        REFERENCES inventory_items(id),
    CONSTRAINT fk_reconciliation_event_user FOREIGN KEY (performed_by_user_id)
        REFERENCES users(id),
    CONSTRAINT chk_reconciliation_event_type CHECK (
        event_type IN ('OPENED', 'MATCHED', 'REGISTERED_AND_MATCHED', 'CLOSED', 'CANCELLED', 'NOTE')
    ),
    CONSTRAINT chk_reconciliation_event_status CHECK (
        to_status IN ('OPEN', 'MATCHED', 'CLOSED', 'CANCELLED')
    )
) ENGINE=InnoDB
  DEFAULT CHARSET=utf8mb4
  COLLATE=utf8mb4_unicode_ci;


-- ============================================================
-- 3. OLD GOLD: sale from the bulk stock (F7)
-- ============================================================

INSERT INTO old_gold_movement_types (
    code,
    name,
    description,
    expected_direction
)
VALUES
    (
        'INVOICE_SALE_OUT',
        'Invoice Sale Out',
        'Old-gold weight sold to a customer on an invoice (sale from old-gold stock)',
        'OUT'
    )
ON DUPLICATE KEY UPDATE
    name = VALUES(name),
    description = VALUES(description),
    expected_direction = VALUES(expected_direction);


-- ============================================================
-- 4. CASH SETTLEMENT ADJUSTMENT (one per invoice)
--
-- R = N - P (signed residual). A = the part of R the employee
-- explicitly closed as a practical cash settlement, stored with
-- the same sign as R. Only R_account = R - A may reach the
-- customer account. This is not a commercial discount and never
-- creates customer balance, Dead Cash or pending cash.
-- ============================================================

CREATE TABLE IF NOT EXISTS invoice_settlement_adjustments (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    invoice_id BIGINT UNSIGNED NOT NULL,
    residual_signed DECIMAL(16,2) NOT NULL,
    amount_signed DECIMAL(16,2) NOT NULL,
    reason VARCHAR(500) DEFAULT NULL,
    accepted_by_user_id INT UNSIGNED NOT NULL,
    approved_by_user_id INT UNSIGNED DEFAULT NULL,
    approved_at DATETIME DEFAULT NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uq_settlement_invoice (invoice_id),
    CONSTRAINT fk_settlement_invoice FOREIGN KEY (invoice_id) REFERENCES invoices(id),
    CONSTRAINT fk_settlement_accepted_by FOREIGN KEY (accepted_by_user_id) REFERENCES users(id),
    CONSTRAINT fk_settlement_approved_by FOREIGN KEY (approved_by_user_id) REFERENCES users(id),
    CONSTRAINT chk_settlement_amount CHECK (
        amount_signed <> 0
        AND residual_signed <> 0
        AND SIGN(amount_signed) = SIGN(residual_signed)
        AND ABS(amount_signed) <= ABS(residual_signed)
        AND ABS(amount_signed) <= 500
    ),
    CONSTRAINT chk_settlement_approval CHECK (
        (approved_by_user_id IS NULL) = (approved_at IS NULL)
    )
) ENGINE=InnoDB
  DEFAULT CHARSET=utf8mb4
  COLLATE=utf8mb4_unicode_ci;


-- ============================================================
-- 5. MANAGEMENT REVIEWS (مراجعات المدير)
--
-- One queue for review types without a table of their own.
-- Unidentified-sale reviews stay in inventory_reconciliation_cases;
-- the review area lists both.
-- ============================================================

CREATE TABLE IF NOT EXISTS management_reviews (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    review_type VARCHAR(40) NOT NULL,
    review_status VARCHAR(20) NOT NULL DEFAULT 'OPEN',
    invoice_id BIGINT UNSIGNED DEFAULT NULL,
    summary VARCHAR(500) NOT NULL,
    details_json JSON DEFAULT NULL,
    created_by_user_id INT UNSIGNED DEFAULT NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    resolved_by_user_id INT UNSIGNED DEFAULT NULL,
    resolved_at DATETIME DEFAULT NULL,
    resolution_notes VARCHAR(1000) DEFAULT NULL,
    PRIMARY KEY (id),
    KEY idx_management_review_open (review_status, review_type),
    KEY idx_management_review_invoice (invoice_id),
    CONSTRAINT fk_management_review_invoice FOREIGN KEY (invoice_id) REFERENCES invoices(id),
    CONSTRAINT fk_management_review_created_by FOREIGN KEY (created_by_user_id) REFERENCES users(id),
    CONSTRAINT fk_management_review_resolved_by FOREIGN KEY (resolved_by_user_id) REFERENCES users(id),
    CONSTRAINT chk_management_review_type CHECK (
        review_type IN ('MAKING_PRICE', 'CASH_SETTLEMENT', 'OLD_GOLD_DISCOUNT')
    ),
    CONSTRAINT chk_management_review_status CHECK (
        review_status IN ('OPEN', 'RESOLVED', 'DISMISSED')
    ),
    CONSTRAINT chk_management_review_resolution CHECK (
        (review_status = 'OPEN') = (resolved_at IS NULL)
    )
) ENGINE=InnoDB
  DEFAULT CHARSET=utf8mb4
  COLLATE=utf8mb4_unicode_ci;

-- Review type list: replaced only while it lacks OLD_GOLD_DISCOUNT
-- (a database that already ran an earlier draft of this file).
SET @masgold_sql := IF(
    EXISTS (SELECT 1 FROM information_schema.TABLE_CONSTRAINTS tc
              JOIN information_schema.CHECK_CONSTRAINTS cc
                ON cc.CONSTRAINT_SCHEMA = tc.CONSTRAINT_SCHEMA AND cc.CONSTRAINT_NAME = tc.CONSTRAINT_NAME
             WHERE tc.CONSTRAINT_SCHEMA = DATABASE() AND tc.TABLE_NAME = 'management_reviews'
               AND tc.CONSTRAINT_NAME = 'chk_management_review_type'
               AND cc.CHECK_CLAUSE NOT LIKE '%OLD_GOLD_DISCOUNT%'),
    "ALTER TABLE management_reviews DROP CHECK chk_management_review_type",
    'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(
    NOT EXISTS (SELECT 1 FROM information_schema.TABLE_CONSTRAINTS
                 WHERE CONSTRAINT_SCHEMA = DATABASE() AND TABLE_NAME = 'management_reviews'
                   AND CONSTRAINT_NAME = 'chk_management_review_type'),
    "ALTER TABLE management_reviews ADD CONSTRAINT chk_management_review_type CHECK (
        review_type IN ('MAKING_PRICE', 'CASH_SETTLEMENT', 'OLD_GOLD_DISCOUNT')
    )",
    'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;


-- ============================================================
-- 6. BILLING SETTINGS (single row)
-- ============================================================

CREATE TABLE IF NOT EXISTS billing_settings (
    id TINYINT UNSIGNED NOT NULL,
    settlement_self_limit DECIMAL(16,2) NOT NULL DEFAULT 50.00,
    settlement_approval_limit DECIMAL(16,2) NOT NULL DEFAULT 500.00,
    old_gold_discount_default_percent DECIMAL(5,2) NOT NULL DEFAULT 1.50,
    updated_by_user_id INT UNSIGNED DEFAULT NULL,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    CONSTRAINT fk_billing_settings_user FOREIGN KEY (updated_by_user_id) REFERENCES users(id),
    CONSTRAINT chk_billing_settings_single CHECK (id = 1),
    CONSTRAINT chk_billing_settings_limits CHECK (
        settlement_self_limit >= 0
        AND settlement_self_limit <= settlement_approval_limit
        AND settlement_approval_limit <= 500
    ),
    CONSTRAINT chk_billing_settings_discount CHECK (
        old_gold_discount_default_percent >= 0 AND old_gold_discount_default_percent <= 100
    )
) ENGINE=InnoDB
  DEFAULT CHARSET=utf8mb4
  COLLATE=utf8mb4_unicode_ci;

INSERT INTO billing_settings (id, settlement_self_limit, settlement_approval_limit, old_gold_discount_default_percent)
VALUES (1, 50.00, 500.00, 1.50)
ON DUPLICATE KEY UPDATE id = id;


-- ============================================================
-- 7. PERMISSIONS
-- ============================================================

INSERT INTO permissions (
    code,
    name,
    description
)
VALUES
    (
        'INVOICE_SETTLEMENT_APPROVE',
        'Approve Cash Settlement',
        'Approve a cash settlement above the salesperson limit (up to the approval limit)'
    ),
    (
        'MANAGEMENT_REVIEW',
        'Management Reviews',
        'Open and resolve management reviews'
    ),
    (
        'INVENTORY_RECONCILE',
        'Reconcile Inventory',
        'Link unidentified sales to the exact inventory item'
    )
ON DUPLICATE KEY UPDATE
    name = VALUES(name),
    description = VALUES(description);

-- OWNER and MANAGER receive the new permissions (only those not already assigned).
INSERT INTO role_permissions (
    role_id,
    permission_id
)
SELECT
    r.id,
    p.id
FROM roles r
JOIN permissions p
    ON p.code IN ('INVOICE_SETTLEMENT_APPROVE', 'MANAGEMENT_REVIEW', 'INVENTORY_RECONCILE')
WHERE NOT EXISTS (SELECT 1 FROM role_permissions rp
                   WHERE rp.role_id = r.id AND rp.permission_id = p.id)
  AND r.code IN ('OWNER', 'MANAGER');


-- ============================================================
-- REGISTER MIGRATION
-- ============================================================

INSERT INTO schema_migrations (
    version,
    name
)
VALUES (
    16,
    'Billing workflow: unidentified and old-gold sales, settlement adjustments, management reviews'
)
ON DUPLICATE KEY UPDATE
    name = VALUES(name);


SET SESSION sql_mode = @masgold_saved_sql_mode;
