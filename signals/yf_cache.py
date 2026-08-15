"""TTL cache + simple rate limiter shared by every yfinance call.

yfinance has no official API and Yahoo will IP-block callers that hammer it. This cache makes
sure a) we only ever call yfinance for the current shortlist of candidates (never the whole
market -- callers are responsible for that by construction, since they only look up tickers
that already made it through discovery), and b) repeated lookups for the same ticker within the
TTL window are served from memory instead of hitting the network again.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Callable, Hashable, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")


class YFinanceCache:
    def __init__(self, ttl_seconds: float = 300.0, min_interval_seconds: float = 1.0):
        self.ttl_seconds = ttl_seconds
        self.min_interval_seconds = min_interval_seconds
        self._cache: dict[Hashable, tuple[float, object]] = {}
        self._last_call_monotonic = 0.0
        self._lock = threading.Lock()

    def _throttle(self) -> None:
        with self._lock:
            elapsed = time.monotonic() - self._last_call_monotonic
            if elapsed < self.min_interval_seconds:
                time.sleep(self.min_interval_seconds - elapsed)
            self._last_call_monotonic = time.monotonic()

    def get(self, key: Hashable, fetch_fn: Callable[[], T]) -> T:
        cached = self._cache.get(key)
        now = time.monotonic()
        if cached is not None and (now - cached[0]) < self.ttl_seconds:
            return cached[1]  # type: ignore[return-value]

        self._throttle()
        value = fetch_fn()
        self._cache[key] = (now, value)
        return value
