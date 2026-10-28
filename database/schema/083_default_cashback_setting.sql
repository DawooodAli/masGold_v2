-- Default cashback for BAR / POUND scrap lines (owner request 2026-10-28): the cashback box on the invoice starts at this amount (EGP).
SET @masgold_sql = IF(EXISTS(SELECT 1 FROM schema_migrations WHERE version=82),
 'DO 0', 'SELECT * FROM MIGRATION_083_REFUSED_missing_082');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

INSERT INTO system_settings (setting_key, setting_value, setting_type, description, is_public) VALUES
('default_cashback_amount', '25', 'DECIMAL', 'الكاش باك الافتراضي للسبائك/الجنيهات (ج) — يظهر في خانة الكاش باك', 0)
ON DUPLICATE KEY UPDATE description = VALUES(description);

INSERT INTO schema_migrations(version,name) VALUES (83,'Default cashback setting')
 ON DUPLICATE KEY UPDATE name=VALUES(name);
