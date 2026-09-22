USE masgold_v2;

-- ============================================================
-- masGold v2
-- Migration 004: Old Gold / Scrap Ledger
--
-- Core rule:
-- Current old-gold balance is NEVER manually stored.
-- It is calculated from permanent movement history.
--
-- Positive weight_delta_grams = gold entered old-gold balance
-- Negative weight_delta_grams = gold left old-gold balance
-- ============================================================


-- ------------------------------------------------------------
-- 1. Old-gold movement types
-- ------------------------------------------------------------

CREATE TABLE IF NOT EXISTS old_gold_movement_types (
    id SMALLINT UNSIGNED NOT NULL AUTO_INCREMENT,

    code VARCHAR(50) NOT NULL,
    name VARCHAR(150) NOT NULL,
    description VARCHAR(500) DEFAULT NULL,

    -- Expected direction helps validation in Python later.
    expected_direction VARCHAR(10) NOT NULL,

    is_active BOOLEAN NOT NULL DEFAULT TRUE,

    PRIMARY KEY (id),
    UNIQUE KEY uq_old_gold_movement_type_code (code),

    CONSTRAINT chk_old_gold_expected_direction
        CHECK (
            expected_direction IN (
                'IN',
                'OUT',
                'EITHER'
            )
        )
) ENGINE=InnoDB
  DEFAULT CHARSET=utf8mb4
  COLLATE=utf8mb4_unicode_ci;


-- ------------------------------------------------------------
-- 2. Old-gold movements
--
-- One row = one physical metal movement.
--
-- Examples:
--
-- Customer sells us 5.200g 21K
-- weight_delta_grams = +5.200
--
-- Manufacturing consumes 4.620g 21K
-- weight_delta_grams = -4.620
--
-- Finished stock weight is NOT assumed to equal old-gold
-- consumption weight.
-- ------------------------------------------------------------

CREATE TABLE IF NOT EXISTS old_gold_movements (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,

    movement_type_id SMALLINT UNSIGNED NOT NULL,
    karat_id TINYINT UNSIGNED NOT NULL,

    weight_delta_grams DECIMAL(12,3) NOT NULL,

    -- Optional economics frozen at movement time.
    gold_price_per_gram DECIMAL(14,4) DEFAULT NULL,
    cash_value DECIMAL(16,2) DEFAULT NULL,

    price_quote_id BIGINT UNSIGNED DEFAULT NULL,

    -- Used to connect several movements belonging to one
    -- business operation, e.g. karat conversion or manufacture.
    movement_group_id CHAR(36) DEFAULT NULL,

    -- Optional physical inventory relation.
    -- Example: a stock item converted into scrap.
    related_inventory_item_id BIGINT UNSIGNED DEFAULT NULL,

    -- Generic reference until Billing exists.
    -- Examples later:
    -- INVOICE
    -- MANUFACTURING
    -- INVENTORY_CONVERSION
    -- MARKET
    reference_type VARCHAR(50) DEFAULT NULL,
    reference_id BIGINT UNSIGNED DEFAULT NULL,
    reference_code VARCHAR(100) DEFAULT NULL,

    notes VARCHAR(1000) DEFAULT NULL,

    occurred_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,

    created_by_user_id INT UNSIGNED DEFAULT NULL,

    -- Preserve traceability when importing historical data.
    legacy_source_table VARCHAR(50) DEFAULT NULL,
    legacy_source_id BIGINT UNSIGNED DEFAULT NULL,

    metadata_json JSON DEFAULT NULL,

    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,

    PRIMARY KEY (id),

    KEY idx_old_gold_karat (
        karat_id
    ),

    KEY idx_old_gold_date (
        occurred_at
    ),

    KEY idx_old_gold_type (
        movement_type_id
    ),

    KEY idx_old_gold_group (
        movement_group_id
    ),

    KEY idx_old_gold_inventory_item (
        related_inventory_item_id
    ),

    KEY idx_old_gold_reference (
        reference_type,
        reference_id
    ),

    KEY idx_old_gold_legacy (
        legacy_source_table,
        legacy_source_id
    ),

    CONSTRAINT fk_old_gold_movement_type
        FOREIGN KEY (movement_type_id)
        REFERENCES old_gold_movement_types(id),

    CONSTRAINT fk_old_gold_karat
        FOREIGN KEY (karat_id)
        REFERENCES karats(id),

    CONSTRAINT fk_old_gold_price_quote
        FOREIGN KEY (price_quote_id)
        REFERENCES gold_price_quotes(id)
        ON DELETE SET NULL,

    CONSTRAINT fk_old_gold_inventory_item
        FOREIGN KEY (related_inventory_item_id)
        REFERENCES inventory_items(id)
        ON DELETE SET NULL,

    CONSTRAINT fk_old_gold_user
        FOREIGN KEY (created_by_user_id)
        REFERENCES users(id)
        ON DELETE SET NULL,

    CONSTRAINT chk_old_gold_nonzero_weight
        CHECK (weight_delta_grams <> 0),

    CONSTRAINT chk_old_gold_price_positive
        CHECK (
            gold_price_per_gram IS NULL
            OR gold_price_per_gram > 0
        ),

    CONSTRAINT chk_old_gold_cash_value
        CHECK (
            cash_value IS NULL
            OR cash_value >= 0
        )
) ENGINE=InnoDB
  DEFAULT CHARSET=utf8mb4
  COLLATE=utf8mb4_unicode_ci;


