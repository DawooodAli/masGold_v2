USE masgold_v2;

-- ============================================================
-- masGold v2
-- Migration 001: Foundation
-- Security + employees + master data
-- ============================================================


-- ------------------------------------------------------------
-- 1. Migration history
-- ------------------------------------------------------------

CREATE TABLE IF NOT EXISTS schema_migrations (
    version INT UNSIGNED NOT NULL,
    name VARCHAR(150) NOT NULL,
    applied_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,

    PRIMARY KEY (version)
) ENGINE=InnoDB
  DEFAULT CHARSET=utf8mb4
  COLLATE=utf8mb4_unicode_ci;


-- ------------------------------------------------------------
-- 2. Employees
--
-- Business identity.
-- An employee can exist without having a software login.
-- This is important for historical sales attribution.
-- ------------------------------------------------------------

CREATE TABLE IF NOT EXISTS employees (
    id INT UNSIGNED NOT NULL AUTO_INCREMENT,

    employee_code VARCHAR(30) DEFAULT NULL,
    name VARCHAR(100) NOT NULL,

    is_active BOOLEAN NOT NULL DEFAULT TRUE,

    notes VARCHAR(500) DEFAULT NULL,

    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL
        DEFAULT CURRENT_TIMESTAMP
        ON UPDATE CURRENT_TIMESTAMP,

    PRIMARY KEY (id),
    UNIQUE KEY uq_employees_code (employee_code),
    KEY idx_employees_name (name),
    KEY idx_employees_active (is_active)
) ENGINE=InnoDB
  DEFAULT CHARSET=utf8mb4
  COLLATE=utf8mb4_unicode_ci;


-- ------------------------------------------------------------
-- 3. Roles
-- ------------------------------------------------------------

CREATE TABLE IF NOT EXISTS roles (
    id SMALLINT UNSIGNED NOT NULL AUTO_INCREMENT,

    code VARCHAR(50) NOT NULL,
    name VARCHAR(100) NOT NULL,

    is_system BOOLEAN NOT NULL DEFAULT FALSE,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,

    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL
        DEFAULT CURRENT_TIMESTAMP
        ON UPDATE CURRENT_TIMESTAMP,

    PRIMARY KEY (id),
    UNIQUE KEY uq_roles_code (code)
) ENGINE=InnoDB
  DEFAULT CHARSET=utf8mb4
  COLLATE=utf8mb4_unicode_ci;


-- ------------------------------------------------------------
-- 4. Permissions
-- ------------------------------------------------------------

CREATE TABLE IF NOT EXISTS permissions (
    id SMALLINT UNSIGNED NOT NULL AUTO_INCREMENT,

    code VARCHAR(100) NOT NULL,
    name VARCHAR(150) NOT NULL,
    description VARCHAR(500) DEFAULT NULL,

    is_active BOOLEAN NOT NULL DEFAULT TRUE,

    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,

    PRIMARY KEY (id),
    UNIQUE KEY uq_permissions_code (code)
) ENGINE=InnoDB
  DEFAULT CHARSET=utf8mb4
  COLLATE=utf8mb4_unicode_ci;


-- ------------------------------------------------------------
-- 5. Role permissions
-- ------------------------------------------------------------

CREATE TABLE IF NOT EXISTS role_permissions (
    role_id SMALLINT UNSIGNED NOT NULL,
    permission_id SMALLINT UNSIGNED NOT NULL,

    granted_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,

    PRIMARY KEY (role_id, permission_id),

    CONSTRAINT fk_role_permissions_role
        FOREIGN KEY (role_id)
        REFERENCES roles(id)
        ON DELETE CASCADE,

    CONSTRAINT fk_role_permissions_permission
        FOREIGN KEY (permission_id)
        REFERENCES permissions(id)
        ON DELETE CASCADE
) ENGINE=InnoDB
  DEFAULT CHARSET=utf8mb4
  COLLATE=utf8mb4_unicode_ci;


