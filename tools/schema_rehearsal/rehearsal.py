"""Orchestration: account check, target state, protected-schema fingerprints, run."""
from tools.schema_rehearsal.guards import (
    PROTECTED_APP_SCHEMAS, RehearsalRefused, analyse_grants, validate_target,
)


MARKER_TABLE = "masgold_rehearsal_marker"
MARKER_KEY = "MASGOLD_SCHEMA_REHEARSAL"
TOOL_NAME = "tools.schema_rehearsal v1"
SENTINEL = "MASGOLD_DATABASE="
# System schemas are compared by structure only: InnoDB statistics tables in
# `mysql` legitimately change whenever tables are created in the target.
FINGERPRINT_SYSTEM_SCHEMAS = ("mysql", "sys")


def _in_list(names):
    return ", ".join(f"'{n}'" for n in names)


def fingerprint_sql():
    apps, systems = _in_list(PROTECTED_APP_SCHEMAS), _in_list(FINGERPRINT_SYSTEM_SCHEMAS)
    both = _in_list(PROTECTED_APP_SCHEMAS + FINGERPRINT_SYSTEM_SCHEMAS)
    return f"""SET SESSION information_schema_stats_expiry = 0;
SELECT SCHEMA_NAME, SHA2(CONCAT_WS('|', 'S', DEFAULT_CHARACTER_SET_NAME, DEFAULT_COLLATION_NAME), 256)
  FROM information_schema.SCHEMATA WHERE SCHEMA_NAME IN ({both})
UNION ALL
SELECT TABLE_SCHEMA, SHA2(CONCAT_WS('|', 'T', TABLE_NAME, TABLE_TYPE, IFNULL(ENGINE, ''),
       IFNULL(CREATE_TIME, ''), IFNULL(TABLE_COLLATION, ''),
       IF(TABLE_SCHEMA IN ({apps}), IFNULL(UPDATE_TIME, ''), '')), 256)
  FROM information_schema.TABLES WHERE TABLE_SCHEMA IN ({both})
UNION ALL
SELECT TABLE_SCHEMA, SHA2(CONCAT_WS('|', 'C', TABLE_NAME, COLUMN_NAME, ORDINAL_POSITION, COLUMN_TYPE,
       IS_NULLABLE, IFNULL(COLUMN_DEFAULT, '<null>'), EXTRA), 256)
  FROM information_schema.COLUMNS WHERE TABLE_SCHEMA IN ({both})
UNION ALL
SELECT TABLE_SCHEMA, SHA2(CONCAT_WS('|', 'I', TABLE_NAME, INDEX_NAME, SEQ_IN_INDEX,
       IFNULL(COLUMN_NAME, ''), NON_UNIQUE), 256)
  FROM information_schema.STATISTICS WHERE TABLE_SCHEMA IN ({both})
UNION ALL
SELECT CONSTRAINT_SCHEMA, SHA2(CONCAT_WS('|', 'K', TABLE_NAME, CONSTRAINT_NAME, CONSTRAINT_TYPE), 256)
  FROM information_schema.TABLE_CONSTRAINTS WHERE CONSTRAINT_SCHEMA IN ({both})
UNION ALL
SELECT TABLE_SCHEMA, SHA2(CONCAT_WS('|', 'V', TABLE_NAME, SHA2(VIEW_DEFINITION, 256)), 256)
  FROM information_schema.VIEWS WHERE TABLE_SCHEMA IN ({both})
UNION ALL
SELECT ROUTINE_SCHEMA, SHA2(CONCAT_WS('|', 'R', ROUTINE_NAME, ROUTINE_TYPE, LAST_ALTERED), 256)
  FROM information_schema.ROUTINES WHERE ROUTINE_SCHEMA IN ({both})
UNION ALL
SELECT TRIGGER_SCHEMA, SHA2(CONCAT_WS('|', 'G', TRIGGER_NAME, CREATED), 256)
  FROM information_schema.TRIGGERS WHERE TRIGGER_SCHEMA IN ({both})
UNION ALL
SELECT EVENT_SCHEMA, SHA2(CONCAT_WS('|', 'E', EVENT_NAME, LAST_ALTERED), 256)
  FROM information_schema.EVENTS WHERE EVENT_SCHEMA IN ({both});
"""


def summarize_fingerprint(rows):
    """{schema: sorted row hashes} for every protected schema; invisible schemas map to None."""
    result = {name: None for name in PROTECTED_APP_SCHEMAS + FINGERPRINT_SYSTEM_SCHEMAS}
    for schema, digest in rows:
        if schema in result:
            result[schema] = (result[schema] or []) + [digest]
    return {name: (tuple(sorted(digests)) if digests is not None else None) for name, digests in result.items()}


