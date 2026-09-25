"""Schema rehearsal: prove the numbered chain 001-015 on a disposable MySQL database.

    python -m tools.schema_rehearsal verify
    python -m tools.schema_rehearsal run --target masgold_rehearsal --create
    python -m tools.schema_rehearsal run --target masgold_rehearsal --rebuild
    python -m tools.schema_rehearsal write-manifest

Rehearsal only: the target must be masgold_rehearsal. There is no production
cut-over mode. The database name is never read from .env; the password comes
from MASGOLD_REHEARSAL_PASSWORD or an interactive prompt, never the command line.
"""
import argparse
import getpass
import json
import os
from pathlib import Path
import sys

from tools.schema_rehearsal import chain as chain_module
from tools.schema_rehearsal.guards import RehearsalRefused, validate_target
from tools.schema_rehearsal.mysql_client import DEFAULT_MYSQL, Credentials, MysqlClient, MysqlError, option_file
from tools.schema_rehearsal.rehearsal import Rehearsal


PASSWORD_ENV = "MASGOLD_REHEARSAL_PASSWORD"


def parser():
    root = argparse.ArgumentParser(prog="python -m tools.schema_rehearsal", description=__doc__,
                                   formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = root.add_subparsers(dest="command", required=True)
    commands.add_parser("verify", help="check files, manifest and git state; no database access")
    commands.add_parser("write-manifest", help="rewrite database/schema/SHA256SUMS from the chain files")
    run = commands.add_parser("run", help="build 001-015 into the rehearsal database")
    run.add_argument("--target", required=True, help="must be masgold_rehearsal")
    run.add_argument("--user", default="masgold_build")
    run.add_argument("--host", default="localhost", help="matches the masgold_build@localhost account")
    run.add_argument("--port", type=int, default=3306)
    run.add_argument("--mysql", type=Path, default=DEFAULT_MYSQL, help="path to mysql.exe")
    state = run.add_mutually_exclusive_group()
    state.add_argument("--create", action="store_true", help="create the target when it does not exist")
    state.add_argument("--rebuild", action="store_true", help="drop and rebuild a marked rehearsal target")
    run.add_argument("--allow-privileged-account", action="store_true",
                     help="accept an account with rights beyond the target (not recommended)")
    run.add_argument("--report", type=Path, help="write the JSON result here")
    return root


def password():
    value = os.environ.get(PASSWORD_ENV)
    return value if value is not None else getpass.getpass("MySQL password for the rehearsal account: ")


def main(argv=None, runner=None):
    args = parser().parse_args(argv)
    try:
        if args.command == "write-manifest":
            chain_module.MANIFEST.write_text(chain_module.manifest_text(), encoding="utf-8", newline="\n")
            print(f"wrote {chain_module.MANIFEST}")
            return 0
        if args.command == "verify":
            files = chain_module.load_chain()
            for f in files:
                print(f"{f.sha256}  {f.name}")
            print(f"OK: {len(files)} files verified against {chain_module.MANIFEST.name} and HEAD.")
            return 0

        target = validate_target(args.target)          # before anything else, including the password
        files = chain_module.load_chain()
        if not args.mysql.is_file():
            raise RehearsalRefused("MYSQL_MISSING", f"{args.mysql} not found.")
        credentials = Credentials(args.host, args.port, args.user, password())
        with option_file(credentials) as options:
            client = MysqlClient(args.mysql, options, **({"runner": runner} if runner else {}))
            result = Rehearsal(client, target, files).run(
                create=args.create, rebuild=args.rebuild, allow_privileged=args.allow_privileged_account)
        text = json.dumps(result, indent=1, ensure_ascii=False)
        if args.report:
            args.report.parent.mkdir(parents=True, exist_ok=True)
            args.report.write_text(text, encoding="utf-8")
        print(text)
        print("REHEARSAL OK")
        return 0
    except (RehearsalRefused, MysqlError) as exc:
        print(f"REFUSED/FAILED: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
