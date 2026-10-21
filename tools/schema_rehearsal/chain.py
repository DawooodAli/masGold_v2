"""The numbered schema chain, its committed sha256 manifest and git state."""
from dataclasses import dataclass
import hashlib
from pathlib import Path
import re
import subprocess

from tools.schema_rehearsal.guards import RehearsalRefused, prepare_sql


REPO_ROOT = Path(__file__).resolve().parents[2]
SCHEMA_DIR = REPO_ROOT / "database" / "schema"
MANIFEST = SCHEMA_DIR / "SHA256SUMS"
FIRST, LAST = 1, 68


@dataclass(frozen=True)
class ChainFile:
    name: str
    path: Path
    sha256: str
    sql: str          # prepared text that will be sent to mysql.exe


def sha256_of(data):
    """sha256 of the file with CRLF normalized to LF, so a git autocrlf checkout
    (core.autocrlf=true on this machine) does not change the committed hash."""
    return hashlib.sha256(data.replace(b"\r\n", b"\n")).hexdigest()


def chain_names(schema_dir=SCHEMA_DIR):
    names = sorted(p.name for p in schema_dir.glob("[0-9][0-9][0-9]_*.sql"))
    numbers = [int(n[:3]) for n in names]
    if numbers != list(range(FIRST, LAST + 1)):
        raise RehearsalRefused("CHAIN_INCOMPLETE",
                               f"Expected one file for each of {FIRST:03d}-{LAST:03d}, found {numbers}.")
    return names


def read_manifest(path=MANIFEST):
    if not path.exists():
        raise RehearsalRefused("MANIFEST_MISSING", f"{path.name} is missing.")
    entries = {}
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        match = re.fullmatch(r"([0-9a-f]{64})  ([0-9]{3}_[\w]+\.sql)", line)
        if not match or match.group(2) in entries:
            raise RehearsalRefused("MANIFEST_INVALID", f"{path.name} line {number} is invalid.")
        entries[match.group(2)] = match.group(1)
    return entries


def manifest_text(schema_dir=SCHEMA_DIR):
    return "".join(f"{sha256_of((schema_dir / n).read_bytes())}  {n}\n" for n in chain_names(schema_dir))


def _git(*args, runner=subprocess.run):
    result = runner(["git", "-C", str(REPO_ROOT), *args], capture_output=True)
    return result.returncode, result.stdout


def verify_committed(paths, runner=subprocess.run):
    """Every file must be tracked and have no staged or unstaged change against HEAD."""
    for path in paths:
        relative = path.relative_to(REPO_ROOT).as_posix()
        code, _ = _git("ls-files", "--error-unmatch", "--", relative, runner=runner)
        if code != 0:
            raise RehearsalRefused("NOT_COMMITTED", f"{relative} is not committed.")
        code, _ = _git("diff", "--quiet", "HEAD", "--", relative, runner=runner)
        if code != 0:
            raise RehearsalRefused("NOT_COMMITTED", f"{relative} differs from HEAD.")


def load_chain(schema_dir=SCHEMA_DIR, manifest=MANIFEST, runner=subprocess.run, require_committed=True):
    """Verified, prepared chain files 001-028, in order."""
    names = chain_names(schema_dir)
    entries = read_manifest(manifest)
    if set(entries) != set(names):
        raise RehearsalRefused("MANIFEST_MISMATCH",
                               f"Manifest lists {sorted(entries)}, chain is {names}.")
    if require_committed:
        verify_committed([manifest] + [schema_dir / n for n in names], runner=runner)
    files = []
    for name in names:
        data = (schema_dir / name).read_bytes()
        if sha256_of(data) != entries[name]:
            raise RehearsalRefused("SHA256_MISMATCH", f"{name} does not match {manifest.name}.")
        files.append(ChainFile(name, schema_dir / name, entries[name], prepare_sql(name, data.decode("utf-8"))))
    return files