-- ------------------------------------------------------------
-- 6. Users
--
-- Security identity.
-- Passwords/PINs will NEVER be stored as plain text.
-- ------------------------------------------------------------

CREATE TABLE IF NOT EXISTS users (
    id INT UNSIGNED NOT NULL AUTO_INCREMENT,

    employee_id INT UNSIGNED DEFAULT NULL,

    username VARCHAR(100) NOT NULL,
    password_hash VARCHAR(255) NOT NULL,

    -- Optional second authentication for sensitive owner actions.
    pin_hash VARCHAR(255) DEFAULT NULL,

    display_name VARCHAR(100) NOT NULL,

    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    must_change_password BOOLEAN NOT NULL DEFAULT FALSE,

    last_login_at DATETIME DEFAULT NULL,

    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL
        DEFAULT CURRENT_TIMESTAMP
        ON UPDATE CURRENT_TIMESTAMP,

    PRIMARY KEY (id),

    UNIQUE KEY uq_users_username (username),
    UNIQUE KEY uq_users_employee (employee_id),

    CONSTRAINT fk_users_employee
        FOREIGN KEY (employee_id)
        REFERENCES employees(id)
        ON DELETE SET NULL
) ENGINE=InnoDB
  DEFAULT CHARSET=utf8mb4
  COLLATE=utf8mb4_unicode_ci;


-- ------------------------------------------------------------
-- 7. User roles
--
-- A user can have more than one role if needed later.
-- ------------------------------------------------------------

CREATE TABLE IF NOT EXISTS user_roles (
    user_id INT UNSIGNED NOT NULL,
    role_id SMALLINT UNSIGNED NOT NULL,

    assigned_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,

    PRIMARY KEY (user_id, role_id),

    CONSTRAINT fk_user_roles_user
        FOREIGN KEY (user_id)
        REFERENCES users(id)
        ON DELETE CASCADE,

    CONSTRAINT fk_user_roles_role
        FOREIGN KEY (role_id)
        REFERENCES roles(id)
        ON DELETE CASCADE
) ENGINE=InnoDB
  DEFAULT CHARSET=utf8mb4
  COLLATE=utf8mb4_unicode_ci;


-- ------------------------------------------------------------
-- 8. Karats
-- ------------------------------------------------------------

CREATE TABLE IF NOT EXISTS karats (
    id TINYINT UNSIGNED NOT NULL AUTO_INCREMENT,

    karat_value TINYINT UNSIGNED NOT NULL,

    -- Examples:
    -- 18K = 0.750000
    -- 21K = 0.875000
    -- 24K = 1.000000
    purity DECIMAL(8,6) NOT NULL,

    name VARCHAR(50) NOT NULL,

    sort_order TINYINT UNSIGNED NOT NULL DEFAULT 0,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,

    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL
        DEFAULT CURRENT_TIMESTAMP
        ON UPDATE CURRENT_TIMESTAMP,

    PRIMARY KEY (id),
    UNIQUE KEY uq_karats_value (karat_value),

    CONSTRAINT chk_karats_value
        CHECK (karat_value > 0 AND karat_value <= 24),

    CONSTRAINT chk_karats_purity
        CHECK (purity > 0 AND purity <= 1)
) ENGINE=InnoDB
  DEFAULT CHARSET=utf8mb4
  COLLATE=utf8mb4_unicode_ci;


-- ------------------------------------------------------------
-- 9. Item categories
--
-- Replaces hard-coded A / B / C / T.
-- We preserve those codes initially for historical migration.
--
-- Making values are PER GRAM.
-- ------------------------------------------------------------

