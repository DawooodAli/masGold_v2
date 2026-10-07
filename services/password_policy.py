"""New-password policy used by OWNER recovery (the first-owner setup keeps its own identical checks).

Messages never contain the password. No trimming or normalization: a password is used exactly as typed."""
import hmac

MIN_LENGTH, MAX_LENGTH = 6, 1024
INVALID_PASSWORD = "استخدم كلمة مرور من ٦ إلى ١٠٢٤ حرفاً."
CONFIRMATION_MISMATCH = "تأكيد كلمة المرور غير مطابق."


def validate_new_password(password, confirmation):
    if not isinstance(password, str) or not MIN_LENGTH <= len(password) <= MAX_LENGTH:
        raise ValueError(INVALID_PASSWORD)
    if not isinstance(confirmation, str) or not MIN_LENGTH <= len(confirmation) <= MAX_LENGTH:
        raise ValueError(CONFIRMATION_MISMATCH)
    if not hmac.compare_digest(password.encode("utf-8"), confirmation.encode("utf-8")):
        raise ValueError(CONFIRMATION_MISMATCH)