-- ------------------------------------------------------------
-- 3. Current old-gold balances
--
-- This is a VIEW, not another stored balance.
--
-- That means there is only ONE source of truth:
-- old_gold_movements.
-- ------------------------------------------------------------

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

GROUP BY
    k.id,
    k.karat_value,
    k.name;


-- ============================================================
-- INITIAL MOVEMENT TYPES
-- ============================================================

INSERT INTO old_gold_movement_types (
    code,
    name,
    description,
    expected_direction
)
VALUES

    (
        'CUSTOMER_BUY_IN',
        'Bought From Customer',
        'Old gold purchased from a customer',
        'IN'
    ),

    (
        'MARKET_IN',
        'Market Gold In',
        'Old gold received from the market or external party',
        'IN'
    ),

    (
        'STOCK_SCRAP_IN',
        'Inventory Converted to Old Gold',
        'Sellable stock item transferred into old-gold balance',
        'IN'
    ),

    (
        'MANUFACTURE_OUT',
        'Manufacturing Out',
        'Actual old-gold weight sent or consumed for manufacturing',
        'OUT'
    ),

    (
        'MARKET_SALE_OUT',
        'Old Gold Sold Out',
        'Old gold sold or transferred out to the market',
        'OUT'
    ),

    (
        'KARAT_CONVERSION_IN',
        'Karat Conversion In',
        'Gold received into this karat after a conversion',
        'IN'
    ),

    (
        'KARAT_CONVERSION_OUT',
        'Karat Conversion Out',
        'Gold removed from this karat for conversion',
        'OUT'
    ),

    (
        'ADJUSTMENT_IN',
        'Positive Adjustment',
        'Authorized physical old-gold increase',
        'IN'
    ),

    (
        'ADJUSTMENT_OUT',
        'Negative Adjustment',
        'Authorized physical old-gold decrease',
        'OUT'
    ),

    (
        'LEGACY_IN',
        'Legacy Historical In',
        'Historical incoming movement migrated from production',
        'IN'
    ),

    (
        'LEGACY_OUT',
        'Legacy Historical Out',
        'Historical outgoing movement migrated from production',
        'OUT'
    )

ON DUPLICATE KEY UPDATE
    name = VALUES(name),
    description = VALUES(description),
    expected_direction = VALUES(expected_direction);


-- ============================================================
-- OLD-GOLD PERMISSIONS
-- ============================================================

INSERT INTO permissions (
    code,
    name,
    description
)
VALUES

    (
        'OLD_GOLD_VIEW',
        'View Old Gold',
        'Allows viewing old-gold balances and movement history'
    ),

    (
        'OLD_GOLD_MANAGE',
        'Manage Old Gold',
        'Allows normal old-gold movements'
    ),

    (
        'OLD_GOLD_ADJUST',
        'Adjust Old Gold',
        'Allows authorized physical old-gold adjustments'
    ),

    (
        'OLD_GOLD_CONVERT_STOCK',
        'Convert Stock to Old Gold',
        'Allows a sellable inventory item to be transferred into old gold'
    )

ON DUPLICATE KEY UPDATE
    name = VALUES(name),
    description = VALUES(description);


-- Owner receives only permissions not already assigned.

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
    4,
    'Old gold movement ledger and balances'
)
ON DUPLICATE KEY UPDATE
    name = VALUES(name);