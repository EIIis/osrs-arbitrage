"""Deterministic statistical rules; all windows end at the evaluation time."""

from dataclasses import replace
from decimal import Decimal
from statistics import median

from app.domain.calculations import ceil, floor, net
from app.domain.types import PROFILE_RULES, RULES, Analysis, Bucket, Mode, Profile, Quote, Recommendation


class InsufficientData(ValueError):
    pass


def quantile(values: list[Decimal], probability: Decimal) -> Decimal:
    values = sorted(values)
    if not values:
        raise InsufficientData("Missing observations in an analysis window.")
    pos = (len(values) - 1) * probability
    left = floor(pos)
    right = min(left + 1, len(values) - 1)
    return values[left] + (values[right] - values[left]) * (pos - left)


def coverage(rows: list[Bucket], start: int, end: int) -> Decimal:
    """Union of valid closed intervals, rather than an assumed sample count."""
    spans = sorted(
        (max(start, b.start), min(end, b.end))
        for b in rows
        if b.valid and b.end <= end and b.end > start and b.start < end
    )
    covered, cursor = 0, start
    for left, right in spans:
        covered += max(0, right - max(cursor, left))
        cursor = max(cursor, right)
    return Decimal(covered) / max(1, end - start)


def window(rows: list[Bucket], start: int, end: int) -> list[Bucket]:
    return [b for b in rows if b.valid and start <= b.start and b.end <= end]


def analyze(rows: list[Bucket], mode: Mode, now: int, hourly_rows: list[Bucket] | None = None) -> Analysis:
    rule = RULES[mode]
    # observed_at prevents accidental use of future information during historical replay.
    rows = [b for b in rows if b.observed_at <= now and b.end <= now]
    cov = coverage(rows, now - rule.history_seconds, now)
    if cov < Decimal(".8"):
        raise InsufficientData(f"Historical coverage {cov:.0%}; at least 80% required.")
    entry = window(rows, now - rule.entry_seconds, now)
    exits = window(rows, now - rule.exit_seconds, now)
    recent = window(rows, now - rule.trend_seconds, now)
    previous = window(rows, now - 2 * rule.trend_seconds, now - rule.trend_seconds)
    if not all((entry, exits, recent, previous)):
        raise InsufficientData("Resolution is too coarse or a required window is empty.")
    recent_mid = Decimal(median([b.midpoint for b in recent]))
    previous_mid = Decimal(median([b.midpoint for b in previous]))
    trend = (recent_mid / previous_mid - 1) * 100
    lows = [b.low for b in entry]
    highs = [b.high for b in exits]
    # Hourly resampling uses observed medians and skips missing hours.
    hourly: dict[int, list[Decimal]] = {}
    for b in hourly_rows if hourly_rows is not None else rows:
        if (
            b.valid
            and b.interval <= 3600
            and b.observed_at <= now
            and b.end <= now
            and b.start >= now - rule.history_seconds
        ):
            hourly.setdefault(b.start // 3600, []).append(b.midpoint)
    changes = []
    centers = {key: Decimal(median(values)) for key, values in hourly.items()}
    for hour, value in centers.items():
        if hour - 1 in centers:
            changes.append(abs((value / centers[hour - 1] - 1) * 100))
    volatility = Decimal(median(changes)) if changes else None
    volume_rows = window(rows, now - rule.volume_seconds, now)
    volume = min(sum(b.high_volume for b in volume_rows), sum(b.low_volume for b in volume_rows))
    return Analysis(
        ceil(quantile(lows, Decimal(".25"))),
        ceil(quantile(lows, Decimal(".5"))),
        floor(quantile(highs, rule.exit_quantiles[0])),
        floor(quantile(highs, rule.exit_quantiles[1])),
        trend,
        volatility,
        cov,
        volume,
    )


def recommend(
    item_id: int,
    name: str,
    buy_limit: int | None,
    bought_recently: int,
    mode: Mode,
    profile: Profile,
    budget: int,
    quote: Quote,
    hourly: Bucket | None,
    rows: list[Bucket],
    now: int,
    hourly_rows: list[Bucket] | None = None,
) -> Recommendation:
    rule = RULES[mode]
    minimum_volume, participation = PROFILE_RULES[profile]
    if not quote.fresh(now, rule.freshness):
        raise InsufficientData("Stale, missing, or one-sided latest quote.")
    if hourly is None or not hourly.valid or not 0 <= now - hourly.end <= 7200:
        raise InsufficientData("Missing or stale hourly aggregate.")
    hourly_volume = min(hourly.high_volume, hourly.low_volume)
    if hourly_volume < minimum_volume:
        raise InsufficientData("Insufficient two-sided hourly volume.")
    data = analyze(rows, mode, now, hourly_rows)
    if data.buy_low <= 0 or data.buy_low > data.buy_high or data.sell_low > data.sell_high:
        raise InsufficientData("Invalid price bands.")
    if quote.low < Decimal(data.buy_low) * Decimal(".99") or quote.low > Decimal(data.buy_high) * Decimal(
        "1.01"
    ):
        raise InsufficientData("Latest low is outside the current entry band tolerance.")
    if data.trend_percent < (Decimal("-1") if mode == Mode.QUICK else Decimal(0)):
        raise InsufficientData("Recent trend is still declining.")
    unit_profit = net(data.sell_low, item_id) - data.buy_high
    roi = Decimal(unit_profit) / data.buy_high * 100
    if unit_profit <= 0 or roi < rule.min_roi:
        raise InsufficientData("Conservative net return is below the mode threshold.")
    if budget < data.buy_high:
        raise InsufficientData("Budget cannot fund one unit at the suggested entry price.")
    quantity = None
    if buy_limit is not None:
        quantity = max(
            0,
            min(
                budget // data.buy_high,
                max(0, buy_limit - bought_recently),
                floor(Decimal(data.volume) * participation),
            ),
        )
        if quantity <= 0:
            raise InsufficientData("Budget, recorded buy limit, or participation cap leaves no quantity.")
    explanation = (
        f"{roi:.2f}% conservative net return; trend {data.trend_percent:+.2f}%; "
        f"{hourly_volume:,} trades/hour on the quieter side; {data.coverage:.0%} history coverage. "
        "Offer estimates, not confirmed fills. Buy-limit availability excludes unrecorded activity."
    )
    if quantity is None:
        explanation += " Buy limit unknown: quantity recommendation omitted."
    if data.volatility is None:
        explanation += " Hourly volatility is unavailable and receives no stability ranking credit."
    return Recommendation(
        item_id, name, mode, data, quantity, unit_profit, roi, unit_profit * (quantity or 0), explanation
    )


def rank(candidates: list[Recommendation]) -> list[Recommendation]:
    def percentile(value, values, reverse=False):
        if len(values) <= 1:
            return Decimal(1)
        better = sum(other > value if reverse else other < value for other in values)
        return Decimal(better) / (len(values) - 1)

    values = list(candidates)
    output = []
    for c in candidates:
        score = (
            Decimal(".5") * percentile(c.total_profit, [v.total_profit for v in values])
            + Decimal(".2") * percentile(c.roi, [v.roi for v in values])
            + Decimal(".2") * percentile(c.analysis.volume, [v.analysis.volume for v in values])
            + Decimal(".1")
            * (
                percentile(
                    c.analysis.volatility,
                    [v.analysis.volatility for v in values if v.analysis.volatility is not None],
                    True,
                )
                if c.analysis.volatility is not None
                else Decimal(0)
            )
        )
        output.append(replace(c, score=score))
    return sorted(output, key=lambda c: (-c.score, c.item_id))
