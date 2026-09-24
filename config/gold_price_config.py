"""OS credentials first, optional developer environment fallback; no secret repr."""
from dataclasses import dataclass, field
import math
import os
from pathlib import Path
from urllib.parse import urlsplit

from dotenv import load_dotenv


def credential_diagnostics(entered_key, stored_key, source, selected_key, *, stored_known=True):
    """Temporary credential metadata; never return the key or comparison data."""
    return {
        "credential_source": source,
        "configured": bool(selected_key.strip()),
        "character_count": len(selected_key.strip()),
    }


@dataclass(frozen=True)
class GoldTimoConfig:
    base_url: str = "https://goldtimo.com/api/v1"
    api_key: str = field(default="", repr=False)
    timeout_seconds: float = 10.0
    key_had_whitespace: bool = field(default=False, init=False, repr=False)
    credential_trace: dict = field(default_factory=dict, repr=False, compare=False)

    def __post_init__(self):
        parsed = urlsplit(self.base_url)
        if (parsed.scheme != "https" or not parsed.hostname or parsed.username
                or parsed.password or parsed.query or parsed.fragment):
            raise ValueError("GoldTimo base URL must be an HTTPS URL without credentials or query.")
        if not isinstance(self.api_key, str):
            raise ValueError("GoldTimo API key has an invalid format.")
        clean_key = self.api_key.strip()
        object.__setattr__(self, "key_had_whitespace", clean_key != self.api_key)
        if "\r" in clean_key or "\n" in clean_key:
            raise ValueError("GoldTimo API key cannot contain embedded line breaks.")
        # Never rewrite case, quotes, prefixes or internal characters. A caller
        # must enter the raw key; the HTTP client still rejects unsafe headers.
        if not self.credential_trace:
            object.__setattr__(self, "credential_trace", credential_diagnostics(
                self.api_key, None, "ENTERED" if self.api_key else "NONE",
                self.api_key, stored_known=False))
        object.__setattr__(self, "api_key", clean_key)
        if (isinstance(self.timeout_seconds, bool)
                or not math.isfinite(self.timeout_seconds)
                or not 0.1 <= self.timeout_seconds <= 30):
            raise ValueError("GoldTimo timeout must be between 0.1 and 30 seconds.")

    @classmethod
    def from_env(cls, secret_store=None, options=None, *, entered_key=None, trace_stored=False):
        from services.secret_store import SecretStore, SecretStoreError
        load_dotenv(Path(__file__).resolve().parent.parent / ".env", override=False)
        options = options or {}
        if entered_key is not None and not isinstance(entered_key, str):
            raise ValueError("GoldTimo API key has an invalid format.")
        if entered_key is not None and not entered_key.strip():
            # A whitespace-only draft is no key; fall back like an empty field.
            entered_key = None
        stored, stored_known = None, False
        if not entered_key or trace_stored:
            try:
                stored = (secret_store or SecretStore()).get_secret("goldtimo_api_key")
                stored_known = True
            except SecretStoreError:
                if not entered_key:
                    raise
                # Comparing storage must never prevent a transient-key fetch.
        environment = os.getenv("MASGOLD_GOLDTIMO_API_KEY", "")
        selected = entered_key or stored or environment
        source = "ENTERED" if entered_key else "KEYRING" if stored else "ENV" if environment else "NONE"
        return cls(
            base_url=options.get("base_url", os.getenv("MASGOLD_GOLDTIMO_BASE_URL", "https://goldtimo.com/api/v1")).rstrip("/"),
            api_key=selected,
            timeout_seconds=float(options.get("timeout_seconds", os.getenv("MASGOLD_GOLDTIMO_TIMEOUT_SECONDS", "10"))),
            credential_trace=credential_diagnostics(entered_key, stored, source, selected,
                                                     stored_known=stored_known),
        )
