-- ============================================================
-- masGold v2 — System Settings Table & Permissions
-- Migration 081
-- ============================================================

SET @masgold_sql = IF(EXISTS(SELECT 1 FROM schema_migrations WHERE version = 80),
 'DO 0', 'SELECT * FROM MIGRATION_081_REFUSED_missing_080');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

-- Permissions
INSERT INTO permissions(code, name, description) VALUES
 ('SYSTEM_SETTINGS_VIEW', 'عرض إعدادات النظام', 'View system configuration settings'),
 ('SYSTEM_SETTINGS_MANAGE', 'إدارة إعدادات النظام', 'Create and update system configuration settings')
ON DUPLICATE KEY UPDATE name=VALUES(name), description=VALUES(description);

-- Grant to OWNER only (settings management is owner-level)
INSERT INTO role_permissions(role_id, permission_id)
 SELECT r.id, p.id FROM roles r JOIN permissions p ON p.code = 'SYSTEM_SETTINGS_VIEW'
 WHERE r.code = 'OWNER' AND NOT EXISTS
 (SELECT 1 FROM role_permissions rp WHERE rp.role_id = r.id AND rp.permission_id = p.id);

INSERT INTO role_permissions(role_id, permission_id)
 SELECT r.id, p.id FROM roles r JOIN permissions p ON p.code = 'SYSTEM_SETTINGS_MANAGE'
 WHERE r.code = 'OWNER' AND NOT EXISTS
 (SELECT 1 FROM role_permissions rp WHERE rp.role_id = r.id AND rp.permission_id = p.id);

-- Settings table
CREATE TABLE IF NOT EXISTS system_settings (
    setting_key VARCHAR(64) NOT NULL,
    setting_value TEXT NOT NULL,
    setting_type ENUM('STRING','INT','DECIMAL','BOOL','JSON') NOT NULL DEFAULT 'STRING',
    description VARCHAR(255) DEFAULT NULL,
    is_public TINYINT(1) NOT NULL DEFAULT 0,  -- 1 = readable by all authenticated users
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    updated_by_user_id INT UNSIGNED DEFAULT NULL,
    PRIMARY KEY (setting_key),
    CONSTRAINT fk_sys_set_updated_by FOREIGN KEY (updated_by_user_id) REFERENCES employees(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Seed default settings (idempotent)
INSERT INTO system_settings (setting_key, setting_value, setting_type, description, is_public) VALUES
('shop_name', 'مجوهرات ماس', 'STRING', 'اسم المحل المعروض في الواجهة والتقارير', 1),
('shop_address', '', 'STRING', 'عنوان المحل للفواتير', 1),
('shop_phone', '', 'STRING', 'هاتف المحل', 1),
('default_old_gold_discount_pct', '1.50', 'DECIMAL', 'نسبة الخصم الافتراضية للكسر (1.00-5.00)', 0),
('min_old_gold_discount_pct', '1.00', 'DECIMAL', 'أقل نسبة خصم مسموحة للكسر', 0),
('max_old_gold_discount_pct', '5.00', 'DECIMAL', 'أعلى نسبة خصم مسموحة للكسر', 0),
('dead_stock_days', '180', 'INT', 'أيام اعتبار القطعة ميتة (Dead Stock)', 0),
('min_sales_for_velocity', '3', 'INT', 'أقل عدد مبيعات لحساب سرعة التدوير', 0),
('velocity_days', '90', 'INT', 'فترة حساب سرعة التدوير (أيام)', 0),
('low_cover_days', '14', 'INT', 'أيام التغطية المنخفضة للتنبيه', 0),
('weight_band_top', '10', 'INT', 'أعلى شريحة وزن في تقرير الأوزان', 0),
('price_deviation_warning_pct', '10', 'INT', 'نسبة انحراف السعر للتحذير (%)', 0),
('auto_login_enabled', '0', 'BOOL', 'تفعيل الدخول التلقائي (للبيئة التطوير فقط)', 0),
('invoice_print_copies', '2', 'INT', 'عدد نسخ الطباعة الافتراضي', 1),
('enable_book_mode', '0', 'BOOL', 'تفعيل وضع BOOK للعطلات السوقية', 0),
('currency_symbol', 'ج', 'STRING', 'رمز العملة', 1),
('date_format', 'YYYY-MM-DD', 'STRING', 'تنسيق التاريخ', 1)
ON DUPLICATE KEY UPDATE
    setting_value = VALUES(setting_value),
    description = VALUES(description),
    is_public = VALUES(is_public);

INSERT INTO schema_migrations(version,name) VALUES (81,'System settings table and permissions')
 ON DUPLICATE KEY UPDATE name=VALUES(name);

-- ============================================================
-- End
-- ============================================================