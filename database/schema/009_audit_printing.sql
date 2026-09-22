USE masgold_v2;

-- ============================================================
-- masGold v2
-- Migration 009: Audit + Printing
--
-- Important:
-- Physical bill/barcode dimensions are NOT guessed here.
-- They will be copied exactly from the proven prototype.
-- ============================================================


-- ============================================================
-- AUDIT
-- ============================================================


-- ------------------------------------------------------------
-- 1. Global audit log
--
-- Important business actions will write here through services.
--
-- We deliberately do NOT rely only on UI logs.
-- ------------------------------------------------------------

CREATE TABLE IF NOT EXISTS audit_log (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,

    user_id INT UNSIGNED DEFAULT NULL,

    event_type VARCHAR(80) NOT NULL,

    entity_type VARCHAR(80) NOT NULL,
    entity_id BIGINT UNSIGNED DEFAULT NULL,

    entity_reference VARCHAR(150) DEFAULT NULL,

    description VARCHAR(1000) DEFAULT NULL,

    before_json JSON DEFAULT NULL,
    after_json JSON DEFAULT NULL,

    metadata_json JSON DEFAULT NULL,

    workstation_name VARCHAR(150) DEFAULT NULL,

    occurred_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,

    PRIMARY KEY (id),

    KEY idx_audit_user (
        user_id
    ),

    KEY idx_audit_event (
        event_type
    ),

    KEY idx_audit_entity (
        entity_type,
        entity_id
    ),

    KEY idx_audit_date (
        occurred_at
    ),

    CONSTRAINT fk_audit_user
        FOREIGN KEY (user_id)
        REFERENCES users(id)
        ON DELETE SET NULL
) ENGINE=InnoDB
  DEFAULT CHARSET=utf8mb4
  COLLATE=utf8mb4_unicode_ci;


-- ============================================================
-- PRINTING
-- ============================================================


-- ------------------------------------------------------------
-- 2. Printer profiles
--
-- Dimensions are stored in millimeters.
--
-- They are nullable initially because we will copy the exact
-- measurements from the existing prototype.
-- ------------------------------------------------------------

CREATE TABLE IF NOT EXISTS printer_profiles (
    id SMALLINT UNSIGNED NOT NULL AUTO_INCREMENT,

    code VARCHAR(50) NOT NULL,
    name VARCHAR(150) NOT NULL,

    profile_type VARCHAR(20) NOT NULL,

    printer_name VARCHAR(255) DEFAULT NULL,

    paper_width_mm DECIMAL(8,2) DEFAULT NULL,
    paper_height_mm DECIMAL(8,2) DEFAULT NULL,

    margin_top_mm DECIMAL(8,2) NOT NULL DEFAULT 0.00,
    margin_bottom_mm DECIMAL(8,2) NOT NULL DEFAULT 0.00,
    margin_left_mm DECIMAL(8,2) NOT NULL DEFAULT 0.00,
    margin_right_mm DECIMAL(8,2) NOT NULL DEFAULT 0.00,

    orientation VARCHAR(20)
        NOT NULL DEFAULT 'PORTRAIT',

    dpi SMALLINT UNSIGNED DEFAULT NULL,

    copies_default SMALLINT UNSIGNED
        NOT NULL DEFAULT 1,

    is_configured BOOLEAN NOT NULL DEFAULT FALSE,
    is_default BOOLEAN NOT NULL DEFAULT FALSE,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,

    notes VARCHAR(1000) DEFAULT NULL,

    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,

    updated_at DATETIME NOT NULL
        DEFAULT CURRENT_TIMESTAMP
        ON UPDATE CURRENT_TIMESTAMP,

    PRIMARY KEY (id),

    UNIQUE KEY uq_printer_profiles_code (
        code
    ),

    KEY idx_printer_profile_type (
        profile_type
    ),

    CONSTRAINT chk_printer_profile_type
        CHECK (
            profile_type IN (
                'INVOICE',
                'BARCODE',
                'OTHER'
            )
        ),

    CONSTRAINT chk_printer_orientation
        CHECK (
            orientation IN (
                'PORTRAIT',
                'LANDSCAPE'
            )
        ),

    CONSTRAINT chk_printer_width
        CHECK (
            paper_width_mm IS NULL
            OR paper_width_mm > 0
        ),

    CONSTRAINT chk_printer_height
        CHECK (
            paper_height_mm IS NULL
            OR paper_height_mm > 0
        ),

    CONSTRAINT chk_printer_copies
        CHECK (
            copies_default > 0
        )
) ENGINE=InnoDB
  DEFAULT CHARSET=utf8mb4
  COLLATE=utf8mb4_unicode_ci;


-- ------------------------------------------------------------
-- 3. Print template versions
--
-- Allows layout modernization without losing knowledge of
-- which version produced an old document.
-- ------------------------------------------------------------

