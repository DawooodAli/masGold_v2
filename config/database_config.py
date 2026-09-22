from dataclasses import dataclass
from pathlib import Path
import os

from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parent.parent
ENV_FILE = PROJECT_ROOT / ".env"

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