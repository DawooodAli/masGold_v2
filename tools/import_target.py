"""Which database an old-data import may write to: the production database, or a throw-away rehearsal copy named masgold_import_<something> (built by tools.build_production --skip-account). Nothing else is ever accepted."""
import re

PRODUCTION = "masgold_prod"
REHEARSAL_PATTERN = re.compile(r"masgold_import_[a-z0-9_]{1,30}")


def resolve(words):
    """['prod'] -> masgold_prod; ['test', 'masgold_import_x'] -> that name; anything else -> None."""
    words = list(words or [])
    if words == ["prod"]:
        return PRODUCTION
    if len(words) == 2 and words[0] == "test" and REHEARSAL_PATTERN.fullmatch(words[1]):
        return words[1]
    return None
