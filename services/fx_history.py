"""A daily history of the dollar / euro rates against the pound, recorded by THIS computer from the GoldTimo /rates answer the program already reads.

It exists only to give the market readings a history (GoldTimo returns today's rate only). A small JSON file in the user's local application folder, one value per
day and currency (the latest of the day wins), the last KEEP_DAYS days kept. Display only: no database, no accounting rule. A failing read or write never reaches
the home page."""
from datetime import date
import json
import logging
import os
from pathlib import Path

CODES = ("USD", "EUR")
KEEP_DAYS = 400
logger = logging.getLogger("fx_history")


def default_path():
    base = os.environ.get("LOCALAPPDATA") or str(Path.home())
    return Path(base) / "masGold v2" / "fx_history.json"


class FxHistory:
    def __init__(self, path=None, today=date.today):
        self._path, self._today = Path(path) if path else default_path(), today

    def _load(self):
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def record(self, rows):
        """rows = [{code, price}] of the rates answer; keeps the latest price of today for USD and EUR. Never raises."""
        try:
            prices = {str(r["code"]): str(r["price"]) for r in rows or [] if str(r.get("code")) in CODES}
            if not prices:
                return
            data = self._load()
            day = self._today().isoformat()
            for code, price in prices.items():
                series = data.setdefault(code, {})
                series[day] = price
                for old in sorted(series)[:-KEEP_DAYS]:
                    del series[old]
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_text(json.dumps(data, ensure_ascii=False, sort_keys=True), encoding="utf-8")
        except Exception:
            logger.exception("fx history write failed")

    def series(self, code):
        """[(date, Decimal)] oldest first for one currency (empty when nothing is recorded)."""
        from decimal import Decimal, InvalidOperation
        out = []
        for day, price in (self._load().get(code) or {}).items():
            try:
                out.append((date.fromisoformat(day), Decimal(str(price))))
            except (ValueError, InvalidOperation):
                continue
        return sorted(out)


_SHARED = None


def shared():
    global _SHARED
    if _SHARED is None:
        _SHARED = FxHistory()
    return _SHARED
