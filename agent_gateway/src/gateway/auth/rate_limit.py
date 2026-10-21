"""In-memory sliding-window rate limiting.

State is intentionally not persisted: a restart clears counters.  This is
documented.  Authentication failures are limited per client address, and
operations are limited per agent by policy.
"""

from __future__ import annotations

import threading
import time
from collections import deque


class SlidingWindowLimiter:
    def __init__(self, *, max_keys: int = 10_000) -> None:
        self._events: dict[str, deque[float]] = {}
        self._lock = threading.Lock()
        self._max_keys = max_keys
        self._last_cleanup = time.monotonic()

    def _cleanup(self, now: float, window: float) -> None:
        if now - self._last_cleanup < 60:
            return
        self._last_cleanup = now
        stale = [key for key, events in self._events.items() if not events or events[-1] < now - window]
        for key in stale[: max(1, len(stale) // 2)]:
            self._events.pop(key, None)
        if len(self._events) > self._max_keys:
            # Keep the most recently active keys.
            ordered = sorted(self._events.items(), key=lambda item: item[1][-1] if item[1] else 0)
            for key, _ in ordered[: len(self._events) - self._max_keys]:
                self._events.pop(key, None)

    def check_and_record(self, key: str, *, limit: int, window_seconds: int) -> tuple[bool, int]:
        """Record an event if the key is under the limit.

        Returns ``(allowed, retry_after_seconds)``.  A denied call does not
        record an event, so a blocked caller cannot extend its own lockout
        indefinitely.
        """

        if limit <= 0:
            return True, 0
        now = time.monotonic()
        cutoff = now - window_seconds
        with self._lock:
            self._cleanup(now, window_seconds)
            events = self._events.setdefault(key, deque())
            while events and events[0] < cutoff:
                events.popleft()
            if len(events) >= limit:
                retry_after = max(1, int(window_seconds - (now - events[0])) + 1)
                return False, retry_after
            events.append(now)
            return True, 0

    def reset(self, key: str) -> None:
        with self._lock:
            self._events.pop(key, None)

    def clear(self) -> None:
        with self._lock:
            self._events.clear()
