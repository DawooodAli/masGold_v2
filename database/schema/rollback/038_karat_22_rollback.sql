-- Refuse rollback once any derived quote carries provenance, or any 22K reference (supplier, invoice, stock, Scrap, price quote,
-- custody) exists, or a later migration exists. The 22K master row is reference data: it is never deleted or deactivated here
-- (a reactivation cannot be told apart from an insert afterwards), only the provenance column is removed.
SET @masgold_sql = IF(
 EXISTS(SELECT 1 FROM gold_price_quotes WHERE derived_from_quote_id IS NOT NULL)
 OR EXISTS(SELECT 1 FROM karats k JOIN gold_price_quotes q ON q.karat_id = k.id WHERE k.karat_value = 22)
 OR EXISTS(SELECT 1 FROM karats k JOIN suppliers s ON s.karat_id = k.id WHERE k.karat_value = 22)
 OR EXISTS(SELECT 1 FROM karats k JOIN supplier_purchase_invoices i ON i.karat_id = k.id WHERE k.karat_value = 22)
 OR EXISTS(SELECT 1 FROM karats k JOIN supplier_gold_movements m ON m.karat_id = k.id WHERE k.karat_value = 22)
 OR EXISTS(SELECT 1 FROM karats k JOIN inventory_items i ON i.karat_id = k.id WHERE k.karat_value = 22)
 OR EXISTS(SELECT 1 FROM karats k JOIN old_gold_movements m ON m.karat_id = k.id WHERE k.karat_value = 22)
 OR EXISTS(SELECT 1 FROM karats k JOIN invoice_price_snapshots p ON p.karat_id = k.id WHERE k.karat_value = 22)
 OR EXISTS(SELECT 1 FROM karats k JOIN market_trip_entry_gold g ON g.karat_id = k.id WHERE k.karat_value = 22)
 OR EXISTS(SELECT 1 FROM schema_migrations WHERE version>38),
 'SELECT * FROM ROLLBACK_038_REFUSED_22k_data_provenance_or_later_migration', 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;
ALTER TABLE gold_price_quotes DROP CHECK chk_gold_quote_derived;
ALTER TABLE gold_price_quotes DROP FOREIGN KEY fk_gold_quotes_derived;
ALTER TABLE gold_price_quotes DROP KEY idx_gold_quotes_derived;
ALTER TABLE gold_price_quotes DROP COLUMN derived_from_quote_id;
DELETE FROM schema_migrations WHERE version=38;