class Rehearsal:
    def __init__(self, client, target, chain, log=print):
        self.client, self.target, self.chain, self.log = client, validate_target(target), chain, log

    # -------------------------------------------------------------- account

    def check_account(self, allow_privileged):
        user = self.client.query("SELECT CURRENT_USER();")[0][0]
        reasons = analyse_grants(row[0] for row in self.client.query("SHOW GRANTS;"))
        visible = [name for name, digests in self.fingerprint().items()
                   if digests is not None and name in PROTECTED_APP_SCHEMAS]
        if visible:
            reasons.append(f"can see protected schemas: {', '.join(visible)}")
        if reasons and not allow_privileged:
            raise RehearsalRefused("ACCOUNT_TOO_BROAD",
                                   f"{user} is not limited to {self.target}: " + "; ".join(reasons))
        return user, reasons

    # -------------------------------------------------------------- fingerprints

    def fingerprint(self):
        return summarize_fingerprint(self.client.query(fingerprint_sql()))

    def verify_protected(self, baseline, after):
        current = self.fingerprint()
        changed = sorted(name for name in baseline if baseline[name] != current.get(name))
        if changed:
            raise RehearsalRefused("PROTECTED_CHANGED",
                                   f"Protected schema(s) changed after {after}: {', '.join(changed)}. "
                                   "Stopped. (A live legacy application writing to golddb also "
                                   "triggers this; rehearse while it is idle.)")

    # -------------------------------------------------------------- target

    def target_state(self):
        exists = self.client.query(
            f"SELECT COUNT(*) FROM information_schema.SCHEMATA WHERE SCHEMA_NAME = '{self.target}';")
        if exists[0][0] == "0":
            return "MISSING"
        counts = self.client.query(f"""SELECT
  (SELECT COUNT(*) FROM information_schema.TABLES WHERE TABLE_SCHEMA = '{self.target}'),
  (SELECT COUNT(*) FROM information_schema.TABLES WHERE TABLE_SCHEMA = '{self.target}'
      AND TABLE_NAME = '{MARKER_TABLE}' AND TABLE_TYPE = 'BASE TABLE'),
  (SELECT COUNT(*) FROM information_schema.ROUTINES WHERE ROUTINE_SCHEMA = '{self.target}')
  + (SELECT COUNT(*) FROM information_schema.TRIGGERS WHERE TRIGGER_SCHEMA = '{self.target}')
  + (SELECT COUNT(*) FROM information_schema.EVENTS WHERE EVENT_SCHEMA = '{self.target}');""")[0]
        tables, marker, other = (int(value) for value in counts)
        if tables == 0 and other == 0:
            return "EMPTY"
        if marker:
            rows = self.client.query(
                f"SELECT target_database FROM {MARKER_TABLE} WHERE marker_key = '{MARKER_KEY}';",
                database=self.target)
            if rows == [[self.target]]:
                return "MARKED"
        return "FOREIGN"

    def _write_marker(self):
        self.client.run_script(f"""CREATE TABLE {MARKER_TABLE} (
    marker_key VARCHAR(64) NOT NULL PRIMARY KEY,
    target_database VARCHAR(64) NOT NULL,
    tool VARCHAR(100) NOT NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);
INSERT INTO {MARKER_TABLE} (marker_key, target_database, tool)
VALUES ('{MARKER_KEY}', DATABASE(), '{TOOL_NAME}');
""", database=self.target)

    def _create(self):
        validate_target(self.target)
        self.client.run_script(f"CREATE DATABASE `{self.target}` "
                               "CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;", database=None)

    def prepare_target(self, create=False, rebuild=False):
        state = self.target_state()
        if state == "FOREIGN":
            raise RehearsalRefused("TARGET_NOT_EMPTY",
                                   f"{self.target} has objects but no rehearsal marker; it will not be touched.")
        if state == "MISSING":
            if not create:
                raise RehearsalRefused("TARGET_MISSING", f"{self.target} does not exist; pass --create.")
            self._create()
        elif state == "MARKED":
            if not rebuild:
                raise RehearsalRefused("TARGET_ALREADY_BUILT",
                                       f"{self.target} holds an earlier rehearsal; pass --rebuild to replace it.")
            # Re-check immediately before the only DROP the tool ever issues.
            validate_target(self.target)
            if self.target_state() != "MARKED":
                raise RehearsalRefused("TARGET_NOT_EMPTY", f"{self.target} lost its marker; not dropped.")
            self.client.run_script(f"DROP DATABASE `{self.target}`;", database=None)
            self._create()
        self._write_marker()
        if self.target_state() != "MARKED":
            raise RehearsalRefused("MARKER_FAILED", f"Could not mark {self.target}.")
        return state

    # -------------------------------------------------------------- run

    def apply_chain(self, baseline):
        applied = []
        for chain_file in self.chain:
            validate_target(self.target)
            output = self.client.run_script(
                chain_file.sql + f"\nSELECT CONCAT('{SENTINEL}', IFNULL(DATABASE(), '<NULL>'));\n",
                database=self.target)
            lines = [line for line in output.splitlines() if line.startswith(SENTINEL)]
            if lines != [SENTINEL + self.target]:
                raise RehearsalRefused("DATABASE_SWITCHED",
                                       f"After {chain_file.name} the session database was {lines}.")
            self.verify_protected(baseline, chain_file.name)
            applied.append(chain_file.name)
            self.log(f"applied {chain_file.name}")
        return applied

    def verify_result(self):
        versions = [int(row[0]) for row in self.client.query(
            "SELECT version FROM schema_migrations ORDER BY version;", database=self.target)]
        expected = [int(f.name[:3]) for f in self.chain]
        if versions != expected:
            raise RehearsalRefused("MIGRATIONS_INCOMPLETE", f"schema_migrations has {versions}, expected {expected}.")
        tables = self.client.query(
            f"SELECT TABLE_TYPE, COUNT(*) FROM information_schema.TABLES WHERE TABLE_SCHEMA = '{self.target}' "
            "GROUP BY TABLE_TYPE ORDER BY TABLE_TYPE;")
        return {"schema_migrations": versions, "objects": {kind: int(count) for kind, count in tables}}

    def run(self, create=False, rebuild=False, allow_privileged=False):
        user, reasons = self.check_account(allow_privileged)
        baseline = self.fingerprint()
        state = self.prepare_target(create=create, rebuild=rebuild)
        self.verify_protected(baseline, "preparing the target")
        applied = self.apply_chain(baseline)
        result = self.verify_result()
        self.verify_protected(baseline, "verification")
        return {"target": self.target, "account": user, "account_warnings": reasons,
                "initial_state": state, "applied": applied,
                "files": {f.name: f.sha256 for f in self.chain},
                "protected_schemas_visible": sorted(k for k, v in baseline.items() if v is not None),
                **result}
