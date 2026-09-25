"""TEMPORARY DEVELOPMENT AUTO-LOGIN — DISABLE BEFORE PRODUCTION.

While the application is being built, startup opens the MainWindow directly as
an existing account instead of showing the Login window on every run.
Set DEV_AUTO_LOGIN = False to restore the normal Login window; nothing else
needs to change.

The session is still a real, verified AppSession: the account must exist, be
active, hold APP_LOGIN, have an active linked employee (if any) and not require
a password change. Its real user id, permissions and employee link are used,
and the login is recorded exactly as a normal login. No password is stored.

It never activates in a frozen/packaged build, when MASGOLD_ENV names a
production/release environment, or against a protected production database.
"""
import os
import sys

# TEMPORARY DEVELOPMENT AUTO-LOGIN — DISABLE BEFORE PRODUCTION
DEV_AUTO_LOGIN = True
DEV_AUTO_LOGIN_USERNAME = "admin"

PRODUCTION_ENVIRONMENTS = frozenset({"production", "prod", "release"})
PROTECTED_DATABASES = frozenset({"masgold_prod"})


def dev_auto_login_username(environ=None, frozen=None):
    """The development account to sign in automatically, or None when disabled."""
    if not DEV_AUTO_LOGIN:
        return None
    environ = os.environ if environ is None else environ
    if getattr(sys, "frozen", False) if frozen is None else frozen:
        return None
    if environ.get("MASGOLD_ENV", "development").strip().lower() in PRODUCTION_ENVIRONMENTS:
        return None
    if environ.get("MASGOLD_DB_NAME", "masgold_v2").strip().lower() in PROTECTED_DATABASES:
        return None
    return DEV_AUTO_LOGIN_USERNAME or None
