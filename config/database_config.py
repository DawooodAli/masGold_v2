from dataclasses import dataclass
from pathlib import Path
import os
import re
import sys

from dotenv import load_dotenv


FROZEN = bool(getattr(sys, "frozen", False))
# A packaged build (PyInstaller folder) keeps its settings file NEXT TO the executable, never inside the bundle; from source it is the project folder.
PROJECT_ROOT = Path(sys.executable).resolve().parent if FROZEN else Path(__file__).resolve().parent.parent


def env_file_path(environ=None):
    """The settings file of this run: `.env` (development, the default) or the one named by MASGOLD_ENV_FILE (for example `.env.production`, started through run_production.bat). Only a plain
    `.env` / `.env.<name>` file in the project folder is accepted, so the variable can never point anywhere else."""
    environ = os.environ if environ is None else environ
    name = str(environ.get("MASGOLD_ENV_FILE", "") or "").strip()
    if name and re.fullmatch(r"\.env(\.[A-Za-z0-9_-]{1,30})?", name):
        return PROJECT_ROOT / name
    return PROJECT_ROOT / (".env.production" if FROZEN else ".env")      # the packaged program is the production program


ENV_FILE = env_file_path()

load_dotenv(ENV_FILE, override=True)


@dataclass(frozen=True)
class DatabaseConfig:
    host: str
    port: int
    database: str
    user: str
    password: str

    @classmethod
    def from_env(cls) -> "DatabaseConfig":
        host = os.getenv("MASGOLD_DB_HOST", "127.0.0.1")
        port = int(os.getenv("MASGOLD_DB_PORT", "3306"))
        database = os.getenv("MASGOLD_DB_NAME", "masgold_v2")
        user = os.getenv("MASGOLD_DB_USER", "root")
        password = os.getenv("MASGOLD_DB_PASSWORD")

        if not password:
            raise RuntimeError(
                "MASGOLD_DB_PASSWORD is missing from the .env file."
            )

        return cls(
            host=host,
            port=port,
            database=database,
            user=user,
            password=password,
        )