"""Pure safety guards for the schema rehearsal tool (no I/O, no database).

Every function raises RehearsalRefused with a reason code; nothing is fixed
or guessed on the caller's behalf.
"""
import re


REHEARSAL_TARGET = "masgold_rehearsal"

# Application schemas on this server that the tool must never touch.
PROTECTED_APP_SCHEMAS = ("masgold_v2", "masgold_prod", "golddb", "masgold", "maindb", "shippingdb")
SYSTEM_SCHEMAS = ("mysql", "sys", "information_schema", "performance_schema")
REFUSED_TARGETS = PROTECTED_APP_SCHEMAS + SYSTEM_SCHEMAS

# 001-011 were written with this exact first statement.
LEGACY_USE_STATEMENT = "USE masgold_v2;"
LEGACY_USE_FILES = tuple(f"{n:03d}" for n in range(1, 12))

_IDENTIFIER = re.compile(r"^[a-z0-9_]{1,64}$")


class RehearsalRefused(RuntimeError):
    def __init__(self, code, message):
        super().__init__(f"{code}: {message}")
        self.code = code


# ------------------------------------------------------------------ target


def validate_target(name):
    """Only the disposable rehearsal database is an acceptable target."""
    if not isinstance(name, str) or not name:
        raise RehearsalRefused("TARGET_REQUIRED", "--target is required.")
    if name.lower() in REFUSED_TARGETS or name.lower().startswith("masgold_prod"):
        raise RehearsalRefused("TARGET_PROTECTED", f"'{name}' is a protected schema.")
    if name != REHEARSAL_TARGET:
        raise RehearsalRefused("TARGET_NOT_ALLOWED",
                               f"Only '{REHEARSAL_TARGET}' may be rehearsed (got '{name}').")
    if not _IDENTIFIER.match(name):
        raise RehearsalRefused("TARGET_INVALID", f"Invalid database name '{name}'.")
    return name


# ------------------------------------------------------------------ SQL text


def _scan(text):
    """Yield (kind, start, end) for comments and quoted literals outside each other."""
    i, n = 0, len(text)
    while i < n:
        ch = text[i]
        if ch in "'\"`":
            j = i + 1
            while j < n:
                if text[j] == "\\" and ch != "`":
                    j += 2
                    continue
                if text[j] == ch:
                    if j + 1 < n and text[j + 1] == ch:
                        j += 2
                        continue
                    break
                j += 1
            yield ("quote" + ch, i, min(j + 1, n))
            i = j + 1
        elif text.startswith("--", i) and (i + 2 >= n or text[i + 2] in " \t\r\n") or ch == "#":
            j = text.find("\n", i)
            j = n if j < 0 else j
            yield ("comment", i, j)
            i = j
        elif text.startswith("/*", i):
            j = text.find("*/", i + 2)
            j = n if j < 0 else j + 2
            yield ("comment", i, j)
            i = j
        else:
            i += 1


def code_only(text, keep_identifiers=False):
    """Text with comments and string literals blanked; offsets match the input.

    Backtick identifiers are blanked too unless keep_identifiers is set.
    """
    out, last = [], 0
    for kind, start, end in _scan(text):
        out.append(text[last:start])
        if kind == "comment":
            out.append(re.sub(r"[^\n]", " ", text[start:end]))
        elif kind == "quote`" and keep_identifiers:
            out.append(text[start:end])
        else:
            out.append(kind[-1] + " " * max(end - start - 2, 0) + kind[-1])
        last = end
    out.append(text[last:])
    return "".join(out)


def literals(text):
    """Contents of every quoted literal ('...', "...", `...`) outside comments."""
    return [(kind[-1], text[start + 1:end - 1]) for kind, start, end in _scan(text) if kind != "comment"]


def statements(code):
    return [s.strip() for s in code.split(";") if s.strip()]


_DANGEROUS = [
    (r"\b(CREATE|DROP|ALTER)\s+(DATABASE|SCHEMA)\b", "DATABASE_DDL"),
    (r"\b(CREATE|DROP|ALTER|RENAME)\s+USER\b", "ACCOUNT_DDL"),
    (r"\bGRANT\b[^;]*\bTO\b", "GRANT"),
    (r"\bREVOKE\b[^;]*\bFROM\b", "REVOKE"),
    (r"\bSET\s+(GLOBAL|PERSIST|PERSIST_ONLY|PASSWORD)\b|@@GLOBAL\.", "GLOBAL_SETTING"),
    (r"\bLOAD\s+(DATA|XML)\b", "LOAD_DATA"),
    (r"\bINTO\s+(OUTFILE|DUMPFILE)\b", "FILE_WRITE"),
    (r"\b(INSTALL|UNINSTALL)\s+(PLUGIN|COMPONENT)\b", "PLUGIN"),
    (r"\bSHUTDOWN\b|\bRESTART\b\s*;", "SERVER_CONTROL"),
    (r"\bCHANGE\s+(MASTER|REPLICATION)\b|\bRESET\s+(MASTER|REPLICA|SLAVE)\b", "REPLICATION"),
]
_CROSS_DB = re.compile(
    r"(?<![\w.$])`?(" + "|".join(REFUSED_TARGETS[:-2] + ("performance_schema", REHEARSAL_TARGET)) +
    r")`?\s*\.\s*[`\w*]", re.I)
