"""إعطاء باركود تلقائي للقطع الموجودة في المخزون بدون باركود (قطع أُدخلت قبل أن يصبح الباركود إلزاميًا).

usage:
    python -m tools.assign_missing_barcodes report prod
    python -m tools.assign_missing_barcodes apply prod

* Target: `masgold_prod` or a `masgold_import_*` copy only (`tools/import_target.py`); take a backup before `apply`.
* Pieces that are IN_STOCK (or returned and waiting for inspection) and have NO active barcode get the next canonical NNYYMMDD value of TODAY (`barcode_number_sequences`, the same allocation as the
  application: a stored barcode is never issued again). Insert-only: one `inventory_barcodes` row per piece with the reason 'Automatic barcode (assigned later)'; nothing else is touched."""
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MISSING = ("SELECT i.id FROM inventory_items i WHERE i.inventory_status IN ('IN_STOCK', 'RETURNED_PENDING') "
           "AND NOT EXISTS (SELECT 1 FROM inventory_barcodes b WHERE b.inventory_item_id = i.id AND b.barcode_status = 'ACTIVE') ORDER BY i.id;")


def main(argv):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass
    mode = argv[0] if argv else ""
    from tools.import_target import resolve
    target = resolve(argv[1:2])
    if mode not in ("report", "apply") or target is None or len(argv) != 2:
        print(__doc__)
        return 2
    sys.path.insert(0, str(ROOT))
    from config.database_config import DatabaseConfig
    from tools.schema_rehearsal.mysql_client import DEFAULT_MYSQL, Credentials, MysqlClient, option_file
    cfg = DatabaseConfig.from_env()
    if cfg.host not in ("localhost", "127.0.0.1"):
        print("refused: the database server is not this machine")
        return 2
    with option_file(Credentials("localhost", cfg.port, cfg.user, cfg.password)) as options:
        client = MysqlClient(DEFAULT_MYSQL, options)
        ids = [int(r[0]) for r in client.query(MISSING, database=target)]
        print(f"pieces without an active barcode: {len(ids)}", ids[:30])
        if mode == "report" or not ids:
            return 0
        today = date.today()
        part = today.strftime("%y%m%d")
        # the day's allocation, exactly like BarcodeRepository.allocate_barcode_value (seed from the day's stored numeric barcodes, never reuse a value), in ONE transaction
        script = [
            "START TRANSACTION;",
            f"INSERT INTO barcode_number_sequences (sequence_date, last_seq) SELECT '{today.isoformat()}', COALESCE(MAX(CAST(LEFT(barcode_value, CHAR_LENGTH(barcode_value) - 6) AS UNSIGNED)), 0) "
            f"FROM inventory_barcodes WHERE barcode_value REGEXP '^[0-9]{{8,9}}$' AND RIGHT(barcode_value, 6) = '{part}' ON DUPLICATE KEY UPDATE sequence_date = sequence_date;",
            f"SELECT last_seq INTO @seq FROM barcode_number_sequences WHERE sequence_date = '{today.isoformat()}' FOR UPDATE;"]
        for item in ids:
            script += ["SET @seq = @seq + 1;",
                       f"INSERT INTO inventory_barcodes (inventory_item_id, barcode_value, barcode_status, reason) VALUES ({item}, CONCAT(LPAD(@seq, 2, '0'), '{part}'), 'ACTIVE', 'Automatic barcode (assigned later)');"]
        script += [f"UPDATE barcode_number_sequences SET last_seq = @seq WHERE sequence_date = '{today.isoformat()}';", "COMMIT;"]
        client.run_script("\n".join(script), database=target)
        left = len(client.query(MISSING, database=target))
        print("OK: left without barcode:", left)
        return 0 if left == 0 else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
