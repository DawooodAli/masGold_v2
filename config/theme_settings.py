"""Local (per-workstation) colour theme of the application.

The theme is a look of THIS computer's screen, not shop data, so it is kept in QSettings next to the printer choice and never in MySQL.
An unknown or missing value is the default gold theme."""
from PySide6.QtCore import QSettings

ORGANIZATION, APPLICATION = "masGold", "masGold_v2"
THEME_KEY = "ui/theme"
DEFAULT_THEME = "gold"
# (code, Arabic name): the codes are the keys of the palettes in qml/Theme.qml.
THEMES = (("gold", "ذهبي (الافتراضي)"), ("blue", "أزرق ليلي"), ("green", "أخضر زمردي"), ("violet", "بنفسجي"))
THEME_CODES = tuple(code for code, _ in THEMES)


class ThemeSettings:
    def __init__(self, store=None):
        self._store = store if store is not None else QSettings(QSettings.NativeFormat, QSettings.UserScope, ORGANIZATION, APPLICATION)

    def theme(self):
        value = str(self._store.value(THEME_KEY, "") or "").strip()
        return value if value in THEME_CODES else DEFAULT_THEME

    def set_theme(self, code):
        if code not in THEME_CODES:
            raise ValueError("Unknown theme.")
        self._store.setValue(THEME_KEY, code)
        self._store.sync()
