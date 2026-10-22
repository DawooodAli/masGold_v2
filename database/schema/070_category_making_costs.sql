-- Actual making COST per gram of each item category («الحروف»: A B C D L T), set by the owner in Settings and frozen into every piece at stock entry. Prepared; NOT applied to any database by the task that wrote it.
--   item_category_costs: ONE number per category for every karat. It is the CURRENT cost; a piece keeps the cost it was entered with (inventory_items.acquisition_making_per_gram), so changing a cost
--   never rewrites a piece or a past sale. The making-profit report compares the making charged on a sale line with that cost; a piece entered before any cost existed is estimated from the CURRENT
--   category cost and shown apart as an estimate.
-- No existing row is rewritten. The CHECK expressions have NOT been executed on a MySQL server before the rehearsal gate. Every step is guarded (re-run safe).
SET @masgold_sql = IF(EXISTS(SELECT 1 FROM schema_migrations WHERE version=69),
 'DO 0', 'SELECT * FROM MIGRATION_070_REFUSED_missing_069');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

CREATE TABLE IF NOT EXISTS item_category_costs (
 category_id SMALLINT UNSIGNED NOT NULL,
 making_cost_per_gram DECIMAL(12,2) NOT NULL,
 updated_by_user_id INT UNSIGNED DEFAULT NULL,
 updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
 PRIMARY KEY (category_id),
 CONSTRAINT fk_category_cost_category FOREIGN KEY (category_id) REFERENCES item_categories(id) ON DELETE CASCADE,
 CONSTRAINT fk_category_cost_user FOREIGN KEY (updated_by_user_id) REFERENCES users(id) ON DELETE SET NULL,
 CONSTRAINT chk_category_cost_amount CHECK (making_cost_per_gram >= 0)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

INSERT INTO schema_migrations(version,name) VALUES (70,'Making cost per gram of each item category (for the making profit report)')
 ON DUPLICATE KEY UPDATE name=VALUES(name);
