-- Scrap adjustment by the manager (تعديل رصيد الكسر مع السبب). Prepared; NOT applied to any database by the task that wrote it.
--   The permission OLD_GOLD_ADJUST has existed since 004 (the owner holds it); the adjustment itself posts the existing ADJUSTMENT_IN / ADJUSTMENT_OUT movement types.
--   This migration only grants the right to the MANAGER role (the OWNER already has every permission; it is granted again here when missing). Re-run safe.
SET @masgold_sql = IF(EXISTS(SELECT 1 FROM schema_migrations WHERE version=74),
 'DO 0', 'SELECT * FROM MIGRATION_075_REFUSED_missing_074');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

UPDATE permissions SET name = 'Adjust Old Gold (Scrap) with a reason', description = 'Correct the physical Scrap of one karat with a mandatory reason (owner and manager)' WHERE code = 'OLD_GOLD_ADJUST';

INSERT INTO role_permissions(role_id, permission_id)
 SELECT r.id, p.id FROM roles r JOIN permissions p ON p.code = 'OLD_GOLD_ADJUST'
 WHERE r.code IN ('OWNER', 'MANAGER') AND NOT EXISTS
 (SELECT 1 FROM role_permissions rp WHERE rp.role_id = r.id AND rp.permission_id = p.id);

INSERT INTO schema_migrations(version,name) VALUES (75,'Scrap adjustment: OLD_GOLD_ADJUST granted to the manager role')
 ON DUPLICATE KEY UPDATE name=VALUES(name);
