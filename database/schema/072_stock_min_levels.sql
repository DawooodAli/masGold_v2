-- Minimum stock levels set by the owner (reorder alerts). Prepared; NOT applied to any database by the task that wrote it.
--   stock_min_levels: for an item TYPE of a KARAT the owner says "keep at least N pieces and / or G grams in stock"; the inventory alerts report lists every type / karat that is below its
--   minimum. One rule per (type, karat). The rule only alerts: nothing is ordered or changed automatically. Rules are plain settings (edited or deleted from Settings, every change audited).
-- No existing row is rewritten. The CHECK expressions have NOT been executed on a MySQL server before the rehearsal gate. Every step is guarded (re-run safe).
SET @masgold_sql = IF(EXISTS(SELECT 1 FROM schema_migrations WHERE version=71),
 'DO 0', 'SELECT * FROM MIGRATION_072_REFUSED_missing_071');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

CREATE TABLE IF NOT EXISTS stock_min_levels (
 id INT UNSIGNED NOT NULL AUTO_INCREMENT,
 item_type_id SMALLINT UNSIGNED NOT NULL,
 karat_id TINYINT UNSIGNED NOT NULL,
 min_pieces INT UNSIGNED DEFAULT NULL,
 min_grams DECIMAL(12,3) DEFAULT NULL,
 is_active TINYINT(1) NOT NULL DEFAULT 1,
 created_by_user_id INT UNSIGNED DEFAULT NULL,
 created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
 updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
 PRIMARY KEY (id),
 UNIQUE KEY uq_stock_min_level (item_type_id, karat_id),
 CONSTRAINT fk_stock_min_type FOREIGN KEY (item_type_id) REFERENCES item_types(id) ON DELETE CASCADE,
 CONSTRAINT fk_stock_min_karat FOREIGN KEY (karat_id) REFERENCES karats(id),
 CONSTRAINT fk_stock_min_user FOREIGN KEY (created_by_user_id) REFERENCES users(id) ON DELETE SET NULL,
 CONSTRAINT chk_stock_min_amounts CHECK ((min_pieces IS NOT NULL OR min_grams IS NOT NULL) AND (min_pieces IS NULL OR min_pieces >= 1) AND (min_grams IS NULL OR min_grams > 0))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

INSERT INTO schema_migrations(version,name) VALUES (72,'Minimum stock levels per item type and karat (reorder alerts)')
 ON DUPLICATE KEY UPDATE name=VALUES(name);
