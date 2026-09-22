USE masgold_v2;

-- ============================================================
-- masGold v2
-- Migration 008: Owner JRD
--
-- JRD = 21K-equivalent position
--
-- INCLUDED:
--   New sellable stock
--   Old sellable stock
--   Old gold / scrap
--   Physical cash converted to 21K
--
-- EXCLUDED:
--   Bank balances
--   Customer receivables
--
-- This is OWNER-ONLY information at application/service level.
-- ============================================================


-- ------------------------------------------------------------
-- 1. JRD settings
-- ------------------------------------------------------------

CREATE TABLE IF NOT EXISTS jrd_settings (
    id TINYINT UNSIGNED NOT NULL,

    base_karat_id TINYINT UNSIGNED NOT NULL,

    -- How physical cash is converted into grams of 21K.
    --
    -- SHOP_SELL:
    -- how much 21K the physical cash can buy using shop sell price.
    --
    -- SHOP_BUY:
    -- alternative valuation basis if owner chooses later.
    cash_price_basis VARCHAR(20)
        NOT NULL DEFAULT 'SHOP_SELL',

    is_enabled BOOLEAN NOT NULL DEFAULT TRUE,

    updated_by_user_id INT UNSIGNED DEFAULT NULL,

    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
        ON UPDATE CURRENT_TIMESTAMP,

    PRIMARY KEY (id),

    CONSTRAINT fk_jrd_base_karat
        FOREIGN KEY (base_karat_id)
        REFERENCES karats(id),

    CONSTRAINT fk_jrd_settings_user
        FOREIGN KEY (updated_by_user_id)
        REFERENCES users(id)
        ON DELETE SET NULL,

    CONSTRAINT chk_jrd_single_row
        CHECK (id = 1),

    CONSTRAINT chk_jrd_cash_price_basis
        CHECK (
            cash_price_basis IN (
                'SHOP_BUY',
                'SHOP_SELL'
            )
        )
) ENGINE=InnoDB
  DEFAULT CHARSET=utf8mb4
  COLLATE=utf8mb4_unicode_ci;


-- ------------------------------------------------------------
-- 2. Current gold components
--
-- Converts every karat to 21K equivalent:
--
-- equivalent_21 =
--     grams × karat / 21
--
-- Examples:
-- 21K = grams × 1
-- 18K = grams × 18/21
-- 24K = grams × 24/21
-- ------------------------------------------------------------

CREATE OR REPLACE VIEW vw_jrd_current_gold_components AS

SELECT
    'NEW_STOCK' AS component_type,

    k.id AS karat_id,
    k.karat_value,

    CAST(
        COALESCE(
            SUM(
                CASE
                    WHEN i.inventory_status = 'IN_STOCK'
                     AND i.stock_classification = 'NEW_STOCK'
                    THEN i.weight_grams
                    ELSE 0
                END
            ),
            0
        )
        AS DECIMAL(18,3)
    ) AS physical_grams,

    CAST(
        COALESCE(
            SUM(
                CASE
                    WHEN i.inventory_status = 'IN_STOCK'
                     AND i.stock_classification = 'NEW_STOCK'
                    THEN i.weight_grams
                    ELSE 0
                END
            ),
            0
        )
        * k.karat_value / 21
        AS DECIMAL(18,3)
    ) AS equivalent_21_grams

FROM karats k

LEFT JOIN inventory_items i
    ON i.karat_id = k.id

GROUP BY
    k.id,
    k.karat_value


UNION ALL


SELECT
    'OLD_STOCK' AS component_type,

    k.id AS karat_id,
    k.karat_value,

    CAST(
        COALESCE(
            SUM(
                CASE
                    WHEN i.inventory_status = 'IN_STOCK'
                     AND i.stock_classification = 'OLD_STOCK'
                    THEN i.weight_grams
                    ELSE 0
                END
            ),
            0
        )
        AS DECIMAL(18,3)
    ) AS physical_grams,

    CAST(
        COALESCE(
            SUM(
                CASE
                    WHEN i.inventory_status = 'IN_STOCK'
                     AND i.stock_classification = 'OLD_STOCK'
                    THEN i.weight_grams
                    ELSE 0
                END
            ),
            0
        )
        * k.karat_value / 21
        AS DECIMAL(18,3)
    ) AS equivalent_21_grams

