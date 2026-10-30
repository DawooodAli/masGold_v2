"""إعطاء الفئة B للقطع التي بدون حرف (فئة): pieces of any status whose `category_id` is empty (the old shop's data has no category letters).

usage:
    python -m tools.set_missing_category report prod
    python -m tools.set_missing_category apply prod

* Target: `masgold_prod` or a `masgold_import_*` copy only (`tools/import_target.py`); take a backup before `apply` (the daily backup is enough if it ran today).
* ONE UPDATE in one transaction: `inventory_items.category_id` becomes the active category with code B, only where it is NULL. Weight, karat, status, barcode, invoices and movements are not touched.
* ONE audit row `INVENTORY_CATEGORY_MISSING_SET` keeps the count, the category and every piece id (the old value was empty for all of them), so the change can be reversed from the record.
* The cost of a piece stays what it was stored with; the making-profit report estimates a piece with no stored cost from the CURRENT cost of its category (letter B from now on)."""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MISSING = "SELECT i.id FROM inventory_items i WHERE i.category_id IS NULL ORDER BY i.id;"


def main(argv):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass
    mode = argv[0] if argv else ""
    sys.path.insert(0, str(ROOT))
    from tools.import_target import resolve
    target = resolve(argv[1:2])
    if mode not in ("report", "apply") or target is None or len(argv) != 2:
        print(__doc__)
        return 2
    from config.database_config import DatabaseConfig
    from tools.schema_rehearsal.mysql_client import DEFAULT_MYSQL, Credentials, MysqlClient, option_file
    cfg = DatabaseConfig.from_env()
    if cfg.host not in ("localhost", "127.0.0.1"):
        print("refused: the database server is not this machine")
        return 2
    with option_file(Credentials("localhost", cfg.port, cfg.user, cfg.password)) as options:
        client = MysqlClient(DEFAULT_MYSQL, options)
        category = client.query("SELECT id FROM item_categories WHERE code = 'B' AND is_active = TRUE;", database=target)
        if len(category) != 1:
            print("refused: there is no single active category B")
            return 1
        category_id = int(category[0][0])
        ids = [int(r[0]) for r in client.query(MISSING, database=target)]
        by_status = client.query("SELECT inventory_status, COUNT(*) FROM inventory_items WHERE category_id IS NULL GROUP BY inventory_status;", database=target)
        print(f"pieces without a category: {len(ids)}", {r[0]: int(r[1]) for r in by_status}, ids[:20])
        if mode == "report" or not ids:
            return 0
        metadata = json.dumps({"category_code": "B", "category_id": category_id, "count": len(ids), "item_ids": ids, "old_category": None}, ensure_ascii=False).replace("'", "''")
        user = client.query("SELECT id FROM users WHERE username = 'admin' LIMIT 1;", database=target)
        user_sql = str(int(user[0][0])) if user else "NULL"
        script = ["START TRANSACTION;",
                  f"UPDATE inventory_items SET category_id = {category_id} WHERE category_id IS NULL;",
                  "INSERT INTO audit_log (user_id, event_type, entity_type, entity_id, entity_reference, description, metadata_json, occurred_at) "
                  f"VALUES ({user_sql}, 'INVENTORY_CATEGORY_MISSING_SET', 'INVENTORY_ITEM', {ids[0]}, 'B', 'فئة B للقطع التي بدون حرف ({len(ids)} قطعة)', '{metadata}', NOW());",
                  "COMMIT;"]
        client.run_script("\n".join(script), database=target)
        left = len(client.query(MISSING, database=target))
        print("OK: left without category:", left)
        return 0 if left == 0 else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
