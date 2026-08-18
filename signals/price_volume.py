"""Price/volume/intraday-volatility confirmation signal, via yfinance."""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import Optional

import yfinance as yf

from signals.yf_cache import YFinanceCache

logger = logging.getLogger(__name__)


def _is_missing(value) -> bool:
    if value is None:
        return True
    try:
        return math.isnan(float(value))
    except (TypeError, ValueError):
        return True


@dataclass
class PriceVolumeSignal:
    price: float
    price_change_pct: float
    volume: float
    avg_volume: float
    volume_ratio: float
    intraday_volatility_pct: float
    score: float


def _scale(value: float, full_score_value: float) -> float:
    """Linear 0-100 scale: 0 at value=0, 100 at value>=full_score_value."""
    if full_score_value <= 0:
        return 0.0
    return max(0.0, min(100.0, (value / full_score_value) * 100.0))


def get_price_volume_signal(
    ticker: str, cache: YFinanceCache, cfg: dict
) -> Optional[PriceVolumeSignal]:
    lookback_days = int(cfg["lookback_days"])

    def fetch():
        return yf.Ticker(ticker).history(period=f"{lookback_days + 5}d", interval="1d")

    try:
        hist = cache.get(("history_1d", ticker, lookback_days), fetch)
    except Exception:
        logger.exception("yfinance history fetch failed for %s", ticker)
        return None

    if hist is None or hist.empty or len(hist) < 2:
        return None

    today = hist.iloc[-1]
    prev = hist.iloc[-2]

    if _is_missing(prev["Close"]):
        return None

    # yfinance's daily history can carry an incomplete last row -- Volume already populated but
    # OHLC still NaN, seen while Yahoo hasn't finished settling the current session's close yet.
    # Silently letting NaN flow through here previously produced a plausible-looking but garbage
    # score (NaN propagates through the _scale() min/max calls without raising). Fall back to a
    # live quote (fast_info) for today's price/high/low in that case; Volume from history is
    # still valid either way, confirmed in testing.
    today_incomplete = _is_missing(today["Close"]) or _is_missing(today["Low"])

    if today_incomplete:
        try:
            fast_info = cache.get(("fast_info", ticker), lambda: yf.Ticker(ticker).fast_info)
            today_close = float(fast_info["lastPrice"])
            today_high = float(fast_info["dayHigh"])
            today_low = float(fast_info["dayLow"])
        except Exception:
            logger.warning("%s: history's last bar is incomplete and fast_info fallback failed", ticker)
            return None
        if _is_missing(today_close) or _is_missing(today_low):
            return None
    else:
        today_close = float(today["Close"])
        today_high = float(today["High"])
        today_low = float(today["Low"])

    if _is_missing(today["Volume"]):
        return None

    price = today_close
    price_change_pct = (today_close - float(prev["Close"])) / float(prev["Close"]) * 100.0
    volume = float(today["Volume"])  # Volume is populated even when OHLC is incomplete

    baseline_window = hist["Volume"].iloc[:-1].tail(lookback_days)
    avg_volume = float(baseline_window.mean()) if len(baseline_window) else 0.0
    volume_ratio = (volume / avg_volume) if avg_volume > 0 else 0.0

    intraday_volatility_pct = (today_high - today_low) / today_low * 100.0

    weights = cfg["weights"]
    volume_component = _scale(volume_ratio, cfg["volume_ratio_full_score"])
    price_change_component = _scale(abs(price_change_pct), cfg["price_change_pct_full_score"])
    volatility_component = _scale(
        abs(intraday_volatility_pct), cfg["intraday_volatility_full_score_pct"]
    )

    score = (
        weights["volume"] * volume_component
        + weights["price_change"] * price_change_component
        + weights["intraday_volatility"] * volatility_component
    )

    return PriceVolumeSignal(
        price=price,
        price_change_pct=price_change_pct,
        volume=volume,
        avg_volume=avg_volume,
        volume_ratio=volume_ratio,
        intraday_volatility_pct=intraday_volatility_pct,
        score=score,
    )