FROM karats k

LEFT JOIN inventory_items i
    ON i.karat_id = k.id

GROUP BY
    k.id,
    k.karat_value


UNION ALL


SELECT
    'OLD_GOLD' AS component_type,

    k.id AS karat_id,
    k.karat_value,

    CAST(
        COALESCE(
            og.balance_grams,
            0
        )
        AS DECIMAL(18,3)
    ) AS physical_grams,

    CAST(
        COALESCE(
            og.balance_grams,
            0
        )
        * k.karat_value / 21
        AS DECIMAL(18,3)
    ) AS equivalent_21_grams

FROM karats k

LEFT JOIN vw_old_gold_balances og
    ON og.karat_id = k.id;


-- ------------------------------------------------------------
-- 3. Current gold totals
-- ------------------------------------------------------------

CREATE OR REPLACE VIEW vw_jrd_current_gold_totals AS

SELECT

    CAST(
        COALESCE(
            SUM(
                CASE
                    WHEN component_type = 'NEW_STOCK'
                    THEN equivalent_21_grams
                    ELSE 0
                END
            ),
            0
        )
        AS DECIMAL(18,3)
    ) AS new_stock_equivalent_21,

    CAST(
        COALESCE(
            SUM(
                CASE
                    WHEN component_type = 'OLD_STOCK'
                    THEN equivalent_21_grams
                    ELSE 0
                END
            ),
            0
        )
        AS DECIMAL(18,3)
    ) AS old_stock_equivalent_21,

    CAST(
        COALESCE(
            SUM(
                CASE
                    WHEN component_type = 'OLD_GOLD'
                    THEN equivalent_21_grams
                    ELSE 0
                END
            ),
            0
        )
        AS DECIMAL(18,3)
    ) AS old_gold_equivalent_21,

    CAST(
        COALESCE(
            SUM(equivalent_21_grams),
            0
        )
        AS DECIMAL(18,3)
    ) AS total_gold_equivalent_21

FROM vw_jrd_current_gold_components;


-- ------------------------------------------------------------
-- 4. Current JRD
--
-- IMPORTANT:
-- Bank is displayed for context but EXCLUDED from JRD.
--
-- If there is no valid active 21K price,
-- cash equivalent and total JRD become NULL.
-- We deliberately do NOT guess.
-- ------------------------------------------------------------

CREATE OR REPLACE VIEW vw_jrd_current AS

SELECT

    x.base_karat_value,

    x.new_stock_equivalent_21,
    x.old_stock_equivalent_21,
    x.old_gold_equivalent_21,

    x.total_gold_equivalent_21,

    x.physical_cash_egp,

    x.bank_balance_egp_excluded,

    x.cash_price_basis,

    x.cash_conversion_price_per_gram,

    CASE

        WHEN x.cash_conversion_price_per_gram IS NULL
          OR x.cash_conversion_price_per_gram <= 0
        THEN NULL

        ELSE CAST(
            x.physical_cash_egp
            /
            x.cash_conversion_price_per_gram

            AS DECIMAL(18,3)
        )

    END AS cash_equivalent_21,

    CASE

        WHEN x.cash_conversion_price_per_gram IS NULL
          OR x.cash_conversion_price_per_gram <= 0
        THEN NULL

        ELSE CAST(
            x.total_gold_equivalent_21
            +
            (
                x.physical_cash_egp
                /
                x.cash_conversion_price_per_gram
            )

            AS DECIMAL(18,3)
        )

    END AS total_jrd_21,

    x.active_quote_id,

    CASE

        WHEN x.cash_conversion_price_per_gram IS NULL
          OR x.cash_conversion_price_per_gram <= 0
        THEN 'PRICE_MISSING'

        ELSE 'READY'

    END AS jrd_status

