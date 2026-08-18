"""Catalyst-news discovery: surface tickers with a fresh, material headline (contract win, FDA
approval, acquisition, etc.) even if nothing has shown up on Reddit/StockTwits yet.

Why this exists: mention-based discovery (discovery/candidates.py) only ever looks at a ticker
once it's already trending socially, which by construction lags the news that caused the move --
by the time a stock is loud on Reddit, the price has usually already reacted. Scanning news
directly closes (part of) that gap for tickers already on the known-ticker whitelist.

Coverage limits, stated plainly:
  - This only ever checks tickers already in data/tickers.csv. A ticker that has never been added
    to the whitelist is invisible to this scanner no matter what, since Finnhub's free tier has no
    "any ticker, any headline" firehose -- only per-symbol company news. There is no free-tier way
    around this; broad real-time news-with-ticker-tagging is what paid terminals sell.
  - To respect Finnhub's free-tier rate limit (60 calls/min), this scans a rotating batch of the
    whitelist each poll cycle rather than all of it -- see `batch_size` in config.yaml. Full
    coverage of the whitelist takes multiple cycles, not one.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass

from signals.news import NewsProvider

logger = logging.getLogger(__name__)


@dataclass
class CatalystHit:
    ticker: str
    headline: str
    url: str | None


def _current_batch(all_tickers: list[str], batch_size: int, interval_seconds: float) -> list[str]:
    """Deterministically rotate through `all_tickers` in slices of `batch_size`, advancing one
    slice per poll interval, without needing any persisted cursor state."""
    if not all_tickers or batch_size <= 0:
        return []
    num_batches = max(1, -(-len(all_tickers) // batch_size))  # ceil division
    current_index = int(time.time() // max(interval_seconds, 1)) % num_batches
    start = current_index * batch_size
    return all_tickers[start : start + batch_size]


def scan_for_catalysts(
    known_tickers: set[str],
    news_provider: NewsProvider,
    keywords: list[str],
    lookback_hours: float,
    batch_size: int,
    interval_seconds: float,
) -> list[CatalystHit]:
    tickers = sorted(known_tickers)  # stable order so the rotation is deterministic
    batch = _current_batch(tickers, batch_size, interval_seconds)
    if not batch:
        return []

    keywords_lower = [k.lower() for k in keywords]
    hits: list[CatalystHit] = []

    for ticker in batch:
        try:
            headlines = news_provider.get_recent_headlines(ticker, lookback_hours)
        except Exception:
            logger.exception("Catalyst news scan failed for %s", ticker)
            continue

        for article in headlines:
            headline = (article.get("headline") or "").strip()
            if not headline:
                continue
            headline_lower = headline.lower()
            if any(kw in headline_lower for kw in keywords_lower):
                hits.append(CatalystHit(ticker=ticker, headline=headline, url=article.get("url")))
                break  # one hit per ticker per cycle is enough to flag it

    if hits:
        logger.info(
            "Catalyst scan: %d/%d tickers checked this cycle, %d hit(s): %s",
            len(batch), len(tickers), len(hits), [h.ticker for h in hits],
        )
    return hits