CREATE TABLE IF NOT EXISTS item_categories (
    id SMALLINT UNSIGNED NOT NULL AUTO_INCREMENT,

    code VARCHAR(20) NOT NULL,
    name VARCHAR(100) NOT NULL,

    purchase_making_min_per_gram DECIMAL(12,2) DEFAULT NULL,
    purchase_making_max_per_gram DECIMAL(12,2) DEFAULT NULL,

    sale_making_min_per_gram DECIMAL(12,2) DEFAULT NULL,
    sale_making_max_per_gram DECIMAL(12,2) DEFAULT NULL,

    -- Optional hard threshold.
    -- Later Billing can require manager approval below this value.
    manager_approval_below_per_gram DECIMAL(12,2) DEFAULT NULL,

    sort_order SMALLINT UNSIGNED NOT NULL DEFAULT 0,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,

    notes VARCHAR(500) DEFAULT NULL,

    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL
        DEFAULT CURRENT_TIMESTAMP
        ON UPDATE CURRENT_TIMESTAMP,

    PRIMARY KEY (id),

    UNIQUE KEY uq_item_categories_code (code),

    CONSTRAINT chk_category_purchase_range
        CHECK (
            purchase_making_min_per_gram IS NULL
            OR purchase_making_max_per_gram IS NULL
            OR purchase_making_min_per_gram
               <= purchase_making_max_per_gram
        ),

    CONSTRAINT chk_category_sale_range
        CHECK (
            sale_making_min_per_gram IS NULL
            OR sale_making_max_per_gram IS NULL
            OR sale_making_min_per_gram
               <= sale_making_max_per_gram
        )
) ENGINE=InnoDB
  DEFAULT CHARSET=utf8mb4
  COLLATE=utf8mb4_unicode_ci;


-- ------------------------------------------------------------
-- 10. Factories
--
-- Production DB currently has:
-- factory.symb
-- factory.def
--
-- v2 gives those fields clear meanings.
-- ------------------------------------------------------------

CREATE TABLE IF NOT EXISTS factories (
    id SMALLINT UNSIGNED NOT NULL AUTO_INCREMENT,

    code VARCHAR(50) DEFAULT NULL,
    name VARCHAR(150) NOT NULL,

    is_active BOOLEAN NOT NULL DEFAULT TRUE,

    notes VARCHAR(500) DEFAULT NULL,

    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL
        DEFAULT CURRENT_TIMESTAMP
        ON UPDATE CURRENT_TIMESTAMP,

    PRIMARY KEY (id),

    UNIQUE KEY uq_factories_code (code),
    KEY idx_factories_name (name),
    KEY idx_factories_active (is_active)
) ENGINE=InnoDB
  DEFAULT CHARSET=utf8mb4
  COLLATE=utf8mb4_unicode_ci;


-- ------------------------------------------------------------
-- 11. Item types
--
-- Production DB currently stores item names in `store`.
-- In v2 every item type can optionally have a default category.
-- ------------------------------------------------------------

CREATE TABLE IF NOT EXISTS item_types (
    id SMALLINT UNSIGNED NOT NULL AUTO_INCREMENT,

    code VARCHAR(50) DEFAULT NULL,
    name VARCHAR(150) NOT NULL,

    default_category_id SMALLINT UNSIGNED DEFAULT NULL,

    is_active BOOLEAN NOT NULL DEFAULT TRUE,

    sort_order SMALLINT UNSIGNED NOT NULL DEFAULT 0,

    notes VARCHAR(500) DEFAULT NULL,

    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL
        DEFAULT CURRENT_TIMESTAMP
        ON UPDATE CURRENT_TIMESTAMP,

    PRIMARY KEY (id),

    UNIQUE KEY uq_item_types_code (code),
    UNIQUE KEY uq_item_types_name (name),

    CONSTRAINT fk_item_types_default_category
        FOREIGN KEY (default_category_id)
        REFERENCES item_categories(id)
        ON DELETE SET NULL
) ENGINE=InnoDB
  DEFAULT CHARSET=utf8mb4
  COLLATE=utf8mb4_unicode_ci;



-- ============================================================
-- INITIAL SYSTEM DATA
-- ============================================================


-- ------------------------------------------------------------
-- Roles
-- ------------------------------------------------------------

INSERT INTO roles (code, name, is_system)
VALUES
    ('OWNER',       'Owner',       TRUE),
    ('MANAGER',     'Manager',     TRUE),
    ('SALESPERSON', 'Salesperson', TRUE),
    ('CASHIER',     'Cashier',     TRUE)
