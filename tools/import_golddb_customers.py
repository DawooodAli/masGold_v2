"""نقل عملاء البرنامج القديم: copy every customer of the old shop's database (golddb.customerdata) into the NEW production database, as they are.

usage:
    python -m tools.import_golddb_customers report
    python -m tools.import_golddb_customers apply prod

* `golddb` is only READ. The target is `masgold_prod` only, through the administrator credentials already in the application's .env, and ONLY when its `customers` table is empty and the schema is at the chain's last level (insert-only: nothing is updated, deleted or rebuilt).
* The old customer number is KEPT as the new `customers.id` (so the old and new numbers match and later imports can link by it); the code is the system's own `C{id:06d}`. Names are copied with only the surrounding blanks trimmed; a blank place stays empty; the old table has no mobile numbers, so none is invented.
* No balance, ledger entry or invoice is created: customer balances are entered by the owner afterwards. Every imported customer carries a note «مستورد من البرنامج القديم (رقم N)».
* One transaction: either every customer is imported or none."""
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OLD_DATABASE, TARGET = "golddb", "masgold_prod"
MAX_NAME, MAX_PLACE = 150, 150
NOTE = "مستورد من البرنامج القديم (رقم {id})"


def clean_customers(rows):
    """rows: [(id, name, place)] as text. Returns {"customers": [{id, name, place}], "problems": [...], "trimmed": n}. A row with a non-numeric id, a blank name or a too long value is a problem
    (reported, never guessed); ids must be unique."""
    customers, problems, seen, trimmed = [], [], set(), 0
    for raw_id, raw_name, raw_place in rows:
        text_id = str(raw_id).strip()
        if not text_id.isdigit() or int(text_id) <= 0:
            problems.append((raw_id, "bad id"))
            continue
        customer_id = int(text_id)
        name = "" if raw_name is None or str(raw_name) == "NULL" else str(raw_name)
        place = "" if raw_place is None or str(raw_place) == "NULL" else str(raw_place)
        if name != name.strip() or place != place.strip():
            trimmed += 1
        name, place = name.strip(), place.strip()
        if not name:
            problems.append((customer_id, "blank name"))
        elif len(name) > MAX_NAME or len(place) > MAX_PLACE:
            problems.append((customer_id, "value too long"))
        elif customer_id in seen:
            problems.append((customer_id, "duplicate id"))
        else:
            seen.add(customer_id)
            customers.append({"id": customer_id, "name": name, "place": place or None})
    return {"customers": customers, "problems": problems, "trimmed": trimmed}


def _utf8(text):
    return "CONVERT(X'" + text.encode("utf-8").hex() + "' USING utf8mb4)"


def insert_sql(customers):
    """One transaction; every text goes in as a hex literal (no quoting or escaping to get wrong)."""
    values = ",\n".join(
        "({id}, 'C{id:06d}', {name}, NULL, {place}, {note}, 1)".format(
            id=c["id"], name=_utf8(c["name"]), place=_utf8(c["place"]) if c["place"] else "NULL", note=_utf8(NOTE.format(id=c["id"])))
        for c in customers)
    return ("SET NAMES utf8mb4;\nSTART TRANSACTION;\nINSERT INTO customers (id, customer_code, name, mobile, place, notes, is_active) VALUES\n" + values + ";\nCOMMIT;\n")


def main(argv):
    try:
        sys.stdout.reconfigure(encoding="utf-8")                    # Arabic text in the messages (the Windows console default cannot encode it)
    except (AttributeError, ValueError):
        pass
    mode = argv[0] if argv else ""
    from tools.import_target import resolve
    target = resolve(argv[1:]) if mode == "apply" else None
    if mode not in ("report", "apply") or (mode == "apply" and target is None):
        print(__doc__)
        return 2
    sys.path.insert(0, str(ROOT))
    from config.database_config import DatabaseConfig
    from tools.schema_rehearsal import chain as chain_module
    from tools.schema_rehearsal.mysql_client import DEFAULT_MYSQL, Credentials, MysqlClient, option_file
    cfg = DatabaseConfig.from_env()
    if cfg.host not in ("localhost", "127.0.0.1"):
        print("refused: the database server is not this machine")
        return 2
    with option_file(Credentials("localhost", cfg.port, cfg.user, cfg.password)) as options:
        client = MysqlClient(DEFAULT_MYSQL, options)
        rows = [(r[0], r[1], r[2] if len(r) > 2 else "") for r in client.query("SELECT id, name, IFNULL(place, '') FROM customerdata ORDER BY id;", database=OLD_DATABASE)]
        result = clean_customers(rows)
        print({"read": len(rows), "clean": len(result["customers"]), "problems": len(result["problems"]), "trimmed": result["trimmed"]})
        if result["problems"]:
            print("problems:", result["problems"][:10])
        if mode == "report":
            return 0 if not result["problems"] else 1
        if result["problems"]:
            print("refused: fix the problems first (nothing was written)")
            return 1
        last = int(chain_module.load_chain(require_committed=False)[-1].name[:3])
        level = int(client.query("SELECT MAX(version) FROM schema_migrations;", database=target)[0][0])
        existing = int(client.query("SELECT COUNT(*) FROM customers;", database=target)[0][0])
        if level != last or existing != 0:
            print(f"refused: the target must be at level {last} with no customers (level {level}, customers {existing}); nothing was written")
            return 2
        client.run_script(insert_sql(result["customers"]), database=target)
        count = int(client.query("SELECT COUNT(*) FROM customers;", database=target)[0][0])
        top = client.query("SELECT MAX(id), MIN(customer_code), MAX(customer_code) FROM customers;", database=target)[0]
        auto = client.query("SELECT AUTO_INCREMENT FROM information_schema.TABLES WHERE TABLE_SCHEMA = '" + target + "' AND TABLE_NAME = 'customers';")[0][0]
        print(f"OK: {count} customers imported into {target} (highest id {top[0]}, codes {top[1]} .. {top[2]}, next new id {auto})")
        return 0 if count == len(result["customers"]) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
