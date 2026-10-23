"""ربط سلفة قديمة بموظف: links an OLD manual «سلف موظفين» withdrawal (recorded before migration 074, no employee) to its employee, so it shows as an open advance.

usage:
    python -m tools.attach_advance report prod
    python -m tools.attach_advance apply prod F0000001 شلاطة

* Target: `masgold_prod` or a `masgold_import_*` copy only (`tools/import_target.py`). The schema must be at level 74 or later.
* `report` lists the manual withdrawals of category STAFF_ADVANCE that have no advance yet and the active employees. `apply` links ONE operation (its code, as shown in the treasury) to ONE employee (exact name).
* Insert-only: one `cash_advances` row with the operation's own amount; the withdrawal itself and the cash ledger are never touched. Refused for a reversed operation, an operation already linked, or an unknown / ambiguous name."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _quote(text):
    return "'" + str(text).replace("\\", "\\\\").replace("'", "''") + "'"


def main(argv):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass
    mode = argv[0] if argv else ""
    from tools.import_target import resolve
    words = argv[1:2] if mode == "report" else argv[1:2] if mode == "apply" else []
    target = resolve(words)
    if mode not in ("report", "apply") or target is None or (mode == "apply" and len(argv) != 4):
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
        query = lambda sql: client.query(sql, database=target)
        if int(query("SELECT MAX(version) FROM schema_migrations;")[0][0]) < 74:
            print("refused: apply migration 074 first (the database is below level 74)")
            return 2
        open_rows = query(
            "SELECT fo.id, fo.amount, fo.occurred_at, COALESCE(fo.reason, '') FROM financial_operations fo "
            "WHERE fo.operation_type = 'MANUAL_WITHDRAWAL' AND fo.category = 'STAFF_ADVANCE' "
            "AND NOT EXISTS (SELECT 1 FROM cash_advances a WHERE a.operation_id = fo.id) "
            "AND NOT EXISTS (SELECT 1 FROM transaction_corrections k WHERE k.source_type = 'FINANCIAL_OPERATION' AND k.source_id = fo.id AND k.correction_type = 'FULL_REVERSAL') "
            "ORDER BY fo.id;")
        for row in open_rows:
            print(f"F{int(row[0]):07d}", row[1], row[2], row[3])
        if mode == "report":
            print("employees:", [r[0] for r in query("SELECT name FROM employees WHERE is_active = TRUE ORDER BY name;")])
            return 0
        code, name = argv[2].strip().upper(), argv[3].strip()
        digits = code[1:] if code.startswith("F") else ""
        if not digits.isdigit() or int(digits) not in {int(r[0]) for r in open_rows}:
            print("refused: that code is not an unlinked, unreversed «سلف موظفين» withdrawal")
            return 1
        found = query(f"SELECT id FROM employees WHERE is_active = TRUE AND name = {_quote(name)};")
        if len(found) != 1:
            print(f"refused: {len(found)} active employees are named exactly like that")
            return 1
        operation = int(digits)
        user = query("SELECT performed_by_user_id FROM financial_operations WHERE id = %d;" % operation)[0][0]
        client.run_script(
            "INSERT INTO cash_advances (kind, employee_id, customer_id, amount, operation_id, note, created_by_user_id, created_at) "
            f"SELECT 'EMPLOYEE', {int(found[0][0])}, NULL, amount, id, reason, {int(user)}, NOW() FROM financial_operations WHERE id = {operation};",
            database=target)
        print("OK: linked", f"F{operation:07d}", "to", name)
        return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
