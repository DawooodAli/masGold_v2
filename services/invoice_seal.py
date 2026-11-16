"""The invoice seal: a short code that only this shop can compute for an invoice (owner 2026-11-16, anti-forgery).

The code is an HMAC-SHA256 over the invoice's STORED facts (number, authoritative net amount, its direction, the invoice date) with a secret key kept in the
database (`system_settings`, key `internal.invoice_seal_key`, never shown). Changing the number, the amount, the direction or the date on a printed / PDF
invoice makes the code wrong; nobody without the key can compute a valid one. Copying a genuine invoice unchanged still shows a genuine code - that is the
natural limit of any code. Pure functions: no database, no Qt.
"""
import hashlib
import hmac
import re
import secrets
from decimal import Decimal

KEY_SETTING = "internal.invoice_seal_key"
KEY_DESCRIPTION = "Secret key of the invoice seal (internal; never shown)"
ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"          # Crockford base32: no I, L, O, U
CODE_LEN, CHECK_LEN = 8, 2


def new_key():
    return secrets.token_hex(32)


def facts_text(number, net_amount, net_direction, invoice_date):
    """The canonical text the code covers. The amount is fixed to two decimals; the date to YYYY-MM-DD."""
    amount = Decimal(str(net_amount or 0)).quantize(Decimal("0.01"))
    day = str(invoice_date or "")[:10]
    return f"MG1|{str(number).strip()}|{amount}|{str(net_direction or '').strip()}|{day}"


def _digest(key, text):
    return hmac.new(str(key).encode("utf-8"), text.encode("utf-8"), hashlib.sha256).digest()


def _encode(raw, length):
    value = int.from_bytes(raw, "big")
    chars = []
    for _ in range(length):
        value, index = divmod(value, 32)
        chars.append(ALPHABET[index])
    return "".join(chars)


def code_for(key, number, net_amount, net_direction, invoice_date):
    """The printed code, grouped as XXXX-XXXX."""
    raw = _encode(_digest(key, facts_text(number, net_amount, net_direction, invoice_date)), CODE_LEN)
    return f"{raw[:4]}-{raw[4:]}"


def check_for(key, number):
    """Two characters that depend on the invoice number only (a typo in the printed number is visible without the other facts)."""
    return _encode(_digest(key, f"MG1-N|{str(number).strip()}"), CHECK_LEN)


def clean_code(text):
    """What the employee typed, normalised (case, dashes, spaces; the look-alike letters O, I, L)."""
    raw = re.sub(r"[^0-9A-Za-z]", "", str(text or "")).upper().replace("O", "0").replace("I", "1").replace("L", "1")
    return f"{raw[:4]}-{raw[4:]}" if len(raw) == CODE_LEN else raw


def matches(expected, typed):
    return hmac.compare_digest(str(expected), clean_code(typed))
