"""Dependency-free safeguards for AI usage."""

from collections import deque
from time import monotonic


class SlidingWindowLimiter:
    """In-process rate limiter; resets when the bot is restarted."""

    def __init__(self, limit: int, window_seconds: int, clock=monotonic):
        if limit <= 0 or window_seconds <= 0:
            raise ValueError("Rate limits must be positive")
        self.limit = limit
        self.window_seconds = window_seconds
        self._clock = clock
        self._events = {}

    def allow(self, key) -> bool:
        now = self._clock()
        entries = self._events.get(key)
        if entries is None:
            if len(self._events) >= 2048:
                self._events = {
                    k: v for k, v in self._events.items()
                    if v and now - v[-1] < self.window_seconds
                }
            entries = self._events.setdefault(key, deque())
        while entries and now - entries[0] >= self.window_seconds:
            entries.popleft()
        if len(entries) >= self.limit:
            return False
        entries.append(now)
        return True
