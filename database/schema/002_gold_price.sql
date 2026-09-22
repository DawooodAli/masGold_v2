USE masgold_v2;

-- ============================================================
-- masGold v2
-- Migration 002: Gold Price Engine
--
-- Keeps:
--   1. Price sources
--   2. Every historical quote
--   3. Activation history
--   4. Current active price
--   5. Engine configuration
--
-- IMPORTANT:
-- Historical quotes are intended to be immutable.
-- ============================================================


-- ------------------------------------------------------------
-- 1. Gold price sources
-- ------------------------------------------------------------

CREATE TABLE IF NOT EXISTS gold_price_sources (
    id SMALLINT UNSIGNED NOT NULL AUTO_INCREMENT,

    code VARCHAR(50) NOT NULL,
    name VARCHAR(150) NOT NULL,

    source_type VARCHAR(30) NOT NULL,

    -- Lower number = higher priority
    priority SMALLINT UNSIGNED NOT NULL DEFAULT 100,

    is_enabled BOOLEAN NOT NULL DEFAULT TRUE,

    notes VARCHAR(500) DEFAULT NULL,

    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL
        DEFAULT CURRENT_TIMESTAMP
        ON UPDATE CURRENT_TIMESTAMP,

    PRIMARY KEY (id),

    UNIQUE KEY uq_gold_price_sources_code (code),

    CONSTRAINT chk_gold_price_source_type
        CHECK (
            source_type IN (
                'LOCAL_MARKET',
                'GLOBAL_REFERENCE',
                'MANUAL',
                'LEGACY'
            )
        )
) ENGINE=InnoDB
  DEFAULT CHARSET=utf8mb4
  COLLATE=utf8mb4_unicode_ci;


-- ------------------------------------------------------------
-- 2. Gold price quotes
--
-- Every reading/entry is kept permanently.
--
-- One quote_group_id groups 18K / 21K / 24K prices
-- received or entered at the same moment.
-- ------------------------------------------------------------

CREATE TABLE IF NOT EXISTS gold_price_quotes (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,

    quote_group_id CHAR(36) NOT NULL,

    source_id SMALLINT UNSIGNED NOT NULL,
    karat_id TINYINT UNSIGNED NOT NULL,

    -- Price shop pays when BUYING gold
    shop_buy_price_per_gram DECIMAL(14,4) DEFAULT NULL,

    -- Price shop charges when SELLING gold
    shop_sell_price_per_gram DECIMAL(14,4) DEFAULT NULL,

    -- Optional global/reference value for comparison
    reference_price_per_gram DECIMAL(14,4) DEFAULT NULL,

    quote_method VARCHAR(20) NOT NULL DEFAULT 'DIRECT',

    provider_timestamp DATETIME DEFAULT NULL,

    fetched_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,

    validation_status VARCHAR(20)
        NOT NULL DEFAULT 'ACCEPTED',

    deviation_percent DECIMAL(10,4) DEFAULT NULL,

    validation_message VARCHAR(500) DEFAULT NULL,

    entered_by_user_id INT UNSIGNED DEFAULT NULL,

    notes VARCHAR(500) DEFAULT NULL,

    PRIMARY KEY (id),

    KEY idx_gold_quotes_group (quote_group_id),
    KEY idx_gold_quotes_source (source_id),
    KEY idx_gold_quotes_karat (karat_id),
    KEY idx_gold_quotes_fetched (fetched_at),

    CONSTRAINT fk_gold_quotes_source
        FOREIGN KEY (source_id)
        REFERENCES gold_price_sources(id),

    CONSTRAINT fk_gold_quotes_karat
        FOREIGN KEY (karat_id)
        REFERENCES karats(id),

    CONSTRAINT fk_gold_quotes_user
        FOREIGN KEY (entered_by_user_id)
        REFERENCES users(id)
        ON DELETE SET NULL,

    CONSTRAINT chk_gold_quote_method
        CHECK (
            quote_method IN (
                'DIRECT',
                'DERIVED',
                'MANUAL',
                'LEGACY'
            )
        ),

    CONSTRAINT chk_gold_quote_validation
        CHECK (
            validation_status IN (
                'PENDING',
                'ACCEPTED',
                'REJECTED'
            )
        ),

    CONSTRAINT chk_gold_quote_buy_positive
        CHECK (
            shop_buy_price_per_gram IS NULL
            OR shop_buy_price_per_gram > 0
        ),

    CONSTRAINT chk_gold_quote_sell_positive
        CHECK (
            shop_sell_price_per_gram IS NULL
            OR shop_sell_price_per_gram > 0
        ),

    CONSTRAINT chk_gold_quote_reference_positive
        CHECK (
            reference_price_per_gram IS NULL
            OR reference_price_per_gram > 0
        )
) ENGINE=InnoDB
  DEFAULT CHARSET=utf8mb4
  COLLATE=utf8mb4_unicode_ci;


