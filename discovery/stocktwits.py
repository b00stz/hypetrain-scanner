"""StockTwits public trending-symbols source.

StockTwits' trending endpoint returns a ranked list of currently-trending symbols but no
historical baseline of its own -- we derive "mentions_now vs baseline" ourselves by recording a
rank-derived count on every poll (storage.mention_history) and comparing against the rolling
average recorded there. See discovery/candidates.py for how this baseline is computed.
"""
from __future__ import annotations

import logging

import requests
from tenacity import retry, stop_after_attempt, wait_exponential

logger = logging.getLogger(__name__)


class StockTwitsSource:
    def __init__(self, trending_url: str, timeout_seconds: float = 10.0):
        self.trending_url = trending_url
        self.timeout_seconds = timeout_seconds

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=1, max=8), reraise=True)
    def _fetch(self) -> dict:
        # StockTwits' edge/CDN returns 403 for requests with no (or a bare-library) User-Agent.
        headers = {"User-Agent": "Mozilla/5.0 (compatible; hypetrain-scanner/0.1)"}
        resp = requests.get(self.trending_url, timeout=self.timeout_seconds, headers=headers)
        resp.raise_for_status()
        return resp.json()

    def get_trending_counts(self) -> dict[str, float]:
        """Return {ticker: rank_weighted_count} for currently trending symbols.

        Rank-weighted so the top of the trending list counts for more than the bottom:
        count = (list_size - rank_index).
        """
        try:
            data = self._fetch()
        except Exception:
            logger.exception("StockTwits trending fetch failed")
            return {}

        symbols = data.get("symbols", [])
        counts: dict[str, float] = {}
        n = len(symbols)
        for idx, entry in enumerate(symbols):
            symbol = (entry.get("symbol") or "").strip().upper()
            if not symbol:
                continue
            counts[symbol] = float(n - idx)
        return counts

    def thread_url(self, ticker: str) -> str:
        return f"https://stocktwits.com/symbol/{ticker}"
