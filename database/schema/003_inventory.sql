USE masgold_v2;

-- ============================================================
-- masGold v2
-- Migration 003: Inventory Core
-- ============================================================


-- ------------------------------------------------------------
-- 1. Inventory movement types
-- ------------------------------------------------------------

CREATE TABLE IF NOT EXISTS inventory_movement_types (
    id SMALLINT UNSIGNED NOT NULL AUTO_INCREMENT,

    code VARCHAR(50) NOT NULL,
    name VARCHAR(150) NOT NULL,
    description VARCHAR(500) DEFAULT NULL,

    is_active BOOLEAN NOT NULL DEFAULT TRUE,

    PRIMARY KEY (id),
    UNIQUE KEY uq_inventory_movement_types_code (code)
) ENGINE=InnoDB
  DEFAULT CHARSET=utf8mb4
  COLLATE=utf8mb4_unicode_ci;


-- ------------------------------------------------------------
-- 2. Physical inventory items
--
-- One row = one real physical jewelry piece.
--
-- The primary key is the permanent item identity.
-- Barcode is NOT the item identity.
-- ------------------------------------------------------------

CREATE TABLE IF NOT EXISTS inventory_items (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,

    item_type_id SMALLINT UNSIGNED NOT NULL,
    karat_id TINYINT UNSIGNED NOT NULL,

    category_id SMALLINT UNSIGNED DEFAULT NULL,
    factory_id SMALLINT UNSIGNED DEFAULT NULL,

    weight_grams DECIMAL(12,3) NOT NULL,

    size VARCHAR(50) DEFAULT NULL,

    -- NEW_STOCK and OLD_STOCK are both sellable inventory.
    stock_classification VARCHAR(20)
        NOT NULL DEFAULT 'NEW_STOCK',

    -- Lifecycle of the physical item.
    inventory_status VARCHAR(20)
        NOT NULL DEFAULT 'IN_STOCK',

    -- How this physical piece entered inventory.
    acquisition_source VARCHAR(30)
        NOT NULL DEFAULT 'MARKET_PURCHASE',

    acquired_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,

    -- Frozen acquisition economics.
    acquisition_gold_price_per_gram DECIMAL(14,4) DEFAULT NULL,
    acquisition_making_per_gram DECIMAL(12,2) DEFAULT NULL,
    acquisition_making_total DECIMAL(14,2) DEFAULT NULL,
    acquisition_total_value DECIMAL(16,2) DEFAULT NULL,

    -- Optional link to the exact gold-price quote used.
    acquisition_quote_id BIGINT UNSIGNED DEFAULT NULL,

    notes VARCHAR(1000) DEFAULT NULL,

    created_by_user_id INT UNSIGNED DEFAULT NULL,
    updated_by_user_id INT UNSIGNED DEFAULT NULL,

    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL
        DEFAULT CURRENT_TIMESTAMP
        ON UPDATE CURRENT_TIMESTAMP,

    PRIMARY KEY (id),

    KEY idx_inventory_status (
        inventory_status
    ),

    KEY idx_inventory_classification (
        stock_classification
    ),

    KEY idx_inventory_karat_weight (
        karat_id,
        weight_grams
    ),

    KEY idx_inventory_item_type (
        item_type_id
    ),

    KEY idx_inventory_category (
        category_id
    ),

    KEY idx_inventory_factory (
        factory_id
    ),

    KEY idx_inventory_recovery_search (
        inventory_status,
        karat_id,
        item_type_id,
        weight_grams
    ),

    CONSTRAINT fk_inventory_item_type
        FOREIGN KEY (item_type_id)
        REFERENCES item_types(id),

    CONSTRAINT fk_inventory_karat
        FOREIGN KEY (karat_id)
        REFERENCES karats(id),

    CONSTRAINT fk_inventory_category
        FOREIGN KEY (category_id)
        REFERENCES item_categories(id)
        ON DELETE SET NULL,

    CONSTRAINT fk_inventory_factory
        FOREIGN KEY (factory_id)
        REFERENCES factories(id)
        ON DELETE SET NULL,

    CONSTRAINT fk_inventory_acquisition_quote
        FOREIGN KEY (acquisition_quote_id)
        REFERENCES gold_price_quotes(id)
        ON DELETE SET NULL,

    CONSTRAINT fk_inventory_created_user
        FOREIGN KEY (created_by_user_id)
        REFERENCES users(id)
        ON DELETE SET NULL,

    CONSTRAINT fk_inventory_updated_user
        FOREIGN KEY (updated_by_user_id)
        REFERENCES users(id)
        ON DELETE SET NULL,

    CONSTRAINT chk_inventory_weight
        CHECK (weight_grams > 0),

    CONSTRAINT chk_inventory_classification
        CHECK (
            stock_classification IN (
                'NEW_STOCK',
                'OLD_STOCK'
            )
        ),

    CONSTRAINT chk_inventory_status
        CHECK (
            inventory_status IN (
                'IN_STOCK',
                'SOLD',
                'SCRAPPED',
                'REMOVED'
            )
        ),

    CONSTRAINT chk_inventory_source
        CHECK (
            acquisition_source IN (
                'MARKET_PURCHASE',
                'OLD_GOLD_MANUFACTURE',
                'LEGACY_MIGRATION',
                'MANUAL_ENTRY'
            )
        ),

    CONSTRAINT chk_inventory_acquisition_gold_price
        CHECK (
            acquisition_gold_price_per_gram IS NULL
            OR acquisition_gold_price_per_gram > 0
        ),

    CONSTRAINT chk_inventory_acquisition_making
        CHECK (
            acquisition_making_per_gram IS NULL
            OR acquisition_making_per_gram >= 0
        ),

    CONSTRAINT chk_inventory_acquisition_making_total
        CHECK (
            acquisition_making_total IS NULL
            OR acquisition_making_total >= 0
        )
) ENGINE=InnoDB
  DEFAULT CHARSET=utf8mb4
  COLLATE=utf8mb4_unicode_ci;


