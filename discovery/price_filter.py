"""One-time (per scanner startup) price-based ticker filtering.

Why this exists: "penny stock" is fundamentally a price definition, not a subreddit or mention-
count one. Ranking candidates purely by mention_ratio lets large, heavily-discussed names
(AAPL, MSFT, SPY, ...) win nearly every candidate slot over genuine penny stocks, since r/stocks,
r/options, and StockTwits' trending list are all large-cap-biased, and even r/pennystocks itself
mentions megacaps in passing. Filtering the known-ticker universe by actual current price, before
any discovery source runs, is the only reliable way to make the scanner actually focus on
penny-stock-tier names.

This runs once at `live` startup (not per poll cycle) via a single batched yfinance call --
539 tickers took ~45s in testing. Re-running the scanner re-classifies with current prices.
"""
from __future__ import annotations

import logging
import math

import yfinance as yf

logger = logging.getLogger(__name__)


def filter_by_max_price(tickers: set[str], max_price: float) -> set[str]:
    """Return the subset of `tickers` whose last close is <= max_price.

    Tickers yfinance can't price (delisted, renamed, transient batch failures) are excluded --
    fails closed, since the point is a focused set, not a best-effort superset.
    """
    if not tickers:
        return set()

    ticker_list = sorted(tickers)
    try:
        data = yf.download(
            ticker_list, period="1d", group_by="ticker", threads=True, progress=False
        )
    except Exception:
        logger.exception("Batch price fetch failed; price filter yielding no tickers this run")
        return set()

    kept: set[str] = set()
    unpriced = 0
    for ticker in ticker_list:
        try:
            close = float(data[ticker]["Close"].iloc[-1])
        except Exception:
            unpriced += 1
            continue
        if math.isnan(close):
            unpriced += 1
            continue
        if close <= max_price:
            kept.add(ticker)

    logger.info(
        "Price filter: %d/%d tickers <= $%.2f (%d unpriced/excluded)",
        len(kept), len(ticker_list), max_price, unpriced,
    )
    return kept
