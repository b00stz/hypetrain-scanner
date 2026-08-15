"""Backtest mode: for every logged signal, pull forward price data via yfinance and compute what
would have happened if you'd bought at signal time. Run this repeatedly while tuning scoring
weights in config.yaml -- see README.md. This is analysis, not a recommendation to trade.
"""
from __future__ import annotations

import logging
import statistics
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Optional

import yfinance as yf

from storage import Storage

logger = logging.getLogger(__name__)

HORIZON_DAYS = (1, 3, 7)


def _price_on_or_after(hist, target_date) -> Optional[float]:
    for idx, row in hist.iterrows():
        idx_date = idx.date() if hasattr(idx, "date") else idx
        if idx_date >= target_date:
            return float(row["Close"])
    return None


def _forward_returns(ticker: str, signal_dt: datetime, entry_price: float) -> dict[int, Optional[float]]:
    start_date = signal_dt.date()
    end_date = start_date + timedelta(days=max(HORIZON_DAYS) + 5)
    try:
        hist = yf.Ticker(ticker).history(
            start=start_date.isoformat(), end=end_date.isoformat(), interval="1d"
        )
    except Exception:
        logger.exception("yfinance forward-price fetch failed for %s", ticker)
        return {h: None for h in HORIZON_DAYS}

    if hist is None or hist.empty or not entry_price:
        return {h: None for h in HORIZON_DAYS}

    returns: dict[int, Optional[float]] = {}
    for horizon in HORIZON_DAYS:
        price = _price_on_or_after(hist, start_date + timedelta(days=horizon))
        returns[horizon] = (
            (price - entry_price) / entry_price * 100.0 if price is not None else None
        )
    return returns


def _dominant_signal(row) -> str:
    scores = {
        "social": row["social_score"] or 0.0,
        "price_volume": row["price_volume_score"] or 0.0,
        "options": row["options_score"] or 0.0,
        "news": row["news_score"] or 0.0,
    }
    return max(scores, key=scores.get)


def run_backtest(
    storage: Storage,
    crossed_only: bool = True,
    since: Optional[str] = None,
    limit: Optional[int] = None,
) -> None:
    rows = storage.fetch_signals(crossed_threshold_only=crossed_only, since=since, limit=limit)
    if not rows:
        print("No signals found matching the given filters.")
        return

    run_ts = datetime.now(timezone.utc).isoformat()
    results = []

    for row in rows:
        ticker = row["ticker"]
        entry_price = row["price"]
        if not entry_price:
            continue
        signal_dt = datetime.fromisoformat(row["timestamp"])
        returns = _forward_returns(ticker, signal_dt, entry_price)

        storage.insert_backtest_result(
            run_timestamp=run_ts,
            signal_id=row["id"],
            ticker=ticker,
            signal_timestamp=row["timestamp"],
            entry_price=entry_price,
            return_1d_pct=returns.get(1),
            return_3d_pct=returns.get(3),
            return_7d_pct=returns.get(7),
            crossed_threshold=bool(row["crossed_threshold"]),
        )
        results.append(
            {"ticker": ticker, "returns": returns, "dominant_signal": _dominant_signal(row)}
        )

    _print_summary(results)


def _print_bucket(label: str, results: list[dict]) -> None:
    print(f"--- {label} (n={len(results)}) ---")
    for horizon in HORIZON_DAYS:
        values = [
            r["returns"].get(horizon) for r in results if r["returns"].get(horizon) is not None
        ]
        if not values:
            print(f"  {horizon}d: no data")
            continue
        hit_rate = sum(1 for v in values if v > 0) / len(values) * 100.0
        avg = statistics.mean(values)
        median = statistics.median(values)
        stdev = statistics.stdev(values) if len(values) > 1 else 0.0
        print(
            f"  {horizon}d: n={len(values)} hit_rate={hit_rate:.1f}% avg={avg:+.2f}% "
            f"median={median:+.2f}% stdev={stdev:.2f} min={min(values):+.2f}% max={max(values):+.2f}%"
        )
    print()


def _print_summary(results: list[dict]) -> None:
    print(f"\nBacktested {len(results)} signals\n")
    _print_bucket("ALL", results)

    by_signal: dict[str, list[dict]] = defaultdict(list)
    for r in results:
        by_signal[r["dominant_signal"]].append(r)
    for signal_type, bucket in sorted(by_signal.items()):
        _print_bucket(f"dominant signal = {signal_type}", bucket)
