USE masgold_v2;

-- ============================================================
-- masGold v2
-- Migration 011: Analytics Foundation
--
-- Supports:
--   Historical analytics
--   Demand / stock availability
--   Seasonality
--   Forecasting
--   Explainable recommendations
--   Recommendation outcomes
--
-- Analytics data can always be rebuilt from operational data.
-- ============================================================


-- ============================================================
-- 1. ANALYTICAL SEGMENTS
--
-- A segment represents a combination such as:
--
-- 21K + Ring + Category B
--
-- or:
--
-- 18K + Bracelet + Category C + Factory X
--
-- NULL means "all" for that dimension.
-- ============================================================

CREATE TABLE IF NOT EXISTS analytics_segments (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,

    segment_key VARCHAR(255) NOT NULL,
    name VARCHAR(255) DEFAULT NULL,

    karat_id TINYINT UNSIGNED DEFAULT NULL,
    item_type_id SMALLINT UNSIGNED DEFAULT NULL,
    category_id SMALLINT UNSIGNED DEFAULT NULL,
    factory_id SMALLINT UNSIGNED DEFAULT NULL,

    stock_classification VARCHAR(20) DEFAULT NULL,

    is_active BOOLEAN NOT NULL DEFAULT TRUE,

    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,

    PRIMARY KEY (id),

    UNIQUE KEY uq_analytics_segment_key (
        segment_key
    ),

    KEY idx_segment_karat (
        karat_id
    ),

    KEY idx_segment_item (
        item_type_id
    ),

    KEY idx_segment_category (
        category_id
    ),

    KEY idx_segment_factory (
        factory_id
    ),

    CONSTRAINT fk_segment_karat
        FOREIGN KEY (karat_id)
        REFERENCES karats(id)
        ON DELETE SET NULL,

    CONSTRAINT fk_segment_item_type
        FOREIGN KEY (item_type_id)
        REFERENCES item_types(id)
        ON DELETE SET NULL,

    CONSTRAINT fk_segment_category
        FOREIGN KEY (category_id)
        REFERENCES item_categories(id)
        ON DELETE SET NULL,

    CONSTRAINT fk_segment_factory
        FOREIGN KEY (factory_id)
        REFERENCES factories(id)
        ON DELETE SET NULL,

    CONSTRAINT chk_segment_stock_class
        CHECK (
            stock_classification IS NULL
            OR stock_classification IN (
                'NEW_STOCK',
                'OLD_STOCK'
            )
        )
) ENGINE=InnoDB
  DEFAULT CHARSET=utf8mb4
  COLLATE=utf8mb4_unicode_ci;


-- ============================================================
-- 2. ANALYTICS BUILD / REBUILD RUNS
--
-- Lets us know when and how derived analytics were created.
-- ============================================================

CREATE TABLE IF NOT EXISTS analytics_runs (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,

    run_type VARCHAR(30) NOT NULL,

    date_from DATE DEFAULT NULL,
    date_to DATE DEFAULT NULL,

    source_type VARCHAR(30) NOT NULL,

    status VARCHAR(20)
        NOT NULL DEFAULT 'RUNNING',

    started_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    completed_at DATETIME DEFAULT NULL,

    rows_processed BIGINT UNSIGNED NOT NULL DEFAULT 0,

    quality_score DECIMAL(6,3) DEFAULT NULL,

    notes VARCHAR(2000) DEFAULT NULL,

    created_by_user_id INT UNSIGNED DEFAULT NULL,

    PRIMARY KEY (id),

    KEY idx_analytics_run_type (
        run_type
    ),

    KEY idx_analytics_run_status (
        status
    ),

    KEY idx_analytics_run_date (
        started_at
    ),

    CONSTRAINT fk_analytics_run_user
        FOREIGN KEY (created_by_user_id)
        REFERENCES users(id)
        ON DELETE SET NULL,

    CONSTRAINT chk_analytics_run_type
        CHECK (
            run_type IN (
                'DAILY_BUILD',
                'FULL_REBUILD',
                'LEGACY_IMPORT',
                'BACKFILL'
            )
        ),

    CONSTRAINT chk_analytics_source
        CHECK (
            source_type IN (
                'V2_OPERATIONAL',
                'LEGACY_PRODUCTION',
                'MIXED'
            )
        ),

    CONSTRAINT chk_analytics_status
        CHECK (
            status IN (
                'RUNNING',
                'SUCCESS',
                'FAILED',
                'PARTIAL'
            )
        ),

    CONSTRAINT chk_analytics_quality
        CHECK (
            quality_score IS NULL
            OR (
                quality_score >= 0
                AND quality_score <= 100
            )
        )
) ENGINE=InnoDB
  DEFAULT CHARSET=utf8mb4
  COLLATE=utf8mb4_unicode_ci;


