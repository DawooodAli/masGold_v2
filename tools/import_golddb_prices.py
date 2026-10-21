"""One-time import of the OLD shop's gold price history into `gold_price_history_legacy` (migration 066).

    python -m tools.import_golddb_prices report <output-dir>
        READ ONLY. Reads the old price table (SELECT only), applies the owner-approved cleaning rules (services/price_history_rules.py) and writes three CSV files for review:
        accepted-daily.csv, rejected.csv and swapped.csv. Nothing is written to any database.
    python -m tools.import_golddb_prices apply rehearsal <output-dir>
    python -m tools.import_golddb_prices apply dev <output-dir>
        Writes the cleaned daily rows into the target (`rehearsal` = masgold_rehearsal through the dedicated account and MASGOLD_REHEARSAL_PASSWORD, `dev` = masgold_v2 through the
        application configuration). REFUSES when the target's level is below 66 or the history table already holds rows (it is imported once, insert-only). The old database is only read.

The password is never printed. The old database is never written."""
import csv
import os
import sys
from pathlib import Path

sys.path.insert(0, os.getcwd())
from config.database_config import DatabaseConfig
from services.price_history_rules import clean_prices
from tools.schema_rehearsal.guards import validate_target
from tools.schema_rehearsal.mysql_client import DEFAULT_MYSQL, Credentials, MysqlClient, option_file

OLD_DATABASE = "golddb"
KARAT = 21


def read_old_prices(client):
    rows = client.query("SELECT id, low, high, date FROM goldprice ORDER BY date, id", database=OLD_DATABASE)
    from datetime import date
    return [{"id": int(r[0]), "low": r[1], "high": r[2], "date": date.fromisoformat(r[3])} for r in rows]


def write_reports(result, out):
    out.mkdir(parents=True, exist_ok=True)
    with open(out / "accepted-daily.csv", "w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.writer(handle)
        writer.writerow(["date", "shop_buy (low)", "shop_sell (high)", "entries_in_day", "swapped"])
        for d in result["daily"]:
            writer.writerow([d["date"].isoformat(), d["shop_buy"], d["shop_sell"], d["entries"], int(d["swapped"])])
    with open(out / "rejected.csv", "w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.writer(handle)
        writer.writerow(["id", "date", "low", "high", "reason"])
        for r in result["rejected"]:
            writer.writerow([r["id"], r["date"].isoformat(), r["low"], r["high"], r["reason"]])
    with open(out / "swapped.csv", "w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.writer(handle)
        writer.writerow(["id", "date", "original low", "original high"])
        for r in result["swapped"]:
            writer.writerow([r["id"], r["date"].isoformat(), r["low"], r["high"]])


def insert_sql(daily):
    values = ",\n".join(f"('{d['date'].isoformat()}', {KARAT}, {d['shop_buy']}, {d['shop_sell']}, 'GOLDDB', {d['entries']}, {int(d['swapped'])})" for d in daily)
    return ("INSERT INTO gold_price_history_legacy (price_date, karat_value, shop_buy, shop_sell, source, entries_in_day, swapped) VALUES\n" + values + ";\n")


def main(argv):
    mode = argv[0] if argv else ""
    if mode not in ("report", "apply"):
        print(__doc__)
        return 2
    cfg = DatabaseConfig.from_env()
    old_credentials = Credentials("localhost", cfg.port, cfg.user, cfg.password)
    with option_file(old_credentials) as options:
        rows = read_old_prices(MysqlClient(DEFAULT_MYSQL, options))
    result = clean_prices(rows)
    print(result["counts"])
    if mode == "report":
        write_reports(result, Path(argv[1]))
        print("reports written to", argv[1])
        return 0
    target_name, out = argv[1], Path(argv[2])
    write_reports(result, out)
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
        if level < 66:
            print(f"REFUSED: {database} is at level {level}, the history table needs 66")
            return 3
        existing = int(client.query("SELECT COUNT(*) FROM gold_price_history_legacy", database=database)[0][0])
        if existing:
            print(f"REFUSED: the history already holds {existing} rows (it is imported once)")
            return 3
        client.run_script(insert_sql(result["daily"]) + "SELECT CONCAT('SENT:', DATABASE());\n", database=database)
        stored = client.query("SELECT COUNT(*), MIN(price_date), MAX(price_date) FROM gold_price_history_legacy", database=database)[0]
        print("imported into", database, stored)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
