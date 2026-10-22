-- Stocktake write-off (الجرد: شطب المتبقي). Prepared; NOT applied to any database by the task that wrote it.
--   After a barcode stocktake the owner / manager may approve it: every piece still in "المتبقي" of the counted scope leaves the stock (inventory_items.inventory_status = 'REMOVED',
--   a new movement type STOCKTAKE_WRITE_OFF) with a mandatory reason, and the reconciliation (expected / found / written off, per karat) is stored. Nothing is deleted: the piece, its barcode and its history stay.
--   stocktake_adjustments       one immutable record per approval (scope, counts and grams, reason, summary, the confirming user and time, request key)
--   stocktake_adjustment_items  the written-off pieces (one row per piece, a piece can be written off once)
--   permission STOCKTAKE_ADJUST (OWNER, MANAGER)
-- Every step is guarded (re-run safe). The CHECK expressions have NOT been executed on a MySQL server before the rehearsal gate.
SET @masgold_sql = IF(EXISTS(SELECT 1 FROM schema_migrations WHERE version=72),
 'DO 0', 'SELECT * FROM MIGRATION_073_REFUSED_missing_072');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

CREATE TABLE IF NOT EXISTS stocktake_adjustments (
 id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
 code VARCHAR(16) NOT NULL,
 item_type_id SMALLINT UNSIGNED DEFAULT NULL,
 karat_id TINYINT UNSIGNED DEFAULT NULL,
 expected_count INT UNSIGNED NOT NULL,
 expected_grams DECIMAL(14,3) NOT NULL,
 found_count INT UNSIGNED NOT NULL,
 found_grams DECIMAL(14,3) NOT NULL,
 written_off_count INT UNSIGNED NOT NULL,
 written_off_grams DECIMAL(14,3) NOT NULL,
 reason_code VARCHAR(20) NOT NULL,
 note VARCHAR(300) DEFAULT NULL,
 summary_json JSON NOT NULL,
 request_key VARCHAR(64) NOT NULL,
 request_hash CHAR(64) NOT NULL,
 confirmed_by_user_id INT UNSIGNED DEFAULT NULL,
 confirmed_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
 PRIMARY KEY (id),
 UNIQUE KEY uq_stocktake_adjustment_code (code),
 UNIQUE KEY uq_stocktake_adjustment_key (request_key),
 CONSTRAINT fk_stocktake_adj_user FOREIGN KEY (confirmed_by_user_id) REFERENCES users(id) ON DELETE SET NULL,
 CONSTRAINT chk_stocktake_adj_reason CHECK (reason_code IN ('SOLD_OUTSIDE','MISSING','DATA_ERROR','OTHER')),
 CONSTRAINT chk_stocktake_adj_note CHECK (reason_code <> 'OTHER' OR (note IS NOT NULL AND CHAR_LENGTH(TRIM(note)) > 0)),
 CONSTRAINT chk_stocktake_adj_counts CHECK (written_off_count >= 1 AND expected_count = found_count + written_off_count),
 CONSTRAINT chk_stocktake_adj_grams CHECK (written_off_grams > 0 AND expected_grams = found_grams + written_off_grams)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS stocktake_adjustment_items (
 id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
 adjustment_id BIGINT UNSIGNED NOT NULL,
 inventory_item_id BIGINT UNSIGNED NOT NULL,
 karat_id TINYINT UNSIGNED NOT NULL,
 weight_grams DECIMAL(10,3) NOT NULL,
 barcode_snapshot VARCHAR(100) DEFAULT NULL,
 inventory_movement_id BIGINT UNSIGNED DEFAULT NULL,
 PRIMARY KEY (id),
 UNIQUE KEY uq_stocktake_item_once (inventory_item_id),
 KEY idx_stocktake_item_adjustment (adjustment_id),
 CONSTRAINT fk_stocktake_item_adjustment FOREIGN KEY (adjustment_id) REFERENCES stocktake_adjustments(id),
 CONSTRAINT fk_stocktake_item_piece FOREIGN KEY (inventory_item_id) REFERENCES inventory_items(id),
 CONSTRAINT fk_stocktake_item_karat FOREIGN KEY (karat_id) REFERENCES karats(id),
 CONSTRAINT fk_stocktake_item_movement FOREIGN KEY (inventory_movement_id) REFERENCES inventory_movements(id),
 CONSTRAINT chk_stocktake_item_weight CHECK (weight_grams > 0)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

INSERT INTO inventory_movement_types (code, name, description) VALUES
 ('STOCKTAKE_WRITE_OFF', 'Stocktake Write-Off', 'A piece still missing after an approved barcode stocktake leaves the stock (status REMOVED); nothing is deleted')
 ON DUPLICATE KEY UPDATE name=VALUES(name), description=VALUES(description);

INSERT INTO permissions(code,name,description) VALUES
 ('STOCKTAKE_ADJUST','Approve Stocktake Write-Off','Approve a barcode stocktake and write off the pieces that were not found (owner and manager)')
 ON DUPLICATE KEY UPDATE name=VALUES(name), description=VALUES(description);
INSERT INTO role_permissions(role_id,permission_id)
 SELECT r.id,p.id FROM roles r JOIN permissions p ON p.code='STOCKTAKE_ADJUST'
 WHERE r.code IN ('OWNER','MANAGER') AND NOT EXISTS
 (SELECT 1 FROM role_permissions rp WHERE rp.role_id=r.id AND rp.permission_id=p.id);

INSERT INTO schema_migrations(version,name) VALUES (73,'Stocktake write-off: approved barcode stocktake removes the missing pieces with a stored reconciliation')
 ON DUPLICATE KEY UPDATE name=VALUES(name);