-- ============================================================
-- 3. DAILY INVENTORY / DEMAND METRICS
--
-- This is the major table for:
--
-- demand
-- turnover
-- stock availability
-- stockout detection
-- making/margin analysis
-- seasonality
--
-- One row = one date + one analytical segment.
-- ============================================================

CREATE TABLE IF NOT EXISTS analytics_daily_metrics (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,

    metric_date DATE NOT NULL,

    segment_id BIGINT UNSIGNED NOT NULL,

    analytics_run_id BIGINT UNSIGNED DEFAULT NULL,

    -- -----------------------
    -- Inventory
    -- -----------------------

    opening_stock_quantity INT UNSIGNED NOT NULL DEFAULT 0,

    closing_stock_quantity INT UNSIGNED NOT NULL DEFAULT 0,

    opening_stock_weight_grams DECIMAL(18,3)
        NOT NULL DEFAULT 0.000,

    closing_stock_weight_grams DECIMAL(18,3)
        NOT NULL DEFAULT 0.000,

    -- Estimated time during which stock was unavailable.
    stockout_hours DECIMAL(8,3)
        NOT NULL DEFAULT 0.000,

    availability_percent DECIMAL(7,4)
        NOT NULL DEFAULT 100.0000,

    -- -----------------------
    -- Sales
    -- -----------------------

    sold_quantity INT UNSIGNED NOT NULL DEFAULT 0,

    sold_weight_grams DECIMAL(18,3)
        NOT NULL DEFAULT 0.000,

    sales_value DECIMAL(18,2)
        NOT NULL DEFAULT 0.00,

    sale_making_total DECIMAL(18,2)
        NOT NULL DEFAULT 0.00,

    -- -----------------------
    -- Purchases / additions
    -- -----------------------

    purchased_quantity INT UNSIGNED NOT NULL DEFAULT 0,

    purchased_weight_grams DECIMAL(18,3)
        NOT NULL DEFAULT 0.000,

    purchase_value DECIMAL(18,2)
        NOT NULL DEFAULT 0.00,

    purchase_making_total DECIMAL(18,2)
        NOT NULL DEFAULT 0.00,

    -- -----------------------
    -- Stock ageing/reclassify
    -- -----------------------

    new_to_old_quantity INT UNSIGNED NOT NULL DEFAULT 0,

    new_to_old_weight_grams DECIMAL(18,3)
        NOT NULL DEFAULT 0.000,

    old_to_new_quantity INT UNSIGNED NOT NULL DEFAULT 0,

    old_to_new_weight_grams DECIMAL(18,3)
        NOT NULL DEFAULT 0.000,

    scrapped_quantity INT UNSIGNED NOT NULL DEFAULT 0,

    scrapped_weight_grams DECIMAL(18,3)
        NOT NULL DEFAULT 0.000,

    -- -----------------------
    -- Barcode/reconciliation
    -- -----------------------

    barcode_missing_count INT UNSIGNED NOT NULL DEFAULT 0,

    unidentified_sale_count INT UNSIGNED NOT NULL DEFAULT 0,

    unidentified_sale_weight_grams DECIMAL(18,3)
        NOT NULL DEFAULT 0.000,

    -- -----------------------
    -- Data confidence
    -- -----------------------

    data_quality_score DECIMAL(6,3) DEFAULT NULL,

    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,

    PRIMARY KEY (id),

    UNIQUE KEY uq_daily_segment (
        metric_date,
        segment_id
    ),

    KEY idx_daily_metric_date (
        metric_date
    ),

    KEY idx_daily_metric_segment (
        segment_id
    ),

    KEY idx_daily_metric_run (
        analytics_run_id
    ),

    CONSTRAINT fk_daily_metric_segment
        FOREIGN KEY (segment_id)
        REFERENCES analytics_segments(id),

    CONSTRAINT fk_daily_metric_run
        FOREIGN KEY (analytics_run_id)
        REFERENCES analytics_runs(id)
        ON DELETE SET NULL,

    CONSTRAINT chk_daily_availability
        CHECK (
            availability_percent >= 0
            AND availability_percent <= 100
        ),

    CONSTRAINT chk_daily_stockout_hours
        CHECK (
            stockout_hours >= 0
            AND stockout_hours <= 24
        ),

    CONSTRAINT chk_daily_quality
        CHECK (
            data_quality_score IS NULL
            OR (
                data_quality_score >= 0
                AND data_quality_score <= 100
            )
        )
) ENGINE=InnoDB
  DEFAULT CHARSET=utf8mb4
  COLLATE=utf8mb4_unicode_ci;


