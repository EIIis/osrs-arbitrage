from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum


class Mode(StrEnum):
    QUICK = "quick"
    SWING = "swing"
    HOLD = "hold"


class Profile(StrEnum):
    CONSERVATIVE = "conservative"
    BALANCED = "balanced"
    AGGRESSIVE = "aggressive"


@dataclass(frozen=True)
class ModeRules:
    lookback: str
    history_seconds: int
    entry_seconds: int
    exit_seconds: int
    trend_seconds: int
    exit_quantiles: tuple[Decimal, Decimal]
    min_roi: Decimal
    freshness: int
    history_ttl: int
    volume_seconds: int
    decline_warning: Decimal
    cooldown: int


RULES = {
    Mode.QUICK: ModeRules(
        "24h",
        86400,
        3600,
        3600,
        1800,
        (Decimal(".25"), Decimal(".5")),
        Decimal(".5"),
        300,
        300,
        3600,
        Decimal("2"),
        1800,
    ),
    Mode.SWING: ModeRules(
        "7d",
        604800,
        21600,
        604800,
        21600,
        (Decimal(".4"), Decimal(".6")),
        Decimal("2"),
        1800,
        900,
        86400,
        Decimal("5"),
        21600,
    ),
    Mode.HOLD: ModeRules(
        "30d",
        2592000,
        86400,
        2592000,
        86400,
        (Decimal(".4"), Decimal(".6")),
        Decimal("5"),
        7200,
        3600,
        604800,
        Decimal("10"),
        86400,
    ),
}
PROFILE_RULES = {
    Profile.CONSERVATIVE: (100, Decimal(".02")),
    Profile.BALANCED: (50, Decimal(".05")),
    Profile.AGGRESSIVE: (10, Decimal(".10")),
}


@dataclass(frozen=True)
class Bucket:
    start: int
    interval: int
    high: Decimal | None
    low: Decimal | None
    high_volume: int
    low_volume: int
    observed_at: int = 0

    @property
    def end(self) -> int:
        return self.start + self.interval

    @property
    def valid(self) -> bool:
        return (
            self.high is not None
            and self.low is not None
            and self.high > 0
            and self.low > 0
            and self.high_volume > 0
            and self.low_volume > 0
        )

    @property
    def midpoint(self) -> Decimal:
        assert self.high is not None and self.low is not None
        return (self.high + self.low) / 2


@dataclass(frozen=True)
class Quote:
    high: int | None
    low: int | None
    high_time: int | None
    low_time: int | None
    observed_at: int

    def fresh(self, now: int, maximum_age: int) -> bool:
        return all(
            v is not None and 0 <= now - v <= maximum_age
            for v in (self.high_time, self.low_time, self.observed_at)
        ) and bool(self.high and self.low and self.high > 0 and self.low > 0)


@dataclass(frozen=True)
class Analysis:
    buy_low: int
    buy_high: int
    sell_low: int
    sell_high: int
    trend_percent: Decimal
    volatility: Decimal | None
    coverage: Decimal
    volume: int


@dataclass(frozen=True)
class Recommendation:
    item_id: int
    name: str
    mode: Mode
    analysis: Analysis
    quantity: int | None
    unit_profit: int
    roi: Decimal
    total_profit: int
    explanation: str
    score: Decimal = Decimal(0)
