USE masgold_v2;

-- ============================================================
-- masGold v2
-- Migration 005: Customers + Ledger + Dead Cash
--
-- BALANCE RULE:
--
-- Positive balance:
--     Customer owes money to the shop
--
-- Negative balance:
--     Shop owes money / credit to the customer
--
-- Zero:
--     Fully settled
--
-- The current customer balance is CALCULATED from movements.
-- We never overwrite customers.balance.
-- ============================================================


-- ------------------------------------------------------------
-- 1. Customer types
-- ------------------------------------------------------------

CREATE TABLE IF NOT EXISTS customer_types (
    id SMALLINT UNSIGNED NOT NULL AUTO_INCREMENT,

    code VARCHAR(50) NOT NULL,
    name VARCHAR(100) NOT NULL,

    is_active BOOLEAN NOT NULL DEFAULT TRUE,

    sort_order SMALLINT UNSIGNED NOT NULL DEFAULT 0,

    PRIMARY KEY (id),
    UNIQUE KEY uq_customer_types_code (code)
) ENGINE=InnoDB
  DEFAULT CHARSET=utf8mb4
  COLLATE=utf8mb4_unicode_ci;


-- ------------------------------------------------------------
-- 2. Customers
--
-- IMPORTANT:
-- Unlike the old production DB, customer name is NOT UNIQUE.
-- Two real customers can have the same name.
-- ------------------------------------------------------------

CREATE TABLE IF NOT EXISTS customers (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,

    customer_code VARCHAR(50) DEFAULT NULL,

    name VARCHAR(150) NOT NULL,

    mobile VARCHAR(50) DEFAULT NULL,
    place VARCHAR(150) DEFAULT NULL,

    customer_type_id SMALLINT UNSIGNED DEFAULT NULL,

    is_active BOOLEAN NOT NULL DEFAULT TRUE,

    notes VARCHAR(1000) DEFAULT NULL,

    created_by_user_id INT UNSIGNED DEFAULT NULL,
    updated_by_user_id INT UNSIGNED DEFAULT NULL,

    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,

    updated_at DATETIME NOT NULL
        DEFAULT CURRENT_TIMESTAMP
        ON UPDATE CURRENT_TIMESTAMP,

    PRIMARY KEY (id),

    UNIQUE KEY uq_customers_code (customer_code),

    KEY idx_customers_name (name),
    KEY idx_customers_mobile (mobile),
    KEY idx_customers_place (place),
    KEY idx_customers_type (customer_type_id),
    KEY idx_customers_active (is_active),

    CONSTRAINT fk_customers_type
        FOREIGN KEY (customer_type_id)
        REFERENCES customer_types(id)
        ON DELETE SET NULL,

    CONSTRAINT fk_customers_created_user
        FOREIGN KEY (created_by_user_id)
        REFERENCES users(id)
        ON DELETE SET NULL,

    CONSTRAINT fk_customers_updated_user
        FOREIGN KEY (updated_by_user_id)
        REFERENCES users(id)
        ON DELETE SET NULL
) ENGINE=InnoDB
  DEFAULT CHARSET=utf8mb4
  COLLATE=utf8mb4_unicode_ci;


-- ------------------------------------------------------------
-- 3. Customer transaction types
--
-- expected_direction:
--
-- INCREASE = normally increases amount customer owes shop
-- DECREASE = normally decreases amount customer owes shop
-- EITHER   = special authorized adjustment
-- ------------------------------------------------------------

CREATE TABLE IF NOT EXISTS customer_transaction_types (
    id SMALLINT UNSIGNED NOT NULL AUTO_INCREMENT,

    code VARCHAR(60) NOT NULL,
    name VARCHAR(150) NOT NULL,

    expected_direction VARCHAR(10) NOT NULL,

    description VARCHAR(500) DEFAULT NULL,

    is_active BOOLEAN NOT NULL DEFAULT TRUE,

    PRIMARY KEY (id),

    UNIQUE KEY uq_customer_transaction_type_code (code),

    CONSTRAINT chk_customer_transaction_direction
        CHECK (
            expected_direction IN (
                'INCREASE',
                'DECREASE',
                'EITHER'
            )
        )
) ENGINE=InnoDB
  DEFAULT CHARSET=utf8mb4
  COLLATE=utf8mb4_unicode_ci;


