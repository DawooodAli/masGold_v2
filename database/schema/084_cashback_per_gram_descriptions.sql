-- The cashback is EGP PER GRAM (owner 2026-10-28): only the descriptions of its two System Settings change.
SET @masgold_sql = IF(EXISTS(SELECT 1 FROM schema_migrations WHERE version=83),
 'DO 0', 'SELECT * FROM MIGRATION_084_REFUSED_missing_083');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

UPDATE system_settings SET description = 'أعلى كاش باك للسبائك/الجنيهات بدون مراجعة (ج للجرام)؛ أعلى منه يُراجَع من المدير' WHERE setting_key = 'cashback_review_limit';
UPDATE system_settings SET description = 'الكاش باك الافتراضي للسبائك/الجنيهات (ج للجرام) — يظهر في خانة الكاش باك' WHERE setting_key = 'default_cashback_amount';

INSERT INTO schema_migrations(version,name) VALUES (84,'Cashback per gram setting descriptions')
 ON DUPLICATE KEY UPDATE name=VALUES(name);