FROM (

    SELECT
        bk.karat_value AS base_karat_value,

        gt.new_stock_equivalent_21,
        gt.old_stock_equivalent_21,
        gt.old_gold_equivalent_21,
        gt.total_gold_equivalent_21,

        CAST(
            COALESCE(
                (
                    SELECT SUM(cb.balance)
                    FROM vw_cash_balances cb
                    WHERE cb.include_in_jrd = TRUE
                ),
                0
            )
            AS DECIMAL(18,2)
        ) AS physical_cash_egp,

        CAST(
            COALESCE(
                (
                    SELECT SUM(bb.balance)
                    FROM vw_bank_balances bb
                ),
                0
            )
            AS DECIMAL(18,2)
        ) AS bank_balance_egp_excluded,

        js.cash_price_basis,

        CASE

            WHEN js.cash_price_basis = 'SHOP_BUY'
            THEN q.shop_buy_price_per_gram

            WHEN js.cash_price_basis = 'SHOP_SELL'
            THEN q.shop_sell_price_per_gram

            ELSE NULL

        END AS cash_conversion_price_per_gram,

        q.id AS active_quote_id

    FROM jrd_settings js

    JOIN karats bk
        ON bk.id = js.base_karat_id

    CROSS JOIN vw_jrd_current_gold_totals gt

    LEFT JOIN active_gold_prices agp
        ON agp.karat_id = js.base_karat_id

    LEFT JOIN gold_price_quotes q
        ON q.id = agp.quote_id

    WHERE js.id = 1

) x;


-- ------------------------------------------------------------
-- 5. JRD historical snapshots
--
-- This freezes the confidential owner position at a point
-- in time.
-- ------------------------------------------------------------

CREATE TABLE IF NOT EXISTS jrd_snapshots (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,

    snapshot_at DATETIME NOT NULL,

    base_karat_id TINYINT UNSIGNED NOT NULL,

    new_stock_equivalent_21 DECIMAL(18,3) NOT NULL,

    old_stock_equivalent_21 DECIMAL(18,3) NOT NULL,

    old_gold_equivalent_21 DECIMAL(18,3) NOT NULL,

    total_gold_equivalent_21 DECIMAL(18,3) NOT NULL,

    physical_cash_egp DECIMAL(18,2) NOT NULL,

    -- Stored for explanation only.
    -- Never included in total_jrd_21.
    bank_balance_egp_excluded DECIMAL(18,2)
        NOT NULL DEFAULT 0.00,

    cash_price_basis VARCHAR(20) NOT NULL,

    cash_conversion_price_per_gram DECIMAL(14,4) NOT NULL,

    cash_price_quote_id BIGINT UNSIGNED DEFAULT NULL,

    cash_equivalent_21 DECIMAL(18,3) NOT NULL,

    total_jrd_21 DECIMAL(18,3) NOT NULL,

    snapshot_type VARCHAR(20)
        NOT NULL DEFAULT 'AUTOMATIC',

    created_by_user_id INT UNSIGNED DEFAULT NULL,

    notes VARCHAR(1000) DEFAULT NULL,

    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,

    PRIMARY KEY (id),

    KEY idx_jrd_snapshot_date (
        snapshot_at
    ),

    KEY idx_jrd_snapshot_type (
        snapshot_type
    ),

    CONSTRAINT fk_jrd_snapshot_base_karat
        FOREIGN KEY (base_karat_id)
        REFERENCES karats(id),

    CONSTRAINT fk_jrd_snapshot_quote
        FOREIGN KEY (cash_price_quote_id)
        REFERENCES gold_price_quotes(id)
        ON DELETE SET NULL,

    CONSTRAINT fk_jrd_snapshot_user
        FOREIGN KEY (created_by_user_id)
        REFERENCES users(id)
        ON DELETE SET NULL,

    CONSTRAINT chk_jrd_snapshot_price_basis
        CHECK (
            cash_price_basis IN (
                'SHOP_BUY',
                'SHOP_SELL'
            )
        ),

    CONSTRAINT chk_jrd_snapshot_type
        CHECK (
            snapshot_type IN (
                'AUTOMATIC',
                'DAILY_CLOSE',
                'MANUAL',
                'LEGACY'
            )
        ),

    CONSTRAINT chk_jrd_snapshot_price
        CHECK (
            cash_conversion_price_per_gram > 0
        )
) ENGINE=InnoDB
  DEFAULT CHARSET=utf8mb4
  COLLATE=utf8mb4_unicode_ci;


