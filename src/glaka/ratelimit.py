"""A small sliding-window rate limiter (in memory, per process)."""

import time
from collections import defaultdict, deque
from threading import Lock


class RateLimiter:
    def __init__(self, limit: int, window_seconds: float) -> None:
        self.limit, self.window = limit, window_seconds
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = Lock()

    def allow(self, key: str) -> bool:
        """Record a hit for `key` and say whether it's within the limit."""
        now = time.monotonic()
        with self._lock:
            hits = self._hits[key]
            while hits and hits[0] <= now - self.window:
                hits.popleft()
            if len(hits) >= self.limit:
                return False
            hits.append(now)
            return True
