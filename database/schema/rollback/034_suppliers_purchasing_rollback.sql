-- Refuse rollback once any supplier data exists. Never erase ledger or traceability history.
SET @masgold_sql = IF(
 EXISTS(SELECT 1 FROM suppliers)
 OR EXISTS(SELECT 1 FROM supplier_purchase_invoices)
 OR EXISTS(SELECT 1 FROM supplier_invoice_groups)
 OR EXISTS(SELECT 1 FROM supplier_gold_movements)
 OR EXISTS(SELECT 1 FROM supplier_making_movements)
 OR EXISTS(SELECT 1 FROM supplier_invoice_pieces)
 OR EXISTS(SELECT 1 FROM inventory_items WHERE acquisition_source='SUPPLIER_PURCHASE')
 OR EXISTS(SELECT 1 FROM schema_migrations WHERE version>34),
 'SELECT * FROM ROLLBACK_034_REFUSED_supplier_data_or_later_migration', 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;
ALTER TABLE inventory_items DROP CHECK chk_inventory_acquisition_source;
ALTER TABLE inventory_items MODIFY COLUMN acquisition_source ENUM('MARKET_PURCHASE','OLD_GOLD_MANUFACTURE','OLD_STOCK_CONVERSION','LEGACY_MIGRATION','MANUAL_ENTRY') NOT NULL;
ALTER TABLE inventory_items ADD CONSTRAINT chk_inventory_acquisition_source CHECK (
 acquisition_source IN ('MARKET_PURCHASE','OLD_GOLD_MANUFACTURE','OLD_STOCK_CONVERSION','LEGACY_MIGRATION','MANUAL_ENTRY'));
DROP TABLE supplier_invoice_pieces;
DROP TABLE supplier_making_movements;
DROP TABLE supplier_gold_movements;
DROP TABLE supplier_making_movement_types;
DROP TABLE supplier_gold_movement_types;
DROP TABLE supplier_invoice_groups;
DROP TABLE supplier_purchase_invoices;
DROP TABLE suppliers;
DELETE rp FROM role_permissions rp JOIN permissions p ON p.id=rp.permission_id
 WHERE p.code IN ('SUPPLIER_VIEW','SUPPLIER_MANAGE','SUPPLIER_INVOICE_EDIT','SUPPLIER_INVOICE_POST','SUPPLIER_INTAKE');
DELETE FROM permissions
 WHERE code IN ('SUPPLIER_VIEW','SUPPLIER_MANAGE','SUPPLIER_INVOICE_EDIT','SUPPLIER_INVOICE_POST','SUPPLIER_INTAKE');
DELETE FROM schema_migrations WHERE version=34;
