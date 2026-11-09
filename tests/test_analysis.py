from dataclasses import replace
from decimal import Decimal

import pytest

from app.domain.analysis import InsufficientData, analyze, rank, recommend
from app.domain.types import RULES, Bucket, Mode, Profile, Quote

NOW = 1794268800


def history(mode, high="120.50", low="100.25"):
    rule = RULES[mode]
    interval = {Mode.QUICK: 300, Mode.SWING: 3600, Mode.HOLD: 7200}[mode]
    return [
        Bucket(t, interval, Decimal(high), Decimal(low), 1000, 1000, NOW - 1)
        for t in range(NOW - rule.history_seconds, NOW, interval)
    ]


def quote():
    return Quote(121, 101, NOW - 10, NOW - 10, NOW)


def hourly():
    return Bucket(NOW - 3600, 3600, Decimal("120.50"), Decimal("100.25"), 1000, 1000, NOW)


@pytest.mark.parametrize("mode", list(Mode))
async def test_all_modes_decimal_precision_and_participation(mode):
    rows = history(mode)
    analysis = analyze(rows, mode, NOW)
    assert analysis.buy_low == 101 and analysis.sell_low == 120
    result = recommend(453, "Coal", 13000, 100, mode, Profile.BALANCED, 1000000, quote(), hourly(), rows, NOW)
    assert result.unit_profit == 17
    assert result.quantity <= 12900
    assert result.quantity <= int(Decimal(analysis.volume) * Decimal(".05"))
    assert result.roi >= RULES[mode].min_roi


@pytest.mark.parametrize("mode", list(Mode))
def test_continuing_decline_rejected(mode):
    rows = history(mode)
    duration = RULES[mode].trend_seconds
    rows = [replace(b, high=Decimal(110), low=Decimal(90)) if b.start >= NOW - duration else b for b in rows]
    with pytest.raises(InsufficientData, match="declining"):
        recommend(
            453,
            "Coal",
            13000,
            0,
            mode,
            Profile.BALANCED,
            100000,
            Quote(110, 90, NOW, NOW, NOW),
            hourly(),
            rows,
            NOW,
        )


def test_missing_history_zero_volume_and_future_information():
    rows = history(Mode.QUICK)
    with pytest.raises(InsufficientData, match="coverage"):
        analyze(rows[100:], Mode.QUICK, NOW)
    with pytest.raises(InsufficientData):
        analyze([replace(b, high_volume=0) for b in rows], Mode.QUICK, NOW)
    with pytest.raises(InsufficientData):
        analyze(rows, Mode.QUICK, NOW - 3600)  # fetched later than simulated evaluation


@pytest.mark.parametrize("problem", ["stale", "one_sided", "thin", "limit", "budget", "margin", "entry"])
def test_quality_gates(problem):
    q, h, rows, limit, budget = quote(), hourly(), history(Mode.QUICK), 13000, 100000
    if problem == "stale":
        q = replace(q, low_time=NOW - 1000)
    if problem == "one_sided":
        q = replace(q, high=None)
    if problem == "thin":
        h = replace(h, low_volume=1)
    if problem == "limit":
        limit = 0
    if problem == "budget":
        budget = 50
    if problem == "margin":
        rows = history(Mode.QUICK, high="102.00")
    if problem == "entry":
        q = replace(q, low=200)
    with pytest.raises(InsufficientData):
        recommend(453, "Coal", limit, 0, Mode.QUICK, Profile.BALANCED, budget, q, h, rows, NOW)


def test_unknown_limit_omits_quantity_and_ranking_is_deterministic():
    a = recommend(
        453,
        "Coal",
        None,
        0,
        Mode.QUICK,
        Profile.BALANCED,
        100000,
        quote(),
        hourly(),
        history(Mode.QUICK),
        NOW,
    )
    assert a.quantity is None
    b = replace(a, item_id=440)
    assert [r.item_id for r in rank([a, b])] == [440, 453]


def test_recovering_swing_is_allowed():
    rows = history(Mode.SWING)
    rows = [
        replace(b, high=Decimal("119.5"), low=Decimal("99.25")) if NOW - 43200 <= b.start < NOW - 21600 else b
        for b in rows
    ]
    result = recommend(
        453, "Coal", 13000, 0, Mode.SWING, Profile.BALANCED, 100000, quote(), hourly(), rows, NOW
    )
    assert result.analysis.trend_percent > 0


def test_overlapping_buckets_do_not_inflate_coverage():
    from app.domain.analysis import coverage

    rows = history(Mode.QUICK)
    assert coverage(rows + rows, NOW - 86400, NOW) == 1


def test_unknown_buy_limit_does_not_bypass_budget():
    with pytest.raises(InsufficientData, match="Budget"):
        recommend(
            453,
            "Coal",
            None,
            0,
            Mode.QUICK,
            Profile.BALANCED,
            50,
            quote(),
            hourly(),
            history(Mode.QUICK),
            NOW,
        )


def test_coarse_hold_history_uses_observed_hourly_context_for_volatility():
    rows = history(Mode.HOLD)
    assert analyze(rows, Mode.HOLD, NOW).volatility is None
    hourly_rows = [
        Bucket(t, 3600, Decimal(120 + index % 3), Decimal(100 + index % 3), 1000, 1000, NOW - 1)
        for index, t in enumerate(range(NOW - 7 * 86400, NOW, 3600))
    ]
    assert analyze(rows, Mode.HOLD, NOW, hourly_rows).volatility > 0