CREATE TABLE IF NOT EXISTS print_template_versions (
    id SMALLINT UNSIGNED NOT NULL AUTO_INCREMENT,

    template_code VARCHAR(50) NOT NULL,

    document_type VARCHAR(30) NOT NULL,

    version VARCHAR(30) NOT NULL,

    description VARCHAR(500) DEFAULT NULL,

    is_active BOOLEAN NOT NULL DEFAULT TRUE,

    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,

    PRIMARY KEY (id),

    UNIQUE KEY uq_print_template_version (
        template_code,
        version
    ),

    CONSTRAINT chk_print_template_type
        CHECK (
            document_type IN (
                'INVOICE',
                'BARCODE',
                'PAYMENT_RECEIPT'
            )
        )
) ENGINE=InnoDB
  DEFAULT CHARSET=utf8mb4
  COLLATE=utf8mb4_unicode_ci;


-- ------------------------------------------------------------
-- 4. Print jobs
--
-- Keeps printing history.
--
-- Physical settings are SNAPSHOTTED into every print job so
-- later profile changes do not rewrite history.
-- ------------------------------------------------------------

CREATE TABLE IF NOT EXISTS print_jobs (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,

    printer_profile_id SMALLINT UNSIGNED DEFAULT NULL,

    template_version_id SMALLINT UNSIGNED DEFAULT NULL,

    document_type VARCHAR(30) NOT NULL,

    invoice_id BIGINT UNSIGNED DEFAULT NULL,

    inventory_item_id BIGINT UNSIGNED DEFAULT NULL,

    barcode_id BIGINT UNSIGNED DEFAULT NULL,

    invoice_document_id BIGINT UNSIGNED DEFAULT NULL,

    copies SMALLINT UNSIGNED NOT NULL DEFAULT 1,

    print_status VARCHAR(20)
        NOT NULL DEFAULT 'QUEUED',

    -- Snapshot of physical configuration used.
    printer_name_snapshot VARCHAR(255) DEFAULT NULL,

    paper_width_mm_snapshot DECIMAL(8,2) DEFAULT NULL,
    paper_height_mm_snapshot DECIMAL(8,2) DEFAULT NULL,

    margin_top_mm_snapshot DECIMAL(8,2) DEFAULT NULL,
    margin_bottom_mm_snapshot DECIMAL(8,2) DEFAULT NULL,
    margin_left_mm_snapshot DECIMAL(8,2) DEFAULT NULL,
    margin_right_mm_snapshot DECIMAL(8,2) DEFAULT NULL,

    orientation_snapshot VARCHAR(20) DEFAULT NULL,
    dpi_snapshot SMALLINT UNSIGNED DEFAULT NULL,

    requested_by_user_id INT UNSIGNED DEFAULT NULL,

    requested_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,

    started_at DATETIME DEFAULT NULL,
    completed_at DATETIME DEFAULT NULL,

    error_message VARCHAR(2000) DEFAULT NULL,

    notes VARCHAR(1000) DEFAULT NULL,

    PRIMARY KEY (id),

    KEY idx_print_jobs_invoice (
        invoice_id
    ),

    KEY idx_print_jobs_inventory (
        inventory_item_id
    ),

    KEY idx_print_jobs_barcode (
        barcode_id
    ),

    KEY idx_print_jobs_status (
        print_status
    ),

    KEY idx_print_jobs_date (
        requested_at
    ),

    CONSTRAINT fk_print_job_profile
        FOREIGN KEY (printer_profile_id)
        REFERENCES printer_profiles(id)
        ON DELETE SET NULL,

    CONSTRAINT fk_print_job_template
        FOREIGN KEY (template_version_id)
        REFERENCES print_template_versions(id)
        ON DELETE SET NULL,

    CONSTRAINT fk_print_job_invoice
        FOREIGN KEY (invoice_id)
        REFERENCES invoices(id)
        ON DELETE SET NULL,

    CONSTRAINT fk_print_job_inventory
        FOREIGN KEY (inventory_item_id)
        REFERENCES inventory_items(id)
        ON DELETE SET NULL,

    CONSTRAINT fk_print_job_barcode
        FOREIGN KEY (barcode_id)
        REFERENCES inventory_barcodes(id)
        ON DELETE SET NULL,

    CONSTRAINT fk_print_job_document
        FOREIGN KEY (invoice_document_id)
        REFERENCES invoice_documents(id)
        ON DELETE SET NULL,

    CONSTRAINT fk_print_job_user
        FOREIGN KEY (requested_by_user_id)
        REFERENCES users(id)
        ON DELETE SET NULL,

    CONSTRAINT chk_print_job_type
        CHECK (
            document_type IN (
                'INVOICE',
                'BARCODE',
                'PAYMENT_RECEIPT'
            )
        ),

    CONSTRAINT chk_print_job_status
        CHECK (
            print_status IN (
                'QUEUED',
                'PRINTING',
                'SUCCESS',
                'FAILED',
                'CANCELLED'
            )
        ),

    CONSTRAINT chk_print_job_copies
        CHECK (
            copies > 0
        )
) ENGINE=InnoDB
  DEFAULT CHARSET=utf8mb4
  COLLATE=utf8mb4_unicode_ci;


