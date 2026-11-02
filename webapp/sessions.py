"""Server-side sessions of the iPad reports app: a random 256-bit token -> one verified AppSession + its ReportService.

Nothing identity-related is ever taken from the browser except this opaque token. A session expires after IDLE_SECONDS without a request or MAX_SECONDS after login, whichever comes first;
logout and expiry drop the AppSession (the actor is cleared, so a service call with an old actor is refused)."""
import secrets
from threading import RLock
from time import monotonic

IDLE_SECONDS = 20 * 60
MAX_SECONDS = 12 * 60 * 60
MAX_SESSIONS = 20


class Entry:
    __slots__ = ("session", "service", "created", "last", "deadcash")

    def __init__(self, session, service, now):
        self.session, self.service, self.created, self.last = session, service, now, now
        self.deadcash = None


class SessionStore:
    def __init__(self, clock=monotonic, idle=IDLE_SECONDS, maximum=MAX_SECONDS, capacity=MAX_SESSIONS):
        self._clock, self._idle, self._max, self._capacity = clock, idle, maximum, capacity
        self._lock = RLock()
        self._entries = {}

    def add(self, session, service):
        with self._lock:
            self._purge()
            if len(self._entries) >= self._capacity:                       # the oldest session makes room; a flood of logins never grows the table
                oldest = min(self._entries, key=lambda t: self._entries[t].last)
                self._drop(oldest)
            token = secrets.token_urlsafe(32)
            self._entries[token] = Entry(session, service, self._clock())
            return token

    def get(self, token):
        """The live entry (and its idle timer is renewed) or None."""
        if not token:
            return None
        with self._lock:
            entry = self._entries.get(token)
            if entry is None:
                return None
            now = self._clock()
            if now - entry.last > self._idle or now - entry.created > self._max:
                self._drop(token)
                return None
            entry.last = now
            return entry

    def remove(self, token):
        with self._lock:
            self._drop(token)

    def count(self):
        with self._lock:
            self._purge()
            return len(self._entries)

    def _purge(self):
        now = self._clock()
        for token in [t for t, e in self._entries.items() if now - e.last > self._idle or now - e.created > self._max]:
            self._drop(token)

    def _drop(self, token):
        entry = self._entries.pop(token, None)
        if entry is not None:
            try:
                entry.session.logout()
            except Exception:                                              # a broken session object must never keep a token alive
                pass
