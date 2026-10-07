"""TEMPORARY DEVELOPMENT AUTO-LOGIN — DISABLE BEFORE PRODUCTION.

Development convenience only, and OFF by default (fail closed): without an explicit developer opt-in
the normal Login window is always shown — running from source, a non-production database name or a
non-frozen build never enable it by themselves.

The ONE switch is the environment variable

    MASGOLD_DEV_AUTO_LOGIN=1

(for example in the local, git-ignored `.env`). Any other value, or no value, means disabled. When it is
enabled, startup opens the MainWindow directly as an existing account instead of showing the Login window.

The session is still a real, verified AppSession: the account must exist, be active, hold APP_LOGIN, have an
active linked employee (if any) and not require a password change. Its real user id, permissions and
employee link are used, and the login is recorded exactly as a normal login. No password is stored.

Even when opted in it never activates in a frozen/packaged build, when MASGOLD_ENV names a
production/release environment, or against a protected production database.
"""
import os
import sys

# TEMPORARY DEVELOPMENT AUTO-LOGIN — DISABLE BEFORE PRODUCTION
DEV_AUTO_LOGIN_ENV = "MASGOLD_DEV_AUTO_LOGIN"
DEV_AUTO_LOGIN_USERNAME = "admin"

PRODUCTION_ENVIRONMENTS = frozenset({"production", "prod", "release"})
PROTECTED_DATABASES = frozenset({"masgold_prod"})


def dev_auto_login_username(environ=None, frozen=None):
    """The development account to sign in automatically, or None (the default) when not explicitly enabled."""
    environ = os.environ if environ is None else environ
    if str(environ.get(DEV_AUTO_LOGIN_ENV, "")).strip() != "1":
        return None                                   # no explicit opt-in: the normal Login window
    if getattr(sys, "frozen", False) if frozen is None else frozen:
        return None
    if environ.get("MASGOLD_ENV", "development").strip().lower() in PRODUCTION_ENVIRONMENTS:
        return None
    if environ.get("MASGOLD_DB_NAME", "masgold_v2").strip().lower() in PROTECTED_DATABASES:
        return None
    return DEV_AUTO_LOGIN_USERNAME or None