-- ------------------------------------------------------------
-- 4. Customer transaction ledger
--
-- amount_delta is SIGNED.
--
-- Example:
--
-- Sale remainder:
--     +5000
--
-- Customer later pays:
--     -5000
--
-- Balance becomes zero.
--
-- Example when shop owes customer:
--
-- Shop owes customer:
--     -3000
--
-- Shop later pays customer:
--     +3000
--
-- Balance becomes zero.
-- ------------------------------------------------------------

CREATE TABLE IF NOT EXISTS customer_transactions (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,

    customer_id BIGINT UNSIGNED NOT NULL,

    transaction_type_id SMALLINT UNSIGNED NOT NULL,

    amount_delta DECIMAL(16,2) NOT NULL,

    -- Generic reference until Billing tables are created.
    reference_type VARCHAR(50) DEFAULT NULL,
    reference_id BIGINT UNSIGNED DEFAULT NULL,
    reference_code VARCHAR(100) DEFAULT NULL,

    description VARCHAR(500) DEFAULT NULL,
    notes VARCHAR(1000) DEFAULT NULL,

    occurred_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,

    created_by_user_id INT UNSIGNED DEFAULT NULL,

    -- Historical migration traceability.
    legacy_source_table VARCHAR(50) DEFAULT NULL,
    legacy_source_id BIGINT UNSIGNED DEFAULT NULL,

    metadata_json JSON DEFAULT NULL,

    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,

    PRIMARY KEY (id),

    KEY idx_customer_transactions_customer (
        customer_id
    ),

    KEY idx_customer_transactions_date (
        occurred_at
    ),

    KEY idx_customer_transactions_type (
        transaction_type_id
    ),

    KEY idx_customer_transactions_reference (
        reference_type,
        reference_id
    ),

    KEY idx_customer_transactions_legacy (
        legacy_source_table,
        legacy_source_id
    ),

    CONSTRAINT fk_customer_transaction_customer
        FOREIGN KEY (customer_id)
        REFERENCES customers(id),

    CONSTRAINT fk_customer_transaction_type
        FOREIGN KEY (transaction_type_id)
        REFERENCES customer_transaction_types(id),

    CONSTRAINT fk_customer_transaction_user
        FOREIGN KEY (created_by_user_id)
        REFERENCES users(id)
        ON DELETE SET NULL,

    CONSTRAINT chk_customer_transaction_nonzero
        CHECK (amount_delta <> 0)
) ENGINE=InnoDB
  DEFAULT CHARSET=utf8mb4
  COLLATE=utf8mb4_unicode_ci;


-- ------------------------------------------------------------
-- 5. Current customer balances
--
-- VIEW ONLY.
-- Source of truth is customer_transactions.
-- ------------------------------------------------------------

CREATE OR REPLACE VIEW vw_customer_balances AS

SELECT
    c.id AS customer_id,
    c.customer_code,
    c.name,
    c.mobile,
    c.place,

    CAST(
        COALESCE(
            SUM(ct.amount_delta),
            0
        )
        AS DECIMAL(16,2)
    ) AS balance,

    CASE

        WHEN COALESCE(SUM(ct.amount_delta), 0) > 0
            THEN 'CUSTOMER_OWES_SHOP'

        WHEN COALESCE(SUM(ct.amount_delta), 0) < 0
            THEN 'SHOP_OWES_CUSTOMER'

        ELSE 'SETTLED'

    END AS balance_status,

    MAX(ct.occurred_at) AS last_transaction_at

FROM customers c

LEFT JOIN customer_transactions ct
    ON ct.customer_id = c.id

GROUP BY
    c.id,
    c.customer_code,
    c.name,
    c.mobile,
    c.place;


-- ------------------------------------------------------------
-- 6. Dead Cash write-offs
--
-- Permanent history.
--
-- amount is always positive here.
--
-- The corresponding customer transaction will normally be
-- negative because Dead Cash reduces money owed by customer.
--
-- Partial Dead Cash is naturally supported.
-- ------------------------------------------------------------

