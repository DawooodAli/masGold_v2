-- Rollback of 084 (descriptions only). Refuses when a later migration exists.
SET @masgold_sql = IF(EXISTS(SELECT 1 FROM schema_migrations WHERE version > 84),
 'SELECT * FROM ROLLBACK_084_REFUSED_later_migration_exists', 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;
UPDATE system_settings SET description = 'أعلى كاش باك للسبائك/الجنيهات بدون مراجعة (ج)؛ أعلى منه يُراجَع من المدير' WHERE setting_key = 'cashback_review_limit';
UPDATE system_settings SET description = 'الكاش باك الافتراضي للسبائك/الجنيهات (ج) — يظهر في خانة الكاش باك' WHERE setting_key = 'default_cashback_amount';
DELETE FROM schema_migrations WHERE version = 84;
