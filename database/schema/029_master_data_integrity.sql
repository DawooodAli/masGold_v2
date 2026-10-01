-- Master Data integrity: a Factory, Item Type or Category that any stock / history row references can
-- never be deleted by the DATABASE either (the Service check gives the Arabic message; this is the floor).
--  * Every ON DELETE SET NULL foreign key to factories / item_types / item_categories becomes RESTRICT.
--    fk_inventory_item_type is already restrictive (NO ACTION) and is left unchanged.
--  * old_gold_movements.factory_id (INT, no key) is narrowed to the factories.id type, indexed and
--    given a RESTRICT foreign key. Existing values are validated first; nothing is rewritten.
--  * Adds MASTER_DATA_MANAGE (create / edit / enable / disable / permanently delete master data), OWNER only.
SET @masgold_sql = IF(EXISTS(SELECT 1 FROM schema_migrations WHERE version=28),
 'DO 0', 'SELECT * FROM MIGRATION_029_REFUSED_missing_028');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

-- Old-gold factory references must already point at real factories (never nulled or guessed).
SET @masgold_sql = IF(EXISTS(SELECT 1 FROM old_gold_movements m LEFT JOIN factories f ON f.id=m.factory_id
                             WHERE m.factory_id IS NOT NULL AND f.id IS NULL),
 'SELECT * FROM MIGRATION_029_REFUSED_old_gold_movements_unknown_factory', 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

-- ---------------------------------------------------------------- SET NULL -> RESTRICT
-- Each FK: drop only while it is still ON DELETE SET NULL, then re-add (same name/column) as RESTRICT.
SET @masgold_sql := IF(EXISTS(SELECT 1 FROM information_schema.REFERENTIAL_CONSTRAINTS WHERE CONSTRAINT_SCHEMA=DATABASE()
 AND CONSTRAINT_NAME='fk_inventory_factory' AND DELETE_RULE='SET NULL'),
 "ALTER TABLE inventory_items DROP FOREIGN KEY fk_inventory_factory", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;
SET @masgold_sql := IF(NOT EXISTS(SELECT 1 FROM information_schema.REFERENTIAL_CONSTRAINTS WHERE CONSTRAINT_SCHEMA=DATABASE()
 AND CONSTRAINT_NAME='fk_inventory_factory'),
 "ALTER TABLE inventory_items ADD CONSTRAINT fk_inventory_factory FOREIGN KEY (factory_id) REFERENCES factories(id) ON DELETE RESTRICT", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(EXISTS(SELECT 1 FROM information_schema.REFERENTIAL_CONSTRAINTS WHERE CONSTRAINT_SCHEMA=DATABASE()
 AND CONSTRAINT_NAME='fk_inventory_category' AND DELETE_RULE='SET NULL'),
 "ALTER TABLE inventory_items DROP FOREIGN KEY fk_inventory_category", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;
SET @masgold_sql := IF(NOT EXISTS(SELECT 1 FROM information_schema.REFERENTIAL_CONSTRAINTS WHERE CONSTRAINT_SCHEMA=DATABASE()
 AND CONSTRAINT_NAME='fk_inventory_category'),
 "ALTER TABLE inventory_items ADD CONSTRAINT fk_inventory_category FOREIGN KEY (category_id) REFERENCES item_categories(id) ON DELETE RESTRICT", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(EXISTS(SELECT 1 FROM information_schema.REFERENTIAL_CONSTRAINTS WHERE CONSTRAINT_SCHEMA=DATABASE()
 AND CONSTRAINT_NAME='fk_reconciliation_factory' AND DELETE_RULE='SET NULL'),
 "ALTER TABLE inventory_reconciliation_cases DROP FOREIGN KEY fk_reconciliation_factory", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;
SET @masgold_sql := IF(NOT EXISTS(SELECT 1 FROM information_schema.REFERENTIAL_CONSTRAINTS WHERE CONSTRAINT_SCHEMA=DATABASE()
 AND CONSTRAINT_NAME='fk_reconciliation_factory'),
 "ALTER TABLE inventory_reconciliation_cases ADD CONSTRAINT fk_reconciliation_factory FOREIGN KEY (observed_factory_id) REFERENCES factories(id) ON DELETE RESTRICT", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(EXISTS(SELECT 1 FROM information_schema.REFERENTIAL_CONSTRAINTS WHERE CONSTRAINT_SCHEMA=DATABASE()
 AND CONSTRAINT_NAME='fk_reconciliation_item_type' AND DELETE_RULE='SET NULL'),
 "ALTER TABLE inventory_reconciliation_cases DROP FOREIGN KEY fk_reconciliation_item_type", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;
SET @masgold_sql := IF(NOT EXISTS(SELECT 1 FROM information_schema.REFERENTIAL_CONSTRAINTS WHERE CONSTRAINT_SCHEMA=DATABASE()
 AND CONSTRAINT_NAME='fk_reconciliation_item_type'),
 "ALTER TABLE inventory_reconciliation_cases ADD CONSTRAINT fk_reconciliation_item_type FOREIGN KEY (observed_item_type_id) REFERENCES item_types(id) ON DELETE RESTRICT", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(EXISTS(SELECT 1 FROM information_schema.REFERENTIAL_CONSTRAINTS WHERE CONSTRAINT_SCHEMA=DATABASE()
 AND CONSTRAINT_NAME='fk_reconciliation_category' AND DELETE_RULE='SET NULL'),
 "ALTER TABLE inventory_reconciliation_cases DROP FOREIGN KEY fk_reconciliation_category", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;
SET @masgold_sql := IF(NOT EXISTS(SELECT 1 FROM information_schema.REFERENTIAL_CONSTRAINTS WHERE CONSTRAINT_SCHEMA=DATABASE()
 AND CONSTRAINT_NAME='fk_reconciliation_category'),
 "ALTER TABLE inventory_reconciliation_cases ADD CONSTRAINT fk_reconciliation_category FOREIGN KEY (observed_category_id) REFERENCES item_categories(id) ON DELETE RESTRICT", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(EXISTS(SELECT 1 FROM information_schema.REFERENTIAL_CONSTRAINTS WHERE CONSTRAINT_SCHEMA=DATABASE()
 AND CONSTRAINT_NAME='fk_segment_factory' AND DELETE_RULE='SET NULL'),
 "ALTER TABLE analytics_segments DROP FOREIGN KEY fk_segment_factory", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;
SET @masgold_sql := IF(NOT EXISTS(SELECT 1 FROM information_schema.REFERENTIAL_CONSTRAINTS WHERE CONSTRAINT_SCHEMA=DATABASE()
 AND CONSTRAINT_NAME='fk_segment_factory'),
 "ALTER TABLE analytics_segments ADD CONSTRAINT fk_segment_factory FOREIGN KEY (factory_id) REFERENCES factories(id) ON DELETE RESTRICT", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(EXISTS(SELECT 1 FROM information_schema.REFERENTIAL_CONSTRAINTS WHERE CONSTRAINT_SCHEMA=DATABASE()
 AND CONSTRAINT_NAME='fk_segment_item_type' AND DELETE_RULE='SET NULL'),
 "ALTER TABLE analytics_segments DROP FOREIGN KEY fk_segment_item_type", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;
SET @masgold_sql := IF(NOT EXISTS(SELECT 1 FROM information_schema.REFERENTIAL_CONSTRAINTS WHERE CONSTRAINT_SCHEMA=DATABASE()
 AND CONSTRAINT_NAME='fk_segment_item_type'),
 "ALTER TABLE analytics_segments ADD CONSTRAINT fk_segment_item_type FOREIGN KEY (item_type_id) REFERENCES item_types(id) ON DELETE RESTRICT", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(EXISTS(SELECT 1 FROM information_schema.REFERENTIAL_CONSTRAINTS WHERE CONSTRAINT_SCHEMA=DATABASE()
 AND CONSTRAINT_NAME='fk_segment_category' AND DELETE_RULE='SET NULL'),
 "ALTER TABLE analytics_segments DROP FOREIGN KEY fk_segment_category", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;
SET @masgold_sql := IF(NOT EXISTS(SELECT 1 FROM information_schema.REFERENTIAL_CONSTRAINTS WHERE CONSTRAINT_SCHEMA=DATABASE()
 AND CONSTRAINT_NAME='fk_segment_category'),
 "ALTER TABLE analytics_segments ADD CONSTRAINT fk_segment_category FOREIGN KEY (category_id) REFERENCES item_categories(id) ON DELETE RESTRICT", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

-- An item type's default category: deleting the category must not silently clear the default.
SET @masgold_sql := IF(EXISTS(SELECT 1 FROM information_schema.REFERENTIAL_CONSTRAINTS WHERE CONSTRAINT_SCHEMA=DATABASE()
 AND CONSTRAINT_NAME='fk_item_types_default_category' AND DELETE_RULE='SET NULL'),
 "ALTER TABLE item_types DROP FOREIGN KEY fk_item_types_default_category", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;
SET @masgold_sql := IF(NOT EXISTS(SELECT 1 FROM information_schema.REFERENTIAL_CONSTRAINTS WHERE CONSTRAINT_SCHEMA=DATABASE()
 AND CONSTRAINT_NAME='fk_item_types_default_category'),
 "ALTER TABLE item_types ADD CONSTRAINT fk_item_types_default_category FOREIGN KEY (default_category_id) REFERENCES item_categories(id) ON DELETE RESTRICT", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

-- ---------------------------------------------------------------- old_gold_movements.factory_id
SET @masgold_sql := IF(EXISTS(SELECT 1 FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=DATABASE()
 AND TABLE_NAME='old_gold_movements' AND COLUMN_NAME='factory_id' AND COLUMN_TYPE='int unsigned'),
 "ALTER TABLE old_gold_movements MODIFY COLUMN factory_id SMALLINT UNSIGNED DEFAULT NULL", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(NOT EXISTS(SELECT 1 FROM information_schema.STATISTICS WHERE TABLE_SCHEMA=DATABASE()
 AND TABLE_NAME='old_gold_movements' AND COLUMN_NAME='factory_id' AND SEQ_IN_INDEX=1),
 "ALTER TABLE old_gold_movements ADD INDEX idx_old_gold_movements_factory (factory_id)", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

SET @masgold_sql := IF(NOT EXISTS(SELECT 1 FROM information_schema.REFERENTIAL_CONSTRAINTS WHERE CONSTRAINT_SCHEMA=DATABASE()
 AND CONSTRAINT_NAME='fk_old_gold_movement_factory'),
 "ALTER TABLE old_gold_movements ADD CONSTRAINT fk_old_gold_movement_factory FOREIGN KEY (factory_id) REFERENCES factories(id) ON DELETE RESTRICT", 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

-- ---------------------------------------------------------------- MASTER_DATA_MANAGE (OWNER only)
INSERT INTO permissions(code,name,description)
 VALUES ('MASTER_DATA_MANAGE','Manage Master Data',
         'Allows creating, editing, enabling, disabling and permanently deleting inventory master data.')
 ON DUPLICATE KEY UPDATE name=VALUES(name);
INSERT INTO role_permissions(role_id,permission_id)
 SELECT r.id,p.id FROM roles r JOIN permissions p ON p.code='MASTER_DATA_MANAGE'
 WHERE r.code='OWNER' AND NOT EXISTS
 (SELECT 1 FROM role_permissions rp WHERE rp.role_id=r.id AND rp.permission_id=p.id);

INSERT INTO schema_migrations(version,name) VALUES (29,'Master Data integrity: restrictive history FKs and MASTER_DATA_MANAGE')
 ON DUPLICATE KEY UPDATE name=VALUES(name);
