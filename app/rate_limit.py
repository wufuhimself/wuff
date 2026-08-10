"""Process-wide sliding-window rate limiter.

Blocks the calling thread until a slot is free, so callers just call
acquire() before each request and never think about pacing. Thread-safe;
shared across the web app, its background sync scheduler, and any CLI code
running in the same process.
"""
import threading
import time
from collections import deque


class RateLimiter:
    def __init__(self, max_calls: int, per_seconds: float = 60.0):
        self.max_calls = max_calls
        self.per_seconds = per_seconds
        self._lock = threading.Lock()
        self._timestamps: deque = deque()

    def acquire(self) -> None:
        """Block until making one more call now stays within the budget."""
        while True:
            with self._lock:
                now = time.monotonic()
                while self._timestamps and now - self._timestamps[0] >= self.per_seconds:
                    self._timestamps.popleft()
                if len(self._timestamps) < self.max_calls:
                    self._timestamps.append(now)
                    return
                wait = self.per_seconds - (now - self._timestamps[0])
            time.sleep(max(wait, 0.01))