-- ============================================================
-- 4. DAILY SHOP-WIDE METRICS
--
-- Not tied to product segment.
--
-- Useful for management dashboard / historical charts.
-- ============================================================

CREATE TABLE IF NOT EXISTS analytics_daily_shop_metrics (
    metric_date DATE NOT NULL,

    analytics_run_id BIGINT UNSIGNED DEFAULT NULL,

    sell_invoice_count INT UNSIGNED NOT NULL DEFAULT 0,
    buy_invoice_count INT UNSIGNED NOT NULL DEFAULT 0,
    mixed_invoice_count INT UNSIGNED NOT NULL DEFAULT 0,

    sold_weight_grams DECIMAL(18,3)
        NOT NULL DEFAULT 0.000,

    bought_weight_grams DECIMAL(18,3)
        NOT NULL DEFAULT 0.000,

    sales_value DECIMAL(18,2)
        NOT NULL DEFAULT 0.00,

    purchases_value DECIMAL(18,2)
        NOT NULL DEFAULT 0.00,

    physical_cash_in DECIMAL(18,2)
        NOT NULL DEFAULT 0.00,

    physical_cash_out DECIMAL(18,2)
        NOT NULL DEFAULT 0.00,

    bank_in DECIMAL(18,2)
        NOT NULL DEFAULT 0.00,

    bank_out DECIMAL(18,2)
        NOT NULL DEFAULT 0.00,

    customer_balance_created DECIMAL(18,2)
        NOT NULL DEFAULT 0.00,

    customer_balance_collected DECIMAL(18,2)
        NOT NULL DEFAULT 0.00,

    dead_cash_amount DECIMAL(18,2)
        NOT NULL DEFAULT 0.00,

    data_quality_score DECIMAL(6,3) DEFAULT NULL,

    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,

    PRIMARY KEY (metric_date),

    KEY idx_daily_shop_run (
        analytics_run_id
    ),

    CONSTRAINT fk_daily_shop_run
        FOREIGN KEY (analytics_run_id)
        REFERENCES analytics_runs(id)
        ON DELETE SET NULL,

    CONSTRAINT chk_daily_shop_quality
        CHECK (
            data_quality_score IS NULL
            OR (
                data_quality_score >= 0
                AND data_quality_score <= 100
            )
        )
) ENGINE=InnoDB
  DEFAULT CHARSET=utf8mb4
  COLLATE=utf8mb4_unicode_ci;


-- ============================================================
-- 5. CALENDAR / SEASONAL EVENTS
-- ============================================================

CREATE TABLE IF NOT EXISTS calendar_event_types (
    id SMALLINT UNSIGNED NOT NULL AUTO_INCREMENT,

    code VARCHAR(60) NOT NULL,
    name VARCHAR(150) NOT NULL,

    description VARCHAR(500) DEFAULT NULL,

    is_active BOOLEAN NOT NULL DEFAULT TRUE,

    PRIMARY KEY (id),

    UNIQUE KEY uq_calendar_event_type_code (
        code
    )
) ENGINE=InnoDB
  DEFAULT CHARSET=utf8mb4
  COLLATE=utf8mb4_unicode_ci;