-- ------------------------------------------------------------
-- 3. Gold price activation history
--
-- A quote existing does not necessarily mean masGold used it.
-- This records which quote actually became the shop price.
-- ------------------------------------------------------------

CREATE TABLE IF NOT EXISTS gold_price_activations (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,

    karat_id TINYINT UNSIGNED NOT NULL,
    quote_id BIGINT UNSIGNED NOT NULL,

    activation_type VARCHAR(20) NOT NULL,

    activated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,

    activated_by_user_id INT UNSIGNED DEFAULT NULL,

    reason VARCHAR(500) DEFAULT NULL,

    PRIMARY KEY (id),

    KEY idx_gold_activation_karat (karat_id),
    KEY idx_gold_activation_quote (quote_id),
    KEY idx_gold_activation_date (activated_at),

    CONSTRAINT fk_gold_activation_karat
        FOREIGN KEY (karat_id)
        REFERENCES karats(id),

    CONSTRAINT fk_gold_activation_quote
        FOREIGN KEY (quote_id)
        REFERENCES gold_price_quotes(id),

    CONSTRAINT fk_gold_activation_user
        FOREIGN KEY (activated_by_user_id)
        REFERENCES users(id)
        ON DELETE SET NULL,

    CONSTRAINT chk_gold_activation_type
        CHECK (
            activation_type IN (
                'AUTO',
                'MANUAL',
                'FALLBACK',
                'RESTORE',
                'LEGACY'
            )
        )
) ENGINE=InnoDB
  DEFAULT CHARSET=utf8mb4
  COLLATE=utf8mb4_unicode_ci;


-- ------------------------------------------------------------
-- 4. Current active gold prices
--
-- Only ONE current active quote for each karat.
-- Historical activations remain above.
-- ------------------------------------------------------------

CREATE TABLE IF NOT EXISTS active_gold_prices (
    karat_id TINYINT UNSIGNED NOT NULL,

    quote_id BIGINT UNSIGNED NOT NULL,
    activation_id BIGINT UNSIGNED NOT NULL,

    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
        ON UPDATE CURRENT_TIMESTAMP,

    PRIMARY KEY (karat_id),

    UNIQUE KEY uq_active_gold_activation (activation_id),

    CONSTRAINT fk_active_gold_karat
        FOREIGN KEY (karat_id)
        REFERENCES karats(id),

    CONSTRAINT fk_active_gold_quote
        FOREIGN KEY (quote_id)
        REFERENCES gold_price_quotes(id),

    CONSTRAINT fk_active_gold_activation
        FOREIGN KEY (activation_id)
        REFERENCES gold_price_activations(id)
) ENGINE=InnoDB
  DEFAULT CHARSET=utf8mb4
  COLLATE=utf8mb4_unicode_ci;


-- ------------------------------------------------------------
-- 5. Gold price engine settings
--
-- Single-row configuration.
-- We start MANUAL until the automatic provider is integrated.
-- ------------------------------------------------------------

