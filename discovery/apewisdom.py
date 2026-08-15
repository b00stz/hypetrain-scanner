"""Reddit mention counts via ApeWisdom's free public API (apewisdom.io), not the official Reddit
Data API.

Why: Reddit's own Data API now requires a formal "Responsible Builder Policy" access request for
any app reading subreddit content, including personal/non-commercial ones, and approval is not
guaranteed. ApeWisdom is a long-running third-party aggregator that already tracks mention counts
across the same investing subreddits (wallstreetbets, stocks, options, ...) via its own
arrangement with Reddit's data, and exposes it through a public, unauthenticated JSON API. Using
it removes the need for any Reddit API credentials or approval entirely.

Docs: https://apewisdom.io/api -- no API key required.
"""
from __future__ import annotations

import logging

import requests
from tenacity import retry, stop_after_attempt, wait_exponential

logger = logging.getLogger(__name__)

BASE_URL = "https://apewisdom.io/api/v1.0/filter"


class ApeWisdomSource:
    def __init__(self, filters: list[str], timeout_seconds: float = 10.0):
        self.filters = filters
        self.timeout_seconds = timeout_seconds

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=1, max=8), reraise=True)
    def _fetch_page(self, filter_name: str, page: int = 1) -> dict:
        url = f"{BASE_URL}/{filter_name}/page/{page}"
        resp = requests.get(url, timeout=self.timeout_seconds)
        resp.raise_for_status()
        return resp.json()

    def get_mention_counts(self) -> tuple[dict[str, float], dict[str, str]]:
        """Return (counts, example_link_by_ticker), mirroring the discovery.stocktwits interface.

        `count` is the current mentions figure ApeWisdom reports for each ticker in each
        configured filter (subreddit or subreddit group); the highest count seen across filters
        wins if a ticker appears in more than one. Baselines are computed the same way as every
        other source, via storage.mention_history -- see discovery/candidates.py.
        """
        counts: dict[str, float] = {}
        links: dict[str, str] = {}

        for filter_name in self.filters:
            try:
                data = self._fetch_page(filter_name, page=1)
            except Exception:
                logger.exception("ApeWisdom fetch failed for filter=%s", filter_name)
                continue

            for entry in data.get("results", []):
                ticker = (entry.get("ticker") or "").strip().upper()
                if not ticker:
                    continue
                try:
                    mentions = float(entry.get("mentions", 0))
                except (TypeError, ValueError):
                    continue
                if mentions <= 0:
                    continue
                if mentions > counts.get(ticker, 0.0):
                    counts[ticker] = mentions
                    links[ticker] = f"https://apewisdom.io/stocks/{ticker}/"

        return counts, links
