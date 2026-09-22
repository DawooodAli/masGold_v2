"""Search-only Arabic folding; never use these functions on values being saved."""

import re


_REPLACEMENTS = dict(zip("أإآٱةىؤئ", "ااااهيوي"))
_REPLACEMENTS.update(zip("٠١٢٣٤٥٦٧٨٩", "0123456789"))
_REPLACEMENTS.update(zip("۰۱۲۳۴۵۶۷۸۹", "0123456789"))
_TRANSLATION = str.maketrans(_REPLACEMENTS)
# Arabic combining marks, including Quranic marks; do not strip other scripts.
_MARKS = (
    "[ـ\u0610-\u061a\u064b-\u065f\u0670\u06d6-\u06dc"
    "\u06df-\u06e4\u06e7-\u06e8\u06ea-\u06ed"
    "\u0898-\u089f\u08ca-\u08e1\u08e3-\u08ff]"
)
# Explicit Unicode whitespace keeps Python and MySQL/ICU behavior identical.
_SPACES = (
    "[ \t\n\r\v\f\u001c-\u001f\u0085\u00a0\u1680"
    "\u2000-\u200a\u2028\u2029\u202f\u205f\u3000]+"
)
_MOBILE_FORMATTING = "[ +().‐‑‒–—―−-]"
_COLUMN = re.compile(r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)?")


def normalize_search_text(value: str | None) -> str:
    """Fold Arabic letters/digits, remove marks and collapse whitespace."""
    text = re.sub(_MARKS, "", value or "").translate(_TRANSLATION)
    return re.sub(_SPACES, " ", text).strip(" ")


def normalize_mobile_search(value: str | None) -> str:
    """Also ignore common telephone formatting, without interpreting country codes."""
    return re.sub(_MOBILE_FORMATTING, "", normalize_search_text(value))


def mysql_search_expression(column: str, *, mobile: bool = False) -> tuple[str, tuple]:
    """Return a MySQL 8 expression and bind values using the same search rules.

    The column must be a code-defined identifier, never employee input. All
    replacement values/patterns are parameters. Apply in WHERE before paging;
    SELECT the original columns so displayed and stored text remains unchanged.
    """
    if not _COLUMN.fullmatch(column):
        raise ValueError("Expected a plain SQL column identifier.")
    expression = f"REGEXP_REPLACE(COALESCE({column}, ''), %s, %s)"
    params = [_MARKS, ""]
    for original, replacement in _REPLACEMENTS.items():
        expression = f"REPLACE({expression}, %s, %s)"
        params.extend((original, replacement))
    expression = f"TRIM(REGEXP_REPLACE({expression}, %s, %s))"
    params.extend((_SPACES, " "))
    if mobile:
        expression = f"REGEXP_REPLACE({expression}, %s, %s)"
        params.extend((_MOBILE_FORMATTING, ""))
    return expression, tuple(params)