-- ------------------------------------------------------------
-- 3. Barcode assignments
--
-- An item may have different barcodes during its lifetime,
-- but only ONE can be ACTIVE at a time.
--
-- A barcode value can never be reused for another item.
-- ------------------------------------------------------------

CREATE TABLE IF NOT EXISTS inventory_barcodes (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,

    inventory_item_id BIGINT UNSIGNED NOT NULL,

    barcode_value VARCHAR(100) NOT NULL,

    barcode_status VARCHAR(20)
        NOT NULL DEFAULT 'ACTIVE',

    assigned_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    retired_at DATETIME DEFAULT NULL,

    assigned_by_user_id INT UNSIGNED DEFAULT NULL,

    reason VARCHAR(500) DEFAULT NULL,

    -- MySQL allows multiple NULL values in a UNIQUE index.
    -- This generated column lets us enforce:
    -- only one ACTIVE barcode per item.
    active_item_id BIGINT UNSIGNED
        GENERATED ALWAYS AS (
            CASE
                WHEN barcode_status = 'ACTIVE'
                THEN inventory_item_id
                ELSE NULL
            END
        ) STORED,

    PRIMARY KEY (id),

    UNIQUE KEY uq_inventory_barcode_value (
        barcode_value
    ),

    UNIQUE KEY uq_inventory_one_active_barcode (
        active_item_id
    ),

    KEY idx_inventory_barcode_item (
        inventory_item_id
    ),

    CONSTRAINT fk_inventory_barcode_item
        FOREIGN KEY (inventory_item_id)
        REFERENCES inventory_items(id),

    CONSTRAINT fk_inventory_barcode_user
        FOREIGN KEY (assigned_by_user_id)
        REFERENCES users(id)
        ON DELETE SET NULL,

    CONSTRAINT chk_inventory_barcode_status
        CHECK (
            barcode_status IN (
                'ACTIVE',
                'REPLACED',
                'RETIRED'
            )
        )
) ENGINE=InnoDB
  DEFAULT CHARSET=utf8mb4
  COLLATE=utf8mb4_unicode_ci;


