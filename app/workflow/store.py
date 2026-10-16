from __future__ import annotations

import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field

from app.model.client import ModelAdapter
from app.workflow.state import Session


@dataclass
class _Entry:
    session: Session
    model: ModelAdapter = field(repr=False)
    last_seen: float
    lock: threading.Lock = field(default_factory=threading.Lock)


class SessionStore:
    def __init__(self, ttl_minutes: int):
        self._ttl = ttl_minutes * 60
        self._entries: dict[str, _Entry] = {}
        self._lock = threading.Lock()

    def create(self, model: ModelAdapter) -> Session:
        session = Session()
        with self._lock:
            self._purge()
            self._entries[session.id] = _Entry(session, model, time.monotonic())
        return session

    def model(self, session_id: str) -> ModelAdapter | None:
        with self._lock:
            entry = self._live(session_id)
            return entry.model if entry else None

    @contextmanager
    def checkout(self, session_id: str) -> Iterator[Session | None]:
        """Holds the session's lock for one turn and refreshes its expiry.
        Yields None when the session is unknown or expired."""
        with self._lock:
            entry = self._live(session_id)
        if entry is None:
            yield None
            return
        with entry.lock:
            try:
                yield entry.session
            finally:
                entry.last_seen = time.monotonic()

    def discard(self, session_id: str) -> None:
        with self._lock:
            self._entries.pop(session_id, None)

    def _live(self, session_id: str) -> _Entry | None:
        entry = self._entries.get(session_id)
        if entry is not None and time.monotonic() - entry.last_seen > self._ttl:
            del self._entries[session_id]
            return None
        if entry is not None:
            entry.last_seen = time.monotonic()
        return entry

    def _purge(self) -> None:
        now = time.monotonic()
        for sid in [s for s, e in self._entries.items() if now - e.last_seen > self._ttl]:
            del self._entries[sid]
