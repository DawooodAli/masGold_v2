-- Small pieces (under 1 g) are sold for a fixed making PER PIECE (owner 2026-11-04): the accepted range, in EGP per piece, is a System Setting.
SET @masgold_sql = IF(EXISTS(SELECT 1 FROM schema_migrations WHERE version=96),
 'DO 0', 'SELECT * FROM MIGRATION_097_REFUSED_missing_096');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

INSERT INTO system_settings (setting_key, setting_value, setting_type, description, is_public) VALUES
('small_piece_making_min', '300', 'DECIMAL', 'أقل مصنعية مقبولة للقطعة الأقل من جرام (ج للقطعة)', 0),
('small_piece_making_max', '450', 'DECIMAL', 'أعلى مصنعية مقبولة للقطعة الأقل من جرام (ج للقطعة)', 0)
ON DUPLICATE KEY UPDATE description = VALUES(description);

INSERT INTO schema_migrations(version,name) VALUES (97,'Small piece making settings')
 ON DUPLICATE KEY UPDATE name=VALUES(name);