-- ------------------------------------------------------------
-- 4. Barcode events
--
-- Used for:
-- barcode missing reports
-- recovery
-- reprints
-- replacement history
--
-- Losing the physical label does NOT delete/change the item.
-- ------------------------------------------------------------

CREATE TABLE IF NOT EXISTS inventory_barcode_events (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,

    inventory_item_id BIGINT UNSIGNED NOT NULL,
    barcode_id BIGINT UNSIGNED DEFAULT NULL,

    event_type VARCHAR(30) NOT NULL,

    occurred_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,

    performed_by_user_id INT UNSIGNED DEFAULT NULL,

    notes VARCHAR(500) DEFAULT NULL,

    details_json JSON DEFAULT NULL,

    PRIMARY KEY (id),

    KEY idx_barcode_events_item (
        inventory_item_id
    ),

    KEY idx_barcode_events_date (
        occurred_at
    ),

    KEY idx_barcode_events_type (
        event_type
    ),

    CONSTRAINT fk_barcode_events_item
        FOREIGN KEY (inventory_item_id)
        REFERENCES inventory_items(id),

    CONSTRAINT fk_barcode_events_barcode
        FOREIGN KEY (barcode_id)
        REFERENCES inventory_barcodes(id)
        ON DELETE SET NULL,

    CONSTRAINT fk_barcode_events_user
        FOREIGN KEY (performed_by_user_id)
        REFERENCES users(id)
        ON DELETE SET NULL,

    CONSTRAINT chk_barcode_event_type
        CHECK (
            event_type IN (
                'ASSIGNED',
                'MISSING_REPORTED',
                'RECOVERED',
                'REPRINTED',
                'REPLACED',
                'RETIRED'
            )
        )
) ENGINE=InnoDB
  DEFAULT CHARSET=utf8mb4
  COLLATE=utf8mb4_unicode_ci;


-- ------------------------------------------------------------
-- 5. Inventory movement history
--
-- This is the permanent history of the physical item.
--
-- Examples:
-- NEW_STOCK -> OLD_STOCK
-- IN_STOCK  -> SOLD
-- weight correction
-- migrated historical item
-- ------------------------------------------------------------

