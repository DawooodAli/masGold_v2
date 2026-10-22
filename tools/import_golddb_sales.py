"""One-time import of the OLD shop's sales history into `sales_history_legacy` (migration 071).

    python -m tools.import_golddb_sales report <output-dir>
        READ ONLY. Reads the old bills table (SELECT only), applies the mechanical cleaning rules (services/sales_history_rules.py) and writes daily.csv for review.
        Nothing is written to any database.
    python -m tools.import_golddb_sales apply rehearsal <output-dir>
    python -m tools.import_golddb_sales apply dev <output-dir>
        Writes the cleaned DAILY rows into the target (`rehearsal` = masgold_rehearsal through the dedicated account and MASGOLD_REHEARSAL_PASSWORD, `dev` = masgold_v2 through the application
        configuration). REFUSES when the target's level is below 71 or the history table already holds rows (it is imported once, insert-only). The old database is only read.

The password is never printed. The old database is never written."""
import csv
import os
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, os.getcwd())
from config.database_config import DatabaseConfig
from services.sales_history_rules import clean_sales
from tools.schema_rehearsal.guards import validate_target
from tools.schema_rehearsal.mysql_client import DEFAULT_MYSQL, Credentials, MysqlClient, option_file

OLD_DATABASE = "golddb"


def read_old_sales(client):
    rows = client.query("SELECT id, bill_id, date, weight FROM employeebill ORDER BY id", database=OLD_DATABASE)
    return [{"id": int(r[0]), "bill_id": r[1], "date": date.fromisoformat(r[2]), "weight": None if r[3] in ("NULL", "") else r[3]} for r in rows]


def write_report(result, out):
    out.mkdir(parents=True, exist_ok=True)
    with open(out / "daily.csv", "w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.writer(handle)
        writer.writerow(["date", "bills", "bills_with_weight", "grams"])
        for d in result["daily"]:
            writer.writerow([d["date"].isoformat(), d["bills"], d["bills_with_weight"], d["grams"]])


def insert_sql(daily):
    values = ",\n".join(f"('{d['date'].isoformat()}', {d['bills']}, {d['bills_with_weight']}, {d['grams']}, 'GOLDDB')" for d in daily)
    return "INSERT INTO sales_history_legacy (sale_date, bills, bills_with_weight, grams, source) VALUES\n" + values + ";\n"


def main(argv):
    mode = argv[0] if argv else ""
    if mode not in ("report", "apply"):
        print(__doc__)
        return 2
    cfg = DatabaseConfig.from_env()
    old_credentials = Credentials("localhost", cfg.port, cfg.user, cfg.password)
    with option_file(old_credentials) as options:
        rows = read_old_sales(MysqlClient(DEFAULT_MYSQL, options))
    result = clean_sales(rows)
    print(result["counts"])
    if mode == "report":
        write_report(result, Path(argv[1]))
        print("report written to", argv[1])
        return 0
    target_name, out = argv[1], Path(argv[2])
    write_report(result, out)
    if target_name == "rehearsal":
        database, credentials = validate_target("masgold_rehearsal"), Credentials("localhost", 3306, "masgold_build", os.environ["MASGOLD_REHEARSAL_PASSWORD"])
    elif target_name == "dev":
        assert cfg.database == "masgold_v2", "configured database is not masgold_v2"
        database, credentials = "masgold_v2", old_credentials
    else:
        print("target must be rehearsal or dev")
        return 2
    with option_file(credentials) as options:
        client = MysqlClient(DEFAULT_MYSQL, options)
        assert client.query("SELECT DATABASE()", database=database)[0][0] == database
        level = int(client.query("SELECT MAX(version) FROM schema_migrations", database=database)[0][0])
        if level < 71:
            print(f"REFUSED: {database} is at level {level}, the history table needs 71")
            return 3
        existing = int(client.query("SELECT COUNT(*) FROM sales_history_legacy", database=database)[0][0])
        if existing:
            print(f"REFUSED: the history already holds {existing} rows (it is imported once)")
            return 3
        client.run_script(insert_sql(result["daily"]) + "SELECT CONCAT('SENT:', DATABASE());\n", database=database)
        stored = client.query("SELECT COUNT(*), MIN(sale_date), MAX(sale_date), SUM(grams) FROM sales_history_legacy", database=database)[0]
        print("imported into", database, stored)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