CREATE TABLE IF NOT EXISTS calendar_events (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,

    event_type_id SMALLINT UNSIGNED NOT NULL,

    event_name VARCHAR(200) NOT NULL,

    date_from DATE NOT NULL,
    date_to DATE NOT NULL,

    country_code CHAR(2)
        NOT NULL DEFAULT 'EG',

    notes VARCHAR(1000) DEFAULT NULL,

    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,

    PRIMARY KEY (id),

    KEY idx_calendar_event_dates (
        date_from,
        date_to
    ),

    KEY idx_calendar_event_type (
        event_type_id
    ),

    CONSTRAINT fk_calendar_event_type
        FOREIGN KEY (event_type_id)
        REFERENCES calendar_event_types(id),

    CONSTRAINT chk_calendar_event_dates
        CHECK (
            date_to >= date_from
        )
) ENGINE=InnoDB
  DEFAULT CHARSET=utf8mb4
  COLLATE=utf8mb4_unicode_ci;


-- ============================================================
-- 6. DATA QUALITY ISSUES
--
-- Particularly important for the 2+ year legacy DB.
--
-- We do NOT invent missing data.
-- ============================================================

CREATE TABLE IF NOT EXISTS analytics_data_quality_issues (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,

    analytics_run_id BIGINT UNSIGNED DEFAULT NULL,

    issue_type VARCHAR(80) NOT NULL,

    severity VARCHAR(20) NOT NULL,

    source_table VARCHAR(100) DEFAULT NULL,
    source_record_id VARCHAR(100) DEFAULT NULL,

    date_from DATE DEFAULT NULL,
    date_to DATE DEFAULT NULL,

    description VARCHAR(2000) NOT NULL,

    details_json JSON DEFAULT NULL,

    status VARCHAR(20)
        NOT NULL DEFAULT 'OPEN',

    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,

    resolved_at DATETIME DEFAULT NULL,

    PRIMARY KEY (id),

    KEY idx_quality_issue_run (
        analytics_run_id
    ),

    KEY idx_quality_issue_type (
        issue_type
    ),

    KEY idx_quality_issue_status (
        status
    ),

    CONSTRAINT fk_quality_issue_run
        FOREIGN KEY (analytics_run_id)
        REFERENCES analytics_runs(id)
        ON DELETE SET NULL,

    CONSTRAINT chk_quality_issue_severity
        CHECK (
            severity IN (
                'INFO',
                'WARNING',
                'HIGH',
                'CRITICAL'
            )
        ),

    CONSTRAINT chk_quality_issue_status
        CHECK (
            status IN (
                'OPEN',
                'REVIEWED',
                'RESOLVED',
                'ACCEPTED'
            )
        )
) ENGINE=InnoDB
  DEFAULT CHARSET=utf8mb4
  COLLATE=utf8mb4_unicode_ci;


-- ============================================================
-- 7. FORECAST RUNS
-- ============================================================

CREATE TABLE IF NOT EXISTS forecast_runs (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,

    model_code VARCHAR(100) NOT NULL,
    model_version VARCHAR(100) NOT NULL,

    trained_from DATE DEFAULT NULL,
    trained_to DATE DEFAULT NULL,

    forecast_created_at DATETIME
        NOT NULL DEFAULT CURRENT_TIMESTAMP,

    status VARCHAR(20)
        NOT NULL DEFAULT 'RUNNING',

    training_data_quality_score DECIMAL(6,3) DEFAULT NULL,

    parameters_json JSON DEFAULT NULL,
    diagnostics_json JSON DEFAULT NULL,

    notes VARCHAR(2000) DEFAULT NULL,

    PRIMARY KEY (id),

    KEY idx_forecast_runs_date (
        forecast_created_at
    ),

    KEY idx_forecast_runs_model (
        model_code,
        model_version
    ),

    CONSTRAINT chk_forecast_run_status
        CHECK (
            status IN (
                'RUNNING',
                'SUCCESS',
                'FAILED',
                'PARTIAL'
            )
        ),

    CONSTRAINT chk_forecast_quality
        CHECK (
            training_data_quality_score IS NULL
            OR (
                training_data_quality_score >= 0
                AND training_data_quality_score <= 100
            )
        )
) ENGINE=InnoDB
  DEFAULT CHARSET=utf8mb4
  COLLATE=utf8mb4_unicode_ci;


