"""hypetrain-scanner CLI: `live` polling mode and `backtest` mode.

NOT INVESTMENT ADVICE. See README.md for the full disclaimer -- hype-driven signals are
typically already priced in by the time they're detectable this way.
"""
from __future__ import annotations

import argparse
import logging
import os
import time
from datetime import datetime, timezone

from alerting import AlertContext, AlertLinks, maybe_alert
from configuration import load_config, setup_logging
from discovery.candidates import Candidate, build_candidates
from discovery.reddit import RedditSource
from discovery.stocktwits import StockTwitsSource
from discovery.tickers import DEFAULT_TICKERS_CSV, load_known_tickers
from scoring import compute_hype_score
from signals.news import FinnhubNewsProvider, NewsProvider, NewsSignal, get_news_signal
from signals.options import OptionsProvider, OptionsSignal, YFinanceOptionsProvider
from signals.price_volume import get_price_volume_signal
from signals.yf_cache import YFinanceCache
from storage import SignalRecord, Storage

logger = logging.getLogger(__name__)


def build_stocktwits_source(config: dict) -> StockTwitsSource | None:
    cfg = config["discovery"]["stocktwits"]
    if not cfg.get("enabled", True):
        return None
    return StockTwitsSource(cfg["trending_url"], cfg.get("request_timeout_seconds", 10))


def build_reddit_source(config: dict, known_tickers: set[str]) -> RedditSource | None:
    cfg = config["discovery"]["reddit"]
    if not cfg.get("enabled", True):
        return None
    if not os.environ.get("REDDIT_CLIENT_ID") or not os.environ.get("REDDIT_CLIENT_SECRET"):
        logger.warning("Reddit discovery disabled: REDDIT_CLIENT_ID/REDDIT_CLIENT_SECRET not set")
        return None
    return RedditSource(
        subreddits=cfg["subreddits"],
        known_tickers=known_tickers,
        posts_per_subreddit=cfg.get("posts_per_subreddit", 75),
        mention_lookback_hours=cfg.get("mention_lookback_hours", 24),
    )


def build_news_provider(config: dict, cache: YFinanceCache) -> NewsProvider | None:
    api_key = os.environ.get("FINNHUB_API_KEY")
    if not api_key:
        logger.warning("News signal disabled: FINNHUB_API_KEY not set")
        return None
    return FinnhubNewsProvider(api_key, cache)


def run_cycle(
    config: dict,
    storage: Storage,
    known_tickers: set[str],
    stocktwits_source: StockTwitsSource | None,
    reddit_source: RedditSource | None,
    yf_cache: YFinanceCache,
    options_provider: OptionsProvider,
    news_provider: NewsProvider | None,
) -> None:
    candidates = build_candidates(
        storage=storage,
        known_tickers=known_tickers,
        stocktwits_source=stocktwits_source,
        reddit_source=reddit_source,
        baseline_lookback_days=config["discovery"]["mentions"]["baseline_lookback_days"],
        cold_start_baseline=config["discovery"]["mentions"]["cold_start_baseline"],
        limit=config["polling"]["candidate_limit"],
    )
    logger.info("Cycle: %d candidates from discovery", len(candidates))

    for cand in candidates:
        _evaluate_candidate(cand, config, storage, yf_cache, options_provider, news_provider)


