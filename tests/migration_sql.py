"""Read-only parsing of the masGold schema SQL files for tests.

Nothing here connects to MySQL. Statements are split with quote and comment
awareness; guarded DDL (``SET @masgold_sql := IF(<guard>, "<ddl>", 'DO 0')``)
is unwrapped so tests can inspect both the guard and the statement it runs.
"""
from dataclasses import dataclass
from pathlib import Path
import re


SCHEMA_DIR = Path(__file__).resolve().parent.parent / "database" / "schema"
ROLLBACK_DIR = SCHEMA_DIR / "rollback"


@dataclass(frozen=True)
class Statement:
    sql: str            # the statement that takes effect (inner DDL when guarded)
    guard: str | None   # the IF(...) condition text, None for a direct statement

    @property
    def guarded(self):
        return self.guard is not None


def read(name, directory=SCHEMA_DIR):
    return (directory / name).read_text(encoding="utf-8")


def strip_comments(text):
    """Remove -- and /* */ comments outside quoted text."""
    out, i, n, quote = [], 0, len(text), None
    while i < n:
        ch = text[i]
        if quote:
            out.append(ch)
            if ch == quote:
                quote = None
            i += 1
        elif ch in "'\"`":
            quote = ch
            out.append(ch)
            i += 1
        elif text.startswith("--", i):
            while i < n and text[i] != "\n":
                i += 1
        elif text.startswith("/*", i):
            end = text.find("*/", i + 2)
            i = n if end < 0 else end + 2
        else:
            out.append(ch)
            i += 1
    return "".join(out)


def split_statements(text):
    """Top-level statements, comments removed, split on ; outside quotes."""
    text = strip_comments(text)
    statements, current, quote = [], [], None
    for ch in text:
        if quote:
            current.append(ch)
            if ch == quote:
                quote = None
        elif ch in "'\"`":
            quote = ch
            current.append(ch)
        elif ch == ";":
            statement = "".join(current).strip()
            if statement:
                statements.append(statement)
            current = []
        else:
            current.append(ch)
    tail = "".join(current).strip()
    if tail:
        statements.append(tail)
    return statements


_GUARDED = re.compile(r'^SET\s+@masgold_sql\s*:=\s*IF\((?P<guard>.*?),\s*"(?P<ddl>[^"]*)"\s*,\s*\'DO 0\'\s*\)$',
                      re.S | re.I)
_PLUMBING = re.compile(r"^(PREPARE|EXECUTE|DEALLOCATE)\b|^SET\s+(@masgold_|SESSION\s+sql_mode)", re.I)


def statements(text):
    """Effective statements; PREPARE/EXECUTE plumbing and session settings dropped."""
    result = []
    for raw in split_statements(text):
        match = _GUARDED.match(raw)
        if match:
            result.append(Statement(" ".join(match["ddl"].split()), match["guard"]))
        elif not _PLUMBING.match(raw):
            result.append(Statement(" ".join(raw.split()), None))
    return result


def raw_statements(text):
    return [" ".join(s.split()) for s in split_statements(text)]


def table_of(sql):
    match = re.match(r"^(?:ALTER TABLE|CREATE TABLE IF NOT EXISTS|CREATE TABLE)\s+(\w+)", sql, re.I)
    return match.group(1) if match else None


def _balanced(sql, start):
    """Text inside the parenthesis opening at sql[start]."""
    depth, quote = 0, None
    for i in range(start, len(sql)):
        ch = sql[i]
        if quote:
            if ch == quote:
                quote = None
        elif ch in "'\"":
            quote = ch
        elif ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                return sql[start + 1:i]
    raise ValueError(f"Unbalanced parenthesis in: {sql[start:start + 80]}")


def _split_top_level(body):
    parts, current, depth, quote = [], [], 0, None
    for ch in body:
        if quote:
            if ch == quote:
                quote = None
        elif ch in "'\"":
            quote = ch
        elif ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        elif ch == "," and depth == 0:
            parts.append("".join(current))
            current = []
            continue
        current.append(ch)
    parts.append("".join(current))
    return parts


def checks(text):
    """{constraint_name: (table, expression)} for every CHECK the file creates."""
    found = {}
    for statement in statements(text):
        table = table_of(statement.sql)
        for match in re.finditer(r"CONSTRAINT\s+(\w+)\s+CHECK\s*(?=\()", statement.sql, re.I):
            found[match.group(1)] = (table, _balanced(statement.sql, match.end()).strip())
    return found


def view(text, name):
    """The SELECT body of CREATE OR REPLACE VIEW <name>."""
    for statement in statements(text):
        match = re.match(rf"^CREATE OR REPLACE VIEW\s+{name}\s+AS\s+(.*)$", statement.sql, re.I | re.S)
        if match:
            return match.group(1)
    raise LookupError(name)


def create_table_columns(text):
    """{table: [column names]} for CREATE TABLE statements in the file."""
    tables = {}
    for statement in statements(text):
        if not statement.sql.upper().startswith("CREATE TABLE"):
            continue
        body = _balanced(statement.sql, statement.sql.index("("))
        columns = []
        for part in _split_top_level(body):
            word = part.strip().split(" ", 1)[0]
            if word and word.upper() not in ("PRIMARY", "UNIQUE", "KEY", "CONSTRAINT", "INDEX"):
                columns.append(word)
        tables[table_of(statement.sql)] = columns
    return tables


def added_columns(text):
    """{(table, column)} added with ALTER TABLE ... ADD COLUMN."""
    return {(table_of(s.sql), m.group(1)) for s in statements(text)
            for m in re.finditer(r"ADD COLUMN\s+(\w+)", s.sql, re.I)}


def created_objects(text):
    """Names of every table, column, index, key and constraint the file creates."""
    names = set()
    for statement in statements(text):
        sql = statement.sql
        if re.match(r"^CREATE TABLE", sql, re.I):
            names.add(table_of(sql))
            names.update(re.findall(r"(?:CONSTRAINT|UNIQUE KEY|KEY)\s+(\w+)", sql, re.I))
        names.update(re.findall(r"ADD (?:COLUMN|CONSTRAINT|UNIQUE KEY|KEY)\s+(\w+)", sql, re.I))
    return names
