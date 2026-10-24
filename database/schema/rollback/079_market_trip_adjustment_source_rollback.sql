-- Rollback of 079. Refuse while a no-Scrap adjustment or a later migration depends on the source distinction.
SET @masgold_sql = IF(
 EXISTS(SELECT 1 FROM market_trip_settle_adjust WHERE source_kind <> 'SHOP_SCRAP')
 OR EXISTS(SELECT 1 FROM schema_migrations WHERE version > 79),
 'SELECT * FROM ROLLBACK_079_REFUSED_unsourced_adjustment_exists', 'DO 0');
PREPARE masgold_stmt FROM @masgold_sql; EXECUTE masgold_stmt; DEALLOCATE PREPARE masgold_stmt;

ALTER TABLE market_trip_settle_adjust DROP CHECK chk_mtsa_source_kind;
ALTER TABLE market_trip_settle_adjust DROP COLUMN source_kind;
DELETE FROM schema_migrations WHERE version = 79;