CREATE TABLE IF NOT EXISTS dead_cash_writeoffs (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,

    customer_id BIGINT UNSIGNED NOT NULL,

    amount DECIMAL(16,2) NOT NULL,

    -- Customer balance immediately before write-off.
    balance_before DECIMAL(16,2) NOT NULL,

    -- Customer balance immediately after write-off.
    balance_after DECIMAL(16,2) NOT NULL,

    writeoff_transaction_id BIGINT UNSIGNED NOT NULL,

    reason VARCHAR(1000) NOT NULL,

    notes VARCHAR(1000) DEFAULT NULL,

    occurred_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,

    created_by_user_id INT UNSIGNED DEFAULT NULL,

    -- A write-off is never deleted.
    -- If entered incorrectly, it can be reversed.
    is_reversed BOOLEAN NOT NULL DEFAULT FALSE,

    reversed_at DATETIME DEFAULT NULL,
    reversed_by_user_id INT UNSIGNED DEFAULT NULL,

    reversal_transaction_id BIGINT UNSIGNED DEFAULT NULL,

    reversal_reason VARCHAR(1000) DEFAULT NULL,

    PRIMARY KEY (id),

    UNIQUE KEY uq_dead_cash_writeoff_transaction (
        writeoff_transaction_id
    ),

    UNIQUE KEY uq_dead_cash_reversal_transaction (
        reversal_transaction_id
    ),

    KEY idx_dead_cash_customer (
        customer_id
    ),

    KEY idx_dead_cash_date (
        occurred_at
    ),

    KEY idx_dead_cash_reversed (
        is_reversed
    ),

    CONSTRAINT fk_dead_cash_customer
        FOREIGN KEY (customer_id)
        REFERENCES customers(id),

    CONSTRAINT fk_dead_cash_writeoff_transaction
        FOREIGN KEY (writeoff_transaction_id)
        REFERENCES customer_transactions(id),

    CONSTRAINT fk_dead_cash_created_user
        FOREIGN KEY (created_by_user_id)
        REFERENCES users(id)
        ON DELETE SET NULL,

    CONSTRAINT fk_dead_cash_reversed_user
        FOREIGN KEY (reversed_by_user_id)
        REFERENCES users(id)
        ON DELETE SET NULL,

    CONSTRAINT fk_dead_cash_reversal_transaction
        FOREIGN KEY (reversal_transaction_id)
        REFERENCES customer_transactions(id)
        ON DELETE SET NULL,

    CONSTRAINT chk_dead_cash_amount
        CHECK (amount > 0)
) ENGINE=InnoDB
  DEFAULT CHARSET=utf8mb4
  COLLATE=utf8mb4_unicode_ci;


-- ------------------------------------------------------------
-- 7. Customer flags
--
-- DEAD_CASH_WARNING is the prototype-2 behavior:
--
-- Dead Cash history remains forever.
-- The active warning can later be removed.
--
-- Only one active flag of each type is allowed per customer.
-- ------------------------------------------------------------

CREATE TABLE IF NOT EXISTS customer_flags (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,

    customer_id BIGINT UNSIGNED NOT NULL,

    flag_type VARCHAR(50) NOT NULL,

    severity VARCHAR(20)
        NOT NULL DEFAULT 'WARNING',

    reason VARCHAR(1000) NOT NULL,

    related_dead_cash_id BIGINT UNSIGNED DEFAULT NULL,

    is_active BOOLEAN NOT NULL DEFAULT TRUE,

    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    created_by_user_id INT UNSIGNED DEFAULT NULL,

    deactivated_at DATETIME DEFAULT NULL,
    deactivated_by_user_id INT UNSIGNED DEFAULT NULL,

    deactivation_reason VARCHAR(1000) DEFAULT NULL,

    active_flag_key VARCHAR(150)
        GENERATED ALWAYS AS (
            CASE
                WHEN is_active = 1
                THEN CONCAT(customer_id, ':', flag_type)
                ELSE NULL
            END
        ) STORED,

    PRIMARY KEY (id),

    UNIQUE KEY uq_customer_active_flag (
        active_flag_key
    ),

    KEY idx_customer_flags_customer (
        customer_id
    ),

    KEY idx_customer_flags_type (
        flag_type
    ),

    KEY idx_customer_flags_active (
        is_active
    ),

    CONSTRAINT fk_customer_flag_customer
        FOREIGN KEY (customer_id)
        REFERENCES customers(id),

    CONSTRAINT fk_customer_flag_dead_cash
        FOREIGN KEY (related_dead_cash_id)
        REFERENCES dead_cash_writeoffs(id)
        ON DELETE SET NULL,

    CONSTRAINT fk_customer_flag_created_user
        FOREIGN KEY (created_by_user_id)
        REFERENCES users(id)
        ON DELETE SET NULL,

    CONSTRAINT fk_customer_flag_deactivated_user
        FOREIGN KEY (deactivated_by_user_id)
        REFERENCES users(id)
        ON DELETE SET NULL,

    CONSTRAINT chk_customer_flag_severity
        CHECK (
            severity IN (
                'INFO',
                'WARNING',
                'CRITICAL'
            )
        )
) ENGINE=InnoDB
  DEFAULT CHARSET=utf8mb4
  COLLATE=utf8mb4_unicode_ci;


