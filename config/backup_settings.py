"""Where the automatic database backups go (owner 2026-11-01: «E:» broke, so the folder must be changeable to any drive from Settings).

It is a choice of THIS computer, kept in a small JSON file under LOCALAPPDATA (not MySQL, not the registry) because the scheduled Windows tasks run `tools.backup_database` outside the program
and read it from here. A missing, unreadable or empty file means the original default folder. Nothing here touches a database."""
import json
import os
from pathlib import Path

DEFAULT_DEST = Path(r"E:\masGold_backups")
FOLDER_NAME = "masGold_backups"


def settings_path():
    base = os.environ.get("LOCALAPPDATA") or str(Path.home())
    return Path(base) / "masGold v2" / "backup_settings.json"


def destination(path=None):
    """The backup folder now: the saved one, otherwise the default."""
    try:
        data = json.loads((path or settings_path()).read_text(encoding="utf-8"))
        value = str(data.get("dest", "")).strip()
    except (OSError, ValueError, AttributeError):
        return DEFAULT_DEST
    return Path(value) if value else DEFAULT_DEST


def reachable(folder):
    """True when the drive of the folder is present now (a cheap look, nothing is created)."""
    path = Path(str(folder))
    return bool(path.drive) and Path(path.drive + "\\").exists()


def check(folder):
    """(ok, message): the folder (or its drive) exists, can be created and a file can be written and removed in it. Creates the folder when the drive exists."""
    text = str(folder or "").strip().strip('"')
    if not text:
        return False, "اكتب مسار الفولدر."
    path = Path(text)
    if not path.is_absolute() or not path.drive:
        return False, "اكتب مسارًا كاملًا يبدأ بحرف الدرايف، مثل D:\\masGold_backups."
    if not Path(path.drive + "\\").exists():
        return False, f"الدرايف {path.drive} غير موجود الآن."
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / ".masgold_write_test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
    except OSError:
        return False, "لا يمكن الكتابة في هذا الفولدر. اختر فولدرًا آخر."
    return True, "الفولدر جاهز للنسخ الاحتياطي."


def save(folder, path=None):
    """Validate and store the folder; returns the stored Path. Raises ValueError with the Arabic reason."""
    ok, message = check(folder)
    if not ok:
        raise ValueError(message)
    target = path or settings_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps({"dest": str(Path(str(folder).strip().strip('"')))}, ensure_ascii=False), encoding="utf-8")
    return Path(str(folder).strip().strip('"'))
