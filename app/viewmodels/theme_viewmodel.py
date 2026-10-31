"""The colour theme choice for QML: a local look of this workstation (QSettings), changed from Settings > المظهر. No business data and no SQL."""
from PySide6.QtCore import QObject, Property, Signal, Slot

from config.theme_settings import DEFAULT_THEME, THEMES, ThemeSettings


class ThemeViewModel(QObject):
    changed = Signal()

    def __init__(self, parent=None, settings=None):
        super().__init__(parent)
        self._settings = settings if settings is not None else ThemeSettings()
        try:
            self._name = self._settings.theme()
        except Exception:                                   # an unreadable store never blocks the application
            self._name = DEFAULT_THEME

    @Property(str, notify=changed)
    def name(self):
        return self._name

    @Property("QVariantList", constant=True)
    def themes(self):
        return [{"code": code, "label": label} for code, label in THEMES]

    @Slot(str, result=bool)
    def setTheme(self, code):
        try:
            self._settings.set_theme(code)
        except Exception:
            return False
        self._name = code
        self.changed.emit()
        return True