-- ============================================================
-- INITIAL CUSTOMER TYPES
-- ============================================================

INSERT INTO customer_types (
    code,
    name,
    sort_order
)
VALUES
    ('REGULAR', 'Regular', 1),
    ('VIP',     'VIP',     2)

ON DUPLICATE KEY UPDATE
    name = VALUES(name),
    sort_order = VALUES(sort_order);


-- ============================================================
-- CUSTOMER TRANSACTION TYPES
-- ============================================================

INSERT INTO customer_transaction_types (
    code,
    name,
    expected_direction,
    description
)
VALUES

    (
        'SALE_BALANCE_DUE',
        'Sale Remainder',
        'INCREASE',
        'Unpaid amount from a sale; customer owes the shop'
    ),

    (
        'BUY_BALANCE_DUE',
        'Purchase Remainder',
        'DECREASE',
        'Unpaid amount when shop buys from customer; shop owes customer'
    ),

    (
        'CUSTOMER_PAYMENT',
        'Customer Payment',
        'DECREASE',
        'Customer pays money to the shop'
    ),

    (
        'SHOP_PAYMENT',
        'Shop Payment to Customer',
        'INCREASE',
        'Shop pays money owed to the customer'
    ),

    (
        'CUSTOMER_ADVANCE',
        'Customer Advance',
        'DECREASE',
        'Customer deposits money in advance, creating customer credit'
    ),

    (
        'ADVANCE_APPLIED',
        'Customer Advance Applied',
        'INCREASE',
        'Previously deposited customer credit is used'
    ),

    (
        'DEAD_CASH_WRITE_OFF',
        'Dead Cash Write-Off',
        'DECREASE',
        'Unrecoverable customer receivable is written off'
    ),

    (
        'DEAD_CASH_REVERSAL',
        'Dead Cash Reversal',
        'INCREASE',
        'Restores a previously written-off customer receivable'
    ),

    (
        'MANUAL_ADJUSTMENT',
        'Manual Adjustment',
        'EITHER',
        'Authorized correction to customer financial balance'
    ),

    (
        'LEGACY_ADJUSTMENT',
        'Legacy Historical Adjustment',
        'EITHER',
        'Historical customer balance movement migrated from production'
    )

ON DUPLICATE KEY UPDATE
    name = VALUES(name),
    expected_direction = VALUES(expected_direction),
    description = VALUES(description);


-- ============================================================
-- CUSTOMER / DEAD CASH PERMISSIONS
-- ============================================================

INSERT INTO permissions (
    code,
    name,
    description
)
VALUES

    (
        'CUSTOMER_BALANCE_ADJUST',
        'Adjust Customer Balance',
        'Allows authorized customer balance corrections'
    ),

    (
        'CUSTOMER_FLAGS_MANAGE',
        'Manage Customer Flags',
        'Allows customer warning flags to be activated or deactivated'
    ),

    (
        'DEAD_CASH_REVERSE',
        'Reverse Dead Cash',
        'Allows reversal of an incorrectly entered Dead Cash write-off'
    )

ON DUPLICATE KEY UPDATE
    name = VALUES(name),
    description = VALUES(description);


-- Owner receives only missing permissions.

INSERT INTO role_permissions (
    role_id,
    permission_id
)
SELECT
    r.id,
    p.id
FROM roles r
CROSS JOIN permissions p

LEFT JOIN role_permissions rp
    ON rp.role_id = r.id
   AND rp.permission_id = p.id

WHERE r.code = 'OWNER'
  AND rp.role_id IS NULL;


-- ============================================================
-- REGISTER MIGRATION
-- ============================================================

INSERT INTO schema_migrations (
    version,
    name
)
VALUES (
    5,
    'Customers, customer ledger, flags and Dead Cash'
)
ON DUPLICATE KEY UPDATE
    name = VALUES(name);