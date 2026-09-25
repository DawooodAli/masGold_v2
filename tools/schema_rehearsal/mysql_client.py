"""mysql.exe runner with a temporary option file for credentials.

The password is written only to a private temporary option file that is
deleted afterwards; it never appears on a command line or in output.
--defaults-file makes that file the ONLY option file read, so a user-level
my.ini cannot inject database=, init-command= or other settings.
"""
from contextlib import contextmanager
from dataclasses import dataclass
import os
from pathlib import Path
import shutil
import subprocess
import tempfile


DEFAULT_MYSQL = Path(r"C:\Program Files\MySQL\MySQL Server 8.0\bin\mysql.exe")


class MysqlError(RuntimeError):
    pass


@dataclass(frozen=True)
class Credentials:
    host: str
    port: int
    user: str
    password: str

    def __repr__(self):
        return f"Credentials(host={self.host!r}, port={self.port!r}, user={self.user!r}, password=***)"


def _quote(value):
    return '"' + str(value).replace("\\", "\\\\").replace('"', '\\"') + '"'


def option_file_text(credentials):
    for value in (credentials.host, credentials.user, credentials.password):
        if "\n" in str(value) or "\r" in str(value):
            raise ValueError("Credentials may not contain line breaks.")
    return ("[client]\n"
            f"host={_quote(credentials.host)}\n"
            f"port={int(credentials.port)}\n"
            f"user={_quote(credentials.user)}\n"
            f"password={_quote(credentials.password)}\n")


@contextmanager
def option_file(credentials):
    directory = tempfile.mkdtemp(prefix="masgold-rehearsal-")
    path = Path(directory) / "client.cnf"
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(option_file_text(credentials))
        yield path
    finally:
        shutil.rmtree(directory, ignore_errors=True)


class MysqlClient:
    def __init__(self, mysql_path, option_path, runner=subprocess.run):
        self.mysql_path, self.option_path, self.runner = Path(mysql_path), Path(option_path), runner

    def command(self, database=None):
        args = [str(self.mysql_path),
                f"--defaults-file={self.option_path}",      # must be the first option
                "--protocol=TCP",
                "--batch", "--skip-column-names", "--raw",
                "--binary-mode",                             # disables mysql client commands
                "--default-character-set=utf8mb4"]
        if database is not None:
            args.append(f"--database={database}")
        return args

    def _run(self, sql, database):
        result = self.runner(self.command(database), input=sql.encode("utf-8"), capture_output=True)
        if result.returncode != 0:
            raise MysqlError(result.stderr.decode("utf-8", "replace").strip() or f"mysql exit {result.returncode}")
        return result.stdout.decode("utf-8", "replace")

    def query(self, sql, database=None):
        """Rows as lists of strings (tab-separated batch output)."""
        return [line.split("\t") for line in self._run(sql, database).splitlines() if line]

    def run_script(self, sql, database):
        return self._run(sql, database)