ON DUPLICATE KEY UPDATE
    name = VALUES(name);


-- ------------------------------------------------------------
-- Initial permissions
--
-- More permissions will be introduced by later migrations
-- as their modules are built.
-- ------------------------------------------------------------

INSERT INTO permissions (code, name, description)
VALUES

    ('APP_LOGIN',
     'Application Login',
     'Allows the user to log in to masGold'),

    ('BILLING_USE',
     'Use Billing',
     'Allows normal billing operations'),

    ('INVENTORY_VIEW',
     'View Inventory',
     'Allows viewing inventory'),

    ('INVENTORY_MANAGE',
     'Manage Inventory',
     'Allows adding, editing and moving inventory'),

    ('CUSTOMERS_VIEW',
     'View Customers',
     'Allows viewing customer information'),

    ('CUSTOMERS_MANAGE',
     'Manage Customers',
     'Allows customer creation and editing'),

    ('REPORTS_VIEW',
     'View Reports',
     'Allows normal operational reports'),

    ('OWNER_JRD_VIEW',
     'View Owner JRD',
     'Allows access to the confidential JRD owner position'),

    ('PAYMENT_CORRECT',
     'Correct Payments',
     'Allows correction of invoice settlement methods'),

    ('DEAD_CASH_VIEW',
     'View Dead Cash',
     'Allows viewing Dead Cash information'),

    ('DEAD_CASH_MANAGE',
     'Manage Dead Cash',
     'Allows Dead Cash write-offs and related actions'),

    ('LEGACY_BILLS_VIEW',
     'View Historical Bills',
     'Allows viewing migrated historical invoices'),

    ('LEGACY_BILLS_REPRINT',
     'Reprint Historical Bills',
     'Allows historical bill reprinting'),

    ('SETTINGS_MANAGE',
     'Manage Settings',
     'Allows modification of system settings and master data'),

    ('USERS_MANAGE',
     'Manage Users',
     'Allows user, role and permission administration'),

    ('AUDIT_VIEW',
     'View Audit Log',
     'Allows access to application audit history')

ON DUPLICATE KEY UPDATE
    name = VALUES(name),
    description = VALUES(description);


-- ------------------------------------------------------------
-- OWNER gets every permission.
--
-- IMPORTANT:
-- Other roles intentionally receive nothing yet.
-- We will define their exact permissions later instead
-- of guessing what your employees should be allowed to do.
-- ------------------------------------------------------------

INSERT IGNORE INTO role_permissions (role_id, permission_id)
SELECT
    r.id,
    p.id
FROM roles r
CROSS JOIN permissions p
WHERE r.code = 'OWNER';


-- ------------------------------------------------------------
-- Karats used by the real business
-- ------------------------------------------------------------

INSERT INTO karats (
    karat_value,
    purity,
    name,
    sort_order
)
VALUES
    (21, 0.875000, '21K', 1),
    (18, 0.750000, '18K', 2),
    (24, 1.000000, '24K', 3)

ON DUPLICATE KEY UPDATE
    purity = VALUES(purity),
    name = VALUES(name),
    sort_order = VALUES(sort_order);


-- ------------------------------------------------------------
-- Existing production category codes.
--
-- We deliberately DO NOT invent their making ranges yet.
-- You will define the real business ranges later.
-- ------------------------------------------------------------

INSERT INTO item_categories (
    code,
    name,
    sort_order
)
VALUES
    ('A', 'A', 1),
    ('B', 'B', 2),
    ('C', 'C', 3),
    ('D', 'D', 4),
    ('L', 'L', 5),
    ('T', 'T', 6)

ON DUPLICATE KEY UPDATE
    name = VALUES(name),
    sort_order = VALUES(sort_order);
-- ------------------------------------------------------------
-- Register migration
-- ------------------------------------------------------------

INSERT INTO schema_migrations (
    version,
    name
)
VALUES (
    1,
    'Foundation: security, employees and master data'
)
ON DUPLICATE KEY UPDATE
    name = VALUES(name);