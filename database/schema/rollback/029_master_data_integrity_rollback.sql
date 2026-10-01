-- Restoring the 028 ON DELETE SET NULL semantics would let a master delete silently erase the identity of
-- existing stock history. Refuse the rollback while ANY such history references master data (or while a
-- later migration exists); only a database with no referencing history may go back.
SET @masgold_sql = IF(
 EXISTS(SELECT 1 FROM inventory_items)
 OR EXISTS(SELECT 1 FROM inventory_reconciliation_cases WHERE observed_factory_id IS NOT NULL
           OR observed_item_type_id IS NOT NULL OR observed_category_id IS NOT NULL)
 OR EXISTS(SELECT 1 FROM old_gold_movements WHERE factory_id IS NOT NULL)
 OR EXISTS(SELECT 1 FROM analytics_segments WHERE factory_id IS NOT NULL OR item_type_id IS NOT NULL
           OR category_id IS NOT NULL)
 OR EXISTS(SELECT 1 FROM item_types WHERE default_category_id IS NOT NULL)
 OR EXISTS(SELECT 1 FROM schema_migrations WHERE version>29),
 'SELECT * FROM ROLLBACK_029_REFUSED_master_data_history_or_later_migration', 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

ALTER TABLE old_gold_movements DROP FOREIGN KEY fk_old_gold_movement_factory;
ALTER TABLE old_gold_movements DROP INDEX idx_old_gold_movements_factory;
ALTER TABLE old_gold_movements MODIFY COLUMN factory_id INT UNSIGNED DEFAULT NULL;

ALTER TABLE item_types DROP FOREIGN KEY fk_item_types_default_category;
ALTER TABLE item_types ADD CONSTRAINT fk_item_types_default_category FOREIGN KEY (default_category_id) REFERENCES item_categories(id) ON DELETE SET NULL;
ALTER TABLE analytics_segments DROP FOREIGN KEY fk_segment_category;
ALTER TABLE analytics_segments ADD CONSTRAINT fk_segment_category FOREIGN KEY (category_id) REFERENCES item_categories(id) ON DELETE SET NULL;
ALTER TABLE analytics_segments DROP FOREIGN KEY fk_segment_item_type;
ALTER TABLE analytics_segments ADD CONSTRAINT fk_segment_item_type FOREIGN KEY (item_type_id) REFERENCES item_types(id) ON DELETE SET NULL;
ALTER TABLE analytics_segments DROP FOREIGN KEY fk_segment_factory;
ALTER TABLE analytics_segments ADD CONSTRAINT fk_segment_factory FOREIGN KEY (factory_id) REFERENCES factories(id) ON DELETE SET NULL;
ALTER TABLE inventory_reconciliation_cases DROP FOREIGN KEY fk_reconciliation_category;
ALTER TABLE inventory_reconciliation_cases ADD CONSTRAINT fk_reconciliation_category FOREIGN KEY (observed_category_id) REFERENCES item_categories(id) ON DELETE SET NULL;
ALTER TABLE inventory_reconciliation_cases DROP FOREIGN KEY fk_reconciliation_item_type;
ALTER TABLE inventory_reconciliation_cases ADD CONSTRAINT fk_reconciliation_item_type FOREIGN KEY (observed_item_type_id) REFERENCES item_types(id) ON DELETE SET NULL;
ALTER TABLE inventory_reconciliation_cases DROP FOREIGN KEY fk_reconciliation_factory;
ALTER TABLE inventory_reconciliation_cases ADD CONSTRAINT fk_reconciliation_factory FOREIGN KEY (observed_factory_id) REFERENCES factories(id) ON DELETE SET NULL;
ALTER TABLE inventory_items DROP FOREIGN KEY fk_inventory_category;
ALTER TABLE inventory_items ADD CONSTRAINT fk_inventory_category FOREIGN KEY (category_id) REFERENCES item_categories(id) ON DELETE SET NULL;
ALTER TABLE inventory_items DROP FOREIGN KEY fk_inventory_factory;
ALTER TABLE inventory_items ADD CONSTRAINT fk_inventory_factory FOREIGN KEY (factory_id) REFERENCES factories(id) ON DELETE SET NULL;

DELETE rp FROM role_permissions rp JOIN permissions p ON p.id=rp.permission_id WHERE p.code='MASTER_DATA_MANAGE';
DELETE FROM permissions WHERE code='MASTER_DATA_MANAGE';
DELETE FROM schema_migrations WHERE version=29;
