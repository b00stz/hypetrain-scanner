"""Merge StockTwits + Reddit mention sources into a single ranked candidate list.

Each source's raw "count" is persisted to storage.mention_history on every poll, and compared
against its own trailing rolling average to get a mentions_now/mentions_baseline ratio per
source. The candidate's overall social_ratio is the max across sources (a spike on any one
source is what we're hunting for).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from discovery.reddit import RedditSource
from discovery.stocktwits import StockTwitsSource
from storage import Storage

logger = logging.getLogger(__name__)


@dataclass
class Candidate:
    ticker: str
    stocktwits_mentions_now: Optional[float] = None
    stocktwits_mentions_baseline: Optional[float] = None
    stocktwits_ratio: Optional[float] = None
    reddit_mentions_now: Optional[float] = None
    reddit_mentions_baseline: Optional[float] = None
    reddit_ratio: Optional[float] = None
    reddit_link: Optional[str] = None
    social_ratio: float = 0.0
    sources: list = field(default_factory=list)

    @property
    def stocktwits_link(self) -> str:
        return f"https://stocktwits.com/symbol/{self.ticker}"


def build_candidates(
    storage: Storage,
    known_tickers: set[str],
    stocktwits_source: Optional[StockTwitsSource],
    reddit_source: Optional[RedditSource],
    baseline_lookback_days: float,
    cold_start_baseline: float,
    limit: int,
) -> list[Candidate]:
    now = datetime.now(timezone.utc)
    now_iso = now.isoformat()
    candidates: dict[str, Candidate] = {}

    if stocktwits_source is not None:
        st_counts = stocktwits_source.get_trending_counts()
        for ticker, count in st_counts.items():
            if ticker not in known_tickers:
                continue
            storage.record_mentions("stocktwits", ticker, count, now_iso)
            baseline = storage.mention_baseline(
                "stocktwits", ticker, baseline_lookback_days, now, cold_start_baseline
            )
            ratio = count / baseline
            cand = candidates.setdefault(ticker, Candidate(ticker=ticker))
            cand.stocktwits_mentions_now = count
            cand.stocktwits_mentions_baseline = baseline
            cand.stocktwits_ratio = ratio
            cand.sources.append("stocktwits")

    if reddit_source is not None:
        reddit_counts, reddit_links = reddit_source.get_mention_counts()
        for ticker, count in reddit_counts.items():
            if ticker not in known_tickers:
                continue
            storage.record_mentions("reddit", ticker, count, now_iso)
            baseline = storage.mention_baseline(
                "reddit", ticker, baseline_lookback_days, now, cold_start_baseline
            )
            ratio = count / baseline
            cand = candidates.setdefault(ticker, Candidate(ticker=ticker))
            cand.reddit_mentions_now = count
            cand.reddit_mentions_baseline = baseline
            cand.reddit_ratio = ratio
            cand.reddit_link = reddit_links.get(ticker)
            cand.sources.append("reddit")

    for cand in candidates.values():
        ratios = [r for r in (cand.stocktwits_ratio, cand.reddit_ratio) if r is not None]
        cand.social_ratio = max(ratios) if ratios else 0.0

    ranked = sorted(candidates.values(), key=lambda c: c.social_ratio, reverse=True)
    return ranked[:limit]
