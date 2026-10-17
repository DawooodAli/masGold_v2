"""Cross-flow request-key guard of the market-custody / holding / settlement family against the marketer documents (055).

The marketer account (`marketer_documents`) already refuses a key that a custody entry, a holding document, a receipt or a settlement carries. The older
flows did not look at `marketer_documents`, so a key could be reused by an unrelated custody entry / holding document / receipt / supplier settlement after
a marketer document had taken it. `require_no_marketer_key` is the other direction: every INSERT of those flows calls it, through the repository that owns
the insert (the chokepoint every posting path shares).

What it is NOT: a database constraint. There is no cross-table unique key, so two transactions that use the same key in different flows at the same moment
are not prevented by this check alone (see REVIEW-STABILIZATION.md); it is a current locking read taken under the transaction's own locks.
The table is probed first so a database that is still on migration 054 keeps working (the guard is then a no-op, as `history_count` is)."""

MESSAGE = "طلب العملية مستخدم لعملية مختلفة."
CODE = "REQUEST_CONFLICT"


class RequestKeyInUse(ValueError):
    """The key belongs to a marketer document: the operation that asked is a different one. Same shape as TripError (a ValueError with a stable `code`)."""

    def __init__(self, message=MESSAGE):
        super().__init__(message)
        self.code = CODE


def marketer_key_in_use(fetch_one, key):
    """True when a marketer document carries `key` (a CURRENT read). `fetch_one(sql, params)` is the calling repository's own reader."""
    table = fetch_one("SELECT COUNT(*) AS n FROM information_schema.TABLES WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'marketer_documents'")
    if not table or not int(table["n"]):
        return False
    row = fetch_one("SELECT COUNT(*) AS n FROM marketer_documents WHERE request_key = %s FOR SHARE", (key,))
    return bool(row) and int(row["n"]) > 0


def require_no_marketer_key(fetch_one, key):
    if marketer_key_in_use(fetch_one, key):
        raise RequestKeyInUse()