CREATE TABLE IF NOT EXISTS inventory_movements (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,

    inventory_item_id BIGINT UNSIGNED NOT NULL,
    movement_type_id SMALLINT UNSIGNED NOT NULL,

    from_stock_classification VARCHAR(20) DEFAULT NULL,
    to_stock_classification VARCHAR(20) DEFAULT NULL,

    from_inventory_status VARCHAR(20) DEFAULT NULL,
    to_inventory_status VARCHAR(20) DEFAULT NULL,

    from_weight_grams DECIMAL(12,3) DEFAULT NULL,
    to_weight_grams DECIMAL(12,3) DEFAULT NULL,

    -- Generic document reference.
    -- Billing migration will later add direct invoice relations.
    reference_type VARCHAR(50) DEFAULT NULL,
    reference_id BIGINT UNSIGNED DEFAULT NULL,
    reference_code VARCHAR(100) DEFAULT NULL,

    reason VARCHAR(500) DEFAULT NULL,
    notes VARCHAR(1000) DEFAULT NULL,

    occurred_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,

    performed_by_user_id INT UNSIGNED DEFAULT NULL,

    PRIMARY KEY (id),

    KEY idx_inventory_movements_item (
        inventory_item_id
    ),

    KEY idx_inventory_movements_type (
        movement_type_id
    ),

    KEY idx_inventory_movements_date (
        occurred_at
    ),

    KEY idx_inventory_movements_reference (
        reference_type,
        reference_id
    ),

    CONSTRAINT fk_inventory_movement_item
        FOREIGN KEY (inventory_item_id)
        REFERENCES inventory_items(id),

    CONSTRAINT fk_inventory_movement_type
        FOREIGN KEY (movement_type_id)
        REFERENCES inventory_movement_types(id),

    CONSTRAINT fk_inventory_movement_user
        FOREIGN KEY (performed_by_user_id)
        REFERENCES users(id)
        ON DELETE SET NULL,

    CONSTRAINT chk_inventory_move_from_class
        CHECK (
            from_stock_classification IS NULL
            OR from_stock_classification IN (
                'NEW_STOCK',
                'OLD_STOCK'
            )
        ),

    CONSTRAINT chk_inventory_move_to_class
        CHECK (
            to_stock_classification IS NULL
            OR to_stock_classification IN (
                'NEW_STOCK',
                'OLD_STOCK'
            )
        ),

    CONSTRAINT chk_inventory_move_from_status
        CHECK (
            from_inventory_status IS NULL
            OR from_inventory_status IN (
                'IN_STOCK',
                'SOLD',
                'SCRAPPED',
                'REMOVED'
            )
        ),

    CONSTRAINT chk_inventory_move_to_status
        CHECK (
            to_inventory_status IS NULL
            OR to_inventory_status IN (
                'IN_STOCK',
                'SOLD',
                'SCRAPPED',
                'REMOVED'
            )
        )
) ENGINE=InnoDB
  DEFAULT CHARSET=utf8mb4
  COLLATE=utf8mb4_unicode_ci;


-- ------------------------------------------------------------
-- 6. Inventory reconciliation cases
--
-- Emergency path when the salesperson genuinely cannot
-- identify the real inventory item.
--
-- This prevents "sell without barcode" from silently
-- corrupting stock.
-- ------------------------------------------------------------

CREATE TABLE IF NOT EXISTS inventory_reconciliation_cases (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,

    case_type VARCHAR(30) NOT NULL,

    case_status VARCHAR(20)
        NOT NULL DEFAULT 'OPEN',

    observed_karat_id TINYINT UNSIGNED DEFAULT NULL,
    observed_item_type_id SMALLINT UNSIGNED DEFAULT NULL,
    observed_category_id SMALLINT UNSIGNED DEFAULT NULL,
    observed_factory_id SMALLINT UNSIGNED DEFAULT NULL,

    observed_weight_grams DECIMAL(12,3) DEFAULT NULL,

    -- Filled when the real item is eventually identified.
    matched_inventory_item_id BIGINT UNSIGNED DEFAULT NULL,

    -- Temporary reference until Billing tables exist.
    reference_number VARCHAR(100) DEFAULT NULL,

    reason VARCHAR(500) NOT NULL,
    notes VARCHAR(1000) DEFAULT NULL,

    created_by_user_id INT UNSIGNED DEFAULT NULL,
    resolved_by_user_id INT UNSIGNED DEFAULT NULL,

    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    resolved_at DATETIME DEFAULT NULL,

    PRIMARY KEY (id),

    KEY idx_reconciliation_status (
        case_status
    ),

    KEY idx_reconciliation_date (
        created_at
    ),

    KEY idx_reconciliation_karat_weight (
        observed_karat_id,
        observed_weight_grams
    ),

    CONSTRAINT fk_reconciliation_karat
        FOREIGN KEY (observed_karat_id)
        REFERENCES karats(id)
        ON DELETE SET NULL,

    CONSTRAINT fk_reconciliation_item_type
        FOREIGN KEY (observed_item_type_id)
        REFERENCES item_types(id)
        ON DELETE SET NULL,

    CONSTRAINT fk_reconciliation_category
        FOREIGN KEY (observed_category_id)
        REFERENCES item_categories(id)
        ON DELETE SET NULL,

    CONSTRAINT fk_reconciliation_factory
        FOREIGN KEY (observed_factory_id)
        REFERENCES factories(id)
        ON DELETE SET NULL,

    CONSTRAINT fk_reconciliation_matched_item
        FOREIGN KEY (matched_inventory_item_id)
        REFERENCES inventory_items(id)
        ON DELETE SET NULL,

    CONSTRAINT fk_reconciliation_created_user
        FOREIGN KEY (created_by_user_id)
        REFERENCES users(id)
        ON DELETE SET NULL,

    CONSTRAINT fk_reconciliation_resolved_user
        FOREIGN KEY (resolved_by_user_id)
        REFERENCES users(id)
        ON DELETE SET NULL,

    CONSTRAINT chk_reconciliation_type
        CHECK (
            case_type IN (
                'UNIDENTIFIED_SALE',
                'PHYSICAL_SHORTAGE',
                'PHYSICAL_SURPLUS',
                'OTHER'
            )
        ),

    CONSTRAINT chk_reconciliation_status
        CHECK (
            case_status IN (
                'OPEN',
                'MATCHED',
                'CLOSED',
                'CANCELLED'
            )
        ),

    CONSTRAINT chk_reconciliation_weight
        CHECK (
            observed_weight_grams IS NULL
            OR observed_weight_grams > 0
        )
) ENGINE=InnoDB
  DEFAULT CHARSET=utf8mb4
  COLLATE=utf8mb4_unicode_ci;


