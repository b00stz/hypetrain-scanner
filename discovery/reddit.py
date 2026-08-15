"""Reddit mention source via PRAW. Ticker mentions are found with a regex against the known-ticker
list (discovery/tickers.py) -- deliberately not free-text NLP, so it's fast, cheap, and predictable.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone

import praw

from discovery.tickers import extract_tickers

logger = logging.getLogger(__name__)


class RedditSource:
    def __init__(
        self,
        subreddits: list[str],
        known_tickers: set[str],
        posts_per_subreddit: int = 75,
        mention_lookback_hours: float = 24,
    ):
        self.subreddits = subreddits
        self.known_tickers = known_tickers
        self.posts_per_subreddit = posts_per_subreddit
        self.mention_lookback_hours = mention_lookback_hours
        self._reddit = praw.Reddit(
            client_id=os.environ["REDDIT_CLIENT_ID"],
            client_secret=os.environ["REDDIT_CLIENT_SECRET"],
            user_agent=os.environ.get("REDDIT_USER_AGENT", "hypetrain-scanner/0.1"),
        )

    def get_mention_counts(self) -> tuple[dict[str, float], dict[str, str]]:
        """Return (counts, example_permalink_by_ticker) for tickers mentioned in the lookback window."""
        cutoff = datetime.now(timezone.utc) - timedelta(hours=self.mention_lookback_hours)
        counts: dict[str, float] = {}
        example_links: dict[str, str] = {}

        for subreddit_name in self.subreddits:
            try:
                subreddit = self._reddit.subreddit(subreddit_name)
                for submission in subreddit.new(limit=self.posts_per_subreddit):
                    created = datetime.fromtimestamp(submission.created_utc, tz=timezone.utc)
                    if created < cutoff:
                        continue
                    text = f"{submission.title}\n{submission.selftext or ''}"
                    tickers = extract_tickers(text, self.known_tickers)
                    for ticker in tickers:
                        counts[ticker] = counts.get(ticker, 0) + 1
                        example_links.setdefault(
                            ticker, f"https://reddit.com{submission.permalink}"
                        )
            except Exception:
                logger.exception("Reddit fetch failed for r/%s", subreddit_name)
                continue

        return counts, example_links
