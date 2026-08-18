"""News-headline confirmation signal via a pluggable NewsProvider interface."""
from __future__ import annotations

import logging
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

import requests

from signals.yf_cache import YFinanceCache

logger = logging.getLogger(__name__)


class NewsProvider(ABC):
    @abstractmethod
    def get_recent_count(self, ticker: str, hours: float) -> int:
        ...

    @abstractmethod
    def get_baseline_daily_rate(self, ticker: str, baseline_days: float) -> float:
        ...

    @abstractmethod
    def example_link(self, ticker: str) -> Optional[str]:
        ...

    @abstractmethod
    def get_recent_headlines(self, ticker: str, hours: float) -> list[dict]:
        """Return recent articles as-is (each with at least 'headline' and 'url'), for
        keyword-based catalyst scanning -- see discovery/catalyst.py."""
        ...


class FinnhubNewsProvider(NewsProvider):
    BASE_URL = "https://finnhub.io/api/v1/company-news"

    def __init__(
        self,
        api_key: str,
        cache: YFinanceCache,
        timeout_seconds: float = 10.0,
        min_interval_seconds: float = 1.1,
    ):
        self.api_key = api_key
        self.cache = cache
        self.timeout_seconds = timeout_seconds
        # Finnhub's free tier is 60 calls/minute. Catalyst scanning (a batch of the whitelist
        # each cycle) plus per-candidate news confirmation can add up to ~65 calls in a single
        # cycle -- without pacing, that blew through the limit and returned 429s, which were
        # then silently treated as "no news" (risking a missed catalyst on exactly the cycle it
        # mattered). Throttle actual network calls the same way yfinance calls already are;
        # cache hits (self.cache.get) skip this entirely since fetch() only runs on a miss.
        self.min_interval_seconds = min_interval_seconds
        self._last_call_monotonic = 0.0
        self._last_articles: dict[str, list[dict]] = {}

    def _throttle(self) -> None:
        elapsed = time.monotonic() - self._last_call_monotonic
        if elapsed < self.min_interval_seconds:
            time.sleep(self.min_interval_seconds - elapsed)
        self._last_call_monotonic = time.monotonic()

    def _fetch(self, ticker: str, from_date: str, to_date: str) -> list[dict]:
        key = ("finnhub_news", ticker, from_date, to_date)

        def fetch():
            self._throttle()
            resp = requests.get(
                self.BASE_URL,
                params={"symbol": ticker, "from": from_date, "to": to_date, "token": self.api_key},
                timeout=self.timeout_seconds,
            )
            resp.raise_for_status()
            return resp.json()

        try:
            articles = self.cache.get(key, fetch)
        except Exception:
            logger.warning("Finnhub news fetch failed for %s (rate-limited or unavailable)", ticker)
            articles = []
        self._last_articles[ticker] = articles
        return articles

    def get_recent_count(self, ticker: str, hours: float) -> int:
        now = datetime.now(timezone.utc)
        start = now - timedelta(hours=hours)
        articles = self._fetch(ticker, start.date().isoformat(), now.date().isoformat())
        cutoff_ts = start.timestamp()
        return sum(1 for a in articles if a.get("datetime", 0) >= cutoff_ts)

    def get_baseline_daily_rate(self, ticker: str, baseline_days: float) -> float:
        now = datetime.now(timezone.utc)
        start = now - timedelta(days=baseline_days)
        articles = self._fetch(ticker, start.date().isoformat(), now.date().isoformat())
        if baseline_days <= 0:
            return 0.0
        return len(articles) / baseline_days

    def example_link(self, ticker: str) -> Optional[str]:
        articles = self._last_articles.get(ticker) or []
        if not articles:
            return None
        return articles[0].get("url")

    def get_recent_headlines(self, ticker: str, hours: float) -> list[dict]:
        now = datetime.now(timezone.utc)
        start = now - timedelta(hours=hours)
        articles = self._fetch(ticker, start.date().isoformat(), now.date().isoformat())
        cutoff_ts = start.timestamp()
        return [a for a in articles if a.get("datetime", 0) >= cutoff_ts]


@dataclass
class NewsSignal:
    count_recent: float
    count_baseline: float
    score: float
    link: Optional[str] = None


def _scale(ratio: float, full_score_ratio: float) -> float:
    if full_score_ratio <= 0:
        return 0.0
    return max(0.0, min(100.0, (ratio / full_score_ratio) * 100.0))


def get_news_signal(ticker: str, provider: NewsProvider, cfg: dict) -> NewsSignal:
    hours = float(cfg["lookback_hours"])
    baseline_days = float(cfg["baseline_lookback_days"])

    recent_count = provider.get_recent_count(ticker, hours)
    baseline_daily_rate = provider.get_baseline_daily_rate(ticker, baseline_days)

    expected_recent = baseline_daily_rate * (hours / 24.0)
    expected_recent = max(expected_recent, 1e-6)  # avoid divide-by-zero on cold names
    ratio = recent_count / expected_recent

    score = _scale(ratio, cfg["spike_ratio_full_score"])

    return NewsSignal(
        count_recent=recent_count,
        count_baseline=expected_recent,
        score=score,
        link=provider.example_link(ticker),
    )
