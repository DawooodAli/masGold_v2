"""نقل البيانات الأساسية من البرنامج القديم: the factories, the item types and the employees of golddb copied into the NEW production database, as they are.

usage:
    python -m tools.import_golddb_master report
    python -m tools.import_golddb_master apply prod

* `golddb` is only READ. The target is `masgold_prod` only (administrator credentials of the application's .env), ONLY when `factories`, `item_types` and `employees` are all empty and the schema is at the chain's last level. Insert-only, one transaction.
* factories: code = the old symbol, name = the old definition (surrounding blanks trimmed). `factories.code` must be unique: the SECOND factory that uses an already used symbol gets NO code (never an invented one); it is reported.
* item types: the old names, no code, no default category. employees: `EMP001`, `EMP002`... in the order of the old employee id; the old shop's «علي» is written as in the old table.
* Nothing else is created (no users, no stock, no balances)."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OLD_DATABASE, TARGET = "golddb", "masgold_prod"


def _text(value):
    return "" if value is None or str(value) == "NULL" else str(value).strip()


def clean_master(factories, item_types, employees):
    """factories: [(id, symb, def)]; item_types: [(id, item)]; employees: [(id, name)] (text rows). Returns the rows to insert plus the problems and the factories left without a code."""
    problems, no_code, seen_codes, seen_names = [], [], set(), set()
    out_factories, out_types, out_employees = [], [], []
    for raw_id, symb, name in factories:
        name, code = _text(name), _text(symb)
        if not name:
            problems.append(("factory", raw_id, "blank name"))
            continue
        if code and code.upper() in seen_codes:
            no_code.append((raw_id, name, code))
            code = ""
        elif code:
            seen_codes.add(code.upper())
        out_factories.append({"code": code or None, "name": name})
    for raw_id, name in item_types:
        name = _text(name)
        if not name:
            problems.append(("item type", raw_id, "blank name"))
        elif name in seen_names:
            problems.append(("item type", raw_id, "duplicate name"))
        else:
            seen_names.add(name)
            out_types.append({"name": name})
    for number, (raw_id, name) in enumerate(sorted(employees, key=lambda r: int(r[0])), 1):
        name = _text(name)
        if not name:
            problems.append(("employee", raw_id, "blank name"))
        else:
            out_employees.append({"code": f"EMP{number:03d}", "name": name})
    return {"factories": out_factories, "item_types": out_types, "employees": out_employees, "problems": problems, "factories_without_code": no_code}


def _utf8(text):
    return "CONVERT(X'" + text.encode("utf-8").hex() + "' USING utf8mb4)"


def insert_sql(data):
    parts = ["SET NAMES utf8mb4;", "START TRANSACTION;"]
    if data["factories"]:
        parts.append("INSERT INTO factories (code, name) VALUES\n" + ",\n".join(
            f"({_utf8(f['code']) if f['code'] else 'NULL'}, {_utf8(f['name'])})" for f in data["factories"]) + ";")
    if data["item_types"]:
        parts.append("INSERT INTO item_types (name) VALUES\n" + ",\n".join(f"({_utf8(t['name'])})" for t in data["item_types"]) + ";")
    if data["employees"]:
        parts.append("INSERT INTO employees (employee_code, name) VALUES\n" + ",\n".join(f"('{e['code']}', {_utf8(e['name'])})" for e in data["employees"]) + ";")
    parts.append("COMMIT;")
    return "\n".join(parts) + "\n"


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
        data = clean_master(old("SELECT idfactory, symb, def FROM factory ORDER BY idfactory;"), old("SELECT id, item FROM store ORDER BY id;"),
                            old("SELECT idemployee, name FROM employee ORDER BY idemployee;"))
        print({k: len(data[k]) for k in ("factories", "item_types", "employees", "problems", "factories_without_code")})
        if data["factories_without_code"]:
            print("factories left without a code (their symbol is already used):", data["factories_without_code"])
        if data["problems"]:
            print("problems:", data["problems"])
        if mode == "report":
            return 0 if not data["problems"] else 1
        if data["problems"]:
            print("refused: fix the problems first (nothing was written)")
            return 1
        last = int(chain_module.load_chain(require_committed=False)[-1].name[:3])
        level = int(client.query("SELECT MAX(version) FROM schema_migrations;", database=target)[0][0])
        counts = {t: int(client.query(f"SELECT COUNT(*) FROM {t};", database=target)[0][0]) for t in ("factories", "item_types", "employees")}
        if level != last or any(counts.values()):
            print(f"refused: the target must be at level {last} with empty factories / item types / employees (level {level}, {counts}); nothing was written")
            return 2
        client.run_script(insert_sql(data), database=target)
        after = {t: int(client.query(f"SELECT COUNT(*) FROM {t};", database=target)[0][0]) for t in ("factories", "item_types", "employees")}
        print("OK:", after)
        expected = {"factories": len(data["factories"]), "item_types": len(data["item_types"]), "employees": len(data["employees"])}
        return 0 if after == expected else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
