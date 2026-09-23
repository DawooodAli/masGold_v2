"""Environment-only credentials and transport settings for price providers."""
from dataclasses import dataclass, field
import math
import os
from pathlib import Path
from urllib.parse import urlsplit

from dotenv import load_dotenv


@dataclass(frozen=True)
class GoldTimoConfig:
    base_url: str = "https://goldtimo.com/api/v1"
    api_key: str = field(default="", repr=False)
    timeout_seconds: float = 5.0

    def __post_init__(self):
        parsed = urlsplit(self.base_url)
        if (parsed.scheme != "https" or not parsed.hostname or parsed.username
                or parsed.password or parsed.query or parsed.fragment):
            raise ValueError("GoldTimo base URL must be an HTTPS URL without credentials or query.")
        if not isinstance(self.api_key, str) or any(c in self.api_key for c in "\r\n"):
            raise ValueError("GoldTimo API key has an invalid format.")
        if (isinstance(self.timeout_seconds, bool)
                or not math.isfinite(self.timeout_seconds)
                or not 0.1 <= self.timeout_seconds <= 30):
            raise ValueError("GoldTimo timeout must be between 0.1 and 30 seconds.")

    @classmethod
    def from_env(cls):
        load_dotenv(Path(__file__).resolve().parent.parent / ".env", override=False)
        return cls(
            base_url=os.getenv("MASGOLD_GOLDTIMO_BASE_URL", "https://goldtimo.com/api/v1").rstrip("/"),
            api_key=os.getenv("MASGOLD_GOLDTIMO_API_KEY", "").strip(),
            timeout_seconds=float(os.getenv("MASGOLD_GOLDTIMO_TIMEOUT_SECONDS", "5")),
        )
