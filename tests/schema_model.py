"""Static model of the schema a fresh build of the numbered chain produces.

Replays CREATE TABLE / ALTER TABLE / CREATE INDEX statements from
database/schema/NNN_*.sql in order (guarded DDL is assumed to run, as on an
empty database). No MySQL server is used; tests/migration_sql.py parses.
"""
import re

from tests import migration_sql


_TYPE = re.compile(r"^(BIGINT|INT|INTEGER|SMALLINT|TINYINT|MEDIUMINT|DECIMAL|VARCHAR|CHAR|DATETIME|DATE|TIME|"
                   r"TIMESTAMP|TEXT|MEDIUMTEXT|LONGTEXT|JSON|BOOLEAN|BOOL|ENUM|DOUBLE|FLOAT)"
                   r"(\s*\([^)]*\))?(\s+UNSIGNED)?", re.I)


def chain_files():
    """Numbered chain files in order: 001_*.sql, 002_*.sql, ..."""
    return sorted(p.name for p in migration_sql.SCHEMA_DIR.glob("[0-9][0-9][0-9]_*.sql"))


def normalize_type(definition):
    match = _TYPE.match(definition)
    base, args = match.group(1).lower(), (match.group(2) or "").replace(" ", "")
    unsigned = " unsigned" if match.group(3) else ""
    if base in ("boolean", "bool"):
        return "tinyint(1)"
    if base == "integer":
        base = "int"
    if base in ("bigint", "int", "smallint", "mediumint") or (base == "tinyint" and args != "(1)"):
        args = ""
    if base != "enum":
        args = args.lower()
    return base + args + unsigned


def _column(part):
    name, rest = part.split(None, 1)
    upper = rest.upper()
    return name.strip("`"), {
        "type": normalize_type(rest),
        "nullable": not re.search(r"\bNOT NULL\b|\bPRIMARY KEY\b", upper),
    }


def _names(text):
    return [c.strip().strip("`").split("(")[0].strip() for c in migration_sql._split_top_level(text)]


class SchemaModel:
    def __init__(self):
        self.tables, self.views, self.problems = {}, set(), []

    def _item(self, table, part):
        t = self.tables[table]
        fk = re.match(r"CONSTRAINT\s+(\w+)\s+FOREIGN KEY\s*\(([^)]*)\)\s*REFERENCES\s+(\w+)\s*\(([^)]*)\)", part, re.I)
        if fk:
            t["fks"][fk.group(1)] = {"cols": _names(fk.group(2)), "ref": fk.group(3), "refcols": _names(fk.group(4))}
        elif re.match(r"CONSTRAINT\s+\w+\s+CHECK", part, re.I):
            t["checks"].add(re.match(r"CONSTRAINT\s+(\w+)", part, re.I).group(1))
        elif re.match(r"PRIMARY KEY", part, re.I):
            t["indexes"]["PRIMARY"] = {"cols": _names(re.search(r"\((.*)\)$", part).group(1)), "unique": True}
        elif re.match(r"(UNIQUE\s+KEY|UNIQUE\s+INDEX|KEY|INDEX)\s", part, re.I):
            m = re.match(r"(UNIQUE\s+KEY|UNIQUE\s+INDEX|KEY|INDEX)\s+(\w+)\s*\((.*)\)$", part, re.I)
            t["indexes"][m.group(2)] = {"cols": _names(m.group(3)), "unique": m.group(1).upper().startswith("UNIQUE")}
        else:
            name, column = _column(part)
            if name in t["columns"]:
                self.problems.append(f"duplicate column {table}.{name}")
            t["columns"][name] = column
            if re.search(r"\bPRIMARY KEY\b", part, re.I):
                t["indexes"]["PRIMARY"] = {"cols": [name], "unique": True}

    def apply(self, sql, source):
        create = re.match(r"CREATE TABLE (?:IF NOT EXISTS )?(\w+)\s*\(", sql, re.I)
        if create:
            name = create.group(1)
            if name in self.tables:
                return                                   # IF NOT EXISTS on a fresh chain
            body = migration_sql._balanced(sql, create.end() - 1)
            self.tables[name] = {"columns": {}, "indexes": {}, "fks": {}, "checks": set(), "source": source}
            for part in migration_sql._split_top_level(body):
                self._item(name, part.strip())
            return
        alter = re.match(r"ALTER TABLE (\w+)\s+(.*)$", sql, re.I | re.S)
        if alter:
            table = alter.group(1)
            if table not in self.tables:
                self.problems.append(f"{source}: ALTER on missing table {table}")
                return
            t = self.tables[table]
            for clause in migration_sql._split_top_level(alter.group(2)):
                clause = clause.strip()
                if m := re.match(r"ADD COLUMN\s+(.*?)(\s+(AFTER\s+\w+|FIRST))?$", clause, re.I | re.S):
                    self._item(table, m.group(1))
                elif m := re.match(r"MODIFY (?:COLUMN )?(.*)$", clause, re.I | re.S):
                    name, column = _column(m.group(1))
                    if name not in t["columns"]:
                        self.problems.append(f"{source}: MODIFY missing column {table}.{name}")
                    t["columns"][name] = column
                elif m := re.match(r"ADD\s+(.*)$", clause, re.I | re.S):
                    self._item(table, m.group(1))
                elif m := re.match(r"DROP (CHECK|INDEX|FOREIGN KEY|COLUMN)\s+(\w+)$", clause, re.I):
                    kind, name = m.group(1).upper(), m.group(2)
                    bucket = {"CHECK": t["checks"], "INDEX": t["indexes"], "FOREIGN KEY": t["fks"],
                              "COLUMN": t["columns"]}[kind]
                    if name not in bucket:
                        self.problems.append(f"{source}: DROP {kind} missing {table}.{name}")
                    bucket.discard(name) if isinstance(bucket, set) else bucket.pop(name, None)
                else:
                    self.problems.append(f"{source}: unparsed ALTER clause {clause[:60]}")
            return
        index = re.match(r"CREATE (UNIQUE )?INDEX (\w+) ON (\w+)\s*\((.*)\)$", sql, re.I)
        if index:
            self.tables[index.group(3)]["indexes"][index.group(2)] = {
                "cols": _names(index.group(4)), "unique": bool(index.group(1))}
            return
        view = re.match(r"CREATE OR REPLACE VIEW (\w+)", sql, re.I)
        if view:
            self.views.add(view.group(1))
            return
        if not re.match(r"(USE|INSERT|DELETE|SELECT|SET|DO)\b", sql, re.I):
            self.problems.append(f"{source}: unhandled statement {sql[:60]}")


def build(files=None):
    model = SchemaModel()
    for name in files or chain_files():
        for statement in migration_sql.statements(migration_sql.read(name)):
            model.apply(statement.sql, name)
    return model