def _evaluate_candidate(
    cand: Candidate,
    config: dict,
    storage: Storage,
    yf_cache: YFinanceCache,
    options_provider: OptionsProvider,
    news_provider: NewsProvider | None,
) -> None:
    pv_signal = get_price_volume_signal(cand.ticker, yf_cache, config["signals"]["price_volume"])
    if pv_signal is None:
        logger.info("Skipping %s: no usable price data from yfinance", cand.ticker)
        return

    if config["signals"]["options"].get("enabled", True):
        opt_signal = options_provider.get_options_signal(cand.ticker)
    else:
        opt_signal = OptionsSignal(available=False)

    if news_provider is not None:
        news_signal = get_news_signal(cand.ticker, news_provider, config["signals"]["news"])
    else:
        news_signal = NewsSignal(count_recent=0, count_baseline=0, score=0.0, link=None)

    breakdown = compute_hype_score(
        social_ratio=cand.social_ratio,
        price_volume_score=pv_signal.score,
        options_score=opt_signal.score,
        options_available=opt_signal.available,
        news_score=news_signal.score,
        config=config,
    )

    now_iso = datetime.now(timezone.utc).isoformat()
    record = SignalRecord(
        timestamp=now_iso,
        ticker=cand.ticker,
        social_mentions_now=cand.stocktwits_mentions_now or cand.reddit_mentions_now,
        social_mentions_baseline=cand.stocktwits_mentions_baseline or cand.reddit_mentions_baseline,
        social_ratio=cand.social_ratio,
        social_score=breakdown.social_score,
        price=pv_signal.price,
        price_change_pct=pv_signal.price_change_pct,
        volume=pv_signal.volume,
        avg_volume_20d=pv_signal.avg_volume,
        volume_ratio=pv_signal.volume_ratio,
        intraday_volatility_pct=pv_signal.intraday_volatility_pct,
        price_volume_score=pv_signal.score,
        options_available=opt_signal.available,
        put_call_volume_ratio=opt_signal.put_call_volume_ratio,
        options_score=breakdown.options_score,
        news_count_recent=news_signal.count_recent,
        news_count_baseline=news_signal.count_baseline,
        news_score=news_signal.score,
        total_score=breakdown.total_score,
        crossed_threshold=breakdown.crossed_threshold,
        raw={
            "stocktwits_ratio": cand.stocktwits_ratio,
            "reddit_ratio": cand.reddit_ratio,
            "sources": cand.sources,
        },
    )
    signal_id = storage.insert_signal(record)
    logger.info(
        "%s: score=%.1f (social=%.1f pv=%.1f options=%s news=%.1f) crossed=%s",
        cand.ticker,
        breakdown.total_score,
        breakdown.social_score,
        breakdown.price_volume_score,
        f"{breakdown.options_score:.1f}" if opt_signal.available else "n/a",
        breakdown.news_score,
        breakdown.crossed_threshold,
    )

    if breakdown.crossed_threshold:
        ctx = AlertContext(
            ticker=cand.ticker,
            breakdown=breakdown,
            price=pv_signal.price,
            links=AlertLinks(
                stocktwits=cand.stocktwits_link if cand.stocktwits_ratio else None,
                reddit=cand.reddit_link,
                news=news_signal.link,
            ),
        )
        maybe_alert(
            storage,
            signal_id,
            ctx,
            cooldown_hours=config["alerting"]["cooldown_hours"],
            paper_mode=config.get("paper_mode", True),
        )


def cmd_live(args: argparse.Namespace, config: dict) -> None:
    if config.get("paper_mode", True):
        logger.info("Running in PAPER MODE: alerts are logged/emailed only, never trades.")

    storage = Storage(config["database"]["path"])
    known_tickers = load_known_tickers(
        DEFAULT_TICKERS_CSV, excluded=config["discovery"].get("excluded_tickers", [])
    )
    logger.info("Loaded %d known tickers", len(known_tickers))

    stocktwits_source = build_stocktwits_source(config)
    reddit_source = build_reddit_source(config, known_tickers)
    yf_cache = YFinanceCache(ttl_seconds=config["polling"]["interval_seconds"])
    options_provider = YFinanceOptionsProvider(yf_cache, config["signals"]["options"])
    news_provider = build_news_provider(config, yf_cache)

    interval = config["polling"]["interval_seconds"]
    while True:
        started = time.monotonic()
        try:
            run_cycle(
                config, storage, known_tickers, stocktwits_source, reddit_source,
                yf_cache, options_provider, news_provider,
            )
        except Exception:
            logger.exception("Unhandled error during poll cycle")

        if args.once:
            return

        elapsed = time.monotonic() - started
        sleep_for = max(0.0, interval - elapsed)
        logger.info("Cycle done in %.1fs, sleeping %.1fs", elapsed, sleep_for)
        time.sleep(sleep_for)


def cmd_backtest(args: argparse.Namespace, config: dict) -> None:
    from backtest import run_backtest

    storage = Storage(config["database"]["path"])
    run_backtest(
        storage,
        crossed_only=not args.all_signals,
        since=args.since,
        limit=args.limit,
    )


def main() -> None:
    parser = argparse.ArgumentParser(prog="hypetrain-scanner")
    parser.add_argument("--config", default="config.yaml", help="path to config.yaml")
    sub = parser.add_subparsers(dest="command", required=True)

    live_parser = sub.add_parser("live", help="run the near-real-time discovery+alert loop")
    live_parser.add_argument(
        "--once", action="store_true", help="run a single poll cycle and exit (useful for cron)"
    )
    live_parser.set_defaults(func=cmd_live)

    backtest_parser = sub.add_parser("backtest", help="evaluate logged signals against forward returns")
    backtest_parser.add_argument(
        "--all-signals", action="store_true",
        help="include signals that never crossed the alert threshold (default: crossed-only)",
    )
    backtest_parser.add_argument("--since", default=None, help="ISO8601 timestamp lower bound")
    backtest_parser.add_argument("--limit", type=int, default=None, help="max signals to evaluate")
    backtest_parser.set_defaults(func=cmd_backtest)

    args = parser.parse_args()
    config = load_config(args.config)
    setup_logging(config)
    args.func(args, config)


if __name__ == "__main__":
    main()
