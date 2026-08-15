from scoring import compute_hype_score


def _config(**overrides):
    base = {
        "discovery": {"mentions": {"ratio_full_score": 5.0}},
        "scoring": {
            "weights": {"social": 0.35, "price_volume": 0.30, "options": 0.15, "news": 0.20},
            "alert_threshold": 70,
        },
    }
    base.update(overrides)
    return base


def test_all_signals_maxed_gives_top_score():
    breakdown = compute_hype_score(
        social_ratio=5.0,  # == ratio_full_score -> social_score 100
        price_volume_score=100.0,
        options_score=100.0,
        options_available=True,
        news_score=100.0,
        config=_config(),
    )
    assert breakdown.total_score == 100.0
    assert breakdown.crossed_threshold is True


def test_zero_signals_gives_zero_score_and_no_alert():
    breakdown = compute_hype_score(
        social_ratio=0.0,
        price_volume_score=0.0,
        options_score=0.0,
        options_available=True,
        news_score=0.0,
        config=_config(),
    )
    assert breakdown.total_score == 0.0
    assert breakdown.crossed_threshold is False


def test_social_ratio_scales_linearly_up_to_full_score_ratio():
    breakdown = compute_hype_score(
        social_ratio=2.5,  # half of ratio_full_score=5.0 -> social_score 50
        price_volume_score=0.0,
        options_score=0.0,
        options_available=False,
        news_score=0.0,
        config=_config(),
    )
    assert breakdown.social_score == 50.0


def test_social_ratio_above_full_score_ratio_is_capped_at_100():
    breakdown = compute_hype_score(
        social_ratio=50.0,
        price_volume_score=0.0,
        options_score=0.0,
        options_available=False,
        news_score=0.0,
        config=_config(),
    )
    assert breakdown.social_score == 100.0


def test_missing_options_data_renormalizes_remaining_weights():
    common_kwargs = dict(
        social_ratio=0.0,
        price_volume_score=80.0,
        options_score=0.0,
        news_score=0.0,
    )
    with_options = compute_hype_score(**common_kwargs, options_available=True, config=_config())
    without_options = compute_hype_score(**common_kwargs, options_available=False, config=_config())

    # Excluding the (zero-scored, but weighted) options component should raise the total,
    # since the remaining weight is redistributed rather than treating "unavailable" as 0.
    assert without_options.total_score > with_options.total_score
    assert without_options.options_available is False
    assert without_options.options_score == 0.0


def test_threshold_boundary_is_inclusive():
    config = _config()
    config["scoring"]["alert_threshold"] = 50
    breakdown = compute_hype_score(
        social_ratio=2.5,  # social_score = 50, all other weights zero contribution paths
        price_volume_score=50.0,
        options_score=50.0,
        options_available=True,
        news_score=50.0,
        config=config,
    )
    assert breakdown.total_score == 50.0
    assert breakdown.crossed_threshold is True