-- ------------------------------------------------------------
-- 6. Exact gold breakdown for each snapshot
--
-- Example:
--
-- Snapshot 55
--
-- NEW_STOCK 21K  900g
-- NEW_STOCK 18K  210g
-- OLD_STOCK 21K  300g
-- OLD_GOLD  24K   21g
--
-- This lets us reproduce every historical JRD precisely.
-- ------------------------------------------------------------

CREATE TABLE IF NOT EXISTS jrd_snapshot_gold_components (
    snapshot_id BIGINT UNSIGNED NOT NULL,

    component_type VARCHAR(20) NOT NULL,

    karat_id TINYINT UNSIGNED NOT NULL,

    physical_grams DECIMAL(18,3) NOT NULL,

    equivalent_21_grams DECIMAL(18,3) NOT NULL,

    PRIMARY KEY (
        snapshot_id,
        component_type,
        karat_id
    ),

    CONSTRAINT fk_jrd_component_snapshot
        FOREIGN KEY (snapshot_id)
        REFERENCES jrd_snapshots(id)
        ON DELETE CASCADE,

    CONSTRAINT fk_jrd_component_karat
        FOREIGN KEY (karat_id)
        REFERENCES karats(id),

    CONSTRAINT chk_jrd_component_type
        CHECK (
            component_type IN (
                'NEW_STOCK',
                'OLD_STOCK',
                'OLD_GOLD'
            )
        )
) ENGINE=InnoDB
  DEFAULT CHARSET=utf8mb4
  COLLATE=utf8mb4_unicode_ci;


-- ------------------------------------------------------------
-- 7. Additional Owner-only permissions
-- ------------------------------------------------------------

INSERT INTO permissions (
    code,
    name,
    description
)
VALUES

    (
        'OWNER_JRD_SETTINGS',
        'Manage Owner JRD Settings',
        'Allows modification of confidential JRD calculation settings'
    ),

    (
        'OWNER_JRD_SNAPSHOT',
        'Create Owner JRD Snapshot',
        'Allows creation of confidential JRD historical snapshots'
    )

ON DUPLICATE KEY UPDATE
    name = VALUES(name),
    description = VALUES(description);


-- IMPORTANT:
-- Only OWNER receives OWNER_JRD permissions.

INSERT INTO role_permissions (
    role_id,
    permission_id
)
SELECT
    r.id,
    p.id
FROM roles r

JOIN permissions p
    ON p.code IN (
        'OWNER_JRD_VIEW',
        'OWNER_JRD_SETTINGS',
        'OWNER_JRD_SNAPSHOT'
    )

LEFT JOIN role_permissions rp
    ON rp.role_id = r.id
   AND rp.permission_id = p.id

WHERE r.code = 'OWNER'
  AND rp.role_id IS NULL;


-- ------------------------------------------------------------
-- 8. Default JRD configuration = 21K
-- ------------------------------------------------------------

INSERT INTO jrd_settings (
    id,
    base_karat_id,
    cash_price_basis,
    is_enabled
)

SELECT
    1,
    k.id,
    'SHOP_SELL',
    TRUE

FROM karats k

WHERE k.karat_value = 21

ON DUPLICATE KEY UPDATE
    base_karat_id = VALUES(base_karat_id);


-- ------------------------------------------------------------
-- 9. Register migration
-- ------------------------------------------------------------

INSERT INTO schema_migrations (
    version,
    name
)
VALUES (
    8,
    'Confidential Owner JRD 21K-equivalent position'
)
ON DUPLICATE KEY UPDATE
    name = VALUES(name);