_USE_STATEMENT = re.compile(r"^\s*USE\s+`?[\w$]+`?\s*;?\s*$", re.I)
_CLIENT_COMMAND = re.compile(r"^\s*(source|system|connect|delimiter|tee|pager|charset|prompt|rehash|"
                             r"warnings|nowarning|resetconnection|query_attributes|ssl_session_data_print)\b",
                             re.I)


def check_sql_safety(name, text):
    """Refuse USE, client commands, cross-database references and server-level statements.

    Literal contents are scanned as well, because the migrations run DDL through
    PREPARE of quoted strings.
    """
    code = code_only(text)
    if "\\" in code:
        raise RehearsalRefused("CLIENT_COMMAND", f"{name}: backslash command outside a literal.")
    for statement in statements(code):
        if re.match(r"^USE\b", statement, re.I):
            raise RehearsalRefused("USE_STATEMENT", f"{name}: '{statement[:40]}' is not allowed.")
        if _CLIENT_COMMAND.match(statement):
            raise RehearsalRefused("CLIENT_COMMAND", f"{name}: client command '{statement[:40]}'.")
    # Literals that can become SQL: those inside SET @var / PREPARE statements
    # (INSERT data such as the permission name 'Use Billing' is not SQL).
    start = 0
    for end in [i for i, ch in enumerate(code) if ch == ";"] + [len(code)]:
        if re.match(r"\s*(SET\s+@|PREPARE\b)", code[start:end], re.I):
            for quote, content in literals(text[start:end]):
                if quote != "`" and _USE_STATEMENT.match(content):
                    raise RehearsalRefused("USE_STATEMENT",
                                           f"{name}: dynamic '{content.strip()}' is not allowed.")
        start = end + 1
    scanned = code + "\n" + "\n".join(content for quote, content in literals(text) if quote != "`")
    identifiers = code_only(text, keep_identifiers=True).replace("`", "")
    cross = _CROSS_DB.search(identifiers) or _CROSS_DB.search(scanned)
    if cross:
        raise RehearsalRefused("CROSS_DATABASE", f"{name}: reference to '{cross.group(1)}.'.")
    for pattern, code_name in _DANGEROUS:
        found = re.search(pattern, scanned, re.I)
        if found:
            raise RehearsalRefused(code_name, f"{name}: '{found.group(0)[:40]}' is not allowed.")


def prepare_sql(name, text):
    """Return the SQL to run: exactly one leading 'USE masgold_v2;' removed from 001-011."""
    number = name[:3]
    code = code_only(text)
    first = re.search(r"\S", code)
    if number in LEGACY_USE_FILES:
        if not first or text[first.start():first.start() + len(LEGACY_USE_STATEMENT)] != LEGACY_USE_STATEMENT:
            raise RehearsalRefused("USE_MISSING",
                                   f"{name}: expected '{LEGACY_USE_STATEMENT}' as its first statement.")
        start = first.start()
        text = text[:start] + " " * len(LEGACY_USE_STATEMENT) + text[start + len(LEGACY_USE_STATEMENT):]
    check_sql_safety(name, text)
    return text


# ------------------------------------------------------------------ account


def analyse_grants(grant_lines):
    """Reasons why an account is broader than the rehearsal database ([] = restricted)."""
    reasons = []
    for line in grant_lines:
        match = re.match(r"GRANT\s+(.*?)\s+ON\s+(\S+)\s+TO\s", line, re.I)
        if not match:
            if re.match(r"GRANT\s+`", line, re.I):
                reasons.append(f"role grant: {line[:80]}")
            continue
        privileges, scope = match.group(1).upper(), match.group(2).replace("`", "").lower()
        if "WITH GRANT OPTION" in line.upper():
            reasons.append(f"GRANT OPTION: {line[:80]}")
        if scope in ("*.*",) and privileges.strip() != "USAGE":
            reasons.append(f"global privileges: {privileges[:60]}")
        database = scope.split(".", 1)[0]
        if scope != "*.*" and database != REHEARSAL_TARGET and privileges.strip() != "USAGE":
            reasons.append(f"privileges on {database}")
    return reasons