CREATE TABLE IF NOT EXISTS gold_price_engine_settings (
    id TINYINT UNSIGNED NOT NULL,

    mode VARCHAR(20) NOT NULL DEFAULT 'MANUAL',

    primary_source_id SMALLINT UNSIGNED DEFAULT NULL,
    fallback_source_id SMALLINT UNSIGNED DEFAULT NULL,
    reference_source_id SMALLINT UNSIGNED DEFAULT NULL,

    refresh_seconds INT UNSIGNED NOT NULL DEFAULT 60,

    stale_after_seconds INT UNSIGNED NOT NULL DEFAULT 300,

    max_deviation_percent DECIMAL(8,4)
        NOT NULL DEFAULT 5.0000,

    updated_by_user_id INT UNSIGNED DEFAULT NULL,

    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
        ON UPDATE CURRENT_TIMESTAMP,

    PRIMARY KEY (id),

    CONSTRAINT fk_price_engine_primary
        FOREIGN KEY (primary_source_id)
        REFERENCES gold_price_sources(id)
        ON DELETE SET NULL,

    CONSTRAINT fk_price_engine_fallback
        FOREIGN KEY (fallback_source_id)
        REFERENCES gold_price_sources(id)
        ON DELETE SET NULL,

    CONSTRAINT fk_price_engine_reference
        FOREIGN KEY (reference_source_id)
        REFERENCES gold_price_sources(id)
        ON DELETE SET NULL,

    CONSTRAINT fk_price_engine_user
        FOREIGN KEY (updated_by_user_id)
        REFERENCES users(id)
        ON DELETE SET NULL,

    CONSTRAINT chk_price_engine_single_row
        CHECK (id = 1),

    CONSTRAINT chk_price_engine_mode
        CHECK (
            mode IN (
                'AUTO',
                'MANUAL'
            )
        ),

    CONSTRAINT chk_price_refresh
        CHECK (refresh_seconds >= 10),

    CONSTRAINT chk_price_stale
        CHECK (stale_after_seconds >= refresh_seconds),

    CONSTRAINT chk_price_deviation
        CHECK (
            max_deviation_percent > 0
            AND max_deviation_percent <= 100
        )
) ENGINE=InnoDB
  DEFAULT CHARSET=utf8mb4
  COLLATE=utf8mb4_unicode_ci;


-- ============================================================
-- INITIAL DATA
-- ============================================================


-- ------------------------------------------------------------
-- Manual source
-- ------------------------------------------------------------

INSERT INTO gold_price_sources (
    code,
    name,
    source_type,
    priority,
    is_enabled,
    notes
)
VALUES (
    'MANUAL',
    'Manual Shop Price',
    'MANUAL',
    1,
    TRUE,
    'Manual price entered by an authorized masGold user'
)
ON DUPLICATE KEY UPDATE
    name = VALUES(name),
    source_type = VALUES(source_type),
    priority = VALUES(priority),
    is_enabled = VALUES(is_enabled);


-- ------------------------------------------------------------
-- Legacy production source
--
-- This will be used later during historical migration.
-- It is NOT a live automatic source.
-- ------------------------------------------------------------

INSERT INTO gold_price_sources (
    code,
    name,
    source_type,
    priority,
    is_enabled,
    notes
)
VALUES (
    'LEGACY_PRODUCTION',
    'Legacy Production Database',
    'LEGACY',
    999,
    FALSE,
    'Historical prices migrated from the original production database'
)
ON DUPLICATE KEY UPDATE
    name = VALUES(name),
    source_type = VALUES(source_type),
    priority = VALUES(priority),
    is_enabled = VALUES(is_enabled);


-- ------------------------------------------------------------
-- Engine starts in MANUAL mode.
--
-- We will switch to AUTO only after integrating and testing
-- the Egyptian/local provider.
-- ------------------------------------------------------------

INSERT INTO gold_price_engine_settings (
    id,
    mode,
    refresh_seconds,
    stale_after_seconds,
    max_deviation_percent
)
VALUES (
    1,
    'MANUAL',
    60,
    300,
    5.0000
)
ON DUPLICATE KEY UPDATE
    mode = VALUES(mode);


-- ------------------------------------------------------------
-- Register migration
-- ------------------------------------------------------------

INSERT INTO schema_migrations (
    version,
    name
)
VALUES (
    2,
    'Gold price engine and immutable price history'
)
ON DUPLICATE KEY UPDATE
    name = VALUES(name);