-- ============================================================
-- 8. FORECAST RESULTS
-- ============================================================

CREATE TABLE IF NOT EXISTS forecast_results (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,

    forecast_run_id BIGINT UNSIGNED NOT NULL,

    segment_id BIGINT UNSIGNED NOT NULL,

    metric_code VARCHAR(80) NOT NULL,

    period_start DATE NOT NULL,
    period_end DATE NOT NULL,

    forecast_value DECIMAL(18,4) NOT NULL,

    lower_bound DECIMAL(18,4) DEFAULT NULL,
    upper_bound DECIMAL(18,4) DEFAULT NULL,

    confidence_label VARCHAR(20) NOT NULL,

    confidence_score DECIMAL(6,3) DEFAULT NULL,

    explanation_json JSON DEFAULT NULL,

    PRIMARY KEY (id),

    KEY idx_forecast_segment (
        segment_id
    ),

    KEY idx_forecast_period (
        period_start,
        period_end
    ),

    KEY idx_forecast_metric (
        metric_code
    ),

    CONSTRAINT fk_forecast_result_run
        FOREIGN KEY (forecast_run_id)
        REFERENCES forecast_runs(id)
        ON DELETE CASCADE,

    CONSTRAINT fk_forecast_result_segment
        FOREIGN KEY (segment_id)
        REFERENCES analytics_segments(id),

    CONSTRAINT chk_forecast_dates
        CHECK (
            period_end >= period_start
        ),

    CONSTRAINT chk_forecast_confidence
        CHECK (
            confidence_label IN (
                'LOW',
                'MEDIUM',
                'HIGH'
            )
        ),

    CONSTRAINT chk_forecast_score
        CHECK (
            confidence_score IS NULL
            OR (
                confidence_score >= 0
                AND confidence_score <= 100
            )
        )
) ENGINE=InnoDB
  DEFAULT CHARSET=utf8mb4
  COLLATE=utf8mb4_unicode_ci;


-- ============================================================
-- 9. RECOMMENDATION TYPES
-- ============================================================

CREATE TABLE IF NOT EXISTS recommendation_types (
    id SMALLINT UNSIGNED NOT NULL AUTO_INCREMENT,

    code VARCHAR(80) NOT NULL,
    name VARCHAR(150) NOT NULL,

    description VARCHAR(500) DEFAULT NULL,

    is_active BOOLEAN NOT NULL DEFAULT TRUE,

    PRIMARY KEY (id),

    UNIQUE KEY uq_recommendation_type_code (
        code
    )
) ENGINE=InnoDB
  DEFAULT CHARSET=utf8mb4
  COLLATE=utf8mb4_unicode_ci;


-- ============================================================
-- 10. RECOMMENDATIONS
-- ============================================================

