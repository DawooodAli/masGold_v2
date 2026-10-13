-- No receipt history, including reversed receipts, may be erased.
SET @masgold_sql=IF(EXISTS(SELECT 1 FROM holding_receipts)
 OR EXISTS(SELECT 1 FROM receipt_pieces)
 OR EXISTS(SELECT 1 FROM receipt_stock_movements)
 OR EXISTS(SELECT 1 FROM intermediary_holding_movements WHERE receipt_id IS NOT NULL)
 OR EXISTS(SELECT 1 FROM transaction_corrections WHERE source_type='HOLDING_RECEIPT')
 OR EXISTS(SELECT 1 FROM schema_migrations WHERE version>51),
 'SELECT * FROM ROLLBACK_051_REFUSED_history_or_later_migration','DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;
ALTER TABLE intermediary_holding_movements DROP FOREIGN KEY fk_ihm_receipt;
ALTER TABLE intermediary_holding_movements DROP INDEX idx_ihm_receipt;
ALTER TABLE intermediary_holding_movements DROP INDEX uq_ihm_receipt_type;
ALTER TABLE intermediary_holding_movements DROP CHECK chk_ihm_shape;
ALTER TABLE intermediary_holding_movements DROP COLUMN receipt_id;
ALTER TABLE intermediary_holding_movements ADD CONSTRAINT chk_ihm_shape CHECK (weight_grams>0 AND ((document_id IS NOT NULL AND settlement_id IS NULL AND movement_type='HANDOVER' AND doc_kind='HANDOVER' AND weight_delta=weight_grams AND correction_id IS NULL) OR (document_id IS NOT NULL AND settlement_id IS NULL AND movement_type='RETURN' AND doc_kind='RETURN' AND weight_delta=-weight_grams AND correction_id IS NULL) OR (document_id IS NOT NULL AND settlement_id IS NULL AND movement_type='HANDOVER_REVERSAL' AND doc_kind='HANDOVER' AND weight_delta=-weight_grams AND correction_id IS NOT NULL) OR (document_id IS NOT NULL AND settlement_id IS NULL AND movement_type='RETURN_REVERSAL' AND doc_kind='RETURN' AND weight_delta=weight_grams AND correction_id IS NOT NULL) OR (movement_type='SETTLEMENT' AND document_id IS NULL AND doc_kind IS NULL AND settlement_id IS NOT NULL AND weight_delta=-weight_grams AND correction_id IS NULL) OR (movement_type='SETTLEMENT_REVERSAL' AND document_id IS NULL AND doc_kind IS NULL AND settlement_id IS NOT NULL AND weight_delta=weight_grams AND correction_id IS NOT NULL)));
DROP TABLE receipt_stock_movements;
DROP TABLE receipt_pieces;
DROP TABLE holding_receipts;
ALTER TABLE intermediary_holding_pools DROP INDEX uq_ihp_receipt;
ALTER TABLE transaction_corrections DROP CHECK chk_correction_source;
ALTER TABLE transaction_corrections ADD CONSTRAINT chk_correction_source CHECK ((source_type IN ('INVOICE','EXCHANGE','RETURN') AND invoice_id IS NOT NULL) OR (source_type='CUSTOMER_ACCOUNT_SETTLEMENT' AND invoice_id IS NULL) OR (source_type IN ('FINANCIAL_OPERATION','CUSTOMER_DEPOSIT','SUPPLIER_MAKING_PAYMENT','MARKET_TRIP_ENTRY','SUPPLIER_GOLD_SETTLEMENT') AND invoice_id IS NULL AND correction_type='FULL_REVERSAL'));
DELETE FROM schema_migrations WHERE version=51;
