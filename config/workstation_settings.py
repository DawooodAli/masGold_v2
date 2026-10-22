"""Local (per Windows user / workstation) choice to open the program WITHOUT the login window, as one named account.

It lives with the other local settings (QSettings), never in the shop's database and never in a file that is committed. Empty = the normal Login window (the default). The account must still exist, be
active and hold APP_LOGIN when the program starts: AuthenticationService.establish_automatic_session re-checks all of that (a name typed into QML is never trusted). Passwords stay in the database for the
checks that ask for them (مراجعات المدير, Dead Cash, user administration)."""
from PySide6.QtCore import QSettings

ORGANIZATION, APPLICATION = "masGold", "masGold_v2"
AUTO_SIGN_IN_KEY = "security/auto_sign_in_user"


def _store(settings=None):
    return settings if settings is not None else QSettings(ORGANIZATION, APPLICATION)


def auto_sign_in_user(settings=None):
    """The account the program opens as without asking for a password, or None."""
    value = str(_store(settings).value(AUTO_SIGN_IN_KEY, "") or "").strip()
    return value or None


def set_auto_sign_in_user(username, settings=None):
    store = _store(settings)
    store.setValue(AUTO_SIGN_IN_KEY, str(username or "").strip())
    store.sync()