CREATE TABLE IF NOT EXISTS recommendations (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,

    recommendation_type_id SMALLINT UNSIGNED NOT NULL,

    segment_id BIGINT UNSIGNED DEFAULT NULL,

    forecast_run_id BIGINT UNSIGNED DEFAULT NULL,

    visibility_level VARCHAR(20)
        NOT NULL DEFAULT 'MANAGEMENT',

    recommendation_text VARCHAR(2000) NOT NULL,

    confidence_label VARCHAR(20) NOT NULL,

    confidence_score DECIMAL(6,3) DEFAULT NULL,

    reason_json JSON NOT NULL,

    status VARCHAR(20)
        NOT NULL DEFAULT 'OPEN',

    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,

    valid_until DATETIME DEFAULT NULL,

    acknowledged_at DATETIME DEFAULT NULL,
    acknowledged_by_user_id INT UNSIGNED DEFAULT NULL,

    acted_at DATETIME DEFAULT NULL,
    acted_by_user_id INT UNSIGNED DEFAULT NULL,

    PRIMARY KEY (id),

    KEY idx_recommendation_type (
        recommendation_type_id
    ),

    KEY idx_recommendation_segment (
        segment_id
    ),

    KEY idx_recommendation_status (
        status
    ),

    KEY idx_recommendation_date (
        created_at
    ),

    CONSTRAINT fk_recommendation_type
        FOREIGN KEY (recommendation_type_id)
        REFERENCES recommendation_types(id),

    CONSTRAINT fk_recommendation_segment
        FOREIGN KEY (segment_id)
        REFERENCES analytics_segments(id)
        ON DELETE SET NULL,

    CONSTRAINT fk_recommendation_forecast
        FOREIGN KEY (forecast_run_id)
        REFERENCES forecast_runs(id)
        ON DELETE SET NULL,

    CONSTRAINT fk_recommendation_ack_user
        FOREIGN KEY (acknowledged_by_user_id)
        REFERENCES users(id)
        ON DELETE SET NULL,

    CONSTRAINT fk_recommendation_action_user
        FOREIGN KEY (acted_by_user_id)
        REFERENCES users(id)
        ON DELETE SET NULL,

    CONSTRAINT chk_recommendation_visibility
        CHECK (
            visibility_level IN (
                'OPERATIONAL',
                'MANAGEMENT',
                'OWNER'
            )
        ),

    CONSTRAINT chk_recommendation_confidence
        CHECK (
            confidence_label IN (
                'LOW',
                'MEDIUM',
                'HIGH'
            )
        ),

    CONSTRAINT chk_recommendation_score
        CHECK (
            confidence_score IS NULL
            OR (
                confidence_score >= 0
                AND confidence_score <= 100
            )
        ),

    CONSTRAINT chk_recommendation_status
        CHECK (
            status IN (
                'OPEN',
                'ACKNOWLEDGED',
                'ACTED',
                'DISMISSED',
                'EXPIRED'
            )
        )
) ENGINE=InnoDB
  DEFAULT CHARSET=utf8mb4
  COLLATE=utf8mb4_unicode_ci;


-- ============================================================
-- 11. RECOMMENDATION OUTCOMES
--
-- Later we can compare advice vs actual result.
-- ============================================================

CREATE TABLE IF NOT EXISTS recommendation_outcomes (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,

    recommendation_id BIGINT UNSIGNED NOT NULL,

    evaluated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,

    outcome_status VARCHAR(30) NOT NULL,

    outcome_json JSON DEFAULT NULL,

    notes VARCHAR(2000) DEFAULT NULL,

    recorded_by_user_id INT UNSIGNED DEFAULT NULL,

    PRIMARY KEY (id),

    KEY idx_recommendation_outcome (
        recommendation_id
    ),

    CONSTRAINT fk_recommendation_outcome_recommendation
        FOREIGN KEY (recommendation_id)
        REFERENCES recommendations(id)
        ON DELETE CASCADE,

    CONSTRAINT fk_recommendation_outcome_user
        FOREIGN KEY (recorded_by_user_id)
        REFERENCES users(id)
        ON DELETE SET NULL,

    CONSTRAINT chk_recommendation_outcome_status
        CHECK (
            outcome_status IN (
                'POSITIVE',
                'NEUTRAL',
                'NEGATIVE',
                'INSUFFICIENT_DATA'
            )
        )
) ENGINE=InnoDB
  DEFAULT CHARSET=utf8mb4
  COLLATE=utf8mb4_unicode_ci;


-- ============================================================
-- INITIAL CALENDAR EVENT TYPES
--
-- These do NOT assume they affect your shop.
-- They merely allow analytics to test whether an effect exists.
-- ============================================================

