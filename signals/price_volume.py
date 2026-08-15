"""Price/volume/intraday-volatility confirmation signal, via yfinance."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

import yfinance as yf

from signals.yf_cache import YFinanceCache

logger = logging.getLogger(__name__)


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

    if prev["Close"] in (0, None) or today["Low"] in (0, None):
        return None

    price = float(today["Close"])
    price_change_pct = (float(today["Close"]) - float(prev["Close"])) / float(prev["Close"]) * 100.0
    volume = float(today["Volume"])

    baseline_window = hist["Volume"].iloc[:-1].tail(lookback_days)
    avg_volume = float(baseline_window.mean()) if len(baseline_window) else 0.0
    volume_ratio = (volume / avg_volume) if avg_volume > 0 else 0.0

    intraday_volatility_pct = (
        (float(today["High"]) - float(today["Low"])) / float(today["Low"]) * 100.0
    )

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
