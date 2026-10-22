-- Manageable income / expense categories for the categorised manual cash movements of 025 / 026 / 068 (Settings → تصنيفات المصروفات والإيرادات). Prepared; NOT applied to any database by the task that wrote it.
--   cash_categories: the list the treasury «حركة نقدية جديدة» offers. The ten categories that existed in code are seeded (is_system = 1, same codes and Arabic names); the owner can ADD more
--   (direction IN / OUT / BOTH), RENAME, DISABLE and DELETE an UNUSED one from Settings. A category used by any financial operation can only be disabled: the foreign key refuses its deletion.
--   financial_operations.category now references cash_categories(code) instead of the fixed list of chk_fo_category (which only keeps "a category belongs to a manual deposit / withdrawal");
--   whether a category fits the direction of the operation is enforced by the service from this table.
-- No existing financial_operations row is rewritten (every stored category is one of the seeded codes). The CHECK / FK expressions have NOT been executed on a MySQL server before the rehearsal gate.
-- Every step is guarded (re-run safe).
SET @masgold_sql = IF(EXISTS(SELECT 1 FROM schema_migrations WHERE version=68),
 'DO 0', 'SELECT * FROM MIGRATION_069_REFUSED_missing_068');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

CREATE TABLE IF NOT EXISTS cash_categories (
 id SMALLINT UNSIGNED NOT NULL AUTO_INCREMENT,
 code VARCHAR(20) NOT NULL,
 name VARCHAR(60) NOT NULL,
 direction VARCHAR(4) NOT NULL,
 is_active TINYINT(1) NOT NULL DEFAULT 1,
 is_system TINYINT(1) NOT NULL DEFAULT 0,
 sort_order SMALLINT NOT NULL DEFAULT 100,
 created_by_user_id INT UNSIGNED DEFAULT NULL,
 created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
 updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
 PRIMARY KEY (id),
 UNIQUE KEY uq_cash_category_code (code),
 UNIQUE KEY uq_cash_category_name (name),
 CONSTRAINT fk_cash_category_user FOREIGN KEY (created_by_user_id) REFERENCES users(id) ON DELETE SET NULL,
 CONSTRAINT chk_cash_category_direction CHECK (direction IN ('IN', 'OUT', 'BOTH')),
 CONSTRAINT chk_cash_category_name CHECK (CHAR_LENGTH(TRIM(name)) > 0),
 CONSTRAINT chk_cash_category_code CHECK (CHAR_LENGTH(TRIM(code)) > 0)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

INSERT INTO cash_categories(code, name, direction, is_active, is_system, sort_order) VALUES
 ('EXPENSE', 'مصروفات', 'OUT', 1, 1, 10),
 ('SALARY', 'مرتبات', 'OUT', 1, 1, 20),
 ('RENT', 'إيجار', 'OUT', 1, 1, 30),
 ('UTILITIES', 'كهرباء ومياه', 'OUT', 1, 1, 40),
 ('TRANSPORT', 'نقل', 'OUT', 1, 1, 50),
 ('MAINTENANCE', 'صيانة', 'OUT', 1, 1, 60),
 ('HOSPITALITY', 'بوفيه وضيافة', 'OUT', 1, 1, 70),
 ('STAFF_ADVANCE', 'سلف موظفين', 'OUT', 1, 1, 80),
 ('REVENUE', 'إيراد', 'IN', 1, 1, 90),
 ('REPAIR', 'مرمة', 'BOTH', 1, 1, 100)
 ON DUPLICATE KEY UPDATE code = code;

SET @masgold_sql := IF(EXISTS(SELECT 1 FROM information_schema.TABLE_CONSTRAINTS tc JOIN information_schema.CHECK_CONSTRAINTS cc ON cc.CONSTRAINT_SCHEMA=tc.CONSTRAINT_SCHEMA AND cc.CONSTRAINT_NAME=tc.CONSTRAINT_NAME
 WHERE tc.TABLE_SCHEMA=DATABASE() AND tc.TABLE_NAME='financial_operations' AND tc.CONSTRAINT_NAME='chk_fo_category' AND cc.CHECK_CLAUSE LIKE '%STAFF_ADVANCE%'),
 'ALTER TABLE financial_operations DROP CHECK chk_fo_category', 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(NOT EXISTS(SELECT 1 FROM information_schema.TABLE_CONSTRAINTS WHERE CONSTRAINT_SCHEMA=DATABASE() AND TABLE_NAME='financial_operations' AND CONSTRAINT_NAME='chk_fo_category'),
 "ALTER TABLE financial_operations ADD CONSTRAINT chk_fo_category CHECK (category IS NULL OR operation_type IN ('MANUAL_DEPOSIT', 'MANUAL_WITHDRAWAL'))", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(NOT EXISTS(SELECT 1 FROM information_schema.TABLE_CONSTRAINTS WHERE CONSTRAINT_SCHEMA=DATABASE() AND TABLE_NAME='financial_operations' AND CONSTRAINT_NAME='fk_fo_category'),
 'ALTER TABLE financial_operations ADD CONSTRAINT fk_fo_category FOREIGN KEY (category) REFERENCES cash_categories(code) ON UPDATE RESTRICT ON DELETE RESTRICT', 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

INSERT INTO schema_migrations(version,name) VALUES (69,'Manageable income and expense categories (Settings) for categorised manual cash movements')
 ON DUPLICATE KEY UPDATE name=VALUES(name);
