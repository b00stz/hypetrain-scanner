"""Options confirmation signal.

IMPORTANT: yfinance's `Ticker.option_chain()` is a free, delayed snapshot of end-of-day-ish
volume/open-interest -- it is NOT real-time options flow, and thin/illiquid names often have
sparse or stale chains. Treat this signal as the lowest-confidence of the four. `OptionsProvider`
is an interface specifically so a paid options-flow API (e.g. Unusual Whales, CBOE LiveVol,
Benzinga Options) can be swapped in later without touching scoring.py or main.py.
"""
from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional

import yfinance as yf

from signals.yf_cache import YFinanceCache

logger = logging.getLogger(__name__)


@dataclass
class OptionsSignal:
    available: bool
    call_volume: Optional[float] = None
    put_volume: Optional[float] = None
    put_call_volume_ratio: Optional[float] = None
    score: float = 0.0


class OptionsProvider(ABC):
    @abstractmethod
    def get_options_signal(self, ticker: str) -> OptionsSignal:
        ...


def _scale_bullish_ratio(ratio: float, full_score_at: float, zero_score_at: float) -> float:
    """Lower put/call volume ratio == more bullish call-buying == higher score."""
    if ratio <= full_score_at:
        return 100.0
    if ratio >= zero_score_at:
        return 0.0
    span = zero_score_at - full_score_at
    return 100.0 * (zero_score_at - ratio) / span


class YFinanceOptionsProvider(OptionsProvider):
    def __init__(self, cache: YFinanceCache, cfg: dict):
        self.cache = cache
        self.cfg = cfg

    def get_options_signal(self, ticker: str) -> OptionsSignal:
        try:
            tk = self.cache.get(("ticker_obj", ticker), lambda: yf.Ticker(ticker))
            expirations = self.cache.get(("options_expirations", ticker), lambda: tk.options)
            if not expirations:
                return OptionsSignal(available=False)

            nearest_expiry = expirations[0]
            chain = self.cache.get(
                ("option_chain", ticker, nearest_expiry),
                lambda: tk.option_chain(nearest_expiry),
            )

            call_volume = float(chain.calls["volume"].fillna(0).sum())
            put_volume = float(chain.puts["volume"].fillna(0).sum())

            if call_volume <= 0:
                return OptionsSignal(
                    available=True, call_volume=call_volume, put_volume=put_volume
                )

            ratio = put_volume / call_volume
            score = _scale_bullish_ratio(
                ratio,
                self.cfg["put_call_volume_ratio_bullish_full_score"],
                self.cfg["put_call_volume_ratio_bearish_zero_score"],
            )
            return OptionsSignal(
                available=True,
                call_volume=call_volume,
                put_volume=put_volume,
                put_call_volume_ratio=ratio,
                score=score,
            )
        except Exception:
            logger.exception("yfinance options fetch failed for %s", ticker)
            return OptionsSignal(available=False)


class PaidOptionsFlowProvider(OptionsProvider):
    """Stub adapter for a paid real-time options-flow API.

    To use: implement `get_options_signal` against your provider's API (e.g. Unusual Whales,
    CBOE LiveVol, Benzinga Options) and pass an instance of this class into main.py in place of
    YFinanceOptionsProvider. Nothing else in the pipeline needs to change.
    """

    def __init__(self, api_key: str):
        self.api_key = api_key

    def get_options_signal(self, ticker: str) -> OptionsSignal:
        raise NotImplementedError(
            "PaidOptionsFlowProvider is a stub -- implement get_options_signal() against your "
            "paid options-flow API before using it."
        )
