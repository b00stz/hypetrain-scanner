"""Combine the four signal types into a single 0-100 hype score using config-driven weights.

No magic numbers live here -- every threshold and weight comes from config.yaml so the scoring
can be tuned (and backtested) without touching code.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ScoreBreakdown:
    social_score: float
    price_volume_score: float
    options_score: float
    options_available: bool
    news_score: float
    total_score: float
    crossed_threshold: bool


def _scale(ratio: float, full_score_ratio: float) -> float:
    if full_score_ratio <= 0:
        return 0.0
    return max(0.0, min(100.0, (ratio / full_score_ratio) * 100.0))


def compute_hype_score(
    social_ratio: float,
    price_volume_score: float,
    options_score: float,
    options_available: bool,
    news_score: float,
    config: dict,
) -> ScoreBreakdown:
    social_full_score_ratio = config["discovery"]["mentions"]["ratio_full_score"]
    social_score = _scale(social_ratio, social_full_score_ratio)

    weights = config["scoring"]["weights"]
    scores = {
        "social": social_score,
        "price_volume": price_volume_score,
        "news": news_score,
    }
    active_weights = {
        "social": weights["social"],
        "price_volume": weights["price_volume"],
        "news": weights["news"],
    }

    # A missing/thin options chain (common for low-liquidity names) drops the options
    # component from the weighted average entirely, rather than silently scoring it 0 and
    # dragging the total down for a reason unrelated to hype.
    if options_available:
        scores["options"] = options_score
        active_weights["options"] = weights["options"]

    weight_sum = sum(active_weights.values())
    total_score = (
        sum(scores[k] * active_weights[k] for k in active_weights) / weight_sum
        if weight_sum > 0
        else 0.0
    )

    threshold = config["scoring"]["alert_threshold"]
    crossed_threshold = total_score >= threshold

    return ScoreBreakdown(
        social_score=social_score,
        price_volume_score=price_volume_score,
        options_score=options_score if options_available else 0.0,
        options_available=options_available,
        news_score=news_score,
        total_score=total_score,
        crossed_threshold=crossed_threshold,
    )
