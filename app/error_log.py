"""A rotating error log for this workstation (LOCALAPPDATA, folder "masGold v2", logs/errors.log).

The workers log an unexpected failure (exception type, MySQL error code and the call frames; never a payload, SQL parameter or password) but nothing wrote it anywhere, so the employee only
saw the generic Arabic message. This keeps those lines in a small file the owner can send. Failing to create the file never stops the program."""
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path


def log_path():
    base = os.environ.get("LOCALAPPDATA") or str(Path.home())
    return Path(base) / "masGold v2" / "logs" / "errors.log"


def install():
    """Attach the file handler to the root logger once (ERROR and above, 1 MB x 3 files)."""
    root = logging.getLogger()
    if any(getattr(handler, "_masgold_error_log", False) for handler in root.handlers):
        return None
    try:
        path = log_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        handler = RotatingFileHandler(path, maxBytes=1_000_000, backupCount=3, encoding="utf-8")
    except OSError:
        return None
    handler._masgold_error_log = True
    handler.setLevel(logging.ERROR)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    root.addHandler(handler)
    return path
