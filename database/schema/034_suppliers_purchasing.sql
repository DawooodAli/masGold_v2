-- Purchasing & Suppliers V1 (Phase 1): supplier master, supplier purchase invoices with making groups,
-- immutable supplier GOLD and supplier MAKING ledgers, and the traceability link from a posted invoice group to
-- the inventory pieces created from it.
--
-- Two independent dimensions, never combined:
--   GOLD   grams of a single karat (the invoice weight is the contractual truth), plus its 21K equivalent
--          (Eq21 = weight x karat / 21, stored with 6 decimals; the physical weight and karat are never replaced).
--   MAKING EGP = SUM(group supplier weight x group EGP/gram). Later shop-scale barcode weights never change it.
--
-- SIGN CONVENTION (one, for both ledgers):  delta > 0  = the shop OWES the supplier  (علينا للمورد)
--                                            delta < 0  = the SUPPLIER owes the shop (لنا عند المورد)
-- Balances are always SUM(delta) of the ledger and are never stored or floored at zero.
-- Ledger rows are append-only; a correction is a future linked row, never an update or delete.
-- A posted invoice is idempotent: one gold movement and one making movement per source (unique source key).
-- Weight variance (shop barcoded weight - supplier weight) is informational and is not stored here.
-- Inventory pieces received from a supplier carry the new acquisition source SUPPLIER_PURCHASE (see below).
SET @masgold_sql = IF(EXISTS(SELECT 1 FROM schema_migrations WHERE version=33),
 'DO 0', 'SELECT * FROM MIGRATION_034_REFUSED_missing_033');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