-- ------------------------------------------------------------
-- 5. Print calibration history
--
-- Lets us adjust a new printer without changing application
-- source code.
-- ------------------------------------------------------------

CREATE TABLE IF NOT EXISTS printer_calibrations (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,

    printer_profile_id SMALLINT UNSIGNED NOT NULL,

    offset_x_mm DECIMAL(8,2) NOT NULL DEFAULT 0.00,
    offset_y_mm DECIMAL(8,2) NOT NULL DEFAULT 0.00,

    scale_x_percent DECIMAL(8,3)
        NOT NULL DEFAULT 100.000,

    scale_y_percent DECIMAL(8,3)
        NOT NULL DEFAULT 100.000,

    notes VARCHAR(1000) DEFAULT NULL,

    created_by_user_id INT UNSIGNED DEFAULT NULL,

    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,

    is_active BOOLEAN NOT NULL DEFAULT TRUE,

    PRIMARY KEY (id),

    KEY idx_printer_calibration_profile (
        printer_profile_id
    ),

    KEY idx_printer_calibration_active (
        printer_profile_id,
        is_active
    ),

    CONSTRAINT fk_printer_calibration_profile
        FOREIGN KEY (printer_profile_id)
        REFERENCES printer_profiles(id),

    CONSTRAINT fk_printer_calibration_user
        FOREIGN KEY (created_by_user_id)
        REFERENCES users(id)
        ON DELETE SET NULL,

    CONSTRAINT chk_printer_scale_x
        CHECK (
            scale_x_percent > 0
        ),

    CONSTRAINT chk_printer_scale_y
        CHECK (
            scale_y_percent > 0
        )
) ENGINE=InnoDB
  DEFAULT CHARSET=utf8mb4
  COLLATE=utf8mb4_unicode_ci;


-- ============================================================
-- INITIAL PRINT PROFILES
--
-- IMPORTANT:
-- Dimensions remain NULL intentionally.
-- We will populate them from the working prototype.
-- ============================================================

INSERT INTO printer_profiles (
    code,
    name,
    profile_type,
    is_configured,
    is_default,
    is_active,
    notes
)
VALUES

    (
        'INVOICE_DEFAULT',
        'Invoice Printer',
        'INVOICE',
        FALSE,
        TRUE,
        TRUE,
        'Physical dimensions must be copied from the existing proven prototype'
    ),

    (
        'BARCODE_DEFAULT',
        'Barcode Printer',
        'BARCODE',
        FALSE,
        TRUE,
        TRUE,
        'Physical label dimensions must be copied from the existing proven prototype'
    )

ON DUPLICATE KEY UPDATE
    name = VALUES(name),
    notes = VALUES(notes);


-- ============================================================
-- INITIAL TEMPLATE VERSIONS
-- ============================================================

INSERT INTO print_template_versions (
    template_code,
    document_type,
    version,
    description,
    is_active
)
VALUES

    (
        'INVOICE',
        'INVOICE',
        'v2.0',
        'Initial masGold v2 invoice layout; physical dimensions inherited from prototype',
        TRUE
    ),

    (
        'BARCODE',
        'BARCODE',
        'v2.0',
        'Initial masGold v2 barcode layout; physical dimensions inherited from prototype',
        TRUE
    ),

    (
        'PAYMENT_RECEIPT',
        'PAYMENT_RECEIPT',
        'v2.0',
        'Payment and settlement receipt',
        TRUE
    )

ON DUPLICATE KEY UPDATE
    description = VALUES(description),
    is_active = VALUES(is_active);


-- ============================================================
-- PRINTING PERMISSIONS
-- ============================================================

INSERT INTO permissions (
    code,
    name,
    description
)
VALUES

    (
        'PRINT_INVOICE',
        'Print Invoice',
        'Allows invoice printing and normal reprinting'
    ),

    (
        'PRINT_BARCODE',
        'Print Barcode',
        'Allows barcode label printing'
    ),

    (
        'PRINT_PAYMENT_RECEIPT',
        'Print Payment Receipt',
        'Allows printing customer settlement receipts'
    ),

    (
        'PRINT_HISTORY_VIEW',
        'View Print History',
        'Allows viewing printing history and failures'
    ),

    (
        'PRINT_SETTINGS_MANAGE',
        'Manage Printing Settings',
        'Allows printer profiles, dimensions and calibration changes'
    )

ON DUPLICATE KEY UPDATE
    name = VALUES(name),
    description = VALUES(description);


-- Owner receives missing permissions.

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
    9,
    'Global audit trail and physical printing foundation'
)
ON DUPLICATE KEY UPDATE
    name = VALUES(name);