INSERT INTO calendar_event_types (
    code,
    name,
    description
)
VALUES

    (
        'RAMADAN',
        'Ramadan',
        'Ramadan period'
    ),

    (
        'EID_FITR',
        'Eid al-Fitr',
        'Eid al-Fitr period'
    ),

    (
        'EID_ADHA',
        'Eid al-Adha',
        'Eid al-Adha period'
    ),

    (
        'VALENTINES',
        'Valentine''s Day',
        'Valentine sales period'
    ),

    (
        'MOTHERS_DAY',
        'Mother''s Day',
        'Mother''s Day sales period'
    ),

    (
        'SUMMER',
        'Summer Season',
        'Configured summer sales period'
    ),

    (
        'WEDDING_SEASON',
        'Wedding Season',
        'Configured wedding season'
    ),

    (
        'CHRISTMAS_NEW_YEAR',
        'Christmas / New Year',
        'Christmas and New Year period'
    ),

    (
        'SCHOOL_UNIVERSITY',
        'School / University Period',
        'Configured education-related period'
    ),

    (
        'CUSTOM',
        'Custom Event',
        'Shop-specific or manually configured event'
    )

ON DUPLICATE KEY UPDATE
    name = VALUES(name),
    description = VALUES(description);


-- ============================================================
-- INITIAL RECOMMENDATION TYPES
-- ============================================================

INSERT INTO recommendation_types (
    code,
    name,
    description
)
VALUES

    (
        'BUY_MORE',
        'Buy More',
        'Demand/stock evidence indicates replenishment opportunity'
    ),

    (
        'REPLENISH_SOON',
        'Replenish Soon',
        'Current stock may not cover expected near-term demand'
    ),

    (
        'STOP_BUYING',
        'Stop Buying',
        'Existing stock coverage is excessive relative to demand'
    ),

    (
        'SLOW_MOVING',
        'Slow Moving',
        'Inventory is moving significantly slower than expected'
    ),

    (
        'STOCKOUT_RISK',
        'Stockout Risk',
        'Inventory is likely to become unavailable'
    ),

    (
        'HIGH_PURCHASE_MAKING',
        'High Purchase Making',
        'Purchase making is unusually high relative to historical/category range'
    ),

    (
        'LOW_SALE_MAKING',
        'Low Sale Making',
        'Sale making is unusually low relative to historical/category range'
    ),

    (
        'OLD_STOCK_RISK',
        'Old Stock Risk',
        'Segment shows elevated conversion from new stock to old stock'
    ),

    (
        'BARCODE_PROCESS_ISSUE',
        'Barcode Process Issue',
        'Repeated missing barcode or unidentified-sale behavior detected'
    )

ON DUPLICATE KEY UPDATE
    name = VALUES(name),
    description = VALUES(description);


-- ============================================================
-- PERMISSIONS
-- ============================================================

INSERT INTO permissions (
    code,
    name,
    description
)
VALUES

    (
        'ANALYTICS_VIEW',
        'View Analytics',
        'Allows access to analytical reports and graphs'
    ),

    (
        'ANALYTICS_REBUILD',
        'Rebuild Analytics',
        'Allows analytical derived data to be regenerated'
    ),

    (
        'FORECAST_VIEW',
        'View Forecasts',
        'Allows viewing demand and inventory forecasts'
    ),

    (
        'RECOMMENDATION_VIEW',
        'View Recommendations',
        'Allows viewing business recommendations'
    ),

    (
        'RECOMMENDATION_MANAGE',
        'Manage Recommendations',
        'Allows acknowledging and recording recommendation actions/outcomes'
    )

ON DUPLICATE KEY UPDATE
    name = VALUES(name),
    description = VALUES(description);


-- Owner receives new permissions.

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
-- GLOBAL SHOP SEGMENT
--
-- Useful when analysis is not filtered by item/category/etc.
-- ============================================================

INSERT INTO analytics_segments (
    segment_key,
    name
)
VALUES (
    'ALL',
    'All Inventory'
)
ON DUPLICATE KEY UPDATE
    name = VALUES(name);


-- ============================================================
-- REGISTER MIGRATION
-- ============================================================

INSERT INTO schema_migrations (
    version,
    name
)
VALUES (
    11,
    'Analytics, forecasting, seasonality and recommendation foundation'
)
ON DUPLICATE KEY UPDATE
    name = VALUES(name);