-- ============================================================
-- INITIAL MOVEMENT TYPES
-- ============================================================

INSERT INTO inventory_movement_types (
    code,
    name,
    description
)
VALUES

    (
        'ITEM_CREATED',
        'Item Created',
        'A new physical inventory item was created'
    ),

    (
        'ITEM_EDITED',
        'Item Edited',
        'Inventory item information was corrected or edited'
    ),

    (
        'NEW_TO_OLD',
        'New Stock to Old Stock',
        'Physical item was reclassified from new stock to old stock'
    ),

    (
        'OLD_TO_NEW',
        'Old Stock to New Stock',
        'Physical item was restored from old stock to new stock'
    ),

    (
        'SOLD',
        'Sold',
        'Physical inventory item was sold'
    ),

    (
        'SALE_REVERSED',
        'Sale Reversed',
        'A previous sale was reversed and the item returned to inventory'
    ),

    (
        'SCRAPPED',
        'Converted to Old Gold',
        'Physical item left sellable inventory and was transferred to old gold/scrap'
    ),

    (
        'RESTORED',
        'Restored',
        'Previously removed item was restored to inventory'
    ),

    (
        'MANUAL_ADJUSTMENT',
        'Manual Adjustment',
        'Authorized physical inventory correction'
    ),

    (
        'MIGRATED',
        'Legacy Migration',
        'Historical physical item imported from the production database'
    )

ON DUPLICATE KEY UPDATE
    name = VALUES(name),
    description = VALUES(description);


-- ============================================================
-- INVENTORY-SPECIFIC PERMISSIONS
-- ============================================================

INSERT INTO permissions (
    code,
    name,
    description
)
VALUES

    (
        'INVENTORY_CONVERT_OLD',
        'Convert Stock to Old Stock',
        'Allows NEW_STOCK to OLD_STOCK reclassification and reversal'
    ),

    (
        'INVENTORY_RECONCILE',
        'Reconcile Inventory',
        'Allows resolution of unidentified sales and physical stock differences'
    ),

    (
        'BARCODE_RECOVER',
        'Recover Missing Barcode',
        'Allows searching and identifying an inventory item whose physical barcode is missing'
    ),

    (
        'BARCODE_REPRINT',
        'Reprint Barcode',
        'Allows printing an existing inventory barcode again'
    )

ON DUPLICATE KEY UPDATE
    name = VALUES(name),
    description = VALUES(description);


-- Owner automatically receives all new permissions.

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
    3,
    'Inventory identity, barcodes, movements and reconciliation'
)
ON DUPLICATE KEY UPDATE
    name = VALUES(name);