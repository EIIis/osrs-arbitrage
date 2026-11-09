"""Validated Wiki v2 transport. Public data only; no Discord token is sent."""

import asyncio
import json
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from email.utils import parsedate_to_datetime

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from app.core.exceptions import MarketError
from app.domain.types import Bucket, Quote


class CatalogItem(BaseModel):
    id: int = Field(gt=0)
    name: str = Field(min_length=1, max_length=255)
    members: bool
    limit: int | None = Field(default=None, ge=0)

    @field_validator("limit")
    @classmethod
    def unknown_limit(cls, value):
        return value or None


class LatestPrice(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    high: int | None
    low: int | None
    high_time: int | None = Field(alias="highTime")
    low_time: int | None = Field(alias="lowTime")

    @field_validator("high", "low", "high_time", "low_time")
    @classmethod
    def nonnegative(cls, value):
        if value is not None and value < 0:
            raise ValueError("Negative price or timestamp")
        return value


class Average(BaseModel):
    high: Decimal | None = Field(alias="avgHighPrice")
    low: Decimal | None = Field(alias="avgLowPrice")
    high_volume: int = Field(alias="highPriceVolume", ge=0)
    low_volume: int = Field(alias="lowPriceVolume", ge=0)

    @field_validator("high", "low")
    @classmethod
    def finite_price(cls, value):
        if value is not None and (
            not value.is_finite() or value < 0 or value * 100 != (value * 100).to_integral_value()
        ):
            raise ValueError("Averages must be finite nonnegative GP with at most two decimal places")
        return value

    def bucket(self, start: int, interval: int, observed_at: int) -> Bucket:
        return Bucket(start, interval, self.high, self.low, self.high_volume, self.low_volume, observed_at)


class HistoryPoint(Average):
    timestamp: int = Field(ge=0)


class HistoryResponse(BaseModel):
    itemId: int = Field(gt=0)
    startTimestamp: int = Field(ge=0)
    endTimestamp: int = Field(ge=0)
    timestep: int = Field(gt=0)
    data: list[HistoryPoint]

    @model_validator(mode="after")
    def valid_range(self):
        if self.endTimestamp <= self.startTimestamp:
            raise ValueError("Invalid history bounds")
        stamps = [p.timestamp for p in self.data]
        if len(stamps) != len(set(stamps)) or any(
            t < self.startTimestamp or t > self.endTimestamp for t in stamps
        ):
            raise ValueError("Duplicate or out-of-range history timestamp")
        return self


@dataclass(frozen=True)
class History:
    item_id: int
    start: int
    end: int
    interval: int
    buckets: list[Bucket]


class WikiClient:
    def __init__(
        self,
        base_url: str,
        user_agent: str,
        interval: float = 1.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        self.client = httpx.AsyncClient(
            base_url=base_url.rstrip("/") + "/",
            headers={"User-Agent": user_agent},
            timeout=15,
            transport=transport,
        )
        self.interval = max(1.0, interval)
        self.history_lock = asyncio.Lock()
        self.blocked_until: dict[str, float] = {}
        self.last_history = 0.0
        self.cache: dict[tuple, tuple[float, object]] = {}

    async def close(self):
        await self.client.aclose()

    async def _get(self, endpoint: str, params=None):
        remaining = self.blocked_until.get(endpoint, 0) - time.monotonic()
        if remaining > 0:
            raise MarketError(
                f"Provider retry window active for {endpoint}; {int(remaining) + 1}s remaining."
            )
        for attempt in range(3):
            try:
                response = await self.client.get(endpoint, params=params)
                if response.status_code == 429 or response.status_code >= 500:
                    wait = 2**attempt
                    retry = response.headers.get("Retry-After")
                    if retry:
                        try:
                            wait = max(wait, float(retry))
                        except ValueError:
                            try:
                                wait = max(
                                    wait, (parsedate_to_datetime(retry) - datetime.now(UTC)).total_seconds()
                                )
                            except (ValueError, TypeError):
                                pass
                    # Do not retry earlier than a long Retry-After, and do not block a loop indefinitely.
                    if wait > 60:
                        self.blocked_until[endpoint] = time.monotonic() + wait
                        raise MarketError(
                            f"Provider requested a {int(wait)}s retry delay; request path paused until then."
                        )
                    if attempt == 2:
                        self.blocked_until[endpoint] = time.monotonic() + wait
                        response.raise_for_status()
                    await asyncio.sleep(max(0, wait))
                    continue
                response.raise_for_status()
                return json.loads(response.text, parse_float=Decimal)
            except (httpx.RequestError, httpx.HTTPStatusError) as exc:
                if attempt < 2 and (isinstance(exc, httpx.RequestError) or exc.response.status_code >= 500):
                    await asyncio.sleep(2**attempt)
                    continue
                raise MarketError(f"Wiki request failed: {type(exc).__name__}") from exc
            except (ValueError, TypeError) as exc:
                raise MarketError("Wiki returned invalid JSON.") from exc
        raise MarketError("Wiki request exhausted retries.")

    async def catalog(self) -> list[CatalogItem]:
        try:
            data = await self._get("mapping")
            if not isinstance(data, list) or not data:
                raise ValueError("Empty catalog")
            return [CatalogItem.model_validate(row) for row in data]
        except (ValidationError, ValueError, TypeError) as exc:
            raise MarketError("Invalid Wiki mapping contract.") from exc

    async def latest(self, now: int) -> dict[int, Quote]:
        try:
            raw = (await self._get("latest"))["data"]
            result = {}
            for key, value in raw.items():
                p = LatestPrice.model_validate(value)
                result[int(key)] = Quote(p.high, p.low, p.high_time, p.low_time, now)
            return result
        except (ValidationError, ValueError, KeyError, TypeError, AttributeError) as exc:
            raise MarketError("Invalid Wiki latest contract.") from exc

    async def averages(self, interval: int, now: int) -> dict[int, Bucket]:
        if interval not in (300, 3600):
            raise ValueError("Only five-minute and hourly bulk aggregates are supported.")
        try:
            raw = await self._get("5m" if interval == 300 else "1h")
            stamp = int(raw["timestamp"])
            if stamp < 0 or stamp + interval > now:
                raise ValueError("Future or invalid aggregate timestamp")
            return {
                int(key): Average.model_validate(value).bucket(stamp, interval, now)
                for key, value in raw["data"].items()
            }
        except (ValidationError, ValueError, KeyError, TypeError, AttributeError) as exc:
            raise MarketError("Invalid Wiki aggregate contract.") from exc

    async def history(self, item_id: int, lookback: str, ttl: int, now: int) -> History:
        key = (item_id, lookback)
        # One lock coalesces concurrent command/background requests and limits history traffic.
        async with self.history_lock:
            cached = self.cache.get(key)
            if cached and time.monotonic() - cached[0] < ttl:
                return cached[1]
            await asyncio.sleep(max(0, self.interval - (time.monotonic() - self.last_history)))
            self.last_history = time.monotonic()
            try:
                value = HistoryResponse.model_validate(
                    await self._get("timeseries", {"id": item_id, "lookback": lookback})
                )
                if value.itemId != item_id or value.endTimestamp > now + value.timestep:
                    raise ValueError("Wrong item or future history range")
                history = History(
                    item_id,
                    value.startTimestamp,
                    value.endTimestamp,
                    value.timestep,
                    [
                        p.bucket(p.timestamp, value.timestep, now)
                        for p in value.data
                        if p.timestamp + value.timestep <= now
                    ],
                )
            except (ValidationError, ValueError, TypeError) as exc:
                raise MarketError("Invalid Wiki v2 timeseries contract.") from exc
            self.cache[key] = (time.monotonic(), history)
            return history
