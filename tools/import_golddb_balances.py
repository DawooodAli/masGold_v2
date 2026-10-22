"""نقل أرصدة العملاء من البرنامج القديم: ONE opening entry per customer in the NEW production customer ledger, from the old cash book (golddb.cash).

usage:
    python -m tools.import_golddb_balances report
    python -m tools.import_golddb_balances apply prod

* The balance of a customer = the sum of his «متبقي» rows (unpaid remainder, `amount_out`) minus the sum of his «سداد» rows (later payments, `amount_in`) in the old `cash` table; positive = the customer owes the shop, negative = the shop owes the customer (the system's own sign). The owner confirmed this rule (2026-10-22) and will adjust the balances himself afterwards.
* Only customers that exist in the imported customer list get an entry (cash rows whose customer is not in `customerdata` are ignored, owner decision). A zero balance gets no entry. `golddb` is only READ.
* Each entry is one `customer_transactions` row of the existing type LEGACY_ADJUSTMENT («تسوية تاريخية مرحّلة»), with the old customer number as legacy provenance and the two sums in its metadata. No invoice, payment or other history is created.
* Target `masgold_prod` only; refused unless the schema is at the chain's last level, the customer ledger is EMPTY and every customer of the list exists. One transaction: all entries or none."""
import sys
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OLD_DATABASE, TARGET = "golddb", "masgold_prod"
DESCRIPTION = "رصيد من البرنامج القديم (متبقي − سداد)"


def net_balances(rows, known_ids):
    """rows: [(cust_id, owed, paid)] as text sums. Returns {"entries": [{customer_id, owed, paid, balance}], "ignored": n, "zero": n}. Unknown / blank customers are ignored; a zero balance is skipped."""
    entries, ignored, zero = [], 0, 0
    for raw_id, owed, paid in rows:
        text = str(raw_id).strip()
        if not text.isdigit() or int(text) not in known_ids:
            ignored += 1
            continue
        owed, paid = Decimal(str(owed or 0)), Decimal(str(paid or 0))
        balance = owed - paid
        if balance == 0:
            zero += 1
            continue
        entries.append({"customer_id": int(text), "owed": owed, "paid": paid, "balance": balance})
    return {"entries": entries, "ignored": ignored, "zero": zero}


def insert_sql(entries):
    text = "CONVERT(X'" + DESCRIPTION.encode("utf-8").hex() + "' USING utf8mb4)"
    values = ",\n".join(
        f"({e['customer_id']}, (SELECT id FROM customer_transaction_types WHERE code = 'LEGACY_ADJUSTMENT'), {e['balance']:.2f}, {text}, NOW(), 'golddb.cash', {e['customer_id']}, "
        f"JSON_OBJECT('owed', {e['owed']}, 'paid', {e['paid']}, 'rule', 'owed_minus_paid'))" for e in entries)
    return ("SET NAMES utf8mb4;\nSTART TRANSACTION;\nINSERT INTO customer_transactions (customer_id, transaction_type_id, amount_delta, description, occurred_at, legacy_source_table, legacy_source_id, metadata_json) VALUES\n"
            + values + ";\nCOMMIT;\n")


def main(argv):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
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
        old = lambda sql: client.query(sql, database=OLD_DATABASE)
        known = {int(r[0]) for r in old("SELECT id FROM customerdata;")}
        sums = old("SELECT cust_id, SUM(CASE WHEN cashAction = 'متبقي' THEN amount_out ELSE 0 END), SUM(CASE WHEN cashAction = 'سداد' THEN amount_in ELSE 0 END) "
                   "FROM cash WHERE cust_id IS NOT NULL AND TRIM(cust_id) <> '' AND cust_id <> '0' GROUP BY cust_id;")
        result = net_balances(sums, known)
        owing = [e for e in result["entries"] if e["balance"] > 0]
        credit = [e for e in result["entries"] if e["balance"] < 0]
        print({"entries": len(result["entries"]), "owe_the_shop": len(owing), "shop_owes": len(credit), "zero": result["zero"], "ignored_unknown_customers": result["ignored"]})
        print(f"total owed to the shop: {sum((e['balance'] for e in owing), Decimal(0)):,.2f}   total owed to customers: {sum((-e['balance'] for e in credit), Decimal(0)):,.2f}")
        if mode == "report":
            return 0
        last = int(chain_module.load_chain(require_committed=False)[-1].name[:3])
        level = int(client.query("SELECT MAX(version) FROM schema_migrations;", database=target)[0][0])
        ledger = int(client.query("SELECT COUNT(*) FROM customer_transactions;", database=target)[0][0])
        present = {int(r[0]) for r in client.query("SELECT id FROM customers;", database=target)}
        missing = [e["customer_id"] for e in result["entries"] if e["customer_id"] not in present]
        if level != last or ledger != 0 or missing:
            print(f"refused: the target must be at level {last}, with an empty customer ledger and every customer present (level {level}, ledger rows {ledger}, missing customers {missing[:5]}); nothing was written")
            return 2
        client.run_script(insert_sql(result["entries"]), database=target)
        stats = client.query("SELECT COUNT(*), SUM(balance > 0), SUM(balance < 0), SUM(CASE WHEN balance > 0 THEN balance ELSE 0 END), SUM(CASE WHEN balance < 0 THEN -balance ELSE 0 END) FROM vw_customer_balances WHERE balance <> 0;",
                             database=target)[0]
        print(f"OK: customers with a balance now: {stats[0]} (owe the shop {stats[1]} = {stats[3]}, the shop owes {stats[2]} = {stats[4]})")
        expected_total = sum((e["balance"] for e in result["entries"]), Decimal(0))
        actual_total = Decimal(client.query("SELECT COALESCE(SUM(balance), 0) FROM vw_customer_balances;", database=target)[0][0])
        return 0 if int(stats[0]) == len(result["entries"]) and actual_total == expected_total else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