CREATE TABLE IF NOT EXISTS suppliers (
 id INT UNSIGNED NOT NULL AUTO_INCREMENT,
 name VARCHAR(150) NOT NULL,
 phone VARCHAR(50) DEFAULT NULL,
 notes VARCHAR(1000) DEFAULT NULL,
 is_active BOOLEAN NOT NULL DEFAULT TRUE,
 created_by_user_id INT UNSIGNED DEFAULT NULL,
 updated_by_user_id INT UNSIGNED DEFAULT NULL,
 created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
 updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
 PRIMARY KEY (id),
 UNIQUE KEY uq_suppliers_name (name),
 CONSTRAINT fk_suppliers_created_user FOREIGN KEY (created_by_user_id) REFERENCES users(id),
 CONSTRAINT fk_suppliers_updated_user FOREIGN KEY (updated_by_user_id) REFERENCES users(id),
 CONSTRAINT chk_suppliers_name CHECK (CHAR_LENGTH(TRIM(name)) > 0)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ONE supplier invoice = ONE karat. The supplier weight is authoritative for the supplier gold account.
-- DRAFT is editable; POSTED is the accounting truth (no destructive edit); CANCELLED is a draft that was dropped.
CREATE TABLE IF NOT EXISTS supplier_purchase_invoices (
 id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
 supplier_id INT UNSIGNED NOT NULL,
 karat_id TINYINT UNSIGNED NOT NULL,
 supplier_reference VARCHAR(100) DEFAULT NULL,
 invoice_date DATE NOT NULL,
 supplier_weight_grams DECIMAL(12,3) NOT NULL,
 gross_making_total DECIMAL(16,2) NOT NULL,
 status VARCHAR(10) NOT NULL DEFAULT 'DRAFT',
 notes VARCHAR(1000) DEFAULT NULL,
 draft_request_key CHAR(36) NOT NULL,
 post_request_key CHAR(36) DEFAULT NULL,
 created_by_user_id INT UNSIGNED NOT NULL,
 posted_by_user_id INT UNSIGNED DEFAULT NULL,
 created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
 posted_at DATETIME DEFAULT NULL,
 cancelled_at DATETIME DEFAULT NULL,
 updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
 PRIMARY KEY (id),
 UNIQUE KEY uq_spi_draft_request (draft_request_key),
 UNIQUE KEY uq_spi_post_request (post_request_key),
 KEY idx_spi_supplier (supplier_id, invoice_date, id),
 KEY idx_spi_reference (supplier_id, supplier_reference),
 KEY idx_spi_status (status, id),
 CONSTRAINT fk_spi_supplier FOREIGN KEY (supplier_id) REFERENCES suppliers(id),
 CONSTRAINT fk_spi_karat FOREIGN KEY (karat_id) REFERENCES karats(id),
 CONSTRAINT fk_spi_created_user FOREIGN KEY (created_by_user_id) REFERENCES users(id),
 CONSTRAINT fk_spi_posted_user FOREIGN KEY (posted_by_user_id) REFERENCES users(id),
 CONSTRAINT chk_spi_weight CHECK (supplier_weight_grams > 0),
 CONSTRAINT chk_spi_making CHECK (gross_making_total >= 0),
 CONSTRAINT chk_spi_status CHECK (status IN ('DRAFT', 'POSTED', 'CANCELLED')),
 CONSTRAINT chk_spi_state CHECK (
  (status = 'DRAFT' AND posted_at IS NULL AND post_request_key IS NULL AND cancelled_at IS NULL)
  OR (status = 'POSTED' AND posted_at IS NOT NULL AND posted_by_user_id IS NOT NULL
      AND post_request_key IS NOT NULL AND cancelled_at IS NULL)
  OR (status = 'CANCELLED' AND cancelled_at IS NOT NULL AND posted_at IS NULL AND post_request_key IS NULL))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Making groups: the supplier weight and the EGP-per-gram rate. making_total is the group's contractual making
-- (weight x rate rounded once to 0.01 by the service), snapshotted so a posted invoice never depends on recomputation.
CREATE TABLE IF NOT EXISTS supplier_invoice_groups (
 id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
 invoice_id BIGINT UNSIGNED NOT NULL,
 group_no SMALLINT UNSIGNED NOT NULL,
 description VARCHAR(150) DEFAULT NULL,
 supplier_weight_grams DECIMAL(12,3) NOT NULL,
 making_per_gram DECIMAL(12,2) NOT NULL,
 making_total DECIMAL(16,2) NOT NULL,
 created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
 PRIMARY KEY (id),
 UNIQUE KEY uq_sig_invoice_group (invoice_id, group_no),
 UNIQUE KEY uq_sig_invoice_id (invoice_id, id),
 CONSTRAINT fk_sig_invoice FOREIGN KEY (invoice_id) REFERENCES supplier_purchase_invoices(id),
 CONSTRAINT chk_sig_weight CHECK (supplier_weight_grams > 0),
 CONSTRAINT chk_sig_rate CHECK (making_per_gram >= 0),
 CONSTRAINT chk_sig_total CHECK (making_total >= 0)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS supplier_gold_movement_types (
 id SMALLINT UNSIGNED NOT NULL AUTO_INCREMENT,
 code VARCHAR(50) NOT NULL,
 name VARCHAR(150) NOT NULL,
 description VARCHAR(500) DEFAULT NULL,
 is_active BOOLEAN NOT NULL DEFAULT TRUE,
 PRIMARY KEY (id),
 UNIQUE KEY uq_supplier_gold_movement_types_code (code)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS supplier_making_movement_types (
 id SMALLINT UNSIGNED NOT NULL AUTO_INCREMENT,
 code VARCHAR(50) NOT NULL,
 name VARCHAR(150) NOT NULL,
 description VARCHAR(500) DEFAULT NULL,
 is_active BOOLEAN NOT NULL DEFAULT TRUE,
 PRIMARY KEY (id),
 UNIQUE KEY uq_supplier_making_movement_types_code (code)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Immutable supplier GOLD ledger. weight_delta_grams is the physical/contractual grams in the invoice's own karat;
-- equivalent_21_delta_grams is its normalized 21K value. Both carry the same sign (see the convention above).
CREATE TABLE IF NOT EXISTS supplier_gold_movements (
 id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
 supplier_id INT UNSIGNED NOT NULL,
 movement_type_id SMALLINT UNSIGNED NOT NULL,
 karat_id TINYINT UNSIGNED NOT NULL,
 weight_delta_grams DECIMAL(14,3) NOT NULL,
 equivalent_21_delta_grams DECIMAL(18,6) NOT NULL,
 source_type VARCHAR(40) NOT NULL,
 source_id BIGINT UNSIGNED NOT NULL,
 source_reference VARCHAR(60) DEFAULT NULL,
 description VARCHAR(500) DEFAULT NULL,
 occurred_at DATETIME NOT NULL,
 created_by_user_id INT UNSIGNED NOT NULL,
 PRIMARY KEY (id),
 UNIQUE KEY uq_sgm_source (source_type, source_id, movement_type_id),
 KEY idx_sgm_supplier (supplier_id, occurred_at, id),
 KEY idx_sgm_karat (supplier_id, karat_id),
 CONSTRAINT fk_sgm_supplier FOREIGN KEY (supplier_id) REFERENCES suppliers(id),
 CONSTRAINT fk_sgm_type FOREIGN KEY (movement_type_id) REFERENCES supplier_gold_movement_types(id),
 CONSTRAINT fk_sgm_karat FOREIGN KEY (karat_id) REFERENCES karats(id),
 CONSTRAINT fk_sgm_user FOREIGN KEY (created_by_user_id) REFERENCES users(id),
 CONSTRAINT chk_sgm_sign CHECK (
  (weight_delta_grams > 0 AND equivalent_21_delta_grams > 0)
  OR (weight_delta_grams < 0 AND equivalent_21_delta_grams < 0))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Immutable supplier MAKING ledger in EGP (independent of gold; future payments / credits / market refund are new
-- movement types on this same table).
CREATE TABLE IF NOT EXISTS supplier_making_movements (
 id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
 supplier_id INT UNSIGNED NOT NULL,
 movement_type_id SMALLINT UNSIGNED NOT NULL,
 amount_delta DECIMAL(16,2) NOT NULL,
 source_type VARCHAR(40) NOT NULL,
 source_id BIGINT UNSIGNED NOT NULL,
 source_reference VARCHAR(60) DEFAULT NULL,
 description VARCHAR(500) DEFAULT NULL,
 occurred_at DATETIME NOT NULL,
 created_by_user_id INT UNSIGNED NOT NULL,
 PRIMARY KEY (id),
 UNIQUE KEY uq_smm_source (source_type, source_id, movement_type_id),
 KEY idx_smm_supplier (supplier_id, occurred_at, id),
 CONSTRAINT fk_smm_supplier FOREIGN KEY (supplier_id) REFERENCES suppliers(id),
 CONSTRAINT fk_smm_type FOREIGN KEY (movement_type_id) REFERENCES supplier_making_movement_types(id),
 CONSTRAINT fk_smm_user FOREIGN KEY (created_by_user_id) REFERENCES users(id),
 CONSTRAINT chk_smm_amount CHECK (amount_delta <> 0)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Traceability: supplier invoice -> making group -> inventory piece. One piece belongs to at most one group.
-- making_per_gram is the group's ORIGINAL rate at intake (a historical snapshot). The piece itself is created
-- through the canonical inventory path; the actual weight and barcode stay on inventory_items / inventory_barcodes.
CREATE TABLE IF NOT EXISTS supplier_invoice_pieces (
 id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
 inventory_item_id BIGINT UNSIGNED NOT NULL,
 invoice_id BIGINT UNSIGNED NOT NULL,
 group_id BIGINT UNSIGNED NOT NULL,
 making_per_gram DECIMAL(12,2) NOT NULL,
 request_key CHAR(36) NOT NULL,
 created_by_user_id INT UNSIGNED NOT NULL,
 created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
 PRIMARY KEY (id),
 UNIQUE KEY uq_sip_item (inventory_item_id),
 UNIQUE KEY uq_sip_request (request_key),
 KEY idx_sip_group (invoice_id, group_id),
 CONSTRAINT fk_sip_item FOREIGN KEY (inventory_item_id) REFERENCES inventory_items(id),
 CONSTRAINT fk_sip_group FOREIGN KEY (invoice_id, group_id) REFERENCES supplier_invoice_groups(invoice_id, id),
 CONSTRAINT fk_sip_user FOREIGN KEY (created_by_user_id) REFERENCES users(id),
 CONSTRAINT chk_sip_rate CHECK (making_per_gram >= 0)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Supplier merchandise is its own acquisition source: SUPPLIER_PURCHASE (توريد مورد). MARKET_PURCHASE keeps meaning
-- gold bought on the market (a future workflow); no existing value is renamed or removed and no row is rewritten.
-- 013 made the column ENUM(5 values) NOT NULL plus the named CHECK chk_inventory_acquisition_source; both are widened
-- by one value appended at the END (metadata-only for the ENUM). Every step is guarded, so a re-run changes nothing.
SET @masgold_sql := IF(
 NOT EXISTS (SELECT 1 FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'inventory_items'
   AND COLUMN_NAME = 'acquisition_source' AND COLUMN_TYPE LIKE '%SUPPLIER_PURCHASE%'),
 "ALTER TABLE inventory_items MODIFY COLUMN acquisition_source ENUM('MARKET_PURCHASE','OLD_GOLD_MANUFACTURE','OLD_STOCK_CONVERSION','LEGACY_MIGRATION','MANUAL_ENTRY','SUPPLIER_PURCHASE') NOT NULL",
 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(
 EXISTS (SELECT 1 FROM information_schema.CHECK_CONSTRAINTS WHERE CONSTRAINT_SCHEMA = DATABASE()
   AND CONSTRAINT_NAME = 'chk_inventory_acquisition_source' AND CHECK_CLAUSE NOT LIKE '%SUPPLIER_PURCHASE%'),
 "ALTER TABLE inventory_items DROP CHECK chk_inventory_acquisition_source",
 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(
 NOT EXISTS (SELECT 1 FROM information_schema.TABLE_CONSTRAINTS WHERE CONSTRAINT_SCHEMA = DATABASE()
   AND TABLE_NAME = 'inventory_items' AND CONSTRAINT_NAME = 'chk_inventory_acquisition_source'),
 "ALTER TABLE inventory_items ADD CONSTRAINT chk_inventory_acquisition_source CHECK (
   acquisition_source IN ('MARKET_PURCHASE','OLD_GOLD_MANUFACTURE','OLD_STOCK_CONVERSION','LEGACY_MIGRATION','MANUAL_ENTRY','SUPPLIER_PURCHASE'))",
 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

INSERT INTO supplier_gold_movement_types(code,name,description) VALUES
 ('PURCHASE_INVOICE','Supplier Purchase Invoice','Gold received on a supplier purchase invoice; the shop owes the supplier')
 ON DUPLICATE KEY UPDATE name=VALUES(name), description=VALUES(description);
INSERT INTO supplier_making_movement_types(code,name,description) VALUES
 ('PURCHASE_INVOICE','Supplier Purchase Invoice Making','Making (EGP) due on a supplier purchase invoice; the shop owes the supplier')
 ON DUPLICATE KEY UPDATE name=VALUES(name), description=VALUES(description);

INSERT INTO permissions(code,name,description) VALUES
 ('SUPPLIER_VIEW','View Suppliers','View suppliers, their balances, purchase invoices and ledgers'),
 ('SUPPLIER_MANAGE','Manage Suppliers','Create, edit, activate and deactivate suppliers'),
 ('SUPPLIER_INVOICE_EDIT','Edit Supplier Purchase Invoices','Create, edit and cancel draft supplier purchase invoices'),
 ('SUPPLIER_INVOICE_POST','Post Supplier Purchase Invoices','Post a supplier purchase invoice to the supplier ledgers'),
 ('SUPPLIER_INTAKE','Supplier Barcode Intake','Create inventory pieces and barcodes from a posted supplier invoice')
 ON DUPLICATE KEY UPDATE name=VALUES(name), description=VALUES(description);
INSERT INTO role_permissions(role_id,permission_id)
 SELECT r.id,p.id FROM roles r JOIN permissions p
  ON p.code IN ('SUPPLIER_VIEW','SUPPLIER_MANAGE','SUPPLIER_INVOICE_EDIT','SUPPLIER_INVOICE_POST','SUPPLIER_INTAKE')
 WHERE r.code IN ('OWNER','MANAGER') AND NOT EXISTS
 (SELECT 1 FROM role_permissions rp WHERE rp.role_id=r.id AND rp.permission_id=p.id);

INSERT INTO schema_migrations(version,name) VALUES (34,'Purchasing and Suppliers V1: supplier ledgers, purchase invoices and intake links')
 ON DUPLICATE KEY UPDATE name=VALUES(name);
